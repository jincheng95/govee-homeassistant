# Heater autoStop (#29) and Dehumidifier H7150 (#54)

**Date**: 2026-04-13
**Scope**: Two related open issues covering appliance device types.
**Status**: Ready for implementation — #29 is fully actionable; #54 needs user diagnostics before full implementation.

---

## Summary

Both issues share a root cause: the integration's appliance support is incomplete. For **#29** (H713C heater), the `autoStop` field of the `temperature_setting` STRUCT is never parsed from API responses, so `state.heater_auto_stop` stays `None`; every temperature change sends `autoStop=0`, silently disabling the user's chosen setting. The heater-auto-stop switch only fires when a separate `thermostatToggle` capability exists, which H713C doesn't expose. For **#54** (H7150 dehumidifier), the light-platform filter is over-permissive — any device with `supports_power` that isn't a fan becomes a light entity, and there is no `is_humidifier` property or `humidifier.py` platform. Short-term fix for #54 is a device-type filter; full fix requires diagnostics and a new `humidifier` platform using HA's `HumidifierEntity` with `HumidifierDeviceClass.DEHUMIDIFIER`. Recommend also repositioning the heater from `light + number + switch` to the HA-idiomatic `climate` platform, but that is larger scope and can follow #29's minimal fix.

---

## Research Questions

| # | Question | Answer |
|---|---|---|
| 1 | Why does autoStop always read 0 on the H713C? | `update_from_api` in `models/state.py:147-197` has no branch for `devices.capabilities.temperature_setting`. The value arrives and is dropped. |
| 2 | Why doesn't the existing heater-auto-stop switch fix it? | The switch is gated on `device.supports_thermostat_toggle` (`switch.py:82-84`), which checks for a separate `thermostatToggle` capability. H713C only exposes `autoStop` inside the `temperature_setting` STRUCT. |
| 3 | Why does H7150 appear as a light? | `light.py:71-74` creates a light for every device that `supports_power and not device.is_fan`. No dehumidifier / heater / purifier exclusions. `device.is_humidifier` doesn't exist at all. |
| 4 | What capabilities does H7150 expose? | Not publicly documented. Sibling H7151 exposes `powerSwitch`, `range/humidity` (30–80%), `work_mode/workMode` STRUCT (workMode 1/3/8 = gearMode/Auto/Dryer; modeValue Low/Med/High), and an event capability for water-tank-full (alarm type 58). H7150 likely follows the same shape; needs a diagnostic dump to confirm. |
| 5 | What HA entity type fits a dehumidifier? | Native `humidifier` platform with `HumidifierDeviceClass.DEHUMIDIFIER`. Precedent: `homeassistant/components/lg_thinq/humidifier.py`. Companion `sensor` for current humidity and `binary_sensor` for water-tank are conventional. |
| 6 | Is the current heater model (light + number + switch) HA-idiomatic? | No. HA community and precedent integrations expose heaters as `climate` entities with `hvac_modes = [OFF, HEAT, AUTO, FAN_ONLY]` + `fan_modes`. The user's comment "a generic climate thermostat needs a switch as source" is essentially asking for this. |

---

## Findings

### Heater autoStop (#29) — three compounding bugs

1. **State is never parsed**. `models/state.py:147-197` `update_from_api` handles `online`, `on_off`, `range`, `color_setting`, `toggle`, `work_mode`, `mode` — but not `temperature_setting`. The H713C's STRUCT value arrives (`{"autoStop": 1, "temperature": 22, "unit": "Celsius"}`) and is silently discarded. `state.heater_auto_stop` stays `None` forever.

2. **Number entity defaults to 0**. `number.py:309-317` sends a temperature command by reading `state.heater_auto_stop` and falling back to `0`:
   ```python
   auto_stop = 0
   if state and state.heater_auto_stop is not None:
       auto_stop = state.heater_auto_stop
   ```
   With #1 unparsed, the condition never matches. Every temperature write disables the user's autoStop preference, matching the user's complaint: "at each temperature change the auto hold switched off."

