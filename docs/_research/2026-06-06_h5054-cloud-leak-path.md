<!-- no-registry: research informs a single bugfix/feature path for issue #62, not a multi-item roadmap -->
# H5054 Water-Leak Delivery Path — Cloud vs RF (issue #62)

Research: 2026-06-06. Type: Feature Investigation. Context: hacs-govee cloud integration shipped H5054 entity scaffolding (2026.6.6/.7); steamer70's debug log shows the entity never receives a trip. Question: how does the H5054 actually deliver a leak, and can this cloud integration receive it?

## Summary

The H5054 is a **433 MHz RF-only leaf device** — no WiFi, no BLE-IoT, `supports_iot=false`. Its leak event **never** appears on Govee's developer API v2.0 or AWS IoT account-topic MQTT, which is why the shipped MQTT-flat-key parse can never fire (confirmed by steamer70's log: no per-device MQTT topic, no account-topic message). Two real delivery paths exist: **(a) local RTL_433/SDR** (the community-standard, orthogonal to this integration), and **(b) the Govee account/BFF HTTP API** — `bff-app/v1/device/list` + `leak/rest/device/v1/warnMessage` — which homebridge-govee polls for H5054 and which **this integration already partially calls** (`auth.py:132`, `fetch_bff_leak_sensors`). Path (b) is the actionable fix: it requires the user's H5040 WiFi gateway (which steamer70 has, since his sensors reach the Govee app at all) and reuses the existing account Bearer token. The shipped MQTT-flat-key code is dead and should be removed.

## Research Questions

1. **What radio delivers an H5054 leak?** 433 MHz RF only (OOK/ASK, tri-bit encoded). No BLE, no WiFi on the sensor. rtl_433 decodes it as Protocol 192 (older HW) / 231 (newer HW, label "Number: 2"), both in rtl_433 master ≥ v23.11.
2. **Does the trip ever reach Govee's developer API / AWS IoT?** No. `govee2mqtt` marks H5054 `supports_iot=false`; no per-device MQTT topic; the developer API *lists* the device with a `bodyAppearedEvent` capability but never delivers its state (poll returns only `online`). The `bodyAppearedEvent` capability is a dead stub.
3. **How does the Govee app get notified then?** `H5054 --433MHz--> H5040 WiFi gateway --HTTPS--> Govee cloud --FCM/APNS--> app`. Proprietary push, not an IoT topic. Requires the H5040 gateway.
4. **Is there any pollable cloud endpoint with leak state?** Yes — the **account/BFF API** (`app2.govee.com`), not the developer API. homebridge-govee tags H5054 `http` and polls `bff-app/v1/device/list` (battery/online/gwonline/lastTime) + `leak/rest/device/v1/warnMessage` (unread `LeakageAlert`).
5. **Can this integration reuse it?** Yes, with the account token it already obtains for AWS IoT certs. It already calls `bff-app/v1/device/list` and parses `lastDeviceData`; it skips H5054 (no LoRa `sno`) and never calls `warnMessage`.
6. **What's the catch?** Account-API rate limits (homebridge issue #543: 17 sensors @30s exhausted it), gateway dependency, and no heartbeat (sensor transmits only on events).

## Findings

### F1 — H5054 is 433 MHz RF-only (rf-transmission.md)
H5040+H5054 bundle: H5040 is the WiFi gateway with a "433 Pairing Button". Independent RTL-SDR captures confirm 433 MHz; rtl_433 PR #1653 added Protocol 192. Tri-bit encoding; 6-byte payload; leak byte `0xfb`; last byte parity/CRC. No BLE/WiFi radio on the sensor. Sources: https://github.com/merbanan/rtl_433/pull/1653 , https://manuals.plus/govee/h5040h5054-water-detector-with-wifi-gateway-manual , https://github.com/wallacebrf/Govee-H5054-Leak-Detector

### F2 — No developer-API / AWS-IoT delivery (rf-transmission.md, ha-integration-paths.md)
`govee2mqtt` issue #378: H5054 = `supports_iot=false`, no IoT topic, "Unable to control". Matches steamer70's log exactly: `Device DABFC0D6A5FE0008E8 has no MQTT topic in response`; zero account-topic messages during a 4-min capture spanning a real trip. The shipped MQTT-flat-key parse (`_WATER_LEAK_MQTT_KEYS`) has no message to parse. Sources: https://github.com/wez/govee2mqtt/issues/378 , steamer70 debug log (issue #62)

### F3 — RTL_433/SDR is the community-standard HA path (ha-integration-paths.md)
HA community thread 412928 consensus: RTL_433 + MQTT is the only viable path. Caveat: rtl_433 MQTT autodiscovery exposes battery but **not** the `event` (leak/button) field by default — manual YAML or a community script (mekaneck/Govee-Water-Leak-Home-Assistant-Autodiscovery) required. Orthogonal to this integration (needs SDR hardware + separate add-on). Sources: https://community.home-assistant.io/t/govee-h5054-water-sensor/412928 , https://github.com/mekaneck/Govee-Water-Leak-Home-Assistant-Autodiscovery

