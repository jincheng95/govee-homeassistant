# Research: PR #37 — `devices.types.air_purifier` Validation

**Date**: 2026-04-08
**Type**: Feature Investigation (PR validation)
**Status**: Complete
**Stack**: Home Assistant custom component, Python 3.12, Govee Cloud API v2.0

---

## Summary

PR #37 proposes adding `DEVICE_TYPE_AIR_PURIFIER = "devices.types.air_purifier"` and making both `is_fan` and `is_purifier` return True for it, so devices like the Govee H7126 produce a fan entity. The PR's core claim is validated: `devices.types.air_purifier` is the canonical Govee API v2.0 string, confirmed by four independent primary sources (official Govee Developer docs, wez/govee2mqtt, Mavrrick Hubitat driver, two H7126 API fixtures, and H7121 fixture) — and **the codebase's existing `DEVICE_TYPE_PURIFIER = "devices.types.purifier"` constant is wrong**: zero real devices report that string. The PR is functionally correct and safe to merge, but should be improved to (a) fix the dead `devices.types.purifier` string rather than alias around it, and (b) ship with test coverage for both `is_fan` and `is_purifier` on the new type. The duplicate-control observation (fan entity + purifier mode select both created) is a real UX concern but not a regression, since the wrong string meant no purifier ever worked before.

---

## Research Questions

### Q1: Is `devices.types.air_purifier` a real Govee API v2.0 device type, and does H7126 actually report it?
**Answer**: Yes, confirmed by four independent primary sources. Govee's official Developer API docs list `devices.types.air_purifier` explicitly (no `devices.types.purifier` exists). wez/govee2mqtt's `DeviceType` enum in `src/platform_api.rs:818-832` uses `air_purifier`. Mavrrick's Hubitat driver maps H7120/H7122/H7123/H7126/H712C to `devices.types.air_purifier`. Two independent H7126 captures (disforw/goveelife fixture from issue #83 and CanDuru4/govee fork fixture) both show `"type": "devices.types.air_purifier"`, and a govee2mqtt H7121 fixture corroborates.

### Q2: Is `devices.types.purifier` (the current codebase constant) a real string?
**Answer**: No — zero primary sources use it. A GitHub code search across the wez/govee2mqtt repo returned no hits, and it appears nowhere in the Govee developer docs or any community integration. The constant in `custom_components/govee/models/device.py:33` is simply wrong and has never matched a real device. This means hacs-govee's purifier path has been dead code since it was written — no user has ever had a working purifier select entity through the current code.

### Q3: Does having both `is_fan` and `is_purifier` return True cause duplicate entity creation?
**Answer**: Yes, it creates two overlapping entities but no identity collision. An air_purifier device gets a `GoveeFanEntity` from `fan.py:51` (supports on/off, speed Sleep/Low/High, preset modes Normal/Auto) AND a `GoveePurifierModeSelectEntity` from `select.py:179` (select with the same gearMode options). These are different entity types so HA doesn't error, but the user sees redundant controls — the fan entity's speed already covers Sleep/Low/High and its preset mode already covers Auto. Not a regression since old behavior was broken; but a UX smell the PR doesn't address.

### Q4: Does the current fan/purifier code handle H7126's workMode correctly?
**Answer**: Mostly yes, with one caveat. `fan.py` already uses `device.get_fan_speed_options()` to detect gearMode speed values dynamically and exposes `PRESET_MODE_AUTO = "Auto"` via `workMode=3`. `models/device.py:482-515` `get_purifier_mode_options()` supports both flat `workMode` options and nested `modeValue → gearMode` structures. **Caveat**: H7126 firmware versions ship *two* different workMode shapes (one nested `gearMode → Sleep/Low/High`, one flat `Sleeping/Low/High/Custom`) — per independent fixtures. The existing parser handles the nested form; the flat form would need verification.

