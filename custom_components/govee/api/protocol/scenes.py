"""Catalogue scene upload for the H70CX effect family.

A named scene is a blob the app pushes as ``0xA3`` multipacket frames and then
commits with ``33 05 04 <codeLo> <codeHi>``. Three blob kinds exist::

    empty          firmware preset      commit only
    49 ...         H70CX effect         marker 0x58, lead byte dropped
    anything else  RGBIC effect         marker 0x02, blob sent whole

An H70CX blob is ``49 | len16 | effect | params | nColours | RGB...``. Effect
12 stacks layers, each ``len16 | effect | params | nColours | RGB...``. A
scene's speed and direction are single bytes inside those records, so the
adjustments rewrite the blob and the scene is uploaded again. The length of
``params`` depends on the effect, so a palette is rewritten only for effects
whose parameter length is known.

An RGBIC blob is ``count``, then per effect ``len | record``::

    head(5) | nBright | nBright x brightness(6) | colour | inAreaMove(3) | areaMove(4)
    colour = type | speed | x | nColours | RGB...

Its speed is spread over up to four bytes per effect: brightness byte 3,
colour byte 1, in-area-move byte 2 and area-move byte 2.
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
_DIRECTION_AT: Mapping[int, int] = {1: 2, 2: 2, 3: 2, 5: 2, 8: 2, 9: 2, 11: 3, 17: 1}

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
class RgbicPage:
    """The speed values of one effect of an RGBIC blob, one value per level."""

    page: int
    """0-based index of the effect inside the blob."""

    color: tuple[int, ...] = ()
    move_in: tuple[int, ...] = ()
    move_all: tuple[int, ...] = ()
    bright: tuple[tuple[int, ...], ...] = ()
    """One row per brightness record, in blob order."""

    @property
    def levels(self) -> int:
        return max(len(self.color), len(self.move_in), len(self.move_all), *(len(row) for row in self.bright), 0)


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

    pages: tuple[RgbicPage, ...] = ()
    """Speed values of an RGBIC blob, one entry per adjustable effect."""

    @property
    def speed_levels(self) -> int:
        """How many speed levels the scene offers."""
        if self.speeds:
            return len(self.speeds)
        return max((page.levels for page in self.pages), default=0)


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
        config: The decoded JSON value: an object for an H70CX blob, a list
            of per-effect entries for an RGBIC blob.
        blob: The scene blob the config belongs to.

    Returns:
        The offered adjustments, or None when the scene offers none this
        module can apply.
    """
    if blob[:1] != bytes([H70CX_LEAD]):
        return _parse_rgbic_adjust(config) if blob else None
    if not isinstance(config, Mapping):
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


def _parse_rgbic_adjust(config: Any) -> SceneAdjust | None:
    if not isinstance(config, Sequence) or isinstance(config, (str, bytes)):
        return None
    chosen: dict[int, Mapping[str, Any]] = {}
    for entry in config:
        if not isinstance(entry, Mapping):
            continue
        page = entry.get("page")
        if isinstance(page, int) and not isinstance(page, bool) and page >= 0:
            # Where a page repeats, the later entry replaces the earlier.
            chosen[page] = entry

    pages: list[RgbicPage] = []
    default: Any = None
    for page, entry in sorted(chosen.items()):
        rows = entry.get("bright")
        bright = (
            tuple(_byte_list(row.get("brightValue")) if isinstance(row, Mapping) else () for row in rows)
            if isinstance(rows, Sequence) and not isinstance(rows, (str, bytes))
            else ()
        )
        candidate = RgbicPage(
            page=page,
            color=_byte_list(entry.get("color")),
            move_in=_byte_list(entry.get("moveIn")),
            move_all=_byte_list(entry.get("moveAll")),
            bright=bright,
        )
        if candidate.levels:
            pages.append(candidate)
            if default is None:
                default = entry.get("defaultIndex")
    if not pages:
        return None
    adjust = SceneAdjust(pages=tuple(pages))
    return SceneAdjust(pages=adjust.pages, default_speed=_default_index(default, adjust.speed_levels))


def _rgbic_records(blob: bytes | bytearray) -> list[dict[str, Any]]:
    """Positions of the speed bytes of every effect in an RGBIC blob."""
    if not blob:
        raise FrameError("RGBIC scene blob is empty")
    records: list[dict[str, Any]] = []
    position = 1
    for _ in range(blob[0]):
        if position >= len(blob):
            raise FrameError("RGBIC effect header runs past the blob")
        start = position + 1
        end = start + blob[position]
        if end > len(blob) or start + 6 > end:
            raise FrameError("RGBIC effect runs past the blob")
        brights = blob[start + 5]
        color = start + 6 + 6 * brights
        if color + 4 > end:
            raise FrameError("RGBIC effect has no colour block")
        move_in = color + 4 + 3 * blob[color + 3]
        move_all = move_in + 3
        if move_all + 4 != end:
            raise FrameError("RGBIC effect length does not match its fields")
        records.append(
            {
                "bright": [start + 6 + 6 * index + 3 for index in range(brights)],
                "color": color + 1,
                "move_in": move_in + 2,
                "move_all": move_all + 2,
            }
        )
        position = end
    if position != len(blob):
        raise FrameError("RGBIC scene blob has trailing bytes")
    return records


