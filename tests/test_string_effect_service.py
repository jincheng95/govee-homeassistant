"""``govee.apply_string_effect`` (fork feature)."""

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
    SERVICE_APPLY_STRING_EFFECT_SCHEMA,
    async_apply_string_effect_handler,
)

LOOKUP = "custom_components.govee.services._get_coordinator_for_device"
DEVICE_ID = "AA:BB:CC:DD:EE:FF:60:B0"
COLORS = [[255, 0, 0], [0, 255, 0]]


def _coordinator(sku: str = "H80C5", *, scene: str | None = None) -> MagicMock:
    device = MagicMock()
    device.sku = sku
    device.name = "String lights"
    coordinator = MagicMock()
    coordinator.devices = {DEVICE_ID: device}
    state = GoveeDeviceState.create_empty(DEVICE_ID)
    state.active_scene = "7" if scene else None
    state.active_scene_name = scene
    coordinator.get_state = MagicMock(return_value=state)
    coordinator.state = state
    return coordinator


def _call(**data: Any) -> SimpleNamespace:
    return SimpleNamespace(data=SERVICE_APPLY_STRING_EFFECT_SCHEMA({"device_id": DEVICE_ID, **data}))


async def _run(coordinator: MagicMock, call: SimpleNamespace, *, routed: bool = True) -> AsyncMock:
    route = AsyncMock(return_value=routed)
    with patch(LOOKUP, return_value=(coordinator, DEVICE_ID)), patch.object(
        services.raw_router, "async_route_frames", route
    ):
        await async_apply_string_effect_handler(MagicMock(), call)
    return route


class TestSchema:
    def test_names_are_case_insensitive(self) -> None:
        data = SERVICE_APPLY_STRING_EFFECT_SCHEMA(
            {"device_id": DEVICE_ID, "effect": "Rainbow", "colors": COLORS, "direction": "Clockwise"}
        )
        assert (data["effect"], data["direction"]) == ("rainbow", "clockwise")
        assert "speed" not in data

    @pytest.mark.parametrize(
        "extra",
        [
            {"effect": "sparkle", "colors": COLORS},
            {"effect": "rainbow"},
            {"effect": "rainbow", "colors": []},
            {"effect": "rainbow", "colors": [[1, 1, 1]] * 9},
            {"effect": "rainbow", "colors": [[1, 1]]},
            {"effect": "rainbow", "colors": COLORS, "speed": 101},
            {"effect": "rainbow", "colors": COLORS, "direction": "sideways"},
        ],
    )
    def test_rejects_malformed_input(self, extra: dict[str, Any]) -> None:
        with pytest.raises(vol.Invalid):
            SERVICE_APPLY_STRING_EFFECT_SCHEMA({"device_id": DEVICE_ID, **extra})


class TestHandler:
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

    async def test_unknown_device(self) -> None:
        with patch(LOOKUP, return_value=None), pytest.raises(ServiceValidationError) as err:
            await async_apply_string_effect_handler(MagicMock(), _call(effect="flame", colors=COLORS))
        assert err.value.translation_key == "device_not_found"

    @pytest.mark.parametrize("sku", ["H6076", "H0000"])
    async def test_a_sku_without_scene_upload_is_refused(self, sku: str) -> None:
        with pytest.raises(ServiceValidationError) as err:
            await _run(_coordinator(sku), _call(effect="flame", colors=COLORS))
        assert err.value.translation_key == "string_effect_unsupported"

    @pytest.mark.parametrize(
        "data",
        [
            {"effect": "flame", "colors": COLORS, "direction": "up"},
            {"effect": "rainbow", "colors": [[1, 1, 1]]},
        ],
    )
    async def test_what_the_codec_refuses_is_a_validation_error(self, data: dict[str, Any]) -> None:
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


async def test_the_service_is_registered(hass) -> None:
    services.async_setup_services(hass)
    assert hass.services.has_service("govee", "apply_string_effect")
    with patch.object(services, "async_apply_string_effect_handler", AsyncMock()) as handler:
        await hass.services.async_call(
            "govee", "apply_string_effect", {"device_id": DEVICE_ID, "effect": "flame", "colors": COLORS}, blocking=True
        )
    handler.assert_awaited_once()
