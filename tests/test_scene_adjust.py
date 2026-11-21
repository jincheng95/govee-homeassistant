"""Scene catalogue and the scene speed / direction selects (fork feature).

1. Catalogue parsing: one scene per name, presets and junk left out.
2. Catalogue cache: one fetch per SKU, a failed fetch backs off.
3. Wiring: only SKUs whose profile declares scene upload get the selects.
4. The selects: options follow the running scene, a choice uploads the
   rewritten scene and belongs to that scene, a refused upload keeps the
   previous choice.
5. Cleanup: the selects leave the registry with the scenes option.
"""

from __future__ import annotations

import asyncio
import base64
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er

from custom_components.govee import _async_cleanup_orphaned_entities, scene_catalogue
from custom_components.govee.api.protocol import get_profile, scenes
from custom_components.govee.const import (
    CONF_ENABLE_SCENES,
    SUFFIX_SCENE_DIRECTION_SELECT,
    SUFFIX_SCENE_SPEED_SELECT,
)
from custom_components.govee.models import GoveeDevice, GoveeDeviceState
from custom_components.govee.models.device import DEVICE_TYPE_LIGHT
from custom_components.govee.platforms import scene_adjust
from custom_components.govee.platforms.scene_adjust import (
    GoveeSceneDirectionSelect,
    GoveeSceneSpeedSelect,
    async_scene_adjust_select_entities,
    speed_labels,
)
from tests.coordinator_mocks import follow_online_flag
from tests.test_cov_init import _coordinator as _cleanup_coordinator
from tests.test_cov_init import _entry as _cleanup_entry
from tests.test_cov_init import _seed

DEVICE_ID = "AA:BB:CC:DD:EE:FF:60:B0"
SKU = "H80C5"

RAINBOW = bytes.fromhex("491a000304055c07ff0000ff7f00ffff0000ff000000ff00ffff8b00ff")
RAINBOW_CONFIG = {"defDirIndex": 0, "defSpeed": 1, "directions": [5, 6, 7, 8], "speedRange": [85, 92, 98]}
FLAME = bytes.fromhex("490a0004045a010002ff0000ff7f00")
FLAME_CONFIG = {"defDirIndex": -1, "defSpeed": -1, "directions": [], "speedRange": [60, 70, 80, 90]}
RGBIC = bytes([0x02]) + bytes(range(1, 40))
TWO_EFFECT = bytes.fromhex(
    "02290000000f0001ffff0000141402fc0506000000ff000000000000000000ff000000000000fc1000e500"
    "290000000f0001ffff0000141402fc0506ff000000000000000000ff000000000000000200fc1200e500"
)
TWO_EFFECT_CONFIG = [
    {"defaultIndex": 1, "color": [247, 252, 252], "page": 0, "moveAll": [229, 229, 252]},
    {"moveAll": [229, 229, 252], "color": [247, 252, 252], "page": 1, "defaultIndex": 1},
]


def _effect(blob: bytes, code: int, config: Any = None) -> dict[str, Any]:
    return {
        "scenceParam": base64.b64encode(blob).decode(),
        "sceneCode": code,
        "speedInfo": {"config": json.dumps(config) if config is not None else "", "supSpeed": config is not None},
    }


def _payload() -> dict[str, Any]:
    return {
        "status": 200,
        "data": {
            "categories": [
                {
                    "categoryName": "Natural",
                    "scenes": [
                        {"sceneName": "Rainbow B", "lightEffects": [_effect(RAINBOW, 18188, RAINBOW_CONFIG)]},
                        {"sceneName": "Flame B", "lightEffects": [_effect(FLAME, 20001, FLAME_CONFIG)]},
                        {"sceneName": "Candy Cane", "lightEffects": [_effect(RGBIC, 12337, [{"page": 1}])]},
                        {"sceneName": "Christmas", "lightEffects": [_effect(TWO_EFFECT, 12333, TWO_EFFECT_CONFIG)]},
                        {"sceneName": "Sunrise", "lightEffects": [_effect(b"", 0)]},
                        {"sceneName": "Rainbow B", "lightEffects": [_effect(FLAME, 3)]},
                    ],
                },
                {"categoryName": "Empty", "scenes": []},
            ]
        },
    }


# ==============================================================================
# 1. Parsing
# ==============================================================================


