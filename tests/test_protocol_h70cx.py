"""Composed string-light effects (fork feature).

The golden is the frame set sent to an H80C5, after which the lamp reported
DIY mode with code 714.
"""

from __future__ import annotations

import pytest

from custom_components.govee.api.protocol import h70cx
from custom_components.govee.api.protocol.errors import FrameError

RED_GREEN = [(255, 0, 0), (0, 255, 0)]


def test_probe_frames_match_what_the_lamp_accepted() -> None:
    frames = [frame.hex() for frame in h70cx.effect_frames("rainbow", RED_GREEN)]
    assert frames == [
        "a3000102580b000304055c02ff000000ff0000af",
        "a3ff00000000000000000000000000000000005c",
        "33050aca020000000000000000000000000000f4",
    ]


def test_record_is_effect_params_count_colours() -> None:
    record = h70cx.effect_record("flame", RED_GREEN)
    assert record == bytes([4]) + bytes.fromhex("0a5e0100") + bytes([2, 255, 0, 0, 0, 255, 0])


@pytest.mark.parametrize(
    ("effect", "speed_at", "direction_at", "direction"),
    [
        ("flow", 3, 2, "down"),
        ("layered", 3, 2, "down"),
        ("rainbow", 3, 2, "down"),
        ("flame", 2, None, None),
        ("vortex", 3, 2, "down"),
        ("diffuse", 2, None, None),
        ("concentric", 2, None, None),
        ("rotate", 3, 2, "down"),
        ("starlight", 6, None, None),
        ("wave", 2, None, None),
        ("blend", 2, 1, "up"),
    ],
)
def test_speed_and_direction_land_on_their_bytes(
    effect: str, speed_at: int, direction_at: int | None, direction: str | None
) -> None:
    stock = h70cx.effect_record(effect, RED_GREEN)
    record = h70cx.effect_record(effect, RED_GREEN, speed=7, direction=direction)

    expected = {speed_at: 7}
    if direction is not None:
        expected[direction_at] = h70cx.DIRECTION_IDS[direction]
    changed = {index: record[index] for index in range(len(stock)) if stock[index] != record[index]}
    assert changed == expected
    assert h70cx.has_direction(effect) is (direction_at is not None)


GENERIC = ("flow", "layered", "rainbow", "flame", "vortex", "diffuse", "concentric", "rotate", "starlight", "wave", "blend")


def test_every_generic_template_is_listed_once_per_effect_id() -> None:
    ids = [h70cx.TEMPLATES[name].effect for name in GENERIC]
    assert len(ids) == len(set(ids))
    assert all(h70cx.TEMPLATES[name].directions is None for name in GENERIC if name != "blend")


def test_direction_names_are_lower_case_and_complete() -> None:
    assert h70cx.DIRECTION_IDS["clockwise upward"] == 23
    assert all(name == name.lower() for name in h70cx.DIRECTION_IDS)
    assert len(h70cx.DIRECTION_IDS) == 16


@pytest.mark.parametrize(
    ("effect", "colors", "kwargs"),
    [
        ("sparkle", RED_GREEN, {}),
        ("rainbow", [(255, 0, 0)], {}),
        ("rainbow", [(1, 1, 1)] * 9, {}),
        ("rainbow", [(1, 1), (2, 2, 2)], {}),
        ("rainbow", [(1, 1, 256), (2, 2, 2)], {}),
        ("rainbow", RED_GREEN, {"speed": 101}),
        ("rainbow", RED_GREEN, {"speed": -1}),
        ("rainbow", RED_GREEN, {"direction": "sideways"}),
        ("flame", RED_GREEN, {"direction": "up"}),
    ],
)
def test_refusals(effect: str, colors: list, kwargs: dict) -> None:
    with pytest.raises(FrameError):
        h70cx.effect_record(effect, colors, **kwargs)


def test_starlight_takes_a_single_colour() -> None:
    assert h70cx.effect_record("starlight", [(9, 9, 9)])[-4:] == bytes([1, 9, 9, 9])
