<div align="center">

# Govee Cloud Integration for Home Assistant

**Control Govee lights, plugs, fans, humidifiers, heaters, thermometers, air‑quality & CO₂ monitors, presence & leak sensors — with optional real‑time push over Govee's AWS IoT MQTT and automatic local LAN control for devices that support it.**

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5?style=flat-square)](https://github.com/hacs/integration)
[![Release](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/lasswellt/govee-homeassistant/badges/release.json)](https://github.com/lasswellt/govee-homeassistant/releases)
![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2024.11+-41BDF5?style=flat-square&logo=home-assistant&logoColor=white)
![Quality scale](https://img.shields.io/badge/quality%20scale-silver-silver?style=flat-square)
[![License](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/lasswellt/govee-homeassistant/badges/license.json)](LICENSE.txt)

[![Active installs](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/lasswellt/govee-homeassistant/badges/installs.json)](https://analytics.home-assistant.io/)
[![Govee API status](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/lasswellt/govee-homeassistant/badges/api-status.json)](#-live-status)
[![Stars](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/lasswellt/govee-homeassistant/badges/stars.json)](https://github.com/lasswellt/govee-homeassistant/stargazers)

</div>

> **Hub (cloud)** · IoT class `cloud_push` (MQTT + polling) · UI‑only config, no YAML

---

## 🔀 About this fork

A fork of [`lasswellt/govee-homeassistant`](https://github.com/lasswellt/govee-homeassistant) that pushes multi‑zone lamps (H60B0 first) beyond what the cloud API exposes, using a hardware‑verified device protocol:

- **Per‑zone light entities** — each zone of a multi‑zone lamp becomes its own light, named after the zone; the whole‑device light acts as a WLED‑style master (opt‑in).
- **Raw LAN write transport** — zone and segment commands go over local UDP instead of the cloud, with transport‑health tracking (opt‑in).
- **DIY effect composer** — per‑zone mode / speed / palette entities staged locally and uploaded as one effect, plus a `govee.apply_diy_effect` service for automations.
- Everything else tracks upstream; versions are upstream‑anchored (`YYYY.M.D.patch`).

---

## 📊 Live status

<div align="center">

<img alt="Active installs trend" src="https://raw.githubusercontent.com/lasswellt/govee-homeassistant/badges/installs-trend.svg?v=3" width="49%" />
&nbsp;
<img alt="Govee API uptime" src="https://raw.githubusercontent.com/lasswellt/govee-homeassistant/badges/api-uptime.svg?v=3" width="49%" />

<img alt="Installs by version" src="https://raw.githubusercontent.com/lasswellt/govee-homeassistant/badges/versions.svg?v=3" width="99%" />

<img alt="GitHub star growth" src="https://raw.githubusercontent.com/lasswellt/govee-homeassistant/badges/stars-trend.svg?v=1" width="99%" />

</div>

<sub>**Active installs** counts only versions **released by this repository** — other `govee` forks and legacy installs sharing the same domain are excluded — and reflects Home Assistant instances opted into Usage‑level analytics, so true usage is higher. **Govee API status** pings `openapi.api.govee.com` and `app2.govee.com` hourly: round of red bars on the right = an outage today, not a problem with your setup. Both graphs update automatically via GitHub Actions ([uptime](.github/workflows/uptime.yml) · [install‑stats](.github/workflows/install-stats.yml)).</sub>

---

## What this is

A custom component that talks to Govee's cloud. Add your Govee API key and your devices show up in Home Assistant. Add your Govee account email/password as well and you also get **real‑time updates** (push) instead of polling alone, plus support for **hub‑based leak sensors**. Devices with Govee's **LAN API** enabled are additionally controlled **locally, automatically** — with the cloud as fallback.

It is **capability‑based**: entities are created from the capabilities Govee reports for each device, not a hard‑coded SKU list — so new models in a known device class generally work without an update. A handful of things Govee's API can't express are keyed to specific models (leak sensors and their hubs, presence sensors, thermometers absent from the developer API, and models that report Fahrenheit without saying so); everything else is derived from what the device advertises.

> **Cloud / WiFi devices only.** Bluetooth‑only devices (e.g. a standalone H5075 thermometer with no gateway) don't appear in Govee's cloud API. For those, use Home Assistant's first‑party [**Govee Bluetooth (`govee_ble`)**](https://www.home-assistant.io/integrations/govee_ble/) integration. The two can run side by side.

---

## How this compares

Govee in Home Assistant has several integrations, and it's easy to pick one that can't control your devices. Quick orientation:

| Integration | How it talks to Govee | Scenes / RGBIC segments | Non‑light devices | Notes |
|---|---|---|---|---|
| **This integration** | Cloud API v2 **+ AWS IoT MQTT push + local LAN (auto)** | ✅ Yes | Plugs, fans, humidifiers, heaters, sensors, leak hubs | Full feature set; push updates; LAN‑enabled lights controlled locally with cloud fallback; handles Govee's 2026 email‑2FA login |
| [`govee_light_local`](https://www.home-assistant.io/integrations/govee_light_local/) (HA built‑in) | LAN UDP | ❌ No | Lights only | Fast & local, but on/off + brightness + color only, and only models with LAN control enabled |
| [`govee_ble`](https://www.home-assistant.io/integrations/govee_ble/) (HA built‑in) | Bluetooth | ❌ No | Sensors only | Read‑only sensors — **no light control** |
| [govee2mqtt](https://github.com/wez/govee2mqtt) | LAN + cloud + MQTT | ✅ Yes | Wide | Most capable, but requires a separate MQTT broker/add‑on to run |
| [goveelife](https://github.com/disforw/goveelife) | Cloud OpenAPI v2 | ✅ Yes | Best for appliances | Polling‑only; strong on heaters/fans/humidifiers |

**Why pick this one:**

- **Full control of cloud‑only WiFi devices.** Many bulbs/strips (e.g. H6099) have **no LAN API** and **no light control over BLE** — the cloud path is the only way to get scenes, RGBIC segments, music mode and DreamView. The HA built‑in LAN/BLE integrations can't do this; people often conclude "Govee + HA is broken" when really they're using the wrong integration for the device.
- **MQTT push, not just polling.** Real‑time state arrives over AWS IoT, which also eases the Govee cloud rate limits (100 req/min, 10,000/day) that poll‑only integrations can hit on larger setups.
- **Local LAN control, zero setup.** Devices with Govee's LAN API enabled are discovered automatically and get local reads plus verified local writes (power, brightness, color, color temperature) — no toggle to flip, no broker to run. Every LAN write is confirmed by reading the device back; if it doesn't confirm, the command falls through to MQTT/REST so a device is never stranded. This also rescues devices whose color changes Govee's cloud accepts and then never delivers.
- **Resilient account login.** Govee added mandatory email **2FA** in 2026, which silently broke older account‑login integrations at startup. This one handles 2FA in an interactive setup/reconfigure flow and caches IoT credentials across reloads.
- **No extra infrastructure.** Full features without standing up a separate MQTT broker the way a bridge‑style setup (govee2mqtt) requires.

---

## Supported Govee devices

| Category | Examples | Entities you get |
|---|---|---|
| **Lights** (strips, bulbs, bars, TV backlights, sync boxes) | H619x, H61xx, H6058, H6099, H66A0, H6604 | Light (on/off, brightness, RGB, color temp), scene & DIY selectors, music‑mode switch, DreamView switch; sync boxes return to their HDMI/Video source when you clear the scene |
| **RGBIC lights** | H619C, H6198, H60A6 | Everything above **plus** per‑segment color control (see [Segments](#rgbic-segment-control)); Ceiling Light Pro (H60A6) adds an ambient/backlight‑ring switch |
| **Multi‑zone lamps** | H60B2, H60B3, H60B0 | Per‑zone on/off switches (Light Zone 1/2/3); the H60B3 uplighter adds Nebula/Side/Bottom light switches, and the H60B0 (same lamp, ripple diffuser) adds Ripple/Side/Bottom |
| **Smart plugs / sockets** | H5080, H5083, H5089 | Switch; outlet extenders (H5089) expose each outlet separately **plus** an RGB Night Light |
| **Ceiling fan + light combos** | H1310, H1370 | Separate Main light & Background light **and** a Fan entity (on/off, speed, reverse, oscillation) |
| **Tower / pedestal fans** | H7101, H7102, H7106, H7107 | Fan (speed, oscillation, preset modes) |
| **Air purifiers** | H7120–H7127 | Fan / work modes, filter‑life sensor, air‑quality (AQI) sensor, optional nightlight |
| **Humidifiers & dehumidifiers** | H7140, H7141, H7150, H7151, H7152 | Modes + target‑humidity setpoint; dehumidifiers add a **Water Tank Full** sensor (real‑time event push, API key only) with a paired **Clear Water Alert** button |
| **Aroma diffusers** | H7161 | Power switch + light/mist scene selector |
| **Space heaters** | H7130, H7131, H713B, H721C | Power switch, target‑temperature number, auto‑stop switch; temperature unit follows what the device itself reports |
| **Thermometers / hygrometers** | H5103, H5107, H5109, H5111, H5112, H5179, H5301, H5310 | Temperature & humidity sensors, **Battery** (account login) + a "Last Changed" timestamp; gateway‑bridged models (H5301/H5310 via an H5044) nest under the hub |
| **Air‑quality & CO₂ monitors** | H5106, H5140 | CO₂ (ppm), air‑quality (AQI), temperature & humidity sensors |
| **Presence sensors** | H5127 | Occupancy binary sensor, updated in real time over MQTT |
| **Leak sensors** | H5054, H5055, H5058, H5059 (via an H5040/H5043/H5044 hub) | Moisture binary sensor, battery, sensor/gateway connectivity, last‑wet timestamp, button‑press event |

Don't see your device, or a capability is missing? [Open an issue](https://github.com/lasswellt/govee-homeassistant/issues) with a diagnostics download (see [Diagnostics](#diagnostics--debug-logging)).

---

## How to install Govee in Home Assistant

### HACS (recommended)

1. HACS → **⋮** → **Custom repositories**
2. Repository: `https://github.com/lasswellt/govee-homeassistant`, Category: **Integration**
3. Install **Govee Cloud Integration**, then **restart Home Assistant**

### Manual

Copy `custom_components/govee/` into your Home Assistant `config/custom_components/` directory and restart.

---

## Set up

### 1. Get a Govee API key

In the **Govee Home** app: **Profile → Settings (gear) → Apply for API Key**. You'll receive it by email, usually within minutes.

### 2. Add the integration

**Settings → Devices & Services → Add Integration → Govee Cloud Integration**, then paste your API key.

The API key alone gives you device control and **polling** for state.

### 3. (Optional but recommended) Add account login for real‑time updates

In the same setup flow you can enter your **Govee account email and password**. This enables:

- **Real‑time push updates** over AWS IoT MQTT (no waiting for the next poll)
- **Leak‑sensor support** (H5054 / H5055 / H5058 / H5059 via an H5040/H5043/H5044 hub)
- **Battery levels** on battery‑powered sensors — the developer API doesn't expose them at all
- **Thermometers the developer API doesn't return** (e.g. H5301, H5310), and readings for those it returns empty (e.g. H5179, H5112)
- **MQTT‑based control**, if you turn it on in options

#### Two‑factor (email code)

Since 2026 Govee requires email verification for account login. If your account has it on, the flow will pause, Govee emails you a **code**, and you enter it to finish. The code expires in ~15 minutes. Credentials are stored encrypted in your config entry.

> Account login is optional. Without it, the integration runs in polling‑only mode and everything except the features listed above still works. You can add or remove it later via **⋮ → Reconfigure** without losing your devices.

---

## Configuration options

After setup, open **Settings → Devices & Services → Govee Cloud Integration → ⚙️ Configure**:

| Option | Default | What it does |
|---|---|---|
| **Polling interval (seconds)** | `60` | How often to poll the cloud for state (30–300). MQTT and LAN updates arrive between polls. |
| **Leak sensor polling interval (seconds)** | `120` | How often standalone RF water detectors (e.g. H5054) are checked for a leak (60–3600). These have no push channel, so a leak surfaces with up to this much delay — lower reacts faster but makes more account API calls. Needs account login; ignored if you have no such detectors. |
| **Temperature unit from Govee API (thermometers)** | `Auto` | Govee returns thermometer values in the device's app unit with **no** unit metadata. **Auto** (default) reads your account's own °C/°F preference where Govee exposes it, falls back to converting the models known to report Fahrenheit, and trusts the rest; pick **Fahrenheit** if a reading still looks ~1.8× too high (e.g. 74 instead of 23), or **Celsius** to never convert. |
| **Enable group devices** | `off` | Surface the device groups you created in the Govee app as single light entities (power/brightness/color; state is best‑effort). |
| **Enable scene selector** | `on` | Create a per‑device dropdown to activate Govee scenes. |
| **Enable DIY scene selector** | `on` | Create a per‑device dropdown for your DIY scenes. |
| **Expose per‑device transport connectivity sensors** | `off` | Add diagnostic binary sensors showing each device's MQTT/BLE/LAN reachability. |
| **Send power/brightness/color over MQTT (experimental)** | `off` | Routes those commands through Govee's MQTT channel instead of the REST API — lower latency, bypasses REST rate limits. Requires account login; falls back to REST automatically. Uses an undocumented channel, so leave off if commands misbehave. |
| **LAN device addresses / subnets (advanced)** | *(blank)* | Only needed when LAN‑enabled devices sit on a different subnet/VLAN than Home Assistant. Comma‑separated IPs, broadcast addresses, and/or CIDR subnets (/24 or smaller). Leave blank when everything shares HA's network — discovery is automatic. Enter `off` to disable LAN discovery and local control entirely. |

RGBIC devices get a second step after submitting, where you choose a **segment mode** for each device individually — see [Segments](#rgbic-segment-control).

---

## Real‑time updates & local LAN control

With account login configured, the integration maintains an AWS IoT MQTT connection and applies state changes the moment they happen. Without it, state comes from polling on your configured interval. A **"Govee Integration"** device exposes diagnostics for this: API rate‑limit remaining, MQTT status, and a **"Last MQTT Received"** timestamp.

Every device also gets two diagnostic timestamps — **Last Updated** (when data last arrived) and **Last Command Sent** — plus a **Connectivity** sensor. Turning on **Expose per‑device transport connectivity sensors** adds one reachability sensor per transport (Cloud API, MQTT, Bluetooth, LAN) for pinpointing which path a device is actually using.

**Local LAN control is automatic.** If a device has Govee's LAN API turned on (Govee Home app → device settings → LAN Control), the integration finds it via a periodic local discovery scan and starts using the LAN for state reads and for **power, brightness, color and color temperature** commands — no option to enable. Every LAN write is **verified by reading the device back**; an unconfirmed write falls through to MQTT/REST instantly, and a device that stops answering is demoted back to cloud transports until it reappears. Devices on another subnet/VLAN can be reached via the **LAN device addresses** option (see above).

This matters beyond speed: Govee's cloud sometimes answers a color command with `success` and never delivers it to the device (the light doesn't change, and nothing reports an error). Sending color locally sidesteps the cloud entirely — see [Colors don't apply](#troubleshooting).

**Command routing.** Each command takes the fastest transport that can carry it *and confirm it*, falling back automatically: **BLE → LAN → MQTT → cloud REST**. LAN carries power, brightness, color and color temperature — exactly the four values a device reports back, which is what makes verify‑by‑read possible. MQTT (opt‑in) carries power, brightness and color. Direct BLE control is deliberately limited to one model confirmed to honour it (H6199); other models advertise Bluetooth but silently drop writes. Everything else — scenes, segments, music mode, work modes, toggles — always goes over the cloud API.

Commands always use optimistic updates, so the UI reflects your action immediately and reconciles with the next confirmed state.

---

## RGBIC segment control

For RGBIC strips/bars you can control individual lighting segments. After saving the options you're asked which RGBIC devices to configure, then a mode for **each one separately** — so a 14‑segment strip can be Individual while a bar you only ever set as a whole is Grouped:

- **Individual** (default) — one light entity per segment, for maximum control.
- **Grouped** — a single "Segments" entity that sets all segments together.
- **Disabled** — no segment entities.

Segment colors aren't reliably returned by the API, so segment entities keep optimistic state and restore it across restarts.

There's also a service for automations:

```yaml
service: govee.set_segment_color
data:
  device_id: "AA:BB:CC:DD:EE:FF:00:11"
  segments: [0, 1, 2]
  rgb_color: [255, 0, 0]
```

---

## Scenes, DIY, music & DreamView

- **Scenes / DIY scenes** — activated through per‑device select dropdowns (toggle in options). The API doesn't reliably report the active scene, so the selection is preserved optimistically and cleared when you switch to another mode (color, color temp, music, etc.).
- **Music mode** — exposed as a switch on capable lights.
- **DreamView / video sync** — exposed as a switch on capable backlights.
- Use the **`govee.refresh_scenes`** service to re‑pull the scene catalog (optionally for one `device_id`).

---

## DIY effects on multi‑zone lamps (fork)

On a multi‑zone lamp (H60B0 today) a **DIY effect** is not a setting you toggle — it's a small document you author: each zone gets an effect mode, a transition speed, a colour palette, and (on the ripple zone only) a diffuser direction and flow rate. The whole thing is uploaded to the lamp in one shot over the local network.

One option in ⚙️ Configure gates the feature, because a DIY upload has no cloud equivalent at all:

| Option | Needed for |
|---|---|
| **Split a multi‑zone lamp into per‑zone lights** | The per‑zone entities, the DIY authoring controls and the upload |

The upload itself needs no extra option — it always uses the local network, so the lamp has to be reachable there (its **LAN (raw)** connectivity sensor says whether it is). With the option on, each zone gets staging controls (mode / speed / palette / direction / flow rate) plus an **Apply DIY effect** button. Nothing leaves the house until you press it.

### `govee.apply_diy_effect`

For automations, the same upload is available as an action. It **targets** the lamp the normal Home Assistant way — pick the device, or any one of its light entities, from the picker:

```yaml
actions:
  - action: govee.apply_diy_effect
    target:
      device_id: 4a1f2c9e8b7d6a5c4e3f2a1b0c9d8e7f
    data:
      ripple_mode: twinkle
      ripple_speed: 60
      ripple_colors: [[255, 0, 0], [0, 176, 255]]
      ripple_direction: ccw
      ripple_flow_rate: 30
      ring_mode: gradient
      ring_colors: [[255, 255, 255]]
```

**Targeting.** Exactly one DIY‑capable device per call — a DIY effect is an authored document with per‑zone mode tables, so fanning one call across several lamps would be a partial write rather than a success. Targeting a device that isn't a multi‑zone lamp, or resolving to more than one, fails with a message saying so. A raw Govee device id (`AA:BB:CC:DD:EE:FF:00:11`) passed as `device_id` is still accepted, so automations written before the picker existed keep working.

**Fields.** All eight are optional:

| Field | Values | Default |
|---|---|---|
| `ripple_mode` | `none`, `gradient`, `breathe`, `rainbow`, `twinkle`, `jumping` — or a raw number 0–255 | — |
| `ripple_speed` | 1–100 | `50` |
| `ripple_colors` | list of `[R, G, B]`, 1–16 entries | — |
| `ripple_direction` | `cw`, `ccw`, `reverse` | `cw` |
| `ripple_flow_rate` | 1–100 | `50` |
| `ring_mode` | `none`, `gradient`, `breathe`, `twinkle`, `rainbow`, `graffiti`, `flow`, `alternate`, `gleam`, `cover`, `colorful` — or a raw number 0–255 | — |
| `ring_speed` | 1–100 | `50` |
| `ring_colors` | list of `[R, G, B]`, 1–16 entries | — |

The ring zone has no direction or flow rate — its record carries no such field on the wire, so neither is offered for it. Raw numbers are accepted for both modes because the ripple's mode table is known to be incomplete and the lamp also plays the ring's mode numbers on it.

**Semantics.** A call is self‑contained, so the same automation produces the same lamp every time:

- A zone with **all** of its fields omitted (or `*_mode: none`) is switched **off** in the uploaded effect — not left at whatever you last staged in the UI.
- A field omitted inside a zone you *do* use takes the **fixed default** above, again ignoring the staged draft.
- Naming any of a zone's fields requires that zone's `*_mode` — turning a zone on is a choice, and there's no sensible mode to invent for it.
- A zone that's on needs at least one colour.

Whatever the action sends is mirrored back into the DIY staging entities, so the authoring controls show what actually went to the lamp rather than a draft the automation just overwrote.

---

## Device groups

Enable **group devices** in options to surface Govee‑app groups as single light entities. A command to a group is sent once and fanned out to all members by Govee's cloud, which syncs better than grouping the same lights with Home Assistant helpers (those fire separate commands that arrive at slightly different times). Group state is best‑effort (groups can't be polled), and group lights support power/brightness/color only.

---

## Thermometers & sensors

Thermometer/hygrometer readings (H5103, H5107, H5109, H5179, …) come from Govee's **cloud**, which only refreshes them on its own schedule:

- **WiFi‑native sensors** (e.g. H5179): on the order of ~10 minutes.
- **Bluetooth sensors behind a gateway** (e.g. H5075/H5110 through an **H5151** WiFi gateway): the gateway batch‑uploads infrequently — often many minutes (observed ~15–60 min; the exact interval is Govee's, not guaranteed).

So a reading can look "frozen" while polling is perfectly healthy — it's the latest value Govee has. This is a Govee cloud limitation, not an integration bug (govee2mqtt and homebridge‑govee hit the same wall, and AWS IoT MQTT carries no thermometer data at all). Each thermometer exposes a **"Last Changed"** diagnostic timestamp so you can see how old the value is.

**Battery & gateway‑bridged sensors.** Battery level for battery‑powered sensors (thermometers, leak detectors) comes from your Govee **account** data, so it needs account login (email/password) — an API key alone can't see it. It's refreshed every 5 minutes, so give it a few minutes after a restart before assuming it's missing. Sensors that reach the cloud through a hub are handled two different ways, depending on how Govee exposes them. Models the developer API doesn't return at all (H5301, H5310) are discovered from the **account device list** and nested under their hub. Models it does return but with empty readings (H5179, H5112) are discovered normally and only their *values* are read from the account data.

Some gateway‑bridged sensors are listed by Govee with no reading attached. When that happens the integration keeps polling the regular cloud API for them rather than assuming the account data will fill in, and switches over automatically if it ever does — so a sensor isn't left permanently blank because of which source Govee happened to populate.

**Temperature unit.** Govee reports thermometer values with no unit field, so the integration defaults to an **Auto** mode. Auto first looks for your account's own °C/°F display preference, which Govee exposes per device and which the cloud API mirrors when it returns the reading; where that isn't available it falls back to converting the models known to report Fahrenheit, and trusts everything else. If a reading is still ~1.8× off, set the unit explicitly in ⚙️ Configure — see [Configuration options](#configuration-options).

**Other sensors.** Air‑quality/CO₂ monitors (H5106, H5140) expose CO₂ (ppm), AQI, temperature and humidity from the cloud poll (not MQTT). The H5127 presence sensor reports **occupancy** in real time over MQTT. Dehumidifiers surface a **Water Tank Full** sensor driven by Govee's official event push (API key only — no account login needed); it fires when the tank is full **or** the bucket is pulled out. Govee never sends a "cleared" event, so the alert latches — surviving HA restarts — until you press the paired **Clear Water Alert** button after emptying/re‑inserting the tank; the sensor's `changed_at` attribute carries the last event/clear time for custom automations. None of these expose a live PM2.5 or room temp/humidity beyond what's listed — those are Bluetooth‑only in the Govee app.

**Want real‑time (~2 s) readings?** Govee thermometers broadcast over Bluetooth:

1. Enable Home Assistant's first‑party [**Govee Bluetooth (`govee_ble`)**](https://www.home-assistant.io/integrations/govee_ble/) for any sensor within Bluetooth range of your HA host.
2. For distant sensors, add an [**ESPHome Bluetooth proxy**](https://esphome.io/components/bluetooth_proxy.html) nearby.

---

## Services

| Service | Purpose |
|---|---|
| `govee.refresh_scenes` | Re‑fetch the scene catalog from Govee (optional `device_id`). |
| `govee.set_segment_color` | Set RGB color on specific segments of an RGBIC device. |
| `govee.apply_diy_effect` | Compose and upload a DIY effect to one multi‑zone lamp (fork; targets a device or one of its entities — see [above](#goveeapply_diy_effect)). |

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Devices not showing up | They must be WiFi/cloud devices. Bluetooth‑only devices need [`govee_ble`](https://www.home-assistant.io/integrations/govee_ble/). |
| **Color doesn't apply** — on/off and scenes work, color changes nothing | Govee's cloud sometimes accepts a color command and never delivers it. Turn on **LAN Control** for the device in the Govee Home app so color is sent locally; if the device has no LAN API, enable **Send power/brightness/color over MQTT** in ⚙️ Configure (needs account login). If neither works, the device firmware is ignoring the command — attach diagnostics to an issue. |
| Thermometer reads ~1.8× too high (e.g. 74 vs 23) | Set **Temperature unit from Govee API → Fahrenheit** in ⚙️ Configure. |
| Thermometer value looks "frozen" | Expected — Govee's cloud refreshes on its own cadence. See [Thermometers & sensors](#thermometers--sensors). |
| Sensor shows **Unknown** and never updates | Gateway‑bridged sensors depend on data Govee may not be publishing for your account. Grab a diagnostics download and open an issue — the `bff_device_values` section shows whether the reading exists at all. |
| Leak alert arrives late | Standalone RF detectors (H5054) have no push channel and are polled; lower the **Leak sensor polling interval**. Hub‑attached sensors (H5058/H5059) push in real time and aren't affected. |
| Battery missing on a sensor | Battery comes from your Govee **account** data, so account login is required — an API key alone can't see it. It's fetched every 5 minutes, so allow a few minutes after a restart. |
| No real‑time updates / no leak sensors | Add your Govee account email/password (enables MQTT). API key alone is polling‑only. |
| LAN sensor shows Disconnected / device not found locally | Enable **LAN Control** for the device in the Govee Home app. Across subnets/VLANs, add the device's IP or subnet under **LAN device addresses** in ⚙️ Configure. |
| Re‑prompted for a 2FA code / login fails | Reconfigure the integration and complete the email‑code step; codes expire in ~15 minutes. |
| Rate‑limit warnings | The Govee API allows 100 requests/min and 10,000/day. Increase the polling interval if you have many devices. |

If something's still wrong, grab a diagnostics download (below) and [open an issue](https://github.com/lasswellt/govee-homeassistant/issues).

---

## Diagnostics & debug logging

> Steps below are for **Home Assistant 2026.x**. Diagnostics auto‑redact your API key, account credentials, tokens, and device MAC addresses, so they're safe to attach to a GitHub issue.

### Download diagnostics (best for most reports)

**Whole integration:**

1. **Settings → Devices & Services**
2. Click **Govee Cloud Integration**
3. On the integration's entry, open the **⋮** (three‑dot) menu → **Download diagnostics**
4. Attach the downloaded JSON to your issue

**A single device** (when only one device misbehaves):

1. **Settings → Devices & Services → Govee Cloud Integration → _N_ devices**
2. Open the device
3. **⋮** (top‑right) → **Download diagnostics**

The download includes each device's parsed state, the verbatim cloud response, the last MQTT push, per‑transport health (including LAN discovery results), a ring buffer of recent OpenAPI event pushes (e.g. water‑tank‑full), and — for leak‑sensor and gateway‑sensor troubleshooting — recent hub packets and a privacy‑safe summary of what the account API returns for each device.

The most useful section for "I pressed the button and nothing happened" reports is **`recent_commands`**: every recent control command with the exact payload sent, which transport carried it (cloud, LAN, MQTT or BLE), and how the device or cloud answered — including *why* a local write wasn't confirmed. If a command shows `success` there and the device still didn't react, that's strong evidence the problem is on Govee's side rather than in Home Assistant.

### Capture a debug log (no YAML needed)

Home Assistant can record a scoped debug log with one click:

1. **Settings → Devices & Services → Govee Cloud Integration**
2. On the entry's **⋮** menu → **Enable debug logging**
3. **Reproduce the problem** (toggle the device, wait for an update, etc.)
4. Return to the **⋮** menu → **Disable debug logging** — Home Assistant **automatically downloads** the log file
5. Attach it to your issue

<details>
<summary>YAML alternative (advanced)</summary>

Add to `configuration.yaml`, restart, reproduce, then collect from **Settings → System → Logs → Download full log**:

```yaml
logger:
  default: warning
  logs:
    custom_components.govee: debug
    custom_components.govee.api.auth: debug   # add for login / leak‑sensor issues
    aiomqtt: debug                            # add for real‑time / MQTT issues
```
</details>

### What to include in an issue

- The device **SKU / model** (e.g. `H6199`) and what's wrong
- A **diagnostics download** (and a **debug log** if it's a control/connectivity problem)
- Your Home Assistant and integration versions

---

## Contributing

Issues and PRs welcome. Development quick start:

```bash
# Tests, type-check, lint, format
pytest          # or: tox
mypy custom_components/govee
flake8 .
black .
```

---

## Disclaimer & license

This is an unofficial integration and is not affiliated with or endorsed by Govee. "Govee" is a trademark of its respective owner. Use at your own risk.

Licensed under the terms in [LICENSE](LICENSE.txt).
