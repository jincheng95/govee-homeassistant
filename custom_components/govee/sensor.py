"""Sensor platform for Govee integration.

Provides sensor entities for:
- Rate limit remaining (diagnostic)
- MQTT connection status (diagnostic)
- Temperature / humidity properties on stand-alone sensors (H5109, H5179)
- Leak sensor battery level (from BFF API polling)
- Leak sensor last wet event timestamp (from BFF API polling)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    EntityCategory,
    PERCENTAGE,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_API_TEMPERATURE_UNIT,
    DEFAULT_API_TEMPERATURE_UNIT,
    DOMAIN,
)
from .coordinator import GoveeCoordinator
from .entity import GoveeEntity
from .models import GoveeDevice
from .models.device import GoveeLeakSensor, leak_sensor_device_info

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Govee sensors from a config entry."""
    coordinator: GoveeCoordinator = entry.runtime_data

    entities: list[SensorEntity] = [
        GoveeRateLimitSensor(coordinator, entry.entry_id),
    ]

    # Add MQTT status sensors if MQTT is configured
    if coordinator.mqtt_client is not None:
        entities.append(GoveeMqttStatusSensor(coordinator, entry.entry_id))
        entities.append(GoveeMqttLastReceivedSensor(coordinator, entry.entry_id))

    # Per-device temperature / humidity sensors for stand-alone sensors
    # like H5109 and H5179 (issue #62). Anything that exposes the
    # corresponding `property` capability gets the entity, regardless of
    # device_type — the integration shouldn't have to know about every SKU.
    for device in coordinator.devices.values():
        if device.is_group:
            continue
        # Per-device data-freshness diagnostic for every physical device.
        entities.append(GoveeAllDataLastUpdatedSensor(coordinator, device))
        if device.supports_temperature_sensor:
            entities.append(GoveeTemperatureSensor(coordinator, device))
        if device.supports_humidity_sensor:
            entities.append(GoveeHumiditySensor(coordinator, device))
        if device.supports_temperature_sensor or device.supports_humidity_sensor:
            entities.append(GoveeSensorReadingTimestampSensor(coordinator, device))

    # Add leak sensor entities. Register hub devices first so the leak
    # sensors' `via_device` link resolves (must run after orphan-cleanup
    # in __init__.py, hence here, not in the coordinator's _async_setup).
    coordinator.register_leak_hubs()
    seen_hubs: set[str] = set()
    for sensor in coordinator.leak_sensors.values():
        entities.append(GoveeLeakBatterySensor(coordinator, sensor))
        entities.append(GoveeLeakLastWetSensor(coordinator, sensor))
        entities.append(GoveeLeakAlertStatusSensor(coordinator, sensor))
        entities.append(GoveeLeakDeviceAddressSensor(sensor))
        if sensor.hub_device_id and sensor.hub_device_id not in seen_hubs:
            seen_hubs.add(sensor.hub_device_id)
            entities.append(GoveeLeakHubAddressSensor(sensor.hub_device_id))

    async_add_entities(entities)
    _LOGGER.debug("Set up %d Govee sensor entities", len(entities))


class GoveeRateLimitSensor(CoordinatorEntity["GoveeCoordinator"], SensorEntity):
    """Sensor showing API rate limit remaining.

    Helps users monitor their API usage and avoid hitting limits.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "rate_limit_remaining"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "requests"
    _attr_icon = "mdi:speedometer"

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        entry_id: str,
    ) -> None:
        """Initialize the rate limit sensor."""
        super().__init__(coordinator)

        self._attr_unique_id = f"{entry_id}_rate_limit"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info for the integration hub."""
        return DeviceInfo(
            identifiers={(DOMAIN, "hub")},
            name="Govee Integration",
            manufacturer="Govee",
            model="Cloud API",
        )

    @property
    def native_value(self) -> int:
        """Return the current rate limit remaining."""
        return self.coordinator.api_rate_limit_remaining

    @property
    def extra_state_attributes(self) -> dict[str, int]:
        """Return additional rate limit info."""
        return {
            "total_limit": self.coordinator.api_rate_limit_total,
            "reset_time": self.coordinator.api_rate_limit_reset,
        }


