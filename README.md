<div align="center">

# Govee Cloud Integration for Home Assistant

**Control Govee lights, plugs, fans, humidifiers, heaters, thermometers, air‑quality & CO₂ monitors, presence & leak sensors — with optional real‑time push over Govee's AWS IoT MQTT.**

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

A custom component that talks to Govee's cloud. Add your Govee API key and your devices show up in Home Assistant. Add your Govee account email/password as well and you also get **real‑time updates** (push) instead of polling alone, plus support for **hub‑based leak sensors**.

It is **capability‑based**: entities are created from the capabilities Govee reports for each device, not a hard‑coded SKU list — so new models in a known device class generally work without an update.

> **Cloud / WiFi devices only.** Bluetooth‑only devices (e.g. a standalone H5075 thermometer with no gateway) don't appear in Govee's cloud API. For those, use Home Assistant's first‑party [**Govee Bluetooth (`govee_ble`)**](https://www.home-assistant.io/integrations/govee_ble/) integration. The two can run side by side.

---

## How this compares

Govee in Home Assistant has several integrations, and it's easy to pick one that can't control your devices. Quick orientation:

| Integration | How it talks to Govee | Scenes / RGBIC segments | Non‑light devices | Notes |
|---|---|---|---|---|
| **This integration** | Cloud API v2 **+ AWS IoT MQTT push** | ✅ Yes | Plugs, fans, humidifiers, heaters, sensors, leak hubs | Full feature set; push updates; handles Govee's 2026 email‑2FA login |
| [`govee_light_local`](https://www.home-assistant.io/integrations/govee_light_local/) (HA built‑in) | LAN UDP | ❌ No | Lights only | Fast & local, but on/off + brightness + color only, and only models with LAN control enabled |
| [`govee_ble`](https://www.home-assistant.io/integrations/govee_ble/) (HA built‑in) | Bluetooth | ❌ No | Sensors only | Read‑only sensors — **no light control** |
| [govee2mqtt](https://github.com/wez/govee2mqtt) | LAN + cloud + MQTT | ✅ Yes | Wide | Most capable, but requires a separate MQTT broker/add‑on to run |
| [goveelife](https://github.com/disforw/goveelife) | Cloud OpenAPI v2 | ✅ Yes | Best for appliances | Polling‑only; strong on heaters/fans/humidifiers |

**Why pick this one:**

- **Full control of cloud‑only WiFi devices.** Many bulbs/strips (e.g. H6099) have **no LAN API** and **no light control over BLE** — the cloud path is the only way to get scenes, RGBIC segments, music mode and DreamView. The HA built‑in LAN/BLE integrations can't do this; people often conclude "Govee + HA is broken" when really they're using the wrong integration for the device.
- **MQTT push, not just polling.** Real‑time state arrives over AWS IoT, which also eases the Govee cloud rate limits (100 req/min, 10,000/day) that poll‑only integrations can hit on larger setups.
- **Resilient account login.** Govee added mandatory email **2FA** in 2026, which silently broke older account‑login integrations at startup. This one handles 2FA in an interactive setup/reconfigure flow and caches IoT credentials across reloads.
- **No extra infrastructure.** Full features without standing up a separate MQTT broker the way a bridge‑style setup (govee2mqtt) requires.

---

## Supported Govee devices

| Category | Examples | Entities you get |
|---|---|---|
| **Lights** (strips, bulbs, bars, TV backlights, sync boxes) | H619x, H61xx, H6099, H66A0, H6604 | Light (on/off, brightness, RGB, color temp), scene & DIY selectors, music‑mode switch, DreamView switch; sync boxes return to their HDMI/Video source when you clear the scene |
| **RGBIC lights** | H619C, H6198, H60A6 | Everything above **plus** per‑segment color control (see [Segments](#rgbic-segment-control)); Ceiling Light Pro (H60A6) adds an ambient/backlight‑ring switch |
| **Multi‑zone lamps** | H60B2 | Per‑zone on/off switches (Light Zone 1/2/3) |
| **Smart plugs / sockets** | H5080, H5083, H5089 | Switch; outlet extenders (H5089) expose each outlet separately **plus** an RGB Night Light |
| **Ceiling fan + light combos** | H1310, H1370 | Separate Main Light & Background Light **and** a Fan entity (on/off, speed, reverse, oscillation) |
| **Tower / pedestal fans** | H7101, H7102, H7106, H7107 | Fan (speed, oscillation, preset modes) |
| **Air purifiers** | H7120–H7127 | Fan / work modes, filter‑life sensor, air‑quality (AQI) sensor, optional nightlight |
| **Humidifiers & dehumidifiers** | H7140, H7141, H7150, H7151, H7152 | Modes + target‑humidity setpoint; dehumidifiers add a **Water Tank Full** sensor (needs account login) |
| **Aroma diffusers** | H7161 | Power switch + light/mist scene selector |
| **Space heaters** | H7130, H7131, H721C | Power switch, target‑temperature number, auto‑stop switch |
| **Thermometers / hygrometers** | H5103, H5107, H5109, H5179, H5301, H5310 | Temperature & humidity sensors, **Battery** (account login) + a "Last Changed" timestamp; gateway‑bridged models (H5301/H5310 via an H5044) nest under the hub |
| **Air‑quality & CO₂ monitors** | H5106, H5140 | CO₂ (ppm), air‑quality (AQI), temperature & humidity sensors |
| **Presence sensors** | H5127 | Occupancy binary sensor, updated in real time over MQTT |
| **Leak sensors** | H5058, H5059, H5054 via an H5040/H5043/H5044 hub | Moisture binary sensor, battery, sensor/gateway connectivity, last‑wet timestamp, button‑press event |

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
- **Leak‑sensor support** (H5058 / H5059, and other LoRa leak sensors, via an H5043/H5044 hub)

#### Two‑factor (email code)

Since 2026 Govee requires email verification for account login. If your account has it on, the flow will pause, Govee emails you a **code**, and you enter it to finish. The code expires in ~15 minutes. Credentials are stored encrypted in your config entry.

> Account login is optional. Without it, the integration runs in polling‑only mode and everything except real‑time push and leak sensors still works.

---

## Configuration options

After setup, open **Settings → Devices & Services → Govee Cloud Integration → ⚙️ Configure**:

| Option | Default | What it does |
|---|---|---|
| **Polling interval (seconds)** | `60` | How often to poll the cloud for state (30–600). MQTT updates arrive between polls. |
| **Temperature unit from Govee API (thermometers)** | `Auto` | Govee returns thermometer values in the device's app unit with **no** unit metadata. **Auto** (default) converts the models known to report Fahrenheit and trusts the rest; pick **Fahrenheit** if a reading still looks ~1.8× too high (e.g. 74 instead of 23), or **Celsius** to never convert. |
| **Enable group devices** | `off` | Surface the device groups you created in the Govee app as single light entities (power/brightness/color; state is best‑effort). |
| **Enable scene selector** | `on` | Create a per‑device dropdown to activate Govee scenes. |
| **Enable DIY scene selector** | `on` | Create a per‑device dropdown for your DIY scenes. |
| **Expose per‑device transport connectivity sensors** | `off` | Add diagnostic binary sensors showing each device's Cloud/MQTT/BLE reachability. |

RGBIC devices get a second step after submitting, where you choose a **segment mode** per device — see below.

---

## Real‑time updates

With account login configured, the integration maintains an AWS IoT MQTT connection and applies state changes the moment they happen. Without it, state comes from polling on your configured interval. A **"Govee Integration"** device exposes diagnostics for this: API rate‑limit remaining, MQTT status, and a **"Last MQTT Received"** timestamp.

Commands always use optimistic updates, so the UI reflects your action immediately and reconciles with the next confirmed state.

---

## RGBIC segment control

For RGBIC strips/bars you can control individual lighting segments. Pick a mode per device in the options flow:

- **Individual** (default) — one light entity per segment, for maximum control.
- **Grouped** — a single entity that sets all segments together.
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

## Device groups

Enable **group devices** in options to surface Govee‑app groups as single light entities. A command to a group is sent once and fanned out to all members by Govee's cloud, which syncs better than grouping the same lights with Home Assistant helpers (those fire separate commands that arrive at slightly different times). Group state is best‑effort (groups can't be polled), and group lights support power/brightness/color only.

---

## Thermometers & sensors

Thermometer/hygrometer readings (H5103, H5107, H5109, H5179, …) come from Govee's **cloud**, which only refreshes them on its own schedule:

- **WiFi‑native sensors** (e.g. H5179): on the order of ~10 minutes.
- **Bluetooth sensors behind a gateway** (e.g. H5075/H5110 through an **H5151** WiFi gateway): the gateway batch‑uploads infrequently — often many minutes (observed ~15–60 min; the exact interval is Govee's, not guaranteed).

So a reading can look "frozen" while polling is perfectly healthy — it's the latest value Govee has. This is a Govee cloud limitation, not an integration bug (govee2mqtt and homebridge‑govee hit the same wall, and AWS IoT MQTT carries no thermometer data at all). Each thermometer exposes a **"Last Changed"** diagnostic timestamp so you can see how old the value is.

**Battery & gateway‑bridged sensors.** Battery level for battery‑powered sensors (thermometers, leak detectors) comes from your Govee **account** data, so it needs account login (email/password) — an API key alone can't see it. Sensors that reach the cloud through a hub (e.g. H5301/H5310 via an **H5044** gateway) are discovered from the account device list and nested under the hub.

**Temperature unit.** Govee reports thermometer values with no unit field, so the integration defaults to an **Auto** mode that converts the models known to report Fahrenheit and trusts the rest. If a reading is still ~1.8× off, set the unit explicitly in ⚙️ Configure — see [Configuration options](#configuration-options).

**Other sensors.** Air‑quality/CO₂ monitors (H5106, H5140) expose CO₂ (ppm), AQI, temperature and humidity from the cloud poll (not MQTT). The H5127 presence sensor reports **occupancy** in real time over MQTT. Dehumidifiers surface a **Water Tank Full** sensor from account data. None of these expose a live PM2.5 or room temp/humidity beyond what's listed — those are Bluetooth‑only in the Govee app.

**Want real‑time (~2 s) readings?** Govee thermometers broadcast over Bluetooth:

1. Enable Home Assistant's first‑party [**Govee Bluetooth (`govee_ble`)**](https://www.home-assistant.io/integrations/govee_ble/) for any sensor within Bluetooth range of your HA host.
2. For distant sensors, add an [**ESPHome Bluetooth proxy**](https://esphome.io/components/bluetooth_proxy.html) nearby.

---

## Services

| Service | Purpose |
|---|---|
| `govee.refresh_scenes` | Re‑fetch the scene catalog from Govee (optional `device_id`). |
| `govee.set_segment_color` | Set RGB color on specific segments of an RGBIC device. |

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Devices not showing up | They must be WiFi/cloud devices. Bluetooth‑only devices need [`govee_ble`](https://www.home-assistant.io/integrations/govee_ble/). |
| Thermometer reads ~1.8× too high (e.g. 74 vs 23) | Set **Temperature unit from Govee API → Fahrenheit** in ⚙️ Configure. |
| Thermometer value looks "frozen" | Expected — Govee's cloud refreshes on its own cadence. See [Thermometers & sensors](#thermometers--sensors). |
| No real‑time updates / no leak sensors | Add your Govee account email/password (enables MQTT). API key alone is polling‑only. |
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

The download includes each device's parsed state, the verbatim cloud response, the last MQTT push, per‑transport health, and — for leak‑sensor troubleshooting — recent hub packets and a privacy‑safe summary of the account device list.

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
