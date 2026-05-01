# Research: Multi-Transport Single-Entity Architecture (BLE + MQTT + REST)

**Date**: 2026-04-09
**Type**: Architecture Decision
**Status**: Complete
**Stack**: hacs-govee (HA custom component, Python 3.12)
**Supersedes**: The dual-entry model from `2026-04-08_ble-direct-support.md` Phase 2-4. Phase 1 (`api/ble.py`) remains valid.

---

## Summary

The earlier BLE research planned a dual-entry model: separate BLE config entries with separate BLE entities per device, the same pattern august+yalexs_ble use. This research explores the user's preferred alternative: **one entity per physical device, with the coordinator routing commands through BLE → MQTT → REST based on availability**. After surveying six HA core integrations (shelly, switchbot, esphome, matter, tuya, tplink), none implement single-entity-multi-transport with cloud fallback — this would be novel in HA. However, the codebase analysis reveals that hacs-govee's architecture is **perfectly structured for it**: all entity commands already funnel through a single gateway (`coordinator.async_control_device()` at coordinator.py:586), the observer pattern (`_notify_observers`) already delivers MQTT state pushes to entities transparently, and `bluetooth.async_register_callback` requires no bluetooth config entry — the existing cloud entry can freely subscribe to BLE advertisements. The unified model is recommended because it eliminates entity duplication, avoids device-registry merge gymnastics, and makes BLE a transparent enhancement rather than a second integration the user has to manage. Phase 1 (`api/ble.py`) is fully reusable — the `GoveeBLEDevice` class just gets instantiated per-device inside the existing coordinator rather than per-config-entry in a separate coordinator.

---

## Research Questions

### Q1: Which HA integrations serve a single entity from multiple transport backends?
**Answer**: None of the six surveyed integrations (shelly, switchbot, esphome, matter, tuya, tplink) implement single-entity-multi-transport with fallback. Shelly comes closest with dual coordinators per generation, but they serve the same protocol's entities, not as cross-protocol fallbacks. Matter hides transport behind an external server. All others are single-transport per entity, or dual-integration (separate entities). **The unified coordinator model would be novel in HA** — but nothing in HA's architecture prevents it. Every API we need (`bluetooth.async_register_callback`, `async_ble_device_from_address`, `establish_connection`) is available to any integration regardless of config entry source.

### Q2: Where is the command dispatch injection point?
**Answer**: `coordinator.async_control_device(device_id, command)` at `coordinator.py:586`. Every entity command (power, brightness, color, color_temp, scene, music mode, DreamView, DIY scene) funnels through this single method. It currently calls `self._api_client.control_device(device_id, device.sku, command)` at line 612. Adding a BLE-first dispatch is a ~15-line change:
```python
# Before REST, try BLE if available
if self._ble_device_for(device_id) is not None:
    try:
        await self._send_via_ble(device_id, command)
        return True
    except BleakError:
        _LOGGER.debug("BLE failed for %s, falling back to REST", device_id)
# Existing REST path unchanged
```

### Q3: How would the coordinator match cloud device IDs to BLE MACs?
**Answer** *(updated 2026-04-09 after verification attempt)*: The "first 6 bytes of the 8-byte cloud device_id = BLE MAC" assumption is **unconfirmed**. No primary source (Beshelmek/govee_ble_lights, wez/govee2mqtt, Govee API docs, Govee FAQ) confirms the mapping. Beshelmek treats cloud and BLE as separate discovery paths with different unique_ids. wez/govee2mqtt doesn't do direct BLE. The `/user/devices` response has no `bleAddress` field. **Primary matching strategy**: extract the SKU from the BLE advertising name (`Govee_H6072_754B` → `H6072`), find cloud devices with that SKU. If exactly one match, done. For multiple same-SKU devices, use the MAC-prefix heuristic (`device_id[:17] == ble_mac`) as an unproven-but-plausible tiebreaker. Group devices (numeric IDs) are never BLE.

