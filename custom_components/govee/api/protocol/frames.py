"""20-byte Govee protocol frames, the ``ptReal`` envelope and base64 packing.

Everything in this module is pure: bytes in, bytes out, no sockets, no clock,
no Home Assistant.

A frame is exactly 20 bytes::

    [0]     proType    0x33 write/set, 0xAA query/read, 0xA3 multipacket chunk
    [1]     commandType
    [2:19]  payload, zero-padded
    [19]    checksum = XOR of bytes[0..18]

The frame itself is transport-agnostic: the identical 20 bytes travel over LAN
UDP (base64 inside the ``ptReal`` JSON envelope), over BLE GATT (wrapped in
AES-128-ECB + RC4 under a session key), and over Govee's cloud MQTT (also as
``ptReal``). Only the envelope helpers at the bottom of this module are
LAN/cloud-specific.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from .errors import FrameError, SegmentMaskError

FRAME_LENGTH = 20
"""Total frame length on the wire, checksum included."""

BODY_LENGTH = 19
"""Bytes available to the caller before the checksum."""

PRO_TYPE_WRITE = 0x33
PRO_TYPE_QUERY = 0xAA
PRO_TYPE_MULTIPACKET = 0xA3

MASK_BYTES = 2
"""Segment masks are always 2 bytes, LSB-first: mask0 = segs 0-7, mask1 = 8-15."""

MAX_SEGMENTS = MASK_BYTES * 8

HA_BRIGHTNESS_MAX = 255
"""Home Assistant's brightness scale. Levels on the wire are 0-100."""


def xor_checksum(data: Iterable[int]) -> int:
    """XOR every byte together — the frame's trailing check byte."""
    checksum = 0
    for byte in data:
        checksum ^= byte
    return checksum


def build_frame(body: Sequence[int] | bytes | bytearray) -> bytes:
    """Zero-pad ``body`` to 19 bytes and append the XOR checksum."""
    if len(body) > BODY_LENGTH:
        raise FrameError(f"frame body too long: {len(body)} > {BODY_LENGTH}")
    try:
        padded = bytes(body) + bytes(BODY_LENGTH - len(body))
    except ValueError as err:  # a byte outside 0..255
        raise FrameError(f"frame body contains a non-byte value: {err}") from err
    return padded + bytes([xor_checksum(padded)])


def frame_to_base64(frame: bytes) -> str:
    """Base64-encode one frame for the ``ptReal`` envelope."""
    if len(frame) != FRAME_LENGTH:
        raise FrameError(f"frame must be {FRAME_LENGTH} bytes, got {len(frame)}")
    return base64.b64encode(frame).decode("ascii")


def ptreal_message(frames: Iterable[bytes]) -> dict[str, Any]:
    """Wrap frames in the ``ptReal`` envelope.

    The ``command`` array may hold several frames; the device applies them in
    order. That is how the 0xA3 multipacket effect upload is sent.
    """
    commands = [frame_to_base64(frame) for frame in frames]
    if not commands:
        raise FrameError("ptReal envelope needs at least one frame")
    return {"msg": {"cmd": "ptReal", "data": {"command": commands}}}


def encode_message(message: Mapping[str, Any]) -> bytes:
    """Serialise a LAN JSON message to the bytes that go on the wire."""
    return json.dumps(message, separators=(",", ":")).encode("utf-8")


def kelvin_bytes(kelvin: int) -> bytes:
    """Kelvin is 2 bytes, BIG-endian. 2700 K -> ``0a 8c``."""
    if not 0 <= kelvin <= 0xFFFF:
        raise FrameError(f"kelvin out of 16-bit range: {kelvin}")
    return kelvin.to_bytes(2, "big")


def segment_mask(segments: Iterable[int], *, segment_count: int) -> bytes:
    """Pack 0-based segment indices into 2 LSB-first mask bytes.

    ``mask0`` bit *i* selects segment *i* (0-7); ``mask1`` bit *i* selects
    segment *i+8*. Refuses an empty selection: an all-zero mask is accepted by
    the firmware and silently does nothing.
    """
    if segment_count > MAX_SEGMENTS:
        raise SegmentMaskError(f"segment_count {segment_count} exceeds {MAX_SEGMENTS}")
    mask = 0
    for index in segments:
        if not 0 <= index < segment_count:
            raise SegmentMaskError(f"segment {index} out of range 0..{segment_count - 1}")
        mask |= 1 << index
    if mask == 0:
        raise SegmentMaskError("empty segment mask — the device would silently ignore the frame")
    return mask.to_bytes(MASK_BYTES, "little")


def write_at(body: bytearray, offset: int, data: bytes) -> None:
    """Place ``data`` at a fixed byte offset inside a frame body.

    Used for segment masks, whose offset differs per attribute (byte 10 for
    colour, byte 6 for brightness on the H60B0) and is therefore table data,
    never a hardcoded literal in an encoder.
    """
    end = offset + len(data)
    if end > BODY_LENGTH:
        raise FrameError(f"write at offset {offset} would overflow the frame body")
    if len(body) < end:
        body.extend(bytes(end - len(body)))
    body[offset:end] = data


def rgb_bytes(rgb: tuple[int, int, int]) -> bytes:
    """Validate and pack an ``(r, g, b)`` triple."""
    if len(rgb) != 3 or any(not 0 <= component <= 255 for component in rgb):
        raise FrameError(f"invalid RGB triple: {rgb!r}")
    return bytes(rgb)


def clamp_percent(value: int) -> int:
    """Clamp a 0-100 percentage (brightness, flow rate)."""
    return max(0, min(100, int(value)))


def ha_to_percent(ha_brightness: int) -> int:
    """HA's 0-255 brightness as the 0-100 the frames carry.

    Never rounds a lit entity down to 0: 0 is "off" on the wire, which is a
    power intent, not a level. The inverse is
    :func:`..segment_readback.level_to_ha_brightness`.
    """
    return max(1, min(100, round(int(ha_brightness) * 100 / HA_BRIGHTNESS_MAX)))
