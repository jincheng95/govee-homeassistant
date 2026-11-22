"""Device settings of the H60B0 uplighter over raw frames (fork feature).

1. Power-off memory: the frames, the readback, the select and its option gate.

Device ids are the suite's synthetic ones.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import State
from homeassistant.exceptions import ServiceValidationError

from custom_components.govee import raw_status
from custom_components.govee.api import raw_router
from custom_components.govee.api.protocol import Capability, FrameError, GoveeCodec, get_profile
from custom_components.govee.const import CONF_ENABLE_LAN_RAW_WRITE, SUFFIX_POWER_OFF_MEMORY
from custom_components.govee.models import GoveeDevice, GoveeDeviceState
from custom_components.govee.models.device import DEVICE_TYPE_LIGHT
from custom_components.govee.platforms.device_settings import (
    GoveePowerOffMemorySelect,
    async_device_setting_select_entities,
    power_off_memory_options,
)

DEVICE_ID = "AA:BB:CC:DD:EE:FF:60:B0"
SKU = "H60B0"

MEMORY_LAST_STATE = bytes([0x33, 0x41, 0x02, 0x00] + [0x00] * 15 + [0x70])
MEMORY_ON = bytes([0x33, 0x41, 0x02, 0x01] + [0x00] * 15 + [0x71])
MEMORY_STAY_OFF = bytes([0x33, 0x41, 0x02, 0x02] + [0x00] * 15 + [0x72])
MEMORY_LAST_STATE_REPLY = bytes([0xAA, 0x41, 0x02, 0x00] + [0x00] * 15 + [0xE9])


def _frame(*body: int) -> bytes:
    """Pad ``body`` to 19 bytes and append the XOR checksum."""
    padded = bytes(body) + bytes(19 - len(body))
    checksum = 0
    for byte in padded:
        checksum ^= byte
    return padded + bytes([checksum])


def _codec() -> GoveeCodec:
    return GoveeCodec(get_profile(SKU))


def _light(sku: str = SKU) -> GoveeDevice:
    return GoveeDevice(device_id=DEVICE_ID, sku=sku, name="Uplighter", device_type=DEVICE_TYPE_LIGHT, capabilities=())


def _coordinator() -> Any:
    coordinator = MagicMock()
    coordinator.config_entry.options = {CONF_ENABLE_LAN_RAW_WRITE: True}
    coordinator.devices = {DEVICE_ID: _light()}
    coordinator.last_update_success = True
    coordinator.get_state = MagicMock(return_value=GoveeDeviceState.create_empty(DEVICE_ID))
    return coordinator


def _memory_select(coordinator: Any | None = None) -> GoveePowerOffMemorySelect:
    entry = MagicMock()
    entry.options = {CONF_ENABLE_LAN_RAW_WRITE: True}
    (entity,) = async_device_setting_select_entities(coordinator or _coordinator(), entry)
    assert isinstance(entity, GoveePowerOffMemorySelect)
    entity.hass = MagicMock()
    entity.async_write_ha_state = MagicMock()
    return entity


# ==============================================================================
# 1. Power-off memory
# ==============================================================================


class TestPowerOffMemoryFrame:
    @pytest.mark.parametrize(
        ("behaviour", "expected"),
        [(0, MEMORY_LAST_STATE), (1, MEMORY_ON), (2, MEMORY_STAY_OFF)],
    )
    def test_frames_are_byte_exact(self, behaviour: int, expected: bytes) -> None:
        assert _codec().power_off_memory(behaviour) == expected

    def test_every_behaviour_is_offered(self) -> None:
        assert _codec().power_off_memory_behaviours() == (0, 1, 2)

    @pytest.mark.parametrize("behaviour", [-1, 3])
    def test_other_behaviours_are_refused(self, behaviour: int) -> None:
        with pytest.raises(FrameError):
            _codec().power_off_memory(behaviour)

    def test_the_setting_is_queried(self) -> None:
        assert raw_router.supports_setting(SKU, Capability.POWER_OFF_MEMORY) is True


class TestPowerOffMemorySelect:
    def test_shape(self) -> None:
        entity = _memory_select()
        assert entity.unique_id == f"{DEVICE_ID}{SUFFIX_POWER_OFF_MEMORY}"
        assert entity.translation_key == "power_off_memory"
        assert entity.options == ["last_state", "on", "off"]
        assert entity.current_option is None

    def test_options_follow_the_profile(self) -> None:
        assert power_off_memory_options("H80C5") == ["last_state", "on", "off"]
        assert power_off_memory_options("H6076") == []
        assert power_off_memory_options("H0000") == []

    async def test_an_unknown_option_is_a_translated_error(self) -> None:
        entity = _memory_select()
        with patch.object(raw_router, "async_route_frames", AsyncMock(return_value=True)) as route:
            with pytest.raises(ServiceValidationError) as err:
                await entity.async_select_option("bright")
        assert err.value.translation_key == "unknown_option"
        assert err.value.translation_placeholders == {"option": "bright", "device": "Uplighter"}
        route.assert_not_awaited()

    async def test_on_writes_its_byte(self) -> None:
        entity = _memory_select()
        with patch.object(raw_router, "async_route_frames", AsyncMock(return_value=True)) as route:
            await entity.async_select_option("on")
        assert list(route.await_args.args[4]) == [MEMORY_ON]
        assert entity.current_option == "on"

    def test_the_reported_option_is_shown(self) -> None:
        coordinator = _coordinator()
        entity = _memory_select(coordinator)
        raw_status.apply_raw_status(coordinator, _light(), [MEMORY_LAST_STATE_REPLY])
        assert entity.current_option == "last_state"

    def test_a_reported_on_is_shown(self) -> None:
        coordinator = _coordinator()
        entity = _memory_select(coordinator)
        entity._option = "off"
        raw_status.apply_raw_status(coordinator, _light(), [_frame(0xAA, 0x41, 0x02, 0x01)])
        assert entity.current_option == "on"

    @pytest.mark.parametrize(("restored", "expected"), [("off", "off"), ("on", "on"), ("bright", None)])
    async def test_restores_only_an_offered_option(self, restored: str, expected: str | None) -> None:
        entity = _memory_select()
        entity.async_get_last_state = AsyncMock(return_value=State("select.x", restored))
        with patch("homeassistant.helpers.update_coordinator.CoordinatorEntity.async_added_to_hass", AsyncMock()):
            await entity.async_added_to_hass()
        assert entity.current_option == expected
