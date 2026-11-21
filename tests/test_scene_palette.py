"""Scene palettes, the shared settings store and the reset button (fork feature).

1. Blob rewrite: palettes located per layer, rewritten byte-exact with all
   three length fields, combined with speed and direction.
2. Availability: every scene-setting entity across the scene kinds.
3. Setting a palette: validation, upload, settings kept per scene code.
4. Reset: catalogue values back, refused upload keeps the settings.
5. Restore: the active scene's values come back after a restart.
6. Wiring and cleanup.
7. The raw power push and DIY / music mode reports against these entities.

Every blob here is synthetic and shaped like a catalogue blob.
"""

from __future__ import annotations

import base64
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.restore_state import RestoredExtraData

from custom_components.govee import _async_cleanup_orphaned_entities, scene_catalogue
from custom_components.govee.api.protocol import FrameError, get_profile, scenes
from custom_components.govee.const import (
    CONF_ENABLE_SCENES,
    SCENE_SETTING_SUFFIXES,
    SUFFIX_SCENE_PALETTE_TEXT,
    SUFFIX_SCENE_RESET_BUTTON,
)
from custom_components.govee.models import GoveeDeviceState
from custom_components.govee.platforms import scene_adjust
from custom_components.govee.platforms.scene_adjust import (
    GoveeScenePaletteText,
    GoveeSceneResetButton,
    SceneSettings,
    async_scene_adjust_select_entities,
    async_scene_palette_text_entities,
    async_scene_reset_button_entities,
    scene_blob,
)
from tests.coordinator_mocks import follow_online_flag
from tests.test_cov_init import _coordinator as _cleanup_coordinator
from tests.test_cov_init import _entry as _cleanup_entry
from tests.test_cov_init import _seed
from tests.test_scene_adjust import DEVICE_ID, SKU, TWO_EFFECT, TWO_EFFECT_CONFIG, _device, _entry, _routed

# One effect-3 record, two colours.
SINGLE = bytes([0x49, 0x0B, 0x00, 0x03, 0x04, 0x05, 0x5C, 0x02, 0xFF, 0x00, 0x00, 0x00, 0x00, 0xFF])

# Effect 14 sub-type 1, one colour.
LAYER_RIBBON = bytes([0x0E, 0x01, 0x32, 0x02, 0x08, 0x01, 0x0D, 0x02, 0x01, 0x01, 0x00, 0x5B, 0xFF])
# Effect 17, two colours.
LAYER_SMUDGE = bytes([0x11, 0x02, 0x32, 0x02, 0x35, 0xFF, 0xFF, 0x00, 0x5A, 0xFF])
# Effect 4, one colour.
LAYER_FOUR = bytes([0x04, 0x5A, 0x01, 0x00, 0x02, 0x01, 0xFF, 0x00, 0x00])
# Effect 16 with a sub-type the parameter table lacks.
LAYER_UNKNOWN = bytes([0x10, 0x01, 0x05, 0x01, 0xFF, 0x00, 0x00])

TWO_LAYER = bytes([0x49, 0x1F, 0x00, 0x0C, 0x02, 0x02, 0x00, 0x0D, 0x00, *LAYER_RIBBON, 0x0A, 0x00, *LAYER_SMUDGE])
THREE_LAYER = bytes(
    [0x49, 0x2A, 0x00, 0x0C, 0x03, 0x02, 0x00, 0x0D, 0x00, *LAYER_RIBBON, 0x0A, 0x00, *LAYER_SMUDGE, 0x09, 0x00]
    + list(LAYER_FOUR)
)
GAP_LAYER = bytes(
    [0x49, 0x27, 0x00, 0x0C, 0x03, 0x02, 0x00, 0x0D, 0x00, *LAYER_RIBBON, 0x07, 0x00, *LAYER_UNKNOWN, 0x09, 0x00]
    + list(LAYER_FOUR)
)
TWO_LAYER_CONFIG = {
    "multiLayerSpeeds": [[0x20, 0x30], [0x40, 0x50]],
    "directions": [3, 4],
    "multiLayersDirectionRange": [1],
    "defSpeed": 0,
    "defDirIndex": 0,
}

