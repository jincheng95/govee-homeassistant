"""Tests for the ``lan_udp`` transport health surface (fork feature).

Two entity-level requirements and one honesty requirement:

* ``lan_udp`` appears in the always-on ``*_connectivity`` binary sensor's
  attributes, alongside the transports that were already there.
* ``lan_udp`` appears as its own opt-in per-transport entity, following exactly
  the convention of the four existing ones (translation key, unique id, icon,
  and the same two-file translation rule).
* Its availability means "a raw frame sent now would have somewhere to go" —
  never "a write landed". Raw frames are unconfirmable, so a send stamps a
  timestamp and nothing more.

The honesty requirement is the one with teeth. The sensor must agree with the
writer's own gates — the LINK gates, i.e. ``lan_target(require_option=False)``,
which is what the zone and DIY paths use. So the cases that matter are the ones
where a device looks perfectly healthy on the LAN and still cannot be written
to (the model is on a stack that ignores raw frames over LAN), and the converse:
``enable_lan_raw_write`` off must NOT read as a dead link, because zone and DIY
writes go out over it regardless.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from custom_components.govee import lan_udp_health
from custom_components.govee.binary_sensor import (
    GoveeDeviceConnectivity,
    GoveeTransportConnectivity,
    _TRANSPORT_SPECS,
    async_setup_entry,
)
from custom_components.govee.const import CONF_ENABLE_LAN_RAW_WRITE, CONF_EXPOSE_TRANSPORT_ENTITIES
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


def _coordinator(device: GoveeDevice | None = None, *, on_lan: bool = True, raw_write: bool = True) -> MagicMock:
    device = device or _device()
    tracker = TransportHealthTracker()
    coordinator = MagicMock()
    # Explicit, never a MagicMock: an auto-truthy options mapping made every
    # option-gated assertion in this file pass vacuously.
    coordinator.config_entry.options = {CONF_ENABLE_LAN_RAW_WRITE: raw_write}
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
    """The fifth transport registered exactly like the four before it."""

    def test_lan_udp_is_a_tracked_transport(self):
        assert "lan_udp" in TRANSPORT_KINDS
        assert lan_udp_health.TRANSPORT_LAN_UDP == "lan_udp"

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
    """The opt-in per-transport entity and the always-on attributes."""

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
    async def test_entity_absent_when_not_exposed(self):
        coordinator = _coordinator()
        added: list = []
        await async_setup_entry(MagicMock(), _entry(coordinator, expose=False), lambda e: added.extend(e))

        assert not any(isinstance(e, GoveeTransportConnectivity) and e._transport == "lan_udp" for e in added)

    def test_aggregate_sensor_carries_lan_udp_attributes(self):
        device = _device()
        coordinator = _coordinator(device)
        lan_udp_health.refresh(coordinator)
        entity = _aggregate(coordinator, device)

        attrs = entity.extra_state_attributes
        assert attrs["lan_udp_available"] is True
        # The pre-existing transports are untouched.
        assert "cloud_api_available" in attrs
        assert "lan_available" in attrs

    def test_aggregate_sensor_reports_the_failure_reason(self):
        device = _device()
        coordinator = _coordinator(device, on_lan=False)
        lan_udp_health.refresh(coordinator)
        entity = _aggregate(coordinator, device)

        attrs = entity.extra_state_attributes
        assert attrs["lan_udp_available"] is False
        assert attrs["lan_udp_last_failure_reason"] == "no_lan_presence"


# ==============================================================================
# 3. What "available" honestly means
# ==============================================================================


class TestSemantics:
    """What availability is allowed to claim on an unconfirmable path."""

    def test_reachable_and_profiled_is_available(self):
        coordinator = _coordinator()
        lan_udp_health.refresh(coordinator)

        health = coordinator.get_transport_health(DEVICE_ID, "lan_udp")
        assert health.is_available is True
        assert health.last_failure_reason is None

    def test_absent_from_the_lan_map_is_unavailable(self):
        coordinator = _coordinator(on_lan=False)
        lan_udp_health.refresh(coordinator)

        health = coordinator.get_transport_health(DEVICE_ID, "lan_udp")
        assert health.is_available is False
        assert health.last_failure_reason == lan_udp_health.REASON_NO_LAN_PRESENCE

    def test_reachable_but_unprofiled_sku_is_unavailable(self):
        coordinator = _coordinator(_device(sku="H6199"))
        lan_udp_health.refresh(coordinator)

        health = coordinator.get_transport_health(DEVICE_ID, "lan_udp")
        assert health.is_available is False
        assert health.last_failure_reason == lan_udp_health.REASON_NO_RAW_PROFILE

    def test_the_transport_option_off_still_reports_available(self):
        """The zone/DIY paths use this link with the option off.

        `enable_lan_raw_write` buys an optional fast path for writes the cloud
        can also make; zone and DIY writes call `lan_target(require_option=
        False)` and use the link regardless. Reporting the link down while it is
        carrying those writes is the lie, not the other way round.
        """
        coordinator = _coordinator(raw_write=False)
        lan_udp_health.refresh(coordinator)

        health = coordinator.get_transport_health(DEVICE_ID, "lan_udp")
        assert health.is_available is True
        assert health.last_failure_reason is None

    def test_a_sku_that_ignores_lan_raw_frames_is_unavailable(self):
        """Profiled and reachable is not enough — the pipe has to be right.

        The H6046 answers LAN discovery perfectly and discards every raw frame
        it is sent there. Reporting it available would be exactly the silent
        no-op this transport is most prone to.
        """
        coordinator = _coordinator(_device(sku="H6046"))
        lan_udp_health.refresh(coordinator)

        health = coordinator.get_transport_health(DEVICE_ID, "lan_udp")
        assert health.is_available is False
        assert health.last_failure_reason == lan_udp_health.REASON_NO_LAN_RAW_TRANSPORT

    def test_a_sku_that_does_carry_lan_raw_frames_is_available(self):
        coordinator = _coordinator(_device(sku="H6076"))
        lan_udp_health.refresh(coordinator)

        assert coordinator.get_transport_health(DEVICE_ID, "lan_udp").is_available is True

    def test_regaining_lan_presence_clears_the_gate_reason(self):
        """Gate reasons are this pass's to clear; a send failure is not."""
        coordinator = _coordinator(on_lan=False)
        lan_udp_health.refresh(coordinator)
        assert coordinator.get_transport_health(DEVICE_ID, "lan_udp").last_failure_reason is not None
        coordinator._lan_devices[DEVICE_ID] = object()

        lan_udp_health.refresh(coordinator)

        health = coordinator.get_transport_health(DEVICE_ID, "lan_udp")
        assert health.is_available is True
        assert health.last_failure_reason is None

    def test_zone_lights_on_and_the_raw_option_off_reports_available(self):
        """The C9 boundary, stated as a state the sensor must report.

        Zone lights on + `enable_lan_raw_write` off is the shipped default
        combination for a multi-zone lamp, and every zone write in it goes over
        this link. The sensor has to agree with `lan_target(require_option=
        False)`, which is what those writes call.
        """
        from custom_components.govee.api import lan_raw
        from custom_components.govee.const import CONF_ENABLE_ZONE_LIGHTS

        coordinator = _coordinator(raw_write=False)
        coordinator.config_entry.options[CONF_ENABLE_ZONE_LIGHTS] = True
        lan_udp_health.refresh(coordinator)

        assert coordinator.get_transport_health(DEVICE_ID, "lan_udp").is_available is True
        # ...and that is not a guess: the writer agrees.
        assert lan_raw.lan_target(coordinator, DEVICE_ID, "H60B0", require_option=False) is not None

    def test_a_send_stamps_a_time_but_never_availability(self):
        """A UDP datagram into the void proves nothing about the device."""
        coordinator = _coordinator(on_lan=False)
        lan_udp_health.refresh(coordinator)

        lan_udp_health.note_send(coordinator, DEVICE_ID)

        health = coordinator.get_transport_health(DEVICE_ID, "lan_udp")
        assert health.last_send_ts is not None
        assert health.is_available is False

    def test_no_receive_direction_is_ever_claimed(self):
        coordinator = _coordinator()
        lan_udp_health.refresh(coordinator)
        lan_udp_health.note_send(coordinator, DEVICE_ID)

        # Raw frames get no reply of any kind, so there is nothing to stamp.
        assert coordinator.get_transport_health(DEVICE_ID, "lan_udp").last_success_ts is None

    def test_a_hard_send_failure_is_recorded(self):
        coordinator = _coordinator()
        lan_udp_health.refresh(coordinator)

        lan_udp_health.note_failure(coordinator, DEVICE_ID)

        health = coordinator.get_transport_health(DEVICE_ID, "lan_udp")
        assert health.is_available is False
        assert health.last_failure_reason == lan_udp_health.REASON_SEND_FAILED

    def test_a_send_failure_survives_the_gate_re_score(self):
        """Never available *and* carrying a failure reason — that pair is a lie."""
        coordinator = _coordinator()
        lan_udp_health.refresh(coordinator)
        lan_udp_health.note_failure(coordinator, DEVICE_ID)

        lan_udp_health.refresh(coordinator)

        health = coordinator.get_transport_health(DEVICE_ID, "lan_udp")
        assert health.is_available is False
        assert health.last_failure_reason == lan_udp_health.REASON_SEND_FAILED

    def test_an_accepted_send_clears_the_send_failure(self):
        """The socket accepting a datagram is what disproves 'the socket refused'."""
        coordinator = _coordinator()
        lan_udp_health.refresh(coordinator)
        lan_udp_health.note_failure(coordinator, DEVICE_ID)

        lan_udp_health.note_send(coordinator, DEVICE_ID)

        health = coordinator.get_transport_health(DEVICE_ID, "lan_udp")
        assert health.is_available is True
        assert health.last_failure_reason is None

    def test_a_gate_still_wins_over_a_cleared_send_failure(self):
        coordinator = _coordinator()
        lan_udp_health.refresh(coordinator)
        lan_udp_health.note_failure(coordinator, DEVICE_ID)
        lan_udp_health.note_send(coordinator, DEVICE_ID)
        coordinator._lan_devices.clear()

        lan_udp_health.refresh(coordinator)

        health = coordinator.get_transport_health(DEVICE_ID, "lan_udp")
        assert health.is_available is False
        assert health.last_failure_reason == lan_udp_health.REASON_NO_LAN_PRESENCE


# ==============================================================================
# 4. Translations — both files, identical (the two-file rule)
# ==============================================================================


class TestTranslations:
    """The two-file translation rule, applied to the new key."""

    @staticmethod
    def _block(name: str) -> dict:
        data = json.loads((ROOT / name).read_text(encoding="utf-8"))
        return data["entity"]["binary_sensor"]

    def test_present_in_strings(self):
        assert self._block("strings.json")["lan_udp_connectivity"] == {"name": "LAN (raw)"}

    def test_present_in_en_json(self):
        assert self._block("translations/en.json")["lan_udp_connectivity"] == {"name": "LAN (raw)"}

    def test_identical_across_files(self):
        assert (
            self._block("strings.json")["lan_udp_connectivity"]
            == self._block("translations/en.json")["lan_udp_connectivity"]
        )
