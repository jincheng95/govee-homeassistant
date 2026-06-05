---
scope:
  topic: mqtt-push-control-latency
  type: feature-investigation
  date: 2026-06-04
  quantified:
    - claim: "MQTT-native control transport touches 3 files"
      count: 3
      unit: files
      items:
        - custom_components/govee/api/mqtt.py
        - custom_components/govee/coordinator.py
        - custom_components/govee/ble_passthrough.py
  status: proposed
---

# Research: MQTT Push Control Commands for Faster Light Reactions

## Summary

Govee's AWS IoT MQTT channel accepts **native control commands** (`turn`, `brightness`, `colorwc`) — not only the `ptReal` BLE-passthrough this integration currently publishes. Sending power/brightness/color over MQTT is ~50ms vs ~500ms–4s for REST `POST /router/api/v1/device/control`, and bypasses the REST 100/min · 10,000/day rate limits. Two mature community projects (`wez/govee2mqtt`, `bwp91/homebridge-govee`) ship native-MQTT control with **zero account-ban reports over 2+ years**. **Recommendation: implement MQTT-native control as an optimization transport slotted between the existing BLE-first and REST paths, REST always retained as fallback — but gate it behind transport-health confirmation, because the real UX win is reliability/rate-headroom, not perceived latency (HA optimistic state already masks REST latency).** Do NOT pursue the BLE-over-MQTT (ptReal opcode) path for power/brightness/color — native MQTT commands are simpler and avoid reverse-engineering BLE opcodes + XOR checksums.

## Research Questions

**Q1. Can Govee MQTT accept control commands, and in what format?**
Yes. Native commands, device-specific publish topic (`GD/...`), envelope `{"msg":{"cmd","data","cmdVersion","transaction","type":1}}`:
- Power: `{"cmd":"turn","data":{"val":1}}`
- Brightness: `{"cmd":"brightness","data":{"val":75}}` (1–100)
- Color: `{"cmd":"colorwc","data":{"color":{"r","g","b"},"colorTemInKelvin":0}}`
- Legacy fallback `color` (`cmdVersion:1`, RGB only) for older devices.
Source: `docs/govee-protocol-reference.md:831-920`; `wez/govee2mqtt`; `bwp91/homebridge-govee`.

**Q2. Latency MQTT vs REST?**
MQTT ~50ms (AWS IoT endpoint) vs REST 2–4s documented / ~500ms typical. ~40–80× on paper. **No published field measurement of device reaction time** — caveat: bottleneck may be device/cloud, not transport. `docs/govee-protocol-reference.md:35-41`.

**Q3. Does official Developer API v2.0 expose MQTT control? Rate-limit bypass?**
No — official `developer.govee.com` API is REST-only. AWS IoT MQTT is the undocumented app channel (reverse-engineered). MQTT control bypasses REST 100/min · 10,000/day limits (no documented MQTT rate limit). REST still needed for discovery + scene fetch.

**Q4. ptReal vs native — which path for power/brightness/color?**
Native (`turn`/`brightness`/`colorwc`). `ptReal` carries base64 BLE packets (`33 01`/`33 04`/`33 05 02` + XOR checksum) and is correct for features with no native command (music, DreamView, DIY scenes). Using ptReal for basic control would require building + maintaining BLE opcode encoders for no benefit. The codebase has **no** native-MQTT publish method yet — only `async_publish_ptreal` (`mqtt.py:573`).

**Q5. Device topic acquisition / stability?**
Per-device publish topics fetched from undocumented endpoint via `_fetch_device_topics()` (`coordinator.py:579`), cached in `self._device_topics`, lazily refreshed by `_ensure_device_topic()` (`coordinator.py:1338`). No TTL/versioning observed; publish returns False on missing topic → falls through to REST. Group devices (`device_id.isdigit()`): no evidence they support MQTT control.

## Findings

### F1 — Native MQTT control is documented and proven (not ptReal)
`docs/govee-protocol-reference.md:835-920` documents `turn`/`brightness`/`colorwc` with full envelopes, plus the `color` legacy fallback (try `colorwc`, watch `GA/` topic for state echo within 5s, fall back to `color`). `wez/govee2mqtt` (~350★, Rust, 2+yr) and `bwp91/homebridge-govee` (~586★, Node, official `aws-iot-device-sdk`) both publish these natively. App-version check applies only to REST login (status 454); MQTT commands carry no version header.

