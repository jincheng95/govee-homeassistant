<!-- no-registry: feature investigation; entity/field counts are a design sketch, not a countable migration scope -->

# Research: Per-Device Connectivity Diagnostics — Directional Freshness (last MQTT push/receive, API push/receive)

**Date:** 2026-06-05
**Type:** Feature Investigation
**Topic slug:** diagnostics-per-device-connectivity
**Stack:** HA custom component, Python 3.12+, `DataUpdateCoordinator`, cloud_push (MQTT + REST polling)
**Supersedes/extends:** `2026-05-30_mqtt-last-received-status-fields.md` (hub-level MQTT receive), `2026-06-04_diagnostic-data-sensors.md` (hub/per-device "All Data Last Updated")

> **Update 2026-06-05 (implemented):** the attributes-only recommendation below was
> revised at user request to ship **user-visible per-device entities** (dashboard-able):
> per-device `sensor.*_last_command_sent` (TIMESTAMP) + `binary_sensor.*_connectivity`
> (CONNECTIVITY, always-on, carries the full per-transport last-received/last-sent
> breakdown as attributes); the existing `all_data_last_updated` sensor was relabeled
> "Last Update Received" as its inbound counterpart. The granular per-transport ×3
> connectivity binaries remain opt-in (`CONF_EXPOSE_TRANSPORT_ENTITIES`). Directional
> `TransportHealth` split (`last_send_ts`) + per-device MQTT receive timestamp landed as
> the data layer. See `sensor.py`, `binary_sensor.py`, `coordinator.py`.

## Summary

Goal: make diagnostics user-friendly and **per-device**, surfacing **directional** connectivity freshness — last MQTT push (outbound command), last MQTT receive (inbound state), last API push (outbound REST command), last API receive (REST poll). Two prior docs delivered the *hub-level* and *undirected* pieces. The core gap: `TransportHealth.last_success_ts` (`models/transport.py:25`) is **direction-agnostic** — `_record_transport_success` is called for BOTH send and receive on the same (device, transport) bucket (`cloud_api`: receive coordinator.py:1010 + send :1255/:1303; `mqtt`: receive :892 + send :1228), so a poll and a command overwrite the same field. None of the 4 directional timestamps exist per device today. Recommendation: add a `last_send_ts` direction to `TransportHealth` (existing `last_success_ts` becomes the *receive* stamp), reroute the 3 send call-sites to a new `record_send`, add a per-device MQTT receive timestamp to `GoveeAwsIotClient`, and **expose all four as `extra_state_attributes` on the existing per-device `GoveeTransportConnectivity` binary sensor — NOT as 4N new entities**. Both library-docs and the contrarian web-researcher independently converge on attributes-over-entities to avoid entity sprawl, recorder churn, and PR-review friction. Gate behind the existing `CONF_EXPOSE_TRANSPORT_ENTITIES` opt-in (already default False).

## Research Questions

1. **What directional tracking exists today?** None per-device. `TransportHealth` has one `last_success_ts` per (device, transport), stamped by both send and receive paths — conflated. Hub-level `_last_message_ts` (mqtt.py:128) is MQTT-receive only. (codebase-analyst)
2. **Where are the send/receive sites?** REST receive coordinator.py:1010 (`get_device_state`); REST send :1255/:1303 (`control_device`); MQTT receive :892 (`_handle_message`→`_record_transport_success`); MQTT send :1228 (`_try_mqtt_command`→`async_publish_command`, mqtt.py:573). All route through `_record_transport_success(device_id, kind)`. (codebase-analyst, verified)
3. **Best UX to show 4 timestamps/device?** Attributes on one connectivity entity, not 4N TIMESTAMP entities. HA guidance: high-cardinality diagnostics → `extra_state_attributes`; opt-in via `entity_registry_enabled_default=False` / explicit option. (library-docs, web-researcher — converge)
4. **What's already exposed per-device?** `GoveeTransportConnectivity` ×3 (cloud_api/mqtt/ble), opt-in `CONF_EXPOSE_TRANSPORT_ENTITIES` (const.py:19, default False), already carries `last_success`/`last_failure`/`last_failure_reason` attributes (binary_sensor.py:173-185). `GoveeAllDataLastUpdatedSensor` (per-device, always-on, sensor.py:327). (codebase-analyst, verified)
5. **Is directional push/receive a known pattern?** No prior art — novel. Existing integrations (mobile_app `last_seen`, zwave_js) expose at most one undirected "last seen", as an attribute. (web-researcher, contrarian — `[UNVERIFIED]` no counter-precedent found)
6. **Is "Energy"/REST-poll-time already solved?** "All Data Last Updated" per-device exists (`device_data_last_updated`, coordinator.py:406) = effectively "last API receive" hub-agnostic. Energy remains infeasible (no Govee API capability — prior doc).

