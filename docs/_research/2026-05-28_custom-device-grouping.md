<!-- no-registry: exploratory — DIY-scene-to-group feasibility needs a live Govee API test before scope can be quantified; file counts below are an implementation sketch, not a migration metric -->

# Research: Custom Device Grouping (issue #70)

**Date:** 2026-05-28
**Type:** Feature Investigation
**Source:** https://github.com/lasswellt/govee-homeassistant/issues/70
**Stack:** Home Assistant custom component (Python 3.12+), Govee Cloud API v2.0 + AWS IoT MQTT

---

## Summary

Govee app custom groups are **already partially supported** by this integration, contrary to what the public API docs and other projects suggest. The device-list endpoint returns groups as virtual devices with numeric-only IDs (e.g. `11825917`); with `enable_groups=True` (default `False`) each group gets a `GoveeLightEntity` and control commands go to the group `device_id` as a **single REST call that the Govee cloud fans out server-side** — strictly better sync than HA helpers or govee2mqtt, both of which fan out client-side per member. The real gaps for #70 are: (1) **discoverability** — the feature is off by default and undocumented; (2) **no state sync** — group state is stubbed `online=True`/optimistic because groups can't be polled and have no MQTT topic; (3) **DIY scenes on groups** is blocked in code and of unverified API feasibility. Recommendation: ship the discoverability + docs win now, treat DIY-on-group as a spike gated on a live API test, and do not pursue client-side fan-out (it's the inferior path this integration already avoids).

---

## Research Questions

**Q1. Does the Govee API expose user-created groups?**
Split verdict. Official OpenAPI docs and govee2mqtt: **no** documented group endpoints. But this integration's production code: **yes, empirically** — `GET /router/api/v1/user/devices` returns group entries (numeric `device_id` or `device_type` in `devices.types.group` / `same_mode_group` / `scenic_group`). See Dissent below.

**Q2. Can a group be controlled atomically?**
Yes, more so than the alternatives. A single `PUT .../device/control` to the group `device_id` is accepted for `powerSwitch`/`brightness`/`colorRgb`; the Govee cloud handles member fan-out server-side (`coordinator.py:783`, `api/client.py:321`, no client fan-out). This is closer to atomic than HA helpers / govee2mqtt / homebridge-govee, which all dispatch N per-member commands.

**Q3. What works vs. doesn't for a group today?**
Works (`enable_groups=True`): light entity — power/brightness/color, with `RestoreEntity` persistence. Skipped (23 `is_group` guards across 9 files): scene/DIY/music selectors (`select.py:87`), music/DreamView/appliance switches (`switch.py`), temp/humidity + transport sensors, humidifier, diagnostics.

**Q4. State sync for groups?**
None. `_fetch_device_state()` (`coordinator.py:602-611`) short-circuits groups to `online=True` with no API call; `entity.py:75` forces `available=True`. No MQTT topic exists for groups (`api/auth.py:423-430`). State is purely optimistic + restored from the HA state machine.

**Q5. DIY scenes to a group?**
Unverified. Blocked in code at `select.py:87` (no DIY entity for groups) and `async_send_diy_scene` falls back to BLE/MQTT which groups lack. The REST path itself doesn't check `is_group`, but whether the Govee cloud accepts `diyScene` for a group `device_id` (or returns a DIY list for a group sku) is **untested** — the key open question.

---

## Findings

### F1 — Groups are virtual devices already in the pipeline
`models/device.py:738-746` classifies a group via three `device_type` strings or `device_id.isdigit()`. `coordinator.py:374` filters them out entirely when `enable_groups=False` (the default). CLAUDE.md documents the numeric-ID convention (`11825917`). So the plumbing to detect and admit groups exists; it's gated off.

### F2 — Single-command server-side fan-out is the integration's hidden advantage
Group control takes the same dispatch path as any device (`coordinator.async_control_device` → `api.control_device` → `ENDPOINT_CONTROL`). One HTTP call per user action regardless of member count. The cloud fans out to members. This sidesteps the client-side-fan-out desync that the issue author hit with HA helpers and that web research confirms is universal in govee2mqtt/homebridge-govee.

