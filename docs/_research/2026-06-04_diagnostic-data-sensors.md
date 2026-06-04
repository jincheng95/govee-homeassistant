<!-- no-registry: feature investigation; no quantified file/component scope language -->

# Research: Diagnostic-Data Sensors ("All Data Last Updated", "Energy Last Updated", "Online", "Websocket Last Updated")

**Date:** 2026-06-04
**Type:** Feature Investigation
**Stack:** Home Assistant custom component, Python 3.12+, `DataUpdateCoordinator`, cloud_push (MQTT + polling), quality_scale=silver

## Summary

Goal: surface device "Diagnostic" panel rows like `All Data Last Updated: 2 hours ago`, `Energy Last Updated: 1 minute ago`, `Online: Connected`, `Websocket Last Updated: 3 minutes ago`. The "X ago" rendering is HA-native: any `SensorDeviceClass.TIMESTAMP` sensor returning a tz-aware `datetime` renders relative client-side. Of the 4 requested rows, **1 already exists** (Websocket → `GoveeMqttLastReceivedSensor`), **1 is partial** (Online → `GoveeMqttStatusSensor`/`GoveeTransportConnectivity`), **1 is missing-but-trivial** (All Data Last Updated → switch coordinator base to `TimestampDataUpdateCoordinator` + 1 sensor class), and **1 is infeasible** (Energy → Govee Cloud API v2.0 exposes no energy/kWh capability). Recommendation: add the "All Data Last Updated" sensor, optionally relabel the MQTT sensor to "Websocket", skip "Energy". Heed the contrarian recorder-churn caveat — TIMESTAMP diagnostic sensors only re-write state when the underlying timestamp changes, so set no `state_class` and keep them `EntityCategory.DIAGNOSTIC`.

## Research Questions

1. **How does HA render "X ago"?** `SensorDeviceClass.TIMESTAMP` + `native_value` returning a timezone-aware `datetime`. Frontend renders relative; no custom code needed. (library-docs)
2. **Which requested rows already exist?** Websocket Last Updated = `GoveeMqttLastReceivedSensor` (sensor.py:186). Online = partial via `GoveeMqttStatusSensor` (sensor.py:144) + `GoveeTransportConnectivity` (binary_sensor.py:133). (codebase-analyst)
3. **How to source "All Data Last Updated"?** `GoveeCoordinator` uses the plain `DataUpdateCoordinator` (no last-poll timestamp). HA's `TimestampDataUpdateCoordinator` adds `last_update_success_time: datetime | None`, auto-stamped on each successful refresh. Switch the base class. (library-docs + codebase-analyst)
4. **Is Energy data available?** No. Zero hits for `energy`/`watt`/`kwh`/`electricity` across `custom_components/govee/`; Govee Cloud API v2.0 parses no energy capability. Infeasible without a new endpoint. (codebase-analyst)
5. **Hub-level vs per-device diagnostic entities?** Hub: no `device_info`, `unique_id` includes `entry_id`. Per-device: `device_info`, `has_entity_name=True`, `unique_id` includes `device_id`. (library-docs)
6. **Is exposing these as sensors a good idea?** Contested — see Dissent. Net: acceptable for this codebase (pattern already established) provided no `state_class` and diagnostic category. (web-researcher contra; codebase-analyst pro)

## Findings

