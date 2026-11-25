"""The fork's scene paths carry upstream's scene ``paramId`` (#309)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.govee import bulb_actions, scene_catalogue
from custom_components.govee.platforms import scene_adjust

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
