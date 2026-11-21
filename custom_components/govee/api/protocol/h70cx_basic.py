"""Basic and combo DIY effects for the H70CX string-light family.

A basic effect is ``effect | sub | speed | 3n | RGB...`` for ``n`` colours. A
combo is ``ff 00 | speed | 3n | RGB... | 2k | (effect, sub)...`` for ``k``
member effects. Both upload as ``0xA3`` marker ``0x04`` with no length prefix
and commit as a DIY with :data:`.h70cx.EFFECT_CODE`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .errors import FrameError
from .h70cx import EFFECT_CODE
from .packets import DIY_COMMIT_SUB_MODE, commit_frame
from .scenes import terminated_chunks

MARKER = 0x04
MAX_COLORS = 8
MAX_SPEED = 100

COMBO = "combo"
COMBO_SPEED = 50
COMBO_MIN_SPEED = 1
MAX_COMBINED = 4

_COMBO_HEAD = bytes([0xFF, 0x00])


@dataclass(frozen=True)
class BasicEffect:
    """One basic effect and the limits the lamp's app enforces for it."""

    effect: int
    sub: int
    """Sub-effect id; for a two-way effect, the clockwise one."""
    min_colors: int
    speed: int
    """Default speed."""
    two_way: bool = False
    """Whether the sub-effect byte carries the direction."""


EFFECTS: Mapping[str, BasicEffect] = {
    "flow_1": BasicEffect(8, 9, 2, 85, two_way=True),
    "rainbow_1": BasicEffect(9, 9, 2, 85, two_way=True),
    "fade_1": BasicEffect(0, 2, 1, 50),
    "fade_2": BasicEffect(0, 0, 1, 50),
    "fade_3": BasicEffect(0, 1, 1, 50),
    "marquee_1": BasicEffect(3, 3, 2, 85),
    "marquee_2": BasicEffect(3, 4, 2, 85),
    "marquee_3": BasicEffect(3, 5, 2, 85),
    "breathe": BasicEffect(2, 2, 1, 50),
}

COMBINABLE: tuple[str, ...] = ("flow_1", "rainbow_1", "fade_1", "marquee_3", "breathe")
"""The effects a combo may hold. A combo member always plays clockwise."""

NAMES: tuple[str, ...] = (*EFFECTS, COMBO)

DIRECTION_SUBS: Mapping[str, int] = {"clockwise": 9, "anticlockwise": 10}
"""Sub-effect id per direction name, for two-way effects."""


def _palette(effect: str, colors: Sequence[Sequence[int]], min_colors: int) -> bytes:
    if not min_colors <= len(colors) <= MAX_COLORS:
        raise FrameError(f"{effect} takes {min_colors} to {MAX_COLORS} colours")
    palette = bytearray([3 * len(colors)])
    for color in colors:
        if len(color) != 3 or any(not isinstance(part, int) or not 0 <= part <= 0xFF for part in color):
            raise FrameError("a colour must be three values from 0 to 255")
        palette += bytes(color)
    return bytes(palette)


def _speed(speed: int | None, default: int, low: int) -> int:
    if speed is None:
        return default
    if not isinstance(speed, int) or not low <= speed <= MAX_SPEED:
        raise FrameError(f"speed must be from {low} to {MAX_SPEED}")
    return speed


def _combo_record(
    colors: Sequence[Sequence[int]], speed: int | None, direction: str | None, combine: Sequence[str] | None
) -> bytes:
    if direction is not None:
        raise FrameError(f"{COMBO} has no direction")
    if not combine:
        raise FrameError(f"{COMBO} needs 1 to {MAX_COMBINED} effects to combine")
    if len(combine) > MAX_COMBINED:
        raise FrameError(f"{COMBO} combines at most {MAX_COMBINED} effects")
    if len(set(combine)) != len(combine):
        raise FrameError(f"{COMBO} takes each effect once")
    pairs = bytearray([2 * len(combine)])
    for name in combine:
        if name not in COMBINABLE:
            raise FrameError(f"{COMBO} cannot hold {name!r} (have: {', '.join(COMBINABLE)})")
        pairs += bytes([EFFECTS[name].effect, EFFECTS[name].sub])
    speed_byte = _speed(speed, COMBO_SPEED, COMBO_MIN_SPEED)
    return _COMBO_HEAD + bytes([speed_byte]) + _palette(COMBO, colors, 1) + bytes(pairs)


def effect_record(
    effect: str,
    colors: Sequence[Sequence[int]],
    *,
    speed: int | None = None,
    direction: str | None = None,
    combine: Sequence[str] | None = None,
) -> bytes:
    """Encode one basic or combo effect record.

    Args:
        effect: A key of :data:`EFFECTS`, or :data:`COMBO`.
        colors: The palette, each colour three values from 0 to 255.
        speed: 0-100 (1-100 for a combo), or None for the effect's default.
        direction: A key of :data:`DIRECTION_SUBS` for a two-way effect, or
            None for clockwise.
        combine: The :data:`COMBINABLE` effects a combo plays, 1 to 4, each
            once. Only a combo takes it.

    Raises:
        FrameError: Unknown effect or direction, a bad colour or speed, too
            few or too many colours, a direction for an effect without one,
            or a bad ``combine`` list.
    """
    if effect == COMBO:
        return _combo_record(colors, speed, direction, combine)
    try:
        spec = EFFECTS[effect]
    except KeyError:
        raise FrameError(f"unknown effect {effect!r} (have: {', '.join(NAMES)})") from None
    if combine is not None:
        raise FrameError(f"only {COMBO} takes effects to combine")
    sub = spec.sub
    if direction is not None:
        if not spec.two_way:
            raise FrameError(f"{effect} has no direction")
        if direction not in DIRECTION_SUBS:
            raise FrameError(f"{effect} turns {' or '.join(DIRECTION_SUBS)}, not {direction!r}")
        sub = DIRECTION_SUBS[direction]
    speed_byte = _speed(speed, spec.speed, 0)
    return bytes([spec.effect, sub, speed_byte]) + _palette(effect, colors, spec.min_colors)


def effect_frames(
    effect: str,
    colors: Sequence[Sequence[int]],
    *,
    speed: int | None = None,
    direction: str | None = None,
    combine: Sequence[str] | None = None,
) -> list[bytes]:
    """The frames that upload and show one basic or combo effect. See :func:`effect_record`."""
    record = effect_record(effect, colors, speed=speed, direction=direction, combine=combine)
    return [
        *terminated_chunks(record, MARKER),
        commit_frame(sub_mode=DIY_COMMIT_SUB_MODE, index=EFFECT_CODE),
    ]
