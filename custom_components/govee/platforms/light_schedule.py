"""Diagnostic sensors for a light's on-device schedule (fork feature)."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import EntityCategory

from ..const import SUFFIX_SCHEDULE_SLEEP, SUFFIX_SCHEDULE_TIMERS, SUFFIX_SCHEDULE_WAKE
from ..coordinator import GoveeCoordinator
from ..entity import GoveeEntity
from ..light_schedule import LightSchedule, reports_schedule, schedules
from ..models import GoveeDevice

_LOGGER = logging.getLogger(__name__)

STATE_ON = "on"
STATE_OFF = "off"


def async_light_schedule_sensor_entities(coordinator: GoveeCoordinator) -> list[SensorEntity]:
    """Build the three schedule sensors for SKUs that report a schedule."""
    entities: list[SensorEntity] = []
    for device in coordinator.devices.values():
        if device.is_group or not reports_schedule(device.sku):
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
        return schedules(self.coordinator).get(self._device_id, LightSchedule())


class GoveeSleepTimerSensor(_GoveeScheduleSensor):
    """Whether the light's sleep timer is on."""

    _attr_icon = "mdi:sleep"

    def __init__(self, coordinator: GoveeCoordinator, device: GoveeDevice) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.device_id}{SUFFIX_SCHEDULE_SLEEP}"
        self._attr_name = "Sleep timer"

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

    _attr_icon = "mdi:weather-sunset-up"

    def __init__(self, coordinator: GoveeCoordinator, device: GoveeDevice) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.device_id}{SUFFIX_SCHEDULE_WAKE}"
        self._attr_name = "Wake-up"

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
            "ramp_minutes": wake.ramp_minutes,
            "end_brightness": wake.end_brightness,
            "repeat": wake.repeat,
        }


class GoveeTimersSensor(_GoveeScheduleSensor):
    """How many of the light's timers are on."""

    _attr_icon = "mdi:timer-outline"

    def __init__(self, coordinator: GoveeCoordinator, device: GoveeDevice) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.device_id}{SUFFIX_SCHEDULE_TIMERS}"
        self._attr_name = "Timers on"

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
                {"on": timer.enabled, "time": f"{timer.hour:02d}:{timer.minute:02d}", "repeat": timer.repeat}
                for timer in timers
            ]
        }
