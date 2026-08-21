"""Tests for user-defined segment-group light entities (roadmap 1.11, fork).

Organised around the properties the entity has to have, mirroring
``test_segment_readback.py``'s structure for the individual-segment entity:

1. Wiring — ``async_segment_group_entities`` builds one entity per group.
2. One frame per group — the H6046 BLE tier, two 5-segment groups.
3. Turn on/off — optimistic state, cloud fallback.
4. Readback aggregation — any-member-on, first-lit-member colour, the grace
   window, and no churn on agreement.
5. RestoreEntity.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.govee.api.protocol import get_profile
from custom_components.govee.api.segment_readback import SegmentReading
from custom_components.govee.const import (
    CONF_ENABLE_BLE_RAW_WRITE,
    CONF_ENABLE_LAN_RAW_WRITE,
    CONF_ENABLE_MQTT_CONTROL,
)
from custom_components.govee.models import GoveeCapability, GoveeDevice, SegmentColorCommand
from custom_components.govee.models.device import (
    CAPABILITY_ON_OFF,
    CAPABILITY_SEGMENT_COLOR,
    DEVICE_TYPE_LIGHT,
    INSTANCE_POWER,
    INSTANCE_SEGMENT_COLOR,
)
from custom_components.govee.platforms.segment_group import (
    READBACK_WRITE_GRACE_SECONDS,
    GoveeSegmentGroupEntity,
    async_segment_group_entities,
)

DEVICE_ID = "AA:BB:AA:BB:CC:11:22:33"  # synthetic; H6046-shaped
SKU = "H6046"
H6046_SEGMENTS = 10  # the profile's verified count — see test_segment_limit.py
LEFT = (0, 1, 2, 3, 4)
RIGHT = (5, 6, 7, 8, 9)


def _device(sku: str = SKU, segments: int = H6046_SEGMENTS) -> GoveeDevice:
    return GoveeDevice(
        device_id=DEVICE_ID,
        sku=sku,
        name="Light Bar",
        device_type=DEVICE_TYPE_LIGHT,
        capabilities=(
            GoveeCapability(type=CAPABILITY_ON_OFF, instance=INSTANCE_POWER, parameters={}),
            GoveeCapability(
                type=CAPABILITY_SEGMENT_COLOR,
                instance=INSTANCE_SEGMENT_COLOR,
                parameters={"fields": [{"fieldName": "segment", "elementRange": {"min": 0, "max": segments - 1}}]},
            ),
        ),
    )


def _coordinator(*, ble: bool = False) -> MagicMock:
    coordinator = MagicMock()
    coordinator.last_update_success = True
    coordinator.async_control_device = AsyncMock(return_value=True)
    coordinator.is_power_off_pending = MagicMock(return_value=False)
    coordinator.get_state = MagicMock(return_value=None)
    # Matches tests/test_ble_raw_write.py's `_coordinator` — the shape
    # `raw_write_enabled` / the BLE tier's own gate and routing read.
    coordinator._govee_zone_state_registry = None
    coordinator.config_entry.entry_id = "entry_segment_group"
    coordinator.config_entry.options = {
        CONF_ENABLE_BLE_RAW_WRITE: ble,
        CONF_ENABLE_LAN_RAW_WRITE: False,
        CONF_ENABLE_MQTT_CONTROL: False,
    }
    coordinator.mqtt_connected = False
    coordinator._lan_devices = {}
    coordinator._mqtt_client = None
    coordinator._ensure_device_topic = AsyncMock(return_value=None)
    return coordinator


def _group_entity(
    group_name: str = "Left",
    indices: tuple[int, ...] = LEFT,
    *,
    coordinator: MagicMock | None = None,
) -> GoveeSegmentGroupEntity:
    entity = GoveeSegmentGroupEntity(coordinator or _coordinator(), _device(), group_name, indices)
    entity.hass = MagicMock()
    entity.async_write_ha_state = MagicMock()
    return entity


class TestWiring:
    """``async_segment_group_entities`` builds one entity per configured group."""

    def test_one_entity_per_group_in_order(self):
        coordinator = _coordinator()
        entry = MagicMock()
        entry.options = {
            "segment_groups_by_device": {
                DEVICE_ID: {"Left": list(LEFT), "Right": list(RIGHT)},
            }
        }

        entities = async_segment_group_entities(coordinator, _device(), entry)

        assert [e._group_name for e in entities] == ["Left", "Right"]
        assert entities[0]._segment_indices == LEFT
        assert entities[1]._segment_indices == RIGHT
        assert all(isinstance(e, GoveeSegmentGroupEntity) for e in entities)

    def test_no_groups_saved_is_no_entities(self):
        coordinator = _coordinator()
        entry = MagicMock()
        entry.options = {}

        assert async_segment_group_entities(coordinator, _device(), entry) == []

    def test_entity_name_is_the_group_name_verbatim(self):
        entity = _group_entity("Left Bar")
        assert entity._attr_name == "Left Bar"

    def test_unique_id_uses_the_group_suffix(self):
        entity = _group_entity("Left Bar")
        assert entity._attr_unique_id == f"{DEVICE_ID}_segment_group_left_bar"


class TestOneFramePerGroup:
    """H6046 (BLE-only raw tier): each group paints as exactly one masked frame."""

    @pytest.mark.asyncio
    async def test_left_group_paints_one_frame_matching_the_codec(self):
        from custom_components.govee.api import ble_raw_write

        ble_raw_write._LINKS.clear()
        client = MagicMock()
        client.is_connected = True
        writes: list[bytes] = []

        async def _write(_uuid: str, frame: bytes, response: bool = True) -> None:
            writes.append(bytes(frame))

        client.write_gatt_char = AsyncMock(side_effect=_write)
        client.disconnect = AsyncMock()

        coordinator = _coordinator(ble=True)
        with (
            patch.object(ble_raw_write, "HAS_BLUETOOTH", True),
            patch.object(ble_raw_write, "_resolve", lambda coordinator, address: MagicMock(address=address)),
            patch.object(ble_raw_write, "close_stale_connections_by_address", AsyncMock()),
            patch.object(ble_raw_write, "establish_connection", AsyncMock(return_value=client)),
        ):
            entity = _group_entity("Left", LEFT, coordinator=coordinator)
            await entity.async_turn_on(rgb_color=(255, 0, 0))

        codec = get_profile(SKU)
        from custom_components.govee.api.protocol import GoveeCodec

        expected = GoveeCodec(codec).segment_color((255, 0, 0), segments=list(LEFT))

        assert writes == [expected]
        coordinator.async_control_device.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_right_group_masks_only_its_own_segments(self):
        from custom_components.govee.api import ble_raw_write

        ble_raw_write._LINKS.clear()
        client = MagicMock()
        client.is_connected = True
        writes: list[bytes] = []

        async def _write(_uuid: str, frame: bytes, response: bool = True) -> None:
            writes.append(bytes(frame))

        client.write_gatt_char = AsyncMock(side_effect=_write)
        client.disconnect = AsyncMock()

        coordinator = _coordinator(ble=True)

        with (
            patch.object(ble_raw_write, "HAS_BLUETOOTH", True),
            patch.object(ble_raw_write, "_resolve", lambda coordinator, address: MagicMock(address=address)),
            patch.object(ble_raw_write, "close_stale_connections_by_address", AsyncMock()),
            patch.object(ble_raw_write, "establish_connection", AsyncMock(return_value=client)),
        ):
            entity = _group_entity("Right", RIGHT, coordinator=coordinator)
            await entity.async_turn_on(rgb_color=(0, 255, 0))

        from custom_components.govee.api.protocol import GoveeCodec

        expected = GoveeCodec(get_profile(SKU)).segment_color((0, 255, 0), segments=list(RIGHT))

        assert len(writes) == 1
        assert writes[0] == expected
        # Byte-different from the Left group's frame — a different mask.
        left_expected = GoveeCodec(get_profile(SKU)).segment_color((0, 255, 0), segments=list(LEFT))
        assert writes[0] != left_expected


class TestTurnOnOff:
    """Optimistic state and the cloud fallback when no raw tier accepts it."""

    @pytest.mark.asyncio
    async def test_turn_on_falls_back_to_cloud_command_when_raw_declines(self):
        coordinator = _coordinator()
        entity = _group_entity(coordinator=coordinator)

        with (
            patch(
                "custom_components.govee.platforms.segment_group.async_segment_color",
                AsyncMock(return_value=False),
            ),
            patch(
                "custom_components.govee.platforms.segment_group.async_ensure_device_powered",
                AsyncMock(),
            ),
        ):
            await entity.async_turn_on(rgb_color=(1, 2, 3), brightness=200)

        coordinator.async_control_device.assert_awaited_once()
        device_id, command = coordinator.async_control_device.call_args[0]
        assert device_id == DEVICE_ID
        assert isinstance(command, SegmentColorCommand)
        assert command.segment_indices == LEFT
        assert entity.is_on is True
        assert entity.rgb_color == (1, 2, 3)
        assert entity.brightness == 200

    @pytest.mark.asyncio
    async def test_turn_off_paints_black_across_the_whole_group(self):
        coordinator = _coordinator()
        entity = _group_entity(coordinator=coordinator)
        entity._is_on = True

        with patch(
            "custom_components.govee.platforms.segment_group.async_segment_color",
            AsyncMock(return_value=False),
        ):
            await entity.async_turn_off()

        coordinator.async_control_device.assert_awaited_once()
        _, command = coordinator.async_control_device.call_args[0]
        assert command.segment_indices == LEFT
        assert command.color.r == 0 and command.color.g == 0 and command.color.b == 0
        assert entity.is_on is False

    @pytest.mark.asyncio
    async def test_turn_off_skipped_when_device_already_off(self):
        coordinator = _coordinator()
        state = MagicMock()
        state.power_state = False
        coordinator.get_state = MagicMock(return_value=state)
        entity = _group_entity(coordinator=coordinator)

        await entity.async_turn_off()

        coordinator.async_control_device.assert_not_awaited()
        assert entity.is_on is False


class TestReadbackAggregation:
    """is_on = any member on; colour adopted from the first lit member."""

    @pytest.mark.asyncio
    async def test_one_member_on_aggregates_group_is_on(self):
        entity = _group_entity("Left", LEFT)
        entity._is_on = False

        entity._handle_segment_readback({2: SegmentReading(2, 70, (255, 0, 255))})

        assert entity.is_on is True
        assert entity.rgb_color == (255, 0, 255)
        entity.async_write_ha_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_all_members_off_turns_the_group_off(self):
        entity = _group_entity("Left", LEFT)
        entity._is_on = True

        entity._handle_segment_readback({i: SegmentReading(i, 100, (0, 0, 0)) for i in LEFT})

        assert entity.is_on is False
        entity.async_write_ha_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_colour_comes_from_the_first_lit_member_in_group_order(self):
        entity = _group_entity("Left", LEFT)
        entity._is_on = False

        readings = {
            0: SegmentReading(0, 100, (0, 0, 0)),
            1: SegmentReading(1, 50, (10, 20, 30)),
            2: SegmentReading(2, 80, (40, 50, 60)),
        }
        entity._handle_segment_readback(readings)

        assert entity.rgb_color == (10, 20, 30)

    @pytest.mark.asyncio
    async def test_no_readings_for_this_group_s_members_is_a_no_op(self):
        entity = _group_entity("Left", LEFT)
        entity._is_on = True
        entity._rgb_color = (9, 9, 9)

        entity._handle_segment_readback({9: SegmentReading(9, 70, (1, 1, 1))})

        assert entity.rgb_color == (9, 9, 9)
        entity.async_write_ha_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_agreement_writes_nothing(self):
        entity = _group_entity("Left", LEFT)
        entity._is_on = True
        entity._rgb_color = (10, 20, 30)
        entity._brightness = 255  # level 100

        entity._handle_segment_readback({0: SegmentReading(0, 100, (10, 20, 30))})

        entity.async_write_ha_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_reading_inside_the_grace_window_is_ignored(self):
        entity = _group_entity("Left", LEFT)
        entity._rgb_color = (0, 255, 0)
        entity._written_at = time.monotonic()

        entity._handle_segment_readback({0: SegmentReading(0, 100, (255, 0, 0))})

        assert entity.rgb_color == (0, 255, 0)
        entity.async_write_ha_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_reading_after_the_grace_window_applies(self):
        entity = _group_entity("Left", LEFT)
        entity._rgb_color = (0, 255, 0)
        entity._written_at = time.monotonic() - (READBACK_WRITE_GRACE_SECONDS + 0.1)

        entity._handle_segment_readback({0: SegmentReading(0, 100, (255, 0, 0))})

        assert entity.rgb_color == (255, 0, 0)
        entity.async_write_ha_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_a_paint_stamps_the_grace_window(self):
        entity = _group_entity("Left", LEFT)
        assert entity._written_at == 0.0

        with (
            patch(
                "custom_components.govee.platforms.segment_group.async_segment_color",
                AsyncMock(return_value=True),
            ),
            patch(
                "custom_components.govee.platforms.segment_group.async_ensure_device_powered",
                AsyncMock(),
            ),
        ):
            await entity.async_turn_on(rgb_color=(1, 2, 3))

        assert entity._written_at > 0.0


class TestRestoreEntity:
    """State survives an HA restart via RestoreEntity."""

    @pytest.mark.asyncio
    async def test_restores_on_state_colour_and_brightness(self):
        entity = _group_entity("Left", LEFT)
        last_state = MagicMock()
        last_state.state = "on"
        last_state.attributes = {"brightness": 128, "rgb_color": [1, 2, 3]}

        with (
            patch.object(GoveeSegmentGroupEntity, "async_get_last_state", AsyncMock(return_value=last_state)),
            patch(
                "homeassistant.helpers.restore_state.RestoreEntity.async_added_to_hass",
                AsyncMock(),
            ),
            patch(
                "homeassistant.helpers.dispatcher.async_dispatcher_connect",
                MagicMock(return_value=MagicMock()),
            ),
        ):
            entity.async_on_remove = MagicMock()
            await entity.async_added_to_hass()

        assert entity.is_on is True
        assert entity.brightness == 128
        assert entity.rgb_color == (1, 2, 3)

    @pytest.mark.asyncio
    async def test_no_prior_state_keeps_the_constructor_defaults(self):
        entity = _group_entity("Left", LEFT)

        with (
            patch.object(GoveeSegmentGroupEntity, "async_get_last_state", AsyncMock(return_value=None)),
            patch(
                "homeassistant.helpers.restore_state.RestoreEntity.async_added_to_hass",
                AsyncMock(),
            ),
            patch(
                "homeassistant.helpers.dispatcher.async_dispatcher_connect",
                MagicMock(return_value=MagicMock()),
            ),
        ):
            entity.async_on_remove = MagicMock()
            await entity.async_added_to_hass()

        assert entity.is_on is True
        assert entity.brightness == 255
        assert entity.rgb_color == (255, 255, 255)
