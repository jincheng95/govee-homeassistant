"""Segment-boundary blending (``gradientToggle``) as a switch — fork feature.

Hardware-verified 2026-08-13 on an H6076 and an H6046, the only two SKUs in the
fleet that advertise the capability: the toggle decides whether the NEXT segment
paint blends its colours across segment boundaries (on) or leaves them hard
(off). Toggling it alone is a no-op until that repaint.

Detection is capability-driven — no SKU list — so a device that does not
advertise ``gradientToggle`` never gets the entity, and a future SKU that does
gets it for free.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.govee.models import GoveeCapability, GoveeDevice, ToggleCommand
from custom_components.govee.models.device import (
    CAPABILITY_COLOR_SETTING,
    CAPABILITY_ON_OFF,
    CAPABILITY_RANGE,
    CAPABILITY_TOGGLE,
    DEVICE_TYPE_LIGHT,
    INSTANCE_BRIGHTNESS,
    INSTANCE_COLOR_RGB,
    INSTANCE_GRADUAL_ON,
    INSTANCE_POWER,
)

ENTITY = "GoveeSegmentBlendingSwitchEntity"


def _cap(cap_type: str, instance: str, params: dict | None = None) -> GoveeCapability:
    return GoveeCapability(type=cap_type, instance=instance, parameters=params or {})


def _h6076() -> GoveeDevice:
    """Capability shape from the live LAN/API probe of 2026-08-13."""
    return GoveeDevice(
        device_id="AA:BB:77:88:99:AA:BB:CC",
        sku="H6076",
        name="Floor Lamp",
        device_type=DEVICE_TYPE_LIGHT,
        capabilities=(
            _cap(CAPABILITY_ON_OFF, INSTANCE_POWER),
            _cap(CAPABILITY_RANGE, INSTANCE_BRIGHTNESS),
            _cap(CAPABILITY_COLOR_SETTING, INSTANCE_COLOR_RGB),
            _cap(CAPABILITY_TOGGLE, INSTANCE_GRADUAL_ON),
            _cap(CAPABILITY_TOGGLE, "dreamViewToggle"),
        ),
    )


def _h60b0() -> GoveeDevice:
    """The uplighter: named light toggles, but NO gradientToggle."""
    return GoveeDevice(
        device_id="AA:BB:DD:EE:FF:44:55:66",
        sku="H60B0",
        name="Uplighter Floor Lamp",
        device_type=DEVICE_TYPE_LIGHT,
        capabilities=(
            _cap(CAPABILITY_ON_OFF, INSTANCE_POWER),
            _cap(CAPABILITY_RANGE, INSTANCE_BRIGHTNESS),
            _cap(CAPABILITY_TOGGLE, "rippleLightToggle"),
        ),
    )


class TestCapabilityDetection:
    """``supports_segment_blending`` reads the capability, never the SKU."""

    def test_h6076_advertises_it(self):
        assert _h6076().supports_segment_blending is True

    def test_h60b0_does_not(self):
        assert _h60b0().supports_segment_blending is False

    def test_a_future_sku_advertising_it_is_covered(self):
        dev = GoveeDevice(
            device_id="AA:BB:CC:DD:EE:FF:00:99",
            sku="H9999",
            name="Unknown lamp",
            device_type=DEVICE_TYPE_LIGHT,
            capabilities=(_cap(CAPABILITY_TOGGLE, INSTANCE_GRADUAL_ON),),
        )
        assert dev.supports_segment_blending is True

    def test_the_instance_name_alone_is_not_enough(self):
        # Same instance string under a different capability type is not the
        # toggle — the pair must match.
        dev = GoveeDevice(
            device_id="AA:BB:CC:DD:EE:FF:00:98",
            sku="H9998",
            name="Odd lamp",
            device_type=DEVICE_TYPE_LIGHT,
            capabilities=(_cap(CAPABILITY_RANGE, INSTANCE_GRADUAL_ON),),
        )
        assert dev.supports_segment_blending is False


class TestSwitchPlatformWiring:
    """Only a device advertising the capability gets the entity."""

    async def _setup(self, device: GoveeDevice) -> list:
        from custom_components.govee import switch as switch_mod

        coordinator = MagicMock()
        coordinator.devices = {device.device_id: device}
        entry = MagicMock()
        entry.runtime_data = coordinator
        added: list = []
        await switch_mod.async_setup_entry(MagicMock(), entry, lambda ents: added.extend(ents))
        return added

    @pytest.mark.asyncio
    async def test_h6076_gets_a_segment_blending_switch(self):
        added = await self._setup(_h6076())

        blending = [e for e in added if type(e).__name__ == ENTITY]
        assert len(blending) == 1
        assert blending[0]._attr_unique_id == "AA:BB:77:88:99:AA:BB:CC_segment_blending"
        assert blending[0]._attr_translation_key == "govee_segment_blending"

    @pytest.mark.asyncio
    async def test_h60b0_gets_none(self):
        added = await self._setup(_h60b0())

        assert [e for e in added if type(e).__name__ == ENTITY] == []


class TestCommands:
    """on/off send the ``gradientToggle`` capability payload."""

    def _entity(self):
        from custom_components.govee.switch import GoveeSegmentBlendingSwitchEntity

        device = _h6076()
        coordinator = MagicMock()
        coordinator.async_control_device = AsyncMock(return_value=True)
        entity = GoveeSegmentBlendingSwitchEntity(coordinator, device)
        entity.async_write_ha_state = MagicMock()
        return entity, coordinator

    @pytest.mark.asyncio
    async def test_turn_on_sends_the_toggle_enabled(self):
        entity, coordinator = self._entity()

        await entity.async_turn_on()

        coordinator.async_control_device.assert_awaited_once_with(
            "AA:BB:77:88:99:AA:BB:CC",
            ToggleCommand(toggle_instance=INSTANCE_GRADUAL_ON, enabled=True),
        )
        assert entity.is_on is True

    @pytest.mark.asyncio
    async def test_turn_off_sends_the_toggle_disabled(self):
        entity, coordinator = self._entity()
        await entity.async_turn_on()
        coordinator.async_control_device.reset_mock()

        await entity.async_turn_off()

        coordinator.async_control_device.assert_awaited_once_with(
            "AA:BB:77:88:99:AA:BB:CC",
            ToggleCommand(toggle_instance=INSTANCE_GRADUAL_ON, enabled=False),
        )
        assert entity.is_on is False

    @pytest.mark.asyncio
    async def test_a_failed_command_does_not_move_the_optimistic_state(self):
        entity, coordinator = self._entity()
        # Start from ON — the opposite of the entity's initial state — so the
        # assertion can only hold if the failed turn_off left it alone.
        await entity.async_turn_on()
        assert entity.is_on is True
        coordinator.async_control_device = AsyncMock(return_value=False)

        await entity.async_turn_off()

        assert entity.is_on is True