### F1 — "X ago" is automatic for TIMESTAMP sensors
`SensorDeviceClass.TIMESTAMP` requires `native_value` to return a **timezone-aware** `datetime`. HA frontend renders the relative "2 hours ago" string. Naive datetimes raise "Invalid datetime" log spam — always use `homeassistant.util.dt.utcnow()` (`dt_util.utcnow()`), never `datetime.now()`. (library-docs; web-researcher #6: uptime issues #169687/#169743 traced to naive datetime)

### F2 — Existing diagnostic surface is substantial
Hub-level (`{DOMAIN, "hub"}`, config-entry scope), sensor.py:
- `GoveeRateLimitSensor` (sensor.py:97) — MEASUREMENT, DIAGNOSTIC
- `GoveeMqttStatusSensor` (sensor.py:144) — ENUM `connected/disconnected/unavailable`, DIAGNOSTIC
- `GoveeMqttLastReceivedSensor` (sensor.py:186) — **TIMESTAMP**, DIAGNOSTIC → sources `coordinator.mqtt_last_message_ts`

binary_sensor.py:
- `GoveeTransportConnectivity` (binary_sensor.py:133) — CONNECTIVITY, DIAGNOSTIC, per `(device, cloud_api|mqtt|ble)`, opt-in; `.available` consumes `coordinator.last_update_success`
- `GoveeLeakOnlineSensor` (binary_sensor.py:232), `GoveeLeakHubOnlineSensor` (binary_sensor.py:267) — CONNECTIVITY, DIAGNOSTIC

This codebase already ships multiple TIMESTAMP + CONNECTIVITY diagnostic entities → the pattern is an established project convention.

### F3 — Mapping of requested rows

| Requested row | Status | Detail |
|---|---|---|
| `Websocket Last Updated: 3 minutes ago` | **EXISTS** | `GoveeMqttLastReceivedSensor` (sensor.py:186); label is "Last MQTT Received" — rename-only if "Websocket" wording required |
| `Online: Connected` | **PARTIAL** | `GoveeMqttStatusSensor` covers MQTT-online; `GoveeTransportConnectivity` covers per-transport. No dedicated hub REST-online binary_sensor |
| `All Data Last Updated: 2 hours ago` | **MISSING (trivial)** | No hub-level last-poll timestamp. Needs `TimestampDataUpdateCoordinator` base + 1 sensor class |
| `Energy Last Updated: 1 minute ago` | **MISSING (infeasible)** | No energy capability in codebase or Govee API v2.0 |

### F4 — "All Data Last Updated" data source
`GoveeCoordinator(DataUpdateCoordinator[...])` (coordinator.py:109) exposes only `last_update_success: bool`. `_async_update_data` (coordinator.py:901) counts `successful_updates` but stamps no datetime. `mqtt_last_message_ts` covers MQTT pushes only — not REST polls.

HA's `TimestampDataUpdateCoordinator` (helpers/update_coordinator.py:517) subclasses the base and auto-populates `last_update_success_time: datetime | None` in `_async_refresh_finished` whenever `last_update_success` is True. Switching the base class is a zero-logic change.

### F5 — Energy is not available
No `energy`/`watt`/`kwh`/`electricity` references anywhere in `custom_components/govee/`. Govee Cloud API v2.0 parses no energy-monitoring capability. An "Energy Last Updated" timestamp would have no underlying data to stamp. (codebase-analyst; web-researcher #5 flags synthesized energy metadata as misleading)

## Compatibility Analysis

- **Coordinator base swap:** `TimestampDataUpdateCoordinator` is API-compatible with `DataUpdateCoordinator` (it is a direct subclass). No behavior change beyond gaining `last_update_success_time`. Low risk. Import from `homeassistant.helpers.update_coordinator`.
- **New TIMESTAMP sensor:** mirrors existing `GoveeMqttLastReceivedSensor` exactly — same base classes (`CoordinatorEntity["GoveeCoordinator"]`, `SensorEntity`), same device_class, same hub `device_info`. No new dependency.
- **Translations:** `tests/test_translations.py` auto-fails if a new `translation_key` lacks an entry in `translations/en.json`. Must add to both `strings.json` and `translations/en.json`.
- **Recorder:** TIMESTAMP value changes only when the poll succeeds (≈ every poll interval, 30–600 s). Bounded churn. Do **not** set `state_class` on a datetime sensor (semantics break — web-researcher #4).

## Recommendation

**Implement "All Data Last Updated"; relabel MQTT sensor optionally; skip Energy.**

| Row | Action |
|---|---|
| All Data Last Updated | ADD — switch coordinator base + new `GoveeAllDataLastUpdatedSensor` |
| Websocket Last Updated | RELABEL only (optional) — `GoveeMqttLastReceivedSensor` translation_key/name "Websocket"/"MQTT" wording |
| Online: Connected | KEEP existing — already covered by `GoveeMqttStatusSensor`; add hub REST-online binary_sensor only if a distinct "REST reachable" signal is wanted |
| Energy Last Updated | SKIP — no data source; do not synthesize |

## Implementation Sketch

1. **Coordinator base** — coordinator.py import block (~:21) + class def (coordinator.py:109):
   ```python
   from homeassistant.helpers.update_coordinator import (
       DataUpdateCoordinator,
       TimestampDataUpdateCoordinator,  # add
   )
   ...
   class GoveeCoordinator(TimestampDataUpdateCoordinator[dict[str, GoveeDeviceState]]):
   ```
   Gains `coordinator.last_update_success_time`. Zero logic change.

2. **New sensor** — sensor.py, copy `GoveeMqttLastReceivedSensor` (sensor.py:186–221) as template:
   ```python
   class GoveeAllDataLastUpdatedSensor(CoordinatorEntity["GoveeCoordinator"], SensorEntity):
       _attr_has_entity_name = True
       _attr_translation_key = "all_data_last_updated"
       _attr_entity_category = EntityCategory.DIAGNOSTIC
       _attr_device_class = SensorDeviceClass.TIMESTAMP
       _attr_icon = "mdi:database-clock"
       # __init__: _attr_unique_id = f"{entry_id}_all_data_last_updated"; hub device_info
       @property
       def native_value(self) -> datetime | None:
           return self.coordinator.last_update_success_time
   ```

3. **Register** — sensor.py `async_setup_entry` (~:56), unconditional (not gated on `mqtt_client`):
   ```python
   entities: list[SensorEntity] = [
       GoveeRateLimitSensor(coordinator, entry.entry_id),
       GoveeAllDataLastUpdatedSensor(coordinator, entry.entry_id),
   ]
   ```

4. **Translations** — add under `entity.sensor` in BOTH `strings.json` (~:171, after `mqtt_last_received`) and `translations/en.json`:
   ```json
   "all_data_last_updated": { "name": "All Data Last Updated" }
   ```

5. **Tests** — add to `tests/test_coordinator.py` (or new `tests/test_sensor.py`): assert `sensor.<...>_all_data_last_updated` is `STATE_UNKNOWN` before first poll, then a UTC isoformat after a successful refresh. Updating `strings.json`+`translations/en.json` keeps `tests/test_translations.py` green.

## Risks

- **Naive-datetime regression:** A TIMESTAMP sensor returning a non-tz-aware datetime produces "Invalid datetime" log spam and `unknown` states. `TimestampDataUpdateCoordinator` already stamps with tz-aware UTC, so sourcing `last_update_success_time` directly avoids this. Do not reconstruct the datetime manually.
- **Recorder noise:** Diagnostic timestamp sensors re-write state every successful poll. With a 30 s poll interval this is meaningful long-term DB growth. Mitigation: keep `EntityCategory.DIAGNOSTIC`, set no `state_class`, and consider documenting a recorder `exclude` for users who care. This is the core of the web-researcher dissent below — treat it as a known trade-off, not a blocker, given the codebase already ships such sensors.
- **Energy expectation gap:** Users seeing the reference UI may expect an "Energy Last Updated" row. It cannot be delivered truthfully — Govee API v2.0 exposes no energy data. Synthesizing a timestamp from unrelated data would be misleading. Document the omission rather than fake it.
- **Naming ambiguity:** "Websocket" vs "MQTT" — Govee real-time transport is AWS IoT MQTT, not a raw websocket. Relabeling to "Websocket" trades technical accuracy for matching the reference UI wording. Decide deliberately.

## Dissent / Contradictory Evidence

- **web-researcher (contrarian):** HA core integrations generally do **not** expose "last_updated" timestamp sensors as entities — that data conventionally lives in `async_get_config_entry_diagnostics()` dicts. Argues separate "Online" sensors are redundant vs the entity `available` property, and that timestamp sensors add recorder churn and flapping risk. Recommends migrating to a diagnostics dict + availability properties rather than adding entities.
- **codebase-analyst (counter):** This integration already ships `GoveeMqttLastReceivedSensor`, `GoveeSensorReadingTimestampSensor`, `GoveeLeakLastWetSensor` (TIMESTAMP) and multiple CONNECTIVITY binary sensors. The pattern is an established project convention, so adding one more consistent diagnostic sensor is low-friction and matches user/UI expectations.
- **Resolution:** Proceed with the entity approach (consistency wins within this codebase), but adopt the web-researcher mitigations: no `state_class`, diagnostic category, tz-aware datetime, and a documented recorder-exclude note.

## Open Questions

- Should "Online" gain a dedicated hub-level REST-reachability binary_sensor distinct from MQTT status, or is `GoveeMqttStatusSensor` + `GoveeTransportConnectivity` sufficient? Depends on whether users need to distinguish "REST polling works but MQTT down".
- Is matching the reference UI's "Websocket"/"Energy" wording a hard requirement, or is technical accuracy ("MQTT", omit Energy) acceptable?

## References

- https://developers.home-assistant.io/docs/core/entity/sensor — SensorEntity, `SensorDeviceClass.TIMESTAMP`, datetime handling, state_class
- https://developers.home-assistant.io/docs/core/entity — `EntityCategory.DIAGNOSTIC`, device_info, has_entity_name
- https://developers.home-assistant.io/docs/core/entity/binary-sensor — `BinarySensorDeviceClass.CONNECTIVITY` semantics
- https://developers.home-assistant.io/docs/entity_index/ — entity registry guidance, `available` as connectivity mechanism
- https://github.com/home-assistant/core/blob/dev/homeassistant/helpers/update_coordinator.py — `DataUpdateCoordinator`, `TimestampDataUpdateCoordinator`, `last_update_success_time`
- https://github.com/home-assistant/core/blob/dev/homeassistant/components/sensor/__init__.py — TIMESTAMP rendering, datetime conversion
- https://www.home-assistant.io/integrations/mqtt/#sensor — `last_reported`/`force_update` stability note
- https://github.com/home-assistant/core/blob/dev/homeassistant/components/mqtt/sensor.py — `dt_util.parse_datetime()` datetime handling
- GitHub issues #169687, #169743 — uptime sensor naive-datetime validation failures

### Codebase refs
- `custom_components/govee/coordinator.py:109` — `GoveeCoordinator` base class
- `custom_components/govee/coordinator.py:901` — `_async_update_data`
- `custom_components/govee/sensor.py:186` — `GoveeMqttLastReceivedSensor` (TIMESTAMP template)
- `custom_components/govee/sensor.py:144` — `GoveeMqttStatusSensor`
- `custom_components/govee/binary_sensor.py:133` — `GoveeTransportConnectivity`
- `custom_components/govee/strings.json:171` — `mqtt_last_received` translation anchor