class TestParseCatalogue:
    def test_scenes_by_name(self) -> None:
        found = scene_catalogue.parse_catalogue(_payload())
        assert set(found) == {"Rainbow B", "Flame B", "Candy Cane", "Christmas"}
        rainbow = found["Rainbow B"]
        assert (rainbow.code, rainbow.blob) == (18188, RAINBOW)
        assert rainbow.adjust is not None and rainbow.adjust.directions == (5, 6, 7, 8)
        assert found["Candy Cane"].adjust is None

    @pytest.mark.parametrize(
        "payload",
        [
            None,
            {},
            {"data": None},
            {"data": {"categories": "x"}},
            {"data": {"categories": [None, {"scenes": "x"}, {"scenes": [None]}]}},
            {"data": {"categories": [{"scenes": [{"sceneName": 5, "lightEffects": []}]}]}},
            {"data": {"categories": [{"scenes": [{"sceneName": "A", "lightEffects": []}]}]}},
            {"data": {"categories": [{"scenes": [{"sceneName": "A", "lightEffects": ["x"]}]}]}},
        ],
    )
    def test_junk_payloads_yield_nothing(self, payload: Any) -> None:
        assert scene_catalogue.parse_catalogue(payload) == {}

    @pytest.mark.parametrize(
        "effect",
        [
            {"scenceParam": "!!!", "sceneCode": 5},
            {"scenceParam": base64.b64encode(RAINBOW).decode(), "sceneCode": "5"},
            {"scenceParam": base64.b64encode(RAINBOW).decode(), "sceneCode": 70000},
            {"scenceParam": base64.b64encode(RAINBOW).decode(), "sceneCode": True},
        ],
    )
    def test_unusable_effects_are_left_out(self, effect: dict[str, Any]) -> None:
        payload = {"data": {"categories": [{"scenes": [{"sceneName": "A", "lightEffects": [effect]}]}]}}
        assert scene_catalogue.parse_catalogue(payload) == {}

    def test_bad_speed_config_keeps_the_scene_without_adjustments(self) -> None:
        effect = {"scenceParam": base64.b64encode(RAINBOW).decode(), "sceneCode": 9, "speedInfo": {"config": "{"}}
        payload = {"data": {"categories": [{"scenes": [{"sceneName": "A", "lightEffects": [effect]}]}]}}
        found = scene_catalogue.parse_catalogue(payload)
        assert found["A"].adjust is None

    def test_missing_speed_info_is_fine(self) -> None:
        effect = {"scenceParam": base64.b64encode(RAINBOW).decode(), "sceneCode": 9}
        payload = {"data": {"categories": [{"scenes": [{"sceneName": "A", "lightEffects": [effect]}]}]}}
        assert scene_catalogue.parse_catalogue(payload)["A"].adjust is None


# ==============================================================================
# 2. Cache
# ==============================================================================


class _Response:
    def __init__(self, status: int, payload: Any) -> None:
        self.status = status
        self._payload = payload

    async def json(self, content_type: Any = None) -> Any:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload

    async def __aenter__(self) -> _Response:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None


def _session(*responses: Any) -> MagicMock:
    session = MagicMock()
    queue = list(responses)

    def _get(*args: Any, **kwargs: Any) -> Any:
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    session.get = MagicMock(side_effect=_get)
    return session


class TestCatalogueCache:
    async def _load(self, session: MagicMock, store: scene_catalogue.SceneCatalogue) -> bool:
        with patch.object(scene_catalogue, "async_get_clientsession", return_value=session):
            return await store.async_load(SKU)

    async def test_one_fetch_then_cached(self) -> None:
        store = scene_catalogue.SceneCatalogue(MagicMock())
        session = _session(_Response(200, _payload()))

        assert await self._load(session, store) is True
        assert await self._load(session, store) is True

        assert session.get.call_count == 1
        assert session.get.call_args.kwargs["params"] == {"sku": SKU}
        assert store.loaded(SKU) is True
        assert store.scene(SKU, "Flame B") is not None
        assert store.scene(SKU, None) is None
        assert store.scene("H0000", "Flame B") is None

    @pytest.mark.parametrize(
        "response",
        [
            _Response(503, {}),
            _Response(200, ValueError("not json")),
            _Response(200, {"data": {"categories": []}}),
            aiohttp.ClientError("down"),
            asyncio.TimeoutError(),
        ],
    )
    async def test_a_failed_fetch_backs_off(self, response: Any) -> None:
        store = scene_catalogue.SceneCatalogue(MagicMock())
        session = _session(response)

        assert await self._load(session, store) is False
        assert await self._load(session, store) is False

        assert session.get.call_count == 1
        assert store.loaded(SKU) is False

    async def test_a_stale_copy_survives_a_failed_refresh(self) -> None:
        store = scene_catalogue.SceneCatalogue(MagicMock())
        session = _session(_Response(200, _payload()), _Response(500, {}))
        assert await self._load(session, store) is True

        store._retry_at[SKU] = 0.0
        assert await self._load(session, store) is True
        assert session.get.call_count == 2

    def test_one_store_per_coordinator(self) -> None:
        coordinator = MagicMock()
        coordinator._govee_scene_catalogue = None
        first = scene_catalogue.catalogue(coordinator)
        assert scene_catalogue.catalogue(coordinator) is first


