<!-- no-registry: single-device root-cause follow-up; no multi-item quantified scope -->
# Research: H5310 surfaces but temp/humidity read "unknown" (issue #97)

**Date:** 2026-06-07
**Type:** Bug analysis (follow-up to `2026-06-06_h5310-diagnostic-rootcause.md`)
**Issue:** https://github.com/lasswellt/govee-homeassistant/issues/97
**Input:** reporter diagnostics on **v2026.6.11** (account login configured, H5310 via H5044 gateway)
**Status:** Root cause found — two independent defects (availability gating + humidity sentinel)

## Summary

The v2026.6.10 SKU fix worked: on v2026.6.11 the H5310 is **discovered** and an HA device with temperature + humidity sensors exists. But both entities show "unknown"/unavailable after 30 min. Diagnostics give two decisive, independent causes:

1. **`online: false` makes every entity unavailable** even though a valid `sensor_temperature: 26.4` is present and fresh (`cloud_api.last_success` 03:22:35). `GoveeEntity.available` (`entity.py:82`) returns `state.online`; for gateway-bridged **battery** sensors `online` flaps false between infrequent uploads, so a perfectly good reading renders as unavailable. This is the dominant symptom (temperature is valid yet hidden).
2. **Humidity is a sentinel, not a reading.** `sensor_humidity: 655.35` == `0xFFFF / 100` (65535 centi). The H5310 is a **pool/water thermometer with no humidity sensor**; Govee reports `hum` as the u16 sentinel `0xFFFF`. `_bff_reading` (`auth.py:181`) de-scales it to 655.35 instead of treating it as "no value" → a humidity entity exists with an impossible value (and would show 655.35, or unknown once availability is also fixed).

Neither is a discovery regression — the #86/#88 BFF path is working. Both are post-discovery data-handling defects.

## Evidence (from diagnostics `data.devices[H5310].state`)

