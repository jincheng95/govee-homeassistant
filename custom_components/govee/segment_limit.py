"""Hardware-verified segment counts (fork feature).

Why this exists
---------------
Govee's platform API over-reports how many segments an RGBIC lamp has. Live
evidence 2026-08-14: the devices endpoint advertises **15** segments for the
H6076 (``segmentedColorRgb``'s ``elementRange``) while the lamp physically has
**7**; the H6046 is advertised as 15 and has 10 (2 bars x 5). Only the H60B0
(8) is reported exactly. The over-report costs twice:

* **Phantom entities.** ``Segment 8`` .. ``Segment 15`` on a 7-segment lamp are
  lights that can never light anything.
* **A refused fast path.** The raw-write path compares the entity segment count
  against the profile's mask width and refuses when they disagree — the table
  describing different hardware is the dangerous case, since a mask built for
  the wrong width lights the wrong LEDs. Fed the cloud's 15 against a mask
  width of 7, that gate did exactly what it should and sent every dining-room
  segment paint back over the cloud.

So the fix belongs at entity creation, not at the gate: create as many segment
entities as the hardware has. The count comes from the profile table's mask
width (:attr:`~.api.protocol.profiles.DeviceProfile.verified_segment_count`),
which is the same number the codec builds masks from — one source of truth, no
second constant to drift.

A SKU with no profile keeps the cloud's count untouched: this module only ever
*lowers* a count it has hardware knowledge about, and never raises one (a lamp
reporting fewer segments than the table expects is a different hardware
revision, which the raw-write gate still refuses).
"""

from __future__ import annotations

import logging
from typing import Any, Final

from .api.protocol import GoveeProtocolError, PROFILES
from .const import SUFFIX_SEGMENT

_LOGGER = logging.getLogger(__name__)

# Individual-segment unique ids are ``<device_id>_segment_<index>`` with a
# 0-based index. Kept here so the pruning branch in ``__init__.py`` stays one
# call rather than a second copy of the format.
_SEGMENT_PREFIX: Final = SUFFIX_SEGMENT


def verified_segment_count(sku: str) -> int | None:
    """The physical segment count for ``sku``, or None when not in the table.

    Args:
        sku: The device model (``H6076``).

    Returns:
        The profile's mask width, or None for an unprofiled SKU.
    """
    profile = PROFILES.get(str(sku or "").upper())
    if profile is None:
        return None
    try:
        return profile.verified_segment_count
    except GoveeProtocolError:  # pragma: no cover - a malformed table entry
        return None


def segment_count(device: Any) -> int:
    """How many segment entities a device should actually get.

    The cloud's advertised count, capped at the profile's verified count when
    the table knows this SKU. Never raised above what the cloud reports.

    Args:
        device: A ``GoveeDevice`` (anything with ``sku`` / ``segment_count``).

    Returns:
        The number of segments to expose, 0-based indices ``0..n-1``.
    """
    advertised = int(getattr(device, "segment_count", 0) or 0)
    verified = verified_segment_count(str(getattr(device, "sku", "") or ""))
    if verified is None or advertised <= verified:
        return advertised
    _LOGGER.debug(
        "Govee segments: %s advertises %d segments, hardware has %d — capping",
        getattr(device, "sku", "?"),
        advertised,
        verified,
    )
    return verified


def is_individual_segment_suffix(suffix: str) -> bool:
    """Whether ``suffix`` names an individual segment entity.

    The index must be checked, not just the ``_segment_`` prefix: other suffixes
    share it (``_segment_blending``) and would otherwise be treated as segments.

    Args:
        suffix: The unique_id with the device id stripped (``_segment_11``).

    Returns:
        True only for ``_segment_<digits>``.
    """
    if not suffix.startswith(_SEGMENT_PREFIX):
        return False
    return suffix[len(_SEGMENT_PREFIX) :].isdigit()


def is_phantom_segment_id(suffix: str, sku: str, advertised: int) -> bool:
    """Whether a segment unique-id suffix belongs to a segment that cannot exist.

    Used by the registry cleanup so the extras created before the cap existed
    (or before a profile learned the SKU) are removed on the next reload
    instead of lingering as permanently unavailable entities.

    Args:
        suffix: The unique_id with the device id stripped (``_segment_11``).
        sku: The device's model.
        advertised: The segment count the cloud reports for the device.

    Returns:
        True when the suffix names an individual segment above the cap.
    """
    if not is_individual_segment_suffix(suffix):
        return False
    index_text = suffix[len(_SEGMENT_PREFIX) :]
    verified = verified_segment_count(sku)
    if verified is None:
        return False
    cap = min(advertised, verified) if advertised > 0 else verified
    return int(index_text) >= cap
