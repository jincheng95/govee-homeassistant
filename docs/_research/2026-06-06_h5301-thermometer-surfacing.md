<!-- no-registry: single-feature investigation; no multi-item quantified scope -->
# Research: Surfacing the Govee H5301 thermo-hygrometer (issue #86)

**Date:** 2026-06-06
**Type:** Feature Investigation
**Issue:** https://github.com/lasswellt/govee-homeassistant/issues/86
**Status:** Actionable — surfacing path exists; one diagnostic confirmation needed

## Summary

H5301 doesn't appear in the integration because device discovery is built **only** from the Govee Developer Platform API (`GET /router/api/v1/user/devices`), and that curated endpoint omits app-only thermo-hygrometers. The integration has **no SKU allowlist** — detection is capability-based — so if H5301 were in `/user/devices` with `sensorTemperature`/`sensorHumidity`, it would already work. It isn't returned, hence "just the hub."

A surfacing path already exists in this codebase: H5054 leak sensors are surfaced **not** from the Developer API but from the account-login **BFF device list** (`https://app2.govee.com/bff-app/v1/device/list`), filtered by `LEAK_SENSOR_SKUS`. That same BFF list returns the full account roster — including app-only WiFi thermo-hygrometers — with last readings embedded in `deviceExt`. **Recommendation:** extend the existing BFF discovery path to thermo-hygrometer SKUs, reusing the H5054 precedent end-to-end. First, confirm H5301 is in the user's `bff_device_census` via a diagnostics download.

## Research Questions

**Q1: What is the H5301?**
WiFi Thermo-Hygrometer. 2.4GHz WiFi **+** Bluetooth via Govee Home app; 3×AA, ~7-month battery; ±0.3°C / ±3%RH; 2s app refresh, 20-day online storage. NOT pure-BLE. Source: us.govee.com smart-thermo-hygrometers collection.

**Q2: Does the Developer API (`/user/devices`) return H5301?**
Almost certainly no — consistent with "just the hub." The Developer API exposes a curated device set; battery WiFi thermo-hygrometers are frequently app-only and absent from it. Decisive confirmation requires the account's raw device list (see Q5 / diagnostics). The integration parses `/user/devices` `data[]` directly (`api/client.py:266`) with **no SKU gate** — absence there = invisible.

**Q3: Does H5301 need a Govee gateway/hub?**
No dedicated gateway. It's WiFi-native (connects directly to 2.4GHz). "The hub" the user sees is the integration's own HA service/hub device, not a Govee gateway — i.e. the integration loaded but discovered zero controllable devices.

**Q4: What device `type`/capability instances does H5301 report?**
Unknown without a live payload. If surfaced via the Developer API it would likely carry `devices.types.thermometer` + `sensorTemperature`/`sensorHumidity` (already handled, `models/device.py:391-411`). Via the BFF list, readings live in `deviceExt.lastDeviceData` (same shape the H5054 path parses).

**Q5: Is H5301 on Govee's published Developer-API device list / handled by other HA integrations?**
- Govee Developer API: no public per-SKU master list; coverage is curated.
- **`govee_ble`** (HA core / Bluetooth-Devices/govee-ble v1.2.0): NO H5301. `_MODEL_DB` covers H5121–H5127, H5130 (motion/button/presence/etc.), not the H53xx thermo line.
- **govee2mqtt** (wez): NO H5301; explicitly "no BLE support," WiFi SKUs only. But confirms the key mechanism: it queries the **undocumented `app2.govee.com` API** for a fuller device+room list than the Developer API exposes.
- No existing GitHub issues specific to H5301 cloud visibility.

## Findings

### F1 — Discovery is Developer-API-only, capability-gated (no SKU allowlist)
`GoveeApiClient.get_devices()` (`api/client.py:245-279`) builds entities solely from `/user/devices` `data[]`. There is no sub-device/gateway-child traversal and no SKU filter. Thermometer detection is capability-based (`supports_temperature_sensor`, `supports_humidity_sensor`, `is_thermometer` — `models/device.py:391-411`). Implication: a device the Developer API doesn't return cannot be surfaced through this path regardless of code changes.

### F2 — The BFF/app device list already returns the full account roster (the H5054 precedent)
`auth.py` already defines and calls the account-login app endpoints:
- `GOVEE_DEVICE_LIST_URL = https://app2.govee.com/device/rest/devices/v1/list` (auth.py:146) — used in `fetch_device_topics` for MQTT.
- `GOVEE_BFF_DEVICE_LIST_URL = https://app2.govee.com/bff-app/v1/device/list` (auth.py:147).

