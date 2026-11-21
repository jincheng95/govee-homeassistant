"""``govee.upload_bulb_positions`` validation and send path (fork feature).

The coordinator is mocked; the device lookup is monkeypatched as in
``test_services.py``. The upload runs through the real raw router with the LAN
and MQTT senders mocked. Point lists are synthetic.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError, Unauthorized

from custom_components.govee import raw_status
from custom_components.govee.api import lan_raw, mqtt_raw_write, raw_router
from custom_components.govee.api.protocol import paint, positions
from custom_components.govee.const import CONF_ENABLE_LAN_RAW_WRITE, DOMAIN
from custom_components.govee.bulb_actions import (
    SERVICE_PAINT_BULBS_SCHEMA,
    SERVICE_UPLOAD_BULB_POSITIONS,
    SERVICE_UPLOAD_BULB_POSITIONS_SCHEMA,
    async_paint_bulbs_handler,
    async_upload_bulb_positions_handler,
)
from custom_components.govee.services import async_setup_services
from custom_components.govee.zone_state import profile_for

LOOKUP = "custom_components.govee.services._get_coordinator_for_device"
SLEEP = "custom_components.govee.api.raw_router.asyncio.sleep"
DEVICE_ID = "AA:BB:CC:DD:EE:FF:00:11"
OTHER_ID = "AA:BB:CC:DD:EE:FF:00:22"
FRAME = bytes([0x33, 0x01, 0x01, *([0x00] * 16), 0x33])
REAL_MQTT_SEND = mqtt_raw_write.async_send_frames


def _grid(count: int = 300) -> list[list[int]]:
    """``count`` synthetic points on a 10 x 30 grid, already in the 0..500 cube."""
    return [[(index % 10) * 50, (index // 10) * 17, 0] for index in range(count)]


@pytest.fixture
def coordinator(monkeypatch) -> MagicMock:
    device = MagicMock(name="device")
    device.device_id = DEVICE_ID
    device.name = "Tree"
    device.sku = "H80C5"
    coordinator = MagicMock(name="coordinator")
    coordinator.devices = {DEVICE_ID: device}
    coordinator.config_entry.options = {CONF_ENABLE_LAN_RAW_WRITE: True}
    coordinator._lan_devices = {DEVICE_ID: SimpleNamespace(ip="lamp")}
    coordinator.lan_send = AsyncMock(return_value=True)
    coordinator.mqtt_send = AsyncMock(return_value=True)
    monkeypatch.setattr(lan_raw, "async_send_frames", coordinator.lan_send)
    monkeypatch.setattr(mqtt_raw_write, "async_send_frames", coordinator.mqtt_send)
    monkeypatch.setattr(LOOKUP, lambda hass, raw: (coordinator, DEVICE_ID))
    return coordinator


@pytest.fixture
def sleep(monkeypatch) -> AsyncMock:
    real_sleep = asyncio.sleep

    async def _yield(_seconds: float) -> None:
        await real_sleep(0)

    mock = AsyncMock(side_effect=_yield)
    monkeypatch.setattr(SLEEP, mock)
    return mock


def _lan_frames(coordinator: MagicMock) -> list[bytes]:
    """The frames of every LAN message, each of which must carry one packet."""
    frames = []
    for call in coordinator.lan_send.await_args_list:
        (frame,) = call.args[3]
        frames.append(frame)
    return frames


def _call(positions_data, *, normalize: bool = True) -> SimpleNamespace:
    data = SERVICE_UPLOAD_BULB_POSITIONS_SCHEMA(
        {"device_id": DEVICE_ID, "positions": positions_data, "normalize": normalize}
    )
    return SimpleNamespace(data=data)


class TestSendPath:
    async def test_sends_every_packet_in_its_own_message_with_a_gap(self, coordinator, sleep) -> None:
        points = _grid()

        await async_upload_bulb_positions_handler(MagicMock(), _call(points, normalize=False))

        expected = positions.position_map_frames([tuple(point) for point in points], bulb_count=300)
        assert _lan_frames(coordinator) == expected
        assert len(expected) == 113
        assert {call.args[:3] for call in coordinator.lan_send.await_args_list} == {(coordinator, DEVICE_ID, "lamp")}
        assert {call.kwargs["repeats"] for call in coordinator.lan_send.await_args_list} == {1}
        assert sleep.await_count == 112
        sleep.assert_awaited_with(positions.MESSAGE_GAP_SECONDS)
        coordinator.mqtt_send.assert_not_awaited()

    async def test_normalizes_by_default(self, coordinator, sleep) -> None:
        points = [[x / 10, y / 10 + 7, z / 10 - 3] for x, y, z in _grid()]

        await async_upload_bulb_positions_handler(MagicMock(), _call(points))

        normalized = positions.normalize_positions([tuple(point) for point in points])
        expected = positions.position_map_frames(normalized, bulb_count=300)
        assert _lan_frames(coordinator) == expected

    async def test_integral_floats_pass_without_normalizing(self, coordinator, sleep) -> None:
        points = [[float(axis) for axis in point] for point in _grid()]

        await async_upload_bulb_positions_handler(MagicMock(), _call(points, normalize=False))

        assert coordinator.lan_send.await_count == 113

    async def test_a_refused_send_stops_without_changing_pipe(self, coordinator, sleep) -> None:
        coordinator.lan_send.side_effect = [True, True, False]

        with pytest.raises(HomeAssistantError) as excinfo:
            await async_upload_bulb_positions_handler(MagicMock(), _call(_grid()))

        assert excinfo.value.translation_key == "bulb_positions_failed"
        assert excinfo.value.translation_placeholders == {"device": "Tree", "sent": "2", "total": "113"}
        assert coordinator.lan_send.await_count == 3
        coordinator.mqtt_send.assert_not_awaited()

    async def test_without_lan_the_whole_map_goes_over_mqtt(self, coordinator, sleep) -> None:
        coordinator._lan_devices = {}

        await async_upload_bulb_positions_handler(MagicMock(), _call(_grid(), normalize=False))

        coordinator.lan_send.assert_not_awaited()
        assert [call.args[4] for call in coordinator.mqtt_send.await_args_list] == [
            [frame] for frame in positions.position_map_frames([tuple(p) for p in _grid()], bulb_count=300)
        ]

    async def test_the_raw_option_gates_the_upload(self, coordinator, sleep, monkeypatch) -> None:
        coordinator.config_entry.options = {}
        monkeypatch.setattr(mqtt_raw_write, "async_send_frames", REAL_MQTT_SEND)

        with pytest.raises(HomeAssistantError) as excinfo:
            await async_upload_bulb_positions_handler(MagicMock(), _call(_grid()))

        assert excinfo.value.translation_placeholders == {"device": "Tree", "sent": "0", "total": "113"}
        coordinator.lan_send.assert_not_awaited()

    async def test_two_uploads_do_not_interleave(self, coordinator, sleep) -> None:
        first = _grid()
        second = [[x, y, 7] for x, y, _z in _grid()]

        await asyncio.gather(
            async_upload_bulb_positions_handler(MagicMock(), _call(first, normalize=False)),
            async_upload_bulb_positions_handler(MagicMock(), _call(second, normalize=False)),
        )

        frames_a = positions.position_map_frames([tuple(p) for p in first], bulb_count=300)
        frames_b = positions.position_map_frames([tuple(p) for p in second], bulb_count=300)
        assert _lan_frames(coordinator) in (frames_a + frames_b, frames_b + frames_a)

    async def test_a_paint_waits_for_a_running_upload(self, coordinator, sleep) -> None:
        paint_call = SimpleNamespace(data=SERVICE_PAINT_BULBS_SCHEMA({"device_id": DEVICE_ID}))

        await asyncio.gather(
            async_upload_bulb_positions_handler(MagicMock(), _call(_grid(), normalize=False)),
            async_paint_bulbs_handler(MagicMock(), paint_call),
        )

        messages = [call.args[3] for call in coordinator.lan_send.await_args_list]
        assert len(messages) == 114
        assert all(message[0][0] == 0xA4 for message in messages[:113])
        assert messages[113] == paint.paint_frames([], bulb_count=300)

    async def test_frames_wait_for_a_running_upload_to_the_same_device_only(self, coordinator, sleep) -> None:
        profile = profile_for("H80C5")
        coordinator._lan_devices[OTHER_ID] = SimpleNamespace(ip="other")
        held = asyncio.Event()
        release = asyncio.Event()

        async def _send(_coordinator, device_id, _ip, _frames, **_kwargs) -> bool:
            if device_id == DEVICE_ID and coordinator.lan_send.await_count == 5:
                held.set()
                await release.wait()
            return True

        coordinator.lan_send.side_effect = _send
        upload = asyncio.ensure_future(async_upload_bulb_positions_handler(MagicMock(), _call(_grid())))
        await held.wait()
        same = asyncio.ensure_future(raw_router.async_route_frames(coordinator, DEVICE_ID, "H80C5", profile, [FRAME]))
        for _ in range(5):
            await asyncio.sleep(0)

        assert await raw_router.async_route_frames(coordinator, OTHER_ID, "H80C5", profile, [FRAME]) is True
        assert not same.done()
        release.set()
        await asyncio.gather(upload, same)

        sends = [(call.args[1], call.args[3]) for call in coordinator.lan_send.await_args_list]
        own = [message for device_id, message in sends if device_id == DEVICE_ID]
        assert sends[5] == (OTHER_ID, [FRAME])
        assert len(own) == 114
        assert all(message[0][0] == 0xA4 for message in own[:113])
        assert own[113] == [FRAME]

    async def test_an_empty_upload_sends_nothing(self, coordinator) -> None:
        sent = await raw_router.async_route_upload(
            coordinator, DEVICE_ID, "H80C5", MagicMock(), [], gap_seconds=0.0, what="nothing"
        )
        assert sent == 0
        coordinator.lan_send.assert_not_awaited()

    async def test_a_cancelled_upload_logs_how_far_it_got(self, coordinator, sleep, caplog) -> None:
        sent = asyncio.Event()

        async def _send(*_args, **_kwargs) -> bool:
            if coordinator.lan_send.await_count == 5:
                sent.set()
            return True

        coordinator.lan_send.side_effect = _send
        task = asyncio.ensure_future(async_upload_bulb_positions_handler(MagicMock(), _call(_grid())))
        await sent.wait()
        task.cancel()
        with caplog.at_level(logging.WARNING), pytest.raises(asyncio.CancelledError):
            await task

        assert "bulb position map to AA:BB:CC:DD:EE:FF:00:11 was cancelled after 5 of 113 messages" in caplog.text


class TestSettingsSweep:
    """The settings sweep against a single H80C5 with raw writes on."""

    @pytest.fixture(autouse=True)
    def _single_device(self, coordinator) -> None:
        coordinator.devices[DEVICE_ID].is_group = False

    async def test_the_sweep_sends_one_query_per_setting(self, coordinator) -> None:
        assert await raw_status.async_query_settings(coordinator, DEVICE_ID) == 2
        assert coordinator.mqtt_send.await_count == 2

    async def test_the_sweep_skips_a_device_mid_upload(self, coordinator) -> None:
        async with raw_router.upload_lock(coordinator, DEVICE_ID):
            assert await raw_status.async_query_settings(coordinator, DEVICE_ID) == 0

        coordinator.mqtt_send.assert_not_awaited()


class TestValidation:
    async def test_wrong_count(self, coordinator, sleep) -> None:
        with pytest.raises(ServiceValidationError) as excinfo:
            await async_upload_bulb_positions_handler(MagicMock(), _call(_grid(299)))

        assert excinfo.value.translation_key == "bulb_position_count"
        assert excinfo.value.translation_placeholders == {"device": "Tree", "count": "299", "expected": "300"}
        coordinator.lan_send.assert_not_awaited()

    @pytest.mark.parametrize(
        "bad",
        [["a", 0, 0], [0, 0], [0, True, 0], [0, float("nan"), 0], 5, {"x": 0}],
        ids=["string", "pair", "bool", "nan", "scalar", "mapping"],
    )
    async def test_non_numeric_point(self, coordinator, sleep, bad) -> None:
        points = _grid()
        points[7] = bad

        with pytest.raises(ServiceValidationError) as excinfo:
            await async_upload_bulb_positions_handler(MagicMock(), _call(points))

        assert excinfo.value.translation_key == "invalid_bulb_positions"
        assert "position 7" in excinfo.value.translation_placeholders["reason"]
        coordinator.lan_send.assert_not_awaited()

    @pytest.mark.parametrize("bad", [[0, 1001, 0], [0, -1, 0], [0, 2.5, 0]], ids=["above-max", "negative", "fraction"])
    async def test_out_of_range_without_normalizing(self, coordinator, sleep, bad) -> None:
        points = _grid()
        points[3] = bad

        with pytest.raises(ServiceValidationError) as excinfo:
            await async_upload_bulb_positions_handler(MagicMock(), _call(points, normalize=False))

        assert excinfo.value.translation_key == "invalid_bulb_positions"
        assert "point 3" in excinfo.value.translation_placeholders["reason"]
        coordinator.lan_send.assert_not_awaited()

    async def test_unsupported_device(self, coordinator, sleep) -> None:
        coordinator.devices[DEVICE_ID].sku = "H60B0"

        with pytest.raises(ServiceValidationError) as excinfo:
            await async_upload_bulb_positions_handler(MagicMock(), _call(_grid()))

        assert excinfo.value.translation_key == "bulb_positions_unsupported"
        coordinator.lan_send.assert_not_awaited()

    async def test_device_without_a_profile(self, coordinator, sleep) -> None:
        coordinator.devices[DEVICE_ID].sku = "H6199"

        with pytest.raises(ServiceValidationError) as excinfo:
            await async_upload_bulb_positions_handler(MagicMock(), _call(_grid()))

        assert excinfo.value.translation_key == "bulb_positions_unsupported"

    async def test_unknown_device(self, monkeypatch) -> None:
        monkeypatch.setattr(LOOKUP, lambda hass, raw: None)

        with pytest.raises(ServiceValidationError) as excinfo:
            await async_upload_bulb_positions_handler(MagicMock(), _call(_grid()))

        assert excinfo.value.translation_key == "device_not_found"


class TestRegistration:
    async def test_registered_service_delegates_to_handler(self, hass, coordinator, sleep) -> None:
        async_setup_services(hass)

        await hass.services.async_call(
            DOMAIN,
            SERVICE_UPLOAD_BULB_POSITIONS,
            {"device_id": DEVICE_ID, "positions": _grid()},
            blocking=True,
        )

        assert coordinator.lan_send.await_count == 113

    async def test_non_admin_users_cannot_upload(self, hass, hass_read_only_user, coordinator, sleep) -> None:
        async_setup_services(hass)

        with pytest.raises(Unauthorized):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_UPLOAD_BULB_POSITIONS,
                {"device_id": DEVICE_ID, "positions": _grid()},
                blocking=True,
                context=Context(user_id=hass_read_only_user.id),
            )

        coordinator.lan_send.assert_not_awaited()
