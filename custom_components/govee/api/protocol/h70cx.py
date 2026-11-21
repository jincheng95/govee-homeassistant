"""Composed effects for the H70CX string-light family.

One effect is ``effect | params | nColours | RGB...``. It uploads like a scene
blob (``len16`` in front, ``0xA3`` marker ``0x58``) and is committed as a DIY:
``33 05 0a ca 02``.

A layered effect stacks two or three such records under effect id 12, each
framed by its own ``len16``, and uploads and commits the same way.

The generic names carry a parameter block from a stock scene. The numbered and
other app-named effects carry the block of the matching composer tile and only
take the directions, colour counts and options that tile offers. Every byte a
caller does not set keeps its template value.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .errors import FrameError
from .packets import DIY_COMMIT_SUB_MODE, commit_frame
from .paint import MESSAGE_FRAMES
from .scenes import DIRECTION_NAMES, H70CX_MARKER, MULTI_LAYER_EFFECT, _field_positions, terminated_chunks

EFFECT_CODE = 714
MAX_COLORS = 8
MAX_SPEED = 100

MIN_LAYERS = 2
MAX_LAYERS = 3
MAX_FRAMES = MESSAGE_FRAMES
"""Frames one upload may take, commit included, so it fits one ``ptReal`` message."""

# Header of a layered record after the layer count. The two bytes are fixed.
_LAYERED_MODE = 2
_LAYERED_FLAG = 0

SHAPES: Mapping[str, int] = {"round": 0, "square": 1, "rhomb": 2}
CENTERS: Mapping[str, int] = {
    "upper_left": 0,
    "left_center": 1,
    "bottom_left": 2,
    "top_center": 3,
    "center": 4,
    "bottom_center": 5,
    "upper_right": 6,
    "right_center": 7,
    "bottom_right": 8,
}

# Record positions of the shape and centre bytes of a shaped effect.
_SHAPE_AT = 4
_CENTER_AT = 5


def _directions(*ids: int) -> tuple[str, ...]:
    return tuple(DIRECTION_NAMES[direction].lower() for direction in ids)


@dataclass(frozen=True)
class EffectTemplate:
    """One composable effect."""

    effect: int
    params: bytes
    min_colors: int = 1
    directions: tuple[str, ...] | None = None
    """Direction names the effect takes: the tile's own plus any a stock scene
    uses with the same effect id. None takes any named direction."""
    shaped: bool = False
    """Whether the effect takes a shape and a centre point."""


_NONE: tuple[str, ...] = ()
_UP_DOWN = _directions(1, 2)
_STRIPES = _directions(1, 2, 3, 4, 5, 6, 7, 8)
_WHIRLPOOL = _directions(13, 14)
_SPIN = _directions(3, 4)
_SPIRAL = _directions(23, 15, 24, 16)
_FLOW = _directions(1, 2, 3, 4, 5, 9, 10)
_DIAGONAL = _directions(5, 6, 7, 8)

TEMPLATES: Mapping[str, EffectTemplate] = {
    "flow": EffectTemplate(1, bytes([0x03, 0x09, 0x62, 0x01, 0x01, 0x48]), 2),
    "layered": EffectTemplate(2, bytes([0x0A, 0x03, 0x5A, 0x00, 0x01, 0x3C, 0x01]), 2),
    "rainbow": EffectTemplate(3, bytes([0x04, 0x05, 0x5C]), 2),
    "flame": EffectTemplate(4, bytes([0x0A, 0x5E, 0x01, 0x00]), 2),
    "vortex": EffectTemplate(5, bytes([0x03, 0x0E, 0x5A, 0x01, 0x04, 0x00, 0x00, 0x00, 0x00]), 2),
    "diffuse": EffectTemplate(6, bytes([0x05, 0x5C, 0x00, 0x24, 0x05, 0x00, 0x00, 0x00, 0x00]), 2),
    "concentric": EffectTemplate(7, bytes([0x03, 0x60, 0x01, 0x32, 0x05, 0x00, 0x00, 0x00, 0x00]), 2),
    "rotate": EffectTemplate(8, bytes([0x04, 0x03, 0x5D, 0x01, 0x11, 0x04, 0x00, 0x00, 0x00, 0x00]), 2),
    "starlight": EffectTemplate(10, bytes([0x1E, 0x00, 0x01, 0x00, 0x00, 0x1E]), 1),
    "wave": EffectTemplate(14, bytes([0x01, 0x32, 0x02, 0x08, 0x01, 0x0D, 0x02, 0x01]), 1),
    "blend": EffectTemplate(17, bytes([0x02, 0x32]), 2, _UP_DOWN),
    "stripes_1": EffectTemplate(2, bytes([0x03, 0x05, 0x5E, 0x01, 0x01, 0x00, 0x01]), 2, _STRIPES),
    "stripes_2": EffectTemplate(2, bytes([0x09, 0x05, 0x5E, 0x00, 0x00, 0x00, 0x01]), 2, _STRIPES),
    "bounce_1": EffectTemplate(4, bytes([0x01, 0x5E, 0x01, 0x00]), 4, _NONE),
    "bounce_2": EffectTemplate(4, bytes([0x06, 0x5E, 0x01, 0x01]), 4, _NONE),
    "bounce_3": EffectTemplate(4, bytes([0x0A, 0x5E, 0x00, 0x00]), 4, _NONE),
    "whirlpool": EffectTemplate(5, bytes([0x05, 0x0D, 0x5E, 0x01, 0x05, 0x00, 0x00, 0x00, 0x00]), 4, _WHIRLPOOL),
    "diffuse_1": EffectTemplate(6, bytes([0x05, 0x5E, 0x00, 0x46, 0x05, 0x00, 0x00, 0x00, 0x00]), 4, _NONE),
    "diffuse_2": EffectTemplate(7, bytes([0x02, 0x5E, 0x01, 0x1E, 0x04, 0x00, 0x00, 0x00, 0x00]), 4, _NONE),
    "spin_1": EffectTemplate(8, bytes([0x08, 0x03, 0x5E, 0x00, 0x0A, 0x05, 0x00, 0x00, 0x00, 0x00]), 2, _SPIN),
    "spin_2": EffectTemplate(8, bytes([0x05, 0x03, 0x5E, 0x00, 0x01, 0x05, 0x00, 0x00, 0x00, 0x00]), 2, _SPIN),
    "spin_3": EffectTemplate(18, bytes([0x01, 0x32, 0x02]), 3, _NONE),
    "spiral": EffectTemplate(9, bytes([0x05, 0x17, 0x5A, 0x00, 0x01, 0x02, 0x00, 0x64, 0x00]), 1, _SPIRAL),
    "flow_2": EffectTemplate(1, bytes([0x03, 0x01, 0x64, 0x01, 0x01, 0x00]), 2, _FLOW),
    "flow_3": EffectTemplate(1, bytes([0x05, 0x01, 0x64, 0x01, 0x00, 0x00]), 2, _FLOW),
    "flow_4": EffectTemplate(1, bytes([0x08, 0x01, 0x64, 0x00, 0x00, 0x00]), 2, _FLOW),
    "rainbow_2": EffectTemplate(3, bytes([0x04, 0x05, 0x55]), 2, _DIAGONAL),
    "ribbon_1": EffectTemplate(14, bytes([0x01, 0x32, 0x02, 0x07, 0x01, 0x14, 0x02, 0x01]), 1, _NONE),
    "ribbon_2": EffectTemplate(14, bytes([0x01, 0x32, 0x02, 0x07, 0x01, 0x14, 0x02, 0x00]), 1, _NONE),
    "mountain_peak": EffectTemplate(14, bytes([0x02, 0x32, 0x0A, 0x01, 0x01]), 1, _NONE),
    "candy_crush": EffectTemplate(16, bytes([0x03, 0x32, 0x00]), 1, _NONE),
    "smudge": EffectTemplate(17, bytes([0x01, 0x32]), 1, _UP_DOWN),
    "flexing": EffectTemplate(15, bytes([0x01, 0x32, 0x01, 0x02, 0x04]), 1, _NONE, shaped=True),
}

DIRECTION_IDS: Mapping[str, int] = {name.lower(): direction for direction, name in DIRECTION_NAMES.items()}


def has_direction(effect: str) -> bool:
    """Whether ``effect`` takes a direction."""
    template = _template(effect)
    record = bytes([template.effect]) + template.params
    return _field_positions(record, 0, len(record))[1] is not None and template.directions != ()


def _template(effect: str) -> EffectTemplate:
    try:
        return TEMPLATES[effect]
    except KeyError:
        raise FrameError(f"unknown effect {effect!r} (have: {', '.join(TEMPLATES)})") from None


def _option(effect: str, template: EffectTemplate, kind: str, value: str, table: Mapping[str, int]) -> int:
    if not template.shaped:
        raise FrameError(f"{effect} has no {kind}")
    if value not in table:
        raise FrameError(f"unknown {kind} {value!r} (have: {', '.join(table)})")
    return table[value]


def effect_record(
    effect: str,
    colors: Sequence[Sequence[int]],
    *,
    speed: int | None = None,
    direction: str | None = None,
    shape: str | None = None,
    center: str | None = None,
) -> bytes:
    """Encode one effect record.

    Args:
        effect: A key of :data:`TEMPLATES`.
        colors: The palette, each colour three values from 0 to 255.
        speed: 0-100, or None for the template's speed.
        direction: A lower-case direction name the effect takes, or None for
            the template's direction.
        shape: A key of :data:`SHAPES`, for a shaped effect only.
        center: A key of :data:`CENTERS`, for a shaped effect only.

    Raises:
        FrameError: Unknown effect or direction, a bad colour or speed, too
            few or too many colours, a direction the effect does not take, or
            a shape or centre for an effect without one.
    """
    template = _template(effect)
    if not template.min_colors <= len(colors) <= MAX_COLORS:
        raise FrameError(f"{effect} takes {template.min_colors} to {MAX_COLORS} colours")
    palette = bytearray()
    for color in colors:
        if len(color) != 3 or any(not isinstance(part, int) or not 0 <= part <= 0xFF for part in color):
            raise FrameError("a colour must be three values from 0 to 255")
        palette += bytes(color)

    record = bytearray([template.effect]) + template.params
    speed_at, direction_at = _field_positions(record, 0, len(record))
    if speed is not None:
        if not isinstance(speed, int) or not 0 <= speed <= MAX_SPEED:
            raise FrameError(f"speed must be from 0 to {MAX_SPEED}")
        if speed_at is not None:
            record[speed_at] = speed
    if direction is not None:
        if direction not in DIRECTION_IDS:
            raise FrameError(f"unknown direction {direction!r}")
        if direction_at is None or template.directions == ():
            raise FrameError(f"{effect} has no direction")
        if template.directions is not None and direction not in template.directions:
            raise FrameError(f"{effect} takes only {', '.join(template.directions)}, not {direction!r}")
        record[direction_at] = DIRECTION_IDS[direction]
    if shape is not None:
        record[_SHAPE_AT] = _option(effect, template, "shape", shape, SHAPES)
    if center is not None:
        record[_CENTER_AT] = _option(effect, template, "center", center, CENTERS)
    return bytes(record) + bytes([len(colors)]) + bytes(palette)


def _upload(record: bytes) -> list[bytes]:
    payload = bytes([len(record) & 0xFF, len(record) >> 8]) + record
    frames = [
        *terminated_chunks(payload, H70CX_MARKER),
        commit_frame(sub_mode=DIY_COMMIT_SUB_MODE, index=EFFECT_CODE),
    ]
    if len(frames) > MAX_FRAMES:
        raise FrameError(f"the effect needs {len(frames)} frames, the limit is {MAX_FRAMES}")
    return frames


def effect_frames(
    effect: str,
    colors: Sequence[Sequence[int]],
    *,
    speed: int | None = None,
    direction: str | None = None,
    shape: str | None = None,
    center: str | None = None,
) -> list[bytes]:
    """The frames that upload and show one composed effect. See :func:`effect_record`."""
    return _upload(effect_record(effect, colors, speed=speed, direction=direction, shape=shape, center=center))


@dataclass(frozen=True)
class Layer:
    """One layer of a layered effect. The fields are those of :func:`effect_record`."""

    effect: str
    colors: Sequence[Sequence[int]]
    speed: int | None = None
    direction: str | None = None
    shape: str | None = None
    center: str | None = None


class LayerError(FrameError):
    """One layer of a layered effect could not be encoded."""

    def __init__(self, layer: int, reason: str) -> None:
        super().__init__(f"layer {layer}: {reason}")
        self.layer = layer
        """1-based position of the layer in the call."""
        self.reason = reason


def layered_record(layers: Sequence[Layer]) -> bytes:
    """Encode a layered effect record.

    The record is ``0c | nLayers | mode | flag`` followed by ``len16 | record``
    per layer, in the order given. Every layer is checked as
    :func:`effect_record` checks a single effect.

    Args:
        layers: :data:`MIN_LAYERS` to :data:`MAX_LAYERS` layers, each naming
            a key of :data:`TEMPLATES`.

    Raises:
        FrameError: Too few or too many layers.
        LayerError: A layer names an effect that cannot be a layer or fails
            the checks of :func:`effect_record`.
    """
    if not MIN_LAYERS <= len(layers) <= MAX_LAYERS:
        raise FrameError(f"a layered effect takes {MIN_LAYERS} to {MAX_LAYERS} layers, not {len(layers)}")
    out = bytearray([MULTI_LAYER_EFFECT, len(layers), _LAYERED_MODE, _LAYERED_FLAG])
    for index, layer in enumerate(layers, start=1):
        if layer.effect not in TEMPLATES:
            raise LayerError(index, f"{layer.effect} cannot be a layer (have: {', '.join(TEMPLATES)})")
        try:
            record = effect_record(
                layer.effect,
                layer.colors,
                speed=layer.speed,
                direction=layer.direction,
                shape=layer.shape,
                center=layer.center,
            )
        except FrameError as err:
            raise LayerError(index, str(err)) from err
        out += bytes([len(record) & 0xFF, len(record) >> 8]) + record
    return bytes(out)


def layered_frames(layers: Sequence[Layer]) -> list[bytes]:
    """The frames that upload and show one layered effect. See :func:`layered_record`.

    Raises:
        FrameError: As :func:`layered_record`, or the upload needs more than
            :data:`MAX_FRAMES` frames.
    """
    return _upload(layered_record(layers))
