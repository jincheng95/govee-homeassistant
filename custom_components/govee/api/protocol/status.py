"""Device reports decoded from raw status frames.

::

    ee 30 01 <00|01> ...   power report, pushed when the power changes
    aa a3 <00|01>          gradient reply to an ``aa a3`` query
    aa 41 02 <n>           power-off memory reply to an ``aa 41`` query
    aa 11 <en> <brightness> <minutes> <minutes left> ...
                           sleep timer reply to an ``aa 11`` query; both
                           minute bytes read as 0-255, as the encoder writes
    aa 12 <en> <brightness> <hour> <minute> <repeat> <minutes> 00 <R G B> <K>
                           wake-up reply to an ``aa 12`` query; an enable
                           byte of ``ff`` reads as disabled
    aa 23 ff <4 x 4>       timer reply to an ``aa 23 ff`` query, one
                           ``<flags> <hour> <minute> <repeat>`` group per slot

A frame with a bad checksum, a wrong length or an unexpected value is skipped.
``DeviceProfile.power_push`` and the capabilities in :data:`SETTING_QUERIES`
and :data:`SCHEDULE_QUERIES` say which reports a SKU sends.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Final

from .encoders import (
    GRADIENT_COMMAND_TYPE,
    LIGHT_TIMER_COMMAND_TYPE,
    POWER_OFF_MEMORY_BEHAVIOURS,
    POWER_OFF_MEMORY_COMMAND_TYPE,
    REPEAT_MARKER,
    SLEEP_TIMER_COMMAND_TYPE,
    TIMER_ENABLED,
    TIMER_TURN_ON,
    WAKE_UP_COMMAND_TYPE,
    repeat_days,
)
from .frames import FRAME_LENGTH, PRO_TYPE_QUERY, xor_checksum
from .profiles import Capability

POWER_REPORT: Final = (0xEE, 0x30, 0x01)
POWER_OFF_MEMORY_SUB_TYPE: Final = 0x02
TIMER_READ_ALL: Final = 0xFF
"""The ``aa 23`` selector that reads every timer slot."""
TIMER_REPLY_SLOTS: Final = 4
WAKE_UP_NEVER_SET: Final = 0xFF
"""The wake-up enable byte of a light whose wake-up was never set. It reads as disabled."""

SETTING_QUERIES: Final[Mapping[Capability, int]] = {
    Capability.GRADIENT: GRADIENT_COMMAND_TYPE,
    Capability.POWER_OFF_MEMORY: POWER_OFF_MEMORY_COMMAND_TYPE,
}
"""The query command type that reads back each setting capability."""

SCHEDULE_QUERIES: Final[Mapping[Capability, tuple[int, ...]]] = {
    Capability.SLEEP_TIMER: (SLEEP_TIMER_COMMAND_TYPE,),
    Capability.WAKE_UP: (WAKE_UP_COMMAND_TYPE,),
    Capability.LIGHT_TIMER: (LIGHT_TIMER_COMMAND_TYPE, TIMER_READ_ALL),
}
"""The query bytes after ``aa`` that read back each schedule capability.

