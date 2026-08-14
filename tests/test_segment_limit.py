"""Tests for the hardware-verified segment cap (fork feature).

Govee's platform API over-reports segments on the RGBIC family: the live
devices endpoint advertises 15 for the H6076 (7 real, verified 2026-08-14) and
15 for the H6046 (10 real). The integration created one light entity per
advertised segment, so eight of the dining lamp's fifteen segment entities were
phantoms — and the raw-write path, which refuses when the entity count
disagrees with the profile's mask width, correctly sent every paint back over
the cloud.

The cap is the profile table's mask width, i.e. the same number the codec
builds masks from. These tests pin: the counts themselves, the cap at entity
creation, the registry pruning of the extras, and the untouched behaviour of a
SKU the table has never seen.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from custom_components.govee import segment_limit
from custom_components.govee.api.protocol import get_profile
from custom_components.govee.const import SUFFIX_SEGMENT
from custom_components.govee.models import GoveeCapability, GoveeDevice
from custom_components.govee.models.device import (
    CAPABILITY_COLOR_SETTING,
    CAPABILITY_ON_OFF,
    CAPABILITY_RANGE,
    CAPABILITY_SEGMENT_COLOR,
    DEVICE_TYPE_LIGHT,
    INSTANCE_BRIGHTNESS,
    INSTANCE_COLOR_RGB,
    INSTANCE_POWER,
    INSTANCE_SEGMENT_COLOR,
)
from custom_components.govee.platforms.grouped_segment import GoveeGroupedSegmentEntity
from custom_components.govee.platforms.segment import GoveeSegmentEntity

DEVICE_ID = "AA:BB:CC:DD:EE:FF:60:76"

# What the cloud advertises for both mask-addressed SKUs, and what the hardware
# actually has (reference/govee-device-protocol.md §3.2).
CLOUD_ADVERTISED = 15
H6076_SEGMENTS = 7
H6046_SEGMENTS = 10
H60B0_SEGMENTS = 8


def _device(sku: str = "H6076", segments: int = CLOUD_ADVERTISED) -> GoveeDevice:
    """A light device advertising ``segments`` segments, cloud-style."""
    return GoveeDevice(
        device_id=DEVICE_ID,
        sku=sku,
        name="Dining Lamp",
        device_type=DEVICE_TYPE_LIGHT,
        capabilities=(
            GoveeCapability(type=CAPABILITY_ON_OFF, instance=INSTANCE_POWER, parameters={}),
            GoveeCapability(type=CAPABILITY_RANGE, instance=INSTANCE_BRIGHTNESS, parameters={}),
            GoveeCapability(type=CAPABILITY_COLOR_SETTING, instance=INSTANCE_COLOR_RGB, parameters={}),
            GoveeCapability(
                type=CAPABILITY_SEGMENT_COLOR,
                instance=INSTANCE_SEGMENT_COLOR,
                parameters={"fields": [{"fieldName": "segment", "elementRange": {"min": 0, "max": segments - 1}}]},
            ),
        ),
    )


def _entry(coordinator: Any) -> Any:
    entry = MagicMock()
    entry.runtime_data = coordinator
    entry.options = {"segment_mode_by_device": {}}
    return entry


def _coordinator(device: GoveeDevice) -> Any:
    coordinator = MagicMock()
    coordinator._govee_zone_state_registry = None
    coordinator.devices = {device.device_id: device}
    coordinator.config_entry.options = {}
    coordinator.last_update_success = True
    return coordinator


# ==============================================================================
# 1. The number itself
# ==============================================================================


class TestVerifiedCount:
    """The cap is the profile's mask width, not a second constant."""

    @pytest.mark.parametrize(
        ("sku", "expected"),
        [("H6076", H6076_SEGMENTS), ("H6046", H6046_SEGMENTS), ("H60B0", H60B0_SEGMENTS)],
    )
    def test_every_profiled_sku_states_its_real_count(self, sku: str, expected: int):
        assert segment_limit.verified_segment_count(sku) == expected

    def test_the_count_is_the_mask_width_the_codec_uses(self):
        """One source of truth: whichever zone owns the segments declares it."""
        assert get_profile("H6076").verified_segment_count == get_profile("H6076").zone("segments").segments
        # ...including the SKU whose segments live on a zone (H60B0 -> ring).
        h60b0 = get_profile("H60B0")
        assert h60b0.verified_segment_count == h60b0.zone(h60b0.segment_zone_key or "").segments

    def test_an_unprofiled_sku_has_no_verified_count(self):
        assert segment_limit.verified_segment_count("H6199") is None
        assert segment_limit.verified_segment_count("") is None

    def test_the_cloud_count_is_capped_not_replaced(self):
        assert segment_limit.segment_count(_device(segments=CLOUD_ADVERTISED)) == H6076_SEGMENTS
        # A lower advertised count is never raised to the table's width.
        assert segment_limit.segment_count(_device(segments=4)) == 4

    def test_an_unprofiled_sku_keeps_the_cloud_count(self):
        assert segment_limit.segment_count(_device(sku="H6199", segments=CLOUD_ADVERTISED)) == CLOUD_ADVERTISED


