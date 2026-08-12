"""Custom services for Govee integration.

Provides services for:
- Refresh all scenes
- Control segment colors
- Apply a DIY effect to a multi-zone lamp (fork)
- Send raw commands (advanced)
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.target import (
    TargetSelection,
    async_extract_referenced_entity_ids,
)

from .api.protocol import (
    DIRECTIONS,
    MODE_NONE,
    DeviceProfile,
    DiyEffectSpec,
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
from .models import GoveeDevice, RGBColor, SegmentColorCommand

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
# The per-zone parameters are FLAT (`ripple_mode`, `ring_speed`, ...) rather
# than one JSON object per zone: flat fields are the only shape the service UI
# can give an individual selector to, and an automation editor that can offer a
# speed slider and a mode dropdown is worth more than the nesting saved.
#
# The zone grouping is recovered from the `<zone_key>_` prefix, so the field
# names stay in lockstep with the profile's DIY zone keys by construction.
#
# `*_mode` takes either a name from that zone's table ("twinkle") or a raw int:
# the ripple's table is known incomplete and the hardware demonstrably accepts
# ring-enum ints on it, so refusing ints here would refuse effects the lamp can
# play. That is also why the UI selector is a dropdown with `custom_value`.
_DIY_COLORS = vol.All(
    cv.ensure_list,
    [vol.All(vol.ExactSequence((cv.byte, cv.byte, cv.byte)), vol.Coerce(tuple))],
)
_DIY_MODE = vol.Any(vol.All(vol.Coerce(int), vol.Range(min=0, max=255)), cv.string)
_DIY_PERCENT = vol.All(vol.Coerce(int), vol.Range(min=1, max=100))

# Zone key -> its flat field suffixes, longest tail first. The ripple is the
# only zone whose wire record has a direction/flow-rate tail; the ring encoder
# rejects them outright, so they are not offered for it.
DIY_ZONE_FIELDS: Mapping[str, tuple[str, ...]] = {
    "ripple": ("mode", "speed", "colors", "direction", "flow_rate"),
    "ring": ("mode", "speed", "colors"),
}


def _zone_field(zone_key: str, suffix: str) -> str:
    """The flat service-field name for one zone parameter."""
    return f"{zone_key}_{suffix}"


def _require_mode_with_zone_fields(data: dict[str, Any]) -> dict[str, Any]:
    """Refuse a zone that is named by some field but given no mode.

    Naming a zone is what switches it *on* in the uploaded effect, and there is
    no defensible default mode to invent for it — the old nested schema made
    ``mode`` required inside a zone record for the same reason.
    """
    for zone_key, suffixes in DIY_ZONE_FIELDS.items():
        present = [s for s in suffixes if _zone_field(zone_key, s) in data]
        if present and "mode" not in present:
            raise vol.Invalid(
                f"{_zone_field(zone_key, 'mode')} is required when any other {zone_key} field is given",
                path=[_zone_field(zone_key, "mode")],
            )
    return data


SERVICE_APPLY_DIY_EFFECT_SCHEMA = vol.Schema(
    vol.All(
        {
            # Native HA targeting: entity_id / device_id / area_id / floor_id /
            # label_id, all resolved down to one Govee device by the handler.
            **cv.TARGET_SERVICE_FIELDS,
            vol.Optional("ripple_mode"): _DIY_MODE,
            vol.Optional("ripple_speed"): _DIY_PERCENT,
            vol.Optional("ripple_colors"): _DIY_COLORS,
            vol.Optional("ripple_direction"): vol.In(sorted(DIRECTIONS)),
            vol.Optional("ripple_flow_rate"): _DIY_PERCENT,
            vol.Optional("ring_mode"): _DIY_MODE,
            vol.Optional("ring_speed"): _DIY_PERCENT,
            vol.Optional("ring_colors"): _DIY_COLORS,
        },
        _require_mode_with_zone_fields,
    )
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
            HomeAssistantError: If the target does not resolve to exactly one
                DIY-capable Govee device, a mode name is not in that zone's
                table, the effect cannot be encoded (no zone on, or a zone with
                a mode and no colours), or the upload could not be sent.
        """
        coordinator, device, profile, diy = _resolve_diy_target(hass, call)
        device_id = device.device_id

        store = diy_store(coordinator)
        for zone in diy.zones:
            store.update(
                device_id,
                zone.zone_key,
                **_staged_record(zone, _zone_call_data(zone, call.data)),
            )

        await async_send_diy_effect(coordinator, device, profile, store.effects(device_id, diy))
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


