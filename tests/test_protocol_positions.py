"""The ``0xA4`` MTU packetiser and the bulb position map encoder (fork feature).

Every point list here is synthetic. The 300-point helix only has the shape of a
real map: 300 bulbs inside the 0..500 cube.
"""

from __future__ import annotations

import math

import pytest

from custom_components.govee.api.protocol import get_profile, positions
from custom_components.govee.api.protocol.errors import FrameError, PositionMapError
from custom_components.govee.api.protocol.frames import FRAME_LENGTH, xor_checksum
from custom_components.govee.api.protocol.packets import chunk_mtu


def _helix(count: int = 300) -> list[tuple[int, int, int]]:
    """A synthetic helix of ``count`` bulbs inside the 0..500 cube, y up."""
    return [
        (
            250 + round(200 * math.cos(index / 12)),
            round(index * 500 / (count - 1)),
            250 + round(200 * math.sin(index / 12)),
        )
        for index in range(count)
    ]


class TestChunkMtu:
    def test_two_bulb_map_is_a_first_and_a_last_packet(self) -> None:
        frames = positions.position_map_frames([(1, 2, 3), (256, 500, 1000)], bulb_count=2)

        assert frames == [
            bytes([0xA4, 0x00, 0x00, 0x01, 0x02, 0x00, 0x24, 0x00, 0x02, 0x00])
            + bytes([0x01, 0x00, 0x02, 0x00, 0x03, 0x01, 0x00, 0x01, 0xF4, 0x75]),
            bytes([0xA4, 0xFF, 0xFF, 0x03, 0xE8]) + bytes(14) + bytes([0x4F]),
        ]

    def test_middle_packets_carry_sixteen_bytes_and_a_little_endian_sequence(self) -> None:
        points = [(1, 2, 3), (4, 5, 6), (7, 8, 9), (10, 11, 12), (13, 14, 15)]

        frames = positions.position_map_frames(points, bulb_count=5)

        assert frames == [
            bytes([0xA4, 0x00, 0x00, 0x01, 0x03, 0x00, 0x24, 0x00, 0x05, 0x00])
            + bytes([0x01, 0x00, 0x02, 0x00, 0x03, 0x00, 0x04, 0x00, 0x05, 0x86]),
            bytes([0xA4, 0x01, 0x00, 0x00, 0x06, 0x00, 0x07, 0x00, 0x08, 0x00])
            + bytes([0x09, 0x00, 0x0A, 0x00, 0x0B, 0x00, 0x0C, 0x00, 0x0D, 0xA5]),
            bytes([0xA4, 0xFF, 0xFF, 0x00, 0x0E, 0x00, 0x0F]) + bytes(12) + bytes([0xA5]),
        ]

    def test_a_payload_that_fits_the_first_packet_still_ends_with_an_empty_last_packet(self) -> None:
        frames = chunk_mtu(0x5F, bytes([0x01, 0x02]))

        assert frames[0][:9] == bytes([0xA4, 0x00, 0x00, 0x01, 0x02, 0x00, 0x5F, 0x01, 0x02])
        assert frames[1] == bytes([0xA4, 0xFF, 0xFF]) + bytes(16) + bytes([0xA4 ^ 0xFF ^ 0xFF])

    def test_a_remainder_of_exactly_sixteen_bytes_fills_the_last_packet(self) -> None:
        frames = chunk_mtu(0x24, bytes(range(28)))

        assert len(frames) == 2
        assert frames[1][:19] == bytes([0xA4, 0xFF, 0xFF]) + bytes(range(12, 28))

    def test_rejects_a_command_that_is_not_a_byte(self) -> None:
        with pytest.raises(FrameError):
            chunk_mtu(0x100, b"\x00")

    def test_rejects_a_payload_whose_packet_count_overflows(self) -> None:
        with pytest.raises(FrameError, match="overflows"):
            chunk_mtu(0x24, bytes(12 + 16 * 0xFFFF))


