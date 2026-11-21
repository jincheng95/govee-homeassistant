"""Layered composed string-light effects (fork feature).

A layered record stacks 1 to 3 single-effect records. The stock blocks below
are layers as Govee's scenes store them; every other colour is synthetic.
"""

from __future__ import annotations

import pytest

from custom_components.govee.api.protocol import h70cx
from custom_components.govee.api.protocol.errors import FrameError
from custom_components.govee.api.protocol.packets import packet_count
from custom_components.govee.api.protocol.scenes import H70CX_LEAD, _records

Layer = h70cx.Layer
TWO = [(255, 0, 0), (0, 255, 0)]
EIGHT = [(index, 0, 255 - index) for index in range(8)]


def _stock(*layers: bytes) -> bytes:
    """A layered record with the fixed header around raw layer records."""
    out = bytearray([0x0C, len(layers), 0x02, 0x00])
    for layer in layers:
        out += bytes([len(layer), 0]) + layer
    return bytes(out)


def _colours(layer: bytes, count_at: int) -> list[tuple[int, int, int]]:
    """The palette of a raw layer whose colour count sits at ``count_at``."""
    first = count_at + 1
    return [(layer[i], layer[i + 1], layer[i + 2]) for i in range(first, first + 3 * layer[count_at], 3)]


# Rustling leaves B: Flow upper left at 90 under Starlight at 30.
LEAVES_FLOW = bytes([0x01, 0x03, 0x05, 0x5A, 0x01, 0x00, 0x00, 0x04, 0x00, 0xFF, 0x27, 0x07, 0xFF, 0x5C, 0x1A, 0xFF, 0x24, 0x4D, 0xFF, 0x00])
LEAVES_STAR = bytes([0x0A, 0x1E, 0x00, 0x01, 0x00, 0x00, 0x1E, 0x02, 0x00, 0xFF, 0x00, 0xFF, 0xFF, 0x00])

# Universe B: Diffuse at 93, Spiral anticlockwise upward at 93, Starlight at 1.
UNIVERSE_DIFFUSE = bytes([0x06, 0x04, 0x5D, 0x01, 0x01, 0x05, 0x00, 0x00, 0x00, 0x00, 0x03, 0x00, 0xFF, 0xFF, 0x00, 0x00, 0xFF, 0xC1, 0x00, 0xFF])
UNIVERSE_SPIRAL = bytes([0x09, 0x05, 0x0F, 0x5D, 0x00, 0x01, 0x02, 0x00, 0x64, 0x00, 0x03, 0x00, 0x00, 0x00, 0xFF, 0xFF, 0xFF, 0xFF, 0x00, 0x00])
UNIVERSE_STAR = bytes([0x0A, 0x32, 0x00, 0x01, 0x00, 0x00, 0x01, 0x02, 0xFF, 0xFF, 0x00, 0xFF, 0xE5, 0xFF])

# Hot Pink B: Stripes clockwise at 90, Starlight at 65, Flow anticlockwise at 90.
PINK_STRIPES = bytes([0x02, 0x0A, 0x03, 0x5A, 0x00, 0x01, 0x3C, 0x01, 0x07, 0xFF, 0x8B, 0x14, 0xFF, 0x33, 0xA7, 0x54, 0xFF, 0xC4, 0xFF, 0x47, 0x99, 0xFF, 0x51, 0xCC, 0x8A, 0xE8, 0xFF, 0xFF, 0x9A, 0x96])
PINK_STAR = bytes([0x0A, 0x23, 0x00, 0x41, 0x00, 0x01, 0x41, 0x02, 0xFF, 0xD8, 0xA2, 0xFF, 0xAA, 0x14])
PINK_FLOW = bytes([0x01, 0x08, 0x04, 0x5A, 0x01, 0x01, 0x06, 0x03, 0xFF, 0x4C, 0xD0, 0x54, 0xFF, 0xC4, 0xFF, 0x7A, 0xA6])

# Each case: the layers, the stock layers, and the record positions where the
# template's default block differs from the stock block, with the template's
# byte. Speed and direction never appear there.
CASES = {
    "flow_under_starlight": (
        [
            Layer("flow_2", _colours(LEAVES_FLOW, 7), speed=90, direction="upper left"),
            Layer("starlight", _colours(LEAVES_STAR, 7), speed=30),
        ],
        (LEAVES_FLOW, LEAVES_STAR),
        {11: 0x01},
    ),
    "three_layers": (
        [
            Layer("diffuse", _colours(UNIVERSE_DIFFUSE, 10), speed=93),
            Layer("spiral", _colours(UNIVERSE_SPIRAL, 10), speed=93, direction="anticlockwise upward"),
            Layer("starlight", _colours(UNIVERSE_STAR, 7), speed=1),
        ],
        (UNIVERSE_DIFFUSE, UNIVERSE_SPIRAL, UNIVERSE_STAR),
        {7: 0x05, 9: 0x00, 10: 0x24, 51: 0x1E},
    ),
    "starlight_between": (
        [
            Layer("layered", _colours(PINK_STRIPES, 8), speed=90, direction="clockwise"),
            Layer("starlight", _colours(PINK_STAR, 7), speed=65),
            Layer("flow_2", _colours(PINK_FLOW, 7), speed=90, direction="anticlockwise"),
        ],
        (PINK_STRIPES, PINK_STAR, PINK_FLOW),
        {39: 0x1E, 41: 0x01, 43: 0x00, 55: 0x03, 60: 0x00},
    ),
}


