"""Frame encoders, named by the profile table.

Every encoder is a pure function of ``(constants, **intent)``, where
``constants`` is the per-capability constant block from :mod:`.profiles`. Nothing
here branches on SKU: a new SKU is a table entry pointing at an existing encoder,
or at a new function added here and named by the table. Byte positions and byte
constants are data; anything algorithmic is code.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

from .errors import UnknownEncodingError
from .frames import (
    PRO_TYPE_QUERY,
    PRO_TYPE_WRITE,
    build_frame,
    clamp_percent,
    kelvin_bytes,
    rgb_bytes,
    write_at,
)

SUB_MODE_COMMAND_TYPE = 0x05
"""commandType for every sub-mode frame (mode select, zone/segment operate)."""

ZONE_POWER_COMMAND_TYPE = 0x30
WHOLE_POWER_COMMAND_TYPE = 0x01
WHOLE_BRIGHTNESS_COMMAND_TYPE = 0x04
BAR_POWER_MASK_COMMAND_TYPE = 0x33

Encoder = Callable[..., bytes]


def constant(constants: Mapping[str, Any], key: str) -> int:
    """Read a required constant, refusing to guess when the table says UNKNOWN."""
    try:
        value = constants[key]
    except KeyError as err:
        raise UnknownEncodingError(f"profile constant {key!r} is missing") from err
    if not isinstance(value, int) or isinstance(value, bool):
        raise UnknownEncodingError(f"profile constant {key!r} is UNKNOWN for this SKU — refusing to guess the bytes")
    return value


# whole-device


def whole_power(constants: Mapping[str, Any], *, on: bool) -> bytes:
    """``33 01 <00|01>``."""
    del constants
    return build_frame([PRO_TYPE_WRITE, WHOLE_POWER_COMMAND_TYPE, 1 if on else 0])


def whole_brightness(constants: Mapping[str, Any], *, percent: int) -> bytes:
    """``33 04 <0-100>``."""
    del constants
    return build_frame([PRO_TYPE_WRITE, WHOLE_BRIGHTNESS_COMMAND_TYPE, clamp_percent(percent)])


def zone_power(constants: Mapping[str, Any], *, zone_byte: int, on: bool) -> bytes:
    """``33 30 <zone> <00|01>``."""
    del constants
    return build_frame([PRO_TYPE_WRITE, ZONE_POWER_COMMAND_TYPE, zone_byte, 1 if on else 0])


def bar_power_mask(constants: Mapping[str, Any], *, mask: int) -> bytes:
    """``33 33 <mask>`` — H6046's two-bar power mask (01 = A, 10 = B, 11 = both)."""
    del constants
    return build_frame([PRO_TYPE_WRITE, BAR_POWER_MASK_COMMAND_TYPE, mask & 0xFF])


def query(constants: Mapping[str, Any], *, command_type: int) -> bytes:
    """``aa <commandType>``.

    Kept for completeness and golden-frame coverage only: raw ``ptReal``
    queries get NO reply over LAN (see :mod:`.client`).
    """
    del constants
    return build_frame([PRO_TYPE_QUERY, command_type & 0xFF])


def mode_select(
    constants: Mapping[str, Any],
    *,
    sub_mode: int,
    index: int = 0,
    marker: int | None = None,
) -> bytes:
    """``33 05 <submode> <idxLo> <idxHi> [marker]``.

    The sub-mode byte comes from the profile's ``modes`` map rather than from a
    capability constant block, because one device offers several of them.
    """
    del constants
    body = bytearray([PRO_TYPE_WRITE, SUB_MODE_COMMAND_TYPE, sub_mode, index & 0xFF, (index >> 8) & 0xFF])
    if marker is not None:
        body.append(marker & 0xFF)
    return build_frame(body)


# sub-mode operate, zone-addressed (H60B0 family, sub_mode 0x2c)


def zone_color(
    constants: Mapping[str, Any],
    *,
    zone_byte: int,
    rgb: tuple[int, int, int],
    kelvin: int = 0,
    mask: bytes | None = None,
) -> bytes:
    """``33 05 <sub> <zone> <attr> <R G B> <Khi Klo> [mask0 mask1]``."""
    body = bytearray(
        [
            PRO_TYPE_WRITE,
            SUB_MODE_COMMAND_TYPE,
            constant(constants, "sub_mode"),
            zone_byte,
            constant(constants, "attribute"),
        ]
    )
    body += rgb_bytes(rgb)
    body += kelvin_bytes(kelvin)
    if mask is not None:
        write_at(body, constant(constants, "mask_offset"), mask)
    return build_frame(body)


