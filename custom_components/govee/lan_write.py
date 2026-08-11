"""Raw-LAN fast path for zone power toggles (fork feature).

Why this exists
---------------
The H60B0 uplighter's three zone switches (``rippleLightToggle``,
``sideLightToggle``, ``bottomLightToggle``) go out over the cloud today, which
means a 1-3 second round trip for the controls that get pressed most. The same
intent is one 20-byte UDP frame on the LAN — ``33 30 <zone> <00|01>`` — and the
lamp acts on it as fast as the packet arrives.

This module is the whole feature. It sits between the switch entity and the
codec in :mod:`.api.protocol`, decides whether the LAN write is *safe* for this
device, sends it, and applies the optimistic state the LAN transport cannot
confirm. ``switch.py`` carries a single line per toggle method.

Deliberate scope: zone power only. Colour, brightness, segments and scenes stay
on the cloud path in this branch — the point is to prove the latency claim on
the smallest possible surface.

What makes the LAN write safe
-----------------------------
Four gates, all of which must pass, or the caller falls through to the cloud
path unchanged:

1. The ``enable_lan_raw_write`` option is on (it defaults to **off**).
2. The toggle instance maps to a zone key we know (:data:`ZONE_KEY_BY_TOGGLE`).
3. The device's SKU has a raw-LAN profile that declares ``ZONE_POWER`` for that
   zone, *and* the zone has a zone byte. A profile that does not know the bytes
   raises out of the codec rather than guessing (see ``profiles.UNKNOWN``).
4. The coordinator currently has a live LAN correlation (an IP) for the device.

Nothing here ever raises at the caller: every failure path returns ``False``,
which means "I did not handle it, do what you did before".

Write-only, therefore optimistic and repeated
---------------------------------------------
The raw LAN channel is fire-and-forget by measurement, not by choice: devices
answer no ``ptReal`` query and a LAN command produces no cloud status push
either (see :mod:`.api.protocol.client`). Two consequences are baked in here:

* **Optimistic state.** There is no echo to wait for, so the switch state is
  set the moment the datagrams are away.
* **Repeats.** UDP is lossy and unacknowledged, and a dropped zone-power frame
  would leave the lamp disagreeing with the UI until something else corrected
  it. The frames are idempotent — ``33 30 03 01`` says "downlight on", not
  "toggle the downlight" — so sending :data:`LAN_WRITE_REPEATS` copies a few
  tens of milliseconds apart costs nothing but makes a single lost packet a
  non-event. Repeats are only free because the frame is *absolute*: replaying
  it can never invert the state, which a "toggle" frame would.

The hardware constraint
-----------------------
The H60B0 can light at most two of its three zones at once: switching one on
can knock another off inside the lamp, with no notification. That limit is
declared in the profile table as
:class:`~.api.protocol.profiles.MaxSimultaneousZones` and is read out of it here
— the ordering of ``zone_keys`` in the table is the displacement order (the
first-listed zone yields first, which is why the H60B0 note reads "enabling the
downlight kicks the ripple off"). When a LAN write displaces a zone, that
zone's switch entity gets its optimistic state cleared too, or the UI would
show a zone lit that the lamp has already dropped.

Fork-maintenance note: the coordinator and entity internals this module reaches
into are read in ONE place each (the accessor block at the bottom), so an
upstream rename is a one-line fix here rather than a scattered merge conflict.
This is the same discipline :mod:`.lan_nudge` established.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any, Final

from .api.protocol import (
    Capability,
    DeviceProfile,
    LanUdpClient,
    GoveeCodec,
    GoveeProtocolError,
    MaxSimultaneousZones,
    get_profile,
)
from .const import CONF_ENABLE_LAN_RAW_WRITE, DEFAULT_ENABLE_LAN_RAW_WRITE

if TYPE_CHECKING:
    from .coordinator import GoveeCoordinator

_LOGGER = logging.getLogger(__name__)

# Cloud capability instance -> raw-LAN profile zone key. The cloud names the
# parts after what they look like; the profile table names them after what the
# firmware calls them, so the join has to be explicit.
ZONE_KEY_BY_TOGGLE: Final[dict[str, str]] = {
    "rippleLightToggle": "ripple",
    "sideLightToggle": "ring",
    "bottomLightToggle": "downlight",
}

# How many copies of the (idempotent) frame go out per press. See the module
# docstring: UDP is unacknowledged, and an absolute frame makes repeats free.
LAN_WRITE_REPEATS: Final = 3
# Gap between copies. Long enough that a burst-drop on a busy AP does not eat
# every copy, short enough to stay far inside "instant" for a human finger.
LAN_WRITE_GAP_SECONDS: Final = 0.03

_CLIENT: LanUdpClient | None = None


def _client() -> LanUdpClient:
    """The module-wide write-only UDP client (binds nothing, holds no socket)."""
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = LanUdpClient()
    return _CLIENT


async def async_zone_power(entity: Any, *, on: bool) -> bool:
    """Try to switch one zone over raw LAN.

    Args:
        entity: The ``GoveeNamedLightSwitchEntity`` being toggled.
        on: Target state for the zone.

    Returns:
        True when the LAN write was sent and the optimistic state applied — the
        caller must then do nothing else. False means "not handled": the caller
        falls through to the cloud path exactly as before.
    """
    coordinator = _coordinator(entity)
    if not _option_enabled(coordinator):
        return False

    instance = _toggle_instance(entity)
    zone_key = ZONE_KEY_BY_TOGGLE.get(instance)
    if zone_key is None:
        return False

    device_id = _device_id(entity)
    profile = _profile(_sku(entity))
    if profile is None or not _zone_power_supported(profile, zone_key):
        _LOGGER.debug(
            "Govee LAN write: %s/%s has no raw zone-power profile — using cloud transport",
            _sku(entity),
            instance,
        )
        return False

    ip = _lan_ip(coordinator, device_id)
    if ip is None:
        _LOGGER.debug(
            "Govee LAN write: %s not reachable on the LAN — using cloud transport",
            device_id,
        )
        return False

    started = time.monotonic()
    try:
        frame = GoveeCodec(profile).zone_power(zone_key, on)
        await _async_send_repeated(ip, frame)
    except (GoveeProtocolError, OSError) as err:
        _LOGGER.debug(
            "Govee LAN write: raw send to %s (%s) failed (%s) — using cloud transport",
            device_id,
            ip,
            err,
        )
        return False

    elapsed_ms = (time.monotonic() - started) * 1000
    _apply_optimistic(entity, profile, zone_key, on)
    _LOGGER.debug(
        "Govee LAN write: %s zone %s -> %s via LAN transport %s in %.1f ms (%d x frame %s)",
        device_id,
        zone_key,
        "on" if on else "off",
        ip,
        elapsed_ms,
        LAN_WRITE_REPEATS,
        frame.hex(" "),
    )
    return True


async def _async_send_repeated(ip: str, frame: bytes) -> None:
    """Send the same frame :data:`LAN_WRITE_REPEATS` times, spaced out."""
    client = _client()
    for attempt in range(LAN_WRITE_REPEATS):
        if attempt:
            await asyncio.sleep(LAN_WRITE_GAP_SECONDS)
        await client.async_send_frame(ip, frame)


def _zone_power_supported(profile: DeviceProfile, zone_key: str) -> bool:
    """Whether this profile can express zone power for ``zone_key``."""
    try:
        zone = profile.zone(zone_key)
    except GoveeProtocolError:
        return False
    if zone.zone_byte is None:
        return False
    return profile.supports(Capability.ZONE_POWER, zone=zone_key)


def _profile(sku: str) -> DeviceProfile | None:
    """The raw-LAN profile for ``sku``, or None when the table has no entry."""
    try:
        return get_profile(sku)
    except GoveeProtocolError:
        return None


# ----------------------------------------------------------------------
# Optimistic state, including the zones the hardware displaces
# ----------------------------------------------------------------------


def _apply_optimistic(entity: Any, profile: DeviceProfile, zone_key: str, on: bool) -> None:
    """Set the switch state now, and clear any zone the lamp just dropped."""
    _set_state(entity, on)
    if not on:
        return

    siblings = _sibling_zone_switches(entity)
    lit = {key for key, other in siblings.items() if _is_on(other)}
    for displaced in _displaced_zone_keys(profile, zone_key, lit):
        _LOGGER.debug(
            "Govee LAN write: zone %s displaced %s (hardware limit)",
            zone_key,
            displaced,
        )
        _set_state(siblings[displaced], False)


def _displaced_zone_keys(profile: DeviceProfile, zone_key: str, lit: set[str]) -> list[str]:
    """Zones the hardware turns off because ``zone_key`` was turned on.

    The limit and the zone set come from the profile's
    :class:`MaxSimultaneousZones` entries; the *order* of ``zone_keys`` in the
    table is the displacement order, first-listed first. Nothing about the
    H60B0 specifically is encoded here.
    """
    displaced: list[str] = []
    for constraint in _constraints_for(profile, zone_key):
        on_now = [key for key in constraint.zone_keys if key == zone_key or (key in lit and key not in displaced)]
        for key in constraint.zone_keys:
            if len(on_now) <= constraint.limit:
                break
            if key == zone_key or key not in on_now:
                continue
            on_now.remove(key)
            displaced.append(key)
    return displaced


def _constraints_for(profile: DeviceProfile, zone_key: str) -> tuple[MaxSimultaneousZones, ...]:
    """The profile's simultaneity constraints that cover ``zone_key``."""
    return tuple(constraint for constraint in profile.constraints if zone_key in constraint.zone_keys)


