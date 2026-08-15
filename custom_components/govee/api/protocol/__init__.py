"""Raw Govee device protocol — frames, per-SKU profiles, and a LAN sender.

A self-contained library layer with zero Home Assistant imports. Layering:
``profiles`` (what a SKU can do, and the bytes that do it) -> ``encoders``
(frame layouts named by the table) -> ``frames`` (20-byte frames, XOR checksum,
masks, ``ptReal`` envelope) -> ``packets`` / ``diy`` (multipacket effect uploads)
-> ``codec`` (profile + intent -> frames) -> ``client`` (write-only UDP to 4003).
Only ``client`` is transport-specific; the frames themselves are neutral.
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
    HA_BRIGHTNESS_MAX,
    build_frame,
    frame_to_base64,
    ha_to_percent,
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
    "HA_BRIGHTNESS_MAX",
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
    "ha_to_percent",
    "ptreal_message",
    "resolve_mode",
    "segment_mask",
    "upload_effect",
    "validate_table",
    "xor_checksum",
]
