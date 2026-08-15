"""DIY effect payload encoder — the ``0x50``-form blob the H60B0 takes.

A DIY effect is per-zone: each zone gets a record of ``<mode> <recLen> <body>``,
where the body is ``<modeParam> <speed> <colorCount> <RGB>xN`` plus, on zones
whose :class:`~.profiles.DiyZoneSpec` declares them, a direction and flow rate.
``recLen`` counts the bytes after it; ``recLen = 0`` is a zone switched off. Both
records are always present, in the profile's declared order, behind a constant
lead byte; the payload rides an ``0xA3`` multipacket upload and a commit frame
(:mod:`.packets`). Effect modes are per-zone ints with no shared enum — the
name-to-int tables live in the profile, and raw ints are accepted everywhere.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from .errors import DiyEffectError
from .frames import rgb_bytes
from .profiles import DiyEffectSpec, DiyZoneSpec

PAYLOAD_LEAD = 0x0D
"""Constant first byte of every DIY wire payload."""

DIRECTION_CW = 0x01
DIRECTION_CCW = 0x02
DIRECTION_REVERSE = 0x03
"""Ripple diffuser directions. ``REVERSE`` is a distinct code, not a toggle —
the LAN path is write-only, so the device cannot be asked to invert an unread
current state. Its physical effect is unverified (§4.1 of the protocol doc)."""

DIRECTIONS: Mapping[str, int] = {
    "cw": DIRECTION_CW,
    "ccw": DIRECTION_CCW,
    "reverse": DIRECTION_REVERSE,
}

MODE_NONE = 0
"""Mode int for a zone switched off within the DIY ("None" in the app)."""

MAX_DIY_COLORS = 16
"""Palette cap. The format is length-prefixed so larger would encode, but no
sample exceeds 7 colours and the app's own ceiling is unknown — refuse rather
than send an unproven count."""

_SPEED_RANGE = range(1, 101)


@dataclass(frozen=True)
class DiyZoneEffect:
    """One zone's slice of a DIY effect, as the user means it.

    ``mode`` is the raw per-zone effect int (resolve names with
    :func:`resolve_mode`). ``direction`` and ``flow_rate`` may only be set for
    zones whose spec declares them — passing either for the ring is an error,
    not a silent drop.
    """

    mode: int
    speed: int = 50
    colors: tuple[tuple[int, int, int], ...] = field(default_factory=tuple)
    direction: int | None = None
    flow_rate: int | None = None
    mode_param: int = 0


def resolve_mode(spec: DiyZoneSpec, value: int | str) -> int:
    """Turn a mode name from the zone's bound table (or a raw int) into the int.

    Raw ints pass through unvalidated beyond byte range: the tables are known
    incomplete for the ripple, which accepts ring-enum values on top of its
    own (§4.1).
    """
    if isinstance(value, str):
        try:
            return spec.modes[value.strip().lower()]
        except KeyError:
            raise DiyEffectError(
                f"zone {spec.zone_key!r} has no DIY mode {value!r} "
                f"(have: {sorted(spec.modes)}; raw ints are also accepted)"
            ) from None
    if not 0 <= value <= 0xFF:
        raise DiyEffectError(f"DIY mode int {value} out of byte range")
    return value


def encode_record(spec: DiyZoneSpec, effect: DiyZoneEffect | None) -> bytes:
    """Encode one zone record. ``None`` (or mode 0) is the empty "off" record."""
    if effect is None or effect.mode == MODE_NONE:
        return bytes([MODE_NONE, 0])

    if not 0 < effect.mode <= 0xFF:
        raise DiyEffectError(
            f"zone {spec.zone_key!r}: DIY mode int {effect.mode} out of byte range"
        )
    if effect.speed not in _SPEED_RANGE:
        raise DiyEffectError(
            f"zone {spec.zone_key!r}: effect speed {effect.speed} outside 1-100"
        )
    if not effect.colors:
        raise DiyEffectError(
            f"zone {spec.zone_key!r}: a DIY effect needs at least one palette colour"
        )
    if len(effect.colors) > MAX_DIY_COLORS:
        raise DiyEffectError(
            f"zone {spec.zone_key!r}: {len(effect.colors)} palette colours, cap is {MAX_DIY_COLORS}"
        )
    if not 0 <= effect.mode_param <= 0xFF:
        raise DiyEffectError(
            f"zone {spec.zone_key!r}: mode_param {effect.mode_param} out of byte range"
        )

    body = bytearray([effect.mode_param, effect.speed, len(effect.colors)])
    for rgb in effect.colors:
        body += rgb_bytes(rgb)

    if spec.has_direction:
        direction = DIRECTION_CW if effect.direction is None else effect.direction
        if direction not in DIRECTIONS.values():
            raise DiyEffectError(
                f"zone {spec.zone_key!r}: direction {direction} not one of "
                f"{sorted(DIRECTIONS.values())} (cw/ccw/reverse)"
            )
        body.append(direction)
    elif effect.direction is not None:
        raise DiyEffectError(f"zone {spec.zone_key!r} carries no direction field")

    if spec.has_flow_rate:
        flow = 50 if effect.flow_rate is None else effect.flow_rate
        if flow not in _SPEED_RANGE:
            raise DiyEffectError(
                f"zone {spec.zone_key!r}: flow rate {flow} outside 1-100"
            )
        body.append(flow)
    elif effect.flow_rate is not None:
        raise DiyEffectError(f"zone {spec.zone_key!r} carries no flow-rate field")

    return bytes([effect.mode, len(body)]) + bytes(body)


def encode_payload(
    diy: DiyEffectSpec, effects: Mapping[str, DiyZoneEffect | None]
) -> bytes:
    """Encode the full wire payload: lead byte + every zone record in order.

    ``effects`` maps zone key → effect; a zone absent from the mapping gets
    the empty "off" record (both records are always present on the wire).
    Unknown keys are an error — a typo would silently switch that zone off.
    """
    known = {spec.zone_key for spec in diy.zones}
    strays = set(effects) - known
    if strays:
        raise DiyEffectError(
            f"unknown DIY zone keys {sorted(strays)} (have: {sorted(known)})"
        )

    payload = bytearray([PAYLOAD_LEAD])
    for spec in diy.zones:
        payload += encode_record(spec, effects.get(spec.zone_key))
    return bytes(payload)


def all_off(effects: Iterable[DiyZoneEffect | None]) -> bool:
    """True when every zone is off — a payload not worth uploading."""
    return all(effect is None or effect.mode == MODE_NONE for effect in effects)
