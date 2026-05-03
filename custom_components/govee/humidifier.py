"""Humidifier platform for Govee integration.

Exposes Govee humidifiers and dehumidifiers (e.g. H7150) as HA humidifier
entities with on/off, target-humidity, and mode selection.

Mode mapping (H7150 dehumidifier reference device):
- ``gearMode`` (workMode=1) — manual speed (Low=1, High=3)
- ``Auto``     (workMode=3) — humidity-target mode (30–80%)
- ``Dryer``    (workMode=8) — fixed-dry mode (modeValue ignored)

The API does not expose a current-humidity reading for H7150, so this
platform reports target humidity only.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.humidifier import (
    HumidifierDeviceClass,
    HumidifierEntity,
)
from homeassistant.components.humidifier.const import HumidifierEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import GoveeCoordinator
from .entity import GoveeEntity
from .models import GoveeDevice, PowerCommand, WorkModeCommand

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0

# Canonical mode names surfaced to Home Assistant. The set of modes offered
# by a given device is intersected with these at entity-construction time.
MODE_LOW = "low"
MODE_HIGH = "high"
MODE_AUTO = "auto"
MODE_DRYER = "dryer"

# Map canonical HA mode name -> (govee_mode_name, fallback_mode_value).
# govee_mode_name is matched case-insensitively against the device's
# work_mode and gear options. fallback_mode_value is used when the device's
# capability doesn't specify a value (e.g. Dryer always sends 0).
_MODE_ALIASES: dict[str, tuple[str, int]] = {
    MODE_LOW: ("low", 1),
    MODE_HIGH: ("high", 3),
    MODE_AUTO: ("auto", 0),
    MODE_DRYER: ("dryer", 0),
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Govee humidifier entities from a config entry."""
    coordinator: GoveeCoordinator = entry.runtime_data

    entities: list[HumidifierEntity] = []
    for device in coordinator.devices.values():
        if device.is_humidifier and not device.is_group:
            entities.append(GoveeHumidifierEntity(coordinator, device))
            _LOGGER.debug(
                "Created humidifier entity for %s (%s, type=%s)",
                device.name,
                device.sku,
                device.device_type,
            )

    async_add_entities(entities)


