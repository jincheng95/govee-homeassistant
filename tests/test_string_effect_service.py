"""``govee.apply_diy_effect`` on a whole-light-effect device (fork feature).

One action serves both shapes of DIY: per-zone fields on a multi-zone lamp,
and ``effect`` / ``colors`` / ``speed`` / ``direction`` on a string light.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.govee import diy_actions
from custom_components.govee.api.protocol import get_profile, h70cx, h70cx_basic
from custom_components.govee.models import GoveeDeviceState
from custom_components.govee.diy_actions import (
    SERVICE_APPLY_DIY_EFFECT_SCHEMA,
    async_apply_diy_effect_handler,
)

LOOKUP = "custom_components.govee.services._get_coordinator_for_device"
TARGETS = "custom_components.govee.diy_actions._targeted_govee_device_ids"
DEVICE_ID = "AA:BB:CC:DD:EE:FF:60:B0"
OTHER_ID = "AA:BB:CC:DD:EE:FF:60:B1"
COLORS = [[255, 0, 0], [0, 255, 0]]


def _coordinator(skus: dict[str, str] | None = None, *, scene: str | None = None) -> MagicMock:
    coordinator = MagicMock()
    coordinator.devices = {}
    for device_id, sku in (skus or {DEVICE_ID: "H80C5"}).items():
        device = MagicMock()
        device.sku = sku
        device.name = f"Light {sku}"
        device.device_id = device_id
        coordinator.devices[device_id] = device
    state = GoveeDeviceState.create_empty(DEVICE_ID)
    state.active_scene = "7" if scene else None
    state.active_scene_name = scene
    coordinator.get_state = MagicMock(return_value=state)
    coordinator.state = state
    return coordinator


def _call(**data: Any) -> SimpleNamespace:
    return SimpleNamespace(data=SERVICE_APPLY_DIY_EFFECT_SCHEMA({"device_id": [DEVICE_ID], **data}))


async def _run(coordinator: MagicMock, call: SimpleNamespace, *, routed: bool = True) -> AsyncMock:
    route = AsyncMock(return_value=routed)

    def _lookup(hass: Any, raw_id: str) -> Any:
        return (coordinator, raw_id) if raw_id in coordinator.devices else None

    with patch(TARGETS, return_value=list(coordinator.devices) + ["unknown"]), patch(LOOKUP, side_effect=_lookup), patch.object(
        diy_actions.raw_router, "async_route_frames", route
    ):
        await async_apply_diy_effect_handler(MagicMock(), call)
    return route


class TestSchema:
    def test_names_are_case_insensitive(self) -> None:
        data = SERVICE_APPLY_DIY_EFFECT_SCHEMA({"effect": "Rainbow", "colors": COLORS, "direction": "Clockwise"})
        assert (data["effect"], data["direction"]) == ("rainbow", "clockwise")
        assert "speed" not in data

    @pytest.mark.parametrize(
        "extra",
        [
            {"effect": "sparkle", "colors": COLORS},
            {"effect": "rainbow", "colors": []},
            {"effect": "rainbow", "colors": [[1, 1, 1]] * 9},
            {"effect": "rainbow", "colors": [[1, 1]]},
            {"effect": "rainbow", "colors": COLORS, "speed": 101},
            {"effect": "rainbow", "colors": COLORS, "direction": "sideways"},
            {"effect": "combo", "colors": COLORS, "combine": ["marquee_1"]},
            {"effect": "combo", "colors": COLORS, "combine": []},
            {"effect": "combo", "colors": COLORS, "combine": ["breathe"] * 5},
        ],
    )
    def test_rejects_malformed_input(self, extra: dict[str, Any]) -> None:
        with pytest.raises(vol.Invalid):
            SERVICE_APPLY_DIY_EFFECT_SCHEMA(extra)

    def test_basic_names_and_combine_are_lowered(self) -> None:
        data = SERVICE_APPLY_DIY_EFFECT_SCHEMA({"effect": "Combo", "colors": COLORS, "combine": "Breathe"})
        assert (data["effect"], data["combine"]) == ("combo", ["breathe"])


class TestBasicEffect:
    async def test_routes_a_basic_effect(self) -> None:
        coordinator = _coordinator(scene="Rainbow B")
        route = await _run(coordinator, _call(effect="flow_1", colors=COLORS, speed=20, direction="anticlockwise"))

        expected = h70cx_basic.effect_frames("flow_1", COLORS, speed=20, direction="anticlockwise")
        assert list(route.await_args.args[4]) == expected
        assert coordinator.state.active_scene_name is None

    async def test_routes_a_combo(self) -> None:
        coordinator = _coordinator()
        route = await _run(coordinator, _call(effect="combo", colors=COLORS, combine=["flow_1", "breathe"]))

        expected = h70cx_basic.effect_frames("combo", COLORS, combine=["flow_1", "breathe"])
        assert list(route.await_args.args[4]) == expected

    @pytest.mark.parametrize(
        ("data", "reason"),
        [
            ({"effect": "marquee_1", "colors": [[1, 1, 1]]}, "marquee_1 takes 2 to 8 colours"),
            ({"effect": "fade_1", "colors": COLORS, "direction": "clockwise"}, "fade_1 has no direction"),
            ({"effect": "flow_1", "colors": COLORS, "direction": "up"}, "flow_1 turns clockwise or anticlockwise, not 'up'"),
            ({"effect": "combo", "colors": COLORS}, "combo needs 1 to 4 effects to combine"),
            ({"effect": "combo", "colors": COLORS, "combine": ["fade_1"], "speed": 0}, "speed must be from 1 to 100"),
            ({"effect": "combo", "colors": COLORS, "combine": ["fade_1", "fade_1"]}, "combo takes each effect once"),
            ({"effect": "breathe", "colors": COLORS, "combine": ["fade_1"]}, "only combo takes effects to combine"),
            ({"effect": "rainbow", "colors": COLORS, "combine": ["fade_1"]}, "only combo takes effects to combine"),
        ],
    )
    async def test_invalid_input_is_a_translated_validation_error(self, data: dict[str, Any], reason: str) -> None:
        coordinator = _coordinator()
        with pytest.raises(ServiceValidationError) as err:
            await _run(coordinator, _call(**data))
        assert err.value.translation_key == "invalid_string_effect"
        assert err.value.translation_placeholders == {"device": "Light H80C5", "reason": reason}


class TestWholeLightEffect:
    async def test_routes_the_codecs_frames(self) -> None:
        coordinator = _coordinator()
        route = await _run(coordinator, _call(effect="rotate", colors=COLORS, speed=40, direction="anticlockwise"))

        expected = h70cx.effect_frames("rotate", [(255, 0, 0), (0, 255, 0)], speed=40, direction="anticlockwise")
        args = route.await_args.args
        assert args[:4] == (coordinator, DEVICE_ID, "H80C5", get_profile("H80C5"))
        assert list(args[4]) == expected

    async def test_an_effect_clears_the_running_scene(self) -> None:
        coordinator = _coordinator(scene="Rainbow B")
        await _run(coordinator, _call(effect="flame", colors=COLORS))
        assert coordinator.state.active_scene is None
        assert coordinator.state.active_scene_name is None
        coordinator.async_update_listeners.assert_called_once()

    async def test_no_scene_means_no_listener_update(self) -> None:
        coordinator = _coordinator()
        await _run(coordinator, _call(effect="flame", colors=COLORS))
        coordinator.async_update_listeners.assert_not_called()

    @pytest.mark.parametrize(
        "data",
        [
            {"effect": "flame", "colors": COLORS, "direction": "up"},
            {"effect": "rainbow", "colors": [[1, 1, 1]]},
            {"effect": "rainbow"},
            {"colors": COLORS},
            {"ring_mode": "gradient", "ring_colors": [[1, 2, 3]]},
        ],
    )
    async def test_what_cannot_be_composed_is_a_validation_error(self, data: dict[str, Any]) -> None:
        coordinator = _coordinator(scene="Rainbow B")
        with pytest.raises(ServiceValidationError) as err:
            await _run(coordinator, _call(**data))
        assert err.value.translation_key == "invalid_string_effect"
        assert coordinator.state.active_scene_name == "Rainbow B"

    async def test_a_refused_upload_raises_and_keeps_the_scene(self) -> None:
        coordinator = _coordinator(scene="Rainbow B")
        with pytest.raises(HomeAssistantError) as err:
            await _run(coordinator, _call(effect="flame", colors=COLORS), routed=False)
        assert err.value.translation_key == "command_failed"
        assert coordinator.state.active_scene_name == "Rainbow B"


class TestAppNamedEffect:
    def test_shape_and_center_are_lowered(self) -> None:
        data = SERVICE_APPLY_DIY_EFFECT_SCHEMA(
            {"effect": "Flexing", "colors": COLORS, "shape": "Round", "center": "Bottom_Right"}
        )
        assert (data["effect"], data["shape"], data["center"]) == ("flexing", "round", "bottom_right")

    @pytest.mark.parametrize("extra", [{"shape": "oval"}, {"center": "middle"}, {"shape": 3}])
    def test_unknown_shape_or_center_is_malformed(self, extra: dict[str, Any]) -> None:
        with pytest.raises(vol.Invalid):
            SERVICE_APPLY_DIY_EFFECT_SCHEMA({"effect": "flexing", "colors": COLORS, **extra})

    async def test_routes_flexing_with_its_options(self) -> None:
        coordinator = _coordinator()
        route = await _run(
            coordinator, _call(effect="flexing", colors=COLORS, speed=9, shape="square", center="top_center")
        )

        record = bytes([0x0F, 0x01, 0x09, 0x01, 0x01, 0x03, 0x02, 255, 0, 0, 0, 255, 0])
        expected = h70cx.effect_frames("flexing", COLORS, speed=9, shape="square", center="top_center")
        frames = list(route.await_args.args[4])
        assert frames == expected
        assert frames[0][5:19] == (bytes([len(record), 0]) + record)[:14]

    async def test_routes_smudge_down(self) -> None:
        coordinator = _coordinator()
        route = await _run(coordinator, _call(effect="smudge", colors=COLORS, direction="down"))

        record = bytes([0x11, 0x02, 0x32, 0x02, 255, 0, 0, 0, 255, 0])
        frames = list(route.await_args.args[4])
        assert frames[0][5:7 + len(record)] == bytes([len(record), 0]) + record

    @pytest.mark.parametrize(
        ("data", "reason"),
        [
            ({"effect": "smudge", "direction": "clockwise"}, "smudge takes only up, down, not 'clockwise'"),
            ({"effect": "spin_3", "colors": [[1, 2, 3]] * 3, "direction": "clockwise"}, "spin_3 has no direction"),
            ({"effect": "candy_crush", "direction": "up"}, "candy_crush has no direction"),
            ({"effect": "spiral", "direction": "up"}, (
                "spiral takes only clockwise upward, anticlockwise upward, clockwise downward, "
                "anticlockwise downward, not 'up'"
            )),
            ({"effect": "bounce_1"}, "bounce_1 takes 4 to 8 colours"),
            ({"effect": "smudge", "shape": "round"}, "smudge has no shape"),
            ({"effect": "rainbow", "center": "center"}, "rainbow has no center"),
            ({"effect": "breathe", "shape": "round"}, "breathe has no shape"),
            ({"effect": "flow_1", "center": "center"}, "flow_1 has no center"),
        ],
    )
    async def test_options_an_effect_lacks_are_translated_errors(self, data: dict[str, Any], reason: str) -> None:
        coordinator = _coordinator(scene="Rainbow B")
        with pytest.raises(ServiceValidationError) as err:
            await _run(coordinator, _call(**{"colors": COLORS, **data}))
        assert err.value.translation_key == "invalid_string_effect"
        assert err.value.translation_placeholders == {"device": "Light H80C5", "reason": reason}
        assert coordinator.state.active_scene_name == "Rainbow B"


class TestTargeting:
    async def test_two_diy_capable_devices_are_refused(self) -> None:
        coordinator = _coordinator({DEVICE_ID: "H80C5", OTHER_ID: "H60B0"})
        with pytest.raises(HomeAssistantError) as err:
            await _run(coordinator, _call(effect="flame", colors=COLORS))
        assert err.value.translation_key == "diy_target_ambiguous"
        assert err.value.translation_placeholders["count"] == "2"

    async def test_a_plain_light_beside_the_string_light_is_ignored(self) -> None:
        coordinator = _coordinator({DEVICE_ID: "H80C5", OTHER_ID: "H0000"})
        route = await _run(coordinator, _call(effect="flame", colors=COLORS))
        assert route.await_args.args[1] == DEVICE_ID

    async def test_a_zone_lamp_still_takes_the_zone_path(self) -> None:
        coordinator = _coordinator({DEVICE_ID: "H60B0"})
        sender = AsyncMock()
        with patch.object(diy_actions, "async_send_diy_effect", sender), patch.object(diy_actions, "diy_store", MagicMock()):
            await _run(coordinator, _call(ring_mode="gradient", ring_colors=[[1, 2, 3]]))
        sender.assert_awaited_once()

    async def test_a_light_with_no_diy_at_all_keeps_the_old_error(self) -> None:
        coordinator = _coordinator({DEVICE_ID: "H0000"})
        with pytest.raises(HomeAssistantError, check=lambda err: err.translation_key == "diy_unsupported"):
            await _run(coordinator, _call(effect="flame", colors=COLORS))


LAYERS = [
    {"effect": "flow_2", "colors": COLORS, "speed": 90, "direction": "upper left"},
    {"effect": "starlight", "colors": COLORS, "speed": 3},
]


class TestLayeredEffect:
    def test_layer_fields_are_lowered(self) -> None:
        data = SERVICE_APPLY_DIY_EFFECT_SCHEMA(
            {"layers": [{"effect": "Flexing", "colors": COLORS, "shape": "Round", "center": "Top_Center"}]}
        )
        assert data["layers"] == [
            {"effect": "flexing", "colors": [(255, 0, 0), (0, 255, 0)], "shape": "round", "center": "top_center"}
        ]

    @pytest.mark.parametrize(
        "layer",
        [
            {"effect": "flow"},
            {"colors": COLORS},
            {"effect": "sparkle", "colors": COLORS},
            {"effect": "flow", "colors": COLORS, "combine": ["breathe"]},
            {"effect": "flow", "colors": COLORS, "speed": 101},
            {"effect": "flow", "colors": [[1, 1]]},
        ],
    )
    def test_a_malformed_layer_is_rejected(self, layer: dict[str, Any]) -> None:
        with pytest.raises(vol.Invalid):
            SERVICE_APPLY_DIY_EFFECT_SCHEMA({"layers": [LAYERS[0], layer]})

    async def test_routes_the_layered_frames(self) -> None:
        coordinator = _coordinator(scene="Rainbow B")
        route = await _run(coordinator, _call(layers=LAYERS))

        expected = h70cx.layered_frames(
            [
                h70cx.Layer("flow_2", [(255, 0, 0), (0, 255, 0)], speed=90, direction="upper left"),
                h70cx.Layer("starlight", [(255, 0, 0), (0, 255, 0)], speed=3),
            ]
        )
        args = route.await_args.args
        assert args[:4] == (coordinator, DEVICE_ID, "H80C5", get_profile("H80C5"))
        assert list(args[4]) == expected
        assert coordinator.state.active_scene_name is None

    @pytest.mark.parametrize(
        ("extra", "fields"),
        [
            ({"effect": "flow"}, "effect"),
            ({"colors": COLORS}, "colors"),
            ({"speed": 5, "direction": "up"}, "speed, direction"),
            ({"shape": "round", "center": "center"}, "shape, center"),
            ({"combine": ["breathe"]}, "combine"),
        ],
    )
    async def test_layers_beside_the_effect_fields_are_refused(self, extra: dict[str, Any], fields: str) -> None:
        coordinator = _coordinator(scene="Rainbow B")
        with pytest.raises(ServiceValidationError) as err:
            await _run(coordinator, _call(layers=LAYERS, **extra))
        assert err.value.translation_key == "layers_with_effect_fields"
        assert err.value.translation_placeholders == {"device": "Light H80C5", "fields": fields}
        assert coordinator.state.active_scene_name == "Rainbow B"

    async def test_four_layers_are_refused(self) -> None:
        coordinator = _coordinator()
        with pytest.raises(ServiceValidationError) as err:
            await _run(coordinator, _call(layers=[LAYERS[0]] * 4))
        assert err.value.translation_key == "invalid_layer_count"
        assert err.value.translation_placeholders == {
            "device": "Light H80C5",
            "count": "4",
            "min": "1",
            "max": "3",
        }

    async def test_one_layer_is_a_layered_effect(self) -> None:
        coordinator = _coordinator()
        route = await _run(coordinator, _call(layers=[LAYERS[1]]))

        expected = h70cx.layered_frames([h70cx.Layer("starlight", [(255, 0, 0), (0, 255, 0)], speed=3)])
        assert list(route.await_args.args[4]) == expected

    @pytest.mark.parametrize(
        ("layer", "index", "reason"),
        [
            ({"effect": "smudge", "colors": COLORS, "direction": "clockwise"}, "2", "smudge takes only up, down, not 'clockwise'"),
            ({"effect": "bounce_1", "colors": COLORS}, "2", "bounce_1 takes 4 to 8 colours"),
            ({"effect": "rainbow", "colors": COLORS, "center": "center"}, "2", "rainbow has no center"),
        ],
    )
    async def test_a_layer_error_carries_the_layer_index(self, layer: dict[str, Any], index: str, reason: str) -> None:
        coordinator = _coordinator()
        with pytest.raises(ServiceValidationError) as err:
            await _run(coordinator, _call(layers=[LAYERS[0], layer]))
        assert err.value.translation_key == "invalid_string_effect_layer"
        assert err.value.translation_placeholders == {"device": "Light H80C5", "layer": index, "reason": reason}

    @pytest.mark.parametrize("effect", [*h70cx_basic.NAMES])
    async def test_an_older_format_effect_is_not_a_layer(self, effect: str) -> None:
        coordinator = _coordinator()
        with pytest.raises(ServiceValidationError) as err:
            await _run(coordinator, _call(layers=[{"effect": effect, "colors": COLORS}, *LAYERS]))
        assert err.value.translation_key == "invalid_string_effect_layer"
        assert err.value.translation_placeholders["layer"] == "1"
        assert err.value.translation_placeholders["reason"].startswith(f"{effect} cannot be a layer")

    async def test_an_upload_over_the_frame_limit_is_a_translated_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(h70cx, "MAX_FRAMES", 2)
        coordinator = _coordinator()
        with pytest.raises(ServiceValidationError) as err:
            await _run(coordinator, _call(layers=LAYERS))
        assert err.value.translation_key == "invalid_string_effect"
        assert err.value.translation_placeholders["reason"].endswith("the limit is 2")

    async def test_a_refused_upload_raises(self) -> None:
        coordinator = _coordinator(scene="Rainbow B")
        with pytest.raises(HomeAssistantError) as err:
            await _run(coordinator, _call(layers=LAYERS), routed=False)
        assert err.value.translation_key == "command_failed"
        assert coordinator.state.active_scene_name == "Rainbow B"
