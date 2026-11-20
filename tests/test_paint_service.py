"""``govee.paint_bulbs`` (fork feature).

The handler resolves the device, refuses SKUs whose profile declares no
per-LED paint, turns the call into frames with the codec, and routes them.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.govee import services
from custom_components.govee.api.protocol import get_profile, paint
from custom_components.govee.models import GoveeDeviceState
from custom_components.govee.services import (
    SERVICE_PAINT_BULBS_SCHEMA,
    _expand_bulbs,
    async_paint_bulbs_handler,
)

LOOKUP = "custom_components.govee.services._get_coordinator_for_device"
DEVICE_ID = "AA:BB:CC:DD:EE:FF:60:B0"


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
    return SimpleNamespace(data=SERVICE_PAINT_BULBS_SCHEMA({"device_id": DEVICE_ID, **data}))


async def _run(coordinator: MagicMock, call: SimpleNamespace, *, routed: bool = True) -> AsyncMock:
    route = AsyncMock(return_value=routed)
    with patch(LOOKUP, return_value=(coordinator, DEVICE_ID)), patch.object(
        services.raw_router, "async_route_frames", route
    ):
        await async_paint_bulbs_handler(MagicMock(), call)
    return route


class TestExpandBulbs:
    def test_numbers_and_ranges(self) -> None:
        assert _expand_bulbs([3, "5-7", "9", " 11 - 12 "]) == [3, 5, 6, 7, 9, 11, 12]

    @pytest.mark.parametrize("entry", ["7-5", "a-b", "x", "", "1-"])
    def test_bad_entries(self, entry: str) -> None:
        with pytest.raises(ValueError):
            _expand_bulbs([entry])


class TestSchema:
    def test_defaults(self) -> None:
        data = SERVICE_PAINT_BULBS_SCHEMA({"device_id": DEVICE_ID})
        assert data["colors"] == []
        assert data["base_color"] == (0, 0, 0)
        assert data["base_brightness"] == 100

    @pytest.mark.parametrize(
        "extra",
        [
            {"colors": [{"rgb_color": [1, 2], "bulbs": [0]}]},
            {"colors": [{"rgb_color": [1, 2, 300], "bulbs": [0]}]},
            {"colors": [{"bulbs": [0]}]},
            {"base_brightness": 101},
            {"base_color": [0, 0]},
        ],
    )
    def test_rejects_malformed_input(self, extra: dict[str, Any]) -> None:
        with pytest.raises(vol.Invalid):
            SERVICE_PAINT_BULBS_SCHEMA({"device_id": DEVICE_ID, **extra})


class TestHandler:
    async def test_routes_the_codecs_frames(self) -> None:
        coordinator = _coordinator()
        call = _call(
            colors=[{"rgb_color": [255, 0, 0], "bulbs": ["0-9"]}, {"rgb_color": [0, 255, 0], "bulbs": [10, "290-299"]}],
            base_color=[0, 0, 32],
            base_brightness=40,
        )

        route = await _run(coordinator, call)

        expected = paint.paint_frames(
            [((255, 0, 0), range(10)), ((0, 255, 0), [10, *range(290, 300)])],
            bulb_count=300,
            base_color=(0, 0, 32),
            base_brightness=40,
        )
        args = route.await_args.args
        assert args[:4] == (coordinator, DEVICE_ID, "H80C5", get_profile("H80C5"))
        assert list(args[4]) == expected

    async def test_a_long_paint_goes_out_as_spaced_messages(self) -> None:
        colors = [{"rgb_color": [index, 0, 0], "bulbs": [index]} for index in range(30)]
        sleeps: list[float] = []

        async def _sleep(seconds: float) -> None:
            sleeps.append(seconds)

        with patch.object(services.asyncio, "sleep", _sleep):
            route = await _run(_coordinator(), _call(colors=colors))

        sent = [list(call.args[4]) for call in route.await_args_list]
        expected = paint.paint_frames([((index, 0, 0), [index]) for index in range(30)], bulb_count=300)
        assert len(sent) > 1
        assert all(len(message) <= paint.MESSAGE_FRAMES for message in sent)
        assert [frame for message in sent for frame in message] == expected
        assert sleeps == [paint.MESSAGE_GAP_SECONDS] * (len(sent) - 1)

    async def test_a_refused_later_message_stops_the_upload(self) -> None:
        colors = [{"rgb_color": [index, 0, 0], "bulbs": [index]} for index in range(30)]
        route = AsyncMock(side_effect=[True, False, True])
        with patch(LOOKUP, return_value=(_coordinator(), DEVICE_ID)), patch.object(
            services.raw_router, "async_route_frames", route
        ), patch.object(services.asyncio, "sleep", AsyncMock()), pytest.raises(HomeAssistantError):
            await async_paint_bulbs_handler(MagicMock(), _call(colors=colors))
        assert route.await_count == 2

    async def test_a_paint_clears_the_running_scene(self) -> None:
        coordinator = _coordinator(scene="Rainbow B")
        await _run(coordinator, _call())
        assert coordinator.state.active_scene is None
        assert coordinator.state.active_scene_name is None
        coordinator.async_update_listeners.assert_called_once()

    async def test_no_scene_means_no_listener_update(self) -> None:
        coordinator = _coordinator()
        await _run(coordinator, _call())
        coordinator.async_update_listeners.assert_not_called()

    async def test_missing_state_is_tolerated(self) -> None:
        coordinator = _coordinator()
        coordinator.get_state = MagicMock(return_value=None)
        await _run(coordinator, _call())
        coordinator.async_update_listeners.assert_not_called()

    async def test_unknown_device(self) -> None:
        with patch(LOOKUP, return_value=None), pytest.raises(ServiceValidationError) as err:
            await async_paint_bulbs_handler(MagicMock(), _call())
        assert err.value.translation_key == "device_not_found"

    @pytest.mark.parametrize("sku", ["H6076", "H0000"])
    async def test_a_sku_without_paint_is_refused(self, sku: str) -> None:
        with pytest.raises(ServiceValidationError) as err:
            await _run(_coordinator(sku), _call())
        assert err.value.translation_key == "bulb_paint_unsupported"

    @pytest.mark.parametrize(
        "colors",
        [
            [{"rgb_color": [1, 1, 1], "bulbs": [300]}],
            [{"rgb_color": [1, 1, 1], "bulbs": ["9-3"]}],
            [{"rgb_color": [1, 1, 1], "bulbs": ["nope"]}],
        ],
    )
    async def test_bad_bulbs_are_a_validation_error(self, colors: list[dict[str, Any]]) -> None:
        coordinator = _coordinator(scene="Rainbow B")
        with pytest.raises(ServiceValidationError) as err:
            await _run(coordinator, _call(colors=colors))
        assert err.value.translation_key == "invalid_bulb_paint"
        assert coordinator.state.active_scene_name == "Rainbow B"

    async def test_a_paint_too_large_for_one_message_is_refused(self) -> None:
        colors = [{"rgb_color": [index, 0, 0], "bulbs": [index]} for index in range(200)]
        with pytest.raises(ServiceValidationError) as err:
            await _run(_coordinator(), _call(colors=colors))
        assert err.value.translation_key == "invalid_bulb_paint"
        assert "frames" in err.value.translation_placeholders["reason"]

    async def test_a_refused_upload_raises_and_keeps_the_scene(self) -> None:
        coordinator = _coordinator(scene="Rainbow B")
        with pytest.raises(HomeAssistantError) as err:
            await _run(coordinator, _call(), routed=False)
        assert err.value.translation_key == "command_failed"
        assert coordinator.state.active_scene_name == "Rainbow B"


async def test_the_service_is_registered(hass) -> None:
    services.async_setup_services(hass)
    assert hass.services.has_service("govee", "paint_bulbs")
    with patch.object(services, "async_paint_bulbs_handler", AsyncMock()) as handler:
        await hass.services.async_call("govee", "paint_bulbs", {"device_id": DEVICE_ID}, blocking=True)
    handler.assert_awaited_once()