RED, BLUE = (0xFF, 0x00, 0x00), (0x00, 0x00, 0xFF)


# ==============================================================================
# 1. Blob rewrite
# ==============================================================================


class TestScenePalettes:
    def test_single_effect(self) -> None:
        assert scenes.scene_palettes(SINGLE) == ((RED, BLUE),)

    def test_layers(self) -> None:
        assert scenes.scene_palettes(THREE_LAYER) == (
            ((0x00, 0x5B, 0xFF),),
            ((0x35, 0xFF, 0xFF), (0x00, 0x5A, 0xFF)),
            (RED,),
        )

    def test_an_unknown_effect_or_sub_type_has_no_palette(self) -> None:
        assert scenes.scene_palettes(GAP_LAYER) == (((0x00, 0x5B, 0xFF),), None, (RED,))
        assert scenes.scene_palettes(bytes([0x49, 0x02, 0x00, 0x13, 0x00])) == (None,)

    def test_a_colour_list_that_does_not_end_the_record_is_not_guessed(self) -> None:
        trailing = bytes([0x49, 0x0C, 0x00, 0x03, 0x04, 0x05, 0x5C, 0x02, 0xFF, 0x00, 0x00, 0x00, 0x00, 0xFF, 0x00])
        assert scenes.scene_palettes(trailing) == (None,)
        assert scenes.scene_palettes(bytes([0x49, 0x02, 0x00, 0x0E, 0x01])) == (None,)
        assert scenes.scene_palettes(bytes([0x49, 0x01, 0x00, 0x0E])) == (None,)

    @pytest.mark.parametrize("blob", [b"", TWO_EFFECT, SINGLE[:-1]])
    def test_other_kinds_and_malformed_blobs_have_none(self, blob: bytes) -> None:
        assert scenes.scene_palettes(blob) == ()


class TestPaletteBlob:
    def test_single_effect_grows(self) -> None:
        out = scenes.palette_blob(SINGLE, {0: [(1, 2, 3), (4, 5, 6), (7, 8, 9)]})
        assert out == bytes([0x49, 0x0E, 0x00, 0x03, 0x04, 0x05, 0x5C, 0x03, 1, 2, 3, 4, 5, 6, 7, 8, 9])

    def test_two_layers_one_shrinks(self) -> None:
        out = scenes.palette_blob(TWO_LAYER, {1: [(0x10, 0x20, 0x30)]})
        assert out == bytes(
            [0x49, 0x1C, 0x00, 0x0C, 0x02, 0x02, 0x00, 0x0D, 0x00, *LAYER_RIBBON]
            + [0x07, 0x00, 0x11, 0x02, 0x32, 0x01, 0x10, 0x20, 0x30]
        )

    def test_three_layers_two_change(self) -> None:
        out = scenes.palette_blob(
            THREE_LAYER, {0: [(1, 1, 1), (2, 2, 2), (3, 3, 3)], 2: [(0xAA, 0xBB, 0xCC), (0x11, 0x22, 0x33)]}
        )
        assert out == bytes(
            [0x49, 0x33, 0x00, 0x0C, 0x03, 0x02, 0x00]
            + [0x13, 0x00, 0x0E, 0x01, 0x32, 0x02, 0x08, 0x01, 0x0D, 0x02, 0x01, 0x03, 1, 1, 1, 2, 2, 2, 3, 3, 3]
            + [0x0A, 0x00, *LAYER_SMUDGE]
            + [0x0C, 0x00, 0x04, 0x5A, 0x01, 0x00, 0x02, 0x02, 0xAA, 0xBB, 0xCC, 0x11, 0x22, 0x33]
        )

    def test_the_same_palette_is_the_same_blob(self) -> None:
        assert scenes.palette_blob(THREE_LAYER, {1: [(0x35, 0xFF, 0xFF), (0x00, 0x5A, 0xFF)]}) == THREE_LAYER

    def test_bytes_after_the_last_layer_are_kept(self) -> None:
        tailed = bytes([0x49, 0x05, 0x00, 0x0C, 0x00, 0x02, 0x00, 0xAB])
        assert scenes.palette_blob(tailed, {}) == tailed

    @pytest.mark.parametrize(
        ("blob", "palettes"),
        [
            (TWO_EFFECT, {0: [RED]}),
            (TWO_LAYER, {2: [RED]}),
            (TWO_LAYER, {-1: [RED]}),
            (TWO_LAYER, {0: []}),
            (TWO_LAYER, {0: [RED] * 9}),
            (TWO_LAYER, {0: [(0x100, 0, 0)]}),
            (TWO_LAYER, {0: [(1, 2)]}),
            (GAP_LAYER, {1: [RED]}),
            (SINGLE[:-1], {0: [RED]}),
        ],
    )
    def test_refusals(self, blob: bytes, palettes: dict[int, Any]) -> None:
        with pytest.raises(FrameError):
            scenes.palette_blob(blob, palettes)


