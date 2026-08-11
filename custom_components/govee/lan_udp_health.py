"""Health for the raw LAN UDP write path (fork feature).

The integration already tracks four transports per device — ``cloud_api``,
``mqtt``, ``ble`` and ``lan`` — and surfaces them on the ``*_connectivity``
binary sensor (as attributes, always) and as one opt-in entity per transport.
The raw UDP write path added by :mod:`.api.lan_raw_write` is a fifth, and this module
is the only place that knows how to score it.

What "available" honestly means here
------------------------------------
**Not** "a write landed". It cannot mean that. Raw ``ptReal`` frames are
write-only by measurement: devices answer no raw query and produce no cloud
status push in response to a raw command, so there is no acknowledgement of any
kind to wait for. Inventing a health signal the protocol cannot provide would
be worse than reporting nothing.

So ``lan_udp`` availability is defined as **the device is addressable and we
know its language**:

* it answered LAN discovery / ``devStatus`` and is currently correlated to an
  IP (exactly the same evidence the existing ``lan`` transport uses), **and**
* its SKU has an entry in the raw-protocol profile table, so a frame can be
  built for it at all.

Failure reasons are correspondingly narrow: ``no_lan_presence`` (never answered
discovery, or dropped out of the LAN map), ``no_raw_profile`` (reachable, but
this integration has no frame layout for the model), ``send_failed`` (the
datagram itself could not be handed to the socket — the one hard negative the
path can produce).

Directionality
--------------
Sends stamp ``last_send_ts`` *without* touching ``is_available``. The tracker's
``mark_send`` sets availability as a side effect, which is right for MQTT (a
publish that returns proves the broker took it) and wrong here (a UDP datagram
into the void proves nothing about the device). The distinction is deliberate;
see the recording-asymmetry note in :mod:`.transport_health`.

``last_success_ts`` is never stamped for ``lan_udp``. There is no receive
direction on this transport — the reads belong to ``lan``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Final

from .models.transport import TransportHealth
from .zone_state import profile_for

if TYPE_CHECKING:
    from .coordinator import GoveeCoordinator

_LOGGER = logging.getLogger(__name__)

TRANSPORT_LAN_UDP: Final = "lan_udp"
"""Transport kind for the raw write-only UDP path (see models/transport.py)."""

REASON_NO_LAN_PRESENCE: Final = "no_lan_presence"
REASON_NO_RAW_PROFILE: Final = "no_raw_profile"
REASON_SEND_FAILED: Final = "send_failed"


def refresh(coordinator: GoveeCoordinator) -> None:
    """Re-score ``lan_udp`` for every known device.

    Called from the coordinator's LAN staleness pass, so it runs on the same
    cadence as the ``lan`` transport it derives from and needs no timer of its
    own.
    """
    lan_active = _lan_active_ids(coordinator)
    for device_id, device in _devices(coordinator).items():
        health = _health(coordinator, device_id)
        if health is None:
            continue
        if device_id not in lan_active:
            health.is_available = False
            health.last_failure_reason = REASON_NO_LAN_PRESENCE
            continue
        if profile_for(_sku(device)) is None:
            health.is_available = False
            health.last_failure_reason = REASON_NO_RAW_PROFILE
            continue
        health.is_available = True
        if health.last_failure_reason in (REASON_NO_LAN_PRESENCE, REASON_NO_RAW_PROFILE):
            health.last_failure_reason = None


def note_send(coordinator: GoveeCoordinator, device_id: str) -> None:
    """Stamp an outbound raw frame. Deliberately does NOT set availability."""
    health = _health(coordinator, device_id)
    if health is None:
        return
    health.last_send_ts = datetime.now(timezone.utc)


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
