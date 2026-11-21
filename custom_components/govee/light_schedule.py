"""On-device sleep, wake-up and timer settings of a light (fork feature).

:func:`~.api.protocol.decode_status` reads them from the ``aa 11``, ``aa 12``
and ``aa 23`` frames of a status push. This module keeps the last value per
device. Each push merges into it, and so does each schedule action after its
write is sent.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Final

from .api.protocol import LightTimer, SleepTimer, WakeUp, decode_status
from .zone_state import profile_for

if TYPE_CHECKING:
    from .coordinator import GoveeCoordinator
    from .models import GoveeDevice

_STORE_ATTR: Final = "_govee_light_schedules"


@dataclass(frozen=True)
class LightSchedule:
    """A light's schedule as last reported or written. None is not known."""

    sleep: SleepTimer | None = None
    wake: WakeUp | None = None
    timers: tuple[LightTimer, ...] | None = None


def schedules(coordinator: GoveeCoordinator) -> dict[str, LightSchedule]:
    """The coordinator's schedule store, built on first use."""
    existing = getattr(coordinator, _STORE_ATTR, None)
    if not isinstance(existing, dict):
        existing = {}
        setattr(coordinator, _STORE_ATTR, existing)
    return existing


def schedule(coordinator: GoveeCoordinator, device_id: str) -> LightSchedule:
    """The device's schedule as last reported or written."""
    return schedules(coordinator).get(device_id, LightSchedule())


def reports_schedule(sku: str) -> bool:
    profile = profile_for(sku)
    return profile is not None and profile.schedule_report


def apply_schedule_report(coordinator: GoveeCoordinator, device: GoveeDevice, frames: Iterable[bytes]) -> bool:
    """Merge the schedule frames of one push into the store.

    Returns:
        True when anything stored for the device changed.
    """
    if not reports_schedule(device.sku):
        return False
    seen = decode_status(frames)
    before = schedule(coordinator, device.device_id)
    after = LightSchedule(
        sleep=before.sleep if seen.sleep is None else seen.sleep,
        wake=before.wake if seen.wake_up is None else seen.wake_up,
        timers=before.timers if seen.timers is None else seen.timers,
    )
    if after == before:
        return False
    schedules(coordinator)[device.device_id] = after
    return True


def record_sleep(coordinator: GoveeCoordinator, device_id: str, sleep: SleepTimer) -> None:
    """Store a sleep timer a write just set."""
    schedules(coordinator)[device_id] = replace(schedule(coordinator, device_id), sleep=sleep)


def record_wake_up(coordinator: GoveeCoordinator, device_id: str, wake: WakeUp) -> None:
    """Store a wake-up a write just set."""
    schedules(coordinator)[device_id] = replace(schedule(coordinator, device_id), wake=wake)


def record_timer(coordinator: GoveeCoordinator, device_id: str, slot: int, timer: LightTimer) -> None:
    """Store one timer slot a write just set.

    Nothing is stored until a push has reported every slot, because the store
    holds the slots as one tuple.
    """
    current = schedule(coordinator, device_id)
    if current.timers is None or not 0 <= slot < len(current.timers):
        return
    timers = current.timers[:slot] + (timer,) + current.timers[slot + 1 :]
    schedules(coordinator)[device_id] = replace(current, timers=timers)