### Q5: Are other device types missing from `DEVICE_TYPE_*` constants?
**Answer**: Yes — at least five. `docs/govee-protocol-reference.md` section 8.4 documents types for dehumidifier (H7151), ice_maker (H7172), thermometer (H5179), air_quality_monitor (H5140), and sensor (motion), but `models/device.py:27-33` only defines constants for light, socket, heater, humidifier, fan, and purifier. Not a blocker for PR #37, but worth a follow-up issue to round out the enum with the full observed taxonomy (~14 real types).

---

## Findings

### Theme 1: The canonical string is `devices.types.air_purifier`, not `purifier`

- **Govee Developer API docs** list `devices.types.air_purifier` and do not list `devices.types.purifier` at all. Source: https://developer.govee.com/reference/get-you-devices
- **wez/govee2mqtt** uses an authoritative Rust enum at `src/platform_api.rs:818-832` with `AirPurifier = "devices.types.air_purifier"`. No occurrence of `devices.types.purifier` anywhere in the repo.
- **Mavrrick Hubitat driver** (`Govee/v2/Mavrrick.GoveeIntegrationv2.groovy:1183-1208`) maps every purifier SKU it knows (H7120, H7122, H7123, H7126, H712C) to `devices.types.air_purifier`.
- **Two independent H7126 fixtures** captured by HA community integrations (disforw/goveelife tests and CanDuru4/govee) both report `"type": "devices.types.air_purifier"`.
- **hacs-govee's `DEVICE_TYPE_PURIFIER = "devices.types.purifier"`** (at `custom_components/govee/models/device.py:33`) is therefore wrong. The purifier code path has been unreachable since it was written.

### Theme 2: Entity-creation impact is safe but duplicates UX

- **Fan platform gate** (`custom_components/govee/fan.py:51`): `if device.is_fan: entities.append(GoveeFanEntity(...))`. Fan entity provides on/off, dynamic speed count from gearMode options, preset modes `["Normal", "Auto"]`, and oscillation if supported.
- **Select platform gate** (`custom_components/govee/select.py:179-193`): `if device.is_purifier: entities.append(GoveePurifierModeSelectEntity(...))`. Select extracts the same gearMode options via `get_purifier_mode_options()`.
- **Light platform** (`custom_components/govee/light.py:73`): excludes `device.is_fan` devices, so air_purifier does not get a light entity.
- **Result with PR #37**: an H7126 gets BOTH a fan entity AND a purifier-mode-select entity. They control the same underlying workMode capability. No identity collision (different platforms, different unique IDs), but duplicated controls.
- **Risk**: Low — the purifier select is additive, and before the PR no purifier ever worked at all.

### Theme 3: H7126 workMode structure varies by firmware

- **Nested-gearMode form** (disforw fixture, 2025-10-13):
  ```json
  {"workMode": {"options": [{"name": "gearMode", "value": 1}, {"name": "Custom", "value": 2}, {"name": "Auto", "value": 3}]},
   "modeValue": {"options": [{"name": "gearMode", "options": [{"name": "Sleep"}, {"name": "Low"}, {"name": "High"}]}, ...]}}
  ```
- **Flat form** (CanDuru4 fixture): `workMode` exposes `Sleeping/Low/High/Custom` directly, no nested `modeValue.gearMode`.
- **Current parser** at `models/device.py:482-515` handles the nested form. Flat form support is less certain — a test fixture with the flat shape should be added.

### Theme 4: Test coverage gap

- `tests/test_models.py:253-255,314` has tests for `is_fan` and `test_fan_not_light`.
- **No tests** exist for `is_purifier` at all — explaining why the wrong constant was never caught.
- PR #37 ships no tests. A rebase-and-merge by us should add:
  - `test_is_purifier_for_air_purifier_type`
  - `test_is_fan_for_air_purifier_type`
  - `test_air_purifier_not_light`
  - Ideally: a test for `get_purifier_mode_options()` against an H7126-like capability dict.

### Theme 5: Broader device-type gaps (out of scope but noted)

