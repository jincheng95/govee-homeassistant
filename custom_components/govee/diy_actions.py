"""The ``govee.apply_diy_effect`` service action (fork).

One authored effect goes to one device. A multi-zone lamp takes per-zone fields
(``ripple_mode``, ``ring_speed`` and so on). A string light takes one
whole-light effect, a basic or combined effect, or ``layers``.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, Final

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.target import (
    TargetSelection,
    async_extract_referenced_entity_ids,
)

from .api import raw_router
from .api.protocol import (
    DIRECTIONS,
    MODE_NONE,
    DeviceProfile,
    DiyEffectSpec,
    DiyZoneSpec,
    GoveeProtocolError,
    resolve_mode,
)
from .api.protocol import h70cx, h70cx_basic
from .bulb_actions import RGB_SCHEMA, clear_optimistic_scene
from .const import DOMAIN
from .coordinator import GoveeCoordinator
from .diy_state import (
    DEFAULT_FLOW_RATE,
    DEFAULT_SPEED,
    async_send_diy_effect,
    diy_spec_for,
)
from .diy_state import store as diy_store
from .models import GoveeDevice
from .upstream_access import coordinator_for_device
from .zone_state import profile_for

_LOGGER = logging.getLogger(__name__)

SERVICE_APPLY_DIY_EFFECT: Final = "apply_diy_effect"

ATTR_COLORS = "colors"
ATTR_EFFECT = "effect"
ATTR_SPEED = "speed"
ATTR_DIRECTION = "direction"
ATTR_COMBINE = "combine"
ATTR_SHAPE = "shape"
ATTR_CENTER = "center"
ATTR_LAYERS = "layers"
ATTR_PARAMS = "params"
ATTR_LAYER_MODE = "layer_mode"

# Top-level whole-light fields a layered call leaves out.
_SINGLE_EFFECT_FIELDS = (
    ATTR_EFFECT,
    ATTR_COLORS,
    ATTR_SPEED,
    ATTR_DIRECTION,
    ATTR_SHAPE,
    ATTR_CENTER,
    ATTR_PARAMS,
    ATTR_COMBINE,
)

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
_DIY_FLOW_RATE = vol.All(vol.Coerce(int), vol.Range(min=0, max=100))

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


_STRING_EFFECT = vol.All(cv.string, vol.Lower, vol.In(sorted({*h70cx.TEMPLATES, *h70cx_basic.NAMES})))
_STRING_COLORS = vol.All(cv.ensure_list, vol.Length(min=1, max=h70cx.MAX_COLORS), [RGB_SCHEMA])
_STRING_SPEED = vol.All(vol.Coerce(int), vol.Range(min=0, max=h70cx.MAX_SPEED))
_STRING_DIRECTION = vol.All(cv.string, vol.Lower, vol.In(sorted(h70cx.DIRECTION_IDS)))
_SHAPE = vol.All(cv.string, vol.Lower, vol.In(list(h70cx.SHAPES)))
_CENTER = vol.All(cv.string, vol.Lower, vol.In(list(h70cx.CENTERS)))
_PARAMS = vol.All(cv.ensure_list, vol.Length(min=1), [cv.byte])

# A layer accepts every whole-light name so that an older-format one is
# refused by the handler with a translated reason.
_LAYER_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_EFFECT): _STRING_EFFECT,
        vol.Required(ATTR_COLORS): _STRING_COLORS,
        vol.Optional(ATTR_SPEED): _STRING_SPEED,
        vol.Optional(ATTR_DIRECTION): _STRING_DIRECTION,
        vol.Optional(ATTR_SHAPE): _SHAPE,
        vol.Optional(ATTR_CENTER): _CENTER,
        vol.Optional(ATTR_PARAMS): _PARAMS,
    }
)

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
            vol.Optional("ripple_flow_rate"): _DIY_FLOW_RATE,
            vol.Optional("ring_mode"): _DIY_MODE,
            vol.Optional("ring_speed"): _DIY_PERCENT,
            vol.Optional("ring_colors"): _DIY_COLORS,
            # Whole-light effect, for lamps with no zones (H70CX family).
            vol.Optional(ATTR_EFFECT): _STRING_EFFECT,
            vol.Optional(ATTR_COLORS): _STRING_COLORS,
            vol.Optional(ATTR_SPEED): _STRING_SPEED,
            vol.Optional(ATTR_DIRECTION): _STRING_DIRECTION,
            vol.Optional(ATTR_COMBINE): vol.All(
                cv.ensure_list,
                vol.Length(min=1, max=h70cx_basic.MAX_COMBINED),
                [vol.All(cv.string, vol.Lower, vol.In(h70cx_basic.COMBINABLE))],
            ),
            vol.Optional(ATTR_SHAPE): _SHAPE,
            vol.Optional(ATTR_CENTER): _CENTER,
            vol.Optional(ATTR_PARAMS): _PARAMS,
            vol.Optional(ATTR_LAYERS): vol.All(cv.ensure_list, vol.Length(min=1), [_LAYER_SCHEMA]),
            vol.Optional(ATTR_LAYER_MODE): vol.All(cv.string, vol.Lower, vol.In(list(h70cx.LAYER_MODES))),
        },
        _require_mode_with_zone_fields,
    )
)


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
    string_target = _string_effect_target(hass, call)
    if string_target is not None:
        await _async_apply_string_effect(*string_target, call.data)
        _LOGGER.info("Applied DIY effect to %s", string_target[1].name)
        return

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


async def _async_apply_string_effect(
    coordinator: GoveeCoordinator, device: GoveeDevice, profile: DeviceProfile, data: Mapping[str, Any]
) -> None:
    """Upload one composed, basic, combo or layered whole-light effect (H70CX family)."""
    if ATTR_LAYER_MODE in data and ATTR_LAYERS not in data:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="layer_mode_without_layers",
            translation_placeholders={"device": device.name},
        )
    if ATTR_LAYERS in data:
        frames = _layered_frames(device, data)
    else:
        frames = _single_effect_frames(device, data)

    if not await raw_router.async_route_frames(
        coordinator, device.device_id, device.sku, profile, frames, what="string effect"
    ):
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="command_failed",
            translation_placeholders={"device": device.name},
        )
    clear_optimistic_scene(coordinator, device.device_id)


def _layered_frames(device: GoveeDevice, data: Mapping[str, Any]) -> list[bytes]:
    """Frames for the ``layers`` field, refusing it beside the single-effect fields."""
    mixed = [field for field in _SINGLE_EFFECT_FIELDS if field in data]
    if mixed:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="layers_with_effect_fields",
            translation_placeholders={"device": device.name, "fields": ", ".join(mixed)},
        )
    items = data[ATTR_LAYERS]
    if not h70cx.MIN_LAYERS <= len(items) <= h70cx.MAX_LAYERS:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_layer_count",
            translation_placeholders={
                "device": device.name,
                "count": str(len(items)),
                "min": str(h70cx.MIN_LAYERS),
                "max": str(h70cx.MAX_LAYERS),
            },
        )
    layers = [
        h70cx.Layer(
            effect=item[ATTR_EFFECT],
            colors=item[ATTR_COLORS],
            speed=item.get(ATTR_SPEED),
            direction=item.get(ATTR_DIRECTION),
            shape=item.get(ATTR_SHAPE),
            center=item.get(ATTR_CENTER),
            params=item.get(ATTR_PARAMS),
        )
        for item in items
    ]
    try:
        return h70cx.layered_frames(layers, data.get(ATTR_LAYER_MODE, h70cx.DEFAULT_LAYER_MODE))
    except h70cx.LayerError as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_string_effect_layer",
            translation_placeholders={"device": device.name, "layer": str(err.layer), "reason": err.reason},
        ) from err
    except GoveeProtocolError as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_string_effect",
            translation_placeholders={"device": device.name, "reason": str(err)},
        ) from err


def _single_effect_frames(device: GoveeDevice, data: Mapping[str, Any]) -> list[bytes]:
    """Frames for the top-level whole-light fields."""
    try:
        if ATTR_EFFECT not in data or ATTR_COLORS not in data:
            raise GoveeProtocolError("this light takes the whole-light fields: effect and colors, or layers")
        effect = data[ATTR_EFFECT]
        if effect in h70cx_basic.NAMES:
            for option in (ATTR_SHAPE, ATTR_CENTER, ATTR_PARAMS):
                if option in data:
                    raise GoveeProtocolError(f"{effect} has no {option}")
            return h70cx_basic.effect_frames(
                effect,
                data[ATTR_COLORS],
                speed=data.get(ATTR_SPEED),
                direction=data.get(ATTR_DIRECTION),
                combine=data.get(ATTR_COMBINE),
            )
        if ATTR_COMBINE in data:
            raise GoveeProtocolError(f"only {h70cx_basic.COMBO} takes effects to combine")
        return h70cx.effect_frames(
            effect,
            data[ATTR_COLORS],
            speed=data.get(ATTR_SPEED),
            direction=data.get(ATTR_DIRECTION),
            shape=data.get(ATTR_SHAPE),
            center=data.get(ATTR_CENTER),
            params=data.get(ATTR_PARAMS),
        )
    except GoveeProtocolError as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_string_effect",
            translation_placeholders={"device": device.name, "reason": str(err)},
        ) from err


def _string_effect_target(
    hass: HomeAssistant, call: ServiceCall
) -> tuple[GoveeCoordinator, GoveeDevice, DeviceProfile] | None:
    """The one whole-light-effect device a DIY call targets, or None.

    None hands the call to the zone path, which also words every error for a
    target that names no such device.

    Raises:
        HomeAssistantError: The target mixes such a device with another
            DIY-capable one.
    """
    capable = 0
    found_string: tuple[GoveeCoordinator, GoveeDevice, DeviceProfile] | None = None
    for raw_id in _targeted_govee_device_ids(hass, call):
        found = coordinator_for_device(hass, raw_id)
        if found is None:
            continue
        coordinator, device_id = found
        device = coordinator.devices[device_id]
        if diy_spec_for(device.sku) is not None:
            capable += 1
            continue
        profile = profile_for(device.sku)
        if profile is not None and profile.scene_upload:
            capable += 1
            found_string = (coordinator, device, profile)
    if found_string is None:
        return None
    if capable > 1:
        raise HomeAssistantError(
            f"This action uploads one authored effect and takes exactly one device, but the target resolved to {capable}"
        )
    return found_string


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
        if coordinator_for_device(hass, raw) is not None:
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
        found = coordinator_for_device(hass, raw_id)
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


@callback
def async_setup_diy_actions(hass: HomeAssistant) -> None:
    """Register ``govee.apply_diy_effect``."""

    async def _apply_diy_effect(call: ServiceCall) -> None:
        await async_apply_diy_effect_handler(hass, call)

    hass.services.async_register(
        DOMAIN,
        SERVICE_APPLY_DIY_EFFECT,
        _apply_diy_effect,
        schema=SERVICE_APPLY_DIY_EFFECT_SCHEMA,
    )
