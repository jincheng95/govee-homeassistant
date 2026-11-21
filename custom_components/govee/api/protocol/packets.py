"""The ``0xA3`` multipacket chunker.

Effects (DIY and named scenes) carry more parameters than fit in a 20-byte
frame, so the payload is chunked across ``0xA3`` frames and then activated by a
separate commit frame::

    pkt 0     a3 00 | <lead> <pktCount> <marker> | first 14 data bytes | xor
    pkt N     a3 <seq 01,02,...>                 | 17 data bytes       | xor
    last      a3 ff                              | remaining data      | xor
    commit    33 05 <submode> <idxLo> <idxHi> [marker]   submode 0x0a for DIY

The H60B0 takes the DIY commit with or without the trailing marker byte.

Packet 0 spends 3 of its 17 data bytes on that header, so effect data starts at
its 4th data byte. Payloads come from :mod:`.diy`.

The ``0xA4`` MTU multipacket (:func:`chunk_mtu`) carries a command byte and
16-bit packet count and sequence numbers instead.
"""

from __future__ import annotations

from collections.abc import Sequence

from .encoders import mode_select
from .errors import FrameError
from .frames import BODY_LENGTH, PRO_TYPE_MTU, PRO_TYPE_MULTIPACKET, build_frame

CHUNK_DATA_BYTES = BODY_LENGTH - 2
"""Data bytes per continuation packet: 19 body bytes minus proType and sequence."""

HEADER_BYTES = 3
"""``<lead> <pktCount> <marker>`` — packet 0 only."""

FIRST_CHUNK_DATA_BYTES = CHUNK_DATA_BYTES - HEADER_BYTES

SEQUENCE_TERMINATOR = 0xFF
"""The final chunk's sequence byte, whatever its ordinal would have been."""

DEFAULT_HEADER_LEAD = 0x01
"""Constant first header byte in every capture we have."""

DIY_COMMIT_SUB_MODE = 0x0A


def packet_count(data_length: int) -> int:
    """How many ``0xA3`` packets ``data_length`` effect bytes need."""
    if data_length < 0:
        raise FrameError("negative effect payload length")
    if data_length <= FIRST_CHUNK_DATA_BYTES:
        return 1
    remainder = data_length - FIRST_CHUNK_DATA_BYTES
    return 1 + (remainder + CHUNK_DATA_BYTES - 1) // CHUNK_DATA_BYTES


def chunk_effect(
    data: bytes | bytearray,
    *,
    marker: int,
    lead: int = DEFAULT_HEADER_LEAD,
) -> list[bytes]:
    """Split an effect payload into ``0xA3`` frames.

    ``data`` is the effect payload proper — it must NOT include the 3 header
    bytes, which this function generates. ``marker`` is the effect id echoed by
    the commit frame (``0x58`` in the captured H60B0 DIY).
    """
    total = packet_count(len(data))
    if total > SEQUENCE_TERMINATOR:
        raise FrameError(f"effect payload needs {total} packets, sequence byte overflows")

    frames: list[bytes] = []
    header = bytes([lead & 0xFF, total & 0xFF, marker & 0xFF])
    first = bytes(data[:FIRST_CHUNK_DATA_BYTES])
    rest = bytes(data[FIRST_CHUNK_DATA_BYTES:])

    chunks: list[bytes] = [header + first]
    for offset in range(0, len(rest), CHUNK_DATA_BYTES):
        chunks.append(rest[offset : offset + CHUNK_DATA_BYTES])

    for index, chunk in enumerate(chunks):
        sequence = SEQUENCE_TERMINATOR if index == len(chunks) - 1 else index
        frames.append(build_frame(bytes([PRO_TYPE_MULTIPACKET, sequence]) + chunk))
    return frames


def commit_frame(
    *,
    sub_mode: int = DIY_COMMIT_SUB_MODE,
    index: int = 0,
    marker: int | None = None,
) -> bytes:
    """The ``33 05 <submode> <idxLo> <idxHi> [marker]`` activation frame.

    ``marker=None`` leaves the marker byte off.

    Same layout as a mode select — a commit *is* a mode select naming the
    just-uploaded effect — so it delegates to :func:`~.encoders.mode_select`.
    """
    return mode_select({}, sub_mode=sub_mode & 0xFF, index=index, marker=marker)


def upload_effect(
    data: bytes | bytearray,
    *,
    marker: int,
    sub_mode: int = DIY_COMMIT_SUB_MODE,
    index: int = 0,
    lead: int = DEFAULT_HEADER_LEAD,
) -> Sequence[bytes]:
    """Chunks + commit, in the order they must be sent."""
    return [*chunk_effect(data, marker=marker, lead=lead), commit_frame(sub_mode=sub_mode, index=index, marker=marker)]


MTU_FIRST_DATA_BYTES = BODY_LENGTH - 7
"""Data bytes in the first ``0xA4`` packet, after ``a4 00 00 01 <n16> <command>``."""

MTU_DATA_BYTES = BODY_LENGTH - 3
"""Data bytes in every later ``0xA4`` packet, after ``a4 <seq16>``."""

MTU_LAST_SEQUENCE = 0xFFFF
"""The last packet's sequence number."""

MTU_FIRST_LEAD = 0x01
"""Constant byte after the first packet's zero sequence."""


def chunk_mtu(command: int, data: bytes | bytearray) -> list[bytes]:
    """Split a payload into ``0xA4`` MTU multipacket frames for ``command``.

    The packet count ``n`` and the sequence numbers are u16 little-endian::

        first   a4 00 00 01 <n> <command> | first 12 data bytes  | xor
        middle  a4 <seq 1, 2, ...>        | 16 data bytes        | xor
        last    a4 ff ff                  | remaining data       | xor

    ``n`` counts every packet, first and last included, so a message is at
    least two packets. The last packet carries 1-16 bytes, or none when the
    payload fits in the first packet.

    Args:
        command: The command byte the payload belongs to.
        data: The command payload.

    Returns:
        The frames, in send order.

    Raises:
        FrameError: If ``command`` is not a byte or the payload needs more
            packets than the 16-bit count can hold.
    """
    if not 0 <= command <= 0xFF:
        raise FrameError(f"MTU command is not a byte: {command}")
    rest = bytes(data[MTU_FIRST_DATA_BYTES:])
    chunks = [rest[offset : offset + MTU_DATA_BYTES] for offset in range(0, len(rest), MTU_DATA_BYTES)] or [b""]
    total = len(chunks) + 1
    if total > MTU_LAST_SEQUENCE:
        raise FrameError(f"MTU payload needs {total} packets, the packet count overflows")

    header = bytes([PRO_TYPE_MTU, 0x00, 0x00, MTU_FIRST_LEAD]) + total.to_bytes(2, "little") + bytes([command])
    frames = [build_frame(header + bytes(data[:MTU_FIRST_DATA_BYTES]))]
    for sequence, chunk in enumerate(chunks[:-1], start=1):
        frames.append(build_frame(bytes([PRO_TYPE_MTU]) + sequence.to_bytes(2, "little") + chunk))
    frames.append(build_frame(bytes([PRO_TYPE_MTU]) + MTU_LAST_SEQUENCE.to_bytes(2, "little") + chunks[-1]))
    return frames
