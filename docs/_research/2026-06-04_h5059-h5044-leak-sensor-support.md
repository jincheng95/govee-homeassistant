<!-- no-registry: single-issue bugfix (2 edited files), not a multi-item rollout -->
# Research: H5044 hub + H5059 water-leak-sensor support (issue #87)

Type: Feature Investigation / protocol reverse-engineering
Source data: diagnostics `config_entry-govee-01KST4Q1MR0YR9W20GPEQ9D3E6.json` (v2026.6.1) attached to [issue #87 comment 4607175283](https://github.com/lasswellt/govee-homeassistant/issues/87#issuecomment-4607175283)

## Summary

H5059 leak sensors behind an H5044 hub need **no new subsystem** — the leak pipeline (BFF discovery → `GoveeLeakSensor` model → `_sno_to_sensor_id` map → MQTT `multiSync` decode → `moisture` `binary_sensor`) already exists and works for H5058/H5054/H5055. The diagnostics prove H5059 fails on exactly **two defects**:

1. **Discovery SKU filter** — `LEAK_SENSOR_SKUS` (`models/device.py:17`) omits `"H5059"`, so `fetch_bff_leak_sensors` (`auth.py:562`) `continue`s past all 5 sensors → 0 discovered → empty `_sno_to_sensor_id` → every leak event drops as "unknown sensor". (H5044 already in `LEAK_HUB_SKUS`.)
2. **Wet-bit offset** — the `ee34` decoder (`mqtt.py:510`) reads `is_wet = raw[5] == 0x01`, but `raw[5]` is **battery** (`0x64`=100). The real probe-state bytes are `raw[14]` and `raw[16]`. Result: `is_wet` always `False`.

Recommendation: add `"H5059"` to `LEAK_SENSOR_SKUS` and change the wet decode to `raw[14] or raw[16]` (guarded by `len(raw) >= 17`). ~2-line core fix. The maintainer's stated hypothesis that the `0xEE 0x35` packet carries the wet alarm is **refuted by the data** (see Findings §3).

## Research Questions

1. **How does leak state reach the integration at runtime — poll or push?**
   Push only. State arrives as MQTT `multiSync` packets from the H5044 hub (`07:…`), decoded in `mqtt.py:_handle_multisync`. The Developer-API poll returns no live leak state. BFF poll (`_poll_bff_leak_state`) supplies battery/online, not real-time wet.

2. **Which packet/bytes carry the wet state?**
   `ee34` packet, bytes `[14]` and `[16]` (`0x01`=wet, `0x00`=dry). Both bytes track every LEAK↔CLEAR transition in the capture with 100% correlation. `raw[5]` (what the code reads today) is the battery percentage and is constant `0x64`. See decode table §2.

3. **How does a `(hub, slot)` event map to a specific H5059 device?**
   `multiSync` `raw[2]` = slot, which equals the BFF `sno` field. The coordinator already builds `_sno_to_sensor_id[(hub_device_id, sno)]` at discovery (`coordinator.py:626`). Diagnostics BFF census shows `sno` 0–4 present for all 5 H5059s with `gateway_sku: "H5044"`. Mapping auto-populates once discovery is unblocked (RQ1/defect-1).

4. **What is the `0xEE 0x35` packet the maintainer flagged as "the wet alarm"?**
   NOT the alarm. `ee35` fires only on the *clear* edge, with an all-zero payload (probe bytes `00`, battery byte `00`). No `ee35` appears at either leak edge. It is a clear/heartbeat digest. Building detection on it would never fire. See §3.

5. **Can per-probe `top`/`bot` be exposed (issue request)?**
   Partially. `raw[14]` and `raw[16]` are the two probes; `raw[15]=0x03` separates them. In this capture both probes always move together (user bridged both), so which offset is `top` vs `bot` is unresolved — exposing them as two raw attributes is safe; labeling them top/bot is an assumption pending a single-probe trip.

## Findings

### §1 — The leak subsystem is complete and SKU-gated

`fetch_bff_leak_sensors` (`auth.py:556-665`) parses BFF `data.devices[]`, reads `deviceExt.deviceSettings.sno` + `gatewayInfo.device`, and emits sensor dicts; the coordinator (`coordinator.py:582-641`) builds `GoveeLeakSensor` + `GoveeLeakSensorState` and the `_sno_to_sensor_id` map; `binary_sensor.py` exposes `GoveeLeakBinarySensor` (device_class `MOISTURE`), `GoveeLeakOnlineSensor`, `GoveeLeakHubOnlineSensor`. All gated by `LEAK_SENSOR_SKUS`/`LEAK_HUB_SKUS` (`models/device.py:17-18`):

```python
LEAK_SENSOR_SKUS = frozenset({"H5058", "H5054", "H5055"})   # ← no H5059
LEAK_HUB_SKUS = frozenset({"H5043", "H5044"})                # ← H5044 already present
```

`auth.py:562`: `if sku not in LEAK_SENSOR_SKUS: continue` drops all 5 H5059 devices → "Discovered 0 leak sensors and 0 hubs". The diagnostics `bff_device_census` confirms the data is present but flagged out:

```json
{ "sku": "H5059", "in_leak_sensor_skus": false, "in_leak_hub_skus": false,
  "has_sno": true, "sno": 4, "has_gateway_info": true, "gateway_sku": "H5044" }
```
(5 entries, `sno` 0–4, all `gateway_sku: "H5044"`.) — diagnostics `data.bff_device_census`

### §2 — `ee34` decode (proven from leak↔clear paired packets)

9 packets captured; user "tripped 2 sensors, one of them twice" = slot 0 once, slot 2 twice. `ee34` layout (20 bytes):

| off | LEAK | CLEAR | meaning |
|---|---|---|---|
| 0–1 | `ee 34` | `ee 34` | header (`0x34` = leak/dry report) |
| 2 | `00`/`02` | `00`/`02` | **sensor slot = BFF `sno`** |
| 5 | `64` | `64` | **battery % (100)** — what `mqtt.py:510` wrongly reads as wet |
| 9–12 | rising | rising | 4-byte big-endian epoch (`6a1f4a58` < `…5b` < `…b6`) |
| 14 | `01` | `00` | **probe state #1 (wet/dry)** |
| 15 | `03` | `03` | separator / probe mask |
| 16 | `01` | `00` | **probe state #2 (wet/dry)** |
| 19 | varies | varies | checksum (not a plain sum-mod-256; not needed) |

Correlation across all 9 packets — `b14`/`b16` flip together exactly on the LEAK/CLEAR edges, `b5` never changes:

```
ts        hdr   b2(slot) b5  b14 b15 b16  label
21:25:45 ee34   00       64  01  03  01   slot0 LEAK
21:25:49 ee34   02       64  01  03  01   slot2 LEAK
21:26:21 ee34   00       64  00  03  00   slot0 CLEAR
21:26:22 ee34   02       64  00  03  00   slot2 CLEAR
21:27:19 ee34   02       64  01  03  01   slot2 LEAK (2nd)
21:27:29 ee34   02       64  00  03  00   slot2 CLEAR (2nd)
```
— diagnostics `data.recent_multisync`

### §3 — `ee35` is a clear/heartbeat digest, NOT the wet alarm (contrarian)

Maintainer hypothesis (issue comment 4598427479): *"The real wet alarm is almost certainly that `0xEE 0x35` subtype."* The data refutes this:

```
21:26:21 ee35  b2=00 b5=00 payload=all-zero   (coincides with slot0 CLEAR)
21:26:21 ee35  b2=02 b5=00 payload=all-zero   (coincides with slot2 CLEAR)
21:27:29 ee35  b2=02 b5=00 payload=all-zero   (coincides with slot2 CLEAR #2)
```

`ee35` appears **only on clear edges**, never on either leak edge, and its payload is all-zero (no wet bit to read). The wet signal is fully present in `ee34` `b14`/`b16`. Implementing detection against `ee35` would never fire — this is the most important course-correction in the investigation. `ee35` is safe to keep logging as informational (likely a "return-to-normal"/periodic hub digest; `b4=0x03`).

### §4 — Residual assumption: `raw[2] == sno`

The model documents `sno: "maps to MQTT packet byte 2"` (`models/device.py`) and the existing H5058 path relies on it. Diagnostics are consistent (slots 0 & 2 tripped; BFF has `sno` 0–4) but do not *prove* the specific name↔slot pairing because the user didn't report which named sensors were tripped. Confidence: high (established contract, consistent data); validate by confirming the entity that lights up matches the physically tripped sensor.

## Compatibility Analysis

- Stack: HA custom component, Python 3.12+ (user on 3.14.2 / HA 2026.5.4 / HAOS 17.3). No new deps.
- No model/schema changes: `GoveeLeakSensor.sno`, `GoveeLeakSensorState.is_wet`, `_sno_to_sensor_id`, dispatcher signal `{DOMAIN}_leak_update`, and all three `binary_sensor` entity classes already exist.
- BFF parsing already handles the `_json_str`-wrapped `deviceSettings`/`gatewayInfo` shape (`auth.py:568-595`); the skeleton confirms `sno` + `gatewayInfo.device` live where the parser reads them.
- Integration complexity: **low** — additive SKU entry + offset correction. No migration, no breaking change.

## Recommendation

Ship both fixes together (sensor is useless with either alone):

1. `custom_components/govee/models/device.py:17`
   ```python
   LEAK_SENSOR_SKUS = frozenset({"H5058", "H5054", "H5055", "H5059"})
   ```
2. `custom_components/govee/api/mqtt.py` `_handle_multisync`, `0x34` branch — replace `is_wet = raw[5] == 0x01` and widen the length guard:
   ```python
   if raw[1] == 0x34 and len(raw) >= 17:
       # raw[14] / raw[16] = the two probe states (0x01 wet, 0x00 dry).
       # raw[5] is battery (0x64), NOT wet — see issue #87 diagnostics.
       is_wet = raw[14] == 0x01 or raw[16] == 0x01
   ```
   Update the docstring byte map (`mqtt.py:480-483`) accordingly.

Optional enhancement (issue requested per-probe attributes): expose `raw[14]`/`raw[16]` as `probe_1`/`probe_2` extra-state attributes on `GoveeLeakBinarySensor` (thread them through `event_data`/`GoveeLeakSensorState`). Defer top/bot labeling until a single-probe trip disambiguates the offsets.

## Implementation Sketch

1. `models/device.py:17` — add `"H5059"` to `LEAK_SENSOR_SKUS`.
2. `api/mqtt.py:_handle_multisync` — fix wet offset + length guard + docstring (above).
3. Tests:
   - `test_auth.py` — BFF fixture with an H5059 + `sno` + `gatewayInfo` → `fetch_bff_leak_sensors` returns 1 sensor mapped to its hub.
   - new/`test_coordinator.py` — feed the real `ee34` LEAK then CLEAR hex (`§2`) through the multiSync decode path; assert `leak_states[id].is_wet` toggles `True`→`False` and `_sno_to_sensor_id[(hub, 0)]` resolves.
   - regression: an `ee34` packet with `raw[5]=0x64` and `raw[14]=0x00` must decode `is_wet=False` (guards against re-reading battery).
4. Bump `manifest.json` → `2026.6.2`; comment on #87 asking @jeffarndt to confirm the named sensor that lights up matches the one physically tripped (closes the §4 assumption for a safety device).
5. Docs: add H5059/H5044 to `docs/govee-protocol-reference.md` `ee34` byte map.

## Risks

- **Safety device — false negatives are the failure that matters.** The fix rests on a single user's capture where both probes always moved together. Before treating detection as proven, get @jeffarndt to (a) confirm the correct named entity reports wet, and (b) ideally trip a single probe so `b14` vs `b16` (top/bot) is disambiguated. Until then, OR-ing the two probe bytes is the conservative choice (any probe wet → moisture on), which biases toward alerting rather than silence — the correct bias for a leak sensor.
- **`raw[2] == sno` (§4) is an inherited contract, not re-proven here.** If a future hub firmware reorders slots, mapping breaks silently (events log "unknown sensor"). The existing diagnostics ring buffer (`recent_multisync`) already makes this debuggable.
- **Checksum at `raw[19]` is unverified.** The decoder does not validate it, so a corrupt packet could in principle decode to a spurious state; low likelihood over MQTT (TCP/TLS), and not a regression from current behavior.
- **`ee35` left undecoded.** Acceptable — it carries no wet state. If a future capture shows `ee35` with a non-zero payload at a *leak* edge, revisit; current evidence says it only marks clears.

## Open Questions

- Which physical offset (`raw[14]` vs `raw[16]`) is the upper (`top`) vs lower (`bot`) probe? Needs a single-probe trip to resolve; until then per-probe attributes can only be labeled `probe_1`/`probe_2`.
- Does `ee35`'s `b4` (`0x03`) encode paired-sensor count or a subtype? Not needed for detection; note only.

## References

- [Issue #87 — H5044 Hub with H5059 water sensor support?](https://github.com/lasswellt/govee-homeassistant/issues/87)
- [Comment 4598427479 — maintainer's `ee35` wet-alarm hypothesis (refuted §3)](https://github.com/lasswellt/govee-homeassistant/issues/87#issuecomment-4598427479)
- [Comment 4607175283 — diagnostics JSON with `ee34`/`ee35` bytes + BFF census](https://github.com/lasswellt/govee-homeassistant/issues/87#issuecomment-4607175283)
- Diagnostics: `data.recent_multisync`, `data.bff_device_census`, `data.bff_response_skeleton`
- Code: `custom_components/govee/api/mqtt.py:475-560`, `api/auth.py:556-665`, `coordinator.py:582-641,762-790`, `models/device.py:17-18,822-840`, `binary_sensor.py`
- [GoveeLife H5044/H5059 user manual (product reference only; no protocol detail)](https://manuals.plus/goveelife/h5044-h5059-water-leak-detector-manual)