## Findings

### F1 — `TransportHealth.last_success_ts` conflates push + receive (root gap)
`models/transport.py:19-46`: single `last_success_ts` / `last_failure_ts` per (device, transport). `TransportHealthTracker.record_success` (transport_health.py:57) stamps `mark_success(now)`. Coordinator calls it for opposite directions on the same bucket:
- `cloud_api` **receive**: `coordinator.py:1010` after `get_device_state`
- `cloud_api` **send**: `coordinator.py:1255` + `:1303` after `control_device`
- `mqtt` **receive**: `coordinator.py:892` after `_handle_message`
- `mqtt` **send**: `coordinator.py:1228` after `_try_mqtt_command` (`async_publish_command`, mqtt.py:573)

A command immediately after a poll overwrites the poll's timestamp — current data cannot answer "when did we last *receive* vs last *send*". (codebase-analyst + first-hand verification)

### F2 — Per-device MQTT receive timestamp missing
`api/mqtt.py`: `_last_messages: dict[str,dict]` (mqtt.py:126) stores per-device payload, no time. `_last_message_ts` (mqtt.py:128, stamped :414) is **hub-level scalar** only. No `dict[device_id → datetime]`. (codebase-analyst, verified)

### F3 — Existing per-device diagnostic surface (reuse target)
`GoveeTransportConnectivity` (binary_sensor.py:133) — `BinarySensorDeviceClass.CONNECTIVITY`, `EntityCategory.DIAGNOSTIC`, one per (device, transport), `unique_id = f"{device_id}_{transport}_connectivity"`. Already emits attributes (binary_sensor.py:173):
```python
attrs["last_success"] = health.last_success_ts.isoformat()
attrs["last_failure"] = health.last_failure_ts.isoformat()
attrs["last_failure_reason"] = health.last_failure_reason
```
This is the natural home for `last_send` / `last_receive`. Gated by `CONF_EXPOSE_TRANSPORT_ENTITIES`. `GoveeAllDataLastUpdatedSensor` (sensor.py:327, per-device, always-on) already renders REST-receive freshness as a TIMESTAMP entity.

### F4 — UX guidance: attributes over entities (independent convergence)
- library-docs: HA recommends attributes for high-cardinality diagnostics; one CONNECTIVITY sensor + `extra_state_attributes` for the 4 timestamps; pair `EntityCategory.DIAGNOSTIC` with `entity_registry_enabled_default=False` for opt-in. Attributes don't trigger recorder re-writes unless main state changes.
- web-researcher (contrarian): same conclusion — "safer against PR review friction ('why not attributes?') and avoids logbook spam." Observed precedent (mobile_app exposes `last_updated` as an attribute, not a dedicated TIMESTAMP entity; zwave_js uses event entities for actionable state) leans toward attributes over dedicated timestamp entities. `[UNVERIFIED]` — no formal HA core rule located.
- Convergence across ≥2 independent agents + HA core precedent → treat as established.

### F5 — Diagnostics download already carries directional-ish raw data
`diagnostics.py` `_device_diag` (diagnostics.py:113) already dumps per-device `transport_health` (is_available, last_success, last_failure, reason) + `last_mqtt_message` + `raw_api_state`. The *download* is rich; the gap is **live, directional, user-visible** signal. HA guidance (library-docs Q6): download JSON for ephemeral/large/debug; entities for monitoring/automation. Directional freshness is automation-worthy → entity attributes justified.

## Compatibility Analysis

- **Model change additive:** add `last_send_ts: datetime | None = None` + `mark_send(now)` to `TransportHealth`. Existing `last_success_ts` keeps semantics but now means *receive* — rename optional (cosmetic; defer to avoid churn). No breaking change to diagnostics download (keys preserved). Low risk.
- **Tracker:** add `record_send(device_id, transport)` to `TransportHealthTracker` (transport_health.py:57 sibling). Callback-free, side-effect-free design preserved.
- **MQTT client:** add `_last_message_per_device: dict[str, datetime]` + `last_message_ts_for(device_id)` property; stamp in `_handle_message` (mqtt.py:414) alongside existing hub `_last_message_ts`. No new dep (`datetime`/`timezone` already imported).
- **Entity layer:** extend `GoveeTransportConnectivity.extra_state_attributes` only — no new entity classes, no new translation keys, no `async_setup_entry` change. Zero entity-count growth.
- **Recorder:** attributes ride the binary_sensor's existing state writes (connectivity flips), NOT every poll → bounded churn. No `state_class` concern (binary sensor). Honors web-researcher's anti-spam caveat.
- **Tests:** `tests/test_translations.py` unaffected (no new keys). Add tracker `record_send` unit tests + binary_sensor attribute assertions.

