"""Zone power decoded from the ``ee 30 01 <bits>`` report (fork feature).

A multi-zone lamp sends this frame after every whole-lamp or zone power change.
Bit 7 of ``<bits>`` is the whole lamp. Each other bit is one zone, placed by
:attr:`~.protocol.profiles.ZoneSpec.power_bit`. Decode only, with no HA
imports. The XOR checksum is an integrity check, never an authenticity one.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Final

from .protocol.profiles import DeviceProfile
from .segment_readback import FRAME_LENGTH, payload_frames

REPORT_HEADER: Final = (0xEE, 0x30, 0x01)
"""``ee 30 01`` — the zone power report. Byte 3 carries the power bits."""

LAMP_POWER_BIT: Final = 0x80
"""Set while the whole lamp is on."""


@dataclass(frozen=True)
class ZonePowerReport:
    """Whole-lamp and per-zone power, as the lamp reports it.

    Attributes:
        lamp_on: Whether the whole lamp is on.
        zones: Zone key to lit state, for every zone with a power bit.
    """

    lamp_on: bool
    zones: Mapping[str, bool]


def _checksum_ok(frame: bytes) -> bool:
    checksum = 0
    for byte in frame[: FRAME_LENGTH - 1]:
        checksum ^= byte
    return checksum == frame[FRAME_LENGTH - 1]


def decode_frames(frames: Iterable[bytes], profile: DeviceProfile) -> ZonePowerReport | None:
    """Decode the newest zone power report in ``frames``.

    Args:
        frames: Raw 20-byte frames in arrival order. Other frames are skipped.
        profile: The device's profile, which places each zone's bit.

    Returns:
        The last valid report, or None when the profile declares no report or
        ``frames`` carry none.
    """
    if not profile.zone_power_report:
        return None
    bits: int | None = None
    for frame in frames:
        if len(frame) != FRAME_LENGTH or tuple(frame[:3]) != REPORT_HEADER:
            continue
        if not _checksum_ok(frame):
            continue
        bits = frame[3]
    if bits is None:
        return None
    return ZonePowerReport(
        lamp_on=bool(bits & LAMP_POWER_BIT),
        zones={zone.key: bool(bits & zone.power_bit) for zone in profile.zones if zone.power_bit is not None},
    )


def decode_payload(data: Mapping[str, Any], profile: DeviceProfile) -> ZonePowerReport | None:
    """Decode the zone power report carried by one inbound MQTT payload.

    Args:
        data: The parsed AWS IoT message, ``op.command`` included.
        profile: The device's profile.

    Returns:
        The newest report in the payload, or None.
    """
    if not profile.zone_power_report:
        return None
    return decode_frames(payload_frames(data), profile)
