"""Local controls of the H80C5 string lights over raw frames (fork feature).

1. Colour temperature: the frame, the clamp, the raw route and the
   coordinator tier that tries it before the cloud.

Device ids are the suite's synthetic ones.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.util import color as color_util

from custom_components.govee.api import raw_router
from custom_components.govee.api.protocol import GoveeCodec, UnsupportedCapabilityError, get_profile
from custom_components.govee.const import CONF_ENABLE_LAN_RAW_WRITE
from custom_components.govee.models import ColorTempCommand, GoveeDevice, GoveeDeviceState
from custom_components.govee.models.device import DEVICE_TYPE_LIGHT

DEVICE_ID = "AA:BB:CC:DD:EE:FF:60:B0"
SKU = "H80C5"


def _frame(*body: int) -> bytes:
    """A 20-byte frame: ``body`` zero-padded to 19 bytes plus the XOR checksum."""
    padded = bytes(body) + bytes(19 - len(body))
    checksum = 0
    for byte in padded:
        checksum ^= byte
    return padded + bytes([checksum])


def _device(*, sku: str = SKU, segments: int = 10, is_group: bool = False) -> Any:
    return SimpleNamespace(device_id=DEVICE_ID, sku=sku, segment_count=segments, is_group=is_group)


def _raw_coordinator(*, raw: bool = True) -> Any:
    coordinator = MagicMock()
    coordinator.config_entry.options = {CONF_ENABLE_LAN_RAW_WRITE: raw}
    return coordinator


# ==============================================================================
# 1. Colour temperature
# ==============================================================================


class TestColorTempFrame:
    @pytest.mark.parametrize(
        ("kelvin", "white", "expected"),
        [
            (
                2700,
                (0xFF, 0xAE, 0x54),
                _frame(0x33, 0x05, 0x15, 0x01, 0xFF, 0xFF, 0xFF, 0x0A, 0x8C, 0xFF, 0xAE, 0x54, 0xFF, 0x03),
            ),
            (
                9000,
                (0xD9, 0xE1, 0xFF),
                _frame(0x33, 0x05, 0x15, 0x01, 0xFF, 0xFF, 0xFF, 0x23, 0x28, 0xD9, 0xE1, 0xFF, 0xFF, 0x03),
            ),
        ],
    )
    def test_whole_string_frame_is_byte_exact(self, kelvin: int, white: tuple[int, int, int], expected: bytes) -> None:
        assert GoveeCodec(get_profile(SKU)).segment_color_temp(kelvin, white) == expected

    def test_checksums_match_the_known_frames(self) -> None:
        codec = GoveeCodec(get_profile(SKU))
        assert codec.segment_color_temp(2700, (0xFF, 0xAE, 0x54))[19] == 0xA2
        assert codec.segment_color_temp(9000, (0xD9, 0xE1, 0xFF))[19] == 0xED

    def test_a_segment_selection_sets_the_mask(self) -> None:
        frame = GoveeCodec(get_profile(SKU)).segment_color_temp(5800, (0xFF, 0xF1, 0xE7), segments=[2, 9])
        assert frame[:14] == bytes([0x33, 0x05, 0x15, 0x01, 0xFF, 0xFF, 0xFF, 0x16, 0xA8, 0xFF, 0xF1, 0xE7, 0x04, 0x02])

    @pytest.mark.parametrize(("requested", "sent"), [(2000, 2700), (9500, 9000), (4000, 4000)])
    def test_kelvin_is_clamped_to_the_profile_range(self, requested: int, sent: int) -> None:
        frame = GoveeCodec(get_profile(SKU)).segment_color_temp(requested, (0, 0, 0))
        assert int.from_bytes(frame[7:9], "big") == sent

    def test_the_profile_range(self) -> None:
        kelvin = get_profile(SKU).kelvin
        assert (kelvin.minimum, kelvin.maximum) == (2700, 9000)

    @pytest.mark.parametrize("sku", ["H6046", "H6076"])
    def test_other_segment_skus_do_not_encode_it(self, sku: str) -> None:
        with pytest.raises(UnsupportedCapabilityError):
            GoveeCodec(get_profile(sku)).segment_color_temp(4000, (0, 0, 0))


class TestColorTempRoute:
    async def test_sends_the_clamped_kelvin_with_its_white_point(self) -> None:
        coordinator = _raw_coordinator()
        with patch.object(raw_router, "async_route_frames", AsyncMock(return_value=True)) as route:
            sent = await raw_router.async_color_temp(coordinator, _device(), 2000)

        assert sent == 2700
        red, green, blue = color_util.color_temperature_to_rgb(2700)
        expected = GoveeCodec(get_profile(SKU)).segment_color_temp(2700, (round(red), round(green), round(blue)))
        args = route.await_args.args
        assert args[:4] == (coordinator, DEVICE_ID, SKU, get_profile(SKU))
        assert list(args[4]) == [expected]

    @pytest.mark.parametrize(
        ("device", "raw"),
        [
            (_device(sku="H6076", segments=7), True),
            (_device(sku="H60B0", segments=8), True),
            (_device(sku="H0000"), True),
            (_device(is_group=True), True),
            (_device(segments=8), True),
            (_device(), False),
        ],
    )
    async def test_declines_without_sending(self, device: Any, raw: bool) -> None:
        with patch.object(raw_router, "async_route_frames", AsyncMock(return_value=True)) as route:
            assert await raw_router.async_color_temp(_raw_coordinator(raw=raw), device, 4000) is None
        route.assert_not_awaited()

    async def test_no_tier_taking_the_frame_is_none(self) -> None:
        with patch.object(raw_router, "async_route_frames", AsyncMock(return_value=False)):
            assert await raw_router.async_color_temp(_raw_coordinator(), _device(), 4000) is None


def _coordinator() -> Any:
    import custom_components.govee.coordinator as coord_mod

    config_entry = MagicMock()
    config_entry.entry_id = "test_entry"
    config_entry.options = {}
    coord = coord_mod.GoveeCoordinator(
        hass=MagicMock(),
        config_entry=config_entry,
        api_client=MagicMock(),
        iot_credentials=None,
        poll_interval=60,
    )
    coord._devices[DEVICE_ID] = GoveeDevice(
        device_id=DEVICE_ID, sku=SKU, name="String lights", device_type=DEVICE_TYPE_LIGHT, capabilities=()
    )
    coord._states[DEVICE_ID] = GoveeDeviceState.create_empty(DEVICE_ID)
    coord.async_set_updated_data = MagicMock()
    coord._api_client.control_device = AsyncMock(return_value=True)
    return coord


class TestColorTempTier:
    async def test_a_raw_send_skips_the_cloud(self) -> None:
        coord = _coordinator()
        with patch("custom_components.govee.coordinator.async_raw_color_temp", AsyncMock(return_value=2700)) as raw:
            assert await coord.async_control_device(DEVICE_ID, ColorTempCommand(kelvin=2000)) is True

        raw.assert_awaited_once_with(coord, coord._devices[DEVICE_ID], 2000)
        coord._api_client.control_device.assert_not_awaited()
        assert coord._states[DEVICE_ID].color_temp_kelvin == 2700
        coord.async_set_updated_data.assert_called()

    async def test_no_raw_send_falls_through_to_rest(self) -> None:
        coord = _coordinator()
        with patch("custom_components.govee.coordinator.async_raw_color_temp", AsyncMock(return_value=None)):
            assert await coord.async_control_device(DEVICE_ID, ColorTempCommand(kelvin=4000)) is True

        coord._api_client.control_device.assert_awaited_once()
