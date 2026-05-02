---
scope:
  - id: cf-2026-05-01-platform-parallel-updates
    unit: files
    target: 11
    description: |
      Add `PARALLEL_UPDATES = 0` module-level constant to every platform file
      so coordinator-pushed updates aren't throttled to 1 concurrent call.
      quality_scale.yaml:72 currently claims this is done but no PARALLEL_UPDATES
      constant exists in any platform module.
    acceptance:
      - grep_present:
          pattern: '^PARALLEL_UPDATES\s*=\s*0'
          min: 11
      - shell: 'test "$(grep -lE ''^PARALLEL_UPDATES'' custom_components/govee/light.py custom_components/govee/switch.py custom_components/govee/select.py custom_components/govee/sensor.py custom_components/govee/button.py custom_components/govee/fan.py custom_components/govee/humidifier.py custom_components/govee/binary_sensor.py custom_components/govee/number.py custom_components/govee/platforms/segment.py custom_components/govee/platforms/grouped_segment.py | wc -l)" = "11"'
  - id: cf-2026-05-01-inject-websession
    unit: files
    target: 2
    description: |
      Replace bare `aiohttp.ClientSession()` instantiation in api/client.py
      and api/auth.py with `homeassistant.helpers.aiohttp_client.async_get_clientsession(hass)`.
      Required by Platinum quality_scale rule `inject-websession`. ~6 call sites
      across 2 files (client.py:97; auth.py:241,290,357,448,499).
    acceptance:
      - grep_absent: 'aiohttp\.ClientSession\(\)'
      - grep_present:
          pattern: 'async_get_clientsession'
          min: 2
---

# Govee HACS Integration — Codebase Audit vs HA Best Practices (2026)

## Summary

Govee integration is structurally sound (Bronze + Silver compliant; Gold mostly compliant) but **falsely claims Platinum compliance** in `quality_scale.yaml` for two rules: `inject-websession` (no calls to `async_get_clientsession`; bare `aiohttp.ClientSession()` everywhere) and `parallel-updates` (no `PARALLEL_UPDATES` constant in any platform file). Three Critical issues, five High, five Medium, five Low. Strengths: correct generic typing on `ConfigEntry[GoveeCoordinator]` and `DataUpdateCoordinator[dict[str,GoveeDeviceState]]`, proper `entry.async_on_unload` lifecycle, correct `async_forward_entry_setups` (plural), no legacy `async_setup`, BLE connectable-only enrollment, blocking I/O properly offloaded via `run_in_executor`. Recommendation: ship a "platinum-fix" sprint addressing C1-C3 + H3-H5 before the next release; defer coordinator decomposition (H1) to a refactor sprint with test ratchet.

## Research Questions

**Q1: What HA dev requirements changed in 2024.11–2026.5 that affect this integration?**
A1: `async_forward_entry_setup` (singular) deprecated, removed 2025.6 — Govee already uses plural form (`__init__.py:209`). Explicit `option_flow=` deprecated 2025.12 — needs verification in `config_flow.py`. `_async_setup()` coordinator hook added 2024.8 for one-time async init. No breaking changes for any platform Govee uses (light/switch/sensor/binary_sensor/button/select/fan/humidifier/number).

**Q2: Bronze/Silver/Gold/Platinum rule status?**
A2: Bronze ✓, Silver ✓, Gold mostly ✓ (`stale-devices` marked todo but actually implemented; `docs-examples`, `docs-use-cases` truly todo), **Platinum: 2 false claims** — `inject-websession` and `parallel-updates`. `strict-typing` claim plausible but unverified by mypy run.

**Q3: Bluetooth integration alignment?**
A3: ✓ Connectable-only manifest entries; ✓ `bleak-retry-connector` declared; ✓ `async_scanner_count(connectable=True)` guard before BLE enrollment (`coordinator.py:506`). No central `bluetooth.async_get_scanner()` use, but enrollment goes through HA-managed scanner via `bluetooth_adapters` indirection.

**Q4: Translation, repairs, diagnostics correctness?**
A4: All four repair issue keys translated (`translations/en.json:238-254`). Diagnostics redacts API key, password, certs, tokens — but **MAC-format device IDs leak** (`diagnostics.py:41-43`).