class GoveeMqttStatusSensor(CoordinatorEntity["GoveeCoordinator"], SensorEntity):
    """Sensor showing MQTT connection status.

    Indicates whether real-time push updates are working.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "mqtt_status"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["connected", "disconnected", "unavailable"]
    _attr_icon = "mdi:cloud-sync"

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        entry_id: str,
    ) -> None:
        """Initialize the MQTT status sensor."""
        super().__init__(coordinator)

        self._attr_unique_id = f"{entry_id}_mqtt_status"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info for the integration hub."""
        return DeviceInfo(
            identifiers={(DOMAIN, "hub")},
            name="Govee Integration",
            manufacturer="Govee",
            model="Cloud API",
        )

    @property
    def native_value(self) -> str:
        """Return the current MQTT status."""
        mqtt_client = self.coordinator.mqtt_client
        if mqtt_client is None:
            return "unavailable"
        return "connected" if mqtt_client.connected else "disconnected"


class GoveeMqttLastReceivedSensor(CoordinatorEntity["GoveeCoordinator"], SensorEntity):
    """Timestamp of the last inbound MQTT state message (hub-level diagnostic).

    Shows when a real-time push update last arrived from AWS IoT. Renders as
    "X minutes ago" in HA. Reports unavailable until the first push arrives.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "mqtt_last_received"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:cloud-sync-outline"

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        entry_id: str,
    ) -> None:
        """Initialize the MQTT last-received timestamp sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_mqtt_last_received"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info for the integration hub."""
        return DeviceInfo(
            identifiers={(DOMAIN, "hub")},
            name="Govee Integration",
            manufacturer="Govee",
            model="Cloud API",
        )

    @property
    def native_value(self) -> datetime | None:
        """Return the UTC timestamp of the last MQTT push, or None."""
        return self.coordinator.mqtt_last_message_ts


