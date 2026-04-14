# HA Pattern Validation + Per-Device Transport Connectivity Entities

**Date**: 2026-04-13
**Scope**: (a) Validate the three bug fixes from `2026-04-13_open-bugs-triage.md` against Home Assistant developer docs and established integrations. (b) Design user-visible connectivity entities for API / MQTT / BLE status per device.
**Status**: Ready for implementation — corrects several non-idiomatic patterns from the earlier triage doc.

---

## Summary

Internet validation flagged three meaningful corrections to the earlier triage plan, and produced a concrete entity design for per-device transport visibility:

1. **#53 segment serialization** — drop the hand-rolled `asyncio.Queue` + `sleep(0.1)`. Use `homeassistant.helpers.debounce.Debouncer(cooldown=0.6, immediate=True)` plus a per-device `asyncio.Lock`. The 100ms sleep doesn't match the documented 100/min Govee limit (~600ms avg); `Debouncer` is the canonical primitive.
2. **#60 optimistic grace period** — pattern is correct ("partially optimistic" per HA architecture discussion #740), but **90s is too long**. Cut to ~10–15s, key reconciliation off MQTT push events rather than wall-clock, and set `always_update=False` on the coordinator. Don't set `assumed_state=True` for the whole entity — that's only for fully-optimistic devices.
3. **#58 BLE discovery** — manifest `bluetooth:` matchers and runtime `async_register_callback` are **complementary** (upstream `govee_ble` uses both). **Drop manufacturer ID `34818` (0x8802)** — not found in Nordic's company_ids DB nor in upstream `Bluetooth-Devices/govee-ble`. Keep `34819` (0x8803) and narrow with `manufacturer_data_start` to avoid false positives. Three-character glob rule applies: `Govee_*` is fine; shorter prefixes aren't.
4. **Connectivity entities** — add three `binary_sensor` entities per device (`api_connectivity`, `mqtt_connectivity`, `ble_connectivity`) with `BinarySensorDeviceClass.CONNECTIVITY` + `EntityCategory.DIAGNOSTIC`, gated behind a new opt-in option `CONF_EXPOSE_TRANSPORT_ENTITIES` (default off) to avoid entity explosion. Data source already exists in commit `d8f230c` as `extra_state_attributes`; we're promoting it to first-class entities with timestamps and failure tracking.

---

## Research Questions

| # | Question | Answer |
|---|---|---|
| 1 | Is a custom async queue + sleep the HA-idiomatic way to rate-limit? | No. `homeassistant.helpers.debounce.Debouncer(hass, logger, cooldown, immediate)` is the documented primitive. It provides lock-based concurrency and coalesces bursts. |
| 2 | Is pre-dispatch optimistic state OK for a coordinator entity? | Yes — HA architecture discussion #740 explicitly describes "partially optimistic" as this pattern. Use `async_write_ha_state()`, not `async_schedule_update_ha_state(True)`. |
| 3 | Is 90s the right grace period for power/brightness? | No. Community/community precedent expects ~5s UI feedback. Target 10–15s max; short-circuit on MQTT confirmation. |
| 4 | Are manifest `bluetooth:` matchers and runtime callbacks duplicative? | Complementary. Manifest drives config-flow discovery for *unconfigured* devices; runtime callbacks feed live adverts into the *configured* entry. Upstream `govee_ble` uses both. |
| 5 | Is `0x8802` a real Govee manufacturer ID? | Unverified. Not in Nordic SIG DB nor `Bluetooth-Devices/govee-ble`. Drop until we observe it in a capture. `0x8803` is de-facto Govee (used by H5127). |
| 6 | Should transport connectivity be a `sensor` or `binary_sensor`? | `binary_sensor` with `device_class=CONNECTIVITY`. ENUM sensor only makes sense for >2 states; for per-transport the boolean plus `unavailable` is sufficient. |
| 7 | How should entities get connectivity updates? | Event-driven via existing `async_set_updated_data` + observer pattern. Plus a 60s housekeeping task to age-out stale BLE adverts. Avoid independent polling by entities. |

---

## Findings

### HA-idiomatic rate limiting (corrects #53)

- `homeassistant.helpers.debounce.Debouncer(hass, logger, cooldown, immediate, function=..., background=...)` docstring on dev branch explicitly says it is "appropriate for rate limit calls to a specific command." Provides lock-based concurrency and coalesces pending calls via `_execute_at_end_of_timer`.
- `DataUpdateCoordinator` accepts `request_refresh_debouncer=Debouncer(...)`; the 2025-10 coordinator retrigger update lets refresh requests queue while one is in flight.
- HA integration-fetching-data docs: "Home Assistant manages parallel requests through semaphores per integration… customize this by defining `PARALLEL_UPDATES` in your platform module."
- 100ms fixed sleep double-penalizes: `aiohttp-retry` in `api/client.py` already handles 429 backoff. A cooldown closer to the documented 600ms average-per-request is correct.
- `async_write_ha_state()` writes optimistic state immediately; `async_schedule_update_ha_state(True)` triggers a coordinator refresh — wrong tool for optimistic writes.

