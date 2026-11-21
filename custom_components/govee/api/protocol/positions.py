"""Bulb position map for string lights that play effects in 3D (fork feature).

The lamp stores one map and applies it to every position-aware effect. The
payload is the bulb count, then ``x, y, z`` per bulb in LED order, all u16
big-endian; ``y`` is height. It travels as one ``0xA4`` message, command
``0x24``, and the lamp never reports the coordinates back.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from .errors import PositionMapError
from .packets import chunk_mtu

POSITION_COMMAND: Final = 0x24

APP_EXTENT: Final = 500
"""Edge of the cube the Govee app fits its maps into, minimum at the origin."""

MAX_COORDINATE: Final = 1000
"""Largest coordinate accepted. Values above :data:`APP_EXTENT` are untested on hardware."""

MESSAGE_GAP_SECONDS: Final = 0.12
"""Pause between packets. Each packet goes in its own ``ptReal`` message."""


def normalize_positions(points: Sequence[Sequence[float]], *, extent: int = APP_EXTENT) -> list[tuple[int, int, int]]:
    """Scale points uniformly into a cube of edge ``extent``.

    Each axis is shifted so its minimum is 0. One scale factor serves all
    three axes, so the shape keeps its aspect ratio and the longest axis spans
    ``0..extent``. Points that all coincide map to the origin.

    Args:
        points: ``(x, y, z)`` triples of any real numbers.
        extent: Edge of the target cube.

    Returns:
        Integer triples in ``0..extent``.
    """
    if not points:
        return []
    minimums = [min(point[axis] for point in points) for axis in range(3)]
    span = max(max(point[axis] for point in points) - minimums[axis] for axis in range(3))
    scale = extent / span if span else 0.0
    return [
        (
            min(extent, round((point[0] - minimums[0]) * scale)),
            min(extent, round((point[1] - minimums[1]) * scale)),
            min(extent, round((point[2] - minimums[2]) * scale)),
        )
        for point in points
    ]


def encode_positions(points: Sequence[Sequence[int]], *, bulb_count: int) -> bytes:
    """The ``0x24`` payload for a position map.

    Args:
        points: One ``(x, y, z)`` integer triple per bulb, in LED order.
        bulb_count: How many bulbs the lamp has. The map must cover every one.

    Returns:
        ``count16`` followed by ``x16 y16 z16`` per bulb, big-endian.

    Raises:
        PositionMapError: If the point count differs from ``bulb_count``, a
            point is not a triple, or a coordinate is not an int in
            ``0..MAX_COORDINATE``.
    """
    if len(points) != bulb_count:
        raise PositionMapError(f"the map has {len(points)} points, the lamp has {bulb_count} bulbs")
    payload = bytearray(bulb_count.to_bytes(2, "big"))
    for index, point in enumerate(points):
        if len(point) != 3:
            raise PositionMapError(f"point {index} is not an (x, y, z) triple")
        for value in point:
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_COORDINATE:
                raise PositionMapError(f"point {index} has {value!r}, expected an integer in 0..{MAX_COORDINATE}")
            payload += value.to_bytes(2, "big")
    return bytes(payload)


def position_map_frames(points: Sequence[Sequence[int]], *, bulb_count: int) -> list[bytes]:
    """The ``0xA4`` frames that store a position map, in send order.

    Raises:
        PositionMapError: See :func:`encode_positions`.
    """
    return chunk_mtu(POSITION_COMMAND, encode_positions(points, bulb_count=bulb_count))
