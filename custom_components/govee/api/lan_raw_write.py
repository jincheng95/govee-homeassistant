"""Raw-LAN fast path for device commands (fork feature).

Why this exists
---------------
The H60B0 uplighter's three zone switches (``rippleLightToggle``,
``sideLightToggle``, ``bottomLightToggle``) go out over the cloud today, which
means a 1-3 second round trip for the controls that get pressed most. The same
intent is one 20-byte UDP frame on the LAN — ``33 30 <zone> <00|01>`` — and the
lamp acts on it as fast as the packet arrives.

This module is the transport half of that feature. It sits between the entities
and the codec in :mod:`.protocol`, decides whether a LAN write is *safe* for
a given device, sends it, and reports the result. Callers keep their own
optimistic state; shared zone state lives in :mod:`..zone_state`.

Scope, in the order it grew:

* **zone power** — the switches in ``switch.py`` (:func:`async_zone_power`).
* **zone colour / brightness / colour temperature / flow rate** — the zone
  light and number entities in ``platforms/zone_light.py``, via :func:`async_send_frames`.
  These have no cloud equivalent at zone granularity, so there is nothing to
  fall back to; see that module for what the entities do instead.
* **per-segment colour** — the segment light entities in
  ``platforms/segment.py`` and ``platforms/grouped_segment.py``
  (:func:`async_segment_color`). This one *does* have a cloud equivalent
  (``SegmentColorCommand``), so it falls back cleanly.

What makes a LAN write safe
---------------------------
Gates, all of which must pass, or the caller falls through to whatever it did
before:

1. The ``enable_lan_raw_write`` option is on (it defaults to **off**).
2. **The SKU actually carries raw frames over LAN** — ``profile.carries(
   Transport.LAN_RAW)``. Having a profile is not enough: some SKUs are on an
   older stack that takes raw frames only over BLE and ignores them on the
   LAN. That is the dangerous case, because the datagram still leaves, no
   reply is expected, and the write looks successful while the lamp does
   nothing. Anything that gets here without this check is a silent no-op with
   an optimistic state update on top of it.
3. The intent maps to a capability the device's SKU profile declares.
4. Every profile constant the frame needs is *known*. A profile that does not
   know a byte raises out of the codec rather than guessing — a wrong sub-mode
   byte is silently ignored by firmware, which is indistinguishable from a dead
   network (see ``profiles.UNKNOWN``).
5. The coordinator currently has a live LAN correlation (an IP) for the device.

Nothing here raises at the caller: every failure path returns ``False``, which
means "I did not handle it, do what you did before".

Write-only, therefore optimistic and repeated
---------------------------------------------
The raw LAN channel is fire-and-forget by measurement, not by choice: devices
answer no ``ptReal`` query and a LAN command produces no cloud status push
either (see :mod:`.protocol.client`). Two consequences are baked in here:

* **Optimistic state.** There is no echo to wait for, so entity state is set
  the moment the datagrams are away.
* **Repeats.** UDP is lossy and unacknowledged, and a dropped frame would leave
  the lamp disagreeing with the UI until something else corrected it. The
  frames are idempotent — ``33 30 03 01`` says "downlight on", not "toggle the
  downlight" — so sending :data:`LAN_WRITE_REPEATS` copies a few tens of
  milliseconds apart costs nothing but makes a single lost packet a non-event.
  Repeats are only free because the frames are *absolute*: replaying one can
  never invert the state, which a "toggle" frame would.

Music mode
----------
Measured trap, handled by documentation rather than by a guess: while a lamp is
in music mode it *accepts* colour writes and even echoes them back in a
``devStatus`` read, but keeps animating — the write has no visible effect. The
only command observed to leave music mode is a **cloud** colour command. Raw
frames sent by this module therefore cannot rescue a lamp from music mode; the
whole-device light entity's colour control (which is a cloud command) can.
Nothing here attempts a LAN mode-select for that purpose: it was never
confirmed to work, and a mode byte that firmware silently drops looks exactly
like success.

Fork-maintenance note: the coordinator and entity internals this module reaches
into are read in ONE place each (the accessor block at the bottom), so an
upstream rename is a one-line fix here rather than a scattered merge conflict.
This is the same discipline :mod:`.lan_nudge` established.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Final

from .. import lan_udp_health
from ..const import CONF_ENABLE_LAN_RAW_WRITE, DEFAULT_ENABLE_LAN_RAW_WRITE
from ..zone_state import ZONE_KEY_BY_TOGGLE, displaced_zone_keys, profile_for, registry
from .protocol import (
    Capability,
    DeviceProfile,
    GoveeCodec,
    GoveeProtocolError,
    LanUdpClient,
    Transport,
)

if TYPE_CHECKING:
    from ..coordinator import GoveeCoordinator

_LOGGER = logging.getLogger(__name__)

# Re-exported for callers and tests that predate the split into zone_state.
_displaced_zone_keys = displaced_zone_keys

# Profile zone key used by SKUs whose segments are addressed by mask alone.
SEGMENT_ZONE_KEY: Final = "segments"

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


# ----------------------------------------------------------------------
# Generic send path
# ----------------------------------------------------------------------


def lan_write_enabled(coordinator: GoveeCoordinator) -> bool:
    """Whether the user turned the raw-LAN transport on."""
    return _option_enabled(coordinator)


def lan_target(coordinator: GoveeCoordinator, device_id: str | None, sku: str) -> tuple[str, DeviceProfile] | None:
    """The ``(ip, profile)`` a raw write to this device would use, or None.

    None means one of the transport gates failed — the option is off, the SKU
    is not in the profile table, **the SKU does not carry raw frames over LAN**,
    or the device has no live LAN correlation. Callers use it both to route a
    write and to decide whether a LAN-only control can be offered at all.

    Args:
        coordinator: The coordinator owning the device.
        device_id: The Govee device id, or None for an entity without one.
        sku: The device model, used to look the profile up.

    Returns:
        The LAN address and profile to write with, or None to fall back.
    """
    if not _option_enabled(coordinator):
        return None
    profile = profile_for(sku)
    if profile is None:
        return None
    if not profile.carries(Transport.LAN_RAW):
        # Having a profile is NOT permission to send. Some SKUs accept the
        # datagram and ignore the frame, which is indistinguishable from
        # success on a fire-and-forget path with no reply — so a device whose
        # raw pipe is BLE must fall back to the cloud here, not silently
        # no-op with an optimistic state update on top.
        _LOGGER.debug(
            "Govee LAN write: %s does not carry raw frames over LAN (raw transports: %s) — using cloud transport",
            sku,
            ", ".join(transport.value for transport in profile.transports),
        )
        return None
    if _writes_suppressed(coordinator, device_id):
        # Upstream holds LAN *writes* for a device whose recent LAN writes did
        # not confirm (issue #57) and routes them to MQTT/REST for the cooldown.
        # Raw frames ride the same UDP path to the same lamp, so sending them
        # inside that window is exactly what the flag says not to do — and they
        # cannot even fail visibly, being unacknowledged.
        _LOGGER.debug("Govee LAN write: writes to %s are suppressed by the coordinator — using cloud transport", sku)
        return None
    ip = _lan_ip(coordinator, device_id)
    if ip is None:
        return None
    return ip, profile


async def async_send_frames(
    coordinator: GoveeCoordinator,
    device_id: str,
    ip: str,
    frames: Sequence[bytes],
    *,
    what: str = "frames",
) -> bool:
    """Send pre-built frames to a device, repeated. True when they went out.

    The frames travel in ONE ``ptReal`` envelope per repeat, which the device
    applies in order — so "set colour, then switch the zone on" arrives as a
    unit rather than as two datagrams that can be reordered or split.
    """
    if not frames:
        return False
    started = time.monotonic()
    try:
        sent = await _async_send_repeated(ip, frames)
    except GoveeProtocolError as err:
        # The frames were built successfully and then could not be wrapped for
        # the wire. That is a bug in this integration, not evidence about the
        # network — so it must NOT mark the device's transport unavailable,
        # which would stick (refresh() never clears a hard send failure).
        _LOGGER.debug(
            "Govee LAN write: cannot build an envelope for %s (%s) — falling back",
            device_id,
            err,
        )
        return False
    except OSError as err:
        _LOGGER.debug(
            "Govee LAN write: raw send to %s (%s) failed (%s) — falling back",
            device_id,
            ip,
            err,
        )
        lan_udp_health.note_failure(coordinator, device_id)
        return False

    lan_udp_health.note_send(coordinator, device_id)
    _LOGGER.debug(
        "Govee LAN write: %s %s via LAN transport %s in %.1f ms (%d/%d x %s)",
        device_id,
        what,
        ip,
        (time.monotonic() - started) * 1000,
        sent,
        LAN_WRITE_REPEATS,
        " | ".join(frame.hex(" ") for frame in frames),
    )
    return True


async def _async_send_repeated(ip: str, frames: Sequence[bytes]) -> int:
    """Send the same envelope :data:`LAN_WRITE_REPEATS` times, spaced out.

    The repeats are redundancy, not a sequence: the frames are absolute, so one
    copy reaching the socket says exactly what three do. A copy that fails
    therefore does not fail the write — reporting failure would send the caller
    to the cloud with a second, contradictory command for something the device
    has already been told.

    Args:
        ip: The device's LAN address.
        frames: The frames to put in each envelope.

    Returns:
        How many copies were handed to the socket (at least one).

    Raises:
        OSError: If every copy failed.
        GoveeProtocolError: If the envelope could not be built at all.
    """
    client = _client()
    sent = 0
    last_error: OSError | None = None
    for attempt in range(LAN_WRITE_REPEATS):
        if attempt:
            await asyncio.sleep(LAN_WRITE_GAP_SECONDS)
        try:
            await client.async_send_frames(ip, list(frames))
        except OSError as err:
            last_error = err
            continue
        sent += 1
    if sent == 0:
        raise last_error if last_error is not None else OSError("no copies were sent")
    return sent


# ----------------------------------------------------------------------
# Zone power (the switch entities in switch.py)
# ----------------------------------------------------------------------


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
    instance = _toggle_instance(entity)
    zone_key = ZONE_KEY_BY_TOGGLE.get(instance)
    if zone_key is None:
        return False

    device_id = _device_id(entity)
    sku = _sku(entity)
    target = lan_target(coordinator, device_id, sku)
    if target is None:
        return False
    ip, profile = target

    if not zone_power_supported(profile, zone_key):
        _LOGGER.debug(
            "Govee LAN write: %s/%s has no raw zone-power profile — using cloud transport",
            sku,
            instance,
        )
        return False

    try:
        frame = GoveeCodec(profile).zone_power(zone_key, on)
    except GoveeProtocolError as err:
        _LOGGER.debug("Govee LAN write: cannot encode zone power for %s (%s)", sku, err)
        return False

    if device_id is None or not await async_send_frames(
        coordinator, device_id, ip, [frame], what=f"zone {zone_key} -> {'on' if on else 'off'}"
    ):
        return False

    _set_state(entity, on)
    registry(coordinator).apply(device_id, sku, zone_key, on)
    return True


def zone_power_supported(profile: DeviceProfile, zone_key: str) -> bool:
    """Whether this profile can express zone power for ``zone_key``."""
    try:
        zone = profile.zone(zone_key)
    except GoveeProtocolError:
        return False
    if zone.zone_byte is None:
        return False
    return profile.supports(Capability.ZONE_POWER, zone=zone_key)


# ----------------------------------------------------------------------
# Per-segment colour (the segment light entities)
# ----------------------------------------------------------------------


async def async_segment_color(entity: Any, rgb: tuple[int, int, int], segments: Sequence[int]) -> bool:
    """Try to paint segments of an RGBIC device over raw LAN.

    Returns False — "not handled, use the cloud" — for every reason a raw frame
    would be a guess: option off, no profile, no ``SEGMENT_COLOR`` capability, a
    profile constant still UNKNOWN (the H6076's segment sub-mode), no LAN
    correlation, or a mismatch between the segment count the entities index and
    the mask width the table declares.

    The cloud fallback is exact here — ``SegmentColorCommand`` expresses the
    same intent — so nothing is lost by refusing.
    """
    coordinator = _coordinator(entity)
    device_id = _device_id(entity)
    sku = _sku(entity)
    target = lan_target(coordinator, device_id, sku)
    if target is None or device_id is None:
        return False
    ip, profile = target

    if not profile.supports(Capability.SEGMENT_COLOR, zone=SEGMENT_ZONE_KEY):
        return False
    if not _segment_count_matches(entity, profile):
        _LOGGER.debug(
            "Govee LAN write: %s segment count disagrees with the profile mask width — using cloud transport",
            device_id,
        )
        return False

    try:
        frame = GoveeCodec(profile).segment_color(rgb, segments=list(segments), zone=SEGMENT_ZONE_KEY)
    except GoveeProtocolError as err:
        # UnknownEncodingError lands here: the table refuses to guess a byte.
        _LOGGER.debug("Govee LAN write: cannot encode segment colour for %s (%s) — using cloud transport", sku, err)
        return False

    return await async_send_frames(coordinator, device_id, ip, [frame], what=f"segments {list(segments)} -> {rgb}")


def _segment_count_matches(entity: Any, profile: DeviceProfile) -> bool:
    """Whether the device's reported segment count matches the profile's mask.

    The entities index segments from the cloud's ``segmentedColorRgb`` count; the
    mask width comes from the table. If they disagree the table is describing a
    different revision of the hardware and the bits would land on the wrong
    LEDs, so the write is refused rather than approximated.
    """
    try:
        zone = profile.zone(SEGMENT_ZONE_KEY)
    except GoveeProtocolError:
        return False
    return bool(zone.segments) and zone.segments == _segment_count(entity)


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
    return str(getattr(getattr(entity, "_device", None), "sku", "") or "")


def _segment_count(entity: Any) -> int:
    """How many segments the device reports (drives the entity indices)."""
    return int(getattr(getattr(entity, "_device", None), "segment_count", 0) or 0)


def _toggle_instance(entity: Any) -> str:
    """The cloud capability instance the switch drives (``rippleLightToggle``)."""
    return str(getattr(entity, "_toggle_instance", "") or "")


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


def _writes_suppressed(coordinator: GoveeCoordinator, device_id: str | None) -> bool:
    """Whether upstream is currently holding LAN writes for ``device_id``.

    Reads upstream's own cooldown predicate, which also re-arms an expired
    window — the same call upstream's `_try_lan_command` makes, so the fork's
    raw path and upstream's LAN tier come out of the cooldown together.

    Strictly ``is True``: this reaches a private upstream method, and anything
    that is not a real ``True`` (a rename leaving None, a stub) must mean "not
    suppressed", i.e. the behaviour this module had before the check existed.
    """
    if device_id is None:
        return False
    suppressed = getattr(coordinator, "_lan_writes_suppressed", None)
    if suppressed is None:
        return False
    return suppressed(device_id) is True


def _option_enabled(coordinator: GoveeCoordinator) -> bool:
    """Whether the user turned the raw-LAN transport on."""
    return bool(coordinator.config_entry.options.get(CONF_ENABLE_LAN_RAW_WRITE, DEFAULT_ENABLE_LAN_RAW_WRITE))


# ``_profile`` predates :mod:`..zone_state`; kept as the module's one-line
# accessor so existing callers and tests keep working.
_profile = profile_for
_zone_power_supported = zone_power_supported