Documented in `docs/govee-protocol-reference.md:8.4` but missing from `models/device.py:27-33`:
- `devices.types.dehumidifier` (H7151)
- `devices.types.ice_maker` (H7172)
- `devices.types.thermometer` (H5179)
- `devices.types.air_quality_monitor` (H5140)
- `devices.types.sensor` (motion sensors)
- `devices.types.kettle`, `devices.types.aroma_diffuser`, `devices.types.box` — not yet seen in issues, but present in govee2mqtt's parser.

---

## Compatibility Analysis

### Stack Compatibility

| Aspect | Status | Notes |
|--------|--------|-------|
| Govee API v2.0 | Validated | Confirmed against 4 primary sources |
| Python 3.12 | Compatible | No language features used |
| models/device.py module | Compatible | Additive change, no refactor |
| fan.py platform | Compatible | Existing `get_fan_speed_options()` already parses gearMode |
| select.py platform | Compatible | Existing `get_purifier_mode_options()` handles nested shape |
| tests/test_models.py | Needs augmentation | is_purifier coverage missing entirely |
| Existing `devices.types.purifier` users | N/A | Zero real devices ever reported this string |

### Integration Complexity

- **Effort estimate**: Low (hours)
- **Files affected**: 2 — `custom_components/govee/models/device.py`, `tests/test_models.py`
- **Breaking changes**: None. The change is strictly additive.
- **Migration path**: None required.

---

## Recommendation

### Decision

**Merge PR #37 with modifications**, applied locally before merging (author's original patch used as the starting point):

1. **Replace** the incorrect string rather than alias around it. Change `DEVICE_TYPE_PURIFIER = "devices.types.purifier"` to `DEVICE_TYPE_PURIFIER = "devices.types.air_purifier"` and drop the separate `DEVICE_TYPE_AIR_PURIFIER` constant. There is no real device reporting `"devices.types.purifier"`, so keeping it as a fallback obscures the bug.
2. **Keep** PR #37's behavioral change: air purifiers match both `is_fan` and `is_purifier`. Acceptable given the PR author's reported goal of fan entity creation, and since the select entity is additive.
3. **Add** tests in `tests/test_models.py` covering `is_fan`, `is_purifier`, and negative `is_light` for the new type, plus a `get_purifier_mode_options()` test using an H7126-shaped workMode capability dict.
4. **File a follow-up issue** to (a) round out the missing `DEVICE_TYPE_*` constants from the protocol reference, and (b) decide whether the duplicate fan+select UX is desirable or whether we should drop the select path for air_purifier devices.

### Rationale

- The PR's API claim is validated by multiple primary sources — this is not a speculative fix.
- The current `DEVICE_TYPE_PURIFIER` constant is definitively wrong; keeping both strings only accumulates dead code.
- Fan + select duplication is additive and not a regression. Leave the UX debate for a follow-up.
- Test coverage is the only real blocker — the whole reason this bug shipped is that `is_purifier` had zero tests.

### Comparison Matrix — PR approaches

