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
differs per SKU (``0x2c`` on the H60B0, ``0x15`` on the H6046/H6076).

Transports
----------
Which *pipe* accepts a raw frame is a property of the SKU, not of the frame.
The frames themselves are transport-agnostic — the same 20 bytes travel over
LAN UDP, BLE GATT and cloud MQTT — but the lamps do not all listen on all
three, so every profile declares a preference-ordered, non-empty
:class:`Transport` tuple. Measured, not assumed:

* **H60B0, H6076** take raw frames over LAN UDP (and over encrypted BLE).
* **H6046** *ignores raw LAN frames entirely.* Seven envelope shapes were tried
  against it with a power-off frame whose effect is independently readable, and
  none of them changed anything, while the same frame worked first try on the
  H60B0. It is not an envelope problem: this SKU is on an older stack that
  carries raw frames only over an unencrypted BLE link.

The split is predictable from the BLE advertisement's manufacturer id, which a
scanner can read before connecting: :data:`BLE_MANUFACTURER_MODERN` (``0x8843``)
is the modern stack, :data:`BLE_MANUFACTURER_LEGACY` (``0x8803``) the legacy
one. That is why :attr:`DeviceProfile.ble_manufacturer_id` is table data.

**The transport list is a gate, not a hint.** A raw sender cannot detect a
mismatch — it has no SKU, sends fire-and-forget, and reads no reply — so a
frame posted to a SKU that does not carry it leaves the socket, changes
nothing, and is indistinguishable from success. Callers must check
:meth:`DeviceProfile.carries` before sending and fall back to whatever cloud
path they had.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum, unique
from typing import Any, Final

from .encoders import ENCODERS
from .errors import GoveeProtocolError


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
class Transport(str, Enum):
    """A pipe that can carry raw frames to a device.

    The bytes are identical on all three; only the framing around them and the
    set of SKUs that listen differ.
    """

    LAN_RAW = "lan_raw"
    """``ptReal`` frames in a JSON envelope, UDP to the device's command port."""

    BLE_ENCRYPTED = "ble_encrypted"
    """GATT write after a session handshake, frames encrypted under the key."""

    BLE_PLAINTEXT = "ble_plaintext"
    """Same characteristic and the same 20-byte frames, no handshake, no crypto."""


BLE_MANUFACTURER_MODERN: Final = 0x8843
"""Advertisement manufacturer id of the modern stack (LAN raw + encrypted BLE)."""

