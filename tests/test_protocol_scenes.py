"""Catalogue scene upload and the speed / direction rewrites (fork feature).

The two upload goldens are the frames sent to an H80C5, after which the lamp
reported the scene's catalogue code. The blobs are catalogue data.
"""

from __future__ import annotations

import pytest

from custom_components.govee.api.protocol import get_profile, scenes
from custom_components.govee.api.protocol.errors import FrameError
from custom_components.govee.api.protocol.frames import xor_checksum

# A single-effect blob: rainbow (effect 3), direction 5, speed 0x5c, 7 colours.
RAINBOW = bytes.fromhex("491a000304055c07ff0000ff7f00ffff0000ff000000ff00ffff8b00ff")
RAINBOW_CODE = 18188
RAINBOW_CONFIG = {"defDirIndex": 0, "defSpeed": 1, "directions": [5, 6, 7, 8], "speedRange": [85, 92, 98]}

# A two-layer blob: both layers are wave (effect 14, sub-type 1).
TWO_LAYER = bytes.fromhex(
    "4925000c02030010000e01320208010d020102005bff00afff0d000e012b020c01100100010000ff"
)

# An RGBIC blob is opaque here; only its length matters.
RGBIC = bytes([0x02]) + bytes(range(1, 40))


def _hex(frames: list[bytes]) -> list[str]:
    return [frame.hex() for frame in frames]


class TestSceneFrames:
    def test_h70cx_blob_drops_its_lead_byte_and_uses_marker_58(self) -> None:
        assert _hex(scenes.scene_frames(RAINBOW, RAINBOW_CODE)) == [
            "a3000102581a000304055c07ff0000ff7f00ff3b",
            "a3ffff0000ff000000ff00ffff8b00ff000000d7",
            "3305040c47000000000000000000000000000079",
        ]

    def test_rgbic_blob_is_sent_whole_with_marker_02(self) -> None:
        frames = scenes.scene_frames(RGBIC, 12337)
        assert frames[0][:5] == bytes([0xA3, 0x00, 0x01, len(frames) - 1, 0x02])
        assert frames[0][5:19] == RGBIC[:14]
        assert frames[-2][:2] == bytes([0xA3, 0xFF])
        assert frames[-1].hex().startswith("3305043130")

    def test_preset_scene_is_a_bare_commit(self) -> None:
        assert _hex(scenes.scene_frames(b"", 100)) == ["33050464000000000000000000000000000000" + "56"]

    def test_short_payload_still_gets_a_terminator_packet(self) -> None:
        blob = bytes([scenes.H70CX_LEAD]) + bytes(range(14))
        frames = scenes.scene_frames(blob, 1)
        assert len(frames) == 3
        assert frames[0][:5] == bytes([0xA3, 0x00, 0x01, 0x02, 0x58])
        assert frames[0][5:19] == bytes(range(14))
        assert frames[1][:2] == bytes([0xA3, 0xFF])
        assert frames[1][2:19] == bytes(17)

    def test_every_frame_is_checksummed(self) -> None:
        for frame in scenes.scene_frames(RGBIC, 12337):
            assert len(frame) == 20
            assert xor_checksum(frame[:19]) == frame[19]

    @pytest.mark.parametrize("code", [-1, 0x10000])
    def test_code_must_fit_two_bytes(self, code: int) -> None:
        with pytest.raises(FrameError):
            scenes.scene_frames(RAINBOW, code)


class TestParseAdjust:
    def test_single_effect_config(self) -> None:
        adjust = scenes.parse_adjust(RAINBOW_CONFIG, RAINBOW)
        assert adjust == scenes.SceneAdjust(
            speeds=((85,), (92,), (98,)),
            directions=(5, 6, 7, 8),
            direction_layers=None,
            default_speed=1,
            default_direction=0,
        )

    def test_layered_speeds_win_over_the_flat_range(self) -> None:
        config = {
            "defDirIndex": -1,
            "defSpeed": -1,
            "directions": [],
            "multiLayerSpeeds": [[40, 30], [50, 43], [60, 55]],
            "speedRange": [1, 2, 3],
            "multiLayersDirectionRange": [0],
        }
        adjust = scenes.parse_adjust(config, TWO_LAYER)
        assert adjust is not None
        assert adjust.speeds == ((40, 30), (50, 43), (60, 55))
        assert adjust.direction_layers == (0,)
        assert adjust.default_speed is None
        assert adjust.default_direction is None

    def test_a_scene_offering_nothing_is_none(self) -> None:
        config = {"defDirIndex": -1, "defSpeed": -1, "directions": [], "multiLayerSpeeds": []}
        assert scenes.parse_adjust(config, TWO_LAYER) is None

    @pytest.mark.parametrize("config", [None, "", [{"page": 1, "moveIn": [1, 2, 3]}], 7])
    def test_non_object_config_is_none(self, config: object) -> None:
        assert scenes.parse_adjust(config, RAINBOW) is None

    def test_rgbic_blob_is_none_even_with_an_object_config(self) -> None:
        assert scenes.parse_adjust(RAINBOW_CONFIG, RGBIC) is None

    def test_junk_values_are_dropped(self) -> None:
        config = {"speedRange": [85, "x", 300, True, 98], "directions": "nope", "defSpeed": 9}
        adjust = scenes.parse_adjust(config, RAINBOW)
        assert adjust is not None
        assert adjust.speeds == ((85,), (98,))
        assert adjust.directions == ()
        assert adjust.default_speed is None


