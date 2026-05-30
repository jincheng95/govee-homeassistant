<!-- no-registry: exploratory — Phase 2 (in-integration BLE decode) is gated on a correlation spike; scope quantifiable only after the spike resolves the BLE↔cloud id mapping -->

# Research + Decision: Thermometer/hygrometer reading freshness (#83)

**Date:** 2026-05-29
**Type:** Architecture Decision (formalized from Feature Investigation)
**Status:** DECIDED — Phase 0/1 adopt now; Phase 2 gated on a spike
**Issues:** #83 (H5075/H5110/H5179 stale), related #85 (unit), #62 (H5054/H5109), #57 (local control)
**Stack:** HA custom component, Govee Cloud API v2.0 + AWS IoT MQTT + BFF account API + existing BLE layer

---

## Summary

Stale thermometer readings are an **architectural limit of Govee's cloud**, not an integration bug: the H5151 gateway batch-uploads BLE sensor readings every **15–60 min** (WiFi-native H5179 ~10 min), and the Platform API faithfully returns that last value — which we already parse and display correctly (proven by davcamer's v2026.5.13 diagnostics). AWS IoT carries **no** sensor telemetry; govee2mqtt hits the identical wall. The only real-time path is **local BLE advertisement decode**, which HA's first-party `govee_ble` integration and the `govee-ble` library already do for 20+ thermometer SKUs (govee2mqtt does not). **Decision:** treat the cloud value as authoritative-but-batched and make its age visible (Phase 0–1), officially route real-time to `govee_ble` + an ESPHome BLE proxy for remote sensors (Phase 1), and **defer** in-integration BLE decode (Phase 2) behind a correlation spike — because reimplementing a maintained first-party decoder carries maintenance + mis-attribution risk that the documented `govee_ble` path avoids entirely.

---

## Research Questions

