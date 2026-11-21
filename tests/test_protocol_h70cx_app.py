"""App-named composed string-light effects (fork feature).

Each record head is the composer tile's default block. Colours are synthetic.
"""

from __future__ import annotations

import pytest

from custom_components.govee.api.protocol import h70cx
from custom_components.govee.api.protocol.errors import FrameError

FOUR = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255)]
PALETTE = bytes([4, 255, 0, 0, 0, 255, 0, 0, 0, 255, 255, 255, 255])

HEADS = {
    "ribbon_1": bytes([0x0E, 0x01, 0x32, 0x02, 0x07, 0x01, 0x14, 0x02, 0x01]),
    "ribbon_2": bytes([0x0E, 0x01, 0x32, 0x02, 0x07, 0x01, 0x14, 0x02, 0x00]),
    "mountain_peak": bytes([0x0E, 0x02, 0x32, 0x0A, 0x01, 0x01]),
    "candy_crush": bytes([0x10, 0x03, 0x32, 0x00]),
    "smudge": bytes([0x11, 0x01, 0x32]),
    "flexing": bytes([0x0F, 0x01, 0x32, 0x01, 0x02, 0x04]),
    "stripes_1": bytes([0x02, 0x03, 0x05, 0x5E, 0x01, 0x01, 0x00, 0x01]),
    "stripes_2": bytes([0x02, 0x09, 0x05, 0x5E, 0x00, 0x00, 0x00, 0x01]),
    "bounce_1": bytes([0x04, 0x01, 0x5E, 0x01, 0x00]),
    "bounce_2": bytes([0x04, 0x06, 0x5E, 0x01, 0x01]),
    "bounce_3": bytes([0x04, 0x0A, 0x5E, 0x00, 0x00]),
    "whirlpool": bytes([0x05, 0x05, 0x0D, 0x5E, 0x01, 0x05, 0x00, 0x00, 0x00, 0x00]),
    "diffuse_1": bytes([0x06, 0x05, 0x5E, 0x00, 0x46, 0x05, 0x00, 0x00, 0x00, 0x00]),
    "diffuse_2": bytes([0x07, 0x02, 0x5E, 0x01, 0x1E, 0x04, 0x00, 0x00, 0x00, 0x00]),
    "spin_1": bytes([0x08, 0x08, 0x03, 0x5E, 0x00, 0x0A, 0x05, 0x00, 0x00, 0x00, 0x00]),
    "spin_2": bytes([0x08, 0x05, 0x03, 0x5E, 0x00, 0x01, 0x05, 0x00, 0x00, 0x00, 0x00]),
    "spin_3": bytes([0x12, 0x01, 0x32, 0x02]),
    "spiral": bytes([0x09, 0x05, 0x17, 0x5A, 0x00, 0x01, 0x02, 0x00, 0x64, 0x00]),
    "flow_2": bytes([0x01, 0x03, 0x01, 0x64, 0x01, 0x01, 0x00]),
    "flow_3": bytes([0x01, 0x05, 0x01, 0x64, 0x01, 0x00, 0x00]),
    "flow_4": bytes([0x01, 0x08, 0x01, 0x64, 0x00, 0x00, 0x00]),
    "rainbow_2": bytes([0x03, 0x04, 0x05, 0x55]),
}

SPEED_AT = {
    "ribbon_1": 2, "ribbon_2": 2, "mountain_peak": 2, "candy_crush": 2, "smudge": 2, "flexing": 2,
    "stripes_1": 3, "stripes_2": 3, "bounce_1": 2, "bounce_2": 2, "bounce_3": 2, "whirlpool": 3,
    "diffuse_1": 2, "diffuse_2": 2, "spin_1": 3, "spin_2": 3, "spin_3": 2, "spiral": 3,
    "flow_2": 3, "flow_3": 3, "flow_4": 3, "rainbow_2": 3,
}

_STRIPES = {
    "up": 1, "down": 2, "clockwise": 3, "anticlockwise": 4,
    "upper left": 5, "lower left": 6, "upper right": 7, "lower right": 8,
}
_FLOW = {"up": 1, "down": 2, "clockwise": 3, "anticlockwise": 4, "expand vertically": 9, "gather vertically": 10}

# Effect -> (direction byte position, direction name -> byte).
DIRECTIONS: dict[str, tuple[int, dict[str, int]]] = {
    "smudge": (1, {"up": 1, "down": 2}),
    "stripes_1": (2, _STRIPES),
    "stripes_2": (2, _STRIPES),
    "whirlpool": (2, {"gather horizontally": 13, "expand horizontally": 14}),
    "spin_1": (2, {"clockwise": 3, "anticlockwise": 4}),
    "spin_2": (2, {"clockwise": 3, "anticlockwise": 4}),
    "spiral": (2, {"clockwise upward": 23, "anticlockwise upward": 15, "clockwise downward": 24,
                   "anticlockwise downward": 16}),
    "flow_2": (2, _FLOW),
    "flow_3": (2, _FLOW),
    "flow_4": (2, _FLOW),
    "rainbow_2": (2, {"upper left": 5, "lower left": 6, "upper right": 7, "lower right": 8}),
}

MIN_COLORS = {
    "bounce_1": 4, "bounce_2": 4, "bounce_3": 4, "whirlpool": 4, "diffuse_1": 4, "diffuse_2": 4,
    "spin_3": 3,
    "stripes_1": 2, "stripes_2": 2, "spin_1": 2, "spin_2": 2, "flow_2": 2, "flow_3": 2, "flow_4": 2,
    "rainbow_2": 2,
}


