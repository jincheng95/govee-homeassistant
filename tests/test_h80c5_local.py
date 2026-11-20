"""Local controls of the H80C5 string lights over raw frames (fork feature).

1. Colour temperature: the frame, the clamp, the raw route and the
   coordinator tier that tries it before the cloud.
2. Segment gradient: the frame, the raw setting route and the blending
   switch that takes it before the cloud toggle.
3. Power-off memory: the frame, the select, its option gate and cleanup.
4. Power push: the decoder, the state update and the MQTT raw-frame hook.
5. Settings readback: the gradient and power-off memory replies, the store
   the entities read, and the queries that ride every status request.

Device ids are the suite's synthetic ones.
"""

from __future__ import annotations

import base64
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import State
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import color as color_util

from custom_components.govee import _async_cleanup_orphaned_entities, raw_status

from custom_components.govee.api import raw_router
from custom_components.govee.api.protocol import (
    FrameError,
    GoveeCodec,
    StatusReport,
    UnsupportedCapabilityError,
    decode_status,
    get_profile,
)
from custom_components.govee.const import CONF_ENABLE_LAN_RAW_WRITE, SUFFIX_POWER_OFF_MEMORY
from custom_components.govee.api.protocol import Capability
from custom_components.govee.models import ColorTempCommand, GoveeDevice, GoveeDeviceState, ToggleCommand
from custom_components.govee.models.device import DEVICE_TYPE_LIGHT, INSTANCE_GRADUAL_ON
from custom_components.govee.platforms.device_settings import (
    GoveePowerOffMemorySelect,
    async_device_setting_select_entities,
)
from custom_components.govee.switch import GoveeSegmentBlendingSwitchEntity
from tests.test_cov_init import _coordinator as _cleanup_coordinator
from tests.test_cov_init import _entry as _cleanup_entry
from tests.test_cov_init import _seed

DEVICE_ID = "AA:BB:CC:DD:EE:FF:60:B0"
SKU = "H80C5"


def _frame(*body: int) -> bytes:
    """Pad ``body`` to 19 bytes and append the XOR checksum."""
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


# ==============================================================================
# 2. Segment gradient
# ==============================================================================


class TestGradientFrame:
    @pytest.mark.parametrize(("on", "expected"), [(False, _frame(0x33, 0xA3, 0x00)), (True, _frame(0x33, 0xA3, 0x01))])
    def test_frame_is_byte_exact(self, on: bool, expected: bytes) -> None:
        assert GoveeCodec(get_profile(SKU)).gradient(on) == expected

    def test_checksum_matches_the_known_frame(self) -> None:
        assert GoveeCodec(get_profile(SKU)).gradient(False)[19] == 0x90

    @pytest.mark.parametrize("sku", ["H6046", "H6076", "H60B0"])
    def test_other_skus_do_not_encode_it(self, sku: str) -> None:
        with pytest.raises(UnsupportedCapabilityError):
            GoveeCodec(get_profile(sku)).gradient(True)


class TestSettingRoute:
    async def test_routes_the_built_frame(self) -> None:
        coordinator = _raw_coordinator()
        with patch.object(raw_router, "async_route_frames", AsyncMock(return_value=True)) as route:
            assert await raw_router.async_device_setting(
                coordinator, _device(), Capability.GRADIENT, lambda codec: codec.gradient(True)
            )
        args = route.await_args.args
        assert args[:4] == (coordinator, DEVICE_ID, SKU, get_profile(SKU))
        assert list(args[4]) == [_frame(0x33, 0xA3, 0x01)]

    @pytest.mark.parametrize(
        ("device", "raw"),
        [
            (_device(sku="H6076", segments=7), True),
            (_device(sku="H0000"), True),
            (_device(is_group=True), True),
            (_device(), False),
        ],
    )
    async def test_declines_without_sending(self, device: Any, raw: bool) -> None:
        with patch.object(raw_router, "async_route_frames", AsyncMock(return_value=True)) as route:
            assert not await raw_router.async_device_setting(
                _raw_coordinator(raw=raw), device, Capability.GRADIENT, lambda codec: codec.gradient(True)
            )
        route.assert_not_awaited()

    def test_supports_setting(self) -> None:
        assert raw_router.supports_setting(SKU, Capability.GRADIENT) is True
        assert raw_router.supports_setting("H6076", Capability.GRADIENT) is False
        assert raw_router.supports_setting("H0000", Capability.GRADIENT) is False