class GoveeTemperatureSensor(GoveeEntity, SensorEntity):
    """Read-only temperature reading from devices like H5109 and H5179.

    Backed by the ``devices.capabilities.property`` / ``sensorTemperature``
    capability. Values are pushed through the standard coordinator state
    flow so MQTT updates and API polls both feed it.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "sensor_temperature"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_suggested_display_precision = 1

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        device: GoveeDevice,
    ) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.device_id}_temperature"

    @property
    def native_value(self) -> float | None:
        state = self.device_state
        if not state or state.sensor_temperature is None:
            return None

        value = float(state.sensor_temperature)

        # Some thermometer/hygrometer SKUs (H5179, H5109, H5110, HS5108,
        # HS5106) return °F via the Cloud API without unit metadata. When
        # the user opts in, normalize to °C so HA renders the correct value.
        config_entry = self.coordinator.config_entry
        api_unit = (
            config_entry.options.get(
                CONF_API_TEMPERATURE_UNIT,
                DEFAULT_API_TEMPERATURE_UNIT,
            )
            if config_entry is not None
            else DEFAULT_API_TEMPERATURE_UNIT
        )
        if api_unit == "fahrenheit":
            return (value - 32.0) * (5.0 / 9.0)

        return value


class GoveeHumiditySensor(GoveeEntity, SensorEntity):
    """Read-only humidity reading from devices like H5109 and H5179."""

    _attr_has_entity_name = True
    _attr_translation_key = "sensor_humidity"
    _attr_device_class = SensorDeviceClass.HUMIDITY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_suggested_display_precision = 1

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        device: GoveeDevice,
    ) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.device_id}_humidity"

    @property
    def native_value(self) -> float | None:
        state = self.device_state
        return state.sensor_humidity if state else None


class GoveeSensorReadingTimestampSensor(GoveeEntity, SensorEntity):
    """When this device's temperature/humidity reading last changed.

    Govee batches BLE-bridged thermometers (H5075/H5110 via an H5151 gateway)
    to the cloud every 15-60 min, so a reading can look "frozen" while polling
    is healthy. This diagnostic timestamp makes the reading's age visible —
    "updated 22 min ago" — instead of leaving users guessing (#83). Semantic
    is last *change* (the Cloud API does not expose the device reading time).
    """

    _attr_has_entity_name = True
    _attr_translation_key = "sensor_reading_changed"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        device: GoveeDevice,
    ) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.device_id}_reading_changed"

    @property
    def native_value(self) -> datetime | None:
        return self.coordinator.sensor_reading_changed_at(self._device.device_id)


class GoveeAllDataLastUpdatedSensor(GoveeEntity, SensorEntity):
    """When this device last received data over any transport.

    Max of the per-transport last-success timestamps (Cloud API / MQTT /
    BLE). Renders as a relative "X ago" so users can see overall data
    freshness per device at a glance ("All Data Last Updated").
    """

    _attr_has_entity_name = True
    _attr_translation_key = "all_data_last_updated"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:database-clock"

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        device: GoveeDevice,
    ) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.device_id}_all_data_last_updated"

    @property
    def native_value(self) -> datetime | None:
        return self.coordinator.device_data_last_updated(self._device.device_id)


class GoveeLeakBatterySensor(SensorEntity):
    """Sensor showing leak sensor battery level (from BFF API polling).

    Uses dispatcher signal for updates to avoid churning unrelated entities.
    """

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "leak_battery"

    def __init__(self, coordinator: GoveeCoordinator, sensor: GoveeLeakSensor) -> None:
        self._coordinator = coordinator
        self._sensor = sensor
        self._attr_unique_id = f"{sensor.device_id}_battery"

    @property
    def device_info(self) -> DeviceInfo:
        return leak_sensor_device_info(self._sensor, DOMAIN)

    @property
    def native_value(self) -> int | None:
        state = self._coordinator.leak_states.get(self._sensor.device_id)
        return state.battery if state else None

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, f"{DOMAIN}_leak_update", self._handle_leak_update
            )
        )

    @callback
    def _handle_leak_update(self) -> None:
        self.async_write_ha_state()


class GoveeLeakLastWetSensor(SensorEntity):
    """Sensor showing when the last leak was detected."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_translation_key = "leak_last_wet"

    def __init__(self, coordinator: GoveeCoordinator, sensor: GoveeLeakSensor) -> None:
        self._coordinator = coordinator
        self._sensor = sensor
        self._attr_unique_id = f"{sensor.device_id}_last_wet"

    @property
    def device_info(self) -> DeviceInfo:
        return leak_sensor_device_info(self._sensor, DOMAIN)

    @property
    def native_value(self) -> datetime | None:
        state = self._coordinator.leak_states.get(self._sensor.device_id)
        if state and state.last_wet_time:
            return datetime.fromtimestamp(state.last_wet_time / 1000, tz=timezone.utc)
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


class GoveeLeakAlertStatusSensor(SensorEntity):
    """Sensor showing leak alert acknowledgment status."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["Pending", "Acknowledged"]
    _attr_icon = "mdi:bell-alert"
    _attr_translation_key = "leak_alert_status"

    def __init__(self, coordinator: GoveeCoordinator, sensor: GoveeLeakSensor) -> None:
        self._coordinator = coordinator
        self._sensor = sensor
        self._attr_unique_id = f"{sensor.device_id}_alert_status"

    @property
    def device_info(self) -> DeviceInfo:
        return leak_sensor_device_info(self._sensor, DOMAIN)

    @property
    def native_value(self) -> str | None:
        state = self._coordinator.leak_states.get(self._sensor.device_id)
        if state is None:
            return None
        return "Acknowledged" if state.read else "Pending"

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, f"{DOMAIN}_leak_update", self._handle_leak_update
            )
        )

    @callback
    def _handle_leak_update(self) -> None:
        self.async_write_ha_state()


class GoveeLeakDeviceAddressSensor(SensorEntity):
    """Diagnostic sensor exposing the leak sensor's IEEE EUI-64 address.

    HA's `serial_number` device field expects a manufacturer serial; the
    Govee cloud only knows the wireless address, which is not the same
    thing. Surfacing it here as a diagnostic entity keeps it visible
    without mislabeling it on the device card.
    """

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "ieee_address"
    _attr_icon = "mdi:identifier"

    def __init__(self, sensor: GoveeLeakSensor) -> None:
        self._sensor = sensor
        self._attr_unique_id = f"{sensor.device_id}_address"
        self._attr_native_value = sensor.device_id

    @property
    def device_info(self) -> DeviceInfo:
        return leak_sensor_device_info(self._sensor, DOMAIN)


class GoveeLeakHubAddressSensor(SensorEntity):
    """Diagnostic sensor exposing the hub's IEEE EUI-64 address."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "ieee_address"
    _attr_icon = "mdi:identifier"

    def __init__(self, hub_device_id: str) -> None:
        self._hub_device_id = hub_device_id
        self._attr_unique_id = f"{hub_device_id}_address"
        self._attr_native_value = hub_device_id

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={(DOMAIN, self._hub_device_id)})
