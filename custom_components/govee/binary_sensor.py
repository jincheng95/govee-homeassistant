"""Binary sensor platform for Govee integration.

Exposes per-device connectivity status for each transport (Cloud REST
API, AWS IoT MQTT, direct BLE) as CONNECTIVITY diagnostic entities.

Also provides leak sensor binary sensors:
- Moisture detection (real-time via MQTT multiSync)
- Sensor connectivity (BFF API polling)
- Gateway connectivity (BFF API polling)

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
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_EXPOSE_TRANSPORT_ENTITIES,
    DEFAULT_EXPOSE_TRANSPORT_ENTITIES,
    DOMAIN,
)
from .coordinator import GoveeCoordinator
from .entity import GoveeEntity
from .models import TransportKind
from .models.device import GoveeLeakSensor, leak_sensor_device_info

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

    # Leak sensor entities — always exposed when leak sensors are discovered.
    # Register hub devices first so leak sensors' `via_device` link resolves
    # (must run after orphan-cleanup in __init__.py, hence here, not in
    # the coordinator's _async_setup).
    coordinator.register_leak_hubs()
    seen_hubs: set[str] = set()
    for sensor in coordinator.leak_sensors.values():
        entities.append(GoveeLeakBinarySensor(coordinator, sensor))
        entities.append(GoveeLeakOnlineSensor(coordinator, sensor))
        if sensor.hub_device_id and sensor.hub_device_id not in seen_hubs:
            seen_hubs.add(sensor.hub_device_id)
            entities.append(
                GoveeLeakHubOnlineSensor(coordinator, sensor.hub_device_id)
            )

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


class GoveeLeakBinarySensor(BinarySensorEntity):
    """Binary sensor for Govee leak detection (MQTT real-time).

    Subscribes to the leak-specific dispatcher signal rather than the
    coordinator's generic update to avoid churning unrelated entities.
    """

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.MOISTURE

    def __init__(self, coordinator: GoveeCoordinator, sensor: GoveeLeakSensor) -> None:
        self._coordinator = coordinator
        self._sensor = sensor
        self._attr_unique_id = f"{sensor.device_id}_leak"
        # name=None → entity uses the device name directly (e.g. "Water heater")
        self._attr_name = None

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(**leak_sensor_device_info(self._sensor, DOMAIN))

    @property
    def is_on(self) -> bool | None:
        state = self._coordinator.leak_states.get(self._sensor.device_id)
        return state.is_wet if state else None

    @property
    def available(self) -> bool:
        return self._sensor.device_id in self._coordinator.leak_states

    async def async_added_to_hass(self) -> None:
        """Subscribe to leak-specific dispatcher signal."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, f"{DOMAIN}_leak_update", self._handle_leak_update
            )
        )

    @callback
    def _handle_leak_update(self) -> None:
        """Handle leak-specific update signal."""
        self.async_write_ha_state()


class GoveeLeakOnlineSensor(BinarySensorEntity):
    """Binary sensor for the LoRa link between the leak sensor and its hub."""

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "leak_online"
    _attr_icon = "mdi:radio-tower"

    def __init__(self, coordinator: GoveeCoordinator, sensor: GoveeLeakSensor) -> None:
        self._coordinator = coordinator
        self._sensor = sensor
        self._attr_unique_id = f"{sensor.device_id}_online"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(**leak_sensor_device_info(self._sensor, DOMAIN))

    @property
    def is_on(self) -> bool | None:
        state = self._coordinator.leak_states.get(self._sensor.device_id)
        return state.online if state else None

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, f"{DOMAIN}_leak_update", self._handle_leak_update
            )
        )

    @callback
    def _handle_leak_update(self) -> None:
        self.async_write_ha_state()


class GoveeLeakHubOnlineSensor(BinarySensorEntity):
    """Binary sensor for the leak sensor hub's cloud connectivity.

    One entity per hub. State is derived from the ``gateway_online`` field
    of any child leak sensor (all sensors on the same hub share the same
    cloud-connection state).
    """

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_translation_key = "leak_hub_online"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:web"

    def __init__(self, coordinator: GoveeCoordinator, hub_device_id: str) -> None:
        self._coordinator = coordinator
        self._hub_device_id = hub_device_id
        self._attr_unique_id = f"{hub_device_id}_hub_online"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={(DOMAIN, self._hub_device_id)})

    @property
    def is_on(self) -> bool | None:
        for sensor in self._coordinator.leak_sensors.values():
            if sensor.hub_device_id != self._hub_device_id:
                continue
            state = self._coordinator.leak_states.get(sensor.device_id)
            if state is not None:
                return state.gateway_online
        return None

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, f"{DOMAIN}_leak_update", self._handle_leak_update
            )
        )

    @callback
    def _handle_leak_update(self) -> None:
        self.async_write_ha_state()