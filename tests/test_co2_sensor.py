"""Tests for CO₂ sensor support on the H5140 Smart CO₂ Monitor (issue #117).

The H5140 exposes a ``devices.capabilities.property`` /
``carbonDioxideConcentration`` capability whose value is the CO₂ concentration
in ppm (e.g. 609). It is surfaced as a numeric sensor under the HA CO2 device
class.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import CONCENTRATION_PARTS_PER_MILLION

from custom_components.govee.sensor import GoveeCO2Sensor
from custom_components.govee.models import GoveeDevice, GoveeDeviceState
from custom_components.govee.models.device import CAPABILITY_PROPERTY, INSTANCE_CO2


def _h5140() -> GoveeDevice:
    return GoveeDevice.from_api_response(
        {
            "device": "AA:BB:CC:DD:EE:FF:51:40",
            "sku": "H5140",
            "deviceName": "Smart CO2 Monitor",
            "type": "devices.types.air_quality_monitor",
            "capabilities": [
                {"type": CAPABILITY_PROPERTY, "instance": INSTANCE_CO2},
                {"type": CAPABILITY_PROPERTY, "instance": "sensorTemperature"},
                {"type": CAPABILITY_PROPERTY, "instance": "sensorHumidity"},
            ],
        }
    )


def test_supports_co2():
    assert _h5140().supports_co2 is True


def test_state_parses_co2():
    state = GoveeDeviceState(device_id="x")
    state.update_from_api(
        {
            "capabilities": [
                {
                    "type": CAPABILITY_PROPERTY,
                    "instance": "carbonDioxideConcentration",
                    "state": {"value": 609},
                }
            ]
        }
    )
    assert state.carbon_dioxide == 609


def test_co2_entity():
    dev = _h5140()
    state = GoveeDeviceState(device_id=dev.device_id)
    state.carbon_dioxide = 609
    coordinator = MagicMock()
    coordinator.get_state = MagicMock(return_value=state)
    entity = GoveeCO2Sensor(coordinator, dev)
    assert entity.native_value == 609
    assert entity.device_class == SensorDeviceClass.CO2
    assert entity.native_unit_of_measurement == CONCENTRATION_PARTS_PER_MILLION
    assert entity.unique_id == f"{dev.device_id}_co2"