### F4 — The account/BFF HTTP poll path (homebridge-http.md) ← actionable
homebridge-govee `constants.js:493` `sensorLeak: ['H5054','H5058','H5059']`, all `http`-only. Per 30 s tick:
1. `GET https://app2.govee.com/bff-app/v1/device/list` → per device: `deviceExt.deviceSettings`(JSON str)→`battery`; `deviceExt.lastDeviceData`(JSON str)→`lastTime`,`online`,`gwonline`.
2. If `lastTime > 0`: `POST https://app2.govee.com/leak/rest/device/v1/warnMessage` body `{"device":"<id-no-colons>","limit":50,"sku":"H5054"}` → array of `{read,message}`.
3. `leakDetected = any(!m.read and m.message.lower().replace(' ','').startswith('leakagealert'))`; `online = gwonline and online`.
Source: https://github.com/homebridge-plugins/homebridge-govee (`lib/connection/http.js`, `lib/platform.js` ~1458-1547, `lib/device/sensor-leak.js`)

### F5 — This integration already has 80% of path (b) (codebase)
`auth.py:132` `GOVEE_BFF_DEVICE_LIST_URL = "https://app2.govee.com/bff-app/v1/device/list"`; `fetch_bff_leak_sensors()` already GETs it with the account Bearer token + `appVersion: 7.4.10` + matching User-Agent, and already parses `lastDeviceData` → `online`/`gwonline`/`lastTime`/`read` (`auth.py:610-637`). **Gap 1:** H5054 is skipped at `auth.py:584-591` because it has no `sno` (the LoRa slot index used by H5058-on-hub). **Gap 2:** the `warnMessage` endpoint is never called — leak state for H5054 needs it (the existing `read`/`lastTime` proxy is for the H5058 shape).

### F6 — Gateway dependency + rate limits (homebridge-http.md)
`online = gwonline && online` → requires the H5040 gateway online. steamer70 necessarily has an H5040 (his RF-only sensors reach the cloud). Account-API rate limits are real: homebridge issue #543 — 17 sensors @30 s exhausted the cap. steamer70 has 10 H5054s → naïve 30 s polling = ~31k req/day, over the 10k/day budget. Mitigate: poll the leak path on a slower cadence and only `warnMessage` when `lastTime` advanced since last poll.

## Compatibility Analysis

- **Auth:** zero new auth. Reuses the account login (`GOVEE_LOGIN_URL`) + Bearer token already obtained for AWS IoT certs. Same host (`app2.govee.com`), same headers (`appVersion 7.4.10`, `clientId`, `User-Agent GoveeHome/7.4.10 ...`).
- **Existing code:** extends `fetch_bff_leak_sensors` / the BFF census already in `auth.py`. New constant `GOVEE_LEAK_WARN_URL = "https://app2.govee.com/leak/rest/device/v1/warnMessage"`.
- **Entity:** the shipped `GoveeWaterLeakBinarySensor` (moisture) + `state.water_leak` are reusable as the sink; or route H5054 through the existing `leak_sensors`/`leak_states` machinery (which already has battery/online/last-wet entities). The latter is cleaner — it gives battery + online + last-wet for free and matches the H5058 UX.
- **Gateway requirement:** account-API path only works when the user has an H5040. Users without a gateway must use RTL_433 (path a) — document this.
- **Rate limit:** the developer API budget (100/min, 10k/day) is tracked already; the account/BFF API is a *separate* host — confirm its limits empirically and default to a conservative leak-poll interval (≥ 60-120 s, event-gated).

## Recommendation

Implement **path (b): account/BFF poll**. Concretely:

| Step | Change | File |
|---|---|---|
| 1 | Stop dropping H5054: when `sno is None` but `sku in {H5054}`, treat as standalone gateway sensor (no `sno`, `hub_device_id` from gateway info if present) | `api/auth.py` |
| 2 | Add `GOVEE_LEAK_WARN_URL` + `fetch_leak_warnings(device_id, sku)` POST helper | `api/auth.py` |
| 3 | Per poll: for each H5054, if `lastTime > 0` and advanced, call warnMessage; set `is_wet = any unread LeakageAlert` | `coordinator.py` |
| 4 | Surface via existing leak-sensor entities (moisture + battery + online + last-wet) OR the shipped `water_leak` field | `binary_sensor.py` / `sensor.py` |
| 5 | **Remove** the dead `_WATER_LEAK_MQTT_KEYS` parse + the speculative MQTT flat-key code (no IoT topic exists) | `models/state.py` |
| 6 | Conservative leak-poll cadence + event-gating to respect account-API limits | `coordinator.py` |
| 7 | Repair/docs: H5054 needs an H5040 gateway for cloud; gateway-less users → RTL_433 | `repairs.py` / README |

Keep the shipped availability fix (2026.6.7) — correct regardless. Hold 2026.6.7 release or fold these changes in.

## Implementation Sketch

