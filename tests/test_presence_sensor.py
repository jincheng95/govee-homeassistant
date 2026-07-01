"""Tests for mmWave presence/occupancy sensor support (H5127, issue #124).

The H5127 surfaces in the developer device list with a single
``bodyAppearedEvent`` event capability — the SAME instance the H5054 water
detector uses (and the H5054's ``eventState`` advertises the same two-option
shape). They are told apart by SKU (``PRESENCE_SENSOR_SKUS``), so the H5127
becomes an OCCUPANCY sensor — not a moisture/water-leak one — without dropping
the H5054's leak sensor. Live presence arrives via an MQTT ``status`` push
carrying ``triSta`` (1=present, 0=absent).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.govee.binary_sensor import GoveeOccupancyBinarySensor
from custom_components.govee.models import (
    GoveeCapability,
    GoveeDevice,
    GoveeDeviceState,
)
from custom_components.govee.models.device import (
    CAPABILITY_EVENT,
    INSTANCE_BODY_APPEARED_EVENT,
)


def _h5127_raw() -> dict:
    """Raw /user/devices payload for an H5127."""
    return {
        "device": "AA:BB:CC:DD:EE:FF:41:02",
        "sku": "H5127",
        "deviceName": "Presence Sensor",
        "type": "devices.types.sensor",
        "capabilities": [
            {
                "type": CAPABILITY_EVENT,
                "instance": INSTANCE_BODY_APPEARED_EVENT,
                "alarmType": 50,
                "eventState": {
                    "options": [
                        {"name": "Presence", "value": 1},
                        {"name": "Absence", "value": 2},
                    ]
                },
            }
        ],
    }


@pytest.fixture
def h5127_device() -> GoveeDevice:
    return GoveeDevice.from_api_response(_h5127_raw())


@pytest.fixture
def h5054_device() -> GoveeDevice:
    # Same device_type and bodyAppearedEvent instance as the H5127 — only the
    # SKU differs, which is exactly how they're told apart.
    return GoveeDevice(
        device_id="DABFC0D6A5FE0008E8",
        sku="H5054",
        name="Washing Machine",
        device_type="devices.types.sensor",
        capabilities=(
            GoveeCapability(
                type=CAPABILITY_EVENT,
                instance=INSTANCE_BODY_APPEARED_EVENT,
                parameters={},
            ),
        ),
    )


class TestClassification:
    def test_presence_detected(self, h5127_device):
        assert h5127_device.supports_presence_event is True

    def test_presence_is_not_water_leak(self, h5127_device):
        # The core bug: it must NOT be classified as a leak/moisture sensor.
        assert h5127_device.supports_water_leak_event is False

    def test_h5054_still_water_leak_not_presence(self, h5054_device):
        assert h5054_device.supports_water_leak_event is True
        assert h5054_device.supports_presence_event is False

    def test_h5054_with_two_option_eventstate_stays_leak(self):
        # Regression guard (v2026.6.24): the real H5054's bodyAppearedEvent
        # carries a two-option eventState too, so shape-based detection
        # mis-classified it as presence and dropped its leak sensor. SKU-locked
        # detection keeps the H5054 a water-leak sensor regardless of eventState.
        device = GoveeDevice.from_api_response(
            {
                "device": "H5054X",
                "sku": "H5054",
                "deviceName": "Leak",
                "type": "devices.types.sensor",
                "capabilities": [
                    {
                        "type": CAPABILITY_EVENT,
                        "instance": INSTANCE_BODY_APPEARED_EVENT,
                        "eventState": {
                            "options": [
                                {"name": "Leak", "value": 1},
                                {"name": "Dry", "value": 2},
                            ]
                        },
                    }
                ],
            }
        )
        assert device.supports_presence_event is False
        assert device.supports_water_leak_event is True


class TestStateParsing:
    def test_presence_from_mqtt_tristate_present(self):
        # H5127 live push: cmd="status", state.triSta=1 -> present (#124).
        state = GoveeDeviceState(device_id="x")
        state.update_from_mqtt({"triSta": 1, "sta": {"stc": "x"}, "result": 1})
        assert state.presence is True

    def test_presence_from_mqtt_tristate_absent(self):
        state = GoveeDeviceState(device_id="x")
        state.presence = True
        state.update_from_mqtt({"triSta": 0})
        assert state.presence is False

    def test_presence_from_api_body_appeared(self):
        state = GoveeDeviceState(device_id="x")
        state.update_from_api(
            {
                "capabilities": [
                    {
                        "type": CAPABILITY_EVENT,
                        "instance": INSTANCE_BODY_APPEARED_EVENT,
                        "state": {"value": 1},
                    }
                ]
            }
        )
        assert state.presence is True

    def test_presence_defaults_none(self):
        assert GoveeDeviceState(device_id="x").presence is None


class TestOccupancyEntity:
    def _coordinator(self, device, state):
        c = MagicMock()
        c.get_state = MagicMock(return_value=state)
        c.last_update_success = True
        return c

    def test_is_on_reads_presence(self, h5127_device):
        state = GoveeDeviceState(device_id=h5127_device.device_id)
        state.presence = True
        entity = GoveeOccupancyBinarySensor(
            self._coordinator(h5127_device, state), h5127_device
        )
        assert entity.is_on is True
        state.presence = False
        assert entity.is_on is False

    def test_unique_id(self, h5127_device):
        state = GoveeDeviceState(device_id=h5127_device.device_id)
        entity = GoveeOccupancyBinarySensor(
            self._coordinator(h5127_device, state), h5127_device
        )
        assert entity.unique_id == f"{h5127_device.device_id}_occupancy"

    def test_available_despite_offline_device(self, h5127_device):
        state = GoveeDeviceState(device_id=h5127_device.device_id, online=False)
        entity = GoveeOccupancyBinarySensor(
            self._coordinator(h5127_device, state), h5127_device
        )
        assert entity.available is True
