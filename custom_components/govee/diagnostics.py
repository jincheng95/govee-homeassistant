"""Diagnostics support for Govee integration.

Provides debug information for troubleshooting without exposing sensitive data.
"""

from __future__ import annotations

import dataclasses
import hashlib
import re
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_API_KEY, CONF_EMAIL, CONF_PASSWORD
from .coordinator import GoveeCoordinator

# Keys to redact from diagnostic output. Includes the raw-response identity
# fields ("device" = MAC in /device/state + device-list, "deviceName" = the
# user's chosen name) so the captured raw API/MQTT payloads stay PII-free.
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
    "device_id",
    "device",
    "deviceName",
    "mac",
}


def _serialize_state(state: Any) -> dict[str, Any] | None:
    """Full dump of a GoveeDeviceState (all fields, incl. sensor readings).

    Never raises — diagnostics must always produce output.
    """
    if state is None:
        return None
    try:
        return dataclasses.asdict(state)
    except Exception:  # pragma: no cover - defensive
        return {
            "online": getattr(state, "online", None),
            "power_state": getattr(state, "power_state", None),
            "source": getattr(state, "source", None),
        }


# Govee device IDs are MAC-derived: 8 colon-separated hex octets
# (e.g., "03:9C:DC:06:75:4B:10:7C"). Group device IDs are numeric-only.
_MAC_PATTERN = re.compile(r"^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5,7}$")


def _looks_like_mac(value: str) -> bool:
    """Return True if value matches the Govee MAC-derived device-id format."""
    return bool(_MAC_PATTERN.match(value))


def _anonymize_device_id(value: str) -> str:
    """Replace a MAC-derived id with a stable short hash (PII redaction)."""
    return f"device_{hashlib.sha256(value.encode()).hexdigest()[:8]}"


def _anonymize_device_keys(data: dict[str, Any]) -> dict[str, Any]:
    """Replace MAC-format dict keys with stable hashes; leave other keys intact."""
    return {
        (_anonymize_device_id(k) if isinstance(k, str) and _looks_like_mac(k) else k): v
        for k, v in data.items()
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator: GoveeCoordinator = entry.runtime_data

    mqtt_client = coordinator.mqtt_client
    raw_state = coordinator.api_client.last_raw_state
    raw_mqtt = mqtt_client.last_messages if mqtt_client else {}

    # Collect device information. Each device carries:
    #  - parsed state (ALL fields, including sensor_temperature/humidity)
    #  - raw_api_state: the verbatim /device/state payload it was parsed from
    #  - last_mqtt_message: the verbatim AWS IoT push it last received
    # These let us debug state-shape issues (e.g. thermometers #83) directly
    # from a diagnostics download instead of asking for a debug log.
    devices_info: dict[str, Any] = {}
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
            "state": _serialize_state(state),
            "raw_api_state": raw_state.get(device_id),
            "last_mqtt_message": raw_mqtt.get(device_id),
            "transport": {
                "cloud_api": True,
                "mqtt": coordinator.mqtt_connected,
                "ble": coordinator.is_ble_available(device_id),
            },
        }

    # Collect MQTT status
    mqtt_info = None
    if mqtt_client:
        mqtt_info = {
            "available": mqtt_client.available,
            "connected": mqtt_client.connected,
            "tracked_devices": len(raw_mqtt),
        }

    # Collect API client info
    api_info = {
        "rate_limit_remaining": coordinator.api_rate_limit_remaining,
        "rate_limit_total": coordinator.api_rate_limit_total,
        "rate_limit_reset": coordinator.api_rate_limit_reset,
    }

    diagnostics_data = {
        "config_entry": {
            "entry_id": entry.entry_id,
            "version": entry.version,
            "data": dict(entry.data),
            "options": dict(entry.options),
        },
        "devices": devices_info,
        # Verbatim device-list response from the most recent discovery poll.
        "raw_api_devices": coordinator.api_client.last_raw_devices,
        "device_count": len(coordinator.devices),
        "mqtt": mqtt_info,
        "api": api_info,
        "scene_cache_count": coordinator.scene_cache_count,
    }

    # Redact known-sensitive keys anywhere in the tree (covers the raw API/MQTT
    # payloads' "device"/"deviceName" fields), then hash the MAC-format device
    # map keys (MAC = PII per HA diagnostics guidance).
    redacted: dict[str, Any] = async_redact_data(diagnostics_data, TO_REDACT)
    redacted["devices"] = _anonymize_device_keys(redacted["devices"])
    return redacted
