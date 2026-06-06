"""Tests for standalone water-leak detector support (H5054, issue #62).

The H5054 surfaces in the developer device list with a single
``bodyAppearedEvent`` event capability — distinct from the H5058 leak sensor
(hub/BFF path). Detection is capability-based; the trip normally lands via
MQTT push since the device-state poll only returns ``online``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.govee.binary_sensor import GoveeWaterLeakBinarySensor
from custom_components.govee.models import (
    GoveeCapability,
    GoveeDevice,
    GoveeDeviceState,
)
from custom_components.govee.models.device import (
    CAPABILITY_EVENT,
    INSTANCE_BODY_APPEARED_EVENT,
)

# --------------------------------------------------------------------------- #
# Fixtures — H5054 shape from issue #62 diagnostics
# --------------------------------------------------------------------------- #


@pytest.fixture
def h5054_device() -> GoveeDevice:
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
        is_group=False,
    )


# --------------------------------------------------------------------------- #
# Device-model detection
# --------------------------------------------------------------------------- #


class TestDeviceModel:
    def test_water_leak_event_detected(self, h5054_device):
        assert h5054_device.supports_water_leak_event is True

    def test_non_leak_device_not_detected(self):
        device = GoveeDevice(
            device_id="x",
            sku="H6159",
            name="Strip",
            device_type="devices.types.light",
            capabilities=(),
            is_group=False,
        )
        assert device.supports_water_leak_event is False


# --------------------------------------------------------------------------- #
# State parsing — REST device-state poll
# --------------------------------------------------------------------------- #


class TestStateParsing:
    def test_water_leak_from_api_scalar(self):
        state = GoveeDeviceState(device_id="x")
        state.update_from_api(
            {
                "capabilities": [
                    {
                        "type": "devices.capabilities.event",
                        "instance": "bodyAppearedEvent",
                        "state": {"value": 1},
                    }
                ]
            }
        )
        assert state.water_leak is True

    def test_water_leak_from_api_struct(self):
        state = GoveeDeviceState(device_id="x")
        state.update_from_api(
            {
                "capabilities": [
                    {
                        "type": "devices.capabilities.event",
                        "instance": "bodyAppearedEvent",
                        "state": {"value": {"state": True}},
                    }
                ]
            }
        )
        assert state.water_leak is True

    def test_water_leak_defaults_none(self):
        state = GoveeDeviceState(device_id="x")
        assert state.water_leak is None

    def test_water_full_not_set_by_leak_event(self):
        """bodyAppearedEvent must not bleed into the dehumidifier water_full flag."""
        state = GoveeDeviceState(device_id="x")
        state.update_from_api(
            {
                "capabilities": [
                    {
                        "type": "devices.capabilities.event",
                        "instance": "bodyAppearedEvent",
                        "state": {"value": 1},
                    }
                ]
            }
        )
        assert state.water_full is None


# --------------------------------------------------------------------------- #
# State parsing — MQTT push: H5054 has no MQTT topic (issue #62), so an
# unrelated push must never touch the leak flag.
# --------------------------------------------------------------------------- #


class TestMqttParsing:
    def test_unrelated_push_leaves_leak_untouched(self):
        state = GoveeDeviceState(device_id="x")
        state.update_from_mqtt({"onOff": 1, "brightness": 50})
        assert state.water_leak is None


# --------------------------------------------------------------------------- #
# Binary sensor entity
# --------------------------------------------------------------------------- #


class TestBinarySensor:
    def _entity(self, h5054_device, leak_value, last_update_success=True):
        state = GoveeDeviceState(device_id=h5054_device.device_id, online=False)
        state.water_leak = leak_value
        coordinator = MagicMock()
        coordinator.devices = {h5054_device.device_id: h5054_device}
        coordinator.get_state = MagicMock(return_value=state)
        coordinator.last_update_success = last_update_success
        entity = GoveeWaterLeakBinarySensor(coordinator, h5054_device)
        return entity

    def test_unique_id(self, h5054_device):
        entity = self._entity(h5054_device, None)
        assert entity.unique_id == "DABFC0D6A5FE0008E8_water_leak"

    def test_is_on_wet(self, h5054_device):
        assert self._entity(h5054_device, True).is_on is True

    def test_is_on_dry(self, h5054_device):
        assert self._entity(h5054_device, False).is_on is False

    def test_is_on_unknown(self, h5054_device):
        assert self._entity(h5054_device, None).is_on is None

    def test_available_despite_offline_device(self, h5054_device):
        """Entity stays available even though the detector reports online=False."""
        assert self._entity(h5054_device, False).available is True

    def test_unavailable_when_coordinator_failed(self, h5054_device):
        entity = self._entity(h5054_device, None, last_update_success=False)
        assert entity.available is False
