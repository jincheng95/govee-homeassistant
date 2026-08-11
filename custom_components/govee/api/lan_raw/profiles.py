"""Declarative per-SKU capability table.

The whole point of this module is that **adding a SKU is adding one table
entry**. Nothing in this package branches on ``sku ==``; the codec looks a
device up once and then works entirely off the profile.

What is data and what is code
-----------------------------
Data (here): zone bytes, segment counts, kelvin ranges, hardware constraints,
and the per-capability constant block — the sub-mode byte, the attribute byte,
and the segment-mask offset (which differs *by attribute*, so it is table
data, never an encoder literal). Code (:mod:`.encoders`): the frame layouts.
Each capability *names* an encoder; anything algorithmic (masks, checksums,
clamping) stays in code.

UNKNOWN
-------
The table can say "I do not know this byte": :data:`UNKNOWN` in a constant
block makes the codec raise :class:`~.errors.UnknownEncodingError` rather than
guess. Guessing is never safe — a wrong sub-mode byte is *silently ignored* by
the firmware, which is indistinguishable from a dead network, and the byte
differs per SKU (``0x2c`` on the H60B0, ``0x15`` on the H6046).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum, unique
from typing import Any, Final

from .encoders import ENCODERS
from .errors import LanRawError


class Unknown:
    """Sentinel for a byte constant we have not yet verified against hardware."""

    _instance: Unknown | None = None

    def __new__(cls) -> Unknown:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return "UNKNOWN"

    def __bool__(self) -> bool:
        return False


UNKNOWN: Final = Unknown()

ConstantValue = int | Unknown
Constants = Mapping[str, ConstantValue]


@unique
class Capability(str, Enum):
    """Everything the raw-LAN layer can express, whether or not a SKU has it."""

    POWER = "power"
    BRIGHTNESS = "brightness"
    ZONE_POWER = "zone_power"
    ZONE_COLOR = "zone_color"
    ZONE_BRIGHTNESS = "zone_brightness"
    ZONE_COLOR_TEMP = "zone_color_temp"
    ZONE_FLOW_RATE = "zone_flow_rate"
    SEGMENT_COLOR = "segment_color"
    SEGMENT_BRIGHTNESS = "segment_brightness"
    BAR_POWER_MASK = "bar_power_mask"
    MODE_SELECT = "mode_select"
    QUERY = "query"


@dataclass(frozen=True)
class CapabilitySpec:
    """How one capability is encoded for one SKU."""

    encoder: str
    """Key into :data:`~.encoders.ENCODERS`."""

    constants: Constants = field(default_factory=dict)
    """Byte constants handed to the encoder: sub_mode, attribute, mask_offset."""

    verified: bool = True
    """True when a frame of this exact shape has been seen to work on hardware."""

    note: str = ""

    @property
    def known(self) -> bool:
        """False when any constant is UNKNOWN, i.e. the codec must refuse."""
        return all(not isinstance(value, Unknown) for value in self.constants.values())


@dataclass(frozen=True)
class ZoneSpec:
    """One addressable zone. ``zone_byte`` is None for SKUs with no zone byte."""

    key: str
    name: str
    zone_byte: int | None
    segments: int = 0
    capabilities: frozenset[Capability] = frozenset()
    note: str = ""

    @property
    def segmented(self) -> bool:
        return self.segments > 0


@dataclass(frozen=True)
class KelvinRange:
    """Usable colour-temperature range, in kelvin."""

    minimum: int
    maximum: int
    verified: bool = True
    note: str = ""

    def clamp(self, kelvin: int) -> int:
        return max(self.minimum, min(self.maximum, int(kelvin)))


@dataclass(frozen=True)
class MaxSimultaneousZones:
    """Hardware limit: at most ``limit`` of ``zone_keys`` can be lit at once.

    Declarative only — nothing in this package enforces it yet. It is recorded
    here so a future zone-light platform reads the constraint out of the table
    instead of rediscovering it by eye.
    """

    limit: int
    zone_keys: tuple[str, ...]
    note: str = ""


@dataclass(frozen=True)
class DeviceProfile:
    """Everything the codec needs to talk to one SKU."""

    sku: str
    goods_type: int
    name: str
    kelvin: KelvinRange
    zones: tuple[ZoneSpec, ...] = ()
    capabilities: Mapping[Capability, CapabilitySpec] = field(default_factory=dict)
    modes: Mapping[str, ConstantValue] = field(default_factory=dict)
    constraints: tuple[MaxSimultaneousZones, ...] = ()
    note: str = ""

    def zone(self, key: str) -> ZoneSpec:
        for zone in self.zones:
            if zone.key == key:
                return zone
        raise LanRawError(f"{self.sku} has no zone {key!r} (have: {[z.key for z in self.zones]})")

    def supports(self, capability: Capability, *, zone: str | None = None) -> bool:
        if capability not in self.capabilities:
            return False
        if zone is None:
            return True
        return capability in self.zone(zone).capabilities


# ==========================================================================
# The table
# ==========================================================================

H60B0: Final = DeviceProfile(
    sku="H60B0",
    goods_type=301,
    name="Uplighter",
    # Manufacturer claims 9000 K; above ~6500 K the firmware drops the zone
    # entirely, so 6500 K is a hard cap (verified on hardware).
    kelvin=KelvinRange(2000, 6500, verified=True, note="above ~6500 K the zone drops out"),
    zones=(
        ZoneSpec(
            key="ripple",
            name="TOP",
            zone_byte=1,
            segments=0,
            capabilities=frozenset(
                {
                    Capability.ZONE_POWER,
                    Capability.ZONE_COLOR,
                    Capability.ZONE_BRIGHTNESS,
                    Capability.ZONE_FLOW_RATE,
                }
            ),
            note="ripple uplight; app name TOP",
        ),
        ZoneSpec(
            key="ring",
            name="SIDE",
            zone_byte=2,
            segments=8,
            capabilities=frozenset(
                {
                    Capability.ZONE_POWER,
                    Capability.ZONE_COLOR,
                    Capability.ZONE_BRIGHTNESS,
                }
            ),
            note="ring uplight, 8 addressable segments; app name SIDE",
        ),
        ZoneSpec(
            key="downlight",
            name="BOTTOM",
            zone_byte=3,
            segments=0,
            capabilities=frozenset(
                {
                    Capability.ZONE_POWER,
                    Capability.ZONE_BRIGHTNESS,
                    Capability.ZONE_COLOR_TEMP,
                }
            ),
            note="white-only downlight; app name BOTTOM",
        ),
        ZoneSpec(
            key="top_side_platform",
            name="TOP_SIDE_PLATFORM",
            zone_byte=4,
            capabilities=frozenset(),
            note="combo pseudo-zone, colour-strip form only — not modelled",
        ),
        ZoneSpec(
            key="top_side",
            name="TOP_SIDE",
            zone_byte=5,
            capabilities=frozenset(),
            note="combo pseudo-zone, colour-strip form only — not modelled",
        ),
    ),
    capabilities={
        Capability.POWER: CapabilitySpec("whole_power"),
        Capability.BRIGHTNESS: CapabilitySpec("whole_brightness"),
        Capability.ZONE_POWER: CapabilitySpec("zone_power"),
        Capability.ZONE_COLOR: CapabilitySpec(
            "zone_color",
            # mask sits AFTER the kelvin field for the colour attribute
            {"sub_mode": 0x2C, "attribute": 0x01, "mask_offset": 10},
        ),
        Capability.ZONE_BRIGHTNESS: CapabilitySpec(
            "zone_level",
            # ...but immediately after the level byte for brightness
            {"sub_mode": 0x2C, "attribute": 0x02, "mask_offset": 6},
        ),
        Capability.ZONE_COLOR_TEMP: CapabilitySpec(
            "zone_kelvin",
            {"sub_mode": 0x2C, "attribute": 0x03},
        ),
        Capability.ZONE_FLOW_RATE: CapabilitySpec(
            "zone_level",
            # same attribute byte as colour temp; the zone decides the meaning
            {"sub_mode": 0x2C, "attribute": 0x03},
        ),
        Capability.MODE_SELECT: CapabilitySpec("mode_select"),
        Capability.QUERY: CapabilitySpec("query"),
    },
    modes={"scene": 0x04, "diy": 0x0A, "solid": 0x0D, "music": 0x13, "game": 0x14},
    constraints=(
        MaxSimultaneousZones(
            limit=2,
            zone_keys=("ripple", "ring", "downlight"),
            note="hardware limit: enabling the downlight kicks the ripple off",
        ),
    ),
)

H6046: Final = DeviceProfile(
    sku="H6046",
    goods_type=112,
    name="Light bar",
    # Verified on hardware: unlike the H60B0, this SKU really does reach
    # 9000 K — a colorwc sweep 2700->9000 K was accepted at every step, with
    # devStatus echoing each requested value and the lamp staying lit. Kelvin
    # ceilings are per-firmware and never carry across SKUs.
    kelvin=KelvinRange(2000, 9000, verified=True, note="9000 K confirmed by colorwc sweep + devStatus readback"),
    zones=(
        ZoneSpec(
            key="segments",
            name="Bars",
            zone_byte=None,
            segments=10,
            capabilities=frozenset({Capability.SEGMENT_COLOR, Capability.SEGMENT_BRIGHTNESS}),
            note="2 bars x 5 segments; no zone byte, segments are addressed by mask alone",
        ),
    ),
    capabilities={
        Capability.POWER: CapabilitySpec("whole_power"),
        Capability.BRIGHTNESS: CapabilitySpec("whole_brightness"),
        Capability.BAR_POWER_MASK: CapabilitySpec("bar_power_mask"),
        Capability.SEGMENT_COLOR: CapabilitySpec(
            "segment_color_v2",
            {"sub_mode": 0x15, "attribute": 0x01, "mask_offset": 12},
        ),
        Capability.SEGMENT_BRIGHTNESS: CapabilitySpec(
            "segment_level_v2",
            # Sources disagree on the attribute byte (captures say 0x02, the
            # decompiled SubModeColorV2 struct numbers brightness 0x03) and
            # therefore on the mask offset. A hardware probe of the 0x02 form
            # (`33 05 15 02 ...`) produced no observable change at all — the
            # frame appears to be discarded. Refuse until one is confirmed.
            {"sub_mode": 0x15, "attribute": UNKNOWN, "mask_offset": UNKNOWN},
            verified=False,
            note="attribute byte disputed; raw 0x02 form probed 2026-08-11 and had no effect",
        ),
        Capability.MODE_SELECT: CapabilitySpec("mode_select"),
        Capability.QUERY: CapabilitySpec("query"),
    },
    modes={"scene": 0x04},
)

H6076: Final = DeviceProfile(
    sku="H6076",
    goods_type=69,
    name="Floor lamp",
    kelvin=KelvinRange(2000, 9000, verified=False, note="generic base2light; ceiling UNVERIFIED"),
    zones=(
        ZoneSpec(
            key="segments",
            name="Segments",
            zone_byte=None,
            segments=7,
            capabilities=frozenset({Capability.SEGMENT_COLOR}),
            note="segment count comes from the runtime device IC value, not a hardcoded table",
        ),
    ),
    capabilities={
        Capability.POWER: CapabilitySpec("whole_power"),
        Capability.BRIGHTNESS: CapabilitySpec("whole_brightness"),
        Capability.SEGMENT_COLOR: CapabilitySpec(
            "segment_color_v2",
            # The sub-mode byte is the open question: most likely the shared
            # RGBIC form rather than the legacy 0x15, which would also change
            # the body layout (hence the encoder name is provisional too).
            # Settling it needs a LAN test against the lamp with someone
            # watching the segments; nothing here can confirm it in software.
            {"sub_mode": UNKNOWN, "attribute": 0x01, "mask_offset": 12},
            verified=False,
            note="per-segment sub-mode UNCONFIRMED — needs a hardware test against the lamp",
        ),
        Capability.MODE_SELECT: CapabilitySpec("mode_select"),
        Capability.QUERY: CapabilitySpec("query"),
    },
    modes={"scene": 0x04},
)

PROFILES: Final[Mapping[str, DeviceProfile]] = {profile.sku: profile for profile in (H60B0, H6046, H6076)}


def get_profile(sku: str) -> DeviceProfile:
    """Look a profile up by SKU (case-insensitive)."""
    try:
        return PROFILES[sku.upper()]
    except KeyError as err:
        raise LanRawError(f"no raw-LAN profile for SKU {sku!r}") from err


def validate_table() -> None:
    """Assert every capability names an encoder that actually exists.

    Cheap insurance against a typo in a new table entry; the test suite calls
    it, and so may a diagnostics dump.
    """
    for profile in PROFILES.values():
        for capability, spec in profile.capabilities.items():
            if spec.encoder not in ENCODERS:
                raise LanRawError(f"{profile.sku}/{capability.value} names unknown encoder {spec.encoder!r}")
        for zone in profile.zones:
            for capability in zone.capabilities:
                if capability not in profile.capabilities:
                    raise LanRawError(
                        f"{profile.sku} zone {zone.key!r} claims {capability.value} "
                        "which the profile does not encode"
                    )
        for constraint in profile.constraints:
            for key in constraint.zone_keys:
                profile.zone(key)


def describe(profile: DeviceProfile) -> dict[str, Any]:
    """A JSON-safe dump of a profile, for diagnostics and docs."""
    return {
        "sku": profile.sku,
        "goods_type": profile.goods_type,
        "kelvin": {
            "min": profile.kelvin.minimum,
            "max": profile.kelvin.maximum,
            "verified": profile.kelvin.verified,
        },
        "zones": [
            {
                "key": zone.key,
                "name": zone.name,
                "zone_byte": zone.zone_byte,
                "segments": zone.segments,
                "capabilities": sorted(capability.value for capability in zone.capabilities),
            }
            for zone in profile.zones
        ],
        "capabilities": {
            capability.value: {
                "encoder": spec.encoder,
                "verified": spec.verified,
                "known": spec.known,
            }
            for capability, spec in profile.capabilities.items()
        },
        "constraints": [
            {"max_simultaneous_zones": constraint.limit, "zones": list(constraint.zone_keys)}
            for constraint in profile.constraints
        ],
    }