### Optimistic state in HA (corrects #60)

- No first-class `grace_period` API exists. The pattern is documented in architecture discussion #740 as "partially optimistic": optimistic on command, overwrite on next poll/push.
- `Entity.assumed_state = True` is a **frontend signal** that HA can't confirm state — appropriate for devices with no read-back. Our devices *do* have readback (eventually), just with lag. So don't globally set `assumed_state`.
- Event-driven primitives: `homeassistant.helpers.event.async_call_later(hass, delay, cb)` for one-shot reconciliation; `coordinator.async_set_updated_data(data)` for immediate push.
- MQTT light docs confirm the pattern: "the light will immediately change state after every command."
- Neither `govee_ble` nor `govee_light_local` has a 90s precedent — both are `local_push`, relying on advertisement/UDP cadence (~every few seconds).
- **Corrected design**: grace = `min(2 * poll_interval, 15)` seconds; any MQTT push that confirms (or contradicts) the optimistic value clears the window immediately. Also add `always_update=False` to the coordinator so unchanged data doesn't re-write entity state.

### BLE manifest + runtime discovery (corrects #58)

- Manifest `bluetooth:` schema (developers.home-assistant.io/docs/creating_integration_manifest#bluetooth): fields `local_name`, `service_uuid`, `service_data_uuid`, `manufacturer_id`, `manufacturer_data_start`, `connectable`. None individually required. Rule: "Your integration is discovered if all items of any of the specified matchers are found in the Bluetooth data."
- Constraint: "Matches for `local_name` may not contain any patterns in the first three (3) characters." Our `Govee_*`, `ihoment_*`, `GBK_*` all comply (5, 7, 3 fixed chars respectively).
- Manifest vs runtime: complementary. Manifest triggers HA's discovery/config-flow UI for unconfigured devices. Runtime `bluetooth.async_register_callback` feeds live adverts into an already-configured entry. Upstream `govee_ble`, `homekit_controller`, `bthome` all register both.
- Manufacturer-ID verification:

  | ID (dec) | ID (hex) | Nordic SIG DB | Bluetooth-Devices/govee-ble | Status |
  |---|---|---|---|---|
  | 34818 | 0x8802 | No | No | Unverified — do not ship |
  | 34819 | 0x8803 | No | Yes (H5127 presence) | OK — de-facto Govee |
  | 61320 | 0xEF88 | No | Related family | OK |

- Upstream `homeassistant/components/govee_ble/manifest.json` precedent: `{"manufacturer_id": 34819, "manufacturer_data_start": [236, 0, 0, 1]}`. Narrow with `manufacturer_data_start` to cut false positives.

### Connectivity entities — current state (commit `d8f230c`)

- `custom_components/govee/diagnostics.py:64-69` — transport flags exposed in the diagnostics download only.
- `custom_components/govee/entity.py:89-102` — every device entity exposes `transport_cloud_api`, `transport_mqtt`, `transport_ble` as `extra_state_attributes`. Not templatable without `state_attr()`, no timestamps, no failure reasons.
- `custom_components/govee/sensor.py` — two hub-level sensors: `GoveeRateLimitSensor` and `GoveeMqttStatusSensor` (enum `connected`/`disconnected`/`unavailable`). Nothing per-device.
- Coordinator tracking: `mqtt_connected` (`coordinator.py:222-225`), `is_ble_available(device_id)` (`:227-229`), `_ble_devices` populated at `:332-346`.
- Hooks for health tracking: REST at `async_control_device` (`:749-760`); BLE at `_try_ble_command` (`:771-805`); MQTT state flips at `api/mqtt.py:266,294,320` (no callback surface — consumers poll).

### Connectivity entities — HA conventions

- `binary_sensor` with `BinarySensorDeviceClass.CONNECTIVITY` (on=connected, off=disconnected). Docs: developers.home-assistant.io/docs/core/entity/binary-sensor/.
- `EntityCategory.DIAGNOSTIC` required so these don't clutter the default device card.
- Explicit `_attr_icon` (CONNECTIVITY auto-icons are plug/unplug, not what we want):
  - Cloud API: `mdi:cloud` / `mdi:cloud-off-outline`
  - MQTT: `mdi:cloud-sync`
  - BLE: `mdi:bluetooth` / `mdi:bluetooth-off`
- Companion `SensorDeviceClass.TIMESTAMP` entities for last-seen are idiomatic (precedent: `mobile_app`, `unifi`, `tplink`).
- Single-coordinator update pattern is HA's preferred flow — do not poll per-entity.

---

## Compatibility Analysis

| Change | Files Touched | New Deps | Risk |
|---|---|---|---|
| #53 Debouncer + Lock | `coordinator.py` | None (HA core) | Low — swap queue for documented helper |
| #60 Grace period (10–15s, MQTT-aware) | `coordinator.py`, `models/state.py` | None | Low |
| #58 manifest + runtime matchers | `manifest.json`, `coordinator.py` | None | Low — additive discovery |
| Connectivity entities (MVP) | new `binary_sensor.py`, `coordinator.py`, `models/state.py` or new `models/transport.py`, `config_flow.py`, `strings.json`, `translations/en.json` | None | Low-moderate — new platform |

All fixes align with HA `DataUpdateCoordinator` + `CoordinatorEntity` pattern. No framework upgrades required. Python 3.12+ already.

---

## Recommendation

Corrected implementation order:

1. **Connectivity-entity MVP** first — it doesn't depend on the other fixes and gives users immediate visibility into what's actually broken in their setup. This also makes it easier to verify fixes #60 and #58 in the field (users can see BLE flip from off → on after #58 ships).
2. **#53 fix** with `Debouncer` + per-device `asyncio.Lock`.
3. **#60 fix** with shortened grace period (10–15s), MQTT-aware reconciliation, and `always_update=False`.
4. **#58 fix** with manifest `bluetooth:` entries + runtime `manufacturer_id=0x8803` callback (drop 0x8802 until captured).

Rationale for reordering: shipping connectivity entities first means users can report with precise data ("BLE shows disconnected on H707C when outside"), validating #60 before we ship a speculative 90s grace period nobody can measure.

---

## Implementation Sketch

### #53 — Debouncer + per-device Lock

`custom_components/govee/coordinator.py`:

```python
from homeassistant.helpers.debounce import Debouncer

SEGMENT_COOLDOWN = 0.6  # ~matches 100 req/min Govee limit

# in __init__:
self._segment_locks: dict[str, asyncio.Lock] = {}
self._segment_debouncers: dict[str, Debouncer] = {}
```

In `async_control_device`, for `SegmentColorCommand`:
- Apply optimistic state and call `async_write_ha_state()` (via observer) immediately.
- Acquire per-device `Lock` and dispatch REST under the lock.
- Use a per-device `Debouncer(cooldown=SEGMENT_COOLDOWN, immediate=True, function=self._dispatch_segment)` to coalesce bursts; pending commands overwrite latest.
- Mirror the `active_scene` non-clobber guard so the post-REST poll doesn't overwrite still-pending segment optimistic state.

### #60 — Shortened grace period + MQTT-aware reconciliation

`custom_components/govee/models/state.py`:

```python
last_optimistic_update: float | None = None
```

Set `self.last_optimistic_update = time.monotonic()` in every `apply_optimistic_*` method.

`custom_components/govee/coordinator.py` (`_fetch_device_state`, ~line 596):

```python
OPTIMISTIC_GRACE = min(2 * self.update_interval.total_seconds(), 15)
existing = self._states.get(device_id)
if (
    existing
    and existing.source == "optimistic"
    and existing.last_optimistic_update is not None
    and (time.monotonic() - existing.last_optimistic_update) < OPTIMISTIC_GRACE
):
    if state.power_state != existing.power_state:
        state.power_state = existing.power_state
        state.brightness = existing.brightness
```

In `_on_mqtt_state_update` (`coordinator.py:509`), when an MQTT push lands: clear `last_optimistic_update` so the grace window ends early.

Also set `always_update=False` in the `DataUpdateCoordinator` constructor (`coordinator.py` `__init__`).

### #58 — manifest + runtime matchers

`custom_components/govee/manifest.json`:

```json
"bluetooth": [
  {"local_name": "Govee_*", "connectable": true},
  {"local_name": "ihoment_*", "connectable": true},
  {"local_name": "GBK_*", "connectable": true},
  {"manufacturer_id": 34819, "manufacturer_data_start": [236, 0], "connectable": true}
]
```

`custom_components/govee/coordinator.py` (around line 284-291): add runtime callback registration:

```python
_GOVEE_MANUFACTURER_IDS = (34819,)  # 0x8803; 34818/0x8802 unverified
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

### Connectivity entities — MVP

1. **`models/transport.py`** (new):
```python
@dataclass
class TransportHealth:
    transport: Literal["cloud_api", "mqtt", "ble"]
    is_available: bool = False
    last_success_ts: datetime | None = None
    last_failure_ts: datetime | None = None
    last_failure_reason: str | None = None
```

2. **`coordinator.py`**:
   - `__init__`: `self._transport_health: dict[str, dict[str, TransportHealth]] = {}`
   - `_discover_devices`: init three `TransportHealth` per device.
   - `async_control_device` REST branch (`:749-760`): stamp `cloud_api`.
   - `_try_ble_command` (`:771-805`): stamp `ble` on success, mark `is_available=False` + reason on failure.
   - `_handle_ble_advertisement` (`:332-346`): stamp `ble` last_success_ts on each advert.
   - MVP: poll `mqtt_client.connected` in `_async_update_data` and refresh all devices' `mqtt.is_available`.
   - Expose `get_transport_health(device_id, transport) -> TransportHealth`.

3. **`binary_sensor.py`** (new platform):
```python
PLATFORMS += [Platform.BINARY_SENSOR]  # in __init__.py

class _GoveeTransportConnectivity(GoveeEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, device_id, transport, icon):
        super().__init__(coordinator, device_id)
        self._transport = transport
        self._attr_icon = icon
        self._attr_translation_key = f"{transport}_connectivity"
        self._attr_unique_id = f"{device_id}_{transport}_connectivity"

    @property
    def is_on(self) -> bool:
        return self.coordinator.get_transport_health(
            self._device_id, self._transport
        ).is_available
```

Subclasses for cloud_api / mqtt / ble with appropriate icons.

4. **`config_flow.py`** options schema: add `CONF_EXPOSE_TRANSPORT_ENTITIES` (default `False`). `binary_sensor.async_setup_entry` early-returns when false.

5. **`strings.json` + `translations/en.json`**: add entity translations.

### Connectivity entities — Follow-up
- `on_connection_change` callback in `GoveeAwsIotClient` for instant MQTT flips (remove polling lag).
- Failure counters + `last_failure_reason` as `extra_state_attributes`.
- 60s staleness sweeper for BLE via `async_track_time_interval`.
- Optional `SensorDeviceClass.TIMESTAMP` last-seen sensors.
- Optional repairs issue when all three transports are down for >5 min.

---

## Risks

- **Debouncer behavior under heavy scene change**: `immediate=True` fires first call immediately, debounces subsequent calls during cooldown. If a user triggers a scene that changes every segment, only the first segment lands immediately; the rest coalesce to one call. Mitigation: per-segment or per-(device, segment) debouncers if this becomes visible.
- **Grace period still masks real failures**: a 10-15s window is small but will mask brief device outages. Accept the tradeoff vs. UI flipflop.
- **`0x8803` false positives**: other vendors occasionally reuse unregistered manufacturer IDs. `manufacturer_data_start: [236, 0]` (0xEC 0x00) narrows significantly — matches observed Govee adverts in issue #58.
- **Entity explosion**: 3× binary_sensors × N devices can mean 60+ diagnostic entities. Opt-in via `CONF_EXPOSE_TRANSPORT_ENTITIES` and `EntityCategory.DIAGNOSTIC` (hidden from default card) mitigate.
- **Manifest `bluetooth:` entries trigger discovery UI**: existing configured users may see discovery notifications on first restart. Harmless but expect forum reports.

---

## References

- [Integration manifest — Bluetooth](https://developers.home-assistant.io/docs/creating_integration_manifest#bluetooth)
- [Bluetooth APIs](https://developers.home-assistant.io/docs/core/bluetooth/api/)
- [Fetching data](https://developers.home-assistant.io/docs/integration_fetching_data/)
- [Coordinator retrigger (2025-10)](https://developers.home-assistant.io/blog/2025/10/05/coordinator-retrigger/)
- [`homeassistant/helpers/debounce.py`](https://github.com/home-assistant/core/blob/dev/homeassistant/helpers/debounce.py)
- [`homeassistant/components/govee_ble/manifest.json`](https://github.com/home-assistant/core/blob/dev/homeassistant/components/govee_ble/manifest.json)
- [Bluetooth-Devices/govee-ble parser](https://github.com/Bluetooth-Devices/govee-ble/blob/main/src/govee_ble/parser.py)
- [Nordic bluetooth-numbers-database](https://github.com/NordicSemiconductor/bluetooth-numbers-database)
- [MQTT Light optimistic mode](https://www.home-assistant.io/integrations/light.mqtt/)
- [Architecture discussion #740 — optimistic states](https://github.com/home-assistant/architecture/discussions/740)
- [Binary sensor developer docs](https://developers.home-assistant.io/docs/core/entity/binary-sensor/)
- [Entity generic properties (EntityCategory)](https://developers.home-assistant.io/docs/core/entity/#generic-properties)
- [Binary sensor user docs (device classes)](https://www.home-assistant.io/integrations/binary_sensor/)
- [MQTT availability topic / LWT pattern](https://www.home-assistant.io/integrations/binary_sensor.mqtt/)
- Prior research: [`docs/_research/2026-04-13_open-bugs-triage.md`](./2026-04-13_open-bugs-triage.md)
