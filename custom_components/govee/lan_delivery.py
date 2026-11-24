"""Delivery check for raw LAN writes (fork feature).

A lamp whose profile sets ``echoes_raw_writes`` answers every raw write with a
cloud ``ptReal`` message, so a LAN write with no answer did not arrive. A missed
answer marks the LAN address suspect, and raw writes then go over the cloud. A
different address, a LAN reading from the device or an answered retry clears
the mark. A write-only pin that keeps missing raises a repair issue.

The check runs only while the cloud session is connected, has heard the device
and could carry the same frames.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Final

from homeassistant.helpers import issue_registry as ir

from .api import mqtt_raw_write
from .api.segment_readback import payload_frames
from .const import DOMAIN, LAN_RESCAN_INTERVAL
from .zone_state import profile_for

if TYPE_CHECKING:
    from .coordinator import GoveeCoordinator

_LOGGER = logging.getLogger(__name__)

ECHO_TIMEOUT_SECONDS: Final = 2.5
"""How long a write waits for its answer. The slowest answer seen took 1.1 s."""

SUSPECT_RETRY_SECONDS: Final = LAN_RESCAN_INTERVAL
"""How long a suspect address is skipped before one write tries it again."""

PIN_MISS_THRESHOLD: Final = 3
"""Consecutive misses on a write-only pin before the repair issue."""

ISSUE_LAN_PIN_UNREACHABLE: Final = "lan_pin_unreachable"

_ECHO_CMD: Final = "ptReal"
_TARGETS_ATTR: Final = "_govee_lan_delivery"


@dataclass
class _Target:
    """What is known about one device's LAN address."""

    pending_ip: str | None = None
    pending_since: float = 0.0
    pending_heads: set[int] = field(default_factory=set)
    waiter: asyncio.Future[bool] | None = None
    suspect_ip: str | None = None
    suspect_since: float = 0.0
    suspect_at: datetime | None = None
    misses: int = 0
    reported: bool = False


def note_send(coordinator: GoveeCoordinator, device_id: str, ip: str, frames: Sequence[bytes]) -> None:
    """Start watching for the answer to a raw LAN write that just went out.

    A write sent while an earlier one is still unanswered joins that watch.
    """
    targets = _targets(coordinator)
    if not _echo_expected(coordinator, device_id):
        return
    target = targets.setdefault(device_id, _Target())
    _settle(coordinator, device_id, target)
    if target.pending_ip is None:
        target.pending_ip = ip
        target.pending_since = time.monotonic()
        target.pending_heads = set()
    target.pending_heads.update(frame[0] for frame in frames if frame)


def note_inbound(coordinator: GoveeCoordinator, device_id: str, payload: dict[str, Any]) -> None:
    """Take an inbound cloud message as the answer to a watched write.

    Only a ``ptReal`` message counts, and only when one of its acks opens with
    the first byte of a watched frame. A status push or the answer to a query
    says nothing about delivery. An answer that comes after the wait ran out
    does not count either.
    """
    if payload.get("cmd") != _ECHO_CMD:
        return
    target = _targets(coordinator).get(device_id)
    if target is None:
        return
    _settle(coordinator, device_id, target)
    if target.pending_ip is None:
        return
    if target.pending_heads.isdisjoint(ack[0] for ack in payload_frames(payload) if ack):
        return
    target.pending_ip = None
    _resolve(target, True)
    if target.suspect_ip is not None or target.misses:
        _LOGGER.debug("Govee LAN delivery: %s answered a LAN write again", device_id)
    _clear(coordinator, device_id, target)


async def async_confirm(coordinator: GoveeCoordinator, device_id: str) -> bool:
    """Wait for the answer to the last LAN write to ``device_id``.

    Returns:
        False when the wait ran out. True when the answer came, and when no
        write is being watched.
    """
    target = _targets(coordinator).get(device_id)
    if target is None or target.pending_ip is None:
        return True
    remaining = target.pending_since + ECHO_TIMEOUT_SECONDS - time.monotonic()
    if remaining > 0:
        target.waiter = target.waiter or asyncio.get_running_loop().create_future()
        try:
            return await asyncio.wait_for(asyncio.shield(target.waiter), remaining)
        except TimeoutError:
            pass
    return not _settle(coordinator, device_id, target, overdue=True)


def is_suspect(coordinator: GoveeCoordinator, device_id: str, ip: str) -> bool:
    """Whether a raw write to ``device_id`` at ``ip`` should skip the LAN tier.

    False again once :data:`SUSPECT_RETRY_SECONDS` have passed, so that one
    write retries the address.
    """
    target = _targets(coordinator).get(device_id)
    if target is None:
        return False
    _settle(coordinator, device_id, target)
    if target.suspect_ip is None:
        return False
    if target.suspect_ip != ip or _read_since(coordinator, device_id, target.suspect_at):
        _clear(coordinator, device_id, target)
        return False
    return time.monotonic() - target.suspect_since < SUSPECT_RETRY_SECONDS


