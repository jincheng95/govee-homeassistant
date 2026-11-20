"""On-device sleep, wake-up and timer settings of a light (fork feature).

A light reports them in three status frames::

    aa 11 | enable startBrightness minutes remaining ...
    aa 12 | enable endBrightness hour minute repeat rampMinutes ...
    aa 23 | ff, then four of: enableAndType hour minute repeat

Only an enable byte of 1 counts as on for sleep and wake-up; a timer counts as
on when the top bit of its first byte is set. Neither rule has been checked
against a light with a schedule switched on.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from .zone_state import profile_for

if TYPE_CHECKING:
    from .coordinator import GoveeCoordinator
    from .models import GoveeDevice

_STORE_ATTR: Final = "_govee_light_schedules"
_TIMER_SLOTS: Final = 4
_TIMER_ON: Final = 0x80


@dataclass(frozen=True)
class SleepTimer:
    enabled: bool
    start_brightness: int
    minutes: int
    remaining_minutes: int


@dataclass(frozen=True)
class WakeUp:
    enabled: bool
    end_brightness: int
    hour: int
    minute: int
    repeat: int
    ramp_minutes: int


@dataclass(frozen=True)
class Timer:
    enabled: bool
    hour: int
    minute: int
    repeat: int


@dataclass(frozen=True)
class LightSchedule:
    """What one status push said. A field stays None when its frame was absent."""

    sleep: SleepTimer | None = None
    wake: WakeUp | None = None
    timers: tuple[Timer, ...] | None = None


def _valid(raw: bytes) -> bool:
    if len(raw) != 20 or raw[0] != 0xAA:
        return False
    checksum = 0
    for byte in raw[:19]:
        checksum ^= byte
    return checksum == raw[19]


def decode(frames: Iterable[bytes]) -> LightSchedule:
    """Read the schedule frames out of one status push."""
    sleep: SleepTimer | None = None
    wake: WakeUp | None = None
    timers: tuple[Timer, ...] | None = None
    for raw in frames:
        if not _valid(raw):
            continue
        body = raw[2:19]
        if raw[1] == 0x11:
            sleep = SleepTimer(body[0] == 1, min(body[1], 100), min(body[2], 240), min(body[3], 240))
        elif raw[1] == 0x12:
            wake = WakeUp(body[0] == 1, min(body[1], 100), min(body[2], 23), min(body[3], 59), body[4], body[5])
        elif raw[1] == 0x23 and body[0] == 0xFF:
            timers = tuple(
                Timer(
                    bool(body[1 + 4 * slot] & _TIMER_ON),
                    min(body[2 + 4 * slot], 23),
                    min(body[3 + 4 * slot], 59),
                    body[4 + 4 * slot],
                )
                for slot in range(_TIMER_SLOTS)
            )
    return LightSchedule(sleep, wake, timers)


def schedules(coordinator: GoveeCoordinator) -> dict[str, LightSchedule]:
    """The coordinator's schedule store, built on first use."""
    existing: dict[str, LightSchedule] | None = getattr(coordinator, _STORE_ATTR, None)
    if existing is None:
        existing = {}
        setattr(coordinator, _STORE_ATTR, existing)
    return existing


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
    seen = decode(frames)
    store = schedules(coordinator)
    before = store.get(device.device_id, LightSchedule())
    after = LightSchedule(
        sleep=before.sleep if seen.sleep is None else seen.sleep,
        wake=before.wake if seen.wake is None else seen.wake,
        timers=before.timers if seen.timers is None else seen.timers,
    )
    if after == before:
        return False
    store[device.device_id] = after
    return True
