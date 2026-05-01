# Research: HA BLE Integration Patterns (applied to hacs-govee)

**Date**: 2026-04-08
**Type**: Feature Investigation (pattern catalog)
**Status**: Complete
**Stack**: Home Assistant custom component, Python 3.12, `bleak` + `bleak_retry_connector`
**Companion doc**: [`2026-04-08_ble-direct-support.md`](2026-04-08_ble-direct-support.md) — architectural decision; this doc drills into implementation patterns

---

## Summary

Surveyed six HA core BLE integrations (`led_ble`, `yalexs_ble`, `snooz`, `keymitt_ble`, `bthome`, `switchbot`) and the HA Bluetooth helper APIs + `bleak_retry_connector` to extract reusable patterns. **`led_ble` is the closest template for hacs-govee** and should be cloned almost verbatim — plain `DataUpdateCoordinator[None]` for polling fallback, a PASSIVE `async_register_callback` that only feeds fresh `BLEDevice` refs into the device library via `set_ble_device_and_advertisement_data`, device-library-level `register_callback` that the entity subscribes to in `async_added_to_hass` for push bypass, and a startup `asyncio.Event` gated by `DEVICE_TIMEOUT → ConfigEntryNotReady`. Three critical patterns the previous BLE-support research didn't call out must be added: (1) **`close_stale_connections_by_address(address)` before every setup** — the single biggest gotcha for new BLE integrations; a dangling GATT handle from a crashed HA blocks reconnect until reboot; (2) **`ble_device_callback` on `establish_connection`** wired to `async_ble_device_from_address` so proxy handoff works mid-retry; (3) **post-discovery advertisements do NOT auto-deliver** — after the entry is set up, you must call `async_register_callback` with an explicit address matcher to receive ongoing ADVs. Additionally, the `mode` argument to `async_register_callback` is currently a no-op (HA scanner always runs ACTIVE), and `bleak_retry_connector`'s per-attempt timeouts (`BLEAK_TIMEOUT=20s`, `BLEAK_SAFETY_TIMEOUT=60s`, `MAX_CONNECT_ATTEMPTS=4`) are the envelope our code must respect.

---

## Research Questions

### Q1: Which 6-8 HA core BLE integrations are the best references for write-heavy commanded devices?
**Answer**: In descending relevance — `led_ble` (primary template: RGB light), `yalexs_ble` (dual-transport cloud+BLE coexistence), `snooz` (command-driven, no coordinator), `switchbot` (hybrid advert+GATT, good for pattern comparison), `keymitt_ble` (BLE button + `PassiveBluetoothDataUpdateCoordinator` contrast), and `bthome` (pure advertisement sensors — contrast case, shows what NOT to do for lights).

### Q2: What patterns are shared across them?
**Answer**: Six canonical patterns shared by ≥5/6 integrations: (1) `unique_id = discovery_info.address` in `async_step_bluetooth` followed by `_abort_if_unique_id_configured()`; (2) `dependencies: ["bluetooth_adapters"]` in manifest; (3) `raise ConfigEntryNotReady` when `async_ble_device_from_address` returns `None`; (4) `entry.runtime_data = <typed dataclass>` (snooz is the lone holdout still on `hass.data[DOMAIN]`); (5) `type FooConfigEntry = ConfigEntry[FooData]` type alias; (6) `DeviceInfo(connections={(dr.CONNECTION_BLUETOOTH, address)})`. Together these form the "canonical shape" any new BLE integration should start from.

### Q3: How do they handle common failure modes?
**Answer**: (a) **Stale GATT handles** — `close_stale_connections_by_address(address)` called before setup (yalexs_ble, switchbot) to free dangling links from a crashed HA; this is the single highest-impact defensive call. (b) **Proxy handoff during retry** — `establish_connection(..., ble_device_callback=lambda: bluetooth.async_ble_device_from_address(hass, address, connectable=True))` so each retry fetches a fresh device (switchbot). (c) **Adapter disappearance** — `bluetooth.async_track_unavailable` with a callback that calls `device_lib.reset_advertisement_state()` and sets entity available=False (yalexs_ble). (d) **Address rotation on macOS** — switchbot refreshes `self.ble_device = service_info.device` on every advert because CoreBluetooth UUIDs can rotate. (e) **Startup race** — `asyncio.Event()` + `async with asyncio.timeout(DEVICE_TIMEOUT): await startup_event.wait()` + `ConfigEntryNotReady` (led_ble) ensures the entry only finishes setup after first observed state.

### Q4: Should we publish our protocol as a PyPI library?
**Answer**: **Not initially.** Every HA core BLE integration surveyed delegates GATT I/O to a dedicated PyPI library (`led-ble`, `yalexs-ble`, `pysnooz`, `PySwitchbot`, `PyMicroBot`, `bthome-ble`) — this is the canonical pattern, and an eventual PyPI lib (`govee-ble-lights`, say) is the right long-term shape if we ever upstream to HA core. But for an initial HACS-only release, an in-tree `api/ble.py` module with the same API surface as a PyPI lib (classes with `turn_on`/`set_rgb`/`register_callback`/`set_ble_device_and_advertisement_data`/`stop`) is the pragmatic path. Structure the module as if it were a library: if we later extract it, no refactor needed.