class TestAdjustBlob:
    ADJUST = scenes.SceneAdjust(speeds=((85,), (92,), (98,)), directions=(5, 6, 7, 8))

    def test_speed_writes_one_byte(self) -> None:
        out = scenes.adjust_blob(RAINBOW, self.ADJUST, speed=2)
        assert out[6] == 98
        assert out[:6] == RAINBOW[:6] and out[7:] == RAINBOW[7:]

    def test_direction_writes_one_byte(self) -> None:
        out = scenes.adjust_blob(RAINBOW, self.ADJUST, direction=3)
        assert out[5] == 8
        assert out[:5] == RAINBOW[:5] and out[6:] == RAINBOW[6:]

    def test_both_at_once(self) -> None:
        out = scenes.adjust_blob(RAINBOW, self.ADJUST, speed=0, direction=1)
        assert (out[5], out[6]) == (6, 85)

    def test_nothing_requested_is_the_same_blob(self) -> None:
        assert scenes.adjust_blob(RAINBOW, self.ADJUST) == RAINBOW

    def test_each_layer_takes_its_own_speed(self) -> None:
        adjust = scenes.SceneAdjust(speeds=((40, 30), (60, 55)))
        out = scenes.adjust_blob(TWO_LAYER, adjust, speed=1)
        assert (out[11], out[29]) == (60, 55)
        changed = [index for index in range(len(out)) if out[index] != TWO_LAYER[index]]
        assert changed == [11, 29]

    def test_a_short_speed_row_leaves_later_layers_alone(self) -> None:
        adjust = scenes.SceneAdjust(speeds=((77,),))
        out = scenes.adjust_blob(TWO_LAYER, adjust, speed=0)
        assert (out[11], out[29]) == (77, 0x2B)

    def test_a_record_without_a_direction_byte_is_left_alone(self) -> None:
        # Wave has no direction byte, so a direction request changes nothing.
        adjust = scenes.SceneAdjust(directions=(5, 6), direction_layers=(0, 1, 9))
        assert scenes.adjust_blob(TWO_LAYER, adjust, direction=1) == TWO_LAYER

    def test_novelty_and_geometry_read_their_sub_type(self) -> None:
        def blob(*record: int) -> bytes:
            body = bytes(record) + bytes([1, 255, 0, 0])
            return bytes([0x49, len(body), 0]) + body

        adjust = scenes.SceneAdjust(speeds=((9,),), directions=(7,))

        novelty = scenes.adjust_blob(blob(16, 3, 50, 1), adjust, speed=0, direction=0)
        assert (novelty[5], novelty[6]) == (9, 7)

        static_novelty = blob(16, 4, 1, 2, 3)
        assert scenes.adjust_blob(static_novelty, adjust, speed=0, direction=0) == static_novelty

        geometry = scenes.adjust_blob(blob(15, 3, 50, 1, 13, 0, 0, 0), adjust, speed=0, direction=0)
        assert (geometry[5], geometry[7]) == (9, 7)

        plain_geometry = scenes.adjust_blob(blob(15, 1, 50, 1, 0, 0), adjust, speed=0, direction=0)
        assert plain_geometry[5] == 9
        assert plain_geometry[6:] == blob(15, 1, 50, 1, 0, 0)[6:]

    def test_smudge_takes_its_direction_at_position_one(self) -> None:
        # Synthetic one-layer smudge scene: effect 17, direction 2, speed 50.
        layer = bytes([0x11, 0x02, 0x32, 0x02, 0x10, 0x20, 0x30, 0x40, 0x50, 0x60])
        body = bytes([0x0C, 0x01, 0x02, 0x00, len(layer), 0x00]) + layer
        blob = bytes([0x49, len(body), 0x00]) + body
        adjust = scenes.SceneAdjust(speeds=((40,), (60,)), directions=(1, 2))

        assert scenes.adjust_blob(blob, adjust) == blob
        sped = scenes.adjust_blob(blob, adjust, speed=1)
        assert [i for i in range(len(blob)) if sped[i] != blob[i]] == [11]
        assert sped[11] == 60
        turned = scenes.adjust_blob(blob, adjust, direction=0)
        assert [i for i in range(len(blob)) if turned[i] != blob[i]] == [10]
        assert turned[10] == 1

    @pytest.mark.parametrize(("speed", "direction"), [(3, None), (-1, None), (None, 4), (None, -1)])
    def test_index_out_of_range(self, speed: int | None, direction: int | None) -> None:
        with pytest.raises(FrameError):
            scenes.adjust_blob(RAINBOW, self.ADJUST, speed=speed, direction=direction)

    @pytest.mark.parametrize(
        "blob",
        [
            b"",
            RGBIC,
            RAINBOW[:-1],
            bytes([0x49, 3, 0, 12, 2]),
            bytes([0x49, 4, 0, 12, 2, 3, 0]),
            bytes([0x49, 6, 0, 12, 2, 3, 0, 0, 0]),
            bytes([0x49, 7, 0, 12, 2, 3, 0, 0xFF, 0, 1]),
        ],
    )
    def test_malformed_blob(self, blob: bytes) -> None:
        with pytest.raises(FrameError):
            scenes.adjust_blob(blob, self.ADJUST, speed=0)

    def test_a_field_past_the_record_end_is_skipped(self) -> None:
        # Starlight keeps its speed at position 6; this record stops short.
        blob = bytes([0x49, 3, 0, 10, 0, 0])
        assert scenes.adjust_blob(blob, self.ADJUST, speed=0) == blob


class TestNames:
    def test_known_and_unknown_directions(self) -> None:
        assert scenes.direction_name(3) == "Clockwise"
        assert scenes.direction_name(17) == "Direction 17"


def test_h80c5_declares_scene_upload() -> None:
    assert get_profile("H80C5").scene_upload is True
    assert get_profile("H6076").scene_upload is False