**Q5: Type-safe ConfigEntry / DataUpdateCoordinator patterns?**
A5: ✓ Both correct: `type GoveeConfigEntry = ConfigEntry[GoveeCoordinator]` (`__init__.py:65`); `class GoveeCoordinator(DataUpdateCoordinator[dict[str, GoveeDeviceState]])` (`coordinator.py:131`).

**Q6: New manifest.json requirements 2026?**
A6: No `min_ha_version` field exists in HA spec; HACS uses `hacs.json` `homeassistant: "2024.11.0"` (already present). However Govee uses Python 3.12 `type` alias syntax which would `SyntaxError` on older HA — should add `min_ha_version` defensively (HA does honor it when present, even if not in main manifest spec).

## Findings

### Critical (block Platinum claim or risk regression)

**C1 — `inject-websession` Platinum claim is false.**
Bare `aiohttp.ClientSession()` is created in `api/client.py:97` and `api/auth.py:241,290,357,448,499`. Zero calls to `homeassistant.helpers.aiohttp_client.async_get_clientsession`. `quality_scale.yaml:153-154` claims this rule is `done`. Fix: thread `hass` (or session) into `GoveeApiClient.__init__` and `GoveeAuthClient.__init__`; pass `async_get_clientsession(hass)` from coordinator setup.

**C2 — `parallel-updates` claim is false.**
`quality_scale.yaml:72` says `DEFAULT_PARALLEL_UPDATES set per platform`. No `PARALLEL_UPDATES` module constant exists in any of the 11 platform files (`light.py`, `switch.py`, `select.py`, `sensor.py`, `button.py`, `fan.py`, `humidifier.py`, `binary_sensor.py`, `number.py`, `platforms/segment.py`, `platforms/grouped_segment.py`). Coordinator-pushed integrations should set `PARALLEL_UPDATES = 0`. Fix: add to every platform file.

**C3 — `hass.data[DOMAIN]` mixed with `entry.runtime_data` causes lifecycle leaks.**
`__init__.py:98-218` stores `KEY_IOT_CREDENTIALS`, `KEY_IOT_LOGIN_FAILED`, plus a redundant copy of the coordinator (`domain_data[entry.entry_id] = coordinator`, line 218) — alongside `entry.runtime_data = coordinator` (line 201). IoT creds in `hass.data` survive entry unload (cleanup only fires when `hass.data[DOMAIN]` is fully empty), so reconfigured entries inherit stale creds. Fix: persist creds in `entry.data` via `hass.config_entries.async_update_entry`; remove the `hass.data` coordinator copy.

### High (anti-patterns, functional)

