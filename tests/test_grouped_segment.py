"""Test grouped segment entity.

Verifies that GoveeGroupedSegmentEntity controls all segments as a single entity
with color and brightness control, similar to individual segments but for all at once.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.govee.models import (
    GoveeDeviceState,
    RGBColor,
    SegmentColorCommand,
)
from custom_components.govee.platforms.grouped_segment import GoveeGroupedSegmentEntity


def _make_grouped_segment_entity(
    *,
    power_state: bool = True,
    power_off_pending: bool = False,
    state_exists: bool = True,
    segment_count: int = 8,
) -> GoveeGroupedSegmentEntity:
    """Create a GoveeGroupedSegmentEntity with a mocked coordinator.

    Args:
        power_state: Device power state returned by get_state().
        power_off_pending: Value returned by is_power_off_pending().
        state_exists: Whether get_state() returns a state or None.
        segment_count: Number of segments in the device.
    """
    coordinator = MagicMock()
    coordinator.async_control_device = AsyncMock(return_value=True)
    coordinator.is_power_off_pending = MagicMock(return_value=power_off_pending)
    coordinator.last_update_success = True

    if state_exists:
        state = GoveeDeviceState.create_empty("AA:BB:CC:DD:EE:FF:00:11")
        state.power_state = power_state
        coordinator.get_state = MagicMock(return_value=state)
    else:
        coordinator.get_state = MagicMock(return_value=None)

    device = MagicMock()
    device.device_id = "AA:BB:CC:DD:EE:FF:00:11"
    device.sku = "H60A1"
    device.name = "RGBIC Strip"
    device.segment_count = segment_count

    # Bypass GoveeGroupedSegmentEntity.__init__ which requires a real coordinator
    with patch.object(GoveeGroupedSegmentEntity, "__init__", lambda self, *a, **kw: None):
        entity = GoveeGroupedSegmentEntity.__new__(GoveeGroupedSegmentEntity)

    # Set the attributes that __init__ would normally set
    entity.coordinator = coordinator
    entity._device_id = device.device_id
    entity._device = device
    entity._segment_indices = tuple(range(segment_count))
    entity._is_on = True
    entity._brightness = 255
    entity._rgb_color = (255, 255, 255)
    entity.async_write_ha_state = MagicMock()

    return entity


class TestGroupedSegmentEntity:
    """Test grouped segment entity functionality."""

    @pytest.mark.asyncio
    async def test_turn_on_sends_command_to_all_segments(self):
        """async_turn_on sends command with all segment indices."""
        entity = _make_grouped_segment_entity(segment_count=8)

        await entity.async_turn_on()

        entity.coordinator.async_control_device.assert_called_once()
        args = entity.coordinator.async_control_device.call_args
        assert args[0][0] == "AA:BB:CC:DD:EE:FF:00:11"
        cmd = args[0][1]
        assert isinstance(cmd, SegmentColorCommand)
        assert cmd.segment_indices == tuple(range(8))

    @pytest.mark.asyncio
    async def test_turn_on_with_color(self):
        """async_turn_on sets color from ATTR_RGB_COLOR."""
        entity = _make_grouped_segment_entity()

        await entity.async_turn_on(rgb_color=(255, 0, 0))

        args = entity.coordinator.async_control_device.call_args
        cmd = args[0][1]
        assert cmd.color == RGBColor(r=255, g=0, b=0)
        assert entity._rgb_color == (255, 0, 0)

    @pytest.mark.asyncio
    async def test_turn_on_with_brightness(self):
        """async_turn_on updates brightness from ATTR_BRIGHTNESS."""
        entity = _make_grouped_segment_entity()

        await entity.async_turn_on(brightness=128)

        assert entity._brightness == 128
        entity.async_write_ha_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_turn_off_sends_black_color_to_all_segments(self):
        """async_turn_off sends black color command with all segment indices."""
        entity = _make_grouped_segment_entity(power_state=True, power_off_pending=False)

        await entity.async_turn_off()

        entity.coordinator.async_control_device.assert_called_once()
        args = entity.coordinator.async_control_device.call_args
        cmd = args[0][1]
        assert isinstance(cmd, SegmentColorCommand)
        assert cmd.color == RGBColor(r=0, g=0, b=0)
        assert cmd.segment_indices == tuple(range(8))

    @pytest.mark.asyncio
    async def test_turn_off_skipped_when_power_off_pending(self):
        """async_turn_off skips command when power_off_pending=True."""
        entity = _make_grouped_segment_entity(power_state=True, power_off_pending=True)

        await entity.async_turn_off()

        entity.coordinator.async_control_device.assert_not_called()

    @pytest.mark.asyncio
    async def test_turn_off_skipped_when_device_already_off(self):
        """async_turn_off skips command when device is already off."""
        entity = _make_grouped_segment_entity(power_state=False, power_off_pending=False)

        await entity.async_turn_off()

        entity.coordinator.async_control_device.assert_not_called()

    @pytest.mark.asyncio
    async def test_is_on_property(self):
        """is_on property returns correct state."""
        entity = _make_grouped_segment_entity()
        assert entity.is_on is True

        entity._is_on = False
        assert entity.is_on is False

    @pytest.mark.asyncio
    async def test_brightness_property(self):
        """brightness property returns correct value."""
        entity = _make_grouped_segment_entity()
        assert entity.brightness == 255

        entity._brightness = 128
        assert entity.brightness == 128

    @pytest.mark.asyncio
    async def test_rgb_color_property(self):
        """rgb_color property returns correct tuple."""
        entity = _make_grouped_segment_entity()
        assert entity.rgb_color == (255, 255, 255)

        entity._rgb_color = (255, 0, 0)
        assert entity.rgb_color == (255, 0, 0)

    @pytest.mark.asyncio
    async def test_available_property(self):
        """available property reflects coordinator health."""
        entity = _make_grouped_segment_entity()
        assert entity.available is True

        entity.coordinator.last_update_success = False
        assert entity.available is False

    @pytest.mark.asyncio
    async def test_different_segment_counts(self):
        """Works correctly with different numbers of segments."""
        for segment_count in [1, 4, 8, 16]:
            entity = _make_grouped_segment_entity(segment_count=segment_count)
            assert entity._segment_indices == tuple(range(segment_count))
