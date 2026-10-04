"""Health for the raw LAN UDP write path (fork feature).

Scores ``lan_udp`` as a fifth transport beside ``cloud_api``, ``mqtt``, ``ble``
and ``lan``. Availability cannot mean "a write landed" — nothing on the raw
channel is acknowledged — so it means "a frame sent now would have somewhere to
go": the link gates of :func:`~.api.lan_raw.lan_target` with
``require_option=False``, which is what the zone and DIY paths use. Sends stamp
``last_send_ts`` without establishing availability; ``send_failed`` latches
until the next accepted send; ``last_success_ts`` is never stamped here.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Final

from .api.protocol import Transport
from .models.transport import TransportHealth
from .zone_state import profile_for, zone_lights_enabled

if TYPE_CHECKING:
    from .coordinator import GoveeCoordinator

_LOGGER = logging.getLogger(__name__)

TRANSPORT_LAN_UDP: Final = "lan_udp"
"""Transport kind for the raw write-only UDP path (see models/transport.py)."""

TRANSPORT_LAN: Final = "lan"
"""Upstream's verified LAN transport kind."""

REASON_NO_LAN_PRESENCE: Final = "no_lan_presence"
REASON_NO_RAW_PROFILE: Final = "no_raw_profile"
REASON_NO_LAN_RAW_TRANSPORT: Final = "no_lan_raw_transport"
REASON_SEND_FAILED: Final = "send_failed"

# Reasons this pass owns, and therefore may clear when they stop applying.
# `send_failed` is NOT one of them: a datagram that could not be handed to the
# socket is a hard negative, and nothing observed by this pass disproves it.
# It latches — see `refresh` and `note_send`.
_GATE_REASONS: Final = (
    REASON_NO_LAN_PRESENCE,
    REASON_NO_RAW_PROFILE,
    REASON_NO_LAN_RAW_TRANSPORT,
)


def refresh(coordinator: GoveeCoordinator) -> None:
    """Re-score ``lan_udp`` for every known device.

    Called from the coordinator's LAN staleness pass, so it runs on the same
    cadence as the ``lan`` transport it derives from and needs no timer of its
    own.

    A standing ``send_failed`` is left alone, availability included. This pass
    scores the gates; it observes nothing that disproves a datagram the socket
    refused, so re-reporting the transport as available would produce the one
    state the sensor must never show — available, with a failure reason still
    on the record. Only :func:`note_send` clears that latch.

    Args:
        coordinator: The coordinator whose devices should be re-scored.
    """
    lan_active = _lan_active_ids(coordinator)
    for device_id, device in _devices(coordinator).items():
        health = _health(coordinator, device_id)
        if health is None:
            continue
        reason = _gate_reason(device, on_lan=device_id in lan_active)
        if reason is not None:
            health.is_available = False
            health.last_failure_reason = reason
            continue
        if health.last_failure_reason == REASON_SEND_FAILED:
            continue
        health.is_available = True
        if health.last_failure_reason in _GATE_REASONS:
            health.last_failure_reason = None


def _gate_reason(device: Any, *, on_lan: bool) -> str | None:
    """Why a raw frame to ``device`` could not go out, or None when it could.

    ``enable_lan_raw_write`` is deliberately NOT a gate here: it buys an
    optional fast path for writes the cloud can also make, while the zone and
    DIY paths use this link with ``require_option=False``. Gating on it made the
    sensor report "unavailable" for a link those paths were actively using.

    Args:
        device: The coordinator's device record.
        on_lan: Whether the device is currently correlated to a LAN address.

    Returns:
        A failure-reason string, or None when every gate passes.
    """
    if not on_lan:
        return REASON_NO_LAN_PRESENCE
    profile = profile_for(_sku(device))
    if profile is None:
        return REASON_NO_RAW_PROFILE
    if not profile.carries(Transport.LAN_RAW):
        return REASON_NO_LAN_RAW_TRANSPORT
    return None


def lan_writable(coordinator: GoveeCoordinator, device: Any, *, option_on: bool) -> bool:
    """Whether a raw LAN frame to ``device`` has somewhere to go right now.

    True only when all of these hold: ``option_on``; the device is not a
    group; the coordinator holds its state; its profile carries
    ``Transport.LAN_RAW``; and its ``lan_udp`` or upstream ``lan`` health is
    available. Entities use it to stay available through a cloud outage.

    Args:
        coordinator: The coordinator owning the device.
        device: The coordinator's device record.
        option_on: The option gate of the calling entity.
    """
    if not option_on or getattr(device, "is_group", False):
        return False
    device_id = getattr(device, "device_id", None)
    if device_id is None or coordinator.get_state(device_id) is None:
        return False
    profile = profile_for(_sku(device))
    if profile is None or not profile.carries(Transport.LAN_RAW):
        return False
    return any(_transport_up(coordinator, device_id, kind) for kind in (TRANSPORT_LAN_UDP, TRANSPORT_LAN))


def zone_lan_writable(entity: Any) -> bool:
    """:func:`lan_writable` for a zone or DIY entity, gated by ``enable_zone_lights``."""
    coordinator = entity.coordinator
    entry = getattr(coordinator, "config_entry", None)
    option_on = entry is not None and zone_lights_enabled(entry)
    return lan_writable(coordinator, entity._device, option_on=option_on)


def note_send(coordinator: GoveeCoordinator, device_id: str) -> None:
    """Stamp an outbound raw frame, and clear a standing ``send_failed``.

    Deliberately does NOT *establish* availability: a datagram into the void
    proves nothing about the device. It does clear the ``send_failed`` latch,
    which is a claim about the socket, and a datagram the socket accepted is
    exactly the evidence that disproves it — so the state it set is undone with
    it, and the gates go back to owning availability.
    """
    health = _health(coordinator, device_id)
    if health is None:
        return
    health.last_send_ts = datetime.now(timezone.utc)
    if health.last_failure_reason == REASON_SEND_FAILED:
        health.last_failure_reason = None
        health.is_available = True


def note_failure(coordinator: GoveeCoordinator, device_id: str, reason: str = REASON_SEND_FAILED) -> None:
    """Stamp a hard send failure — the only negative this transport can prove."""
    health = _health(coordinator, device_id)
    if health is None:
        return
    health.mark_failure(datetime.now(timezone.utc), reason)
    _LOGGER.debug("Govee LAN UDP: %s marked unavailable (%s)", device_id, reason)


# Coordinator internals, read in exactly one place each. An upstream rename of
# any of these is a one-line fix in this block.


def _health(coordinator: GoveeCoordinator, device_id: str) -> TransportHealth | None:
    """The device's ``lan_udp`` health entry, provisioning the device if new."""
    coordinator._ensure_transport_health(device_id)
    return coordinator.get_transport_health(device_id, TRANSPORT_LAN_UDP)  # type: ignore[arg-type]


def _transport_up(coordinator: GoveeCoordinator, device_id: str, kind: str) -> bool:
    """Whether ``kind`` health for the device is available, without provisioning it."""
    health = coordinator.get_transport_health(device_id, kind)  # type: ignore[arg-type]
    return health is not None and health.is_available is True


def _devices(coordinator: GoveeCoordinator) -> dict[str, Any]:
    """Every device the coordinator knows about."""
    return coordinator.devices


def _lan_active_ids(coordinator: GoveeCoordinator) -> set[str]:
    """Device ids currently correlated to a LAN address."""
    return set(coordinator._lan_devices)


def _sku(device: Any) -> str:
    """A device's model string."""
    return str(getattr(device, "sku", "") or "")
