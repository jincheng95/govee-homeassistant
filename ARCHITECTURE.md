# Govee Integration Architecture

How the integration is put together, as of the code in this repository. CLAUDE.md carries the working conventions; TESTING.md the test guide; `docs/govee-protocol-reference.md` the protocol detail this document only names.

---

## Overview

A **hub** integration for Govee's cloud. The Developer API (REST, API key) is the stable path: device list, state polling, and control. An optional account login adds three undocumented paths: AWS IoT MQTT for push state and native commands, the account (BFF) API for devices and readings the Developer API omits, and gateway-relayed BLE frames. Two local transports need no credentials: Govee's LAN API for lights that have it enabled, and direct Bluetooth for an allowlist of models.

**Integration type** `hub` · **IoT class** `cloud_push` · **Config** UI only, config entry schema version 2.

Layers, outermost first:

| Layer | Where | Role |
|---|---|---|
| Entities | `light.py`, `select.py`, `switch.py`, `fan.py`, `humidifier.py`, `number.py`, `sensor.py`, `binary_sensor.py`, `event.py`, `button.py`, `platforms/` | Home Assistant platforms; every entity is a `CoordinatorEntity` (most through `GoveeEntity`) |
| Coordinator | `coordinator.py` | Discovery, polling, every transport, command routing, optimistic state, repairs |
| API clients | `api/` | REST, account login and BFF reads, AWS IoT MQTT, OpenAPI events, LAN, BLE, probe frames |
| Models | `models/` | Devices, capabilities, colours, and commands as frozen dataclasses; device state as a mutable object the coordinator updates in place |

---

## Directory structure

