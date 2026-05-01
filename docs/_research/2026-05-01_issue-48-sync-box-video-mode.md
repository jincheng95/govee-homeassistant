# Issue #48 — H6604 Smart AI Sync Box won't return to "Video" mode

**Date**: 2026-05-01
**Type**: Bug analysis + fix
**Issue**: [#48](https://github.com/lasswellt/govee-homeassistant/issues/48)
**Reporter**: Dragon1473 (H6604 AI Sync Box)
**Symptom**: After running a DIY scene for an NHL goal-light automation, setting "DIY scene → None" leaves the device displaying a flat white image instead of resuming HDMI video sync.

---

## Summary

`async_clear_scene` (coordinator.py) historically sent `ColorCommand(white)` to "exit" any active scene when no last colour was cached. That is correct for an LED strip — it pops back to white. For an HDMI sync box, white is exactly the wrong thing: the device's default mode is video sync of the live HDMI feed, and any colour command actively *locks* it out of that mode. The user has no UI to recover Video Mode short of using the Govee app.

H6604's only API-exposed mode controls are `lightScene` (dynamic_scene), `diyScene` (dynamic_scene), and `hdmiSource` (mode). It has neither `dreamViewToggle` nor `movie_setting`, so the existing DreamView switch can't help. Re-selecting an HDMI source via `ModeCommand(mode_instance="hdmiSource", value=N)` is the cleanest way to nudge the device back into Video Mode without sending a colour.

## Root cause

`async_clear_scene` in `coordinator.py` (around L1571) executes a chained heuristic — restore last colour, else last colour-temp, else default white — for *any* device with `supports_rgb`. The H6604 satisfies `supports_rgb` (it has `colorRgb` for manual color control). So when the user cleared a DIY scene with no prior colour cached, the integration sent `ColorCommand(RGBColor(255, 255, 255))`. The Sync Box obeyed: white static color, no video sync.

## Fix

Add an early branch in `async_clear_scene`: if `device.supports_hdmi_source`, skip the colour fallback entirely and instead send `ModeCommand(INSTANCE_HDMI_SOURCE, value=current_or_default_source)`. Re-selecting the same HDMI source triggers the Sync Box to re-engage with the live feed.

Source preference order:
1. `state.hdmi_source` if known (the user's last-selected port).
2. The first option from the `hdmiSource` capability's option list (typically HDMI 1).
3. Hardcoded fallback `1` (rare — would only hit if the capability advertised zero options, which contradicts the spec).

After the command succeeds, clear `active_scene` and `active_diy_scene` locally as before.

## Tests

Added `TestClearSceneOnHdmiSyncBox` in `tests/test_coordinator.py`:

- `test_clear_scene_reselects_known_hdmi_source` — uses cached `state.hdmi_source`
- `test_clear_scene_falls_back_to_first_hdmi_option` — defaults to first option when no state
- `test_clear_scene_does_not_send_color_command_to_sync_box` — regression guard against the original bug

Full suite: 694 → 697 passing.

## Out of scope

- **`movie_setting` capability handler** for H66A0 (TV Backlight 3 Pro). The protocol reference notes this is a distinct capability. Not addressed here because no open issue references it, and adding entity infrastructure speculatively risks UI clutter on devices that don't expose it.
- **rfg81's H605C / H6199 case (comment on #48)**: H605C exposes `dreamViewToggle`, so the existing DreamView switch should already serve the use case. H6199 uses local camera-based sync (BLE only) and is not cloud-controllable per the protocol reference §8.8. No code change needed; a follow-up comment on the issue can direct them to the existing entity.

## Notes for future maintenance

If a future user reports the same "flat white after clearing scene" symptom on a non-`hdmiSource` device, the right fix is *not* to extend this branch but to add the device's specific mode-restore command (e.g. `dreamViewToggle` for devices that have it). Sync boxes are unique in that they have no static-colour resting state.