### F2 — Codebase already has 90% of the transport plumbing
- MQTT client + connection mgmt: `api/mqtt.py` (642L), publish via `self._client.publish(device_topic, json.dumps(payload))` (`mqtt.py:631`).
- Per-device topics resolved + cached: `coordinator.py:579,1338`, `self._device_topics`.
- Transport-health recording already keyed by transport name: `_record_transport_success/_failure(device_id, "mqtt")` used at `coordinator.py:885`.
- Inbound state echo confirmation path: `coordinator.py:860-888` (`update_from_mqtt` clears optimistic window, stamps `mqtt` health).
- Dispatch fork point: `async_control_device()` `coordinator.py:1175` already does BLE-first → REST; an MQTT branch slots in cleanly after the BLE block (~`:1209`).

**Missing piece is small**: a generic `async_publish_command(device_topic, cmd, data)` on `GoveeAwsIotClient` (generalize the `ptReal`-specific `async_publish_ptreal`), plus a `_try_mqtt_command()` dispatcher mirroring `_try_ble_command()`.

### F3 — Perceived-latency win is largely already captured by optimistic state (Dissent)
`web-researcher` (contrarian) argues the juice is not worth the squeeze: HA optimistic state already makes the UI feel instant on REST commands; the loudest community complaints (`homebridge-govee#844` H7016 "No Response") trace to **10s BLE-discovery timeouts**, not transport latency. Real benefits are (a) rate-limit headroom at 20+ devices and (b) faster *actual* device reaction + faster real-world confirmation echo — not the HA dashboard feel. This tempers the recommendation toward "opt-in optimization" rather than "default transport swap."

### F4 — Device quirks must be encoded
`docs/govee-protocol-reference.md:922-928`:
- H5080/H5083: power `val` is `17` (on) / `16` (off), not `1`/`0`.
- H6121: `cmdVersion:1` for status.
- Some older devices: only `color`, not `colorwc`.
A naive `turn:1` will silently fail on H5080/H5083.

## Compatibility Analysis

| Dimension | Fit |
|---|---|
| Python 3.12 / async | ✅ publish is `await self._client.publish(...)`, already async |
| Existing MQTT client | ✅ `GoveeAwsIotClient` connected when email/password + 2FA configured |
| Transport-health model | ✅ `"mqtt"` transport key already exists (`coordinator.py:885`) |
| Optimistic state | ✅ unchanged — applied after transport success, same as BLE/REST |
| Confirmation echo | ✅ device state echo on `GA/` topic already drives `_on_mqtt_state_update` |
| REST fallback | ✅ on publish-False or missing topic, fall through to `control_device()` |
| Rate limits | ✅ MQTT control removes power/brightness/color from REST 100/min budget |
| Gating | ⚠️ only active when IoT creds present (email/password + 2FA verified) — API-key-only installs unaffected, stay REST |
| Group devices | ⚠️ likely unsupported over MQTT — keep on REST (`device_id.isdigit()`) |
| Device quirks | ⚠️ H5080/H5083 power `16/17`, legacy `color` fallback need handling |

## Recommendation

Implement **MQTT-native control as a third transport tier**, ordered: local-BLE → MQTT-native → REST(fallback). Native `turn`/`brightness`/`colorwc`, NOT ptReal BLE opcodes.

| Option | Effort | Robustness | Verdict |
|---|---|---|---|
| **A. MQTT-native control** (`turn`/`brightness`/`colorwc`) | ~3 files, ~80 LOC | Proven by 2 projects; quirks well-documented | ✅ Recommended |
| B. BLE-over-MQTT (ptReal opcodes for power/bri/color) | +3 BLE builders + XOR + opcode RE | Reverse-engineered, fragile, no upside vs A | ❌ Reject |
| C. Do nothing (REST only) | 0 | optimistic state already hides latency | ⚠️ Valid if device count <20 and no reliability complaints |

Gate behind a config/options flag (default off initially) given the undocumented-protocol risk; promote to default after field validation on H601F + H5083 (quirk device) + one group device.

## Implementation Sketch

1. **`api/mqtt.py`** — generalize publish. Add:
   ```python
   async def async_publish_command(self, device_topic: str, cmd: str, data: dict) -> bool:
       if not self._connected or self._client is None or not device_topic:
           return False
       payload = {"msg": {"cmd": cmd, "data": data, "cmdVersion": 0,
                          "transaction": f"v_{int(time.time()*1000)}", "type": 1}}
       try:
           await self._client.publish(device_topic, json.dumps(payload))
           return True
       except Exception as err:
           _LOGGER.error("MQTT publish %s failed: %s", cmd, err); return False
   ```
   (Refactor `async_publish_ptreal` to call it with `cmd="ptReal"`.)

