# Issue #62 — Temperature/humidity + leak sensor support

**Date**: 2026-05-01
**Type**: Feature implementation (partial) + open follow-up
**Issue**: [#62](https://github.com/lasswellt/govee-homeassistant/issues/62)
**Reporter**: steamer70 — wants H5054 (water detector) and H5109 (smart temperature sensor) support.

---

## Summary

Two devices, two different capability shapes. Implemented the well-documented half (temperature/humidity via the standard `devices.capabilities.property` capability — covers H5109, H5179, and any future SKU that exposes the same instances). Deferred the leak-detector half (H5054) pending diagnostics from the reporter — without ground truth on what the cloud API returns, the right binary-sensor instance name is a guess, and PR #56 is already negotiating the related but distinct hub-LoRa H5058 path.

## H5109 — implemented

Per `docs/govee-protocol-reference.md` §8.5 the canonical thermometer SKU is H5179, which exposes:

```json
{"capabilities": [
  {"type": "devices.capabilities.property", "instance": "sensorTemperature"},
  {"type": "devices.capabilities.property", "instance": "sensorHumidity"}
]}
```

H5109 is the "Smart Temperature Sensor" in the same family and is expected to expose the same capability shape. The integration now:

- Adds `INSTANCE_SENSOR_TEMPERATURE` / `INSTANCE_SENSOR_HUMIDITY` constants (`models/device.py`).
- Adds `DEVICE_TYPE_THERMOMETER` and `is_thermometer` for completeness, but routing is **capability-based, not SKU-based** — anything that exposes those property instances picks up the entities, regardless of `device_type`.
- Adds `supports_temperature_sensor` / `supports_humidity_sensor` on `GoveeDevice`.
- Adds `sensor_temperature` / `sensor_humidity` fields on `GoveeDeviceState` and parses them in `update_from_api`. The parser handles both shapes seen in the wild — `state.value` as a plain number, and the legacy `state.value.currentTemperature` / `state.value.currentHumidity` STRUCT.
- Adds `GoveeTemperatureSensor` / `GoveeHumiditySensor` entities in `sensor.py`, each gated on the matching `supports_*` predicate so they don't appear on light devices that happen to share the platform.
- Translation strings updated in `strings.json` and `translations/en.json`.
- Tests in `tests/test_thermometer.py` exercise both the H5109 and H5179 device shapes plus both API state-payload variants.

Because the gating is capability-based, this also covers any other Govee SKU that exposes the same instances — H5074, H5075, H5101, H5102, etc. — for free.

## H5054 — deferred

The H5054 is a stand-alone water sensor with a different lineage from H5058. PR #56 hardcodes `LEAK_SENSOR_SKUS = {"H5058", "H5054", "H5055"}` but routes everything through the H5043 hub's LoRa multiSync MQTT path — that's correct for H5058 but speculative for H5054, which (per Govee's product pages) does not advertise as a hub-paired sensor.

Without a diagnostics dump from the reporter we don't know:
- The actual `device_type` returned by `/device/list/v1`.
- Whether the leak signal is exposed as `devices.capabilities.event` (and under which instance name — `leakEvent`? `waterDetectionEvent`? something else) or as a `property`.
- Whether battery is exposed at all.

Action: comment on the issue asking for diagnostics, then add a generic event-driven leak binary sensor once we have a real payload. The temperature/humidity work shipping today is decoupled and doesn't block this follow-up.

## Tests

- `tests/test_thermometer.py` (8 tests) — capability detection, state parsing both shapes, missing-value safety, light device must not pick up sensors.

Full suite: 697 → 705 passing. Mypy clean. Flake8 clean.
