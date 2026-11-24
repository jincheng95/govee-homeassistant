"""Delivery check for raw LAN writes (:mod:`custom_components.govee.lan_delivery`).

A lamp that answers raw writes over the cloud lets a LAN write be checked. The
router resends an unanswered one over MQTT, later writes skip the suspect
address, and a write-only pin that keeps missing raises a repair issue.
"""

from __future__ import annotations

import asyncio
import base64
import inspect
import logging
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from custom_components.govee import diy_actions, diy_state, lan_delivery
from custom_components.govee.api import raw_router
from custom_components.govee.api.lan_client import LanDeviceInfo
from custom_components.govee.api.protocol import get_profile
from custom_components.govee.const import CONF_ENABLE_LAN_RAW_WRITE, CONF_ENABLE_MQTT_CONTROL, DOMAIN
from custom_components.govee.coordinator import GoveeCoordinator
from custom_components.govee.models.transport import TransportHealth
from custom_components.govee.platforms import zone_light
from custom_components.govee.zone_state import ZONE_KEY_BY_TOGGLE
from tests.conftest import FakeRawClient

# Synthetic id and addresses.
DEVICE_ID = "AA:BB:CC:DD:EE:FF:60:B0"
IP = "10.20.0.52"
OTHER_IP = "10.20.0.53"
ECHO_SKU = "H80C5"
SILENT_SKU = "H6076"
FRAMES = [bytes([0xA3, 0x00, *[0] * 18]), bytes([0x33, 0x05, *[0] * 18])]
ECHO = {"cmd": "ptReal", "state": {"result": 1}, "op": {"command": ["o1gA", "MwUA"]}}
QUERY_ANSWER = {"cmd": "ptReal", "state": {"result": 1}, "op": {"command": [base64.b64encode(bytes([0xAA, 0x11, 0x00])).decode()]}}
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
    coordinator.mqtt_last_receive_for = MagicMock(return_value=datetime.now(timezone.utc))
    coordinator._mqtt_client.async_publish_ptreal = AsyncMock(return_value=True)
    coordinator._ensure_device_topic = AsyncMock(return_value="GA/device-topic")
    coordinator._lan_writes_suppressed = MagicMock(return_value=False)
    coordinator._lan_write_only = set()
    coordinator._lan_write_suppressed_until = {}
    coordinator._lan_devices = {}
    coordinator.lan_health = TransportHealth("lan")
    coordinator.get_transport_health = MagicMock(return_value=coordinator.lan_health)
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
    lan_delivery.note_send(coordinator, DEVICE_ID, coordinator._lan_devices[DEVICE_ID].ip, FRAMES)
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

    async def test_the_answer_to_a_query_is_not_an_answer(self, raw_client: FakeRawClient) -> None:
        coordinator = _coordinator()
        task = asyncio.ensure_future(_route(coordinator))
        await asyncio.sleep(0.01)
        lan_delivery.note_inbound(coordinator, DEVICE_ID, QUERY_ANSWER)

        assert await task is True
        coordinator._mqtt_client.async_publish_ptreal.assert_awaited_once()

    async def test_a_late_answer_does_not_count(self) -> None:
        coordinator = _coordinator()
        lan_delivery.note_send(coordinator, DEVICE_ID, IP, FRAMES)
        await asyncio.sleep(0.06)

        lan_delivery.note_inbound(coordinator, DEVICE_ID, ECHO)

        target = lan_delivery._targets(coordinator)[DEVICE_ID]
        assert (target.misses, target.suspect_ip) == (1, IP)

    async def test_a_write_that_went_over_mqtt_does_not_wait(self, raw_client: FakeRawClient) -> None:
        coordinator = _coordinator()
        lan_delivery.note_send(coordinator, DEVICE_ID, IP, FRAMES)
        coordinator._lan_writes_suppressed = MagicMock(return_value=True)

        assert await _route(coordinator) is True

        coordinator._mqtt_client.async_publish_ptreal.assert_awaited_once()
        assert lan_delivery._targets(coordinator)[DEVICE_ID].pending_ip == IP

    async def test_the_upload_lock_is_free_during_the_wait(self, raw_client: FakeRawClient) -> None:
        coordinator = _coordinator()
        task = asyncio.ensure_future(_route(coordinator))
        await asyncio.sleep(0.01)

        assert raw_router.upload_lock(coordinator, DEVICE_ID).locked() is False
        assert await task is True

    async def test_without_confirm_the_router_does_not_wait(self, raw_client: FakeRawClient) -> None:
        coordinator = _coordinator()

        assert await _route(coordinator, confirm=False) is True

        coordinator._mqtt_client.async_publish_ptreal.assert_not_awaited()
        assert lan_delivery._targets(coordinator)[DEVICE_ID].pending_ip == IP

    async def test_an_overdue_watch_is_a_miss_without_waiting(self, raw_client: FakeRawClient) -> None:
        coordinator = _coordinator()
        lan_delivery.note_send(coordinator, DEVICE_ID, IP, FRAMES)
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
            coordinator.mqtt_last_receive_for = MagicMock(return_value=None)
        if case == "no_client":
            coordinator._mqtt_client = None
            coordinator.mqtt_last_receive_for = MagicMock(return_value=None)
        if case == "mqtt_option_off":
            coordinator.config_entry.options[CONF_ENABLE_MQTT_CONTROL] = False

        assert await _route(coordinator) is True

        assert raw_client.envelopes
        assert lan_delivery._targets(coordinator).get(DEVICE_ID, lan_delivery._Target()).pending_ip is None
        coordinator._request_lan_rescan.assert_not_called()

    async def test_a_session_lost_during_the_wait_is_not_a_miss(self, raw_client: FakeRawClient) -> None:
        coordinator = _coordinator()
        lan_delivery.note_send(coordinator, DEVICE_ID, IP, FRAMES)
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
        lan_delivery.note_send(coordinator, DEVICE_ID, IP, FRAMES)
        since = lan_delivery._targets(coordinator)[DEVICE_ID].pending_since

        lan_delivery.note_send(coordinator, DEVICE_ID, IP, FRAMES)

        assert lan_delivery._targets(coordinator)[DEVICE_ID].pending_since == since

    async def test_the_next_send_counts_an_overdue_watch(self) -> None:
        coordinator = _coordinator()
        lan_delivery.note_send(coordinator, DEVICE_ID, IP, FRAMES)
        await asyncio.sleep(0.06)

        lan_delivery.note_send(coordinator, DEVICE_ID, IP, FRAMES)

        target = lan_delivery._targets(coordinator)[DEVICE_ID]
        assert (target.misses, target.suspect_ip, target.pending_ip) == (1, IP, IP)

    async def test_a_new_address_clears_it(self) -> None:
        coordinator = _coordinator()
        await _miss(coordinator)

        assert lan_delivery.is_suspect(coordinator, DEVICE_ID, OTHER_IP) is False
        assert lan_delivery._targets(coordinator)[DEVICE_ID].misses == 0

    async def test_a_lan_reading_after_the_miss_clears_it(self) -> None:
        coordinator = _coordinator()
        coordinator.lan_health.mark_read(datetime.now(timezone.utc) - timedelta(minutes=1))
        await _miss(coordinator)
        assert lan_delivery.is_suspect(coordinator, DEVICE_ID, IP) is True

        coordinator.lan_health.mark_read(datetime.now(timezone.utc) + timedelta(seconds=1))

        assert lan_delivery.is_suspect(coordinator, DEVICE_ID, IP) is False

    async def test_a_send_to_a_write_only_pin_does_not_clear_it(self) -> None:
        coordinator = _coordinator()
        await _miss(coordinator)

        # What the coordinator stamps after every LAN send to a write-only pin.
        coordinator.lan_health.mark_success(datetime.now(timezone.utc) + timedelta(seconds=1))

        assert lan_delivery.is_suspect(coordinator, DEVICE_ID, IP) is True
        assert lan_delivery._targets(coordinator)[DEVICE_ID].misses == 1

    async def test_the_retry_interval_boundary(self) -> None:
        coordinator = _coordinator()
        await _miss(coordinator)
        target = lan_delivery._targets(coordinator)[DEVICE_ID]

        target.suspect_since = time.monotonic() - lan_delivery.SUSPECT_RETRY_SECONDS + 5
        assert lan_delivery.is_suspect(coordinator, DEVICE_ID, IP) is True
        target.suspect_since = time.monotonic() - lan_delivery.SUSPECT_RETRY_SECONDS - 1
        assert lan_delivery.is_suspect(coordinator, DEVICE_ID, IP) is False

    async def test_zone_power_leaves_a_suspect_address_to_the_cloud(
        self, raw_client: FakeRawClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        coordinator = _coordinator(sku="H60B0")
        instance = next(iter(ZONE_KEY_BY_TOGGLE))
        monkeypatch.setattr(raw_router, "_coordinator", lambda entity: coordinator)
        monkeypatch.setattr(raw_router, "_toggle_instance", lambda entity: instance)
        monkeypatch.setattr(raw_router, "_device_id", lambda entity: DEVICE_ID)
        monkeypatch.setattr(raw_router, "_sku", lambda entity: "H60B0")
        await _miss(coordinator)

        assert await raw_router.async_zone_power(MagicMock(), on=True) is False

        assert raw_client.envelopes == []

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
        coordinator._request_lan_rescan.assert_not_called()

    async def test_the_entry_deletes_the_issue_on_unload(self, hass: HomeAssistant) -> None:
        coordinator = _coordinator(hass=hass)
        coordinator._lan_write_only = {DEVICE_ID}
        for _ in range(lan_delivery.PIN_MISS_THRESHOLD):
            await _miss(coordinator)
        assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is not None

        coordinator.config_entry.async_on_unload.assert_called_once()
        coordinator.config_entry.async_on_unload.call_args.args[0]()

        assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is None

    async def test_a_coordinator_without_an_entry_still_reports(self, hass: HomeAssistant) -> None:
        coordinator = _coordinator(hass=hass)
        coordinator.config_entry = None

        lan_delivery._report_pin(coordinator, DEVICE_ID, IP)

        assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is not None

    async def test_the_first_miss_holds_upstreams_lan_commands(self) -> None:
        coordinator = _coordinator()
        coordinator._lan_write_only = {DEVICE_ID}

        await _miss(coordinator)

        assert coordinator._lan_write_suppressed_until == {DEVICE_ID: float("inf")}
        assert lan_delivery.holds_writes(coordinator, DEVICE_ID) is True

    async def test_an_answered_retry_lifts_the_hold(
        self, raw_client: FakeRawClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        coordinator = _coordinator()
        coordinator._lan_write_only = {DEVICE_ID}
        await _miss(coordinator)
        # Upstream's cooldown now reports the hold, and the raw tier must retry through it.
        coordinator._lan_writes_suppressed = MagicMock(return_value=True)
        monkeypatch.setattr(lan_delivery, "SUSPECT_RETRY_SECONDS", 0)

        assert await _route_answered(coordinator) is True

        assert {host for host, _ in raw_client.envelopes} == {IP}
        coordinator._clear_lan_write_misses.assert_called_once_with(DEVICE_ID)
        assert lan_delivery.holds_writes(coordinator, DEVICE_ID) is False

    async def test_a_scanned_device_is_not_held(self) -> None:
        coordinator = _coordinator()

        await _miss(coordinator)

        assert coordinator._lan_write_suppressed_until == {}
        assert lan_delivery.holds_writes(coordinator, DEVICE_ID) is False
        assert lan_delivery.holds_writes(coordinator, "unknown") is False

    async def test_an_answer_clears_the_issue(self, hass: HomeAssistant) -> None:
        coordinator = _coordinator(hass=hass)
        coordinator._lan_write_only = {DEVICE_ID}
        for _ in range(lan_delivery.PIN_MISS_THRESHOLD):
            await _miss(coordinator)

        lan_delivery.note_send(coordinator, DEVICE_ID, IP, FRAMES)
        lan_delivery.note_inbound(coordinator, DEVICE_ID, ECHO)

        assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is None
        assert lan_delivery.is_suspect(coordinator, DEVICE_ID, IP) is False

    async def test_a_discovered_device_raises_no_issue(self, hass: HomeAssistant) -> None:
        coordinator = _coordinator(hass=hass)
        for _ in range(lan_delivery.PIN_MISS_THRESHOLD + 1):
            await _miss(coordinator)

        assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is None
        assert coordinator._request_lan_rescan.call_count == lan_delivery.PIN_MISS_THRESHOLD + 1


class TestKnownAddress:
    """:func:`raw_router.async_send_to_lan_address`, the zone light and DIY upload path."""

    @staticmethod
    async def _send(coordinator: Any, *, confirm: bool = False) -> bool:
        sku = coordinator.devices[DEVICE_ID].sku
        return await raw_router.async_send_to_lan_address(
            coordinator, DEVICE_ID, sku, get_profile(sku), IP, FRAMES, what="test", confirm=confirm
        )

    async def test_a_suspect_address_is_skipped_for_mqtt(self, raw_client: FakeRawClient) -> None:
        coordinator = _coordinator()
        await _miss(coordinator)

        assert await self._send(coordinator) is True

        assert raw_client.envelopes == []
        coordinator._mqtt_client.async_publish_ptreal.assert_awaited_once()

    async def test_a_suspect_address_is_still_written_when_mqtt_refuses(self, raw_client: FakeRawClient) -> None:
        coordinator = _coordinator()
        await _miss(coordinator)
        coordinator._mqtt_client.async_publish_ptreal = AsyncMock(return_value=False)

        assert await self._send(coordinator) is True

        assert {host for host, _ in raw_client.envelopes} == {IP}

    async def test_an_unconfirmed_write_does_not_wait(self, raw_client: FakeRawClient) -> None:
        coordinator = _coordinator()

        assert await self._send(coordinator) is True

        coordinator._mqtt_client.async_publish_ptreal.assert_not_awaited()

    async def test_an_unanswered_confirmed_write_is_resent(self, raw_client: FakeRawClient) -> None:
        coordinator = _coordinator()

        assert await self._send(coordinator, confirm=True) is True

        assert _published(coordinator) == [base64.b64encode(frame).decode() for frame in FRAMES]

    async def test_it_fails_when_nothing_takes_the_resend(self, raw_client: FakeRawClient) -> None:
        coordinator = _coordinator()
        coordinator._mqtt_client.async_publish_ptreal = AsyncMock(return_value=False)

        assert await self._send(coordinator, confirm=True) is False

    async def test_a_failed_lan_send_fails(self, raw_client: FakeRawClient) -> None:
        coordinator = _coordinator()
        raw_client.error = OSError("network unreachable")

        assert await self._send(coordinator, confirm=True) is False
        coordinator._mqtt_client.async_publish_ptreal.assert_not_awaited()

    async def test_zone_lights_send_through_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        send = AsyncMock(return_value=True)
        monkeypatch.setattr(zone_light.raw_router, "async_send_to_lan_address", send)
        profile = get_profile("H60B0")
        entity = SimpleNamespace(
            coordinator=object(),
            _device_id=DEVICE_ID,
            _device=SimpleNamespace(sku="H60B0"),
            _lan_target=lambda: (IP, profile),
        )

        assert await zone_light._GoveeZoneEntityBase._async_send(entity, FRAMES, what="zone") is True

        assert send.await_args.args[1:5] == (DEVICE_ID, "H60B0", profile, IP)
        assert "confirm" not in send.await_args.kwargs

    async def test_the_diy_upload_asks_for_confirmation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        coordinator = _coordinator(sku="H60B0")
        send = AsyncMock(return_value=True)
        monkeypatch.setattr(raw_router, "async_send_to_lan_address", send)
        monkeypatch.setattr(diy_state, "GoveeCodec", lambda profile: SimpleNamespace(diy_effect=lambda effects: FRAMES))

        await diy_state.async_send_diy_effect(coordinator, coordinator.devices[DEVICE_ID], get_profile("H60B0"), {})

        assert send.await_args.kwargs["confirm"] is True


class TestWiring:
    def test_the_coordinator_members_read_here_exist(self) -> None:
        assert callable(GoveeCoordinator._request_lan_rescan)
        assert callable(GoveeCoordinator._clear_lan_write_misses)
        assert "self._lan_write_suppressed_until.get(device_id)" in inspect.getsource(
            GoveeCoordinator._lan_writes_suppressed
        )
        assert callable(GoveeCoordinator.mqtt_last_receive_for)
        assert callable(GoveeCoordinator.get_transport_health)
        assert "self._lan_write_only" in inspect.getsource(GoveeCoordinator._resolve_lan_overrides)

    def test_a_coordinator_without_those_members_is_tolerated(self) -> None:
        bare = SimpleNamespace()

        lan_delivery._request_rescan(bare)
        lan_delivery._release_writes(bare, DEVICE_ID)

        assert lan_delivery._hold_writes(bare, DEVICE_ID) is False
        assert lan_delivery._write_only(bare, DEVICE_ID) is False

    def test_the_mqtt_raw_frame_hook_feeds_the_check(self, monkeypatch: pytest.MonkeyPatch) -> None:
        coordinator = _coordinator()
        coordinator._devices = coordinator.devices
        monkeypatch.setattr("custom_components.govee.coordinator.apply_raw_status", lambda *args: False)
        monkeypatch.setattr("custom_components.govee.coordinator.apply_zone_power_report", lambda *args: None)
        lan_delivery.note_send(coordinator, DEVICE_ID, IP, FRAMES)

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
