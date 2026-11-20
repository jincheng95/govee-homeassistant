"""Scene catalogue and the scene speed / direction selects (fork feature).

1. Catalogue parsing: one scene per name, presets and junk left out.
2. Catalogue cache: one fetch per SKU, a failed fetch backs off.
3. Wiring: only SKUs whose profile declares scene upload get the selects.
4. The selects: options follow the running scene, a choice uploads the
   rewritten scene, a refused upload restores the previous choice.
5. Cleanup: the selects leave the registry with the scenes option.
"""

from __future__ import annotations

import asyncio
import base64
import json
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
        assert set(found) == {"Rainbow B", "Flame B", "Candy Cane"}
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

    async def test_adding_an_entity_loads_the_catalogue_in_the_background(self) -> None:
        coordinator = _coordinator()
        speed, _ = _selects(coordinator)
        loader = AsyncMock(return_value=True)
        scene_catalogue.catalogue(coordinator).async_load = loader  # type: ignore[method-assign]
        tasks: list[Any] = []
        speed.hass.async_create_task = lambda coro: tasks.append(coro)

        with patch("homeassistant.helpers.update_coordinator.CoordinatorEntity.async_added_to_hass", AsyncMock()):
            await speed.async_added_to_hass()
        await tasks[0]

        loader.assert_awaited_once_with(SKU)
        speed.async_write_ha_state.assert_called_once()

    async def test_a_failed_load_writes_no_state(self) -> None:
        coordinator = _coordinator()
        speed, _ = _selects(coordinator)
        scene_catalogue.catalogue(coordinator).async_load = AsyncMock(return_value=False)  # type: ignore[method-assign]
        await speed._async_load_catalogue()
        speed.async_write_ha_state.assert_not_called()


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

    async def test_a_new_scene_forgets_the_old_choice(self) -> None:
        coordinator = _coordinator()
        speed, _ = _selects(coordinator)
        with _routed():
            await speed.async_select_option("Fast")

        coordinator.state.active_scene_name = "Flame B"
        assert speed.current_option is None

        coordinator.state.active_scene_name = "Rainbow B"
        assert speed.current_option == "Medium"

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
