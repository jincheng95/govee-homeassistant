# BLE Demotion for Silently-Failing Transport (#59)

**Date**: 2026-04-13
**Scope**: Issue #59 — WiFi-enabled Govee devices whose BLE advertisements are heard by HA but whose BLE commands go unacknowledged. The Phase 2 multi-transport dispatch (commit `43a7d01`, v2026.4.2/4.3) prefers BLE on every call and never falls back to REST when BLE "succeeds" at the HCI layer but the device ignores the write. v2026.4.4's optimistic grace period hides the UI flipflop but does not deliver the command.
**Status**: Ready for implementation. Recommends a hybrid fix: SKU allowlist (ship now) + state-reconciliation demotion (follow-up).

---

## Summary

BLE writes go out via `write_gatt_char(..., response=False)` — fire-and-forget at the HCI layer. `_try_ble_command` returns `True` the moment the write completes locally, regardless of whether the device was in RF range or even supports the command. Four SKUs reported in #59 (H6072, H61E1, H60B0, H612A) advertise BLE but don't respond to our current command set, so every control attempt silently disappears. v2026.4.4's 10–15s grace period prevents the UI from flipping back but doesn't trigger a REST retry — the command is lost. Recommend shipping a small SKU allowlist immediately (keeps BLE enabled only for verified SKUs) while building a state-reconciliation demotion mechanism that auto-disables BLE for any device whose BLE "successes" aren't confirmed by the next poll or push.

---

## Research Questions

| # | Question | Answer |
|---|---|---|
| 1 | What does `_try_ble_command` actually verify? | Nothing beyond local HCI acceptance. `api/ble.py:337-343` uses `response=False`. There is no ACK, no GATT read-back, no device-side confirmation. |
| 2 | Why isn't REST attempted after a silent BLE failure? | `coordinator.py:931-935`: any `True` return from `_try_ble_command` returns immediately without REST fallback. There's no "verify and retry" path. |
| 3 | Which SKUs are affected? | Per user reports: H6072, H61E1, H60B0, H612A (broken). Works: H605C (does not advertise BLE), H6199 (slow but functional). All broken SKUs advertise BLE and get enrolled in `self._ble_devices` on first advert. |
| 4 | Is there any BLE allowlist or quality gate today? | No. Any advertisement matching `Govee_*`/`ihoment_*`/`GBK_*` or manufacturer_id 0x8803 enrolls the device for BLE command dispatch. `SEGMENTED_MODELS` only determines encoding, not whether to attempt BLE. |
| 5 | Does v2026.4.4's grace period help? | Only cosmetically. It preserves the optimistic state across one poll cycle so the UI doesn't flipflop. The command still didn't reach the device. |
| 6 | Why did "new API key" appear to fix it for one user? | Almost certainly coincidental. API key is only used by REST; BLE dispatch doesn't touch it. The restart or reconfigure likely masked the issue temporarily (adapter reset, proximity change, or re-enrollment). |

---

## Findings

### BLE "success" ≠ device received the command

`api/ble.py:337-343`:
```python
async def _write(self, frame: bytes) -> None:
    async with self._lock:
        client = await self._ensure_connected()
        await client.write_gatt_char(
            WRITE_CHARACTERISTIC_UUID, frame, response=False
        )
```

`response=False` means **write-without-response**: Bleak returns as soon as the HCI layer queues the frame. No ACK is required from the device. For out-of-range or non-command-capable BLE peripherals this returns success while the frame silently drops on the RF layer.

`coordinator.py:1020-1056` wraps the write in try/except and returns True on any non-exception path. There is no positive confirmation that the device processed the command.

### Dispatch preference is unconditional

`coordinator.py:931-935`:
```python
if HAS_BLUETOOTH and device_id in self._ble_devices:
    if await self._try_ble_command(device_id, command):
        self._apply_optimistic_update(device_id, command)
        self.async_set_updated_data(self._states)
        return True
    # BLE failed — fall through to REST
```

BLE is tried first every time and wins every time `_try_ble_command` returns True. There's no per-device quality signal that can disable BLE for devices where it's known to not work. The Phase 2 multi-transport design assumed BLE success = command delivered, which this issue disproves.

### Affected SKUs are all BLE-advertising RGBIC strips/lamps

