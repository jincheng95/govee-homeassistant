"""Health for the raw LAN UDP write path (fork feature).

The integration already tracks four transports per device — ``cloud_api``,
``mqtt``, ``ble`` and ``lan`` — and surfaces them on the ``*_connectivity``
binary sensor (as attributes, always) and as one opt-in entity per transport.
The raw UDP write path added by :mod:`.api.lan_raw` is a fifth, and this module
is the only place that knows how to score it.

What "available" honestly means here
------------------------------------
**Not** "a write landed". It cannot mean that. Raw ``ptReal`` frames are
write-only by measurement: devices answer no raw query and produce no cloud
status push in response to a raw command, so there is no acknowledgement of any
kind to wait for. Inventing a health signal the protocol cannot provide would
be worse than reporting nothing.

So ``lan_udp`` availability is defined as **a raw frame sent right now would
have somewhere to go**. That is deliberately the same set of conditions
:func:`~.api.lan_raw.lan_target` gates on, because a sensor that reads
"connected" for a device the writer refuses to write to is worse than no sensor
at all:

* the user turned the raw-write transport on (it defaults to off), **and**
* the device answered LAN discovery / ``devStatus`` and is currently correlated
  to an IP (exactly the same evidence the existing ``lan`` transport uses),
  **and**
* its SKU has an entry in the raw-protocol profile table, so a frame can be
  built for it at all, **and**
* that SKU actually *carries* raw frames over LAN. Some models are on an older
  stack whose raw pipe is BLE: they answer LAN discovery perfectly and discard
  every raw frame, so LAN presence alone would be a lie for them.

Failure reasons are correspondingly narrow: ``transport_disabled`` (the option
is off), ``no_lan_presence`` (never answered discovery, or dropped out of the
LAN map), ``no_raw_profile`` (reachable, but this integration has no frame
layout for the model), ``no_lan_raw_transport`` (reachable and profiled, but
this model ignores raw frames on this pipe), ``send_failed`` (the datagram
itself could not be handed to the socket — the one hard negative the path can
produce).

Directionality
--------------
Sends stamp ``last_send_ts`` *without* establishing ``is_available``. The
tracker's ``mark_send`` sets availability as a side effect, which is right for
MQTT (a publish that returns proves the broker took it) and wrong here (a UDP
datagram into the void proves nothing about the device). The distinction is
deliberate; see the recording-asymmetry note in :mod:`.transport_health`.

``send_failed`` latches: it survives the gate re-score, because nothing that
pass observes disproves a datagram the socket refused, and it is cleared by the
next accepted send — the one thing that does. The sensor is therefore never
available with a failure reason still standing.

``last_success_ts`` is never stamped for ``lan_udp``. There is no receive
direction on this transport — the reads belong to ``lan``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Final

from .api.protocol import Transport
from .const import CONF_ENABLE_LAN_RAW_WRITE, DEFAULT_ENABLE_LAN_RAW_WRITE
from .models.transport import TransportHealth
from .zone_state import profile_for

if TYPE_CHECKING:
    from .coordinator import GoveeCoordinator

_LOGGER = logging.getLogger(__name__)

TRANSPORT_LAN_UDP: Final = "lan_udp"
"""Transport kind for the raw write-only UDP path (see models/transport.py)."""

REASON_TRANSPORT_DISABLED: Final = "transport_disabled"
REASON_NO_LAN_PRESENCE: Final = "no_lan_presence"
REASON_NO_RAW_PROFILE: Final = "no_raw_profile"
REASON_NO_LAN_RAW_TRANSPORT: Final = "no_lan_raw_transport"
REASON_SEND_FAILED: Final = "send_failed"

# Reasons this pass owns, and therefore may clear when they stop applying.
# `send_failed` is NOT one of them: a datagram that could not be handed to the
# socket is a hard negative, and nothing observed by this pass disproves it.
# It latches — see `refresh` and `note_send`.
_GATE_REASONS: Final = (
    REASON_TRANSPORT_DISABLED,
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
    enabled = _option_enabled(coordinator)
    lan_active = _lan_active_ids(coordinator)
    for device_id, device in _devices(coordinator).items():
        health = _health(coordinator, device_id)
        if health is None:
            continue
        reason = _gate_reason(device, enabled=enabled, on_lan=device_id in lan_active)
        if reason is not None:
            health.is_available = False
            health.last_failure_reason = reason
            continue
        if health.last_failure_reason == REASON_SEND_FAILED:
            continue
        health.is_available = True
        if health.last_failure_reason in _GATE_REASONS:
            health.last_failure_reason = None


def _gate_reason(device: Any, *, enabled: bool, on_lan: bool) -> str | None:
    """Why a raw frame to ``device`` could not go out, or None when it could.

    Args:
        device: The coordinator's device record.
        enabled: Whether the raw-write option is on.
        on_lan: Whether the device is currently correlated to a LAN address.

    Returns:
        A failure-reason string, or None when every gate passes.
    """
    if not enabled:
        return REASON_TRANSPORT_DISABLED
    if not on_lan:
        return REASON_NO_LAN_PRESENCE
    profile = profile_for(_sku(device))
    if profile is None:
        return REASON_NO_RAW_PROFILE
    if not profile.carries(Transport.LAN_RAW):
        return REASON_NO_LAN_RAW_TRANSPORT
    return None


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


# ----------------------------------------------------------------------
# Coordinator internals, read in exactly one place each. An upstream rename of
# any of these is a one-line fix in this block.
# ----------------------------------------------------------------------


def _health(coordinator: GoveeCoordinator, device_id: str) -> TransportHealth | None:
    """The device's ``lan_udp`` health entry, provisioning the device if new."""
    coordinator._ensure_transport_health(device_id)
    return coordinator.get_transport_health(device_id, TRANSPORT_LAN_UDP)  # type: ignore[arg-type]


def _devices(coordinator: GoveeCoordinator) -> dict[str, Any]:
    """Every device the coordinator knows about."""
    return coordinator.devices


def _lan_active_ids(coordinator: GoveeCoordinator) -> set[str]:
    """Device ids currently correlated to a LAN address."""
    return set(coordinator._lan_devices)


def _sku(device: Any) -> str:
    """A device's model string."""
    return str(getattr(device, "sku", "") or "")


def _option_enabled(coordinator: GoveeCoordinator) -> bool:
    """Whether the user turned the raw-LAN transport on.

    Read defensively: this runs on the coordinator's staleness timer, which can
    fire against a coordinator that has not been bound to a config entry yet.
    No entry means no option, which means the default (off).
    """
    entry = getattr(coordinator, "config_entry", None)
    if entry is None:
        return bool(DEFAULT_ENABLE_LAN_RAW_WRITE)
    return bool(entry.options.get(CONF_ENABLE_LAN_RAW_WRITE, DEFAULT_ENABLE_LAN_RAW_WRITE))
