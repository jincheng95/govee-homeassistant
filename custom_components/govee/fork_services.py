"""Registration of the fork's service actions, called from ``async_setup_services``."""

from __future__ import annotations

from homeassistant.core import HomeAssistant, callback

from .bulb_actions import async_setup_bulb_actions
from .diy_actions import async_setup_diy_actions
from .raw_actions import async_setup_raw_actions


@callback
def async_setup_fork_services(hass: HomeAssistant) -> None:
    """Register every fork action once."""
    async_setup_diy_actions(hass)
    async_setup_bulb_actions(hass)
    async_setup_raw_actions(hass)
