"""Tests for stand-alone temperature/humidity sensor support (issue #62)."""

from __future__ import annotations

import pytest

from custom_components.govee.models import (
    GoveeCapability,
    GoveeDevice,
    GoveeDeviceState,
)
from custom_components.govee.models.device import (
    CAPABILITY_PROPERTY,
    DEVICE_TYPE_THERMOMETER,
    INSTANCE_SENSOR_HUMIDITY,
    INSTANCE_SENSOR_TEMPERATURE,
)


@pytest.fixture
def thermometer_caps():
    return (
        GoveeCapability(
            type=CAPABILITY_PROPERTY,
            instance=INSTANCE_SENSOR_TEMPERATURE,
            parameters={},
        ),
        GoveeCapability(
            type=CAPABILITY_PROPERTY,
            instance=INSTANCE_SENSOR_HUMIDITY,
            parameters={},
        ),
    )


@pytest.fixture
def h5179_device(thermometer_caps):
    """H5179 WiFi Thermometer (canonical) — proves we don't need
    SKU-specific handling, only capability detection."""
    return GoveeDevice(
        device_id="AA:BB:CC:DD:EE:FF:00:11",
        sku="H5179",
        name="Living Room Thermometer",
        device_type=DEVICE_TYPE_THERMOMETER,
        capabilities=thermometer_caps,
        is_group=False,
    )


@pytest.fixture
def h5109_device(thermometer_caps):
    """H5109 Smart Temperature Sensor — issue #62 reporter's device."""
    return GoveeDevice(
        device_id="11:22:33:44:55:66:77:88",
        sku="H5109",
        name="Garage Thermometer",
        device_type=DEVICE_TYPE_THERMOMETER,
        capabilities=thermometer_caps,
        is_group=False,
    )


class TestThermometerDetection:
    def test_h5179_supports_temperature_and_humidity(self, h5179_device):
        assert h5179_device.supports_temperature_sensor is True
        assert h5179_device.supports_humidity_sensor is True

    def test_h5109_supports_temperature_and_humidity(self, h5109_device):
        # Same capabilities, different SKU — capability-based detection
        # means H5109 lights up for free once H5179 works.
        assert h5109_device.supports_temperature_sensor is True
        assert h5109_device.supports_humidity_sensor is True

    def test_thermometer_is_thermometer(self, h5109_device):
        assert h5109_device.is_thermometer is True

    def test_light_device_is_not_thermometer_supports_nothing(self):
        """A regular light must not pick up sensor entities by accident."""
        from custom_components.govee.models.device import (
            CAPABILITY_ON_OFF,
            INSTANCE_POWER,
        )

        light = GoveeDevice(
            device_id="00:11:22:33:44:55:66:77",
            sku="H6072",
            name="Bedroom Lamp",
            device_type="devices.types.light",
            capabilities=(
                GoveeCapability(
                    type=CAPABILITY_ON_OFF,
                    instance=INSTANCE_POWER,
                    parameters={},
                ),
            ),
            is_group=False,
        )
        assert light.supports_temperature_sensor is False
        assert light.supports_humidity_sensor is False
        assert light.is_thermometer is False


class TestThermometerStateParsing:
    def _api_payload(self, *caps):
        return {"capabilities": list(caps)}

    def test_parses_plain_number_value(self):
        state = GoveeDeviceState.create_empty("dev")
        state.update_from_api(
            self._api_payload(
                {
                    "type": CAPABILITY_PROPERTY,
                    "instance": INSTANCE_SENSOR_TEMPERATURE,
                    "state": {"value": 21.5},
                },
                {
                    "type": CAPABILITY_PROPERTY,
                    "instance": INSTANCE_SENSOR_HUMIDITY,
                    "state": {"value": 47.0},
                },
            )
        )
        assert state.sensor_temperature == 21.5
        assert state.sensor_humidity == 47.0

    def test_parses_struct_value(self):
        """Some H5XXX SKUs return a STRUCT under value with currentX
        named fields (legacy shape). Accept both."""
        state = GoveeDeviceState.create_empty("dev")
        state.update_from_api(
            self._api_payload(
                {
                    "type": CAPABILITY_PROPERTY,
                    "instance": INSTANCE_SENSOR_TEMPERATURE,
                    "state": {"value": {"currentTemperature": 19.4}},
                },
                {
                    "type": CAPABILITY_PROPERTY,
                    "instance": INSTANCE_SENSOR_HUMIDITY,
                    "state": {"value": {"currentHumidity": 55.2}},
                },
            )
        )
        assert state.sensor_temperature == 19.4
        assert state.sensor_humidity == 55.2

    def test_missing_value_leaves_state_unchanged(self):
        state = GoveeDeviceState.create_empty("dev")
        state.sensor_temperature = 20.0
        state.update_from_api(
            self._api_payload(
                {
                    "type": CAPABILITY_PROPERTY,
                    "instance": INSTANCE_SENSOR_TEMPERATURE,
                    "state": {},
                }
            )
        )
        assert state.sensor_temperature == 20.0

    def test_non_numeric_value_is_ignored(self):
        state = GoveeDeviceState.create_empty("dev")
        state.update_from_api(
            self._api_payload(
                {
                    "type": CAPABILITY_PROPERTY,
                    "instance": INSTANCE_SENSOR_TEMPERATURE,
                    "state": {"value": "not a number"},
                }
            )
        )
        assert state.sensor_temperature is None