def test_the_app_names_are_all_there() -> None:
    assert set(HEADS) <= set(h70cx.TEMPLATES)
    assert set(SPEED_AT) == set(HEADS)


@pytest.mark.parametrize("effect", sorted(HEADS))
def test_default_record(effect: str) -> None:
    assert h70cx.effect_record(effect, FOUR) == HEADS[effect] + PALETTE


@pytest.mark.parametrize("effect", sorted(HEADS))
def test_speed_lands_on_its_byte(effect: str) -> None:
    record = h70cx.effect_record(effect, FOUR, speed=7)
    expected = bytearray(HEADS[effect] + PALETTE)
    expected[SPEED_AT[effect]] = 7
    assert record == bytes(expected)
    assert h70cx.effect_record(effect, FOUR, speed=0)[SPEED_AT[effect]] == 0
    assert h70cx.effect_record(effect, FOUR, speed=100)[SPEED_AT[effect]] == 100


@pytest.mark.parametrize(
    ("effect", "direction", "position", "value"),
    [
        (effect, name, position, value)
        for effect, (position, table) in sorted(DIRECTIONS.items())
        for name, value in table.items()
    ],
)
def test_each_offered_direction(effect: str, direction: str, position: int, value: int) -> None:
    expected = bytearray(HEADS[effect] + PALETTE)
    expected[position] = value
    assert h70cx.effect_record(effect, FOUR, direction=direction) == bytes(expected)


@pytest.mark.parametrize("effect", sorted(HEADS))
def test_directions_not_offered_are_refused(effect: str) -> None:
    offered = DIRECTIONS.get(effect, (0, {}))[1]
    assert h70cx.has_direction(effect) is bool(offered)
    for name in h70cx.DIRECTION_IDS:
        if name in offered:
            continue
        with pytest.raises(FrameError):
            h70cx.effect_record(effect, FOUR, direction=name)


def test_a_refused_direction_names_the_offered_ones() -> None:
    with pytest.raises(FrameError, match="smudge takes only up, down, not 'clockwise'"):
        h70cx.effect_record("smudge", FOUR, direction="clockwise")
    with pytest.raises(FrameError, match="bounce_1 has no direction"):
        h70cx.effect_record("bounce_1", FOUR, direction="up")


@pytest.mark.parametrize("effect", sorted(HEADS))
def test_colour_range(effect: str) -> None:
    low = MIN_COLORS.get(effect, 1)
    assert h70cx.TEMPLATES[effect].min_colors == low
    h70cx.effect_record(effect, [(1, 2, 3)] * low)
    h70cx.effect_record(effect, [(1, 2, 3)] * 8)
    with pytest.raises(FrameError):
        h70cx.effect_record(effect, [(1, 2, 3)] * 9)
    if low > 1:
        with pytest.raises(FrameError):
            h70cx.effect_record(effect, [(1, 2, 3)] * (low - 1))


@pytest.mark.parametrize(("shape", "value"), [("round", 0), ("square", 1), ("rhomb", 2)])
def test_flexing_shape(shape: str, value: int) -> None:
    record = h70cx.effect_record("flexing", FOUR, shape=shape)
    assert record == bytes([0x0F, 0x01, 0x32, 0x01, value, 0x04]) + PALETTE


@pytest.mark.parametrize(
    ("center", "value"),
    [
        ("upper_left", 0),
        ("left_center", 1),
        ("bottom_left", 2),
        ("top_center", 3),
        ("center", 4),
        ("bottom_center", 5),
        ("upper_right", 6),
        ("right_center", 7),
        ("bottom_right", 8),
    ],
)
def test_flexing_center(center: str, value: int) -> None:
    record = h70cx.effect_record("flexing", FOUR, center=center)
    assert record == bytes([0x0F, 0x01, 0x32, 0x01, 0x02, value]) + PALETTE


def test_flexing_takes_all_three_at_once() -> None:
    record = h70cx.effect_record("flexing", FOUR, speed=9, shape="square", center="bottom_right")
    assert record == bytes([0x0F, 0x01, 0x09, 0x01, 0x01, 0x08]) + PALETTE


@pytest.mark.parametrize(
    ("effect", "kwargs", "reason"),
    [
        ("smudge", {"shape": "round"}, "smudge has no shape"),
        ("rainbow", {"center": "center"}, "rainbow has no center"),
        ("flexing", {"shape": "oval"}, "unknown shape 'oval'"),
        ("flexing", {"center": "middle"}, "unknown center 'middle'"),
        ("flexing", {"direction": "up"}, "flexing has no direction"),
    ],
)
def test_shape_and_center_refusals(effect: str, kwargs: dict, reason: str) -> None:
    with pytest.raises(FrameError) as err:
        h70cx.effect_record(effect, FOUR, **kwargs)
    assert str(err.value).startswith(reason)


def test_frames_carry_shape_and_center() -> None:
    frames = h70cx.effect_frames("flexing", FOUR, shape="round", center="upper_left")
    record = h70cx.effect_record("flexing", FOUR, shape="round", center="upper_left")
    assert frames[0][5:7] == bytes([len(record), 0])
    assert frames[0][7:19] == record[:12]