### Q4: Can a cloud config entry subscribe to BLE advertisements?
**Answer**: Yes. `bluetooth.async_register_callback(hass, callback, matcher, mode)` takes `hass` and a callback — no config entry parameter. Any integration can call it from any `async_setup_entry`. The cloud entry can register a callback with `BluetoothCallbackMatcher(local_name="Govee_*")` (or manufacturer_id if Govee uses one consistently) and receive advertisements for all nearby Govee BLE devices. `bluetooth.async_ble_device_from_address(hass, address, connectable=True)` similarly requires no bluetooth config entry.

### Q5: What happens when BLE is available for some devices but not others?
**Answer**: BLE availability is inherently per-device (only nearby devices are BLE-reachable). The coordinator maintains a dict `_ble_devices: dict[str, GoveeBLEDevice]` keyed by device_id. When a BLE advertisement matches a known cloud device, that device's `GoveeBLEDevice` is instantiated and stored. When the device goes out of BLE range (`async_track_unavailable` fires), the entry is removed. Commands fall through to REST automatically for non-BLE devices or when BLE goes offline.

### Q6: Could BLE writes + MQTT push state complement each other?
**Answer**: Yes — this is the ideal configuration. BLE provides fast local command writes (~50ms round-trip vs ~500ms+ cloud REST). MQTT continues providing real-time state push updates (power, brightness, color changes initiated by the Govee app or hardware button). The coordinator already handles MQTT state updates via `_notify_observers()` — BLE doesn't need to replace MQTT state delivery, just the command write path. Best-of-both-worlds: commands go local via BLE, state comes back real-time via MQTT.

---

## Findings

### Theme 1: The unified model is architecturally cleaner than dual-entry

| Aspect | Dual-entry model (previous plan) | Unified coordinator model (new) |
|---|---|---|
| Entities per device | 2 (cloud + BLE) | **1** |
| Config entries | N+1 (hub + per-BLE-device) | **1** (hub only) |
| User setup | Must pair each BLE device separately | **Automatic** — BLE detected transparently |
| Device registry | Needs CONNECTION_BLUETOOTH merge | **N/A** — single device entry |
| Coordinator classes | 2 (cloud + BLE) | **1** (enhanced cloud) |
| Platform files | cloud light.py + ble_light.py | **Unchanged** light.py |
| Entity classes | GoveeLightEntity + GoveeBLELightEntity | **Unchanged** GoveeLightEntity |
| User confusion | "Which entity do I automate?" | **None** |
| Config flow additions | async_step_bluetooth + async_step_bluetooth_confirm | **None** |
| Code added | ~800 LOC (coordinator_ble, ble_light, config_flow) | **~150 LOC** (coordinator enhancement) |
| Tests added | test_coordinator_ble, test_ble_light, test_config_flow additions | **~100 LOC** (transport dispatch tests) |

### Theme 2: The existing coordinator is the perfect injection point

Codebase analysis reveals:

1. **Single command gateway**: `async_control_device(device_id, command)` at coordinator.py:586. Every entity command flows through it. Adding BLE dispatch is a targeted ~15-line addition, not a refactor.

2. **Command objects are transport-agnostic**: `PowerCommand`, `BrightnessCommand`, `ColorCommand` etc. are pure dataclasses with a `to_api_payload()` method for REST. We add a `to_ble_frame()` method (or the BLE layer maps them internally) and the same command object routes through either transport.

3. **Observer pattern for state delivery**: `_notify_observers(device_id, state)` at coordinator.py:221-225. MQTT state pushes already use this path. BLE state notifications (if we ever add GATT notifications) would use the same path. **Zero entity changes needed.**

4. **Per-device conditional pattern already exists**: coordinator.py:272 (`if device.is_group: skip MQTT`) and coordinator.py:462 (`if device.is_group: skip state fetch`). Adding `if device.ble_available: try BLE first` follows the same pattern.

5. **`GoveeDevice` is a frozen dataclass**: The device model at models/device.py can gain a `ble_address: str | None` field populated during cloud device discovery if we can extract the BLE MAC from the cloud device_id (see Q3). Or it can be populated lazily when a BLE advertisement is correlated with a cloud device.

### Theme 3: BLE advertisement subscription from the cloud entry