Only a profile without ``schedule_report`` is asked, because the others push
these frames unprompted.
"""


@dataclass(frozen=True)
class LightTimer:
    """One on/off timer slot. ``weekdays`` runs 0 Monday to 6 Sunday; empty runs once."""

    enabled: bool
    turn_on: bool
    hour: int
    minute: int
    weekdays: tuple[int, ...]


@dataclass(frozen=True)
class SleepTimer:
    """The sleep timer, which dims from ``start_brightness`` percent to off over ``minutes``."""

    enabled: bool
    start_brightness: int
    minutes: int
    remaining_minutes: int


@dataclass(frozen=True)
class WakeUp:
    """The wake-up, which brightens to ``end_brightness`` percent by the given time."""

    enabled: bool
    end_brightness: int
    hour: int
    minute: int
    weekdays: tuple[int, ...]
    duration: int
    rgb: tuple[int, int, int]
    kelvin: int


@dataclass(frozen=True)
class StatusReport:
    """What one batch of frames reported. A field stays None when absent."""

    power: bool | None = None
    gradient: bool | None = None
    power_off_memory: int | None = None
    timers: tuple[LightTimer, ...] | None = None
    sleep: SleepTimer | None = None
    wake_up: WakeUp | None = None


def _valid(frame: bytes) -> bool:
    return len(frame) == FRAME_LENGTH and xor_checksum(frame[:-1]) == frame[-1]


def _time_of_day(hour: int, minute: int) -> bool:
    return hour <= 23 and minute <= 59


def _decode_timers(frame: bytes) -> tuple[LightTimer, ...] | None:
    timers: list[LightTimer] = []
    for slot in range(TIMER_REPLY_SLOTS):
        flags, hour, minute, repeat = frame[3 + 4 * slot : 7 + 4 * slot]
        if flags & ~(TIMER_ENABLED | TIMER_TURN_ON) or not repeat & REPEAT_MARKER or not _time_of_day(hour, minute):
            return None
        timers.append(
            LightTimer(
                enabled=bool(flags & TIMER_ENABLED),
                turn_on=bool(flags & TIMER_TURN_ON),
                hour=hour,
                minute=minute,
                weekdays=repeat_days(repeat),
            )
        )
    return tuple(timers)


def _decode_sleep(frame: bytes) -> SleepTimer | None:
    enabled, brightness, minutes, remaining = frame[2:6]
    if enabled not in (0, 1) or brightness > 100:
        return None
    return SleepTimer(enabled=enabled == 1, start_brightness=brightness, minutes=minutes, remaining_minutes=remaining)


def _decode_wake_up(frame: bytes) -> WakeUp | None:
    enabled, brightness, hour, minute, repeat, duration = frame[2:8]
    if enabled not in (0, 1, WAKE_UP_NEVER_SET) or brightness > 100 or not repeat & REPEAT_MARKER:
        return None
    if not _time_of_day(hour, minute):
        return None
    return WakeUp(
        enabled=enabled == 1,
        end_brightness=brightness,
        hour=hour,
        minute=minute,
        weekdays=repeat_days(repeat),
        duration=duration,
        rgb=(frame[9], frame[10], frame[11]),
        kelvin=int.from_bytes(frame[12:14], "big"),
    )


def decode_status(frames: Iterable[bytes]) -> StatusReport:
    """Decode the reports in ``frames``; the last frame of each kind wins."""
    power: bool | None = None
    gradient: bool | None = None
    memory: int | None = None
    timers: tuple[LightTimer, ...] | None = None
    sleep: SleepTimer | None = None
    wake: WakeUp | None = None
    for frame in frames:
        if not _valid(frame):
            continue
        if tuple(frame[:3]) == POWER_REPORT and frame[3] in (0, 1):
            power = frame[3] == 1
        elif frame[0] != PRO_TYPE_QUERY:
            continue
        elif frame[1] == GRADIENT_COMMAND_TYPE and frame[2] in (0, 1):
            gradient = frame[2] == 1
        elif (
            frame[1] == POWER_OFF_MEMORY_COMMAND_TYPE
            and frame[2] == POWER_OFF_MEMORY_SUB_TYPE
            and frame[3] in POWER_OFF_MEMORY_BEHAVIOURS
        ):
            memory = frame[3]
        elif frame[1] == LIGHT_TIMER_COMMAND_TYPE and frame[2] == TIMER_READ_ALL:
            timers = _decode_timers(frame) or timers
        elif frame[1] == SLEEP_TIMER_COMMAND_TYPE:
            sleep = _decode_sleep(frame) or sleep
        elif frame[1] == WAKE_UP_COMMAND_TYPE:
            wake = _decode_wake_up(frame) or wake
    return StatusReport(
        power=power, gradient=gradient, power_off_memory=memory, timers=timers, sleep=sleep, wake_up=wake
    )