def _sibling_zone_switches(entity: Any) -> dict[str, Any]:
    """The other zone switches of the same device, keyed by profile zone key.

    Reads the entity platform's registry, which is the only place the sibling
    entities of one device are enumerable from an entity. Absent (an entity not
    yet added to a platform, as in unit tests) simply means no siblings to
    correct.
    """
    platform = getattr(entity, "platform", None)
    registry = getattr(platform, "entities", None) or {}
    siblings: dict[str, Any] = {}
    for other in registry.values():
        if other is entity or _device_id(other) != _device_id(entity):
            continue
        key = ZONE_KEY_BY_TOGGLE.get(_toggle_instance(other))
        if key is not None:
            siblings[key] = other
    return siblings


# ----------------------------------------------------------------------
# Coordinator / entity internals, read in exactly one place each. An upstream
# rename of any of these is a one-line fix in this block.
# ----------------------------------------------------------------------


def _coordinator(entity: Any) -> GoveeCoordinator:
    """The coordinator owning ``entity``."""
    return entity.coordinator  # type: ignore[no-any-return]


def _device_id(entity: Any) -> str | None:
    """The Govee device id behind ``entity``."""
    return getattr(entity, "_device_id", None)


def _sku(entity: Any) -> str:
    """The device SKU (``H60B0``), used to look the raw-LAN profile up."""
    return str(getattr(entity._device, "sku", "") or "")


def _toggle_instance(entity: Any) -> str:
    """The cloud capability instance the switch drives (``rippleLightToggle``)."""
    return str(getattr(entity, "_toggle_instance", "") or "")


def _is_on(entity: Any) -> bool:
    """Current optimistic state of a zone switch."""
    return bool(getattr(entity, "_is_on", False))


def _set_state(entity: Any, on: bool) -> None:
    """Write a zone switch's optimistic state and notify HA."""
    entity._is_on = on
    entity.async_write_ha_state()


def _lan_ip(coordinator: GoveeCoordinator, device_id: str | None) -> str | None:
    """The correlated LAN IP for ``device_id``, or None when not on the LAN.

    Same accessor shape as ``lan_nudge._lan_ip`` on purpose.
    """
    info = coordinator._lan_devices.get(device_id)
    return info.ip if info is not None and info.ip else None


def _option_enabled(coordinator: GoveeCoordinator) -> bool:
    """Whether the user turned the raw-LAN zone-power fast path on."""
    return bool(coordinator.config_entry.options.get(CONF_ENABLE_LAN_RAW_WRITE, DEFAULT_ENABLE_LAN_RAW_WRITE))
