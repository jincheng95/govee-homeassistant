"""Tests for the raw Govee LAN codec (``api/protocol``).

The heart of this file is :data:`GOLDEN_FRAMES`: frames captured from hardware
and verified byte-for-byte, expressed as *profile + intent -> exact bytes*. LAN
writes are unverifiable at runtime (the lamps never answer a raw frame), so
these fixtures ARE the verification: if a refactor changes a single byte, a
golden test fails.

Each fixture is checked three ways — the captured hex, the captured base64, and
an independently recomputed XOR checksum — so a transcription slip cannot
quietly become the expected value.

No test here opens a socket; the client tests inject a fake endpoint factory.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from dataclasses import replace
from unittest.mock import patch

import pytest

from custom_components.govee.api.protocol import (
    Capability,
    LanUdpClient,
    GoveeCodec,
    SegmentMaskError,
    UnknownEncodingError,
    UnsupportedCapabilityError,
    build_frame,
    frames as frames_mod,
    get_profile,
    packets,
    profiles,
    segment_mask,
    validate_table,
    xor_checksum,
)
from custom_components.govee.api.protocol.errors import FrameError, GoveeProtocolError

H60B0 = GoveeCodec.for_sku("H60B0")
H6046 = GoveeCodec.for_sku("H6046")
H6076 = GoveeCodec.for_sku("H6076")


def _hex(frame: bytes) -> str:
    return frame.hex(" ")


# ==============================================================================
# Golden frames — captured from hardware (H60B0 and H6046)
# ==============================================================================

# (id, intent, expected hex, expected base64)
GOLDEN_FRAMES: list[tuple[str, Callable[[], bytes], str, str]] = [
    (
        "whole power ON",
        lambda: H60B0.power(True),
        "33 01 01 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 33",
        "MwEBAAAAAAAAAAAAAAAAAAAAADM=",
    ),
    (
        "whole power OFF",
        lambda: H60B0.power(False),
        "33 01 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 32",
        "MwEAAAAAAAAAAAAAAAAAAAAAADI=",
    ),
    (
        "whole brightness 50%",
        lambda: H60B0.brightness(50),
        "33 04 32 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 05",
        "MwQyAAAAAAAAAAAAAAAAAAAAAAU=",
    ),
    (
        "zone1 ripple power ON",
        lambda: H60B0.zone_power("ripple", True),
        "33 30 01 01 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 03",
        "MzABAQAAAAAAAAAAAAAAAAAAAAM=",
    ),
    (
        "zone2 ring power ON",
        lambda: H60B0.zone_power("ring", True),
        "33 30 02 01 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00",
        "MzACAQAAAAAAAAAAAAAAAAAAAAA=",
    ),
    (
        "zone3 downlight power OFF",
        lambda: H60B0.zone_power("downlight", False),
        "33 30 03 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00",
        "MzADAAAAAAAAAAAAAAAAAAAAAAA=",
    ),
    (
        "ripple solid blue",
        lambda: H60B0.zone_color("ripple", (0, 0, 255)),
        "33 05 2c 01 01 00 00 ff 00 00 00 00 00 00 00 00 00 00 00 e5",
        "MwUsAQEAAP8AAAAAAAAAAAAAAOU=",
    ),
    (
        "ripple brightness 80%",
        lambda: H60B0.zone_brightness("ripple", 80),
        "33 05 2c 01 02 50 00 00 00 00 00 00 00 00 00 00 00 00 00 49",
        "MwUsAQJQAAAAAAAAAAAAAAAAAEk=",
    ),
    (
        "ripple flow rate 75",
        lambda: H60B0.flow_rate("ripple", 75),
        "33 05 2c 01 03 4b 00 00 00 00 00 00 00 00 00 00 00 00 00 53",
        "MwUsAQNLAAAAAAAAAAAAAAAAAFM=",
    ),
    (
        "ring, all 8 segs cyan",
        lambda: H60B0.zone_color("ring", (0, 255, 255)),
        "33 05 2c 02 01 00 ff ff 00 00 ff 00 00 00 00 00 00 00 00 e6",
        "MwUsAgEA//8AAP8AAAAAAAAAAOY=",
    ),
    (
        "ring, seg 3 only yellow",  # 1-based in the spec prose, index 2 here
        lambda: H60B0.zone_color("ring", (255, 255, 0), segments=[2]),
        "33 05 2c 02 01 ff ff 00 00 00 04 00 00 00 00 00 00 00 00 1d",
        "MwUsAgH//wAAAAQAAAAAAAAAAB0=",
    ),
    (
        "ring, segs 1-7 blue",
        lambda: H60B0.zone_color("ring", (0, 0, 255), segments=range(7)),
        "33 05 2c 02 01 00 00 ff 00 00 7f 00 00 00 00 00 00 00 00 99",
        "MwUsAgEAAP8AAH8AAAAAAAAAAJk=",
    ),
    (
        "ring brightness all 100%",
        lambda: H60B0.zone_brightness("ring", 100),
        "33 05 2c 02 02 64 ff 00 00 00 00 00 00 00 00 00 00 00 00 81",
        "MwUsAgJk/wAAAAAAAAAAAAAAAIE=",
    ),
    (
        "ring brightness segs 5-8 at 32%",
        lambda: H60B0.zone_brightness("ring", 32, segments=[4, 5, 6, 7]),
        "33 05 2c 02 02 20 f0 00 00 00 00 00 00 00 00 00 00 00 00 ca",
        "MwUsAgIg8AAAAAAAAAAAAAAAAMo=",
    ),
    (
        "downlight brightness 60%",
        lambda: H60B0.zone_brightness("downlight", 60),
        "33 05 2c 03 02 3c 00 00 00 00 00 00 00 00 00 00 00 00 00 27",
        "MwUsAwI8AAAAAAAAAAAAAAAAACc=",
    ),
    (
        "downlight 2700 K",
        lambda: H60B0.color_temp("downlight", 2700),
        "33 05 2c 03 03 0a 8c 00 00 00 00 00 00 00 00 00 00 00 00 9c",
        "MwUsAwMKjAAAAAAAAAAAAAAAAJw=",
    ),
    (
        "downlight 6500 K (cold cap)",
        lambda: H60B0.color_temp("downlight", 6500),
        "33 05 2c 03 03 19 64 00 00 00 00 00 00 00 00 00 00 00 00 67",
        "MwUsAwMZZAAAAAAAAAAAAAAAAGc=",
    ),
    (
        "solid-colour mode",
        lambda: H60B0.mode("solid"),
        "33 05 0d 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 3b",
        "MwUNAAAAAAAAAAAAAAAAAAAAADs=",
    ),
    (
        "music mode",
        lambda: H60B0.mode("music"),
        "33 05 13 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 25",
        "MwUTAAAAAAAAAAAAAAAAAAAAACU=",
    ),
    (
        "game mode",
        lambda: H60B0.mode("game"),
        "33 05 14 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 22",
        "MwUUAAAAAAAAAAAAAAAAAAAAACI=",
    ),
    (
        "DIY commit (idx 3)",
        lambda: H60B0.mode("diy", index=3, marker=0x58),
        "33 05 0a 03 00 58 00 00 00 00 00 00 00 00 00 00 00 00 00 67",
        "MwUKAwBYAAAAAAAAAAAAAAAAAGc=",
    ),
    (
        "query power state",
        lambda: H60B0.query(0x01),
        "aa 01 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 ab",
        "qgEAAAAAAAAAAAAAAAAAAAAAAKs=",
    ),
    # H6046 SubModeColorV2, segment 1 (index 0) red
    (
        "H6046 segment 1 red",
        lambda: H6046.segment_color((255, 0, 0), segments=[0]),
        "33 05 15 01 ff 00 00 00 00 00 00 00 01 00 00 00 00 00 00 dc",
        "MwUVAf8AAAAAAAAAAQAAAAAAANw=",
    ),
]

GOLDEN_IDS = [case[0] for case in GOLDEN_FRAMES]


class TestGoldenFrames:
    """Hardware-verified frames, pinned byte for byte."""

    @pytest.mark.parametrize(("name", "intent", "expected_hex", "_b64"), GOLDEN_FRAMES, ids=GOLDEN_IDS)
    def test_frame_hex(self, name: str, intent: Callable[[], bytes], expected_hex: str, _b64: str) -> None:
        assert _hex(intent()) == expected_hex, name

    @pytest.mark.parametrize(("name", "intent", "_hexstr", "expected_b64"), GOLDEN_FRAMES, ids=GOLDEN_IDS)
    def test_frame_base64(self, name: str, intent: Callable[[], bytes], _hexstr: str, expected_b64: str) -> None:
        assert base64.b64encode(intent()).decode() == expected_b64, name

    @pytest.mark.parametrize(("name", "_intent", "expected_hex", "expected_b64"), GOLDEN_FRAMES, ids=GOLDEN_IDS)
    def test_spec_table_is_self_consistent(
        self, name: str, _intent: Callable[[], bytes], expected_hex: str, expected_b64: str
    ) -> None:
        """The captured hex, base64 and checksum must agree with each other.

        Guards against a transcription error in the capture notes silently
        becoming this suite's expected value.
        """
        raw = bytes.fromhex(expected_hex.replace(" ", ""))
        assert len(raw) == frames_mod.FRAME_LENGTH
        assert base64.b64encode(raw).decode() == expected_b64
        assert raw[-1] == xor_checksum(raw[:-1])

    def test_every_frame_is_twenty_bytes(self) -> None:
        for name, intent, _hexstr, _b64 in GOLDEN_FRAMES:
            assert len(intent()) == 20, name


# ==============================================================================
# frames.py — checksum, padding, envelope
# ==============================================================================


class TestFramePrimitives:
    """The 20-byte frame, its padding and its XOR checksum."""

    def test_xor_checksum(self) -> None:
        assert xor_checksum(b"") == 0
        assert xor_checksum(b"\x33\x01\x01") == 0x33
        assert xor_checksum([0xFF, 0x0F]) == 0xF0

    def test_build_frame_zero_pads_to_twenty(self) -> None:
        frame = build_frame([0x33, 0x01, 0x01])
        assert len(frame) == 20
        assert frame[3:19] == bytes(16)
        assert frame[19] == xor_checksum(frame[:19])

    def test_build_frame_accepts_a_full_nineteen_byte_body(self) -> None:
        frame = build_frame(bytes(range(19)))
        assert len(frame) == 20

    def test_build_frame_rejects_an_oversized_body(self) -> None:
        with pytest.raises(FrameError):
            build_frame(bytes(20))

    def test_build_frame_rejects_a_non_byte_value(self) -> None:
        with pytest.raises(FrameError):
            build_frame([0x33, 256])

    def test_kelvin_is_big_endian(self) -> None:
        assert frames_mod.kelvin_bytes(2700) == b"\x0a\x8c"
        assert frames_mod.kelvin_bytes(6500) == b"\x19\x64"

    def test_kelvin_rejects_out_of_range(self) -> None:
        with pytest.raises(FrameError):
            frames_mod.kelvin_bytes(70000)

    def test_rgb_validation(self) -> None:
        assert frames_mod.rgb_bytes((1, 2, 3)) == b"\x01\x02\x03"
        with pytest.raises(FrameError):
            frames_mod.rgb_bytes((0, 0, 300))

    def test_percent_clamping(self) -> None:
        assert frames_mod.clamp_percent(-5) == 0
        assert frames_mod.clamp_percent(500) == 100
        assert frames_mod.clamp_percent(42) == 42

    def test_write_at_refuses_to_overflow(self) -> None:
        with pytest.raises(FrameError):
            frames_mod.write_at(bytearray(), 18, b"\x01\x02")

    def test_ptreal_envelope(self) -> None:
        message = frames_mod.ptreal_message([build_frame([0x33, 0x01, 0x01])])
        assert message == {"msg": {"cmd": "ptReal", "data": {"command": ["MwEBAAAAAAAAAAAAAAAAAAAAADM="]}}}

    def test_ptreal_envelope_keeps_frame_order(self) -> None:
        first = H60B0.power(True)
        second = H60B0.brightness(50)
        message = frames_mod.ptreal_message([first, second])
        commands = message["msg"]["data"]["command"]
        assert commands == [base64.b64encode(f).decode() for f in (first, second)]

    def test_ptreal_envelope_rejects_empty(self) -> None:
        with pytest.raises(FrameError):
            frames_mod.ptreal_message([])

    def test_ptreal_envelope_rejects_a_short_frame(self) -> None:
        with pytest.raises(FrameError):
            frames_mod.ptreal_message([b"\x33\x01"])

    def test_encode_message_is_compact_json(self) -> None:
        payload = frames_mod.encode_message({"msg": {"cmd": "ptReal"}})
        assert payload == b'{"msg":{"cmd":"ptReal"}}'
        assert json.loads(payload)["msg"]["cmd"] == "ptReal"


# ==============================================================================
# Segment masks — LSB-first packing and the all-zero guard
# ==============================================================================


class TestSegmentMask:
    """Segment selections resolved into little-endian mask bytes."""

    def test_mask0_holds_segments_zero_to_seven(self) -> None:
        assert segment_mask([0], segment_count=8) == b"\x01\x00"
        assert segment_mask([7], segment_count=8) == b"\x80\x00"
        assert segment_mask(range(8), segment_count=8) == b"\xff\x00"

    def test_mask1_holds_segments_eight_to_fifteen(self) -> None:
        assert segment_mask([8], segment_count=16) == b"\x00\x01"
        assert segment_mask([15], segment_count=16) == b"\x00\x80"
        assert segment_mask([0, 9], segment_count=16) == b"\x01\x02"

    def test_ten_segment_bar_uses_two_bits_of_mask1(self) -> None:
        assert segment_mask(range(10), segment_count=10) == b"\xff\x03"

    def test_empty_mask_is_refused(self) -> None:
        """An all-zero mask is accepted by the firmware and does nothing."""
        with pytest.raises(SegmentMaskError):
            segment_mask([], segment_count=8)

    def test_out_of_range_segment_is_refused(self) -> None:
        with pytest.raises(SegmentMaskError):
            segment_mask([8], segment_count=8)
        with pytest.raises(SegmentMaskError):
            segment_mask([-1], segment_count=8)

    def test_more_than_sixteen_segments_is_refused(self) -> None:
        with pytest.raises(SegmentMaskError):
            segment_mask([0], segment_count=17)

    def test_codec_refuses_an_empty_selection(self) -> None:
        with pytest.raises(SegmentMaskError):
            H60B0.zone_color("ring", (255, 0, 0), segments=[])
        with pytest.raises(SegmentMaskError):
            H60B0.zone_brightness("ring", 50, segments=[])
        with pytest.raises(SegmentMaskError):
            H6046.segment_color((255, 0, 0), segments=[])

    def test_codec_refuses_segments_on_an_unsegmented_zone(self) -> None:
        with pytest.raises(GoveeProtocolError):
            H60B0.zone_color("ripple", (255, 0, 0), segments=[0])

    def test_mask_offset_differs_per_attribute(self) -> None:
        """Colour puts the mask at byte 10, brightness at byte 6 — table data."""
        colour = H60B0.zone_color("ring", (0, 0, 0), segments=[0])
        assert colour[10:12] == b"\x01\x00"
        brightness = H60B0.zone_brightness("ring", 0, segments=[0])
        assert brightness[6:8] == b"\x01\x00"


# ==============================================================================
# Kelvin handling
# ==============================================================================


class TestKelvin:
    """Per-SKU colour-temperature ranges and their clamping."""

    def test_h60b0_clamps_to_the_verified_ceiling(self) -> None:
        """Above ~6500 K the firmware drops the zone entirely."""
        assert H60B0.clamp_kelvin(9000) == 6500
        assert H60B0.clamp_kelvin(1200) == 2000
        assert H60B0.clamp_kelvin(4000) == 4000

    def test_color_temp_frame_is_clamped(self) -> None:
        assert H60B0.color_temp("downlight", 9000) == H60B0.color_temp("downlight", 6500)

    def test_h6046_really_does_reach_9000k(self) -> None:
        """The H60B0's 6500 K ceiling does NOT generalise across SKUs.

        Confirmed on hardware: a sweep from 2700 K to 9000 K was accepted at
        every step, with the device echoing back each requested value and
        staying lit. Kelvin bounds are therefore per-profile data, never a
        shared constant.
        """
        profile = get_profile("H6046")
        assert (profile.kelvin.minimum, profile.kelvin.maximum) == (2000, 9000)
        assert profile.kelvin.verified is True
        assert profile.kelvin.clamp(9000) == 9000
        # ...while the H60B0 still clamps hard at its measured ceiling.
        assert get_profile("H60B0").kelvin.clamp(9000) == 6500


# ==============================================================================
# Profile table
# ==============================================================================


class TestProfiles:
    """The declarative table: lookups, transports, capabilities, self-checks."""

    def test_table_is_internally_consistent(self) -> None:
        validate_table()

    def test_lookup_is_case_insensitive(self) -> None:
        assert get_profile("h60b0") is get_profile("H60B0")

    def test_unknown_sku_is_refused(self) -> None:
        with pytest.raises(GoveeProtocolError):
            get_profile("H0000")

    def test_h60b0_zone_bytes_and_segments(self) -> None:
        profile = get_profile("H60B0")
        assert [(z.key, z.zone_byte, z.segments) for z in profile.zones] == [
            ("ripple", 1, 0),
            ("ring", 2, 8),
            ("downlight", 3, 0),
            ("top_side_platform", 4, 0),
            ("top_side", 5, 0),
        ]

    def test_h60b0_two_of_three_zones_constraint_is_declarative(self) -> None:
        (constraint,) = get_profile("H60B0").constraints
        assert constraint.limit == 2
        assert constraint.zone_keys == ("ripple", "ring", "downlight")

    def test_segment_sub_mode_is_data_not_branching(self) -> None:
        """The sub-mode byte differs per SKU and must stay table data.

        The H6046 and H6076 share the whole 0x15 body; the H60B0 does not.
        Assuming one carries to the other is exactly the mistake that made a
        session's worth of segment paints vanish silently.
        """
        h60b0 = get_profile("H60B0").capabilities[Capability.ZONE_COLOR]
        h6046 = get_profile("H6046").capabilities[Capability.SEGMENT_COLOR]
        h6076 = get_profile("H6076").capabilities[Capability.SEGMENT_COLOR]
        assert h60b0.constants["sub_mode"] == 0x2C
        assert h6046.constants["sub_mode"] == 0x15
        assert h6076.constants["sub_mode"] == 0x15

    def test_the_shipped_table_has_no_unknown_constants_left(self) -> None:
        """Every constant block in the table is settled against hardware.

        Not a style rule: an UNKNOWN is a capability the integration refuses to
        offer, so this is the list of things that currently work.
        """
        for profile in profiles.PROFILES.values():
            for capability, spec in profile.capabilities.items():
                assert spec.known, f"{profile.sku}/{capability.value} still UNKNOWN"

    def test_unknown_constants_refuse_to_encode(self) -> None:
        """The table can say "I do not know" — and then we do not guess.

        Exercised against a synthetic profile so the mechanism stays covered
        now that no shipped SKU has an UNKNOWN left.
        """
        unsettled = profiles.DeviceProfile(
            sku="H9999",
            goods_type=999,
            name="Unsettled",
            kelvin=profiles.KelvinRange(2000, 6500),
            transports=(profiles.Transport.LAN_RAW,),
            ble_manufacturer_id=profiles.BLE_MANUFACTURER_MODERN,
            zones=(
                profiles.ZoneSpec(
                    key="segments",
                    name="Segments",
                    zone_byte=None,
                    segments=4,
                    capabilities=frozenset({Capability.SEGMENT_COLOR}),
                ),
            ),
            capabilities={
                Capability.SEGMENT_COLOR: profiles.CapabilitySpec(
                    "segment_color_v2",
                    {"sub_mode": profiles.UNKNOWN, "attribute": 0x01, "mask_offset": 12},
                    verified=False,
                )
            },
        )
        with pytest.raises(UnknownEncodingError):
            GoveeCodec(unsettled).segment_color((255, 0, 0), segments=[0])

    def test_unsupported_capability_is_distinct_from_unknown(self) -> None:
        with pytest.raises(UnsupportedCapabilityError):
            H60B0.zone_color("downlight", (255, 0, 0))  # white-only zone
        with pytest.raises(UnsupportedCapabilityError):
            H6076.bar_power_mask(0x11)
        with pytest.raises(UnsupportedCapabilityError):
            H60B0.mode("nonsense")

    def test_unknown_zone_is_refused(self) -> None:
        with pytest.raises(GoveeProtocolError):
            H60B0.zone_power("nope", True)

    def test_zoneless_sku_has_no_zone_byte(self) -> None:
        with pytest.raises(GoveeProtocolError):
            H6046.zone_power("segments", True)

    def test_supports_reports_zone_scoped_capabilities(self) -> None:
        profile = get_profile("H60B0")
        assert profile.supports(Capability.ZONE_COLOR, zone="ring")
        assert not profile.supports(Capability.ZONE_COLOR, zone="downlight")
        assert profile.supports(Capability.POWER)

    def test_capability_spec_known_flag(self) -> None:
        assert get_profile("H60B0").capabilities[Capability.ZONE_COLOR].known
        assert not profiles.CapabilitySpec("zone_color", {"sub_mode": profiles.UNKNOWN}).known

    def test_every_profile_declares_a_transport(self) -> None:
        """A SKU with no raw pipe cannot be driven and must not have a profile."""
        for profile in profiles.PROFILES.values():
            assert profile.transports
            assert len(set(profile.transports)) == len(profile.transports)
            assert profile.preferred_transport is profile.transports[0]

    def test_the_legacy_sku_does_not_carry_raw_frames_over_lan(self) -> None:
        """The one asymmetry the whole transport field exists to express.

        The H6046 accepts the datagram and ignores the frame, which a
        fire-and-forget sender cannot distinguish from success — so this must
        be readable from the table without connecting to anything.
        """
        h6046 = get_profile("H6046")
        assert h6046.carries(profiles.Transport.LAN_RAW) is False
        assert h6046.carries(profiles.Transport.BLE_PLAINTEXT) is True
        assert h6046.carries(profiles.Transport.BLE_ENCRYPTED) is False

        for sku in ("H60B0", "H6076"):
            profile = get_profile(sku)
            assert profile.carries(profiles.Transport.LAN_RAW) is True
            assert profile.carries(profiles.Transport.BLE_PLAINTEXT) is False

    def test_manufacturer_id_predicts_the_stack(self) -> None:
        """A scanner can pick the profile from the advertisement, pre-connect."""
        assert get_profile("H6046").ble_manufacturer_id == profiles.BLE_MANUFACTURER_LEGACY
        assert get_profile("H60B0").ble_manufacturer_id == profiles.BLE_MANUFACTURER_MODERN
        assert get_profile("H6076").ble_manufacturer_id == profiles.BLE_MANUFACTURER_MODERN

        legacy = profiles.BLE_MANUFACTURER_LEGACY
        modern = profiles.BLE_MANUFACTURER_MODERN
        assert legacy != modern
        # The manufacturer id and the transport list must agree, or the
        # advertisement would route a device to a stack it cannot use.
        for profile in profiles.PROFILES.values():
            expected = modern if profile.carries(profiles.Transport.LAN_RAW) else legacy
            assert profile.ble_manufacturer_id == expected

    def test_table_self_check_rejects_a_transportless_profile(self) -> None:
        """validate_table() is the guard rail for a new table entry."""
        base = get_profile("H60B0")
        for bad in ((), (profiles.Transport.LAN_RAW, profiles.Transport.LAN_RAW)):
            broken = replace(base, sku="H9999", transports=bad)
            with patch.dict(profiles.PROFILES, {"H9999": broken}):
                with pytest.raises(GoveeProtocolError):
                    profiles.validate_table()

    def test_describe_is_json_safe(self) -> None:
        for sku in ("H60B0", "H6046", "H6076"):
            dumped = json.dumps(profiles.describe(get_profile(sku)))
            assert sku in dumped
            assert "transports" in dumped

    def test_bar_power_mask(self) -> None:
        assert _hex(H6046.bar_power_mask(0x11)).startswith("33 33 11")

    def test_adding_a_sku_is_one_table_entry(self) -> None:
        """A new SKU reuses existing encoders — no code path keys off the SKU."""
        clone = profiles.DeviceProfile(
            sku="H9999",
            goods_type=999,
            name="Imaginary bar",
            kelvin=profiles.KelvinRange(2000, 6500),
            transports=(profiles.Transport.LAN_RAW,),
            ble_manufacturer_id=profiles.BLE_MANUFACTURER_MODERN,
            zones=(
                profiles.ZoneSpec(
                    key="segments",
                    name="Segments",
                    zone_byte=None,
                    segments=4,
                    capabilities=frozenset({Capability.SEGMENT_COLOR}),
                ),
            ),
            capabilities={
                Capability.SEGMENT_COLOR: profiles.CapabilitySpec(
                    "segment_color_v2",
                    {"sub_mode": 0x15, "attribute": 0x01, "mask_offset": 12},
                )
            },
        )
        frame = GoveeCodec(clone).segment_color((255, 0, 0), segments=[0])
        assert _hex(frame) == _hex(H6046.segment_color((255, 0, 0), segments=[0]))


# ==============================================================================
# 0xA3 multipacket chunker (mechanism only — untested against hardware)
# ==============================================================================


class TestMultipacket:
    """The 0xA3 chunker's arithmetic (mechanism only, hardware-untested)."""

    def test_packet_count_math(self) -> None:
        assert packets.packet_count(0) == 1
        assert packets.packet_count(14) == 1
        assert packets.packet_count(15) == 2
        assert packets.packet_count(31) == 2
        assert packets.packet_count(32) == 3
        # the captured 68-byte DIY blob = 3 header bytes + 65 data bytes
        assert packets.packet_count(65) == 4

    def test_header_occupies_the_first_three_bytes_of_packet_zero(self) -> None:
        chunks = packets.chunk_effect(bytes(range(65)), marker=0x58)
        assert len(chunks) == 4
        assert chunks[0][0:2] == b"\xa3\x00"
        assert chunks[0][2:5] == bytes([0x01, 0x04, 0x58])  # lead, pktCount, marker
        assert chunks[0][5:19] == bytes(range(14))  # effect data starts here

    def test_sequence_bytes_and_terminator(self) -> None:
        chunks = packets.chunk_effect(bytes(65), marker=0x58)
        assert [chunk[1] for chunk in chunks] == [0x00, 0x01, 0x02, 0xFF]

    def test_single_packet_payload_is_also_terminated(self) -> None:
        (chunk,) = packets.chunk_effect(bytes(5), marker=0x58)
        assert chunk[1] == 0xFF

    def test_all_chunks_are_valid_frames(self) -> None:
        for chunk in packets.chunk_effect(bytes(range(65)), marker=0x58):
            assert len(chunk) == 20
            assert chunk[19] == xor_checksum(chunk[:19])

    def test_payload_reassembles(self) -> None:
        data = bytes(range(65))
        chunks = packets.chunk_effect(data, marker=0x58)
        rebuilt = chunks[0][5:19] + b"".join(chunk[2:19] for chunk in chunks[1:])
        assert rebuilt[: len(data)] == data

    def test_commit_frame_matches_the_captured_diy_commit(self) -> None:
        frame = packets.commit_frame(sub_mode=0x0A, index=3, marker=0x58)
        assert _hex(frame) == "33 05 0a 03 00 58 00 00 00 00 00 00 00 00 00 00 00 00 00 67"

    def test_upload_effect_puts_the_commit_last(self) -> None:
        sequence = packets.upload_effect(bytes(20), marker=0x58, index=1)
        assert len(sequence) == 3
        assert sequence[-1][0:3] == b"\x33\x05\x0a"

    def test_oversized_payload_is_refused(self) -> None:
        with pytest.raises(FrameError):
            packets.chunk_effect(bytes(10_000), marker=0x58)


