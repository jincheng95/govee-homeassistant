<!-- no-registry: single-device root-cause follow-up; no multi-item quantified scope -->
# Research: H5310 still not surfacing on v2026.6.9 — diagnostics root cause (issue #86)

**Date:** 2026-06-06
**Type:** Feature Investigation (follow-up to `2026-06-06_h5301-thermometer-surfacing.md`)
**Issue:** https://github.com/lasswellt/govee-homeassistant/issues/86
**Input:** reporter diagnostics `config_entry-govee-01KSKEPETH26D0NYQG9NDGAT0G.json` (on v2026.6.9, account login configured)
**Status:** Root cause found — one-SKU fix + reading-scale refinement

## Summary

Reporter installed v2026.6.9, configured account login, restarted — device still absent. Diagnostics give the decisive answer: the device is **H5310** (GoveeLife Smart Thermometer P2), not **H5301** — the issue title transposed the digits, and the v2026.6.9 fix added `H5301` to `THERMO_HYGRO_BFF_SKUS`. The census entry shows `"sku": "H5310"`, `"in_thermo_hygro_skus": false`. Everything else in the v2026.6.9 design is correct: the H5310 is in the BFF list, bridged via an **H5044** gateway (`sno: 0` + `gatewayInfo`), and its `deviceExt.lastDeviceData` carries exactly `tem`/`hum`/`online`/`lastTime` — the keys `_bff_reading` already targets. **Fix:** add `H5310` to `THERMO_HYGRO_BFF_SKUS`, and de-scale the confirmed centi-unit `tem`/`hum` keys unconditionally (the magnitude heuristic mis-handles near-0 °C). No architectural change.

## Research Questions

**Q1: Why is the device absent on v2026.6.9 despite account login?**
SKU mismatch. Device SKU is `H5310`; the v2026.6.9 allowlist had only `H5301`. Census: `{"sku":"H5310","in_thermo_hygro_skus":false,...}`. Discovery ran (census populated) but the SKU filter rejected it.

**Q2: Is the device in the BFF list at all?**
Yes — sole entry. Developer API returns nothing for this account (`raw_api_devices: []`, `device_count: 0`), so the BFF path is the *only* way it can surface. Confirms the v2026.6.9 architecture is the right one.

