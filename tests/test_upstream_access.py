"""The private upstream members the fork reads, pinned against the real objects (fork)."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from homeassistant.exceptions import ServiceValidationError

from custom_components.govee import services, upstream_access
from custom_components.govee.scene_cache import SceneCacheManager
from custom_components.govee.select import GoveeSceneSelectEntity
from tests.test_cov_coordinator_setup import _coordinator as _real_coordinator
from tests.test_scene_adjust import DEVICE_ID, PLATFORM_SCENES, _coordinator


@pytest.fixture(autouse=True)
def _fresh_warnings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(upstream_access, "_warned", set())


class TestTheMembersExist:
    def test_the_services_helpers(self) -> None:
        assert callable(services._get_coordinator_for_device)
        assert isinstance(services._device_not_found("x"), ServiceValidationError)

    def test_the_platform_scene_cache(self) -> None:
        coordinator = _real_coordinator()
        assert isinstance(coordinator._scene_cache, SceneCacheManager)
        assert upstream_access.platform_scene_cache(coordinator) == {}
        coordinator._scene_cache._scene_cache["dev"] = (0.0, [{"name": "Aurora"}])
        assert upstream_access.platform_scene_cache(coordinator) == {"dev": (0.0, [{"name": "Aurora"}])}

    def test_the_scene_select_map(self) -> None:
        coordinator = _coordinator(scene=None)
        entity = GoveeSceneSelectEntity(coordinator, coordinator.devices[DEVICE_ID], PLATFORM_SCENES[:2])
        assert all(isinstance(value, tuple) and len(value) == 3 for value in entity._scene_map.values())
        assert upstream_access.scene_select_names(entity) == [scene["name"] for scene in PLATFORM_SCENES[:2]]


class TestAMissingMember:
    def test_a_missing_function_raises_and_warns_once(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.delattr(services, "_device_not_found")
        with caplog.at_level(logging.WARNING):
            for _ in range(2):
                with pytest.raises(RuntimeError):
                    upstream_access.device_not_found("x")
        assert [record.levelno for record in caplog.records] == [logging.WARNING]
        assert "services._device_not_found" in caplog.text

    def test_a_missing_lookup_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delattr(services, "_get_coordinator_for_device")
        with pytest.raises(RuntimeError):
            upstream_access.coordinator_for_device(MagicMock(), "x")

    @pytest.mark.parametrize(
        ("coordinator", "member"),
        [
            (SimpleNamespace(), "GoveeCoordinator._scene_cache"),
            (SimpleNamespace(_scene_cache=SimpleNamespace()), "SceneCacheManager._scene_cache"),
        ],
    )
    def test_a_missing_scene_cache_reads_as_none(
        self, coordinator: object, member: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING):
            assert upstream_access.platform_scene_cache(coordinator) is None  # type: ignore[arg-type]
        assert member in caplog.text

    def test_a_missing_scene_map_reads_as_no_names(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING):
            assert upstream_access.scene_select_names(SimpleNamespace()) == []
        assert "GoveeSceneSelectEntity._scene_map" in caplog.text
