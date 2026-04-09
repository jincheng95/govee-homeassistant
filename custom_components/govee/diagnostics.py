"""Diagnostics support for Govee integration.

Provides debug information for troubleshooting without exposing sensitive data.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_API_KEY, CONF_EMAIL, CONF_PASSWORD
from .coordinator import GoveeCoordinator

# Keys to redact from diagnostic output
TO_REDACT = {
    CONF_API_KEY,
    CONF_EMAIL,
    CONF_PASSWORD,
    "token",
    "refresh_token",
    "iot_cert",
    "iot_key",
    "iot_ca",
    "client_id",
    "account_topic",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator: GoveeCoordinator = entry.runtime_data

    # Collect device information
    devices_info = {}
    for device_id, device in coordinator.devices.items():
        state = coordinator.get_state(device_id)
        devices_info[device_id] = {
            "sku": device.sku,
            "name": device.name,
            "device_type": device.device_type,
            "is_group": device.is_group,
            "capabilities": [
                {
                    "type": cap.type,
                    "instance": cap.instance,
                    "parameters": cap.parameters,
                }
                for cap in device.capabilities
            ],
            "state": {
                "online": state.online if state else None,
                "power_state": state.power_state if state else None,
                "brightness": state.brightness if state else None,
                "color": state.color.as_tuple if state and state.color else None,
                "color_temp_kelvin": state.color_temp_kelvin if state else None,
                "source": state.source if state else None,
            },
            "transport": {
                "cloud_api": True,
                "mqtt": coordinator.mqtt_connected,
                "ble": coordinator.is_ble_available(device_id),
            },
        }

    # Collect MQTT status
    mqtt_client = coordinator.mqtt_client
    mqtt_info = None
    if mqtt_client:
        mqtt_info = {
            "available": mqtt_client.available,
            "connected": mqtt_client.connected,
        }

    # Collect API client info
    api_info = {
        "rate_limit_remaining": coordinator.api_rate_limit_remaining,
        "rate_limit_total": coordinator.api_rate_limit_total,
        "rate_limit_reset": coordinator.api_rate_limit_reset,
    }

    # Build diagnostics data
    diagnostics_data = {
        "config_entry": {
            "entry_id": entry.entry_id,
            "version": entry.version,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "devices": devices_info,
        "device_count": len(coordinator.devices),
        "mqtt": mqtt_info,
        "api": api_info,
        "scene_cache_count": coordinator.scene_cache_count,
    }

    return diagnostics_data