| SKU | Type | In `SEGMENTED_MODELS` | Reported | Likely root cause |
|---|---|---|---|---|
| H6072 | RGBICWW Floor Lamp | Yes | Broken | Advertises BLE but doesn't accept our command frames |
| H61E1 | LED Strip Light M1 | No | Broken | Same |
| H60B0 | Uplighter Floor Lamp | No | Broken | Same |
| H612A | Sofa Strip Light | No | Broken | Same |
| H605C | RGBIC TV Backlight | No | Works | Doesn't advertise BLE; stays cloud-only |
| H6199 | RGBIC | Yes | Slow (~10s) | BLE works but marginal RF |

The pattern: **advertisement-capable ≠ command-capable**. The Phase 1 BLE library was validated against a narrow SKU set (likely H6072/H6199/H6102/H6053) and the command frames work for those — but enrollment happens for any Govee SKU that advertises.

### v2026.4.4 optimistic grace period is a UX patch, not a fix

The grace period (`OPTIMISTIC_GRACE_CAP_SECONDS=15`) preserves `state.power_state` during a window after a control command so the API poll can't flip it back. That's correct for the "device briefly out of BLE range" case where the command does eventually land. It does **not** retry the command via REST. For #59 devices that will never receive BLE, the grace window expires and the user sees the correct ("off") state — but their "turn on" command was lost.

---

## Compatibility Analysis

| Change | Files | Risk | User impact |
|---|---|---|---|
| SKU allowlist (ship immediately) | `api/ble.py`, `coordinator.py` (enroll gate) | Low | Most users will lose BLE acceleration temporarily until their SKU is added. Acceptable trade-off for correctness. |
| State-reconciliation demotion | `models/transport.py`, `coordinator.py`, `models/state.py` | Medium | Preserves BLE for devices where it works; auto-disables for devices where it doesn't. Requires one or two "failed" commands before demotion, so first commands may still be lost. |
| Options toggle "prefer BLE transport" | `config_flow.py`, `coordinator.py` | Low | Gives paranoid users an escape hatch. |

All three changes compose cleanly. None require framework or dependency changes.

---

## Recommendation

Ship **both**:

1. **Immediate: SKU allowlist for BLE enrollment.** Any device whose SKU isn't on a small, verified list stays cloud-only. Ships to master in 24 hours. Users who report BLE working for additional SKUs get them added next release. This prevents further #59 reports without shipping speculative auto-demotion logic.

2. **Follow-up: State-reconciliation demotion.** After any optimistic BLE command, verify the next MQTT push or REST poll agrees with the optimistic state. If three consecutive commands for a device show a post-command mismatch, demote BLE for that device until the next HA restart (or a configurable TTL). Resets on agreement. The existing `TransportHealth` dataclass already carries failure metadata — add three counters and one timestamp.

Do **not** rely solely on a config toggle. Most users won't know to flip it, and the default behavior needs to be correct.

### Comparison matrix

| Approach | Correctness | Latency on broken SKUs | User config | Code complexity |
|---|---|---|---|---|
| Allowlist | ✅ Perfect for listed SKUs | N/A — BLE skipped | None | Trivial |
| State-reconciliation | ⚠️ 1-3 lost commands then demoted | ~2-3 poll cycles | None | Moderate |
| Options toggle | ❌ Default still broken | Unchanged | Manual | Trivial |

---

## Implementation Sketch

### Step 1 — SKU allowlist (ships now)

**`custom_components/govee/api/ble.py`** (after `SEGMENTED_MODELS` at line 78):
```python
# SKUs with verified working BLE command dispatch. Devices not in this set
# remain cloud-only even if they advertise BLE (issue #59 — many SKUs
# advertise BLE but silently drop our command frames).
BLE_COMMAND_SUPPORTED_MODELS: frozenset[str] = frozenset({
    "H6199",  # RGBIC — confirmed working (slow but reliable)
    # Additional SKUs added as users confirm reliable BLE dispatch.
})
```

**`custom_components/govee/coordinator.py`** (inside `_handle_ble_advertisement`, before the `self._ble_devices[matched_id] = GoveeBLEDevice(...)` block):
```python
if ble_sku not in BLE_COMMAND_SUPPORTED_MODELS:
    # Keep the device cloud-only. It will still update advertisements
    # (so the connectivity sensor shows the BLE adapter is seeing it)
    # but commands won't be dispatched over BLE.
    return
```

