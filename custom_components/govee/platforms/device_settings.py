"""Device settings that exist only as raw frames (fork feature).

The power-off memory select decides what the device does when power returns.
It is built for profiles that encode ``Capability.POWER_OFF_MEMORY`` while
``enable_lan_raw_write`` is on, because no cloud command can carry it.
"""

from __future__ import annotations

import logging
from typing import Final

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.restore_state import RestoreEntity

from ..api.protocol import Capability
from ..api.raw_router import async_device_setting, supports_setting
from ..const import DOMAIN, SUFFIX_POWER_OFF_MEMORY
from ..coordinator import GoveeCoordinator
from ..entity import GoveeEntity
from ..models import GoveeDevice
from ..raw_status import device_settings, record_settings
from ..zone_state import raw_settings_enabled

_LOGGER = logging.getLogger(__name__)

POWER_OFF_MEMORY_OPTIONS: Final = ("last_state", "on", "off")
"""Option keys in wire order. The index is the byte the frame carries."""


def async_device_setting_select_entities(coordinator: GoveeCoordinator, entry: ConfigEntry) -> list[SelectEntity]:
    """Build the power-off memory select for devices whose profile encodes it."""
    if not raw_settings_enabled(entry):
        return []
    entities: list[SelectEntity] = [
        GoveePowerOffMemorySelect(coordinator, device)
        for device in coordinator.devices.values()
        if not device.is_group and supports_setting(device.sku, Capability.POWER_OFF_MEMORY)
    ]
    if entities:
        _LOGGER.debug("Created %d Govee power-off memory select entities", len(entities))
    return entities


class GoveePowerOffMemorySelect(GoveeEntity, SelectEntity, RestoreEntity):
    """What the device does when power returns.

    Shows the setting the device last reported (:mod:`..raw_status`), else the
    last option written or restored.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "power_off_memory"
    _attr_options = list(POWER_OFF_MEMORY_OPTIONS)

    def __init__(self, coordinator: GoveeCoordinator, device: GoveeDevice) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.device_id}{SUFFIX_POWER_OFF_MEMORY}"
        self._option: str | None = None

    async def async_added_to_hass(self) -> None:
        """Restore the last option."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state in POWER_OFF_MEMORY_OPTIONS:
            self._option = last_state.state

    @property
    def current_option(self) -> str | None:
        reported = device_settings(self.coordinator, self._device_id).power_off_memory
        return self._option if reported is None else POWER_OFF_MEMORY_OPTIONS[reported]

    async def async_select_option(self, option: str) -> None:
        if option not in POWER_OFF_MEMORY_OPTIONS:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_option",
                translation_placeholders={"option": option, "device": self._device.name},
            )
        behaviour = POWER_OFF_MEMORY_OPTIONS.index(option)
        if not await async_device_setting(
            self.coordinator,
            self._device,
            Capability.POWER_OFF_MEMORY,
            lambda codec: codec.power_off_memory(behaviour),
        ):
            raise self._command_failed()
        self._option = option
        record_settings(self.coordinator, self._device_id, power_off_memory=behaviour)
        self.async_write_ha_state()
