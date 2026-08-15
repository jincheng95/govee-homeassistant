"""Raw frames over the LAN ``ptReal`` UDP datagram (fork feature).

The fastest raw tier (~30 ms) and the only pipe the zone and DIY paths have.
Write-only: nothing on this channel is acknowledged and a raw write is invisible
to ``devStatus``, so callers keep optimistic state and every frame goes out
:data:`LAN_WRITE_REPEATS` times — the frames are absolute, so a replay can never
invert a state the way a "toggle" frame would.

Nothing here raises at the caller: every failure path returns False, meaning
"I did not handle it, do what you did before". Tier selection lives in
:mod:`.raw_router`; coordinator internals are read in one place each below.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Sequence
from typing import TYPE_CHECKING, Final

from .. import lan_udp_health
from ..const import CONF_ENABLE_LAN_RAW_WRITE, DEFAULT_ENABLE_LAN_RAW_WRITE
from ..zone_state import profile_for
from .protocol import DeviceProfile, GoveeProtocolError, LanUdpClient, Transport

if TYPE_CHECKING:
    from ..coordinator import GoveeCoordinator

_LOGGER = logging.getLogger(__name__)

# How many copies of the (idempotent) frame go out per press. UDP is
# unacknowledged, and an absolute frame makes repeats free.
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


def lan_write_enabled(coordinator: GoveeCoordinator) -> bool:
    """Whether the user turned the raw-LAN transport on."""
    return bool(coordinator.config_entry.options.get(CONF_ENABLE_LAN_RAW_WRITE, DEFAULT_ENABLE_LAN_RAW_WRITE))


def lan_target(
    coordinator: GoveeCoordinator,
    device_id: str | None,
    sku: str,
    *,
    require_option: bool = True,
) -> tuple[str, DeviceProfile] | None:
    """The ``(ip, profile)`` a raw write to this device would use, or None.

    None means one of the transport gates failed — the option is off, the SKU
    is not in the profile table, **the SKU does not carry raw frames over LAN**,
    or the device has no live LAN correlation. Callers use it both to route a
    write and to decide whether a LAN-only control can be offered at all.

    Args:
        coordinator: The coordinator owning the device.
        device_id: The Govee device id, or None for an entity without one.
        sku: The device model, used to look the profile up.
        require_option: Whether ``enable_lan_raw_write`` must be on. That
            option buys an optional *fast path* for writes the cloud can also
            make, so the zone and DIY paths — for which this is the only pipe
            that exists — pass False and are gated by their own option.

    Returns:
        The LAN address and profile to write with, or None to fall back.
    """
    if require_option and not lan_write_enabled(coordinator):
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


# Coordinator internals, read in exactly one place each.


def _lan_ip(coordinator: GoveeCoordinator, device_id: str | None) -> str | None:
    """The correlated LAN IP for ``device_id``, or None when not on the LAN.

    Same accessor shape as ``lan_nudge._lan_ip`` on purpose.
    """
    info = coordinator._lan_devices.get(device_id)
    return info.ip if info is not None and info.ip else None