Import `BLE_COMMAND_SUPPORTED_MODELS` alongside `SEGMENTED_MODELS` at the top of coordinator.py.

### Step 2 — State-reconciliation demotion (follow-up)

**`custom_components/govee/models/transport.py`** — add to `TransportHealth`:
```python
# Consecutive state-poll disagreements after a "successful" optimistic
# write via this transport. Reset on agreement. Used to auto-demote BLE
# when the device silently drops commands (issue #59).
mismatch_count: int = 0
demoted_until: datetime | None = None
```

Add a helper:
```python
def record_mismatch(self, now: datetime, threshold: int = 3,
                    ttl: timedelta = timedelta(hours=1)) -> bool:
    self.mismatch_count += 1
    if self.mismatch_count >= threshold:
        self.demoted_until = now + ttl
        return True
    return False

def record_agreement(self) -> None:
    self.mismatch_count = 0
```

**`custom_components/govee/coordinator.py`**:

After applying an optimistic write via BLE, stamp what we expect:
```python
# around line 931 (after _try_ble_command succeeds)
state = self._states.get(device_id)
if state:
    state.pending_ble_verification = self._extract_verifiable_field(command)
```

In `_fetch_device_state` (after cloud poll) and in `_on_mqtt_state_update`:
```python
pending = existing_state.pending_ble_verification
if pending:
    actual = getattr(state, pending.field)
    ble_health = self._transport_health[device_id]["ble"]
    if actual == pending.value:
        ble_health.record_agreement()
    else:
        if ble_health.record_mismatch(datetime.now(timezone.utc)):
            _LOGGER.warning(
                "Demoting BLE for %s after %d silent failures",
                device_id, ble_health.mismatch_count,
            )
    existing_state.pending_ble_verification = None
```

In `async_control_device`:
```python
ble_health = self._transport_health.get(device_id, {}).get("ble")
if (
    HAS_BLUETOOTH
    and device_id in self._ble_devices
    and not self._ble_demoted(ble_health)
):
    ...
```

Where `_ble_demoted` checks `ble_health.demoted_until` against `now`.

Verification scope: for #59 purposes, verifying `power_state` only is sufficient — it's the clearest binary signal. Brightness and color mismatches are noisier (RGB colorRgb=0 during scenes etc).

### Step 3 — Options toggle (optional, ship with Step 1)

Add `CONF_DISABLE_BLE_COMMANDS` in `config_flow.py` options. Defaults to `False`. When `True`, skip `_try_ble_command` entirely. Useful for users who know their setup needs cloud-only.

---

## Risks

- **SKU allowlist too aggressive.** If the initial allowlist is empty or near-empty, users currently benefiting from BLE (MQTT-offline fallback, low-latency commands) regress. Mitigation: seed with H6199 (reported working) and invite users to open issues reporting SKUs that work in the field.
- **State reconciliation false positives.** If the cloud API lags behind a real BLE command (e.g. AWS propagation delay), we might demote BLE on a device where it's actually working. Mitigation: high threshold (3+ mismatches), long TTL reset on agreement, and only demote when the mismatch is on `power_state` (the most reliable field).
- **Device-side caching.** Some Govee devices cache the last-seen cloud state. A BLE-only change may not be reflected in the cloud poll immediately. Mitigation: MQTT push is the primary verification signal when available.
- **Users with BLE-only (no cloud) scenarios.** Currently unsupported by the integration (API key is required). Not affected.

---

## References

- Issue #59: https://github.com/lasswellt/govee-homeassistant/issues/59
- Commit `43a7d01` (Phase 2 multi-transport): added BLE-first dispatch without verification.
- Commit `dfd0ba2` / v2026.4.4: grace period that masked the UI flipflop but didn't deliver commands.
- Prior research `2026-04-13_open-bugs-triage.md` (#60 analysis): identified that BLE writes are fire-and-forget at the HCI layer.
- `api/ble.py:337-343` — `_write()` uses `response=False`.
- `coordinator.py:931-935` — BLE-first dispatch with no quality gate.
- `coordinator.py:1020-1056` — `_try_ble_command()` records success on any non-exception path.
- HA Bluetooth ACK semantics: https://developers.home-assistant.io/docs/core/bluetooth/api/
