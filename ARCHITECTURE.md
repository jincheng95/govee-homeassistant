# Govee Integration Architecture

This document provides a comprehensive overview of the Govee Home Assistant integration architecture.

---

## Overview

The Govee integration is a **hub-type** Home Assistant integration that connects to the Govee Cloud API v2.0 to control lights, LED strips, and smart plugs. It follows Clean Architecture principles with:

- **Config Flow**: UI-based configuration with reauth and reconfigure support
- **DataUpdateCoordinator**: Centralized state management and polling
- **Platform Entities**: Light, Scene, Switch, Sensor, Button platforms
- **Command Pattern**: Immutable command objects for device control
- **Protocol Interfaces**: Clean separation between layers
- **Repairs Framework**: Actionable notifications for common issues

**Integration Type**: `hub` (cloud service managing multiple devices)
**IoT Class**: `cloud_push` (MQTT real-time updates with polling fallback)
**API Version**: Govee API v2.0

---

## Directory Structure

```
custom_components/govee/
├── __init__.py              # Integration entry point
├── config_flow.py           # Config flow (user, account, reauth, reconfigure)
├── coordinator.py           # DataUpdateCoordinator with MQTT integration
├── entity.py                # Base entity class (GoveeEntity)
├── light.py                 # Light platform
├── scene.py                 # Scene platform
├── switch.py                # Switch platform (plugs, night light)
├── sensor.py                # Sensor platform (rate limit, MQTT status)
├── button.py                # Button platform (refresh scenes)
├── services.py              # Custom services
├── repairs.py               # Repairs framework integration
├── diagnostics.py           # Diagnostics for troubleshooting
├── const.py                 # Constants
├── manifest.json            # Integration metadata
├── strings.json             # UI strings
├── services.yaml            # Service definitions
├── quality_scale.yaml       # Quality scale tracking
├── translations/
│   └── en.json              # English translations
├── models/                  # Domain models (frozen dataclasses)
│   ├── __init__.py
│   ├── device.py            # GoveeDevice, GoveeCapability
│   ├── state.py             # GoveeDeviceState, RGBColor
│   └── commands.py          # Command pattern implementations
├── platforms/
│   ├── __init__.py
│   └── segment.py           # Segment light entities (RGBIC)
├── protocols/               # Protocol interfaces
│   ├── __init__.py
│   ├── api.py               # IApiClient, IAuthProvider
│   └── state.py             # IStateProvider, IStateObserver
└── api/                     # API layer
    ├── __init__.py
    ├── client.py            # GoveeApiClient (REST)
    ├── auth.py              # GoveeAuthClient (account login)
    ├── mqtt.py              # GoveeAwsIotClient (real-time MQTT)
    └── exceptions.py        # Exception hierarchy
```

---

## Component Responsibilities

### Entry Point (`__init__.py`)

- `async_setup_entry()`: Initialize integration
- `async_unload_entry()`: Clean up on removal
- Creates API client and coordinator
- Forwards platform setup
- Registers update listener for options changes

### Coordinator (`coordinator.py`)

Central hub for device state management:

- **Device Discovery**: Fetches devices from API on setup
- **Parallel State Fetching**: Queries all device states concurrently
- **MQTT Integration**: Real-time state updates via AWS IoT
- **Scene Caching**: Caches scenes to minimize API calls
- **Optimistic Updates**: Immediate UI feedback after commands
- **Observer Pattern**: Notifies entities of state changes
- **Repairs Integration**: Creates repair issues for errors

### Config Flow (`config_flow.py`)

UI-based configuration:

1. **User Step**: Enter API key
2. **Account Step**: Optional email/password for MQTT
3. **Reauth Step**: Re-authenticate on 401 errors
4. **Reconfigure Step**: Update credentials without removing integration
5. **Options Flow**: Poll interval, enable groups/scenes/segments

### Models (`models/`)

Frozen dataclasses for immutability:

- **GoveeDevice**: Device metadata and capabilities
- **GoveeDeviceState**: Current device state (mutable for updates)
- **RGBColor**: Immutable RGB color value
- **Commands**: PowerCommand, BrightnessCommand, ColorCommand, etc.

### Protocols (`protocols/`)

Clean Architecture interfaces:

- **IApiClient**: Contract for API operations
- **IAuthProvider**: Contract for authentication
- **IStateProvider**: Contract for state access
- **IStateObserver**: Contract for state change notifications

### API Layer (`api/`)

- **GoveeApiClient**: REST API with aiohttp-retry for resilience
- **GoveeAuthClient**: Account login and IoT credential retrieval
- **GoveeAwsIotClient**: AWS IoT MQTT for real-time updates
- **Exceptions**: Hierarchical exception classes with translation support

---

## Data Flow

### State Update Flow

```
Poll Interval Timer
        ↓
coordinator._async_update_data()
        ↓
Parallel: fetch state for all devices
        ↓
Process results:
  - Success → Update state
  - Auth Error → Create repair issue, trigger reauth
  - Rate Limit → Create repair issue, keep previous state
        ↓
coordinator.async_set_updated_data()
        ↓
Entities receive state update
```

### MQTT Real-time Flow

```
MQTT Message Received
        ↓
_on_mqtt_state_update()
        ↓
Update state from MQTT data
        ↓
coordinator.async_set_updated_data()
        ↓
Notify observers
        ↓
UI updated immediately
```

### Control Command Flow

```
User Action (turn on, set color, etc.)
        ↓
Entity method (async_turn_on, etc.)
        ↓
coordinator.async_control_device()
        ↓
Create Command object (immutable)
        ↓
API client sends command
        ↓
Apply optimistic state update
        ↓
UI updated immediately
```

---

## Platforms

| Platform | Entity Types | Description |
|----------|--------------|-------------|
| `light` | GoveeLightEntity, GoveeSegmentLight | Main lights and RGBIC segments |
| `scene` | GoveeSceneEntity | Dynamic scenes from Govee cloud |
| `switch` | GoveePlugSwitchEntity, GoveeNightLightSwitchEntity | Smart plugs, night light toggle |
| `sensor` | Rate limit, MQTT status | Diagnostic sensors |
| `button` | Refresh scenes | Manual scene refresh |

---

## Services

| Service | Description |
|---------|-------------|
| `govee.refresh_scenes` | Refresh scene list from API |
| `govee.set_segment_color` | Set color for RGBIC segments |

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

### `api/raw_router.py` — four-tier dispatch

`async_route_frames()` hands a frame sequence to the best pipe the device has right
now, in order:

1. **LAN raw** — one `ptReal` UDP datagram on the local subnet; the default for SKUs
   on the modern stack.
2. **BLE plaintext** — the same bytes over an unencrypted GATT write, for SKUs whose
   only raw pipe that is. BLE is a one-central link, so it is tried second and held
   only briefly.
3. **MQTT `ptReal`** — the same bytes published to the device's cloud topic. Covers a
   lamp with no LAN correlation and every SKU with no usable local raw pipe.
4. **Cloud command** — not a tier of the router: when the router returns `False`, the
   caller falls back to whatever ordinary cloud capability command it had.

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

## Error Handling

### Exception Hierarchy

```
GoveeApiError (base)
├── GoveeAuthError (401) → Triggers reauth, creates repair issue
├── GoveeRateLimitError (429) → Creates repair issue, keeps previous state
├── GoveeConnectionError → Logs warning, retries
└── GoveeDeviceNotFoundError (400) → Expected for groups, uses optimistic state
```

### Repairs Framework

Actionable repair notifications:

- **auth_failed**: Fixable, guides to reauth flow
- **rate_limited**: Warning with reset time estimate
- **mqtt_disconnected**: Warning about real-time updates

---

## Configuration Options

| Option | Default | Description |
|--------|---------|-------------|
| `poll_interval` | 60s | State refresh frequency |
| `enable_groups` | false | Include Govee app groups |
| `enable_scenes` | true | Create scene entities |
| `enable_segments` | true | Create segment entities for RGBIC |

---

## Quality Scale

The integration targets **Gold tier** compliance:

- ✅ Config flow with test coverage
- ✅ Unique entity IDs
- ✅ Device info for all entities
- ✅ Diagnostics platform
- ✅ Reauthentication flow
- ✅ Reconfigure flow
- ✅ Repairs framework
- ✅ Entity translations
- ✅ Async dependencies

See `quality_scale.yaml` for detailed compliance tracking.