2. **`ble_passthrough.py`** (or new small `mqtt_control.py`) — typed helpers building `data` per command + quirks:
   - `PowerCommand` → `{"val": 17 if sku in {"H5080","H5083"} and on else (16 if quirk else int(on))}`
   - `BrightnessCommand` → `{"val": brightness_1_100}`
   - `ColorCommand` → `colorwc` `{"color":{r,g,b},"colorTemInKelvin":0}`, fallback `color` (`cmdVersion:1`) on no echo.

3. **`coordinator.py`** — insert MQTT branch in `async_control_device()` after BLE block (~`:1209`):
   ```python
   if self._mqtt_control_enabled and self._iot_client and self._iot_client.connected \
      and device_id in self._device_topics and not device.is_group:
       if await self._try_mqtt_command(device_id, device.sku, command):
           self._record_transport_success(device_id, "mqtt")
           self._apply_optimistic_update(device_id, command)
           self.async_set_updated_data(self._states)
           return True
       self._record_transport_failure(device_id, "mqtt", "publish_failed")
       # fall through to REST
   ```
   `_try_mqtt_command()` accepts `PowerCommand | BrightnessCommand | ColorCommand`; all others return False → REST. ColorTemp/Scene/Segment stay REST.

4. **Confirmation**: rely on existing `GA/` echo → `_on_mqtt_state_update` (`coordinator.py:860`). Optional: watch for echo within 5s before declaring success (matches govee-cloud `colorwc`→`color` fallback). Optimistic-window expiry already covers a missing echo.

5. **Tests**: add `test_mqtt_control.py` — publish payload shape per command, H5083 `16/17` power quirk, `colorwc`→`color` fallback, missing-topic → REST fallthrough, group-device skip.

## Risks

- **Undocumented-protocol breakage (HIGH).** Format is reverse-engineered; a Govee firmware/server change can alter or reject it with no warning. `homebridge-govee` carries ongoing device-quirk issues. *Mitigation:* REST fallback always retained; log MQTT failure rate; feature flag to disable; alert if drop rate >5%.
- **Silent command drop / QoS 0 (MEDIUM).** Publishes are fire-and-forget, no ACK. Optimistic state can mask a dropped command. *Mitigation:* gate "success" on the `GA/` state echo (5s window) before suppressing the REST retry; otherwise treat as failure and fall through.
- **Account action (LOW–MEDIUM).** No bans reported across `govee2mqtt`/`homebridge-govee` in 2+ yr on the same endpoint the Govee app uses; still undocumented. *Mitigation:* opt-in flag; identical traffic profile to existing `ptReal` publishes already shipped.
- **Device-quirk failures (MEDIUM).** H5080/H5083 power `16/17`; older devices need legacy `color`. A naive implementation fails silently on these. *Mitigation:* encode quirks (F4); echo-confirmation catches the silent failures.
- **Group devices (LOW).** Likely unsupported over MQTT. *Mitigation:* skip via `device.is_group` → REST.

## Open Questions

- Actual device-reaction latency over MQTT vs REST is unmeasured in the field — the ~50ms figure is endpoint latency, not confirmed end-to-end device response. Worth a measurement on H601F before committing to "default on."
- Does the device-specific publish topic differ in scheme between this integration's `_fetch_device_topics()` output and govee2mqtt's `GD/{uuid}` convention? Verify the cached topic string format before first publish.
- Existing `async_publish_ptreal` payload nests `device`+`sku` inside `data`; reference native commands omit them (topic is device-scoped). Confirm which the server requires for native `turn`/`brightness`.

## References

- `docs/govee-protocol-reference.md:35-41` — latency table (REST 2-4s, MQTT ~50ms, LAN <10ms)
- `docs/govee-protocol-reference.md:831-928` — native MQTT command formats, envelope, variants, device quirks
- `custom_components/govee/api/mqtt.py:573-642` — `async_publish_ptreal` (publish surface to generalize)
- `custom_components/govee/coordinator.py:1175` — `async_control_device` dispatch fork; `:579,1338` device topics; `:860-888` MQTT state echo
- `wez/govee2mqtt` — https://github.com/wez/govee2mqtt (native `turn`/`brightness`/`colorwc` over MQTT)
- `bwp91/homebridge-govee` — https://github.com/homebridge-plugins/homebridge-govee (native MQTT control, official aws-iot-device-sdk)
- `homebridge-govee#844` — https://github.com/homebridge-plugins/homebridge-govee/issues/844 ("No Response" = 10s BLE discovery timeout, not transport latency)
- `docs/_research/2026-03-30_govee-aws-iot.md` (this repo) — Finding 3: "We Only Send ptReal Commands via MQTT"