**Q1. Why stale?** Govee cloud architecture — H5151 gateway batches uploads 15/30/60 min; H5179 WiFi ~10 min; no "immediate" for gateway-bridged sensors. (HA #88775; H5151 manual.)
**Q2. Is our parsing wrong?** No — davcamer v2026.5.13 dump: REST returns the value, parser yields it, `source: api`. Value is simply old.
**Q3. Can MQTT deliver fresh readings?** No — AWS IoT has no sensor telemetry for these SKUs (our dump + govee2mqtt `iot_api_supported=false`). 2026.5.11 MQTT-parse fix is inert here (harmless, keep).
**Q4. Does anyone beat it via cloud?** No — govee2mqtt (#308/#296/#206) suffers identical thermometer staleness via the same Platform poll. (homebridge-govee uses the same OpenAPI poll architecturally, but no thermometer-specific staleness report was located — do not cite as direct evidence.)
**Q5. What gives real-time?** Local BLE advert decode only — `govee-ble` (20+ SKUs H5051–H5198) + HA `govee_ble` (`connectable:false`; matcher mfr-ids include 34819/`0x88EC`; service-uuid `0000ec88-…` is among the matchers). govee2mqtt BLE is control-only.

---

## Findings

### F1 — Staleness is architectural and universal
HA #88775 (H5071 stuck), govee2mqtt #308 (H5100/H5151 empty/stale); H5151 manual documents 15–60 min upload cadence. No third party beats it via cloud.

### F2 — Integration is correct end-to-end (cloud path)
v2026.5.13 diagnostics (davcamer, 3× H5110): `raw_api_state` carries `sensorTemperature`/`sensorHumidity`; parsed `source: api`; `transport_health.cloud_api.last_success` advances each poll; `mqtt.tracked_devices: 0`, `last_mqtt_message: null`.

### F3 — BLE advertisement decode is the real-time path
`govee-ble` parser: H5075 = 3-byte big-endian (`temp_c=v/10000`, `hum=(v%1000)/10`, sign bit `0x800000`); AES-ECB for 24-byte encrypted variants; mfr-ids `0xEC88`+15; service-uuid `0000ec88-0000-1000-8000-00805f9b34fb`. Passive (~2s adverts, no pairing). HA `govee_ble` ships exactly this.

### F4 — We already own most BLE plumbing (but not decode)
`ble_advertisement.py` subscribes to Govee adverts today (for *light* command dispatch only); manifest `bluetooth:` matchers are `connectable:true` + single mfr-id 34819; `const.GOVEE_BLE_MANUFACTURER_IDS` exists. No sensor manufacturer-data decode.

### F5 — #85/#62 corroborated
govee2mqtt #206 = our #85 (unit scaling) — handled by `CONF_API_TEMPERATURE_UNIT`. steamer70's HomeBridge log: H5054/H5109 via OpenAPI/account path, like our H5058 BFF work.

---

## Dissent / Contradictory Evidence

- **"MQTT fix solves #83" — refuted** by our dump + govee2mqtt source. Keep the parse (harmless); it is not the fix.
- **"BFF/account API is fresher" — unconfirmed, likely false.** Undocumented + app-version-gated (breaks under Govee's 2026-03 enforcement); probably the same batched value. May carry a `lastTime` useful for reading-age (Phase 1) — inspect one real `/bff-app/v1/device/list` response, don't assume freshness.
- **BLE decode is not universal.** Requires the sensor within Bluetooth range of an HA adapter / ESPHome proxy. The canonical #83 setup (sensors remote, bridged via H5151 precisely because they're far) gets **no benefit** without a proxy. Must be stated plainly or those reporters still see "stale."

---

## Validation against HA 2026 best practices

Validated 2026-05-30 against developers.home-assistant.io (quality scale, sensor entity, bluetooth, device registry). Three corrections fold into the plan:

1. **`entity-unavailable` rule vs "keep showing stale value" (Phase 0) — partial conflict.** HA: *"If we can't fetch data … mark it as unavailable … a better state than showing the last known state"* (rules/entity-unavailable). Nuance: our poll *succeeds* (the API returns a value), so it is not a fetch failure — but displaying an hours-old reading as current is what the rule discourages. **Idiomatic resolution:** when a reading-age source exists (BFF `lastTime`, Open Q2), mark the sensor **`unavailable` past a configurable staleness threshold** rather than silently showing old data. Without an age source we cannot distinguish stale from fresh, so we display the last value and document the limitation. Our `quality_scale.yaml` claims `entity-unavailable: done` (Silver) — honest caveat: thermometer entities currently stay available while stale, so true compliance needs the age-threshold gate.
2. **Reading-age → dedicated diagnostic `TIMESTAMP` sensor, NOT `extra_state_attributes`.** Per entity/sensor docs (`extra_state_attributes` has a 16384-byte limit + DB bloat for changing values; "create an additional sensor entity" instead). Use `device_class=SensorDeviceClass.TIMESTAMP` + `entity_category=DIAGNOSTIC`. Already the codebase pattern (`sensor.py:296`). Updates Phase 1a.
3. **Phase 2 BLE: use the blessed stack, not a hand-rolled callback.** Canonical 2026 pattern is `PassiveBluetoothProcessorCoordinator[SensorUpdate]` + the maintained `govee-ble` lib (v1.2.0, Feb 2026; powers HA's `govee_ble`; parses `BluetoothServiceInfoBleak`→temp/hum/battery), with `connectable: false` matchers and `bluetooth.async_address_present`/learned-interval for availability. Our current `connectable: true` light matchers don't block passive adverts but are inaccurate for sensors.

Confirmed-good (no change): temp/humidity `state_class=MEASUREMENT`, store °C, add `suggested_display_precision=1`; device-classes unchanged in 2026; BLE+cloud **overlap is allowed** — merged via **shared device identifiers** (caveat: our cloud 8-octet id ≠ `govee_ble`'s BLE MAC, so they won't auto-merge → duplicate devices unless we register the BLE MAC as a secondary identifier).

## Compatibility Analysis

- `govee_ble` is a **first-party HA integration** — zero code/maintenance for us; works out of the box for in-range sensors; keys by BLE device natively (no correlation problem).
- `govee-ble` (the library) is pure-Python, already a transitive dep of HA core; if we ever do Phase 2 we depend on it rather than hand-roll the parser (incl. AES, 20+ SKUs).
- Existing reuse for Phase 2: `ble_advertisement.py` subscription, `GOVEE_BLE_MANUFACTURER_IDS`, transport-health (`source="ble"`), existing `GoveeTemperatureSensor`/`GoveeHumiditySensor`.
- **Phase 2 key risk:** BLE↔cloud device-id correlation (6-octet BLE MAC + local_name `GVH5075_XXXX` vs 8-octet cloud `device_id`), critical when a user has multiple same-SKU sensors.

---

## Decision

**Adopt the "authoritative-but-batched cloud value + visible age + first-party `govee_ble` for real-time" approach. Defer in-integration BLE decode.**

Phased:

| Phase | Decision | Status | Effort |
|---|---|---|---|
| **0** | Document the cloud-staleness reality (15–60 min, by Govee design); stop treating it as a parse bug; reply on #83 | **ADOPT now** | XS |
| **1a** | Reading-age visibility: expose a `last reading` timestamp/attribute on the temp/humidity sensors so users see "updated 22 min ago" | **ADOPT** | S |
| **1b** | Document + recommend HA first-party **`govee_ble`** for in-range sensors and an **ESPHome BLE proxy** for remote ones; optional Repairs hint when a Govee thermometer advert is seen but only the cloud entity exists | **ADOPT** | S |
| **2** | In-integration BLE advert decode via `govee-ble`, feeding existing cloud entities (single entity, prefer BLE over stale REST) | **DEFER** → correlation spike first | M |
| — | AWS IoT for sensors | **REJECT** (no telemetry) | — |
| — | Assume BFF is fresher | **REJECT** (inspect for `lastTime` only) | — |

**Rationale for deferring Phase 2 (the key call):** real-time already exists as a maintained, HA-blessed path (`govee_ble`). Reimplementing decode in-integration buys only entity-unification (one entity instead of two) at the cost of (a) maintaining a parser for 20+ SKUs incl. AES, and (b) a mis-attribution risk if BLE↔cloud correlation is wrong for multi-same-SKU users. That trade is not justified until there's demonstrated demand AND the correlation rule is proven on real hardware. Phase 0–1 resolve the actual user pain (confusion + no real-time option) at XS/S cost and near-zero risk.

---

## Implementation Sketch

**Phase 0 (docs/issue):** README "Sensors" note + #83 reply: cloud readings are Govee-batched 15–60 min; integration displays them correctly; real-time needs BLE.

**Phase 1a (reading-age + staleness, HA-idiomatic per Validation):**
- Stamp the wall time when `sensor_temperature`/`sensor_humidity` last *changed* (in `models/state.py` / coordinator preservation at `coordinator.py:706-721`); prefer a real device `lastTime` if the BFF carries one.
- Expose as a **dedicated diagnostic timestamp sensor** (`device_class=SensorDeviceClass.TIMESTAMP`, `entity_category=DIAGNOSTIC`) per thermometer in `sensor.py` — NOT `extra_state_attributes`. Mirror the existing `sensor.py:296` timestamp sensor.
- If a reading-age source exists, add a configurable staleness threshold; the temp/humidity entity returns `available=False` once the reading exceeds it (satisfies `entity-unavailable`). Without an age source, keep displaying + document.
- Probe: inspect one real `/bff-app/v1/device/list` (we already fetch it, `api/auth.py:488`) for a per-device `lastTime`/`lastDeviceData` (Open Q2).

**Phase 1b (route real-time):** README section + a Repairs issue (`repairs.py`) that fires when an advert from a Govee thermometer mfr-id is observed but the device has only a cloud entity, suggesting `govee_ble` / ESPHome proxy.

**Phase 2 (deferred — spike first; use the blessed BLE stack per Validation):** add `govee-ble` to `manifest.json` requirements; add passive (`connectable:false`) thermometer matchers mirroring HA `govee_ble`; consume adverts via `PassiveBluetoothProcessorCoordinator`/`bluetooth.async_register_callback(PASSIVE)` and parse with `govee-ble` (do NOT hand-roll the decoder); set `state.sensor_*` with `source="ble"`, invert REST preservation so a fresh BLE value wins; availability via `bluetooth.async_address_present`. **Gate on a verified correlation rule** (spike: one user with cloud diagnostics + a BLE scan, ideally multi-same-SKU like davcamer's 3× H5110). If correlation proves unreliable, fall back to Phase 1b (recommend first-party `govee_ble`).

Affected: `README.md`, `sensor.py`, `models/state.py`, `coordinator.py`, `repairs.py` (Phase 1); `manifest.json`, `ble_advertisement.py`, `const.py` (Phase 2).

---

## Risks

- **Phase 1b expectation gap** — the loudest #83 reporters are bridged-remote; `govee_ble` won't help them without a proxy. Phase 0 docs must lead so they understand why.
- **Phase 2 correlation is unproven** — shipping decode before confirming the BLE↔cloud id mapping risks delivering a reading to the wrong entity for multi-same-SKU users. Strictly gate on a verified match; needs a real-hardware spike.
- **Phase 1a timestamp source** — if no BFF `lastTime` exists, "last reading" can only mean "last time the value changed in HA", which is weaker but still useful; document the semantics so it isn't misread as a device clock.
- **Scope creep vs `govee_ble`** — Phase 2 duplicates a first-party integration; only justified by the one-stop-shop goal (#57) and single-entity UX. Decision above defers it deliberately.

---

## Open Questions

1. BLE↔cloud device-id correlation rule for thermometers (MAC-suffix? `GVH5075_XXXX` name suffix?) — needs one user with cloud diagnostics + a BLE scan (Phase 2 gate).
2. Does `/bff-app/v1/device/list` carry a per-device `lastTime`/`lastDeviceData` we can use for Phase 1a reading-age? (We already fetch it — inspect one real response.)
3. Is there real demand for in-integration BLE (Phase 2) vs users accepting `govee_ble`? Gauge from #83/#57 responses before building.

---

## References

- #83 / #85 / #62 / #57 — this repo; davcamer v2026.5.13 diagnostics (issue #83)
- govee-ble parser: https://github.com/Bluetooth-Devices/govee-ble/blob/main/src/govee_ble/parser.py
- HA govee_ble integration: https://www.home-assistant.io/integrations/govee_ble/
- HA govee_ble manifest: https://raw.githubusercontent.com/home-assistant/core/dev/homeassistant/components/govee_ble/manifest.json
- HA core #88775 (stale thermometer): https://github.com/home-assistant/core/issues/88775
- govee2mqtt #308 (H5100/H5151 empty): https://github.com/wez/govee2mqtt/issues/308
- govee2mqtt #296 (H5075 unsupported): https://github.com/wez/govee2mqtt/issues/296
- govee2mqtt #206 (unit scaling): https://github.com/wez/govee2mqtt/issues/206
- homebridge-govee #733 (H5054 water-leak sensor — relevant to #62, NOT thermometer staleness): https://github.com/homebridge-plugins/homebridge-govee/issues/733
- Govee subscribe-device-event: https://developer.govee.com/reference/subscribe-device-event
- H5075 BLE analysis (WimsWorld): https://wimsworld.wordpress.com/2020/07/11/govee-h5075-and-h5074-bluetooth-low-energy-and-mrtg/
- Codebase: `ble_advertisement.py`, `manifest.json` (`bluetooth:`), `const.GOVEE_BLE_MANUFACTURER_IDS`, `api/auth.py:488` (BFF), `sensor.py` (incl. `:296` timestamp sensor), `coordinator.py:706-721` (sensor preservation), `quality_scale.yaml`
- HA quality scale `entity-unavailable`: https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/entity-unavailable/
- HA sensor entity (extra_state_attributes guidance): https://developers.home-assistant.io/docs/core/entity/sensor/
- HA bluetooth (PassiveBluetoothProcessorCoordinator, matchers, async_address_present): https://developers.home-assistant.io/docs/core/bluetooth/
- HA device registry (overlapping integrations / shared identifiers): https://developers.home-assistant.io/docs/device_registry_index/
- govee-ble library (maintained, powers HA govee_ble): https://github.com/Bluetooth-Devices/govee-ble
