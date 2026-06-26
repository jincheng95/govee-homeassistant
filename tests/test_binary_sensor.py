"""Tests for the Govee binary_sensor platform — LAN connectivity (LAN-014).

Covers the LAN slice of the per-transport connectivity surface:
- The ``lan_connectivity`` per-transport entity is created only under the
  opt-in ``CONF_EXPOSE_TRANSPORT_ENTITIES`` option (and skipped otherwise).
- The always-on aggregate ``GoveeDeviceConnectivity`` folds ``lan`` in for
  free because it iterates ``TRANSPORT_KINDS`` (which now includes ``lan``).
- The ``lan_connectivity`` translation key is present and identical in BOTH
  ``strings.json`` and ``translations/en.json`` (the CLAUDE.md two-file rule).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

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


def _light() -> GoveeDevice:
    """A plain light with only power — no water/air/leak entities spawned."""
    return GoveeDevice(
        device_id="AA:BB:CC:DD:EE:FF:00:11",
        sku="H6072",
        name="Test Lamp",
        device_type=DEVICE_TYPE_LIGHT,
        capabilities=(
            GoveeCapability(type=CAPABILITY_ON_OFF, instance=INSTANCE_POWER, parameters={}),
        ),
    )


def _coordinator(device: GoveeDevice) -> MagicMock:
    coordinator = MagicMock()
    coordinator.devices = {device.device_id: device}
    coordinator.leak_sensors = {}
    coordinator.register_leak_hubs = MagicMock()
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


class TestTransportSpecs:
    def test_lan_spec_appended(self):
        # The hand-maintained spec table must carry the LAN row.
        assert ("lan", "lan_connectivity", "mdi:lan") in _TRANSPORT_SPECS
        # ...and exactly once.
        lan_specs = [s for s in _TRANSPORT_SPECS if s[0] == "lan"]
        assert len(lan_specs) == 1


class TestLanTransportEntity:
    async def test_lan_connectivity_entity_created_when_exposed(self):
        device = _light()
        coordinator = _coordinator(device)
        entry = _entry(coordinator, expose=True)

        added: list = []
        await async_setup_entry(MagicMock(), entry, lambda e: added.extend(e))

        lan_entities = [
            e
            for e in added
            if isinstance(e, GoveeTransportConnectivity) and e._transport == "lan"
        ]
        assert len(lan_entities) == 1
        lan = lan_entities[0]
        assert lan.translation_key == "lan_connectivity"
        assert lan.unique_id == f"{device.device_id}_lan_connectivity"
        assert lan.icon == "mdi:lan"

    async def test_lan_connectivity_entity_absent_when_not_exposed(self):
        device = _light()
        coordinator = _coordinator(device)
        entry = _entry(coordinator, expose=False)

        added: list = []
        await async_setup_entry(MagicMock(), entry, lambda e: added.extend(e))

        # No per-transport entities at all when the opt-in is off.
        assert not any(isinstance(e, GoveeTransportConnectivity) for e in added)

    async def test_lan_entity_reports_health(self):
        device = _light()
        coordinator = _coordinator(device)
        coordinator.get_transport_health.return_value = TransportHealth(
            transport="lan", is_available=True
        )
        entry = _entry(coordinator, expose=True)

        added: list = []
        await async_setup_entry(MagicMock(), entry, lambda e: added.extend(e))

        lan = next(
            e
            for e in added
            if isinstance(e, GoveeTransportConnectivity) and e._transport == "lan"
        )
        assert lan.is_on is True
        coordinator.get_transport_health.assert_called_with(device.device_id, "lan")


class TestAggregateFoldsLan:
    def test_is_on_true_when_only_lan_available(self):
        # Proves the always-on aggregate iterates "lan" — if it didn't, an
        # only-LAN-available device would read as unreachable.
        coordinator = MagicMock()
        coordinator.get_transport_health.side_effect = lambda did, kind: TransportHealth(
            transport=kind, is_available=(kind == "lan")
        )
        device = _light()
        entity = _aggregate(coordinator, device)
        assert entity.is_on is True

    def test_attributes_include_lan(self):
        lan_health = TransportHealth(transport="lan", is_available=True)

        def _health(did, kind):
            return lan_health if kind == "lan" else None

        coordinator = MagicMock()
        coordinator.get_transport_health.side_effect = _health
        coordinator.mqtt_last_receive_for.return_value = None
        device = _light()
        entity = _aggregate(coordinator, device)

        attrs = entity.extra_state_attributes
        assert attrs["lan_available"] is True


class TestTranslationKeySync:
    def _block(self, filename: str) -> dict:
        base = Path(__file__).resolve().parent.parent / "custom_components" / "govee"
        path = base / filename
        with open(path) as handle:
            data = json.load(handle)
        return data["entity"]["binary_sensor"]

    def test_lan_connectivity_in_strings(self):
        block = self._block("strings.json")
        assert block["lan_connectivity"] == {"name": "LAN"}

    def test_lan_connectivity_in_en_json(self):
        block = self._block("translations/en.json")
        assert block["lan_connectivity"] == {"name": "LAN"}

    def test_lan_connectivity_identical_across_files(self):
        strings_block = self._block("strings.json")
        en_block = self._block("translations/en.json")
        assert strings_block["lan_connectivity"] == en_block["lan_connectivity"]