**H1 — `coordinator.py` is a 1691-line monolith.**
Single `GoveeCoordinator` class spans: device discovery, REST polling, MQTT lifecycle, BLE advertisement handling, BLE passthrough, command dispatch, scene caching, optimistic state, transport health tracking, repair-issue creation, observer fan-out. Test friction is high; change-risk concentrates. Decompose into `BleManager`, `MqttManager`, `TransportHealthTracker`, `SceneCacheManager`. Industry contrarian evidence: large coordinators correlate with availability bugs (HA core issue [#157017](https://github.com/home-assistant/core/issues/157017)).

**H2 — Mutable state mutation without `async_set_updated_data` reassignment.**
`coordinator.py:547`: `existing_state.online = True` mutates a `@dataclass` (not frozen) in place. Subsequent `async_set_updated_data(self._states)` passes the same dict reference; HA's `CoordinatorEntity` listeners may not detect the mutation as a change. Fix: `self._states[matched_id] = dataclasses.replace(existing_state, online=True)`; then call `async_set_updated_data`.

**H3 — `manifest.json` missing `min_ha_version`.**
`__init__.py:65` uses Python 3.12 `type` alias syntax: `type GoveeConfigEntry = ConfigEntry[GoveeCoordinator]`. Older HA gives confusing `SyntaxError` at import. Fix: `"min_ha_version": "2024.1.0"` (Python 3.12 first shipped in HA 2024.1).

**H4 — MAC-format device IDs leak in diagnostics.**
`diagnostics.py:41-43` uses `device_id` (e.g., `03:9C:DC:06:75:4B:10:7C`) as both dict keys and values. `TO_REDACT` (line 18-29) excludes API keys, passwords, certs, tokens — but not device identifiers. MAC = PII per HA diagnostics privacy guidance. Fix: add `"device_id"`, `"mac"` to `TO_REDACT`.

**H5 — Detached task in repairs flow.**
`repairs.py:180`: `hass.async_create_task` to start reauth flow is not lifecycle-bound; if entry unloads while task is pending, no cancellation. Fix: call `hass.config_entries.flow.async_init(...)` directly (it returns an awaitable; no detached task needed).

### Medium (cleanup)

**M1** — `__init__.py:218` stores coordinator in `hass.data[DOMAIN][entry.entry_id]` redundantly; never read back. Remove.

**M2** — `__init__.py:379` calls `hass.states.async_remove()` before `entity_registry.async_remove()`; registry removal cascades automatically. Remove the manual call (race-prone).

**M3** — `sensor.py:25` imports `DeviceInfo` from `homeassistant.helpers.entity` with `# type: ignore[attr-defined]`. Canonical path is `homeassistant.helpers.device_registry.DeviceInfo` (used correctly in `entity.py:14`). Align.

**M4** — `__init__.py:213` uses `_services_setup` string sentinel in `hass.data` as registration guard. Fragile — failed loads leave the sentinel set. Fix: use `hass.services.has_service(DOMAIN, SERVICE_REFRESH_SCENES)`.

**M5** — `coordinator.py:961` uses `self.hass.async_create_task()`. Prefer `self.config_entry.async_create_background_task(hass, ...)` so the task is cancelled on entry unload.

### Low (polish)

**L1** — `manifest.json` includes `"ssdp": []` and `"zeroconf": []` (empty arrays). Absent keys are equivalent. Remove.

**L2** — `quality_scale.yaml:144-146` marks `stale-devices: todo`, but `_async_cleanup_orphaned_entities` (`__init__.py:277-418`) already implements stale-device removal. Update the comment to `done`.

**L3** — `entity.py:90-102` injects three transport keys into `extra_state_attributes` for every entity on every state write. Bloats state machine for installs with 50+ segment entities. The same data is already exposed via dedicated entities in `binary_sensor.py`. Gate behind `CONF_EXPOSE_TRANSPORT_ENTITIES` or remove from base.

**L4** — `manifest.json` has no top-level `quality_scale` field. While optional for HACS, HA core honors it; matches `quality_scale.yaml`. Add `"quality_scale": "silver"` (NOT `platinum` until C1 + C2 are fixed).

**L5** — `light.py:15`, `platforms/segment.py:13`, `platforms/grouped_segment.py:12` carry `# type: ignore[attr-defined]` on standard `homeassistant.components.light` imports. Suggests dev-env stub gap, not real type errors. Suppression masks future regressions. Investigate root cause.

### What's Done Well

- ✓ Generic typing: `ConfigEntry[GoveeCoordinator]` (`__init__.py:65`), `DataUpdateCoordinator[dict[str, GoveeDeviceState]]` (`coordinator.py:131`).
- ✓ `async_config_entry_first_refresh` invoked (`__init__.py:189`).
- ✓ No legacy `async_setup` module function — entry-only setup.
- ✓ `CoordinatorEntity["GoveeCoordinator"]` inheritance with correct generic param (`entity.py:24`).
- ✓ `has_entity_name = True` (`entity.py:34`) — Gold tier.
- ✓ Repairs translations complete in `translations/en.json:238-254`.
- ✓ Blocking I/O offloaded: SSL context creation (`api/mqtt.py:237-240`) and temp-dir cleanup (`api/mqtt.py:160-168`) both via `run_in_executor`.
- ✓ `entry.async_on_unload` for BLE unsubs and update listener (`__init__.py:206,221`).
- ✓ `async_unload_entry` complete: platforms, coordinator, hass.data, services (`__init__.py:226-252`).
- ✓ `async_forward_entry_setups` (plural) — already migrated (`__init__.py:209`).
- ✓ `ConfigFlowResult` typing on all flow steps (`config_flow.py:17`).
- ✓ BLE connectable-only enrollment with `connectable=True` guard (`coordinator.py:506`).
- ✓ Diagnostics redacts `api_key`, `password`, certs, tokens (`diagnostics.py:18-29`).

## Compatibility Analysis

| Dimension | Status | Notes |
|---|---|---|
| HA min version | 2024.11.0 | Matches `hacs.json`. Should add `manifest.min_ha_version: "2024.1.0"` defensively. |
| Python | 3.12+ | Hard requirement (uses `type` alias). |
| async-only | ✓ | All I/O async; offloaded blocking calls correctly. |
| Manifest schema | ✓ + cleanup | Required fields present; remove empty `ssdp`/`zeroconf`. |
| Bluetooth platform | ✓ | Connectable-only, `bleak-retry-connector`, manifest patterns correct. |
| MQTT | aiomqtt 2.0 | No dependency on core MQTT integration; isolated from core MQTT reconnect bugs. |
| 2025.6 deprecations | ✓ | `async_forward_entry_setups` (plural) already used. |
| 2025.12 deprecations | ⚠ verify | `option_flow=` explicit setting deprecation — confirm `config_flow.py` does not pass `config_entry` explicitly to `OptionsFlow.__init__`. |
| Quality scale gold | mostly | `stale-devices` actually implemented; only `docs-examples`/`docs-use-cases` are real todos. |
| Quality scale platinum | ✗ | C1 (websession) + C2 (parallel-updates) block. |

## Recommendation

Ship a single "platinum-fix" sprint covering C1–C3 + H3–H5 before the next release. Estimated: ~2 days. Defer H1 (coordinator decomposition) to a separate refactor sprint behind a test-coverage ratchet — it's the highest-ROI maintainability win but largest blast radius. M1–M5 + L1–L5 are bundle-able cleanups for the same sprint.

Order of operations:
1. **C2 + L4** — add `PARALLEL_UPDATES = 0` to all 11 platforms; set `manifest.quality_scale: silver` (truthful claim).
2. **C1** — refactor `GoveeApiClient` / `GoveeAuthClient` to accept `hass` (or session); pass `async_get_clientsession(hass)` from setup.
3. **C3 + M1** — collapse `hass.data` usage; persist IoT creds in `entry.data`; remove redundant coordinator copy.
4. **H3** — add `manifest.min_ha_version: "2024.1.0"`.
5. **H4** — extend `TO_REDACT` with `"device_id"`, `"mac"`; redact dict keys.
6. **H5** — replace detached task with awaited `flow.async_init`.
7. **H2** — apply `dataclasses.replace` pattern; add regression test for state propagation.
8. **M2–M5 + L1–L3, L5** — cleanup batch.
9. **Update `quality_scale.yaml`**: revise `inject-websession` and `parallel-updates` from `done` → claim only after fix; mark `stale-devices: done`.
10. **Future sprint**: H1 coordinator split (`BleManager`, `MqttManager`, `TransportHealthTracker`, `SceneCacheManager`).

## Implementation Sketch

**C1 — Inject websession:**
```python
# api/client.py
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

class GoveeApiClient:
    def __init__(self, hass: HomeAssistant, api_key: str, ...):
        self._session = async_get_clientsession(hass)
        # remove all `aiohttp.ClientSession()` creation
```
Same in `api/auth.py`. Coordinator/setup passes `hass`.

**C2 — Parallel updates:**
```python
# top of light.py, switch.py, select.py, sensor.py, button.py,
# fan.py, humidifier.py, binary_sensor.py, number.py,
# platforms/segment.py, platforms/grouped_segment.py
PARALLEL_UPDATES = 0
```

**C3 — Drop hass.data:**
```python
# __init__.py — store IoT creds in entry.data
hass.config_entries.async_update_entry(
    entry, data={**entry.data, KEY_IOT_CREDENTIALS: creds}
)
# remove domain_data[entry.entry_id] = coordinator (line 218)
# remove KEY_IOT_CREDENTIALS / KEY_IOT_LOGIN_FAILED from hass.data path
```

**H2 — Immutable state update:**
```python
# coordinator.py:547
self._states[matched_id] = dataclasses.replace(existing_state, online=True)
self.async_set_updated_data(self._states)
```

**H3 — manifest.json:**
```json
{
  "domain": "govee",
  "min_ha_version": "2024.1.0",
  "quality_scale": "silver",
  ...
}
```

**H4 — diagnostics:**
```python
# diagnostics.py
TO_REDACT = {..., "device_id", "mac"}
# Use async_redact_data which handles nested keys. For dict-keyed-by-MAC,
# explicitly hash or redact keys before passing to async_redact_data.
```

## Risks

- **MQTT reconnect (cross-cutting):** HA core MQTT has documented reconnect failures (issues [#50892](https://github.com/home-assistant/core/issues/50892), [#132985](https://github.com/home-assistant/core/issues/132985)). Govee uses `aiomqtt` directly (not core MQTT), so it does not inherit the bug — but should add an explicit health check / reconnect-on-failure handler regardless. Audit `api/mqtt.py:455` lifecycle: does it retry on broker drop? If not, this becomes a Silver `log-when-unavailable` issue.

- **Listener leak risk:** Govee's observer fan-out (`coordinator.register_observer`) requires every observer to be deregistered on entry unload. Verify each platform's `async_will_remove_from_hass` (or equivalent) calls `unregister_observer`. Cross-reference HA core leak issues [#153073](https://github.com/home-assistant/core/issues/153073), [#142261](https://github.com/home-assistant/core/issues/142261). This is a separate audit item not yet completed.

- **Coordinator decomposition (H1) is high blast-radius.** Recommend:
  1. Snapshot test results before refactor.
  2. Extract one manager at a time.
  3. Re-run full suite after each extraction; abort and revert on regression.
  4. Do not bundle with behavior changes — refactor-only commits.

- **Platinum-fix sprint regression risk:** C1 (websession injection) changes constructor signatures of `GoveeApiClient` and `GoveeAuthClient`. All call sites and tests need synchronized updates; mock fixtures in `tests/conftest.py` likely change.

## Dissent / Contradictory Evidence

- **Coordinator size (H1):** No HA core rule explicitly bans large coordinator files. The contrarian web-researcher finding ("large coordinators correlate with availability bugs") is correlative, not causal. Counter-evidence: many official integrations have 1000+ LOC coordinators that pass Platinum review. Treat H1 as maintainability-driven, not compliance-driven.

- **`min_ha_version` (H3):** Library-docs research found NO official `min_ha_version` field in the manifest spec. However HA core does honor it when present (verified via core source). HACS-only integrations rely on `hacs.json:homeassistant`. Conservative recommendation: add both.

- **Bluetooth + Cloud hybrid:** No official HA guidance exists for hybrid integrations (web-researcher). Govee's BLE-after-cloud-bootstrap pattern is novel. No clear best-practice violation, but no validation either.

## Open Questions

- Does `config_flow.py` set `option_flow=` explicitly when registering options? (2025.12 deprecation) — needs grep verification.
- Is `aiomqtt` configured with explicit reconnect logic in `api/mqtt.py`, or does it rely on session-level retry? — needs review.
- Does the coordinator observer pattern guarantee `unregister_observer` on entity removal? — separate audit needed.
- Are the `# type: ignore[attr-defined]` suppressions on `homeassistant.components.light` imports a real bug or a stale dev-env issue? — needs mypy run with current HA stubs.

## References

- HA Manifest spec — https://developers.home-assistant.io/docs/creating_integration_manifest
- Quality Scale tiers — https://developers.home-assistant.io/docs/core/integration-quality-scale
- ConfigEntry / runtime_data — https://developers.home-assistant.io/docs/config_entries_index
- DataUpdateCoordinator — https://developers.home-assistant.io/docs/integration_fetching_data
- Coordinator `_async_setup` (2024.8) — https://developers.home-assistant.io/blog/2024/08/05/coordinator_async_setup/
- Bluetooth integration patterns — https://developers.home-assistant.io/docs/network_discovery
- 2025.6 release (Bluetooth overhaul) — https://www.home-assistant.io/blog/2025/06/11/release-20256/
- Coordinator availability issues — https://github.com/home-assistant/core/issues/157017
- MQTT reconnect bugs — https://github.com/home-assistant/core/issues/50892 ; https://github.com/home-assistant/core/issues/132985
- Listener-leak post-mortems — https://github.com/home-assistant/core/issues/153073 ; https://github.com/home-assistant/core/issues/142261
- `async_forward_entry_setup` deprecation — https://github.com/custom-components/nordpool/issues/405
- HACS option_flow deprecation — https://github.com/hacs/integration/issues/4314
- pytest-homeassistant-custom-component fixture pitfalls — https://github.com/MatthewFlamm/pytest-homeassistant-custom-component/issues/153
