"""Per-device transport health tracking.

Tracks connectivity status for each transport (Cloud REST API, AWS IoT
MQTT, direct BLE) so user-visible diagnostic entities can reflect which
channels are currently usable for a device.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

TransportKind = Literal["cloud_api", "mqtt", "ble"]

TRANSPORT_KINDS: tuple[TransportKind, ...] = ("cloud_api", "mqtt", "ble")


@dataclass
class TransportHealth:
    """Connectivity status for a single (device, transport) pair."""

    transport: TransportKind
    is_available: bool = False
    last_success_ts: datetime | None = None
    last_failure_ts: datetime | None = None
    last_failure_reason: str | None = None

    def mark_success(self, now: datetime) -> None:
        """Record a successful use of this transport."""
        self.is_available = True
        self.last_success_ts = now
        self.last_failure_reason = None

    def mark_failure(self, now: datetime, reason: str) -> None:
        """Record a failed use of this transport."""
        self.is_available = False
        self.last_failure_ts = now
        self.last_failure_reason = reason

    def mark_unavailable(self, reason: str | None = None) -> None:
        """Mark this transport as unavailable without stamping a failure time."""
        self.is_available = False
        if reason is not None:
            self.last_failure_reason = reason
