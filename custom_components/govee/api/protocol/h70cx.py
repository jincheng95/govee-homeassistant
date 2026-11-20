"""Composed effects for the H70CX string-light family.

One effect is ``effect | params | nColours | RGB...``. It uploads like a scene
blob (``len16`` in front, ``0xA3`` marker ``0x58``) and is committed as a DIY:
``33 05 0a ca 02``.

Each effect's parameter block is taken whole from a stock scene that uses the
effect, so every byte a caller does not set holds a value the lamp is known to
accept. Only the speed and direction bytes are rewritten.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .errors import FrameError
from .packets import DIY_COMMIT_SUB_MODE, commit_frame
from .scenes import DIRECTION_NAMES, H70CX_MARKER, _field_positions, terminated_chunks

EFFECT_CODE = 714
MAX_COLORS = 8
MAX_SPEED = 100


@dataclass(frozen=True)
class EffectTemplate:
    """One composable effect."""

    effect: int
    params: bytes
    min_colors: int = 1
    """Fewest colours any stock scene gives this effect."""


TEMPLATES: Mapping[str, EffectTemplate] = {
    "flow": EffectTemplate(1, bytes([0x03, 0x09, 0x62, 0x01, 0x01, 0x48]), 2),
    "layered": EffectTemplate(2, bytes([0x0A, 0x03, 0x5A, 0x00, 0x01, 0x3C, 0x01]), 2),
    "rainbow": EffectTemplate(3, bytes([0x04, 0x05, 0x5C]), 2),
    "flame": EffectTemplate(4, bytes([0x0A, 0x5E, 0x01, 0x00]), 2),
    "vortex": EffectTemplate(5, bytes([0x03, 0x0E, 0x5A, 0x01, 0x04, 0x00, 0x00, 0x00, 0x00]), 2),
    "diffuse": EffectTemplate(6, bytes([0x05, 0x5C, 0x00, 0x24, 0x05, 0x00, 0x00, 0x00, 0x00]), 2),
    "concentric": EffectTemplate(7, bytes([0x03, 0x60, 0x01, 0x32, 0x05, 0x00, 0x00, 0x00, 0x00]), 2),
    "rotate": EffectTemplate(8, bytes([0x04, 0x03, 0x5D, 0x01, 0x11, 0x04, 0x00, 0x00, 0x00, 0x00]), 2),
    "spiral": EffectTemplate(9, bytes([0x05, 0x0F, 0x5D, 0x00, 0x01, 0x02, 0x00, 0x64, 0x00]), 2),
    "starlight": EffectTemplate(10, bytes([0x1E, 0x00, 0x01, 0x00, 0x00, 0x1E]), 1),
    "wave": EffectTemplate(14, bytes([0x01, 0x32, 0x02, 0x08, 0x01, 0x0D, 0x02, 0x01]), 1),
    "blend": EffectTemplate(17, bytes([0x02, 0x32]), 2),
}

DIRECTION_IDS: Mapping[str, int] = {name.lower(): direction for direction, name in DIRECTION_NAMES.items()}


def has_direction(effect: str) -> bool:
    """Whether ``effect`` carries a direction byte."""
    template = _template(effect)
    record = bytes([template.effect]) + template.params
    return _field_positions(record, 0, len(record))[1] is not None


def _template(effect: str) -> EffectTemplate:
    try:
        return TEMPLATES[effect]
    except KeyError:
        raise FrameError(f"unknown effect {effect!r} (have: {', '.join(TEMPLATES)})") from None


def effect_record(
    effect: str,
    colors: Sequence[Sequence[int]],
    *,
    speed: int | None = None,
    direction: str | None = None,
) -> bytes:
    """Encode one effect record.

    Args:
        effect: A key of :data:`TEMPLATES`.
        colors: The palette, each colour three values from 0 to 255.
        speed: 0-100, or None for the stock scene's speed.
        direction: A lower-case direction name, or None for the stock one.

    Raises:
        FrameError: Unknown effect or direction, a bad colour or speed, too
            few or too many colours, or a direction for an effect without one.
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
        if direction_at is None:
            raise FrameError(f"{effect} has no direction")
        record[direction_at] = DIRECTION_IDS[direction]
    return bytes(record) + bytes([len(colors)]) + bytes(palette)


def effect_frames(
    effect: str,
    colors: Sequence[Sequence[int]],
    *,
    speed: int | None = None,
    direction: str | None = None,
) -> list[bytes]:
    """The frames that upload and show one composed effect. See :func:`effect_record`."""
    record = effect_record(effect, colors, speed=speed, direction=direction)
    payload = bytes([len(record) & 0xFF, len(record) >> 8]) + record
    return [
        *terminated_chunks(payload, H70CX_MARKER),
        commit_frame(sub_mode=DIY_COMMIT_SUB_MODE, index=EFFECT_CODE),
    ]
