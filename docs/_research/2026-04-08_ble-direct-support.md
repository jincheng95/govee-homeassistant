# Research: Direct BLE Support for hacs-govee (developing PR #52 ourselves)

**Date**: 2026-04-08
**Type**: Architecture Decision + Feature Investigation
**Status**: Complete
**Stack**: Home Assistant custom component, Python 3.12, existing cloud integration (Govee API v2.0 REST + AWS IoT MQTT)

---

## Summary

We're taking on direct BLE support for Govee BLE lights ourselves rather than waiting on PR #52's author to iterate. The PR's GATT protocol code is substantially correct (matches Beshelmek's reference implementation on packet framing, XOR checksum, command bytes, and characteristic UUIDs) but has two meaningful protocol divergences that must be fixed, plus architectural flaws that make the PR unmergeable as-is. The correct architecture is a **single `govee` integration with two coexisting config entry types** (cloud-account + BLE-device), branching in `async_setup_entry` on a new `CONF_ENTRY_TYPE` discriminator — not a separate integration and not a runtime `isinstance` leak into `light.py`. GATT I/O must be delegated to a new `api/ble.py` module that owns a `BleakClient` via `bleak-retry-connector`, following the `led_ble` core integration precedent. The manifest stays `cloud_push` (per-entry classes aren't a thing), BLE discovery is wired via `manifest.json` `bluetooth:` matchers plus a new `async_step_bluetooth` config flow step, and cloud+BLE entries for the same physical device merge into one device-registry row via a shared `connections={(CONNECTION_BLUETOOTH, mac)}` tuple — the same pattern august + yalexs_ble use. Adopting Beshelmek's `unique_id = mac.replace(":","")` format gives users a clean migration path from `Beshelmek/govee_ble_lights` without automation breakage.

---

## Research Questions

### Q1: What HA integration pattern fits a dual-mode cloud + BLE Govee integration?
**Answer**: Single domain, two entry types, branch in `async_setup_entry`. HA supports N config entries per domain with different `data` shapes; the idiomatic fork is a discriminator field (`entry.data[CONF_ENTRY_TYPE]`) plus distinct `entry.unique_id` namespaces (cloud = email, BLE = `discovery_info.address.upper()`). Avoid a second `govee_ble` HACS integration — it would double the user-visible setup surface without architectural benefit since we're not publishing to HA core. The canonical HA-core precedent (`august` + `yalexs_ble`) is a two-domain split, but that pattern exists mostly because those are separately maintained repos; a single-domain dual-entry setup is equally valid and used by integrations like `dyson_local`.

### Q2: How does HA Bluetooth discovery wire end-to-end?
**Answer**: Five pieces. (1) `manifest.json` `bluetooth:` matchers with `local_name` wildcards (`Govee_*`, `ihoment_*`, `GBK_*`) plus `"connectable": true` because we do GATT writes. (2) `dependencies: ["bluetooth_adapters"]`. (3) Config flow `async_step_bluetooth(discovery_info: BluetoothServiceInfoBleak)` → `async_set_unique_id(address)` → `async_step_bluetooth_confirm`. (4) At setup time: `bluetooth.async_ble_device_from_address(hass, address, connectable=True)` → `ConfigEntryNotReady` if None. (5) Register a `PASSIVE` callback via `bluetooth.async_register_callback` that pushes fresh `BLEDevice` references into the device library via `set_ble_device_and_advertisement_data()`. The GATT client itself is owned by the device library (a new `api/ble.py` module), not the coordinator.

### Q3: Is PR #52's Govee BLE wire protocol correct?
**Answer**: Substantially yes, with three identified discrepancies to fix. Packet framing (20 bytes, head `0x33`, XOR checksum in byte 19), write characteristic `00010203-0405-0607-0809-0a0b0c0d2b11`, and command bytes `POWER=0x01, BRIGHTNESS=0x04, COLOR=0x05, SEGMENT=0xA5` all match Beshelmek's proven reference. **Divergences** — (a) PR #52 rescales brightness to 0–100 for segmented devices while Beshelmek sends 0–255 for all models; (b) PR #52 ends the SEGMENTS frame with `0xFF, 0xFF` but Beshelmek uses `0xFF, 0x7F`; (c) PR #52 has a `LEGACY = 0x0D` color type that is **not present** in Beshelmek's reference — source unclear, should be removed unless a primary source is found. Beshelmek's `SEGMENTED_MODELS` whitelist is only 4 SKUs (`H6053, H6072, H6102, H6199`); PR #52's approach of asking the user to choose "segmented" at setup is a reasonable simplification but should default per-SKU where possible.

### Q4: Is there a Python library we can depend on instead of inlining the protocol?
**Answer**: No usable library exists for writable Govee BLE lights. The `govee-ble` package on PyPI (maintained by `bdraco` at `bluetooth-devices/govee-ble`) is a **passive advertisement parser for Govee temperature/humidity sensors**, not a writable GATT client — it's what HA core's `govee_ble` integration uses for H5xxx sensors. `govee-api-ble` and `govee-h6199-ble` exist but are single-SKU hobby projects. **Conclusion: inline the protocol**, lifting patterns (not code) from `Beshelmek/govee_ble_lights` which is the most battle-tested reference with 76 SKU profiles and 122 GitHub stars.

### Q5: How should cloud+BLE entries for the same physical device coexist?
**Answer**: Merge at the device registry via a shared `connections={(dr.CONNECTION_BLUETOOTH, mac)}` tuple — the same mechanism august+yalexs_ble use (`yalexs_ble/entity.py`). Both the cloud entity (when the device is a BLE-capable SKU with a known MAC from the cloud `/user/devices` response) and the BLE entity emit identical `CONNECTION_BLUETOOTH` tuples; HA merges them into one device row while keeping them as separate entities (`light.h601f` cloud + `light.h601f_ble` BLE). Single-entity-multi-transport ("automatic takeover") is **not a pattern HA supports** — attempting to unify the two into one entity is what got PR #52 into the isinstance-leak trouble in `light.py`.

### Q6: Can we migrate `Beshelmek/govee_ble_lights` users cleanly?
**Answer**: Yes, via unique_id format compatibility. Beshelmek's entity unique_id is `mac.replace(":","")` (12 hex chars, no separators); if we match that format for our BLE entities, a user can remove `govee_ble_lights` from HACS and add ours without losing entity IDs or breaking automations. Config entry data is different (Beshelmek stores `CONF_MODEL` from a dropdown; we'll probe advertisement name and probably ask only for segmented on/off), so the config entry itself doesn't migrate — but the entity registry ID survives, which is what actually matters for HA automations.