class _Schedule:
    """A store whose clock, tasks and timers the test drives by hand."""

    def __init__(self, session: MagicMock) -> None:
        self.now = 1000.0
        self.tasks: list[Any] = []
        self.timers: list[tuple[float, Any, MagicMock]] = []
        self.hass = MagicMock()
        self.hass.async_create_task = self.tasks.append
        self.coordinator = MagicMock()
        self.store = scene_catalogue.SceneCatalogue(self.coordinator)
        self._patches = [
            patch.object(scene_catalogue, "async_get_clientsession", return_value=session),
            patch.object(scene_catalogue, "time", SimpleNamespace(monotonic=lambda: self.now, time=lambda: 0.0)),
            patch.object(scene_catalogue, "async_call_later", self._call_later),
        ]

    def _call_later(self, _hass: Any, delay: float, action: Any) -> MagicMock:
        cancel = MagicMock()
        self.timers.append((delay, action, cancel))
        return cancel

    async def run(self) -> None:
        while self.tasks:
            await self.tasks.pop(0)

    async def fire(self) -> None:
        delay, action, _cancel = self.timers[-1]
        self.now += delay
        action(None)
        await self.run()

    def __enter__(self) -> _Schedule:
        for active in self._patches:
            active.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        for active in self._patches:
            active.stop()


class TestCatalogueSchedule:
    async def test_a_failed_first_fetch_is_retried_then_refreshed_daily(self) -> None:
        session = _session(_Response(503, {}), _Response(200, _payload()), _Response(200, _payload()))
        with _Schedule(session) as schedule:
            schedule.store.async_schedule(schedule.hass, SKU)
            await schedule.run()
            assert schedule.store.loaded(SKU) is False
            schedule.coordinator.async_update_listeners.assert_not_called()
            assert schedule.timers[-1][0] == scene_catalogue.RETRY_AFTER_FAILURE

            await schedule.fire()
            assert schedule.store.loaded(SKU) is True
            schedule.coordinator.async_update_listeners.assert_called_once()
            assert schedule.timers[-1][0] == scene_catalogue.CATALOGUE_TTL

            await schedule.fire()
            assert session.get.call_count == 3
            assert schedule.coordinator.async_update_listeners.call_count == 2
            assert schedule.timers[-1][0] == scene_catalogue.CATALOGUE_TTL

    async def test_a_failed_refresh_keeps_the_copy_and_retries(self) -> None:
        session = _session(_Response(200, _payload()), _Response(500, {}))
        with _Schedule(session) as schedule:
            schedule.store.async_schedule(schedule.hass, SKU)
            await schedule.run()
            await schedule.fire()
            assert schedule.store.scene(SKU, "Flame B") is not None
            schedule.coordinator.async_update_listeners.assert_called_once()
            assert schedule.timers[-1][0] == scene_catalogue.RETRY_AFTER_FAILURE

    async def test_a_reload_is_never_scheduled_at_once(self) -> None:
        with _Schedule(_session()) as schedule:
            schedule.store._retry_at[SKU] = schedule.now + 3600
            schedule.store.async_schedule(schedule.hass, SKU)
            schedule.now += 3599.5
            await schedule.run()
            assert schedule.timers[-1][0] == scene_catalogue.MIN_RELOAD_DELAY

    async def test_scheduling_twice_starts_one_load(self) -> None:
        with _Schedule(_session(_Response(200, _payload()))) as schedule:
            schedule.store.async_schedule(schedule.hass, SKU)
            schedule.store.async_schedule(schedule.hass, SKU)
            assert len(schedule.tasks) == 1
            schedule.coordinator.config_entry.async_on_unload.assert_called_once_with(schedule.store.async_stop)
            await schedule.run()

    async def test_unloading_stops_the_schedule(self) -> None:
        session = _session(_Response(503, {}))
        with _Schedule(session) as schedule:
            schedule.store.async_schedule(schedule.hass, SKU)
            await schedule.run()
            cancel = schedule.timers[-1][2]
            schedule.store.async_stop()
            cancel.assert_called_once()

            schedule.store.async_schedule(schedule.hass, "H70C1")
            assert schedule.tasks == []

    async def test_a_load_that_ends_after_unloading_schedules_nothing(self) -> None:
        with _Schedule(_session(_Response(200, _payload()))) as schedule:
            schedule.store.async_schedule(schedule.hass, SKU)
            schedule.store.async_stop()
            await schedule.run()
            assert schedule.timers == []
            schedule.coordinator.async_update_listeners.assert_not_called()


