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

from .errors import FrameError, UnknownEncodingError
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
GRADIENT_COMMAND_TYPE = 0xA3
POWER_OFF_MEMORY_COMMAND_TYPE = 0x41
POWER_OFF_MEMORY_BEHAVIOURS = range(3)
"""0 restores the last state, 1 comes back on, 2 stays off."""
SLEEP_TIMER_COMMAND_TYPE = 0x11
WAKE_UP_COMMAND_TYPE = 0x12
LIGHT_TIMER_COMMAND_TYPE = 0x23

TIMER_ENABLED = 0x80
TIMER_TURN_ON = 0x01
REPEAT_MARKER = 0x80
"""Always set in a repeat byte. Bits 0-6 are Monday to Sunday."""
WEEKDAY_COUNT = 7

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


def gradient(constants: Mapping[str, Any], *, on: bool) -> bytes:
    """``33 a3 <00|01>`` — blend segment paints across segment boundaries."""
    del constants
    return build_frame([PRO_TYPE_WRITE, GRADIENT_COMMAND_TYPE, 1 if on else 0])


def power_off_memory_behaviours(constants: Mapping[str, Any]) -> tuple[int, ...]:
    """The behaviours a SKU takes, in wire order.

    The optional ``behaviours`` constant is a bit mask with bit ``n`` set for
    behaviour ``n``. Without it every behaviour is taken.
    """
    if "behaviours" not in constants:
        return tuple(POWER_OFF_MEMORY_BEHAVIOURS)
    mask = constant(constants, "behaviours")
    return tuple(behaviour for behaviour in POWER_OFF_MEMORY_BEHAVIOURS if mask & (1 << behaviour))


def power_off_memory(constants: Mapping[str, Any], *, behaviour: int) -> bytes:
    """``33 41 <sub_type> <behaviour>`` — what the device does when power returns.

    ``behaviour`` must be one of :func:`power_off_memory_behaviours`.
    """
    allowed = power_off_memory_behaviours(constants)
    if behaviour not in allowed:
        raise FrameError(f"power-off memory behaviour {behaviour!r} is not one of {allowed}")
    return build_frame([PRO_TYPE_WRITE, POWER_OFF_MEMORY_COMMAND_TYPE, constant(constants, "sub_type"), behaviour])


# schedules


def repeat_byte(weekdays: Iterable[int]) -> int:
    """Encode weekdays (0 Monday to 6 Sunday) as a repeat byte. No days means run once."""
    value = REPEAT_MARKER
    for day in weekdays:
        if not 0 <= day < WEEKDAY_COUNT:
            raise FrameError(f"weekday {day!r} is not 0-6")
        value |= 1 << day
    return value


def repeat_days(value: int) -> tuple[int, ...]:
    """Decode a repeat byte into weekdays, 0 Monday to 6 Sunday."""
    return tuple(day for day in range(WEEKDAY_COUNT) if value & (1 << day))


def _time_bytes(hour: int, minute: int) -> list[int]:
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise FrameError(f"time {hour}:{minute} is not a time of day")
    return [hour, minute]


def _byte(name: str, value: int, low: int, high: int = 0xFF) -> int:
    if not low <= value <= high:
        raise FrameError(f"{name} {value!r} is not {low}-{high}")
    return value


def light_timer(
    constants: Mapping[str, Any],
    *,
    slot: int,
    enabled: bool,
    turn_on: bool,
    hour: int,
    minute: int,
    weekdays: Iterable[int] = (),
) -> bytes:
    """``33 23 <slot> <flags> <hour> <minute> <repeat>``.

    Flags bit 7 enables the timer and bit 0 makes it turn the light on rather
    than off. ``slot`` is 0-based and below the ``slots`` constant.
    """
    _byte("timer slot", slot, 0, constant(constants, "slots") - 1)
    flags = (TIMER_ENABLED if enabled else 0) | (TIMER_TURN_ON if turn_on else 0)
    return build_frame(
        [PRO_TYPE_WRITE, LIGHT_TIMER_COMMAND_TYPE, slot, flags, *_time_bytes(hour, minute), repeat_byte(weekdays)]
    )


def sleep_timer(constants: Mapping[str, Any], *, enabled: bool, start_brightness: int, minutes: int) -> bytes:
    """``33 11 <enable> <brightness> <minutes> <minutes> 00 00 <trailer hi> <trailer lo>``.

    The light dims from ``start_brightness`` percent to off over ``minutes``.
    The two-byte ``trailer`` constant is written as the table gives it.
    """
    trailer = constant(constants, "trailer")
    body = [
        PRO_TYPE_WRITE,
        SLEEP_TIMER_COMMAND_TYPE,
        1 if enabled else 0,
        _byte("sleep brightness", start_brightness, 1, 100),
        _byte("sleep minutes", minutes, 1),
        minutes,
        0,
        0,
    ]
    return build_frame(bytes(body) + trailer.to_bytes(2, "big"))


def wake_up(
    constants: Mapping[str, Any],
    *,
    enabled: bool,
    end_brightness: int,
    hour: int,
    minute: int,
    weekdays: Iterable[int],
    duration: int,
    kelvin: int,
    white_rgb: tuple[int, int, int],
) -> bytes:
    """``33 12 <enable> <brightness> <hour> <minute> <repeat> <minutes> 00 <R G B> <Khi Klo>``.

    The light brightens to ``end_brightness`` percent over ``duration``
    minutes, ending at ``hour``:``minute``, in ``kelvin`` with ``white_rgb``
    as its RGB approximation.
    """
    del constants
    body = bytearray(
        [
            PRO_TYPE_WRITE,
            WAKE_UP_COMMAND_TYPE,
            1 if enabled else 0,
            _byte("wake-up brightness", end_brightness, 1, 100),
            *_time_bytes(hour, minute),
            repeat_byte(weekdays),
            _byte("wake-up minutes", duration, 1),
            0,
        ]
    )
    body += rgb_bytes(white_rgb)
    body += kelvin_bytes(kelvin)
    return build_frame(body)


def query(constants: Mapping[str, Any], *, command_type: int) -> bytes:
    """``aa <commandType>``.

    Raw ``ptReal`` queries get NO reply over LAN (see :mod:`.client`). Over
    MQTT ``ptReal`` the device answers on its cloud topic.
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


def music_select(
    *,
    sub_mode: int,
    effect: int,
    sensitivity: int,
    soft: bool = False,
    manual_rgb: tuple[int, int, int] | None = None,
) -> bytes:
    """``33 05 <submode> <effect> <sensitivity> <soft> <manual> <R G B>``.

    Same opening bytes as :func:`mode_select`, but byte 4 is the sensitivity
    rather than a high index byte. ``manual_rgb`` sets the manual flag and the
    colour. With ``soft`` off and no ``manual_rgb`` the frame equals the
    five-byte form.
    """
    body = bytearray([PRO_TYPE_WRITE, SUB_MODE_COMMAND_TYPE, sub_mode, effect, sensitivity, 1 if soft else 0])
    if manual_rgb is not None:
        body.append(1)
        body += rgb_bytes(manual_rgb)
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
    "gradient": gradient,
    "power_off_memory": power_off_memory,
    "light_timer": light_timer,
    "sleep_timer": sleep_timer,
    "wake_up": wake_up,
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
