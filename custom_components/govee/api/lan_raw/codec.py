"""Profile-driven frame builder — the public surface of the raw-LAN codec.

:class:`LanRawCodec` binds one :class:`~.profiles.DeviceProfile` and turns
*intents* ("paint ring segments 0-3 blue") into 20-byte frames. It never reads
device state: zone and segment state is readable by nothing — not LAN, not
cloud — so every consumer is necessarily optimistic and must be able to build a
frame from nothing but the intent.

Segment indices are always 0-based, matching the mask bit positions.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from .encoders import ENCODERS
from .errors import LanRawError, UnknownEncodingError, UnsupportedCapabilityError
from .frames import ptreal_message, segment_mask
from .profiles import (
    Capability,
    CapabilitySpec,
    DeviceProfile,
    Unknown,
    ZoneSpec,
    get_profile,
)


class LanRawCodec:
    """Build raw-LAN frames for one device, driven entirely by its profile."""

    def __init__(self, profile: DeviceProfile) -> None:
        self.profile = profile

    @classmethod
    def for_sku(cls, sku: str) -> LanRawCodec:
        return cls(get_profile(sku))

    # -- plumbing ---------------------------------------------------------

    def _spec(self, capability: Capability, *, zone: ZoneSpec | None = None) -> CapabilitySpec:
        spec = self.profile.capabilities.get(capability)
        if spec is None:
            raise UnsupportedCapabilityError(f"{self.profile.sku} does not support {capability.value}")
        if zone is not None and capability not in zone.capabilities:
            raise UnsupportedCapabilityError(
                f"{self.profile.sku} zone {zone.key!r} does not support {capability.value}"
            )
        if not spec.known:
            unknown = sorted(key for key, value in spec.constants.items() if isinstance(value, Unknown))
            raise UnknownEncodingError(
                f"{self.profile.sku}/{capability.value}: {', '.join(unknown)} UNKNOWN"
                + (f" — {spec.note}" if spec.note else "")
            )
        return spec

    def _encode(self, capability: Capability, zone: ZoneSpec | None = None, **kwargs: Any) -> bytes:
        spec = self._spec(capability, zone=zone)
        encoder = ENCODERS[spec.encoder]
        return encoder(spec.constants, **kwargs)

    def _zone(self, key: str) -> ZoneSpec:
        return self.profile.zone(key)

    @staticmethod
    def _zone_byte(zone: ZoneSpec) -> int:
        if zone.zone_byte is None:
            raise LanRawError(f"zone {zone.key!r} is not addressed by a zone byte")
        return zone.zone_byte

    def _mask(self, zone: ZoneSpec, segments: Iterable[int] | None) -> bytes | None:
        """Resolve a segment selection into mask bytes.

        ``None`` on an unsegmented zone means "no mask field at all"; ``None``
        on a segmented zone means "every segment". An explicitly empty
        selection is an error — an all-zero mask is silently ignored by the
        firmware.
        """
        if not zone.segmented:
            if segments is not None:
                raise LanRawError(f"zone {zone.key!r} has no addressable segments")
            return None
        chosen = range(zone.segments) if segments is None else segments
        return segment_mask(chosen, segment_count=zone.segments)

    def _require_mask(self, zone: ZoneSpec, segments: Iterable[int] | None) -> bytes:
        """Like :meth:`_mask` but for capabilities whose frame always has one."""
        mask = self._mask(zone, segments)
        if mask is None:
            raise LanRawError(f"zone {zone.key!r} has no addressable segments")
        return mask

    def clamp_kelvin(self, kelvin: int) -> int:
        """Clamp to the profile's usable range (2000-6500 K on the H60B0)."""
        return self.profile.kelvin.clamp(kelvin)

    # -- whole device -----------------------------------------------------

    def power(self, on: bool) -> bytes:
        return self._encode(Capability.POWER, on=on)

    def brightness(self, percent: int) -> bytes:
        return self._encode(Capability.BRIGHTNESS, percent=percent)

    def query(self, command_type: int) -> bytes:
        """Build an ``aa <commandType>`` frame. Nothing will answer it — see
        :mod:`.client`. Present for completeness and golden-frame coverage."""
        return self._encode(Capability.QUERY, command_type=command_type)

    def mode(self, name: str, *, index: int = 0, marker: int | None = None) -> bytes:
        """Select a firmware mode by profile-declared name (``solid``, ``music``...)."""
        sub_mode = self.profile.modes.get(name)
        if sub_mode is None:
            raise UnsupportedCapabilityError(
                f"{self.profile.sku} has no mode {name!r} (have: {sorted(self.profile.modes)})"
            )
        if isinstance(sub_mode, Unknown):
            raise UnknownEncodingError(f"{self.profile.sku} mode {name!r} sub-mode byte is UNKNOWN")
        return self._encode(Capability.MODE_SELECT, sub_mode=sub_mode, index=index, marker=marker)

    def bar_power_mask(self, mask: int) -> bytes:
        """H6046 two-bar power mask (0x01 = bar A, 0x10 = bar B, 0x11 = both)."""
        return self._encode(Capability.BAR_POWER_MASK, mask=mask)

    # -- zone-addressed (H60B0 family) ------------------------------------

    def zone_power(self, zone: str, on: bool) -> bytes:
        spec = self._zone(zone)
        return self._encode(Capability.ZONE_POWER, spec, zone_byte=self._zone_byte(spec), on=on)

    def zone_color(
        self,
        zone: str,
        rgb: tuple[int, int, int],
        *,
        segments: Iterable[int] | None = None,
        kelvin: int = 0,
    ) -> bytes:
        spec = self._zone(zone)
        return self._encode(
            Capability.ZONE_COLOR,
            spec,
            zone_byte=self._zone_byte(spec),
            rgb=rgb,
            kelvin=kelvin,
            mask=self._mask(spec, segments),
        )

    def zone_brightness(self, zone: str, percent: int, *, segments: Iterable[int] | None = None) -> bytes:
        spec = self._zone(zone)
        return self._encode(
            Capability.ZONE_BRIGHTNESS,
            spec,
            zone_byte=self._zone_byte(spec),
            level=percent,
            mask=self._mask(spec, segments),
        )

    def color_temp(self, zone: str, kelvin: int) -> bytes:
        spec = self._zone(zone)
        return self._encode(
            Capability.ZONE_COLOR_TEMP,
            spec,
            zone_byte=self._zone_byte(spec),
            kelvin=self.clamp_kelvin(kelvin),
        )

    def flow_rate(self, zone: str, rate: int) -> bytes:
        """Ripple flow rate, 0-100. LAN-only — the cloud API cannot express it."""
        spec = self._zone(zone)
        return self._encode(Capability.ZONE_FLOW_RATE, spec, zone_byte=self._zone_byte(spec), level=rate)

    # -- segment-addressed (no zone byte) ---------------------------------

    def segment_color(
        self,
        rgb: tuple[int, int, int],
        *,
        segments: Iterable[int] | None = None,
        zone: str = "segments",
        kelvin: int = 0,
        white_rgb: tuple[int, int, int] = (0, 0, 0),
    ) -> bytes:
        spec = self._zone(zone)
        mask = self._require_mask(spec, segments)
        return self._encode(Capability.SEGMENT_COLOR, spec, rgb=rgb, mask=mask, kelvin=kelvin, white_rgb=white_rgb)

    def segment_brightness(
        self, percent: int, *, segments: Iterable[int] | None = None, zone: str = "segments"
    ) -> bytes:
        spec = self._zone(zone)
        mask = self._require_mask(spec, segments)
        return self._encode(Capability.SEGMENT_BRIGHTNESS, spec, level=percent, mask=mask)

    # -- envelope ---------------------------------------------------------

    @staticmethod
    def message(frames: Sequence[bytes]) -> Mapping[str, Any]:
        """Wrap frames in the ``ptReal`` envelope, ready for the client."""
        return ptreal_message(frames)