`bluetooth.async_register_callback` signature:
```python
async_register_callback(hass, callback, matcher, mode) -> unsub_callable
```
No config entry parameter — any integration can call it. The cloud config entry's `async_setup_entry` can register a callback to detect nearby Govee BLE devices:

```python
# In async_setup_entry, after coordinator is created:
@callback
def _on_govee_ble_advertisement(service_info: BluetoothServiceInfoBleak, change) -> None:
    coordinator.handle_ble_advertisement(service_info)

entry.async_on_unload(bluetooth.async_register_callback(
    hass, _on_govee_ble_advertisement,
    BluetoothCallbackMatcher(local_name="Govee_*"),
    BluetoothScanningMode.ACTIVE,
))
# Repeat for ihoment_* and GBK_* prefixes, or use manufacturer_id if stable
```

The coordinator's `handle_ble_advertisement` method:
1. Extracts SKU from `service_info.name` (e.g. `Govee_H6072_754B` → `H6072`)
2. Finds cloud devices matching that SKU; if exactly one, correlation is unambiguous
3. For multiple same-SKU devices, tries MAC-prefix heuristic as tiebreaker
4. If matched, instantiates/refreshes a `GoveeBLEDevice` for that device
5. Future commands to that device try BLE first

### Theme 4: The transport priority chain

```
Entity.async_turn_on(color=(255,0,0))
  → coordinator.async_control_device(device_id, ColorCommand(r=255, g=0, b=0))
    → if device has BLE available:
        → GoveeBLEDevice.set_rgb(255, 0, 0)  [local, ~50ms]
        → on BleakError: fall through
    → REST: api_client.control_device(device_id, sku, command)  [cloud, ~500ms]
    → on success: apply optimistic state + _notify_observers()

State comes back via:
  → MQTT push → _notify_observers(device_id, state)  [real-time, cloud-pushed]
  → REST poll → periodic fetch → _notify_observers()  [fallback, every 60s]
  → (future) BLE GATT notifications → _notify_observers()
```

Entities are completely transport-unaware. They call `async_control_device()` and receive state via the observer pattern. BLE, MQTT, and REST are implementation details of the coordinator.

### Theme 5: What changes from Phase 1

**Phase 1 (`api/ble.py`) is fully reusable.** The `GoveeBLEDevice` class, frame builders, constants, and all 62 tests are valid. What changes:

| Component | Previous plan (dual-entry) | New plan (unified) |
|---|---|---|
| `api/ble.py` | Used by `coordinator_ble.py` | **Used by `coordinator.py`** — same class, different owner |
| `coordinator_ble.py` | New file, 150 LOC | **Deleted from plan** — functionality absorbed into existing coordinator |
| `platforms/ble_light.py` | New file, 150 LOC | **Deleted from plan** — existing `light.py` entity works as-is |
| `config_flow.py` additions | `async_step_bluetooth` + `async_step_bluetooth_confirm` | **No config flow changes** — BLE is auto-detected |
| `coordinator.py` | Unchanged | **Enhanced** — BLE transport dispatch + advertisement subscription |
| `__init__.py` | Entry-type branching | **Simplified** — BLE subscription in existing cloud setup |
| `models/device.py` | Unchanged | **Enhanced** — `ble_address` field on GoveeDevice |
| `const.py` | New CONF_ENTRY_TYPE, ENTRY_TYPE_BLE, etc. | **Minimal** — just `CONF_ENABLE_BLE` option |
| `manifest.json` | bluetooth matchers + bluetooth_adapters dep | **Same** (still needed for BLE advertisement delivery) |
| Tests | test_coordinator_ble, test_ble_light, config flow tests | **Smaller** — transport dispatch tests in test_coordinator.py |

### Theme 6: Manifest still needs bluetooth matchers

Even though we don't create BLE config entries, we need `bluetooth` matchers in `manifest.json` for HA to deliver BLE advertisements to our callback. Without the `bluetooth:` key in the manifest, `async_register_callback` still works but the integration won't appear in HA's Bluetooth device discovery — which is fine since we don't want discovery, we just want passive monitoring.

