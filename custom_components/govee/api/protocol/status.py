"""Device reports decoded from raw status frames.

::

    ee 30 01 <00|01> ...   power report, pushed when the power changes

A frame with a bad checksum, a wrong length or an unexpected value is skipped.
Which reports a SKU sends is profile data (``DeviceProfile.power_push``).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

from .frames import FRAME_LENGTH, xor_checksum

POWER_REPORT: Final = (0xEE, 0x30, 0x01)


@dataclass(frozen=True)
class StatusReport:
    """What one batch of frames reported. A field stays None when absent."""

    power: bool | None = None


def _valid(frame: bytes) -> bool:
    return len(frame) == FRAME_LENGTH and xor_checksum(frame[:-1]) == frame[-1]


def decode_status(frames: Iterable[bytes]) -> StatusReport:
    """Decode the reports in ``frames``; the last frame of each kind wins."""
    power: bool | None = None
    for frame in frames:
        if not _valid(frame):
            continue
        if tuple(frame[:3]) == POWER_REPORT and frame[3] in (0, 1):
            power = frame[3] == 1
    return StatusReport(power=power)