```
custom_components/govee/
├── __init__.py              # async_setup (services), async_setup_entry, unload, migration, cleanup, device removal
├── config_flow.py           # user, account, verification_code, bluetooth, reauth, reconfigure, options
├── coordinator.py           # GoveeCoordinator (DataUpdateCoordinator); GoveeConfigEntry alias
├── entity.py                # GoveeEntity: unique id, device info, availability, _async_send_command
├── light.py                 # Main light, nightlight, main panel
├── select.py                # Scene, DIY, snapshot, HDMI, music, fan-speed, purifier, preset selects
├── switch.py                # Plugs, sockets, outlets, zones, named lights, music, DreamView, auto-stop, probe polling
├── fan.py                   # Tower and purifier fans, ceiling fans
├── humidifier.py            # Humidifiers and dehumidifiers
├── number.py                # Music sensitivity, heater target, probe alarm limits
├── sensor.py                # Readings, thermometers, probes, filter, AQI/CO2, diagnostics
├── binary_sensor.py         # Connectivity, water tank, pump, leak, occupancy, leak/hub online
├── event.py                 # Leak sensor button presses
├── button.py                # Refresh scenes, clear water alert
├── services.py              # govee.refresh_scenes, govee.set_segment_color
├── repairs.py               # Repair issues and their fix flows
├── diagnostics.py           # Config-entry and device diagnostics with redaction
├── scene_cache.py           # Scene and DIY scene cache with TTL
├── transport_health.py      # Per-device, per-transport health tracking
├── ble_advertisement.py     # Bluetooth advertisement correlation and enrolment
├── ble_passthrough.py       # BLE frames tunnelled over AWS IoT
├── const.py                 # Constants, SKU lists, option keys and ranges
├── manifest.json            # Metadata, requirements, Bluetooth matchers
├── strings.json             # UI strings (mirrored in translations/en.json; ca and es partial)
├── icons.json               # Entity icons by translation key
├── services.yaml            # Service action fields
├── quality_scale.yaml       # Quality scale self-assessment, one comment per rule
├── py.typed                 # PEP 561 marker
├── text.py                  # FORK: text platform (DIY palettes)
├── segment_limit.py         # FORK: segment-count cap (profile vs cloud)
├── zone_state.py            # FORK: zone registry + profile lookup
├── diy_state.py             # FORK: staged DIY document + upload
├── diy_previews.py          # FORK: palette previews
├── child_power.py           # FORK: whole-lamp power behind a child entity
├── lan_confirm.py           # FORK: echo-lag-aware LAN confirm
├── lan_udp_health.py        # FORK: health for the raw LAN UDP link
├── models/
│   ├── device.py            # GoveeDevice, GoveeCapability, leak sensor models, synthetic probe thermometers
│   ├── state.py             # GoveeDeviceState (mutable), RGBColor
│   ├── commands.py          # Command objects (Power, Brightness, Color, Scene, Segment, Toggle, ...)
│   └── transport.py         # TransportHealth
├── platforms/
│   ├── segment.py           # One light entity per RGBIC segment
│   ├── grouped_segment.py   # One light entity for all segments
│   ├── zone_light.py        # FORK: per-zone light entities
│   └── diy_effect.py        # FORK: DIY-effect authoring entities
└── api/
    ├── client.py            # GoveeApiClient: REST with aiohttp-retry, rate-limit accounting
    ├── auth.py              # GoveeAuthClient: login, 2FA, IoT credentials, BFF reads
    ├── mqtt.py              # GoveeAwsIotClient: AWS IoT MQTT over mutual TLS, ptReal
    ├── mqtt_control.py      # Native MQTT command mapping
    ├── openapi_events.py    # Official event push channel (API key only)
    ├── lan.py, lan_client.py, lan_control.py   # LAN discovery, client, command mapping
    ├── ble.py, ble_packet.py, ble_crypto.py    # Direct BLE transport, frames, encrypted handshake
    ├── probe_thermometer.py # Probe thermometer frame decoding and encoding
    ├── exceptions.py        # GoveeApiError hierarchy
    ├── lan_nudge.py         # FORK: LAN state nudge on MQTT change signals
    ├── segment_readback.py  # FORK: segment state readback
    ├── raw_router.py        # FORK: raw tier selection (see below)
    ├── lan_raw.py           # FORK: raw tier 1 — LAN UDP ptReal
    ├── ble_raw_write.py     # FORK: raw tier 2 — plaintext BLE
    ├── mqtt_raw_write.py    # FORK: raw tier 3 — cloud MQTT ptReal
    └── protocol/            # FORK: the codec package
        ├── profiles.py      # Per-SKU hardware truth table
        ├── codec.py         # GoveeCodec (frame assembly)
        ├── encoders.py      # Per-capability byte encoders
        ├── frames.py        # 20-byte frame primitives
        ├── packets.py       # 0xA3 multipacket chunker
        ├── diy.py           # DIY effect payloads
        ├── client.py        # LanUdpClient (the only LAN-specific piece)
        └── errors.py        # GoveeProtocolError hierarchy
```

---

## Component responsibilities

### Entry point (`__init__.py`)

- `async_setup` registers the two service actions once for the domain; each call resolves a loaded entry or raises `ServiceValidationError`.
- `async_setup_entry` builds the REST client and the coordinator, stores the coordinator in `entry.runtime_data` (typed as `GoveeConfigEntry`), runs the first refresh (`ConfigEntryAuthFailed` for a bad key, `ConfigEntryNotReady` for a cloud outage), removes entities and devices the account no longer reports, registers the Bluetooth unsubscribe callbacks and the options listener with `entry.async_on_unload`, and forwards the ten platforms.
- `async_unload_entry` unloads the platforms and shuts the coordinator down, which stops MQTT, the OpenAPI listener, LAN, BLE, and every timer.
- `async_migrate_entry` moves v1 entries to schema v2 (IoT credentials live in `entry.data`).
- `_async_cleanup_orphaned_entities` removes entities of devices missing from a complete discovery and honours the feature toggles; leak sensors, hubs, and the diagnostics device are protected, and a failed or empty discovery skips removal. `async_remove_config_entry_device` lets the user delete a device the account no longer reports.