3. **Switch entity is gated on the wrong capability**. `switch.py:82-84` creates `GoveeAutoStopSwitchEntity` only when `device.supports_thermostat_toggle` — a `devices.capabilities.toggle` with instance `thermostatToggle`. The H713C diagnostic shared in the issue comment shows no such toggle — `autoStop` is inside the `temperature_setting` STRUCT instead. So the switch entity is never created for this device.

### Heater idiomatic representation

User explicitly says "on/off should be a switch, not a light — a generic thermostat helper needs a switch as source." HA precedent for smart heaters is a `climate` entity:

- `HVACMode.HEAT` ↔ `workMode=1` (gearMode) with `fan_mode` from `modeValue` (Low/Med/High)
- `HVACMode.AUTO` ↔ `workMode=3` (uses `targetTemperature`)
- `HVACMode.FAN_ONLY` ↔ `workMode=9`
- `HVACMode.OFF` ↔ powerSwitch off
- `ClimateEntityFeature.TARGET_TEMPERATURE` (5–30 °C) + `ClimateEntityFeature.FAN_MODE`

This eliminates the "power is a light" problem, the fan-mode gap, and the target-vs-current-temperature graphing gap in one change. Large scope vs. the minimal #29 fix; recommend minimal fix first, climate as a follow-up.

### Dehumidifier (#54) — routing and missing platform

1. **Filter too permissive** (`light.py:71-74`):
   ```python
   if device.supports_power and not device.is_fan:
       entities.append(GoveeLightEntity(...))
   ```
   Any `supports_power` device that isn't a fan becomes a light — heater, dehumidifier, purifier, even plugs in edge cases (plugs are routed to switch separately).

2. **`is_humidifier` property missing** (`models/device.py`). Constant `DEVICE_TYPE_HUMIDIFIER = "devices.types.humidifier"` exists (line 30) and `is_heater`, `is_purifier`, `is_plug` properties exist, but the humidifier equivalent was never added.

3. **No `humidifier.py` platform**. HA provides `homeassistant.components.humidifier.HumidifierEntity` with `HumidifierDeviceClass.DEHUMIDIFIER`. Precedent: `homeassistant/components/lg_thinq/humidifier.py`.

