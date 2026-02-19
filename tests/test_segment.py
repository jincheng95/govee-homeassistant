"""Test segment entity turn_off logic (issue #16).

Verifies that GoveeSegmentEntity.async_turn_off skips the API call when
a power-off is already in flight or the device is already off, preventing
race conditions that cause RGBIC firmware glitches.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.govee.models import (
    GoveeDeviceState,
    RGBColor,
    SegmentColorCommand,
)
from custom_components.govee.platforms.segment import GoveeSegmentEntity


def _make_segment_entity(
    *,
    power_state: bool = True,
    power_off_pending: bool = False,
    state_exists: bool = True,
) -> GoveeSegmentEntity:
    """Create a GoveeSegmentEntity with a mocked coordinator.

    Args:
        power_state: Device power state returned by get_state().
        power_off_pending: Value returned by is_power_off_pending().
        state_exists: Whether get_state() returns a state or None.
    """
    coordinator = MagicMock()
    coordinator.async_control_device = AsyncMock(return_value=True)
    coordinator.is_power_off_pending = MagicMock(return_value=power_off_pending)

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

    # Bypass GoveeEntity.__init__ which requires a real coordinator
    with patch.object(GoveeSegmentEntity, "__init__", lambda self, *a, **kw: None):
        entity = GoveeSegmentEntity.__new__(GoveeSegmentEntity)

    # Set the attributes that __init__ would normally set
    entity.coordinator = coordinator
    entity._device_id = device.device_id
    entity._segment_index = 3
    entity._is_on = True
    entity._brightness = 255
    entity._rgb_color = (255, 255, 255)
    entity.async_write_ha_state = MagicMock()

    return entity


class TestSegmentTurnOffLogic:
    """Test segment async_turn_off race-condition guards."""

    @pytest.mark.asyncio
    async def test_turn_off_sends_command_when_device_on(self):
        """API call is sent when device is on and no power-off pending."""
        entity = _make_segment_entity(power_state=True, power_off_pending=False)

        await entity.async_turn_off()

        entity.coordinator.async_control_device.assert_called_once()
        args = entity.coordinator.async_control_device.call_args
        assert args[0][0] == "AA:BB:CC:DD:EE:FF:00:11"
        cmd = args[0][1]
        assert isinstance(cmd, SegmentColorCommand)
        assert cmd.color == RGBColor(r=0, g=0, b=0)
        assert cmd.segment_indices == (3,)

    @pytest.mark.asyncio
    async def test_turn_off_skipped_when_power_off_pending(self):
        """API call is skipped when power_off_pending=True."""
        entity = _make_segment_entity(power_state=True, power_off_pending=True)

        await entity.async_turn_off()

        entity.coordinator.async_control_device.assert_not_called()

    @pytest.mark.asyncio
    async def test_turn_off_skipped_when_device_already_off(self):
        """API call is skipped when device is already off."""
        entity = _make_segment_entity(power_state=False, power_off_pending=False)

        await entity.async_turn_off()

        entity.coordinator.async_control_device.assert_not_called()

    @pytest.mark.asyncio
    async def test_turn_off_skipped_when_both_conditions(self):
        """API call is skipped when both conditions are true."""
        entity = _make_segment_entity(power_state=False, power_off_pending=True)

        await entity.async_turn_off()

        entity.coordinator.async_control_device.assert_not_called()

    @pytest.mark.asyncio
    async def test_local_state_updated_when_command_sent(self):
        """_is_on and async_write_ha_state are always called after sending command."""
        entity = _make_segment_entity(power_state=True, power_off_pending=False)

        await entity.async_turn_off()

        assert entity._is_on is False
        entity.async_write_ha_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_local_state_updated_when_command_skipped(self):
        """_is_on and async_write_ha_state are always called even when command is skipped."""
        entity = _make_segment_entity(power_state=True, power_off_pending=True)

        await entity.async_turn_off()

        assert entity._is_on is False
        entity.async_write_ha_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_turn_off_when_no_state_exists(self):
        """API call is sent when get_state returns None (device state unknown)."""
        entity = _make_segment_entity(state_exists=False, power_off_pending=False)

        await entity.async_turn_off()

        # When state is None, device_already_off is False, so command should be sent
        entity.coordinator.async_control_device.assert_called_once()