class TestStockScenes:
    @pytest.mark.parametrize("case", list(CASES))
    def test_header_framing_and_lengths_match_the_stock_record(self, case: str) -> None:
        layers, stock_layers, _ = CASES[case]
        built = h70cx.layered_record(layers)
        stock = _stock(*stock_layers)

        assert len(built) == len(stock)
        assert built[:4] == bytes([0x0C, len(layers), 0x02, 0x00])
        blob = bytes([H70CX_LEAD, len(built), 0]) + built
        assert [end - start for start, end in _records(blob)] == [len(layer) for layer in stock_layers]

    @pytest.mark.parametrize("case", list(CASES))
    def test_only_the_template_blocks_differ_from_the_stock_record(self, case: str) -> None:
        layers, stock_layers, template_bytes = CASES[case]
        expected = bytearray(_stock(*stock_layers))
        for position, value in template_bytes.items():
            assert expected[position] != value
            expected[position] = value

        assert h70cx.layered_record(layers) == bytes(expected)

    def test_a_layer_with_the_stock_block_is_byte_exact(self) -> None:
        built = h70cx.layered_record(CASES["flow_under_starlight"][0])
        assert built[-len(LEAVES_STAR) - 2 :] == bytes([len(LEAVES_STAR), 0]) + LEAVES_STAR

        built = h70cx.layered_record(CASES["three_layers"][0])
        start = 4 + 2 + len(UNIVERSE_DIFFUSE) + 2
        assert built[start : start + len(UNIVERSE_SPIRAL)] == UNIVERSE_SPIRAL

        built = h70cx.layered_record(CASES["starlight_between"][0])
        assert built[6 : 6 + len(PINK_STRIPES)] == PINK_STRIPES


class TestLayeredRecord:
    def test_each_layer_is_its_single_effect_record(self) -> None:
        first = h70cx.effect_record("flexing", TWO, speed=9, shape="square", center="top_center")
        second = h70cx.effect_record("smudge", TWO[:1], direction="down")
        built = h70cx.layered_record(
            [Layer("flexing", TWO, speed=9, shape="square", center="top_center"), Layer("smudge", TWO[:1], direction="down")]
        )
        assert built == bytes([0x0C, 2, 2, 0, len(first), 0]) + first + bytes([len(second), 0]) + second

    @pytest.mark.parametrize("count", [0, 4])
    def test_layer_count_is_one_to_three(self, count: int) -> None:
        with pytest.raises(FrameError, match=f"takes 1 to 3 layers, not {count}") as err:
            h70cx.layered_record([Layer("flow", TWO)] * count)
        assert not isinstance(err.value, h70cx.LayerError)

    @pytest.mark.parametrize("effect", ["flow_1", "combo", "breathe"])
    def test_an_older_format_effect_cannot_be_a_layer(self, effect: str) -> None:
        with pytest.raises(h70cx.LayerError) as err:
            h70cx.layered_record([Layer("flow", TWO), Layer(effect, TWO)])
        assert err.value.layer == 2
        assert err.value.reason.startswith(f"{effect} cannot be a layer (have: flow, layered,")

    @pytest.mark.parametrize(
        ("layer", "reason"),
        [
            (Layer("smudge", TWO, direction="clockwise"), "smudge takes only up, down, not 'clockwise'"),
            (Layer("bounce_1", TWO), "bounce_1 takes 4 to 8 colours"),
            (Layer("rainbow", TWO, shape="round"), "rainbow has no shape"),
            (Layer("flame", TWO, speed=101), "speed must be from 0 to 100"),
        ],
    )
    def test_a_layer_error_names_the_layer(self, layer: h70cx.Layer, reason: str) -> None:
        with pytest.raises(h70cx.LayerError) as err:
            h70cx.layered_record([Layer("flow", TWO), Layer("starlight", TWO), layer])
        assert (err.value.layer, err.value.reason) == (3, reason)
        assert str(err.value) == f"layer 3: {reason}"


class TestLayeredFrames:
    def test_uploads_like_a_single_effect(self) -> None:
        layers = [Layer("flow_2", TWO, speed=90), Layer("starlight", TWO, speed=3)]
        record = h70cx.layered_record(layers)
        frames = h70cx.layered_frames(layers)

        assert frames[0][:5] == bytes([0xA3, 0x00, 0x01, packet_count(len(record) + 2), 0x58])
        assert frames[0][5:19] == (bytes([len(record), 0]) + record)[:14]
        assert frames[-2][1] == 0xFF
        assert frames[-1][:5] == bytes([0x33, 0x05, 0x0A, 0xCA, 0x02])

    def test_the_largest_layered_effect_fits_one_message(self) -> None:
        widest = max(h70cx.TEMPLATES, key=lambda name: len(h70cx.TEMPLATES[name].params))
        frames = h70cx.layered_frames([Layer(widest, EIGHT)] * h70cx.MAX_LAYERS)
        assert len(frames) <= h70cx.MAX_FRAMES

    def test_an_upload_over_the_frame_limit_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(h70cx, "MAX_FRAMES", 4)
        with pytest.raises(FrameError, match="the effect needs [0-9]+ frames, the limit is 4"):
            h70cx.layered_frames([Layer("rotate", EIGHT)] * 2)
