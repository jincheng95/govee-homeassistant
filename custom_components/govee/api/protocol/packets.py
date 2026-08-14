"""The ``0xA3`` multipacket chunker.

Effects (DIY and named scenes) carry more parameters than fit in a 20-byte
frame, so the payload is chunked across ``0xA3`` frames and then activated by a
separate commit frame::

    pkt 0     a3 00 | <lead> <pktCount> <marker> | first 14 data bytes | xor
    pkt N     a3 <seq 01,02,...>                 | 17 data bytes       | xor
    last      a3 ff                              | remaining data      | xor
    commit    33 05 <submode> <idxLo> <idxHi> [marker]   submode 0x0a for DIY

Packet 0 spends 3 of its 17 data bytes on that header, so effect data starts
at the 4th data byte of packet 0.

The effect payloads it carries are built by :mod:`.diy` and assembled by
:meth:`~.codec.GoveeCodec.diy_effect`. Chunk boundaries, the ``0xff``
terminator and the commit frame are pinned by ``tests/test_protocol.py``.
"""

from __future__ import annotations

from collections.abc import Sequence

from .encoders import mode_select
from .errors import FrameError
from .frames import BODY_LENGTH, PRO_TYPE_MULTIPACKET, build_frame

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
