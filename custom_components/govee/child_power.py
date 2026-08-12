"""Child-entity power sync to the whole-device light (fork feature).

Zones and segments are *children* of one lamp: neither has any power of its
own, and lighting one while the lamp is reported off produces a child entity
that says "on" above a device that says "off" — the exact state the zone
lights were fixed to avoid.

The zone lights have always resolved this by powering the lamp on the **normal
transport** before their own (raw-LAN) write goes out: the whole-device power
command is the only one with real state behind it, so it goes through
``coordinator.async_control_device`` (BLE > LAN > MQTT > REST, with upstream's
optimistic update) rather than as a raw frame. The segment entities did not,
so painting a segment left the master off. This module is that one rule,
extracted so both platforms call the same code.

Deliberately one-directional: a child turning **off** must never power the lamp
off, because the other children may still be lit and nothing reports per-child
power back (see roadmap 1.5).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .models import PowerCommand

if TYPE_CHECKING:  # pragma: no cover
    from .coordinator import GoveeCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_ensure_device_powered(coordinator: GoveeCoordinator, device_id: str) -> None:
    """Power the lamp on over the normal path when it is reported off.

    A no-op when the device is already on, or when nothing is known about it —
    an unknown state is not evidence that the lamp is off, and sending a power
    command on a guess would light a lamp the user did not ask to light.

    Args:
        coordinator: The coordinator owning the device.
        device_id: The Govee device id of the parent lamp.
    """
    state = coordinator.get_state(device_id)
    if state is None or state.power_state:
        return
    _LOGGER.debug("Govee child power sync: powering %s on for a child turn_on", device_id)
    await coordinator.async_control_device(device_id, PowerCommand(power_on=True))
