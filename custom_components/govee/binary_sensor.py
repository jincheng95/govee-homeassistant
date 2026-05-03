"""Binary sensor platform for Govee integration.

Exposes per-device connectivity status for each transport (Cloud REST
API, AWS IoT MQTT, direct BLE) as CONNECTIVITY diagnostic entities.

Entities are opt-in via the ``expose_transport_entities`` option to avoid
creating 3×N diagnostic entities by default.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_EXPOSE_TRANSPORT_ENTITIES,
    DEFAULT_EXPOSE_TRANSPORT_ENTITIES,
)
from .coordinator import GoveeCoordinator
from .entity import GoveeEntity
from .models import TransportKind

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0


_TRANSPORT_SPECS: tuple[tuple[TransportKind, str, str], ...] = (
    ("cloud_api", "cloud_api_connectivity", "mdi:cloud"),
    ("mqtt", "mqtt_connectivity", "mdi:cloud-sync"),
    ("ble", "ble_connectivity", "mdi:bluetooth"),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Govee binary sensors from a config entry."""
    coordinator: GoveeCoordinator = entry.runtime_data

    entities: list[BinarySensorEntity] = []

    # Water-tank-full sensor for dehumidifiers — always exposed since it
    # maps to a real device event and there's one per device, not 3×N.
    for device in coordinator.devices.values():
        if device.is_group:
            continue
        if device.supports_water_full_event:
            entities.append(GoveeWaterFullBinarySensor(coordinator, device))

    # Transport connectivity entities are opt-in to avoid creating 3×N
    # diagnostic entities by default.
    if entry.options.get(
        CONF_EXPOSE_TRANSPORT_ENTITIES, DEFAULT_EXPOSE_TRANSPORT_ENTITIES
    ):
        for device in coordinator.devices.values():
            if device.is_group:
                continue
            for kind, translation_key, icon in _TRANSPORT_SPECS:
                entities.append(
                    GoveeTransportConnectivity(
                        coordinator=coordinator,
                        device=device,
                        transport=kind,
                        translation_key=translation_key,
                        icon=icon,
                    )
                )
    else:
        _LOGGER.debug("Transport connectivity entities disabled via options; skipping")

    if entities:
        async_add_entities(entities)
        _LOGGER.debug("Set up %d binary sensor entities", len(entities))


class GoveeWaterFullBinarySensor(GoveeEntity, BinarySensorEntity):
    """Binary sensor reporting the water-tank-full event for dehumidifiers."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_translation_key = "govee_water_full"
    _attr_icon = "mdi:cup-water"

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        device: Any,
    ) -> None:
        """Initialize the water-full binary sensor."""
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.device_id}_water_full"

    @property
    def is_on(self) -> bool | None:
        """Return True when the water tank is full."""
        state = self.device_state
        return state.water_full if state else None


class GoveeTransportConnectivity(GoveeEntity, BinarySensorEntity):
    """Per-device connectivity status for a single transport."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        device: Any,
        transport: TransportKind,
        translation_key: str,
        icon: str,
    ) -> None:
        """Initialize the connectivity binary sensor."""
        super().__init__(coordinator, device)
        self._transport = transport
        self._attr_translation_key = translation_key
        self._attr_icon = icon
        self._attr_unique_id = f"{device.device_id}_{transport}_connectivity"

    @property
    def is_on(self) -> bool | None:
        """Return True when the transport is currently usable for this device."""
        health = self.coordinator.get_transport_health(self._device_id, self._transport)
        if health is None:
            return None
        return health.is_available

    @property
    def available(self) -> bool:
        """Connectivity sensors are available whenever the coordinator is.

        They report their own state (on/off) rather than inheriting the
        main device's online flag — otherwise an offline device would
        hide the very diagnostic needed to understand why.
        """
        return self.coordinator.last_update_success

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return timestamps and failure reason for this transport."""
        health = self.coordinator.get_transport_health(self._device_id, self._transport)
        if health is None:
            return {}
        attrs: dict[str, Any] = {}
        if health.last_success_ts is not None:
            attrs["last_success"] = health.last_success_ts.isoformat()
        if health.last_failure_ts is not None:
            attrs["last_failure"] = health.last_failure_ts.isoformat()
        if health.last_failure_reason is not None:
            attrs["last_failure_reason"] = health.last_failure_reason
        return attrs
