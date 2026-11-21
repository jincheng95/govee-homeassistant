"""Basic and combo string-light effects (fork feature)."""

from __future__ import annotations

import pytest

from custom_components.govee.api.protocol import h70cx, h70cx_basic
from custom_components.govee.api.protocol.errors import FrameError
from custom_components.govee.api.protocol.frames import build_frame
from custom_components.govee.api.protocol.packets import commit_frame

RED = (0xFF, 0x00, 0x00)
ORANGE = (0xFF, 0x7F, 0x00)
YELLOW = (0xFF, 0xFF, 0x00)
GREEN = (0x00, 0xFF, 0x00)
BLUE = (0x00, 0x00, 0xFF)
CYAN = (0x00, 0xFF, 0xFF)
PURPLE = (0x8B, 0x00, 0xFF)
WHITE = (0xFF, 0xFF, 0xFF)

COMMIT = commit_frame(index=h70cx.EFFECT_CODE)


def test_rainbow_1_anticlockwise_fits_one_frame() -> None:
    frames = h70cx_basic.effect_frames("rainbow_1", [PURPLE, WHITE], speed=85, direction="anticlockwise")
    assert frames == [
        build_frame(bytes([0xA3, 0x00, 0x01, 0x02, 0x04, 0x09, 0x0A, 0x55, 0x06, 0x8B, 0x00, 0xFF, 0xFF, 0xFF, 0xFF])),
        build_frame(bytes([0xA3, 0xFF])),
        COMMIT,
    ]


def test_breathe_with_eight_colours_spills_into_the_terminator() -> None:
    colors = [PURPLE, WHITE, RED, ORANGE, YELLOW, GREEN, BLUE, CYAN]
    frames = h70cx_basic.effect_frames("breathe", colors)
    assert frames == [
        build_frame(
            bytes([0xA3, 0x00, 0x01, 0x02, 0x04, 0x02, 0x02, 0x32, 0x18])
            + bytes([0x8B, 0x00, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x00, 0x00, 0xFF])
        ),
        build_frame(
            bytes([0xA3, 0xFF, 0x7F, 0x00, 0xFF, 0xFF, 0x00, 0x00, 0xFF, 0x00, 0x00, 0x00, 0xFF, 0x00, 0xFF, 0xFF])
        ),
        COMMIT,
    ]


def test_combo_of_four_takes_three_frames() -> None:
    colors = [RED, ORANGE, YELLOW, GREEN, BLUE, CYAN, PURPLE]
    frames = h70cx_basic.effect_frames(
        "combo", colors, speed=50, combine=["flow_1", "rainbow_1", "fade_1", "marquee_3"]
    )
    assert frames == [
        build_frame(
            bytes([0xA3, 0x00, 0x01, 0x03, 0x04, 0xFF, 0x00, 0x32, 0x15])
            + bytes([0xFF, 0x00, 0x00, 0xFF, 0x7F, 0x00, 0xFF, 0xFF, 0x00, 0x00])
        ),
        build_frame(
            bytes([0xA3, 0x01, 0xFF, 0x00, 0x00, 0x00, 0xFF, 0x00, 0xFF, 0xFF, 0x8B, 0x00, 0xFF])
            + bytes([0x08, 0x08, 0x09, 0x09, 0x09, 0x00])
        ),
        build_frame(bytes([0xA3, 0xFF, 0x02, 0x03, 0x05])),
        COMMIT,
    ]


@pytest.mark.parametrize(
    ("effect", "head"),
    [
        ("flow_1", bytes([0x08, 0x09, 85])),
        ("rainbow_1", bytes([0x09, 0x09, 85])),
        ("fade_1", bytes([0x00, 0x02, 50])),
        ("fade_2", bytes([0x00, 0x00, 50])),
        ("fade_3", bytes([0x00, 0x01, 50])),
        ("marquee_1", bytes([0x03, 0x03, 85])),
        ("marquee_2", bytes([0x03, 0x04, 85])),
        ("marquee_3", bytes([0x03, 0x05, 85])),
        ("breathe", bytes([0x02, 0x02, 50])),
    ],
)
def test_defaults_and_ids(effect: str, head: bytes) -> None:
    assert h70cx_basic.effect_record(effect, [RED, GREEN]) == head + bytes([6, *RED, *GREEN])


def test_flow_1_turns_both_ways() -> None:
    assert h70cx_basic.effect_record("flow_1", [RED, GREEN], direction="clockwise")[1] == 0x09
    assert h70cx_basic.effect_record("flow_1", [RED, GREEN], direction="anticlockwise")[1] == 0x0A


def test_speed_edges_go_out_as_is() -> None:
    assert h70cx_basic.effect_record("fade_1", [RED], speed=0)[2] == 0
    assert h70cx_basic.effect_record("fade_1", [RED], speed=100)[2] == 100
    assert h70cx_basic.effect_record("combo", [RED], speed=1, combine=["breathe"])[2] == 1


def test_combo_defaults_and_breathe_pair() -> None:
    record = h70cx_basic.effect_record("combo", [RED], combine=["breathe"])
    assert record == bytes([0xFF, 0x00, 50, 3, *RED, 2, 0x02, 0x02])


def test_every_name_is_a_new_effect() -> None:
    assert not set(h70cx_basic.NAMES) & set(h70cx.TEMPLATES)
    assert set(h70cx_basic.COMBINABLE) <= set(h70cx_basic.EFFECTS)


@pytest.mark.parametrize(
    ("effect", "colors", "kwargs"),
    [
        ("sparkle", [RED], {}),
        ("flow_1", [RED], {}),
        ("marquee_2", [RED], {}),
        ("breathe", [], {}),
        ("breathe", [RED] * 9, {}),
        ("breathe", [(1, 1)], {}),
        ("breathe", [(1, 1, 256)], {}),
        ("breathe", [RED], {"speed": 101}),
        ("breathe", [RED], {"speed": -1}),
        ("breathe", [RED], {"direction": "clockwise"}),
        ("breathe", [RED], {"combine": ["breathe"]}),
        ("flow_1", [RED, GREEN], {"direction": "up"}),
        ("combo", [RED], {}),
        ("combo", [RED], {"combine": []}),
        ("combo", [], {"combine": ["breathe"]}),
        ("combo", [RED], {"combine": ["breathe"], "speed": 0}),
        ("combo", [RED], {"combine": ["breathe"], "direction": "clockwise"}),
        ("combo", [RED], {"combine": ["breathe", "breathe"]}),
        ("combo", [RED], {"combine": ["marquee_1"]}),
        ("combo", [RED], {"combine": ["flow_1", "rainbow_1", "fade_1", "marquee_3", "breathe"]}),
    ],
)
def test_refusals(effect: str, colors: list, kwargs: dict) -> None:
    with pytest.raises(FrameError):
        h70cx_basic.effect_record(effect, colors, **kwargs)