```python
# api/auth.py
GOVEE_LEAK_WARN_URL = "https://app2.govee.com/leak/rest/device/v1/warnMessage"

async def fetch_leak_warnings(self, device_id: str, sku: str, token: str) -> bool:
    """True if an unread LeakageAlert exists for a standalone H5054 (issue #62)."""
    body = {"device": device_id.replace(":", ""), "limit": 50, "sku": sku}
    headers = {...Bearer token + appVersion 7.4.10 + UA...}
    # POST GOVEE_LEAK_WARN_URL; data = res["data"] or []
    return any(
        not m.get("read", True)
        and str(m.get("message", "")).lower().replace(" ", "").startswith("leakagealert")
        for m in data
    )
```
```python
# api/auth.py fetch_bff_leak_sensors — replace the unconditional `sno is None: skip`
if sno is None:
    if sku == "H5054":            # standalone gateway sensor: no LoRa slot
        sensors.append({"device_id": device_id, "name": name, "sku": sku,
                        "hub_device_id": device_settings.get("gatewayInfo", {}).get("device", ""),
                        "sno": None, "battery": device_settings.get("battery"), ...})
        continue
    _LOGGER.debug("Leak sensor %s has no sno, skipping", name); continue
```
```python
# coordinator.py leak refresh — for H5054 sensors, gate warnMessage on lastTime advance
if sensor.sku == "H5054" and ld_last_time and ld_last_time > seen_last_time.get(did, 0):
    is_wet = await auth_client.fetch_leak_warnings(did, "H5054", token)
    self._leak_states[did] = replace(state, is_wet=is_wet, online=gwonline and online)
```

Cadence: run the H5054 leak check on its own interval (e.g. every 120 s, or only when `device/list` shows `lastTime` advanced) rather than each 60 s device poll, to stay under the account-API cap.

## Risks

- **Account-API rate limits are unquantified for this host.** homebridge issue #543 proves they exist (17 sensors @30s exhausted). steamer70's 10 sensors make this the primary risk. Mitigation: event-gate `warnMessage` on `lastTime` advancing, and use a conservative base interval (≥120 s). Monitor for 429s and back off; surface a repair if the cap is hit. This must be validated against steamer70's real account before shipping a default interval.
- **Gateway dependency.** Path (b) only works with an H5040. Gateway-less users get nothing from the cloud and must use RTL_433. The integration should detect `gwonline`/absence and not silently present a permanently-unknown entity — explain the requirement via a repair or entity attribute.
- **warnMessage shape is reverse-engineered from homebridge, not official.** Field names (`read`, `message`, `LeakageAlert` prefix) and the `device`-without-colons body are from homebridge source, not Govee docs — they may differ per firmware/region. Validate against steamer70's account before relying on the parse; log the raw `warnMessage` response at debug on first implementation.
- **Latency.** Polling (≥60-120 s) means leak detection lags the physical event by up to the interval — acceptable for a leak alert but worth stating; the local RTL_433 path is near-instant by contrast.
- **No heartbeat.** H5054 transmits only on events; a dead/offline sensor is invisible. `gwonline && online` partially covers this but battery-dead sensors may report stale-online.

## Open Questions

- Exact rate limit of `app2.govee.com` BFF/leak endpoints (req/min, req/day) — must be measured, not assumed. Determines the safe default poll interval.
- Does `warnMessage` return a clearable/auto-expiring state, or does `is_wet` latch until the user marks the alert read in the app? If it latches, the integration needs a reset strategy (time-based clear, or mark-read call).
- Does steamer70's `device/list` response carry `gatewayInfo.device` for the H5040 so we can model the gateway as a `via_device` hub (as the H5058 path does)? Needs a fresh BFF census dump.

## References

- rtl_433 H5054 Protocol 192 PR: https://github.com/merbanan/rtl_433/pull/1653
- rtl_433_ESP issue #72 (Protocol 231 missing): https://github.com/NorthernMan54/rtl_433_ESP/issues/72
- govee2mqtt `supports_iot=false`: https://github.com/wez/govee2mqtt/issues/378
- HA community thread (RTL_433 consensus): https://community.home-assistant.io/t/govee-h5054-water-sensor/412928
- HA RTL_433 full guide: https://community.home-assistant.io/t/add-govee-leak-detectors-to-home-assistant-using-docker-rtl-433-sdr-full-integration-guide/956012
- MQTT autodiscovery script: https://github.com/mekaneck/Govee-Water-Leak-Home-Assistant-Autodiscovery
- homebridge-govee Supported-Devices wiki (H5054=http): https://github.com/homebridge-plugins/homebridge-govee/wiki/Supported-Devices
- homebridge-govee source: https://github.com/homebridge-plugins/homebridge-govee (`lib/connection/http.js`, `lib/platform.js`, `lib/device/sensor-leak.js`, `lib/utils/constants.js`)
- H5040+H5054 manual: https://manuals.plus/govee/h5040h5054-water-detector-with-wifi-gateway-manual
- wallacebrf local Arduino project: https://github.com/wallacebrf/Govee-H5054-Leak-Detector
- Local code: `custom_components/govee/api/auth.py:132,499-637`; steamer70 debug log (issue #62)
