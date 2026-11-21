"""On-device schedules and music mode of the H80C5 string lights over raw frames (fork feature).

1. Light timers: the frame, the ``aa 23`` reply decoder and
   ``govee.set_light_timer``.
2. Sleep timer: the frame, the ``aa 11`` reply decoder and
   ``govee.set_sleep_timer``.
3. Wake-up: the frame, the ``aa 12`` reply decoder and ``govee.set_wake_up``.
4. Music mode: the palette upload, the commit, the table check and
   ``govee.set_music_mode``.
5. Music effect choices: the dropdown against every profile's table, and the
   per-device refusals on the H60B0 uplighter.

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
    GoveeProtocolError,
    LightTimer,
    SleepTimer,
    UnsupportedCapabilityError,
    WakeUp,
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
        assert hass.services.has_service(DOMAIN, raw_actions.SERVICE_SET_WAKE_UP)
        assert hass.services.has_service(DOMAIN, raw_actions.SERVICE_SET_MUSIC_MODE)


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
            (raw_actions.SERVICE_SET_WAKE_UP, {"enabled": False}),
            (raw_actions.SERVICE_SET_MUSIC_MODE, {"effect": "rhythm"}),
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


# ==============================================================================
# 3. Wake-up
# ==============================================================================

APP_2700_WHITE = (0xFF, 0xAE, 0x54)
WAKE_TUESDAY = bytes(
    [0x33, 0x12, 0x01, 0x64, 0x00, 0x00, 0x82, 0x0A, 0x00, 0xFF, 0xAE, 0x54, 0x0A, 0x8C, *bytes(5), 0x4F]
)
WAKE_TUESDAY_OFF = bytes(
    [0x33, 0x12, 0x00, 0x64, 0x00, 0x00, 0x82, 0x0A, 0x00, 0xFF, 0xAE, 0x54, 0x0A, 0x8C, *bytes(5), 0x4E]
)
WAKE_ONCE = bytes(
    [0x33, 0x12, 0x01, 0x64, 0x00, 0x00, 0x80, 0x0A, 0x00, 0xFF, 0xAE, 0x54, 0x0A, 0x8C, *bytes(5), 0x4D]
)
WAKE_REPLY_UNSET = bytes(
    [0xAA, 0x12, 0xFF, 0x64, 0x00, 0x00, 0x80, 0x0A, 0x00, 0xFF, 0xAE, 0x54, 0x00, 0x00, *bytes(5), 0xAC]
)


def _wake(**kwargs: Any) -> bytes:
    values: dict[str, Any] = {
        "enabled": True,
        "end_brightness": 100,
        "hour": 0,
        "minute": 0,
        "weekdays": (),
        "duration": 10,
        "kelvin": 2700,
        "white_rgb": APP_2700_WHITE,
        **kwargs,
    }
    return _codec().wake_up(**values)


class TestWakeUpFrame:
    @pytest.mark.parametrize(
        ("kwargs", "expected"),
        [
            ({"weekdays": (1,)}, WAKE_TUESDAY),
            ({"weekdays": (1,), "enabled": False}, WAKE_TUESDAY_OFF),
            ({}, WAKE_ONCE),
        ],
    )
    def test_frames_are_byte_exact(self, kwargs: dict[str, Any], expected: bytes) -> None:
        assert _wake(**kwargs) == expected

    def test_kelvin_is_clamped_to_the_profile_range(self) -> None:
        assert _wake(kelvin=12000)[12:14] == (9000).to_bytes(2, "big")

    @pytest.mark.parametrize(
        "kwargs",
        [{"end_brightness": 0}, {"duration": 0}, {"hour": 24}, {"weekdays": (-1,)}],
    )
    def test_out_of_range_is_refused(self, kwargs: dict[str, Any]) -> None:
        with pytest.raises(FrameError):
            _wake(**kwargs)

    def test_other_skus_do_not_encode_it(self) -> None:
        with pytest.raises(UnsupportedCapabilityError):
            GoveeCodec(get_profile("H6076")).wake_up(
                enabled=False, end_brightness=100, hour=0, minute=0, duration=10, kelvin=2700, white_rgb=(0, 0, 0)
            )


class TestWakeUpDecode:
    def test_the_never_set_reply_reads_as_disabled(self) -> None:
        assert decode_status([WAKE_REPLY_UNSET]).wake_up == WakeUp(
            enabled=False,
            end_brightness=100,
            hour=0,
            minute=0,
            weekdays=(),
            duration=10,
            rgb=APP_2700_WHITE,
            kelvin=0,
        )

    def test_round_trip(self) -> None:
        frame = _wake(hour=6, minute=45, weekdays=(0, 1, 2, 3, 4), duration=30, end_brightness=80)
        assert decode_status([_reply(frame)]).wake_up == WakeUp(
            enabled=True,
            end_brightness=80,
            hour=6,
            minute=45,
            weekdays=(0, 1, 2, 3, 4),
            duration=30,
            rgb=APP_2700_WHITE,
            kelvin=2700,
        )

    @pytest.mark.parametrize(
        "body",
        [
            (0x02, 0x64, 0x00, 0x00, 0x80),
            (0x01, 0x65, 0x00, 0x00, 0x80),
            (0x01, 0x64, 0x18, 0x00, 0x80),
            (0x01, 0x64, 0x00, 0x00, 0x00),
        ],
        ids=["enable", "brightness", "hour", "repeat-marker"],
    )
    def test_an_unexpected_value_skips_the_reply(self, body: tuple[int, ...]) -> None:
        assert decode_status([_reply(bytes([0x33, 0x12, *body, *bytes(13)]))]).wake_up is None


class TestSetWakeUp:
    async def test_tuesday_at_midnight_in_2700_k(self, hass, actions, route) -> None:
        await _call(hass, raw_actions.SERVICE_SET_WAKE_UP, time="00:00", weekdays=["tuesday"])
        expected = _wake(weekdays=(1,), white_rgb=raw_router.white_point(2700))
        assert _sent(route) == [expected]
        assert expected[:9] == WAKE_TUESDAY[:9] and expected[12:14] == WAKE_TUESDAY[12:14]

    async def test_the_white_point_follows_the_kelvin(self, hass, actions, route) -> None:
        await _call(hass, raw_actions.SERVICE_SET_WAKE_UP, time="07:30", color_temp_kelvin=6500, duration=20)
        frame = _sent(route)[0]
        assert frame[4:8] == bytes([7, 30, 0x80, 20])
        assert tuple(frame[9:12]) == raw_router.white_point(6500)
        assert frame[12:14] == (6500).to_bytes(2, "big")

    async def test_disabling_needs_no_time(self, hass, actions, route) -> None:
        await _call(hass, raw_actions.SERVICE_SET_WAKE_UP, enabled=False, weekdays=["tuesday"])
        assert _sent(route)[0][:9] == WAKE_TUESDAY_OFF[:9]

    async def test_an_enabled_wake_up_needs_a_time(self, hass, actions, route) -> None:
        with pytest.raises(ServiceValidationError) as err:
            await _call(hass, raw_actions.SERVICE_SET_WAKE_UP)
        assert err.value.translation_key == "wake_up_needs_time"
        route.assert_not_awaited()

    @pytest.mark.parametrize("kelvin", [2000, 9500])
    async def test_a_kelvin_outside_the_profile_is_a_translated_error(self, hass, actions, route, kelvin) -> None:
        with pytest.raises(ServiceValidationError) as err:
            await _call(hass, raw_actions.SERVICE_SET_WAKE_UP, time="07:00", color_temp_kelvin=kelvin)
        assert err.value.translation_key == "kelvin_out_of_range"
        assert err.value.translation_placeholders == {
            "device": "String lights",
            "kelvin": str(kelvin),
            "minimum": "2700",
            "maximum": "9000",
        }
        route.assert_not_awaited()

    @pytest.mark.parametrize("data", [{"duration": 0}, {"end_brightness": 101}, {"weekdays": ["noday"]}])
    async def test_the_schema_refuses_bad_values(self, hass, actions, route, data) -> None:
        with pytest.raises(vol.Invalid):
            await _call(hass, raw_actions.SERVICE_SET_WAKE_UP, time="07:00", **data)
        route.assert_not_awaited()


# ==============================================================================
# 4. Music mode
# ==============================================================================


def _written(*body: int, checksum: int) -> bytes:
    """A frame written out with its checksum, zero padding implied."""
    return bytes([*body, *bytes(19 - len(body)), checksum])


_RAINBOW = (0xFF, 0x00, 0x00, 0xFF, 0x7F, 0x00, 0xFF, 0xFF, 0x00, 0x00, 0xFF, 0x00)
_RAINBOW_REST = (0x00, 0x00, 0xFF, 0x00, 0xFF, 0xFF, 0x8B, 0x00, 0xFF)

MUSIC_FRAMES: dict[str, list[bytes]] = {
    "energic": [
        _written(0xA3, 0x00, 0x01, 0x02, 0x41, 0x05, 0x07, *_RAINBOW, checksum=0x63),
        _written(0xA3, 0xFF, *_RAINBOW_REST, checksum=0xD7),
        _written(0x33, 0x05, 0x13, 0x05, 0x63, checksum=0x43),
    ],
    "rhythm": [
        _written(0x33, 0x05, 0x13, 0x03, 0x63, checksum=0x45),
    ],
    "separation": [
        _written(0xA3, 0x00, 0x01, 0x02, 0x41, 0x32, 0x07, *_RAINBOW, checksum=0x54),
        _written(0xA3, 0xFF, *_RAINBOW_REST, 0x03, 0x00, 0x55, checksum=0x81),
        _written(0x33, 0x05, 0x13, 0x32, 0x63, checksum=0x74),
    ],
    "hopping": [
        _written(0xA3, 0x00, 0x01, 0x03, 0x41, 0x33, 0x07, *_RAINBOW, checksum=0x54),
        _written(0xA3, 0x01, *_RAINBOW_REST, 0x01, 0x01, 0x01, 0x19, 0x62, 0x01, 0x02, 0x3C, checksum=0x6C),
        _written(0xA3, 0xFF, 0x64, checksum=0x38),
        _written(0x33, 0x05, 0x13, 0x33, 0x63, checksum=0x75),
    ],
    "piano_keys": [
        _written(0xA3, 0x00, 0x01, 0x02, 0x41, 0x34, 0x07, *_RAINBOW, checksum=0x52),
        _written(0xA3, 0xFF, *_RAINBOW_REST, 0x00, 0x0A, 0x23, 0x01, 0x05, checksum=0xFA),
        _written(0x33, 0x05, 0x13, 0x34, 0x63, checksum=0x72),
    ],
    "fountain": [
        _written(0xA3, 0x00, 0x01, 0x02, 0x41, 0x35, 0x07, *_RAINBOW, checksum=0x53),
        _written(0xA3, 0xFF, *_RAINBOW_REST, 0x01, 0x02, 0x1A, 0x55, checksum=0x9B),
        _written(0x33, 0x05, 0x13, 0x35, 0x63, checksum=0x73),
    ],
    "day_and_night": [
        _written(0xA3, 0x00, 0x01, 0x02, 0x41, 0x37, 0x07, *_RAINBOW, checksum=0x51),
        _written(0xA3, 0xFF, *_RAINBOW_REST, 0x08, 0x14, checksum=0xCB),
        _written(0x33, 0x05, 0x13, 0x37, 0x63, checksum=0x71),
    ],
}

RHYTHM_COMMITS = [
    ({"sensitivity": 2}, _written(0x33, 0x05, 0x13, 0x03, 0x02, checksum=0x24)),
    ({"sensitivity": 47}, _written(0x33, 0x05, 0x13, 0x03, 0x2F, checksum=0x09)),
    ({"sensitivity": 92}, _written(0x33, 0x05, 0x13, 0x03, 0x5C, checksum=0x7A)),
    ({"sensitivity": 92, "soft": True}, _written(0x33, 0x05, 0x13, 0x03, 0x5C, 0x01, checksum=0x7B)),
    (
        {"sensitivity": 92, "manual_color": (0xFF, 0x00, 0x00)},
        _written(0x33, 0x05, 0x13, 0x03, 0x5C, 0x00, 0x01, 0xFF, 0x00, 0x00, checksum=0x84),
    ),
    (
        {"sensitivity": 92, "manual_color": (0xFF, 0x7F, 0x00)},
        _written(0x33, 0x05, 0x13, 0x03, 0x5C, 0x00, 0x01, 0xFF, 0x7F, 0x00, checksum=0xFB),
    ),
    (
        {"sensitivity": 92, "manual_color": (0x00, 0x00, 0xFF)},
        _written(0x33, 0x05, 0x13, 0x03, 0x5C, 0x00, 0x01, 0x00, 0x00, 0xFF, checksum=0x84),
    ),
]


def _music_spec() -> Any:
    spec = get_profile(SKU).music
    assert spec is not None
    return spec


class TestMusicFrames:
    @pytest.mark.parametrize("effect", sorted(MUSIC_FRAMES))
    def test_effect_selections_are_byte_exact(self, effect: str) -> None:
        spec = _music_spec()
        colors = spec.default_palette if spec.effects[effect] in spec.palette_effects else None
        assert _codec().music_effect(effect, sensitivity=99, colors=colors) == MUSIC_FRAMES[effect]

    @pytest.mark.parametrize(("kwargs", "expected"), RHYTHM_COMMITS)
    def test_rhythm_commits_are_byte_exact(self, kwargs: dict[str, Any], expected: bytes) -> None:
        assert _codec().music_effect("rhythm", **kwargs) == [expected]

    def test_a_raw_effect_id_and_no_palette_send_the_commit_alone(self) -> None:
        assert _codec().music_effect(0x05, sensitivity=99) == [MUSIC_FRAMES["energic"][-1]]

    def test_an_eight_colour_palette_carries_its_count_and_tail(self) -> None:
        palette = [(index, 0, 0) for index in range(8)]
        frames = _codec().music_effect("day_and_night", sensitivity=50, colors=palette)
        payload = frames[0][5:19] + b"".join(frame[2:19] for frame in frames[1:-1])
        assert frames[0][3] == len(frames) - 1
        assert payload[:2] == bytes([0x37, 8])
        assert payload[26:28] == bytes([0x08, 0x14])

    @pytest.mark.parametrize(
        ("effect", "kwargs", "error"),
        [
            ("rhythm", {"colors": [(1, 2, 3)]}, UnsupportedCapabilityError),
            ("energic", {"colors": []}, GoveeProtocolError),
            ("energic", {"colors": [(0, 0, 0)] * 9}, GoveeProtocolError),
            ("energic", {"sensitivity": 100}, GoveeProtocolError),
            ("disco", {}, GoveeProtocolError),
            (0x100, {}, GoveeProtocolError),
        ],
    )
    def test_out_of_range_is_refused(self, effect: Any, kwargs: dict[str, Any], error: type[Exception]) -> None:
        with pytest.raises(error):
            _codec().music_effect(effect, **{"sensitivity": 50, **kwargs})

    def test_skus_without_a_music_table_refuse(self) -> None:
        with pytest.raises(UnsupportedCapabilityError):
            _codec("H6076").music_effect("rhythm", sensitivity=50)

    @pytest.mark.parametrize("kwargs", [{"soft": True}, {"manual_color": (1, 2, 3)}])
    def test_a_commit_without_options_refuses_them(self, kwargs: dict[str, Any]) -> None:
        from dataclasses import replace

        profile = get_profile(SKU)
        plain = replace(profile, music=replace(_music_spec(), commit_options=False))
        with pytest.raises(UnsupportedCapabilityError):
            GoveeCodec(plain).music_effect("rhythm", sensitivity=50, **kwargs)


class TestMusicTable:
    def test_the_table_passes_its_check(self) -> None:
        from custom_components.govee.api.protocol import validate_table

        validate_table()

    @pytest.mark.parametrize(
        "change",
        [
            {"modes": {"scene": 0x04}},
            {"music_effects": {"rhythm": 0x103}},
            {"music_palette_effects": frozenset({0x99})},
            {"music_palette_tails": {0x03: bytes([1])}},
            {"music_default_palette": ((0, 0, 0),) * 9},
        ],
        ids=["no-sub-mode", "id-range", "palette-effect", "tail-effect", "default-palette"],
    )
    def test_a_broken_entry_is_refused(self, monkeypatch: pytest.MonkeyPatch, change: dict[str, Any]) -> None:
        from dataclasses import replace

        from custom_components.govee.api.protocol import profiles

        spec_changes = {key.removeprefix("music_"): value for key, value in change.items() if key.startswith("music_")}
        profile = get_profile(SKU)
        broken = replace(
            profile,
            modes=change.get("modes", profile.modes),
            music=replace(_music_spec(), **spec_changes),
        )
        monkeypatch.setattr(profiles, "PROFILES", {**profiles.PROFILES, SKU: broken})
        with pytest.raises(GoveeProtocolError):
            profiles.validate_table()


class TestSetMusicMode:
    @pytest.mark.parametrize("effect", sorted(MUSIC_FRAMES))
    async def test_each_effect_sends_the_known_frames(self, hass, actions, route, effect) -> None:
        await _call(hass, raw_actions.SERVICE_SET_MUSIC_MODE, effect=effect)
        assert _sent(route) == MUSIC_FRAMES[effect]

    async def test_options_reach_the_commit(self, hass, actions, route) -> None:
        await _call(
            hass, raw_actions.SERVICE_SET_MUSIC_MODE, effect="Rhythm", sensitivity=92, manual_color=[255, 127, 0]
        )
        assert _sent(route) == [RHYTHM_COMMITS[5][1]]

    async def test_soft(self, hass, actions, route) -> None:
        await _call(hass, raw_actions.SERVICE_SET_MUSIC_MODE, effect="rhythm", sensitivity=92, soft=True)
        assert _sent(route) == [RHYTHM_COMMITS[3][1]]

    async def test_a_palette_replaces_the_default(self, hass, actions, route) -> None:
        await _call(
            hass, raw_actions.SERVICE_SET_MUSIC_MODE, effect="energic", palette=[[255, 0, 0], [0, 0, 255]]
        )
        frames = _sent(route)
        assert frames[0][:13] == bytes([0xA3, 0xFF, 0x01, 0x01, 0x41, 0x05, 0x02, 0xFF, 0, 0, 0, 0, 0xFF])
        assert frames[1] == MUSIC_FRAMES["energic"][-1]

    async def test_without_a_default_palette_the_commit_goes_alone(self, hass, actions, route) -> None:
        actions["coordinator"] = _coordinator(_light("H60B0"))
        await _call(hass, raw_actions.SERVICE_SET_MUSIC_MODE, effect="shiny")
        assert _sent(route) == [bytes([0x33, 0x05, 0x13, 0x31, 0x63] + [0x00] * 14 + [0x77])]

    async def test_soft_sets_the_palette_style_byte_where_the_profile_has_one(self, hass, actions, route) -> None:
        actions["coordinator"] = _coordinator(_light("H60B0"))
        await _call(hass, raw_actions.SERVICE_SET_MUSIC_MODE, effect="bloom", soft=True, palette=[[1, 2, 3]])
        assert _sent(route) == GoveeCodec.for_sku("H60B0").music_effect(
            "bloom", sensitivity=99, colors=[(1, 2, 3)], soft=True
        )
        assert _sent(route)[0][10] == 0x01

    async def test_an_unknown_effect_is_a_translated_error(self, hass, actions, route) -> None:
        with pytest.raises(ServiceValidationError) as err:
            await _call(hass, raw_actions.SERVICE_SET_MUSIC_MODE, effect="disco")
        assert err.value.translation_key == "music_effect_unsupported"
        assert err.value.translation_placeholders == {
            "device": "String lights",
            "effect": "disco",
            "effects": "energic, rhythm, separation, hopping, piano_keys, fountain, day_and_night",
        }
        route.assert_not_awaited()

    async def test_a_palette_on_rhythm_is_a_translated_error(self, hass, actions, route) -> None:
        with pytest.raises(ServiceValidationError) as err:
            await _call(hass, raw_actions.SERVICE_SET_MUSIC_MODE, effect="rhythm", palette=[[1, 2, 3]])
        assert err.value.translation_key == "music_palette_unsupported"
        route.assert_not_awaited()

    async def test_a_long_palette_is_a_translated_error(self, hass, actions, route) -> None:
        with pytest.raises(ServiceValidationError) as err:
            await _call(hass, raw_actions.SERVICE_SET_MUSIC_MODE, effect="fountain", palette=[[1, 2, 3]] * 9)
        assert err.value.translation_key == "music_palette_too_long"
        assert err.value.translation_placeholders == {"maximum": "8"}
        route.assert_not_awaited()

    @pytest.mark.parametrize(
        "data",
        [{"sensitivity": 100}, {"manual_color": [256, 0, 0]}, {"palette": []}, {"palette": [[1, 2]]}],
    )
    async def test_the_schema_refuses_bad_values(self, hass, actions, route, data) -> None:
        with pytest.raises(vol.Invalid):
            await _call(hass, raw_actions.SERVICE_SET_MUSIC_MODE, effect="energic", **data)
        route.assert_not_awaited()


_SHINY_SOFT = [
    _written(0xA3, 0x00, 0x01, 0x02, 0x41, 0x31, 0x07, *_RAINBOW, checksum=0x57),
    _written(0xA3, 0xFF, *_RAINBOW_REST, 0x01, checksum=0xD6),
    _written(0x33, 0x05, 0x13, 0x31, 0x63, checksum=0x77),
]
_RAINBOW_PALETTE = [
    [0xFF, 0x00, 0x00],
    [0xFF, 0x7F, 0x00],
    [0xFF, 0xFF, 0x00],
    [0x00, 0xFF, 0x00],
    [0x00, 0x00, 0xFF],
    [0x00, 0xFF, 0xFF],
    [0x8B, 0x00, 0xFF],
]


def _music_options() -> list[str]:
    import yaml

    with open("custom_components/govee/services.yaml", encoding="utf-8") as handle:
        fields = yaml.safe_load(handle)[raw_actions.SERVICE_SET_MUSIC_MODE]["fields"]
    return fields["effect"]["selector"]["select"]["options"]


class TestMusicEffectChoices:
    def test_the_dropdown_lists_every_profiles_effects(self) -> None:
        """A drift guard: the dropdown is hand-written, the music tables are truth."""
        from custom_components.govee.api.protocol import profiles

        names = {name for profile in profiles.PROFILES.values() if profile.music for name in profile.music.effects}
        options = _music_options()
        assert len(options) == len(set(options))
        assert set(options) == names

    def test_every_effect_has_a_label(self) -> None:
        import json

        for path in ("custom_components/govee/strings.json", "custom_components/govee/translations/en.json"):
            with open(path, encoding="utf-8") as handle:
                labels = json.load(handle)["selector"]["music_effect"]["options"]
            assert set(labels) == set(_music_options()), path

    async def test_an_uplighter_effect_is_byte_exact(self, hass, actions, route) -> None:
        actions["coordinator"] = _coordinator(_light("H60B0"))
        await _call(hass, raw_actions.SERVICE_SET_MUSIC_MODE, effect="shiny", soft=True, palette=_RAINBOW_PALETTE)
        assert _sent(route) == _SHINY_SOFT

    async def test_the_uplighter_refuses_a_string_light_effect(self, hass, actions, route) -> None:
        actions["coordinator"] = _coordinator(_light("H60B0"))
        with pytest.raises(ServiceValidationError) as err:
            await _call(hass, raw_actions.SERVICE_SET_MUSIC_MODE, effect="energic")
        assert err.value.translation_key == "music_effect_unsupported"
        assert err.value.translation_placeholders == {
            "device": "String lights",
            "effect": "energic",
            "effects": "custom, stippling, hopping, flowing_light, luminous, bloom, rhythm, shiny",
        }
        route.assert_not_awaited()

    async def test_the_string_lights_refuse_an_uplighter_effect(self, hass, actions, route) -> None:
        with pytest.raises(ServiceValidationError) as err:
            await _call(hass, raw_actions.SERVICE_SET_MUSIC_MODE, effect="bloom")
        assert err.value.translation_key == "music_effect_unsupported"
        route.assert_not_awaited()

    async def test_a_manual_color_on_the_uplighter_is_a_translated_error(self, hass, actions, route) -> None:
        actions["coordinator"] = _coordinator(_light("H60B0"))
        with pytest.raises(ServiceValidationError) as err:
            await _call(hass, raw_actions.SERVICE_SET_MUSIC_MODE, effect="rhythm", manual_color=[255, 0, 0])
        assert err.value.translation_key == "music_manual_color_unsupported"
        assert err.value.translation_placeholders == {"device": "String lights"}
        route.assert_not_awaited()
