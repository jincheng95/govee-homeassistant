"""DIY effect payload encoding — the ``0x50`` form riding 0xA3 multipackets.

Golden payload bytes follow the worked example in
``reference/govee-device-protocol.md`` §4: lead ``0x0d``, then the ripple
record (mode, recLen, reserved, speed, colour count, N×RGB, direction, flow),
then the ring record (mode, recLen, modeParam, speed, colour count, N×RGB).
"""

from __future__ import annotations

import pytest

from custom_components.govee.api.protocol import (
    DIRECTION_CW,
    DIRECTION_REVERSE,
    DiyEffectError,
    DiyZoneEffect,
    GoveeCodec,
    UnsupportedCapabilityError,
    resolve_mode,
    xor_checksum,
)
from custom_components.govee.api.protocol import diy as diy_mod
from custom_components.govee.api.protocol.profiles import H60B0

CODEC = GoveeCodec.for_sku("H60B0")

SEVEN_COLORS = tuple((index, 2 * index, 3 * index) for index in range(1, 8))
SEVEN_RGB_HEX = " ".join(f"{r:02x} {g:02x} {b:02x}" for r, g, b in SEVEN_COLORS)


def _hex(data: bytes) -> str:
    return data.hex(" ")


# =========================================================================
# Payload records
# =========================================================================


class TestDiyPayload:
    """Record and payload layout against the spec's worked example."""

    def test_worked_example_shape(self) -> None:
        """0d | 01 1a 00 33 07 <7 RGB> 01 32 | 01 18 00 32 07 <7 RGB>."""
        payload = diy_mod.encode_payload(
            H60B0.diy,
            {
                "ripple": DiyZoneEffect(
                    mode=1,
                    speed=0x33,
                    colors=SEVEN_COLORS,
                    direction=DIRECTION_CW,
                    flow_rate=0x32,
                ),
                "ring": DiyZoneEffect(mode=1, speed=0x32, colors=SEVEN_COLORS),
            },
        )
        assert _hex(payload) == (
            f"0d 01 1a 00 33 07 {SEVEN_RGB_HEX} 01 32 01 18 00 32 07 {SEVEN_RGB_HEX}"
        )

    def test_record_length_arithmetic(self) -> None:
        """recLen counts the bytes that follow it within the record."""
        spec = H60B0.diy.zone("ripple")
        record = diy_mod.encode_record(
            spec, DiyZoneEffect(mode=4, colors=((255, 0, 0),))
        )
        assert record[1] == len(record) - 2
        # reserved, speed default 50, count, RGB, direction default CW, flow default 50
        assert _hex(record) == "04 08 00 32 01 ff 00 00 01 32"

    def test_ring_record_has_no_tail(self) -> None:
        spec = H60B0.diy.zone("ring")
        record = diy_mod.encode_record(
            spec, DiyZoneEffect(mode=10, speed=100, colors=((0, 0, 255),))
        )
        assert _hex(record) == "0a 06 00 64 01 00 00 ff"

    def test_cover_mode_param(self) -> None:
        spec = H60B0.diy.zone("ring")
        record = diy_mod.encode_record(
            spec, DiyZoneEffect(mode=9, colors=((1, 2, 3),), mode_param=1)
        )
        assert record[2] == 0x01

    def test_zone_off_is_two_byte_empty_record(self) -> None:
        spec = H60B0.diy.zone("ring")
        assert diy_mod.encode_record(spec, None) == b"\x00\x00"
        assert diy_mod.encode_record(spec, DiyZoneEffect(mode=0)) == b"\x00\x00"

    def test_missing_zone_gets_empty_record(self) -> None:
        payload = diy_mod.encode_payload(
            H60B0.diy, {"ring": DiyZoneEffect(mode=1, colors=((255, 255, 255),))}
        )
        assert _hex(payload) == "0d 00 00 01 06 00 32 01 ff ff ff"

    def test_unknown_zone_key_refused(self) -> None:
        with pytest.raises(DiyEffectError):
            diy_mod.encode_payload(
                H60B0.diy, {"downlight": DiyZoneEffect(mode=1, colors=((1, 1, 1),))}
            )


