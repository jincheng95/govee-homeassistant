"""Service actions for the Govee integration.

Provides:
- ``govee.refresh_scenes``: re-fetch the scene catalog for one or all devices.
- ``govee.set_segment_color``: set the colour of individual RGBIC segments.
- ``govee.send_raw_ptreal``: send a raw BLE ptReal frame (developer/debug aid).
- ``govee.apply_diy_effect``: compose and upload a DIY effect to a multi-zone
  lamp (fork).

Actions are registered once from ``async_setup`` so automations that reference
them validate even while no config entry is loaded (quality-scale rule
``action-setup``), and invalid input raises ``ServiceValidationError`` (rule
``action-exceptions``). ``device_id`` accepts either the Home Assistant device
registry ID (what the device selector produces) or the Govee device ID, so
automations written against the Govee ID keep working.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.service import async_register_admin_service
from homeassistant.helpers.target import (
    TargetSelection,
    async_extract_referenced_entity_ids,
)

from .api.ble_packet import calculate_checksum
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
from .segment_limit import manual_segment_count, segment_count

_LOGGER = logging.getLogger(__name__)

ATTR_DEVICE_ID = "device_id"
ATTR_RGB_COLOR = "rgb_color"
ATTR_SEGMENTS = "segments"
ATTR_FRAME = "frame"

SERVICE_REFRESH_SCENES = "refresh_scenes"
SERVICE_SET_SEGMENT_COLOR = "set_segment_color"
SERVICE_SEND_RAW_PTREAL = "send_raw_ptreal"
SERVICE_APPLY_DIY_EFFECT = "apply_diy_effect"

SERVICE_REFRESH_SCENES_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_DEVICE_ID): cv.string,
    }
)

SERVICE_SEND_RAW_PTREAL_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Required(ATTR_FRAME): cv.string,
    }
)

SERVICE_SET_SEGMENT_COLOR_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Required(ATTR_SEGMENTS): vol.All(cv.ensure_list, [cv.positive_int]),
        vol.Required(ATTR_RGB_COLOR): vol.All(
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


def _loaded_coordinators(hass: HomeAssistant) -> list[GoveeCoordinator]:
    """Return the coordinator of every loaded Govee config entry."""
    return [
        entry.runtime_data
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state is ConfigEntryState.LOADED
    ]


def _resolve_device_id(hass: HomeAssistant, raw_id: str) -> str:
    """Map a Home Assistant device registry ID to the Govee device ID.

    Anything that is not a registry ID (a Govee device ID, for instance)
    passes through unchanged.
    """
    device_entry = dr.async_get(hass).async_get(raw_id)
    if device_entry is not None:
        for domain, identifier in device_entry.identifiers:
            if domain == DOMAIN:
                return identifier
    return raw_id


def _get_coordinator_for_device(hass: HomeAssistant, raw_id: str) -> tuple[GoveeCoordinator, str] | None:
    """Return ``(coordinator, govee_device_id)`` for a device, or None if unknown."""
    device_id = _resolve_device_id(hass, raw_id)
    for coordinator in _loaded_coordinators(hass):
        if device_id in coordinator.devices:
            return coordinator, device_id
    return None


def _device_not_found(raw_id: str) -> ServiceValidationError:
    """Error for a ``device_id`` that no loaded entry knows."""
    return ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="device_not_found",
        translation_placeholders={"device_id": raw_id},
    )


async def async_refresh_scenes_handler(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle ``govee.refresh_scenes`` for one device or every device."""
    raw_id = call.data.get(ATTR_DEVICE_ID)
    if raw_id:
        found = _get_coordinator_for_device(hass, raw_id)
        if found is None:
            raise _device_not_found(raw_id)
        coordinator, device_id = found
        await coordinator.async_get_scenes(device_id, refresh=True)
        _LOGGER.debug("Refreshed scenes for device %s", device_id)
        return

    coordinators = _loaded_coordinators(hass)
    if not coordinators:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="not_loaded",
        )
    for coordinator in coordinators:
        for dev_id, device in coordinator.devices.items():
            if device.supports_scenes:
                await coordinator.async_get_scenes(dev_id, refresh=True)
    _LOGGER.debug("Refreshed scenes for all devices")


