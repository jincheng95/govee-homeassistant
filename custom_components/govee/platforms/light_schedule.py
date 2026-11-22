"""Diagnostic sensors for a light's on-device schedule (fork feature)."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory

from ..api.protocol.encoders import repeat_byte
from ..const import SUFFIX_SCHEDULE_SLEEP, SUFFIX_SCHEDULE_TIMERS, SUFFIX_SCHEDULE_WAKE
from ..coordinator import GoveeCoordinator
from ..entity import GoveeEntity
from ..light_schedule import LightSchedule, schedule, schedule_sensors_enabled
from ..models import GoveeDevice

_LOGGER = logging.getLogger(__name__)

STATE_ON = "on"
STATE_OFF = "off"


def async_light_schedule_sensor_entities(coordinator: GoveeCoordinator, entry: ConfigEntry) -> list[SensorEntity]:
    """Build the three schedule sensors for SKUs whose schedule can be read."""
    entities: list[SensorEntity] = []
    for device in coordinator.devices.values():
        if device.is_group or not schedule_sensors_enabled(entry, device.sku):
            continue
        entities.append(GoveeSleepTimerSensor(coordinator, device))
        entities.append(GoveeWakeUpSensor(coordinator, device))
        entities.append(GoveeTimersSensor(coordinator, device))
    if entities:
        _LOGGER.debug("Created %d Govee light schedule sensors", len(entities))
    return entities


class _GoveeScheduleSensor(GoveeEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def _schedule(self) -> LightSchedule:
        return schedule(self.coordinator, self._device_id)


class GoveeSleepTimerSensor(_GoveeScheduleSensor):
    """Whether the light's sleep timer is on."""

    _attr_translation_key = "schedule_sleep"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [STATE_ON, STATE_OFF]

    def __init__(self, coordinator: GoveeCoordinator, device: GoveeDevice) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.device_id}{SUFFIX_SCHEDULE_SLEEP}"

    @property
    def native_value(self) -> str | None:
        sleep = self._schedule().sleep
        return None if sleep is None else (STATE_ON if sleep.enabled else STATE_OFF)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        sleep = self._schedule().sleep
        if sleep is None:
            return {}
        return {
            "minutes": sleep.minutes,
            "remaining_minutes": sleep.remaining_minutes,
            "start_brightness": sleep.start_brightness,
        }


class GoveeWakeUpSensor(_GoveeScheduleSensor):
    """Whether the light's wake-up is on."""

    _attr_translation_key = "schedule_wake"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [STATE_ON, STATE_OFF]

    def __init__(self, coordinator: GoveeCoordinator, device: GoveeDevice) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.device_id}{SUFFIX_SCHEDULE_WAKE}"

    @property
    def native_value(self) -> str | None:
        wake = self._schedule().wake
        return None if wake is None else (STATE_ON if wake.enabled else STATE_OFF)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        wake = self._schedule().wake
        if wake is None:
            return {}
        return {
            "time": f"{wake.hour:02d}:{wake.minute:02d}",
            "ramp_minutes": wake.duration,
            "end_brightness": wake.end_brightness,
            "repeat": repeat_byte(wake.weekdays),
        }


class GoveeTimersSensor(_GoveeScheduleSensor):
    """How many of the light's timers are on."""

    _attr_translation_key = "schedule_timers"

    def __init__(self, coordinator: GoveeCoordinator, device: GoveeDevice) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.device_id}{SUFFIX_SCHEDULE_TIMERS}"

    @property
    def native_value(self) -> int | None:
        timers = self._schedule().timers
        return None if timers is None else sum(1 for timer in timers if timer.enabled)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        timers = self._schedule().timers
        if timers is None:
            return {}
        return {
            "timers": [
                {
                    "on": timer.enabled,
                    "time": f"{timer.hour:02d}:{timer.minute:02d}",
                    "repeat": repeat_byte(timer.weekdays),
                }
                for timer in timers
            ]
        }