# ==============================================================================
# Write-only client
# ==============================================================================


class _FakeSender:
    """Stand-in for an asyncio DatagramTransport. Never touches a socket."""

    def __init__(self) -> None:
        self.sent: list[bytes] = []
        self.closed = False

    def sendto(self, data: bytes, addr: tuple[str, int] | None = None) -> None:
        self.sent.append(data)

    def close(self) -> None:
        self.closed = True


class TestClient:
    """The write-only UDP sender, driven through an injected endpoint."""

    def _client(self) -> tuple[LanUdpClient, list[tuple[str, int]], _FakeSender]:
        sender = _FakeSender()
        opened: list[tuple[str, int]] = []

        async def factory(host: str, port: int) -> _FakeSender:
            opened.append((host, port))
            return sender

        return LanUdpClient(endpoint_factory=factory), opened, sender

    @pytest.mark.asyncio
    async def test_sends_ptreal_to_port_4003(self) -> None:
        client, opened, sender = self._client()
        await client.async_send_frame("192.0.2.205", H60B0.power(True))
        assert opened == [("192.0.2.205", 4003)]
        payload = json.loads(sender.sent[0])
        assert payload == {"msg": {"cmd": "ptReal", "data": {"command": ["MwEBAAAAAAAAAAAAAAAAAAAAADM="]}}}

    @pytest.mark.asyncio
    async def test_multiple_frames_ride_in_one_datagram(self) -> None:
        client, _opened, sender = self._client()
        await client.async_send_frames("192.0.2.205", [H60B0.power(True), H60B0.brightness(50)])
        assert len(sender.sent) == 1
        assert len(json.loads(sender.sent[0])["msg"]["data"]["command"]) == 2

    @pytest.mark.asyncio
    async def test_endpoint_is_closed_even_on_failure(self) -> None:
        sender = _FakeSender()

        def boom(data: bytes, addr: tuple[str, int] | None = None) -> None:
            raise OSError("network down")

        sender.sendto = boom  # type: ignore[method-assign]

        async def factory(host: str, port: int) -> _FakeSender:
            return sender

        client = LanUdpClient(endpoint_factory=factory)
        with pytest.raises(OSError):
            await client.async_send_frame("192.0.2.205", H60B0.power(True))
        assert sender.closed is True

    @pytest.mark.asyncio
    async def test_client_never_reads(self) -> None:
        """The raw path is write-only by design — see the module docstring."""
        client, _opened, _sender = self._client()
        assert not [name for name in dir(client) if "recv" in name or "read" in name]

    def test_default_port_is_the_command_port(self) -> None:
        from custom_components.govee.api.protocol.client import LAN_COMMAND_PORT

        assert LAN_COMMAND_PORT == 4003


# ==============================================================================
# Layering guarantees
# ==============================================================================


class TestLayering:
    """The package's structural promises, asserted rather than assumed."""

    def test_package_imports_nothing_from_home_assistant(self) -> None:
        """This layer must stay a plain library: no HA, no integration coupling."""
        import pathlib
        import re

        from custom_components.govee.api import protocol

        package = pathlib.Path(protocol.__file__).parent
        modules = sorted(package.glob("*.py"))
        assert len(modules) >= 7
        for module in modules:
            source = module.read_text(encoding="utf-8")
            code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))
            assert "import homeassistant" not in code, module
            assert "from homeassistant" not in code, module
            # no parent-package (integration) imports either
            assert not re.search(r"^\s*from \.\.", code, re.MULTILINE), module
