"""Tests for mmWave presence/occupancy sensor support (H5127, issue #124).

The H5127 surfaces in the developer device list with a single
``bodyAppearedEvent`` event capability — the SAME instance the H5054 water
detector uses. They are told apart only by the capability's ``eventState``
options (Presence/Absence), so the H5127 must become an OCCUPANCY sensor, not a
moisture/water-leak one, and must NOT be polled against the leak warnMessage
endpoint.
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

# eventState block as Govee returns it for the H5127 (issue #124 diagnostics).
_PRESENCE_EVENT_STATE = {
    "options": [
        {"name": "Presence", "value": 1},
        {"name": "Absence", "value": 2},
    ]
}


def _h5127_raw() -> dict:
    """Raw /user/devices payload for an H5127, incl. the top-level eventState."""
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
                "eventState": _PRESENCE_EVENT_STATE,
            }
        ],
    }


@pytest.fixture
def h5127_device() -> GoveeDevice:
    return GoveeDevice.from_api_response(_h5127_raw())


@pytest.fixture
def h5054_device() -> GoveeDevice:
    # Same device_type and instance as the H5127, but no Presence/Absence
    # options — must stay a water-leak detector.
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
    def test_from_api_preserves_event_state(self, h5127_device):
        cap = h5127_device.capabilities[0]
        assert cap.event_state == _PRESENCE_EVENT_STATE

    def test_presence_detected(self, h5127_device):
        assert h5127_device.supports_presence_event is True

    def test_presence_is_not_water_leak(self, h5127_device):
        # The core bug: it must NOT be classified as a leak/moisture sensor.
        assert h5127_device.supports_water_leak_event is False

    def test_h5054_still_water_leak_not_presence(self, h5054_device):
        assert h5054_device.supports_water_leak_event is True
        assert h5054_device.supports_presence_event is False

    def test_presence_detected_for_localized_names(self):
        # #124 follow-up: Govee localizes the option NAMEs (German account
        # returns Anwesenheit/Abwesenheit), so detection must key on the stable
        # integer values, not the English names — else it falls back to a
        # moisture sensor (with a permanent false leak alarm) abroad.
        raw = _h5127_raw()
        raw["capabilities"][0]["eventState"] = {
            "options": [
                {"name": "Anwesenheit", "value": 1},
                {"name": "Abwesenheit", "value": 2},
            ]
        }
        device = GoveeDevice.from_api_response(raw)
        assert device.supports_presence_event is True
        assert device.supports_water_leak_event is False


class TestStateParsing:
    def test_presence_present(self):
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

    def test_presence_absent(self):
        state = GoveeDeviceState(device_id="x")
        state.update_from_api(
            {
                "capabilities": [
                    {
                        "type": CAPABILITY_EVENT,
                        "instance": INSTANCE_BODY_APPEARED_EVENT,
                        "state": {"value": 2},
                    }
                ]
            }
        )
        assert state.presence is False

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
