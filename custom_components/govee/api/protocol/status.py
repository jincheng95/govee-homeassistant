"""Device reports decoded from raw status frames.

::

    ee 30 01 <00|01> ...   power report, pushed when the power changes
    aa a3 <00|01>          gradient reply to an ``aa a3`` query
    aa 41 02 <n>           power-off memory reply to an ``aa 41`` query

A frame with a bad checksum, a wrong length or an unexpected value is skipped.
``DeviceProfile.power_push`` and the setting capabilities in
:data:`SETTING_QUERIES` say which reports a SKU sends.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Final

from .encoders import GRADIENT_COMMAND_TYPE, POWER_OFF_MEMORY_BEHAVIOURS, POWER_OFF_MEMORY_COMMAND_TYPE
from .frames import FRAME_LENGTH, PRO_TYPE_QUERY, xor_checksum
from .profiles import Capability

POWER_REPORT: Final = (0xEE, 0x30, 0x01)
POWER_OFF_MEMORY_SUB_TYPE: Final = 0x02

SETTING_QUERIES: Final[Mapping[Capability, int]] = {
    Capability.GRADIENT: GRADIENT_COMMAND_TYPE,
    Capability.POWER_OFF_MEMORY: POWER_OFF_MEMORY_COMMAND_TYPE,
}
"""The query command type that reads back each setting capability."""


@dataclass(frozen=True)
class StatusReport:
    """What one batch of frames reported. A field stays None when absent."""

    power: bool | None = None
    gradient: bool | None = None
    power_off_memory: int | None = None


def _valid(frame: bytes) -> bool:
    return len(frame) == FRAME_LENGTH and xor_checksum(frame[:-1]) == frame[-1]


def decode_status(frames: Iterable[bytes]) -> StatusReport:
    """Decode the reports in ``frames``; the last frame of each kind wins."""
    power: bool | None = None
    gradient: bool | None = None
    memory: int | None = None
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
    return StatusReport(power=power, gradient=gradient, power_off_memory=memory)
