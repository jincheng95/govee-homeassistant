"""Per-segment state decoded from the MQTT status push (fork feature).

What this is
------------
The `reference` §6.2 read frame ``aa a5 <idx>`` reports four segments' live
brightness and colour, four bytes each::

    aa a5 <idx> | <L R G B> <L R G B> <L R G B> <L R G B> | pad | xor

``idx`` is 1-based and echoes back in byte 2, so ``idx 1`` carries segments
1-4, ``idx 2`` segments 5-8, and so on. ``L`` is the segment's level 0-100,
then its RGB.

Until 2026-08-14 that frame was believed to be a BLE-only answer to an
explicit query. It is not: the H6046 light bar's **AWS IoT status pushes carry
the same frames unsolicited**, base64-encoded in ``op.command`` alongside the
ordinary ``state`` object. Observed live, a bar with segment index 1 painted
magenta at 70 %::

    ["qgUVAAAAAAAAAAAAAAAAAAAAALo=",   # aa 05 — active sub-mode
     "qqUBZAAAAEb/AP9kAAAAZAAAACw=",   # aa a5 01 — segments 1-4
     "qqUCZAAAAGQAAABkAAAAZAAAAA0=",   # aa a5 02 — segments 5-8
     "qqUDZAAAAGQAAABkZEZkAGRkZEo=",   # aa a5 03 — segments 9-12 (10 real)
     "qhEAHg8PAAAAAAAAAAAAAAAAAKU="]   # aa 11 — settings

The second frame's second group is ``46 ff 00 ff``: level 0x46 = 70, RGB
magenta. That is the segment the user had just painted, read back off the
hardware ~1-2 s later.

Why it matters
--------------
Segment entities are optimistic by necessity — the cloud returns empty strings
for segment colours, so ``platforms/segment.py`` keeps local state and
deliberately does not subscribe to coordinator updates. Optimism drifts: a
paint from the vendor app, a scene, or a failed write all leave HA showing
something the bar is not doing. This frame is the correction, on a channel the
integration already subscribes to and at zero extra radio cost.

Scope of this module
--------------------
Decode only. Nothing here imports Home Assistant, knows about entities, or
decides what to do with a reading — the caller supplies the segment count and
routes the result. That keeps the phantom-index rule (below) a caller policy
backed by the profile table rather than a constant hidden in a parser.

**Phantom indices.** The frames always come in whole groups of four, so a
10-segment bar reports 12 segments and the last two are whatever the firmware
left in the buffer (in the capture above: level 100 RGB ``64 46 64`` and level
0 RGB ``64 64 64`` — neither a colour anything is showing). Readings at or
above the profile's verified segment count are dropped, never surfaced.
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


def level_to_ha_brightness(level: int) -> int:
    """The wire's 0-100 level as HA's 0-255 brightness.

    The exact inverse of :func:`..api.protocol.ha_to_percent` at every
    level the write path can produce, which is what keeps a readback of our own
    write from looking like a change: 178 → 70 % → 178.

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
    for encoded in commands:
        if not isinstance(encoded, str):
            continue
        try:
            frames.append(base64.b64decode(encoded))
        except (binascii.Error, ValueError):
            _LOGGER.debug("Failed to base64-decode an op.command entry")
            continue

    return decode_frames(frames, segment_count=segment_count)
