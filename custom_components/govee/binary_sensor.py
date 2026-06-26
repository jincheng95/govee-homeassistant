"""Binary sensor platform for Govee integration.

Exposes per-device connectivity status for each transport (Cloud REST
API, AWS IoT MQTT, direct BLE, local LAN) as CONNECTIVITY diagnostic
entities.

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
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_EXPOSE_TRANSPORT_ENTITIES,
    DEFAULT_EXPOSE_TRANSPORT_ENTITIES,
    DOMAIN,
)
from .coordinator import GoveeCoordinator
from .entity import GoveeEntity
from .models import TransportKind
from .models.transport import TRANSPORT_KINDS
from .models.device import GoveeLeakSensor, leak_sensor_device_info

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0


_TRANSPORT_SPECS: tuple[tuple[TransportKind, str, str], ...] = (
    ("cloud_api", "cloud_api_connectivity", "mdi:cloud"),
    ("mqtt", "mqtt_connectivity", "mdi:cloud-sync"),
    ("ble", "ble_connectivity", "mdi:bluetooth"),
    ("lan", "lan_connectivity", "mdi:lan"),
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
        # Standalone water-leak detectors (H5054) that surface in the developer
        # device list with a bodyAppearedEvent capability — issue #62.
        if device.supports_water_leak_event:
            entities.append(GoveeWaterLeakBinarySensor(coordinator, device))
        # Air-quality sensor presence (H5106 monitor, H7124/H7126 purifiers).
        # Govee's Developer API returns only a constant index (always 1 on the
        # H5106), never a real PM2.5 measurement, so this is a diagnostic
        # presence flag rather than a numeric AQI sensor — issue #114.
        if device.supports_air_quality:
            entities.append(GoveeAirQualityBinarySensor(coordinator, device))
        # Overall per-device connectivity (one entity, always exposed) — carries
        # the full per-transport last-received / last-sent breakdown as
        # attributes. The granular per-transport entities below stay opt-in.
        entities.append(GoveeDeviceConnectivity(coordinator, device))

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
            entities.append(GoveeLeakHubOnlineSensor(coordinator, sensor.hub_device_id))

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


class GoveeWaterLeakBinarySensor(GoveeEntity, BinarySensorEntity):
    """Binary sensor reporting a water-leak trip for standalone detectors (H5054).

    Detected via the ``bodyAppearedEvent`` capability on the developer-API
    device, but the trip itself never reaches the developer API or AWS IoT —
    H5054 is a 433 MHz RF-only sensor bridged to the cloud by an H5040 gateway
    (issue #62). The coordinator polls the account ``warnMessage`` history for
    the leak state and writes it to ``state.water_leak``.
    """

    _attr_device_class = BinarySensorDeviceClass.MOISTURE
    _attr_translation_key = "govee_water_leak"
    _attr_icon = "mdi:water-alert"

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        device: Any,
    ) -> None:
        """Initialize the water-leak binary sensor."""
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.device_id}_water_leak"

    @property
    def available(self) -> bool:
        """Available whenever the coordinator is — not gated on device online.

        H5054 water detectors are sleepy battery devices that report
        ``online: false`` at poll time (they wake only to push an event). The
        base ``GoveeEntity.available`` gates on ``state.online``, which would
        render the leak sensor permanently unavailable — the device shows up
        with an error and a real leak could never surface. Report availability
        from the coordinator instead, like the connectivity sensors do.
        """
        return self.coordinator.last_update_success

    @property
    def is_on(self) -> bool | None:
        """Return True when water is detected."""
        state = self.device_state
        return state.water_leak if state else None


class GoveeAirQualityBinarySensor(GoveeEntity, BinarySensorEntity):
    """Air-quality sensor presence flag (H5106, H7124/H7126) — issue #114.

    The Developer API's ``airQuality`` property returns a single index integer
    with no PM2.5 µg/m³ field, and in practice it never moves off a constant
    value (always ``1`` on the H5106) — it reports that an air-quality sensor is
    *present*, not a usable reading. So this is a diagnostic on/off entity
    (``on`` = the device reports a non-zero air-quality value) rather than the
    earlier numeric AQI sensor, which read as a real measurement it never was.
    """

    _attr_device_class = BinarySensorDeviceClass.PRESENCE
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "air_quality_sensor"
    _attr_icon = "mdi:air-filter"

    def __init__(self, coordinator: GoveeCoordinator, device: Any) -> None:
        """Initialize the air-quality presence binary sensor."""
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.device_id}_air_quality"

    @property
    def is_on(self) -> bool | None:
        """Return True when the device reports a non-zero air-quality value."""
        state = self.device_state
        if state is None or state.air_quality is None:
            return None
        return state.air_quality > 0


class GoveeDeviceConnectivity(GoveeEntity, BinarySensorEntity):
    """Overall per-device connectivity (one entity per device, always exposed).

    ``is_on`` reflects whether any transport can currently reach the device.
    The full directional breakdown — per-transport last received / last sent /
    last failure — rides along as attributes so users get "last MQTT push /
    receive, last API push / receive" at a glance without enabling the
    granular per-transport entities.
    """

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "device_connectivity"
    _attr_icon = "mdi:lan-connect"

    def __init__(self, coordinator: GoveeCoordinator, device: Any) -> None:
        """Initialize the overall connectivity binary sensor."""
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.device_id}_connectivity"

    @property
    def available(self) -> bool:
        """Available whenever the coordinator is — reports its own on/off.

        Like the per-transport sensors, it must not inherit the device's
        online flag, or an offline device would hide the very diagnostic
        needed to understand why it is offline.
        """
        return self.coordinator.last_update_success

    @property
    def is_on(self) -> bool | None:
        """Return True when any transport is currently usable for this device."""
        if self._device.is_group:
            return True
        any_tracked = False
        for kind in TRANSPORT_KINDS:
            health = self.coordinator.get_transport_health(self._device_id, kind)
            if health is None:
                continue
            any_tracked = True
            if health.is_available:
                return True
        if not any_tracked:
            return None
        return False

    def _last_received(self, kind: TransportKind, health: Any) -> Any:
        """Receive timestamp for a transport (per-device MQTT preferred)."""
        if kind == "mqtt":
            per_device = self.coordinator.mqtt_last_receive_for(self._device_id)
            if per_device is not None:
                return per_device
        return health.last_success_ts

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Per-transport directional freshness breakdown."""
        attrs: dict[str, Any] = {}
        for kind in TRANSPORT_KINDS:
            health = self.coordinator.get_transport_health(self._device_id, kind)
            if health is None:
                continue
            received = self._last_received(kind, health)
            if received is not None:
                attrs[f"{kind}_last_received"] = received.isoformat()
            if health.last_send_ts is not None:
                attrs[f"{kind}_last_sent"] = health.last_send_ts.isoformat()
            if health.last_failure_reason is not None:
                attrs[f"{kind}_last_failure_reason"] = health.last_failure_reason
            attrs[f"{kind}_available"] = health.is_available
        return attrs


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
        # Receive direction: last_success_ts is the inbound stamp (poll read or
        # inbound MQTT message). For mqtt, prefer the per-device receive
        # timestamp when present (the hub scalar covers all devices).
        last_received = health.last_success_ts
        if self._transport == "mqtt":
            per_device = self.coordinator.mqtt_last_receive_for(self._device_id)
            if per_device is not None:
                last_received = per_device
        if last_received is not None:
            attrs["last_received"] = last_received.isoformat()
            # Back-compat alias (deprecated, one release) — last_success was the
            # pre-directional combined stamp; now equals the receive timestamp.
            attrs["last_success"] = last_received.isoformat()
        if health.last_send_ts is not None:
            attrs["last_sent"] = health.last_send_ts.isoformat()
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
        return leak_sensor_device_info(self._sensor, DOMAIN)

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
        return leak_sensor_device_info(self._sensor, DOMAIN)

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