```json
{ "online": false,
  "sensor_temperature": 26.4,
  "sensor_humidity": 655.35,
  "source": "api" }
```
- `transport_health.cloud_api`: `is_available: true`, `last_success: 2026-06-07T03:22:35Z` → the BFF poll is succeeding and delivering a fresh temp.
- `bff_device_census`: `{ "sku":"H5310", "in_thermo_hygro_skus": true, "gateway_sku":"H5044" }` → correctly matched (the #86 fix landed).
- `655.35 * 100 = 65535 = 0xFFFF` → unsigned-16 "no reading" sentinel.
- `26.4` = `2640 / 100` → valid centi-°C (≈79.5 °F, plausible pool temp).

## Research Questions

**Q1: Why does the valid 26.4 °C temperature show as unknown?**
Entity availability. `GoveeEntity.available` → `state is not None and state.online` (`entity.py:82`). `state.online` is set from the BFF `lastDeviceData.online` (`coordinator.py:767,810` ← `auth.py:673 ld.get("online", True)`), which is `false` here. HA renders an unavailable entity, masking the reading. Sensors don't override `available`, so both temp and humidity inherit the gate.

**Q2: Why is `online` false when the device is clearly reporting?**
For gateway-bridged battery sensors, `online` reflects the sub-device→gateway link at last sync, not data validity. These sensors sleep and upload infrequently (the codebase already notes this — `coordinator.py:811` "battery WiFi sensors upload infrequently"), so `online` flaps false while the *last reading remains valid and fresh*. Same dual-state nature as leak sensors, which track `online` (sensor→gateway) **and** `gateway_online` separately (`models/device.py:909-910`). The cloud `online` flag is also known to be sticky/unreliable (see `2026-05-01_issue-68-online-recovery.md`).

**Q3: Is 655.35 ever a real humidity?**
No. Humidity is 0–100 %RH; 655.35 is physically impossible and decodes exactly to `0xFFFF` centi. It is Govee's "sensor absent / no value" sentinel. The H5310 (pool thermometer) has no hygrometer.

**Q4: Does the H5310 even have humidity?**
No. The synthetic device unconditionally adds **both** `sensorTemperature` and `sensorHumidity` capabilities (`device.py:869-879`), so a humidity entity is always created for every `THERMO_HYGRO_BFF_SKUS` member regardless of whether the SKU has a hygrometer.

**Q5: Could the temperature also be a sentinel sometimes?**
Yes, defensively. When the pool probe has no fresh sample Govee may report `tem` as `0xFFFF` too. `_bff_reading` would de-scale that to 655.35 °C. Sentinel filtering should cover `tem` as well as `hum`.

## Findings

### F1 — Availability must not gate on `online` for BFF thermo-hygrometers (dominant bug)
`entity.py:82` returns `state.online`. For battery/gateway-bridged sensors `online` is an unreliable liveness flag that flaps false between uploads, hiding valid fresh readings. The temperature is present and recent yet unavailable. This is the primary reason the user sees "unknown."

**Fix direction:** for these sensors, decouple entity availability from the device's self-reported `online`. Options (in preference order):
- (a) Treat a BFF thermometer as available whenever the coordinator's last update succeeded **and** a reading is present (ignore `online` for availability); surface `online` instead as the existing per-device connectivity binary_sensor / `last_received` freshness sensor (already built — `sensor.py` reading-freshness, `2026-05-29_thermometer-freshness.md`).
- (b) Apply the issue-68 spirit: any fresh reading is proof-of-life → set `online=True` on a successful BFF poll that returns a value. Simpler but conflates "reachable" with "has data."

Recommendation: (a) — keep `online` honest as a diagnostic, but don't let it suppress real measurements. Implement by overriding `available` on the temperature/humidity sensor entities (or special-casing `device_id in coordinator._bff_thermometer_ids`) to drop the `state.online` term.

### F2 — Filter the `0xFFFF` (65535 centi) sentinel in `_bff_reading`
`auth.py:181-205` returns any numeric value. Add: when a centi key's raw int equals the no-value sentinel, return `None`. Sentinels observed/expected: `65535` (`0xFFFF`, unsigned). Also guard `32767`/`-1`-style signed sentinels for `tem` defensively. Result: humidity becomes `None` (no spurious 655.35); temperature stays 26.4.

### F3 — Don't synthesize a humidity capability for humidity-less SKUs
`device.py:synthetic_thermometer` always adds `sensorHumidity` (`device.py:875-879`). The H5310 pool thermometer has no hygrometer, so a humidity entity should not exist at all. Either:
- Add a `has_humidity: bool = True` parameter to `synthetic_thermometer` and pass `False` for pool/water SKUs (maintain a small `TEMP_ONLY_BFF_SKUS = {"H5310"}` set), or
- Decide capability from the first reading (omit humidity when the initial `hum` is the sentinel).
The static-set approach is deterministic and avoids a humidity entity that would otherwise be permanently `None`/unknown. F2 (sentinel→None) is still needed regardless, as a safety net.

### F4 — `temCali`/`humCali` and `fahOpen` remain available (not blocking)
`deviceSettings` still carries `temCali`/`humCali` (calibration offsets) and `fahOpen` (°F display flag). Out of scope for #97 but noted: `fahOpen` could later auto-drive `CONF_API_TEMPERATURE_UNIT` (ties to #96), and calibration offsets could match the app.

## Compatibility Analysis

- **No discovery change.** F1–F3 are all post-discovery handling; the #86/#88 BFF path and synthetic-device injection are unchanged.
- **F1** affects only availability of BFF thermo-hygrometers (or, if implemented via issue-68 proof-of-life, only `online` resets on fresh reads). No impact on Developer-API / MQTT devices whose `online` is meaningful.
- **F2** is a pure guard — real readings (incl. negative/near-0 centi from the #86 fix) pass through unchanged; only the `0xFFFF` sentinel is suppressed.
- **F3** removes a humidity entity for `H5310` only. For users on older versions a stale humidity entity may need cleanup (becomes unavailable/orphaned) — acceptable, or leave it and rely on F2 to blank it.

## Recommendation

Ship a patch addressing the two user-visible symptoms:

1. **F1 (must):** Stop gating BFF thermo-hygrometer entity availability on `state.online`. Override `available` on `GoveeTemperatureSensor`/`GoveeHumiditySensor` for `device_id in coordinator._bff_thermometer_ids` to require only coordinator success + a non-None reading. Keep `online` as the connectivity diagnostic.
2. **F2 (must):** In `_bff_reading`, return `None` when a centi key's raw int is the `0xFFFF` (65535) sentinel; defensively cover signed sentinels for `tem`.
3. **F3 (should):** Add `H5310` to a `TEMP_ONLY_BFF_SKUS` set and skip the synthesized `sensorHumidity` capability for those SKUs, so no humidity entity is created for the pool thermometer.
4. **Tests:** H5310 fixture with `online: false` + valid centi `tem` + `hum: 65535` → temperature entity **available** and 26.4; **no** humidity entity (F3) / humidity `None` (F2). Regression: a normal H5301 with real `hum` still produces a humidity entity with the correct value.

## Open Question for Reporter (optional)

Confirm the H5310 has no humidity readout in the Govee app. If a variant *does* report humidity, prefer F2 (sentinel→None) over F3 (drop capability) so humidity appears when real.
