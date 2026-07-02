"""Tests for the OpenAPI event-subscription channel (issues #114, #118).

Covers:
- GoveeOpenApiEventClient message handling (ring buffer + event fan-out)
- Coordinator waterFullEvent application
- The BFF ``deviceSettings.waterFull`` field no longer being applied as tank
  state (it is the app's "Full Bucket Alert" notification setting)
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from custom_components.govee.api.openapi_events import GoveeOpenApiEventClient
from custom_components.govee.models import GoveeCapability, GoveeDevice, GoveeDeviceState
from custom_components.govee.models.device import (
    CAPABILITY_EVENT,
    CAPABILITY_ON_OFF,
    DEVICE_TYPE_DEHUMIDIFIER,
    INSTANCE_POWER,
)

WATER_FULL_PUSH = {
    "sku": "H7150",
    "device": "AA:BB:CC:DD:EE:FF:71:50",
    "deviceName": "Basement Dehumidifier",
    "capabilities": [
        {
            "type": "devices.capabilities.event",
            "instance": "waterFullEvent",
            "state": [
                {
                    "name": "waterFull",
                    "value": 1,
                    "message": "Water bucket is full or has been pulled out",
                }
            ],
        }
    ],
}


def _message(payload: object) -> MagicMock:
    msg = MagicMock()
    msg.payload = json.dumps(payload).encode() if not isinstance(payload, bytes) else payload
    return msg


def _h7150() -> GoveeDevice:
    return GoveeDevice(
        device_id="AA:BB:CC:DD:EE:FF:71:50",
        sku="H7150",
        name="Basement Dehumidifier",
        device_type=DEVICE_TYPE_DEHUMIDIFIER,
        capabilities=(
            GoveeCapability(type=CAPABILITY_ON_OFF, instance=INSTANCE_POWER),
            GoveeCapability(type=CAPABILITY_EVENT, instance="waterFullEvent"),
        ),
    )


class TestOpenApiEventClient:
    def _client(self):
        events: list[tuple] = []
        client = GoveeOpenApiEventClient(
            api_key="test-key",
            on_event=lambda *args: events.append(args),
        )
        return client, events

    def test_water_full_push_fans_out_and_buffers(self):
        client, events = self._client()
        client._handle_message(_message(WATER_FULL_PUSH))

        assert events == [
            (
                "AA:BB:CC:DD:EE:FF:71:50",
                "H7150",
                "waterFullEvent",
                WATER_FULL_PUSH["capabilities"][0]["state"],
            )
        ]
        assert len(client.recent_events) == 1
        assert client.recent_events[0]["payload"] == WATER_FULL_PUSH
        assert "received_at" in client.recent_events[0]

    def test_unknown_event_still_buffered(self):
        # Unknown instances must land in the diagnostics buffer — that's the
        # whole point of the capture (#114) — and still reach the callback.
        client, events = self._client()
        push = {
            "sku": "H9999",
            "device": "AA:BB:CC:DD:EE:FF:99:99",
            "capabilities": [
                {
                    "type": "devices.capabilities.event",
                    "instance": "mysteryEvent",
                    "state": [{"name": "mystery", "value": 7}],
                }
            ],
        }
        client._handle_message(_message(push))
        assert len(client.recent_events) == 1
        assert events[0][2] == "mysteryEvent"

    def test_non_event_capability_not_fanned_out(self):
        client, events = self._client()
        push = {
            "sku": "H7150",
            "device": "AA:BB:CC:DD:EE:FF:71:50",
            "capabilities": [
                {"type": "devices.capabilities.on_off", "instance": "powerSwitch"}
            ],
        }
        client._handle_message(_message(push))
        assert events == []
        assert len(client.recent_events) == 1  # still captured

    def test_undecodable_payload_ignored(self):
        client, events = self._client()
        client._handle_message(_message(b"\xaa\x05not json"))
        assert events == []
        assert client.recent_events == []

    def test_callback_exception_contained(self):
        client = GoveeOpenApiEventClient(
            api_key="test-key",
            on_event=MagicMock(side_effect=RuntimeError("boom")),
        )
        # Must not raise out of the message handler.
        client._handle_message(_message(WATER_FULL_PUSH))


class TestCoordinatorWaterFullEvent:
    def _coordinator(self):
        import custom_components.govee.coordinator as coord_mod

        coord = object.__new__(coord_mod.GoveeCoordinator)
        device = _h7150()
        coord._devices = {device.device_id: device}
        coord._states = {
            device.device_id: GoveeDeviceState.create_empty(device.device_id)
        }
        coord.async_set_updated_data = MagicMock()
        return coord, device

    def test_water_full_event_applied(self):
        coord, device = self._coordinator()
        coord._on_openapi_event(
            device.device_id,
            "H7150",
            "waterFullEvent",
            [{"name": "waterFull", "value": 1, "message": "..."}],
        )
        assert coord._states[device.device_id].water_full is True
        coord.async_set_updated_data.assert_called_once()

    def test_water_full_cleared_value_applied(self):
        coord, device = self._coordinator()
        coord._states[device.device_id].water_full = True
        coord._on_openapi_event(
            device.device_id, "H7150", "waterFullEvent", [{"value": 0}]
        )
        assert coord._states[device.device_id].water_full is False

    def test_unknown_device_ignored(self):
        coord, _ = self._coordinator()
        coord._on_openapi_event(
            "11:22:33:44:55:66:77:88", "H7150", "waterFullEvent", [{"value": 1}]
        )
        coord.async_set_updated_data.assert_not_called()

    def test_other_instance_ignored(self):
        coord, device = self._coordinator()
        coord._on_openapi_event(
            device.device_id, "H7150", "lackWaterEvent", [{"value": 1}]
        )
        assert coord._states[device.device_id].water_full is None
        coord.async_set_updated_data.assert_not_called()


class TestCoordinatorBodyAppearedEvent:
    """bodyAppearedEvent fires for BOTH transitions on presence sensors:
    value 1 = Presence, 2 = Absence (issue #124)."""

    def _coordinator(self, device: GoveeDevice):
        import custom_components.govee.coordinator as coord_mod

        coord = object.__new__(coord_mod.GoveeCoordinator)
        coord._devices = {device.device_id: device}
        coord._states = {
            device.device_id: GoveeDeviceState.create_empty(device.device_id)
        }
        coord.async_set_updated_data = MagicMock()
        return coord

    @staticmethod
    def _h5127() -> GoveeDevice:
        return GoveeDevice(
            device_id="AA:BB:CC:DD:EE:FF:51:27",
            sku="H5127",
            name="Office Presence Sensor",
            device_type="devices.types.sensor",
            capabilities=(
                GoveeCapability(type=CAPABILITY_EVENT, instance="bodyAppearedEvent"),
            ),
        )

    def test_presence_value_1_sets_present(self):
        device = self._h5127()
        coord = self._coordinator(device)
        coord._on_openapi_event(
            device.device_id, "H5127", "bodyAppearedEvent", [{"name": "Presence", "value": 1}]
        )
        assert coord._states[device.device_id].presence is True
        coord.async_set_updated_data.assert_called_once()

    def test_absence_value_2_sets_absent(self):
        device = self._h5127()
        coord = self._coordinator(device)
        coord._states[device.device_id].presence = True
        coord._on_openapi_event(
            device.device_id, "H5127", "bodyAppearedEvent", [{"name": "Absence", "value": 2}]
        )
        assert coord._states[device.device_id].presence is False

    def test_non_presence_sku_ignored(self):
        # The H5054 water detector shares the bodyAppearedEvent instance but
        # means "leak" — its state is owned by the warnMessage poll.
        device = GoveeDevice(
            device_id="AA:BB:CC:DD:EE:FF:50:54",
            sku="H5054",
            name="Water Detector",
            device_type="devices.types.sensor",
            capabilities=(
                GoveeCapability(type=CAPABILITY_EVENT, instance="bodyAppearedEvent"),
            ),
        )
        coord = self._coordinator(device)
        coord._on_openapi_event(
            device.device_id, "H5054", "bodyAppearedEvent", [{"value": 1}]
        )
        assert coord._states[device.device_id].presence is None
        coord.async_set_updated_data.assert_not_called()


class TestBffWaterFullNoLongerApplied:
    """deviceSettings.waterFull is the Full Bucket Alert setting, not tank
    state — it must never populate water_full again (issue #118)."""

    def test_bff_water_full_not_applied(self):
        import custom_components.govee.coordinator as coord_mod

        coord = object.__new__(coord_mod.GoveeCoordinator)
        device = _h7150()
        state = GoveeDeviceState.create_empty(device.device_id)
        coord._devices = {device.device_id: device}
        coord._states = {device.device_id: state}

        coord._apply_bff_thermo_battery(
            {device.device_id: {"water_full": 1, "battery": None}}
        )
        assert state.water_full is None
