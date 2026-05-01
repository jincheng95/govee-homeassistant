# H5089 Outlet Extender Nightlight — Color Regression

<!-- no-registry: scope is contingent on user-supplied diagnostics; fix path not confirmed until device_type known -->

## Summary

H5089 ("Govee Outlet Extender with Nightlight") has no color control because
`is_plug` blocks `is_light_device`, so no `GoveeLightEntity` is created — only
a plug switch and a binary nightlight toggle. No commit between v2026.4.3 and HEAD
changed this path, so either (a) the device never actually had a proper color light
entity and the user is misremembering, or (b) H5089 does not report
`devices.types.socket` and the actual `device_type` from the API is something else.
User diagnostics are the blocking dependency before any fix lands.

## Research Questions

**Q1: What changed between v2026.4.3 and v2026.4.7 that broke H5089 color?**
No commit changed the `is_plug → is_light_device` path. Either the device_type
differs from assumption, or the regression is in capability parsing. Diagnostics required.

**Q2: How is H5089 currently routed?**
If `device_type = "devices.types.socket"`: `is_plug=True` → `is_light_device=False`
→ no `GoveeLightEntity` → no color control. Only `GoveePlugSwitchEntity` (outlet)
and `GoveeNightLightSwitchEntity` (binary toggle) are created.

**Q3: What is the fix?**
If `is_plug` and the device has RGB/color-temp capability, add a `GoveeLightEntity`
alongside the plug switch. If device_type is not socket, root cause is elsewhere.

## Findings

### Entity routing (current)

- `device.py:291-293` — `is_plug = (device_type == "devices.types.socket")`
- `device.py:631-646` — `is_light_device` returns `False` when `is_plug` is True
- `light.py:76` — `GoveeLightEntity` created only when `is_light_device` is True
- `switch.py:52-57` — plug + nightlight switch created for `is_plug` devices; nightlight switch is binary (no color)

### Commits since v2026.4.3 — none affect H5089

`git log v2026.4.3..HEAD` touching device.py / light.py:

| SHA | Commit | H5089 impact |
|-----|--------|-------------|
| f6b6948 | feat: Heater autoStop + dehumidifier routing | Added `is_heater`/`is_humidifier` guard — no socket effect |
| 46e0eb9 | fix: Recognize devices.types.air_purifier | No socket impact |
| 996d400 | feat: Humidifier platform | Expanded `is_humidifier` — no socket impact |

### Two scenarios for why color appeared to work at v2026.4.3

1. **H5089 reports `devices.types.socket`** — `is_plug` has always blocked color.
   Color control never truly worked via a light entity; user may be confusing the
   nightlight binary toggle with color control, or a different version was in use.
2. **H5089 reports a different `device_type`** (e.g., `devices.types.light`) —
   `is_plug` would be False, `supports_rgb` would make `is_light_device=True`, and
   a light entity would be created. In this case, capability parsing or a command
   routing change (not yet identified) is the culprit.

## Compatibility Analysis

- Fix touches `light.py` async_setup_entry only (single file, ~5 lines)
- No dependency changes required
- Pattern mirrors the existing `is_fan` / `is_humidifier` routing logic already in the codebase

## Recommendation

**Do not ship a fix without diagnostics.** The fix path differs entirely depending on
`device_type`. Ask the user (JW1964 on issue #59) for their HA diagnostics JSON — it
will show `device_type` and all capability instances for H5089.

**If `device_type = "devices.types.socket"` (most likely):**
```python
# light.py async_setup_entry — after existing light entity block
if device.is_plug and (device.supports_rgb or device.supports_color_temp):
    entities.append(GoveeLightEntity(coordinator, device))
```
This creates a light entity alongside the plug switch. The nightlight toggle in
switch.py remains as an enable/disable for the nightlight mode.

**If `device_type != "devices.types.socket"`:** trace the capability instance names
from diagnostics and check command routing in `coordinator.py`.

## Implementation Sketch

1. Get diagnostics JSON from user (issue #59 / JW1964)
2. Confirm `device_type` and capability instances (looking for `colorRgb`, `colorTemInKelvin`, or similar on the nightlight)
3. If socket + rgb: add the 2-line fix to `light.py` (see above)
4. Add `GoveeNightLightSwitchEntity` note to switch.py comment — it is binary only; color is via light entity
5. Add H5089 fixture to `tests/` once device_type confirmed
6. Bump to v2026.4.patch

## Risks

- Shipping a fix without diagnostics may produce the wrong entity structure (double on/off, conflicting state)
- The nightlight and main outlet may share a `powerSwitch` capability — creating a `GoveeLightEntity` alongside `GoveePlugSwitchEntity` could result in two entities controlling the same power state; verify capability instances first
- No H5089 fixtures exist; regression test coverage is zero for this device

## References

- Issue #59 comment by @JW1964 (2026-04-16): H5089 color broke post-v2026.4.3
- `custom_components/govee/models/device.py:291-293` — `is_plug`
- `custom_components/govee/models/device.py:631-646` — `is_light_device`
- `custom_components/govee/light.py:76` — light entity creation gate
- `custom_components/govee/switch.py:52-57` — plug/nightlight switch creation