| Criteria | PR #37 as-written | Replace string (recommended) | Only fix `is_purifier` (no fan) |
|---|---|---|---|
| Matches canonical API string | ✓ (adds alongside) | ✓ (fixes directly) | ✓ |
| Produces fan entity (author's goal) | ✓ | ✓ | ✗ |
| Leaves dead code | ✓ dead `DEVICE_TYPE_PURIFIER` | ✗ clean | ✗ clean |
| Duplicate fan + select controls | Yes | Yes | No, but loses speed control UX |
| Minimal diff from current master | Medium | Small | Smallest |
| **Overall** | Acceptable | **Recommended** | Acceptable alternative |

---

## Implementation Sketch

### Step 1: Rebase the PR branch onto current master and apply the simplified fix

```bash
# From a local checkout:
git fetch origin
git checkout master
git pull
git checkout -b fix/air-purifier-device-type
```

### Step 2: Fix the wrong constant in `custom_components/govee/models/device.py`

Replace the existing `DEVICE_TYPE_PURIFIER` string value and update the predicate to also match fans:

```python
# custom_components/govee/models/device.py — around line 33
DEVICE_TYPE_PURIFIER = "devices.types.air_purifier"  # was "devices.types.purifier" — wrong, never matched

# around line 291
@property
def is_fan(self) -> bool:
    """Check if device is a fan or air purifier.

    Air purifiers expose workMode with gearMode speeds (Sleep/Low/High) and
    an Auto preset, which map naturally onto the Home Assistant fan entity.
    """
    return self.device_type in (DEVICE_TYPE_FAN, DEVICE_TYPE_PURIFIER)

# around line 302 — unchanged in value, but is_purifier now correctly matches real devices
@property
def is_purifier(self) -> bool:
    """Check if device is an air purifier."""
    return self.device_type == DEVICE_TYPE_PURIFIER
```

Net effect: same observable behavior as PR #37 (fan + select both created for H7126), no dead strings, no new constants.

### Step 3: Add tests to `tests/test_models.py`

```python
class TestDeviceAirPurifier:
    """Tests for devices.types.air_purifier recognition (H7126-class devices)."""

    def test_air_purifier_is_purifier(self):
        device = GoveeDevice(
            device_id="AA:BB:CC:DD:EE:FF:00:11",
            sku="H7126",
            name="Smart Air Purifier",
            device_type="devices.types.air_purifier",
            capabilities=(),
        )
        assert device.is_purifier is True

    def test_air_purifier_is_also_fan(self):
        """Air purifiers should also match is_fan so a fan entity is created."""
        device = GoveeDevice(
            device_id="AA:BB:CC:DD:EE:FF:00:11",
            sku="H7126",
            name="Smart Air Purifier",
            device_type="devices.types.air_purifier",
            capabilities=(),
        )
        assert device.is_fan is True

    def test_air_purifier_is_not_light(self):
        device = GoveeDevice(
            device_id="AA:BB:CC:DD:EE:FF:00:11",
            sku="H7126",
            name="Smart Air Purifier",
            device_type="devices.types.air_purifier",
            capabilities=(),
        )
        assert device.is_light is False

    def test_purifier_mode_options_nested_gear_mode(self):
        """H7126 workMode capability with nested gearMode → Sleep/Low/High + Auto."""
        cap = GoveeCapability(
            type="devices.capabilities.work_mode",
            instance="workMode",
            parameters={
                "dataType": "STRUCT",
                "fields": [
                    {"fieldName": "workMode", "options": [
                        {"name": "gearMode", "value": 1},
                        {"name": "Custom", "value": 2},
                        {"name": "Auto", "value": 3},
                    ]},
                    {"fieldName": "modeValue", "options": [
                        {"name": "gearMode", "options": [
                            {"name": "Sleep", "value": 1},
                            {"name": "Low", "value": 2},
                            {"name": "High", "value": 3},
                        ]},
                        {"name": "Custom"},
                        {"name": "Auto"},
                    ]},
                ],
            },
        )
        device = GoveeDevice(
            device_id="AA:BB:CC:DD:EE:FF:00:11",
            sku="H7126",
            name="Smart Air Purifier",
            device_type="devices.types.air_purifier",
            capabilities=(cap,),
        )
        options = device.get_purifier_mode_options()
        assert options is not None
        names = [o.get("name") for o in options]
        assert "Sleep" in names and "Low" in names and "High" in names and "Auto" in names
```

Adjust factory arguments to match the actual `GoveeDevice` / `GoveeCapability` constructors in `models/device.py` — the snippet above uses positional/keyword names consistent with the frozen dataclass pattern described in CLAUDE.md.

### Step 4: Run tests

```bash
.venv/bin/python -m pytest tests/test_models.py -v -k "purifier or air"
```

### Step 5: Commit and merge

```bash
git commit -m "fix: Recognize devices.types.air_purifier (H7126) and remove dead purifier string (#37)"
git push origin fix/air-purifier-device-type
gh pr merge 37 --squash  # or merge locally and close #37 with a note
```

Credit the original author (`kami587`) in the commit message — their diagnosis was correct, we just chose a tighter fix.

### Configuration Changes

None. No manifest, no requirements, no config flow changes.

---

## Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Some real device DID report `devices.types.purifier` but nobody tested | Very Low | Low | 4 independent sources disagree. If a user reports a regression, re-add the old string as a second constant. |
| H7126 flat-workMode firmware variant isn't handled by `get_purifier_mode_options()` | Medium | Medium | Add a test fixture for the flat shape; if it fails, extend the parser. Non-blocking — can ship the type fix first. |
| Duplicate fan + select controls confuse users | Medium | Low | Document in release notes; file follow-up issue to consider dropping the select for air_purifier devices. |
| Breaking other fan-entity users by expanding `is_fan` | Low | Medium | The expansion is strictly additive; `DEVICE_TYPE_FAN` devices behave identically. |

### Open Questions

- Should the purifier-mode select entity be removed for air_purifier devices (since the fan entity already exposes Sleep/Low/High via speed and Auto via preset_mode)? This is a UX judgment call — defer to a follow-up.
- Are there H7120/H7122/H7123/H712C firmware variants with the flat workMode shape? The two H7126 fixtures already show both shapes, so assume yes.
- Should we also fix the missing `DEVICE_TYPE_*` constants (dehumidifier, ice_maker, etc.) in the same PR, or separately? Recommend separately to keep this PR focused.

---

## References

1. **Govee Developer API — Get Your Devices** — https://developer.govee.com/reference/get-you-devices — Official list of device type strings; confirms `devices.types.air_purifier` and absence of `devices.types.purifier`.
2. **wez/govee2mqtt DeviceType enum** — https://github.com/wez/govee2mqtt/blob/master/src/platform_api.rs — Authoritative Rust parser for Govee API v2.0; uses `AirPurifier = "devices.types.air_purifier"`.
3. **wez/govee2mqtt H7121 fixture** — https://github.com/wez/govee2mqtt/blob/master/test-data/list_devices_issue4.json — Real captured API response for a Smart Air Purifier with `type: devices.types.air_purifier`.
4. **Mavrrick Hubitat Govee v2 integration** — https://github.com/Mavrrick/Hubitat-by-Mavrrick/blob/master/Govee/v2/Mavrrick.GoveeIntegrationv2.groovy — SKU-to-type mappings for H7120/H7122/H7123/H7126/H712C all use `air_purifier`.
5. **disforw/goveelife H7126 fixture** — https://github.com/disforw/goveelife/blob/main/tests/fixtures/device_responses/h7126_2025-10-13.json (linked from issue #83) — Real H7126 API response with nested `modeValue.gearMode`.
6. **CanDuru4/govee H7126 fixture** — https://github.com/CanDuru4/govee/blob/master/test-data/h7126-air-purifier.json — Second independent H7126 capture with flat workMode shape.
7. **Project docs/govee-protocol-reference.md** (section 8.4) — Internal protocol reference already documenting `devices.types.air_purifier` for H7120/H7122/H7123/H7124/H7127; inconsistent with the code constant.
8. **Codebase: custom_components/govee/models/device.py:33** — Current (wrong) `DEVICE_TYPE_PURIFIER = "devices.types.purifier"`.
9. **Codebase: custom_components/govee/fan.py:40-62** — Fan platform setup; gates entity creation on `device.is_fan`.
10. **Codebase: custom_components/govee/select.py:179-193** — Purifier mode select gate on `device.is_purifier`.
11. **Codebase: custom_components/govee/models/device.py:482-515** — `get_purifier_mode_options()` parser handling nested `modeValue.gearMode` shape.
12. **Codebase: tests/test_models.py:253-255,314** — Existing `is_fan` test patterns; no `is_purifier` coverage exists.