class TestPositionMapFrames:
    def test_three_hundred_bulbs_take_113_packets(self) -> None:
        frames = positions.position_map_frames(_helix(), bulb_count=300)

        assert len(frames) == 113
        assert frames[0][:9] == bytes([0xA4, 0x00, 0x00, 0x01, 0x71, 0x00, 0x24, 0x01, 0x2C])
        assert [frame[1:3] for frame in frames[1:-1]] == [index.to_bytes(2, "little") for index in range(1, 112)]
        assert frames[-1][1:3] == bytes([0xFF, 0xFF])

    def test_every_packet_is_a_checksummed_frame(self) -> None:
        for frame in positions.position_map_frames(_helix(), bulb_count=300):
            assert len(frame) == FRAME_LENGTH
            assert frame[0] == 0xA4
            assert frame[19] == xor_checksum(frame[:19])

    def test_the_packets_reassemble_into_the_payload(self) -> None:
        points = _helix()
        frames = positions.position_map_frames(points, bulb_count=300)

        payload = frames[0][7:19] + b"".join(frame[3:19] for frame in frames[1:-1]) + frames[-1][3:17]

        assert payload == positions.encode_positions(points, bulb_count=300)
        assert len(payload) == 1802

    def test_rejects_the_wrong_point_count(self) -> None:
        with pytest.raises(PositionMapError, match="299 points"):
            positions.position_map_frames(_helix(299), bulb_count=300)

    @pytest.mark.parametrize(
        "bad",
        [(0, 0, 1001), (0, -1, 0), (0, 0.5, 0), (True, 0, 0), (0, "1", 0)],
        ids=["above-max", "negative", "float", "bool", "string"],
    )
    def test_rejects_a_coordinate_that_is_not_an_int_in_range(self, bad) -> None:
        with pytest.raises(PositionMapError, match="point 1"):
            positions.encode_positions([(0, 0, 0), bad], bulb_count=2)

    def test_rejects_a_point_that_is_not_a_triple(self) -> None:
        with pytest.raises(PositionMapError, match="triple"):
            positions.encode_positions([(0, 0, 0), (1, 2)], bulb_count=2)

    def test_accepts_the_largest_coordinate(self) -> None:
        payload = positions.encode_positions([(1000, 1000, 1000)], bulb_count=1)

        assert payload == bytes([0x00, 0x01, 0x03, 0xE8, 0x03, 0xE8, 0x03, 0xE8])


class TestNormalizePositions:
    def test_shifts_each_axis_to_zero_and_scales_uniformly(self) -> None:
        points = [(-10.0, 100.0, 5.0), (90.0, 150.0, 5.0), (40.0, 125.0, 55.0)]

        assert positions.normalize_positions(points) == [(0, 0, 0), (500, 250, 0), (250, 125, 250)]

    def test_a_map_spanning_500_on_its_longest_axis_is_only_shifted(self) -> None:
        points = _helix()

        assert min(axis for point in points for axis in point) == 0
        assert max(axis for point in points for axis in point) == 500
        assert positions.normalize_positions([(x, y, z) for x, y, z in points]) == [
            (x - min(p[0] for p in points), y, z - min(p[2] for p in points)) for x, y, z in points
        ]

    def test_coincident_points_map_to_the_origin(self) -> None:
        assert positions.normalize_positions([(3, 3, 3), (3, 3, 3)]) == [(0, 0, 0), (0, 0, 0)]

    def test_empty_input(self) -> None:
        assert positions.normalize_positions([]) == []

    def test_custom_extent(self) -> None:
        assert positions.normalize_positions([(0, 0, 0), (2, 1, 0)], extent=100) == [(0, 0, 0), (100, 50, 0)]


class TestProfile:
    def test_only_the_h80c5_takes_a_position_map(self) -> None:
        assert get_profile("H80C5").position_map_bulbs == 300
        for sku in ("H60B0", "H6046", "H6076"):
            assert get_profile(sku).position_map_bulbs == 0