# ==============================================================================
# 2. Entity creation
# ==============================================================================


class TestEntityCount:
    """Only as many segment entities as the lamp has segments."""

    @pytest.mark.asyncio
    async def test_segment_entities_are_capped_at_the_hardware_count(self):
        from custom_components.govee import light as light_mod

        device = _device()
        coordinator = _coordinator(device)
        added: list = []

        await light_mod.async_setup_entry(MagicMock(), _entry(coordinator), lambda e: added.extend(e))

        segments = [e for e in added if isinstance(e, GoveeSegmentEntity)]
        assert len(segments) == H6076_SEGMENTS
        assert sorted(e._segment_index for e in segments) == list(range(H6076_SEGMENTS))

    @pytest.mark.asyncio
    async def test_an_unprofiled_sku_keeps_every_advertised_segment(self):
        from custom_components.govee import light as light_mod

        device = _device(sku="H6199")
        coordinator = _coordinator(device)
        added: list = []

        await light_mod.async_setup_entry(MagicMock(), _entry(coordinator), lambda e: added.extend(e))

        segments = [e for e in added if isinstance(e, GoveeSegmentEntity)]
        assert len(segments) == CLOUD_ADVERTISED

    def test_the_grouped_entity_masks_only_real_segments(self):
        device = _device()
        entity = GoveeGroupedSegmentEntity(_coordinator(device), device)

        assert entity._segment_indices == tuple(range(H6076_SEGMENTS))


# ==============================================================================
# 3. Pruning the phantoms already in the registry
# ==============================================================================


class TestPruning:
    """Entities created before the cap are removed on the next reload."""

    def test_a_segment_above_the_hardware_count_is_a_phantom(self):
        assert segment_limit.is_phantom_segment_id(f"{SUFFIX_SEGMENT}7", "H6076", CLOUD_ADVERTISED) is True
        assert segment_limit.is_phantom_segment_id(f"{SUFFIX_SEGMENT}14", "H6076", CLOUD_ADVERTISED) is True

    def test_a_real_segment_is_kept(self):
        assert segment_limit.is_phantom_segment_id(f"{SUFFIX_SEGMENT}0", "H6076", CLOUD_ADVERTISED) is False
        assert segment_limit.is_phantom_segment_id(f"{SUFFIX_SEGMENT}6", "H6076", CLOUD_ADVERTISED) is False

    def test_an_unprofiled_sku_prunes_nothing(self):
        assert segment_limit.is_phantom_segment_id(f"{SUFFIX_SEGMENT}14", "H6199", CLOUD_ADVERTISED) is False

    def test_other_suffixes_are_never_touched(self):
        assert segment_limit.is_phantom_segment_id("_grouped_segments", "H6076", CLOUD_ADVERTISED) is False
        assert segment_limit.is_phantom_segment_id(f"{SUFFIX_SEGMENT}blending", "H6076", CLOUD_ADVERTISED) is False

    def test_the_cap_follows_the_lower_of_the_two_counts(self):
        """A device reporting fewer than the table gets the device's number."""
        assert segment_limit.is_phantom_segment_id(f"{SUFFIX_SEGMENT}5", "H6076", 4) is True

    def test_cleanup_marks_the_phantoms_for_removal(self):
        from custom_components.govee import _is_phantom_segment

        device = _device()
        coordinator = _coordinator(device)

        assert _is_phantom_segment(coordinator, DEVICE_ID, f"{SUFFIX_SEGMENT}9") is True
        assert _is_phantom_segment(coordinator, DEVICE_ID, f"{SUFFIX_SEGMENT}3") is False
        assert _is_phantom_segment(coordinator, "unknown-device", f"{SUFFIX_SEGMENT}9") is False
