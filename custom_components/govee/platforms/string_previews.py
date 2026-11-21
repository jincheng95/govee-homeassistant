"""The string-light DIY effect preview sensor (fork feature)."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import EntityCategory

from ..const import SUFFIX_DIY_EFFECT_PREVIEWS
from ..coordinator import GoveeCoordinator
from ..diy_preview_sources import cdn_count, source_kind, sources, string_effect_sources, takes_string_effects
from ..entity import GoveeEntity
from ..models import GoveeDevice

_LOGGER = logging.getLogger(__name__)


def async_string_preview_sensor_entities(coordinator: GoveeCoordinator) -> list[SensorEntity]:
    """Build one preview sensor per device that takes string effects."""
    entities: list[SensorEntity] = [
        GoveeStringEffectPreviewsSensor(coordinator, device)
        for device in coordinator.devices.values()
        if not device.is_group and takes_string_effects(device.sku)
    ]
    if entities:
        _LOGGER.debug("Created %d Govee DIY effect preview sensors", len(entities))
    return entities


class GoveeStringEffectPreviewsSensor(GoveeEntity, SensorEntity):
    """Preview sources for every string-light effect with shipped artwork.

    The state counts the effects whose ``url`` is a CDN URL. ``previews`` maps
    each effect to ``{"url", "fallback"}``, where ``fallback`` is the shipped
    copy. Adding the entity schedules the vendor list fetch.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "diy_effect_previews"
    _unrecorded_attributes = frozenset({"previews"})

    def __init__(self, coordinator: GoveeCoordinator, device: GoveeDevice) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.device_id}{SUFFIX_DIY_EFFECT_PREVIEWS}"

    async def async_added_to_hass(self) -> None:
        """Schedule the vendor list; setup does not wait on it."""
        await super().async_added_to_hass()
        sources(self.coordinator).async_schedule(self.hass, self._device.sku)

    def _previews(self) -> dict[str, dict[str, str]]:
        return string_effect_sources(sources(self.coordinator).urls(self._device.sku))

    @property
    def native_value(self) -> int:
        return cdn_count(self._previews())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        previews = self._previews()
        updated = sources(self.coordinator).updated(self._device.sku)
        return {
            **super().extra_state_attributes,
            "previews": previews,
            "source": source_kind(previews),
            "updated": None if updated is None else updated.isoformat(),
        }