Actually — **we don't need manifest `bluetooth:` matchers at all for this approach.** The manifest matchers are for triggering `async_step_bluetooth` in the config flow. Since we're not creating BLE config entries, we skip the manifest matchers entirely and just call `async_register_callback` with runtime matchers in `async_setup_entry`. This avoids the `dependencies: ["bluetooth_adapters"]` requirement, which is better for cloud-only installs that don't have Bluetooth hardware.

Updated manifest impact: **none.** Just add `bleak-retry-connector>=3.0.0` to requirements (needed at import time when `api/ble.py` loads).

### Theme 7: User opt-in and configuration

Two options:

**Option A — Automatic (recommended)**: BLE is always active when the HA host has Bluetooth hardware. The coordinator checks for `bluetooth` component availability at setup:
```python
try:
    from homeassistant.components import bluetooth
    HAS_BLUETOOTH = True
except ImportError:
    HAS_BLUETOOTH = False
```
If available, register the advertisement callback. No user action needed. Add a `CONF_ENABLE_BLE` option (default True) so users can disable if BLE causes issues.

**Option B — Opt-in**: Add a `CONF_ENABLE_BLE` toggle in the options flow (default False). User must explicitly enable BLE. Safer but loses the "it just works" benefit.

Recommend Option A: transparent enhancement with an escape hatch.

---

## Compatibility Analysis

### Stack Compatibility

| Aspect | Status | Notes |
|--------|--------|-------|
| Existing cloud entities | Unchanged | Zero entity changes — transport is coordinator-internal |
| Existing automations | Unchanged | Same entity IDs, same state attributes |
| HA installs without Bluetooth | Compatible | BLE code conditionally imported; no crash if bluetooth unavailable |
| `bleak-retry-connector` in requirements | Compatible | Already available in .venv; only loaded when Bluetooth is present |
| `api/ble.py` (Phase 1) | Fully reusable | `GoveeBLEDevice` gets instantiated per-device by the coordinator |
| Existing MQTT push path | Unchanged | MQTT continues delivering state; BLE handles commands |
| Device registry | Unchanged | One entry per device, as before |
| Existing tests | Unchanged | 553 existing tests unaffected |

### Integration Complexity

- **Effort estimate**: Low-Medium (days, not weeks)
- **Files modified**: `coordinator.py` (~100 LOC), `__init__.py` (~30 LOC), `models/device.py` (~10 LOC), `manifest.json` (1 requirement line)
- **Files added**: None (Phase 1 already done)
- **Files deleted from previous plan**: `coordinator_ble.py`, `platforms/ble_light.py` — never built
- **Breaking changes**: None
- **Config flow changes**: None (Option A) or minimal (Option B adds one toggle)
- **New tests**: ~60 LOC in `test_coordinator.py` covering transport dispatch

---

## Recommendation

### Decision

**Adopt the unified coordinator model.** Abandon the dual-entry/dual-entity plan from the previous research. Enhance the existing `GoveeCoordinator` to:
1. Subscribe to BLE advertisements for nearby Govee devices
2. Maintain a per-device `GoveeBLEDevice` cache
3. Route commands through BLE first (when available), falling back to REST
4. Continue receiving state via MQTT (unchanged)

### Rationale

- **One entity per device** — eliminates the "which entity do I automate?" confusion
- **~150 LOC vs ~800 LOC** — dramatically less code to write, test, and maintain
- **Zero config flow changes** — BLE is transparent, users never interact with it
- **Zero entity changes** — transport is a coordinator implementation detail
- **Zero breaking changes** — existing cloud-only behavior is the fallback
- **Complementary transports** — BLE for fast writes, MQTT for real-time state, REST as universal fallback
- **Phase 1 is fully reusable** — `GoveeBLEDevice` + tests just change who instantiates them

### Why not the dual-entry model?

The dual-entry model is idiomatic in HA (august + yalexs_ble) but optimizes for a different situation: two separately-maintained integration repos that cooperate at the device registry level. For a single HACS integration that already manages the device via cloud, adding BLE as a second entry with separate entities creates unnecessary user complexity, code surface, and test burden.

---

## Implementation Sketch