def _blending_switch() -> tuple[GoveeSegmentBlendingSwitchEntity, Any]:
    coordinator = _raw_coordinator()
    coordinator.async_control_device = AsyncMock(return_value=True)
    device = GoveeDevice(
        device_id=DEVICE_ID, sku=SKU, name="String lights", device_type=DEVICE_TYPE_LIGHT, capabilities=()
    )
    entity = GoveeSegmentBlendingSwitchEntity(coordinator, device)
    entity.async_write_ha_state = MagicMock()
    return entity, coordinator


class TestBlendingSwitch:
    @pytest.mark.parametrize("on", [True, False])
    async def test_a_raw_write_skips_the_cloud_toggle(self, on: bool) -> None:
        entity, coordinator = _blending_switch()
        entity._is_on = not on
        with patch.object(raw_router, "async_route_frames", AsyncMock(return_value=True)) as route:
            await (entity.async_turn_on() if on else entity.async_turn_off())

        assert list(route.await_args.args[4]) == [_frame(0x33, 0xA3, int(on))]
        coordinator.async_control_device.assert_not_awaited()
        assert entity.is_on is on
        entity.async_write_ha_state.assert_called_once()

    async def test_no_raw_tier_falls_back_to_the_cloud_toggle(self) -> None:
        entity, coordinator = _blending_switch()
        with patch.object(raw_router, "async_route_frames", AsyncMock(return_value=False)):
            await entity.async_turn_on()

        coordinator.async_control_device.assert_awaited_once_with(
            DEVICE_ID, ToggleCommand(toggle_instance=INSTANCE_GRADUAL_ON, enabled=True)
        )
        assert entity.is_on is True


# ==============================================================================
# 3. Power-off memory
# ==============================================================================


class TestPowerOffMemoryFrame:
    @pytest.mark.parametrize(
        ("behaviour", "expected"),
        [
            (0, _frame(0x33, 0x41, 0x02, 0x00)),
            (1, _frame(0x33, 0x41, 0x02, 0x01)),
            (2, _frame(0x33, 0x41, 0x02, 0x02)),
        ],
    )
    def test_frame_is_byte_exact(self, behaviour: int, expected: bytes) -> None:
        assert GoveeCodec(get_profile(SKU)).power_off_memory(behaviour) == expected

    def test_checksums_match_the_known_frames(self) -> None:
        codec = GoveeCodec(get_profile(SKU))
        assert [codec.power_off_memory(n)[19] for n in (0, 1, 2)] == [0x70, 0x71, 0x72]

    @pytest.mark.parametrize("behaviour", [-1, 3, 255])
    def test_an_unknown_behaviour_is_refused(self, behaviour: int) -> None:
        with pytest.raises(FrameError):
            GoveeCodec(get_profile(SKU)).power_off_memory(behaviour)

    def test_other_skus_do_not_encode_it(self) -> None:
        with pytest.raises(UnsupportedCapabilityError):
            GoveeCodec(get_profile("H6076")).power_off_memory(0)


def _light(sku: str = SKU, device_id: str = DEVICE_ID, *, is_group: bool = False) -> GoveeDevice:
    return GoveeDevice(
        device_id=device_id,
        sku=sku,
        name="String lights",
        device_type=DEVICE_TYPE_LIGHT,
        capabilities=(),
        is_group=is_group,
    )


def _settings_coordinator(*devices: GoveeDevice) -> Any:
    coordinator = _raw_coordinator()
    devices = devices or (_light(),)
    coordinator.devices = {device.device_id: device for device in devices}
    coordinator.last_update_success = True
    coordinator.get_state = MagicMock(return_value=None)
    return coordinator


