"""Per-LED paint payload and frames (fork feature).

The golden is the frame set sent to an H80C5, which then showed the three
colour blocks, holding still, and reported DIY mode with code 800.
"""

from __future__ import annotations

import pytest

from custom_components.govee.api.protocol import get_profile, paint
from custom_components.govee.api.protocol.errors import FrameError

PROBE = [
    ((255, 0, 0), range(0, 10)),
    ((0, 255, 0), range(10, 20)),
    ((0, 0, 255), range(290, 300)),
]


def test_probe_frames_match_what_the_lamp_accepted() -> None:
    frames = [frame.hex() for frame in paint.paint_frames(PROBE, bulb_count=300)]
    assert frames == [
        "a30001050313006400000003000a00ff00000025",
        "a30100000102030405060708090a0000ff000056",
        "a302000a0b0c0d0e0f101112130a000000ff0a5f",
        "a3030022012301240125012601270128012901a0",
        "a3ff2a012b01000000000000000000000000005d",
        "33050a200300000000000000000000000000001f",
    ]


def test_header_carries_the_base_colour_and_brightness() -> None:
    payload = paint.paint_payload([], bulb_count=300, base_color=(1, 2, 3), base_brightness=40)
    assert payload == bytes([19, 0, 40, 1, 2, 3, 0, 0])


def test_an_empty_paint_still_uploads() -> None:
    frames = paint.paint_frames([], bulb_count=300)
    assert len(frames) == 3
    assert frames[0][:5] == bytes([0xA3, 0x00, 0x01, 0x02, 0x03])
    assert frames[1][:2] == bytes([0xA3, 0xFF])


def test_narrow_and_wide_indexes_split_at_256() -> None:
    payload = paint.paint_payload([((9, 8, 7), [300 - 1, 255, 256, 0])], bulb_count=300)
    assert payload[8:] == bytes([4, 0, 9, 8, 7, 2, 0, 0, 255, 0x00, 0x01, 0x2B, 0x01])


def test_a_later_colour_takes_the_led() -> None:
    payload = paint.paint_payload([((1, 1, 1), [0, 1, 2]), ((2, 2, 2), [1])], bulb_count=10)
    assert payload[6:8] == bytes([2, 0])
    assert payload[8:] == bytes([2, 0, 1, 1, 1, 0, 0, 0, 2]) + bytes([1, 0, 2, 2, 2, 0, 0, 1])


def test_same_colour_groups_merge_and_emptied_groups_vanish() -> None:
    payload = paint.paint_payload(
        [((1, 1, 1), [5]), ((2, 2, 2), [6]), ((1, 1, 1), [4, 6])],
        bulb_count=10,
    )
    assert payload[6:8] == bytes([1, 0])
    assert payload[8:] == bytes([3, 0, 1, 1, 1, 0, 0, 4, 5, 6])


@pytest.mark.parametrize(
    "colors",
    [
        [((256, 0, 0), [0])],
        [((0, 0), [0])],
        [((0, 0, -1), [0])],
        [((0, 0, 0), [300])],
        [((0, 0, 0), [-1])],
        [((0, 0, 0), [True])],
        [((0, 0, 0), ["1"])],
    ],
)
def test_bad_colours_and_indexes(colors: list) -> None:
    with pytest.raises(FrameError):
        paint.paint_payload(colors, bulb_count=300)


@pytest.mark.parametrize("kwargs", [{"base_brightness": 101}, {"base_brightness": -1}, {"base_color": (0, 0, 999)}])
def test_bad_base(kwargs: dict) -> None:
    with pytest.raises(FrameError):
        paint.paint_payload([], bulb_count=300, **kwargs)


def test_h80c5_declares_its_led_count() -> None:
    assert get_profile("H80C5").paint_bulbs == 300
    assert get_profile("H6076").paint_bulbs == 0


def test_an_upload_is_split_into_messages_of_twelve_frames() -> None:
    frames = [bytes([index]) for index in range(30)]
    split = paint.messages(frames)
    assert [len(message) for message in split] == [12, 12, 6]
    assert [frame for message in split for frame in message] == frames
    assert paint.messages([]) == []
