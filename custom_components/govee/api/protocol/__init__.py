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
    diy.py        DIY effect payload records (0x50 form) riding the chunker
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

from .client import LAN_COMMAND_PORT, LanUdpClient
from .codec import GoveeCodec
from .diy import (
    DIRECTION_CCW,
    DIRECTION_CW,
    DIRECTION_REVERSE,
    DIRECTIONS,
    MAX_DIY_COLORS,
    MODE_NONE,
    DiyZoneEffect,
    resolve_mode,
)
from .errors import (
    DiyEffectError,
    FrameError,
    GoveeProtocolError,
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
    BLE_MANUFACTURER_LEGACY,
    BLE_MANUFACTURER_MODERN,
    PROFILES,
    UNKNOWN,
    Capability,
    CapabilitySpec,
    DeviceProfile,
    DiyEffectSpec,
    DiyZoneSpec,
    KelvinRange,
    MaxSimultaneousZones,
    SegmentZoneSpec,
    Transport,
    ZoneSpec,
    get_profile,
    validate_table,
)

__all__ = [
    "BLE_MANUFACTURER_LEGACY",
    "BLE_MANUFACTURER_MODERN",
    "DIRECTIONS",
    "DIRECTION_CCW",
    "DIRECTION_CW",
    "DIRECTION_REVERSE",
    "FRAME_LENGTH",
    "LAN_COMMAND_PORT",
    "MAX_DIY_COLORS",
    "MODE_NONE",
    "PROFILES",
    "UNKNOWN",
    "Capability",
    "CapabilitySpec",
    "DeviceProfile",
    "DiyEffectError",
    "DiyEffectSpec",
    "DiyZoneEffect",
    "DiyZoneSpec",
    "FrameError",
    "KelvinRange",
    "LanUdpClient",
    "GoveeCodec",
    "GoveeProtocolError",
    "MaxSimultaneousZones",
    "SegmentMaskError",
    "SegmentZoneSpec",
    "Transport",
    "UnknownEncodingError",
    "UnsupportedCapabilityError",
    "ZoneSpec",
    "build_frame",
    "chunk_effect",
    "commit_frame",
    "frame_to_base64",
    "get_profile",
    "ptreal_message",
    "resolve_mode",
    "segment_mask",
    "upload_effect",
    "validate_table",
    "xor_checksum",
]