def _catalogue_scene(blob: bytes, config: Any = None, code: int = 21001) -> scene_catalogue.CatalogueScene:
    return scene_catalogue.CatalogueScene(
        name="x", code=code, blob=blob, adjust=None if config is None else scenes.parse_adjust(config, blob)
    )


class TestSceneBlob:
    def test_speed_direction_and_palette_together(self) -> None:
        scene = _catalogue_scene(TWO_LAYER, TWO_LAYER_CONFIG)
        out = scene_blob(scene, SceneSettings(speed=1, direction=1, palettes={1: ((0x10, 0x20, 0x30),)}))
        assert out == bytes(
            [0x49, 0x1C, 0x00, 0x0C, 0x02, 0x02, 0x00]
            + [0x0D, 0x00, 0x0E, 0x01, 0x40, 0x02, 0x08, 0x01, 0x0D, 0x02, 0x01, 0x01, 0x00, 0x5B, 0xFF]
            + [0x07, 0x00, 0x11, 0x04, 0x50, 0x01, 0x10, 0x20, 0x30]
        )

    def test_no_settings_is_the_catalogue_blob(self) -> None:
        assert scene_blob(_catalogue_scene(TWO_LAYER, TWO_LAYER_CONFIG), SceneSettings()) == TWO_LAYER

    def test_settings_the_scene_lacks_are_skipped(self) -> None:
        scene = _catalogue_scene(GAP_LAYER, {"speedRange": [1, 2]})
        settings = SceneSettings(speed=7, direction=0, palettes={1: (RED,), 5: (RED,)})
        assert scene_blob(scene, settings) == GAP_LAYER

    def test_an_rgbic_scene_takes_speed_only(self) -> None:
        scene = _catalogue_scene(TWO_EFFECT, TWO_EFFECT_CONFIG)
        settings = SceneSettings(speed=0, direction=0, palettes={0: (RED,)})
        assert scene_blob(scene, settings) == scenes.adjust_blob(TWO_EFFECT, scene.adjust, speed=0)


# ==============================================================================
# Builders for the entity tests
# ==============================================================================


def _effect(blob: bytes, code: int, config: Any = None) -> dict[str, Any]:
    return {
        "scenceParam": base64.b64encode(blob).decode(),
        "sceneCode": code,
        "speedInfo": {"config": json.dumps(config) if config is not None else ""},
    }


