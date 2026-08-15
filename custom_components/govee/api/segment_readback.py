"""Per-segment state decoded from the MQTT status push (fork feature).

The ``aa a5 <idx>`` frame carries four segments' level and RGB, four bytes each,
with a 1-based ``idx`` echoed in byte 2. Some SKUs push it unsolicited on the AWS
IoT status channel, base64 in ``op.command`` — the only readback the otherwise
optimistic segment entities get. Decode only: no HA imports, and the caller
supplies the segment count. Frames always arrive in whole groups of four, so
readings at or above the profile's verified count are phantom padding and are
dropped. The XOR checksum is an integrity check, never an authenticity one.
"""

from __future__ import annotations

import base64
import binascii
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Final

_LOGGER = logging.getLogger(__name__)

FRAME_LENGTH: Final = 20
"""Every frame on this protocol is exactly 20 bytes, checksum included."""

READBACK_HEADER: Final = (0xAA, 0xA5)
"""``aa a5`` — the per-segment state frame. Byte 2 is the 1-based group index."""

SEGMENTS_PER_FRAME: Final = 4
"""Four ``L R G B`` quads per frame; the tail is padding."""

_QUAD_OFFSET: Final = 3
"""First quad starts after ``aa a5 <idx>``."""

HA_BRIGHTNESS_MAX: Final = 255
"""HA's brightness scale. The wire carries 0-100."""

MAX_COMMANDS: Final = 64
"""Most ``op.command`` entries decoded from one payload; the rest are dropped.

A real push carries five. A 10-segment bar needs three readback frames, and a
40-segment one would need ten, so this is far above anything the hardware
produces and far below anything worth spending time decoding.
"""

MAX_COMMAND_CHARS: Final = 1024
"""Longest ``op.command`` entry decoded; a 20-byte frame is 28 base64 chars."""


def level_to_ha_brightness(level: int) -> int:
    """The wire's 0-100 level as HA's 0-255 brightness.

    Not an inverse of :func:`..api.protocol.ha_to_percent`: the wire quantises
    255 values into 101, so a round trip snaps to the nearest of the ~2.55
    HA values that share a level (179 → 70 % → 178). Comparisons that must not
    see that snap as a change belong in level space, not brightness space.

    Args:
        level: Segment level 0-100 as the frame reports it.

    Returns:
        Brightness 0-255, clamped.
    """
    return max(0, min(HA_BRIGHTNESS_MAX, round(int(level) * HA_BRIGHTNESS_MAX / 100)))


@dataclass(frozen=True)
class SegmentReading:
    """One segment's state as the hardware reports it.

    Attributes:
        index: Zero-based flat segment index, matching the entity's own.
        level: Brightness 0-100 exactly as the frame carries it.
        rgb: The segment's colour.
    """

    index: int
    level: int
    rgb: tuple[int, int, int]

    @property
    def is_on(self) -> bool:
        """Whether this segment is actually emitting light.

        Two different things read as "off" on this hardware and both must
        count. A segment turned off through the integration is painted black
        (``platforms/segment.py`` sends RGB 0,0,0 — there is no per-segment
        power command), so it reports level 100 with black. A segment dimmed to
        nothing reports level 0. Treating only one of them as off would leave
        every off segment showing as lit in the other case.
        """
        return self.level > 0 and self.rgb != (0, 0, 0)

    @property
    def brightness(self) -> int:
        """The reading's level on HA's 0-255 scale."""
        return level_to_ha_brightness(self.level)


def _checksum_ok(frame: bytes) -> bool:
    """Whether the frame's XOR checksum byte agrees with its body."""
    checksum = 0
    for byte in frame[: FRAME_LENGTH - 1]:
        checksum ^= byte
    return checksum == frame[FRAME_LENGTH - 1]


def decode_frames(
    frames: Iterable[bytes],
    *,
    segment_count: int,
) -> dict[int, SegmentReading]:
    """Decode the ``aa a5`` frames in ``frames`` into per-segment readings.

    Frames that are not segment readbacks (``aa 05`` sub-mode, ``aa 11``
    settings, the leak hub's ``0xEE`` reports, anything short) are skipped
    silently — this is a filter over a mixed stream, not a parser that expects
    every frame to be its own.

    Args:
        frames: Raw 20-byte frames, in any order.
        segment_count: The profile's verified segment count. Readings at or
            above it are phantom (see the module docstring) and dropped.

    Returns:
        Zero-based segment index → reading. Empty when the stream carried no
        readback frames, which is the common case.
    """
    readings: dict[int, SegmentReading] = {}
    if segment_count <= 0:
        return readings

    for frame in frames:
        if len(frame) != FRAME_LENGTH:
            continue
        if (frame[0], frame[1]) != READBACK_HEADER:
            continue
        if not _checksum_ok(frame):
            _LOGGER.debug("Discarding aa a5 frame with a bad checksum: %s", frame.hex())
            continue

        group = frame[2]
        if group < 1:
            continue

        base = (group - 1) * SEGMENTS_PER_FRAME
        for slot in range(SEGMENTS_PER_FRAME):
            index = base + slot
            if index >= segment_count:
                # Phantom tail: the frame always carries four quads, the
                # hardware does not always have four more segments.
                continue
            offset = _QUAD_OFFSET + slot * 4
            readings[index] = SegmentReading(
                index=index,
                level=frame[offset],
                rgb=(frame[offset + 1], frame[offset + 2], frame[offset + 3]),
            )

    return readings


def decode_payload(
    data: Mapping[str, Any],
    *,
    segment_count: int,
) -> dict[int, SegmentReading]:
    """Decode the segment readback carried by one inbound MQTT payload.

    Args:
        data: The parsed AWS IoT message. ``op.command`` is read when present;
            any other shape yields nothing.
        segment_count: The profile's verified segment count.

    Returns:
        Zero-based segment index → reading, empty when this payload carried no
        readback frames.
    """
    op = data.get("op")
    if not isinstance(op, Mapping):
        return {}
    commands = op.get("command")
    if not isinstance(commands, (list, tuple)):
        return {}

    frames: list[bytes] = []
    # Bounded before decoding: `op.command` is remote input, and a base64
    # string decodes to ~3/4 its length in memory. A real payload carries a
    # handful of 20-byte frames, so both caps are orders of magnitude clear of
    # anything the hardware sends.
    for encoded in commands[:MAX_COMMANDS]:
        if not isinstance(encoded, str):
            continue
        if len(encoded) > MAX_COMMAND_CHARS:
            _LOGGER.debug("Skipping an oversized op.command entry (%d chars)", len(encoded))
            continue
        try:
            frames.append(base64.b64decode(encoded))
        except (binascii.Error, ValueError):
            _LOGGER.debug("Failed to base64-decode an op.command entry")
            continue

    return decode_frames(frames, segment_count=segment_count)