def _options_entry(*, raw: Any = True) -> Any:
    entry = MagicMock()
    entry.options = {CONF_ENABLE_LAN_RAW_WRITE: raw}
    return entry


def _memory_select(coordinator: Any | None = None) -> GoveePowerOffMemorySelect:
    coordinator = coordinator or _settings_coordinator()
    (entity,) = async_device_setting_select_entities(coordinator, _options_entry())
    assert isinstance(entity, GoveePowerOffMemorySelect)
    entity.hass = MagicMock()
    entity.async_write_ha_state = MagicMock()
    return entity


class TestPowerOffMemorySelect:
    def test_shape(self) -> None:
        entity = _memory_select()
        assert entity.unique_id == f"{DEVICE_ID}{SUFFIX_POWER_OFF_MEMORY}"
        assert entity.translation_key == "power_off_memory"
        assert entity.entity_category == "config"
        assert entity.options == ["last_state", "on", "off"]
        assert entity.current_option is None

    def test_only_profiles_that_encode_it_get_one(self) -> None:
        coordinator = _settings_coordinator(
            _light("H6076", "AA:BB:CC:DD:EE:FF:60:76"),
            _light("H0000", "AA:BB:CC:DD:EE:FF:60:B1"),
            _light(SKU, "12345", is_group=True),
        )
        assert async_device_setting_select_entities(coordinator, _options_entry()) == []

    @pytest.mark.parametrize("raw", [False, None, "yes"])
    def test_the_raw_option_gates_it(self, raw: Any) -> None:
        assert async_device_setting_select_entities(_settings_coordinator(), _options_entry(raw=raw)) == []

    @pytest.mark.parametrize(("option", "byte"), [("last_state", 0), ("on", 1), ("off", 2)])
    async def test_an_option_writes_its_byte(self, option: str, byte: int) -> None:
        entity = _memory_select()
        with patch.object(raw_router, "async_route_frames", AsyncMock(return_value=True)) as route:
            await entity.async_select_option(option)

        assert list(route.await_args.args[4]) == [_frame(0x33, 0x41, 0x02, byte)]
        assert entity.current_option == option
        entity.async_write_ha_state.assert_called_once()

    async def test_an_unknown_option_is_a_translated_error(self) -> None:
        entity = _memory_select()
        with pytest.raises(ServiceValidationError) as err:
            await entity.async_select_option("sometimes")
        assert err.value.translation_key == "unknown_option"
        assert err.value.translation_placeholders == {"option": "sometimes", "device": "String lights"}

    async def test_a_refused_write_keeps_the_option(self) -> None:
        entity = _memory_select()
        entity._option = "on"
        with patch.object(raw_router, "async_route_frames", AsyncMock(return_value=False)):
            with pytest.raises(HomeAssistantError) as err:
                await entity.async_select_option("off")
        assert err.value.translation_key == "command_failed"
        assert entity.current_option == "on"
        entity.async_write_ha_state.assert_not_called()

    @pytest.mark.parametrize(("restored", "expected"), [("off", "off"), ("unknown", None)])
    async def test_restores_the_last_option(self, restored: str, expected: str | None) -> None:
        entity = _memory_select()
        entity.async_get_last_state = AsyncMock(return_value=State("select.x", restored))
        with patch("homeassistant.helpers.update_coordinator.CoordinatorEntity.async_added_to_hass", AsyncMock()):
            await entity.async_added_to_hass()
        assert entity.current_option == expected

    async def test_nothing_to_restore(self) -> None:
        entity = _memory_select()
        entity.async_get_last_state = AsyncMock(return_value=None)
        with patch("homeassistant.helpers.update_coordinator.CoordinatorEntity.async_added_to_hass", AsyncMock()):
            await entity.async_added_to_hass()
        assert entity.current_option is None


