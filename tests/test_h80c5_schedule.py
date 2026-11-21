"""On-device schedules of the H80C5 string lights over raw frames (fork feature).

1. Light timers: the frame, the ``aa 23`` reply decoder and
   ``govee.set_light_timer``.
2. Sleep timer: the frame, the ``aa 11`` reply decoder and
   ``govee.set_sleep_timer``.

The actions run on a real ``hass`` through ``hass.services.async_call``, so
every payload passes the real schema. The coordinator is a mock and the raw
route is patched. Device ids are the suite's synthetic ones.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.govee import raw_actions, services
from custom_components.govee.api import raw_router
from custom_components.govee.api.protocol import (
    FrameError,
    GoveeCodec,
    LightTimer,
    SleepTimer,
    UnsupportedCapabilityError,
    decode_status,
    get_profile,
)
from custom_components.govee.const import CONF_ENABLE_LAN_RAW_WRITE, DOMAIN
from custom_components.govee.models import GoveeDevice
from custom_components.govee.models.device import DEVICE_TYPE_LIGHT

DEVICE_ID = "AA:BB:CC:DD:EE:FF:60:B0"
SKU = "H80C5"


def _codec(sku: str = SKU) -> GoveeCodec:
    return GoveeCodec(get_profile(sku))


def _reply(frame: bytes) -> bytes:
    """A write frame turned into the matching ``aa`` reply."""
    body = bytes([0xAA]) + frame[1:19]
    checksum = 0
    for byte in body:
        checksum ^= byte
    return body + bytes([checksum])


def _light(sku: str = SKU, device_id: str = DEVICE_ID, *, is_group: bool = False) -> GoveeDevice:
    return GoveeDevice(
        device_id=device_id,
        sku=sku,
        name="String lights",
        device_type=DEVICE_TYPE_LIGHT,
        capabilities=(),
        is_group=is_group,
    )


def _coordinator(*devices: GoveeDevice, raw: bool = True) -> Any:
    coordinator = MagicMock()
    coordinator.config_entry.options = {CONF_ENABLE_LAN_RAW_WRITE: raw}
    coordinator.devices = {device.device_id: device for device in devices or (_light(),)}
    return coordinator


@pytest.fixture
async def actions(hass, monkeypatch):
    """Register the actions and resolve device ids against a mock coordinator."""
    holder: dict[str, Any] = {"coordinator": _coordinator()}
    services.async_setup_services(hass)
    monkeypatch.setattr(
        services,
        "_get_coordinator_for_device",
        lambda _hass, device_id: (
            (holder["coordinator"], device_id) if device_id in holder["coordinator"].devices else None
        ),
    )
    return holder


@pytest.fixture
def route():
    with patch.object(raw_router, "async_route_frames", AsyncMock(return_value=True)) as mock:
        yield mock


async def _call(hass, service: str, **data: Any) -> None:
    await hass.services.async_call(DOMAIN, service, {"device_id": DEVICE_ID, **data}, blocking=True)


def _sent(route: AsyncMock) -> list[bytes]:
    return list(route.await_args.args[4])


class TestRegistration:
    async def test_the_actions_are_registered(self, hass, actions) -> None:
        assert hass.services.has_service(DOMAIN, raw_actions.SERVICE_SET_LIGHT_TIMER)
        assert hass.services.has_service(DOMAIN, raw_actions.SERVICE_SET_SLEEP_TIMER)


class TestCommonGates:
    async def test_an_unknown_device_is_a_translated_error(self, hass, actions, route) -> None:
        with pytest.raises(ServiceValidationError) as err:
            await hass.services.async_call(
                DOMAIN, raw_actions.SERVICE_SET_LIGHT_TIMER, {"device_id": "missing", "slot": 1}, blocking=True
            )
        assert err.value.translation_key == "device_not_found"
        route.assert_not_awaited()

    @pytest.mark.parametrize(
        "device",
        [_light(sku="H6076"), _light(sku="H7099"), _light(is_group=True)],
        ids=["no-capability", "no-profile", "group"],
    )
    @pytest.mark.parametrize(
        ("service", "data"),
        [
            (raw_actions.SERVICE_SET_LIGHT_TIMER, {"slot": 1, "enabled": False}),
            (raw_actions.SERVICE_SET_SLEEP_TIMER, {}),
        ],
    )
    async def test_a_device_without_the_capability_is_refused(
        self, hass, actions, route, device, service, data
    ) -> None:
        actions["coordinator"] = _coordinator(device)
        with pytest.raises(ServiceValidationError) as err:
            await _call(hass, service, **data)
        assert err.value.translation_key == "raw_action_unsupported"
        assert err.value.translation_placeholders == {"device": "String lights", "action": f"govee.{service}"}
        route.assert_not_awaited()

    async def test_no_raw_pipe_is_a_translated_error(self, hass, actions) -> None:
        with patch.object(raw_router, "async_route_frames", AsyncMock(return_value=False)):
            with pytest.raises(HomeAssistantError) as err:
                await _call(hass, raw_actions.SERVICE_SET_LIGHT_TIMER, slot=1, enabled=False)
        assert err.value.translation_key == "raw_action_unavailable"

    async def test_the_raw_option_off_sends_nothing(self, hass, actions, route) -> None:
        actions["coordinator"] = _coordinator(raw=False)
        with pytest.raises(HomeAssistantError) as err:
            await _call(hass, raw_actions.SERVICE_SET_LIGHT_TIMER, slot=1, enabled=False)
        assert err.value.translation_key == "raw_action_unavailable"
        route.assert_not_awaited()

    async def test_the_route_gets_the_device_and_profile(self, hass, actions, route) -> None:
        await _call(hass, raw_actions.SERVICE_SET_LIGHT_TIMER, slot=1, enabled=False)
        args = route.await_args
        assert args.args[:4] == (actions["coordinator"], DEVICE_ID, SKU, get_profile(SKU))
        assert args.kwargs == {"what": "set_light_timer"}


# ==============================================================================
# 1. Light timers
# ==============================================================================

TIMER_OFF_ONCE = bytes([0x33, 0x23, 0x00, 0x80, 0x00, 0x00, 0x80, *bytes(12), 0x10])
TIMER_ON_MON_SUN = bytes([0x33, 0x23, 0x00, 0x81, 0x00, 0x00, 0xC1, *bytes(12), 0x50])
TIMER_DISABLED = bytes([0x33, 0x23, 0x00, 0x00, 0x00, 0x00, 0x80, *bytes(12), 0x90])
TIMER_REPLY_UNSET = bytes([0xAA, 0x23, 0xFF, *([0x00, 0x00, 0x00, 0x80] * 4), 0x76])


class TestLightTimerFrame:
    @pytest.mark.parametrize(
        ("kwargs", "expected"),
        [
            ({"enabled": True, "turn_on": False, "weekdays": ()}, TIMER_OFF_ONCE),
            ({"enabled": True, "turn_on": True, "weekdays": (0, 6)}, TIMER_ON_MON_SUN),
            ({"enabled": False, "turn_on": False, "weekdays": ()}, TIMER_DISABLED),
        ],
    )
    def test_frames_are_byte_exact(self, kwargs: dict[str, Any], expected: bytes) -> None:
        assert _codec().light_timer(0, hour=0, minute=0, **kwargs) == expected

    def test_slot_and_time_land_in_place(self) -> None:
        frame = _codec().light_timer(3, enabled=True, turn_on=True, hour=23, minute=45, weekdays=(1, 2))
        assert frame[:7] == bytes([0x33, 0x23, 0x03, 0x81, 23, 45, 0x86])

    def test_four_slots(self) -> None:
        assert _codec().timer_slots() == 4

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"slot": 4},
            {"slot": -1},
            {"hour": 24},
            {"minute": 60},
            {"weekdays": (7,)},
        ],
    )
    def test_out_of_range_is_refused(self, kwargs: dict[str, Any]) -> None:
        values = {"slot": 0, "enabled": True, "turn_on": True, "hour": 0, "minute": 0, **kwargs}
        slot = values.pop("slot")
        with pytest.raises(FrameError):
            _codec().light_timer(slot, **values)

    @pytest.mark.parametrize("sku", ["H6076", "H60B0"])
    def test_other_skus_do_not_encode_it(self, sku: str) -> None:
        with pytest.raises(UnsupportedCapabilityError):
            _codec(sku).light_timer(0, enabled=False, turn_on=False, hour=0, minute=0)


class TestLightTimerDecode:
    def test_the_unset_reply(self) -> None:
        unset = LightTimer(enabled=False, turn_on=False, hour=0, minute=0, weekdays=())
        assert decode_status([TIMER_REPLY_UNSET]).timers == (unset,) * 4

    def test_round_trip(self) -> None:
        groups = [
            (0x81, 6, 30, 0x9F),
            (0x80, 23, 0, 0x80),
            (0x01, 7, 15, 0xE0),
            (0x00, 0, 0, 0x80),
        ]
        reply = _reply(bytes([0x33, 0x23, 0xFF, *[byte for group in groups for byte in group], *bytes(3)]))
        assert decode_status([reply]).timers == (
            LightTimer(enabled=True, turn_on=True, hour=6, minute=30, weekdays=(0, 1, 2, 3, 4)),
            LightTimer(enabled=True, turn_on=False, hour=23, minute=0, weekdays=()),
            LightTimer(enabled=False, turn_on=True, hour=7, minute=15, weekdays=(5, 6)),
            LightTimer(enabled=False, turn_on=False, hour=0, minute=0, weekdays=()),
        )

    @pytest.mark.parametrize(
        "group",
        [(0x82, 0, 0, 0x80), (0x80, 24, 0, 0x80), (0x80, 0, 60, 0x80), (0x80, 0, 0, 0x01)],
        ids=["flags", "hour", "minute", "repeat-marker"],
    )
    def test_an_unexpected_value_skips_the_reply(self, group: tuple[int, ...]) -> None:
        reply = _reply(bytes([0x33, 0x23, 0xFF, *group, *([0x00, 0x00, 0x00, 0x80] * 3), *bytes(3)]))
        assert decode_status([reply]).timers is None

    def test_a_bad_reply_keeps_the_last_good_one(self) -> None:
        bad = _reply(bytes([0x33, 0x23, 0xFF, 0x82, *bytes(16)]))
        assert decode_status([TIMER_REPLY_UNSET, bad]).timers is not None

    def test_the_write_ack_is_not_a_reply(self) -> None:
        ack = bytes([0x33, 0x23, *bytes(17), 0x10])
        assert decode_status([ack]).timers is None


class TestSetLightTimer:
    async def test_an_off_timer_that_runs_once(self, hass, actions, route) -> None:
        await _call(hass, raw_actions.SERVICE_SET_LIGHT_TIMER, slot=1, action="turn_off", time="00:00:00")
        assert _sent(route) == [TIMER_OFF_ONCE]

    async def test_an_on_timer_on_monday_and_sunday(self, hass, actions, route) -> None:
        await _call(
            hass,
            raw_actions.SERVICE_SET_LIGHT_TIMER,
            slot=1,
            enabled=True,
            action="turn_on",
            time="00:00",
            weekdays=["sunday", "monday"],
        )
        assert _sent(route) == [TIMER_ON_MON_SUN]

    async def test_disabling_needs_nothing_else(self, hass, actions, route) -> None:
        await _call(hass, raw_actions.SERVICE_SET_LIGHT_TIMER, slot=1, enabled=False)
        assert _sent(route) == [TIMER_DISABLED]

    async def test_the_slot_counts_from_one(self, hass, actions, route) -> None:
        await _call(hass, raw_actions.SERVICE_SET_LIGHT_TIMER, slot=4, action="turn_on", time="21:05")
        assert _sent(route)[0][:7] == bytes([0x33, 0x23, 0x03, 0x81, 21, 5, 0x80])

    async def test_a_slot_past_the_last_is_a_translated_error(self, hass, actions, route) -> None:
        with pytest.raises(ServiceValidationError) as err:
            await _call(hass, raw_actions.SERVICE_SET_LIGHT_TIMER, slot=5, enabled=False)
        assert err.value.translation_key == "timer_slot_out_of_range"
        assert err.value.translation_placeholders == {"device": "String lights", "slots": "4"}
        route.assert_not_awaited()

    @pytest.mark.parametrize("data", [{"time": "07:00"}, {"action": "turn_on"}, {}])
    async def test_an_enabled_timer_needs_a_time_and_an_action(self, hass, actions, route, data) -> None:
        with pytest.raises(ServiceValidationError) as err:
            await _call(hass, raw_actions.SERVICE_SET_LIGHT_TIMER, slot=1, **data)
        assert err.value.translation_key == "timer_needs_time_and_action"
        route.assert_not_awaited()

    @pytest.mark.parametrize(
        "data", [{"slot": 0}, {"slot": 1, "weekdays": ["someday"]}, {"slot": 1, "action": "toggle"}]
    )
    async def test_the_schema_refuses_bad_values(self, hass, actions, route, data) -> None:
        with pytest.raises(vol.Invalid):
            await _call(hass, raw_actions.SERVICE_SET_LIGHT_TIMER, enabled=False, **data)
        route.assert_not_awaited()


# ==============================================================================
# 2. Sleep timer
# ==============================================================================

SLEEP_ON = bytes([0x33, 0x11, 0x01, 0x1E, 0x0F, 0x0F, 0x00, 0x00, 0x03, 0xE8, *bytes(9), 0xD6])
SLEEP_OFF = bytes([0x33, 0x11, 0x00, 0x1E, 0x0F, 0x0F, 0x00, 0x00, 0x03, 0xE8, *bytes(9), 0xD7])
SLEEP_REPLY = bytes([0xAA, 0x11, 0x00, 0x1E, 0x0F, 0x0F, 0x00, 0x00, 0x03, 0xE8, *bytes(9), 0x4E])


class TestSleepTimerFrame:
    @pytest.mark.parametrize(("enabled", "expected"), [(True, SLEEP_ON), (False, SLEEP_OFF)])
    def test_frames_are_byte_exact(self, enabled: bool, expected: bytes) -> None:
        assert _codec().sleep_timer(enabled=enabled, start_brightness=30, minutes=15) == expected

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"start_brightness": 0},
            {"start_brightness": 101},
            {"minutes": 0},
            {"minutes": 256},
        ],
    )
    def test_out_of_range_is_refused(self, kwargs: dict[str, Any]) -> None:
        with pytest.raises(FrameError):
            _codec().sleep_timer(enabled=True, **{"start_brightness": 30, "minutes": 15, **kwargs})

    def test_other_skus_do_not_encode_it(self) -> None:
        with pytest.raises(UnsupportedCapabilityError):
            _codec("H6076").sleep_timer(enabled=False, start_brightness=30, minutes=15)


class TestSleepTimerDecode:
    def test_the_reply(self) -> None:
        expected = SleepTimer(enabled=False, start_brightness=30, minutes=15, remaining_minutes=15)
        assert decode_status([SLEEP_REPLY]).sleep == expected

    def test_round_trip(self) -> None:
        frame = _codec().sleep_timer(enabled=True, start_brightness=80, minutes=120)
        assert decode_status([_reply(frame)]).sleep == SleepTimer(
            enabled=True, start_brightness=80, minutes=120, remaining_minutes=120
        )

    @pytest.mark.parametrize("body", [(0x02, 0x1E, 0x0F, 0x0F), (0x01, 0x65, 0x0F, 0x0F)], ids=["enable", "brightness"])
    def test_an_unexpected_value_skips_the_reply(self, body: tuple[int, ...]) -> None:
        assert decode_status([_reply(bytes([0x33, 0x11, *body, *bytes(14)]))]).sleep is None


class TestSetSleepTimer:
    async def test_on_with_the_defaults(self, hass, actions, route) -> None:
        await _call(hass, raw_actions.SERVICE_SET_SLEEP_TIMER)
        assert _sent(route) == [SLEEP_ON]

    async def test_off(self, hass, actions, route) -> None:
        await _call(hass, raw_actions.SERVICE_SET_SLEEP_TIMER, enabled=False, minutes=15, start_brightness=30)
        assert _sent(route) == [SLEEP_OFF]

    async def test_minutes_and_brightness_land_in_place(self, hass, actions, route) -> None:
        await _call(hass, raw_actions.SERVICE_SET_SLEEP_TIMER, minutes=60, start_brightness=100)
        assert _sent(route)[0][:10] == bytes([0x33, 0x11, 0x01, 100, 60, 60, 0x00, 0x00, 0x03, 0xE8])

    @pytest.mark.parametrize("data", [{"minutes": 0}, {"minutes": 256}, {"start_brightness": 0}])
    async def test_the_schema_refuses_bad_values(self, hass, actions, route, data) -> None:
        with pytest.raises(vol.Invalid):
            await _call(hass, raw_actions.SERVICE_SET_SLEEP_TIMER, **data)
        route.assert_not_awaited()