def _payload() -> dict[str, Any]:
    entries = [
        ("Single", SINGLE, 21001, None),
        ("Layers", TWO_LAYER, 21002, TWO_LAYER_CONFIG),
        ("Gap", GAP_LAYER, 21003, None),
        ("Christmas", TWO_EFFECT, 12333, TWO_EFFECT_CONFIG),
        ("Candy Cane", TWO_EFFECT, 12337, None),
        ("Sunrise", b"", 7, None),
    ]
    return {
        "data": {
            "categories": [
                {"scenes": [{"sceneName": n, "lightEffects": [_effect(b, c, cfg)]} for n, b, c, cfg in entries]}
            ]
        }
    }


def _coordinator(scene: str | None = "Layers") -> Any:
    device = _device()
    coordinator = MagicMock()
    coordinator._govee_scene_catalogue = None
    coordinator._govee_scene_adjust_choices = None
    coordinator.devices = {device.device_id: device}
    coordinator.last_update_success = True
    state = GoveeDeviceState.create_empty(device.device_id)
    state.power_state = True
    state.online = True
    state.active_scene_name = scene
    coordinator.get_state = MagicMock(return_value=state)
    coordinator.state = state
    scene_catalogue.catalogue(coordinator)._scenes[SKU] = scene_catalogue.parse_catalogue(_payload())
    return follow_online_flag(coordinator)


def _entities(coordinator: Any) -> dict[str, Any]:
    entry = _entry()
    speed, direction = async_scene_adjust_select_entities(coordinator, entry)
    palettes = async_scene_palette_text_entities(coordinator, entry)
    (reset,) = async_scene_reset_button_entities(coordinator, entry)
    found = {"speed": speed, "direction": direction, "reset": reset}
    found.update({f"palette{index + 1}": entity for index, entity in enumerate(palettes)})
    for entity in found.values():
        entity.hass = MagicMock()
        entity.async_write_ha_state = MagicMock()
    return found


def _sent(route: AsyncMock) -> list[bytes]:
    return list(route.await_args.args[4])


# ==============================================================================
# 2. Availability
# ==============================================================================

_ORDER = ("speed", "direction", "palette1", "palette2", "palette3", "reset")


@pytest.mark.parametrize(
    ("scene", "expected"),
    [
        (None, ()),
        ("Not in the catalogue", ()),
        ("Sunrise", ()),
        ("Candy Cane", ()),
        ("Single", ("palette1", "reset")),
        ("Layers", ("speed", "direction", "palette1", "palette2", "reset")),
        ("Gap", ("palette1", "palette3", "reset")),
        ("Christmas", ("speed", "reset")),
    ],
)
def test_availability_matrix(scene: str | None, expected: tuple[str, ...]) -> None:
    entities = _entities(_coordinator(scene))
    assert tuple(name for name in _ORDER if entities[name].available) == expected


def test_a_light_that_is_off_or_offline_offers_nothing() -> None:
    coordinator = _coordinator()
    coordinator.state.power_state = False
    assert not any(entity.available for entity in _entities(coordinator).values())

    coordinator = _coordinator()
    coordinator.last_update_success = False
    assert not any(entity.available for entity in _entities(coordinator).values())


def test_palette_values_come_from_the_catalogue() -> None:
    entities = _entities(_coordinator())
    assert entities["palette1"].native_value == "#005bff"
    assert entities["palette2"].native_value == "#35ffff, #005aff"
    assert entities["palette3"].native_value is None
    assert _entities(_coordinator(None))["palette1"].native_value is None


def test_names_and_ids() -> None:
    entities = _entities(_coordinator())
    palette = entities["palette2"]
    assert palette.unique_id == f"{DEVICE_ID}{SUFFIX_SCENE_PALETTE_TEXT}2"
    assert palette.translation_key == "scene_palette"
    assert palette.translation_placeholders == {"layer": "2"}
    assert entities["reset"].unique_id == f"{DEVICE_ID}{SUFFIX_SCENE_RESET_BUTTON}"
    assert entities["reset"].translation_key == "reset_scene_settings"


# ==============================================================================
# 3. Setting a palette
# ==============================================================================