### F3 — State is optimistic-only, by necessity
Groups return 400 on the state endpoint ("device not exist"), so `_fetch_device_state` stubs them online and `light.py:385-398` restores last-known power/brightness via `coordinator.restore_group_state()` (`coordinator.py:1477-1486`). Consequence: if a member is changed outside HA, the group entity won't reflect it. Acceptable for control, imperfect for state.

### F4 — Sync latency is network-bound regardless of approach
Web research: cloud latency 0.5–2s typical, 5s+ on congested/mesh 2.4 GHz networks (govee2mqtt #197, whizz-experts). LAN API (UDP 4001/4002/4003) is per-device only — no group broadcast — and fragile across mesh/guest networks. No transport offers "true atomic" sync; server-side group fan-out (F2) is the best available.

### F5 — DIY scene library access is inconsistent in the ecosystem
govee2mqtt #13 "Not all DIY Scenes available", #3 "DIY Scenes?"; HA community reports missing scenes/effects in the local integration. Per-device DIY works via `devices.capabilities.dynamic_scene`/`diyScene` (`models/commands.py:197`); group applicability is undocumented everywhere.

---

## Dissent / Contradictory Evidence

**Primary contradiction — "API has no groups" (web) vs. "integration controls groups" (code):**

- `library-docs` + `web-researcher` (web/doc sources, govee2mqtt #341): the public Govee Platform API documents **no** group endpoints; groups are an app-only feature; all known integrations fan out per-device.
- `codebase-analyst` (this repo, ground truth): the integration **already receives and controls** group devices via numeric IDs through the standard device-list + control endpoints, in production, today.

**Resolution:** the web agents inspected only the *documented* OpenAPI surface and govee2mqtt (which favors the LAN/per-device path). The live `GET .../user/devices` response empirically includes group virtual-devices that the documented schema omits — an undocumented-but-real API behavior this integration depends on. **Trust the code here.** Single-domain doc-absence is not evidence of API absence. This is the central reframing for #70: the request is ~70% already shipped, just disabled and undocumented.

**Unresolved:** whether server-side group fan-out is meaningfully better-synced than client fan-out in practice (no benchmark found); and whether `diyScene`/scene-list calls accept a group `device_id` (no source either way — needs live test).

---

## Compatibility Analysis

- **Detected stack fit:** native — all changes are Python within `custom_components/govee/`, no new deps. Group plumbing, `RestoreEntity`, scene cache, and command classes already exist.
- **Version compat:** no HA API surface changes; reuses existing `LightEntity`, `SelectEntity`, coordinator patterns.
- **Integration complexity:**
  - Discoverability/docs win: **trivial** (const default + strings + README).
  - DIY-on-group: **medium** + blocked on a live-API unknown.
  - Member-state aggregation: **medium-high**, optional.
- **Breaking-change risk:** flipping `DEFAULT_ENABLE_GROUPS` to `True` would auto-create new light entities for existing users — a visible change; keep opt-in or guard behind a migration note.

---

## Recommendation

**Ship the discoverability win; spike the DIY-on-group unknown; skip client-side fan-out.**

| Option | Effort | Sync quality | Verdict |
|---|---|---|---|
| Document + surface existing `enable_groups` (single-command fan-out) | XS | Best available (server-side) | **DO NOW** |
| Live-API spike: DIY scene + scene-list for group `device_id` | S | n/a | **SPIKE (gates F5)** |
| Member-state aggregation (derive group on/off/brightness from members) | M | improves state accuracy | Optional follow-up |
| Client-side parallel fan-out to members | M | Worse than current | **REJECT** (regresses F2) |
| LAN API group control | L | Marginal, fragile | **REJECT** (no group broadcast exists) |

Rationale: the integration already does the hard part better than the ecosystem. The issue author's pain (helper desync) is solved by enabling groups — they likely don't know the option exists. Lead with that; only expand to DIY/state once the live test (Open Questions) resolves.

---

## Implementation Sketch

**Phase 1 — discoverability (do now, no API risk):**
- `README.md` + `strings.json`/`translations/en.json`: document that enabling **"Enable group devices"** surfaces Govee app groups as single light entities with server-side sync; note state is optimistic.
- Consider a `data_description` hint on `enable_groups` in the options flow (same pattern as the recent `api_temperature_unit` hint).
- Decision required: keep `DEFAULT_ENABLE_GROUPS = False` (safe) vs. flip to `True` (discoverable but adds entities for existing users). Recommend keep `False` + doc.

**Phase 2 — DIY-on-group spike (gated on Open Questions):**
- Live test: does `POST ENDPOINT_DIY_SCENES` with a group numeric `device_id`+sku return scenes? Does `ENDPOINT_CONTROL` accept `DIYSceneCommand` for a group?
- If yes: relax `select.py:87` to allow DIY/scene selectors for groups gated on `device.supports_diy_scenes`; in `coordinator.async_send_diy_scene` skip the BLE/MQTT fallback for `is_group` (REST-only); add `GoveeDeviceNotFoundError` guard in `api/client.get_diy_scenes()` mirroring `get_dynamic_scenes()`.

**Phase 3 — optional member-state aggregation:**
- Add `member_device_ids` to `GoveeDevice` if the API returns membership; aggregate member states into group state in `coordinator` (all-on→on, mean brightness). Only worthwhile if state accuracy complaints persist.

Affected files (sketch): `models/device.py`, `coordinator.py`, `select.py`, `api/client.py`, `README.md`, `strings.json`, `translations/en.json`.

---

## Risks

- **Live-API feasibility is unproven for DIY-on-group.** Implementing Phase 2 before testing risks shipping a selector that 400s or silently no-ops — repeating the DIY-style "ghost control" anti-pattern already present in `async_send_diy_style`. Gate strictly on the live test.
- **State drift on groups is inherent.** With no poll and no MQTT topic, an out-of-HA change to a member leaves the group entity stale. Document this; don't promise live state.
- **Defaulting groups on is a breaking change.** New entities appear for existing users, can collide with their HA helper groups. Keep opt-in unless paired with a migration/repair notice.
- **Undocumented API dependence.** The whole feature rides on an undocumented behavior (groups in the device list). Govee could remove it. Low likelihood (stable for the integration's life) but worth a note.

---

## Open Questions (require live Govee account + group testing)

1. Does `POST ENDPOINT_DIY_SCENES` with a group's numeric `device_id` + sku return a DIY scene list, or 400?
2. Does `POST ENDPOINT_CONTROL` accept a `DIYSceneCommand` (`devices.capabilities.dynamic_scene`/`diyScene`) for a group `device_id`?
3. Does the account-topic MQTT push ever include group-level state, or only per-member?
4. Is server-side group fan-out actually better-synchronized than client fan-out in the field? (no benchmark located)

---

## References

- Issue #70: https://github.com/lasswellt/govee-homeassistant/issues/70
- Govee Developer Platform: https://developer.govee.com/
- Get Devices: https://developer.govee.com/reference/get-you-devices
- Control Device: https://developer.govee.com/reference/control-you-devices
- Get Dynamic Scenes: https://developer.govee.com/reference/get-light-scene
- govee2mqtt #341 (groups not exposed): https://github.com/wez/govee2mqtt/issues/341
- govee2mqtt #197 (slow response): https://github.com/wez/govee2mqtt/issues/197
- govee2mqtt #13 (not all DIY scenes): https://github.com/wez/govee2mqtt/issues/13
- govee2mqtt LAN.md: https://github.com/wez/govee2mqtt/blob/main/docs/LAN.md
- homebridge-govee config (controlInterval): https://github.com/homebridge-plugins/homebridge-govee/wiki/Configuration
- Govee LAN API 101: https://community.govee.com/posts/mastering-the-lan-api-series-lan-api-101/136755
- Govee delayed-response report: https://whizz-experts.com/support/smart-devices/govee-delayed-response/
- Codebase: `models/device.py:738-746`, `coordinator.py:374,602-611,783,1165,1477-1486`, `select.py:87`, `api/auth.py:423-430`, `api/client.py:321,418`, `models/commands.py:197`
