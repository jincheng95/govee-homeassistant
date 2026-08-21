"""Hardware-verified segment counts (fork feature).

Govee's platform API over-reports how many segments an RGBIC lamp has, which
creates entities that can never light anything and trips the raw-write gate
comparing the entity count against the profile's mask width. The cap is that mask
width — the same number the codec builds masks from. This module owns that rule:
it only ever lowers a count for a profiled SKU, and never raises one.
"""

from __future__ import annotations

import logging
from typing import Any, Final

from .api.protocol import GoveeProtocolError, PROFILES
from .const import SUFFIX_SEGMENT, SUFFIX_SEGMENT_GROUP

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


def pushes_segment_readback(sku: str) -> bool:
    """Whether ``sku``'s cloud status pushes carry §6.2 per-segment readback.

    Args:
        sku: The device model (``H6046``).

    Returns:
        True only for a profiled SKU whose table entry declares it. An
        unprofiled or unmarked SKU is False, so nothing is dispatched for it.
    """
    profile = PROFILES.get(str(sku or "").upper())
    return profile is not None and profile.segment_readback


def manual_segment_count(entry_options: Any, device_id: str) -> int | None:
    """The user-entered segment count for ``device_id``, if any was saved.

    Only consulted by :func:`segment_count` when the profile table has no
    ``verified_segment_count`` for the SKU — the profile always wins when it
    has an answer.

    Args:
        entry_options: The config entry's ``options`` mapping.
        device_id: The device to look up.

    Returns:
        The stored count, or None when nothing was ever saved for it.
    """
    stored = dict(entry_options or {}).get("segment_count_by_device", {})
    value = stored.get(device_id)
    return int(value) if value is not None else None


def segment_count(device: Any, manual_override: int | None = None) -> int:
    """How many segment entities a device should actually get.

    The cloud's advertised count, capped at the profile's verified count when
    the table knows this SKU. Never raised above what the cloud reports.

    For an unprofiled SKU, ``manual_override`` — the user's own answer to
    "how many segments does this device really have" — takes the profile's
    place as the cap, itself never raised above what the cloud reports. It is
    ignored once a profile exists; the profile always wins.

    Args:
        device: A ``GoveeDevice`` (anything with ``sku`` / ``segment_count``).
        manual_override: The stored per-device count from options, or None.

    Returns:
        The number of segments to expose, 0-based indices ``0..n-1``.
    """
    advertised = int(getattr(device, "segment_count", 0) or 0)
    verified = verified_segment_count(str(getattr(device, "sku", "") or ""))
    if verified is None:
        if manual_override is None or manual_override <= 0 or advertised <= manual_override:
            return advertised
        _LOGGER.debug(
            "Govee segments: %s has no profile, using the user-entered count %d",
            getattr(device, "sku", "?"),
            manual_override,
        )
        return manual_override
    if advertised <= verified:
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


def is_segment_group_suffix(suffix: str) -> bool:
    """Whether ``suffix`` names a user-defined segment-group entity.

    Args:
        suffix: The unique_id with the device id stripped
            (``_segment_group_left``).

    Returns:
        True only for ``_segment_group_<name>``. Checked explicitly rather
        than relying on ``is_individual_segment_suffix`` returning False for
        it — the two suffixes share a prefix and must be told apart by their
        own matcher, not by one saying no.
    """
    return suffix.startswith(SUFFIX_SEGMENT_GROUP) and len(suffix) > len(SUFFIX_SEGMENT_GROUP)


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
