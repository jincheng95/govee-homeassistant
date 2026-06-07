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


class TestTemperatureSensorFahrenheitConversion:
    """Regression for #72/#78: H5179/H5109/H5110/HS5108/HS5106 report °F via
    cloud API. Verifies the GoveeTemperatureSensor.native_value path honors
    the api_temperature_unit option."""

    def _make_sensor_stub(self, raw_value, api_unit, sku="H6072"):
        from types import SimpleNamespace

        from custom_components.govee.sensor import GoveeTemperatureSensor

        state = SimpleNamespace(sensor_temperature=raw_value)
        coordinator = SimpleNamespace(
            config_entry=SimpleNamespace(options={"api_temperature_unit": api_unit})
        )
        stub = SimpleNamespace(
            device_state=state,
            coordinator=coordinator,
            _device=SimpleNamespace(sku=sku),
        )
        return GoveeTemperatureSensor.native_value.fget(stub)

    def test_celsius_passthrough(self):
        assert self._make_sensor_stub(21.5, "celsius") == 21.5

    def test_celsius_forces_no_conversion_for_known_sku(self):
        # Explicit celsius overrides auto-detection for a °F-reporting SKU.
        assert self._make_sensor_stub(100.83, "celsius", sku="H5109") == 100.83

    def test_fahrenheit_converts(self):
        # 70°F -> 21.111…°C
        result = self._make_sensor_stub(70.0, "fahrenheit")
        assert abs(result - 21.111111) < 1e-4

    def test_fahrenheit_freezing(self):
        # 32°F -> 0°C
        assert abs(self._make_sensor_stub(32.0, "fahrenheit") - 0.0) < 1e-9

    def test_none_passthrough(self):
        assert self._make_sensor_stub(None, "celsius") is None
        assert self._make_sensor_stub(None, "fahrenheit") is None

    def test_auto_converts_known_fahrenheit_sku(self):
        # Issue #96: H5109 reports 100.83°F -> ~38.2°C under auto mode.
        result = self._make_sensor_stub(100.83, "auto", sku="H5109")
        assert abs(result - 38.238889) < 1e-4

    def test_auto_case_insensitive_sku_match(self):
        result = self._make_sensor_stub(100.83, "auto", sku="h5109")
        assert abs(result - 38.238889) < 1e-4

    def test_auto_passthrough_for_unknown_sku(self):
        # A SKU not in FAHRENHEIT_REPORTING_SKUS is trusted as °C under auto.
        assert self._make_sensor_stub(21.5, "auto", sku="H6072") == 21.5

    def test_default_when_option_missing(self):
        from types import SimpleNamespace

        from custom_components.govee.sensor import GoveeTemperatureSensor

        state = SimpleNamespace(sensor_temperature=100.83)
        coordinator = SimpleNamespace(config_entry=SimpleNamespace(options={}))
        stub = SimpleNamespace(
            device_state=state,
            coordinator=coordinator,
            _device=SimpleNamespace(sku="H5109"),
        )
        # Default is auto -> known °F SKU converts.
        result = GoveeTemperatureSensor.native_value.fget(stub)
        assert abs(result - 38.238889) < 1e-4


class TestSyntheticThermometer:
    """GoveeDevice.synthetic_thermometer backs BFF-only H5301 discovery (#86)."""

    def test_synthesizes_thermometer_with_sensor_capabilities(self):
        device = GoveeDevice.synthetic_thermometer(
            device_id="AA:BB:CC:DD:EE:FF:00:11", sku="H5301", name="Office"
        )
        assert device.device_id == "AA:BB:CC:DD:EE:FF:00:11"
        assert device.sku == "H5301"
        assert device.name == "Office"
        assert device.device_type == DEVICE_TYPE_THERMOMETER
        assert device.is_thermometer
        assert device.supports_temperature_sensor
        assert device.supports_humidity_sensor
        assert not device.is_group

    def test_temp_only_sku_omits_humidity_capability(self):
        # H5310 pool thermometer has no hygrometer -> no humidity entity (#97).
        device = GoveeDevice.synthetic_thermometer(
            device_id="03:55:01:25:00:00:00:0D", sku="H5310", name="Pool"
        )
        assert device.supports_temperature_sensor
        assert not device.supports_humidity_sensor


class TestBffReadingSentinel:
    """_bff_reading filters the 0xFFFF no-value sentinel (issue #97)."""

    def test_humidity_sentinel_returns_none(self):
        from custom_components.govee.api.auth import _BFF_HUMIDITY_KEYS, _bff_reading

        # H5310 with no hygrometer reports hum == 0xFFFF (65535 centi).
        assert _bff_reading({"hum": 65535}, _BFF_HUMIDITY_KEYS) is None

    def test_temperature_sentinel_returns_none(self):
        from custom_components.govee.api.auth import _BFF_TEMP_KEYS, _bff_reading

        assert _bff_reading({"tem": 65535}, _BFF_TEMP_KEYS) is None
        assert _bff_reading({"tem": 32767}, _BFF_TEMP_KEYS) is None

    def test_real_centi_values_still_descale(self):
        from custom_components.govee.api.auth import (
            _BFF_HUMIDITY_KEYS,
            _BFF_TEMP_KEYS,
            _bff_reading,
        )

        assert _bff_reading({"tem": 2640}, _BFF_TEMP_KEYS) == 26.4
        assert _bff_reading({"tem": -500}, _BFF_TEMP_KEYS) == -5.0
        assert _bff_reading({"hum": 5550}, _BFF_HUMIDITY_KEYS) == 55.5


class TestBffThermometerAvailability:
    """BFF thermo-hygrometer availability ignores flapping online (issue #97)."""

    def _available(self, *, is_bff, online, has_reading, update_success=True):
        from types import SimpleNamespace

        from custom_components.govee.sensor import GoveeTemperatureSensor

        state = (
            SimpleNamespace(online=online, sensor_temperature=26.4)
            if has_reading
            else None
        )
        coordinator = SimpleNamespace(
            last_update_success=update_success,
            is_bff_thermometer=lambda _id: is_bff,
        )
        stub = SimpleNamespace(
            _device_id="dev",
            coordinator=coordinator,
            device_state=state,
        )
        return GoveeTemperatureSensor.available.fget(stub)

    def test_available_when_online_false_but_reading_present(self):
        # H5310: online flaps false yet a fresh 26.4 reading exists -> available.
        assert self._available(is_bff=True, online=False, has_reading=True) is True

    def test_unavailable_when_no_reading(self):
        assert self._available(is_bff=True, online=False, has_reading=False) is False

    def test_unavailable_when_coordinator_failed(self):
        assert (
            self._available(
                is_bff=True, online=True, has_reading=True, update_success=False
            )
            is False
        )


class TestThermoBatterySensor:
    """GoveeThermoBatterySensor surfaces BFF battery level (issue #86)."""

    def _native(self, battery):
        from types import SimpleNamespace

        from custom_components.govee.sensor import GoveeThermoBatterySensor

        state = (
            SimpleNamespace(battery=battery) if battery is not None else None
        )
        stub = SimpleNamespace(device_state=state)
        return GoveeThermoBatterySensor.native_value.fget(stub)

    def test_reports_battery_level(self):
        assert self._native(88) == 88

    def test_none_when_no_state(self):
        assert self._native(None) is None

    def test_inherits_bff_availability_mixin(self):
        from custom_components.govee.sensor import (
            GoveeThermoBatterySensor,
            _BffThermometerAvailabilityMixin,
        )

        assert issubclass(
            GoveeThermoBatterySensor, _BffThermometerAvailabilityMixin
        )