## Recommendation

**Split `TransportHealth` into receive (`last_success_ts`) + send (`last_send_ts`); surface both per transport as `extra_state_attributes` on `GoveeTransportConnectivity`. Add a per-device MQTT receive timestamp. No new entities. Keep the `CONF_EXPOSE_TRANSPORT_ENTITIES` opt-in.**

| Signal | Source today | Action |
|---|---|---|
| last API receive | `cloud_api` `last_success_ts` (conflated) + `device_data_last_updated` | Keep `last_success_ts` as receive; expose as `last_receive` attr |
| last API push | conflated into `cloud_api` `last_success_ts` | NEW `last_send_ts`; reroute coordinator.py:1255/:1303 → `record_send` |
| last MQTT receive | hub `_last_message_ts` only | NEW per-device `last_message_ts_for`; reroute :892 stays receive |
| last MQTT push | conflated into `mqtt` `last_success_ts` | NEW `last_send_ts`; reroute coordinator.py:1228 → `record_send` |

Rejected alternative: 4 dedicated TIMESTAMP sensors × N devices = 4N entities — entity bloat, recorder churn, PR-review friction (F4). Attributes win.

## Implementation Sketch

1. **`models/transport.py`** — add direction:
   ```python
   last_send_ts: datetime | None = None
   def mark_send(self, now: datetime) -> None:
       self.last_send_ts = now
   ```
2. **`transport_health.py`** — sibling of `record_success` (:57):
   ```python
   def record_send(self, device_id: str, transport: TransportKind) -> None:
       self.ensure(device_id)
       self._health[device_id][transport].mark_send(datetime.now(timezone.utc))
   ```
3. **`coordinator.py`** — add `_record_transport_send(device_id, transport)` (mirror :320), then reroute SEND sites:
   - `:1255`, `:1303` (`cloud_api` after `control_device`) → `_record_transport_send(device_id, "cloud_api")` (keep or drop the receive-success there — a command ACK is not a state read)
   - `:1228` (`mqtt` after `_try_mqtt_command`) → `_record_transport_send(device_id, "mqtt")`
   - Leave `:1010` (REST poll) and `:892` (MQTT `_handle_message`) as receive (`record_success`).
   - Add property `mqtt_last_receive_for(device_id)` delegating to the MQTT client.
4. **`api/mqtt.py`** — `__init__`: `self._last_message_per_device: dict[str, datetime] = {}`; in `_handle_message` (:414): `self._last_message_per_device[device_id] = datetime.now(timezone.utc)`; add `last_message_ts_for(self, device_id)` property.
5. **`binary_sensor.py`** — extend `GoveeTransportConnectivity.extra_state_attributes` (:173):
   ```python
   if health.last_send_ts is not None:
       attrs["last_send"] = health.last_send_ts.isoformat()
   if health.last_success_ts is not None:
       attrs["last_receive"] = health.last_success_ts.isoformat()  # alias; keep last_success for back-compat
   # mqtt transport: also surface per-device receive ts
   if self._transport == "mqtt":
       ts = self.coordinator.mqtt_last_receive_for(self._device_id)
       if ts is not None:
           attrs["last_receive"] = ts.isoformat()
   ```
6. **`diagnostics.py`** — `_transport_health` (:92) add `"last_send": _iso(health.last_send_ts)` so the download mirrors live attrs.
7. **Tests** — `tests/test_transport_health.py`: `record_send` stamps only `last_send_ts`, leaves `last_success_ts`. `tests/test_binary_sensor.py` (or coordinator): attrs include `last_send`/`last_receive`. MQTT: `_handle_message` populates `_last_message_per_device`.
8. **Docs/UX** — README diagnostics section: explain the per-transport `last_send`/`last_receive` attributes and that enabling them needs `CONF_EXPOSE_TRANSPORT_ENTITIES`.

## Risks

