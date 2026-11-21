"""The on-device schedule store and its diagnostic sensors (fork feature).

The ``*_OFF`` frames read as every schedule off. The decoder itself is tested
in ``test_h80c5_schedule.py``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from custom_components.govee import light_schedule
from custom_components.govee.api.protocol import LightTimer, SleepTimer, WakeUp
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
        assert stored.wake == WakeUp(False, 100, 0, 0, (), 10, (0xFF, 0xAE, 0x54), 0)

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
        assert [sensor.translation_key for sensor in (sleep, wake, timers)] == [
            "schedule_sleep",
            "schedule_wake",
            "schedule_timers",
        ]
        assert sleep.options == wake.options == ["on", "off"]
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
                _frame(0x11, 0x01, 0x32, 0xFA, 0x28),
                _frame(0x12, 0x01, 0x50, 0x07, 0x1E, 0xFF, 0x14),
                _frame(0x23, 0xFF, 0x81, 0x16, 0x2D, 0xFF, *([0, 0, 0, 0x80] * 3)),
            ],
        )
        sleep, wake, timers = self._sensors(coordinator)
        assert sleep.native_value == "on"
        assert sleep.extra_state_attributes["minutes"] == 250
        assert wake.native_value == "on"
        assert wake.extra_state_attributes == {"time": "07:30", "ramp_minutes": 20, "end_brightness": 80, "repeat": 0xFF}
        assert timers.native_value == 1
        assert timers.extra_state_attributes["timers"][0] == {"on": True, "time": "22:45", "repeat": 0xFF}

    def test_an_out_of_range_push_keeps_the_last_good_value(self) -> None:
        coordinator = _coordinator()
        device = coordinator.devices[DEVICE_ID]
        light_schedule.apply_schedule_report(coordinator, device, [SLEEP_OFF, WAKE_OFF, TIMERS_OFF])
        assert not light_schedule.apply_schedule_report(
            coordinator, device, [_frame(0x11, 0x01, 0xFF, 0x0F, 0x0F), _frame(0x12, 0x01, 0x64, 0x18, 0, 0x80)]
        )
        assert light_schedule.schedule(coordinator, DEVICE_ID).sleep == SleepTimer(False, 30, 15, 15)


class TestRecord:
    def test_a_written_timer_replaces_its_slot(self) -> None:
        coordinator = _coordinator()
        light_schedule.apply_schedule_report(coordinator, coordinator.devices[DEVICE_ID], [TIMERS_OFF])
        timer = LightTimer(enabled=True, turn_on=True, hour=6, minute=0, weekdays=(0,))
        light_schedule.record_timer(coordinator, DEVICE_ID, 2, timer)
        timers = light_schedule.schedule(coordinator, DEVICE_ID).timers
        assert timers is not None and timers[2] == timer and timers[0].enabled is False

    @pytest.mark.parametrize("slot", [4, -1])
    def test_a_timer_is_not_stored_until_the_slots_are_known(self, slot: int) -> None:
        coordinator = _coordinator()
        timer = LightTimer(enabled=True, turn_on=True, hour=6, minute=0, weekdays=())
        light_schedule.record_timer(coordinator, DEVICE_ID, 0, timer)
        assert light_schedule.schedules(coordinator) == {}
        light_schedule.apply_schedule_report(coordinator, coordinator.devices[DEVICE_ID], [TIMERS_OFF])
        light_schedule.record_timer(coordinator, DEVICE_ID, slot, timer)
        assert timer not in (light_schedule.schedule(coordinator, DEVICE_ID).timers or ())