# ==============================================================================
# Builders for the entity tests
# ==============================================================================


def _device(sku: str = SKU, device_id: str = DEVICE_ID, *, is_group: bool = False) -> GoveeDevice:
    return GoveeDevice(
        device_id=device_id,
        sku=sku,
        name="String lights",
        device_type=DEVICE_TYPE_LIGHT,
        capabilities=(),
        is_group=is_group,
    )


def _coordinator(*devices: GoveeDevice, scene: str | None = "Rainbow B") -> Any:
    devices = devices or (_device(),)
    coordinator = MagicMock()
    coordinator._govee_scene_catalogue = None
    coordinator._govee_scene_adjust_choices = None
    coordinator.devices = {device.device_id: device for device in devices}
    coordinator.last_update_success = True
    state = GoveeDeviceState.create_empty(devices[0].device_id)
    state.power_state = True
    state.online = True
    state.active_scene_name = scene
    coordinator.get_state = MagicMock(return_value=state)
    coordinator.state = state
    store = scene_catalogue.catalogue(coordinator)
    store._scenes[SKU] = scene_catalogue.parse_catalogue(_payload())
    return follow_online_flag(coordinator)


def _entry(*, scenes_on: bool = True) -> Any:
    entry = MagicMock()
    entry.options = {CONF_ENABLE_SCENES: scenes_on}
    return entry


def _selects(coordinator: Any) -> tuple[GoveeSceneSpeedSelect, GoveeSceneDirectionSelect]:
    speed, direction = async_scene_adjust_select_entities(coordinator, _entry())
    for entity in (speed, direction):
        entity.hass = MagicMock()
        entity.async_write_ha_state = MagicMock()
    assert isinstance(speed, GoveeSceneSpeedSelect) and isinstance(direction, GoveeSceneDirectionSelect)
    return speed, direction


def _rgbic_blob(frames: int) -> bytes:
    """An RGBIC blob whose upload takes ``frames`` frames, commit included."""
    for length in range(1, 400):
        blob = bytes([0x02]) + bytes(length)
        if len(scenes.terminated_chunks(blob, scenes.RGBIC_MARKER)) + 1 == frames:
            return blob
    raise AssertionError(frames)  # pragma: no cover


def _routed() -> Any:
    return patch.object(scene_adjust.raw_router, "async_route_frames", AsyncMock(return_value=True))


# ==============================================================================
# 3. Wiring
# ==============================================================================


class TestWiring:
    def test_a_scene_upload_sku_gets_both_selects(self) -> None:
        coordinator = _coordinator()
        speed, direction = _selects(coordinator)
        assert speed.unique_id == f"{DEVICE_ID}{SUFFIX_SCENE_SPEED_SELECT}"
        assert direction.unique_id == f"{DEVICE_ID}{SUFFIX_SCENE_DIRECTION_SELECT}"
        assert (speed.name, direction.name) == ("Scene speed", "Scene direction")

    def test_other_skus_and_groups_get_none(self) -> None:
        coordinator = _coordinator(
            _device("H6076", "AA:BB:CC:DD:EE:FF:60:76"),
            _device("H0000", "AA:BB:CC:DD:EE:FF:60:B1"),
            _device(SKU, "12345", is_group=True),
        )
        assert async_scene_adjust_select_entities(coordinator, _entry()) == []

    def test_the_scenes_option_gates_them(self) -> None:
        assert async_scene_adjust_select_entities(_coordinator(), _entry(scenes_on=False)) == []

    async def test_adding_an_entity_schedules_the_catalogue(self) -> None:
        coordinator = _coordinator()
        speed, _ = _selects(coordinator)
        schedule = MagicMock()
        scene_catalogue.catalogue(coordinator).async_schedule = schedule  # type: ignore[method-assign]

        speed.async_get_last_extra_data = AsyncMock(return_value=None)  # type: ignore[method-assign]
        with patch("homeassistant.helpers.update_coordinator.CoordinatorEntity.async_added_to_hass", AsyncMock()):
            await speed.async_added_to_hass()

        schedule.assert_called_once_with(speed.hass, SKU)


# ==============================================================================
# 4. Behaviour
# ==============================================================================