def _settle(coordinator: GoveeCoordinator, device_id: str, target: _Target, *, overdue: bool = False) -> bool:
    """Count an overdue watch as a miss. True when it did."""
    ip = target.pending_ip
    if ip is None:
        return False
    if not overdue and time.monotonic() - target.pending_since < ECHO_TIMEOUT_SECONDS:
        return False
    target.pending_ip = None
    _resolve(target, False)
    if not _echo_expected(coordinator, device_id):
        # The cloud session went away meanwhile, so the silence proves nothing.
        return False
    target.suspect_ip = ip
    target.suspect_since = time.monotonic()
    target.suspect_at = datetime.now(timezone.utc)
    target.misses += 1
    _LOGGER.debug(
        "Govee LAN delivery: no answer from %s at %s (%d in a row) — raw writes use the cloud",
        device_id,
        ip,
        target.misses,
    )
    if not _write_only(coordinator, device_id):
        # A rescan cannot move a pinned address.
        _request_rescan(coordinator)
    elif target.misses >= PIN_MISS_THRESHOLD and not target.reported:
        target.reported = True
        _report_pin(coordinator, device_id, ip)
    return True


def _resolve(target: _Target, answered: bool) -> None:
    """Wake whoever waits on the watch."""
    waiter, target.waiter = target.waiter, None
    if waiter is not None and not waiter.done():
        waiter.set_result(answered)


def _clear(coordinator: GoveeCoordinator, device_id: str, target: _Target) -> None:
    """Forget the suspect mark, the miss count and the repair issue."""
    target.suspect_ip = None
    target.suspect_at = None
    target.misses = 0
    if target.reported:
        target.reported = False
        ir.async_delete_issue(coordinator.hass, DOMAIN, _issue_id(device_id))


def _report_pin(coordinator: GoveeCoordinator, device_id: str, ip: str) -> None:
    """Warn about, and raise a repair issue for, a write-only pin that never answers."""
    name = str(getattr(coordinator.devices.get(device_id), "name", "") or device_id)
    _LOGGER.warning(
        "Govee LAN: %s did not answer %d writes in a row at its pinned address %s — check the address",
        name,
        PIN_MISS_THRESHOLD,
        ip,
    )
    ir.async_create_issue(
        coordinator.hass,
        DOMAIN,
        _issue_id(device_id),
        is_fixable=False,
        is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_LAN_PIN_UNREACHABLE,
        translation_placeholders={"device": name, "address": ip},
    )


def _issue_id(device_id: str) -> str:
    return f"{ISSUE_LAN_PIN_UNREACHABLE}_{device_id.replace(':', '').lower()}"


def _echo_expected(coordinator: GoveeCoordinator, device_id: str) -> bool:
    """Whether a raw LAN write to the device would be answered over the cloud now."""
    sku = str(getattr(coordinator.devices.get(device_id), "sku", "") or "")
    profile = profile_for(sku)
    if profile is None or not profile.echoes_raw_writes:
        return False
    return mqtt_raw_write.mqtt_raw_target(coordinator, profile, device_id) and _heard(coordinator, device_id)


# Coordinator internals, read in exactly one place each.


def _targets(coordinator: GoveeCoordinator) -> dict[str, _Target]:
    """The per-device records, created on first use.

    A reloaded entry gets a new coordinator, so first use also drops the repair
    issues an earlier coordinator raised for these devices.
    """
    targets = getattr(coordinator, _TARGETS_ATTR, None)
    if not isinstance(targets, dict):
        targets = {}
        setattr(coordinator, _TARGETS_ATTR, targets)
        for device_id in coordinator.devices:
            ir.async_delete_issue(coordinator.hass, DOMAIN, _issue_id(device_id))
    return targets


def _heard(coordinator: GoveeCoordinator, device_id: str) -> bool:
    """Whether the cloud session has delivered a message from the device."""
    return coordinator.mqtt_last_receive_for(device_id) is not None


def _read_since(coordinator: GoveeCoordinator, device_id: str, since: datetime | None) -> bool:
    """Whether a LAN reading from the device was applied after ``since``.

    ``last_success_ts`` cannot answer this, because a send to a write-only pin
    stamps it too.
    """
    health = coordinator.get_transport_health(device_id, "lan")
    read = getattr(health, "last_read_ts", None)
    return isinstance(read, datetime) and since is not None and read > since


def _write_only(coordinator: GoveeCoordinator, device_id: str) -> bool:
    return device_id in (getattr(coordinator, "_lan_write_only", None) or ())


def _request_rescan(coordinator: GoveeCoordinator) -> None:
    rescan = getattr(coordinator, "_request_lan_rescan", None)
    if rescan is not None:
        rescan()
