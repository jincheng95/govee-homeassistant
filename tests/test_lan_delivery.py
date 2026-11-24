"""Delivery check for raw LAN writes (:mod:`custom_components.govee.lan_delivery`).

A lamp that answers raw writes over the cloud lets a LAN write be checked. The
router resends an unanswered one over MQTT, later writes skip the suspect
address, and a write-only pin that keeps missing raises a repair issue.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from custom_components.govee import diy_actions, lan_delivery
from custom_components.govee.api import raw_router
from custom_components.govee.api.lan_client import LanDeviceInfo
from custom_components.govee.api.protocol import get_profile
from custom_components.govee.const import CONF_ENABLE_LAN_RAW_WRITE, CONF_ENABLE_MQTT_CONTROL, DOMAIN
from custom_components.govee.coordinator import GoveeCoordinator
from tests.conftest import FakeRawClient

# Synthetic id and addresses.
DEVICE_ID = "AA:BB:CC:DD:EE:FF:60:B0"
IP = "10.20.0.52"
OTHER_IP = "10.20.0.53"
ECHO_SKU = "H80C5"
SILENT_SKU = "H6076"
FRAMES = [bytes([0xA3, 0x00, *[0] * 18]), bytes([0x33, 0x05, *[0] * 18])]
ECHO = {"cmd": "ptReal", "state": {"result": 1}, "op": {"command": ["o1gA", "MwUA"]}}
ISSUE_ID = "lan_pin_unreachable_aabbccddeeff60b0"


@pytest.fixture(autouse=True)
def short_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lan_delivery, "ECHO_TIMEOUT_SECONDS", 0.05)


def _coordinator(*, sku: str = ECHO_SKU, hass: Any = None) -> Any:
    coordinator = MagicMock()
    coordinator.hass = hass if hass is not None else MagicMock()
    coordinator.config_entry.options = {CONF_ENABLE_LAN_RAW_WRITE: True, CONF_ENABLE_MQTT_CONTROL: True}
    coordinator.devices = {DEVICE_ID: SimpleNamespace(sku=sku, name="Tree", device_id=DEVICE_ID)}
    coordinator.mqtt_connected = True
    coordinator.gateway_route = MagicMock(return_value=None)
    coordinator._mqtt_client = MagicMock()
    coordinator._mqtt_client.async_publish_ptreal = AsyncMock(return_value=True)
    coordinator._ensure_device_topic = AsyncMock(return_value="GA/device-topic")
    coordinator._lan_writes_suppressed = MagicMock(return_value=False)
    coordinator._lan_write_only = set()
    coordinator._lan_devices = {}
    coordinator.get_transport_health = MagicMock(return_value=None)
    _pin(coordinator, IP)
    return coordinator


def _pin(coordinator: Any, ip: str) -> None:
    sku = coordinator.devices[DEVICE_ID].sku
    coordinator._lan_devices[DEVICE_ID] = LanDeviceInfo(
        device_id=DEVICE_ID, ip=ip, mac=DEVICE_ID, sku=sku, firmware="", last_correlated_ts=0.0
    )


async def _route(coordinator: Any, *, confirm: bool = True) -> bool:
    sku = coordinator.devices[DEVICE_ID].sku
    return await raw_router.async_route_frames(
        coordinator, DEVICE_ID, sku, get_profile(sku), FRAMES, what="test", confirm=confirm
    )


async def _route_answered(coordinator: Any) -> bool:
    task = asyncio.ensure_future(_route(coordinator))
    for _ in range(20):
        await asyncio.sleep(0.005)
        if lan_delivery._targets(coordinator).get(DEVICE_ID, lan_delivery._Target()).waiter is not None:
            break
    lan_delivery.note_inbound(coordinator, DEVICE_ID, ECHO)
    return await task


async def _miss(coordinator: Any) -> None:
    """One unanswered, unconfirmed LAN write, counted."""
    lan_delivery.note_send(coordinator, DEVICE_ID, coordinator._lan_devices[DEVICE_ID].ip)
    await asyncio.sleep(0.06)
    lan_delivery.is_suspect(coordinator, DEVICE_ID, IP)


def _published(coordinator: Any) -> list[str]:
    return list(coordinator._mqtt_client.async_publish_ptreal.await_args.args[2])


class TestProfiles:
    def test_only_captured_skus_are_marked(self) -> None:
        assert get_profile("H80C5").echoes_raw_writes is True
        assert get_profile("H60B0").echoes_raw_writes is True
        assert get_profile("H6076").echoes_raw_writes is False
        assert get_profile("H6046").echoes_raw_writes is False


class TestConfirmedWrite:
    async def test_an_answered_lan_write_is_not_resent(self, raw_client: FakeRawClient) -> None:
        coordinator = _coordinator()

        assert await _route_answered(coordinator) is True

        assert {host for host, _ in raw_client.envelopes} == {IP}
        coordinator._mqtt_client.async_publish_ptreal.assert_not_awaited()
        coordinator._request_lan_rescan.assert_not_called()
        assert lan_delivery.is_suspect(coordinator, DEVICE_ID, IP) is False

    async def test_an_unanswered_lan_write_is_resent_over_mqtt(self, raw_client: FakeRawClient) -> None:
        coordinator = _coordinator()

        assert await _route(coordinator) is True

        assert raw_client.envelopes
        assert _published(coordinator) == [base64.b64encode(frame).decode() for frame in FRAMES]
        coordinator._request_lan_rescan.assert_called_once()
        assert lan_delivery.is_suspect(coordinator, DEVICE_ID, IP) is True

    async def test_it_fails_when_mqtt_refuses_the_resend(self, raw_client: FakeRawClient) -> None:
        coordinator = _coordinator()
        coordinator._mqtt_client.async_publish_ptreal = AsyncMock(return_value=False)

        assert await _route(coordinator) is False

    async def test_a_status_push_is_not_an_answer(self, raw_client: FakeRawClient) -> None:
        coordinator = _coordinator()
        task = asyncio.ensure_future(_route(coordinator))
        await asyncio.sleep(0.01)
        lan_delivery.note_inbound(coordinator, DEVICE_ID, {"state": {"onOff": 1}, "op": {"command": []}})

        assert await task is True
        coordinator._mqtt_client.async_publish_ptreal.assert_awaited_once()

    async def test_without_confirm_the_router_does_not_wait(self, raw_client: FakeRawClient) -> None:
        coordinator = _coordinator()

        assert await _route(coordinator, confirm=False) is True

        coordinator._mqtt_client.async_publish_ptreal.assert_not_awaited()
        assert lan_delivery._targets(coordinator)[DEVICE_ID].pending_ip == IP

    async def test_an_overdue_watch_is_a_miss_without_waiting(self, raw_client: FakeRawClient) -> None:
        coordinator = _coordinator()
        lan_delivery.note_send(coordinator, DEVICE_ID, IP)
        await asyncio.sleep(0.06)

        assert await lan_delivery.async_confirm(coordinator, DEVICE_ID) is False

    async def test_nothing_watched_confirms(self) -> None:
        assert await lan_delivery.async_confirm(_coordinator(), DEVICE_ID) is True


class TestWhenNoAnswerCanCome:
    @pytest.mark.parametrize("case", ["unmarked_sku", "disconnected", "never_heard", "no_client", "mqtt_option_off"])
    async def test_the_write_is_not_watched(self, raw_client: FakeRawClient, case: str) -> None:
        coordinator = _coordinator(sku=SILENT_SKU if case == "unmarked_sku" else ECHO_SKU)
        if case == "disconnected":
            coordinator.mqtt_connected = False
        if case == "never_heard":
            coordinator._mqtt_client.last_message_ts_for = MagicMock(return_value=None)
        if case == "no_client":
            coordinator._mqtt_client = None
        if case == "mqtt_option_off":
            coordinator.config_entry.options[CONF_ENABLE_MQTT_CONTROL] = False

        assert await _route(coordinator) is True

        assert raw_client.envelopes
        assert lan_delivery._targets(coordinator).get(DEVICE_ID, lan_delivery._Target()).pending_ip is None
        coordinator._request_lan_rescan.assert_not_called()

    async def test_a_session_lost_during_the_wait_is_not_a_miss(self, raw_client: FakeRawClient) -> None:
        coordinator = _coordinator()
        lan_delivery.note_send(coordinator, DEVICE_ID, IP)
        coordinator.mqtt_connected = False
        await asyncio.sleep(0.06)

        assert await lan_delivery.async_confirm(coordinator, DEVICE_ID) is True
        assert lan_delivery.is_suspect(coordinator, DEVICE_ID, IP) is False

    def test_an_answer_with_nothing_watched_is_ignored(self) -> None:
        coordinator = _coordinator()

        lan_delivery.note_inbound(coordinator, DEVICE_ID, ECHO)
        lan_delivery.note_inbound(coordinator, "unknown", ECHO)

        assert lan_delivery._targets(coordinator) == {}


class TestSuspectAddress:
    async def test_later_writes_skip_the_lan_tier(self, raw_client: FakeRawClient) -> None:
        coordinator = _coordinator()
        await _miss(coordinator)

        assert await _route(coordinator, confirm=False) is True

        assert raw_client.envelopes == []
        coordinator._mqtt_client.async_publish_ptreal.assert_awaited_once()

    async def test_a_second_send_joins_the_first_watch(self) -> None:
        coordinator = _coordinator()
        lan_delivery.note_send(coordinator, DEVICE_ID, IP)
        since = lan_delivery._targets(coordinator)[DEVICE_ID].pending_since

        lan_delivery.note_send(coordinator, DEVICE_ID, IP)

        assert lan_delivery._targets(coordinator)[DEVICE_ID].pending_since == since

    async def test_the_next_send_counts_an_overdue_watch(self) -> None:
        coordinator = _coordinator()
        lan_delivery.note_send(coordinator, DEVICE_ID, IP)
        await asyncio.sleep(0.06)

        lan_delivery.note_send(coordinator, DEVICE_ID, IP)

        target = lan_delivery._targets(coordinator)[DEVICE_ID]
        assert (target.misses, target.suspect_ip, target.pending_ip) == (1, IP, IP)

    async def test_a_new_address_clears_it(self) -> None:
        coordinator = _coordinator()
        await _miss(coordinator)

        assert lan_delivery.is_suspect(coordinator, DEVICE_ID, OTHER_IP) is False
        assert lan_delivery._targets(coordinator)[DEVICE_ID].misses == 0

    async def test_a_lan_read_after_the_miss_clears_it(self) -> None:
        coordinator = _coordinator()
        await _miss(coordinator)
        earlier = datetime.now(timezone.utc) - timedelta(minutes=1)
        coordinator.get_transport_health = MagicMock(return_value=SimpleNamespace(last_success_ts=earlier))
        assert lan_delivery.is_suspect(coordinator, DEVICE_ID, IP) is True

        later = datetime.now(timezone.utc) + timedelta(seconds=1)
        coordinator.get_transport_health = MagicMock(return_value=SimpleNamespace(last_success_ts=later))

        assert lan_delivery.is_suspect(coordinator, DEVICE_ID, IP) is False

    async def test_it_is_retried_after_the_retry_interval(
        self, raw_client: FakeRawClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        coordinator = _coordinator()
        await _miss(coordinator)
        monkeypatch.setattr(lan_delivery, "SUSPECT_RETRY_SECONDS", 0)

        assert await _route_answered(coordinator) is True

        assert raw_client.envelopes
        target = lan_delivery._targets(coordinator)[DEVICE_ID]
        assert (target.suspect_ip, target.misses) == (None, 0)

    async def test_unknown_devices_are_never_suspect(self) -> None:
        assert lan_delivery.is_suspect(_coordinator(), "unknown", IP) is False


class TestWriteOnlyPin:
    async def test_repeated_misses_raise_a_repair_issue(
        self, hass: HomeAssistant, caplog: pytest.LogCaptureFixture
    ) -> None:
        coordinator = _coordinator(hass=hass)
        coordinator._lan_write_only = {DEVICE_ID}
        registry = ir.async_get(hass)

        with caplog.at_level(logging.WARNING, logger=lan_delivery.__name__):
            for expected in (False, False, True, True):
                await _miss(coordinator)
                assert (registry.async_get_issue(DOMAIN, ISSUE_ID) is not None) is expected

        issue = registry.async_get_issue(DOMAIN, ISSUE_ID)
        assert issue.translation_key == "lan_pin_unreachable"
        assert issue.translation_placeholders == {"device": "Tree", "address": IP}
        assert [record.levelno for record in caplog.records] == [logging.WARNING]
        assert IP in caplog.text

    async def test_an_answer_clears_the_issue(self, hass: HomeAssistant) -> None:
        coordinator = _coordinator(hass=hass)
        coordinator._lan_write_only = {DEVICE_ID}
        for _ in range(lan_delivery.PIN_MISS_THRESHOLD):
            await _miss(coordinator)

        lan_delivery.note_send(coordinator, DEVICE_ID, IP)
        lan_delivery.note_inbound(coordinator, DEVICE_ID, ECHO)

        assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is None
        assert lan_delivery.is_suspect(coordinator, DEVICE_ID, IP) is False

    async def test_a_discovered_device_raises_no_issue(self, hass: HomeAssistant) -> None:
        coordinator = _coordinator(hass=hass)
        for _ in range(lan_delivery.PIN_MISS_THRESHOLD + 1):
            await _miss(coordinator)

        assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is None
        assert coordinator._request_lan_rescan.call_count == lan_delivery.PIN_MISS_THRESHOLD + 1


class TestWiring:
    def test_the_mqtt_raw_frame_hook_feeds_the_check(self, monkeypatch: pytest.MonkeyPatch) -> None:
        coordinator = _coordinator()
        coordinator._devices = coordinator.devices
        monkeypatch.setattr("custom_components.govee.coordinator.apply_raw_status", lambda *args: False)
        monkeypatch.setattr("custom_components.govee.coordinator.apply_zone_power_report", lambda *args: None)
        lan_delivery.note_send(coordinator, DEVICE_ID, IP)

        GoveeCoordinator._on_mqtt_raw_frames(coordinator, DEVICE_ID, dict(ECHO))

        assert lan_delivery._targets(coordinator)[DEVICE_ID].pending_ip is None

    async def test_the_string_effect_upload_asks_for_confirmation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        coordinator = _coordinator()
        route = AsyncMock(return_value=True)
        monkeypatch.setattr(diy_actions.raw_router, "async_route_frames", route)

        await diy_actions._async_apply_string_effect(
            coordinator,
            coordinator.devices[DEVICE_ID],
            get_profile(ECHO_SKU),
            {"effect": "rainbow", "colors": [(255, 0, 0), (0, 255, 0)]},
        )

        assert route.await_args.kwargs["confirm"] is True
