"""Shared pieces for tests that stand a MagicMock in for the coordinator."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from custom_components.govee.models.transport import TransportHealth


def follow_online_flag(coordinator: MagicMock) -> MagicMock:
    """Give a mock coordinator a ``device_reachable`` like the real one's (#242).

    Reachable when the state's online flag is set or the mock reports a
    healthy LAN or BLE TransportHealth. A bare MagicMock would answer with a
    truthy mock and make every device look reachable. The state is read at
    call time, so a test that changes ``get_state`` afterwards still counts.
    """

    def _reachable(device_id: str) -> bool:
        state: Any = coordinator.get_state(device_id)
        if state is None:
            return False
        if state.online is True:
            return True
        for kind in ("lan", "ble"):
            health = coordinator.get_transport_health(device_id, kind)
            if isinstance(health, TransportHealth) and health.is_available:
                return True
        return False

    coordinator.device_reachable = MagicMock(side_effect=_reachable)
    return coordinator
