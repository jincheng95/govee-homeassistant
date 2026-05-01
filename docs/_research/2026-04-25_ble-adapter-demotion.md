# BLE Adapter Demotion — Issue #59 Follow-Up

<!-- no-registry: scope is a single coordinator + transport model change; no countable artifacts until implementation -->

## Summary

H6199 is on the BLE allowlist and correctly enrolled for BLE dispatch. But on systems
without a working BLE adapter (HA running in a VM without Bluetooth passthrough),
every command attempt burns ~40 s in `bleak_retry_connector` (4 attempts × ~10 s)
before `BleakNotFoundError` is raised and REST fallback fires. The root cause is that
the BLE dispatch gate checks only dict membership (`device_id in self._ble_devices`),
with no upfront adapter availability check and no failure counter to demote the device
to cloud-only. Two complementary fixes exist: (c) zero-cost upfront adapter check at
enrollment time, and (a+b) runtime consecutive-failure demotion for cases where the
adapter disappears after setup.

## Research Questions

**Q1: Where does the ~40 s delay come from?**
`api/ble.py:99` — `_MAX_CONNECT_ATTEMPTS = 4`. `bleak_retry_connector` uses ~10 s
per attempt. Total: 4 × 10 s = ~40 s. (Issue reports of ~2 min likely describe an
older build or additional retry layers.)

**Q2: Why isn't BLE skipped upfront when no adapter is present?**
`coordinator.py:959-963` — dispatch guard is `HAS_BLUETOOTH and device_id in self._ble_devices`.
`HAS_BLUETOOTH` is True when the bluetooth component loaded (even if no adapter is
physically present). `is_available` on `TransportHealth` is updated by staleness
refresh (`_refresh_ble_staleness`, line 342) but is never consulted at dispatch time.

**Q3: What per-device health state exists?**
`models/transport.py:20-46` — `TransportHealth` has `is_available`, `last_success_ts`,
`last_failure_ts`, `last_failure_reason`. No `consecutive_failures` counter and no
`demoted_until` field.

**Q4: What are the implementation hooks?**
Three complementary hooks — see Recommendation.

**Q5: Does this conflict with the prior silent-drop demotion design?**
No. The 2026-04-13 research documented state-reconciliation demotion for SKUs that
accept BLE writes at the HCI layer but silently ignore them (H6072, H61E1, etc.).
That's a different failure mode (no exception raised). The no-adapter case is
exception-based and is handled by (c) + (a+b) below.

## Findings

### BLE dispatch path

- `coordinator.py:959-963` — BLE-first; no quality gate beyond dict membership
- `coordinator.py:1049-1085` — `_try_ble_command`: broad `except Exception` → `_record_transport_failure` → returns False → REST fallback. Delay is consumed *inside* the exception path before returning.
- `_record_transport_failure` calls `mark_failure` on `TransportHealth` but `mark_failure` does not increment a consecutive counter

### What's missing

| Gap | Location |
|-----|----------|
| Upfront adapter count check | `coordinator.py:482-492` (enrollment) |
| `consecutive_failures` field | `models/transport.py:TransportHealth` |
| `demoted_until` field | `models/transport.py:TransportHealth` |
| Demotion check before dispatch | `coordinator.py:959` |

### HA adapter count API

`homeassistant.components.bluetooth.async_scanner_count(hass, connectable=True)` —
available since HA 2023.9. Returns int (number of connectable BLE adapters currently
registered). Returns 0 when no adapter is present or Bluetooth is disabled.

## Recommendation

Ship two complementary fixes in order of implementation cost:

### Fix 1 — Upfront adapter check at enrollment (½ day, zero ongoing latency)

In `coordinator.py` `_handle_ble_advertisement`, before adding to `_ble_devices`:
```python
from homeassistant.components import bluetooth as bt_component

if bt_component.async_scanner_count(self.hass, connectable=True) == 0:
    _LOGGER.info(
        "%s (%s) BLE advertisement seen but no connectable adapter — skipping BLE enrollment",
        sku, device_id,
    )
    return
```
Zero latency penalty. Limitation: adapters arriving after setup won't re-enable BLE
until integration reload (acceptable — edge case, user can reload).

### Fix 2 — Runtime consecutive-failure demotion (1 day, handles adapter disappearing)

**Step 1** — `models/transport.py` — extend `TransportHealth`:
```python
consecutive_failures: int = 0
demoted_until: datetime | None = None

def mark_failure(self, ...) -> None:
    # existing fields ...
    self.consecutive_failures += 1

def mark_success(self, ...) -> None:
    # existing fields ...
    self.consecutive_failures = 0

def is_demoted(self, now: datetime) -> bool:
    return self.demoted_until is not None and now < self.demoted_until

def demote(self, now: datetime, ttl: timedelta) -> None:
    self.demoted_until = now + ttl
```

**Step 2** — `coordinator.py` — new constants near top:
```python
BLE_DEMOTE_THRESHOLD = 3   # consecutive failures before demotion
BLE_DEMOTE_TTL = timedelta(hours=1)
```

**Step 3** — `coordinator.py` around line 959 — add demotion guard and demote-on-threshold:
```python
ble_health = self._transport_health.get(device_id, {}).get("ble")
ble_demoted = ble_health is not None and ble_health.is_demoted(datetime.now(UTC))
if HAS_BLUETOOTH and device_id in self._ble_devices and not ble_demoted:
    if await self._try_ble_command(device_id, command):
        ...
        return True
    if ble_health and ble_health.consecutive_failures >= BLE_DEMOTE_THRESHOLD:
        ble_health.demote(datetime.now(UTC), BLE_DEMOTE_TTL)
        _LOGGER.warning(
            "%s BLE demoted to cloud-only for %s after %d consecutive failures",
            device_id, BLE_DEMOTE_TTL, BLE_DEMOTE_THRESHOLD,
        )
```

## Implementation Sketch

Files to change:
1. `custom_components/govee/models/transport.py` — add `consecutive_failures`, `demoted_until`, `is_demoted()`, `demote()`, extend `mark_failure`/`mark_success`
2. `custom_components/govee/coordinator.py` — add `BLE_DEMOTE_THRESHOLD`, `BLE_DEMOTE_TTL` constants; add upfront adapter check in `_handle_ble_advertisement`; add demotion guard + demote-on-threshold in `async_control_device`
3. `tests/` — new `test_coordinator_ble.py` with demotion scenario (see test sketch in raw findings)

Ship Fix 1 alone in the next patch (quickest user relief); Fix 2 in follow-up.

## Risks

- `async_scanner_count` is HA-internal; confirm availability in minimum supported HA version for this integration before shipping Fix 1
- Demotion TTL of 1 hour means BLE commands are skipped for 1 hour after 3 failures even if the adapter comes back online — acceptable trade-off; users can reload the integration to clear demotion state
- `TransportHealth` is a frozen dataclass — if it uses `@dataclass(frozen=True)`, the mutation methods need `object.__setattr__` or the class needs to become mutable; check before implementing

## References

- Issue #59, @craigo1975 comment (2026-04-16): BLE timeout log with traceback
- Issue #59, @at-9 comment (2026-04-16): "wasn't using BLE... no BT passthrough to VM"
- `docs/_research/2026-04-13_ble-demotion-issue-59.md` — prior silent-drop demotion design
- `docs/_research/2026-04-08_ble-direct-support.md` — full BLE architecture research
- `custom_components/govee/api/ble.py:91-99` — allowlist + retry constants
- `custom_components/govee/coordinator.py:959-963` — BLE dispatch gate
- `custom_components/govee/models/transport.py:20-46` — `TransportHealth`