The BFF list returns **all** account devices (`data.devices[]`, auth.py:576). H5054 leak sensors are discovered from it, filtered by `if sku not in LEAK_SENSOR_SKUS: continue` (auth.py:582-585), parsing `deviceExt` → `deviceSettings` / `gatewayInfo` / `sno` (auth.py:592-599). `bff_device_census()` (auth.py:863) already exposes a PII-free per-SKU summary of this roster, surfaced in `diagnostics.py:187` and `coordinator.py:679`. **This is the exact mechanism needed for H5301** — same endpoint, same parse, different SKU set + reading fields.

### F3 — Readings are available without the Developer-API state endpoint
For thermo-hygrometers the BFF `deviceExt`/`lastDeviceData` carries last temp/humidity (how the Govee app renders them), and the account-login AWS IoT MQTT path (already connected when email/password configured) pushes live updates. So a BFF-discovered H5301 has a state source without `/device/state` (which won't serve a device absent from `/user/devices`).

### F4 — `govee_ble` is a poor fallback here
H5301 is WiFi-native and not in the `govee_ble` model DB; recommending a local BT proxy both adds hardware and isn't supported by that library today. The account-API path the integration already uses is strictly better for this SKU.

## Compatibility Analysis

- **Auth/session:** Requires email/password account login (already implemented; same session that powers H5054 + MQTT). API-key-only users cannot get H5301 — same limitation as H5054 leak detection. The user in #86 has cloud-only (API key) — they'll need account login configured.
- **App version pin:** BFF calls require `appVersion = 7.4.10` + matching headers (auth.py:550-552; CLAUDE.md). Already maintained; govee2mqtt #622/#626 show Govee enforces minimum app version — keep `GOVEE_APP_VERSION` current.
- **Detection model:** Capability-based design means no architectural change — add a `THERMO_HYGRO_SKUS` set (mirroring `LEAK_SENSOR_SKUS`) or detect by presence of temp/humidity fields in `deviceExt`.
- **Entities:** `GoveeTemperatureSensor` / `GoveeHumiditySensor` (`sensor.py:228-298`) already exist and are reading-source-agnostic (consume coordinator `device_state`). The Fahrenheit-normalization option (`CONF_API_TEMPERATURE_UNIT`) applies.

## Recommendation

**Extend the existing BFF discovery path to thermo-hygrometer SKUs — reuse the H5054 pattern end-to-end.** Do not add a gateway requirement; do not route users to `govee_ble`.

Gate behind a confirmation step because the live payload shape is unverified:

1. **Confirm first (cheap, decisive).** Ask the #86 reporter to configure account login (email/password), then download diagnostics and read `bff_device_census`. If H5301 appears there → F2/F3 path is confirmed viable. If it's absent from the BFF list too → escalate (device may be region/firmware-gated; capture raw BFF response via #87 skeleton).
2. **Implement** (pending confirmation): add thermo-hygro SKU handling to the BFF parse (auth.py:582+), map `deviceExt` readings → `GoveeDeviceState.sensor_temperature/sensor_humidity`, register the existing sensor entities.

| Option | Hardware | Reuses existing code | Verdict |
|---|---|---|---|
| **A. Extend BFF device-list discovery** | none | yes (H5054 precedent, sensor entities) | **Recommended** |
| B. Wait for Developer API to list H5301 | none | n/a | Not actionable (Govee-controlled) |
| C. Govee WiFi gateway → Developer API child | gateway purchase | partial | Unnecessary; H5301 is WiFi-native |
| D. HA core `govee_ble` (BT proxy) | BT adapter/proxy | none | Unsupported SKU; worse UX |

## Implementation Sketch

1. **SKU set** — `const.py`: add `THERMO_HYGRO_BFF_SKUS = {"H5301", ...}` next to `LEAK_SENSOR_SKUS`.
2. **Parse** — `api/auth.py:582+`: in the BFF `devices` loop, branch on thermo-hygro SKUs; extract last temp/humidity from `deviceExt`/`lastDeviceData` (parse JSON-string `deviceExt` as already done at auth.py:592-597). Retain a typed result list (mirror the leak-sensor return).
3. **Coordinator** — surface BFF-discovered thermo-hygro devices as `GoveeDevice`s (synthesize capabilities so `supports_temperature_sensor`/`supports_humidity_sensor` return true) and feed readings into `GoveeDeviceState.sensor_temperature/sensor_humidity` (coordinator state-merge already preserves these, `coordinator.py:1259-1274`).
4. **Entities** — no new classes: `GoveeTemperatureSensor` + `GoveeHumiditySensor` (`sensor.py`) attach automatically.
5. **Diagnostics** — extend `bff_device_census` entry with `in_thermo_hygro_skus` (parallel to `in_leak_sensor_skus`, auth.py:899) for future triage.
6. **Tests** — mirror `test_coordinator.py` H5054 BFF tests with an H5301 BFF fixture (identity + `deviceExt` readings); assert temp/humidity entities created and values flow.
7. **Freshness** — battery WiFi sensors upload infrequently; reuse the existing reading-timestamp/staleness handling (`sensor.py:301-326`, `coordinator.py:1276-1278`).

## Risks

- **Unverified payload shape.** The exact `deviceExt`/`lastDeviceData` keys for H5301 are not confirmed from a real account. Mitigation: the confirm-first step (diagnostics `bff_device_census` + #87 raw BFF skeleton) before writing the parser. This is the single biggest unknown and gates implementation.
- **Account login required.** Surfacing H5301 needs email/password (BFF + MQTT), not just an API key. The #86 reporter currently has cloud-only/API-key — they must add account credentials, which also means the March-2026 2FA flow. Document this clearly so it isn't mistaken for a bug.
- **Undocumented endpoint fragility.** `app2.govee.com` BFF is unofficial; Govee enforces minimum app version (govee2mqtt #622/#626 — "app version too low"). Mitigation: keep `GOVEE_APP_VERSION` current; the integration already depends on this endpoint for H5054/MQTT, so no new exposure.
- **Reading staleness / unit ambiguity.** Infrequent uploads can look "frozen"; API may report °F without metadata. Mitigations already exist (reading-changed timestamp sensor; `CONF_API_TEMPERATURE_UNIT`).

## Dissent / Contradictory Evidence

- **Transport classification conflict:** the `h5301-ha-ecosystem` research agent concluded H5301 is "BLE-only" and recommended a Govee gateway or `govee_ble`. This is **contradicted** by the authoritative Govee product spec (WiFi 2.4GHz + Bluetooth). The agent inferred BLE-only from H53xx naming without checking the product page; treat its gateway/`govee_ble` recommendation as superseded. The `govee_ble` model-DB facts it gathered remain accurate (H5301 genuinely absent there).
- Both background agents under-weighted the in-repo BFF/H5054 precedent (F2), which is the decisive enabler and overrides the "cloud API can't see it" framing — the **Developer** API can't, but the **account/BFF** API the integration already uses can.

## Open Questions

1. Does H5301 actually appear in this account's BFF `/bff-app/v1/device/list` response? (Decisive — resolve via diagnostics before coding.)
2. Exact `deviceExt`/`lastDeviceData` keys + units for H5301 readings?
3. Does the account-login AWS IoT MQTT push live H5301 updates, or is the BFF poll the only refresh source (affecting update latency)?

## References

- Issue #86: https://github.com/lasswellt/govee-homeassistant/issues/86
- Govee smart thermo-hygrometers (product specs): https://us.govee.com/collections/smart-thermo-hygrometers
- Govee Developer API Reference v2.0: https://govee-public.s3.amazonaws.com/developer-docs/GoveeDeveloperAPIReference.pdf
- Govee Developer — Get Your Devices: https://developer.govee.com/reference/get-you-devices
- govee_ble (HA core): https://www.home-assistant.io/integrations/govee_ble/
- govee-ble library (`_MODEL_DB`): https://github.com/Bluetooth-Devices/govee-ble/blob/master/src/govee_ble/parser.py
- govee2mqtt SKUS (no-BLE statement): https://github.com/wez/govee2mqtt/blob/master/docs/SKUS.md
- govee2mqtt app-version enforcement: https://github.com/wez/govee2mqtt/issues/622 , https://github.com/wez/govee2mqtt/issues/626
- Codebase: `api/client.py:245`, `api/auth.py:146-147,452,559,582,863`, `models/device.py:391-411`, `sensor.py:228-326`, `coordinator.py:679,1259-1278`, `diagnostics.py:187`