class TestOptions:
    def test_three_levels_are_named(self) -> None:
        assert speed_labels(3) == ["Slow", "Medium", "Fast"]
        assert speed_labels(4) == ["1", "2", "3", "4"]

    def test_options_and_defaults_come_from_the_running_scene(self) -> None:
        speed, direction = _selects(_coordinator())
        assert speed.options == ["Slow", "Medium", "Fast"]
        assert speed.current_option == "Medium"
        assert direction.options == ["Upper left", "Lower left", "Upper right", "Lower right"]
        assert direction.current_option == "Upper left"
        assert speed.available is True and direction.available is True

    def test_a_scene_without_directions_has_no_direction_select(self) -> None:
        speed, direction = _selects(_coordinator(scene="Flame B"))
        assert speed.options == ["1", "2", "3", "4"]
        assert speed.current_option is None
        assert direction.options == []
        assert direction.current_option is None
        assert direction.available is False

    @pytest.mark.parametrize("scene", [None, "Candy Cane", "Not in the catalogue"])
    def test_no_adjustable_scene_means_unavailable(self, scene: str | None) -> None:
        speed, direction = _selects(_coordinator(scene=scene))
        assert speed.options == [] and direction.options == []
        assert speed.current_option is None and direction.current_option is None
        assert speed.available is False

    def test_a_light_that_is_off_offers_nothing(self) -> None:
        coordinator = _coordinator()
        coordinator.state.power_state = False
        speed, _ = _selects(coordinator)
        assert speed.options == []

    def test_no_state_offers_nothing(self) -> None:
        coordinator = _coordinator()
        coordinator.get_state = MagicMock(return_value=None)
        speed, _ = _selects(coordinator)
        assert speed.options == []


class TestSelecting:
    async def test_speed_uploads_the_rewritten_scene(self) -> None:
        coordinator = _coordinator()
        speed, _ = _selects(coordinator)
        adjust = scene_catalogue.catalogue(coordinator).scene(SKU, "Rainbow B").adjust

        with _routed() as route:
            await speed.async_select_option("Fast")

        expected = scenes.scene_frames(scenes.adjust_blob(RAINBOW, adjust, speed=2), 18188)
        args = route.await_args.args
        assert args[:4] == (coordinator, DEVICE_ID, SKU, get_profile(SKU))
        assert list(args[4]) == expected
        assert speed.current_option == "Fast"
        speed.async_write_ha_state.assert_called_once()

    async def test_the_two_choices_travel_together(self) -> None:
        coordinator = _coordinator()
        speed, direction = _selects(coordinator)
        adjust = scene_catalogue.catalogue(coordinator).scene(SKU, "Rainbow B").adjust

        with _routed() as route:
            await speed.async_select_option("Slow")
            await direction.async_select_option("Lower right")

        expected = scenes.scene_frames(scenes.adjust_blob(RAINBOW, adjust, speed=0, direction=3), 18188)
        assert list(route.await_args.args[4]) == expected
        assert (speed.current_option, direction.current_option) == ("Slow", "Lower right")

    async def test_a_choice_belongs_to_its_scene(self) -> None:
        coordinator = _coordinator()
        speed, _ = _selects(coordinator)
        with _routed():
            await speed.async_select_option("Fast")

        coordinator.state.active_scene_name = "Flame B"
        assert speed.current_option is None

        coordinator.state.active_scene_name = "Rainbow B"
        assert speed.current_option == "Fast"

    @pytest.mark.parametrize("which", ["speed", "direction"])
    async def test_a_refused_upload_restores_the_previous_choice(self, which: str) -> None:
        coordinator = _coordinator()
        speed, direction = _selects(coordinator)
        entity, option, before = (speed, "Fast", "Medium") if which == "speed" else (direction, "Lower left", "Upper left")

        with patch.object(scene_adjust.raw_router, "async_route_frames", AsyncMock(return_value=False)):
            with pytest.raises(HomeAssistantError):
                await entity.async_select_option(option)

        assert entity.current_option == before
        entity.async_write_ha_state.assert_not_called()

    async def test_a_blob_that_cannot_be_rewritten_is_a_readable_error(self) -> None:
        coordinator = _coordinator()
        speed, _ = _selects(coordinator)
        store = scene_catalogue.catalogue(coordinator)
        good = store.scene(SKU, "Rainbow B")
        store._scenes[SKU]["Rainbow B"] = scene_catalogue.CatalogueScene(
            name="Rainbow B", code=good.code, blob=RAINBOW[:-1], adjust=good.adjust
        )

        with _routed() as route, pytest.raises(HomeAssistantError, match="cannot be adjusted"):
            await speed.async_select_option("Fast")

        route.assert_not_awaited()
        assert speed.current_option == "Medium"

    @pytest.mark.parametrize("which", ["speed", "direction"])
    async def test_unknown_option(self, which: str) -> None:
        speed, direction = _selects(_coordinator())
        entity = speed if which == "speed" else direction
        with _routed() as route, pytest.raises(ServiceValidationError):
            await entity.async_select_option("Sideways")
        route.assert_not_awaited()

    async def test_no_running_scene_rejects_any_option(self) -> None:
        speed, direction = _selects(_coordinator(scene=None))
        with pytest.raises(ServiceValidationError):
            await speed.async_select_option("Fast")
        with pytest.raises(ServiceValidationError):
            await direction.async_select_option("Up")