@pytest.mark.parametrize(("raw", "kept"), [(False, False), (True, True)])
async def test_cleanup_follows_the_raw_option(hass, mock_light_device, raw: bool, kept: bool) -> None:
    device_id = mock_light_device.device_id
    entry = _cleanup_entry(hass, **{CONF_ENABLE_LAN_RAW_WRITE: raw})
    registry = er.async_get(hass)
    ids = _seed(registry, entry, ("light", device_id), ("select", f"{device_id}{SUFFIX_POWER_OFF_MEMORY}"))

    await _async_cleanup_orphaned_entities(hass, entry, _cleanup_coordinator([device_id]))

    assert registry.async_get(ids[device_id]) is not None
    assert (registry.async_get(ids[f"{device_id}{SUFFIX_POWER_OFF_MEMORY}"]) is not None) is kept


# ==============================================================================
# 4. Power push
# ==============================================================================

POWER_OFF = _frame(0xEE, 0x30, 0x01, 0x00)
POWER_ON = _frame(0xEE, 0x30, 0x01, 0x01, 0x01)


def _corrupt(frame: bytes) -> bytes:
    return frame[:-1] + bytes([frame[-1] ^ 0xFF])


class TestPowerDecode:
    def test_the_known_frames_carry_their_checksum(self) -> None:
        assert POWER_OFF[19] == 0xDF and POWER_ON[19] == 0xDF

    @pytest.mark.parametrize(("frames", "power"), [([POWER_OFF], False), ([POWER_ON], True), ([], None)])
    def test_power(self, frames: list[bytes], power: bool | None) -> None:
        assert decode_status(frames) == StatusReport(power=power)

    def test_the_last_report_wins(self) -> None:
        assert decode_status([POWER_OFF, POWER_ON]).power is True

    @pytest.mark.parametrize(
        "frame",
        [_corrupt(POWER_ON), POWER_ON[:19], _frame(0xEE, 0x30, 0x01, 0x02), _frame(0xEE, 0x30, 0x02, 0x01)],
    )
    def test_malformed_or_other_frames_are_skipped(self, frame: bytes) -> None:
        assert decode_status([frame]).power is None


def _state_coordinator(power: bool) -> Any:
    coordinator = MagicMock()
    state = GoveeDeviceState.create_empty(DEVICE_ID)
    state.power_state = power
    coordinator.get_state = MagicMock(return_value=state)
    coordinator.state = state
    return coordinator


class TestApplyPower:
    def test_a_change_is_applied(self) -> None:
        coordinator = _state_coordinator(False)
        assert raw_status.apply_raw_status(coordinator, _light(), [POWER_ON]) is True
        assert coordinator.state.power_state is True

    def test_the_same_power_is_no_change(self) -> None:
        coordinator = _state_coordinator(True)
        assert raw_status.apply_raw_status(coordinator, _light(), [POWER_ON]) is False

    @pytest.mark.parametrize(
        "device",
        [_light("H6076"), _light("H60B0"), _light("H0000"), _light(is_group=True)],
    )
    def test_skus_without_the_push_are_left_alone(self, device: GoveeDevice) -> None:
        coordinator = _state_coordinator(False)
        assert raw_status.apply_raw_status(coordinator, device, [POWER_ON]) is False
        assert coordinator.state.power_state is False

    def test_no_state_is_no_change(self) -> None:
        coordinator = MagicMock()
        coordinator.get_state = MagicMock(return_value=None)
        assert raw_status.apply_raw_status(coordinator, _light(), [POWER_ON]) is False


def _payload(*frames: bytes) -> dict[str, Any]:
    return {"op": {"command": [base64.b64encode(frame).decode() for frame in frames]}}


class TestRawFrameHook:
    def test_a_push_updates_power_and_notifies(self) -> None:
        coord = _coordinator()
        coord._on_mqtt_raw_frames(DEVICE_ID, _payload(POWER_ON))
        assert coord._states[DEVICE_ID].power_state is True
        coord.async_set_updated_data.assert_called_once_with(coord._states)

    def test_an_unchanged_power_does_not_notify(self) -> None:
        coord = _coordinator()
        coord._on_mqtt_raw_frames(DEVICE_ID, _payload(POWER_OFF))
        coord.async_set_updated_data.assert_not_called()

    def test_an_unknown_device_is_ignored(self) -> None:
        coord = _coordinator()
        coord._on_mqtt_raw_frames("AA:BB:CC:DD:EE:FF:60:B1", _payload(POWER_ON))
        coord.async_set_updated_data.assert_not_called()


