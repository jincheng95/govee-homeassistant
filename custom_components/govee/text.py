"""Text platform for Govee integration (fork feature).

Exists solely for the DIY-effect palettes in :mod:`.platforms.diy_effect`: an
ordered list of one to sixteen colours has no representation in any other
entity class, so it is edited as text. Every entity on this platform is gated
by ``enable_zone_lights``, so a default install adds this platform and creates
nothing on it.
"""

from __future__ import annotations

import logging

from homeassistant.components.text import TextEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import GoveeCoordinator
from .platforms.diy_effect import async_diy_text_entities

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

    async_add_entities(entities)
    _LOGGER.debug("Set up %d Govee text entities", len(entities))
