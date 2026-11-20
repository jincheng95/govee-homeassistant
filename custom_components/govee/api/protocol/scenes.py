"""Catalogue scene upload for the H70CX effect family.

A named scene is a blob the app pushes as ``0xA3`` multipacket frames and then
commits with ``33 05 04 <codeLo> <codeHi>``. Three blob kinds exist::

    empty          firmware preset      commit only
    49 ...         H70CX effect         marker 0x58, lead byte dropped
    anything else  RGBIC effect         marker 0x02, blob sent whole

An H70CX blob is ``49 | len16 | effect | params | nColours | RGB...``. Effect
12 stacks layers, each ``len16 | effect | params | nColours | RGB...``. A
scene's speed and direction are single bytes inside those records, so the
adjustments rewrite the blob and the scene is uploaded again.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .errors import FrameError
from .frames import PRO_TYPE_MULTIPACKET, build_frame
from .packets import DEFAULT_HEADER_LEAD, FIRST_CHUNK_DATA_BYTES, SEQUENCE_TERMINATOR, chunk_effect, commit_frame

SCENE_SUB_MODE = 0x04

H70CX_LEAD = 0x49
H70CX_MARKER = 0x58
RGBIC_MARKER = 0x02

MULTI_LAYER_EFFECT = 12
_NOVELTY_EFFECT = 16
_GEOMETRY_EFFECT = 15

# Byte positions counted from the effect id (position 0).
_SPEED_AT: Mapping[int, int] = {
    1: 3, 2: 3, 3: 3, 5: 3, 8: 3, 9: 3,
    4: 2, 6: 2, 7: 2,
    10: 6,
    11: 5,
    13: 1,
    14: 2, 15: 2,
    17: 2, 18: 2,
}
_DIRECTION_AT: Mapping[int, int] = {1: 2, 2: 2, 3: 2, 5: 2, 8: 2, 9: 2, 11: 3}

# Novelty and geometry effects carry a sub-type at position 1 that decides
# whether a speed or direction byte exists at all.
_NOVELTY_SPEED_AT: Mapping[int, int] = {3: 2, 5: 2}
_NOVELTY_DIRECTION_AT: Mapping[int, int] = {3: 3}
_GEOMETRY_DIRECTION_AT: Mapping[int, int] = {3: 4}

DIRECTION_NAMES: Mapping[int, str] = {
    1: "Up",
    2: "Down",
    3: "Clockwise",
    4: "Anticlockwise",
    5: "Upper left",
    6: "Lower left",
    7: "Upper right",
    8: "Lower right",
    9: "Expand vertically",
    10: "Gather vertically",
    13: "Gather horizontally",
    14: "Expand horizontally",
    15: "Anticlockwise upward",
    16: "Anticlockwise downward",
    23: "Clockwise upward",
    24: "Clockwise downward",
}


def direction_name(direction: int) -> str:
    """Display name of a direction id; unnamed ids keep their number."""
    return DIRECTION_NAMES.get(direction, f"Direction {direction}")


@dataclass(frozen=True)
class SceneAdjust:
    """The speed levels and directions one scene offers."""

    speeds: tuple[tuple[int, ...], ...] = ()
    """One row per level; each row holds one raw speed byte per layer."""

    directions: tuple[int, ...] = ()
    direction_layers: tuple[int, ...] | None = None
    """Layers a direction applies to. None means every layer."""

    default_speed: int | None = None
    default_direction: int | None = None
    """Indexes into ``speeds`` and ``directions``."""


def _byte_list(value: Any) -> tuple[int, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(item for item in value if isinstance(item, int) and not isinstance(item, bool) and 0 <= item <= 0xFF)


def _default_index(value: Any, length: int) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < length:
        return value
    return None


def parse_adjust(config: Any, blob: bytes) -> SceneAdjust | None:
    """Read a scene's ``speedInfo.config`` object.

    Args:
        config: The decoded JSON value. Only the object form used by H70CX
            blobs is understood; the list form used by RGBIC blobs is not.
        blob: The scene blob the config belongs to.

    Returns:
        The offered adjustments, or None when the scene offers none this
        module can apply.
    """
    if not isinstance(config, Mapping) or blob[:1] != bytes([H70CX_LEAD]):
        return None

    rows: list[tuple[int, ...]] = []
    layered = config.get("multiLayerSpeeds")
    if isinstance(layered, Sequence) and not isinstance(layered, (str, bytes)):
        rows = [row for row in (_byte_list(item) for item in layered) if row]
    if not rows:
        rows = [(speed,) for speed in _byte_list(config.get("speedRange"))]

    directions = _byte_list(config.get("directions"))
    layers = config.get("multiLayersDirectionRange")
    direction_layers = _byte_list(layers) if isinstance(layers, Sequence) and layers else None

    if not rows and not directions:
        return None
    return SceneAdjust(
        speeds=tuple(rows),
        directions=directions,
        direction_layers=direction_layers,
        default_speed=_default_index(config.get("defSpeed"), len(rows)),
        default_direction=_default_index(config.get("defDirIndex"), len(directions)),
    )


def _field_positions(record: bytes | bytearray, start: int, end: int) -> tuple[int | None, int | None]:
    """Absolute positions of the speed and direction bytes of one record."""
    if start >= end:
        raise FrameError("scene effect record is empty")
    effect = record[start]
    speed = _SPEED_AT.get(effect)
    direction = _DIRECTION_AT.get(effect)
    if effect in (_NOVELTY_EFFECT, _GEOMETRY_EFFECT) and start + 1 < end:
        sub_type = record[start + 1]
        if effect == _NOVELTY_EFFECT:
            speed = _NOVELTY_SPEED_AT.get(sub_type)
            direction = _NOVELTY_DIRECTION_AT.get(sub_type)
        else:
            direction = _GEOMETRY_DIRECTION_AT.get(sub_type)

    def _absolute(relative: int | None) -> int | None:
        if relative is None or start + relative >= end:
            return None
        return start + relative

    return _absolute(speed), _absolute(direction)


def _records(blob: bytes | bytearray) -> list[tuple[int, int]]:
    """``(start, end)`` of every effect record in an H70CX blob."""
    if len(blob) < 4 or blob[0] != H70CX_LEAD:
        raise FrameError("not an H70CX scene blob")
    if blob[1] | (blob[2] << 8) != len(blob) - 3:
        raise FrameError("H70CX scene blob length field does not match its size")
    if blob[3] != MULTI_LAYER_EFFECT:
        return [(3, len(blob))]

    if len(blob) < 7:
        raise FrameError("H70CX multi-layer blob is truncated")
    records: list[tuple[int, int]] = []
    position = 7
    for _ in range(blob[4]):
        if position + 2 > len(blob):
            raise FrameError("H70CX layer header runs past the blob")
        length = blob[position] | (blob[position + 1] << 8)
        start = position + 2
        if length == 0 or start + length > len(blob):
            raise FrameError("H70CX layer runs past the blob")
        records.append((start, start + length))
        position = start + length
    return records


def adjust_blob(
    blob: bytes,
    adjust: SceneAdjust,
    *,
    speed: int | None = None,
    direction: int | None = None,
) -> bytes:
    """Return ``blob`` with a speed level and/or a direction written in.

    A record with no byte for the requested field is left alone.

    Args:
        blob: An H70CX scene blob.
        adjust: The scene's offered adjustments.
        speed: Index into ``adjust.speeds``, or None to keep the blob's speed.
        direction: Index into ``adjust.directions``, or None to keep it.

    Raises:
        FrameError: The blob is malformed or an index is out of range.
    """
    out = bytearray(blob)
    records = _records(out)

    if speed is not None:
        if not 0 <= speed < len(adjust.speeds):
            raise FrameError(f"scene has no speed level {speed}")
        for (start, end), value in zip(records, adjust.speeds[speed]):
            position, _ = _field_positions(out, start, end)
            if position is not None:
                out[position] = value

    if direction is not None:
        if not 0 <= direction < len(adjust.directions):
            raise FrameError(f"scene has no direction {direction}")
        layers = range(len(records)) if adjust.direction_layers is None else adjust.direction_layers
        for layer in layers:
            if layer >= len(records):
                continue
            start, end = records[layer]
            _, position = _field_positions(out, start, end)
            if position is not None:
                out[position] = adjust.directions[direction]

    return bytes(out)


def terminated_chunks(data: bytes, marker: int) -> list[bytes]:
    """``0xA3`` packets for ``data``, always ending in a terminator packet."""
    if len(data) > FIRST_CHUNK_DATA_BYTES:
        return chunk_effect(data, marker=marker)
    head = bytes([PRO_TYPE_MULTIPACKET, 0x00, DEFAULT_HEADER_LEAD, 0x02, marker & 0xFF]) + data
    return [build_frame(head), build_frame(bytes([PRO_TYPE_MULTIPACKET, SEQUENCE_TERMINATOR]))]


def scene_frames(blob: bytes, code: int) -> list[bytes]:
    """The frames that upload and start one catalogue scene.

    Args:
        blob: The decoded ``scenceParam``; empty for a firmware preset.
        code: The catalogue ``sceneCode``.

    Raises:
        FrameError: The code does not fit two bytes.
    """
    if not 0 <= code <= 0xFFFF:
        raise FrameError(f"scene code {code} does not fit two bytes")
    commit = commit_frame(sub_mode=SCENE_SUB_MODE, index=code)
    if not blob:
        return [commit]
    if blob[0] == H70CX_LEAD:
        return [*terminated_chunks(blob[1:], H70CX_MARKER), commit]
    return [*terminated_chunks(blob, RGBIC_MARKER), commit]