BLE_MANUFACTURER_LEGACY: Final = 0x8803
"""Advertisement manufacturer id of the legacy stack (plaintext BLE only)."""


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

    transports: tuple[Transport, ...]
    """Pipes this SKU accepts raw frames on, best first. Never empty."""

    ble_manufacturer_id: int
    """Advertisement manufacturer id, so a scanner can pick a profile early."""

    zones: tuple[ZoneSpec, ...] = ()
    capabilities: Mapping[Capability, CapabilitySpec] = field(default_factory=dict)
    modes: Mapping[str, ConstantValue] = field(default_factory=dict)
    constraints: tuple[MaxSimultaneousZones, ...] = ()
    note: str = ""

    def zone(self, key: str) -> ZoneSpec:
        for zone in self.zones:
            if zone.key == key:
                return zone
        raise GoveeProtocolError(f"{self.sku} has no zone {key!r} (have: {[z.key for z in self.zones]})")

    def carries(self, transport: Transport) -> bool:
        """Whether raw frames reach this SKU over ``transport``.

        The gate every raw sender must pass before it puts bytes on the wire.
        A sender cannot discover the answer for itself: the raw path is
        fire-and-forget with no reply, so a frame sent down a pipe the SKU
        ignores looks exactly like a frame that worked.

        Args:
            transport: The pipe the caller is about to send on.

        Returns:
            True when this SKU is known to act on raw frames from that pipe.
        """
        return transport in self.transports

    @property
    def preferred_transport(self) -> Transport:
        """The first transport in the table's preference order."""
        return self.transports[0]

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
    transports=(Transport.LAN_RAW, Transport.BLE_ENCRYPTED),
    ble_manufacturer_id=BLE_MANUFACTURER_MODERN,
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
    # Legacy stack: raw frames reach this SKU ONLY over an unencrypted BLE
    # link. Raw LAN frames are accepted by the socket and ignored by the
    # firmware — seven envelope shapes were tried with an independently
    # readable power-off frame and none of them did anything, while the same
    # frame worked first try on the H60B0. Do not add LAN_RAW here.
    transports=(Transport.BLE_PLAINTEXT,),
    ble_manufacturer_id=BLE_MANUFACTURER_LEGACY,
    zones=(
        ZoneSpec(
            key="segments",
            name="Bars",
            zone_byte=None,
            segments=10,
            capabilities=frozenset({Capability.SEGMENT_COLOR, Capability.SEGMENT_BRIGHTNESS}),
            # The cloud API declares 15 segments for this SKU; the hardware has
            # 10 (2 bars x 5) and segments 11-15 are phantom. Confirmed by
            # per-segment readback. Trust this number, not the cloud's.
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
            # Attribute 0x02 is brightness and the mask follows the level byte
            # immediately: `33 05 15 02 <level> <mask0 mask1>`. Settled by a
            # closed-loop test — write the frame, read the segments back — in
            # which one masked segment moved from level 100 to level 25 with
            # its colour untouched. The earlier probe that "had no effect" was
            # sent over LAN, which this SKU ignores (see `transports` above).
            #
            # mask_offset counts from proType as byte 0, so it differs BY
            # ATTRIBUTE: the colour body runs to index 11 and masks at 12, the
            # brightness body runs to index 4 and masks at 5. Reusing 12 here
            # would write the mask into dead padding and the frame would be a
            # silent no-op.
            {"sub_mode": 0x15, "attribute": 0x02, "mask_offset": 5},
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
    transports=(Transport.LAN_RAW, Transport.BLE_ENCRYPTED),
    ble_manufacturer_id=BLE_MANUFACTURER_MODERN,
    zones=(
        ZoneSpec(
            key="segments",
            name="Segments",
            zone_byte=None,
            segments=7,
            capabilities=frozenset({Capability.SEGMENT_COLOR, Capability.SEGMENT_BRIGHTNESS}),
            note="segment count comes from the runtime device IC value, not a hardcoded table",
        ),
    ),
    capabilities={
        Capability.POWER: CapabilitySpec("whole_power"),
        Capability.BRIGHTNESS: CapabilitySpec("whole_brightness"),
        # This SKU shares the whole 0x15 SubModeColorV2 body with the H6046; the
        # two differ only in transport and segment count. The guess that a
        # "newer" RGBIC sub-mode would apply here was wrong: the lamp reports
        # its live sub-mode as 0x15, and a closed-loop paint test (write, then
        # read the segments back) reddened exactly the masked segment, while the
        # mask-straight-after-RGB and H60B0 zone forms both did nothing.
        Capability.SEGMENT_COLOR: CapabilitySpec(
            "segment_color_v2",
            {"sub_mode": 0x15, "attribute": 0x01, "mask_offset": 12},
        ),
        Capability.SEGMENT_BRIGHTNESS: CapabilitySpec(
            "segment_level_v2",
            # Same body as the H6046, confirmed independently on this SKU: a
            # level+mask frame moved exactly the two masked segments.
            {"sub_mode": 0x15, "attribute": 0x02, "mask_offset": 5},
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
        raise GoveeProtocolError(f"no raw-LAN profile for SKU {sku!r}") from err


def validate_table() -> None:
    """Assert every capability names an encoder that actually exists.

    Cheap insurance against a typo in a new table entry; the test suite calls
    it, and so may a diagnostics dump.
    """
    for profile in PROFILES.values():
        # A SKU with no raw pipe cannot be driven by this package at all and
        # has no business carrying a profile; a duplicated entry would make the
        # preference order meaningless.
        if not profile.transports:
            raise GoveeProtocolError(f"{profile.sku} declares no transport")
        if len(set(profile.transports)) != len(profile.transports):
            raise GoveeProtocolError(f"{profile.sku} repeats a transport: {profile.transports}")
        for capability, spec in profile.capabilities.items():
            if spec.encoder not in ENCODERS:
                raise GoveeProtocolError(f"{profile.sku}/{capability.value} names unknown encoder {spec.encoder!r}")
        for zone in profile.zones:
            for capability in zone.capabilities:
                if capability not in profile.capabilities:
                    raise GoveeProtocolError(
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
        "transports": [transport.value for transport in profile.transports],
        "ble_manufacturer_id": profile.ble_manufacturer_id,
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
