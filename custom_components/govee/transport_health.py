"""Per-device transport-health tracking.

Extracted from coordinator.py for testability + cohesion (audit H1).
Tracks last-success / last-failure timestamps and availability per
(device, transport) where transport ∈ {cloud_api, mqtt, ble}.

The tracker is callback-free and side-effect-free — it owns the
transport-health dict and the policy for staleness decisions, but does
not know about the coordinator, devices, or BLE/MQTT clients. All
inputs are passed in explicitly.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from .models.transport import (
    TRANSPORT_KINDS,
    TransportHealth,
    TransportKind,
)

# Time after which a BLE advertisement is considered stale.
BLE_STALE_SECONDS = 120


class TransportHealthTracker:
    """Owns per-device transport health entries.

    Keys: device_id -> kind -> TransportHealth.
    """

    def __init__(self) -> None:
        self._health: dict[str, dict[TransportKind, TransportHealth]] = {}

    @property
    def health(self) -> dict[str, dict[TransportKind, TransportHealth]]:
        """Read-only view of the underlying dict (mutated in place)."""
        return self._health

    def ensure(self, device_id: str) -> None:
        """Initialize transport-health entries for a device if missing."""
        if device_id in self._health:
            return
        self._health[device_id] = {
            kind: TransportHealth(transport=kind) for kind in TRANSPORT_KINDS
        }

    def get(self, device_id: str, transport: TransportKind) -> TransportHealth | None:
        """Return health for (device, transport), or None if untracked."""
        per_device = self._health.get(device_id)
        if per_device is None:
            return None
        return per_device.get(transport)

    def record_success(self, device_id: str, transport: TransportKind) -> None:
        """Stamp a successful transport use."""
        self.ensure(device_id)
        self._health[device_id][transport].mark_success(datetime.now(timezone.utc))

    def record_failure(
        self, device_id: str, transport: TransportKind, reason: str
    ) -> None:
        """Stamp a failed transport use."""
        self.ensure(device_id)
        self._health[device_id][transport].mark_failure(
            datetime.now(timezone.utc), reason
        )

    def refresh_mqtt_for_devices(
        self,
        device_ids: Iterable[str],
        *,
        connected: bool,
        client_configured: bool,
    ) -> None:
        """Propagate MQTT client connection state to per-device entries.

        Doesn't backdate ``last_success_ts``; only real pushes do that.
        """
        for device_id in device_ids:
            self.ensure(device_id)
            mqtt = self._health[device_id]["mqtt"]
            if connected:
                mqtt.is_available = True
                mqtt.last_failure_reason = None
            else:
                mqtt.is_available = False
                if not client_configured:
                    mqtt.last_failure_reason = "not_configured"
                else:
                    mqtt.last_failure_reason = "disconnected"

    def refresh_ble_staleness(
        self,
        device_ids: Iterable[str],
        ble_connected_ids: set[str],
    ) -> None:
        """Mark BLE unavailable when last advertisement exceeds stale window."""
        now = datetime.now(timezone.utc)
        for device_id in device_ids:
            self.ensure(device_id)
            ble = self._health[device_id]["ble"]
            if device_id not in ble_connected_ids:
                ble.is_available = False
                continue
            last = ble.last_success_ts
            if last is None:
                continue
            if (now - last).total_seconds() > BLE_STALE_SECONDS:
                ble.is_available = False
                ble.last_failure_reason = "stale_advertisement"
