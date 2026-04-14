# Open Bug Triage: #60, #58, #53

**Date**: 2026-04-13
**Scope**: Three open bug issues — root-cause analysis and recommended fixes.
**Status**: Ready for implementation.

---

## Summary

Three distinct bugs, each with a clear root cause and low-to-moderate fix scope:

- **#60 (H707C BLE commands fail)** — BLE writes use fire-and-forget (`response=False`); "command succeeded" only means the HCI layer accepted it, not that the device received it. When the device is out of range, the next API poll (60s) overwrites optimistic state with `power=False`. Fix: add an optimistic grace period for power/brightness (~90s), mirroring the existing scene-preservation pattern.
- **#58 (BLE discovery missing H6053/H6076/H6126)** — Coordinator only registers `local_name` matchers (`Govee_*`, `ihoment_*`, `GBK_*`); no manufacturer-ID matchers for `0x8802`/`0x8803`. Fix: register `manufacturer_id` callbacks alongside name prefixes and add `bluetooth` matchers to `manifest.json`.
- **#53 (H80A1 segment updates drop)** — 14 segment commands fire in parallel via `asyncio`; Govee API silently rate-limits (returns HTTP 200 with empty body, triggering `Invalid JSON response`). Fix: serialize segment commands via an async queue with ~100ms spacing. API v2 already supports multi-segment arrays in one call — longer-term, batch.

All three fixes are localized and can ship independently.

---

## Research Questions

| # | Question | Answer |
|---|---|---|
| 1 | What does "BLE command succeeded" actually confirm? | HCI-layer acceptance only. `api/ble.py:337-343` uses `response=False` (write-without-response). No device ACK. |
| 2 | Why does the API poll overwrite BLE-driven optimistic state? | `coordinator.py:596-649` preserves scenes/DreamView/music-mode optimistic state, but not power/brightness. Those get overwritten unconditionally. |
| 3 | What manufacturer IDs do `0x8802`/`0x8803` represent? | Govee company identifiers. Currently unmatched — the coordinator only registers local_name matchers. |
| 4 | Does Govee API v2 support batched segment updates? | Yes. `SegmentColorCommand.segment_indices` is already a tuple and serializes as `"segment": [0,1,...]`. But every entity dispatches a single-segment command. |
| 5 | Why does the API return an empty body instead of 429? | Silent rate-limiting on Govee's side. `api/client.py:176-180` raises `GoveeApiError("Invalid JSON response: ")` when body is empty. No `Retry-After` header to throttle on. |
| 6 | Are segment commands throttled anywhere? | No. `coordinator.async_control_device()` (lines 713-769) has no queue, semaphore, or delay. 14 segments fire concurrently. |

---

## Findings

### Issue #60 — BLE "success" is misleading

- **Write path** (`api/ble.py:337-343`): `await client.write_gatt_char(..., response=False)` is fire-and-forget. Bleak returns the moment the HCI stack accepts the frame; the RF transmission and device reception are never confirmed.
- **Dispatch** (`coordinator.py:713-806`, commit `43a7d01`): BLE tried first when `device_id in self._ble_devices`. On success, `_apply_optimistic_update()` flips local state and the coordinator returns. Any exception falls through to REST.
- **Overwrite** (`coordinator.py:596-649`): `_fetch_device_state()` preserves `existing_state.active_scene` and certain mode-specific fields but copies `power_state` / `brightness` straight from the API response. When the device was out of range, API correctly reports `power=False`, clobbering the optimistic `True`.
- **Contradiction with CLAUDE.md**: The project already recognizes that some state (scenes) isn't reliably returned by the API and must be preserved optimistically. The same reasoning applies here, but it was never extended to the BLE-transport case.

### Issue #58 — Manufacturer-ID matchers missing

