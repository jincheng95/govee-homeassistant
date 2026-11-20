"""On-device schedule decoding and its diagnostic sensors (fork feature).

The three status frames are the ones an H80C5 pushed with every schedule off.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from custom_components.govee import light_schedule
from custom_components.govee.api.protocol import get_profile
from custom_components.govee.light_schedule import LightSchedule, SleepTimer, Timer, WakeUp
from custom_components.govee.models import GoveeDevice, GoveeDeviceState
from custom_components.govee.models.device import DEVICE_TYPE_LIGHT
from custom_components.govee.platforms.light_schedule import (
    GoveeSleepTimerSensor,
    GoveeTimersSensor,
    GoveeWakeUpSensor,
    async_light_schedule_sensor_entities,
)
from tests.coordinator_mocks import follow_online_flag

DEVICE_ID = "AA:BB:CC:DD:EE:FF:60:B0"


def _frame(*body: int) -> bytes:
    raw = bytes([0xAA, *body]).ljust(19, b"\0")
    checksum = 0
    for byte in raw:
        checksum ^= byte
    return raw + bytes([checksum])


SLEEP_OFF = _frame(0x11, 0x00, 0x1E, 0x0F, 0x0F, 0x00, 0xFF, 0x32)
WAKE_OFF = _frame(0x12, 0xFF, 0x64, 0x00, 0x00, 0x80, 0x0A, 0x00, 0xFF, 0xAE, 0x54)
TIMERS_OFF = _frame(0x23, 0xFF, *([0x00, 0x00, 0x00, 0x80] * 4))


class TestDecode:
    def test_the_captured_frames_read_as_everything_off(self) -> None:
        assert SLEEP_OFF.hex() == "aa11001e0f0f00ff320000000000000000000068"
        assert WAKE_OFF.hex() == "aa12ff640000800a00ffae5400000000000000ac"
        assert TIMERS_OFF.hex() == "aa23ff0000008000000080000000800000008076"

        schedule = light_schedule.decode([SLEEP_OFF, WAKE_OFF, TIMERS_OFF])

        assert schedule.sleep == SleepTimer(False, 30, 15, 15)
        assert schedule.wake == WakeUp(False, 100, 0, 0, 0x80, 10)
        assert schedule.timers == (Timer(False, 0, 0, 0x80),) * 4

    def test_enabled_values(self) -> None:
        schedule = light_schedule.decode(
            [
                _frame(0x11, 0x01, 0x32, 0x3C, 0x28),
                _frame(0x12, 0x01, 0x50, 0x07, 0x1E, 0x7F, 0x14),
                _frame(0x23, 0xFF, 0x81, 0x16, 0x2D, 0x7F, 0x00, 0x00, 0x00, 0x80, 0x80, 0x06, 0x00, 0x01, 0, 0, 0, 0),
            ]
        )
        assert schedule.sleep == SleepTimer(True, 50, 60, 40)
        assert schedule.wake == WakeUp(True, 80, 7, 30, 0x7F, 20)
        assert schedule.timers is not None
        assert [timer.enabled for timer in schedule.timers] == [True, False, True, False]
        assert (schedule.timers[0].hour, schedule.timers[0].minute) == (22, 45)

    def test_out_of_range_bytes_are_clamped(self) -> None:
        schedule = light_schedule.decode([_frame(0x11, 0x01, 0xFF, 0xFF, 0xFF), _frame(0x12, 0x01, 0xFF, 0xFF, 0xFF)])
        assert schedule.sleep == SleepTimer(True, 100, 240, 240)
        assert schedule.wake is not None and (schedule.wake.hour, schedule.wake.minute) == (23, 59)

    @pytest.mark.parametrize(
        "frames",
        [
            [],
            [b"short"],
            [SLEEP_OFF[:19] + b"\x00"],
            [b"\x33" + SLEEP_OFF[1:]],
            [_frame(0x05, 0x04)],
            [_frame(0x23, 0x01, 0x81, 0x16, 0x2D, 0x7F)],
        ],
    )
    def test_frames_that_carry_no_schedule(self, frames: list[bytes]) -> None:
        assert light_schedule.decode(frames) == LightSchedule()


def _device(sku: str = "H80C5", *, is_group: bool = False) -> GoveeDevice:
    return GoveeDevice(
        device_id=DEVICE_ID,
        sku=sku,
        name="String lights",
        device_type=DEVICE_TYPE_LIGHT,
        capabilities=(),
        is_group=is_group,
    )


def _coordinator(device: GoveeDevice | None = None) -> Any:
    device = device or _device()
    coordinator = MagicMock()
    coordinator._govee_light_schedules = None
    coordinator.devices = {device.device_id: device}
    coordinator.last_update_success = True
    state = GoveeDeviceState.create_empty(device.device_id)
    state.online = True
    coordinator.get_state = MagicMock(return_value=state)
    return follow_online_flag(coordinator)


class TestApply:
    def test_a_push_is_stored_and_a_repeat_is_not_a_change(self) -> None:
        coordinator = _coordinator()
        device = coordinator.devices[DEVICE_ID]
        frames = [SLEEP_OFF, WAKE_OFF, TIMERS_OFF]

        assert light_schedule.apply_schedule_report(coordinator, device, frames) is True
        assert light_schedule.apply_schedule_report(coordinator, device, frames) is False
        assert light_schedule.schedules(coordinator)[DEVICE_ID].sleep == SleepTimer(False, 30, 15, 15)

    def test_a_partial_push_keeps_what_was_known(self) -> None:
        coordinator = _coordinator()
        device = coordinator.devices[DEVICE_ID]
        light_schedule.apply_schedule_report(coordinator, device, [SLEEP_OFF, WAKE_OFF, TIMERS_OFF])

        assert light_schedule.apply_schedule_report(coordinator, device, [_frame(0x11, 0x01, 0x32, 0x3C, 0x28)])
        stored = light_schedule.schedules(coordinator)[DEVICE_ID]
        assert stored.sleep == SleepTimer(True, 50, 60, 40)
        assert stored.wake == WakeUp(False, 100, 0, 0, 0x80, 10)

    def test_a_push_without_schedule_frames_stores_nothing(self) -> None:
        coordinator = _coordinator()
        assert light_schedule.apply_schedule_report(coordinator, coordinator.devices[DEVICE_ID], []) is False
        assert light_schedule.schedules(coordinator) == {}

    @pytest.mark.parametrize("sku", ["H6076", "H0000"])
    def test_other_skus_are_ignored(self, sku: str) -> None:
        coordinator = _coordinator(_device(sku))
        device = coordinator.devices[DEVICE_ID]
        assert light_schedule.apply_schedule_report(coordinator, device, [SLEEP_OFF]) is False
        assert light_schedule.schedules(coordinator) == {}

    def test_the_profile_flag(self) -> None:
        assert get_profile("H80C5").schedule_report is True
        assert get_profile("H6076").schedule_report is False


class TestSensors:
    def _sensors(self, coordinator: Any) -> tuple[GoveeSleepTimerSensor, GoveeWakeUpSensor, GoveeTimersSensor]:
        sleep, wake, timers = async_light_schedule_sensor_entities(coordinator)
        assert isinstance(sleep, GoveeSleepTimerSensor)
        assert isinstance(wake, GoveeWakeUpSensor)
        assert isinstance(timers, GoveeTimersSensor)
        return sleep, wake, timers

    def test_only_reporting_skus_get_sensors(self) -> None:
        assert len(async_light_schedule_sensor_entities(_coordinator())) == 3
        assert async_light_schedule_sensor_entities(_coordinator(_device("H6076"))) == []
        assert async_light_schedule_sensor_entities(_coordinator(_device(is_group=True))) == []

    def test_unknown_until_the_first_push(self) -> None:
        sleep, wake, timers = self._sensors(_coordinator())
        for sensor in (sleep, wake, timers):
            assert sensor.native_value is None
            assert sensor.extra_state_attributes == {}
        assert {sleep.name, wake.name, timers.name} == {"Sleep timer", "Wake-up", "Timers on"}
        assert sleep.unique_id == f"{DEVICE_ID}_schedule_sleep"
        assert wake.unique_id == f"{DEVICE_ID}_schedule_wake"
        assert timers.unique_id == f"{DEVICE_ID}_schedule_timers"

    def test_everything_off(self) -> None:
        coordinator = _coordinator()
        light_schedule.apply_schedule_report(coordinator, coordinator.devices[DEVICE_ID], [SLEEP_OFF, WAKE_OFF, TIMERS_OFF])
        sleep, wake, timers = self._sensors(coordinator)

        assert sleep.native_value == "off"
        assert sleep.extra_state_attributes == {"minutes": 15, "remaining_minutes": 15, "start_brightness": 30}
        assert wake.native_value == "off"
        assert wake.extra_state_attributes == {"time": "00:00", "ramp_minutes": 10, "end_brightness": 100, "repeat": 0x80}
        assert timers.native_value == 0
        assert timers.extra_state_attributes["timers"][0] == {"on": False, "time": "00:00", "repeat": 0x80}

    def test_things_switched_on(self) -> None:
        coordinator = _coordinator()
        light_schedule.apply_schedule_report(
            coordinator,
            coordinator.devices[DEVICE_ID],
            [
                _frame(0x11, 0x01, 0x32, 0x3C, 0x28),
                _frame(0x12, 0x01, 0x50, 0x07, 0x1E, 0x7F, 0x14),
                _frame(0x23, 0xFF, 0x81, 0x16, 0x2D, 0x7F, *([0, 0, 0, 0x80] * 3)),
            ],
        )
        sleep, wake, timers = self._sensors(coordinator)
        assert sleep.native_value == "on"
        assert wake.native_value == "on"
        assert wake.extra_state_attributes["time"] == "07:30"
        assert timers.native_value == 1
        assert timers.extra_state_attributes["timers"][0] == {"on": True, "time": "22:45", "repeat": 0x7F}
