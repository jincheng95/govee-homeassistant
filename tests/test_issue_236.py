"""Issue #236: an H5111 behind an H5151 shows its account (BFF) reading.

The freezer's Developer state was ``online: false`` with an empty
``sensorTemperature`` while the BFF ``lastDeviceData`` carried
``{"tem": -2090, "hum": 6000}``. The H5111 was in none of the BFF SKU sets,
so that reading was dropped.
"""

from __future__ import annotations

import json

import pytest

from custom_components.govee.api.auth import GoveeAuthClient
from custom_components.govee.models import GoveeDeviceState
from custom_components.govee.models.device import THERMO_HYGRO_BFF_READ_SKUS

from .test_auth import _bff_response, make_mock_response, make_session_get
from .test_thermometer import TestBffThermoHandover

FREEZER = "AA:BB:CC:DD:EE:FF:51:11"


def _h5111(last_device_data: dict[str, object]) -> dict[str, object]:
    return {
        "sku": "H5111",
        "device": FREEZER,
        "deviceName": "Freezer",
        "deviceExt": json.dumps(
            {
                "deviceSettings": json.dumps({"battery": 100, "sno": 1}),
                "lastDeviceData": json.dumps({**last_device_data, "lastTime": 1791145080000}),
            }
        ),
    }


def test_h5111_reads_from_the_bff() -> None:
    assert "H5111" in THERMO_HYGRO_BFF_READ_SKUS


@pytest.mark.asyncio
async def test_freezer_reading_is_parsed() -> None:
    session = make_session_get(make_mock_response(200, _bff_response([_h5111({"tem": -2090, "hum": 6000})])))
    sensors = await GoveeAuthClient(session=session).fetch_bff_thermo_hygrometers(token="tok")

    assert [(s["sku"], s["temperature"], s["humidity"]) for s in sensors] == [("H5111", -20.9, 60.0)]


class TestHandover:
    """Through the coordinator's hand-over, as in the #151 tests."""

    @staticmethod
    def _sensor(temperature: float | None, humidity: float | None) -> dict[str, object]:
        sensor = TestBffThermoHandover()._sensor(FREEZER, temperature=temperature, humidity=humidity)
        return {**sensor, "sku": "H5111", "name": "Freezer"}

    @pytest.mark.asyncio
    async def test_the_bff_reading_is_applied(self) -> None:
        helper = TestBffThermoHandover()
        state = GoveeDeviceState.create_empty(FREEZER)
        coordinator = helper._coordinator(devices=[FREEZER], states={FREEZER: state})

        await helper._discover(coordinator, [self._sensor(-20.9, 60.0)])
        await helper._refresh(coordinator, [self._sensor(-20.9, 60.0)])

        assert FREEZER in coordinator._bff_thermometer_ids
        assert coordinator._states[FREEZER].sensor_temperature == -20.9
        assert coordinator._states[FREEZER].sensor_humidity == 60.0

    @pytest.mark.asyncio
    async def test_a_sensor_without_a_bff_reading_stays_on_the_developer_poll(self) -> None:
        """The refrigerator next to it has no BFF reading (#151 guard)."""
        helper = TestBffThermoHandover()
        coordinator = helper._coordinator(devices=[FREEZER])

        await helper._discover(coordinator, [self._sensor(None, None)])

        assert FREEZER not in coordinator._bff_thermometer_ids
        assert FREEZER in coordinator._bff_thermo_pending