4. **Expected H7150 capability shape** (per H7151 public data, community.govee2mqtt#145):
   - `devices.capabilities.on_off` / `powerSwitch` — ENUM 0/1
   - `devices.capabilities.range` / `humidity` — INTEGER 30–80
   - `devices.capabilities.work_mode` / `workMode` — STRUCT: `workMode` (gearMode=1, Auto=3, Dryer=8) + `modeValue` (Low=1, Med=2, High=3 for gearMode)
   - `devices.capabilities.event` — water-full alarm type 58
   - Plus (not confirmed) `devices.capabilities.property` / `sensorHumidity` for current humidity

---

## Compatibility Analysis

| Change | Files | Risk | Requires user input |
|---|---|---|---|
| Parse `temperature_setting.autoStop` (#29 fix 1) | `models/state.py` | Low | No |
| Add `supports_temperature_setting_auto_stop` + route to switch (#29 fix 2) | `models/device.py`, `switch.py` | Low | No |
| Fix number-entity preserve logic (#29 fix 3) | `number.py` | Low | No |
| Add `is_humidifier` + tighten light filter (#54 fix 1) | `models/device.py`, `light.py` | Low — additive filter | No |
| New `humidifier.py` platform (#54 fix 2) | new file, `__init__.py`, `coordinator.py` | Medium — unverified capability shape | Yes — H7150 diagnostic dump |
| Heater `climate` platform (follow-up) | new `climate.py`, deprecate heater-light routing | Medium-high | Diagnostic confirming `sensorTemperature` / `targetTemperature` presence |

No framework changes. No new dependencies. Python 3.12+ and existing coordinator flow support all of the above.

---

## Recommendation

Land the two minimal fixes **now**, and **block** the larger refactors behind user-supplied diagnostics:

1. **#29 minimal** — Parse `temperature_setting` STRUCT into `state.heater_temperature` + `state.heater_auto_stop`, add `supports_temperature_setting_auto_stop` gate so the switch is created for struct-only heaters, and have `GoveeAutoStopSwitchEntity` send a `TemperatureSettingCommand(temperature=<preserved>, auto_stop=<new>)` when the toggle capability is absent. This alone unblocks the user and makes every future temperature write preserve autoStop.

2. **#54 minimal** — Add `is_humidifier`, tighten the light filter to exclude all appliance device types (`is_humidifier`, `is_heater`, `is_purifier`). H7150 will no longer appear as a bogus light. The device gets a minimal `switch` for power via the existing plug-and-toggle paths; that's usable until step 3.

3. **#54 full (blocked)** — Request H7150 diagnostic from the user. Once we have the capability list, implement `humidifier.py` using `HumidifierEntity`, `HumidifierDeviceClass.DEHUMIDIFIER`, and a flattened mode list (`["Auto", "Low", "Medium", "High", "Dryer"]`). Add a companion `sensor` for current humidity and a `binary_sensor` for water-tank-full (Govee event type 58).

4. **#29 full (follow-up)** — Introduce a `climate.py` platform mapping Govee workMode/gearMode/targetTemperature to `HVACMode` + `fan_mode`. Deprecate the heater-light path once parity is proven. Gate behind a feature flag for one release to avoid breaking automations.

---

## Implementation Sketch

### #29 minimal fix

**`custom_components/govee/models/state.py`** (after line 196, inside `update_from_api`):
```python
elif cap_type == "devices.capabilities.temperature_setting":
    if instance == "targetTemperature" and isinstance(value, dict):
        temp_val = value.get("temperature")
        if temp_val is not None:
            self.heater_temperature = int(temp_val)
        auto_stop = value.get("autoStop")
        if auto_stop is not None:
            self.heater_auto_stop = int(auto_stop)
```

**`custom_components/govee/models/device.py`** (alongside `supports_thermostat_toggle`):
```python
@property
def supports_temperature_setting_auto_stop(self) -> bool:
    return any(
        cap.type == CAPABILITY_TEMPERATURE_SETTING
        and cap.instance == INSTANCE_TARGET_TEMPERATURE
        and any(f.get("fieldName") == "autoStop" for f in cap.parameters.get("fields", []))
        for cap in self.capabilities
    )
```

**`custom_components/govee/switch.py`**:
- Change the guard at line 82 from `device.supports_thermostat_toggle` to
  `device.supports_thermostat_toggle or device.supports_temperature_setting_auto_stop`.
- In `GoveeAutoStopSwitchEntity.async_turn_on/off`, branch:
  ```python
  if self._device.supports_thermostat_toggle:
      await self.coordinator.async_control_device(
          self._device_id,
          ToggleCommand(toggle_instance=INSTANCE_THERMOSTAT_TOGGLE, enabled=turn_on),
      )
  else:
      state = self.coordinator.get_state(self._device_id)
      temp = state.heater_temperature if state and state.heater_temperature else 20
      await self.coordinator.async_control_device(
          self._device_id,
          TemperatureSettingCommand(temperature=temp, auto_stop=1 if turn_on else 0),
      )
  ```

**`custom_components/govee/number.py`** (`GoveeHeaterTemperatureNumber`): already preserves `auto_stop` when known; after fix 1 it will actually have a value.

### #54 minimal fix

**`custom_components/govee/models/device.py`**:
```python
@property
def is_humidifier(self) -> bool:
    """Humidifier / dehumidifier device."""
    return self.device_type == DEVICE_TYPE_HUMIDIFIER
```

And update `is_light_device` to return `False` when `is_humidifier or is_heater or is_purifier or is_fan or is_plug`.

**`custom_components/govee/light.py`** (line 71-74):
```python
for device in coordinator.devices.values():
    if device.is_light_device and device.supports_power:
        entities.append(GoveeLightEntity(coordinator, device, enable_scenes))
```

**`custom_components/govee/switch.py`** — allow humidifier `powerSwitch` to materialize as a switch (same pattern as plug). Optional interim until humidifier.py ships.

### #54 full plan (blocked on diagnostics)

- New `custom_components/govee/humidifier.py` with `GoveeDehumidifierEntity(HumidifierEntity, CoordinatorEntity)`.
- `_attr_device_class = HumidifierDeviceClass.DEHUMIDIFIER`.
- `_attr_supported_features = HumidifierEntityFeature.MODES`.
- `available_modes = ["Auto", "Low", "Medium", "High", "Dryer"]` (flattened from Govee workMode + modeValue).
- `target_humidity` from `range/humidity`; `current_humidity` from the property capability once confirmed.
- `HumidifierAction.DRYING` when `is_on else HumidifierAction.OFF`.
- Add `Platform.HUMIDIFIER` to `__init__.py` PLATFORMS.

### #29 full plan (follow-up)

- New `custom_components/govee/climate.py` modeled on the HA developer-docs climate template.
- Map `powerSwitch` + `workMode` + `modeValue` to HVAC/fan modes.
- Use `ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.FAN_MODE`.
- Deprecate heater routing in `light.py` behind a migration; keep existing `number.py` temperature entity or move to climate's `target_temperature`.

---

## Risks

- **Temperature command without preserved state**: after #29 fix 1, the autoStop value is populated on first poll. In the window before first poll, a temperature write would still default to 0. Mitigation: set `state.heater_auto_stop` optimistically on any command that carries it, and pass through unchanged values in `GoveeHeaterTemperatureNumber`.
- **H7150 capability assumption**: the H7151 capability shape is our only public reference. If H7150 differs (no Dryer, different range), the humidifier entity will need adjustment after real diagnostics. Mitigation: ship minimal filter fix first; request diagnostics in the issue; build humidifier.py when data is in hand.
- **Climate refactor breaking user automations**: heaters currently surface as lights + numbers + switches. Some users automate against these. Mitigation: keep the number entity for backwards compatibility through one release cycle, or ship climate behind an option.
- **`is_light_device` ripple**: tightening the gate might remove light entities from devices that are genuinely dual-purpose (e.g. heaters with indicator LEDs exposed as `supports_rgb`). None observed in the current device registry, but verify against issue reports before release.

---

## References

- Issue #29 (Heater Auto Hold): https://github.com/lasswellt/govee-homeassistant/issues/29
- Issue #54 (Dehumidifier H7150): https://github.com/lasswellt/govee-homeassistant/issues/54
- H713C diagnostic (user comment on #29): temperature_setting STRUCT with autoStop/temperature/unit fields.
- wez/govee2mqtt#145 (H7151 capability analogue): https://github.com/wez/govee2mqtt/issues/145
- wez/govee2mqtt#307 (Heater Fahrenheit bug): https://github.com/wez/govee2mqtt/issues/307
- HA Humidifier dev docs: https://developers.home-assistant.io/docs/core/entity/humidifier/
- HA Climate dev docs: https://developers.home-assistant.io/docs/core/entity/climate/
- HA LG ThinQ dehumidifier precedent: https://github.com/home-assistant/core/blob/dev/homeassistant/components/lg_thinq/humidifier.py
- HA community H7131/H7135 thread: https://community.home-assistant.io/t/govee-smart-heater-h7131-integration-for-home-assistant/667321
- Midea dehumidifier custom integration (mode+sensor pattern precedent): https://github.com/barban-dev/homeassistant-midea-dehumidifier