class TestSetPalette:
    async def test_upload_carries_every_setting_of_the_scene(self) -> None:
        coordinator = _coordinator()
        entities = _entities(coordinator)

        with _routed() as route:
            await entities["speed"].async_select_option("2")
            await entities["direction"].async_select_option("anticlockwise")
            await entities["palette2"].async_set_value(" #102030 ")

        scene = scene_catalogue.catalogue(coordinator).scene(SKU, "Layers")
        expected = scene_blob(scene, SceneSettings(speed=1, direction=1, palettes={1: ((0x10, 0x20, 0x30),)}))
        assert _sent(route) == scenes.scene_frames(expected, 21002)
        assert route.await_args.args[:4] == (coordinator, DEVICE_ID, SKU, get_profile(SKU))
        assert entities["palette2"].native_value == "#102030"
        assert entities["palette1"].native_value == "#005bff"
        entities["palette2"].async_write_ha_state.assert_called_once()

    async def test_input_is_normalised(self) -> None:
        entities = _entities(_coordinator())
        with _routed():
            await entities["palette1"].async_set_value("#AABBCC,#010203 ,  #FFFFFF")
        assert entities["palette1"].native_value == "#aabbcc, #010203, #ffffff"

    @pytest.mark.parametrize(
        "value",
        ["", "   ", "red", "ff0000", "#ff0000 #00ff00", "#ff0000,,#00ff00", "#ff00001", ", ".join(["#ff0000"] * 9)],
    )
    async def test_invalid_palettes_are_refused(self, value: str) -> None:
        entities = _entities(_coordinator())
        with _routed() as route, pytest.raises(ServiceValidationError) as raised:
            await entities["palette1"].async_set_value(value)
        assert raised.value.translation_key == "invalid_scene_palette"
        route.assert_not_awaited()

    @pytest.mark.parametrize(("scene", "which"), [(None, "palette1"), ("Gap", "palette2"), ("Single", "palette2")])
    async def test_a_layer_that_does_not_apply_is_refused(self, scene: str | None, which: str) -> None:
        entities = _entities(_coordinator(scene))
        with _routed() as route, pytest.raises(ServiceValidationError) as raised:
            await entities[which].async_set_value("#ff0000")
        assert raised.value.translation_key == "scene_setting_unavailable"
        route.assert_not_awaited()

    async def test_a_refused_upload_keeps_the_old_palette(self) -> None:
        entities = _entities(_coordinator())
        with patch.object(scene_adjust.raw_router, "async_route_frames", AsyncMock(return_value=False)):
            with pytest.raises(HomeAssistantError):
                await entities["palette1"].async_set_value("#ff0000")
        assert entities["palette1"].native_value == "#005bff"
        entities["palette1"].async_write_ha_state.assert_not_called()

    async def test_settings_belong_to_a_scene_code(self) -> None:
        coordinator = _coordinator("Single")
        entities = _entities(coordinator)
        with _routed():
            await entities["palette1"].async_set_value("#123456")

        coordinator.state.active_scene_name = "Gap"
        assert entities["palette1"].native_value == "#005bff"

        coordinator.state.active_scene_name = "Single"
        assert entities["palette1"].native_value == "#123456"

    async def test_the_lamp_reporting_another_mode_makes_them_unavailable(self) -> None:
        from tests.test_scene_adjust import _mode_report

        coordinator = _coordinator("Single")
        coordinator._scene_cache = None
        entities = _entities(coordinator)
        device = coordinator.devices[DEVICE_ID]

        scene_catalogue.apply_scene_report(coordinator, device, coordinator.state, [_mode_report(0x04, 0x0B, 0x52)])
        assert coordinator.state.active_scene_name == "Gap"
        assert entities["palette2"].available is False

        scene_catalogue.apply_scene_report(coordinator, device, coordinator.state, [_mode_report(0x15, 0x01)])
        assert not any(entity.available for entity in entities.values())

    async def test_starting_a_scene_again_carries_its_settings(self) -> None:
        coordinator = _coordinator("Single")
        entities = _entities(coordinator)
        with _routed():
            await entities["palette1"].async_set_value("#123456")
        coordinator.state.active_scene_name = None

        with _routed() as route:
            assert await scene_adjust.async_start_scene_locally(coordinator, coordinator.devices[DEVICE_ID], 1, "Single")

        expected = bytes([0x49, 0x08, 0x00, 0x03, 0x04, 0x05, 0x5C, 0x01, 0x12, 0x34, 0x56])
        assert _sent(route) == scenes.scene_frames(expected, 21001)


