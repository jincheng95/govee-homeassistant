---
title: 30-Day Issue Sweep — Protocol & Behavior Findings
date: 2026-06-30
scope:
  window: 2026-06-01 .. 2026-07-01
  issues_reviewed: 36
  purpose: >
    Consolidated, machine-checked findings from every open/closed issue touched in
    the last 30 days. Feeds docs/govee-protocol-reference.md and the README feature list.
---

# 30-Day Issue Sweep — Protocol & Behavior Findings

Generated 2026-06-30 from a fan-out review of the 36 issues updated between 2026-06-01
and 2026-07-01 (open + closed). Each entry captures the concrete Govee protocol/API
behavior learned, verbatim JSON payloads where the issue documented a wire shape, the
resolution + shipping version, and the user-facing feature (if any).

## Summary

| # | State | Category | SKU(s) | Feature shipped |
|---|-------|----------|--------|-----------------|
| [#125](https://github.com/lasswellt/govee-homeassistant/issues/125) | CLOSED | support-request | H5179, H5110, H5106 | Sensor battery readings preserved across cloud polls (no more flicker to unknown) and suppressed for mains-powered devices reporting bogus values (v2026.6.24). |
| [#124](https://github.com/lasswellt/govee-homeassistant/issues/124) | OPEN | bug | H5127, H5054, H6195 | H5127 mmWave presence sensor now classified as an Occupancy binary sensor (device_class: occupancy) with live present/absent updates from MQTT `state.triSta`, instead of being misclassified as a water-leak/moisture sensor. |
| [#123](https://github.com/lasswellt/govee-homeassistant/issues/123) | CLOSED | bug | H5127, H5054, H5059 | — |
| [#122](https://github.com/lasswellt/govee-homeassistant/issues/122) | CLOSED | api-limitation | H616C | — |
| [#121](https://github.com/lasswellt/govee-homeassistant/issues/121) | OPEN | api-limitation | H7107 | — |
| [#120](https://github.com/lasswellt/govee-homeassistant/issues/120) | CLOSED | bug | H7106 | Fixed duplicate fan preset modes and non-working preset speeds on H7106 fans (also unblocks HomeKit export). |
| [#118](https://github.com/lasswellt/govee-homeassistant/issues/118) | OPEN | bug | H7150, H7152 | H7150/H7152 dehumidifier Water Tank Full sensor now reflects real state, sourced from account data deviceSettings.waterFull (requires email/password). |
| [#117](https://github.com/lasswellt/govee-homeassistant/issues/117) | CLOSED | feature | H5140 | H5140: CO2 (ppm) sensor via the `carbonDioxideConcentration` property. |
| [#116](https://github.com/lasswellt/govee-homeassistant/issues/116) | CLOSED | bug | H5106, H5140 | H5106 and H5140 air-quality/CO2 monitors now report correct temperature (Fahrenheit readings auto-converted). |
| [#115](https://github.com/lasswellt/govee-homeassistant/issues/115) | CLOSED | bug | H717A | H717A kettle temperature now auto-converts from Fahrenheit to Celsius (fixes mislabeled temperature readings). |
| [#114](https://github.com/lasswellt/govee-homeassistant/issues/114) | OPEN | feature | H1310, H1370, H7152, H5106, H5089, H7126, H7124, H5220, H5110, H5044, H5126, H5129, H5151 | Added H1310/H1370 Main+Background light switches, H7152 dehumidifier Medium mode + correct humidity setpoint, H5106/H7124/H7126 AQI sensors, H5089 per-outlet switches + Night Light, H7124 Sleep/Auto/Turbo presets, device Snapshots, and suppressed phantom battery on mains-powered devices. |
| [#105](https://github.com/lasswellt/govee-homeassistant/issues/105) | CLOSED | feature | H1370, H1310 | Added support for the H1370 light/fan combo, including the oscillation toggle (v2026.6.14). |
| [#104](https://github.com/lasswellt/govee-homeassistant/issues/104) | CLOSED | bug | H60B2 | Per-zone on/off switches ("Light Zone 1/2/3") for multi-zone lamps like the H60B2 (light1/2/3Toggle capabilities). |
| [#102](https://github.com/lasswellt/govee-homeassistant/issues/102) | CLOSED | bug | H5110, H5179, H5220, H5044 | Fixed H5110/H5179 thermo-hygrometer humidity reading 10× too high on alternating updates (BFF is now tickle-only; value comes from the correctly-scaled Developer API poll). |
| [#101](https://github.com/lasswellt/govee-homeassistant/issues/101) | OPEN | feature | H5059, H5044 | New devices added to your Govee account after HA starts — including leak sensors added to a hub — are now auto-discovered within ~5 min and appear without a manual reload. |
| [#100](https://github.com/lasswellt/govee-homeassistant/issues/100) | CLOSED | api-limitation | H5310, H5044, H5075 | — |
| [#99](https://github.com/lasswellt/govee-homeassistant/issues/99) | CLOSED | feature | H7161 | Added support for the H7161 aroma diffuser (power switch + preset light/mist scene selector). |
| [#98](https://github.com/lasswellt/govee-homeassistant/issues/98) | CLOSED | bug | H6117, H6163 | MQTT state messages now decode tolerantly, so a stray non-UTF-8 byte in a device or scene name no longer drops the whole state update. |
| [#97](https://github.com/lasswellt/govee-homeassistant/issues/97) | CLOSED | feature | H5310, H5044 | Support for the H5310 pool thermometer (temperature + battery sensors), bridged via the H5044 gateway and nested under it in the device tree. |
| [#96](https://github.com/lasswellt/govee-homeassistant/issues/96) | CLOSED | bug | H5109, H5179, H5110, HS5108, HS5106 | Thermometers that report temperature in Fahrenheit (H5109, H5179, H5110, HS5108, HS5106) now auto-convert to the correct reading by default via a new `auto` temperature-unit mode. |
| [#95](https://github.com/lasswellt/govee-homeassistant/issues/95) | CLOSED | api-limitation | H5103 | — |
| [#93](https://github.com/lasswellt/govee-homeassistant/issues/93) | CLOSED | bug | H5109 | Poll-only devices without MQTT push (e.g. H5109 thermometer) now refresh state on every poll instead of freezing after the initial load. |
| [#92](https://github.com/lasswellt/govee-homeassistant/issues/92) | CLOSED | support-request | H5075 | — |
| [#91](https://github.com/lasswellt/govee-homeassistant/issues/91) | CLOSED | support-request | H5107 | — |
| [#88](https://github.com/lasswellt/govee-homeassistant/issues/88) | CLOSED | support-request | H5075, H5151 | — |
| [#87](https://github.com/lasswellt/govee-homeassistant/issues/87) | CLOSED | feature | H5059, H5044 | Added H5059 water leak sensor support (moisture binary_sensor) when paired to an H5043/H5044 hub, with real-time wet/dry detection via hub MQTT multiSync packets. |
| [#86](https://github.com/lasswellt/govee-homeassistant/issues/86) | CLOSED | feature | H5310, H5301, H5044, H5054 | Added H5310 (and H5301) pool/thermo-hygrometer support via the account BFF device list — temperature + battery sensors, humidity suppressed for temp-only probes, nested under the H5044 gateway. |
| [#85](https://github.com/lasswellt/govee-homeassistant/issues/85) | CLOSED | api-limitation | H5103 | — |
| [#84](https://github.com/lasswellt/govee-homeassistant/issues/84) | CLOSED | feature | H60A6 | Support toggling the ambient/backlight ring on Ceiling Light Pro (H60A6) via the backgroundLightToggle capability |
| [#83](https://github.com/lasswellt/govee-homeassistant/issues/83) | CLOSED | bug | H5179, H5110, H5075, H5151, H5111 | BLE-bridged Govee thermometers (H5075/H5110/H5179 via H5151 gateway) now refresh temp/humidity on a ~5-min cadence via a BFF API tickle, expose a Battery sensor from BFF deviceSettings, show a "Last Changed" staleness timestamp, and no longer stop updating when a device goes offline. |
| [#74](https://github.com/lasswellt/govee-homeassistant/issues/74) | CLOSED | feature | H1310 | Added H1310 ceiling fan support: a dedicated Fan entity (alongside the light) with on/off, 6-speed control, and forward/reverse direction. |
| [#70](https://github.com/lasswellt/govee-homeassistant/issues/70) | CLOSED | feature | — | — |
| [#62](https://github.com/lasswellt/govee-homeassistant/issues/62) | CLOSED | feature | H5054, H5109, H5040, H5042, H5083 | Added H5109 temperature/humidity sensors and H5054 water-leak detectors (leak state via account/BFF warnMessage polling through the H5040 gateway). |
| [#57](https://github.com/lasswellt/govee-homeassistant/issues/57) | OPEN | feature | H6072, H6022, H619A, H6175, H618A, H6076, H7093, H60A1, H707B | Native Govee LAN API local control with automatic multicast + subnet-broadcast discovery, read-driven per-device LAN transport health, and LAN → MQTT → REST fallback. |
| [#48](https://github.com/lasswellt/govee-homeassistant/issues/48) | CLOSED | feature | H6604, H605C, H6199 | Setting the scene select to None on a Sync Box (device with hdmiSource capability) now re-selects the current HDMI source to return it to Video Mode instead of a flat-white color. |
| [#24](https://github.com/lasswellt/govee-homeassistant/issues/24) | CLOSED | bug | H6104, H6159 | Clamp brightness conversion so devices reporting brightness outside 0-100 (e.g. H6104 max=254) no longer display over 100%. |

## Findings

### #125 — Battery status on sensors

- **State:** CLOSED · **Category:** support-request
- **SKUs:** H5179, H5110, H5106
- **Device types:** sensor, leak-sensor, thermo-hygrometer
- **Protocol area:** bff-internal-api, state-management

**Finding.** Battery level for Govee battery-powered sensors is NOT available from the Developer API key alone — it is sourced from Govee's account device list (the same BFF/account source used by leak and thermo/hygrometer sensors), so it requires email/password to be configured in the integration options. With only an API key configured there is no battery field at all. The battery value from the account list is intermittent/flaky: it could previously flicker to "unknown" between account refreshes, so it must be preserved (optimistic/last-known state) across cloud polls. Additionally, some mains-powered devices report a bogus constant battery value in the account data and must have battery suppressed. SKUs H5179/H5110/H5106 were cited by the maintainer as example battery-powered sensor SKUs to check for a battery field in the account data (not confirmed as the reporter's specific device).

**Feature.** Sensor battery readings preserved across cloud polls (no more flicker to unknown) and suppressed for mains-powered devices reporting bogus values (v2026.6.24).

**Resolution.** Shipped in v2026.6.24: battery reading is now preserved across cloud polls (fixes flicker to "unknown" between account refreshes) and is suppressed for mains-powered devices that report a bogus constant value. Requires email/password configured (not API-key-only). Reporter confirmed battery working after updating; issue closed.

---

### #124 — H5127 presence sensor misclassified as moisture/water leak sensor

- **State:** OPEN · **Category:** bug
- **SKUs:** H5127, H5054, H6195
- **Device types:** devices.types.sensor
- **Protocol area:** device-profile, mqtt, platform-api, state-management, bff-internal-api

**Finding.** The Govee H5127 (mmWave WiFi presence sensor) reports device_type `devices.types.sensor` and advertises the SAME `bodyAppearedEvent` event capability that the H5054 water-leak detector uses, so capability-only detection (`supports_water_leak_event`) cannot distinguish presence sensors from leak detectors. Discriminators present in the raw capability: (1) H5127's `bodyAppearedEvent` carries `alarmType: 50` and `eventState.options` of Presence=1 / Absence=2; (2) device_type is `devices.types.sensor` (no existing constant, unused by water detectors). CRITICAL follow-up: the H5054 also advertises the same `eventState` option shape (Presence/Absence 1/2), so keying detection on option values/shape mis-classified the real H5054 as a presence sensor and removed its Water Leak entity — detection had to be SKU-locked to H5127. Live presence/absence is delivered over MQTT (NOT the Developer /device/state poll, which only returns `online` for this SKU): the H5127 pushes a `cmd: status` message whose `state.triSta` field = 1 (Present) / 0 (Absent), confirmed against the Govee app journal. The generic MQTT state parser only extracted `power`/`brightness` and ignored `triSta`, so the occupancy entity stayed `unknown` until a `triSta` handler was added. BFF census shows H5127 is a direct-WiFi device: in_leak_sensor_skus=false, in_leak_hub_skus=false, has_sno=false, has_gateway_info=false. Battery % for H5054-class sensors is only available from the account/BFF device list (requires email/password); the API-key-only method has no battery data.

**Feature.** H5127 mmWave presence sensor now classified as an Occupancy binary sensor (device_class: occupancy) with live present/absent updates from MQTT `state.triSta`, instead of being misclassified as a water-leak/moisture sensor.

**Resolution.** Two-part fix. v2026.6.24: H5127 no longer classified as water-leak/moisture; creates an Occupancy binary sensor (device_class: occupancy) and is no longer polled against the leak `warnMessage` history endpoint. v2026.6.25: (a) presence-vs-leak detection SKU-locked to H5127 — v2026.6.24 had keyed on the `bodyAppearedEvent` eventState option shape, which the real H5054 also advertises, so the H5054 lost its Water Leak entity; SKU-locking restores it; (b) MQTT parser now reads `state.triSta` (1=present, 0=absent) and maps it to the Occupancy entity for live tracking. Battery for H5054 requires email/password (BFF) — API-key-only has no battery data. Issue left OPEN pending user confirmation that occupancy tracks live and H5054 Water Leak entities return.

_H5127 raw_api_devices capability payload (Platform API) — bodyAppearedEvent with alarmType 50 and Presence/Absence eventState_

```json
{
  "sku": "H5127",
  "type": "devices.types.sensor",
  "capabilities": [
    {
      "type": "devices.capabilities.event",
      "instance": "bodyAppearedEvent",
      "alarmType": 50,
      "eventState": {
        "options": [
          { "name": "Presence", "value": 1 },
          { "name": "Absence", "value": 2 }
        ]
      }
    }
  ]
}
```

_H5127 BFF census — direct-WiFi, not a leak/hub/LoRa device_

```json
{
  "sku": "H5127",
  "in_leak_sensor_skus": false,
  "in_leak_hub_skus": false,
  "has_sno": false,
  "has_gateway_info": false
}
```

_H5127 MQTT inbound status push — presence carried in state.triSta (1=present, 0=absent)_

```json
{
  "sku": "H5127",
  "device": "[MAC]",
  "cmd": "status",
  "state": {
    "triSta": 1,
    "sta": { "stc": "19_0_35_150940" },
    "result": 1
  },
  "op": { "command": ["[...]"] }
}
```

---

### #123 — H5127 presence sensor misclassified as moisture/water leak sensor

- **State:** CLOSED · **Category:** bug
- **SKUs:** H5127, H5054, H5059
- **Device types:** devices.types.sensor
- **Protocol area:** device-profile, platform-api, bff-internal-api

**Finding.** The Govee H5127 mmWave WiFi Presence Sensor reuses the SAME capability that water/leak detectors use to signal a trip: devices.capabilities.event / instance "bodyAppearedEvent". "bodyAppearedEvent" is a generic "body appeared in range" event, not leak-specific, so a purely capability-based check (supports_water_leak_event) cannot distinguish a presence sensor from a water detector and misclassifies H5127 into the water-detector path (binary_sensor device_class=moisture, polled against the warnMessage leak-history endpoint which returns nothing useful). Two reliable discriminators exist in the raw API capability payload: (1) device_type — H5127 reports "devices.types.sensor" (no existing constant; not used by any water detector), and (2) alarmType — the H5127 bodyAppearedEvent carries alarmType: 50 with eventState.options of Presence(value 1)/Absence(value 2), whereas leak events use a different alarmType. H5127 is a direct WiFi device (BFF census: in_leak_sensor_skus=false, in_leak_hub_skus=false, has_sno=false, has_gateway_info=false — no hub, no LoRa, not a BFF-path sensor). The device also reports distance in feet (e.g. "8.6 Feet") but the API state field name for distance/presence was not captured in this issue. Correct entities would be a binary_sensor device_class=occupancy for present/absent plus a distance sensor.

**Resolution.** Issue is CLOSED. Root cause traced to supports_water_leak_event in models/device.py (~line 506) being purely capability-based on INSTANCE_BODY_APPEARED_EVENT. Two fixes proposed: Option A — a PRESENCE_SENSOR_SKUS = frozenset({"H5127"}) guard that short-circuits supports_water_leak_event and adds supports_presence_sensor (same pattern as the prior H5059 fix); Option B (more robust) — exclude alarmType == 50 in supports_water_leak_event so no SKU list is needed. Either path also needs a DEVICE_TYPE_SENSOR = "devices.types.sensor" constant, a GoveePresenceBinarySensor (device_class=occupancy) in binary_sensor.py, a distance sensor in sensor.py, and a presence_detected: bool|None field in GoveeDeviceState. No shipped fix version is referenced in the issue/comments (only comment is a repost-from-wrong-account note); the presence/distance MQTT state field names remain uncaptured.

_raw_api_devices capability payload for H5127 (devices.capabilities.event / bodyAppearedEvent, alarmType 50, Presence/Absence eventState)_

```json
{
  "sku": "H5127",
  "type": "devices.types.sensor",
  "capabilities": [
    {
      "type": "devices.capabilities.event",
      "instance": "bodyAppearedEvent",
      "alarmType": 50,
      "eventState": {
        "options": [
          { "name": "Presence", "value": 1 },
          { "name": "Absence", "value": 2 }
        ]
      }
    }
  ]
}
```

_BFF census for H5127 (direct WiFi, non-BFF leak path)_

```json
{
  "sku": "H5127",
  "in_leak_sensor_skus": false,
  "in_leak_hub_skus": false,
  "has_sno": false,
  "has_gateway_info": false
}
```

---

### #122 — Issue adding H616C LED light strip

- **State:** CLOSED · **Category:** api-limitation
- **SKUs:** H616C
- **Device types:** light, led-strip, rgb
- **Protocol area:** platform-api, mqtt, bff-internal-api

**Finding.** H616C (WiFi RGB LED strip, pactType 2 / pactCode 1 / proType 2) is NOT exposed by the Govee v2 Developer API. GET https://openapi.api.govee.com/router/api/v1/user/devices returns an empty data array for the key (confirmed both via the integration log "Fetched 0 devices from Govee API" and a direct curl). Govee support confirmed in writing the SKU is not yet configured in their Developer API backend and will be added in app version 7.5.3 (no firm release date). Consequence: REST discovery yields 0 devices, so no light entity is ever created; the integration is behaving correctly. Cross-transport note: the same H616C IS reachable over the other two channels while absent from the Developer REST list. (1) AWS IoT MQTT: the device has a topic (GD/... push / GA/... subscribe) and pushes full cmd:"status" state frames (onOff, brightness, color{r,g,b}, colorTemInKelvin, mode, sta.stc string, result, plus base64 op.command frames), as well as cmd:"ptIotOp", cmd:"ptReal", and cmd:"v1/gw-group/sub-config" acks. Because the coordinator builds its device map from the Developer REST API, every one of these pushes is logged as "MQTT update for unknown device" and dropped. Observed modes: mode:3 and mode:1; onOff 0/1; brightness 100. (2) BFF internal API census/deviceSettings returns the H616C (deviceSettings with wifi/version fields + lastDeviceData.online=false). General pattern documented here: a SKU can be present in MQTT topic-fetch and BFF census while completely missing from the Developer REST device list, and MQTT-only presence is insufficient for entity creation given the current REST-driven coordinator design.

**Resolution.** Not a code bug — closed as a Govee platform/API limitation. Govee support confirmed H616C is not configured in the Developer API backend yet; support slated for Govee app v7.5.3 (no date). No integration change shipped (running 2026.6.22). User to retry once Govee enables the SKU. Reporter also had LAN disabled at startup ("could not bind, LAN disabled: [Errno 98] Address in use") but that was incidental, not the cause.

_MQTT AWS IoT cmd:status push for H616C (topic GA/...)_

```json
{"proType":2,"sku":"H616C","device":"[MAC]","softVersion":"1.02.12","wifiSoftVersion":"1.03.08","wifiHardVersion":"1.04.01","cmd":"status","type":0,"transaction":"[TXN]","pactType":2,"pactCode":1,"state":{"onOff":0,"brightness":100,"color":{"r":255,"g":0,"b":0},"colorTemInKelvin":0,"mode":3,"sta":{"stc":"0_0_37_97839_-703_0_0_400004_34"},"result":1},"op":{"command":["qgUKhQAAAAAAAAAAAAAAAAAAACA=","qhEAHg8PAP8yAAAAAAAAAAAAAGg=","qhL/ZAAAgAoA/65UAAAAAAAAAKw=","qiP/AA..."]}}
```

_MQTT cmd:ptIotOp ack (base64 op.command only)_

```json
{"proType":2,"sku":"H616C","device":"[MAC]","cmd":"ptIotOp","type":2,"transaction":"[TXN]","pactType":2,"pactCode":1,"state":{"result":1},"op":{"command":["6gEB6g=="]}}
```

_MQTT cmd:v1/gw-group/sub-config ack (no op)_

```json
{"proType":2,"sku":"H616C","device":"[MAC]","cmd":"v1/gw-group/sub-config","type":1,"transaction":"[TXN]","pactType":2,"pactCode":1,"state":{"result":1}}
```

_BFF deviceSettings + lastDeviceData values for H616C_

```json
{"sku":"H616C","deviceSettings":{"appVersion":"7.5.20","address":"[REDACTED]","supportEnc":true,"bleName":"[REDACTED]","pactType":2,"wifiSoftVersion":"1.03.08","secretCode":"[REDACTED]","language":"en","wifiHardVersion":"1.04.01","wifiName":"[REDACTED]","ic":40,"wifiMac":"[MAC]","matterId":"[REDACTED]","pactCode":1,"boilWaterCompletedNotiOnOff":1,"completionNotiOnOff":1,"autoShutDownOnOff":1,"deviceName":"[NAME]","sku":"H616C","device":"[MAC]","versionHard":"3.05.00","versionSoft":"1.02.12","topic":"[REDACTED]","playState":false,"wifiFuncList":""},"lastDeviceData":{"online":false}}
```

_BFF device census entry for H616C (diagnostics)_

```json
{"sku":"H616C","in_leak_sensor_skus":false,"in_leak_hub_skus":false,"in_thermo_hygro_skus":false,"has_sno":false,"sno":null,"has_gateway_info":false,"gateway_sku":null}
```

---

### #121 — Add H7107 Temperature & Light Sensors

- **State:** OPEN · **Category:** api-limitation
- **SKUs:** H7107
- **Device types:** devices.types.fan
- **Protocol area:** device-profile, platform-api

**Finding.** H7107 tower fan (device_type devices.types.fan) exposes ONLY three capabilities over the Govee Developer API: devices.capabilities.on_off/powerSwitch, devices.capabilities.toggle/oscillationToggle, and devices.capabilities.work_mode/workMode. workMode is a STRUCT with workMode ENUM (FanSpeed=1, Auto=2, Sleep=3, Nature=4, Custom=5) and modeValue ENUM whose speed range is 1-12 for FanSpeed/Sleep/Nature (Auto and Custom carry defaultValue 0, no range). The built-in room temperature sensor, night light, and display-indicator light that appear in the Govee mobile app are NOT present in the Developer API device profile — per maintainer they are only reachable over BLE / a private channel, so a temperature sensor and separate light entities cannot be created from the cloud API. Existing fan entity already covers power, oscillation, and the speed/preset work modes. raw_api_state confirms the state shape: work_mode is reported as a nested value object {"workMode":1,"modeValue":8}; online/powerSwitch/oscillationToggle return scalar values. Device on this account had MQTT unavailable (has_iot_credentials false, transport mqtt not_configured) and no LAN presence; only cloud_api transport was healthy. Reported on integration version 2026.6.23.

**Resolution.** No code shipped. Maintainer left the issue OPEN as a tracking item: the temperature sensor and the two lights cannot be implemented because the Govee Developer API does not return those capabilities for the H7107 (only on_off, oscillationToggle, workMode). To be revisited if Govee later adds the capabilities to the API (re-download diagnostics periodically) or if a BLE path for the H7107 is reverse-engineered.

_H7107 device profile capabilities (Govee Developer API)_

```json
{
  "sku": "H7107",
  "device_type": "devices.types.fan",
  "is_group": false,
  "capabilities": [
    {
      "type": "devices.capabilities.on_off",
      "instance": "powerSwitch",
      "parameters": {"dataType": "ENUM", "options": [{"name": "on", "value": 1}, {"name": "off", "value": 0}]}
    },
    {
      "type": "devices.capabilities.toggle",
      "instance": "oscillationToggle",
      "parameters": {"dataType": "ENUM", "options": [{"name": "on", "value": 1}, {"name": "off", "value": 0}]}
    },
    {
      "type": "devices.capabilities.work_mode",
      "instance": "workMode",
      "parameters": {
        "dataType": "STRUCT",
        "fields": [
          {
            "fieldName": "workMode",
            "dataType": "ENUM",
            "options": [
              {"name": "FanSpeed", "value": 1},
              {"name": "Auto", "value": 2},
              {"name": "Sleep", "value": 3},
              {"name": "Nature", "value": 4},
              {"name": "Custom", "value": 5}
            ],
            "required": true
          },
          {
            "fieldName": "modeValue",
            "dataType": "ENUM",
            "options": [
              {"name": "FanSpeed", "options": [{"value": 1}, {"value": 2}, {"value": 3}, {"value": 4}, {"value": 5}, {"value": 6}, {"value": 7}, {"value": 8}, {"value": 9}, {"value": 10}, {"value": 11}, {"value": 12}]},
              {"name": "Auto", "defaultValue": 0},
              {"name": "Sleep", "options": [{"value": 1}, {"value": 2}, {"value": 3}, {"value": 4}, {"value": 5}, {"value": 6}, {"value": 7}, {"value": 8}, {"value": 9}, {"value": 10}, {"value": 11}, {"value": 12}]},
              {"name": "Nature", "options": [{"value": 1}, {"value": 2}, {"value": 3}, {"value": 4}, {"value": 5}, {"value": 6}, {"value": 7}, {"value": 8}, {"value": 9}, {"value": 10}, {"value": 11}, {"value": 12}]},
              {"name": "Custom", "defaultValue": 0}
            ],
            "required": true
          }
        ]
      }
    }
  ]
}
```

_H7107 raw_api_state (Developer API state poll)_

```json
{
  "sku": "H7107",
  "device": "[MAC]",
  "capabilities": [
    {"type": "devices.capabilities.online", "instance": "online", "state": {"value": true}},
    {"type": "devices.capabilities.on_off", "instance": "powerSwitch", "state": {"value": 1}},
    {"type": "devices.capabilities.toggle", "instance": "oscillationToggle", "state": {"value": 1}},
    {"type": "devices.capabilities.work_mode", "instance": "workMode", "state": {"value": {"workMode": 1, "modeValue": 8}}}
  ]
}
```

---

### #120 — Duplicate Preset speed/presets not taking effect for Fan

- **State:** CLOSED · **Category:** bug
- **SKUs:** H7106
- **Device types:** fan
- **Protocol area:** device-profile, platform-api

**Finding.** H7106 tower/smart fan exposes work modes that the integration maps into HA fan preset_modes. Two bugs surfaced: (1) The hardcoded FAN_PRESET_MODES set (which already contains "Auto") collided with a work-mode-derived preset also named "Auto", producing two identical "Auto" presets. This is benign inside the integration but breaks HomeKit export because pyhap requires unique preset switch service unique_ids (RuntimeError: Cannot assign IID ... unique_id=Auto ... already in use). The fan.py preset-building loop (~line 148) skips WORK_MODE_GEAR and WORK_MODE_AUTO but did not dedupe extra work-mode names against the already-known FAN_PRESET_MODES, so a device-reported "Auto" work mode was appended a second time. (2) Selecting a preset speed had no effect and the coordinator (coordinator.py:1738) logged "Control command failed: Parameter value out of range" — i.e. the workMode capability value sent for the preset was outside the device's accepted range, so the fan work-mode/gear value mapping was wrong. No raw Govee API capability JSON or MQTT payload was included in the issue; only the integration's own code snippet and HA/HomeKit tracebacks were provided.

**Feature.** Fixed duplicate fan preset modes and non-working preset speeds on H7106 fans (also unblocks HomeKit export).

**Resolution.** Fixed in v2026.6.20 -> resolved in v2026.6.21. Reporter confirmed "Appears to be resolved in Version 2026.6.21". The maintainer's shipped fix corresponds to the reporter's proposed dedup guard (skip any work-mode name whose lowercase already matches an entry in FAN_PRESET_MODES) plus correcting the preset workMode value so it no longer exceeds the device's accepted parameter range.

---

### #118 — Humidity reported by Dehumidifier H7150

- **State:** OPEN · **Category:** bug
- **SKUs:** H7150, H7152
- **Device types:** devices.types.dehumidifier
- **Protocol area:** device-profile, bff-internal-api, platform-api, mqtt, ble, state-management

**Finding.** For the H7150/H7152 dehumidifiers, the "water tank full" state rides on a push-only `devices.capabilities.event` / instance `waterFullEvent` capability. The Platform API `/device/state` poll never returns this event capability (raw_api_state only carries online, powerSwitch, humidity(range) and workMode), and MQTT does not push it for these devices, so the sensor stayed "unknown" forever. The reliable source is the account/BFF device list: `deviceExt.deviceSettings._json_str.waterFull` (int, 1 = full). This account data path requires email/password configured, not just an API key. In this diagnostic BFF `waterFull: 1` correctly produced coordinator `state.water_full: true`. The Water Tank Full sensor is a binary_sensor with device_class=problem, so full/true renders as "Problem".

Target (configured) humidity for these dehumidifiers is only exposed via the Auto-mode setpoint: workMode instance `workMode` is a STRUCT of {workMode ENUM (1=gearMode,3=Auto,8=Dryer), modeValue}; when workMode=3 (Auto), modeValue is the humidity setpoint (range 30-80). In non-Auto modes there is no setpoint so `configured_humidity` reads null (reporter was in work_mode 3 with mode_value 0, hence 0%/null). The CURRENT room humidity and temperature are NOT exposed by Govee's cloud API or account data at all for these dehumidifiers — the Govee app reads those over BLE only, so no live humidity/temp sensor can be surfaced from the cloud integration. Note the `devices.capabilities.range` / instance `humidity` state.value comes back as an empty string "" in the Platform API poll.

**Feature.** H7150/H7152 dehumidifier Water Tank Full sensor now reflects real state, sourced from account data deviceSettings.waterFull (requires email/password).

**Resolution.** Water Tank Full: FIXED in v2026.6.24 — now sourced from account data (`deviceSettings.waterFull`) and preserved across polls; confirmed working in reporter's diagnostic (BFF waterFull:1 -> water_full:true). Requires email/password configured. Current room humidity/temperature: WON'T FIX / API-limited — not available via cloud or account data (BLE-only in the Govee app); target humidity already reads via Auto-mode setpoint (same mechanism as prior H7152 fix). Issue left OPEN pending reporter verification that the water-tank sensor tracks fill/empty.

_H7150 dehumidifier device-profile capabilities (device list) — workMode STRUCT + push-only waterFullEvent_

```json
{
  "sku": "H7150",
  "device_type": "devices.types.dehumidifier",
  "capabilities": [
    {"type": "devices.capabilities.on_off", "instance": "powerSwitch", "parameters": {"dataType": "ENUM", "options": [{"name": "on", "value": 1}, {"name": "off", "value": 0}]}},
    {"type": "devices.capabilities.range", "instance": "humidity", "parameters": {"unit": "unit.percent", "dataType": "INTEGER", "range": {"min": 30, "max": 80, "precision": 1}}},
    {"type": "devices.capabilities.work_mode", "instance": "workMode", "parameters": {"dataType": "STRUCT", "fields": [
      {"fieldName": "workMode", "dataType": "ENUM", "options": [{"name": "gearMode", "value": 1}, {"name": "Auto", "value": 3}, {"name": "Dryer", "value": 8}], "required": true},
      {"fieldName": "modeValue", "dataType": "ENUM", "options": [{"name": "gearMode", "options": [{"name": "Low", "value": 1}, {"name": "High", "value": 3}]}, {"name": "Auto", "range": {"min": 30, "max": 80}}, {"name": "Dryer", "value": 0}], "required": true}
    ]}},
    {"type": "devices.capabilities.event", "instance": "waterFullEvent", "parameters": {}}
  ]
}
```

_H7150 raw_api_state (Platform API /device/state poll) — humidity value empty, no waterFullEvent returned_

```json
{
  "sku": "H7150",
  "device": "[MAC]",
  "capabilities": [
    {"type": "devices.capabilities.online", "instance": "online", "state": {"value": true}},
    {"type": "devices.capabilities.on_off", "instance": "powerSwitch", "state": {"value": 1}},
    {"type": "devices.capabilities.range", "instance": "humidity", "state": {"value": ""}},
    {"type": "devices.capabilities.work_mode", "instance": "workMode", "state": {"value": {"workMode": 3, "modeValue": 0}}}
  ]
}
```

_H7150 last_mqtt_message (MQTT push does not include water-full)_

```json
{"onOff": 1, "result": 1}
```

_H7150 BFF deviceExt.deviceSettings values — waterFull:1 is the reliable water-tank-full source_

```json
{
  "sku": "H7150",
  "deviceSettings": {
    "address": "[REDACTED]",
    "pactCode": 1,
    "waterFull": 1,
    "wifiMac": "[MAC]",
    "pactType": 1,
    "thermometerUnit": 1,
    "language": "en",
    "appVersion": "7.5.20",
    "mcuSoftVersion": "1.00.28",
    "mcuHardVersion": "1.00.02",
    "boilWaterCompletedNotiOnOff": 1,
    "completionNotiOnOff": 1,
    "autoShutDownOnOff": 1,
    "sku": "H7150",
    "versionHard": "1.02.00",
    "versionSoft": "1.00.15",
    "ic": 0,
    "playState": false,
    "wifiFuncList": ""
  },
  "lastDeviceData": {"online": false}
}
```

_BFF response skeleton — deviceExt.deviceSettings._json_str shape carrying waterFull_

```json
{
  "status": "int", "message": "str",
  "data": {"devices": [{
    "deviceId": "int", "groupId": "int", "sku": "str", "device": "[MAC]", "spec": "str",
    "versionHard": "str", "versionSoft": "str", "pactType": "int", "pactCode": "int",
    "deviceExt": {
      "deviceSettings": {"_json_str": {"address": "str", "pactCode": "int", "waterFull": "int", "wifiMac": "str", "thermometerUnit": "int", "appVersion": "str", "mcuSoftVersion": "str", "mcuHardVersion": "str", "boilWaterCompletedNotiOnOff": "int", "completionNotiOnOff": "int", "autoShutDownOnOff": "int", "sku": "str", "versionHard": "str", "versionSoft": "str", "topic": "str", "ic": "int", "playState": "bool"}},
      "lastDeviceData": {"_json_str": {"online": "bool"}}
    }
  }]}
}
```

---

### #117 — H5140: Report CO2 (`carbonDioxideConcentration`)

- **State:** CLOSED · **Category:** feature
- **SKUs:** H5140
- **Device types:** air-quality-monitor, temp-humidity-sensor
- **Protocol area:** device-profile, state-management

**Finding.** The Govee H5140 air/temp/humidity monitor exposes a CO2 measurement via the `carbonDioxideConcentration` property (value in ppm), which surfaces in the integration diagnostics. This is a distinct property alongside the device's existing temperature and humidity readings. A typical indoor value is ~600 ppm, confirming the value is a real CO2 ppm reading rather than a coarse index. Prior context: this was noted in diagnostics discussion on issue #116 and relates to #114. No raw JSON capability/state payload was included in the issue body or comments.

**Feature.** H5140: CO2 (ppm) sensor via the `carbonDioxideConcentration` property.

**Resolution.** Shipped in v2026.6.24 — H5140 now exposes a CO2 sensor (ppm) backed by the `carbonDioxideConcentration` property, alongside the existing temperature/humidity sensors. Reporter (brian6932) confirmed it works after updating and reloading the integration.

---

### #116 — H5106 & H5140: Temperature values incorrect

- **State:** CLOSED · **Category:** bug
- **SKUs:** H5106, H5140
- **Device types:** devices.types.thermometer, devices.types.air_quality_monitor
- **Protocol area:** device-profile, platform-api, mqtt, bff-internal-api

**Finding.** The H5106 (Air Quality Monitor, device_type=devices.types.thermometer) and H5140 (Smart CO2 Monitor, device_type=devices.types.air_quality_monitor) report the `sensorTemperature` property (type=devices.capabilities.property) as a PLAIN FAHRENHEIT FLOAT over the Developer API — e.g. 73.94 / 73.76, i.e. ~74 F ~= 23.3 C. It is NOT centi-encoded (x100) as first assumed. `sensorHumidity` is a plain percent float (51.9 / 51.3) and was always correct — the humidity-correct/temperature-wrong asymmetry was the diagnostic clue. The integration tagged the sensor native unit as Celsius, so the raw Fahrenheit number was rendered as if Celsius (e.g. 73.9 shown as 73.9 C), producing an alarmingly high reading. Same class of bug as #115: the SKU simply was not on the Fahrenheit auto-convert list. Additional capabilities: H5106 exposes `airQuality` (coarse index, observed value 1); H5140 exposes `carbonDioxideConcentration` (ppm, observed value 609). Sensor readings arrive via the cloud REST API, NOT via MQTT — the MQTT status push for these devices carries only onOff and a `sta` block (stc/stw strings), no temp/humidity, and MQTT state updates logged power=None/brightness=None. The BFF (app internal) API deviceExt.deviceSettings carries rich per-device config including `battery`, `fahOpen` (Fahrenheit display toggle), `temCali`/`humCali` (calibration offsets), `temMax`/`temMin`/`humMax`/`humMin`/`co2Min`/`co2Max`/`co2LevelUpper`/`co2LevelLower` thresholds, `wifiLevel`/`wifiMac`; lastDeviceData carries only `online`. BFF census flagged both SKUs as not in leak/thermo-hygrometer lists.

**Feature.** H5106 and H5140 air-quality/CO2 monitors now report correct temperature (Fahrenheit readings auto-converted).

**Resolution.** Two-round fix. v2026.6.17 shipped a WRONG root cause (assumed centi-encoded ×100, added speculative de-scale-by-100 code) — user reported UI still showed the large value while diagnostics looked correct. Reporter's diagnostics JSON revealed the value was actually a plain Fahrenheit float. Corrected in v2026.6.19: H5106 and H5140 added to the Fahrenheit auto-conversion list and the speculative centi code from v2026.6.17 removed. Reporter confirmed "it works now"; issue closed.

_H5140 raw_api_state (Developer API) — sensorTemperature is a plain Fahrenheit float (73.94), not centi-encoded; sensorHumidity plain %; carbonDioxideConcentration in ppm_

```json
{"sku":"H5140","device":"[MAC]","capabilities":[{"type":"devices.capabilities.online","instance":"online","state":{"value":true}},{"type":"devices.capabilities.property","instance":"carbonDioxideConcentration","state":{"value":609}},{"type":"devices.capabilities.property","instance":"sensorTemperature","state":{"value":73.94}},{"type":"devices.capabilities.property","instance":"sensorHumidity","state":{"value":51.9}}]}
```

_H5106 capabilities array (data.devices[].capabilities) — thermometer with extra airQuality property (H5106 raw sensorTemperature observed = 73.76 F, sensorHumidity = 51.3, airQuality = 1)_

```json
[{"type":"devices.capabilities.property","instance":"sensorTemperature","parameters":{}},{"type":"devices.capabilities.property","instance":"sensorHumidity","parameters":{}},{"type":"devices.capabilities.property","instance":"airQuality","parameters":{}}]
```

_H5140 last_mqtt_message — MQTT status push carries no temp/humidity, only onOff + sta(stc/stw) + result_

```json
{"onOff":0,"sta":{"stc":"1_1_62_14280","stw":"201_115_62_[MAC]_0"},"result":1}
```

_BFF deviceExt.deviceSettings._json_str skeleton (app internal API) — carries battery, fahOpen (F display toggle), temCali/humCali calibration, temMax/temMin/humMax/humMin/co2Min/co2Max/co2LevelUpper/co2LevelLower thresholds, wifiLevel/wifiMac; lastDeviceData carries only online_

```json
{"batteryWarning":"bool","temWarning":"bool","pactType":"int","humWarning":"bool","delayPushTime":"int","battery":"int","co2Min":"int","fahOpen":"bool","temCali":"int","temMax":"int","co2LevelUpper":"int","wifiLevel":"int","co2Max":"int","humMax":"int","normalPushOnOff":"bool","temMin":"int","netWaring":"bool","humMin":"int","airQualityOnOff":"int","humCali":"int","co2LevelLower":"int","wifiMac":"str","co2Warning":"bool","uploadRate":"int","criticalOnOff":"bool","emailWarningOnOff":"bool"}
```

---

### #115 — Fahrenheit and Celsius mis-labled

- **State:** CLOSED · **Category:** bug
- **SKUs:** H717A
- **Device types:** kettle, tea pot
- **Protocol area:** device-profile, state-management

**Finding.** The H717A kettle reports its temperature in degrees Fahrenheit via the Govee API, not Celsius. The integration's temperature sensor was tagged with a Celsius native unit, and H717A was NOT on the integration's auto-conversion (°F->°C) list, so a raw °F reading (e.g. 187) was surfaced as if it were 187 °C — an impossible kettle value (water boils at 100 °C), producing the "labels flipped" symptom. Adding H717A to the auto-conversion list makes 187 °F correctly convert to ~86 °C, after which Home Assistant renders it in the user's configured unit. Note: whether the API returns °F vs °C can depend on the Govee app's own unit setting; the integration exposes a per-device "Temperature unit from Govee API" option (Fahrenheit/Celsius) to opt out of the auto-conversion when the app is set to °C.

**Feature.** H717A kettle temperature now auto-converts from Fahrenheit to Celsius (fixes mislabeled temperature readings).

**Resolution.** Fixed in v2026.6.17: H717A added to the °F->°C auto-conversion list so kettle temperature reads a sensible converted value. A "Configure -> Temperature unit from Govee API -> Celsius" option lets users opt out if their Govee app reports Celsius. Issue closed 2026-06-26 as resolved.

---

### #114 — Missing feature support for Govee products (H1310/H1370/H7152/H5106/H5089/H7126/H7124)

- **State:** OPEN · **Category:** feature
- **SKUs:** H1310, H1370, H7152, H5106, H5089, H7126, H7124, H5220, H5110, H5044, H5126, H5129, H5151
- **Device types:** ceiling fan, dehumidifier, air quality monitor, smart outlet extender, air purifier, thermo-hygrometer sensor, gateway, button switch accessory, motion sensor accessory
- **Protocol area:** device-profile, bff-internal-api, state-management, ble

**Finding.** Multi-device tracking issue that surfaced several concrete Govee protocol facts:

CEILING FANS (H1310/H1370): expose separate Main Light and Background Light on/off; also support named "Snapshots" (saved scenes, e.g. "Ambient Light w/ Fan"). Snapshot recall is a one-shot apply: after recall the Developer API returns blank for the affected switches (fan/background light don't reflect toggled state) and the snapshot select reverts to "unknown" on the next poll — un-trackable, optimistic-only.

DEHUMIDIFIER (H7152): the configured humidity setpoint is a distinct value from the Auto-mode humidity value (Auto pins to 80% on this unit); reading the setpoint fixes the earlier 0% display. Work modes are Auto, Dryer, and Gearmode with sub-speeds Low/Medium/High (integration originally only exposed Low/High/Auto/Dryer). The Developer API for H7152 only exposes power, humidity setpoint, work mode, and the water-full event — NO current temp/humidity sensor (app reads those over BLE).

AIR QUALITY (H5106 / air purifiers): the capability named airQuality is a coarse air-quality INDEX (AQI-style), not a PM2.5 µg/m³ measurement. Initially assumed constant 1 (presence flag) but confirmed to vary (value 2 observed) so it is a real coarse reading, kept as a numeric AQI-device-class sensor. The Developer API returns no PM2.5 value.

OUTLET EXTENDER (H5089): has 2 independently switchable outlets; its color/brightness/scenes belong to the NIGHTLIGHT, not the outlet (old single light entity's on/off had toggled the outlet). Nightlight scene names are reported incorrectly by the Govee API — integration just surfaces what Govee sends.

AIR PURIFIERS (H7124/H7126): expose Filter Life % and an AQI sensor. H7124 fan preset modes (Normal/Auto/Sleep/Turbo) live on the fan entity's preset dropdown, separate from the purifier-mode select (gear speeds Low/Med/High). workMode is numeric: 5=Sleep, 3=Auto; the API can report workMode lazily/stale after an in-app change.

BFF INTERNAL API (per-device dump, added as bff_device_values in diagnostics v2026.6.21): confirmed the BFF deviceSettings block carries battery for genuinely battery-powered sensors (H5220, H5110, H5126 button, H5129 motion) plus gatewayInfo (H5044 gateway sku/versions/gatewayId), warning/comfort limits (temMin/Max, humMin/Max, pm25Min/Max, comfortTem/Hum), waterFull for H7152, and a lastDeviceData block. For thermo sensors (H5110) lastDeviceData carries tem/hum/avgDayTem/avgDayHum/lastTime. For accessories, lastDeviceData carries logType/logTime: H5126 button (logType 7=button1 pressed, 8=button2 pressed), H5129 motion (logType 1 = last motion). IMPORTANT: the BFF does NOT carry the live sensor readings people wanted — no current temp/humidity for H7152, no real PM2.5/air-quality value for H5106. Mains-powered devices falsely report a constant battery:100 in BFF deviceSettings (e.g. H5106), producing a bogus battery entity. H5044 gateway supports both LoRa and BLE sub-sensors.

**Feature.** Added H1310/H1370 Main+Background light switches, H7152 dehumidifier Medium mode + correct humidity setpoint, H5106/H7124/H7126 AQI sensors, H5089 per-outlet switches + Night Light, H7124 Sleep/Auto/Turbo presets, device Snapshots, and suppressed phantom battery on mains-powered devices.

**Resolution.** Largely shipped incrementally, issue kept OPEN for user confirmation and follow-ups.
- v2026.6.18: H1310/H1370 Main + Background Light switches; H7152 correct humidity setpoint + Medium mode (Low/Medium/High); H5106 Air Quality sensor; H5089 per-outlet switches + full Night Light light entity (brightness/colour) + Night Light Scene selector; H7126/H7124 Filter Life % + Air Quality sensors; H7124 Sleep/Auto/Turbo fan presets + Night Light + scene selector; Snapshot selector (re-added).
- v2026.6.20: fixed "medium" preset lowercase (missing translation) -> displays "Medium".
- v2026.6.21: airQuality converted to a diagnostic presence binary sensor; redacted per-device raw BFF dump (bff_device_values) added to diagnostics.
- v2026.6.24: airQuality reverted to a numeric AQI sensor (confirmed value moves 1->2); suppressed bogus battery:100 on mains SKUs and preserved real battery across polls.
- v2026.6.25: H5106 phantom battery suppressed by SKU (its device_type wasn't a recognized mains type so prior fix missed it).
Blocked / out of scope: PM2.5 µg/m³ for H5106 and current temp/humidity for H7152 (not in Developer API nor BFF). Left open for: surfacing BFF accessory devices (H5126 button / H5129 motion) as battery + last-event entities; possible per-SKU BLE decoders via BT-proxy for richer data (H5106 PM2.5, H7152 temp/humidity) — both to be tracked as separate issues.

_BFF deviceSettings + lastDeviceData for H7152 dehumidifier (waterFull event; no live temp/humidity)_

```json
{
  "sku": "H7152",
  "deviceSettings": {
    "thermometerUnit": 0,
    "waterFull": 1,
    "mcuHardVersion": "1.00.03",
    "mcuSoftVersion": "1.00.16",
    "pactCode": 1,
    "pactType": 1,
    "supportBleBroadV3": true,
    "wifiHardVersion": "1.02.00",
    "wifiSoftVersion": "1.00.13",
    "subDevices": {},
    "language": "en",
    "appVersion": "7.5.20",
    "supportEnc": true,
    "boilWaterCompletedNotiOnOff": 1,
    "completionNotiOnOff": 1,
    "autoShutDownOnOff": 1,
    "sku": "H7152",
    "versionHard": "1.02.00",
    "versionSoft": "1.00.13",
    "ic": 0,
    "playState": false
  },
  "lastDeviceData": { "online": false }
}
```

_BFF deviceSettings for H5220 thermo sensor via H5044 gateway (battery + gatewayInfo + comfort/warning limits)_

```json
{
  "sku": "H5220",
  "deviceSettings": {
    "temMin": 0, "temMax": 20000, "temWarning": true, "fahOpen": true, "temCali": 0,
    "humMin": 3000, "humMax": 7000, "humWarning": false, "humCali": 0,
    "netWaring": true, "battery": 100, "wifiLevel": 0, "delayPushTime": 0,
    "batteryWarning": true, "pactCode": 2, "pactType": 1, "normalPushOnOff": true, "bdType": 1,
    "gatewayVersionHard": "1.04.01", "gatewayVersionSoft": "1.03.04", "uploadRate": 10,
    "sku": "H5220", "versionHard": "6.01.00", "versionSoft": "1.00.68", "gatewayId": [GATEWAY_ID],
    "gatewayInfo": {
      "sku": "H5044", "pactCode": 2, "pactType": 2,
      "versionHard": "1.04.01", "versionSoft": "1.03.15",
      "wifiHardVersion": "1.04.01", "wifiSoftVersion": "1.03.15", "goodsType": 291
    },
    "standard": 0, "gatewayWarning": false, "criticalOnOff": false,
    "comfortHumMin": 4000, "comfortTemMax": 2800, "weatherForecastOnOff": true,
    "comfortHumMax": 6000, "comfortTemMin": 1800, "sno": 0, "openHourClock": 0, "weatherDisplay": 1
  },
  "lastDeviceData": { "online": false }
}
```

_BFF deviceSettings + lastDeviceData for H5110 thermo sensor via H5044 gateway (lastDeviceData carries tem/hum/avgDay)_

```json
{
  "sku": "H5110",
  "deviceSettings": {
    "temMin": -2000, "temMax": 6000, "temWarning": false, "fahOpen": true, "temCali": 0,
    "humMin": 0, "humMax": 10000, "humWarning": false, "humCali": 0,
    "netWaring": true, "battery": 100, "wifiLevel": 0, "delayPushTime": 0,
    "batteryWarning": true, "pactCode": 1, "pactType": 2, "normalPushOnOff": true, "bdType": 1,
    "gatewayVersionHard": "1.04.01", "gatewayVersionSoft": "1.03.15", "uploadRate": 10,
    "sku": "H5110", "versionHard": "3.01.00", "versionSoft": "1.00.09", "gatewayId": [GATEWAY_ID],
    "gatewayInfo": {
      "index": 1, "sku": "H5044", "pactCode": 2, "pactType": 2,
      "versionHard": "1.04.01", "versionSoft": "1.03.15",
      "wifiHardVersion": "1.04.01", "wifiSoftVersion": "1.03.15"
    },
    "criticalOnOff": false
  },
  "lastDeviceData": {
    "online": false, "tem": 2570, "hum": 4830,
    "lastTime": 1782323339000, "avgDayTem": 2570, "avgDayHum": 4830
  }
}
```

_BFF for accessory devices H5126 button + H5129 motion (battery + lastDeviceData logType/logTime as last-event)_

```json
{
  "sku": "H5126",
  "deviceSettings": { "bdType": 1, "sku": "H5126", "versionHard": "1", "versionSoft": "1", "battery": 100, "time": 1782185645443 },
  "lastDeviceData": { "online": true, "bind": true, "logType": 8, "logTime": 1782185645064 }
}
// logType 7 = button one pressed, 8 = button two pressed
{
  "sku": "H5129",
  "deviceSettings": { "bdType": 1, "sku": "H5129", "versionHard": "3.01.01", "versionSoft": "1.00.07", "battery": 100, "time": 1782606774658 },
  "lastDeviceData": { "online": true, "bind": true, "logType": 1, "logTime": 1782606774296 }
}
// logType 1 = last motion detected
```

_BFF deviceSettings for H5106 AQI monitor (only pm25 warning limits, no real PM2.5; phantom battery:100 on mains device)_

```json
{
  "sku": "H5106",
  "deviceSettings": {
    "airQualityOnOff": 0, "humCali": 0, "humMax": 9900, "humMin": 0, "humWarning": false,
    "pactCode": 1, "pactType": 1, "pm25Max": 999, "pm25Min": 0, "pm25Warning": false,
    "temCali": 0, "temMax": 5000, "temMin": -1000, "temWarning": false, "uploadRate": 10,
    "netWaring": true, "battery": 100, "fahOpen": true, "wifiLevel": 0,
    "sku": "H5106", "versionHard": "3.01.01", "versionSoft": "1.00.16",
    "normalPushOnOff": true, "criticalOnOff": false
  },
  "lastDeviceData": { "online": false }
}
```

---

### #105 — support for H1370, a new light/fan combo.

- **State:** CLOSED · **Category:** feature
- **SKUs:** H1370, H1310
- **Device types:** fan, light, light-fan combo
- **Protocol area:** device-profile

**Finding.** The H1370 is a new light/fan combo (functionally similar to the H1310/H1730-family fans). It is auto-detected from its capabilities, so the light and fan-speed controls worked without code changes. The key protocol fact: the H1370 exposes its oscillation control under a DIFFERENT capability instance than earlier Govee fans, so the integration's oscillation toggle was not being picked up until the new instance was handled. Reverse airflow (fan direction) and multiple fan speeds also work. The H1370 has dual light zones (main/background) which the integration currently routes through a single light entity rather than separate controls. No inline JSON was provided in the issue — the reporter attached a diagnostics download file (external link), so no capability array is captured here.

**Feature.** Added support for the H1370 light/fan combo, including the oscillation toggle (v2026.6.14).

**Resolution.** Shipped in v2026.6.14: added the missing oscillation toggle for the H1370 by handling its differing oscillation capability instance. Light, fan speeds, and reverse airflow already worked via capability auto-detection. Reporter confirmed all functions work on their H1370 units. Dual light zones still route through a single light entity (separate controls not implemented).

---

### #104 — Govee H60B2 (3-Segment Lamp) – Segment entities are not updated correctly and always remain off

- **State:** CLOSED · **Category:** bug
- **SKUs:** H60B2
- **Device types:** light, rgbic-segmented-lamp
- **Protocol area:** device-profile, state-management

**Finding.** The H60B2 exposes TWO distinct kinds of per-zone control that must not be conflated: (1) RGBIC color segments (Top/Middle/Bottom) which are per-zone COLOR only, and (2) real independently switchable ON/OFF zones exposed as capability instances light1Toggle / light2Toggle / light3Toggle (the "light1/2/3Toggle" family). The color segment entities are optimistic-only by design because Govee's Platform API returns EMPTY segment colors, so they cannot reflect app-side changes and always read "off" — this is a general documented segment-state limitation, not H60B2-specific. The switchable zones (light1/2/3Toggle) were previously not surfaced at all; they should be modeled as on/off switch entities, one per zone, coexisting with the color segments (segments = per-zone colour, switches = per-zone on/off). Note also: without MQTT (email/password + 2FA completed), the integration is polling-only and app→HA state changes do not sync live; a dismissed "MQTT 2FA required" repair indicates MQTT was never enabled on the reporter's account.

**Feature.** Per-zone on/off switches ("Light Zone 1/2/3") for multi-zone lamps like the H60B2 (light1/2/3Toggle capabilities).

**Resolution.** Fixed in v2026.6.14: adds a switch per switchable zone ("Light Zone 1/2/3") mapped to the light1/2/3Toggle capabilities, giving working per-zone on/off control. The RGBIC color segment entities remain optimistic-only (documented Govee API limitation — empty segment colors returned). Issue closed as resolved 2026-06-26.

---

### #102 — H5110 shows wrongly scaled Humidity readings every other update

- **State:** CLOSED · **Category:** bug
- **SKUs:** H5110, H5179, H5220, H5044
- **Device types:** thermo-hygrometer, smart-gateway-hub
- **Protocol area:** bff-internal-api, platform-api, state-management

**Finding.** Gateway-bridged thermo-hygrometers (H5110, H5179) that report through the H5044 Smart Gateway 1s hub are double-sourced: the Govee Developer API /device/state returns correctly-scaled humidity, but the v2026.6.13 BFF "tickle" poll (added for #83) also scraped these devices via the leak-sensor side collection in fetch_bff_leak_sensors -> _apply_bff_thermo_reading, which applied a HARD-CODED /10 divisor to the raw BFF `hum` field. That /10 was "confirmed against a real H5110" assuming hum is in tenths, but for these gateway-bridged sensors the raw BFF `hum` is in HUNDREDTHS (100x the percent), so /10 surfaced humidity 10x too high (e.g. 8300 -> 830%, or 511 read as 5110-scale). Because the correct Developer-API poll and the over-scaled BFF side-channel alternate, entities oscillated: clean value, then 10x value, on consecutive updates (e.g. 48, 485, 48, 485; API /device/state showed 51.1 vs HA entity 511). Cross-check confirmed every glitch value = 10x the clean value. The scale factor varies by gateway/firmware, so no fixed divisor was safe. Directly-connected H5220 units on the same hub did NOT exhibit the issue. Reporter noted integration already has a format-aware normalizer (_bff_reading with centi/plain key tags that would scale hum by 100) which the leak side-channel bypassed. Debug log evidence: "[custom_components.govee.api.auth] Discovered 0 leak sensors, 0 hubs, 2 thermo readings from BFF API" (the 2 thermo readings being the two H5179s). Battery-level readings for these thermo-hygrometer models were asked about but not confirmed available.

**Feature.** Fixed H5110/H5179 thermo-hygrometer humidity reading 10× too high on alternating updates (BFF is now tickle-only; value comes from the correctly-scaled Developer API poll).

**Resolution.** Fixed in v2026.6.14. The BFF call is now tickle-only: it still nudges Govee's cloud to refresh the reading, but the humidity/temperature VALUE is taken solely from the Developer /device/state poll, which is correctly scaled and already handles the °C/°F conversion. The hard-coded /10 humidity scaling on the BFF thermo side-channel was removed. Confirmed fixed by k-perri (H5110) and SnoElement (H5179) — humidity now reports correct percentage with no >100% readings across multiple poll cycles.

---

### #101 — Added new devices, had to reload to have them show

- **State:** OPEN · **Category:** feature
- **SKUs:** H5059, H5044
- **Device types:** leak sensor, hub/gateway
- **Protocol area:** device-profile, platform-api, state-management

**Finding.** Sensors added to a Govee hub are sub-devices that do NOT appear in the standard Platform API device list, so they are not noticed by the integration's normal device-discovery poll. Concretely: H5059 leak sensors paired to an H5044 hub are hub-attached sub-devices absent from the standard device list. Regular Wi-Fi devices added to the account ARE surfaced in the standard device list and were already being auto-discovered via a periodic account re-poll (every ~5 min, throttled to respect API rate limits) that triggers an integration reload when new devices are detected. The hub-attached sub-device case was the gap: because these sensors don't show up in the standard list, they required a manual HA reload to appear. Fix detects new hub-attached leak sensors on the periodic account poll and triggers an automatic reload.

**Feature.** New devices added to your Govee account after HA starts — including leak sensors added to a hub — are now auto-discovered within ~5 min and appear without a manual reload.

**Resolution.** v2026.6.14 added automatic discovery for account-added devices: the integration re-checks the device list on a slow ~5 min cadence and reloads itself when new devices appear (manual reload still available as the instant option). v2026.6.24 closes the remaining gap for hub-attached leak sensors (H5059 on H5044) — they are now detected on the periodic account poll and trigger an automatic reload (appears within a few minutes). Issue left OPEN pending user confirmation that the next batch of hub sensors appears without a manual reload.

---

### #100 — Govee Smart Thermometer P2 H5310 with H5044 Gateway

- **State:** CLOSED · **Category:** api-limitation
- **SKUs:** H5310, H5044, H5075
- **Device types:** devices.types.thermometer
- **Protocol area:** device-profile, platform-api, bff-internal-api, mqtt

**Finding.** H5310 (Govee Smart Thermometer P2, pool thermometer) is a thermo/hygro sub-device that reports through an H5044 gateway. BFF census marks it in_thermo_hygro_skus=true, has_gateway_info=true, gateway_sku=H5044, has_sno=true, sno=0. Its device id is a 16-octet EXTENDED address ("03:55:01:25:00:00:00:0B:FF:FF:00:41:FF:FF:00:33"), unlike the standard 8-octet Govee MAC — worth noting for id parsing. Platform API exposes only two property capabilities: sensorTemperature and sensorHumidity (both parameters={}). In the diagnostic, sensor_temperature=33.0 was read correctly but sensor_humidity=655.35 (= 65535/100 = 0xFFFF "no data" sentinel; this pool probe is temperature-only) and online=false, so HA sensor entities render as no-data/unavailable even though a valid temperature is present — the reported "no data displayed" symptom. transport shows cloud_api+mqtt true, ble false; mqtt connected with tracked_devices=1 but device_topic_count=0 (no direct MQTT topic for the sub-device — it rides the gateway). The H5044 gateway pushes 20-byte "multisync" frames (header ee34) captured under recent_multisync; each frame embeds a big-endian unix timestamp (0x6a25xxxx, incrementing by 0x258=600s per 10-minute sample) plus slowly-varying temperature bytes and a trailing 1-byte checksum. The BFF app-internal deviceList is the richer state source: deviceExt.lastDeviceData carries online/tem/hum/lastTime/avgDayTem/avgDayHum, and deviceExt.deviceSettings carries battery, batteryWarning, temMax/temMin, temWarning, humMin/humMax, temCali/humCali, fahOpen, signal, wifiLevel, uploadRate, sno, gatewayId and nested gatewayInfo{device,sku,topic,address,pactCode,pactType,bleName,secretCode,versions,goodsType}.

**Resolution.** Closed with no comments and no shipped-version reference in the issue file. Root cause per diagnostic is a Platform API limitation for gateway-backed thermometers: the sub-device online flag reads false and humidity returns the 0xFFFF/100=655.35 sentinel for a temp-only probe, so entities go unavailable despite a valid sensor_temperature=33.0. Diagnostic captured the BFF deviceSettings/lastDeviceData shape and the H5044 gateway multisync (ee34) MQTT frames as candidate richer data sources; no fix version documented here.

_H5310 device profile + state (Platform API): thermometer behind H5044 gateway; humidity=655.35 sentinel, online=false_

```json
{
  "device": "[MAC-16OCTET e.g. 03:55:01:25:...:33]",
  "sku": "H5310",
  "name": "[NAME]",
  "device_type": "devices.types.thermometer",
  "is_group": false,
  "capabilities": [
    {"type": "devices.capabilities.property", "instance": "sensorTemperature", "parameters": {}},
    {"type": "devices.capabilities.property", "instance": "sensorHumidity", "parameters": {}}
  ],
  "state": {
    "online": false,
    "sensor_temperature": 33.0,
    "sensor_humidity": 655.35,
    "source": "api"
  },
  "transport": {"cloud_api": true, "mqtt": true, "ble": false}
}
```

_bff_device_census: thermo/hygro + gateway mapping (H5310->H5044, sno=0)_

```json
[
  {"sku": "H5310", "in_leak_sensor_skus": false, "in_leak_hub_skus": false, "in_thermo_hygro_skus": true, "has_sno": true, "sno": 0, "has_gateway_info": true, "gateway_sku": "H5044"},
  {"sku": "H5075", "in_leak_sensor_skus": false, "in_leak_hub_skus": false, "in_thermo_hygro_skus": false, "has_sno": false, "sno": null, "has_gateway_info": false, "gateway_sku": null}
]
```

_BFF deviceList skeleton (type names) for thermo/hygro: deviceExt.deviceSettings + lastDeviceData_

```json
{
  "deviceExt": {
    "deviceSettings": {"_json_str": {
      "battery": "int", "batteryWarning": "bool", "deviceType": "int",
      "powerSaveModeState": "bool", "pushState": "bool", "signal": "int", "sno": "int",
      "temMax": "int", "temMin": "int", "temWarning": "bool",
      "humMin": "int", "humMax": "int", "humWarning": "bool", "humCali": "int",
      "temCali": "int", "fahOpen": "bool", "wifiLevel": "int", "uploadRate": "int", "netWaring": "bool",
      "gatewayVersionHard": "str", "gatewayVersionSoft": "str", "gatewayId": "int",
      "sku": "str", "device": "[MAC]", "versionHard": "str", "versionSoft": "str",
      "wifiHardVersion": "str", "wifiSoftVersion": "str",
      "gatewayInfo": {"device": "[MAC]", "sku": "str", "topic": "str", "address": "str", "pactCode": "int", "pactType": "int", "bleName": "str", "secretCode": "str", "versionHard": "str", "versionSoft": "str", "goodsType": "int"},
      "normalPushOnOff": "bool", "gatewayWarning": "bool", "criticalOnOff": "bool", "emailWarningOnOff": "bool", "wifiFuncList": "str", "muteLevel": "int"
    }},
    "lastDeviceData": {"_json_str": {"online": "bool", "tem": "int", "hum": "int", "lastTime": "int", "avgDayTem": "int", "avgDayHum": "int"}}
  }
}
```

_recent_multisync: H5044 gateway MQTT frames (header ee34, 20 bytes; embedded ts 0x6a25xxxx +600s/sample + checksum)_

```json
[
  {"ts": "2026-06-07T20:00:13Z", "header": "ee34", "length": 20, "hex": "ee34000800642514c26a25cdccfd82ccff000047"},
  {"ts": "2026-06-07T20:10:13Z", "header": "ee34", "length": 20, "hex": "ee34000800642514c26a25d024fd82ccff0000b2"},
  {"ts": "2026-06-07T20:20:13Z", "header": "ee34", "length": 20, "hex": "ee34000800642514c26a25d27cfc82ccff0000e9"}
]
```

---

### #99 — Bitte fügen Sie H7161 hinzu (Add support for H7161 aroma diffuser)

- **State:** CLOSED · **Category:** feature
- **SKUs:** H7161
- **Device types:** aroma diffuser, humidifier
- **Protocol area:** device-profile, platform-api, mqtt

**Finding.** H7161 aroma diffuser was detected by the integration but had no control entities because the diffuser device type wasn't mapped. From the (attached, not inlined) diagnostics, the device's Cloud-API capability shape supports a power on/off switch and a preset light/mist scene selector (named scenes like "Bach", "Morgen"). Concrete protocol facts learned: (1) mist intensity and RGB nightlight are app-only and NOT exposed in the Govee Cloud API; (2) the water-low warning capability is referenced as `lackWaterEvent` but is not delivered for this SKU over ANY available transport — not present in the REST API and not pushed via MQTT — so it cannot be surfaced unless Govee begins pushing it via MQTT.

**Feature.** Added support for the H7161 aroma diffuser (power switch + preset light/mist scene selector).

**Resolution.** Shipped in v2026.6.14: H7161 mapped with a power switch (on_off) and a Scene selector for its preset light/mist scenes. User confirmed control works. Water-low (`lackWaterEvent`) deferred — no transport (REST or MQTT) delivers it for this SKU; mist intensity and RGB nightlight are app-only and out of scope for the Cloud API.

---

### #98 — H6117 and H6163 not working

- **State:** CLOSED · **Category:** bug
- **SKUs:** H6117, H6163
- **Device types:** LED strip light
- **Protocol area:** mqtt, state-management

**Finding.** Some older Govee strip lights (H6117, H6163) push AWS IoT / MQTT state messages that contain non-UTF-8 bytes — accented characters such as `°` (0xb0) or `ü` (0xfc) embedded in a scene name or device name. A strict UTF-8 decode of the MQTT payload raises errors like `'utf-8' codec can't decode byte 0xb0 in position 227: invalid start byte`, which caused the integration to drop the ENTIRE state message, so on/off (and other) state never updated. Separately, Govee sometimes sends genuinely malformed JSON over MQTT that fails with `Invalid \uXXXX escape` — this is Govee-side malformation that cannot be recovered, only skipped. Fix decodes payloads tolerantly (a stray byte in a name is replaced instead of discarding the whole update). Note: intermittent on/off *command* delivery (reported <1 in 10 success) is suspected to be a separate control-path issue, left unresolved pending further logs.

**Feature.** MQTT state messages now decode tolerantly, so a stray non-UTF-8 byte in a device or scene name no longer drops the whole state update.

**Resolution.** Shipped in v2026.6.12: MQTT/AWS IoT state payloads now decode UTF-8 tolerantly (replace invalid bytes) so a non-UTF-8 character in a scene/device name no longer drops the whole state update. Malformed JSON (invalid \uXXXX escapes sent by Govee) continues to be skipped gracefully. The state-feedback bug is fixed; the reported intermittent on/off command delivery remains unconfirmed/unresolved (asked to reopen with command-time debug log + diagnostics).

---

### #97 — Add H5310 Pool Thermometer

- **State:** CLOSED · **Category:** feature
- **SKUs:** H5310, H5044
- **Device types:** devices.types.thermometer
- **Protocol area:** bff-internal-api, device-profile, state-management, platform-api

**Finding.** The H5310 pool monitor is a battery-powered, gateway-bridged sensor: it reaches Govee's cloud through an H5044 hub and does NOT appear in the public Developer API device list (device_count=0, raw_api_devices=[], so users "can't find the hub"). It must be discovered from Govee's app/BFF device list instead, where its census line shows has_gateway_info=true and gateway_sku="H5044". The integration classifies it via the thermo/hygro SKU allowlist (bff_device_census flag in_thermo_hygro_skus). Two protocol quirks: (1) The H5310 is a temperature-ONLY probe — it reports humidity as 0xFFFF ("no sensor"), which de-scales to 655.35%, so no humidity entity should be created for it. Capabilities are still advertised as both devices.capabilities.property/sensorTemperature and .../sensorHumidity, but the humidity value is bogus. (2) The gateway's online flag flaps (lastDeviceData.online / state.online reported false between periodic uploads even when a valid temperature reading exists), so temperature availability must not be gated on the online flag. BFF deviceExt.deviceSettings carries battery, signal, wifiLevel, temMax/temMin/temCali, humMax/humMin, sno, and nested gatewayInfo (gatewayId, gateway device/sku); deviceExt.lastDeviceData carries online, tem, hum, lastTime, avgDayTem, avgDayHum (tem/hum are scaled ints). The H5310 device_id uses a 16-byte MAC-like form (03:55:01:25:...:28). The H5044 hub also emits ee34-header multisync frames (20 bytes) seen in recent_multisync.

**Feature.** Support for the H5310 pool thermometer (temperature + battery sensors), bridged via the H5044 gateway and nested under it in the device tree.

**Resolution.** Initial H5310 SKU discovery via BFF thermo/hygro path shipped in v2026.6.10 (commit d83fae7) — flips in_thermo_hygro_skus to true and creates temp+humidity sensors. Follow-up fixes shipped in v2026.6.12: temperature stays available even when the gateway reports online:false between uploads; the bogus 0xFFFF/655.35% humidity entity is suppressed (temp-only probe, no humidity entity created); a Battery sensor was added; and the H5310 now nests under its H5044 gateway in the device tree.

_bff_device_census entry for H5310 (gateway-bridged; in_thermo_hygro_skus flips true once SKU supported)_

```json
{ "sku": "H5310", "in_leak_sensor_skus": false, "in_leak_hub_skus": false, "in_thermo_hygro_skus": true, "has_sno": true, "sno": 0, "has_gateway_info": true, "gateway_sku": "H5044" }
```

_BFF /device/list device entry deviceExt shape (deviceSettings + lastDeviceData) for H5310, types annotated_

```json
{ "deviceId": "int", "groupId": "int", "sku": "str", "device": "[MAC]", "spec": "str", "versionHard": "str", "versionSoft": "str", "deviceName": "[NAME]", "pactType": null, "pactCode": null, "deviceExt": { "deviceSettings": { "_json_str": { "battery": "int", "batteryWarning": "bool", "criticalOnOff": "bool", "deviceType": "int", "emailWarningOnOff": "bool", "powerSaveModeState": "bool", "pushState": "bool", "signal": "int", "sno": "int", "temMax": "int", "temMin": "int", "temWarning": "bool", "uploadRate": "int", "netWaring": "bool", "humWarning": "bool", "humMin": "int", "humCali": "int", "fahOpen": "bool", "temCali": "int", "wifiLevel": "int", "humMax": "int", "sku": "str", "device": "[MAC]", "versionHard": "str", "versionSoft": "str", "wifiHardVersion": "str", "wifiSoftVersion": "str", "gatewayId": "int", "gatewayInfo": { "device": "[MAC]", "sku": "str", "topic": "str", "address": "str", "pactCode": "int", "pactType": "int", "bleName": "str", "secretCode": "str", "versionHard": "str", "versionSoft": "str", "wifiHardVersion": "str", "wifiSoftVersion": "str", "goodsType": "int" }, "normalPushOnOff": "bool", "gatewayWarning": "bool", "wifiFuncList": "str", "muteLevel": "int" } }, "lastDeviceData": { "_json_str": { "online": "bool", "tem": "int", "hum": "int", "lastTime": "int", "avgDayTem": "int", "avgDayHum": "int" } } }, "share": "int", "gidConfirmed": "bool", "gas": null, "goodsType": "int", "attributesId": "int", "supportScene": "int", "cardType": "int" }
```

_Parsed H5310 device (capabilities + state): 16-byte device_id, temp-only probe, humidity=655.35 (0xFFFF no-sensor), online flapping false, raw_api_state null_

```json
{ "03:55:01:25:00:00:00:0D:FF:FF:00:6E:FF:FF:00:28": { "sku": "H5310", "name": "H5310_0028", "device_type": "devices.types.thermometer", "is_group": false, "capabilities": [ { "type": "devices.capabilities.property", "instance": "sensorTemperature", "parameters": {} }, { "type": "devices.capabilities.property", "instance": "sensorHumidity", "parameters": {} } ], "state": { "online": false, "sensor_temperature": 26.4, "sensor_humidity": 655.35, "source": "api" }, "raw_api_state": null, "last_mqtt_message": null, "transport": { "cloud_api": true, "mqtt": true, "ble": false } } }
```

_recent_multisync frame from H5044 hub (ee34 header, 20 bytes)_

```json
{ "ts": "2026-06-07T03:20:15.934647+00:00", "hub_device_id": "[MAC]", "header": "ee34", "length": 20, "hex": "ee34000800642514a36a24e36e9882ccff2000ee" }
```

---

### #96 — H5109 not reporting temperature correctly

- **State:** CLOSED · **Category:** bug
- **SKUs:** H5109, H5179, H5110, HS5108, HS5106
- **Device types:** thermometer, temperature sensor
- **Protocol area:** device-profile, platform-api, state-management

**Finding.** The H5109 hygro-thermometer reports `sensorTemperature` as a Fahrenheit value (e.g. 100.83 = the user's 101°F) but the sensor state is tagged with a native unit of Celsius. Home Assistant then converts the raw number 100.83 as if it were °C -> 213.5°F, producing a wrong reading stuck at 213.5°F. The underlying protocol fact: a class of Govee thermometer SKUs (H5109, H5179, H5110, HS5108, HS5106) emit their `sensorTemperature` numeric already in Fahrenheit rather than Celsius, so the value must be treated as °F before any unit conversion. Previously the integration had a manual `api_temperature_unit` option defaulting to `celsius` (no conversion), which is wrong for these SKUs out of the box.

**Feature.** Thermometers that report temperature in Fahrenheit (H5109, H5179, H5110, HS5108, HS5106) now auto-convert to the correct reading by default via a new `auto` temperature-unit mode.

**Resolution.** Fixed on branch `fix/96-h5109-fahrenheit-auto` (no shipped version number stated in the thread). Added a new default `auto` mode that auto-converts the known Fahrenheit-reporting SKUs via a `FAHRENHEIT_REPORTING_SKUS` set (H5179, H5109, H5110, HS5108, HS5106) in `GoveeTemperatureSensor.native_value`; `fahrenheit`/`celsius` remain as explicit overrides. Files changed: const.py (new set + default `auto`), sensor.py (auto-detect conversion), config_flow.py (option now auto|celsius|fahrenheit), strings.json/translations/en.json, tests/test_thermometer.py. mypy/flake8 PASS, 879 tests pass. Result: correct ~38°C / 101°F reading with no manual config.

---

### #95 — Incorrect mac address through the API

- **State:** CLOSED · **Category:** api-limitation
- **SKUs:** H5103
- **Device types:** temperature/humidity sensor
- **Protocol area:** platform-api, state-management

**Finding.** For H5103 temp/humidity sensors, the MAC/device IDs returned by the Govee Developer Platform API endpoint /router/api/v1/user/devices do NOT match the MAC addresses shown for the same physical sensors in the Govee mobile app. Despite the ID mismatch, the API does return the user's own correct devices/data — it is not returning another account's sensors. The key limitation: sensor readings (temperature + humidity) delivered via the Platform API are significantly DELAYED compared to the live values in the mobile app, which is why HA values initially appear to not match. Reporter confirmed as a false alarm after investigation.

**Resolution.** Closed as a false alarm (self-resolved by reporter). No code change shipped. Takeaway: API-reported device IDs for H5103 differ from app-displayed MACs, and Platform API sensor data lags the mobile app; data is nonetheless correct for the user's own devices.

---

### #93 — H5109 Pool Thermometer does not update temperature

- **State:** CLOSED · **Category:** bug
- **SKUs:** H5109
- **Device types:** devices.types.thermometer
- **Protocol area:** device-profile, platform-api, state-management

**Finding.** H5109 Pool Thermometer (device_type devices.types.thermometer) exposes a single read-only capability devices.capabilities.property / instance "sensorTemperature". The platform poll response (raw_api_state) returns temperature as a plain numeric value under that capability's state.value (e.g. 80.63, in Fahrenheit per user's pool), alongside devices.capabilities.online. Critical transport fact: the H5109 is a BLE sensor bridged through a Govee gateway to the cloud, with NO MQTT push path — diagnostics show transport.mqtt=true but transport_health.mqtt.last_success=null and last_mqtt_message=null, so the cloud poll is the ONLY state-update path for this device. Displayed freshness is also bounded upstream by Govee's gateway->cloud sync cadence: BLE-bridged sensors batch updates ~30-60s+ (undocumented), which is separate from the client. The bug itself was client-side: the coordinator returned the same self._states dict instance each poll while constructed with always_update=False, so HA's identity-based refresh gate never fired async_update_listeners() after the first poll, freezing poll-only (non-MQTT) devices until reload.

**Feature.** Poll-only devices without MQTT push (e.g. H5109 thermometer) now refresh state on every poll instead of freezing after the initial load.

**Resolution.** Fixed by setting coordinator always_update=True (custom_components/govee/coordinator.py:147) so every successful poll notifies listeners despite unchanged dict identity; corrects all poll-only (non-MQTT) devices, not just thermometers. Branch fix/93-thermometer-stale-update; regression test TestCoordinatorAlwaysUpdate::test_always_update_is_true added; mypy/flake8 pass, 790 tests pass. No explicit release version cited in the comments.

_H5109 device profile — capabilities array_

```json
{
  "sku": "H5109",
  "device_type": "devices.types.thermometer",
  "is_group": false,
  "capabilities": [
    {
      "type": "devices.capabilities.property",
      "instance": "sensorTemperature",
      "parameters": {}
    }
  ]
}
```

_H5109 raw_api_state — cloud poll response_

```json
{
  "sku": "H5109",
  "device": "[MAC]",
  "capabilities": [
    {
      "type": "devices.capabilities.online",
      "instance": "online",
      "state": { "value": true }
    },
    {
      "type": "devices.capabilities.property",
      "instance": "sensorTemperature",
      "state": { "value": 80.63 }
    }
  ]
}
```

_H5109 transport / transport_health — no MQTT push path_

```json
{
  "transport": { "cloud_api": true, "mqtt": true, "ble": false },
  "transport_health": {
    "cloud_api": { "is_available": true, "last_success": "[TS]", "last_failure": null },
    "mqtt": { "is_available": true, "last_success": null, "last_failure": null },
    "ble": { "is_available": false, "last_success": null }
  },
  "last_mqtt_message": null
}
```

---

### #92 — Default BLE Integration

- **State:** CLOSED · **Category:** support-request
- **SKUs:** H5075
- **Device types:** thermometer
- **Protocol area:** none

**Finding.** User's Home Assistant integration search only surfaced "Govee Bluetooth (govee_ble)", the first-party BLE integration, not this project. Protocol/packaging fact: this is a custom HACS (cloud) integration and does not appear in HA's built-in "Add Integration" search until installed via HACS and HA is restarted, after which it shows as "Govee Cloud Integration". govee_ble and Govee Cloud Integration are independent and can run side by side. For a Bluetooth-only H5075 thermometer, the built-in govee_ble is recommended (local, cloud-free); the cloud integration here is for WiFi/cloud devices (lights, plugs, hub-based sensors). No API/MQTT/BFF protocol details in this issue.

**Resolution.** Closed as answered (no code change). Maintainer explained the distinction between the built-in govee_ble BLE integration and this HACS cloud integration, and provided HACS install steps (Custom repositories -> add https://github.com/lasswellt/govee-homeassistant, Category Integration -> install "Govee Cloud Integration" -> restart HA -> add via Settings > Devices & Services with Govee API key).

---

### #91 — H5107 is not calculating properly

- **State:** CLOSED · **Category:** support-request
- **SKUs:** H5107
- **Device types:** devices.types.thermometer
- **Protocol area:** platform-api, device-profile, mqtt

**Finding.** The Govee Platform API returns the H5107 thermometer's `sensorTemperature` value (via `devices.capabilities.property` instance `sensorTemperature`) as a bare number in whatever unit the device is configured to in the Govee mobile app (Fahrenheit in this case, e.g. 84.2 = 29.0 C). There is NO unit field in the capability state, so the integration cannot auto-detect the unit and defaults to interpreting the number as Celsius, producing inflated readings. `sensorHumidity` has no unit ambiguity and is always correct. Root cause is identical to issues #78 and #85. Additionally confirmed (per #83): Govee never pushes thermometer readings over MQTT — `last_mqtt_message` is null and `transport_health.mqtt.last_success` is null; thermometer values update only on the cloud poll cadence. This is expected, not a fault.

**Resolution.** Resolved by configuration (no code change / no version bump). User must set the integration option "Temperature unit from Govee API (thermometers)" to Fahrenheit (Configure -> the H5107 then converts 84.2 F -> 29.0 C). The config option already existed; same fix path as #78/#85.

_H5107 thermometer capabilities (device discovery profile)_

```json
{
  "sku": "H5107",
  "device_type": "devices.types.thermometer",
  "is_group": false,
  "capabilities": [
    {"type": "devices.capabilities.property", "instance": "sensorTemperature", "parameters": {}},
    {"type": "devices.capabilities.property", "instance": "sensorHumidity", "parameters": {}}
  ]
}
```

_H5107 raw_api_state (Platform API poll response; temperature returned in device's app-configured unit, no unit field; 84.2 = degrees F)_

```json
{
  "sku": "H5107",
  "device": "[MAC]",
  "capabilities": [
    {"type": "devices.capabilities.online", "instance": "online", "state": {"value": true}},
    {"type": "devices.capabilities.property", "instance": "sensorTemperature", "state": {"value": 84.2}},
    {"type": "devices.capabilities.property", "instance": "sensorHumidity", "state": {"value": 53.9}}
  ]
}
```

_H5107 transport_health showing MQTT never delivers thermometer data (last_mqtt_message null, mqtt.last_success null)_

```json
{
  "last_mqtt_message": null,
  "transport": {"cloud_api": true, "mqtt": true, "ble": false},
  "transport_health": {
    "cloud_api": {"is_available": true, "last_success": "[TIMESTAMP]", "last_failure": null},
    "mqtt": {"is_available": true, "last_success": null, "last_failure": null},
    "ble": {"is_available": false, "last_success": null}
  }
}
```

---

### #88 — Default BLE Integration

- **State:** CLOSED · **Category:** support-request
- **SKUs:** H5075, H5151
- **Device types:** thermometer, sensor
- **Protocol area:** ble, platform-api

**Finding.** H5075 is a Bluetooth-only temperature/humidity sensor. This cloud integration (Govee API key + optional account login) only sees a value that has reached Govee's cloud, on Govee's slow cloud refresh cadence (often many minutes; ref #83). It will only see an H5075 at all if the sensor is bridged to the Govee cloud via a gateway device (e.g. H5151), and even then only on that slow cadence. Home Assistant's first-party govee_ble integration reads the sensor's raw Bluetooth broadcast locally (~every 2 seconds, no cloud), so it is the better path for a thermometer when the device is in BLE range of the HA host or an ESPHome Bluetooth proxy. The two integrations do not hard-override each other — they create separate entities for the same device, and both can run side by side (hide/disable whichever you don't want).

**Resolution.** Closed as answered (no code change). Guidance: use govee_ble for H5075/BLE-only thermometers (fast local reads); keep this cloud integration for lights/plugs/etc. Cloud integration only surfaces an H5075 when bridged through a Govee cloud gateway (e.g. H5151), and only on the slow cloud cadence.

---

### #87 — H5044 Hub with H5059 water sensor support?

- **State:** CLOSED · **Category:** feature
- **SKUs:** H5059, H5044
- **Device types:** devices.types.sensor
- **Protocol area:** mqtt, bff-internal-api, device-profile, platform-api

**Finding.** H5059 water leak sensor (paired to an H5044 hub) support. Key protocol facts confirmed against real debug logs + diagnostics captures:

1. DISCOVERY: Unlike the H5054 (issue #62), the H5059 IS returned by the Govee Developer API. It reports as type=devices.types.sensor with a single capability type=devices.capabilities.event, instance=bodyAppearedEvent, offering options LEAKED (value 1) and UN_LEAKED (value 2), each carrying a probesState {top, bot} (1=leak, 0=clear). This is only the capability *definition* — it does not carry runtime state.

2. RUNTIME STATE DELIVERY: Leak state does NOT come via the state poll. It arrives in real time as multiSync packets pushed from the H5044 hub (hub MAC starts 07:...) over MQTT. The hub packet carries a slot/sno index identifying which paired sensor tripped.

3. SENSOR->SLOT MAPPING: The (hub, slot/sno) -> which H5059 mapping is built from Govee's BFF/account API census, which returns each sensor's sno (0-4 for 5 sensors) and gateway_sku (H5044). Before the fix, H5059 was missing from LEAK_SENSOR_SKUS (which listed only H5058/H5054/H5055), so the BFF parser dropped all H5059s and the census showed "0 leak sensors and 0 hubs"; every hub event then logged "Leak event for unknown sensor" and was dropped.

4. WET DECODE (0xEE 0x34 packet): The multiSync leak packet subtype is 0xEE 0x34. Byte 1 (after ee34) = slot index. Byte 5 = battery percent (0x64 = 100) — the old decoder wrongly read is_wet from byte 5, so it was ALWAYS False. The real probe wet state is in bytes 14 and 16 (1=wet, 0=dry). Fix ORs byte 5 (legacy SKUs) with bytes 14/16 (H5059) so no existing sensor loses detection.

5. BYTES 14/16 ARE NOT PER-PROBE: A targeted single-probe (bottom-only) trip proved bytes 14 and 16 always flip together (both -> 1 on wet, both -> 0 on clear). They are redundant overall-wet flags, not independent top/bot probes. Therefore the H5059 reports a single wet state over multiSync — no per-probe top/bot attribute can be exposed; one moisture binary_sensor is the complete correct surface. (The event-capability definition advertises probesState top/bot, but the hub multiSync stream does not distinguish them.)

6. 0xEE 0x35 PACKET: Initially suspected to be the wet alarm; proven NOT. It fires only on the CLEAR edge with an all-zero payload — a return-to-normal/heartbeat digest. Building detection on ee35 would never have fired.

**Feature.** Added H5059 water leak sensor support (moisture binary_sensor) when paired to an H5043/H5044 hub, with real-time wet/dry detection via hub MQTT multiSync packets.

**Resolution.** Fixed on branch fix/87-h5059-leak-sensor-support and shipped in v2026.6.2. (v2026.6.1 first shipped the leak-sensor diagnostics capture — recent hub packets incl. ee35/ee34 bytes plus a privacy-safe BFF census summary — used to gather the raw data; the version was mentioned as 2026.5.17 pre-release but landed as 2026.6.1/2026.6.2 after the month rolled to June.) Changes: added "H5059" to LEAK_SENSOR_SKUS in models/device.py; decode wet from bytes 14/16 in api/mqtt.py; regression tests in tests/test_mqtt_multisync.py and tests/test_auth.py built from the reporter's real packets. Validated in production against a single-probe capture: all 5 H5059s discovered (sno 0-4, gateway_sku H5044), correct named entity ("Laundry", sno=2) went wet in HA, wet decode reads bytes 14/16 and ignores the 0x64 battery byte.

_H5059 device capability from Developer API (data.devices[]) — bodyAppearedEvent event capability_

```json
{
  "sku": "H5059",
  "device": "[MAC]",
  "deviceName": "[NAME]",
  "type": "devices.types.sensor",
  "capabilities": [
    {
      "type": "devices.capabilities.event",
      "instance": "bodyAppearedEvent",
      "alarmType": 1,
      "eventState": {
        "options": [
          {
            "name": "LEAKED",
            "value": 1,
            "message": "Leaked, probesState indicates the current overall status of the probe; bot stands for the lower probe, top for the upper probe, 1 means water leakage, and 0 means the leakage is cleared.",
            "probesState": { "top": 1, "bot": 1 }
          },
          {
            "name": "UN_LEAKED",
            "value": 2,
            "message": "Un_Leaked, probesState indicates the current overall status of the probe; bot stands for the lower probe, top for the upper probe, 1 means water leakage, and 0 means the leakage is cleared.",
            "probesState": { "top": 0, "bot": 0 }
          }
        ]
      }
    }
  ]
}
```

_H5044 hub multiSync leak packet (0xEE 0x34) raw byte layout — byte1=slot, byte5=battery(0x64=100), bytes14/16=wet flag (1=wet,0=dry, always paired)_

```json
// slot0 LEAK   ee34 00 ...64... 01 03 01 ...   b14=01 b16=01  -> wet
// slot0 CLEAR  ee34 00 ...64... 00 03 00 ...   b14=00 b16=00  -> dry
// slot2 LEAK   ee34 02 ...64... 01 03 01 ...   b14=01 b16=01  -> wet

// June-5 single-probe (bottom) trip, sensor "Laundry" sno=2:
// WET   ee3402...0001030180003c   byte5=0x64  b14=1 b16=1  -> is_wet=True, slot=2
// CLEAR ee3402...0000030080003c   byte5=0x64  b14=0 b16=0  -> is_wet=False

// 0xEE 0x35 packet: fires only on CLEAR edge, all-zero payload = return-to-normal/heartbeat, NOT the wet alarm
```

---

### #86 — H5301 thermometer support

- **State:** CLOSED · **Category:** feature
- **SKUs:** H5310, H5301, H5044, H5054
- **Device types:** thermometer, devices.types.thermometer
- **Protocol area:** bff-internal-api, platform-api, device-profile, state-management

**Finding.** WiFi battery thermo-hygrometers (H5301/H5310) are NOT returned by the Govee Developer API (/user/devices), which only exposes a curated cloud device set. They ARE present in the second, fuller account-login (BFF/app) device list — the same path the H5054 leak sensors use — so the integration synthesizes a thermometer device from that list with no BLE proxy/gateway hardware needed. The BFF reading carries temperature/humidity as fields `tem`/`hum` (canonical Celsius, treated as the real datapoint), plus a battery level that Govee reported all along. Additional BFF fields observed and captured to debug logs but NOT applied: `fahOpen` (°F display flag) and `temCali`/`humCali` (temperature/humidity calibration offsets) — deliberately not transformed without confirmation to avoid wrong readings. The H5310 (GoveeLife Smart Thermometer P2) is a temperature-only pool probe bridged through an H5044 gateway; humidity is suppressed for it (see #97), and it nests under its H5044 gateway in the HA device tree. The gateway's `online` flag flaps unreliably, so readings are kept available despite online=false. Debug discovery logs the flow as `BFF thermo-hygrometer ... lastDeviceData keys=...`. Diagnostics dumps include a privacy-safe `bff_device_census` section (SKU-only, MAC/name/API-key redacted); each entry has a `sku` and an `in_thermo_hygro_skus` boolean used to confirm reachability + recognition. Note H5310 shows `in_thermo_hygro_skus: false` yet still reads correctly once its SKU is matched. The original report was an H5310 (transposed digit from H5301); no real H5301 owner confirmed its payload.

**Feature.** Added H5310 (and H5301) pool/thermo-hygrometer support via the account BFF device list — temperature + battery sensors, humidity suppressed for temp-only probes, nested under the H5044 gateway.

**Resolution.** H5310 support shipped and confirmed working by multiple users. Timeline: v2026.6.9 first added BFF-list thermometer synthesis matching only H5301; v2026.6.10 added H5310 SKU + hardened temperature scaling for sub-zero/near-0C (freezer) readings; v2026.6.12 completed the work — temperature reads correctly, humidity suppressed for the temp-only pool probe (#97), battery sensor surfaced for H5301/H5310, H5310 nested under H5044 gateway, readings stay available through the gateway's flapping online flag, and HA updates only push on actual value change. Requires account email+password login (2FA) configured — API-key-only setups cannot reach the BFF list. Users note the 'auto' temperature scale may be needed. Closed as resolved; H5301 itself never verified by a real owner (original report was an H5310).

_diagnostics bff_device_census entry (SKU-only, redacted) — used to confirm device reachability/recognition_

```json
{
  "bff_device_census": [
    { "sku": "H5310", "in_thermo_hygro_skus": false },
    { "sku": "H5301", "in_thermo_hygro_skus": true }
  ]
}
```

_BFF thermo-hygrometer reading fields (from lastDeviceData) — verbatim field names referenced in comments_

```json
{
  "tem": "<temperature, canonical Celsius>",
  "hum": "<humidity>",
  "fahOpen": "<°F display flag, captured to debug log, not applied>",
  "temCali": "<temperature calibration offset, not applied>",
  "humCali": "<humidity calibration offset, not applied>",
  "online": false
}
```

---

### #85 — H5103 Temperature wrong Shown 74,1C, real in Govee App 23,4C

- **State:** CLOSED · **Category:** api-limitation
- **SKUs:** H5103
- **Device types:** thermometer, wifi-temperature-humidity-sensor
- **Protocol area:** platform-api, state-management

**Finding.** The Govee Platform API returns thermometer temperature readings WITHOUT any unit field. The H5103 (WiFi Temperature/Humidity Sensor) reports its value in whatever temperature unit the Govee app is set to for that device (here Fahrenheit, e.g. raw 74.1), while the integration defaults to interpreting API readings as Celsius — producing a wrong 74.1°C instead of 23.4°C. Because the API omits the unit, the integration cannot auto-detect it; a hub-level option "Temperature unit from Govee API (thermometers)" must be set to Fahrenheit so readings are converted (74.1°F -> 23.4°C). Confirmation: 74.1°F = 23.38°C.

**Resolution.** Not a code bug — closed as resolved-by-configuration. Users with a thermometer that reports Fahrenheit must open the hub's Configure (gear) and set "Temperature unit from Govee API (thermometers)" to Fahrenheit; the integration then converts F->C. This option already existed (issue reported on v2026.5.9); no new version shipped for this issue.

---

### #84 — Ambient light control (not main) Ceiling Light Pro

- **State:** CLOSED · **Category:** feature
- **SKUs:** H60A6
- **Protocol area:** device-profile

**Finding.** The H60A6 "Ceiling Light Pro" has two physical light sources — a main light and an ambient/backlight ring near the ceiling — but the integration only maps the main light. The maintainer hypothesized the ambient channel could appear as (a) a second set of capabilities under a distinct `instance` (e.g. separate brightness/colorRgb), (b) a segment-style sub-light, or (c) a distinct device entry, and requested a diagnostics dump to confirm. The reporter's diagnostics (attached as a file, not inlined in the issue text) confirmed the ambient/backlight is exposed as a single separate capability instance named `backgroundLightToggle` — i.e. an on/off toggle for the ambient ring, NOT a full second color/brightness light. This means ambient control on this SKU is (at least in the reported data) just a power toggle under a dedicated instance rather than a full independent light with its own brightness/color instances. The full capability array JSON was only available as an external GitHub attachment and is not embedded in the issue, so the complete capability shape (type, parameters) is not present in the file.

**Feature.** Support toggling the ambient/backlight ring on Ceiling Light Pro (H60A6) via the backgroundLightToggle capability

**Resolution.** Issue CLOSED. Root cause identified: the ambient light is surfaced as a separate capability instance `backgroundLightToggle` (a distinct on/off toggle for the backlight ring), confirmed by the reporter's diagnostics attachment. No shipped version string (vYYYY.M.P) appears in the issue body or comments, so the exact release that added the control could not be determined from the file.

_Ambient/backlight capability instance for H60A6 (fragment quoted in comment; full capability array was in an external diagnostics attachment not inlined)_

```json
{ "instance": "backgroundLightToggle" }
```

---

### #83 — Temperature/humidity sensors not being updated

- **State:** CLOSED · **Category:** bug
- **SKUs:** H5179, H5110, H5075, H5151, H5111
- **Device types:** devices.types.thermometer
- **Protocol area:** platform-api, bff-internal-api, mqtt, device-profile, state-management

**Finding.** Thermometer read-path facts for Govee BLE/WiFi thermo-hygrometers (device_type devices.types.thermometer):

1. AWS IoT MQTT carries NO thermometer data at all. Diagnostics from multiple reporters consistently show last_mqtt_message: null, mqtt.last_success: null, and account-wide tracked_devices: 0, even with MQTT connected. The integration subscribes to one account-wide topic (not per-device); nothing is mis-wired — Govee simply never pushes temp/humidity over IoT. Cross-confirmed by govee2mqtt flagging these SKUs iot_api_supported: false / ble_only: true. (Historical: update_from_mqtt originally parsed only onOff/brightness/color/colorTemInKelvin and dropped any sensor reading; v2026.5.10 added sensorTemperature/sensorHumidity to the MQTT path, but this is moot since MQTT never carries the data.)

2. Temp/humidity come ONLY from the Developer (Platform REST) API poll. Developer API exposes just two capabilities per thermometer: type=devices.capabilities.property instance=sensorTemperature and instance=sensorHumidity (params={}). No battery, no unit field.

3. "Frozen readings" root cause: Govee's cloud only refreshes these values on its own cadence. WiFi-native (H5179) ~10 min; BLE sensors via an H5151 gateway (H5075/H5110) batch-upload every 15-60 min. The poll is healthy — it returns the newest value Govee has.

4. KEY INSIGHT (davcamer): polling the BFF internal API tickles/forces Govee's cloud to refresh the temp/humidity values exposed on the Developer API. The 5-min BFF poll loop previously started only for accounts with leak sensors (or H5301/H5310); accounts with only thermometers never triggered it, so their readings stayed stale. v2026.6.13 starts the 5-min BFF poll for any BLE-bridged thermometer and reads the live lastDeviceData reading directly from the BFF response. Result: ~5-min update cadence. Requires account (email/password) login (unlocks BFF); API-key-only setups cannot use this path.

5. BFF scaling: temperature in hundredths of °C, humidity in tenths of % (confirmed on H5110). A v2026.6.13 regression read humidity 10x too high; v2026.6.14 (#102) fixed it by sourcing the value from the correctly-scaled Developer API.

6. Battery is exposed only via the BFF deviceSettings (same source as leak sensors / H5301), NOT the Developer API. v2026.6.20 reads battery from BFF deviceSettings and creates a Battery diagnostic sensor for BLE-bridged thermometers.

7. Offline-device crash: when a device is offline, Govee's cloud returns an empty string "" for some capability values; parser did int("") -> ValueError, which failed the ENTIRE state fetch for that device until restart ("Failed to fetch state for [MAC]: invalid literal for int() with base 10: ''"). Tolerated as of v2026.5.15.

8. Fahrenheit is returned by the API without a unit field (also seen in govee2mqtt #206); user must set api_temperature_unit=fahrenheit to convert correctly.

9. Device-model notes: H5111 is temperature-only (fridge/freezer/probe) — Developer API payload has no sensorHumidity and no battery, so no such entities can be created (expected, not a bug). BLE-bridged thermometer device IDs use the 8-octet MAC form (e.g. 7E:69:D8:BF:C4:86:36:62). For true ~2s updates users are pointed to first-party govee_ble + ESPHome Bluetooth proxy (bypasses cloud entirely).

**Feature.** BLE-bridged Govee thermometers (H5075/H5110/H5179 via H5151 gateway) now refresh temp/humidity on a ~5-min cadence via a BFF API tickle, expose a Battery sensor from BFF deviceSettings, show a "Last Changed" staleness timestamp, and no longer stop updating when a device goes offline.

**Resolution.** Resolved (closed v2026.6.20). Progression: v2026.5.10 wired sensorTemperature/sensorHumidity into MQTT path (moot — MQTT carries no sensor data); v2026.5.13 added state/raw_api_state/last_mqtt_message to diagnostics; v2026.5.14 added a "Last Changed" diagnostic timestamp; v2026.5.15 tolerated empty-string "" values from offline devices (fixed int('') crash that killed the whole device state fetch); v2026.6.13 started the 5-min BFF poll for BLE-bridged thermometers even without leak sensors and read the live lastDeviceData reading (the BFF "tickle" forces the Developer API value to refresh — reverses the earlier "Govee cloud limitation, won't fix" conclusion); v2026.6.14 (#102) fixed a 10x humidity scale regression; v2026.6.20 added a Battery sensor sourced from BFF deviceSettings. Confirmed working by davcamer (H5110 via H5151, 5-min cadence) and mattdengler (pool thermometer + R1 Lite temp). Requires account email/password login for the BFF path.

_Developer (Platform REST) API device discovery — thermometer capabilities (from debug log)_

```json
Device: [NAME] ([MAC]) type=devices.types.thermometer is_group=False
  Capability: type=devices.capabilities.property instance=sensorTemperature params={}
  Capability: type=devices.capabilities.property instance=sensorHumidity params={}
```

_Offline-device parse crash (empty-string capability value from cloud)_

```json
Failed to fetch state for [MAC]: invalid literal for int() with base 10: ''
```

_Diagnostics diff — poll healthy (last_success advances) but no new sensor value; MQTT tracked_devices flips 0->1_

```json
< "last_success": "2026-05-29T15:39:54.462805+00:00",
> "last_success": "2026-05-29T15:44:54.458978+00:00",
< "tracked_devices": 0
> "tracked_devices": 1
```

---

### #74 — H1310 Ceiling Fan missing functions to control the fan

- **State:** CLOSED · **Category:** feature
- **SKUs:** H1310
- **Device types:** devices.types.light
- **Protocol area:** device-profile, state-management

**Finding.** The Govee H1310 is a ceiling fan with an integrated light. Govee's Developer API v2.0 classifies it as `devices.types.light` (not `devices.types.fan` / air-purifier), because of the integrated light. As a result the integration only created a light entity and never a fan entity, since fan detection keyed on the fan device type. The H1310 exposes its fan under a DIFFERENT capability shape than Govee's standalone fans (which use workMode / fanSpeed / oscillationToggle):
- type=`devices.capabilities.toggle`, instance=`fanToggle` — fan on/off
- type=`devices.capabilities.mode`, instance=`fanSpeedMode` — 6 discrete speeds (Speed 1-6)
- type=`devices.capabilities.toggle`, instance=`reverseAirflowToggle` — reverse airflow (direction forward/reverse)
Govee's cloud poll does NOT report fan state for this device, so fan state must be treated as optimistic/restored across restarts (same approach as night-light controls). Detection rule adopted: create a ceiling-fan entity for any device exposing `fanToggle` + `fanSpeedMode` (regardless of the light device type). Reverse-airflow/direction is enabled only when `reverseAirflowToggle` is present.
NOTE: The device diagnostics that confirmed this were provided as a downloaded/redacted JSON file attachment (github user-attachments link), so no raw capability JSON is inlined in the issue body/comments.

**Feature.** Added H1310 ceiling fan support: a dedicated Fan entity (alongside the light) with on/off, 6-speed control, and forward/reverse direction.

**Resolution.** Fixed on branch `fix/74-h1310-ceiling-fan` (ships next release; no explicit version tag in comments). Added a new `GoveeCeilingFanEntity` created for any device exposing `fanToggle` + `fanSpeedMode`: on/off, 6-speed control mapped to HA percentage slider, and forward/reverse direction when `reverseAirflowToggle` present. Uses optimistic state restored across restarts because the cloud poll returns no fan state. Files: `custom_components/govee/models/device.py` (supports_ceiling_fan / supports_reverse_airflow / get_ceiling_fan_speed_options), `custom_components/govee/fan.py` (new entity), `tests/test_fan.py`. Type-check/lint PASS, 770 tests PASS. Reporter (BigEZ78) confirmed it works.

---

### #70 — Custom Device Grouping

- **State:** CLOSED · **Category:** feature
- **Device types:** group
- **Protocol area:** platform-api, state-management

**Finding.** Govee app-created custom groups are delivered to the integration as single virtual devices (per CLAUDE.md these have numeric-only IDs). Key protocol behavior: controlling a group entity sends ONE control command to the group device and Govee's cloud fans it out to all member lights, so members change together — unlike an HA helper which fires a separate command per light (those arrive at slightly different times over Wi-Fi, causing the sync delay the user reported). Limitations of group devices: (1) groups cannot be polled by the API, so group state is best-effort and may not reflect out-of-band changes (e.g. from the Govee app) — control is reliable, live state is not; (2) group entities support power/brightness/color only — no scenes, segments, music, or DreamView. Whether DIY scenes can be applied to a group device is undocumented in the Govee API and is currently blocked in the integration; it needs live testing (a debug log showing whether a DIY-scene command sent to a group succeeds) before it can be built.

**Resolution.** No code change shipped. Group support already exists in the integration, gated behind the "Enable group devices" option (default off; Configure → enable "Enable group devices"). DIY-scene-on-group support is blocked pending live debug-log testing against a real group device and is tracked as a follow-up.

---

### #62 — Respectfully requesting support for H5054 Water detector and H5109 Smart temperature sensor

- **State:** CLOSED · **Category:** feature
- **SKUs:** H5054, H5109, H5040, H5042, H5083
- **Device types:** devices.types.sensor, devices.types.thermometer, devices.types.socket
- **Protocol area:** device-profile, bff-internal-api, platform-api, mqtt, state-management

**Finding.** Two distinct device classes clarified:

H5109 Smart Thermometer (device_type devices.types.thermometer): fully supported through the Developer (API-key) platform API. It exposes property capabilities `sensorTemperature` and `sensorHumidity`, polled on the normal cycle and pushed via MQTT. Detection is capability-based (any H51XX exposing these instances gets temp/humidity entities). In the BFF census H5109 carries has_sno=true (sno=1) and gateway_sku=H5042.

H5054 Water Detector (device_type devices.types.sensor): a 433 MHz radio-only sensor with NO WiFi and NO Bluetooth. Signal path is H5054 --433MHz--> H5040 WiFi gateway --> Govee cloud --> phone notification; requires the H5040 gateway.
- Key discovery quirk: the H5054 does NOT initially appear in the Developer API device list (integration-level diagnostics did not include them), yet it does surface via the account/BFF API as devices.types.sensor. When it is returned, its only control capability in the Developer API is a single generic event: `devices.capabilities.event` / instance `bodyAppearedEvent` (Govee reuses the generic bodyAppearedEvent instance for the water trigger, not a water-specific name like waterFullEvent/leakEvent). Its raw_api_state carries only `devices.capabilities.online`.
- The leak alert is NEVER delivered over MQTT/AWS IoT: debug log shows `Device <id> has no MQTT topic in response` for every H5054. The wet event instead lives in the account's cloud warn/message history, reachable only via the account/BFF API (same path the Govee app and Homebridge use).
- Leak-alert entry shape (from account API warnMessage history): a list of {message, time (epoch ms), read} objects, e.g. message "Leakage alert: <name> has detected water leakage...", read=true. Debug line format: `warnMessage for <device> (H5054): N entries raw=[...]`. Alert clears when read/cleared in the Govee app.
- BFF census exposes per-device discovery flags: in_leak_sensor_skus, in_leak_hub_skus, has_sno, sno, has_gateway_info, gateway_sku. H5054 = in_leak_sensor_skus:true, no gateway_info, no sno.
- BFF device object nests deviceExt.deviceSettings._json_str (wifiSoftVersion, wifiMac, topic, matterId, etc.) and deviceExt.lastDeviceData._json_str.online.

Diagnostics tooling: v2026.6.1 added a privacy-safe account-API capture (bff_device_census, bff_response_skeleton, leak_sensors summary) to the diagnostics download to scope this without leaking MACs/names.

**Feature.** Added H5109 temperature/humidity sensors and H5054 water-leak detectors (leak state via account/BFF warnMessage polling through the H5040 gateway).

**Resolution.** Both halves shipped and issue CLOSED. H5109 temperature + humidity sensors shipped in 2026.5.2 (commit b7592ae), capability-based, confirmed working by reporter. H5054: a moisture binary_sensor (device_class moisture, Wet/Dry) was first added in v2026.6.6 based on the Developer-API bodyAppearedEvent capability, but it never flipped because the H5054 has no MQTT topic (433 MHz-only). Real fix shipped in v2026.6.8: the integration now polls the account/BFF API leak-alert (warnMessage) history for H5054 devices; leak lives in account cloud history relayed via the H5040 WiFi gateway. Reporter confirmed the alert reaching the HA log under v2026.6.8 (`(H5054): 1 entries raw=[{'message':'Leakage alert...','time':1780705560000,'read':True}]`). Requires the H5040 gateway; without it the H5054s are silent to everything except their local 100 dB alarm.

_H5054 Developer-API capability (device profile) — generic event reused for water trigger_

```json
{"type":"devices.capabilities.event","instance":"bodyAppearedEvent","parameters":{}}
```

_H5054 raw_api_state — only online capability returned by platform API_

```json
{"sku":"H5054","device":"[MAC]","capabilities":[{"type":"devices.capabilities.online","instance":"online","state":{"value":false}}]}
```

_H5109 thermometer capabilities (platform API property instances)_

```json
[{"type":"devices.capabilities.property","instance":"sensorTemperature"},{"type":"devices.capabilities.property","instance":"sensorHumidity"}]
```

_Account/BFF leak-alert (warnMessage) history entry for H5054_

```json
raw=[{"message":"Leakage alert: [NAME] has detected water leakage. Check the area as soon as possible.","time":1780705560000,"read":true}]
```

_BFF device census entries (discovery flags per device) — from v2026.6.1 diagnostics capture_

```json
[{"sku":"H5109","in_leak_sensor_skus":false,"in_leak_hub_skus":false,"has_sno":true,"sno":1,"has_gateway_info":true,"gateway_sku":"H5042"},{"sku":"H5054","in_leak_sensor_skus":true,"in_leak_hub_skus":false,"has_sno":false,"sno":null,"has_gateway_info":false,"gateway_sku":null}]
```

_BFF response skeleton — data.devices[].deviceExt shape (account API)_

```json
{"status":"int","message":"str","data":{"devices":[{"deviceId":"int","groupId":"int","sku":"str","device":"[MAC]","spec":"str","versionHard":"str","versionSoft":"str","deviceName":"[NAME]","pactType":"int","pactCode":"int","deviceExt":{"deviceSettings":{"_json_str":{"wifiSoftVersion":"str","wifiHardVersion":"str","bleHardVersion":"str","wifiMac":"str","bleName":"str","matterId":"str","topic":"str","sku":"str","device":"[MAC]"}},"lastDeviceData":{"_json_str":{"online":"bool"}},"extResources":{"_json_str":{"skuUrl":"str","ic":"int"}}}}],"groups":[{"groupId":"int","groupName":"str"}]}}
```

_MQTT discovery failure fact (not JSON) — H5054 has no realtime topic_

```json
Device DABFC0D6A5FE0008E8 has no MQTT topic in response
```

---

### #57 — LAN API Support

- **State:** OPEN · **Category:** feature
- **SKUs:** H6072, H6022, H619A, H6175, H618A, H6076, H7093, H60A1, H707B
- **Device types:** light, led-strip
- **Protocol area:** lan, state-management

**Finding.** Govee LAN API local control protocol facts learned while building native LAN transport into the integration:

DISCOVERY / PORTS
- Standard discovery is a multicast scan to 239.255.255.250:4001 with payload {"msg":{"cmd":"scan","data":{"account":"IH"}}}. Devices answer on UDP 4002. Control commands go to the device's UDP port 4003.
- NEWER devices (H707B / H707 "prism") IGNORE the multicast scan entirely and only answer a subnet BROADCAST to x.x.x.255:4001. The integration was updated to auto-derive each adapter's subnet broadcast and scan it alongside multicast (no config).
- Newer firmware also requires a RECENT scan before it will accept local control ("needs a recent scan"/handshake behaviour). rac146 confirmed: sending the broadcast scan first, then a turn command to :4003, made the H707B accept LAN control where a bare turn command failed. The integration's periodic rescan re-emits the broadcast to re-prime this.
- LAN discovery responses report: ip, device (MAC), sku, bleVersionHard, bleVersionSoft, wifiVersionHard, wifiVersionSoft. Confirmed LAN-capable SKUs from user diagnostics: H6072, H6022, H619A, H6175, H618A, H6076, H7093, H60A1 (and eventually H707B).
- Port conflict: another integration (Govee Lights Local) binding UDP 4002 blocks this integration's LAN status listener; removing it restored LAN.

TRANSPORT HEALTH (verify-by-read)
- After a LAN write the integration reads back devStatus to confirm the write landed within ~0.5s. A freshly-powered Govee controller often cannot answer within that window OR briefly echoes its PRE-command state (a content "value_mismatch"), which was wrongly treated as a transport failure, flipping the per-device LAN connectivity sensor to Disconnected on every power-on and falling back to cloud.
- Fix direction: LAN health is now READ-driven — any devStatus the device answers (even a value_mismatch) keeps LAN Connected. Repeated unconfirmed writes only route that device's writes to MQTT/REST for a short cooldown (write_suppressed=true) and never flip the sensor; LAN re-arms once a write confirms. transport_health.lan.last_failure_reason enum: unconfirmed / value_mismatch / stale_lan.
- Multi-NIC / bridged-network caveat: HA in Docker/HAOS bridge mode often cannot send/receive multicast on the physical LAN even though interface_classes reports "private-192.168 (typical LAN)"; broadcast discovery and direct unicast (LAN device addresses option) are the workarounds.
- Command fallback order is LAN → AWS IoT MQTT → REST.

**Feature.** Native Govee LAN API local control with automatic multicast + subnet-broadcast discovery, read-driven per-device LAN transport health, and LAN → MQTT → REST fallback.

**Resolution.** Shipped incrementally (issue still OPEN as feature keeps evolving): v2026.6.14 added read-only LAN discovery block to diagnostics (one-shot UDP scan reporting SKU/firmware, IPs redacted). v2026.6.22 added hysteresis so a single confirm-miss no longer flips LAN down (requires 3 consecutive, matching the read path). v2026.6.23 fixed the value_mismatch flap by making LAN health read-driven, and added automatic subnet-broadcast discovery (x.x.x.255:4001) so H707B-class devices that ignore multicast are found and re-primed. Users SH1FT-W (H6xxx strips) and rac146 (H707B, after removing a conflicting integration holding port 4002) both confirmed LAN then held Connected. Reference implementation cited: wez/govee2mqtt.

_diagnostics lan_discovery — successful responder (per-device shape, majorsl H6072)_

```json
{"lan_discovery":{"scan_attempted":true,"device_count":4,"devices":[{"ip":"[REDACTED]","device":"[MAC]","sku":"H6072","bleVersionHard":"3.02.00","bleVersionSoft":"2.04.10","wifiVersionHard":"1.00.10","wifiVersionSoft":"1.02.11"}]}}
```

_diagnostics lan_discovery — zero devices with interface/probe diagnostics (rac146 H707B)_

```json
{"lan_discovery":{"scan_attempted":true,"device_count":0,"devices":[],"interface_count":1,"interface_classes":["private-192.168 (typical LAN)"],"extra_target_count":0,"error":null,"probe_attempted":false,"probe_response_count":0,"probe_error":null,"commands_answered":[]}}
```

_diagnostics transport_health.lan block (SH1FT-W, value_mismatch flap)_

```json
{"lan":{"is_available":false,"last_received":"2026-06-27T22:20:31.019006+00:00","last_sent":null,"last_success":"2026-06-27T22:20:31.019006+00:00","last_failure":"2026-06-27T22:20:50.592439+00:00","last_failure_reason":"value_mismatch"}}
```

_raw LAN UDP scan + control commands (rac146; broadcast scan primes then turn works)_

```json
// broadcast scan to port 4001:
{"msg":{"cmd":"scan","data":{"account":"IH"}}}  -> udp x.x.x.255:4001
// then control to device port 4003:
{"msg":{"cmd":"turn","data":{"value":1}}}  -> udp <device-ip>:4003
```

---

### #48 — Add option to set Smart AI Sync Box to "Video" mode.

- **State:** CLOSED · **Category:** feature
- **SKUs:** H6604, H605C, H6199
- **Device types:** devices.types.light
- **Protocol area:** device-profile, platform-api, state-management, ble

**Finding.** Clearing a DIY/light scene (scene select -> None) previously sent ColorCommand(white), which locks a device into manual color mode and produces a flat-white image instead of restoring native Video/Sync mode. Protocol facts learned:

1. On devices that expose the `hdmiSource` capability (Smart AI Sync Box, e.g. H6604), the correct way to return to Video Mode is to re-select the current HDMI source via a `ModeCommand` — this forces the Sync Box back into native Video Mode. Sending a color value instead traps it in manual color.

2. On the H605C RGBIC TV Backlight, screen/video sync is exposed via the `devices.capabilities.toggle` instance `dreamViewToggle` (the DreamView switch entity). Toggling DreamView ON after a DIY scene resumes video sync. H605C capability profile also confirms: on_off/powerSwitch, toggle/gradientToggle, range/brightness, segment_color_setting/segmentedBrightness + segmentedColorRgb (segment array size 1-15, elementRange 0-14 => 15 segments), color_setting/colorRgb (0-16777215) + colorTemperatureK (2000-9000K), dynamic_scene/lightScene + diyScene + snapshot (options empty in diagnostics), music_setting/musicMode (Energic=1,Rhythm=2,Spectrum=3,Rolling=4 with sensitivity/autoColor/rgb fields), toggle/dreamViewToggle. Transport flags: cloud_api=true, mqtt=true, ble=false.

3. LIMITATION: H6199 uses camera-based sync, which is BLE/app-only and NOT exposed to the cloud API — that sync mode cannot be controlled from Home Assistant (noted in protocol reference §8.8).

4. FOLLOW-UP / UNRESOLVED: after the fix shipped, the H6604 reporter says setting the scene to "None" still leaves the last frame of the goal animation spinning and does not reset to Video — so the ModeCommand/hdmiSource re-select fix did not fully restore Video Mode for the Sync Box. Issue was closed despite this incomplete result.

**Feature.** Setting the scene select to None on a Sync Box (device with hdmiSource capability) now re-selects the current HDMI source to return it to Video Mode instead of a flat-white color.

**Resolution.** Partially resolved. Fix committed as a6e9b21 and shipped in v2026.5.2 (maintainer first said 2026.5.1, then corrected to 2026.5.2): clearing a scene on a device with the `hdmiSource` capability now re-selects the current HDMI source via `ModeCommand` instead of sending ColorCommand(white), intended to force the Sync Box back to Video Mode. H605C users are directed to the existing DreamView switch (dreamViewToggle) to resume video sync. H6199 camera-based sync remains unsupported (BLE/app-only, not in cloud API). However the H6604 reporter later reported the fix still does not reset to Video (animation keeps spinning); issue is CLOSED with this regression unaddressed.

_H605C RGBIC TV Backlight device profile (BFF-internal capabilities array + state/transport, PII redacted)_

```json
"[MAC]": {
  "sku": "H605C",
  "name": "[NAME]",
  "device_type": "devices.types.light",
  "is_group": false,
  "capabilities": [
    {"type": "devices.capabilities.on_off", "instance": "powerSwitch", "parameters": {"dataType": "ENUM", "options": [{"name": "on", "value": 1}, {"name": "off", "value": 0}]}},
    {"type": "devices.capabilities.toggle", "instance": "gradientToggle", "parameters": {"dataType": "ENUM", "options": [{"name": "on", "value": 1}, {"name": "off", "value": 0}]}},
    {"type": "devices.capabilities.range", "instance": "brightness", "parameters": {"unit": "unit.percent", "dataType": "INTEGER", "range": {"min": 1, "max": 100, "precision": 1}}},
    {"type": "devices.capabilities.segment_color_setting", "instance": "segmentedBrightness", "parameters": {"dataType": "STRUCT", "fields": [{"fieldName": "segment", "size": {"min": 1, "max": 15}, "dataType": "Array", "elementRange": {"min": 0, "max": 14}, "elementType": "INTEGER", "required": true}, {"fieldName": "brightness", "dataType": "INTEGER", "range": {"min": 0, "max": 100, "precision": 1}, "required": true}]}},
    {"type": "devices.capabilities.segment_color_setting", "instance": "segmentedColorRgb", "parameters": {"dataType": "STRUCT", "fields": [{"fieldName": "segment", "size": {"min": 1, "max": 15}, "dataType": "Array", "elementRange": {"min": 0, "max": 14}, "elementType": "INTEGER", "required": true}, {"fieldName": "rgb", "dataType": "INTEGER", "range": {"min": 0, "max": 16777215, "precision": 1}, "reauired": true}]}},
    {"type": "devices.capabilities.color_setting", "instance": "colorRgb", "parameters": {"dataType": "INTEGER", "range": {"min": 0, "max": 16777215, "precision": 1}}},
    {"type": "devices.capabilities.color_setting", "instance": "colorTemperatureK", "parameters": {"dataType": "INTEGER", "range": {"min": 2000, "max": 9000, "precision": 1}}},
    {"type": "devices.capabilities.dynamic_scene", "instance": "lightScene", "parameters": {"dataType": "ENUM", "options": []}},
    {"type": "devices.capabilities.music_setting", "instance": "musicMode", "parameters": {"dataType": "STRUCT", "fields": [{"fieldName": "musicMode", "dataType": "ENUM", "options": [{"name": "Energic", "value": 1}, {"name": "Rhythm", "value": 2}, {"name": "Spectrum", "value": 3}, {"name": "Rolling", "value": 4}], "required": true}, {"unit": "unit.percent", "fieldName": "sensitivity", "dataType": "INTEGER", "range": {"min": 0, "max": 100, "precision": 1}, "required": true}, {"fieldName": "autoColor", "dataType": "ENUM", "options": [{"name": "on", "value": 1}, {"name": "off", "value": 0}], "required": false}, {"fieldName": "rgb", "dataType": "INTEGER", "range": {"min": 0, "max": 16777215, "precision": 1}, "required": false}]}},
    {"type": "devices.capabilities.dynamic_scene", "instance": "diyScene", "parameters": {"dataType": "ENUM", "options": []}},
    {"type": "devices.capabilities.dynamic_scene", "instance": "snapshot", "parameters": {"dataType": "ENUM", "options": []}},
    {"type": "devices.capabilities.toggle", "instance": "dreamViewToggle", "parameters": {"dataType": "ENUM", "options": [{"name": "on", "value": 1}, {"name": "off", "value": 0}]}}
  ],
  "state": {"online": true, "power_state": false, "brightness": 100, "color": [255, 208, 151], "color_temp_kelvin": null, "source": "api"},
  "transport": {"cloud_api": true, "mqtt": true, "ble": false}
}
```

---

### #24 — One device is showing up as powered on, but it's not

- **State:** CLOSED · **Category:** bug
- **SKUs:** H6104, H6159
- **Device types:** devices.types.light
- **Protocol area:** mqtt, platform-api, state-management, device-profile

**Finding.** Two distinct protocol facts about the H6104 (a BLE-only "proType":0 backlight strip) plus contrasting behavior of the WiFi H6159 ("proType":2).

1. BRIGHTNESS RANGE: The H6104 reports brightness on a native 0-254 scale, not 0-100. Both REST poll and MQTT status show brightness=254 at max. The integration treated it as a 0-100 value, computing 254/100 x 255 = 647, which HA rendered as ~255%. Fix = clamp both device->HA and HA->device brightness conversions.

2. H6104 MQTT STATUS SHAPE (proType 0): The H6104 DOES emit real-time status on topic GA/<hash>, but in a legacy nested form the integration does not parse. The actual power is in a stringified inner JSON: msg -> data -> "turn" (1/0), with "brightness":254, "cmd":"status", "type":0, "sku":"H6104". A second variant of the same message adds a top-level "state":{"onOff":0/1,"brightness":254,...}. BOTH H6104 variants are classified by the integration as "command/response" and IGNORED ("Ignoring command/response message"), so no MQTT correction is ever applied to the H6104.

3. H6159 MQTT STATUS SHAPE (proType 2): By contrast the WiFi H6159 sends a clean top-level "state":{"onOff","brightness","colorTemInKelvin","color":{r,g,b},"mode","result"} plus "op":{"command":[base64 BLE frames]}, "softVersion"/"wifiSoftVersion"/"wifiHardVersion", "pactType","pactCode". These ARE parsed and applied ("MQTT state update ... power=X").

4. H6104 REST POWER STATE IS UNRELIABLE: Because the H6104's MQTT is ignored, its power comes only from the ~60s REST poll, which returns wrong/stale power. Initially it returned power=true after the user turned it OFF (app showed off, HA showed on). Later, after integration/version changes, it inverted: log at 22:22 shows "API state change for [H6104]: power True -> False (was source=optimistic)" while the app/physical device were ON. The stale REST poll overwrites the optimistic state each cycle. Device is mains-powered (powerbox, not TV-USB), ruling out the "TV re-powers strip" explanation.

**Feature.** Clamp brightness conversion so devices reporting brightness outside 0-100 (e.g. H6104 max=254) no longer display over 100%.

**Resolution.** PARTIAL. Brightness bug fixed in commit ab44aa6: _device_to_ha_brightness() and _ha_to_device_brightness() now clamp to valid ranges (user confirmed percentage correct). Diagnostics/observability improved (release 2026.5.16): API state-change debug logging ("API state change for <dev>: power X -> Y"), capability parameters in diagnostics, and raw_api_state capture. Power-state issue NOT definitively resolved: root cause is the Govee cloud REST returning unreliable/stale power for the H6104 (first stale-ON, later stale-OFF), combined with the integration ignoring the H6104's proType:0 MQTT status pushes (both the nested msg.data.turn form and the top-level state.onOff variant are treated as command/response and dropped), so no real-time correction happens. The final 2026-06-12 log dump shows the inverted symptom (REST flips ON->OFF while device is on). Issue is CLOSED but the H6104 MQTT-status parsing fix is not confirmed shipped in-thread.

_H6104 MQTT status push, nested legacy form (proType 0) — power in msg.data.turn; currently IGNORED as command/response_

```json
{"msg":"{\"data\":\"{\\\"softversion\\\":\\\"1.01.50\\\",\\\"turn\\\":1,\\\"brightness\\\":254,\\\"timer\\\":{\\\"enable\\\":0,\\\"time\\\":[{\\\"openHour\\\":0,\\\"openMin\\\":0,\\\"closeHour\\\":23,\\\"closeMin\\\":59}]}}\",\"transaction\":\"o_1781206768373\",\"sku\":\"H6104\",\"device\":\"[MAC]\",\"type\":0,\"cmd\":\"status\"}","proType":0}
```

_H6104 MQTT status push, variant WITH top-level state.onOff (proType 0) — still IGNORED as command/response_

```json
{"state":{"onOff":0,"brightness":254,"device":"[MAC]","sku":"H6104"},"msg":"{\"data\":\"{...turn:0,brightness:254...}\",\"transaction\":\"x_1781206794230002\",\"sku\":\"H6104\",\"device\":\"[MAC]\",\"type\":0,\"cmd\":\"status\"}","proType":0}
```

_H6159 MQTT status push (proType 2) — top-level state.onOff, PARSED and applied_

```json
{"proType":2,"sku":"H6159","device":"[MAC]","softVersion":"2.04.00","wifiSoftVersion":"1.02.11","wifiHardVersion":"1.00.10","cmd":"status","type":0,"transaction":"x_1781206794229001","pactType":2,"pactCode":1,"state":{"onOff":0,"brightness":92,"colorTemInKelvin":2000,"color":{"r":255,"g":255,"b":255},"mode":13,"result":1},"op":{"command":["qgUN////B9D/jQsAAAAAAAAAAPM=","qhEAHg8PAAAAAAAAAAAAAAAAAKU=","qhL/ZAAAgAoAAAAAAAAAAAAAAKk=","qiP/AAAAgAAAAIAAAACAAAAAgHY="]}}
```

_Diagnostics device entry for H6104 (capabilities + parsed state; note brightness 254, source=optimistic)_

```json
{"sku":"H6104","name":"[NAME]","device_type":"devices.types.light","is_group":false,"capabilities":[{"type":"devices.capabilities.on_off","instance":"powerSwitch"},{"type":"devices.capabilities.range","instance":"brightness"},{"type":"devices.capabilities.color_setting","instance":"colorRgb"},{"type":"devices.capabilities.color_setting","instance":"colorTemperatureK"},{"type":"devices.capabilities.music_setting","instance":"musicMode"},{"type":"devices.capabilities.dynamic_scene","instance":"diyScene"}],"state":{"online":true,"power_state":false,"brightness":254,"color":null,"color_temp_kelvin":null,"source":"optimistic"}}
```

---