- **manifest.json**: No `bluetooth` key → no HA-managed discovery. All BLE wiring is runtime-only.
- **Runtime matchers** (`coordinator.py:265-294`): Only three `local_name` wildcards (`Govee_*`, `ihoment_*`, `GBK_*`). `BluetoothCallbackMatcher` supports `manufacturer_id` but it isn't used.
- **User's advertisements**: Devices *do* advertise `Govee_H6076_6642` etc., so in principle the name prefix should match. But the log shows no callback invocation. Likely cause: HA Bluetooth scanner delivers advertisements more reliably via `manufacturer_id` matchers than glob name matchers in some setups. Adding both is belt-and-braces.
- **SKU library** (`api/ble.py:78`): `SEGMENTED_MODELS = {"H6053", "H6072", "H6102", "H6199"}`. H6053 is present (correct). H6076, H6126 are non-segmented (correct to omit). No broader `SUPPORTED_MODELS` — the module is intentionally minimal.
- **Phase context**: Commits `f92c5ee` (Phase 1 library) and `43a7d01` (Phase 2 dispatch) deliberately deferred manifest discovery and manufacturer_id matchers to Phases 3-4. This issue is the trigger to pull that work forward.

### Issue #53 — Parallel segment dispatch exceeds API rate limit

- **Entity dispatch** (`platforms/segment.py:101-123`, `grouped_segment.py:101-126`): Each of 14 `GoveeSegmentLight` entities independently calls `coordinator.async_control_device()` with a single-segment `SegmentColorCommand`.
- **No serialization** (`coordinator.py:713-769`): `async_control_device()` hits the REST client directly. 14 concurrent calls in <100ms = ~140 req/s burst against a documented 100/min limit (≈1.67 req/s sustained).
- **Silent rate limit** (`api/client.py:176-180`): Govee returns HTTP 200 with empty body. `response.json()` raises `ContentTypeError` → wrapped as `GoveeApiError("Invalid JSON response: ")`. User's log shows six consecutive such errors, matching the expected N-1 failure pattern (one command fits the token bucket, rest get dropped).
- **Batching is possible** (`models/commands.py:216-234`): `SegmentColorCommand.segment_indices: tuple[int, ...]` — the payload format already supports `"segment": [0,1,2,...]`. Nothing currently constructs multi-index commands.
- **Single-segment updates work** because they fire alone, staying under the burst threshold.

---

## Compatibility Analysis

| Fix | Files Touched | Tests to Add/Update | Risk |
|---|---|---|---|
| #60 grace period | `models/state.py`, `coordinator.py` | `tests/test_coordinator.py`, `tests/test_models.py` | Low — contained to state reconciliation, no wire-protocol changes |
| #58 mfg-ID matchers | `coordinator.py`, `manifest.json`, optional `api/ble.py` | `tests/test_coordinator.py` (BLE subscription tests) | Low — pure additive discovery |
| #53 segment queue | `coordinator.py` | `tests/test_coordinator.py` (queue ordering, timing) | Low-moderate — changes dispatch path for one command type; must preserve optimistic-update semantics |

All three changes are forward-compatible with the existing clean-architecture layering (models / protocols / api / coordinator / entities).

---

## Recommendation

Implement all three fixes in the order **#28-style priority: user-impact first**:

1. **#53 (segment serialization)** — loud failure mode (visible errors), clear root cause, clear proven fix. Ship first.
2. **#60 (optimistic grace period)** — correctness fix with generalized value (also helps MQTT-offline and slow-API cases).
3. **#58 (BLE manufacturer matchers)** — enables more users to actually use BLE transport; complements #60.

---

## Implementation Sketch

### #53 — Segment command queue

**`custom_components/govee/coordinator.py`**:

In `__init__` (around line 113-146):
```python
self._segment_queue: asyncio.Queue[tuple[str, SegmentColorCommand]] = asyncio.Queue()
self._segment_dispatch_task: asyncio.Task | None = None
self._segment_dispatch_delay = 0.1  # 100ms → ~10 req/s, well under 100/min burst
```

Modify `async_control_device()` (line 713) to enqueue segment commands and apply the optimistic update immediately:
```python
if isinstance(command, SegmentColorCommand):
    self._apply_optimistic_update(device_id, command)
    self.async_set_updated_data(self._states)
    await self._segment_queue.put((device_id, command))
    if self._segment_dispatch_task is None or self._segment_dispatch_task.done():
        self._segment_dispatch_task = self.hass.async_create_task(
            self._process_segment_queue()
        )
    return True
```