---

## Findings

### Theme 1: Protocol validation against Beshelmek's reference

Beshelmek's `custom_components/govee-ble-lights/light.py` and `govee_utils.py` are the gold standard for Govee BLE light control — 122 stars, 76 SKU profiles, last pushed 2025-07-09, actively maintained. PR #52's `api/ble_direct.py` is ~90% compatible with Beshelmek:

**Matching (use PR #52 as-is)**:
- Write characteristic UUID: `00010203-0405-0607-0809-0a0b0c0d2b11` ([Beshelmek/light.py:33](https://github.com/Beshelmek/govee_ble_lights/blob/master/custom_components/govee-ble-lights/light.py#L33))
- Packet head `0x33`, checksum algorithm (`XOR all 19 bytes → byte 19`)
- `POWER=0x01`, `BRIGHTNESS=0x04`, `COLOR=0x05`
- `MANUAL/SINGLE=0x02` for non-segmented color
- `SEGMENTS=0x15` mode byte
- Beshelmek `_prepareSinglePacketData` is byte-for-byte equivalent to PR #52's `_generate_frame`

**Divergences to correct before shipping**:

1. **Brightness scaling**: Beshelmek sends the HA brightness value (0–255) unchanged for all SKUs. PR #52 rescales to 0–100 when `self._segmented=True`:
   ```python
   # PR #52 ble_direct.py set_brightness
   payload = round(brightness / 255 * 100) if self._segmented else round(brightness)
   ```
   Beshelmek has no such rescale:
   ```python
   # Beshelmek light.py async_turn_on
   commands.append(self._prepareSinglePacketData(LedCommand.BRIGHTNESS, [brightness]))
   ```
   **Action**: drop the rescale, send brightness 0–255. Reason: Beshelmek is the proven reference with 76 SKU profiles; if the rescale were correct it would be in Beshelmek.

2. **SEGMENTS frame tail byte**: Beshelmek ends the segmented color frame with `0xFF, 0x7F`; PR #52 uses `0xFF, 0xFF`. The difference is trailing bit pattern — unverified which the device firmware actually expects, but the working reference wins:
   ```python
   # Beshelmek light.py:272
   [LedMode.SEGMENTS, 0x01, red, green, blue, 0x00, 0x00, 0x00, 0x00, 0x00, 0xFF, 0x7F]
   ```
   **Action**: match Beshelmek's tail.

3. **`LEGACY = 0x0D` color type**: PR #52 defines `LedColorType.LEGACY = 0x0D` and for non-segmented devices sends BOTH a `SINGLE` frame and a `LEGACY` frame:
   ```python
   # PR #52 ble_direct.py set_color — non-segmented branch
   self._queue(LedPacketCmd.COLOR, [LedColorType.SINGLE, red, green, blue])
   self._queue(LedPacketCmd.COLOR, [LedColorType.LEGACY, red, green, blue])
   ```
   Beshelmek has no `0x0D` constant anywhere; only `MANUAL=0x02, MICROPHONE=0x06, SCENES=0x05, SEGMENTS=0x15`. Source of `0x0D` is unknown.
   **Action**: remove `LEGACY` and the duplicate frame. Ship with only `SINGLE/MANUAL=0x02`. If a specific old SKU turns out to need 0x0D, add it back with a primary source citation.

**Scope narrowed (not in PR #52, not urgent)**:
- Scene effects (Beshelmek's multi-packet `prepareMultiplePacketsData` with `protocol_type=0xa3` + base64 `scenceParam` payloads from its 76 JSON profiles) — out of scope for initial BLE shipping. Add later if demand.
- Microphone mode (`0x06`) — out of scope.

### Theme 2: Dual-mode integration architecture

**Multiple entries per domain is supported** ([HA dev docs — config flow handler](https://developers.home-assistant.io/docs/config_entries_config_flow_handler/)). `async_setup_entry(hass, entry)` is called once per `ConfigEntry`; nothing forces one integration to one entry-shape. Pattern:

```python
async def async_setup_entry(hass: HomeAssistant, entry: GoveeConfigEntry) -> bool:
    if entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_BLE:
        return await _async_setup_ble_entry(hass, entry)
    return await _async_setup_cloud_entry(hass, entry)  # existing flow, unchanged
```

The discriminator lives in `entry.data` (set by config_flow at creation time, never changes). Existing cloud entries without the field default to cloud behavior — **zero-risk migration for existing users**.

**Unique ID namespacing is critical**. HA rejects a second config entry with a unique_id that collides with an existing one. Set:
- Cloud entry unique_id: `email` (current — unchanged)
- BLE entry unique_id: `discovery_info.address.upper()` (MAC with colons, upper-case)

These cannot collide (email vs MAC format).

**`iot_class` is a single manifest value** ([HA dev docs — manifest](https://developers.home-assistant.io/docs/creating_integration_manifest/)). There is no per-entry override. Keep `cloud_push` in `manifest.json` — it's cosmetic for the HA UI badge and does not affect runtime behavior. Changing it to `local_polling` (as PR #52 did) misrepresents the integration.

### Theme 3: BLE client ownership pattern

**Delegate GATT I/O to a new module, not the coordinator**. led_ble is the canonical HA-core precedent: a dedicated `led_ble` PyPI library owns the `BleakClient`, the HA integration's coordinator just calls `.update()`, `.turn_on()`, etc. on it. For hacs-govee, we create `api/ble.py` in-tree (since no usable PyPI library exists) that wraps `bleak-retry-connector`:

```python
# api/ble.py skeleton
class GoveeBLEDevice:
    def __init__(self, ble_device: BLEDevice, segmented: bool = False) -> None: ...
    def set_ble_device_and_advertisement_data(self, ble_device, adv) -> None: ...
    async def update(self) -> GoveeBLEState: ...
    async def turn_on(self) -> None: ...
    async def turn_off(self) -> None: ...
    async def set_brightness(self, value_0_255: int) -> None: ...
    async def set_rgb(self, r: int, g: int, b: int) -> None: ...
    async def stop(self) -> None: ...  # disconnect BleakClient cleanly
```

Internally it opens a `BleakClient` on demand via `bleak_retry_connector.establish_connection`, buffers commands, flushes on writes, reconnects automatically. The `set_ble_device_and_advertisement_data()` method is called from a PASSIVE bluetooth callback registered in `_async_setup_ble_entry` — this refreshes the `BLEDevice` reference so reconnects use the current adapter/RSSI.

**PR #52's mistake**: the `BleakClient` lived directly in `coordinator_ble.py` and the `async_shutdown()` method was a no-op. On entry unload the connection leaked.

### Theme 4: Coordinator choice

**Use plain `DataUpdateCoordinator`, not Active/Passive variants** ([HA dev docs — fetching Bluetooth data](https://developers.home-assistant.io/docs/core/bluetooth/bluetooth_fetching_data/)). The Active/Passive Bluetooth coordinators are for advertisement-driven sensors (thermometers, BTHome, H5xxx). Lights are write-heavy/commanded devices where state comes from GATT reads after writes, not from advertisements. led_ble, magic_home, and yalexs_ble all use the plain coordinator.

**Update interval: 60 seconds, not 15**. PR #52's 15s interval is too aggressive — it risks BLE radio contention, battery drain on mesh proxies, and adapter saturation. led_ble defaults to 30–60s; rely on optimistic state updates from commands. After every write, call `self.coordinator.async_request_refresh()` if you want immediate reconciliation.

### Theme 5: Cloud+BLE device merging

**Same physical device → one HA device row, two entities** (the august+yalexs_ble pattern, [yalexs_ble/entity.py](https://github.com/home-assistant/core/blob/dev/homeassistant/components/yalexs_ble/entity.py)). Both the cloud entity and the BLE entity emit `DeviceInfo` with:
```python
connections={(dr.CONNECTION_BLUETOOTH, mac.upper())}
```
HA's device registry merges entries whose `identifiers` OR `connections` overlap. The two entities stay separate; users see both in the UI attached to one device.

**Existing cloud DeviceInfo needs an upgrade**: `GoveeEntity.device_info` currently emits only `identifiers={(DOMAIN, device_id)}`. For BLE-capable SKUs, add the `connections` tuple using the MAC from the cloud device list (`/user/devices` response contains a BLE MAC field for BLE-capable devices — verify this during implementation).

**"Automatic fallback from cloud to BLE" is NOT supported**. Every entity belongs to exactly one config entry. Don't attempt a single entity that delegates between transports — that's what broke PR #52's architecture.

### Theme 6: Manifest changes

Before PR #52 (current master):
```json
{
  "domain": "govee",
  "name": "Govee Cloud Integration",
  "dependencies": [],
  "iot_class": "cloud_push",
  "integration_type": "hub"
}
```

After BLE support (target):
```json
{
  "domain": "govee",
  "name": "Govee Cloud Integration",     // KEEP — renaming is a separate UX decision
  "bluetooth": [
    {"local_name": "Govee_*", "connectable": true},
    {"local_name": "ihoment_*", "connectable": true},
    {"local_name": "GBK_*", "connectable": true}
  ],
  "dependencies": ["bluetooth_adapters"],  // required for BLE matchers to register
  "iot_class": "cloud_push",              // KEEP — single value, cloud remains primary
  "integration_type": "hub",
  "requirements": [
    "aiohttp-retry>=2.8.3",
    "aiomqtt>=2.0.0",
    "bleak-retry-connector>=3.0.0",       // new
    "cryptography>=41.0.0"
  ]
}
```

Note: `connectable: true` (not `false` as in PR #52). We write GATT characteristics, which requires a connectable transport. Beshelmek uses `false` because Beshelmek also supports non-connectable proxy adapters for advertisement-only discovery, but then has to retry; `true` is cleaner for our use case. HA will still route via ESPHome Bluetooth proxies that support connectable relays.

### Theme 7: Cloud-only platforms must not load for BLE entries

Cloud integration currently forwards to 7 platforms:
```python
PLATFORMS = [SELECT, NUMBER, LIGHT, FAN, SWITCH, SENSOR, BUTTON]
```

For BLE entries, only `LIGHT` applies. Define:
```python
_BLE_PLATFORMS = [Platform.LIGHT]
```
and use the correct list in `_async_setup_ble_entry` / its `async_unload_entry` branch. Do NOT add a runtime `isinstance(coordinator, GoveeBLECoordinator)` check inside `light.py` as PR #52 did — instead, either (a) use a distinct entity class `GoveeBLELightEntity` that's created only in the BLE platform setup path, or (b) parameterize the existing `GoveeLightEntity` with a connector interface. Option (a) is simpler.

### Theme 8: Repairs framework

`custom_components/govee/repairs.py` creates auth/rate_limit/MQTT issues — all cloud-specific. BLE entries must not trigger these. The fix is to branch in repairs creation on entry type (`if entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_BLE: return`), or only call the repairs API from cloud code paths. Low-risk change because BLE coordinator is separate.

### Theme 9: Migration from Beshelmek/govee_ble_lights

Beshelmek's entity unique_id format: `mac.replace(":","")` — e.g. `AABBCCDDEEFF`. Source: [light.py:244-246](https://github.com/Beshelmek/govee_ble_lights/blob/master/custom_components/govee-ble-lights/light.py#L244).

**Migration strategy**: match that exact format. Our BLE light entity unique_id:
```python
self._attr_unique_id = device.address.replace(":", "").lower()
```
A user removing Beshelmek's component and installing ours will see the BLE entity appear with the same `unique_id` in the entity registry — HA will reuse the old `entity_id` and `area_id`, preserving automations.

Caveats:
- Beshelmek stored config entry data differently (`CONF_MODEL` dropdown). Config entries don't migrate — the user re-adds the device via the HA Bluetooth discovery prompt. That's one-time friction.
- Entity names may differ (Beshelmek hardcodes `"GOVEE Light"`; we can use the advertised name). Users can rename to match the old name if they want identical display.

Document this in the release notes.

---

## Compatibility Analysis

### Stack Compatibility

| Aspect | Status | Notes |
|--------|--------|-------|
| HA core version | 2024.5+ | `entry.runtime_data` is needed for clean ownership |
| `bleak-retry-connector>=3.0.0` | Compatible | Core HA already bundles it transitively; adding as explicit requirement is fine |
| Python 3.12 | Compatible | Used throughout |
| Existing `aiomqtt`/`aiohttp-retry`/`cryptography` | Compatible | No conflict |
| Bluetooth proxies (ESPHome) | Compatible | `connectable: true` routes via connectable proxies |
| Existing cloud entries | Compatible | Discriminator defaults to cloud; zero-risk for users without BLE devices |
| `homeassistant.helpers.device_registry.CONNECTION_BLUETOOTH` | Compatible | Available since 2022.x |

### Integration Complexity

- **Effort estimate**: Medium-High (1–2 weeks of focused work)
- **Files affected**:
  - New: `custom_components/govee/api/ble.py` (~250 lines — inline protocol from Beshelmek patterns)
  - New: `custom_components/govee/coordinator_ble.py` (~150 lines — `DataUpdateCoordinator` subclass)
  - New: `custom_components/govee/platforms/ble_light.py` (~150 lines — BLE entity)
  - Modified: `custom_components/govee/__init__.py` (+ ~80 lines — setup branching, BLE-only unload path)
  - Modified: `custom_components/govee/config_flow.py` (+ ~100 lines — two new steps, discriminator)
  - Modified: `custom_components/govee/const.py` (+ ~10 lines — `CONF_ENTRY_TYPE`, `ENTRY_TYPE_CLOUD`, `ENTRY_TYPE_BLE`, `CONF_BLE_SEGMENTED`)
  - Modified: `custom_components/govee/manifest.json` (bluetooth matchers, dependency, requirement)
  - Modified: `custom_components/govee/entity.py` (+ ~20 lines — add `connections={(CONNECTION_BLUETOOTH, mac)}` when cloud device has BLE MAC)
  - Modified: `custom_components/govee/repairs.py` (+ ~5 lines — branch on entry type)
  - Modified: `custom_components/govee/strings.json` + `translations/en.json` (+ ~20 lines — new step labels)
  - Modified: `custom_components/govee/light.py` (no changes — BLE uses its own platform file)
- **New tests**:
  - `tests/test_api_ble.py` (protocol encoding — frame, checksum, commands — fully unit-testable, no BLE mocks needed)
  - `tests/test_coordinator_ble.py` (fake BLE client, verify coordinator poll + state updates)
  - `tests/test_config_flow.py` (add BLE discovery + confirm step tests)
  - `tests/test_ble_light.py` (entity command path with fake coordinator)
- **Breaking changes**: None. Pure addition. Existing cloud users see no behavior change.
- **Migration path**: Beshelmek users can swap cleanly by matching unique_id format (no code required, just documentation).

---

## Recommendation

### Decision

**Build BLE direct support as a second entry type within the existing `govee` integration**, following the architecture below. Take the correct protocol pieces from PR #52 (framing, checksum, UUIDs, command bytes), fix the three protocol divergences vs Beshelmek, and graft them onto a HA-idiomatic integration scaffold modeled after `led_ble`.

### Rationale

- **Single domain avoids HACS duplication**: users add "Govee" once, BLE devices auto-discover. A second domain (`govee_ble`) would force users to install two HACS integrations and duplicate issue-tracker surface.
- **HA supports multi-entry domains natively**: branching in `async_setup_entry` on a discriminator is the documented pattern.
- **Beshelmek's reference validates the protocol**: 76 SKU profiles, 122 stars, working in the wild — we're not inventing; we're adapting.
- **led_ble gives us the architectural template**: DataUpdateCoordinator + device library + passive bluetooth callback is the HA core pattern for write-heavy BLE lights.
- **Zero regression risk for cloud-only users**: the discriminator defaults to cloud, new `bluetooth:` manifest matchers don't activate on systems without Govee BLE devices.
- **Migration from Beshelmek is free**: matching their unique_id format means automations survive the swap.

### Comparison Matrix — architectural approaches

| Criteria | Single domain + 2 entry types (RECOMMENDED) | Second `govee_ble` domain | Single entry + runtime isinstance (PR #52) |
|---|---|---|---|
| User setup surface | 1 integration | 2 integrations | 1 integration |
| Device registry merge | Via `connections` tuple | Via `connections` tuple | N/A (same entry) |
| Avoids cloud/BLE code leakage | ✓ (separate entry setup paths) | ✓ | ✗ (isinstance checks in light.py) |
| `iot_class` correctness | ✓ (stays cloud_push) | ✓ (new domain is local_polling) | ✗ (PR #52 flipped it wrong) |
| Cloud-only platforms stay off BLE entries | ✓ (_BLE_PLATFORMS list) | ✓ (different domain) | ✗ (PR #52 early-returns in light.py) |
| Test isolation | Clean | Clean | Muddled |
| HACS release model | 1 repo | 2 repos | 1 repo |
| Code reuse (constants, entity base) | ✓ | ✗ (duplicate) | ✓ |
| **Overall** | **Recommended** | Acceptable but heavier | Rejected |

---

## Implementation Sketch

### Phase 1: Protocol module (`api/ble.py`)

Port PR #52's `ble_direct.py` into `api/ble.py` with the three protocol fixes applied. Introduce a `GoveeBLEDevice` class that owns a `BleakClient` via `bleak-retry-connector` and exposes the high-level API led_ble-style.

```python
# custom_components/govee/api/ble.py
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum

import bleak_retry_connector
from bleak import BleakClient
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice

_LOGGER = logging.getLogger(__name__)

WRITE_CHARACTERISTIC_UUID = "00010203-0405-0607-0809-0a0b0c0d2b11"
READ_CHARACTERISTIC_UUID = "00010203-0405-0607-0809-0a0b0c0d2b10"
BLE_DISCOVERY_NAMES: tuple[str, ...] = ("Govee_", "ihoment_", "GBK_")

# SKUs that use the segmented color encoding (lifted from Beshelmek/govee_ble_lights)
SEGMENTED_MODELS: frozenset[str] = frozenset({"H6053", "H6072", "H6102", "H6199"})


class LedPacketHead(IntEnum):
    COMMAND = 0x33
    REQUEST = 0xAA


class LedPacketCmd(IntEnum):
    POWER = 0x01
    BRIGHTNESS = 0x04
    COLOR = 0x05


class LedColorMode(IntEnum):
    SINGLE = 0x02       # Beshelmek "MANUAL"
    SCENES = 0x05
    MICROPHONE = 0x06
    SEGMENTS = 0x15


def _build_frame(cmd: LedPacketCmd, payload: bytes | list[int]) -> bytes:
    """Build a 20-byte command frame with XOR checksum."""
    data = bytes([LedPacketHead.COMMAND, cmd & 0xFF]) + bytes(payload)
    data += bytes([0] * (19 - len(data)))
    checksum = 0
    for b in data:
        checksum ^= b
    return data + bytes([checksum & 0xFF])


@dataclass
class GoveeBLEState:
    power: bool | None = None
    brightness: int | None = None  # 0-255 native (no rescale)
    rgb: tuple[int, int, int] | None = None


class GoveeBLEDevice:
    """Owns the BleakClient for a single Govee BLE light.

    Lifecycle: instantiate with a BLEDevice; call `set_ble_device_and_advertisement_data`
    from PASSIVE bluetooth callbacks to keep the reference fresh; call command methods to
    write state; call `update()` from the coordinator poll; call `stop()` on unload.
    """

    def __init__(self, ble_device: BLEDevice, segmented: bool = False) -> None:
        self._ble_device = ble_device
        self._segmented = segmented
        self._client: BleakClient | None = None
        self._state = GoveeBLEState()
        self._lock = asyncio.Lock()
        self._callbacks: list[Callable[[GoveeBLEState], None]] = []

    @property
    def address(self) -> str:
        return self._ble_device.address

    @property
    def name(self) -> str:
        return self._ble_device.name or "Govee BLE Light"

    @property
    def state(self) -> GoveeBLEState:
        return self._state

    def register_callback(self, cb: Callable[[GoveeBLEState], None]) -> Callable[[], None]:
        self._callbacks.append(cb)
        def _unsub() -> None:
            self._callbacks.remove(cb)
        return _unsub

    def set_ble_device_and_advertisement_data(self, ble_device: BLEDevice, adv) -> None:
        """Called by PASSIVE bluetooth callback — refresh BLEDevice ref."""
        self._ble_device = ble_device

    async def _ensure_connected(self) -> BleakClient:
        if self._client is not None and self._client.is_connected:
            return self._client
        self._client = await bleak_retry_connector.establish_connection(
            BleakClient, self._ble_device, self.address,
            disconnected_callback=self._on_disconnected,
        )
        return self._client

    def _on_disconnected(self, _client: BleakClient) -> None:
        self._client = None

    async def _write(self, frame: bytes) -> None:
        async with self._lock:
            client = await self._ensure_connected()
            await client.write_gatt_char(WRITE_CHARACTERISTIC_UUID, frame, response=False)

    async def turn_on(self) -> None:
        await self._write(_build_frame(LedPacketCmd.POWER, [0x01]))
        self._state.power = True
        self._emit()

    async def turn_off(self) -> None:
        await self._write(_build_frame(LedPacketCmd.POWER, [0x00]))
        self._state.power = False
        self._emit()

    async def set_brightness(self, brightness: int) -> None:
        """Set brightness 0-255 (no rescale — Beshelmek-compatible for all SKUs)."""
        b = max(0, min(255, int(brightness)))
        await self._write(_build_frame(LedPacketCmd.BRIGHTNESS, [b]))
        self._state.brightness = b
        self._emit()

    async def set_rgb(self, r: int, g: int, b: int) -> None:
        if self._segmented:
            payload = [LedColorMode.SEGMENTS, 0x01, r, g, b,
                       0x00, 0x00, 0x00, 0x00, 0x00, 0xFF, 0x7F]  # Beshelmek tail
        else:
            payload = [LedColorMode.SINGLE, r, g, b]
        await self._write(_build_frame(LedPacketCmd.COLOR, payload))
        self._state.rgb = (r, g, b)
        self._emit()

    async def update(self) -> GoveeBLEState:
        """Coordinator poll — touch the device to keep the connection warm.

        Govee BLE lights don't reliably respond to state-request packets, so we
        rely on optimistic state from command methods rather than GATT reads.
        """
        return self._state

    async def stop(self) -> None:
        """Cleanly disconnect — called from async_unload_entry."""
        async with self._lock:
            if self._client is not None and self._client.is_connected:
                await self._client.disconnect()
            self._client = None

    def _emit(self) -> None:
        for cb in self._callbacks:
            cb(self._state)
```

### Phase 2: Coordinator (`coordinator_ble.py`)

```python
# custom_components/govee/coordinator_ble.py
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api.ble import GoveeBLEDevice, GoveeBLEState
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
UPDATE_INTERVAL = timedelta(seconds=60)


class GoveeBLECoordinator(DataUpdateCoordinator[GoveeBLEState]):
    """Per-device BLE coordinator.

    Unlike the cloud coordinator (which manages N devices), each BLE entry
    has its own coordinator because each BLE device needs its own BleakClient.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, device: GoveeBLEDevice) -> None:
        self.device = device
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_ble_{device.address}",
            update_interval=UPDATE_INTERVAL,
        )

    async def _async_update_data(self) -> GoveeBLEState:
        return await self.device.update()
```

### Phase 3: Setup branching (`__init__.py`)

```python
# custom_components/govee/__init__.py — additions
from homeassistant.components import bluetooth
from homeassistant.exceptions import ConfigEntryNotReady

from .const import CONF_ENTRY_TYPE, ENTRY_TYPE_BLE, CONF_BLE_SEGMENTED
from .coordinator_ble import GoveeBLECoordinator

_BLE_PLATFORMS: list[Platform] = [Platform.LIGHT]


async def async_setup_entry(hass: HomeAssistant, entry: GoveeConfigEntry) -> bool:
    # Route BLE entries to dedicated setup path.
    if entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_BLE:
        return await _async_setup_ble_entry(hass, entry)
    # Existing cloud setup unchanged.
    return await _async_setup_cloud_entry(hass, entry)


async def _async_setup_ble_entry(hass: HomeAssistant, entry: GoveeConfigEntry) -> bool:
    from .api.ble import GoveeBLEDevice, SEGMENTED_MODELS

    address = entry.data[CONF_ADDRESS]
    ble_device = bluetooth.async_ble_device_from_address(hass, address, connectable=True)
    if ble_device is None:
        raise ConfigEntryNotReady(f"Govee BLE device {address} not found")

    segmented = entry.data.get(CONF_BLE_SEGMENTED, False)
    device = GoveeBLEDevice(ble_device, segmented=segmented)
    coordinator = GoveeBLECoordinator(hass, entry, device)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    # Wire PASSIVE advertisement callback to refresh BLEDevice reference.
    @callback
    def _async_update_ble(service_info, change) -> None:
        device.set_ble_device_and_advertisement_data(service_info.device, service_info.advertisement)

    entry.async_on_unload(bluetooth.async_register_callback(
        hass, _async_update_ble,
        {"address": address, "connectable": True},
        bluetooth.BluetoothScanningMode.PASSIVE,
    ))

    await hass.config_entries.async_forward_entry_setups(entry, _BLE_PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: GoveeConfigEntry) -> bool:
    if entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_BLE:
        unload_ok = await hass.config_entries.async_unload_platforms(entry, _BLE_PLATFORMS)
        if unload_ok:
            await entry.runtime_data.device.stop()
        return unload_ok
    # existing cloud unload unchanged
    return await _async_unload_cloud_entry(hass, entry)
```

### Phase 4: Config flow (`config_flow.py`)

```python
# custom_components/govee/config_flow.py — additions
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.const import CONF_ADDRESS, CONF_NAME

from .api.ble import SEGMENTED_MODELS
from .const import CONF_BLE_SEGMENTED, CONF_ENTRY_TYPE, ENTRY_TYPE_BLE


class GoveeConfigFlow(ConfigFlow, domain=DOMAIN):
    def __init__(self) -> None:
        # ... existing init ...
        self._ble_info: BluetoothServiceInfoBleak | None = None

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak,
    ) -> ConfigFlowResult:
        """Handle auto-discovery of a Govee BLE light."""
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        self._ble_info = discovery_info
        self.context["title_placeholders"] = {"name": discovery_info.name}
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Confirm the discovered BLE light and ask about segmented mode."""
        assert self._ble_info is not None
        info = self._ble_info

        # Default segmented based on SKU guess from advertising name suffix.
        sku_guess = _sku_from_ble_name(info.name) or ""
        default_segmented = sku_guess in SEGMENTED_MODELS

        if user_input is not None:
            return self.async_create_entry(
                title=info.name,
                data={
                    CONF_ENTRY_TYPE: ENTRY_TYPE_BLE,
                    CONF_ADDRESS: info.address.upper(),
                    CONF_NAME: info.name,
                    CONF_BLE_SEGMENTED: user_input[CONF_BLE_SEGMENTED],
                },
            )

        return self.async_show_form(
            step_id="bluetooth_confirm",
            data_schema=vol.Schema({
                vol.Required(CONF_BLE_SEGMENTED, default=default_segmented): bool,
            }),
            description_placeholders={"name": info.name, "address": info.address},
        )


def _sku_from_ble_name(name: str | None) -> str | None:
    """Extract SKU from advertising name like 'Govee_H6072_ABCD'."""
    if not name:
        return None
    parts = name.split("_")
    for part in parts:
        if part.startswith("H") and len(part) == 5 and part[1:].isalnum():
            return part
    return None
```

### Phase 5: BLE light platform (`platforms/ble_light.py`)

```python
# custom_components/govee/platforms/ble_light.py
from __future__ import annotations

from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS, ATTR_RGB_COLOR, ColorMode, LightEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ..const import DOMAIN
from ..coordinator_ble import GoveeBLECoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Govee BLE light from a config entry."""
    coordinator: GoveeBLECoordinator = entry.runtime_data
    async_add_entities([GoveeBLELightEntity(coordinator)])


class GoveeBLELightEntity(CoordinatorEntity[GoveeBLECoordinator], LightEntity):
    """BLE light entity.

    Unique ID matches Beshelmek/govee_ble_lights format (MAC without colons,
    lowercase) so users migrating from that component keep their automations.
    """

    _attr_has_entity_name = True
    _attr_name = None
    _attr_supported_color_modes = {ColorMode.RGB}
    _attr_color_mode = ColorMode.RGB

    def __init__(self, coordinator: GoveeBLECoordinator) -> None:
        super().__init__(coordinator)
        device = coordinator.device
        self._attr_unique_id = device.address.replace(":", "").lower()
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"ble_{device.address.upper()}")},
            connections={(CONNECTION_BLUETOOTH, device.address.upper())},
            name=device.name,
            manufacturer="Govee",
            model=device.name,
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.device.state.power

    @property
    def brightness(self) -> int | None:
        return self.coordinator.device.state.brightness

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        return self.coordinator.device.state.rgb

    async def async_turn_on(self, **kwargs: Any) -> None:
        device = self.coordinator.device
        await device.turn_on()
        if ATTR_BRIGHTNESS in kwargs:
            await device.set_brightness(kwargs[ATTR_BRIGHTNESS])
        if ATTR_RGB_COLOR in kwargs:
            r, g, b = kwargs[ATTR_RGB_COLOR]
            await device.set_rgb(r, g, b)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.device.turn_off()
        self.async_write_ha_state()
```

### Phase 6: Constants (`const.py`)

```python
# custom_components/govee/const.py — additions
CONF_ENTRY_TYPE: Final = "entry_type"
ENTRY_TYPE_CLOUD: Final = "cloud"
ENTRY_TYPE_BLE: Final = "ble"
CONF_BLE_SEGMENTED: Final = "ble_segmented"
```

### Phase 7: Cloud entity device merge (`entity.py`)

```python
# custom_components/govee/entity.py — modification to GoveeEntity.device_info
@property
def device_info(self) -> DeviceInfo:
    info = DeviceInfo(
        identifiers={(DOMAIN, self._device.device_id)},
        manufacturer="Govee",
        model=self._device.sku,
        name=self._device.name,
    )
    # If cloud device reports a BLE MAC, add the connections tuple so HA
    # merges the cloud device with a separate BLE entry for the same hardware.
    ble_mac = getattr(self._device, "ble_address", None)
    if ble_mac:
        info["connections"] = {(CONNECTION_BLUETOOTH, ble_mac.upper())}
    return info
```
(Requires propagating `bleAddress` from the cloud `/user/devices` response into `GoveeDevice` — one new field in `models/device.py`. Verify the API returns it.)

### Phase 8: Manifest

```json
{
  "domain": "govee",
  "name": "Govee Cloud Integration",
  "codeowners": ["@lasswellt"],
  "bluetooth": [
    {"local_name": "Govee_*", "connectable": true},
    {"local_name": "ihoment_*", "connectable": true},
    {"local_name": "GBK_*", "connectable": true}
  ],
  "config_flow": true,
  "dependencies": ["bluetooth_adapters"],
  "documentation": "https://github.com/lasswellt/govee-homeassistant/blob/master/README.md",
  "integration_type": "hub",
  "iot_class": "cloud_push",
  "issue_tracker": "https://github.com/lasswellt/govee-homeassistant/issues",
  "loggers": ["custom_components.govee", "aiohttp", "aiomqtt", "bleak", "bleak_retry_connector"],
  "requirements": [
    "aiohttp-retry>=2.8.3",
    "aiomqtt>=2.0.0",
    "bleak-retry-connector>=3.0.0",
    "cryptography>=41.0.0"
  ]
}
```

### Phase 9: Tests

```python
# tests/test_api_ble.py — protocol unit tests (no BLE mocks needed)
from custom_components.govee.api.ble import _build_frame, LedPacketCmd, LedColorMode

def test_power_on_frame():
    frame = _build_frame(LedPacketCmd.POWER, [0x01])
    assert len(frame) == 20
    assert frame[0] == 0x33
    assert frame[1] == 0x01
    assert frame[2] == 0x01
    # checksum: XOR of bytes 0..18
    expected_checksum = 0
    for b in frame[:19]:
        expected_checksum ^= b
    assert frame[19] == expected_checksum

def test_rgb_single_frame():
    frame = _build_frame(LedPacketCmd.COLOR, [LedColorMode.SINGLE, 255, 128, 64])
    assert frame[0] == 0x33
    assert frame[1] == 0x05
    assert frame[2] == 0x02  # SINGLE
    assert frame[3:6] == bytes([255, 128, 64])

def test_rgb_segmented_frame_matches_beshelmek():
    # Segmented color for H6072 — must end with 0xFF, 0x7F per Beshelmek reference
    frame = _build_frame(
        LedPacketCmd.COLOR,
        [LedColorMode.SEGMENTS, 0x01, 255, 0, 0, 0, 0, 0, 0, 0, 0xFF, 0x7F],
    )
    assert frame[2] == 0x15  # SEGMENTS
    assert frame[3] == 0x01
    assert frame[4:7] == bytes([255, 0, 0])
    assert frame[13] == 0xFF
    assert frame[14] == 0x7F
```

```python
# tests/test_coordinator_ble.py
import pytest
from unittest.mock import AsyncMock, MagicMock

from custom_components.govee.api.ble import GoveeBLEDevice, GoveeBLEState
from custom_components.govee.coordinator_ble import GoveeBLECoordinator

@pytest.mark.asyncio
async def test_coordinator_poll_returns_state(hass, mock_config_entry_ble):
    device = MagicMock(spec=GoveeBLEDevice)
    device.update = AsyncMock(return_value=GoveeBLEState(power=True, brightness=200))
    device.address = "AA:BB:CC:DD:EE:FF"
    coordinator = GoveeBLECoordinator(hass, mock_config_entry_ble, device)
    state = await coordinator._async_update_data()
    assert state.power is True
    assert state.brightness == 200
```

```python
# tests/test_config_flow.py — add BLE discovery tests
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak

async def test_bluetooth_discovery_creates_entry(hass):
    info = BluetoothServiceInfoBleak(
        name="Govee_H6072_ABCD", address="AA:BB:CC:DD:EE:FF", rssi=-50,
        manufacturer_data={}, service_data={}, service_uuids=[],
        source="local", advertisement=None, device=MagicMock(), time=0.0, connectable=True,
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "bluetooth"}, data=info,
    )
    assert result["type"] == "form"
    assert result["step_id"] == "bluetooth_confirm"

    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_BLE_SEGMENTED: True},
    )
    assert result2["type"] == "create_entry"
    assert result2["data"][CONF_ENTRY_TYPE] == ENTRY_TYPE_BLE
    assert result2["data"][CONF_ADDRESS] == "AA:BB:CC:DD:EE:FF"
```

### Phase 10: Translations

Add `config.step.bluetooth_confirm` to `strings.json` and `translations/en.json`:
```json
"bluetooth_confirm": {
  "title": "Confirm Govee BLE light",
  "description": "Add {name} ({address}) as a directly-controlled BLE light?",
  "data": {
    "ble_segmented": "Segmented color mode (enable for H6053, H6072, H6102, H6199 and newer RGBIC strips)"
  }
}
```

### Suggested PR splitting

Split implementation into 4 PRs to keep review surface small:
1. **BLE protocol + unit tests** — `api/ble.py` + `tests/test_api_ble.py`. No HA wiring yet. (~350 LOC)
2. **BLE coordinator + entry branching** — `coordinator_ble.py`, `__init__.py` discriminator, `const.py` constants, `_BLE_PLATFORMS`. (~250 LOC)
3. **BLE light platform + config flow** — `platforms/ble_light.py`, config_flow additions, manifest.json, strings.json, tests. (~350 LOC)
4. **Device registry merge + repairs branching** — `entity.py` `connections` tuple, `repairs.py` entry-type branch, `models/device.py` `ble_address` field. (~100 LOC)

Each PR ships with its own tests and stays at or below the 95% coverage target.

---

## Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Beshelmek's tail byte `0x7F` or our brightness scaling turns out wrong for a specific SKU we don't have | Medium | Medium | Ship with an "advanced" option to toggle tail byte / brightness rescale per-entry; collect user feedback before hardcoding variants. |
| Govee BLE devices don't respond to state-request packets → `update()` is a no-op, optimistic state drifts if device is controlled out-of-band | Medium | Low | Document that BLE mode is command-only (no external state sync); recommend the cloud entry for users who need bidirectional sync. |
| `CONNECTION_BLUETOOTH` merge doesn't trigger because cloud device list doesn't include a `bleAddress` field | Medium | Low | Skip the merge gracefully if MAC unknown; cloud and BLE entries will appear as two devices in the registry, which is not broken, just not merged. File a follow-up to probe the real API response. |
| Adding `bluetooth_adapters` to dependencies fails on HA installs without Bluetooth support | Low | Medium | `bluetooth_adapters` is a core HA integration available in all installs; it's safe to depend on unconditionally. |
| Users on HA < 2024.5 that don't support `entry.runtime_data` | Low | Low | Pin `manifest.json` min HA version via documentation; the cloud side already uses `runtime_data`. |
| bleak-retry-connector version drift breaks our pinned API | Low | Medium | Pin `bleak-retry-connector>=3.0.0,<5.0.0` once tested; track breaking changes in CI. |
| `LEGACY=0x0D` removed from PR #52 actually IS needed for some old bulbs (H611A-class) | Low | Low | If an issue is reported, re-add with a user-confirmed capture. Start without it. |
| ESPHome Bluetooth proxies don't relay commands reliably for Govee lights | Medium | Medium | Document the `connectable: true` requirement; users with non-connectable proxies will need a connectable proxy or native adapter. |
| Multiple BLE entries create adapter contention on busy systems | Low | Medium | 60s update interval is conservative; commands are serialized per-device via `asyncio.Lock`. |

### Open Questions

- Does the cloud `/user/devices` API response include a BLE MAC field for BLE-capable SKUs? (Need to check an actual response — grep for `bleAddress` in `docs/device-profiles/` or capture with debug logs.) If not, the cloud+BLE device-registry merge is degraded to "two separate devices" until we can infer MAC some other way.
- Which specific SKUs need segmented mode beyond Beshelmek's 4-SKU whitelist (`H6053, H6072, H6102, H6199`)? PR #52 mentions newer RGBIC strips; confirm by reading Beshelmek's latest release notes or user issues.
- Does the brightness rescale (PR #52) fix a real bug on any SKU, or is it a speculative PR #52 addition? If unknown: ship with Beshelmek's approach (0–255 native), watch for issues, only add per-SKU rescaling if reported.
- Is there a "probe the device at setup to determine variant" packet? If yes, we could auto-detect segmented instead of asking the user.
- What does a Beshelmek entry's `config_entry.data` look like exactly? We may want to auto-import on first-run if we detect it.

---

## References

1. **Beshelmek/govee_ble_lights** — https://github.com/Beshelmek/govee_ble_lights — the authoritative reference HACS integration; 122 stars, 76 SKU profiles, last pushed 2025-07-09. Source of protocol truth for frame format, command bytes, segmented-model whitelist, and unique_id format.
2. **Beshelmek light.py** — https://github.com/Beshelmek/govee_ble_lights/blob/master/custom_components/govee-ble-lights/light.py — lines 33–54 (UUIDs, command enums, segmented models), 244–246 (unique_id format), 257–309 (turn_on/turn_off pattern), 319–341 (frame builder).
3. **Beshelmek govee_utils.py** — https://github.com/Beshelmek/govee_ble_lights/blob/master/custom_components/govee-ble-lights/govee_utils.py — lines 61–65 (XOR checksum), 1–59 (multi-packet scene payload helper, out of scope for our V1).
4. **Beshelmek manifest.json** — https://github.com/Beshelmek/govee_ble_lights/blob/master/custom_components/govee-ble-lights/manifest.json — manifest format for bluetooth matchers + requirements (note: Beshelmek uses `connectable: false` and `local_polling`; we use `true` and keep `cloud_push`).
5. **HA core `led_ble` integration** — https://github.com/home-assistant/core/tree/dev/homeassistant/components/led_ble — the HA-core template for a write-heavy BLE light integration: manifest matchers, config flow, DataUpdateCoordinator pattern, PASSIVE callback wiring, entry lifecycle with `entry.runtime_data`.
6. **HA core `yalexs_ble` integration** — https://github.com/home-assistant/core/blob/dev/homeassistant/components/yalexs_ble/entity.py — canonical DeviceInfo pattern with `connections={(CONNECTION_BLUETOOTH, mac)}` for device-registry merging across two transports (august cloud + yalexs_ble local).
7. **HA dev docs — Bluetooth API** — https://developers.home-assistant.io/docs/core/bluetooth/api/ — manifest matcher keys, `async_ble_device_from_address`, `async_register_callback`, `async_track_unavailable`.
8. **HA dev docs — Fetching Bluetooth data** — https://developers.home-assistant.io/docs/core/bluetooth/bluetooth_fetching_data/ — decision tree for coordinator choice (plain DataUpdateCoordinator for write-heavy devices).
9. **HA dev docs — Config flow handler** — https://developers.home-assistant.io/docs/config_entries_config_flow_handler/ — multi-entry-type pattern, unique_id semantics, `async_step_bluetooth`.
10. **HA dev docs — Device registry** — https://developers.home-assistant.io/docs/device_registry_index/ — merging rules for `identifiers` + `connections`.
11. **bleak-retry-connector** — https://github.com/Bluetooth-Devices/bleak-retry-connector — `establish_connection()` API for robust GATT connection management; used by all HA core BLE integrations.
12. **`govee-ble` on PyPI** — https://pypi.org/project/govee-ble/ — maintained by J. Nick Koston / `bdraco` at `bluetooth-devices/govee-ble`. **Passive advertisement parser for Govee sensors, not a writable light client** — we verified the `src/govee_ble/parser.py` source and confirmed it's a broadcast parser derived from bleparser. Not suitable as a dependency for this project.
13. **PR #52 source** — https://github.com/lasswellt/govee-homeassistant/pull/52/files — the PR we're replacing: `api/ble_direct.py` protocol layer (correct modulo the three divergences noted in Theme 1), `coordinator_ble.py` (wrong pattern), `platforms/ble_light.py` (leaks isinstance into cloud light.py).
14. **Prior research: PR #52 review** — `docs/_research/` — earlier review comments from our PR #52 Request Changes review; this document supersedes the review's implementation suggestions.
15. **Codebase: `custom_components/govee/__init__.py`** — current `async_setup_entry` structure and `PLATFORMS` list.
16. **Codebase: `custom_components/govee/entity.py`** — `GoveeEntity` base class; target for the `connections` tuple addition.
17. **Codebase: `custom_components/govee/config_flow.py`** — current config flow, target for `async_step_bluetooth` addition.
