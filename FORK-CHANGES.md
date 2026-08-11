# Fork changes

This repository is a fork of [`lasswellt/govee-homeassistant`](https://github.com/lasswellt/govee-homeassistant).
`upstream` is fetch-only; `master` is the branch that gets deployed to Home
Assistant. This file is the running log of every delta we carry, so that
merging upstream is a bounded, reviewable job rather than an archaeology
expedition.

## Classification

| Class | Meaning | Merge risk |
|---|---|---|
| **ADDITIVE** | New files, or new lines inside existing files that add a case to an existing table/branch. Upstream behaviour for every pre-existing input is byte-for-byte unchanged. | Low. A merge conflict here is textual, not semantic. |
| **OVERRIDE** | Changes what upstream already did for an input it already handled. | High. **Must be re-tested whenever upstream touches that area**, even if git merges cleanly. |

Rule of thumb when adding an entry: if you deleted or rewrote a line that
upstream still relies on, it is an OVERRIDE. If you only added, it is ADDITIVE.

---

## Deltas

### 1. `rippleLightToggle` mapping for the H60B0 — **ADDITIVE**

Commit `38bf9c3` · `feat(switch): map rippleLightToggle for the H60B0 uplighter`

The H60B0 is the same hardware as the supported H60B3 with a ripple diffuser
instead of a nebula one, so it reports its top light part as
`rippleLightToggle` where the H60B3 reports `nebulaLightToggle`. Without a
mapping, `NAMED_LIGHT_TOGGLE_SPECS` drops the part entirely.

Touched: `models/device.py`, `switch.py`, `const.py`, `strings.json`,
`translations/en.json`, `README.md`, `tests/test_issue_126.py`.

Additive because every change is a *new entry* in an existing spec table plus
its name strings — no existing toggle's behaviour changes, and no other SKU is
affected. A merge conflict would only be textual (adjacent table lines).

*Upstreamable:* yes, as-is. Worth offering.

### 2. `lan_nudge` — cloud push as a trigger for an authoritative LAN read — **ADDITIVE**

Commit `3d7fd52` · `feat(lan): use cloud pushes as a nudge for an authoritative LAN read`

Treats the MQTT push as a content-free "device X changed, go look" signal and
answers it with a LAN `devStatus` read, decoupling LAN-reachable devices from
Govee's fragile cloud state schema.

Almost all of it is the new file `custom_components/govee/lan_nudge.py` (~289
lines). The integration-side footprint is deliberately tiny:

- `coordinator.py` — **3 inserted lines** (a hook in `_on_mqtt_state_update`, a
  cancel in `async_shutdown`, and the module wiring). Nothing removed, nothing
  reordered.
- `config_flow.py` + `const.py` — the `enable_lan_nudge` option (default ON)
  added alongside the existing options.
- `api/lan.py` and `api/lan_control.py` are **untouched** — `devStatus` is a
  read, compatible with `lan.py`'s read-only policy.

Additive because with the option off, or with no LAN correlation for a device,
every code path behaves exactly as upstream. Still: the coordinator hook lines
are the thing to re-check if upstream restructures `_on_mqtt_state_update`.

*Upstreamable:* plausibly, but it is opinionated. Not offered yet.

### 3. `api/lan_raw/` — raw LAN control library — **ADDITIVE**

Branch `feat/lan-raw-transport`, cut from `upstream/master`.

A self-contained library layer with **zero Home Assistant imports** and zero
imports from the rest of the integration: 20-byte frames, XOR checksum, the
`ptReal` envelope, a declarative per-SKU capability table, the `0xA3`
multipacket chunker, and a write-only UDP sender for port 4003. Plus
`tests/test_lan_raw.py`, which pins every hardware-verified frame from
`govee-lab/PROTOCOL.md` as a golden fixture.

**100% new files.** Not one existing file was modified — that is the point:
this layer could be donated upstream unchanged, and an upstream merge cannot
conflict with it.

Why it exists: the cloud path already gives named scenes, per-segment colour
for the H60B0 *ring only*, zone on/off, music and DreamView. It cannot express
per-zone colour, ripple flow rate, or downlight colour temperature. Those are
LAN-only.

Scope limits on this branch, deliberately: no entities, no coordinator hooks,
no config flow, no reads (raw `ptReal` queries get no LAN reply at all — reads
stay with upstream's `devStatus` code).

*Upstreamable:* yes by construction, though it has no consumer until the zone
lights land.

---

## Planned

### `feat/zone-lights` — **will be the first OVERRIDE**

Exposing the H60B0's three zones as controllable lights means changing how the
existing `light.py` / `switch.py` entities present that device — i.e. changing
upstream behaviour for an input upstream already handles. When that lands it
gets an OVERRIDE entry here, and every upstream merge that touches the light
platform must re-run its tests before being trusted.