### Step 1: BLE correlation fields on `GoveeDevice` (models/device.py)

No static `ble_address` field is needed. BLE correlation is dynamic — the coordinator matches at runtime via SKU extraction from the BLE advertising name. The `GoveeDevice` model stays unchanged. The coordinator maintains a separate `_ble_devices: dict[str, GoveeBLEDevice]` dict keyed by cloud `device_id` that gets populated when a BLE advertisement is correlated.

Add a helper function for SKU extraction:
```python
# In coordinator.py or a utility module
def _sku_from_ble_name(name: str | None) -> str | None:
    """Extract SKU from advertising name like 'Govee_H6072_ABCD'."""
    if not name:
        return None
    parts = name.split("_")
    for part in parts:
        if part.startswith("H") and len(part) >= 4 and part[1:].isalnum():
            return part
    return None
```

### Step 2: Add BLE transport layer to coordinator (coordinator.py)

```python
class GoveeCoordinator:
    def __init__(self, ...):
        # ... existing init ...
        self._ble_devices: dict[str, GoveeBLEDevice] = {}
        self._ble_unsub: Callable | None = None

    async def async_setup(self) -> None:
        # ... existing setup ...
        await self._async_setup_ble()

    async def _async_setup_ble(self) -> None:
        """Subscribe to BLE advertisements for known Govee devices."""
        try:
            from homeassistant.components import bluetooth
        except ImportError:
            _LOGGER.debug("Bluetooth not available, BLE transport disabled")
            return

        @callback
        def _on_ble_advertisement(service_info, change) -> None:
            self._handle_ble_advertisement(service_info)

        # Register for all three Govee name prefixes
        for prefix in ("Govee_*", "ihoment_*", "GBK_*"):
            unsub = bluetooth.async_register_callback(
                self.hass, _on_ble_advertisement,
                {"local_name": prefix, "connectable": True},
                bluetooth.BluetoothScanningMode.ACTIVE,
            )
            self.config_entry.async_on_unload(unsub)

    def _handle_ble_advertisement(self, service_info) -> None:
        """Correlate BLE advertisement with known cloud devices via SKU matching."""
        ble_sku = _sku_from_ble_name(service_info.name)
        if not ble_sku:
            return

        # Find cloud devices with matching SKU
        candidates = [
            (did, dev) for did, dev in self._devices.items()
            if dev.sku == ble_sku and not dev.is_group
        ]

        matched_id: str | None = None
        if len(candidates) == 1:
            matched_id = candidates[0][0]
        elif len(candidates) > 1:
            # Multiple same-SKU devices — try MAC-prefix heuristic as tiebreaker
            ble_mac = service_info.address.upper()
            for did, _dev in candidates:
                if did.upper().startswith(ble_mac):
                    matched_id = did
                    break

        if matched_id is None:
            return

        if matched_id not in self._ble_devices:
            self._ble_devices[matched_id] = GoveeBLEDevice(
                service_info.device,
                segmented=ble_sku in SEGMENTED_MODELS,
            )
            _LOGGER.info(
                "BLE transport available for %s (SKU=%s, BLE=%s)",
                self._devices[matched_id].name, ble_sku, service_info.address,
            )
        else:
            self._ble_devices[matched_id].set_ble_device_and_advertisement_data(
                service_info.device, service_info.advertisement,
            )

    async def async_control_device(self, device_id: str, command: DeviceCommand) -> bool:
        # ... existing validation ...

        # NEW: Try BLE first if available
        ble_device = self._ble_devices.get(device_id)
        if ble_device is not None:
            try:
                await self._send_via_ble(ble_device, command)
                # Apply optimistic state + notify observers (same as REST path)
                self._apply_optimistic_state(device_id, command)
                self._notify_observers(device_id, self._states[device_id])
                return True
            except BleakError:
                _LOGGER.debug("BLE write failed for %s, falling back to REST", device_id)

        # Existing REST path — unchanged
        return await self._api_client.control_device(device_id, device.sku, command)
        # ... existing error handling ...

    async def _send_via_ble(self, ble_device: GoveeBLEDevice, command: DeviceCommand) -> None:
        """Translate a DeviceCommand to BLE and write it."""
        if isinstance(command, PowerCommand):
            if command.value:
                await ble_device.turn_on()
            else:
                await ble_device.turn_off()
        elif isinstance(command, BrightnessCommand):
            await ble_device.set_brightness(command.value)
        elif isinstance(command, ColorCommand):
            await ble_device.set_rgb(command.value.r, command.value.g, command.value.b)
        else:
            # Scene, color_temp, music mode, etc. — not BLE-capable
            raise NotImplementedError(f"BLE does not support {type(command).__name__}")
```