# ==============================================================================
# 5. Cleanup
# ==============================================================================


async def test_cleanup_removes_the_selects_with_the_scenes_option(hass, mock_light_device) -> None:
    device_id = mock_light_device.device_id
    entry = _cleanup_entry(hass, **{CONF_ENABLE_SCENES: False})
    registry = er.async_get(hass)
    ids = _seed(
        registry,
        entry,
        ("light", device_id),
        ("select", f"{device_id}{SUFFIX_SCENE_SPEED_SELECT}"),
        ("select", f"{device_id}{SUFFIX_SCENE_DIRECTION_SELECT}"),
    )

    await _async_cleanup_orphaned_entities(hass, entry, _cleanup_coordinator([device_id]))

    assert {uid for uid, eid in ids.items() if registry.async_get(eid) is not None} == {device_id}


async def test_cleanup_keeps_the_selects_while_scenes_are_on(hass, mock_light_device) -> None:
    device_id = mock_light_device.device_id
    entry = _cleanup_entry(hass, **{CONF_ENABLE_SCENES: True})
    registry = er.async_get(hass)
    ids = _seed(registry, entry, ("select", f"{device_id}{SUFFIX_SCENE_SPEED_SELECT}"))

    await _async_cleanup_orphaned_entities(hass, entry, _cleanup_coordinator([device_id]))

    assert all(registry.async_get(eid) is not None for eid in ids.values())


# ==============================================================================
# 6. Scene readback
# ==============================================================================


def _mode_report(*body: int) -> bytes:
    raw = bytes([0xAA, 0x05, *body]).ljust(19, b"\0")
    checksum = 0
    for byte in raw:
        checksum ^= byte
    return raw + bytes([checksum])


PLATFORM_SCENES = [
    {"name": "Rainbow B", "value": {"id": 4101, "paramId": 1}},
    {"name": "Flame B", "value": {"id": 4102, "paramId": 2}},
    {"name": "Odd", "value": "not a mapping"},
    "junk",
]


def _with_platform_scenes(coordinator: Any, scenes_list: list[Any] | None = None) -> Any:
    coordinator._scene_cache._scene_cache = {DEVICE_ID: (0.0, PLATFORM_SCENES if scenes_list is None else scenes_list)}
    return coordinator


class TestReportedSceneCode:
    def test_scene_mode_carries_the_code(self) -> None:
        assert scene_catalogue.reported_scene_code([_mode_report(0x04, 0x0C, 0x47)]) == 18188

    def test_other_frames_are_skipped_first(self) -> None:
        frames = [b"\xaa\x11" + bytes(18), b"short", _mode_report(0x04, 0x31, 0x30)]
        assert scene_catalogue.reported_scene_code(frames) == 12337

    @pytest.mark.parametrize(
        "frames",
        [
            [],
            [_mode_report(0x15, 0x01)],
            [_mode_report(0x0A, 0x20, 0x03)],
            [_mode_report(0x04, 0x0C, 0x47)[:19] + b"\x00"],
        ],
    )
    def test_no_scene_code(self, frames: list[bytes]) -> None:
        assert scene_catalogue.reported_scene_code(frames) is None


