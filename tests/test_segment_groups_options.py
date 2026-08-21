"""Options-flow and end-to-end tests for roadmap 1.11 (segment groups).

1. The ``configure_device_mode`` step: mode choice, groups text field
   validation/storage, and the count field's visibility/default/storage.
2. The user-entered count flowing into entity creation and the raw-write
   mask-width gate for an unprofiled SKU, exactly like the profile cap does
   for a profiled one (see ``test_segment_limit.py``).
3. Exclusivity + pruning both directions between ``groups`` and the other
   segment modes, and that the blending switch survives every mode.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from custom_components.govee import segment_limit
from custom_components.govee.config_flow import GoveeOptionsFlow
from custom_components.govee.const import (
    SEGMENT_MODE_GROUPED,
    SEGMENT_MODE_GROUPS,
    SEGMENT_MODE_INDIVIDUAL,
    SUFFIX_GROUPED_SEGMENT,
    SUFFIX_SEGMENT,
    SUFFIX_SEGMENT_BLENDING,
    SUFFIX_SEGMENT_GROUP,
)
from custom_components.govee.models import GoveeCapability, GoveeDevice
from custom_components.govee.models.device import (
    CAPABILITY_ON_OFF,
    CAPABILITY_SEGMENT_COLOR,
    DEVICE_TYPE_LIGHT,
    INSTANCE_POWER,
    INSTANCE_SEGMENT_COLOR,
)

DEVICE_ID = "AA:BB:CC:DD:EE:FF:70:01"
PROFILED_DEVICE_ID = "AA:BB:CC:DD:EE:FF:60:76"  # H6076, verified count 7


def _unprofiled_device(sku: str = "H6199", advertised: int = 12) -> GoveeDevice:
    return GoveeDevice(
        device_id=DEVICE_ID,
        sku=sku,
        name="Mystery Strip",
        device_type=DEVICE_TYPE_LIGHT,
        capabilities=(
            GoveeCapability(type=CAPABILITY_ON_OFF, instance=INSTANCE_POWER, parameters={}),
            GoveeCapability(
                type=CAPABILITY_SEGMENT_COLOR,
                instance=INSTANCE_SEGMENT_COLOR,
                parameters={"fields": [{"fieldName": "segment", "elementRange": {"min": 0, "max": advertised - 1}}]},
            ),
        ),
    )


def _profiled_device() -> GoveeDevice:
    return GoveeDevice(
        device_id=PROFILED_DEVICE_ID,
        sku="H6076",
        name="Dining Lamp",
        device_type=DEVICE_TYPE_LIGHT,
        capabilities=(
            GoveeCapability(type=CAPABILITY_ON_OFF, instance=INSTANCE_POWER, parameters={}),
            GoveeCapability(
                type=CAPABILITY_SEGMENT_COLOR,
                instance=INSTANCE_SEGMENT_COLOR,
                parameters={"fields": [{"fieldName": "segment", "elementRange": {"min": 0, "max": 14}}]},
            ),
        ),
    )


def _flow(device: GoveeDevice) -> tuple[GoveeOptionsFlow, Any]:
    flow = GoveeOptionsFlow()
    flow.hass = MagicMock()
    flow._selected_devices = [device.device_id]
    flow._device_index = 0
    flow._device_modes = {}
    flow._device_groups = {}
    flow._device_counts = {}
    flow._global_options = {}

    entry = MagicMock()
    entry.options = {}
    coordinator = MagicMock()
    coordinator.devices = {device.device_id: device}
    entry.runtime_data = coordinator
    return flow, entry


async def _configure(flow: GoveeOptionsFlow, entry: Any, user_input: dict[str, Any] | None):
    with patch.object(GoveeOptionsFlow, "config_entry", new_callable=PropertyMock, return_value=entry):
        return await flow.async_step_configure_device_mode(user_input)


class TestCountFieldVisibility:
    @pytest.mark.asyncio
    async def test_absent_for_a_profiled_sku(self):
        flow, entry = _flow(_profiled_device())

        result = await _configure(flow, entry, None)

        assert "segment_count" not in result["data_schema"].schema

    @pytest.mark.asyncio
    async def test_present_and_defaulted_to_the_cloud_count_for_an_unprofiled_sku(self):
        flow, entry = _flow(_unprofiled_device(advertised=12))

        result = await _configure(flow, entry, None)

        schema = result["data_schema"].schema
        (marker,) = [key for key in schema if str(key) == "segment_count"]
        assert marker.default() == 12


class TestGroupsStorage:
    @pytest.mark.asyncio
    async def test_valid_groups_are_parsed_and_saved(self):
        flow, entry = _flow(_unprofiled_device(advertised=10))

        result = await _configure(
            flow, entry, {"segment_mode": SEGMENT_MODE_GROUPS, "segment_groups": "Left: 1-5; Right: 6-10"}
        )

        assert result["type"] == "create_entry"
        assert result["data"]["segment_mode_by_device"][DEVICE_ID] == SEGMENT_MODE_GROUPS
        assert result["data"]["segment_groups_by_device"][DEVICE_ID] == {
            "Left": [0, 1, 2, 3, 4],
            "Right": [5, 6, 7, 8, 9],
        }

    @pytest.mark.asyncio
    async def test_an_invalid_definition_re_shows_the_form_with_a_field_error(self):
        flow, entry = _flow(_unprofiled_device(advertised=10))

        result = await _configure(flow, entry, {"segment_mode": SEGMENT_MODE_GROUPS, "segment_groups": ""})

        assert result["type"] == "form"
        assert result["errors"]["segment_groups"] == "segment_groups_zero_groups"
        assert flow._device_index == 0

    @pytest.mark.asyncio
    async def test_an_out_of_range_index_is_checked_against_the_effective_cap(self):
        """An unprofiled SKU validates groups against the user's own count, not the raw cloud number."""
        flow, entry = _flow(_unprofiled_device(advertised=20))

        result = await _configure(
            flow,
            entry,
            {"segment_mode": SEGMENT_MODE_GROUPS, "segment_groups": "Left: 1-8", "segment_count": 5},
        )

        assert result["type"] == "form"
        assert result["errors"]["segment_groups"] == "segment_groups_out_of_range"

    @pytest.mark.asyncio
    async def test_a_non_groups_mode_saves_no_group_definition(self):
        flow, entry = _flow(_unprofiled_device(advertised=10))

        result = await _configure(flow, entry, {"segment_mode": SEGMENT_MODE_INDIVIDUAL, "segment_groups": ""})

        assert result["type"] == "create_entry"
        assert DEVICE_ID not in result["data"]["segment_groups_by_device"]


