"""Profile-driven frame builder — the public surface of the raw-LAN codec.

:class:`GoveeCodec` binds one :class:`~.profiles.DeviceProfile` and turns
*intents* ("paint ring segments 0-3 blue") into 20-byte frames. It never reads
device state. Most zone and segment state has no readback, so a consumer must
be able to build a frame from nothing but the intent.

Segment indices are always 0-based, matching the mask bit positions.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from .diy import DiyZoneEffect, all_off, encode_payload
from .encoders import ENCODERS, constant, music_select
from .errors import DiyEffectError, GoveeProtocolError, UnknownEncodingError, UnsupportedCapabilityError
from .frames import ptreal_message, rgb_bytes, segment_mask
from .packets import chunk_effect, upload_effect
from .profiles import (
    Capability,
    CapabilitySpec,
    DeviceProfile,
    Unknown,
    ZoneSpec,
    get_profile,
)


class GoveeCodec:
    """Build raw-LAN frames for one device, driven entirely by its profile."""

    def __init__(self, profile: DeviceProfile) -> None:
        self.profile = profile

    @classmethod
    def for_sku(cls, sku: str) -> GoveeCodec:
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
            raise GoveeProtocolError(f"zone {zone.key!r} is not addressed by a zone byte")
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
                raise GoveeProtocolError(f"zone {zone.key!r} has no addressable segments")
            return None
        chosen = range(zone.segments) if segments is None else segments
        return segment_mask(chosen, segment_count=zone.segments)

    def _require_mask(self, zone: ZoneSpec, segments: Iterable[int] | None) -> bytes:
        """Like :meth:`_mask` but for capabilities whose frame always has one."""
        mask = self._mask(zone, segments)
        if mask is None:
            raise GoveeProtocolError(f"zone {zone.key!r} has no addressable segments")
        return mask

    def clamp_kelvin(self, kelvin: int) -> int:
        """Clamp to the profile's usable range (2700-6500 K on the H60B0)."""
        return self.profile.kelvin.clamp(kelvin)

    # -- whole device -----------------------------------------------------

    def power(self, on: bool) -> bytes:
        return self._encode(Capability.POWER, on=on)

    def brightness(self, percent: int) -> bytes:
        return self._encode(Capability.BRIGHTNESS, percent=percent)

    def query(self, command_type: int) -> bytes:
        """Build an ``aa <commandType>`` frame. Only MQTT ``ptReal`` carries an answer back."""
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

    def gradient(self, on: bool) -> bytes:
        """Blend segment paints across segment boundaries, or keep them hard-edged."""
        return self._encode(Capability.GRADIENT, on=on)

    def power_off_memory(self, behaviour: int) -> bytes:
        """What the device does when power returns: 0 last state, 1 on, 2 off."""
        return self._encode(Capability.POWER_OFF_MEMORY, behaviour=behaviour)

    def music_effect(
        self,
        effect: int | str,
        *,
        sensitivity: int,
        colors: Sequence[tuple[int, int, int]] | None = None,
        soft: bool = False,
        manual_color: tuple[int, int, int] | None = None,
    ) -> list[bytes]:
        """Build a music-effect selection: an optional palette upload, then the commit.

        Args:
            effect: An effect name from the profile's music table, or a raw id.
            sensitivity: Microphone sensitivity, 0 to the profile's maximum.
            colors: The palette to upload first. None sends the commit alone.
            soft: Soft rather than dynamic response. It goes into the palette's
                style byte when the profile has one, else into the commit.
            manual_color: One fixed colour instead of the automatic colours.

        Returns:
            Frames to send in order, in one envelope.

        Raises:
            UnsupportedCapabilityError: The SKU has no music table, the effect
                takes no palette, or the commit carries no options.
            GoveeProtocolError: A name, sensitivity or palette is out of range.
        """
        spec = self.profile.music
        sub_mode = self.profile.modes.get("music")
        if spec is None or not isinstance(sub_mode, int):
            raise UnsupportedCapabilityError(f"{self.profile.sku} has no music effect table")
        if isinstance(effect, str):
            try:
                effect = spec.effects[effect.strip().lower()]
            except KeyError:
                raise GoveeProtocolError(
                    f"{self.profile.sku} has no music effect {effect!r} (have: {sorted(spec.effects)})"
                ) from None
        if not 0 <= effect <= 0xFF:
            raise GoveeProtocolError(f"music effect id {effect} out of byte range")
        if not 0 <= sensitivity <= spec.max_sensitivity:
            raise GoveeProtocolError(f"music sensitivity {sensitivity} outside 0-{spec.max_sensitivity}")
        if (manual_color is not None or (soft and not spec.palette_style)) and not spec.commit_options:
            raise UnsupportedCapabilityError(f"{self.profile.sku} music commit carries no soft or manual colour")

        frames: list[bytes] = []
        if colors is not None:
            if effect not in spec.palette_effects:
                raise UnsupportedCapabilityError(f"music effect {effect:#04x} takes no palette")
            if not 1 <= len(colors) <= spec.max_colors:
                raise GoveeProtocolError(f"{len(colors)} music palette colours, allowed 1-{spec.max_colors}")
            payload = bytearray([effect, len(colors)])
            for rgb in colors:
                payload += rgb_bytes(rgb)
            payload += spec.palette_tails.get(effect, b"")
            if spec.palette_style:
                payload.append(1 if soft else 0)
            frames += chunk_effect(payload, marker=spec.marker)
        commit_soft = soft and not spec.palette_style
        frames.append(
            music_select(
                sub_mode=sub_mode, effect=effect, sensitivity=sensitivity, soft=commit_soft, manual_rgb=manual_color
            )
        )
        return frames

    def timer_slots(self) -> int:
        """How many on/off timers the device keeps."""
        return constant(self._spec(Capability.LIGHT_TIMER).constants, "slots")

    def light_timer(
        self,
        slot: int,
        *,
        enabled: bool,
        turn_on: bool,
        hour: int,
        minute: int,
        weekdays: Iterable[int] = (),
    ) -> bytes:
        """Set one on/off timer.

        Args:
            slot: 0-based timer slot, below :meth:`timer_slots`.
            enabled: Whether the timer runs.
            turn_on: True turns the light on at the time, False turns it off.
            hour: Hour of day, 0-23.
            minute: Minute, 0-59.
            weekdays: Days to repeat on, 0 Monday to 6 Sunday. No days runs once.
        """
        return self._encode(
            Capability.LIGHT_TIMER,
            slot=slot,
            enabled=enabled,
            turn_on=turn_on,
            hour=hour,
            minute=minute,
            weekdays=weekdays,
        )

    def sleep_timer(self, *, enabled: bool, start_brightness: int, minutes: int) -> bytes:
        """Dim the light to off over ``minutes``, starting at ``start_brightness`` percent."""
        return self._encode(
            Capability.SLEEP_TIMER, enabled=enabled, start_brightness=start_brightness, minutes=minutes
        )

    def wake_up(
        self,
        *,
        enabled: bool,
        end_brightness: int,
        hour: int,
        minute: int,
        weekdays: Iterable[int] = (),
        duration: int,
        kelvin: int,
        white_rgb: tuple[int, int, int],
    ) -> bytes:
        """Brighten the light so it reaches ``end_brightness`` at the given time.

        Args:
            enabled: Whether the wake-up runs.
            end_brightness: Brightness reached at the end, in percent.
            hour: Hour of day the wake-up ends, 0-23.
            minute: Minute, 0-59.
            weekdays: Days to repeat on, 0 Monday to 6 Sunday. No days runs once.
            duration: Minutes the light takes to brighten.
            kelvin: Colour temperature, clamped to the profile range.
            white_rgb: RGB approximation of ``kelvin``, carried beside it.
        """
        return self._encode(
            Capability.WAKE_UP,
            enabled=enabled,
            end_brightness=end_brightness,
            hour=hour,
            minute=minute,
            weekdays=weekdays,
            duration=duration,
            kelvin=self.clamp_kelvin(kelvin),
            white_rgb=white_rgb,
        )

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

    def diy_effect(
        self,
        effects: Mapping[str, DiyZoneEffect | None],
        *,
        index: int = 0,
    ) -> list[bytes]:
        """Build the full DIY upload: ``0xA3`` chunks plus the commit frame.

        ``effects`` maps zone key ("ripple", "ring") to that zone's
        :class:`~.diy.DiyZoneEffect`; zones left out get the empty "off"
        record. At least one zone must be on — an all-off DIY would just
        blank the lamp, which is a power intent, not an effect.

        ``index`` is the 16-bit DIY code the commit names, low byte first.
        0 is the default for a locally composed effect.

        Send the returned frames in order, in one envelope
        (:meth:`message` / ``LanUdpClient.async_send_frames``).
        """
        spec = self.profile.diy
        if spec is None:
            raise UnsupportedCapabilityError(f"{self.profile.sku} has no DIY effect layout")
        if all_off(effects.values()):
            raise DiyEffectError("all zones off — refuse to upload a blank DIY effect")
        sub_mode = self.profile.modes.get("diy")
        if not isinstance(sub_mode, int):
            raise UnknownEncodingError(f"{self.profile.sku} 'diy' commit sub-mode is UNKNOWN")
        payload = encode_payload(spec, effects)
        return list(upload_effect(payload, marker=spec.marker, sub_mode=sub_mode, index=index))

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

    def segment_color_temp(
        self,
        kelvin: int,
        white_rgb: tuple[int, int, int],
        *,
        segments: Iterable[int] | None = None,
        zone: str = "segments",
    ) -> bytes:
        """Set segments to a colour temperature.

        Args:
            kelvin: Colour temperature, clamped to the profile range.
            white_rgb: RGB approximation of ``kelvin``, carried beside it.
            segments: 0-based segments; None means every segment.
            zone: The segmented zone.
        """
        spec = self._zone(zone)
        mask = self._require_mask(spec, segments)
        return self._encode(
            Capability.SEGMENT_COLOR_TEMP,
            spec,
            rgb=(0xFF, 0xFF, 0xFF),
            mask=mask,
            kelvin=self.clamp_kelvin(kelvin),
            white_rgb=white_rgb,
        )

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