### Q5: What are the exact usage patterns for `bleak_retry_connector.establish_connection` and the HA Bluetooth helpers?
**Answer**: Full signatures and examples in [Finding Theme 4](#theme-4-api-reference-the-exact-code-we-write) below. Key points: `ble_device_callback` is mandatory for proxy handoff; `use_services_cache=True` is correct for Govee (services don't rotate); `max_attempts=4` is the default envelope (4 connect attempts, up to 9 transient-error retries); per-attempt cap is `BLEAK_TIMEOUT=20s` and the safety cap is `BLEAK_SAFETY_TIMEOUT=60s`. `BluetoothCallbackMatcher` takes `address`, `connectable`, `local_name`, `service_uuid`, `service_data_uuid`, `manufacturer_id`, `manufacturer_data_start` (ANDed within a matcher). `async_register_callback`'s `mode` argument is currently **a no-op** — HA always runs ACTIVE scanning regardless. `async_track_unavailable` fires once after ~195s of silence for connectable devices, ~900s for non-connectable; precision is coarse.

### Q6: What common pitfalls are documented in HA core PR history?
**Answer**: Five high-frequency pitfalls that show up across the surveyed integrations. (1) **Not calling `close_stale_connections_by_address` before setup** → reconnects fail after HA restart. (2) **Stashing a `BLEDevice` reference at setup time and never refreshing it** → proxy handoff breaks, connections fail on the wrong adapter. (3) **Running GATT writes without an `asyncio.Lock`** → concurrent writes corrupt command buffers. (4) **Expecting post-discovery ADVs to auto-deliver** → `async_register_callback` with an explicit address matcher is required; without it, the device library's advertisement cache goes stale immediately after setup. (5) **Missing `entry.async_on_unload` for the bluetooth callback** → phantom callbacks fire after entry unload, causing `TypeError` on torn-down state.

### Q7: How are HA BLE integrations tested without real hardware?
**Answer**: Three patterns. (a) **Direct unit tests of the protocol module** — frame encoding, checksum, packet parsing — no HA or BLE mocks needed, just asserts on byte arrays. `led_ble`, `bthome-ble`, and `govee-ble` (the sensor parser) all use this pattern and it's the most valuable test category. (b) **`tests.components.bluetooth.generate_advertisement_data` + `inject_bluetooth_service_info`** — HA core's fixtures for simulating advertisements through the bluetooth integration without a real adapter. (c) **Mock the device-library class** — pass a `MagicMock(spec=GoveeBLEDevice)` into the coordinator and assert the coordinator/entity wires it correctly. Combined, these give 95%+ coverage without any BLE hardware.

---

## Findings

### Theme 1: The canonical "led_ble shape"

Five of six surveyed integrations follow what I'll call the **canonical shape** — a standard set of code patterns that new BLE integrations should start from. `led_ble` is its clearest expression.

**Canonical `async_setup_entry` skeleton** (led_ble, lightly annotated):
```python
async def async_setup_entry(hass, entry):
    address = entry.unique_id
    assert address is not None

    # 1. Pre-flight: free any dangling connections from a prior crash.
    await close_stale_connections_by_address(address)

    # 2. Fetch the current BLEDevice snapshot.
    ble_device = bluetooth.async_ble_device_from_address(hass, address.upper(), True)
    if not ble_device:
        raise ConfigEntryNotReady(f"Could not find {address}")

    # 3. Instantiate the device library.
    led_ble = LEDBLE(ble_device)

    # 4. Wire PASSIVE advertisement subscription — refresh BLEDevice on every ADV.
    @callback
    def _async_update_ble(service_info, change):
        led_ble.set_ble_device_and_advertisement_data(
            service_info.device, service_info.advertisement,
        )
    entry.async_on_unload(bluetooth.async_register_callback(
        hass, _async_update_ble,
        BluetoothCallbackMatcher({ADDRESS: address}),
        bluetooth.BluetoothScanningMode.PASSIVE,   # no-op today, future-proof
    ))

    # 5. Startup gate — wait for first state or ConfigEntryNotReady.
    startup_event = asyncio.Event()
    cancel_first_update = led_ble.register_callback(lambda *_: startup_event.set())

    # 6. Coordinator for polling fallback.
    coordinator = LEDBLECoordinator(hass, entry, led_ble)
    try:
        await coordinator.async_config_entry_first_refresh()
        async with asyncio.timeout(DEVICE_TIMEOUT):
            await startup_event.wait()
    except (asyncio.TimeoutError, BLEAK_EXCEPTIONS) as ex:
        raise ConfigEntryNotReady(str(ex)) from ex
    finally:
        cancel_first_update()

    # 7. Stash in runtime_data.
    entry.runtime_data = LEDBLEData(entry.title, led_ble, coordinator)

    # 8. Shutdown hook — cleanly close BleakClient on HA stop.
    async def _async_stop(_event):
        await led_ble.stop()
    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_stop)
    )

    # 9. Forward to platforms.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True
```

This is the shape hacs-govee should follow for its BLE entries. Steps 1, 4 (the device-library callback pattern), 5 (startup gate), and 8 (shutdown hook) are all missing or broken in PR #52 and must be added.

**Source**: [led_ble/__init__.py](https://github.com/home-assistant/core/blob/dev/homeassistant/components/led_ble/__init__.py)

### Theme 2: Coordinator choices and what each means

Three distinct coordinator strategies are observable; each is appropriate for different device classes.

| Pattern | Used by | When |
|---|---|---|
| **Plain `DataUpdateCoordinator[None]` + polling fallback** | led_ble | Write-heavy commanded devices where GATT reads occasionally refresh state (lights, locks with poll paths). **This is what Govee needs.** |
| **No coordinator — device library IS the coordinator** | yalexs_ble (`PushLock`), snooz (`SnoozDevice`) | Push-only devices; the library manages its own connection lifecycle, entities subscribe to library-level callbacks directly. Works if the lib is mature. |
| **`ActiveBluetoothDataUpdateCoordinator[None]`** | switchbot | Hybrid advert+GATT devices where ADV payloads carry state AND GATT polls fetch extra details (`_needs_poll` callable gates poll). |
| **`PassiveBluetoothDataUpdateCoordinator` / `PassiveBluetoothProcessorCoordinator`** | keymitt_ble, bthome | Advertisement-driven sensors or devices where ADV payloads contain the full state; no GATT poll. |

**Govee pick**: plain `DataUpdateCoordinator[None]` with a 60s polling interval, matching led_ble. Rationale: Govee BLE lights don't put state in advertisements, they're write-heavy, and the poll is a belt-and-braces fallback for cases where adverts stop flowing.

**Source**: [led_ble/coordinator.py](https://github.com/home-assistant/core/blob/dev/homeassistant/components/led_ble/coordinator.py), [switchbot/coordinator.py](https://github.com/home-assistant/core/blob/dev/homeassistant/components/switchbot/coordinator.py)

### Theme 3: Dual-path updates (coordinator poll + device-level push)

led_ble does something subtle that the earlier research doc missed. It registers TWO update paths and wires the entity to both:

```python
# entity.py __init__
self._device = device
...

# async_added_to_hass
async def async_added_to_hass(self):
    await super().async_added_to_hass()
    self.async_on_remove(
        self._device.register_callback(self._handle_coordinator_update)
    )
```

The result:
- **Polling path** — `DataUpdateCoordinator._async_update_data` → `led_ble.update()` → GATT read → entity via `_handle_coordinator_update` (standard `CoordinatorEntity` path)
- **Push path** — device library receives an advertisement → fires its own `register_callback` listeners → entity's `_handle_coordinator_update` runs without a coordinator cycle

The entity doesn't care which path triggered it — the method is the same. This means **real-time advertisement state bypasses the coordinator poll entirely**, while polling remains the fallback for cases where adverts stopped flowing.

**Govee application**: we implement `GoveeBLEDevice.register_callback(cb)` in `api/ble.py`. The BLE light entity subscribes to it in `async_added_to_hass` alongside the coordinator. Even though Govee BLE lights may not reliably push state back over GATT, this pattern gives us the hook if we later add optimistic state updates triggered by advertisement changes (e.g., battery, RSSI).

**Source**: [led_ble/entity.py](https://github.com/home-assistant/core/blob/dev/homeassistant/components/led_ble/entity.py) `async_added_to_hass`, [led_ble/light.py](https://github.com/home-assistant/core/blob/dev/homeassistant/components/led_ble/light.py)

### Theme 4: API reference — the exact code we write

Documenting the exact signatures and usage patterns for the APIs we'll touch. Three of these (`close_stale_connections_by_address`, `ble_device_callback`, `async_rediscover_address`) were not in the previous research doc.

#### `async_ble_device_from_address(hass, address, connectable=True) -> BLEDevice | None`

- Snapshot from the manager's history dict. Not live — re-fetch right before every connect because `BLEDevice.details` (BlueZ D-Bus path) goes stale.
- `connectable=True` → only returns a BLEDevice reachable for a real connection; `False` includes advertisement-only scanners.
- Returns `None` until the first ADV after HA start.

#### `close_stale_connections_by_address(address)` — **critical defensive call**

```python
from bleak_retry_connector import close_stale_connections_by_address

async def async_setup_entry(hass, entry):
    address = entry.unique_id
    await close_stale_connections_by_address(address)  # <-- FIRST
    ble_device = bluetooth.async_ble_device_from_address(hass, address, True)
    ...
```

Walks BlueZ DBus and disconnects any existing connections to `address` owned by other processes/sessions. Without this, a crashed HA leaves dangling GATT handles that block `establish_connection` until a reboot. **Call this before every setup.** Sources: yalexs_ble, switchbot use it.

#### `bleak_retry_connector.establish_connection(...)` — full signature

```python
async def establish_connection(
    client_class: type[AnyBleakClient],         # BleakClient or BleakClientWithServiceCache
    device: BLEDevice,                           # from async_ble_device_from_address
    name: str,                                   # log label
    disconnected_callback: Callable | None = None,
    max_attempts: int = 4,                       # MAX_CONNECT_ATTEMPTS
    cached_services: BleakGATTServiceCollection | None = None,  # legacy bleak<0.17, ignore
    ble_device_callback: Callable[[], BLEDevice] | None = None,  # <-- IMPORTANT
    use_services_cache: bool = True,             # keep True for Govee
    pair: bool = False,
    **kwargs,
) -> AnyBleakClient
```

**`ble_device_callback` is the key parameter we keep missing.** It's called on every retry attempt to refresh the `BLEDevice` reference — wire it to `bluetooth.async_ble_device_from_address` so retries automatically pick up a newer BLEDevice from a different proxy/adapter if the original goes away mid-retry.

Backoff behavior:
- Per-exception backoff via `calculate_backoff_time`: DBus errors 0.25s, out-of-slots 4s, transient 0.25–1.0s
- `BLEAK_TIMEOUT = 20s` per attempt, `BLEAK_SAFETY_TIMEOUT = 60s` hard cap
- Transient errors (cap `MAX_TRANSIENT_ERRORS = 9`) don't count against `max_attempts`
- Raises `BleakNotFoundError`, `BleakAbortedError`, `BleakOutOfConnectionSlotsError`, `BleakConnectionError` — all subclasses of `BleakError`

**Correct usage**:
```python
def _refresh_ble_device() -> BLEDevice | None:
    return bluetooth.async_ble_device_from_address(hass, address, connectable=True)

client = await establish_connection(
    BleakClient,
    device=_refresh_ble_device() or original_ble_device,
    name=self._name,
    disconnected_callback=self._on_disconnect,
    ble_device_callback=_refresh_ble_device,
    max_attempts=4,
    use_services_cache=True,
)
```

#### `async_register_callback(hass, callback, match, mode) -> unsub`

```python
entry.async_on_unload(bluetooth.async_register_callback(
    hass,
    _async_update_ble,
    BluetoothCallbackMatcher(address=address, connectable=True),
    bluetooth.BluetoothScanningMode.PASSIVE,
))
```

Gotchas:
- Callback must be sync `@callback` — no awaits.
- Within one matcher dict, fields are ANDed (see `match.py` `ble_device_matches`).
- `local_name` with a wildcard in the first 3 characters raises `ValueError`.
- **`mode` is currently a no-op** — the comment in `api.py` reads "*mode is currently not used as we only support active scanning*". Pass `PASSIVE` for future-proofing but the scanner always runs ACTIVE.
- **Post-discovery ADVs do not auto-deliver** — after the manifest-based discovery fires `async_step_bluetooth` once, subsequent ADVs are silent unless you register your own callback with an address matcher. **This is why every write-heavy integration must call `async_register_callback` in setup.**

#### `async_track_unavailable(hass, callback, address, connectable=True) -> unsub`

```python
@callback
def _offline(info: BluetoothServiceInfoBleak) -> None:
    self._attr_available = False
    self.async_write_ha_state()

entry.async_on_unload(
    bluetooth.async_track_unavailable(hass, _offline, address, True)
)
```

Fires once when the device is silent longer than its learned ADV interval. Fallback thresholds (from `habluetooth/const.py`):
- Connectable: `CONNECTABLE_FALLBACK_MAXIMUM_STALE_ADVERTISEMENT_SECONDS = 195`
- Non-connectable: `FALLBACK_MAXIMUM_STALE_ADVERTISEMENT_SECONDS = 900`
- Sweep interval: `UNAVAILABLE_TRACK_SECONDS = 300s`

Precision is coarse. Pair with `async_register_callback` to detect return-to-available (the unavailable callback only fires once per offline transition).

#### `async_rediscover_address(hass, address)` and `async_clear_address_from_match_history(hass, address)`

If a Govee device's firmware update changes its advertisement fingerprint (manufacturer_data, service_data, service_uuids, or name), manifest matchers won't re-fire. Use these to force re-discovery — useful for user-triggered "re-detect device" repair actions.

### Theme 5: The critical gotchas (what we were about to get wrong)

From comparing the two agent surveys against the previous research doc:

1. **Missing `close_stale_connections_by_address`** — our previous plan said "delegate to `establish_connection`" but didn't mention this. After a HA crash, a fresh `establish_connection` fails because BlueZ still holds a connection from the previous PID. Every setup must call this first.

2. **Missing `ble_device_callback`** — our previous plan's `api/ble.py` stub calls `bleak_retry_connector.establish_connection(BleakClient, self._ble_device, self.address)` with only 3 positional args. Without `ble_device_callback=`, retries reuse a stale `BLEDevice` and proxy handoff breaks.

3. **`mode` argument confusion** — the previous doc distinguishes PASSIVE vs ACTIVE scanning modes as if they matter at the callback registration level. In 2026, they don't — HA always runs ACTIVE. The value only matters for the manifest matcher's `connectable` field and for `async_process_advertisements`.

4. **Post-discovery callbacks aren't automatic** — our previous plan implied that because we have `manifest.json` `bluetooth:` matchers, we receive all subsequent advertisements. We don't — after the first discovery, ADVs are silent unless we call `async_register_callback` in setup. This is why led_ble explicitly registers its callback in `async_setup_entry`, and why a BLE integration that does manifest discovery but skips the callback registration silently breaks over time.

5. **Entity subscribes to both coordinator AND device-level callbacks** — covered in Theme 3. Missing this means push state updates only arrive on the next coordinator tick (up to 60s late).

6. **Shutdown hook in addition to unload hook** — led_ble registers both `entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_stop))` AND `await runtime_data.device.stop()` in `async_unload_entry`. The stop event hook is belt-and-braces for HA shutdowns that skip entry unload.

7. **GATT write serialization per device** — `asyncio.Lock` around every write to prevent concurrent commands corrupting the buffer when HA dispatches parallel service calls. `PARALLEL_UPDATES = 0` on the entity class allows parallel updates by HA, so the per-device lock does the actual serialization.

### Theme 6: PyPI device library vs in-tree module

All six surveyed integrations delegate GATT I/O to a PyPI-published device library:

| Integration | Library | Notes |
|---|---|---|
| led_ble | `led-ble` v1.1.8 | Canonical shape — `LEDBLE` class with `update()`, `turn_on()`, `set_rgb()`, `register_callback()`, `set_ble_device_and_advertisement_data()`, `stop()` |
| yalexs_ble | `yalexs-ble` v3.3.0 | `PushLock` class — no HA coordinator, library IS the coordinator |
| snooz | `pysnooz` v0.8.6 | Functional command API — `turn_on(percentage)` returns a command to execute via `_async_execute_command` |
| keymitt_ble | `PyMicroBot` v0.0.23 | `MicroBotApiClient` class with `push_on()` / `push_off()` |
| bthome | `bthome-ble` v3.17.0 | `BTHomeBluetoothDeviceData.update()` — pure advertisement parser |
| switchbot | `PySwitchbot` v2.0.0 | Per-device-type classes; hybrid advert + GATT |

**Govee decision**: start with an in-tree `api/ble.py` module structured with the same API surface as a PyPI library (so it can be extracted later), but do NOT publish to PyPI initially. Reasons:
- Unnecessary external dependency for a HACS-only release.
- Faster iteration without PyPI release cycle.
- If we later upstream to HA core, extracting a `govee-ble-lights` package is a mechanical refactor (the module was already structured that way).

**Structural requirement**: the in-tree module must expose the same methods HA BLE device libraries do, so the HA wiring code looks identical to led_ble's. That means:
```python
class GoveeBLEDevice:
    def __init__(self, ble_device: BLEDevice, ...) -> None: ...
    @property
    def address(self) -> str: ...
    @property
    def name(self) -> str: ...
    def set_ble_device_and_advertisement_data(self, device: BLEDevice, adv: AdvertisementData) -> None: ...
    def register_callback(self, cb: Callable[[GoveeBLEState], None]) -> Callable[[], None]: ...
    async def update(self) -> None: ...   # may no-op for Govee
    async def turn_on(self) -> None: ...
    async def turn_off(self) -> None: ...
    async def set_brightness(self, value_0_255: int) -> None: ...
    async def set_rgb(self, r: int, g: int, b: int) -> None: ...
    async def stop(self) -> None: ...
```

### Theme 7: Test patterns without real hardware

From surveying tests in `led_ble`, `yalexs_ble`, and `switchbot`:

1. **Pure protocol unit tests** — highest-value, easiest to write:
   ```python
   from custom_components.govee.api.ble import _build_frame, LedPacketCmd
   def test_power_on_frame_matches_beshelmek_reference():
       frame = _build_frame(LedPacketCmd.POWER, [0x01])
       assert frame[0] == 0x33
       assert frame[19] == 0x33 ^ 0x01 ^ 0x01  # XOR checksum
   ```
   No HA mocks needed; runs in <1s for hundreds of frame variants.

2. **HA core's bluetooth test fixtures**:
   ```python
   from tests.components.bluetooth import (
       generate_advertisement_data, generate_ble_device,
       inject_bluetooth_service_info,
   )

   async def test_discovery_sets_unique_id(hass):
       info = BluetoothServiceInfoBleak(
           name="Govee_H6072_1234",
           address="AA:BB:CC:DD:EE:FF",
           ...,
           device=generate_ble_device("AA:BB:CC:DD:EE:FF", "Govee_H6072_1234"),
           advertisement=generate_advertisement_data(local_name="Govee_H6072_1234"),
       )
       result = await hass.config_entries.flow.async_init(
           DOMAIN, context={"source": "bluetooth"}, data=info,
       )
       assert result["type"] == "form"
       assert result["step_id"] == "bluetooth_confirm"
   ```

3. **MagicMock the device library**:
   ```python
   from unittest.mock import MagicMock, AsyncMock
   from custom_components.govee.api.ble import GoveeBLEDevice

   def make_fake_device(address="AA:BB:CC:DD:EE:FF"):
       device = MagicMock(spec=GoveeBLEDevice)
       device.address = address
       device.name = "Govee BLE"
       device.update = AsyncMock()
       device.turn_on = AsyncMock()
       device.register_callback = MagicMock(return_value=lambda: None)
       return device
   ```

Coverage target: 95%+ without touching real BLE hardware is achievable because the protocol module is pure and the HA wiring is thin.

---

## Compatibility Analysis

### Stack Compatibility

| Aspect | Status | Notes |
|---|---|---|
| `bleak_retry_connector>=3.0.0` | Compatible (v4.6.0 tested by agent) | Wire `ble_device_callback`; v4.6.0 signature verified |
| `close_stale_connections_by_address` | Compatible | Available in bleak_retry_connector ≥3.x |
| HA 2024.5+ `entry.runtime_data` | Compatible | Cloud side already uses it |
| `BluetoothCallbackMatcher` dict form | Compatible | Matches led_ble's usage |
| `BluetoothScanningMode.PASSIVE` as callback mode | Forward-compat no-op | OK to pass for future-proofing |
| Manifest `bluetooth:` matchers | Compatible | `local_name` wildcard needs first 3 chars literal — "Govee_" / "ihoment" / "GBK_" all comply |
| Test fixtures from `tests.components.bluetooth` | Compatible | Used by every HA core BLE integration's test suite |

### Integration Complexity

- **Effort delta vs previous research doc**: Small — this research tightens implementation details but doesn't change the high-level plan in `2026-04-08_ble-direct-support.md`.
- **Files affected**: same as previous doc, but with concrete additions to `api/ble.py` (close_stale_connections, ble_device_callback), `__init__.py` (startup event, stop event hook), `platforms/ble_light.py` (device-level register_callback in async_added_to_hass).
- **Breaking changes**: none.

---

## Recommendation

### Decision

**Follow `led_ble` almost verbatim**, with specific pattern additions from `yalexs_ble` and `switchbot`, and publish as an in-tree `api/ble.py` module structured as if it were a PyPI library.

### Rationale

- led_ble is the closest device class (write-heavy RGB BLE light, exact match for Govee use case).
- It uses the canonical shape (manifest → config_flow → setup → coordinator → entity) shared by 5/6 of the integrations I surveyed.
- Its dual-path update pattern (coordinator poll + device-level push callback) gives us future-proofing for advertisement-based state if we add it later.
- yalexs_ble and switchbot contribute critical gotcha mitigations (`close_stale_connections_by_address`, `ble_device_callback`) that led_ble implicitly assumes via its lib.
- Structuring `api/ble.py` as a pseudo-library makes future PyPI extraction trivial if we upstream to HA core.

### Comparison Matrix — coordinator / architecture choice

| Criteria | Plain DataUpdateCoordinator (led_ble) | No coordinator (yalexs_ble) | PassiveBluetooth (keymitt_ble) |
|---|---|---|---|
| Matches write-heavy commanded lights | ✓ exactly | ✓ with caveats | ✗ (advert-driven) |
| Polling fallback | ✓ | ✗ | ✗ |
| Device-level push path | ✓ (in addition to polling) | ✓ (only path) | ✓ |
| Minimal boilerplate | ✓ | ✓ | ✗ |
| HA core precedent for lights | led_ble | — | — |
| **Overall** | **Recommended** | Acceptable | Wrong shape |

---

## Implementation Sketch

### Concrete updates to `api/ble.py` skeleton (from previous doc)

Add `close_stale_connections_by_address` and `ble_device_callback` to the `GoveeBLEDevice` class:

```python
# custom_components/govee/api/ble.py — additions beyond previous doc
from typing import TYPE_CHECKING, Callable
from bleak_retry_connector import (
    BleakClientWithServiceCache,
    BleakError,
    BleakNotFoundError,
    close_stale_connections_by_address,
    establish_connection,
)

class GoveeBLEDevice:
    def __init__(
        self,
        ble_device: BLEDevice,
        refresh_ble_device: Callable[[], BLEDevice | None] | None = None,
        segmented: bool = False,
    ) -> None:
        self._ble_device = ble_device
        self._refresh_ble_device = refresh_ble_device  # for proxy handoff
        self._segmented = segmented
        self._client: BleakClientWithServiceCache | None = None
        self._state = GoveeBLEState()
        self._lock = asyncio.Lock()
        self._callbacks: list[Callable[[GoveeBLEState], None]] = []

    async def _ensure_connected(self) -> BleakClientWithServiceCache:
        if self._client is not None and self._client.is_connected:
            return self._client

        # Critical defensive call — free dangling handles from a crashed HA.
        await close_stale_connections_by_address(self.address)

        def _ble_device_callback() -> BLEDevice:
            """Called on every retry attempt — returns fresh BLEDevice for proxy handoff."""
            if self._refresh_ble_device is not None:
                fresh = self._refresh_ble_device()
                if fresh is not None:
                    self._ble_device = fresh
            return self._ble_device

        self._client = await establish_connection(
            BleakClientWithServiceCache,   # enables service caching
            device=self._ble_device,
            name=self.name,
            disconnected_callback=self._on_disconnected,
            ble_device_callback=_ble_device_callback,
            max_attempts=4,
            use_services_cache=True,
        )
        return self._client

    # ... rest as in previous doc
```

### Concrete updates to `__init__.py` `_async_setup_ble_entry`

Add startup event gate, `close_stale_connections`, and the stop event hook:

```python
async def _async_setup_ble_entry(hass: HomeAssistant, entry: GoveeConfigEntry) -> bool:
    from .api.ble import GoveeBLEDevice, SEGMENTED_MODELS
    from bleak_retry_connector import close_stale_connections_by_address

    address = entry.data[CONF_ADDRESS]

    # 1. Free stale connections before anything else.
    await close_stale_connections_by_address(address)

    # 2. Fetch BLEDevice or raise ConfigEntryNotReady.
    ble_device = bluetooth.async_ble_device_from_address(hass, address, connectable=True)
    if ble_device is None:
        raise ConfigEntryNotReady(f"Govee BLE device {address} not yet seen")

    # 3. Build refresh callable for proxy handoff.
    def _refresh() -> BLEDevice | None:
        return bluetooth.async_ble_device_from_address(hass, address, connectable=True)

    segmented = entry.data.get(CONF_BLE_SEGMENTED, False)
    device = GoveeBLEDevice(ble_device, refresh_ble_device=_refresh, segmented=segmented)

    # 4. PASSIVE advertisement callback — feeds device library fresh BLEDevice refs.
    @callback
    def _async_update_ble(service_info, change) -> None:
        device.set_ble_device_and_advertisement_data(
            service_info.device, service_info.advertisement,
        )
    entry.async_on_unload(bluetooth.async_register_callback(
        hass, _async_update_ble,
        {"address": address, "connectable": True},
        bluetooth.BluetoothScanningMode.PASSIVE,   # no-op today, future-proof
    ))

    # 5. Startup gate (optional — skip if Govee lights don't reliably push first state).
    # For hacs-govee we go without it initially; if setup flakes we can add later.

    # 6. Coordinator with polling fallback.
    coordinator = GoveeBLECoordinator(hass, entry, device)
    try:
        await coordinator.async_config_entry_first_refresh()
    except BleakError as err:
        raise ConfigEntryNotReady(str(err)) from err

    entry.runtime_data = coordinator

    # 7. Unavailable tracker — mark entity unavailable if device goes silent.
    @callback
    def _async_device_unavailable(info) -> None:
        _LOGGER.debug("Govee BLE device %s marked unavailable", address)
        # Device library still holds state; HA CoordinatorEntity.available handles the rest.
    entry.async_on_unload(bluetooth.async_track_unavailable(
        hass, _async_device_unavailable, address, connectable=True,
    ))

    # 8. Shutdown hook — cleanly close BleakClient on HA stop.
    async def _async_stop(_event) -> None:
        await device.stop()
    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_stop)
    )

    # 9. Forward to platforms.
    await hass.config_entries.async_forward_entry_setups(entry, _BLE_PLATFORMS)
    return True
```

### Concrete update to `platforms/ble_light.py`

Add the device-level callback subscription in `async_added_to_hass`:

```python
class GoveeBLELightEntity(CoordinatorEntity[GoveeBLECoordinator], LightEntity):
    # ... (as in previous doc)

    async def async_added_to_hass(self) -> None:
        """Subscribe to device-level push callbacks in addition to coordinator updates."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.device.register_callback(
                lambda _state: self._handle_coordinator_update()
            )
        )
```

This wires the led_ble-style dual-path update: coordinator polling (every 60s) AND device-library push callbacks.

### Concrete test patterns (add to Phase 9 in previous doc)

```python
# tests/test_api_ble_additional.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.govee.api.ble import GoveeBLEDevice


@pytest.mark.asyncio
async def test_ensure_connected_calls_close_stale_first():
    ble_device = MagicMock()
    ble_device.address = "AA:BB:CC:DD:EE:FF"
    ble_device.name = "Govee Test"
    device = GoveeBLEDevice(ble_device)

    with patch(
        "custom_components.govee.api.ble.close_stale_connections_by_address",
        AsyncMock(),
    ) as mock_close, patch(
        "custom_components.govee.api.ble.establish_connection",
        AsyncMock(return_value=MagicMock(is_connected=True)),
    ) as mock_establish:
        await device._ensure_connected()

    mock_close.assert_called_once_with("AA:BB:CC:DD:EE:FF")
    assert mock_establish.called
    _, kwargs = mock_establish.call_args
    assert "ble_device_callback" in kwargs
    assert kwargs["max_attempts"] == 4
    assert kwargs["use_services_cache"] is True


@pytest.mark.asyncio
async def test_ble_device_callback_refreshes_on_retry():
    """Verify the ble_device_callback wires back to async_ble_device_from_address."""
    ble_device_v1 = MagicMock(address="AA:BB:CC:DD:EE:FF", name="Govee Test")
    ble_device_v2 = MagicMock(address="AA:BB:CC:DD:EE:FF", name="Govee Test")

    refresh_calls = iter([ble_device_v2])
    device = GoveeBLEDevice(
        ble_device_v1,
        refresh_ble_device=lambda: next(refresh_calls, None),
    )

    # Capture the ble_device_callback passed to establish_connection.
    captured_cb = None
    async def fake_establish(*args, **kwargs):
        nonlocal captured_cb
        captured_cb = kwargs["ble_device_callback"]
        return MagicMock(is_connected=True)

    with patch(
        "custom_components.govee.api.ble.close_stale_connections_by_address", AsyncMock()
    ), patch(
        "custom_components.govee.api.ble.establish_connection", side_effect=fake_establish
    ):
        await device._ensure_connected()

    # Call the captured callback — should return the refreshed BLEDevice.
    refreshed = captured_cb()
    assert refreshed is ble_device_v2
```

---

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| `close_stale_connections_by_address` signature changes in bleak_retry_connector | Low | Medium | Pin `bleak-retry-connector>=3.0.0,<5.0.0`; covered by unit test that mocks the function |
| `mode` argument to `async_register_callback` becomes meaningful (ACTIVE vs PASSIVE matter later) | Low | Low | We pass `PASSIVE` which is the lower-cost default; if it activates in a future HA release, Govee behavior is correct by default |
| Govee BLE lights don't fire coordinator-observable callbacks → device-level register_callback path is effectively unused | Medium | Low | Accepted. The path is there for future advertisement-state work; nothing breaks if it never fires |
| `BleakClientWithServiceCache` caches stale services after firmware update | Low | Medium | Document how users can delete and re-add the entry after firmware updates; `use_services_cache=False` as an advanced option if reports arrive |
| Proxy handoff via `ble_device_callback` doesn't trigger in practice on some HA Bluetooth proxies | Low | Medium | Mitigation baked in — if the callback returns the same BLEDevice, nothing worse than the previous behavior |
| `async_track_unavailable` 195s coarseness causes stale state shown to user for ~3 minutes | Medium | Low | Acceptable; document in README. Users who need faster detection can use automations on the `available` attribute |
| Test fixtures `tests.components.bluetooth` change between HA versions | Low | Medium | Pin HA version via manifest; HA core bluetooth test fixtures are stable since 2024.x |

### Open Questions

- Does led_ble's startup `asyncio.Event` pattern apply to Govee? If the library's first GATT read doesn't reliably succeed, the startup gate causes `ConfigEntryNotReady` on every setup. May need to drop the gate and let polling fallback handle the first state asynchronously.
- When the Govee device is offline but the adapter is fine, does `bluetooth.async_track_unavailable` fire at the right cadence, or does BlueZ's own stale-advertisement detection cause delays?
- Should we implement `close_stale_connections_by_address` call inside the `GoveeBLEDevice._ensure_connected` (every reconnect) or only once at setup? yalexs_ble does it at setup; switchbot does it before every library call. Pick one consistently.
- Govee BLE lights' behavior when multiple HA instances try to connect simultaneously — does the device enforce single-client semantics? Test with two HA boxes once we have the module.

---

## References

1. **led_ble** — https://github.com/home-assistant/core/tree/dev/homeassistant/components/led_ble — primary template; plain DataUpdateCoordinator, PASSIVE callback, device-level register_callback dual-path.
2. **led_ble PyPI lib** — https://pypi.org/project/led-ble/ — canonical device-library shape we should mirror.
3. **yalexs_ble** — https://github.com/home-assistant/core/tree/dev/homeassistant/components/yalexs_ble — dual-transport cloud+BLE coexistence, `close_stale_connections_by_address` pattern, `PushLock` as coordinator.
4. **snooz** — https://github.com/home-assistant/core/tree/dev/homeassistant/components/snooz — no-coordinator pattern, `RestoreEntity` + `assumed_state` for disconnected state.
5. **keymitt_ble** — https://github.com/home-assistant/core/tree/dev/homeassistant/components/keymitt_ble — `PassiveBluetoothDataUpdateCoordinator` with ACTIVE scanning contrast.
6. **bthome** — https://github.com/home-assistant/core/tree/dev/homeassistant/components/bthome — `PassiveBluetoothProcessorCoordinator`, `connectable: false` matchers; contrast case (what NOT to do for write-heavy devices).
7. **switchbot** — https://github.com/home-assistant/core/tree/dev/homeassistant/components/switchbot — hybrid advert+GATT, `ActiveBluetoothDataUpdateCoordinator`, macOS address handling, `close_stale_connections_by_address` before every setup.
8. **HA dev docs — Bluetooth API** — https://developers.home-assistant.io/docs/core/bluetooth/api — canonical public function list and semantics.
9. **HA dev docs — Fetching Bluetooth data** — https://developers.home-assistant.io/docs/core/bluetooth/bluetooth_fetching_data — coordinator decision matrix.
10. **`homeassistant/components/bluetooth/api.py`** — https://github.com/home-assistant/core/blob/dev/homeassistant/components/bluetooth/api.py — source of truth for public function signatures; includes the "mode is currently not used" comment.
11. **`homeassistant/components/bluetooth/match.py`** — https://github.com/home-assistant/core/blob/dev/homeassistant/components/bluetooth/match.py — matcher field names, AND-within-matcher logic, post-discovery fingerprint tracking.
12. **`habluetooth`** — https://pypi.org/project/habluetooth/ — installed by HA; `manager.py`, `const.py`, `models.py` define unavailable tracking timings (`CONNECTABLE_FALLBACK_MAXIMUM_STALE_ADVERTISEMENT_SECONDS = 195`, etc.).
13. **`bleak-retry-connector`** — https://github.com/Bluetooth-Devices/bleak-retry-connector — `establish_connection` signature v4.6.0, backoff behavior, `close_stale_connections_by_address`, `BleakClientWithServiceCache`.
14. **Previous research doc** — `docs/_research/2026-04-08_ble-direct-support.md` — architectural decision (single domain, two entry types) and phase-by-phase implementation plan. This doc refines it with concrete HA-idiomatic patterns.
15. **Beshelmek/govee_ble_lights** — https://github.com/Beshelmek/govee_ble_lights — reference protocol implementation (separately validated in previous research doc).
