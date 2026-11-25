"""Service actions that address a string light bulb by bulb (fork).

- ``govee.paint_bulbs``: colour individual LEDs.
- ``govee.upload_bulb_positions``: store the 3D bulb position map.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Final

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.service import async_register_admin_service

from .api import raw_router
from .api.protocol import GoveeProtocolError, paint, positions
from .api.protocol.errors import PositionMapError
from .const import DOMAIN
from .coordinator import GoveeCoordinator
from .upstream_access import coordinator_for_device, device_not_found
from .zone_state import profile_for

_LOGGER = logging.getLogger(__name__)

ATTR_DEVICE_ID: Final = "device_id"
ATTR_RGB_COLOR: Final = "rgb_color"
ATTR_POSITIONS: Final = "positions"
ATTR_NORMALIZE: Final = "normalize"
ATTR_COLORS: Final = "colors"
ATTR_BULBS: Final = "bulbs"
ATTR_BASE_COLOR: Final = "base_color"
ATTR_BASE_BRIGHTNESS: Final = "base_brightness"

SERVICE_PAINT_BULBS: Final = "paint_bulbs"
SERVICE_UPLOAD_BULB_POSITIONS: Final = "upload_bulb_positions"

MAX_PAINT_FRAMES: Final = 64
"""Most frames one paint may take."""

# Positions are checked in the handler, so a bad point gets a translated error.
SERVICE_UPLOAD_BULB_POSITIONS_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Required(ATTR_POSITIONS): vol.All(cv.ensure_list, list),
        vol.Optional(ATTR_NORMALIZE, default=True): cv.boolean,
    }
)

RGB_SCHEMA = vol.All(vol.ExactSequence((cv.byte, cv.byte, cv.byte)), vol.Coerce(tuple))

SERVICE_PAINT_BULBS_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Optional(ATTR_COLORS, default=list): vol.All(
            cv.ensure_list,
            [
                vol.Schema(
                    {
                        vol.Required(ATTR_RGB_COLOR): RGB_SCHEMA,
                        vol.Required(ATTR_BULBS): vol.All(cv.ensure_list, [vol.Any(cv.positive_int, cv.string)]),
                    }
                )
            ],
        ),
        vol.Optional(ATTR_BASE_COLOR, default=(0, 0, 0)): RGB_SCHEMA,
        vol.Optional(ATTR_BASE_BRIGHTNESS, default=100): vol.All(vol.Coerce(int), vol.Range(min=0, max=100)),
    }
)


def _position_triples(raw: list[Any]) -> list[tuple[float, float, float]]:
    """Each position as a triple of finite real numbers.

    Raises:
        ServiceValidationError: ``invalid_bulb_positions`` for the first entry
            that is not three finite numbers.
    """
    triples: list[tuple[float, float, float]] = []
    for index, point in enumerate(raw):
        if (
            not isinstance(point, (list, tuple))
            or len(point) != 3
            or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in point)
        ):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_bulb_positions",
                translation_placeholders={"reason": f"position {index} is not a list of three numbers"},
            )
        triples.append((point[0], point[1], point[2]))
    return triples


def _as_int(value: float) -> float | int:
    """``value`` as an int when it is integral, else unchanged."""
    return int(value) if isinstance(value, float) and value.is_integer() else value


async def async_upload_bulb_positions_handler(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle ``govee.upload_bulb_positions``.

    Stores a 3D position map on a string light, replacing the one it holds.
    Packets go one per raw ``ptReal`` message over one raw pipe,
    :data:`.positions.MESSAGE_GAP_SECONDS` apart, so a 300-bulb map takes
    about 14 seconds. The LAN pipe sends each packet once.

    Raises:
        ServiceValidationError: Unknown or unsupported device, a position that
            is not three numbers, the wrong number of positions, or (with
            ``normalize`` off) a coordinate that is not an integer in range.
        HomeAssistantError: ``bulb_positions_failed`` with the packets sent,
            when no raw pipe took a packet. Packets already sent are not
            rolled back.
    """
    raw_id = call.data[ATTR_DEVICE_ID]
    found = coordinator_for_device(hass, raw_id)
    if found is None:
        raise device_not_found(raw_id)
    coordinator, device_id = found
    device = coordinator.devices[device_id]

    profile = profile_for(device.sku)
    if profile is None or not profile.position_map_bulbs:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="bulb_positions_unsupported",
            translation_placeholders={"device": device.name},
        )

    triples = _position_triples(call.data[ATTR_POSITIONS])
    if len(triples) != profile.position_map_bulbs:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="bulb_position_count",
            translation_placeholders={
                "device": device.name,
                "count": str(len(triples)),
                "expected": str(profile.position_map_bulbs),
            },
        )

    if call.data[ATTR_NORMALIZE]:
        points: list[tuple[Any, Any, Any]] = list(positions.normalize_positions(triples))
    else:
        points = [(_as_int(x), _as_int(y), _as_int(z)) for x, y, z in triples]
    try:
        frames = positions.position_map_frames(points, bulb_count=profile.position_map_bulbs)
    except PositionMapError as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_bulb_positions",
            translation_placeholders={"reason": str(err)},
        ) from err

    sent = await raw_router.async_route_upload(
        coordinator,
        device_id,
        device.sku,
        profile,
        [[frame] for frame in frames],
        gap_seconds=positions.MESSAGE_GAP_SECONDS,
        what="bulb position map",
        repeat=False,
    )
    if sent < len(frames):
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="bulb_positions_failed",
            translation_placeholders={"device": device.name, "sent": str(sent), "total": str(len(frames))},
        )
    _LOGGER.debug("Uploaded a %d-bulb position map to %s in %d packets", len(points), device.name, len(frames))


