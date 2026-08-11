"""Raw Govee LAN control — frames, per-SKU profiles, and a write-only sender.

A self-contained library layer with **zero Home Assistant imports**: it is unit
-testable as plain Python and could be lifted out of this repo unchanged. This
package adds nothing to the integration by itself — no entities, no coordinator
hooks, no config flow. Those live in later layers that call into it.

Why it exists: the cloud/OpenAPI path already gives named scenes, per-segment
colour for the H60B0 ring, zone on/off toggles, music and DreamView. It cannot
express per-zone colour, ripple flow rate, or downlight colour temperature.
Those are LAN-only, and are the reason for this layer.

Layering::

    profiles.py   what a SKU can do, and the byte constants that do it (data)
    encoders.py   frame layouts, named by the table (code)
    frames.py     20-byte frames, XOR checksum, masks, ptReal envelope
    packets.py    0xA3 multipacket chunker (mechanism only, hardware-untested)
    codec.py      profile + intent -> frames
    client.py     write-only UDP send to :4003 (reads stay with devStatus)

The spec is ``govee-lab/PROTOCOL.md``; ``govee-lab/HANDOFF.md`` keeps the
evidence trail.
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