### Coordinator (`coordinator.py`)

`GoveeCoordinator` is a `DataUpdateCoordinator` and the only owner of device state.

- **Discovery.** Developer API device list, then, with account login, the account list for leak sensors and their hubs, gateway-bridged thermometers, and probe thermometers the Developer API does not return. A rediscovery pass every 5 minutes schedules a reload when a new device appears.
- **Polling.** One request per device, in parallel, each with its own deadline. Devices whose entities are all disabled are skipped. A total outage raises `UpdateFailed` so entities go unavailable and the coordinator logs once; a rate-limit answer backs the interval off and raises the `rate_limited` repair.
- **Push.** MQTT (`_on_mqtt_state_update`), OpenAPI events, LAN reads, and BLE advertisements update the state object in place and call `async_set_updated_data` only when a value changed; the advertisement handler uses `async_update_listeners` so it never reschedules the poll.
- **Control.** `async_control_device(device_id, command)` routes each command to the fastest transport that can carry and confirm it: BLE, then LAN (verified by reading the device back), then MQTT (opt-in, acknowledged at QoS 1), then REST. It applies the optimistic update, paces segment writes, and returns `False` when Govee rejects the command.
- **Supporting state.** Scene cache with TTL, per-transport health, the segment colour overlay replayed after whole-device writes, MQTT topics per device, credential refresh persisted to `entry.data`, and the repair issues.

### Config flow (`config_flow.py`)

| Step | Purpose |
|---|---|
| `user` | API key; validated against the device list; one entry per key |
| `account` | Optional email and password; obtains IoT credentials |
| `verification_code` | Email code when Govee requires 2FA |
| `bluetooth` / `bluetooth_confirm` | Discovery prompt from the manifest's Bluetooth matchers; one prompt, then the user step |
| `reauth` / `reauth_confirm` | New API key through `async_update_reload_and_abort` |
| `reconfigure` | Replace key or account; clears stored IoT material when the account changes |
| Options `init` | Intervals, unit handling, feature toggles, transport options, LAN targets |
| Options `select_segment_devices`, `configure_device_mode` | Per-device segment mode: disabled, grouped, individual, both |

### Entities (`entity.py` and the platforms)

`GoveeEntity` sets the unique id from the device id plus a suffix, builds `device_info` (with `via_device` for hub-attached devices), and reports availability as coordinator health combined with the device's online flag (groups follow coordinator health only). Actions call `_async_send_command`, which raises a translated `HomeAssistantError` when the coordinator returns `False`; invalid input raises `ServiceValidationError`. Entities that keep optimistic state (segments, several switches and numbers) use `RestoreEntity`. Leak-sensor entities subscribe to a dispatcher signal instead of the coordinator so unrelated entities do not churn.

### Models (`models/`)

`GoveeDevice` and `GoveeCapability` are frozen; `GoveeDevice` derives its platform support from capabilities, clamps over-reported segment counts, and can be synthesised for probe thermometers. `GoveeDeviceState` is mutable and updated in place. Commands are frozen objects without a device id; the coordinator supplies it.

### API layer (`api/`)