class TestDiyValidation:
    """Out-of-spec parameters are refused, never silently reshaped."""

    @pytest.mark.parametrize("speed", [0, 101])
    def test_speed_range(self, speed: int) -> None:
        spec = H60B0.diy.zone("ring")
        with pytest.raises(DiyEffectError):
            diy_mod.encode_record(
                spec, DiyZoneEffect(mode=1, speed=speed, colors=((1, 1, 1),))
            )

    def test_empty_palette_refused(self) -> None:
        spec = H60B0.diy.zone("ring")
        with pytest.raises(DiyEffectError):
            diy_mod.encode_record(spec, DiyZoneEffect(mode=1))

    def test_palette_cap(self) -> None:
        spec = H60B0.diy.zone("ring")
        colors = tuple((1, 1, 1) for _ in range(diy_mod.MAX_DIY_COLORS + 1))
        with pytest.raises(DiyEffectError):
            diy_mod.encode_record(spec, DiyZoneEffect(mode=1, colors=colors))

    def test_direction_on_ring_refused(self) -> None:
        spec = H60B0.diy.zone("ring")
        with pytest.raises(DiyEffectError):
            diy_mod.encode_record(
                spec,
                DiyZoneEffect(mode=1, colors=((1, 1, 1),), direction=DIRECTION_REVERSE),
            )

    def test_flow_on_ring_refused(self) -> None:
        spec = H60B0.diy.zone("ring")
        with pytest.raises(DiyEffectError):
            diy_mod.encode_record(
                spec, DiyZoneEffect(mode=1, colors=((1, 1, 1),), flow_rate=50)
            )

    def test_bad_direction_refused(self) -> None:
        spec = H60B0.diy.zone("ripple")
        with pytest.raises(DiyEffectError):
            diy_mod.encode_record(
                spec, DiyZoneEffect(mode=1, colors=((1, 1, 1),), direction=4)
            )

    def test_mode_out_of_byte_range(self) -> None:
        spec = H60B0.diy.zone("ripple")
        with pytest.raises(DiyEffectError):
            diy_mod.encode_record(spec, DiyZoneEffect(mode=256, colors=((1, 1, 1),)))


class TestResolveMode:
    def test_name_lookup_per_zone(self) -> None:
        """twinkle is 11 on the ripple and 3 on the ring — no shared enum."""
        assert resolve_mode(H60B0.diy.zone("ripple"), "twinkle") == 11
        assert resolve_mode(H60B0.diy.zone("ring"), "twinkle") == 3

    def test_raw_int_passthrough(self) -> None:
        """The ripple tolerates ring-enum ints (Simple authoring mode)."""
        assert resolve_mode(H60B0.diy.zone("ripple"), 9) == 9

    def test_unknown_name_refused(self) -> None:
        with pytest.raises(DiyEffectError):
            resolve_mode(H60B0.diy.zone("ring"), "sparkle")


# =========================================================================
# Codec surface: chunks + commit
# =========================================================================


class TestCodecDiyEffect:
    def test_upload_ends_with_commit(self) -> None:
        frames = CODEC.diy_effect(
            {"ripple": DiyZoneEffect(mode=1, colors=SEVEN_COLORS)},
            index=3,
        )
        # Verified DIY commit from the spec: 33 05 0a 03 00 58.
        assert (
            _hex(frames[-1])
            == "33 05 0a 03 00 58 00 00 00 00 00 00 00 00 00 00 00 00 00 67"
        )
        assert frames[0][0] == 0xA3
        assert frames[-2][1] == 0xFF  # last chunk's sequence terminator
        for frame in frames:
            assert len(frame) == 20
            assert xor_checksum(frame[:19]) == frame[19]

    def test_payload_reassembles_across_chunks(self) -> None:
        effects = {
            "ripple": DiyZoneEffect(
                mode=1, speed=0x33, colors=SEVEN_COLORS, flow_rate=0x32
            ),
            "ring": DiyZoneEffect(mode=1, speed=0x32, colors=SEVEN_COLORS),
        }
        frames = CODEC.diy_effect(effects)
        chunks = frames[:-1]
        data = chunks[0][5:19]  # skip proType, seq, 3-byte header
        for chunk in chunks[1:]:
            data += chunk[2:19]
        expected = diy_mod.encode_payload(H60B0.diy, effects)
        assert data[: len(expected)] == expected
        assert chunks[0][3] == len(chunks)  # packet count in header
        assert chunks[0][4] == 0x58  # DIY marker

    def test_all_off_refused(self) -> None:
        with pytest.raises(DiyEffectError):
            CODEC.diy_effect({"ripple": None, "ring": DiyZoneEffect(mode=0)})

    def test_sku_without_diy_refused(self) -> None:
        with pytest.raises(UnsupportedCapabilityError):
            GoveeCodec.for_sku("H6046").diy_effect(
                {"ripple": DiyZoneEffect(mode=1, colors=((1, 1, 1),))}
            )
