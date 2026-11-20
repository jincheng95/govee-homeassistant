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

from custom_components.govee import services
from custom_components.govee.api.protocol import get_profile, h70cx
from custom_components.govee.models import GoveeDeviceState
from custom_components.govee.services import (
    SERVICE_APPLY_DIY_EFFECT_SCHEMA,
    async_apply_diy_effect_handler,
)

LOOKUP = "custom_components.govee.services._get_coordinator_for_device"
TARGETS = "custom_components.govee.services._targeted_govee_device_ids"
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
        services.raw_router, "async_route_frames", route
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
        ],
    )
    def test_rejects_malformed_input(self, extra: dict[str, Any]) -> None:
        with pytest.raises(vol.Invalid):
            SERVICE_APPLY_DIY_EFFECT_SCHEMA(extra)


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


class TestTargeting:
    async def test_two_diy_capable_devices_are_refused(self) -> None:
        coordinator = _coordinator({DEVICE_ID: "H80C5", OTHER_ID: "H60B0"})
        with pytest.raises(HomeAssistantError, match="exactly one device"):
            await _run(coordinator, _call(effect="flame", colors=COLORS))

    async def test_a_plain_light_beside_the_string_light_is_ignored(self) -> None:
        coordinator = _coordinator({DEVICE_ID: "H80C5", OTHER_ID: "H0000"})
        route = await _run(coordinator, _call(effect="flame", colors=COLORS))
        assert route.await_args.args[1] == DEVICE_ID

    async def test_a_zone_lamp_still_takes_the_zone_path(self) -> None:
        coordinator = _coordinator({DEVICE_ID: "H60B0"})
        sender = AsyncMock()
        with patch.object(services, "async_send_diy_effect", sender), patch.object(services, "diy_store", MagicMock()):
            await _run(coordinator, _call(ring_mode="gradient", ring_colors=[[1, 2, 3]]))
        sender.assert_awaited_once()

    async def test_a_light_with_no_diy_at_all_keeps_the_old_error(self) -> None:
        coordinator = _coordinator({DEVICE_ID: "H0000"})
        with pytest.raises(HomeAssistantError, match="does not support DIY effects"):
            await _run(coordinator, _call(effect="flame", colors=COLORS))