# ==============================================================================
# 5. Settings readback
# ==============================================================================

GRADIENT_ON = _frame(0xAA, 0xA3, 0x01)
MEMORY_LAST_STATE = _frame(0xAA, 0x41, 0x02, 0x00)


class TestSettingsDecode:
    def test_the_known_replies_carry_their_checksum(self) -> None:
        assert GRADIENT_ON[19] == 0x08 and MEMORY_LAST_STATE[19] == 0xE9

    def test_replies(self) -> None:
        report = decode_status([GRADIENT_ON, MEMORY_LAST_STATE, _frame(0xAA, 0x41, 0x02, 0x02)])
        assert report == StatusReport(gradient=True, power_off_memory=2)

    def test_gradient_off(self) -> None:
        assert decode_status([_frame(0xAA, 0xA3, 0x00)]).gradient is False

    @pytest.mark.parametrize(
        "frame",
        [
            _frame(0x33, 0xA3, 0x01),
            _frame(0x33, 0x41, 0x00),
            _frame(0xAA, 0xA3, 0x02),
            _frame(0xAA, 0x41, 0x01, 0x00),
            _frame(0xAA, 0x41, 0x02, 0x03),
            _corrupt(GRADIENT_ON),
            _frame(0xAA, 0x05, 0x04, 0x0C, 0x47),
        ],
    )
    def test_acks_and_other_frames_are_skipped(self, frame: bytes) -> None:
        assert decode_status([frame]) == StatusReport()


class TestSettingsStore:
    def test_a_reply_is_stored_once(self) -> None:
        coordinator = _state_coordinator(False)
        assert raw_status.apply_raw_status(coordinator, _light(), [GRADIENT_ON, MEMORY_LAST_STATE]) is True
        assert raw_status.device_settings(coordinator, DEVICE_ID) == raw_status.DeviceSettings(True, 0)
        assert raw_status.apply_raw_status(coordinator, _light(), [GRADIENT_ON]) is False

    def test_a_sku_without_the_setting_ignores_the_reply(self) -> None:
        coordinator = _state_coordinator(False)
        assert raw_status.apply_raw_status(coordinator, _light("H6076"), [GRADIENT_ON, MEMORY_LAST_STATE]) is False
        assert raw_status.device_settings(coordinator, DEVICE_ID) == raw_status.DeviceSettings()

    def test_a_write_is_recorded(self) -> None:
        coordinator = _state_coordinator(False)
        raw_status.record_settings(coordinator, DEVICE_ID, gradient=False)
        raw_status.record_settings(coordinator, DEVICE_ID, power_off_memory=1)
        assert raw_status.device_settings(coordinator, DEVICE_ID) == raw_status.DeviceSettings(False, 1)


class TestEntitiesFollowReports:
    def test_the_switch_shows_the_reported_gradient(self) -> None:
        entity, coordinator = _blending_switch()
        assert entity.is_on is False
        raw_status.apply_raw_status(_with_state(coordinator), _light(), [GRADIENT_ON])
        assert entity.is_on is True

    async def test_a_switch_write_replaces_the_report(self) -> None:
        entity, coordinator = _blending_switch()
        raw_status.apply_raw_status(_with_state(coordinator), _light(), [GRADIENT_ON])
        with patch.object(raw_router, "async_route_frames", AsyncMock(return_value=True)):
            await entity.async_turn_off()
        assert entity.is_on is False

    def test_the_select_shows_the_reported_option(self) -> None:
        coordinator = _settings_coordinator()
        entity = _memory_select(coordinator)
        entity._option = "on"
        raw_status.apply_raw_status(_with_state(coordinator), _light(), [_frame(0xAA, 0x41, 0x02, 0x02)])
        assert entity.current_option == "off"

    async def test_a_select_write_replaces_the_report(self) -> None:
        coordinator = _settings_coordinator()
        entity = _memory_select(coordinator)
        raw_status.apply_raw_status(_with_state(coordinator), _light(), [MEMORY_LAST_STATE])
        with patch.object(raw_router, "async_route_frames", AsyncMock(return_value=True)):
            await entity.async_select_option("on")
        assert entity.current_option == "on"