### Step 3: Cleanup on unload (coordinator.py)

```python
async def async_shutdown(self) -> None:
    # ... existing shutdown ...
    for ble_device in self._ble_devices.values():
        await ble_device.stop()
    self._ble_devices.clear()
```

### Step 4: Manifest + requirements (manifest.json)

```diff
  "requirements": [
    "aiohttp-retry>=2.8.3",
    "aiomqtt>=2.0.0",
+   "bleak-retry-connector>=3.0.0",
    "cryptography>=41.0.0"
  ],
```

No `bluetooth:` matchers needed (no BLE config entries). No `dependencies: ["bluetooth_adapters"]` needed (we conditionally import).

### Step 5: Options flow (config_flow.py) — Optional

```python
vol.Optional(CONF_ENABLE_BLE, default=True): bool,
```

Skip this for the initial implementation — just enable BLE automatically. Add the toggle if users report issues.

### Step 6: Tests (test_coordinator.py additions)

```python
class TestBleTransportDispatch:
    async def test_ble_used_when_available(self, coordinator, mock_ble_device):
        """Commands route through BLE when device has BLE transport."""
        coordinator._ble_devices["AA:BB:CC:DD:EE:FF:00:11"] = mock_ble_device
        await coordinator.async_control_device("AA:BB:CC:DD:EE:FF:00:11", PowerCommand(True))
        mock_ble_device.turn_on.assert_awaited_once()

    async def test_rest_fallback_on_ble_failure(self, coordinator, mock_ble_device, mock_api):
        """Commands fall back to REST when BLE write fails."""
        mock_ble_device.turn_on.side_effect = BleakError("connection lost")
        coordinator._ble_devices["AA:BB:CC:DD:EE:FF:00:11"] = mock_ble_device
        await coordinator.async_control_device("AA:BB:CC:DD:EE:FF:00:11", PowerCommand(True))
        mock_api.control_device.assert_awaited_once()

    async def test_rest_used_when_no_ble(self, coordinator, mock_api):
        """Commands go straight to REST when no BLE device cached."""
        await coordinator.async_control_device("AA:BB:CC:DD:EE:FF:00:11", PowerCommand(True))
        mock_api.control_device.assert_awaited_once()

    async def test_ble_advertisement_creates_ble_device(self, coordinator):
        """BLE advertisement matching a cloud device creates a GoveeBLEDevice."""
        # ... inject fake advertisement ...
        assert "AA:BB:CC:DD:EE:FF:00:11" in coordinator._ble_devices
```

---

## Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| SKU-based matching fails for multiple same-SKU devices | Medium | Medium | MAC-prefix heuristic as tiebreaker (unproven but plausible). If both fail, skip auto-correlation for that device — user can manually associate later via options flow. Most users don't have multiple identical SKUs in BLE range. |
| Cloud device_id first-6-bytes ≠ BLE MAC (tiebreaker fails) | Medium | Low | Only affects the disambiguation tiebreaker for multiple same-SKU devices. Primary SKU matching is unaffected. Log a warning and skip BLE for that device. |
| BLE commands that fail silently (device accepts frame but doesn't act) | Low | Medium | REST fallback catches this: if user sees the command didn't work, MQTT state won't change, next poll reconciles. Consider a "verify after BLE write" option that sends REST poll after BLE. |
| `bleak-retry-connector` import fails on HA installs without system Bluetooth | Medium | Medium | Conditionally import: `try: from .api.ble import GoveeBLEDevice except ImportError: HAS_BLE = False`. Tests for this path. |
| Novel pattern makes HA core reviewers reject a future upstream PR | Medium | Low | Not a near-term concern (HACS-only). If upstreaming, we can split into dual-entry at that point — the command objects are transport-agnostic either way. |
| BLE writes + REST reads cause state race (BLE sets color, REST poll reports stale color before MQTT pushes new) | Medium | Low | Optimistic state after BLE write prevents stale display. MQTT push reconciles within 1-2 seconds. Same race already exists for REST-only commands. |
| `async_register_callback` for 3 name prefixes creates per-prefix callbacks | Low | Low | Each callback is an O(1) dict-match; overhead is negligible. |
| User wants BLE-only mode (no cloud API key) | Low | Medium | Out of scope for this phase. The dual-entry model handles this case. Can add it later as a secondary config flow if demanded. |
| `NotImplementedError` for non-BLE-capable commands (scenes, color_temp) silently falls through to REST | Low | Low | By design — the `except` in `async_control_device` catches it and falls through. Log at DEBUG level. |

### Open Questions

1. ~~**Verify cloud device_id → BLE MAC mapping**~~ — **RESOLVED (2026-04-09)**: Unconfirmed after checking Beshelmek, wez/govee2mqtt, Govee API docs, and Govee FAQ. Replaced with SKU-based matching from BLE advertising name as primary strategy, with MAC-prefix as unproven tiebreaker. See updated Q3 and Step 2 above.
2. **Which commands are BLE-capable?** Currently: power, brightness, RGB color. NOT: scenes, color_temp (would need BLE protocol extensions), work modes. Document the BLE-capable subset clearly.
3. **Should BLE availability be exposed as a device attribute?** An `is_ble_connected` binary sensor or diagnostic attribute would help users troubleshoot. Low priority.
4. **How to handle BLE devices NOT in the cloud device list?** (BLE-only lights that the user doesn't have a cloud API key for.) This is the "BLE-only mode" escape hatch — punt to a future release. The dual-entry model from the previous research doc handles this case.
5. **What if the BLE advertising name doesn't follow the `Prefix_SKU_Suffix` pattern?** Some older devices may use different naming (e.g., `ihoment_H6159` without a suffix, or just the model number). The SKU extraction function should be defensive and handle variations. If no SKU can be extracted, skip correlation for that advertisement.
6. **Multiple same-SKU devices in BLE range with no MAC tiebreaker** — should we prompt the user via an options flow to manually assign BLE↔cloud mappings? Low priority but worth planning the UX for.

---

## References

1. **Codebase: coordinator.py:586-632** — `async_control_device()` command gateway; primary injection point.
2. **Codebase: coordinator.py:211-225** — `register_observer` / `_notify_observers` observer pattern; BLE state updates use the same path as MQTT.
3. **Codebase: coordinator.py:272, 462** — per-device conditional pattern (`device.is_group`); template for `device.supports_ble`.
4. **Codebase: models/device.py:219-226** — `GoveeDevice` frozen dataclass; target for `ble_address` field.
5. **Codebase: api/ble.py** — `GoveeBLEDevice` class (Phase 1, already committed); fully reusable in the unified model.
6. **Codebase: api/client.py:299-341** — `control_device()` REST dispatch; BLE route is an alternative to this path.
7. **Codebase: api/mqtt.py:377** — MQTT state callback invocation; pattern for BLE state delivery.
8. **Codebase: __init__.py:66-216** — `async_setup_entry`; target for BLE advertisement subscription.
9. **HA dev docs — Bluetooth API** — `async_register_callback` requires no config entry; any integration can subscribe.
10. **Shelly integration** — https://github.com/home-assistant/core/tree/dev/homeassistant/components/shelly — dual-coordinator model (closest precedent, not exact match).
11. **Previous research: ble-direct-support.md** — Phase 1 (api/ble.py) design still valid; Phases 2-4 superseded by this doc.
12. **Previous research: ha-ble-integration-patterns.md** — `close_stale_connections_by_address`, `ble_device_callback`, led_ble patterns — all still applicable inside the unified coordinator.
