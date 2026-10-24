"""Device-registry lookups that work across the supported Home Assistant versions."""

from __future__ import annotations

from homeassistant.helpers import device_registry as dr


def get_registry_device(
    registry: dr.DeviceRegistry, identifier: tuple[str, str], config_entry_id: str
) -> dr.DeviceEntry | None:
    """Return the config entry's device with this identifier, if any.

    Home Assistant 2026.10 deprecates ``DeviceRegistry.async_get_device``
    (removed in 2027.8; it raises in tests) in favour of
    ``async_get_device_by_identifier``, which older versions lack. The older
    lookup does not filter by entry, so its result is checked here instead.
    """
    by_identifier = getattr(registry, "async_get_device_by_identifier", None)
    if by_identifier is not None:
        device: dr.DeviceEntry | None = by_identifier(identifier, config_entry_id)
        return device
    legacy = registry.async_get_device(identifiers={identifier})  # pre-2026.10 path of get_registry_device
    return legacy if legacy is not None and config_entry_id in device_config_entry_ids(legacy) else None


def device_config_entry_ids(device: dr.DeviceEntry) -> set[str]:
    """Return the config entries a device belongs to.

    Home Assistant 2026.10 gives every device a single ``config_entry_id`` and
    deprecates the ``config_entries`` set that older versions use.
    """
    entry_id: str | None = getattr(device, "config_entry_id", None)
    if entry_id is not None:
        return {entry_id}
    return set(device.config_entries)