class TestCountStorage:
    @pytest.mark.asyncio
    async def test_the_entered_count_is_saved_for_an_unprofiled_sku(self):
        flow, entry = _flow(_unprofiled_device(advertised=12))

        result = await _configure(
            flow, entry, {"segment_mode": SEGMENT_MODE_INDIVIDUAL, "segment_groups": "", "segment_count": 8}
        )

        assert result["data"]["segment_count_by_device"][DEVICE_ID] == 8

    @pytest.mark.asyncio
    async def test_nothing_is_saved_for_a_profiled_sku(self):
        flow, entry = _flow(_profiled_device())

        result = await _configure(flow, entry, {"segment_mode": SEGMENT_MODE_INDIVIDUAL, "segment_groups": ""})

        assert PROFILED_DEVICE_ID not in result["data"]["segment_count_by_device"]


class TestCountCapEndToEnd:
    """The user's count is the cap, exactly the way the profile cap works."""

    def test_entity_creation_is_capped_at_the_entered_count(self):
        from custom_components.govee import light as light_mod

        device = _unprofiled_device(advertised=20)
        coordinator = MagicMock()
        coordinator.devices = {device.device_id: device}
        entry = MagicMock()
        entry.runtime_data = coordinator
        entry.options = {
            "segment_mode_by_device": {},
            "segment_count_by_device": {DEVICE_ID: 6},
        }
        added: list = []

        import asyncio

        asyncio.run(light_mod.async_setup_entry(MagicMock(), entry, lambda e: added.extend(e)))

        from custom_components.govee.platforms.segment import GoveeSegmentEntity

        segments = [e for e in added if isinstance(e, GoveeSegmentEntity)]
        assert len(segments) == 6
        assert sorted(e._segment_index for e in segments) == list(range(6))

    def test_the_router_mask_gate_uses_the_same_cap(self):
        """`_segment_count_matches` — the gate `async_segment_color` refuses on
        a mismatch — reads the same manual override entity creation used."""
        from custom_components.govee.api.raw_router import _segment_count

        device = _unprofiled_device(advertised=20)
        coordinator = MagicMock()
        coordinator.config_entry.options = {"segment_count_by_device": {DEVICE_ID: 6}}
        entity = MagicMock()
        entity._device = device
        entity.coordinator = coordinator

        assert _segment_count(entity) == 6

    def test_profile_wins_over_a_manual_override(self):
        from custom_components.govee.api.raw_router import _segment_count

        device = _profiled_device()  # H6076, verified count 7
        coordinator = MagicMock()
        coordinator.config_entry.options = {"segment_count_by_device": {PROFILED_DEVICE_ID: 99}}
        entity = MagicMock()
        entity._device = device
        entity.coordinator = coordinator

        assert _segment_count(entity) == 7