def _expand_bulbs(entries: list[Any], bulb_count: int) -> list[int]:
    """Indexes from a mix of numbers and ``"first-last"`` ranges.

    A range is checked against ``bulb_count`` before it is expanded, so its
    size is bounded by the light. Single indexes are checked by the encoder.

    Raises:
        ValueError: An entry is neither a number nor an ascending range, or a
            range reaches outside ``0`` to ``bulb_count - 1``.
    """
    indexes: list[int] = []
    for entry in entries:
        if isinstance(entry, int):
            indexes.append(entry)
            continue
        first, dash, last = str(entry).partition("-")
        if not dash:
            indexes.append(int(first))
            continue
        low, high = int(first), int(last)
        if high < low:
            raise ValueError(f"range {entry!r} runs backwards")
        if low < 0 or high >= bulb_count:
            raise ValueError(f"range {entry!r} is outside 0-{bulb_count - 1}")
        indexes.extend(range(low, high + 1))
    return indexes


async def async_paint_bulbs_handler(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle ``govee.paint_bulbs``.

    Colours the named LEDs and shows the base colour on every other one. The
    paint replaces whatever the light was showing, including an earlier paint.
    Its messages go over one raw pipe, :data:`.paint.MESSAGE_GAP_SECONDS` apart.

    Raises:
        HomeAssistantError: ``command_failed`` when no raw pipe took the first
            message, ``bulb_paint_incomplete`` with the packets sent when a
            later one was refused.
    """
    raw_id = call.data[ATTR_DEVICE_ID]
    found = coordinator_for_device(hass, raw_id)
    if found is None:
        raise device_not_found(raw_id)
    coordinator, device_id = found
    device = coordinator.devices[device_id]

    profile = profile_for(device.sku)
    if profile is None or not profile.paint_bulbs:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="bulb_paint_unsupported",
            translation_placeholders={"device": device.name},
        )

    try:
        colors = [(entry[ATTR_RGB_COLOR], _expand_bulbs(entry[ATTR_BULBS], profile.paint_bulbs)) for entry in call.data[ATTR_COLORS]]
        frames = paint.paint_frames(
            colors,
            bulb_count=profile.paint_bulbs,
            base_color=call.data[ATTR_BASE_COLOR],
            base_brightness=call.data[ATTR_BASE_BRIGHTNESS],
        )
        if len(frames) > MAX_PAINT_FRAMES:
            raise ValueError(f"the paint needs {len(frames)} frames, the limit is {MAX_PAINT_FRAMES}")
    except (ValueError, GoveeProtocolError) as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_bulb_paint",
            translation_placeholders={"device": device.name, "reason": str(err)},
        ) from err

    messages = paint.messages(frames)
    sent = await raw_router.async_route_upload(
        coordinator,
        device_id,
        device.sku,
        profile,
        messages,
        gap_seconds=paint.MESSAGE_GAP_SECONDS,
        what="bulb paint",
    )
    if not sent:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="command_failed",
            translation_placeholders={"device": device.name},
        )
    if sent < len(messages):
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="bulb_paint_incomplete",
            translation_placeholders={
                "device": device.name,
                "sent": str(sum(len(message) for message in messages[:sent])),
                "total": str(len(frames)),
            },
        )

    clear_optimistic_scene(coordinator, device_id)


def clear_optimistic_scene(coordinator: GoveeCoordinator, device_id: str) -> None:
    """Drop the scene shown for a light that was just given something else."""
    state = coordinator.get_state(device_id)
    if state is not None and (state.active_scene or state.active_scene_name):
        state.active_scene = None
        state.active_scene_name = None
        state.active_scene_param_id = None
        coordinator.async_update_listeners()


@callback
def async_setup_bulb_actions(hass: HomeAssistant) -> None:
    """Register the bulb actions."""

    async def _paint_bulbs(call: ServiceCall) -> None:
        await async_paint_bulbs_handler(hass, call)

    async def _upload_bulb_positions(call: ServiceCall) -> None:
        await async_upload_bulb_positions_handler(hass, call)

    hass.services.async_register(DOMAIN, SERVICE_PAINT_BULBS, _paint_bulbs, schema=SERVICE_PAINT_BULBS_SCHEMA)
    # Admin-only: it overwrites the map the lamp's 3D effects are drawn on.
    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_UPLOAD_BULB_POSITIONS,
        _upload_bulb_positions,
        schema=SERVICE_UPLOAD_BULB_POSITIONS_SCHEMA,
    )
