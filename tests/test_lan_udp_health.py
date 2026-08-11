"""Tests for the ``lan_udp`` transport health surface (fork feature).

Two entity-level requirements and one honesty requirement:

* ``lan_udp`` appears in the always-on ``*_connectivity`` binary sensor's
  attributes, alongside the transports that were already there.
* ``lan_udp`` appears as its own opt-in per-transport entity, following exactly
  the convention of the four existing ones (translation key, unique id, icon,
  and the same two-file translation rule).
* Its availability means "the device answered discovery and we have a frame
  layout for it" — never "a write landed". Raw frames are unconfirmable, so a
  send stamps a timestamp and nothing more.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from custom_components.govee import lan_health
from custom_components.govee.binary_sensor import (
    GoveeDeviceConnectivity,
    GoveeTransportConnectivity,
    _TRANSPORT_SPECS,
    async_setup_entry,
)
from custom_components.govee.const import CONF_EXPOSE_TRANSPORT_ENTITIES
from custom_components.govee.models import GoveeCapability, GoveeDevice, TransportHealth
from custom_components.govee.models.device import (
    CAPABILITY_ON_OFF,
    DEVICE_TYPE_LIGHT,
    INSTANCE_POWER,
)
from custom_components.govee.models.transport import TRANSPORT_KINDS
from custom_components.govee.transport_health import TransportHealthTracker

DEVICE_ID = "AA:BB:CC:DD:EE:FF:60:B0"
ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "govee"


def _device(sku: str = "H60B0") -> GoveeDevice:
    return GoveeDevice(
        device_id=DEVICE_ID,
        sku=sku,
        name="Uplighter Floor Lamp",
        device_type=DEVICE_TYPE_LIGHT,
        capabilities=(GoveeCapability(type=CAPABILITY_ON_OFF, instance=INSTANCE_POWER, parameters={}),),
    )


def _coordinator(device: GoveeDevice | None = None, *, on_lan: bool = True) -> MagicMock:
    device = device or _device()
    tracker = TransportHealthTracker()
    coordinator = MagicMock()
    coordinator.devices = {device.device_id: device}
    coordinator.leak_sensors = {}
    coordinator.register_leak_hubs = MagicMock()
    coordinator._lan_devices = {device.device_id: MagicMock()} if on_lan else {}
    coordinator._ensure_transport_health = tracker.ensure
    coordinator.get_transport_health = tracker.get
    coordinator.last_update_success = True
    coordinator.mqtt_last_receive_for = MagicMock(return_value=None)
    return coordinator


def _entry(coordinator: MagicMock, *, expose: bool) -> MagicMock:
    entry = MagicMock()
    entry.runtime_data = coordinator
    entry.options = {CONF_EXPOSE_TRANSPORT_ENTITIES: expose}
    return entry


def _aggregate(coordinator: MagicMock, device: GoveeDevice) -> GoveeDeviceConnectivity:
    with patch.object(GoveeDeviceConnectivity, "__init__", lambda self, *a, **k: None):
        entity = GoveeDeviceConnectivity.__new__(GoveeDeviceConnectivity)
    entity.coordinator = coordinator
    entity._device = device
    entity._device_id = device.device_id
    return entity


# ==============================================================================
# 1. The transport is registered like the others
# ==============================================================================


class TestTransportRegistration:
    def test_lan_udp_is_a_tracked_transport(self):
        assert "lan_udp" in TRANSPORT_KINDS
        assert lan_health.TRANSPORT_LAN_UDP == "lan_udp"

    def test_tracker_provisions_it_for_every_device(self):
        tracker = TransportHealthTracker()
        tracker.ensure(DEVICE_ID)

        assert isinstance(tracker.get(DEVICE_ID, "lan_udp"), TransportHealth)

    def test_spec_row_follows_the_existing_convention(self):
        assert ("lan_udp", "lan_udp_connectivity", "mdi:lan-pending") in _TRANSPORT_SPECS
        assert len([s for s in _TRANSPORT_SPECS if s[0] == "lan_udp"]) == 1
        # ...and it did not displace the existing LAN row.
        assert ("lan", "lan_connectivity", "mdi:lan") in _TRANSPORT_SPECS


# ==============================================================================
# 2. Entities
# ==============================================================================


class TestEntities:
    async def test_entity_created_when_exposed(self):
        device = _device()
        coordinator = _coordinator(device)
        added: list = []
        await async_setup_entry(MagicMock(), _entry(coordinator, expose=True), lambda e: added.extend(e))

        entities = [e for e in added if isinstance(e, GoveeTransportConnectivity) and e._transport == "lan_udp"]
        assert len(entities) == 1
        entity = entities[0]
        assert entity.translation_key == "lan_udp_connectivity"
        assert entity.unique_id == f"{DEVICE_ID}_lan_udp_connectivity"
        assert entity.icon == "mdi:lan-pending"

    async def test_entity_absent_when_not_exposed(self):
        coordinator = _coordinator()
        added: list = []
        await async_setup_entry(MagicMock(), _entry(coordinator, expose=False), lambda e: added.extend(e))

        assert not any(isinstance(e, GoveeTransportConnectivity) and e._transport == "lan_udp" for e in added)

    def test_aggregate_sensor_carries_lan_udp_attributes(self):
        device = _device()
        coordinator = _coordinator(device)
        lan_health.refresh(coordinator)
        entity = _aggregate(coordinator, device)

        attrs = entity.extra_state_attributes
        assert attrs["lan_udp_available"] is True
        # The pre-existing transports are untouched.
        assert "cloud_api_available" in attrs
        assert "lan_available" in attrs

    def test_aggregate_sensor_reports_the_failure_reason(self):
        device = _device()
        coordinator = _coordinator(device, on_lan=False)
        lan_health.refresh(coordinator)
        entity = _aggregate(coordinator, device)

        attrs = entity.extra_state_attributes
        assert attrs["lan_udp_available"] is False
        assert attrs["lan_udp_last_failure_reason"] == "no_lan_presence"


# ==============================================================================
# 3. What "available" honestly means
# ==============================================================================


class TestSemantics:
    def test_reachable_and_profiled_is_available(self):
        coordinator = _coordinator()
        lan_health.refresh(coordinator)

        health = coordinator.get_transport_health(DEVICE_ID, "lan_udp")
        assert health.is_available is True
        assert health.last_failure_reason is None

    def test_absent_from_the_lan_map_is_unavailable(self):
        coordinator = _coordinator(on_lan=False)
        lan_health.refresh(coordinator)

        health = coordinator.get_transport_health(DEVICE_ID, "lan_udp")
        assert health.is_available is False
        assert health.last_failure_reason == lan_health.REASON_NO_LAN_PRESENCE

    def test_reachable_but_unprofiled_sku_is_unavailable(self):
        coordinator = _coordinator(_device(sku="H6199"))
        lan_health.refresh(coordinator)

        health = coordinator.get_transport_health(DEVICE_ID, "lan_udp")
        assert health.is_available is False
        assert health.last_failure_reason == lan_health.REASON_NO_RAW_PROFILE

    def test_a_send_stamps_a_time_but_never_availability(self):
        """A UDP datagram into the void proves nothing about the device."""
        coordinator = _coordinator(on_lan=False)
        lan_health.refresh(coordinator)

        lan_health.note_send(coordinator, DEVICE_ID)

        health = coordinator.get_transport_health(DEVICE_ID, "lan_udp")
        assert health.last_send_ts is not None
        assert health.is_available is False

    def test_no_receive_direction_is_ever_claimed(self):
        coordinator = _coordinator()
        lan_health.refresh(coordinator)
        lan_health.note_send(coordinator, DEVICE_ID)

        # Raw frames get no reply of any kind, so there is nothing to stamp.
        assert coordinator.get_transport_health(DEVICE_ID, "lan_udp").last_success_ts is None

    def test_a_hard_send_failure_is_recorded(self):
        coordinator = _coordinator()
        lan_health.refresh(coordinator)

        lan_health.note_failure(coordinator, DEVICE_ID)

        health = coordinator.get_transport_health(DEVICE_ID, "lan_udp")
        assert health.is_available is False
        assert health.last_failure_reason == lan_health.REASON_SEND_FAILED

    def test_refresh_clears_a_stale_gate_reason_but_not_a_send_failure(self):
        coordinator = _coordinator()
        lan_health.refresh(coordinator)
        lan_health.note_failure(coordinator, DEVICE_ID)

        lan_health.refresh(coordinator)

        health = coordinator.get_transport_health(DEVICE_ID, "lan_udp")
        # Reachability is restored (that is what refresh scores), but the last
        # hard failure stays on the record.
        assert health.is_available is True
        assert health.last_failure_reason == lan_health.REASON_SEND_FAILED


# ==============================================================================
# 4. Translations — both files, identical (the two-file rule)
# ==============================================================================


class TestTranslations:
    @staticmethod
    def _block(name: str) -> dict:
        data = json.loads((ROOT / name).read_text(encoding="utf-8"))
        return data["entity"]["binary_sensor"]

    def test_present_in_strings(self):
        assert self._block("strings.json")["lan_udp_connectivity"] == {"name": "LAN UDP"}

    def test_present_in_en_json(self):
        assert self._block("translations/en.json")["lan_udp_connectivity"] == {"name": "LAN UDP"}

    def test_identical_across_files(self):
        assert (
            self._block("strings.json")["lan_udp_connectivity"]
            == self._block("translations/en.json")["lan_udp_connectivity"]
        )
