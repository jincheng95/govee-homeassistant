"""``govee.upload_bulb_positions`` validation and send path (fork feature).

The coordinator is mocked; the device lookup is monkeypatched as in
``test_services.py``. Point lists are synthetic.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError, Unauthorized

from custom_components.govee.api.protocol import positions
from custom_components.govee.const import DOMAIN
from custom_components.govee.services import (
    SERVICE_UPLOAD_BULB_POSITIONS,
    SERVICE_UPLOAD_BULB_POSITIONS_SCHEMA,
    async_setup_services,
    async_upload_bulb_positions_handler,
)

LOOKUP = "custom_components.govee.services._get_coordinator_for_device"
SLEEP = "custom_components.govee.services.asyncio.sleep"
DEVICE_ID = "AA:BB:CC:DD:EE:FF:00:11"


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
    coordinator.async_send_raw_ptreal = AsyncMock(return_value=True)
    monkeypatch.setattr(LOOKUP, lambda hass, raw: (coordinator, DEVICE_ID))
    return coordinator


@pytest.fixture
def sleep(monkeypatch) -> AsyncMock:
    mock = AsyncMock()
    monkeypatch.setattr(SLEEP, mock)
    return mock


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
        sent = [call.args for call in coordinator.async_send_raw_ptreal.await_args_list]
        assert sent == [(DEVICE_ID, frame) for frame in expected]
        assert len(sent) == 113
        assert sleep.await_count == 112
        sleep.assert_awaited_with(positions.MESSAGE_GAP_SECONDS)

    async def test_normalizes_by_default(self, coordinator, sleep) -> None:
        points = [[x / 10, y / 10 + 7, z / 10 - 3] for x, y, z in _grid()]

        await async_upload_bulb_positions_handler(MagicMock(), _call(points))

        normalized = positions.normalize_positions([tuple(point) for point in points])
        expected = positions.position_map_frames(normalized, bulb_count=300)
        assert [call.args[1] for call in coordinator.async_send_raw_ptreal.await_args_list] == expected

    async def test_integral_floats_pass_without_normalizing(self, coordinator, sleep) -> None:
        points = [[float(axis) for axis in point] for point in _grid()]

        await async_upload_bulb_positions_handler(MagicMock(), _call(points, normalize=False))

        assert coordinator.async_send_raw_ptreal.await_count == 113

    async def test_a_refused_send_stops_and_raises(self, coordinator, sleep) -> None:
        coordinator.async_send_raw_ptreal.side_effect = [True, True, False]

        with pytest.raises(HomeAssistantError) as excinfo:
            await async_upload_bulb_positions_handler(MagicMock(), _call(_grid()))

        assert excinfo.value.translation_key == "bulb_positions_failed"
        assert excinfo.value.translation_placeholders == {"device": "Tree", "sent": "2", "total": "113"}
        assert coordinator.async_send_raw_ptreal.await_count == 3


class TestValidation:
    async def test_wrong_count(self, coordinator, sleep) -> None:
        with pytest.raises(ServiceValidationError) as excinfo:
            await async_upload_bulb_positions_handler(MagicMock(), _call(_grid(299)))

        assert excinfo.value.translation_key == "bulb_position_count"
        assert excinfo.value.translation_placeholders == {"device": "Tree", "count": "299", "expected": "300"}
        coordinator.async_send_raw_ptreal.assert_not_awaited()

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
        coordinator.async_send_raw_ptreal.assert_not_awaited()

    @pytest.mark.parametrize("bad", [[0, 1001, 0], [0, -1, 0], [0, 2.5, 0]], ids=["above-max", "negative", "fraction"])
    async def test_out_of_range_without_normalizing(self, coordinator, sleep, bad) -> None:
        points = _grid()
        points[3] = bad

        with pytest.raises(ServiceValidationError) as excinfo:
            await async_upload_bulb_positions_handler(MagicMock(), _call(points, normalize=False))

        assert excinfo.value.translation_key == "invalid_bulb_positions"
        assert "point 3" in excinfo.value.translation_placeholders["reason"]
        coordinator.async_send_raw_ptreal.assert_not_awaited()

    async def test_unsupported_device(self, coordinator, sleep) -> None:
        coordinator.devices[DEVICE_ID].sku = "H60B0"

        with pytest.raises(ServiceValidationError) as excinfo:
            await async_upload_bulb_positions_handler(MagicMock(), _call(_grid()))

        assert excinfo.value.translation_key == "bulb_positions_unsupported"
        coordinator.async_send_raw_ptreal.assert_not_awaited()

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

        assert coordinator.async_send_raw_ptreal.await_count == 113

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

        coordinator.async_send_raw_ptreal.assert_not_awaited()
