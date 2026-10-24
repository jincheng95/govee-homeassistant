"""Device-registry lookups across Home Assistant versions (registry.py)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from custom_components.govee.registry import device_config_entry_ids, get_registry_device

IDENTIFIER = ("govee", "AA:BB:CC:DD:EE:FF:00:11")


class TestGetRegistryDevice:
    def test_uses_the_per_entry_lookup_when_present(self):
        """Home Assistant 2026.10+: async_get_device (deprecated) is never called."""
        device = SimpleNamespace(config_entry_id="entry")
        registry = MagicMock(spec=["async_get_device_by_identifier", "async_get_device"])
        registry.async_get_device_by_identifier.return_value = device

        assert get_registry_device(registry, IDENTIFIER, "entry") is device
        registry.async_get_device_by_identifier.assert_called_once_with(IDENTIFIER, "entry")
        registry.async_get_device.assert_not_called()

    def test_falls_back_and_filters_by_entry_on_older_versions(self):
        device = SimpleNamespace(config_entries={"entry", "other"})
        registry = MagicMock(spec=["async_get_device"])
        registry.async_get_device.return_value = device

        assert get_registry_device(registry, IDENTIFIER, "entry") is device
        assert get_registry_device(registry, IDENTIFIER, "third") is None
        registry.async_get_device.assert_called_with(identifiers={IDENTIFIER})

    def test_fallback_without_a_device(self):
        registry = MagicMock(spec=["async_get_device"])
        registry.async_get_device.return_value = None

        assert get_registry_device(registry, IDENTIFIER, "entry") is None


class TestDeviceConfigEntryIds:
    def test_single_entry_devices(self):
        assert device_config_entry_ids(SimpleNamespace(config_entry_id="entry")) == {"entry"}

    def test_older_versions_read_the_set(self):
        assert device_config_entry_ids(SimpleNamespace(config_entries={"a", "b"})) == {"a", "b"}