class TestApplySceneReport:
    def test_a_known_code_sets_the_scene(self) -> None:
        coordinator = _with_platform_scenes(_coordinator(scene=None))
        device = coordinator.devices[DEVICE_ID]

        changed = scene_catalogue.apply_scene_report(
            coordinator, device, coordinator.state, [_mode_report(0x04, 0x0C, 0x47)]
        )

        assert changed is True
        assert coordinator.state.active_scene_name == "Rainbow B"
        assert coordinator.state.active_scene == "4101"

    def test_a_scene_missing_from_the_platform_list_keeps_its_name_only(self) -> None:
        coordinator = _with_platform_scenes(_coordinator(scene=None), [])
        device = coordinator.devices[DEVICE_ID]
        coordinator.state.active_scene = "9"

        assert scene_catalogue.apply_scene_report(
            coordinator, device, coordinator.state, [_mode_report(0x04, 0x0C, 0x47)]
        )
        assert coordinator.state.active_scene_name == "Rainbow B"
        assert coordinator.state.active_scene is None

    @pytest.mark.parametrize(
        "scenes_list",
        [[{"name": "Rainbow B", "value": "x"}], [{"name": "Rainbow B", "value": {}}]],
    )
    def test_a_platform_entry_without_an_id(self, scenes_list: list[Any]) -> None:
        coordinator = _with_platform_scenes(_coordinator(scene=None), scenes_list)
        device = coordinator.devices[DEVICE_ID]
        scene_catalogue.apply_scene_report(coordinator, device, coordinator.state, [_mode_report(0x04, 0x0C, 0x47)])
        assert coordinator.state.active_scene is None

    def test_no_scene_cache_at_all(self) -> None:
        coordinator = _coordinator(scene=None)
        coordinator._scene_cache = None
        device = coordinator.devices[DEVICE_ID]
        assert scene_catalogue.apply_scene_report(
            coordinator, device, coordinator.state, [_mode_report(0x04, 0x0C, 0x47)]
        )
        assert coordinator.state.active_scene is None

    @pytest.mark.parametrize(
        "frames",
        [
            [_mode_report(0x04, 100, 0)],
            [_mode_report(0x04, 0x0C, 0x47)[:19] + b"\x00"],
            [],
        ],
    )
    def test_unknown_code_or_no_report_changes_nothing(self, frames: list[bytes]) -> None:
        coordinator = _with_platform_scenes(_coordinator(scene="Flame B"))
        coordinator.state.active_scene = "4102"
        device = coordinator.devices[DEVICE_ID]

        assert scene_catalogue.apply_scene_report(coordinator, device, coordinator.state, frames) is False
        assert (coordinator.state.active_scene, coordinator.state.active_scene_name) == ("4102", "Flame B")

    def test_another_mode_clears_the_scene(self) -> None:
        coordinator = _with_platform_scenes(_coordinator(scene="Flame B"))
        coordinator.state.active_scene = "4102"
        device = coordinator.devices[DEVICE_ID]

        assert scene_catalogue.apply_scene_report(coordinator, device, coordinator.state, [_mode_report(0x15, 0x01)])
        assert (coordinator.state.active_scene, coordinator.state.active_scene_name) == (None, None)
        assert (
            scene_catalogue.apply_scene_report(coordinator, device, coordinator.state, [_mode_report(0x15, 0x01)])
            is False
        )

    def test_the_scene_already_shown_is_not_a_change(self) -> None:
        coordinator = _with_platform_scenes(_coordinator(scene="Rainbow B"))
        device = coordinator.devices[DEVICE_ID]
        assert (
            scene_catalogue.apply_scene_report(coordinator, device, coordinator.state, [_mode_report(0x04, 0x0C, 0x47)])
            is False
        )

    def test_a_sku_without_scene_upload_is_ignored(self) -> None:
        other = _device("H6076", DEVICE_ID)
        coordinator = _with_platform_scenes(_coordinator(other, scene=None))
        assert (
            scene_catalogue.apply_scene_report(coordinator, other, coordinator.state, [_mode_report(0x04, 0x0C, 0x47)])
            is False
        )
        assert coordinator.state.active_scene_name is None


# ==============================================================================
# 7. Local scene start
# ==============================================================================