async def _cleanup_removals(segment_mode: str, unique_ids: list[str]) -> set[str]:
    """Run the orphan cleanup over ``unique_ids``; return the pruned ones."""
    from custom_components.govee import _async_cleanup_orphaned_entities

    device = _unprofiled_device()
    coordinator = MagicMock()
    coordinator.devices = {DEVICE_ID: device}
    coordinator.config_entry = MagicMock()

    entry = MagicMock()
    entry.entry_id = "entry_1"
    entry.options = {"segment_mode_by_device": {DEVICE_ID: segment_mode}}

    entries = []
    for unique_id in unique_ids:
        registry_entry = MagicMock()
        registry_entry.unique_id = unique_id
        registry_entry.entity_id = f"light.{unique_id}"
        entries.append(registry_entry)

    removed: set[str] = set()
    entity_registry = MagicMock()
    entity_registry.async_remove = MagicMock(side_effect=lambda entity_id: removed.add(entity_id))

    with patch("custom_components.govee.er") as er_mod, patch("custom_components.govee.dr") as dr_mod:
        er_mod.async_get.return_value = entity_registry
        er_mod.async_entries_for_config_entry.return_value = entries
        dr_mod.async_get.return_value = MagicMock()
        dr_mod.async_entries_for_config_entry.return_value = []
        await _async_cleanup_orphaned_entities(MagicMock(), entry, coordinator)

    return {entity_id[len("light.") :] for entity_id in removed}


class TestExclusivityAndPruning:
    """``groups`` mode prunes the other segment entities and vice versa."""

    @pytest.mark.asyncio
    async def test_groups_mode_prunes_individual_segments(self):
        removed = await _cleanup_removals(
            SEGMENT_MODE_GROUPS,
            [f"{DEVICE_ID}{SUFFIX_SEGMENT}0", f"{DEVICE_ID}{SUFFIX_SEGMENT_GROUP}left"],
        )
        assert removed == {f"{DEVICE_ID}{SUFFIX_SEGMENT}0"}

    @pytest.mark.asyncio
    async def test_groups_mode_prunes_the_all_segments_entity(self):
        removed = await _cleanup_removals(
            SEGMENT_MODE_GROUPS,
            [f"{DEVICE_ID}{SUFFIX_GROUPED_SEGMENT}", f"{DEVICE_ID}{SUFFIX_SEGMENT_GROUP}left"],
        )
        assert removed == {f"{DEVICE_ID}{SUFFIX_GROUPED_SEGMENT}"}

    @pytest.mark.asyncio
    async def test_individual_mode_prunes_the_custom_groups(self):
        removed = await _cleanup_removals(
            SEGMENT_MODE_INDIVIDUAL,
            [f"{DEVICE_ID}{SUFFIX_SEGMENT}0", f"{DEVICE_ID}{SUFFIX_SEGMENT_GROUP}left"],
        )
        assert removed == {f"{DEVICE_ID}{SUFFIX_SEGMENT_GROUP}left"}

    @pytest.mark.asyncio
    async def test_grouped_mode_prunes_the_custom_groups(self):
        removed = await _cleanup_removals(
            SEGMENT_MODE_GROUPED,
            [f"{DEVICE_ID}{SUFFIX_GROUPED_SEGMENT}", f"{DEVICE_ID}{SUFFIX_SEGMENT_GROUP}left"],
        )
        assert removed == {f"{DEVICE_ID}{SUFFIX_SEGMENT_GROUP}left"}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("mode", [SEGMENT_MODE_GROUPS, SEGMENT_MODE_GROUPED, SEGMENT_MODE_INDIVIDUAL])
    async def test_the_blending_switch_survives_every_mode(self, mode):
        removed = await _cleanup_removals(mode, [f"{DEVICE_ID}{SUFFIX_SEGMENT_BLENDING}"])
        assert removed == set()

    def test_a_group_suffix_is_never_read_as_an_individual_segment(self):
        assert segment_limit.is_individual_segment_suffix(f"{SUFFIX_SEGMENT_GROUP}left") is False

    def test_a_group_suffix_is_matched_explicitly(self):
        assert segment_limit.is_segment_group_suffix(f"{SUFFIX_SEGMENT_GROUP}left") is True
        assert segment_limit.is_segment_group_suffix(f"{SUFFIX_SEGMENT}0") is False
        assert segment_limit.is_segment_group_suffix(SUFFIX_SEGMENT_BLENDING) is False
        assert segment_limit.is_segment_group_suffix(SUFFIX_SEGMENT_GROUP) is False  # empty name