**Q3: What is the H5310 / how is it bridged?**
H5310 = GoveeLife Smart Thermometer P2; H5044 = Smart Gateway 1s. The H5310 is an RF/BLE sensor bridged to cloud by the H5044 gateway. In the BFF entry it carries `sno: 0` + `gatewayInfo{sku:H5044}` — the same hub/slot shape as H5058/H5059 leak sensors, not a standalone WiFi device. (Corrects the prior doc's "WiFi-native H5301" assumption.)

**Q4: Does `lastDeviceData` carry usable readings + correct keys?**
Yes. Skeleton: `lastDeviceData._json_str = {online:bool, tem:int, hum:int, lastTime:int, avgDayTem:int, avgDayHum:int}`. `tem`/`hum` are centi-units (×100). `_bff_reading` already lists `tem`/`hum` first — matches. `deviceSettings` carries `battery`, `versionSoft/Hard`, `sno`, `gatewayInfo`, plus `fahOpen` (°F display flag) and `temCali`/`humCali` (calibration offsets).

**Q5: Does the centi-unit de-scaling hold for all readings?**
Mostly, but the v2026.6.9 heuristic (`int && abs>100 → /100`) breaks for 0–1 °C readings (e.g. 0.5 °C stored as `50` → not de-scaled → renders 50). Now that the shape is confirmed, de-scale the known centi keys (`tem`/`hum`) unconditionally.

## Findings

### F1 — SKU transposition is the whole bug (`H5310` ≠ `H5301`)
The census `in_thermo_hygro_skus: false` is conclusive: discovery reached the device and rejected it on SKU. Adding `H5310` to `THERMO_HYGRO_BFF_SKUS` (`models/device.py:26`) is sufficient for it to surface — the synthesis + entity path is unchanged and already validated by the v2026.6.9 tests.

### F2 — Developer API is empty for this account
`raw_api_devices: []`, `device_count: 0`, `leak_sensors: {}`. The integration's only device source for this user is the BFF list. Validates routing thermo-hygrometers through BFF rather than waiting on the Developer API. Also confirms the synthetic-device injection works with zero developer devices (no early-return path blocks it).

### F3 — Reading shape confirmed (centi-int `tem`/`hum`)
`deviceExt.lastDeviceData` (a JSON string, handled by the existing `isinstance(str) → json.loads`) = `{online, tem, hum, lastTime, avgDayTem, avgDayHum}`. `_bff_reading(ld, _BFF_TEMP_KEYS, 100.0)` / `_BFF_HUMIDITY_KEYS` extract `tem`/`hum` correctly. The 5-min BFF poll (`_refresh_bff_thermometers`) keeps them fresh; `lastTime` backs the existing reading-freshness sensor.

### F4 — De-scale heuristic edge case
`_bff_reading` divides by 100 only when `int && abs(value) > 100`. Readings in [-1.0, 1.0] °C (stored as -100..100) skip de-scaling and render ×100 too large. Fix: treat the Govee centi keys (`tem`, `hum`) as always-centi; keep plain fallbacks (`temperature`/`sensorTemperature`/...) unscaled. Removes the magnitude guess now that evidence exists.

### F5 — Future correctness hooks (not blocking)
`deviceSettings` exposes `fahOpen` (the device's °F display flag) and `temCali`/`humCali` (calibration). The integration already has `CONF_API_TEMPERATURE_UNIT` for the °F-without-metadata case; `fahOpen` could later auto-set it. Calibration offsets could be applied to match the app. Both are refinements, not required to surface the device.

## Compatibility Analysis

- **No new mechanism.** Reuses v2026.6.9 BFF discovery + synthetic thermometer + existing `GoveeTemperatureSensor`/`GoveeHumiditySensor`. Only the SKU set and one helper change.
- **Account login still required** (BFF needs email/password) — reporter already has it.
- **Hub bridging:** H5310 via H5044. The synthetic thermometer surfaces as its own HA device; optionally `via_device=(DOMAIN, hub)` later (mirrors leak-sensor hub registration) — cosmetic.
- **`tem`/`hum` JSON-string nesting** already handled by the v2026.6.9 parser.

## Recommendation

Ship a patch:
1. **Add `H5310`** to `THERMO_HYGRO_BFF_SKUS` (keep `H5301` — harmless, possibly real).
2. **De-scale confirmed centi keys unconditionally** — restructure `_bff_reading` so `tem`/`hum` always ÷100 (int), removing the `abs>100` heuristic; plain fallback keys stay unscaled.
3. Tests: H5310 BFF fixture (gateway-bridged shape with `sno`+`gatewayInfo`+centi `tem`/`hum`) → device synthesized, entities created, readings correct incl. a near-0 °C case.

| Option | Surfaces H5310 | Effort | Verdict |
|---|---|---|---|
| **Add H5310 + scale fix** | yes | one-SKU + helper | **Recommended** |
| Add H5310 only, keep heuristic | yes | one-SKU | works but keeps near-0 °C bug |
| Broaden to whole H53xx family speculatively | maybe | guesswork | rejected — only add evidenced SKUs |

## Implementation Sketch

1. `models/device.py:26` — `THERMO_HYGRO_BFF_SKUS = frozenset({"H5301", "H5310"})`.
2. `api/auth.py` `_bff_reading` — split centi keys from plain: divide by 100 for `tem`/`hum` (int) regardless of magnitude; return plain keys as-is. Update `_BFF_TEMP_KEYS`/`_BFF_HUMIDITY_KEYS` accordingly.
3. `tests/test_auth.py` — add H5310 fixture mirroring the diagnostics skeleton (`deviceExt` JSON-string → `deviceSettings`{battery,sno,gatewayInfo,versionSoft} + `lastDeviceData`{online,tem,hum,lastTime}); assert discovery + readings; add near-0 °C de-scale case to `TestBffReadingHelper`.
4. `tests/test_coordinator.py` — extend discovery test to an `H5310` SKU.
5. Bump `manifest.json` → `2026.6.10`; release.

## Risks

- **Negative / sub-1 °C readings:** the centi-unit fix is the mitigation; add an explicit test at -5 °C and 0.5 °C so a future refactor can't regress it. This is the one correctness trap in the data shape.
- **°F devices:** if `fahOpen` is true the app shows °F, but `tem` is still centi-°C in `lastDeviceData` (consistent with Govee's storage); the existing `CONF_API_TEMPERATURE_UNIT` covers any SKU that genuinely reports °F. Not changing default behavior here.
- **Single-account evidence:** the shape is confirmed from exactly one diagnostics dump. The keys are Govee-standard and match the prior `models/state.py` candidate list, so confidence is high, but a second user's dump would harden the centi-unit assumption for other H53xx SKUs before broadening the allowlist.

## Open Questions

1. Is `H5301` a real distinct SKU, or only the issue-title typo for `H5310`? (Keeping it is harmless; no evidence it exists.)
2. Should the H5310 register `via_device` under its H5044 hub for a cleaner device tree? (Cosmetic; defer.)
3. Do other H53xx P2/P3 thermo-hygrometers share the exact `tem`/`hum` centi shape? (Likely; confirm per-SKU before adding to the allowlist.)

## References

- Issue #86: https://github.com/lasswellt/govee-homeassistant/issues/86
- Reporter diagnostics (attachment): https://github.com/lasswellt/govee-homeassistant/issues/86#issuecomment (config_entry-govee-01KSKEPETH26D0NYQG9NDGAT0G.json)
- GoveeLife Smart Thermometer P2 (H5310) + Smart Gateway 1s (H5044) manual: https://manuals.plus/category/goveelife
- Govee smart thermo-hygrometers: https://us.govee.com/collections/smart-thermo-hygrometers
- Prior research: `docs/_research/2026-06-06_h5301-thermometer-surfacing.md`
- Codebase: `models/device.py:26`, `api/auth.py` `_bff_reading`/`fetch_bff_thermo_hygrometers`, `coordinator.py` `_discover_bff_thermometers`