- `GoveeApiClient`: REST with `aiohttp-retry`, a 30 s timeout, in-body error mapping, and local request accounting (Govee reports the per-minute allowance but not the daily one).
- `GoveeAuthClient`: login, 2FA code request and retry, IoT credential extraction (PEM or PKCS#12), and the account (BFF) reads for topics, leak sensors, thermometers, and the device census.
- `GoveeAwsIotClient`: mutual-TLS MQTT with reconnect backoff, a once-per-outage log, status re-queries, and `ptReal` frames; blocking TLS and temp-file work runs in the executor.
- `GoveeOpenApiEventClient`: the official push channel for events such as water-tank-full, API key only.
- LAN, BLE, and probe modules: discovery and verified writes over UDP, direct BLE with the encrypted handshake newer firmware needs, and the probe thermometer register map.

Both HTTP clients take the Home Assistant `aiohttp` session and never create their own.

---

## Data flow

### Poll

```
update_interval → _async_update_data
  → rediscovery (every 5 min) → reload if a new device appeared
  → gather(_fetch_device_state per pollable device)
  → GoveeAuthError → ConfigEntryAuthFailed (reauth)
  → every read failed to reach Govee → UpdateFailed (entities unavailable, logged once)
  → partial failures keep the previous state per device
  → account (BFF) refresh, LAN overlay, transport health
  → entities re-render through CoordinatorEntity
```

### Push

```
MQTT / OpenAPI event / LAN read / BLE advertisement
  → decode → update GoveeDeviceState in place
  → changed? → async_set_updated_data (BLE advertisements: async_update_listeners)
```

### Control

```
entity action → _async_send_command(command)
  → coordinator.async_control_device(device_id, command)
     → BLE (allowlisted models) → LAN (verified by read-back) → MQTT (opt-in) → REST
     → optimistic state update
  → False → HomeAssistantError("command_failed")
```

---

## Platforms

| Platform | Entities |
|---|---|
| `light` | Main light, nightlight, main panel; per-segment and grouped-segment lights (`platforms/`) |
| `select` | Scene, DIY scene, snapshot, HDMI source, music mode, fan speed, purifier mode, preset scene, nightlight scene |
| `switch` | Plugs, sockets, MQTT outlets, night light, light zones, named lights, music mode, DreamView, heater auto-stop, appliance power, probe live polling |
| `fan` | Tower and purifier fans, ceiling fans |
| `humidifier` | Humidifiers and dehumidifiers |
| `number` | Music sensitivity, heater target temperature, probe alarm limits |
| `sensor` | Temperature, humidity, probe temperatures, battery, filter life, AQI, CO2, dehumidifier mode, kettle temperature, connection mode, and diagnostic timestamps; hub-level rate limit and MQTT status |
| `binary_sensor` | Device connectivity, per-transport connectivity (opt-in), water tank full, pump state, water leak, occupancy, leak sensor and hub online |
| `event` | Leak sensor button press |
| `button` | Refresh scenes, clear water alert |

Every platform declares `PARALLEL_UPDATES = 0`; the coordinator paces writes. Noisy diagnostics (rate limit, last update, last command, MQTT received, leak addresses) are disabled by default.

---

## Services

| Action | Behaviour |
|---|---|
| `govee.refresh_scenes` | Re-fetches the scene catalogue for one device or all; `device_id` accepts a Home Assistant device or a Govee id |
| `govee.set_segment_color` | Sets the RGB colour of listed segments; indices past the device's segment count raise `ServiceValidationError` |
| `govee.send_raw_ptreal` | Admin-only debugging aid: sends one raw BLE ptReal frame over the AWS IoT passthrough |
| `govee.apply_diy_effect` | **Fork.** Compose and upload a DIY effect to one multi-zone lamp |

All are registered in `async_setup` and raise `HomeAssistantError` when Govee rejects the command.

---

## Fork: the raw device protocol layer

The cloud/OpenAPI path cannot express per-zone colour, ripple flow rate, downlight
colour temperature, or a per-segment paint that arrives in one round trip. The fork
adds a second, *raw* control path built on Govee's 20-byte device frames, plus the
state and health machinery that path needs. Everything in this section is additive:
with every fork option off, none of it runs.

### `api/protocol/` — the codec package

A self-contained library with **zero Home Assistant imports**: plain Python, unit
testable on its own, adding no entities and no coordinator hooks by itself.

| Module | Responsibility |
|--------|----------------|
| `profiles.py` | The single hardware-truth table: what each SKU can do, and the byte constants that do it |
| `encoders.py` | Frame layouts, named by the table |
| `frames.py` | 20-byte frame assembly, XOR checksum, segment masks, `ptReal` envelope |
| `packets.py` | `0xA3` multipacket chunker and commit frame (effect uploads) |
| `diy.py` | DIY effect payload records (`0x50` form) riding the chunker |
| `codec.py` | profile + intent → frames |
| `client.py` | Write-only UDP send to port 4003 |
| `errors.py` | Protocol exception hierarchy |

**`profiles.py` is the only place SKU knowledge lives.** Zones, their capabilities,
kelvin ranges, segment mask width, which transports carry raw frames, echo lag, the
simultaneous-zone limit and its displacement order, and the DIY mode tables are all
declared there. No module above it contains an SKU name, zone byte, or kelvin
constant; a new SKU is a table entry, not a code branch. Anything not confirmed on
hardware is marked `UNKNOWN` and refused rather than guessed, and every byte constant
is pinned by golden-frame tests.

Frames are **transport-neutral**: the identical bytes travel over LAN UDP, BLE GATT,
and Govee's cloud MQTT. Only `client.py` is LAN-specific, so a new transport reuses
everything above it.

### `api/raw_router.py` — three-tier dispatch

`async_route_frames()` hands a frame sequence to the best pipe the device has right
now, in order. Each tier lives in its own module:

| # | Tier | Module | Notes |
|---|------|--------|-------|
| 1 | LAN raw | `api/lan_raw.py` | One `ptReal` UDP datagram on the local subnet; the default for SKUs on the modern stack |
| 2 | BLE plaintext | `api/ble_raw_write.py` | The same bytes over an unencrypted GATT write, for SKUs whose only raw pipe that is. A one-central link, so it is tried second and held only briefly |
| 3 | MQTT `ptReal` | `api/mqtt_raw_write.py` | The same bytes published to the device's cloud topic. Covers a lamp with no LAN correlation and every SKU with no usable local raw pipe |

When every tier declines, `async_route_frames()` returns `False` and the **caller**
falls back to whatever ordinary cloud capability command it had. That cloud command is
not a tier of the router — it is the caller's own path, which the router never touches.

Contracts that hold for every tier:

- **A tier never raises at the entity.** Any exception inside a tier is caught, logged
  at debug, and treated as "not handled" so the tiers below it still get their turn.
- **A tier never confirms.** Raw frames are unacknowledged on all three channels, so
  callers keep optimistic state.
- **Each tier applies its own gate** — the user option *and* the profile's declared
  `transports` list. Having a profile is not permission to send: an SKU that accepts
  the datagram and ignores the frame must fall back, because on a write-only path that
  is indistinguishable from success.
- Upstream's issue-#57 LAN write-suppression cooldown is consulted **inside the LAN
  tier only**. It is a statement about the LAN pipe, so a device inside it stays
  paintable over BLE and MQTT.

**Option boundary.** `enable_lan_raw_write` buys an optional *fast path* for writes the
cloud can also make — whole-device and per-segment paints. It does **not** gate the
zone and DIY features: for those, raw LAN is the only pipe that exists at all, so they
are owned by `enable_zone_lights` and call `lan_target(require_option=False)`.
`enable_ble_raw_write` gates the BLE tier alone.

### Per-coordinator state registries

`zone_state.py` and `diy_state.py` hold mutable, per-config-entry runtime state keyed
`(device_id, zone_key)`. They are **not** in `models/` — `models/` is frozen value
objects, these are live registries with listeners, cached lazily on the coordinator so
no upstream `__init__` line is touched.

- **`zone_state`** is the truth for zone on/off. No local channel reports per-zone
  state, and the zones of one lamp are driven from two entity platforms (`switch.py`
  and `platforms/zone_light.py`) that cannot see each other, so the state must sit
  below both. It also applies the profile's `MaxSimultaneousZones` constraint — a lamp
  that can light only two of three zones drops one *inside the lamp* when a third is
  switched on. The displacement ranking (`displacement_order`, weakest first) is a
  fixed ranking, not a recency rule, and it is applied **below the choice of
  transport**, so the displaced zone's entity is corrected whether the write went out
  over LAN or over the cloud.
- **`diy_state`** stages a DIY effect. A DIY effect is a *document*, not a command:
  two zone records with a mode, speed, palette, direction and flow rate, uploaded as
  one multipacket blob and then committed — there is no partial write. So the `select`,
  `number` and `text` entities write fields into a staged record and a `button` (or the
  `govee.apply_diy_effect` service) uploads the assembled document.

Neither registry persists. The entities are `RestoreEntity` instances and seed their
restored value back in on registration, so a restart rebuilds the same optimistic
picture without the registries knowing about HA's state machine.

### Confirm policy (`lan_confirm.py`, `child_power.py`)

Upstream's LAN write path verifies by reading `devStatus` back. Some SKUs keep
reporting their **pre-command** state for a declared *echo lag* after a write, so a
read taken immediately can only fail — and a false failure arms the write-suppression
cooldown on hardware that is behaving correctly. `lan_confirm` therefore settles for
the profile's declared lag before reading, and refuses to count a readback that landed
inside the lag as a miss. An SKU with no declared lag gets `0.0` and every function
becomes a no-op, i.e. upstream's semantics exactly.

`child_power` is the one rule shared by zones and segments: a child entity has no power
of its own, so turning one on first powers the whole lamp over the **normal** transport
(`coordinator.async_control_device`), never as a raw frame — that path is what the
master's own state is derived from. It is deliberately one-directional; a child turning
off never powers the lamp off, because other children may still be lit. The power write
is dispatched with `defer_lan_confirm=True`: the send is inline (the child's frames only
need the datagram to have left the host, since the lamp processes in arrival order) and
the settle, confirm, re-send and miss counting run in a coordinator-owned background
task. A short per-device latch suppresses a duplicate power command inside the echo lag,
when the state snapshot would still read "off".

### `segment_limit.py` — the segment count

Govee's platform API over-reports how many segments an RGBIC lamp has. The over-report
creates phantom entities that can never light anything, and it trips the raw-write gate
that compares the entity segment count against the profile's mask width. The fix is at
entity creation, not at the gate: **the count comes from the profile table's mask
width**, the same number the codec builds masks from. This module owns that rule; it
only ever *lowers* a cloud-reported count for an SKU it has hardware knowledge of, and
never raises one. An unprofiled SKU keeps the cloud's count untouched.

### `api/segment_readback.py` — the one push readback

Segment entities are optimistic by necessity (the cloud returns empty strings for
segment colours) and deliberately do not subscribe to coordinator updates. But some
SKUs push unsolicited `aa a5` frames on the AWS IoT status channel carrying each
segment's level and RGB, in groups of four. Decoding them costs nothing extra on a
channel already subscribed. The module is **decode only** — no HA imports, no entity
knowledge; the caller supplies the segment count and routes the result. Readings at or
above the profile's verified segment count are phantom padding and are dropped. The
XOR checksum is an integrity check, never an authenticity one.

The decoded reading reaches the entities on a dedicated dispatcher signal
(`SIGNAL_SEGMENT_READBACK`), not through the coordinator — a deliberate, narrow
exception to the segment entities' no-coordinator-subscription rule, scoped to exactly
this payload.

### `diy_previews.py` — DIY mode artwork

DIY mode names alone (`twinkle`, `gradient`, `jumping`) do not tell anyone what the lamp
will do, and HA `select` entities cannot render images in their options. The vendor's
preview stills are shipped inside the integration and served from a static URL prefix
outside `/api/`, so a dashboard picture card can render one without a bearer token.
Nothing fetches from Govee at runtime. The mode-name → filename map is the join between
two independently-sourced things, so it is asserted in both directions by the tests: a
mode added to a profile without artwork fails the suite rather than shipping a dead URL.

### `lan_nudge.py`, `lan_udp_health.py`

`lan_nudge` treats the cloud MQTT push as a **content-free change signal** — "device X
changed, go look" — and answers it with a LAN `devStatus` read, decoupling
LAN-reachable devices from the cloud payload schema. Coalescing, self-echo suppression
and a per-device cooldown keep it from amplifying a chatty broker into UDP traffic.

`lan_udp_health` scores the raw LAN write path as a fifth transport beside `cloud_api`,
`mqtt`, `ble` and `lan`. Availability cannot mean "a write landed" — nothing on the raw
channel is acknowledged — so it means "a raw frame sent right now would have somewhere
to go", gated on exactly the conditions `lan_target()` gates on. A sensor reading
"connected" for a device the writer refuses to write to would be worse than no sensor.

---

## Error handling

```
GoveeApiError
├── GoveeAuthError (401)             setup and polling → ConfigEntryAuthFailed → reauth flow
├── GoveeRateLimitError (429)        interval backs off; rate_limited repair (fixable)
├── GoveeConnectionError             setup → ConfigEntryNotReady; polling → per-device isolation, UpdateFailed on total outage
├── GoveeDeviceNotFoundError (400)   expected for groups and probe thermometers; optimistic state
├── GoveeLoginRejectedError          account login rejected; mqtt_disconnected repair (fixable)
├── Govee2FARequiredError (454)      config flow asks for the code; at startup → mqtt_2fa_required repair
└── Govee2FACodeInvalidError (454)   config flow error
```

User-facing failures carry translation keys from the `exceptions` block of `strings.json`.

### Repairs (`repairs.py`)

| Issue | Kind | Fix |
|---|---|---|
| `rate_limited` | Fixable | The flow doubles the polling interval (up to 300 s) |
| `mqtt_disconnected` | Fixable | The flow clears the stored login-failure marker and reloads the entry |
| `mqtt_2fa_required` | Informational | Reconfigure and enter the email code |
| `mqtt_token_expired` | Informational | Reconfigure with the current password |

An invalid API key does not raise a repair; Home Assistant's reauth flow handles it.

---

## Configuration options

| Option | Default | Description |
|--------|---------|-------------|
| `poll_interval` | 60 s | State refresh frequency (30 to 300) |
| `water_detector_poll_interval` | 120 s | Leak poll for standalone RF detectors (60 to 3600) |
| `probe_poll_interval` | 30 s | Read rate for armed probe thermometers (10 to 600) |
| `mqtt_status_interval` | 300 s | MQTT status re-query interval (60 to 3600, 0 = off) |
| `api_temperature_unit` | auto | Fahrenheit handling for thermometer readings |
| `enable_groups` | false | Include Govee app groups |
| `enable_scenes` | true | Scene selects and light effects |
| `enable_diy_scenes` | true | DIY scene selects |
| `expose_transport_entities` | false | Per-transport connectivity sensors |
| `enable_mqtt_control` | false | Route power, brightness, and colour over MQTT |
| `lan_targets` | empty | Extra LAN scan targets, `device_id=ip[!]` overrides, or `off` |
| `segment_mode_by_device` | individual | Per-device segment entity mode |
| `enable_zone_lights` | false | **Fork.** Split a multi-zone lamp into per-zone light entities (and the DIY authoring controls) |
| `enable_lan_raw_write` | false | **Fork.** Opt into the raw LAN tier for segment writes the cloud can also make. Zone and DIY writes do not need it |
| `enable_ble_raw_write` | false | **Fork.** Opt into the plaintext-BLE raw tier, for SKUs with no local Wi-Fi raw pipe |

---

## Quality scale

`manifest.json` declares **silver**, because the fork's own modules are not assessed against the gold and platinum rules. `quality_scale.yaml` records every rule with a comment saying how it is met or why it is exempt, and `docs/code-review-2026-09-13.md` holds the rule-by-rule validation against the code.
