"""Custom services for Govee integration.

Provides services for:
- Refresh all scenes
- Control segment colors
- Apply a DIY effect to a multi-zone lamp (fork)
- Send raw commands (advanced)
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .api.protocol import (
    DIRECTIONS,
    MODE_NONE,
    DiyZoneSpec,
    GoveeProtocolError,
    resolve_mode,
)
from .const import DOMAIN
from .coordinator import GoveeCoordinator
from .diy_state import (
    DEFAULT_FLOW_RATE,
    DEFAULT_SPEED,
    async_send_diy_effect,
    diy_spec_for,
)
from .diy_state import store as diy_store
from .models import RGBColor, SegmentColorCommand

_LOGGER = logging.getLogger(__name__)

# Service names
SERVICE_REFRESH_SCENES = "refresh_scenes"
SERVICE_SET_SEGMENT_COLOR = "set_segment_color"
SERVICE_APPLY_DIY_EFFECT = "apply_diy_effect"

# Service schemas
SERVICE_REFRESH_SCENES_SCHEMA = vol.Schema(
    {
        vol.Optional("device_id"): cv.string,
    }
)

SERVICE_SET_SEGMENT_COLOR_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): cv.string,
        vol.Required("segments"): vol.All(cv.ensure_list, [cv.positive_int]),
        vol.Required("rgb_color"): vol.All(
            vol.ExactSequence((cv.byte, cv.byte, cv.byte)),
            vol.Coerce(tuple),
        ),
    }
)

# -- apply_diy_effect (fork) ------------------------------------------------
#
# One dict per DIY zone. `mode` takes either a name from that zone's table
# ("twinkle") or a raw int: the ripple's table is known incomplete and the
# hardware demonstrably accepts ring-enum ints on it, so refusing ints here
# would refuse effects the lamp can play.
_DIY_COLORS = vol.All(
    cv.ensure_list,
    [vol.All(vol.ExactSequence((cv.byte, cv.byte, cv.byte)), vol.Coerce(tuple))],
)

_DIY_ZONE_FIELDS: dict[Any, Any] = {
    vol.Required("mode"): vol.Any(
        vol.All(vol.Coerce(int), vol.Range(min=0, max=255)), cv.string
    ),
    vol.Optional("speed"): vol.All(vol.Coerce(int), vol.Range(min=1, max=100)),
    vol.Optional("colors"): _DIY_COLORS,
}

# The ripple is the only zone whose wire record has a direction/flow-rate tail;
# the ring encoder rejects them outright, so they are not offered for it.
SERVICE_APPLY_DIY_EFFECT_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): cv.string,
        vol.Optional("ripple"): vol.Schema(
            {
                **_DIY_ZONE_FIELDS,
                vol.Optional("direction"): vol.In(sorted(DIRECTIONS)),
                vol.Optional("flow_rate"): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=100)
                ),
            }
        ),
        vol.Optional("ring"): vol.Schema(dict(_DIY_ZONE_FIELDS)),
    }
)


async def async_setup_services(hass: HomeAssistant) -> None:
    """Set up Govee services."""

    async def async_refresh_scenes(call: ServiceCall) -> None:
        """Refresh scenes for device(s)."""
        device_id = call.data.get("device_id")

        # Get all coordinators
        coordinators = _get_coordinators(hass)

        for coordinator in coordinators:
            if device_id:
                # Refresh specific device
                if device_id in coordinator.devices:
                    await coordinator.async_get_scenes(device_id, refresh=True)
                    _LOGGER.info("Refreshed scenes for device %s", device_id)
            else:
                # Refresh all devices
                for dev_id, device in coordinator.devices.items():
                    if device.supports_scenes:
                        await coordinator.async_get_scenes(dev_id, refresh=True)
                _LOGGER.info("Refreshed scenes for all devices")

    async def async_set_segment_color(call: ServiceCall) -> None:
        """Set color for specific segments."""
        device_id = call.data["device_id"]
        segments = call.data["segments"]
        rgb = call.data["rgb_color"]

        coordinator = _get_coordinator_for_device(hass, device_id)
        if not coordinator:
            _LOGGER.error("Device %s not found", device_id)
            return

        color = RGBColor(r=rgb[0], g=rgb[1], b=rgb[2])
        command = SegmentColorCommand(
            segment_indices=tuple(segments),
            color=color,
        )

        await coordinator.async_control_device(device_id, command)
        _LOGGER.info(
            "Set segments %s to color %s on device %s",
            segments,
            rgb,
            device_id,
        )

    async def async_apply_diy_effect(call: ServiceCall) -> None:
        """Compose and upload a DIY effect to a multi-zone lamp (fork).

        Deliberately *self-contained*: a zone left out of the call is switched
        off in the uploaded effect, and every field a named zone omits falls
        back to a fixed default rather than to whatever the config entities
        happen to be showing. The same call therefore produces the same lamp
        every time it runs, which is what an automation needs.

        The staged records in :mod:`.diy_state` are updated to match, so the
        DIY config entities show what was actually sent instead of a draft the
        service just overwrote.

        Raises:
            HomeAssistantError: If the device is unknown, has no DIY layout, a
                mode name is not in that zone's table, the effect cannot be
                encoded (no zone on, or a zone with a mode and no colours), or
                the upload could not be sent.
        """
        device_id = call.data["device_id"]
        coordinator = _get_coordinator_for_device(hass, device_id)
        if coordinator is None:
            raise HomeAssistantError(
                f"Govee device {device_id} is not known to this integration"
            )
        device = coordinator.devices[device_id]

        resolved = diy_spec_for(device.sku)
        if resolved is None:
            raise HomeAssistantError(
                f"{device.name} ({device.sku}) does not support DIY effects"
            )
        profile, diy = resolved

        store = diy_store(coordinator)
        for zone in diy.zones:
            store.update(
                device_id,
                zone.zone_key,
                **_staged_record(zone, call.data.get(zone.zone_key)),
            )

        await async_send_diy_effect(
            coordinator, device, profile, store.effects(device_id, diy)
        )
        _LOGGER.info("Applied DIY effect to %s", device.name)

    # Register services
    hass.services.async_register(
        DOMAIN,
        SERVICE_REFRESH_SCENES,
        async_refresh_scenes,
        schema=SERVICE_REFRESH_SCENES_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_SEGMENT_COLOR,
        async_set_segment_color,
        schema=SERVICE_SET_SEGMENT_COLOR_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_APPLY_DIY_EFFECT,
        async_apply_diy_effect,
        schema=SERVICE_APPLY_DIY_EFFECT_SCHEMA,
    )

    _LOGGER.debug("Govee services registered")


def _staged_record(zone: DiyZoneSpec, data: dict[str, Any] | None) -> dict[str, Any]:
    """The complete staged record one service call means for one zone.

    Every field is filled in, never merged with what was staged before: a call
    that names a zone but omits its speed means "the default speed", not "keep
    the slider where the user left it". A zone the call does not mention at all
    is switched off.

    Args:
        zone: The zone's DIY layout entry, which supplies the mode table and
            says whether the record even carries a direction / flow rate.
        data: That zone's dict from the service call, or None if omitted.

    Returns:
        Keyword arguments for :meth:`DiyStateStore.update`.

    Raises:
        HomeAssistantError: If the mode name is not in this zone's table.
    """
    if data is None:
        return {"mode": MODE_NONE}
    try:
        mode = resolve_mode(zone, data["mode"])
    except GoveeProtocolError as err:
        raise HomeAssistantError(f"DIY zone {zone.zone_key!r}: {err}") from err
    record: dict[str, Any] = {
        "mode": mode,
        "speed": int(data.get("speed", DEFAULT_SPEED)),
        "colors": tuple(tuple(color) for color in data.get("colors", ())),
    }
    if zone.has_direction:
        record["direction"] = DIRECTIONS[data.get("direction", "cw")]
    if zone.has_flow_rate:
        record["flow_rate"] = int(data.get("flow_rate", DEFAULT_FLOW_RATE))
    return record


async def async_unload_services(hass: HomeAssistant) -> None:
    """Unload Govee services."""
    hass.services.async_remove(DOMAIN, SERVICE_REFRESH_SCENES)
    hass.services.async_remove(DOMAIN, SERVICE_SET_SEGMENT_COLOR)
    hass.services.async_remove(DOMAIN, SERVICE_APPLY_DIY_EFFECT)
    _LOGGER.debug("Govee services unloaded")


def _get_coordinators(hass: HomeAssistant) -> list[GoveeCoordinator]:
    """Get all Govee coordinators."""
    coordinators = []
    for entry in hass.config_entries.async_entries(DOMAIN):
        if hasattr(entry, "runtime_data") and isinstance(
            entry.runtime_data, GoveeCoordinator
        ):
            coordinators.append(entry.runtime_data)
    return coordinators


def _get_coordinator_for_device(
    hass: HomeAssistant,
    device_id: str,
) -> GoveeCoordinator | None:
    """Get coordinator that manages a specific device."""
    for coordinator in _get_coordinators(hass):
        if device_id in coordinator.devices:
            return coordinator
    return None
