"""Issue #258: show when an account-data (BFF) thermometer last measured, and what Govee says.

An H5179 kept showing months-old values as current: its availability
ignores the flapping online flag (#97) and "Last changed" only stamps a
change. The BFF ``lastTime`` is the device's own clock, now exposed as a
Last measurement timestamp, and the account's online flag gets its own
Reported online diagnostic.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from custom_components.govee import binary_sensor as binary_sensor_mod
from custom_components.govee import sensor as sensor_mod
from custom_components.govee.binary_sensor import GoveeReportedOnlineBinarySensor
from custom_components.govee.models import GoveeDeviceState
from custom_components.govee.sensor import GoveeLastMeasurementSensor

from .test_cov_coordinator_setup import _coordinator as _real_coordinator
from .test_cov_sensor import THERMO_ID, _thermometer
from .test_thermometer import TestBffThermoHandover

LAST_TIME_MS = 1791145080000
LAST_TIME = datetime.fromtimestamp(LAST_TIME_MS / 1000, timezone.utc)


class TestLastMeasurementTime:
    @pytest.mark.parametrize(
        ("last_time", "expected"),
        [
            (LAST_TIME_MS, LAST_TIME),
            (1717000000, datetime.fromtimestamp(1717000000, timezone.utc)),  # seconds on some SKUs (H5310)
            (None, None),
            (0, None),
            (-5, None),
            (True, None),
            ("1791145080000", None),
            (10**30, None),
        ],
    )
    def test_parsing(self, last_time, expected):
        coord = _real_coordinator()
        coord._note_bff_last_measurement(THERMO_ID, {"last_time": last_time})
        assert coord.bff_last_measurement_at(THERMO_ID) == expected

    @pytest.mark.asyncio
    async def test_discovery_and_refresh_record_it(self):
        helper = TestBffThermoHandover()
        state = GoveeDeviceState.create_empty("pool")
        coordinator = helper._coordinator(devices=["pool"], states={"pool": state})
        sensor = {**helper._sensor("pool", temperature=21.0), "last_time": LAST_TIME_MS}

        await helper._discover(coordinator, [sensor])
        assert coordinator.bff_last_measurement_at("pool") == LAST_TIME

        await helper._refresh(coordinator, [{**sensor, "last_time": LAST_TIME_MS + 60_000}])
        assert (coordinator.bff_last_measurement_at("pool") - LAST_TIME).total_seconds() == 60


def _coordinator(*, online: bool = True, last_update_success: bool = True, bff: bool = True) -> MagicMock:
    coordinator = MagicMock()
    coordinator.last_update_success = last_update_success
    coordinator.is_bff_thermometer.return_value = bff
    coordinator.is_water_detector.return_value = False
    coordinator.get_state.return_value = GoveeDeviceState(device_id=THERMO_ID, online=online)
    coordinator.bff_last_measurement_at.return_value = LAST_TIME
    return coordinator


class TestEntities:
    def test_last_measurement_value_and_identity(self):
        entity = GoveeLastMeasurementSensor(_coordinator(), _thermometer())
        assert entity.native_value == LAST_TIME
        assert entity.unique_id == f"{THERMO_ID}_last_measurement"
        assert entity.translation_key == "last_measurement"

    def test_last_measurement_stays_available_while_reported_offline(self):
        """BFF thermometers ignore the flapping online flag (#97)."""
        assert GoveeLastMeasurementSensor(_coordinator(online=False), _thermometer()).available is True

    @pytest.mark.parametrize("online", [True, False])
    def test_reported_online_shows_the_flag_and_stays_available(self, online):
        entity = GoveeReportedOnlineBinarySensor(_coordinator(online=online), _thermometer())
        assert entity.is_on is online
        assert entity.available is True
        assert entity.unique_id == f"{THERMO_ID}_reported_online"

    def test_reported_online_follows_the_coordinator_and_needs_a_state(self):
        assert (
            GoveeReportedOnlineBinarySensor(_coordinator(last_update_success=False), _thermometer()).available is False
        )
        coordinator = _coordinator()
        coordinator.get_state.return_value = None
        entity = GoveeReportedOnlineBinarySensor(coordinator, _thermometer())
        assert entity.available is False
        assert entity.is_on is None


async def _added(module, coordinator: MagicMock) -> list:
    coordinator.devices = {THERMO_ID: _thermometer()}
    coordinator.leak_sensors = {}
    coordinator.is_bff_leak_sensor.return_value = False
    coordinator.mqtt_client = None
    entry = MagicMock()
    entry.entry_id = "entry1"
    entry.runtime_data = coordinator
    entry.options = {}
    added: list = []
    await module.async_setup_entry(MagicMock(), entry, added.extend)
    return added


class TestSetup:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("bff", [True, False])
    async def test_created_only_for_bff_thermometers(self, bff):
        sensors = await _added(sensor_mod, _coordinator(bff=bff))
        binary_sensors = await _added(binary_sensor_mod, _coordinator(bff=bff))
        assert any(isinstance(e, GoveeLastMeasurementSensor) for e in sensors) is bff
        assert any(isinstance(e, GoveeReportedOnlineBinarySensor) for e in binary_sensors) is bff
