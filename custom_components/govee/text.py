"""Text platform for Govee integration (fork feature).

Holds palettes, because an ordered list of colours has no representation in
any other entity class: the DIY-effect palettes in :mod:`.platforms.diy_effect`
(gated by ``enable_zone_lights``) and the scene palettes in
:mod:`.platforms.scene_adjust` (gated by the scenes option).
"""

from __future__ import annotations

import logging

from homeassistant.components.text import TextEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import GoveeCoordinator
from .platforms.diy_effect import async_diy_text_entities
from .platforms.scene_adjust import async_scene_palette_text_entities

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Govee text entities from a config entry."""
    coordinator: GoveeCoordinator = entry.runtime_data

    entities: list[TextEntity] = []
    entities.extend(async_diy_text_entities(coordinator, entry))
    entities.extend(async_scene_palette_text_entities(coordinator, entry))

    async_add_entities(entities)
    _LOGGER.debug("Set up %d Govee text entities", len(entities))