def zone_level(
    constants: Mapping[str, Any],
    *,
    zone_byte: int,
    level: int,
    mask: bytes | None = None,
) -> bytes:
    """``33 05 <sub> <zone> <attr> <level> [mask0 mask1]``.

    One shape, two meanings, distinguished purely by the attribute byte the
    table supplies: ``02`` = brightness, ``03`` on the ripple zone = flow rate.
    """
    body = bytearray(
        [
            PRO_TYPE_WRITE,
            SUB_MODE_COMMAND_TYPE,
            constant(constants, "sub_mode"),
            zone_byte,
            constant(constants, "attribute"),
            clamp_percent(level),
        ]
    )
    if mask is not None:
        write_at(body, constant(constants, "mask_offset"), mask)
    return build_frame(body)


def zone_kelvin(constants: Mapping[str, Any], *, zone_byte: int, kelvin: int) -> bytes:
    """``33 05 <sub> <zone> <attr> <Khi Klo>`` — the white-only downlight."""
    body = bytearray(
        [
            PRO_TYPE_WRITE,
            SUB_MODE_COMMAND_TYPE,
            constant(constants, "sub_mode"),
            zone_byte,
            constant(constants, "attribute"),
        ]
    )
    body += kelvin_bytes(kelvin)
    return build_frame(body)


# SubModeColorV2 (H6046 legacy pact_tvlightv3 stack, sub_mode 0x15)


def segment_color_v2(
    constants: Mapping[str, Any],
    *,
    rgb: tuple[int, int, int],
    mask: bytes,
    kelvin: int = 0,
    white_rgb: tuple[int, int, int] = (0, 0, 0),
) -> bytes:
    """``33 05 15 <attr> <R G B> <Khi Klo> <wR wG wB> <mask0> <mask1>``.

    No zone byte: this SKU addresses segments only. 17-byte body per
    ``SubModeColorV2``; 10 of the 16 mask bits are used on the H6046.
    """
    body = bytearray(
        [
            PRO_TYPE_WRITE,
            SUB_MODE_COMMAND_TYPE,
            constant(constants, "sub_mode"),
            constant(constants, "attribute"),
        ]
    )
    body += rgb_bytes(rgb)
    body += kelvin_bytes(kelvin)
    body += rgb_bytes(white_rgb)
    write_at(body, constant(constants, "mask_offset"), mask)
    return build_frame(body)


def segment_level_v2(constants: Mapping[str, Any], *, level: int, mask: bytes) -> bytes:
    """``33 05 15 <attr> <level> <mask0> <mask1>`` — UNVERIFIED shape.

    Two sources disagree: captured LAN traffic puts brightness at attribute
    ``02`` with the mask straight after the level, while the decompiled
    ``SubModeColorV2`` struct numbers brightness ``03`` inside the colour
    body. Profiles that cannot resolve that leave the constants UNKNOWN and
    this encoder refuses.
    """
    body = bytearray(
        [
            PRO_TYPE_WRITE,
            SUB_MODE_COMMAND_TYPE,
            constant(constants, "sub_mode"),
            constant(constants, "attribute"),
            clamp_percent(level),
        ]
    )
    write_at(body, constant(constants, "mask_offset"), mask)
    return build_frame(body)


ENCODERS: Mapping[str, Encoder] = {
    "whole_power": whole_power,
    "whole_brightness": whole_brightness,
    "zone_power": zone_power,
    "bar_power_mask": bar_power_mask,
    "query": query,
    "mode_select": mode_select,
    "zone_color": zone_color,
    "zone_level": zone_level,
    "zone_kelvin": zone_kelvin,
    "segment_color_v2": segment_color_v2,
    "segment_level_v2": segment_level_v2,
}


def encoder_names() -> Iterable[str]:
    """Every encoder a profile may name (used by the table's self-check)."""
    return ENCODERS.keys()
