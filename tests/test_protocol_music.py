"""Music-effect frames: the palette upload under marker 0x41, then the commit.

Expected frames are written out byte for byte, checksum included.
"""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import patch

import pytest

from custom_components.govee.api.protocol import (
    GoveeCodec,
    GoveeProtocolError,
    UnsupportedCapabilityError,
    get_profile,
    profiles,
)

CODEC = GoveeCodec.for_sku("H60B0")

RAINBOW = (
    (0xFF, 0x00, 0x00),
    (0xFF, 0x7F, 0x00),
    (0xFF, 0xFF, 0x00),
    (0x00, 0xFF, 0x00),
    (0x00, 0x00, 0xFF),
    (0x00, 0xFF, 0xFF),
    (0x8B, 0x00, 0xFF),
)


def _commit(effect: int, sensitivity: int, checksum: int) -> bytes:
    return bytes([0x33, 0x05, 0x13, effect, sensitivity] + [0x00] * 14 + [checksum])


class TestCommit:
    @pytest.mark.parametrize(
        ("effect", "sensitivity", "expected"),
        [
            ("rhythm", 99, _commit(0x72, 0x63, 0x34)),
            ("shiny", 99, _commit(0x31, 0x63, 0x77)),
            ("shiny", 0, _commit(0x31, 0x00, 0x14)),
            ("shiny", 49, _commit(0x31, 0x31, 0x25)),
            ("custom", 99, _commit(0x82, 0x63, 0xC4)),
            (0x33, 99, _commit(0x33, 0x63, 0x75)),
        ],
    )
    def test_commit_only(self, effect: int | str, sensitivity: int, expected: bytes) -> None:
        assert CODEC.music_effect(effect, sensitivity=sensitivity) == [expected]

    def test_effect_ids(self) -> None:
        assert dict(get_profile("H60B0").music.effects) == {
            "custom": 0x82,
            "stippling": 0x83,
            "hopping": 0x33,
            "flowing_light": 0x53,
            "luminous": 0x51,
            "bloom": 0x30,
            "rhythm": 0x72,
            "shiny": 0x31,
        }

    @pytest.mark.parametrize("sensitivity", [-1, 100])
    def test_sensitivity_range(self, sensitivity: int) -> None:
        with pytest.raises(GoveeProtocolError):
            CODEC.music_effect("shiny", sensitivity=sensitivity)

    @pytest.mark.parametrize("effect", ["spectrum", 256, -1])
    def test_unknown_effect_refused(self, effect: int | str) -> None:
        with pytest.raises(GoveeProtocolError):
            CODEC.music_effect(effect, sensitivity=50)

    def test_sku_without_music_table_refused(self) -> None:
        with pytest.raises(UnsupportedCapabilityError):
            GoveeCodec.for_sku("H6046").music_effect("shiny", sensitivity=50)


class TestPalette:
    def test_dynamic_palette_then_commit(self) -> None:
        assert CODEC.music_effect("stippling", sensitivity=99, colors=RAINBOW) == [
            bytes([0xA3, 0x00, 0x01, 0x02, 0x41, 0x83, 0x07, 0xFF, 0x00, 0x00,
                   0xFF, 0x7F, 0x00, 0xFF, 0xFF, 0x00, 0x00, 0xFF, 0x00, 0xE5]),
            bytes([0xA3, 0xFF, 0x00, 0x00, 0xFF, 0x00, 0xFF, 0xFF, 0x8B, 0x00,
                   0xFF, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0xD7]),
            _commit(0x83, 0x63, 0xC5),
        ]

    def test_soft_palette_sets_the_style_byte(self) -> None:
        assert CODEC.music_effect("bloom", sensitivity=99, colors=RAINBOW, soft=True) == [
            bytes([0xA3, 0x00, 0x01, 0x02, 0x41, 0x30, 0x07, 0xFF, 0x00, 0x00,
                   0xFF, 0x7F, 0x00, 0xFF, 0xFF, 0x00, 0x00, 0xFF, 0x00, 0x56]),
            bytes([0xA3, 0xFF, 0x00, 0x00, 0xFF, 0x00, 0xFF, 0xFF, 0x8B, 0x00,
                   0xFF, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0xD6]),
            _commit(0x30, 0x63, 0x76),
        ]

    @pytest.mark.parametrize("effect", ["hopping", "custom"])
    def test_effects_without_the_plain_layout_refuse_a_palette(self, effect: str) -> None:
        with pytest.raises(UnsupportedCapabilityError):
            CODEC.music_effect(effect, sensitivity=99, colors=RAINBOW)

    @pytest.mark.parametrize("count", [0, 9])
    def test_palette_size(self, count: int) -> None:
        with pytest.raises(GoveeProtocolError):
            CODEC.music_effect("shiny", sensitivity=99, colors=((1, 2, 3),) * count)


class TestTableSelfCheck:
    def test_table_is_consistent(self) -> None:
        profiles.validate_table()

    @pytest.mark.parametrize(
        "change",
        [
            {"modes": {"diy": 0x0A}},
            {"music": "bad_palette"},
            {"music": "bad_id"},
        ],
    )
    def test_bad_music_spec_is_refused(self, change: dict) -> None:
        base = get_profile("H60B0")
        if change.get("music") == "bad_palette":
            change = {"music": replace(base.music, palette_effects=frozenset({0x99}))}
        elif change.get("music") == "bad_id":
            change = {"music": replace(base.music, effects={"x": 0x100})}
        broken = replace(base, sku="H9999", **change)
        with patch.dict(profiles.PROFILES, {"H9999": broken}):
            with pytest.raises(GoveeProtocolError):
                profiles.validate_table()