class GoveeHumidifierEntity(GoveeEntity, HumidifierEntity):
    """Govee humidifier / dehumidifier entity."""

    _attr_translation_key = "govee_humidifier"
    _attr_supported_features = HumidifierEntityFeature.MODES

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        device: GoveeDevice,
    ) -> None:
        """Initialize the humidifier entity."""
        super().__init__(coordinator, device)
        self._attr_name = None  # use device name (has_entity_name = True)

        self._attr_device_class = (
            HumidifierDeviceClass.DEHUMIDIFIER
            if device.is_dehumidifier
            else HumidifierDeviceClass.HUMIDIFIER
        )

        min_h, max_h = device.get_humidity_range()
        self._attr_min_humidity = min_h
        self._attr_max_humidity = max_h

        # Build per-device maps from the capability so the entity honours
        # whatever the device actually advertises (values may vary by SKU).
        self._mode_to_work_mode: dict[str, int] = {}
        self._mode_to_mode_value: dict[str, int] = {}
        self._work_mode_to_mode: dict[int, str] = {}
        self._gear_mode_values: dict[str, int] = {}

        for opt in device.get_humidifier_work_mode_options():
            name = str(opt.get("name", "")).strip().lower()
            value = opt.get("value")
            if value is None:
                continue
            for ha_mode, (alias, _default) in _MODE_ALIASES.items():
                if name == alias and ha_mode not in (MODE_LOW, MODE_HIGH):
                    self._mode_to_work_mode[ha_mode] = int(value)
                    self._work_mode_to_mode[int(value)] = ha_mode

        # gearMode carries the Low/High sub-options. We map each into a
        # top-level HA mode so users get one flat dropdown.
        gear_work_mode = self._mode_to_work_mode.get("gearmode")
        # The work_mode option may have been named "gearMode" rather than
        # matching an HA alias — locate it explicitly.
        if gear_work_mode is None:
            for opt in device.get_humidifier_work_mode_options():
                if str(opt.get("name", "")).strip().lower() == "gearmode":
                    gear_work_mode = int(opt["value"])
                    break

        if gear_work_mode is not None:
            for gear in device.get_humidifier_gear_options():
                name = str(gear.get("name", "")).strip().lower()
                value = gear.get("value")
                if value is None:
                    continue
                if name in (MODE_LOW, MODE_HIGH):
                    self._mode_to_work_mode[name] = gear_work_mode
                    self._mode_to_mode_value[name] = int(value)
                    self._gear_mode_values[name] = int(value)

        # Dryer / Auto use a fixed mode_value (0) unless specified.
        for ha_mode in (MODE_AUTO, MODE_DRYER):
            if ha_mode in self._mode_to_work_mode:
                self._mode_to_mode_value.setdefault(ha_mode, _MODE_ALIASES[ha_mode][1])

        # Final mode list, ordered for a consistent UI.
        ordered = [MODE_LOW, MODE_HIGH, MODE_AUTO, MODE_DRYER]
        self._attr_available_modes = [
            m for m in ordered if m in self._mode_to_work_mode
        ]

    # --------------------------------------------------------------------- #
    # State
    # --------------------------------------------------------------------- #

    @property
    def is_on(self) -> bool | None:
        """Return True if the device is powered on."""
        state = self.device_state
        return state.power_state if state else None

    @property
    def mode(self) -> str | None:
        """Return the current operating mode."""
        state = self.device_state
        if state is None or state.work_mode is None:
            return None
        ha_mode = self._work_mode_to_mode.get(state.work_mode)
        if ha_mode is None:
            # gearMode — disambiguate by mode_value.
            for name, gear_val in self._gear_mode_values.items():
                if state.mode_value == gear_val and (
                    self._mode_to_work_mode.get(name) == state.work_mode
                ):
                    return name
        return ha_mode

    @property
    def target_humidity(self) -> int | None:
        """Return the target humidity percentage (Auto mode only)."""
        state = self.device_state
        if state is None:
            return None
        auto_work_mode = self._mode_to_work_mode.get(MODE_AUTO)
        if (
            auto_work_mode is not None
            and state.work_mode == auto_work_mode
            and state.mode_value is not None
        ):
            return int(state.mode_value)
        return None

    # --------------------------------------------------------------------- #
    # Commands
    # --------------------------------------------------------------------- #

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the humidifier on."""
        await self.coordinator.async_control_device(
            self._device_id, PowerCommand(power_on=True)
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the humidifier off."""
        await self.coordinator.async_control_device(
            self._device_id, PowerCommand(power_on=False)
        )

    async def async_set_mode(self, mode: str) -> None:
        """Set the operating mode."""
        work_mode = self._mode_to_work_mode.get(mode)
        if work_mode is None:
            raise ValueError(f"Unsupported mode for {self._device.sku}: {mode}")

        if mode == MODE_AUTO:
            # Preserve the current target humidity if one was set; fall back
            # to the device's minimum otherwise so the unit has a sensible
            # setpoint to aim at.
            state = self.device_state
            mode_value = (
                state.mode_value
                if state and state.work_mode == work_mode and state.mode_value
                else self._attr_min_humidity
            )
        else:
            mode_value = self._mode_to_mode_value.get(mode, 0)

        await self.coordinator.async_control_device(
            self._device_id,
            WorkModeCommand(work_mode=work_mode, mode_value=int(mode_value)),
        )

    async def async_set_humidity(self, humidity: int) -> None:
        """Set the target humidity — switches the device into Auto mode."""
        auto_work_mode = self._mode_to_work_mode.get(MODE_AUTO)
        if auto_work_mode is None:
            raise ValueError(
                f"{self._device.sku} does not support target-humidity (Auto) mode"
            )

        clamped = max(self._attr_min_humidity, min(self._attr_max_humidity, humidity))
        await self.coordinator.async_control_device(
            self._device_id,
            WorkModeCommand(work_mode=auto_work_mode, mode_value=int(clamped)),
        )
