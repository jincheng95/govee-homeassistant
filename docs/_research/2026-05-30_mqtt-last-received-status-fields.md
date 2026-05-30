<!-- no-registry: single diagnostic-sensor feature, not a countable migration scope -->

# Research: "Last MQTT Received" status-update fields — ha_carrier pattern → Govee integration

**Date:** 2026-05-30
**Type:** Feature Investigation
**Topic slug:** mqtt-last-received-status-fields
**Context:** Adopt `dahlb/ha_carrier`'s freshness-diagnostic pattern (timestamp sensors showing when push/poll data last arrived) into this Govee integration.

---

## Summary

`ha_carrier` exposes data freshness via 3 hub/system-level `SensorDeviceClass.TIMESTAMP` + `EntityCategory.DIAGNOSTIC` sensors ("All Data / Websocket / Energy Last Updated"), each backed by a `datetime | None` coordinator field stamped `datetime.now(UTC)` at its success point (full poll, websocket push, energy refresh), plus a `BinarySensorDeviceClass.CONNECTIVITY` `OnlineSensor`. This Govee integration already has the connectivity half (`GoveeTransportConnectivity` binary sensor, per-device `last_success_ts`) and a `GoveeMqttStatusSensor` ENUM (connected/disconnected) — but **no hub-level timestamp showing when the last MQTT push actually arrived**. Recommend adding a single `GoveeMqttLastReceivedSensor` (TIMESTAMP, diagnostic) backed by a new `_last_message_ts` field on `GoveeAwsIotClient`, stamped in `_handle_message`. Optionally add a parallel "Last Poll" timestamp for the REST path (ha_carrier's "All Data" equivalent), which HA's `DataUpdateCoordinator` does not track natively.

---

## Research Questions

1. **How does ha_carrier surface "last update" freshness?**
   3 `TimestampSensor` instances per system (`TIMESTAMP_TYPES = ("all_data", "websocket", "energy")`), `SensorDeviceClass.TIMESTAMP`, `EntityCategory.DIAGNOSTIC`, no icon, no translation_key. `native_value = getattr(coordinator, f"timestamp_{type}")`; `available = value is not None`. Source: `custom_components/ha_carrier/sensor.py` ~638-672.

2. **Where are the timestamps stamped?**
   Coordinator fields `timestamp_all_data` / `timestamp_websocket` / `timestamp_energy` (`datetime | None`), all `datetime.now(UTC)`. Stamped in `_async_full_refresh` (poll), `updated_callback` (websocket push), `_async_energy_refresh`. `carrier_data_update_coordinator.py`.

3. **Connectivity exposure?**
   `OnlineSensor` (`binary_sensor.py`): `BinarySensorDeviceClass.CONNECTIVITY` + `EntityCategory.DIAGNOSTIC`, `is_on = not system.status.is_disconnected`, dynamic icon `mdi:wifi-check` / `mdi:wifi-strength-outline`.

4. **What does Govee already have vs. what's missing?**
   Have: `GoveeMqttStatusSensor` (ENUM connected/disconnected/unavailable), `GoveeTransportConnectivity` (CONNECTIVITY binary, per-device, exposes mqtt `last_success_ts` as ISO attribute), `GoveeSensorReadingTimestampSensor` (per-thermometer reading freshness). Missing: hub-level timestamp of last inbound MQTT message; no per-message arrival timestamp tracked anywhere (`api/mqtt.py` `_last_messages` stores raw state dict only, no `datetime`).

5. **Is `transport.py` `last_success_ts` reusable for a hub-level "last MQTT received"?**
   No. `TransportHealth.last_success_ts` (transport="mqtt") is **per-device**, stamped by `coordinator._record_transport_success(device_id, "mqtt")` (`coordinator.py:834`); `refresh_mqtt_for_devices` deliberately does NOT update it. New hub-level `_last_message_ts` on `GoveeAwsIotClient` is the correct approach. (Alternate: `max(last_success_ts)` across mqtt transports — no new state, but cold-start gap before first per-device push.)

---

## Findings

### ha_carrier freshness pattern (verbatim)

`custom_components/ha_carrier/sensor.py`:
```python
class TimestampSensor(CarrierSensor):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator, system_serial, timestamp_type):
        self.timestamp_type = timestamp_type
        super().__init__(
            entity_name=f"{timestamp_type.replace('_', ' ').title()} Last Updated",
            coordinator=coordinator, system_serial=system_serial,
        )

    def _update_entity_attrs(self) -> None:
        self._attr_native_value = getattr(self.coordinator, f"timestamp_{self.timestamp_type}")
        self._attr_available = self._attr_native_value is not None
```

Coordinator (`carrier_data_update_coordinator.py`):
```python
self.timestamp_all_data: datetime | None = None
self.timestamp_websocket: datetime | None = None
self.timestamp_energy: datetime | None = None
# websocket push:
async def updated_callback(self, _message: str) -> None:
    self.timestamp_websocket = datetime.now(UTC)
    ...
    self.async_update_listeners()
```

Key design points: timezone-aware UTC; `None` until first event (sensor reports `available=False`); 3 distinct types so users see which transport is stale.

### Govee current state

`GoveeMqttStatusSensor` (`sensor.py:143-183`) — hub-level diagnostic, the exact pattern to clone:
```python
class GoveeMqttStatusSensor(CoordinatorEntity["GoveeCoordinator"], SensorEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "mqtt_status"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["connected", "disconnected", "unavailable"]
    _attr_icon = "mdi:cloud-sync"

    def __init__(self, coordinator, entry_id):
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_mqtt_status"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={(DOMAIN, "hub")}, name="Govee Integration",
                          manufacturer="Govee", model="Cloud API")
```

`api/mqtt.py` — `_last_messages: dict[str, dict]` retains last raw payload per device but stamps **no time**. `_handle_message` (line ~413) writes `self._last_messages[device_id] = state` — the insertion point for a timestamp stamp.

Connectivity already covered: `GoveeTransportConnectivity` (`binary_sensor.py:133`, `BinarySensorDeviceClass.CONNECTIVITY`) + `_TRANSPORT_SPECS` loop already mirrors ha_carrier's `OnlineSensor`. No new binary sensor needed.

### Convergence / Dissent

No contradictions between agents. ha_carrier uses literal entity names (no translation_key); this repo uses `translation_key` + `strings.json`/`translations/en.json` mirrors — follow the **repo's** convention (translation_key), not ha_carrier's.

---

## Compatibility Analysis

- **Stack:** Home Assistant custom component, Python 3.12+. `SensorDeviceClass.TIMESTAMP` requires `native_value` to be a tz-aware `datetime` — HA renders it as relative "X minutes ago". Matches existing `GoveeSensorReadingTimestampSensor` (already TIMESTAMP, tz-aware UTC) — pattern proven in this repo.
- **No new deps.** `datetime`/`timezone` already imported in `sensor.py`; `api/mqtt.py` imports `time` and needs `from datetime import datetime, timezone` added.
- **Integration complexity:** Low. Additive only — new field, new property, new sensor, 2 translation keys. No change to existing entity behavior. Hub `DeviceInfo` already established.
- **Cold start:** sensor reports `None`/unavailable until first MQTT push — consistent with ha_carrier semantics and acceptable.
- **MQTT-optional:** register only inside the existing `if coordinator.mqtt_client is not None:` block so polling-only installs don't get a permanently-`None` sensor.

---

## Recommendation

Add **`GoveeMqttLastReceivedSensor`** (hub-level, `SensorDeviceClass.TIMESTAMP`, `EntityCategory.DIAGNOSTIC`) backed by a new `_last_message_ts` on `GoveeAwsIotClient`. This is the direct Govee analogue of ha_carrier's "Websocket Last Updated" and is the user's primary ask ("last mqtt received").

| Option | New state | Hub-level | Cold-start gap | Verdict |
|---|---|---|---|---|
| New `_last_message_ts` on MQTT client | yes (1 field) | yes | none after first msg | **Recommended** |
| Reuse `max(transport mqtt last_success_ts)` | no | derived | per-device gap; misses non-device msgs | Fallback only |
| Per-device timestamp sensors | yes (N) | no | none | Entity bloat; rejected |

**Optional follow-on (ha_carrier "All Data Last Updated" equivalent):** add `GoveePollLastUpdatedSensor` stamped at end of `_async_update_data` — HA's `DataUpdateCoordinator` does not natively expose a last-success timestamp. Defer unless users want REST-poll freshness visibility too.

Connectivity binary already exists (`GoveeTransportConnectivity`) — no work needed.

---

## Implementation Sketch

1. **`custom_components/govee/api/mqtt.py`**
   - Add `from datetime import datetime, timezone`.
   - `__init__` (~line 124): `self._last_message_ts: datetime | None = None`.
   - `_handle_message` (~line 413) after `self._last_messages[device_id] = state`:
     ```python
     self._last_message_ts = datetime.now(timezone.utc)
     ```
   - Add property after `last_messages`:
     ```python
     @property
     def last_message_ts(self) -> datetime | None:
         """UTC timestamp of the most recent inbound MQTT state message."""
         return self._last_message_ts
     ```
   - Consider stamping in `_handle_multisync` too (~lines 501/507) if multiSync should count as activity.

2. **`custom_components/govee/coordinator.py`** — after `mqtt_connected` (~line 270):
   ```python
   @property
   def mqtt_last_message_ts(self) -> datetime | None:
       """UTC timestamp of the last inbound MQTT state message, or None."""
       if self._mqtt_client is None:
           return None
       return self._mqtt_client.last_message_ts
   ```

3. **`custom_components/govee/sensor.py`** — add after `GoveeMqttStatusSensor` (~line 184):
   ```python
   class GoveeMqttLastReceivedSensor(CoordinatorEntity["GoveeCoordinator"], SensorEntity):
       """Timestamp of the last inbound MQTT state message (hub-level diagnostic)."""

       _attr_has_entity_name = True
       _attr_translation_key = "mqtt_last_received"
       _attr_entity_category = EntityCategory.DIAGNOSTIC
       _attr_device_class = SensorDeviceClass.TIMESTAMP
       _attr_icon = "mdi:cloud-sync-outline"

       def __init__(self, coordinator: GoveeCoordinator, entry_id: str) -> None:
           super().__init__(coordinator)
           self._attr_unique_id = f"{entry_id}_mqtt_last_received"

       @property
       def device_info(self) -> DeviceInfo:
           return DeviceInfo(identifiers={(DOMAIN, "hub")}, name="Govee Integration",
                             manufacturer="Govee", model="Cloud API")

       @property
       def native_value(self) -> datetime | None:
           return self.coordinator.mqtt_last_message_ts
   ```
   Register in `async_setup_entry` (~line 61), same `if coordinator.mqtt_client is not None:` block as `GoveeMqttStatusSensor`.

4. **Translations** — add under `entity.sensor` in BOTH `strings.json` and `translations/en.json` (identical mirrors), after `mqtt_status`:
   ```json
   "mqtt_last_received": { "name": "Last MQTT Received" },
   ```

5. **Tests** — match `tests/test_coordinator.py::TestSensorReadingChangeTracking` style (`object.__new__(GoveeCoordinator)` + direct attribute patching). Add `TestMqttLastMessageTracking`:
   - `coord._mqtt_client = None` → `mqtt_last_message_ts is None`.
   - `coord._mqtt_client = Mock(last_message_ts=ts)` → returns `ts`.
   - MQTT client: `_handle_message` stamps `last_message_ts` (mock pattern per `tests/test_diagnostics.py:187`).

---

## Risks

- **Multi-path activity definition.** If only `_handle_message` is stamped, a stream of multiSync-only messages would leave "Last MQTT Received" looking stale even though the connection is live. Decide explicitly whether multiSync counts; if yes, stamp both handlers. This is a semantics choice the implementer must make consciously, not silently.
- **Cold start / polling-only installs.** Sensor stays `None` until the first push. Gating registration on `coordinator.mqtt_client is not None` avoids a permanently-unavailable entity on polling-only setups. Acceptable and matches ha_carrier.
- **Reusing per-device `last_success_ts` is a trap.** It is per-device and not updated by `refresh_mqtt_for_devices`; using it for a hub-level sensor would under-report. The new dedicated field avoids this.
- **Timezone correctness.** `native_value` MUST be tz-aware (`datetime.now(timezone.utc)`); a naive datetime raises in HA's TIMESTAMP sensor. The existing `GoveeSensorReadingTimestampSensor` already follows this — mirror it.

## Open Questions

- Should a companion "Last Poll" (REST) timestamp ship in the same change for symmetry with ha_carrier's "All Data Last Updated", or be deferred? Recommendation defers it; confirm with user.
- Does multiSync (`_handle_multisync`) count as MQTT activity for freshness purposes? Needs a decision before implementation.

---

## References

- ha_carrier repo: https://github.com/dahlb/ha_carrier
- ha_carrier `sensor.py` (TimestampSensor ~638-672): https://raw.githubusercontent.com/dahlb/ha_carrier/main/custom_components/ha_carrier/sensor.py
- ha_carrier `binary_sensor.py` (OnlineSensor): https://raw.githubusercontent.com/dahlb/ha_carrier/main/custom_components/ha_carrier/binary_sensor.py
- ha_carrier `carrier_data_update_coordinator.py`: https://raw.githubusercontent.com/dahlb/ha_carrier/main/custom_components/ha_carrier/carrier_data_update_coordinator.py
- HA `SensorDeviceClass.TIMESTAMP` docs: https://developers.home-assistant.io/docs/core/entity/sensor/#available-device-classes
- This repo: `custom_components/govee/sensor.py:143` (`GoveeMqttStatusSensor`), `:258` (`GoveeSensorReadingTimestampSensor`)
- This repo: `custom_components/govee/api/mqtt.py` (`_last_messages`, `_handle_message` ~413)
- This repo: `custom_components/govee/binary_sensor.py:133` (`GoveeTransportConnectivity`), `models/transport.py` (`TransportHealth`)