# ==============================================================================
# 4. Reset
# ==============================================================================


class TestReset:
    async def test_reset_uploads_the_catalogue_scene_and_forgets_the_settings(self) -> None:
        coordinator = _coordinator()
        entities = _entities(coordinator)
        with _routed():
            await entities["speed"].async_select_option("2")
            await entities["palette1"].async_set_value("#ff0000")

        with _routed() as route:
            await entities["reset"].async_press()

        assert _sent(route) == scenes.scene_frames(TWO_LAYER, 21002)
        assert entities["speed"].current_option == "1"
        assert entities["palette1"].native_value == "#005bff"
        coordinator.async_update_listeners.assert_called_once()

    async def test_a_refused_reset_keeps_the_settings(self) -> None:
        entities = _entities(_coordinator())
        with _routed():
            await entities["palette1"].async_set_value("#ff0000")
        with patch.object(scene_adjust.raw_router, "async_route_frames", AsyncMock(return_value=False)):
            with pytest.raises(HomeAssistantError):
                await entities["reset"].async_press()
        assert entities["palette1"].native_value == "#ff0000"

    async def test_a_malformed_blob_is_a_readable_error(self) -> None:
        coordinator = _coordinator("Single")
        entities = _entities(coordinator)
        store = scene_catalogue.catalogue(coordinator)._scenes[SKU]
        store["Single"] = scene_catalogue.CatalogueScene(name="Single", code=0x10000, blob=SINGLE, adjust=None)
        with _routed() as route, pytest.raises(HomeAssistantError, check=lambda err: err.translation_key == "scene_adjust_failed"):
            await entities["reset"].async_press()
        route.assert_not_awaited()

    @pytest.mark.parametrize("scene", [None, "Candy Cane"])
    async def test_nothing_to_reset(self, scene: str | None) -> None:
        entities = _entities(_coordinator(scene))
        with _routed() as route, pytest.raises(ServiceValidationError) as raised:
            await entities["reset"].async_press()
        assert raised.value.translation_key == "scene_setting_unavailable"
        route.assert_not_awaited()


# ==============================================================================
# 5. Restore
# ==============================================================================


async def _add(entity: Any, data: dict[str, Any] | None) -> None:
    entity.async_get_last_extra_data = AsyncMock(return_value=None if data is None else RestoredExtraData(data))
    entity.hass.async_create_task = lambda coro: coro.close()
    with patch("homeassistant.helpers.update_coordinator.CoordinatorEntity.async_added_to_hass", AsyncMock()):
        await entity.async_added_to_hass()


