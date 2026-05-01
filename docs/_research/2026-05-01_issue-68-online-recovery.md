# Issue #68 — Devices stuck `unavailable` after power-cycle

**Date**: 2026-05-01
**Type**: Bug analysis + fix
**Issue**: [#68](https://github.com/lasswellt/govee-homeassistant/issues/68)
**Reporter scenario**: RGBIC LED strip on a smart power strip. Power strip turned off overnight; in the morning, after power restored, the Govee entity stays `unavailable` in HA until the integration is reloaded. Workaround: a daily reload automation.

---

## Summary

`GoveeDeviceState.online` is set from `devices.capabilities.online` on every successful API poll (`get_device_state` builds a fresh state via `create_empty()` and applies the cloud response). Once the Govee cloud reports a device as offline, that flag is sticky until the cloud's own state cache flips it back — which can take an indefinite amount of time after a real recovery, because the cloud only updates after the device pushes a fresh state to AWS IoT.

The integration **also** receives those AWS IoT pushes directly (subscribed to the user's `GA/{account-uuid}` topic). But the existing `update_from_mqtt` ignores the implicit "this device is alive" signal — it parses `onOff`/`brightness`/`color` and never touches `state.online`. Same for `_handle_ble_advertisement`: a BLE ad is direct evidence the device is broadcasting, but it never restores `online`. So the entity stays unavailable until a poll cycle catches an `online: true` from the cloud — which, per the user, can take long enough that they automate a daily reload.

A reload works because the coordinator throws away the old state map; the next poll lands in `create_empty()` (default `online: True`) and either an `online: true` capability lands or no capability lands — both keep `online=True`.

## Root cause

1. **MQTT pushes don't reset `online`.** `GoveeDeviceState.update_from_mqtt` (`models/state.py`) only touches the fields the message carries. Receiving any push at all is direct proof of life, but that signal was unused.
2. **BLE advertisements don't reset `online`.** `_handle_ble_advertisement` (`coordinator.py`) records transport health but never updates `state.online`.
3. **BLE direct command success doesn't reset `online`.** `_try_ble_command` writes a frame to the device over BLE but never marks the device online — even though a successful BLE write is the strongest possible proof of reachability.

The "first-click does nothing, second-click works" symptom the reporter observed is consistent with this analysis: the unavailable entity rejects the first service call; by the time of the second click a poll has landed (or the cloud has caught up).

## Fix

Three small changes, all in the same direction — *direct-from-device signals reset `online=True`*:

| Location | Change |
|---|---|
| `models/state.py:update_from_mqtt` | Set `self.online = True` at the top |
| `coordinator.py:_handle_ble_advertisement` | Set `state.online = True` if the cached state was offline; log the recovery |
| `coordinator.py:_try_ble_command` (success branch) | Set `state.online = True` if the cached state was offline |
| `coordinator.py:_on_mqtt_state_update` | Log "MQTT push restored online status for X" when transitioning offline → online |

Cloud-API success is **not** treated as proof of life: Govee's REST endpoint accepts commands for offline devices and forwards through the cloud. Only direct-from-device transports (MQTT pushes, BLE advertisements, BLE writes that reach the radio) get to flip the flag.

## Tests

Added regression tests:

- `tests/test_models.py::test_mqtt_push_restores_online` — `update_from_mqtt` flips a stale `online: False` back to `True`
- `tests/test_models.py::test_mqtt_empty_push_still_restores_online` — even an empty payload counts as proof of life
- `tests/test_coordinator.py::TestMqttIntegration::test_mqtt_push_recovers_offline_device` — coordinator-layer regression
- `tests/test_coordinator.py::TestBleAdvertisementHandling::test_ble_advertisement_restores_online_after_outage` — BLE ad path

Full suite: 694 passed.

## Limitations / follow-ups

- For **cloud-only deployments** (no email/password configured → no MQTT credentials) the only path to recovery is still cloud-API polling, which depends on Govee updating its own cache. We can't fix that from the client side without a forced refresh signal.
- **Older devices wrap state in a `msg` field** (per `2026-03-30_govee-aws-iot.md`, Finding 4). We currently drop *all* `msg`-keyed messages as command echoes, which may suppress legitimate state updates from those devices. Out of scope for #68 but worth a follow-up audit — it could have the same symptom for a different subset of users.
- **Probe-on-recovery**: when MQTT reports a device back online, we could `request_refresh()` the coordinator immediately rather than waiting up to 60s for the next regular poll. Cheap follow-up if users still report a perceptible delay.