- **`last_success_ts` semantic drift.** It currently means "any successful use"; narrowing it to "receive" by rerouting send call-sites changes what `GoveeTransportConnectivity.is_available` staleness and the diagnostics download report. Mitigation: keep emitting `last_success` attribute as an alias of `last_receive` for one release; document the change; do NOT silently repurpose. This is the only behavior-affecting change — review it deliberately.
- **MQTT push staleness ambiguity.** `last_send_ts` for mqtt only advances when native MQTT control is enabled (`_enable_mqtt_control`, coordinator.py:165) AND a command is issued; an idle device shows a stale/empty push time even on a healthy link. That is correct (no command = no push) but may read as "broken" to users. Document that push-time reflects last *command*, not link health — link health is the binary `is_on`.
- **Novel pattern, no precedent.** Directional per-transport timestamps have no HA-core analogue (web-researcher `[UNVERIFIED]`). Risk: HA reviewers may question necessity. Mitigation: ship as opt-in attributes (already gated), not always-on entities — minimizes surface and review friction.
- **Recorder growth if mis-implemented as entities.** Sticking to attributes on the existing connectivity sensor avoids the 4N-entity recorder churn the contrarian flagged. Do not regress to dedicated TIMESTAMP sensors.
- **Naive datetime.** All stamps must be tz-aware `datetime.now(timezone.utc)` (tracker already does this, transport_health.py:60). A naive datetime breaks TIMESTAMP rendering — real failures #169687, #148202.

## Dissent / Contradictory Evidence

- **web-researcher (contrarian):** found NO formal GitHub issue for "too many diagnostic entities" or "timestamp spam" — the bloat/churn concern is plausible but not formally tracked (`[UNVERIFIED]`). Also found no precedent for directional send/receive timestamps → flags it as unproven for maintenance/UX acceptance. Recommends attributes specifically to dodge PR-review friction.
- **library-docs (pro):** attributes-on-connectivity-sensor is squarely within HA diagnostic-entity guidance; opt-in diagnostic category is the sanctioned mechanism.
- **Resolution:** Both agree on the *mechanism* (attributes, opt-in). They differ only on whether directional split is worth it. Since the data is cheap (additive model field) and the user explicitly asked for push/receive granularity, proceed — but keep it opt-in and attribute-based so the unproven part carries minimal surface.

## Open Questions

- Should a `cloud_api` command success still stamp the *receive* timestamp (a control ACK echoes some state)? Recommendation: no — keep receive = poll-read only, so "last API receive" means "last full state read". Confirm with user.
- Should `last_send`/`last_receive` attributes also appear on `GoveeAllDataLastUpdatedSensor`, or stay only on the (opt-in) connectivity sensor? Default: connectivity sensor only, to keep the always-on entity uncluttered.
- Does the user want these visible **without** enabling `CONF_EXPOSE_TRANSPORT_ENTITIES` (i.e., a new always-on per-device "Connectivity" diagnostic entity)? Trade-off: discoverability vs default entity count.

## References

- https://developers.home-assistant.io/docs/core/entity/ — entity category, generic properties, `entity_registry_enabled_default`
- https://developers.home-assistant.io/docs/core/entity/#entity-category
- https://developers.home-assistant.io/docs/core/entity/binary_sensor/#device-class — CONNECTIVITY semantics
- https://developers.home-assistant.io/docs/core/entity/sensor/#device-class — TIMESTAMP rendering
- https://developers.home-assistant.io/docs/device_registry/ — per-device `device_info`/`identifiers`
- https://developers.home-assistant.io/docs/entity_registry/ — opt-in / disabled-by-default entities
- https://developers.home-assistant.io/docs/recorder/ — recorder churn / exclude guidance
- https://developers.home-assistant.io/docs/creating_integration_manifest/#diagnostics
- https://github.com/home-assistant/core/issues/169687 — naive-datetime TIMESTAMP failure
- https://github.com/home-assistant/core/issues/148202 — ZHA naive datetime tzinfo error
- https://github.com/home-assistant/core/issues/172603 — iOS app `last_updated` relative-time / timezone-mismatch display bug (TIMESTAMP-rendering caveat, not an attribute-vs-entity precedent)

### Codebase refs
- `custom_components/govee/models/transport.py:19` — `TransportHealth` (add `last_send_ts`/`mark_send`)
- `custom_components/govee/transport_health.py:57` — `record_success` (add `record_send`)
- `custom_components/govee/coordinator.py:320` — `_record_transport_success`/`_record_transport_failure`
- `custom_components/govee/coordinator.py:892` — MQTT receive stamp; `:1010` REST receive; `:1228` MQTT send; `:1255`/`:1303` REST send
- `custom_components/govee/coordinator.py:406` — `device_data_last_updated`
- `custom_components/govee/api/mqtt.py:126` — `_last_messages`; `:128`/`:414` hub `_last_message_ts`; `:573` `async_publish_command`
- `custom_components/govee/binary_sensor.py:133` — `GoveeTransportConnectivity`; `:173` `extra_state_attributes`
- `custom_components/govee/sensor.py:327` — `GoveeAllDataLastUpdatedSensor`
- `custom_components/govee/diagnostics.py:92` — `_transport_health`
- `custom_components/govee/const.py:19` — `CONF_EXPOSE_TRANSPORT_ENTITIES` (default False, :34)
