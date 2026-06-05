<div align="center">

# Govee Cloud Integration for Home Assistant

**Control Govee lights, plugs, fans, humidifiers, heaters, thermometers & leak sensors — with optional real‑time push over Govee's AWS IoT MQTT.**

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5?style=flat-square)](https://github.com/hacs/integration)
[![Release](https://img.shields.io/github/v/release/lasswellt/govee-homeassistant?style=flat-square&color=41BDF5)](https://github.com/lasswellt/govee-homeassistant/releases)
![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2024.11+-41BDF5?style=flat-square&logo=home-assistant&logoColor=white)
![Quality scale](https://img.shields.io/badge/quality%20scale-silver-silver?style=flat-square)
[![License](https://img.shields.io/github/license/lasswellt/govee-homeassistant?style=flat-square&color=41BDF5)](LICENSE.txt)

[![Active installs](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/lasswellt/govee-homeassistant/badges/installs.json)](https://analytics.home-assistant.io/)
[![Govee API status](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/lasswellt/govee-homeassistant/badges/api-status.json)](#-live-status)
[![Stars](https://img.shields.io/github/stars/lasswellt/govee-homeassistant?style=flat-square&color=41BDF5)](https://github.com/lasswellt/govee-homeassistant/stargazers)

</div>

> **Hub (cloud)** · IoT class `cloud_push` (MQTT + polling) · UI‑only config, no YAML

---

## 📊 Live status

<div align="center">

<img alt="Active installs trend" src="https://raw.githubusercontent.com/lasswellt/govee-homeassistant/badges/installs-trend.svg?v=3" width="49%" />
&nbsp;
<img alt="Govee API uptime" src="https://raw.githubusercontent.com/lasswellt/govee-homeassistant/badges/api-uptime.svg?v=3" width="49%" />

<img alt="Installs by version" src="https://raw.githubusercontent.com/lasswellt/govee-homeassistant/badges/versions.svg?v=3" width="99%" />

</div>

<sub>**Active installs** counts only versions **released by this repository** — other `govee` forks and legacy installs sharing the same domain are excluded — and reflects Home Assistant instances opted into Usage‑level analytics, so true usage is higher. **Govee API status** pings `openapi.api.govee.com` and `app2.govee.com` hourly: round of red bars on the right = an outage today, not a problem with your setup. Both graphs update automatically via GitHub Actions ([uptime](.github/workflows/uptime.yml) · [install‑stats](.github/workflows/install-stats.yml)).</sub>

---

## What this is

A custom component that talks to Govee's cloud. Add your Govee API key and your devices show up in Home Assistant. Add your Govee account email/password as well and you also get **real‑time updates** (push) instead of polling alone, plus support for **hub‑based leak sensors**.

It is **capability‑based**: entities are created from the capabilities Govee reports for each device, not a hard‑coded SKU list — so new models in a known device class generally work without an update.

> **Cloud / WiFi devices only.** Bluetooth‑only devices (e.g. a standalone H5075 thermometer with no gateway) don't appear in Govee's cloud API. For those, use Home Assistant's first‑party [**Govee Bluetooth (`govee_ble`)**](https://www.home-assistant.io/integrations/govee_ble/) integration. The two can run side by side.

---

## Supported devices

| Category | Examples | Entities you get |
|---|---|---|
| **Lights** (strips, bulbs, bars, TV backlights, sync boxes) | H619x, H61xx, H6099, H66A0, H6604 | Light (on/off, brightness, RGB, color temp), scene & DIY selectors, music‑mode switch, DreamView switch |
| **RGBIC lights** | H619C, H6198, H60A6 | Everything above **plus** per‑segment color control (see [Segments](#rgbic-segment-control)) |
| **Smart plugs / sockets** | H5080, H5083 | Switch; outlet extenders with an RGB nightlight (H5089) also get a color light |
| **Ceiling fan + light combos** | H1310 | Light **and** a Fan entity (on/off, speed, reverse direction) |
| **Tower / pedestal fans** | H7101, H7102, H7107 | Fan (speed, oscillation, preset modes) |
| **Air purifiers** | H7120–H7127 | Fan / work modes, filter‑life sensor, optional nightlight |
| **Humidifiers & dehumidifiers** | H7140, H7141, H7151 | Humidifier entity with modes |
| **Space heaters** | H7130, H7131, H721C | Power switch, target‑temperature number, auto‑stop switch |
| **Thermometers / hygrometers** | H5103, H5107, H5109, H5179 | Temperature & humidity sensors + a "Last Changed" timestamp |
| **Air‑quality monitors** | H5140 | CO₂ / temperature / humidity sensors |
| **Leak sensors** | H5058, H5059 (also H5054/H5055) via an H5043/H5044 hub | Moisture binary sensor, battery, sensor/gateway connectivity, last‑wet timestamp, button‑press event |

Don't see your device, or a capability is missing? [Open an issue](https://github.com/lasswellt/govee-homeassistant/issues) with a diagnostics download (see [Diagnostics](#diagnostics--debug-logging)).

---

## Install

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
| **Temperature unit from Govee API (thermometers)** | `Celsius` | Set to **Fahrenheit** if thermometer readings look ~1.8× too high (e.g. 74 instead of 23). Govee returns the value in the device's app unit with no unit metadata, so it can't be auto‑detected. |
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

## ⭐ Star history

<div align="center">
<a href="https://star-history.com/#lasswellt/govee-homeassistant&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=lasswellt/govee-homeassistant&type=Date&theme=dark" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=lasswellt/govee-homeassistant&type=Date" />
    <img alt="Star history chart for lasswellt/govee-homeassistant" src="https://api.star-history.com/svg?repos=lasswellt/govee-homeassistant&type=Date" width="70%" />
  </picture>
</a>
</div>

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