Add a dispatcher:
```python
async def _process_segment_queue(self) -> None:
    while not self._segment_queue.empty():
        device_id, cmd = await self._segment_queue.get()
        device = self._devices.get(device_id)
        if device is None:
            continue
        try:
            await self._api_client.control_device(device_id, device.sku, cmd)
        except GoveeApiError as err:
            _LOGGER.error("Queued segment command failed for %s: %s", device_id, err)
        await asyncio.sleep(self._segment_dispatch_delay)
```

### #60 — Optimistic grace period

**`custom_components/govee/models/state.py`**:

Add timestamp field:
```python
last_optimistic_update: float | None = None
```

In every `apply_optimistic_*` method, set `self.last_optimistic_update = time.monotonic()`.

**`custom_components/govee/coordinator.py`** (`_fetch_device_state`, ~line 596):

```python
GRACE_PERIOD_SECONDS = 90
existing = self._states.get(device_id)
if (
    existing
    and existing.source == "optimistic"
    and existing.last_optimistic_update is not None
    and (time.monotonic() - existing.last_optimistic_update) < GRACE_PERIOD_SECONDS
):
    if state.power_state != existing.power_state:
        _LOGGER.debug(
            "Preserving optimistic power for %s during grace period "
            "(API=%s, optimistic=%s)",
            device_id, state.power_state, existing.power_state,
        )
        state.power_state = existing.power_state
        state.brightness = existing.brightness
```

### #58 — Manufacturer-ID matchers

**`custom_components/govee/coordinator.py`** (around line 284-291): Add in addition to the existing name-prefix loop:
```python
_GOVEE_MANUFACTURER_IDS = (0x8802, 0x8803)  # 34818, 34819
for mfg_id in _GOVEE_MANUFACTURER_IDS:
    unsubs.append(
        bluetooth.async_register_callback(
            self.hass,
            _on_ble_advertisement,
            BluetoothCallbackMatcher(manufacturer_id=mfg_id, connectable=True),
            BluetoothScanningMode.ACTIVE,
        )
    )
```

**`custom_components/govee/manifest.json`**: Add top-level `bluetooth` key:
```json
"bluetooth": [
  {"local_name": "Govee_*", "connectable": true},
  {"local_name": "ihoment_*", "connectable": true},
  {"local_name": "GBK_*", "connectable": true},
  {"manufacturer_id": 34818, "connectable": true},
  {"manufacturer_id": 34819, "connectable": true}
]
```

---

## Risks

- **#53 queue lifetime**: The queue task must be safely torn down on entry reload. Ensure `async_unload_entry` cancels `_segment_dispatch_task`.
- **#53 optimistic UI lag**: With 100ms spacing, 14 segments take ~1.4s end-to-end. Optimistic state is applied immediately so the UI feels instant; the actual device update trails. Confirm this matches user expectations vs. batching all 14 into one API call (alternative: use the array form of `SegmentColorCommand` — but requires entity-level aggregation).
- **#60 stale state**: If a device is genuinely turned off via the physical switch while the grace period is active, the UI will lag by up to 90s. Acceptable tradeoff vs. the current bug. Consider shortening the grace period (60s) if feedback suggests it's too long.
- **#58 manifest change**: Adding `bluetooth` matchers will trigger HA to offer discovery to existing users — harmless but may appear as repeated "discovered" notifications on first restart.

---

## References

- Issue #60: https://github.com/lasswellt/govee-homeassistant/issues/60
- Issue #58: https://github.com/lasswellt/govee-homeassistant/issues/58
- Issue #53: https://github.com/lasswellt/govee-homeassistant/issues/53
- Commit `f92c5ee`: Phase 1 BLE device library
- Commit `43a7d01`: Phase 2 multi-transport BLE dispatch
- CLAUDE.md "API Limitations & State Handling" — precedent for optimistic state preservation
- Siberiaodens reference fix (parallel context for #53): https://github.com/siberiaodens/govee-homeassistant/commit/73f9dc34a4149d7cb1a26e7f909d3d7cb1a14b62
