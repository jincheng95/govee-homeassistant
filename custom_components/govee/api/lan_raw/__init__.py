"""Raw Govee device protocol — frames, per-SKU profiles, and a write-only
LAN sender.

A self-contained library layer with **zero Home Assistant imports**: plain
Python, unit-testable on its own. It adds nothing to the integration by itself
— no entities, no coordinator hooks, no config flow. Those live in later
layers that call into it.

Why it exists: the cloud/OpenAPI path cannot express per-zone colour, ripple
flow rate, or downlight colour temperature. The raw 20-byte protocol can, and
this package builds those frames from declared per-SKU knowledge instead of
guesswork.

Layering::

    profiles.py   what a SKU can do, and the byte constants that do it (data)
    encoders.py   frame layouts, named by the table (code)
    frames.py     20-byte frames, XOR checksum, masks, ptReal envelope
    packets.py    0xA3 multipacket chunker (mechanism only, hardware-untested)
    codec.py      profile + intent -> frames
    client.py     write-only UDP send to :4003 (reads stay with devStatus)

Despite the package name, only :mod:`.client` is LAN-specific. The frames are
transport-agnostic — the identical bytes travel over LAN UDP, BLE GATT and
Govee's cloud MQTT — so a future transport reuses everything above the client.

Every byte constant here was derived from captures against the three SKUs in
:mod:`.profiles` and is pinned byte-for-byte by the golden-frame tests;
anything not confirmed on hardware is marked UNKNOWN and refused rather than
guessed.
"""

from __future__ import annotations

from .client import LAN_RAW_COMMAND_PORT, LanRawClient
from .codec import LanRawCodec
from .errors import (
    FrameError,
    LanRawError,
    SegmentMaskError,
    UnknownEncodingError,
    UnsupportedCapabilityError,
)
from .frames import (
    FRAME_LENGTH,
    build_frame,
    frame_to_base64,
    ptreal_message,
    segment_mask,
    xor_checksum,
)
from .packets import chunk_effect, commit_frame, upload_effect
from .profiles import (
    PROFILES,
    UNKNOWN,
    Capability,
    CapabilitySpec,
    DeviceProfile,
    KelvinRange,
    MaxSimultaneousZones,
    ZoneSpec,
    get_profile,
    validate_table,
)

__all__ = [
    "FRAME_LENGTH",
    "LAN_RAW_COMMAND_PORT",
    "PROFILES",
    "UNKNOWN",
    "Capability",
    "CapabilitySpec",
    "DeviceProfile",
    "FrameError",
    "KelvinRange",
    "LanRawClient",
    "LanRawCodec",
    "LanRawError",
    "MaxSimultaneousZones",
    "SegmentMaskError",
    "UnknownEncodingError",
    "UnsupportedCapabilityError",
    "ZoneSpec",
    "build_frame",
    "chunk_effect",
    "commit_frame",
    "frame_to_base64",
    "get_profile",
    "ptreal_message",
    "segment_mask",
    "upload_effect",
    "validate_table",
    "xor_checksum",
]
