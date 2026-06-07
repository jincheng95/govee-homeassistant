---
scope:
  registry: thermo-hygro-completeness
  source: docs/_research/2026-06-07_h5301-h5310-thermo-hygro-completeness.md
  issue: 86
  items:
    - id: bff-thermo-battery-sensor
      label: "Surface battery% for BFF thermo-hygrometers (fetched at auth.py:682, discarded — no GoveeDeviceState.battery field, no entity)"
      priority: high
      status: done  # GoveeThermoBatterySensor + GoveeDeviceState.battery
    - id: h5301-payload-confirmation
      label: "Confirm H5301 BFF lastDeviceData key/scale shape (currently speculative — auth.py:664 'unverified'; needs real H5301 diagnostics)"
      priority: high
      status: pending  # blocked: needs a real H5301 diagnostic from a user
    - id: bff-thermo-hub-via-device
      label: "Register H5044 gateway hub + via_device linkage for H5310 (gatewayInfo not extracted; no register_thermo_hubs analog vs leak-sensor path)"
      priority: medium
      status: done  # register_thermo_hubs + GoveeDevice.hub_device_id + via_device
    - id: bff-thermo-fahopen-unit
      label: "Investigate fahOpen flag — determine whether BFF thermo reports °C centi-int or °F; wire to CONF_API_TEMPERATURE_UNIT auto"
      priority: medium
      status: instrumented  # fahOpen captured to debug log + sensor dict; value NOT transformed (no ground-truth — would risk #96 regression)
    - id: bff-thermo-calibration
      label: "Apply temCali/humCali calibration offsets from deviceSettings to readings"
      priority: low
      status: instrumented  # temCali/humCali captured; offsets NOT applied pending evidence they aren't already baked into BFF tem/hum
    - id: bff-thermo-update-churn
      label: "Skip async_set_updated_data when readings unchanged (currently fires every 5-min poll)"
      priority: low
      status: done  # refresh now guards on temp/humidity/battery change
citations:
  - url: https://github.com/lasswellt/govee-homeassistant/issues/86
    status: LIVE
  - url: https://github.com/lasswellt/govee-homeassistant/issues/97
    status: LIVE
  - url: https://us.govee.com/collections/smart-thermo-hygrometers
    status: LIVE
  - url: https://manuals.plus/category/goveelife
    status: UNKNOWN  # 403 bot-block on automated fetch; domain valid
  - url: https://govee-public.s3.amazonaws.com/developer-docs/GoveeDeveloperAPIReference.pdf
    status: LIVE
  - url: https://github.com/wez/govee2mqtt/blob/master/docs/SKUS.md
    status: LIVE
  - url: https://github.com/wez/govee2mqtt/issues/622
    status: LIVE
  - url: https://www.home-assistant.io/integrations/govee_ble/
    status: LIVE
  - url: https://github.com/Bluetooth-Devices/govee-ble/blob/master/src/govee_ble/parser.py
    status: LIVE
  - url: https://developers.home-assistant.io/docs/core/entity/sensor/
    status: LIVE
  - url: https://developers.home-assistant.io/docs/device_registry_index/
    status: LIVE
---
# Research: H5301 / H5310 thermo-hygrometer support — completeness (issue #86)