async def async_set_segment_color_handler(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle ``govee.set_segment_color``.

    Rejects any segment index outside the device's effective
    ``segment_count`` (which already factors in ``SKU_SEGMENT_OVERRIDES`` for
    SKUs like the H7075 that the API over-reports) with a
    ``ServiceValidationError``, so the caller learns why nothing happened
    instead of the cloud silently refusing the command. The fork's hardware
    cap (``segment_limit.segment_count``) applies on top.
    """
    raw_id = call.data[ATTR_DEVICE_ID]
    segments: list[int] = call.data[ATTR_SEGMENTS]
    rgb = call.data[ATTR_RGB_COLOR]

    found = _get_coordinator_for_device(hass, raw_id)
    if found is None:
        raise _device_not_found(raw_id)
    coordinator, device_id = found

    device = coordinator.devices.get(device_id)
    device_name = device.name if device is not None else device_id
    if device is not None:
        options = getattr(getattr(coordinator, "config_entry", None), "options", None)
        count = segment_count(device, manual_segment_count(options, device_id))
        bad = [index for index in segments if index >= count]
        if bad:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="segment_out_of_range",
                translation_placeholders={
                    "device": device_name,
                    "indices": ", ".join(str(index) for index in bad),
                    "count": str(count),
                },
            )

    command = SegmentColorCommand(
        segment_indices=tuple(segments),
        color=RGBColor(r=rgb[0], g=rgb[1], b=rgb[2]),
    )
    if not await coordinator.async_control_device(device_id, command):
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="command_failed",
            translation_placeholders={"device": device_name},
        )
    _LOGGER.debug("Set segments %s to color %s on device %s", segments, rgb, device_id)


async def async_send_raw_ptreal_handler(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle ``govee.send_raw_ptreal`` (developer/debug aid for issue #208).

    Sends an arbitrary BLE ptReal command frame to a device over the AWS IoT
    passthrough. Frames of 19 bytes or fewer get a checksum appended by the
    coordinator; a 20-byte frame must already carry a valid XOR checksum.
    """
    raw_id = call.data[ATTR_DEVICE_ID]
    raw_frame = call.data[ATTR_FRAME]

    cleaned = raw_frame.replace(" ", "").replace(":", "")
    try:
        frame = bytes.fromhex(cleaned)
    except ValueError:
        frame = b""

    if not cleaned or len(cleaned) % 2 != 0 or not frame or len(frame) > 20:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_ptreal_frame",
            translation_placeholders={"frame": raw_frame},
        )

    if len(frame) == 20 and calculate_checksum(list(frame[:19])) != frame[19]:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_ptreal_frame",
            translation_placeholders={"frame": raw_frame},
        )

    found = _get_coordinator_for_device(hass, raw_id)
    if found is None:
        raise _device_not_found(raw_id)
    coordinator, device_id = found

    device = coordinator.devices.get(device_id)
    device_name = device.name if device is not None else device_id

    if not await coordinator.async_send_raw_ptreal(device_id, frame):
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="ptreal_unavailable",
            translation_placeholders={"device": device_name},
        )
    _LOGGER.debug("Sent raw ptReal frame %s to device %s", frame.hex(), device_id)


async def async_apply_diy_effect_handler(hass: HomeAssistant, call: ServiceCall) -> None:
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


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the Govee service actions (called once from ``async_setup``)."""

    async def _refresh_scenes(call: ServiceCall) -> None:
        await async_refresh_scenes_handler(hass, call)

    async def _set_segment_color(call: ServiceCall) -> None:
        await async_set_segment_color_handler(hass, call)

    async def _send_raw_ptreal(call: ServiceCall) -> None:
        await async_send_raw_ptreal_handler(hass, call)

    async def _apply_diy_effect(call: ServiceCall) -> None:
        await async_apply_diy_effect_handler(hass, call)

    hass.services.async_register(
        DOMAIN,
        SERVICE_REFRESH_SCENES,
        _refresh_scenes,
        schema=SERVICE_REFRESH_SCENES_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_SEGMENT_COLOR,
        _set_segment_color,
        schema=SERVICE_SET_SEGMENT_COLOR_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_APPLY_DIY_EFFECT,
        _apply_diy_effect,
        schema=SERVICE_APPLY_DIY_EFFECT_SCHEMA,
    )
    # Admin-only: it sends arbitrary frames to the device (#208 debug aid).
    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_SEND_RAW_PTREAL,
        _send_raw_ptreal,
        schema=SERVICE_SEND_RAW_PTREAL_SCHEMA,
    )


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
    for raw_id in device_ids:
        found = _get_coordinator_for_device(hass, raw_id)
        if found is None:
            continue
        coordinator, device_id = found
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