class TestRestore:
    async def test_saved_data_names_the_active_scene(self) -> None:
        coordinator = _coordinator()
        entities = _entities(coordinator)
        with _routed():
            await entities["speed"].async_select_option("2")
            await entities["palette2"].async_set_value("#010203, #040506")

        assert entities["speed"].extra_restore_state_data.as_dict() == {"scene": 21002, "speed": 1}
        assert entities["direction"].extra_restore_state_data.as_dict() == {"scene": 21002, "direction": None}
        assert entities["palette2"].extra_restore_state_data.as_dict() == {
            "scene": 21002,
            "palette": [[1, 2, 3], [4, 5, 6]],
        }
        assert entities["palette1"].extra_restore_state_data.as_dict() == {"scene": 21002, "palette": None}
        assert _entities(_coordinator(None))["speed"].extra_restore_state_data is None

    async def test_restored_values_seed_their_scene(self) -> None:
        coordinator = _coordinator()
        entities = _entities(coordinator)
        await _add(entities["speed"], {"scene": 21002, "speed": 1})
        await _add(entities["direction"], {"scene": 21002, "direction": 1})
        await _add(entities["palette2"], {"scene": 21002, "palette": [[1, 2, 3]]})

        assert entities["speed"].current_option == "2"
        assert entities["direction"].current_option == "anticlockwise"
        assert entities["palette2"].native_value == "#010203"

        coordinator.state.active_scene_name = "Single"
        assert entities["palette1"].native_value == "#ff0000, #0000ff"

    @pytest.mark.parametrize(
        "data",
        [
            None,
            {},
            {"scene": True, "palette": [[1, 2, 3]]},
            {"scene": "21002", "palette": [[1, 2, 3]]},
            {"scene": 21002, "palette": None},
            {"scene": 21002, "palette": []},
            {"scene": 21002, "palette": [[1, 2, 3]] * 9},
            {"scene": 21002, "palette": [[1, 2]]},
            {"scene": 21002, "palette": [[1, 2, 256]]},
            {"scene": 21002, "palette": [[1, 2, True]]},
            {"scene": 21002, "palette": ["#010203"]},
        ],
    )
    async def test_junk_restore_data_is_ignored(self, data: dict[str, Any] | None) -> None:
        entities = _entities(_coordinator())
        await _add(entities["palette2"], data)
        assert entities["palette2"].native_value == "#35ffff, #005aff"

    @pytest.mark.parametrize("speed", [True, -1, "1"])
    async def test_junk_restored_index_is_ignored(self, speed: Any) -> None:
        entities = _entities(_coordinator())
        await _add(entities["speed"], {"scene": 21002, "speed": speed})
        assert entities["speed"].current_option == "1"

    async def test_a_value_set_this_session_wins_over_restore(self) -> None:
        entities = _entities(_coordinator())
        with _routed():
            await entities["palette2"].async_set_value("#ffffff")
            await entities["speed"].async_select_option("2")
        await _add(entities["palette2"], {"scene": 21002, "palette": [[1, 2, 3]]})
        await _add(entities["speed"], {"scene": 21002, "speed": 0})
        assert entities["palette2"].native_value == "#ffffff"
        assert entities["speed"].current_option == "2"

    async def test_a_restored_index_beyond_the_scene_is_skipped_on_upload(self) -> None:
        coordinator = _coordinator()
        entities = _entities(coordinator)
        await _add(entities["speed"], {"scene": 21002, "speed": 9})
        assert entities["speed"].current_option is None
        with _routed() as route:
            await entities["palette1"].async_set_value("#005bff")
        assert _sent(route) == scenes.scene_frames(TWO_LAYER, 21002)


# ==============================================================================
# 6. Wiring and cleanup
# ==============================================================================


class TestWiring:
    def test_three_palettes_and_one_reset_per_scene_upload_device(self) -> None:
        coordinator = _coordinator()
        palettes = async_scene_palette_text_entities(coordinator, _entry())
        assert [entity.unique_id for entity in palettes] == [
            f"{DEVICE_ID}{SUFFIX_SCENE_PALETTE_TEXT}{layer}" for layer in (1, 2, 3)
        ]
        assert all(isinstance(entity, GoveeScenePaletteText) for entity in palettes)
        (reset,) = async_scene_reset_button_entities(coordinator, _entry())
        assert isinstance(reset, GoveeSceneResetButton)

    def test_the_scenes_option_gates_them(self) -> None:
        coordinator = _coordinator()
        assert async_scene_palette_text_entities(coordinator, _entry(scenes_on=False)) == []
        assert async_scene_reset_button_entities(coordinator, _entry(scenes_on=False)) == []

    async def test_the_platforms_add_them(self) -> None:
        from custom_components.govee import button as button_mod
        from custom_components.govee import text as text_mod

        coordinator = _coordinator()
        entry = _entry()
        entry.runtime_data = coordinator
        entry.options = {CONF_ENABLE_SCENES: True}
        texts: list[Any] = []
        buttons: list[Any] = []
        await text_mod.async_setup_entry(MagicMock(), entry, texts.extend)
        await button_mod.async_setup_entry(MagicMock(), entry, buttons.extend)
        assert sum(isinstance(entity, GoveeScenePaletteText) for entity in texts) == 3
        assert sum(isinstance(entity, GoveeSceneResetButton) for entity in buttons) == 1


