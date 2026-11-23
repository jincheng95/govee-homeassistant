"""The account's stored form of an H70CX DIY effect (fork).

The account keeps a DIY as ``effectStr``, the base64 of a share byte and the
bytes the lamp takes for it, beside the tags the app files it under. The lamp
holds none of it: a saved DIY is uploaded whole each time it is applied, then
committed under its ``diyCode``.

| Kind | Decoded ``effectStr`` | ``effectCode`` |
|---|---|---|
| composed or layered | ``49`` or ``50``, ``len16 LE``, record | 714, 744 or 885 |
| basic | ``00``, record | the effect's own code |
| combo | ``00``, record | 0, then each member's code |
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from .errors import FrameError
from .h70cx import EFFECT_CODE
from .h70cx_basic import COMBO, EFFECTS
from .scenes import MULTI_LAYER_EFFECT

GOODS_TYPES: Final[Mapping[str, int]] = {"H80C5": 260}
"""App product-family number per SKU that can save a DIY."""

MIN_DIY_CODE: Final = 1
MAX_DIY_CODE: Final = 240
"""The range the app draws a new DIY's commit code from."""

MODE_BASIC: Final = 0
MODE_MIX: Final = 1

_SHARE_BASIC: Final = 0x00
_SHARE_COMPOSED: Final = 0x49
_SHARE_COMPOSED_NEW: Final = 0x50

_SPIRAL_CODE: Final = 744
_NEW_CODE: Final = 885
_SPIRAL_EFFECT: Final = 9
_NEW_EFFECTS: Final = frozenset({14, 15, 16, 17, 18})

# (effect, sub) -> effectCode of a basic effect.
_BASIC_CODES: Final[Mapping[tuple[int, int], int]] = {
    (8, 9): 19,
    (8, 10): 20,
    (9, 9): 21,
    (9, 10): 22,
    (0, 2): 3,
    (0, 0): 1,
    (0, 1): 2,
    (3, 3): 10,
    (3, 4): 11,
    (3, 5): 12,
    (2, 2): 9,
}

# Composer tile per effect name. A name with no tile of its own takes the first
# tile that shares its effect id. The app shows the tile's preview for the DIY.
GIF_IDS: Final[Mapping[str, int]] = {
    "stripes_1": 1,
    "stripes_2": 2,
    "bounce_1": 3,
    "bounce_2": 4,
    "bounce_3": 5,
    "whirlpool": 6,
    "diffuse_1": 7,
    "diffuse_2": 8,
    "spin_1": 9,
    "spin_2": 10,
    "spiral": 11,
    "flow_1": 12,
    "flow_2": 13,
    "flow_3": 14,
    "flow_4": 15,
    "rainbow_1": 16,
    "rainbow_2": 17,
    "fade_1": 18,
    "fade_2": 19,
    "fade_3": 20,
    "marquee_1": 21,
    "marquee_2": 22,
    "marquee_3": 23,
    "breathe": 24,
    "ribbon_1": 29,
    "ribbon_2": 30,
    "mountain_peak": 31,
    "candy_crush": 32,
    "smudge": 33,
    "flexing": 34,
    "spin_3": 35,
    "flow": 13,
    "layered": 1,
    "rainbow": 17,
    "flame": 3,
    "vortex": 6,
    "diffuse": 7,
    "concentric": 8,
    "rotate": 9,
    "wave": 29,
    "blend": 33,
}


@dataclass(frozen=True)
class SavedEffect:
    """One effect in the form the account stores."""

    effect_str: str
    effect_code: tuple[int, ...]
    gif_id: int
    mode_type: int


def _encode(share: int, payload: bytes) -> str:
    return base64.b64encode(bytes([share]) + payload).decode("ascii")


def _composed_code(effect_ids: Sequence[int]) -> int:
    if any(effect in _NEW_EFFECTS for effect in effect_ids):
        return _NEW_CODE
    if _SPIRAL_EFFECT in effect_ids:
        return _SPIRAL_CODE
    return EFFECT_CODE


def _layer_effect_ids(record: bytes) -> list[int]:
    """The effect id of each layer of a layered record."""
    ids: list[int] = []
    at = 4
    for _layer in range(record[1]):
        length = record[at] | record[at + 1] << 8
        ids.append(record[at + 2])
        at += 2 + length
    return ids


def composed(record: bytes, name: str) -> SavedEffect:
    """A composed or layered record, as :func:`.h70cx.effect_record` or ``layered_record`` builds it.

    Args:
        record: The record, without its ``len16``.
        name: The effect name, or the first layer's, for the preview tile.
    """
    effect_ids = _layer_effect_ids(record) if record[0] == MULTI_LAYER_EFFECT else [record[0]]
    code = _composed_code(effect_ids)
    share = _SHARE_COMPOSED if code == EFFECT_CODE else _SHARE_COMPOSED_NEW
    payload = bytes([len(record) & 0xFF, len(record) >> 8]) + record
    return SavedEffect(_encode(share, payload), (code,), GIF_IDS.get(name, 0), MODE_BASIC)


def basic(record: bytes, name: str, combine: Sequence[str] | None = None) -> SavedEffect:
    """A basic or combo record, as :func:`.h70cx_basic.effect_record` builds it.

    Args:
        record: The record.
        name: The effect name, :data:`.h70cx_basic.COMBO` for a combo.
        combine: The combo's member names, in order.

    Raises:
        FrameError: The record's effect and sub-effect have no known code.
    """
    if name == COMBO:
        members = [EFFECTS[member] for member in combine or ()]
        codes = (0, *(_BASIC_CODES[(member.effect, member.sub)] for member in members))
        return SavedEffect(_encode(_SHARE_BASIC, record), codes, 0, MODE_MIX)
    try:
        code = _BASIC_CODES[(record[0], record[1])]
    except KeyError:
        raise FrameError(f"{name} cannot be saved with sub-effect {record[1]}") from None
    return SavedEffect(_encode(_SHARE_BASIC, record), (code,), GIF_IDS.get(name, 0), MODE_BASIC)


def decode(effect_str: str) -> tuple[int, bytes]:
    """The share byte and the lamp bytes of a stored ``effectStr``.

    Raises:
        FrameError: Not base64, or empty.
    """
    try:
        raw = base64.b64decode(effect_str, validate=True)
    except (binascii.Error, ValueError) as err:
        raise FrameError("the stored effect is not base64") from err
    if not raw:
        raise FrameError("the stored effect is empty")
    return raw[0], raw[1:]


LIST_EFFECT_CODES: Final[tuple[int, ...]] = (
    0,
    EFFECT_CODE,
    _SPIRAL_CODE,
    _NEW_CODE,
    *sorted(set(_BASIC_CODES.values())),
    732,
    40,
    42,
    45,
)
"""Every ``effectCode`` of the family, the app's paint and template ones included, for the account's list filter."""

LIST_MIX_CODES: Final[tuple[int, ...]] = (19, 21, 3, 12, 9)
"""The codes of the effects a combo may hold, for the same filter."""