def _with_state(coordinator: Any) -> Any:
    coordinator.get_state = MagicMock(return_value=GoveeDeviceState.create_empty(DEVICE_ID))
    return coordinator


class TestSettingsQuery:
    async def test_one_query_per_setting(self) -> None:
        coordinator = _settings_coordinator()
        with patch.object(raw_status.mqtt_raw_write, "async_send_frames", AsyncMock(return_value=True)) as send:
            assert await raw_status.async_query_settings(coordinator, DEVICE_ID) == 2

        sent = [list(c.args[4]) for c in send.await_args_list]
        assert sent == [[_frame(0xAA, 0xA3)], [_frame(0xAA, 0x41)]]
        assert [frame[19] for (frame,) in sent] == [0x09, 0xEB]
        assert send.await_args_list[0].args[:4] == (coordinator, DEVICE_ID, SKU, get_profile(SKU))

    async def test_only_sent_queries_count(self) -> None:
        with patch.object(raw_status.mqtt_raw_write, "async_send_frames", AsyncMock(return_value=False)):
            assert await raw_status.async_query_settings(_settings_coordinator(), DEVICE_ID) == 0

    @pytest.mark.parametrize(
        ("device", "device_id"),
        [
            (_light("H6076"), DEVICE_ID),
            (_light("H0000"), DEVICE_ID),
            (_light(is_group=True), DEVICE_ID),
            (_light(), "AA:BB:CC:DD:EE:FF:60:B1"),
        ],
    )
    async def test_nothing_to_ask(self, device: GoveeDevice, device_id: str) -> None:
        coordinator = _settings_coordinator(device)
        with patch.object(raw_status.mqtt_raw_write, "async_send_frames", AsyncMock(return_value=True)) as send:
            assert await raw_status.async_query_settings(coordinator, device_id) == 0
        send.assert_not_awaited()


def _mqtt_coordinator() -> tuple[Any, Any]:
    coord = _coordinator()
    coord._device_topics = {DEVICE_ID: "GD/synthetic"}
    client = MagicMock()
    client.connected = True
    client.async_publish_status_query = AsyncMock(return_value=True)
    coord._mqtt_client = client
    return coord, client


@pytest.fixture
def no_spacing(monkeypatch: pytest.MonkeyPatch) -> None:
    import custom_components.govee.coordinator as coord_mod

    async def _sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(coord_mod.asyncio, "sleep", _sleep)


class TestQueriesRideStatusRequests:
    async def test_a_status_request_also_reads_the_settings(self, no_spacing: None) -> None:
        coord, client = _mqtt_coordinator()
        coord.status_query_possible = MagicMock(return_value=True)
        with patch("custom_components.govee.coordinator.async_query_raw_settings", AsyncMock()) as query:
            assert await coord.async_request_status(DEVICE_ID, user=True) is True
        client.async_publish_status_query.assert_awaited_once()
        query.assert_awaited_once_with(coord, DEVICE_ID)

    async def test_a_refused_status_request_reads_nothing(self, no_spacing: None) -> None:
        coord, _ = _mqtt_coordinator()
        coord.status_query_possible = MagicMock(return_value=False)
        with patch("custom_components.govee.coordinator.async_query_raw_settings", AsyncMock()) as query:
            assert await coord.async_request_status(DEVICE_ID, user=True) is False
        query.assert_not_awaited()

    async def test_the_sweep_reads_the_settings_per_device(self, no_spacing: None) -> None:
        coord, client = _mqtt_coordinator()
        with patch("custom_components.govee.coordinator.async_query_raw_settings", AsyncMock()) as query:
            await coord._poll_mqtt_status()
        client.async_publish_status_query.assert_awaited_once_with("GD/synthetic")
        query.assert_awaited_once_with(coord, DEVICE_ID)
