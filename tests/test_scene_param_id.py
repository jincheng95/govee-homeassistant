"""The fork's scene paths carry upstream's scene ``paramId`` (#309)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.govee import bulb_actions, scene_catalogue, upstream_access
from custom_components.govee.light import GoveeLightEntity
from custom_components.govee.platforms import scene_adjust
from custom_components.govee.select import GoveeSceneSelectEntity
from tests import test_scene_adjust as rig

DEVICE_ID = "AA:BB:CC:DD:EE:FF:60:B0"
SCENES = [
    {"name": "Aurora-A", "value": {"id": 5, "paramId": 7}},
    {"name": "Sunset", "value": {"id": 6}},
    {"name": "Odd", "value": {"id": 8, "paramId": True}},
    {"name": "Broken", "value": {}},
]


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Aurora-A", ("5", 7)),
        ("Sunset", ("6", None)),
        ("Odd", ("8", None)),
        ("Broken", (None, None)),
        ("Missing", (None, None)),
    ],
)
def test_the_scene_report_lookup_returns_the_param_id(
    monkeypatch: pytest.MonkeyPatch, name: str, expected: tuple[str | None, int | None]
) -> None:
    monkeypatch.setattr(scene_catalogue, "platform_scene_cache", lambda coordinator: {DEVICE_ID: (0.0, SCENES)})

    assert scene_catalogue._platform_scene_id(MagicMock(), DEVICE_ID, name) == expected


def test_the_scene_report_lookup_without_a_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scene_catalogue, "platform_scene_cache", lambda coordinator: None)

    assert scene_catalogue._platform_scene_id(MagicMock(), DEVICE_ID, "Sunset") == (None, None)


def test_clearing_the_shown_scene_clears_its_param_id() -> None:
    state = SimpleNamespace(active_scene="5", active_scene_name="Aurora-A", active_scene_param_id=7)
    coordinator = MagicMock()
    coordinator.get_state.return_value = state

    bulb_actions.clear_optimistic_scene(coordinator, DEVICE_ID)

    assert (state.active_scene, state.active_scene_name, state.active_scene_param_id) == (None, None, None)


async def test_the_scene_select_hands_the_param_id_to_the_local_start(monkeypatch: pytest.MonkeyPatch) -> None:
    start = AsyncMock(return_value=True)
    monkeypatch.setattr(scene_adjust, "async_start_scene_locally", start)
    monkeypatch.setattr(scene_adjust, "scene_select_names", lambda entity: ["Aurora-A", "Sunset"])
    entity = SimpleNamespace(coordinator=object(), _device=object())

    assert await scene_adjust.async_start_scene_from_select(entity, 5, "Aurora-A", 7) is True

    assert start.await_args.args[2:] == (5, "Aurora-A", 7)


# Two variants of one scene id. The catalogue knows only "Rainbow B", and
# "Rainbow C" comes first so a lookup by id alone would pick it.
VARIANTS = [
    {"name": "Rainbow C", "value": {"id": 4101, "paramId": 2}},
    {"name": "Rainbow B", "value": {"id": 4101, "paramId": 1}},
]


def _select(coordinator: Any) -> GoveeSceneSelectEntity:
    entity = GoveeSceneSelectEntity(coordinator, coordinator.devices[rig.DEVICE_ID], VARIANTS)
    entity.hass = MagicMock()
    entity.async_write_ha_state = MagicMock()
    coordinator.async_control_device = AsyncMock(return_value=True)
    return entity


class TestSceneVariants:
    async def test_a_local_start_records_the_variant(self) -> None:
        coordinator = rig._coordinator(scene=None)
        entity = _select(coordinator)

        with rig._routed() as route:
            await entity.async_select_option("Rainbow B")

        route.assert_awaited_once()
        coordinator.async_control_device.assert_not_awaited()
        state = coordinator.state
        assert (state.active_scene, state.active_scene_param_id) == ("4101", 1)
        assert (state.last_scene_id, state.last_scene_param_id) == ("4101", 1)
        assert entity.current_option == "Rainbow B"

    async def test_a_declined_local_start_sends_the_variant_to_the_cloud(self) -> None:
        coordinator = rig._coordinator(scene=None)
        entity = _select(coordinator)

        with patch.object(scene_adjust.raw_router, "async_route_frames", AsyncMock(return_value=False)):
            await entity.async_select_option("Rainbow B")

        command = coordinator.async_control_device.await_args.args[1]
        assert command.get_value() == {"paramId": 1, "id": 4101}

    def test_a_scene_report_resolves_the_variant_and_a_mode_report_clears_it(self) -> None:
        coordinator = rig._coordinator(scene=None)
        coordinator._scene_cache._scene_cache = {rig.DEVICE_ID: (0.0, VARIANTS)}
        device = coordinator.devices[rig.DEVICE_ID]
        state = coordinator.state

        assert scene_catalogue.apply_scene_report(coordinator, device, state, [rig._mode_report(0x04, 0x0C, 0x47)])

        assert (state.active_scene, state.active_scene_param_id) == ("4101", 1)
        assert _select(coordinator).current_option == "Rainbow B"
        light = GoveeLightEntity(coordinator, device, True)
        light._build_effect_mapping(VARIANTS)
        assert light._effect_for_scene("4101", 1) == "Rainbow B"

        assert scene_catalogue.apply_scene_report(coordinator, device, state, [rig._mode_report(0x15, 0x01)])

        assert (state.active_scene, state.active_scene_param_id) == (None, None)

    def test_the_select_names_come_from_three_item_entries(self) -> None:
        entity = _select(rig._coordinator(scene=None))

        assert upstream_access.scene_select_names(entity) == ["Rainbow C", "Rainbow B"]

        entity._scene_map = {"a": (1, "a"), "b": (), "c": "x", "d": (2, 3, "d")}
        assert upstream_access.scene_select_names(entity) == ["a", "d"]