**Date:** 2026-06-07
**Type:** Feature Investigation (completeness audit of partially-shipped #86)
**Issue:** https://github.com/lasswellt/govee-homeassistant/issues/86 (OPEN)
**Shipped so far:** `1cf88d6` (surface BFF-only thermo-hygrometers, H5301), `d83fae7` (add H5310 + robust centi de-scale), branch `fix/97-h5310-readings-unknown` (availability online-gate fix + 0xFFFF humidity sentinel + omit humidity capability for `TEMP_ONLY_BFF_SKUS`)
**Status:** Core path works for H5310; 6 gaps remain. H5301 still unconfirmed against real hardware.

## Summary

#86 core ask is met for **H5310**: discovered via BFF, synthesized as a thermometer device, temperature renders correctly, humidity (a `0xFFFF` sentinel — H5310 has no hygrometer) suppressed. **H5301 support is speculative** — it shares `THERMO_HYGRO_BFF_SKUS` and synthesizes correctly, but no real H5301 diagnostic has confirmed its BFF `lastDeviceData` key names or centi-vs-plain scaling (explicit `auth.py:664` comment: *"reading keys/scaling for H5301 are unverified"*). Highest-value remaining work: **battery% is fetched but silently discarded** (no entity), and **H5301 payload shape needs a real diagnostic**. Medium: H5044 gateway hub `via_device` linkage and `fahOpen` unit detection. Recommend closing #86 as "H5310 supported" only after a battery sensor lands and an H5301 user confirms, or split H5301 into a follow-up issue.

## Research Questions

**Q1 — End-to-end BFF thermo-hygrometer flow?**
Discovery at startup: `_discover_bff_thermometers()` (`coordinator.py:740`) → `GoveeAuthClient.fetch_bff_thermo_hygrometers(token)` (`auth.py:575`) → filter `sku in THERMO_HYGRO_BFF_SKUS` (`auth.py:625`) → parse `deviceExt.deviceSettings` (battery, versions) + `deviceExt.lastDeviceData` via `_bff_reading()` → `GoveeDevice.synthetic_thermometer()` (`models/device.py:851`) → store in `_devices` + `_bff_thermometer_ids` → seed `GoveeDeviceState`. Entities created in `sensor.py:70-82` (`supports_temperature_sensor`/`supports_humidity_sensor` + reading-freshness timestamp). Refresh every 5 min via `_refresh_bff_thermometers()` (preserves last-good reading on omit). Developer-API `/device/state` skipped for `_bff_thermometer_ids` (`coordinator.py:1279`).

**Q2 — H5301 proven or speculative?**
**Speculative.** H5310 is confirmed from real #86 diagnostics (`test_auth.py` "Real #86 shape: H5310 P2 via H5044"). H5301's test uses a hand-constructed payload; `auth.py:664-667` flags reading keys/scaling as unverified and keeps a debug log to capture the real shape from the next H5301 reporter. If H5301 uses a `lastDeviceData` key outside the `_BFF_TEMP_KEYS`/`_BFF_HUMIDITY_KEYS` ladder, both entities silently go unavailable.

**Q3 — Device facts (specs / humidity / API visibility)?**
H5301 = WiFi 2.4GHz thermo-hygrometer, 3×AA (~7 mo), ±0.3°C/±3%RH, temp **and** humidity. H5310 = GoveeLife Smart Thermometer **P2**, RF/BLE bridged via **H5044** gateway, **temperature-only** (humidity = `0xFFFF` sentinel). Both **absent from the Developer API** (`/router/api/v1/user/devices` → `raw_api_devices: []`); both present in account-login **BFF** list (`app2.govee.com/bff-app/v1/device/list`). BFF is the only viable source — validated by the H5054 leak-sensor precedent.

**Q4 — How do other integrations handle these SKUs?**
`govee2mqtt` (wez) routes via the same undocumented `app2.govee.com` BFF API and enforces `appVersion 7.4.10` (matches this integration's `GOVEE_APP_VERSION`). `homebridge-govee` has no explicit H53xx handling (likely BFF too). HA-core `govee_ble` (`Bluetooth-Devices/govee-ble`) covers H5121–H5127/H5130 (motion/buttons) — **not** H53xx thermos; not a usable fallback. No project shows a different decode path for these SKUs.

**Q5 — What remains / what's untested?**
Implemented: discovery, synthetic device, temp/humidity entities, sentinel filter, centi de-scale, availability mixin, reading-freshness, Developer-API skip — all covered by tests (`test_auth.py`, `test_thermometer.py`, `test_coordinator.py`). Untested / missing: **battery entity**, `fahOpen` unit detect, `temCali`/`humCali` calibration, gateway hub registration / `via_device`, `gateway_online` tracking, full-stack `native_value` for a real H5301/H5310 (existing tests stub the entity).

**Q6 — Correctness risks in current code?**
`_bff_reading` centi/float/sentinel logic is correct: float passes through unscaled (`isinstance(raw, int)` guards), int de-scales, `_BFF_NO_VALUE_SENTINELS = {65535, 32767, -1}` filters only the int path. Minor: `-1` would drop a (physically implausible) `-0.01°C` reading — safe. Two soft issues: `state.online = sensor.get("online", True)` default makes transport-health diagnostics report success even when the gateway is offline; and `_refresh_bff_thermometers` builds a fresh state + calls `async_set_updated_data` every 5 min regardless of change → unnecessary entity re-render (leak path shares this).

## Findings

### F1 — H5310 core support is complete; H5301 is inference, not evidence
H5310 validated against real diagnostics end-to-end. H5301 rides the same code by SKU-set membership but has zero confirmed payload evidence (`auth.py:664`). Risk: a real H5301 may key temperature/humidity differently and silently return `None`. **Decision needed:** keep H5301 in `THERMO_HYGRO_BFF_SKUS` (harmless if absent; debug log captures shape on first report) but do not advertise it as "supported" until confirmed.

### F2 — Battery% fetched then discarded (highest-value gap)
`fetch_bff_thermo_hygrometers` returns `battery` (`auth.py:682`) from `deviceSettings`, but `GoveeDeviceState` has **no battery field** and the coordinator stores it nowhere. `GoveeLeakBatterySensor` (`sensor.py:410`) exists for leak sensors; nothing analogous for thermo. A battery sensor is the most user-visible missing entity for a battery device. HA pattern: `SensorDeviceClass.BATTERY` + `MEASUREMENT` + `PERCENTAGE`; availability gated on coordinator + state present (reuse `_BffThermometerAvailabilityMixin` shape).

### F3 — H5044 gateway not modeled (no via_device)
H5310 bridges via H5044 (`gatewayInfo.sku` present in the BFF payload, parsed only in the test fixture). `fetch_bff_thermo_hygrometers` does not extract `hub_device_id`; there is no `register_thermo_hubs()` analog and entity `device_info` has no `via_device`. Leak sensors model this fully (`leak_sensor_device_info` `via_device=(DOMAIN, hub_device_id)` + `register_leak_hubs()`). HA pattern: register hub device first, then child `via_device=(DOMAIN, hub_device_id)`. Cosmetic-but-correct device-tree relationship; also unlocks future `gateway_online` reporting.

### F4 — Unit (°C/°F) source unconfirmed for BFF thermo (`fahOpen`)
`deviceSettings` carries `fahOpen` (device °F-display flag) and `temCali`/`humCali`. Current code assumes BFF `tem`/`hum` are centi-°C. H5301/H5310 are **not** in `FAHRENHEIT_REPORTING_SKUS` (`const.py` — only H5179/H5109/H5110/HS5108/HS5106, the Developer-API °F-without-metadata SKUs from #96). If a BFF thermo ever emits °F when `fahOpen=1`, the `auto` mode passes it through wrong. Extract `fahOpen` and either convert or feed `CONF_API_TEMPERATURE_UNIT` auto. Ties to #96.

### F5 — Calibration offsets ignored (low)
`temCali`/`humCali` (per-device offsets the Govee app applies) are not extracted, so HA may differ from the app by the user's calibration. Apply post-de-scale.

### F6 — Update churn (low)
`_refresh_bff_thermometers` always emits `async_set_updated_data` even when temp/humidity are unchanged (every 5 min). Guard on value change to avoid needless re-render. Shared with the leak path — fix once, apply to both.

## Dissent / Contradictory Evidence

- **"H5301 is BLE-only / needs `govee_ble` or a gateway"** — **Rejected.** An earlier research pass inferred BLE-only from the H53xx naming. Product spec (us.govee.com smart-thermo-hygrometers) lists H5301 as WiFi 2.4GHz native; codebase routes it via WiFi BFF. `govee_ble` absence is real but irrelevant — the BFF path is strictly better.
- **"H5310 has humidity"** — **Rejected.** `hum: 0xFFFF` sentinel + pool/water P2 form factor + `TEMP_ONLY_BFF_SKUS`. Temperature-only.
- Both rejections converge across ≥3 domains (product page, manuals.plus, govee2mqtt docs, in-repo diagnostics).

## Compatibility Analysis

- **No new mechanism for the gaps.** Battery sensor, hub registration, and `via_device` all have a working template in the leak-sensor path (`GoveeLeakBatterySensor`, `register_leak_hubs`, `leak_sensor_device_info`). Reuse, don't invent.
- **`GoveeDeviceState` change required** for F2: add a `battery: int | None` field (mutable state dataclass) — low blast radius.
- **Account login required** (BFF needs email/password) — already a precondition, same as leak sensors.
- **App version pinned** `7.4.10` — Govee enforces a BFF minimum; keep monitored.
- **HA quality scale**: new entities need `has_entity_name=True` (inherited via `GoveeEntity`), `translation_key`, stable `unique_id = f"{device_id}_battery"`, plus `strings.json` + `translations/en.json` entries.

## Recommendation

Prioritized (matches `scope:` items):

1. **HIGH — Battery sensor (F2).** Add `GoveeDeviceState.battery`; store `sensor.get("battery")` in discovery + refresh; add `GoveeThermoBatterySensor` (`SensorDeviceClass.BATTERY`, mixin availability). Highest user value, lowest risk, full template exists.
2. **HIGH — Confirm H5301 (F1).** Keep the `auth.py:668` debug log; request a real H5301 diagnostic on #86. Until then, treat H5301 as provisional in release notes. Consider splitting H5301 into its own tracking issue and closing #86 as "H5310 done."
3. **MEDIUM — H5044 hub `via_device` (F3).** Extract `gatewayInfo` → `hub_device_id`; register hub; add `via_device` to thermo `device_info`. Mirrors leak path.
4. **MEDIUM — `fahOpen` unit (F4).** Extract `fahOpen`; convert or drive `CONF_API_TEMPERATURE_UNIT`. Coordinate with #96.
5. **LOW — Calibration (F5)** and **update-churn guard (F6).**

## Implementation Sketch

```python
# models/state.py — add field
@dataclass
class GoveeDeviceState:
    ...
    battery: int | None = None

# coordinator.py — _discover_bff_thermometers + _refresh_bff_thermometers
state.battery = sensor.get("battery")   # already fetched at auth.py:682

# api/auth.py:fetch_bff_thermo_hygrometers — also surface the hub for F3
"hub_device_id": (device_ext.get("deviceSettings", {})
                  .get("gatewayInfo", {}).get("device", "")),

# sensor.py — new entity (reuse availability mixin)
class GoveeThermoBatterySensor(_BffThermometerAvailabilityMixin, SensorEntity):
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_translation_key = "sensor_battery"
    def __init__(self, coordinator, device):
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device.device_id}_battery"
    @property
    def native_value(self):
        s = self.device_state
        return s.battery if s else None

# sensor.py:async_setup_entry — attach when device is a BFF thermo with battery
if coordinator.is_bff_thermometer(device.device_id):
    entities.append(GoveeThermoBatterySensor(coordinator, device))
```
Tests: extend `test_thermometer.py` / `test_coordinator.py` with a battery-bearing H5310 fixture → battery entity present + correct value; H5301 hand-fixture remains until real data.

## Risks

- **H5301 false-positive support.** Shipping H5301 in the SKU set without confirmed payload keys means an H5301 user could see a discovered device with permanently-unavailable temperature/humidity (key mismatch → `None`). Mitigation: the `auth.py:668` debug log captures the real shape; do not claim H5301 support in release notes until a diagnostic confirms it. This is the main reason #86 should not be silently closed.
- **Battery field source assumption.** `battery` comes from `deviceSettings`; if some SKUs omit it, the sensor must tolerate `None` (it does) and ideally not be created when battery is absent at discovery.
- **`fahOpen` left unhandled** could resurface a #96-style "1.8× too high" report for a BFF thermo whose app is set to °F. Low probability (current readings decode as plausible centi-°C) but explicitly unverified.
- **Soft-churn fix (F6)** must preserve the reading-freshness semantics (`_note_sensor_reading_change`) — guard on value change without suppressing the "last changed" timestamp logic.

## Open Questions

- Does a real **H5301** report `tem`/`hum` under the same keys and centi scaling as H5310? (Blocks confirmed H5301 support — needs a diagnostic from an H5301 owner.)
- Does the BFF `battery` field exist for all thermo SKUs, or only gateway-bridged ones? (Affects whether the battery entity is unconditional.)
- Is `fahOpen` ever `true` for these SKUs in practice, and does it change the `tem` encoding, or only the app's display? (Determines whether F4 is a real correctness fix or cosmetic.)

## References

- Issue #86: https://github.com/lasswellt/govee-homeassistant/issues/86
- Issue #97 (H5310 readings downstream): https://github.com/lasswellt/govee-homeassistant/issues/97
- Govee smart thermo-hygrometers: https://us.govee.com/collections/smart-thermo-hygrometers
- GoveeLife manuals: https://manuals.plus/category/goveelife
- Govee Developer API reference: https://govee-public.s3.amazonaws.com/developer-docs/GoveeDeveloperAPIReference.pdf
- govee2mqtt SKUs: https://github.com/wez/govee2mqtt/blob/master/docs/SKUS.md
- govee2mqtt app-version enforcement: https://github.com/wez/govee2mqtt/issues/622
- govee_ble integration: https://www.home-assistant.io/integrations/govee_ble/
- govee_ble model DB: https://github.com/Bluetooth-Devices/govee-ble/blob/master/src/govee_ble/parser.py
- HA sensor entity docs: https://developers.home-assistant.io/docs/core/entity/sensor/
- HA device registry (via_device): https://developers.home-assistant.io/docs/device_registry_index/
- In-repo: `docs/_research/2026-06-06_h5301-thermometer-surfacing.md`, `2026-06-06_h5310-diagnostic-rootcause.md`, `2026-06-07_h5310-readings-unknown.md`, `2026-05-29_thermometer-freshness.md`, `2026-05-01_issue-68-online-recovery.md`
- Code: `coordinator.py:740-836,1279`; `api/auth.py:181-205,565-685`; `models/device.py:26,851-887`; `sensor.py:70-82,229-298,410`; `entity.py:67-82`
