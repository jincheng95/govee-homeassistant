"""Per-LED paint upload.

The payload names colours and, for each, the LEDs that take it. Every other
LED shows the base colour::

    effect | speed | baseBrightness | baseR baseG baseB | groups16      speed 0 = still
    per group:  count16 | R G B | wide16 | <narrow indexes, 1 byte each>
                                         | <wide indexes, 2 bytes each>

An index of 256 or more is "wide". All 16-bit values are little-endian. The
payload goes out as ``0xA3`` packets with marker ``0x03`` and is committed
with ``33 05 0a 20 03`` (DIY sub-mode, code 800).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from .errors import FrameError
from .packets import DIY_COMMIT_SUB_MODE, commit_frame
from .scenes import terminated_chunks

PAINT_MARKER = 0x03
PAINT_CODE = 800
EFFECT = 19
STILL_SPEED = 0
"""The effect animates at any other speed; at 0 the paint holds still."""

MESSAGE_FRAMES = 12
"""Frames per ``ptReal`` message. The lamp drops larger messages."""
MESSAGE_GAP_SECONDS = 0.04

_WIDE_FROM = 256

RGB = tuple[int, int, int]


def _rgb(value: Sequence[int], what: str) -> RGB:
    if len(value) != 3 or any(not isinstance(part, int) or not 0 <= part <= 0xFF for part in value):
        raise FrameError(f"{what} must be three values from 0 to 255")
    return (value[0], value[1], value[2])


def _u16(value: int) -> bytes:
    return bytes([value & 0xFF, value >> 8])


def paint_payload(
    colors: Iterable[tuple[Sequence[int], Iterable[int]]],
    *,
    bulb_count: int,
    base_color: Sequence[int] = (0, 0, 0),
    base_brightness: int = 100,
) -> bytes:
    """Encode a paint.

    A LED named under two colours takes the later one. Groups of the same
    colour are merged, in first-seen order.

    Args:
        colors: ``(rgb, led indexes)`` pairs; indexes are 0-based.
        bulb_count: How many LEDs the device has.
        base_color: Colour of every LED not named.
        base_brightness: Brightness of the base colour, 0-100.

    Raises:
        FrameError: A colour, an index or the brightness is out of range.
    """
    if not 0 <= base_brightness <= 100:
        raise FrameError("base brightness must be from 0 to 100")
    base = _rgb(base_color, "base colour")

    owner: dict[int, RGB] = {}
    order: list[RGB] = []
    for raw_rgb, indexes in colors:
        rgb = _rgb(raw_rgb, "colour")
        if rgb not in order:
            order.append(rgb)
        for index in indexes:
            if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < bulb_count:
                raise FrameError(f"LED index {index!r} is outside 0-{bulb_count - 1}")
            owner[index] = rgb

    groups: list[bytes] = []
    for rgb in order:
        members = sorted(index for index, color in owner.items() if color == rgb)
        if not members:
            continue
        narrow = [index for index in members if index < _WIDE_FROM]
        wide = [index for index in members if index >= _WIDE_FROM]
        groups.append(
            _u16(len(members)) + bytes(rgb) + _u16(len(wide)) + bytes(narrow) + b"".join(_u16(i) for i in wide)
        )

    head = bytes([EFFECT, STILL_SPEED, base_brightness, *base]) + _u16(len(groups))
    return head + b"".join(groups)


def paint_frames(
    colors: Iterable[tuple[Sequence[int], Iterable[int]]],
    *,
    bulb_count: int,
    base_color: Sequence[int] = (0, 0, 0),
    base_brightness: int = 100,
) -> list[bytes]:
    """The frames that upload and show one paint. See :func:`paint_payload`."""
    payload = paint_payload(colors, bulb_count=bulb_count, base_color=base_color, base_brightness=base_brightness)
    return [
        *terminated_chunks(payload, PAINT_MARKER),
        commit_frame(sub_mode=DIY_COMMIT_SUB_MODE, index=PAINT_CODE),
    ]


def messages(frames: Sequence[bytes]) -> list[list[bytes]]:
    """Split an upload into messages the lamp accepts, in sending order."""
    return [list(frames[start : start + MESSAGE_FRAMES]) for start in range(0, len(frames), MESSAGE_FRAMES)]