def _adjust_rgbic(blob: bytes, adjust: SceneAdjust, speed: int | None) -> bytes:
    if speed is None:
        return blob
    if not 0 <= speed < adjust.speed_levels:
        raise FrameError(f"scene has no speed level {speed}")
    out = bytearray(blob)
    records = _rgbic_records(out)
    for page in adjust.pages:
        if page.page >= len(records):
            continue
        record = records[page.page]
        # A value list shorter than the level leaves its byte alone.
        for values, position in (
            (page.color, record["color"]),
            (page.move_in, record["move_in"]),
            (page.move_all, record["move_all"]),
            *zip(page.bright, record["bright"]),
        ):
            if speed < len(values):
                out[position] = values[speed]
    return bytes(out)


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
        blob: The scene blob.
        adjust: The scene's offered adjustments.
        speed: Index into ``adjust.speeds``, or None to keep the blob's speed.
        direction: Index into ``adjust.directions``, or None to keep it.

    Raises:
        FrameError: The blob is malformed or an index is out of range.
    """
    if blob[:1] != bytes([H70CX_LEAD]):
        if direction is not None:
            raise FrameError("an RGBIC scene has no direction")
        return _adjust_rgbic(blob, adjust, speed)

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


MAX_SCENE_COLOURS = 8

Colour = tuple[int, int, int]

# Parameter bytes between an effect id and its colour count.
_PARAM_LENGTHS: Mapping[int, int] = {
    1: 6, 2: 7, 3: 3, 4: 4, 5: 9, 6: 9, 7: 9, 8: 10, 9: 9,
    10: 6, 11: 5, 13: 4, 17: 2, 18: 3,
}
# The same for sub-typed effects, keyed by the sub-type byte at position 1.
_SUBTYPED_PARAM_LENGTHS: Mapping[int, Mapping[int, int]] = {
    14: {1: 8, 2: 5, 4: 3},
    _GEOMETRY_EFFECT: {1: 5, 3: 7},
    _NOVELTY_EFFECT: {3: 3, 4: 4, 5: 4},
}


def _colour_count_at(record: bytes | bytearray, start: int, end: int) -> int | None:
    """Absolute position of a record's colour count, or None when unknown.

    None also covers a record whose colour list does not end it exactly.
    """
    effect = record[start]
    subtyped = _SUBTYPED_PARAM_LENGTHS.get(effect)
    if subtyped is None:
        length = _PARAM_LENGTHS.get(effect)
    else:
        length = subtyped.get(record[start + 1]) if start + 1 < end else None
    if length is None:
        return None
    at = start + 1 + length
    if at >= end or at + 1 + 3 * record[at] != end:
        return None
    return at


def scene_palettes(blob: bytes) -> tuple[tuple[Colour, ...] | None, ...]:
    """The colour list of every layer of an H70CX blob.

    Returns:
        One entry per layer, None where the colours cannot be located. Empty
        for a blob of another kind or a malformed one.
    """
    if blob[:1] != bytes([H70CX_LEAD]):
        return ()
    try:
        records = _records(blob)
    except FrameError:
        return ()
    found: list[tuple[Colour, ...] | None] = []
    for start, end in records:
        at = _colour_count_at(blob, start, end)
        if at is None:
            found.append(None)
            continue
        found.append(tuple((blob[i], blob[i + 1], blob[i + 2]) for i in range(at + 1, end, 3)))
    return tuple(found)


def palette_blob(blob: bytes, palettes: Mapping[int, Sequence[Colour]]) -> bytes:
    """Return an H70CX ``blob`` with the colour lists of some layers replaced.

    A replaced record changes length, so its layer ``len16`` and the outer
    ``len16`` are rewritten to match. No other byte changes.

    Args:
        blob: The scene blob.
        palettes: New colours by 0-based layer index, 1 to
            :data:`MAX_SCENE_COLOURS` each.

    Raises:
        FrameError: The blob is not H70CX or is malformed, a layer does not
            exist or has no locatable colours, or a palette is out of range.
    """
    if blob[:1] != bytes([H70CX_LEAD]):
        raise FrameError("only an H70CX scene blob has rewritable palettes")
    records = _records(blob)
    for layer, colours in palettes.items():
        if not 0 <= layer < len(records):
            raise FrameError(f"scene has no layer {layer}")
        if not 1 <= len(colours) <= MAX_SCENE_COLOURS:
            raise FrameError(f"a scene palette holds 1 to {MAX_SCENE_COLOURS} colours")
        if any(len(rgb) != 3 or not all(0 <= value <= 0xFF for value in rgb) for rgb in colours):
            raise FrameError("a scene colour is three bytes")

    layered = blob[3] == MULTI_LAYER_EFFECT
    out = bytearray(blob[:7] if layered else blob[:3])
    for layer, (start, end) in enumerate(records):
        record = bytes(blob[start:end])
        if layer in palettes:
            at = _colour_count_at(blob, start, end)
            if at is None:
                raise FrameError(f"the colours of layer {layer} cannot be located")
            colours = palettes[layer]
            record = blob[start:at] + bytes([len(colours)]) + bytes(value for rgb in colours for value in rgb)
        if layered:
            out += len(record).to_bytes(2, "little")
        out += record
    if layered:
        out += blob[records[-1][1] if records else 7 :]
    out[1:3] = (len(out) - 3).to_bytes(2, "little")
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
