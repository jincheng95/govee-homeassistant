"""Device state from raw status frames on the AWS IoT channel (fork feature).

Applies the reports :func:`~.api.protocol.decode_status` reads, for the SKUs
whose profile declares them.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from .api.protocol import decode_status
from .zone_state import profile_for

if TYPE_CHECKING:
    from .coordinator import GoveeCoordinator
    from .models import GoveeDevice


def apply_raw_status(coordinator: GoveeCoordinator, device: GoveeDevice, frames: Iterable[bytes]) -> bool:
    """Apply the reports in ``frames`` to the device's state.

    Args:
        coordinator: The coordinator owning the device.
        device: The device the frames came from.
        frames: Raw 20-byte frames, in any mix.

    Returns:
        True when anything changed, so the caller notifies listeners.
    """
    profile = profile_for(device.sku)
    if profile is None or device.is_group:
        return False
    report = decode_status(frames)
    changed = False
    state = coordinator.get_state(device.device_id)
    if profile.power_push and report.power is not None and state is not None and state.power_state != report.power:
        state.power_state = report.power
        changed = True
    return changed