def _targeted_govee_device_ids(hass: HomeAssistant, call: ServiceCall) -> list[str]:
    """Every Govee device id the call's ``target:`` points at, in a stable order.

    Resolution follows the standard Home Assistant path — the target selection
    is expanded by :func:`homeassistant.helpers.target.async_extract_referenced_entity_ids`,
    which covers ``entity_id``, ``device_id``, ``area_id``, ``floor_id`` and
    ``label_id`` in one go — and the resulting *registry* device ids are then
    turned into Govee device ids through the device registry identifiers this
    integration writes (``{(DOMAIN, device_id)}``, see :class:`.entity.GoveeEntity`).

    Entity targets are mapped through their entity-registry entry's device, so
    picking any one of a lamp's entities picks the lamp.

    A device id that is not in the registry at all but *is* a Govee device id
    known to a coordinator is accepted as-is. That keeps automations written
    against the pre-``target:`` schema (``device_id: "AA:BB:..."``) working
    instead of failing with a registry error nobody can act on.

    Args:
        hass: The Home Assistant instance.
        call: The service call carrying the target selection.

    Returns:
        Govee device ids, de-duplicated, order-stable.
    """
    selected = async_extract_referenced_entity_ids(hass, TargetSelection(call.data))

    ent_reg = er.async_get(hass)
    registry_device_ids = set(selected.referenced_devices)
    for entity_id in selected.referenced:
        entry = ent_reg.async_get(entity_id)
        if entry is not None and entry.device_id:
            registry_device_ids.add(entry.device_id)

    dev_reg = dr.async_get(hass)
    device_ids: list[str] = []
    for registry_id in sorted(registry_device_ids):
        device_entry = dev_reg.async_get(registry_id)
        if device_entry is None:
            continue
        device_ids.extend(identifier for domain, identifier in device_entry.identifiers if domain == DOMAIN)

    # Legacy / raw form: a Govee device id passed straight into `device_id`.
    for raw in sorted(selected.missing_devices):
        if _get_coordinator_for_device(hass, raw) is not None:
            device_ids.append(raw)

    return list(dict.fromkeys(device_ids))


def _resolve_diy_target(
    hass: HomeAssistant, call: ServiceCall
) -> tuple[GoveeCoordinator, GoveeDevice, DeviceProfile, DiyEffectSpec]:
    """The one DIY-capable lamp a call targets, with everything needed to upload.

    Exactly one device is required. A DIY effect is a whole authored document
    rather than a setting, and the two zones' mode tables are per-SKU, so
    fanning one call out over several lamps would either mean the same raw
    ints landing on different effects or a partial failure with some lamps
    already written — neither is a defensible "success".

    Args:
        hass: The Home Assistant instance.
        call: The service call carrying the target selection.

    Returns:
        ``(coordinator, device, profile, diy layout)``.

    Raises:
        HomeAssistantError: If the target resolves to no Govee device, to no
            DIY-capable one, or to more than one.
    """
    device_ids = _targeted_govee_device_ids(hass, call)
    if not device_ids:
        raise HomeAssistantError(
            "No Govee device in the target of this action — pick a Govee device, or "
            "one of its entities, that is known to this integration"
        )

    candidates: list[tuple[GoveeCoordinator, GoveeDevice]] = []
    for device_id in device_ids:
        coordinator = _get_coordinator_for_device(hass, device_id)
        if coordinator is None:
            continue
        candidates.append((coordinator, coordinator.devices[device_id]))
    if not candidates:
        raise HomeAssistantError(f"Govee device {', '.join(device_ids)} is not known to this integration")

    capable: list[tuple[GoveeCoordinator, GoveeDevice, tuple[DeviceProfile, DiyEffectSpec]]] = []
    for coordinator, device in candidates:
        resolved = diy_spec_for(device.sku)
        if resolved is not None:
            capable.append((coordinator, device, resolved))
    if not capable:
        listed = ", ".join(f"{device.name} ({device.sku})" for _c, device in candidates)
        raise HomeAssistantError(f"{listed} does not support DIY effects")
    if len(capable) > 1:
        listed = ", ".join(sorted(device.name for _c, device, _r in capable))
        raise HomeAssistantError(
            f"This action uploads one authored effect and takes exactly one device, "
            f"but the target resolved to {len(capable)}: {listed}"
        )

    coordinator, device, (profile, diy) = capable[0]
    return coordinator, device, profile, diy


def _zone_call_data(zone: DiyZoneSpec, data: Mapping[str, Any]) -> dict[str, Any] | None:
    """One zone's flat call fields, un-prefixed, or None when it is unmentioned.

    Args:
        zone: The zone whose ``zone_key`` prefixes its fields.
        data: The validated service-call data.

    Returns:
        ``{"mode": ..., "speed": ...}`` for a zone the call names, else None —
        which :func:`_staged_record` reads as "switch this zone off".
    """
    fields = {
        suffix: data[_zone_field(zone.zone_key, suffix)]
        for suffix in DIY_ZONE_FIELDS.get(zone.zone_key, ())
        if _zone_field(zone.zone_key, suffix) in data
    }
    return fields or None


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
        if hasattr(entry, "runtime_data") and isinstance(entry.runtime_data, GoveeCoordinator):
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