def test_every_scene_setting_suffix_is_registered() -> None:
    assert set(SCENE_SETTING_SUFFIXES) == {
        "_scene_speed_select",
        "_scene_direction_select",
        "_scene_reset",
        "_scene_palette_1",
        "_scene_palette_2",
        "_scene_palette_3",
    }


@pytest.mark.parametrize("scenes_on", [False, True])
async def test_cleanup_follows_the_scenes_option(hass, mock_light_device, scenes_on: bool) -> None:
    device_id = mock_light_device.device_id
    entry = _cleanup_entry(hass, **{CONF_ENABLE_SCENES: scenes_on})
    registry = er.async_get(hass)
    specs = [("light", device_id), ("button", f"{device_id}{SUFFIX_SCENE_RESET_BUTTON}")]
    specs += [("text", f"{device_id}{SUFFIX_SCENE_PALETTE_TEXT}{layer}") for layer in (1, 2, 3)]
    ids = _seed(registry, entry, *specs)

    await _async_cleanup_orphaned_entities(hass, entry, _cleanup_coordinator([device_id]))

    kept = {uid for uid, eid in ids.items() if registry.async_get(eid) is not None}
    assert kept == (set(ids) if scenes_on else {device_id})


# ==============================================================================
# 7. Raw power push and mode reports together (H80C5)
# ==============================================================================


class TestPowerPushAndModeReports:
    async def test_power_off_then_on_keeps_the_scene_and_its_settings(self) -> None:
        from custom_components.govee import raw_status
        from tests.test_h80c5_local import POWER_OFF, POWER_ON

        coordinator = _coordinator("Layers")
        entities = _entities(coordinator)
        device = coordinator.devices[DEVICE_ID]
        with _routed():
            await entities["palette1"].async_set_value("#123456")

        assert raw_status.apply_raw_status(coordinator, device, [POWER_OFF]) is True
        assert coordinator.state.power_state is False
        assert coordinator.state.active_scene_name == "Layers"
        assert not any(entity.available for entity in entities.values())

        assert raw_status.apply_raw_status(coordinator, device, [POWER_ON]) is True
        assert coordinator.state.power_state is True
        available = tuple(name for name in _ORDER if entities[name].available)
        assert available == ("speed", "direction", "palette1", "palette2", "reset")
        assert entities["palette1"].native_value == "#123456"

    @pytest.mark.parametrize("sub_mode", [0x0A, 0x13], ids=["diy", "music"])
    async def test_a_diy_or_music_report_leaves_no_scene_setting(self, sub_mode: int) -> None:
        from tests.test_scene_adjust import _mode_report

        coordinator = _coordinator("Layers")
        coordinator._scene_cache = None
        entities = _entities(coordinator)
        device = coordinator.devices[DEVICE_ID]

        assert scene_catalogue.apply_scene_report(coordinator, device, coordinator.state, [_mode_report(sub_mode)])
        assert coordinator.state.active_scene is None
        assert coordinator.state.active_scene_name is None
        assert not any(entity.available for entity in entities.values())
        assert all(entities[name].native_value is None for name in ("palette1", "palette2", "palette3"))
        assert entities["speed"].current_option is None
