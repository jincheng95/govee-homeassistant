"""Tests for issue #118 — water-tank-full clearing mechanism.

Govee's OpenAPI event channel only ever pushes ``waterFullEvent`` value=1
(tank full OR bucket pulled) and never a cleared counterpart (confirmed live
across two pull→re-insert cycles), so the Problem sensor latched forever.
Covers the clearing design:

- Coordinator: event latches + stamps ``changed_at``; ``clear_water_full``
  ends the latch; ``restore_water_full`` re-applies a restored snapshot only
  when no live value landed first.
- Binary sensor: RestoreEntity round-trip + merged ``changed_at`` attribute.
- Clear Water Alert button: press wiring + coordinator-level availability.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.govee.binary_sensor import GoveeWaterFullBinarySensor
from custom_components.govee.button import GoveeClearWaterFullButton
from custom_components.govee.models import GoveeCapability, GoveeDevice, GoveeDeviceState
from custom_components.govee.models.device import (
    CAPABILITY_EVENT,
    CAPABILITY_ON_OFF,
    DEVICE_TYPE_DEHUMIDIFIER,
    DEVICE_TYPE_LIGHT,
    INSTANCE_POWER,
)

# Payload shape from the reporter's capture (issue #118).
_EVENT_STATE = [
    {
        "name": "waterFull",
        "value": 1,
        "message": "Water bucket is full or has been pulled out",
    }
]


def _h7150() -> GoveeDevice:
    return GoveeDevice(
        device_id="AA:BB:CC:DD:EE:FF:71:50",
        sku="H7150",
        name="Basement Dehumidifier",
        device_type=DEVICE_TYPE_DEHUMIDIFIER,
        capabilities=(
            GoveeCapability(type=CAPABILITY_ON_OFF, instance=INSTANCE_POWER),
            GoveeCapability(type=CAPABILITY_EVENT, instance="waterFullEvent"),
        ),
    )


def _light() -> GoveeDevice:
    return GoveeDevice(
        device_id="AA:BB:CC:DD:EE:FF:60:01",
        sku="H6001",
        name="Desk Lamp",
        device_type=DEVICE_TYPE_LIGHT,
        capabilities=(GoveeCapability(type=CAPABILITY_ON_OFF, instance=INSTANCE_POWER),),
    )


# --------------------------------------------------------------------------- #
# Coordinator — latch, stamp, clear, restore
# --------------------------------------------------------------------------- #


class TestCoordinatorClearing:
    def _coordinator(self):
        import custom_components.govee.coordinator as coord_mod

        coord = object.__new__(coord_mod.GoveeCoordinator)
        device = _h7150()
        coord._devices = {device.device_id: device}
        coord._states = {
            device.device_id: GoveeDeviceState.create_empty(device.device_id)
        }
        coord._water_full_changed_at = {}
        coord.async_set_updated_data = MagicMock()
        return coord, device

    def test_event_latches_and_stamps(self):
        coord, device = self._coordinator()
        coord._on_openapi_event(device.device_id, "H7150", "waterFullEvent", _EVENT_STATE)
        assert coord._states[device.device_id].water_full is True
        assert coord.water_full_changed_at(device.device_id) is not None

    def test_repeat_event_restamps(self):
        # Models the two events 16 min apart from #118 (re-pulled bucket).
        coord, device = self._coordinator()
        seeded = datetime(2026, 1, 1, tzinfo=timezone.utc)
        coord._states[device.device_id].water_full = True
        coord._water_full_changed_at[device.device_id] = seeded
        coord._on_openapi_event(device.device_id, "H7150", "waterFullEvent", _EVENT_STATE)
        assert coord._states[device.device_id].water_full is True
        assert coord.water_full_changed_at(device.device_id) > seeded

    def test_clear_water_full(self):
        coord, device = self._coordinator()
        coord._states[device.device_id].water_full = True
        coord.clear_water_full(device.device_id)
        assert coord._states[device.device_id].water_full is False
        assert coord.water_full_changed_at(device.device_id) is not None
        coord.async_set_updated_data.assert_called_once_with(coord._states)

    def test_clear_when_never_latched(self):
        # User asserting the tank is OK is harmless — must not raise.
        coord, device = self._coordinator()
        assert coord._states[device.device_id].water_full is None
        coord.clear_water_full(device.device_id)
        assert coord._states[device.device_id].water_full is False

    def test_event_after_clear_relatches(self):
        # The false-OK window after a wrong manual clear is bounded by the
        # next trigger edge.
        coord, device = self._coordinator()
        coord._states[device.device_id].water_full = True
        coord.clear_water_full(device.device_id)
        coord._on_openapi_event(device.device_id, "H7150", "waterFullEvent", _EVENT_STATE)
        assert coord._states[device.device_id].water_full is True

    def test_restore_applies_when_unknown(self):
        coord, device = self._coordinator()
        ts = datetime(2026, 7, 3, 12, 49, 19, tzinfo=timezone.utc)
        coord.restore_water_full(device.device_id, True, ts)
        assert coord._states[device.device_id].water_full is True
        assert coord.water_full_changed_at(device.device_id) == ts
        # Restore runs during entity add — the entity writes its own first
        # state, so the coordinator must NOT notify listeners.
        coord.async_set_updated_data.assert_not_called()

    def test_restore_without_changed_at(self):
        coord, device = self._coordinator()
        coord.restore_water_full(device.device_id, True, None)
        assert coord._states[device.device_id].water_full is True
        assert coord.water_full_changed_at(device.device_id) is None

    def test_restore_does_not_overwrite_live_value(self):
        # A live clear/event landed before entity-add — it must win.
        coord, device = self._coordinator()
        coord._states[device.device_id].water_full = False
        ts = datetime(2026, 7, 3, 12, 49, 19, tzinfo=timezone.utc)
        coord.restore_water_full(device.device_id, True, ts)
        assert coord._states[device.device_id].water_full is False


# --------------------------------------------------------------------------- #
# Binary sensor entity — is_on + changed_at attribute
# --------------------------------------------------------------------------- #


class TestWaterFullSensorEntity:
    def _entity(self, water_full, changed_at=None):
        device = _h7150()
        state = GoveeDeviceState(device_id=device.device_id, online=True)
        state.water_full = water_full
        coordinator = MagicMock()
        # config_entry=None -> GoveeEntity.extra_state_attributes returns {}.
        coordinator.config_entry = None
        coordinator.devices = {device.device_id: device}
        coordinator.get_state = MagicMock(return_value=state)
        coordinator.water_full_changed_at = MagicMock(return_value=changed_at)
        return GoveeWaterFullBinarySensor(coordinator, device)

    def test_unique_id(self):
        entity = self._entity(None)
        assert entity.unique_id == "AA:BB:CC:DD:EE:FF:71:50_water_full"

    def test_is_on_full(self):
        assert self._entity(True).is_on is True

    def test_is_on_clear(self):
        assert self._entity(False).is_on is False

    def test_is_on_unknown(self):
        assert self._entity(None).is_on is None

    def test_changed_at_attribute(self):
        ts = datetime(2026, 7, 3, 12, 49, 19, tzinfo=timezone.utc)
        entity = self._entity(True, changed_at=ts)
        assert entity.extra_state_attributes["changed_at"] == ts.isoformat()

    def test_changed_at_absent_until_stamped(self):
        entity = self._entity(True, changed_at=None)
        assert "changed_at" not in entity.extra_state_attributes


# --------------------------------------------------------------------------- #
# Binary sensor entity — RestoreEntity round-trip
# --------------------------------------------------------------------------- #


class TestWaterFullSensorRestore:
    def _entity(self):
        device = _h7150()
        coordinator = MagicMock()
        coordinator.config_entry = None
        coordinator.devices = {device.device_id: device}
        return GoveeWaterFullBinarySensor(coordinator, device), device

    async def _restore(self, entity, last_state):
        with (
            patch.object(
                GoveeWaterFullBinarySensor.__bases__[0],
                "async_added_to_hass",
                new_callable=AsyncMock,
            ),
            patch.object(
                entity,
                "async_get_last_state",
                new_callable=AsyncMock,
                return_value=last_state,
            ),
        ):
            await entity.async_added_to_hass()

    @pytest.mark.asyncio
    async def test_restores_latched_alert(self):
        from homeassistant.util import dt as dt_util

        entity, device = self._entity()
        await self._restore(
            entity,
            MagicMock(state="on", attributes={"changed_at": "2026-07-03T12:49:19+00:00"}),
        )
        entity.coordinator.restore_water_full.assert_called_once_with(
            device.device_id,
            True,
            dt_util.parse_datetime("2026-07-03T12:49:19+00:00"),
        )

    @pytest.mark.asyncio
    async def test_restores_cleared_state(self):
        entity, device = self._entity()
        await self._restore(entity, MagicMock(state="off", attributes={}))
        entity.coordinator.restore_water_full.assert_called_once_with(
            device.device_id, False, None
        )

    @pytest.mark.asyncio
    async def test_no_previous_state_skips_restore(self):
        entity, _ = self._entity()
        await self._restore(entity, None)
        entity.coordinator.restore_water_full.assert_not_called()

    @pytest.mark.asyncio
    async def test_unavailable_previous_state_skips_restore(self):
        entity, _ = self._entity()
        await self._restore(entity, MagicMock(state="unavailable", attributes={}))
        entity.coordinator.restore_water_full.assert_not_called()

    @pytest.mark.asyncio
    async def test_garbage_changed_at_restores_without_timestamp(self):
        entity, device = self._entity()
        await self._restore(
            entity, MagicMock(state="on", attributes={"changed_at": 12345})
        )
        entity.coordinator.restore_water_full.assert_called_once_with(
            device.device_id, True, None
        )


# --------------------------------------------------------------------------- #
# Clear Water Alert button
# --------------------------------------------------------------------------- #


class TestClearButton:
    def _entity(self, last_update_success=True):
        device = _h7150()
        # Sleepy/offline device: clearing a locally latched alert must not
        # require the device to be online.
        state = GoveeDeviceState(device_id=device.device_id, online=False)
        coordinator = MagicMock()
        coordinator.devices = {device.device_id: device}
        coordinator.get_state = MagicMock(return_value=state)
        coordinator.last_update_success = last_update_success
        return GoveeClearWaterFullButton(coordinator, device), device

    def test_unique_id(self):
        entity, device = self._entity()
        assert entity.unique_id == f"{device.device_id}_clear_water_full"

    def test_translation_key(self):
        entity, _ = self._entity()
        assert entity.translation_key == "clear_water_full"

    def test_no_entity_category(self):
        # User-facing control paired with the alert sensor — must NOT be
        # tucked into the Configuration section.
        entity, _ = self._entity()
        assert entity.entity_category is None

    @pytest.mark.asyncio
    async def test_press_clears_alert(self):
        entity, device = self._entity()
        await entity.async_press()
        entity.coordinator.clear_water_full.assert_called_once_with(device.device_id)

    def test_available_despite_offline_device(self):
        entity, _ = self._entity(last_update_success=True)
        assert entity.available is True

    def test_unavailable_when_coordinator_failed(self):
        entity, _ = self._entity(last_update_success=False)
        assert entity.available is False


# --------------------------------------------------------------------------- #
# Device gating + platform wiring
# --------------------------------------------------------------------------- #


class TestDeviceGating:
    def test_h7150_supports_water_full_event(self):
        assert _h7150().supports_water_full_event is True

    def test_light_does_not_support_water_full_event(self):
        assert _light().supports_water_full_event is False

    async def _setup_buttons(self, device):
        from custom_components.govee import button as button_mod

        coordinator = MagicMock()
        coordinator.devices = {device.device_id: device}
        entry = MagicMock()
        entry.runtime_data = coordinator
        added: list = []
        await button_mod.async_setup_entry(
            MagicMock(), entry, lambda ents: added.extend(ents)
        )
        return [type(e).__name__ for e in added]

    async def test_h7150_gets_clear_button(self):
        names = await self._setup_buttons(_h7150())
        assert "GoveeClearWaterFullButton" in names

    async def test_light_gets_no_clear_button(self):
        names = await self._setup_buttons(_light())
        assert "GoveeClearWaterFullButton" not in names