class TestStartSceneLocally:
    async def test_uploads_the_catalogue_scene_and_records_it(self) -> None:
        coordinator = _coordinator(scene=None)
        device = coordinator.devices[DEVICE_ID]

        with _routed() as route:
            started = await scene_adjust.async_start_scene_locally(coordinator, device, 4101, "Rainbow B")

        assert started is True
        assert list(route.await_args.args[4]) == scenes.scene_frames(RAINBOW, 18188)
        assert (coordinator.state.active_scene, coordinator.state.active_scene_name) == ("4101", "Rainbow B")
        coordinator.async_update_listeners.assert_called_once()

    async def _declined(self, coordinator: Any, name: str = "Rainbow B", *, routed: bool = True) -> AsyncMock:
        device = next(iter(coordinator.devices.values()))
        route = AsyncMock(return_value=routed)
        with patch.object(scene_adjust.raw_router, "async_route_frames", route):
            assert await scene_adjust.async_start_scene_locally(coordinator, device, 1, name) is False
        assert coordinator.state.active_scene_name is None
        return route

    async def test_declines_a_sku_without_scene_upload(self) -> None:
        route = await self._declined(_coordinator(_device("H6076", DEVICE_ID), scene=None))
        route.assert_not_awaited()

    @pytest.mark.parametrize("power", [False, None])
    async def test_declines_a_light_not_known_to_be_on(self, power: bool | None) -> None:
        coordinator = _coordinator(scene=None)
        coordinator.state.power_state = power
        route = await self._declined(coordinator)
        route.assert_not_awaited()

    async def test_declines_without_state(self) -> None:
        coordinator = _coordinator(scene=None)
        state = coordinator.state
        coordinator.get_state = MagicMock(return_value=None)
        device = coordinator.devices[DEVICE_ID]
        with _routed() as route:
            assert await scene_adjust.async_start_scene_locally(coordinator, device, 1, "Rainbow B") is False
        route.assert_not_awaited()
        assert state.active_scene_name is None

    async def test_declines_a_scene_the_catalogue_lacks(self) -> None:
        route = await self._declined(_coordinator(scene=None), "Sunrise")
        route.assert_not_awaited()

    @pytest.mark.parametrize(("frames", "local"), [(16, True), (scenes.MAX_MESSAGE_FRAMES, True), (19, False)])
    async def test_only_a_scene_that_fits_one_message_starts_locally(self, frames: int, local: bool) -> None:
        coordinator = _coordinator(scene=None)
        blob = _rgbic_blob(frames)
        scene_catalogue.catalogue(coordinator)._scenes[SKU]["Long"] = scene_catalogue.CatalogueScene(
            name="Long", code=7, blob=blob, adjust=None
        )
        device = coordinator.devices[DEVICE_ID]
        with _routed() as route:
            started = await scene_adjust.async_start_scene_locally(coordinator, device, 7, "Long")
        assert started is local
        if local:
            assert len(route.await_args.args[4]) == frames
        else:
            route.assert_not_awaited()
            assert coordinator.state.active_scene_name is None

    async def test_declines_a_scene_that_cannot_be_framed(self) -> None:
        coordinator = _coordinator(scene=None)
        scene_catalogue.catalogue(coordinator)._scenes[SKU]["Rainbow B"] = scene_catalogue.CatalogueScene(
            name="Rainbow B", code=0x10000, blob=RAINBOW, adjust=None
        )
        route = await self._declined(coordinator)
        route.assert_not_awaited()

    async def test_declines_when_no_pipe_takes_the_frames(self) -> None:
        coordinator = _coordinator(scene=None)
        route = await self._declined(coordinator, routed=False)
        route.assert_awaited_once()
        coordinator.async_update_listeners.assert_not_called()


class TestSceneSelectRouting:
    def _select(self, coordinator: Any, scenes_list: list[dict[str, Any]]) -> Any:
        from custom_components.govee.select import GoveeSceneSelectEntity

        entity = GoveeSceneSelectEntity(coordinator, coordinator.devices[DEVICE_ID], scenes_list)
        entity.hass = MagicMock()
        entity.async_write_ha_state = MagicMock()
        coordinator.async_control_device = AsyncMock(return_value=True)
        return entity

    async def test_a_catalogue_scene_starts_locally(self) -> None:
        coordinator = _coordinator(scene=None)
        entity = self._select(coordinator, PLATFORM_SCENES[:2])

        with _routed() as route:
            await entity.async_select_option("Rainbow B")

        route.assert_awaited_once()
        coordinator.async_control_device.assert_not_awaited()
        assert coordinator.state.active_scene == "4101"
        entity.async_write_ha_state.assert_called_once()

    async def test_a_declined_local_start_falls_back_to_the_cloud(self) -> None:
        coordinator = _coordinator(scene=None)
        entity = self._select(coordinator, PLATFORM_SCENES[:2])

        with patch.object(scene_adjust.raw_router, "async_route_frames", AsyncMock(return_value=False)):
            await entity.async_select_option("Rainbow B")

        coordinator.async_control_device.assert_awaited_once()
        command = coordinator.async_control_device.await_args.args[1]
        assert (command.scene_id, command.scene_name) == (4101, "Rainbow B")

    async def test_a_repeated_name_always_uses_the_cloud(self) -> None:
        coordinator = _coordinator(scene=None)
        twins = [
            {"name": "Rainbow B", "value": {"id": 4101}},
            {"name": "Rainbow B", "value": {"id": 4999}},
        ]
        entity = self._select(coordinator, twins)

        with _routed() as route:
            await entity.async_select_option("Rainbow B (1)")

        route.assert_not_awaited()
        command = coordinator.async_control_device.await_args.args[1]
        assert command.scene_id == 4999


class TestRgbicSceneSpeed:
    async def test_an_rgbic_scene_offers_speed_but_no_direction(self) -> None:
        coordinator = _coordinator(scene="Christmas")
        speed, direction = _selects(coordinator)
        assert speed.options == ["Slow", "Medium", "Fast"]
        assert speed.current_option == "Medium"
        assert direction.options == [] and direction.available is False

        adjust = scene_catalogue.catalogue(coordinator).scene(SKU, "Christmas").adjust
        with _routed() as route:
            await speed.async_select_option("Slow")

        expected = scenes.scene_frames(scenes.adjust_blob(TWO_EFFECT, adjust, speed=0), 12333)
        assert list(route.await_args.args[4]) == expected
        assert speed.current_option == "Slow"
