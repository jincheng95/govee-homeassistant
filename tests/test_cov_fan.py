"""Coverage tests for the fan platform.

test_fan.py covers the H7101/H7106/H7107 shapes and the Tower Fan MQTT
oscillation path. This file covers what is left: platform wiring, the
defensive branches of the workMode capability parser (malformed speed
options, Auto with its own speed range, duplicate and valueless presets,
devices without an Auto mode), the state-property fallbacks, and the
ceiling fan's RestoreEntity restoration and pushed-state properties.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

from homeassistant.components.fan import DIRECTION_FORWARD, DIRECTION_REVERSE, FanEntityFeature
from homeassistant.core import State
import pytest

from custom_components.govee import fan as fan_mod
from custom_components.govee.fan import (
    PRESET_MODE_AUTO,
    PRESET_MODE_NORMAL,
    GoveeCeilingFanEntity,
    GoveeFanEntity,
)
from custom_components.govee.models import (
    GoveeCapability,
    GoveeDevice,
    GoveeDeviceState,
    ModeCommand,
    ToggleCommand,
    WorkModeCommand,
)
from custom_components.govee.models.device import (
    CAPABILITY_MODE,
    CAPABILITY_ON_OFF,
    CAPABILITY_TOGGLE,
    CAPABILITY_WORK_MODE,
    DEVICE_TYPE_FAN,
    DEVICE_TYPE_LIGHT,
    INSTANCE_FAN_OSCILLATE,
    INSTANCE_FAN_SPEED_MODE,
    INSTANCE_FAN_TOGGLE,
    INSTANCE_OSCILLATION,
    INSTANCE_POWER,
    INSTANCE_REVERSE_AIRFLOW,
    INSTANCE_WORK_MODE,
)

# --------------------------------------------------------------------------- #
# Device builders
# --------------------------------------------------------------------------- #


def _cap(cap_type: str, instance: str, params: dict | None = None) -> GoveeCapability:
    return GoveeCapability(type=cap_type, instance=instance, parameters=params or {})


def _gear_options() -> dict:
    return {
        "name": "gearMode",
        "options": [{"name": "Low", "value": 1}, {"name": "Medium", "value": 2}, {"name": "High", "value": 3}],
    }


def _fan(work_modes: list[dict], mode_values: list[dict], sku: str = "H7101") -> GoveeDevice:
    """A standalone fan with the given workMode / modeValue option lists."""
    return GoveeDevice(
        device_id="AA:BB:CC:DD:EE:FF:71:01",
        sku=sku,
        name="Test Fan",
        device_type=DEVICE_TYPE_FAN,
        capabilities=(
            _cap(CAPABILITY_ON_OFF, INSTANCE_POWER),
            _cap(CAPABILITY_TOGGLE, INSTANCE_OSCILLATION),
            _cap(
                CAPABILITY_WORK_MODE,
                INSTANCE_WORK_MODE,
                {
                    "dataType": "STRUCT",
                    "fields": [
                        {"fieldName": "workMode", "options": work_modes},
                        {"fieldName": "modeValue", "options": mode_values},
                    ],
                },
            ),
        ),
        is_group=False,
    )


def _ceiling_fan(*, reverse: bool = True, oscillate: bool = False) -> GoveeDevice:
    """A ceiling-fan-with-light combo (H1310 shape, H1370 when oscillate=True)."""
    on_off = {"dataType": "ENUM", "options": [{"name": "on", "value": 1}, {"name": "off", "value": 0}]}
    caps = [
        _cap(CAPABILITY_ON_OFF, INSTANCE_POWER),
        _cap(CAPABILITY_TOGGLE, INSTANCE_FAN_TOGGLE, on_off),
        _cap(
            CAPABILITY_MODE,
            INSTANCE_FAN_SPEED_MODE,
            {"dataType": "ENUM", "options": [{"name": f"Speed {i}", "value": i} for i in range(1, 7)]},
        ),
    ]
    if reverse:
        caps.append(_cap(CAPABILITY_TOGGLE, INSTANCE_REVERSE_AIRFLOW, on_off))
    if oscillate:
        caps.append(_cap(CAPABILITY_TOGGLE, INSTANCE_FAN_OSCILLATE, on_off))
    return GoveeDevice(
        device_id="AA:BB:CC:DD:EE:FF:13:70" if oscillate else "AA:BB:CC:DD:EE:FF:13:10",
        sku="H1370" if oscillate else "H1310",
        name="Office Fan",
        device_type=DEVICE_TYPE_LIGHT,
        capabilities=tuple(caps),
        is_group=False,
    )


def _coordinator(device: GoveeDevice, state: GoveeDeviceState | None) -> MagicMock:
    coordinator = MagicMock()
    coordinator.devices = {device.device_id: device}
    coordinator.get_state = MagicMock(return_value=state)
    coordinator.async_control_device = AsyncMock(return_value=True)
    coordinator.last_update_success = True
    coordinator.mqtt_connected = False
    return coordinator


def _state(device: GoveeDevice, work_mode: int | None = None, mode_value=None) -> GoveeDeviceState:
    state = GoveeDeviceState.create_empty(device.device_id)
    state.online = True
    state.power_state = True
    state.work_mode = work_mode
    state.mode_value = mode_value
    return state


def _fan_entity(device: GoveeDevice, state: GoveeDeviceState | None = None) -> tuple[GoveeFanEntity, MagicMock]:
    coordinator = _coordinator(device, state)
    return GoveeFanEntity(coordinator, device), coordinator


def _ceiling_entity(device: GoveeDevice, state: GoveeDeviceState | None = None):
    coordinator = _coordinator(device, state)
    entity = GoveeCeilingFanEntity(coordinator, device)
    entity.hass = MagicMock()
    entity.async_write_ha_state = MagicMock()
    return entity, coordinator


# --------------------------------------------------------------------------- #
# async_setup_entry
# --------------------------------------------------------------------------- #


class TestSetupEntry:
    async def test_creates_fan_and_ceiling_fan_entities(self, mock_fan_device, mock_light_device, caplog):
        ceiling = _ceiling_fan()
        coordinator = MagicMock()
        coordinator.devices = {d.device_id: d for d in (mock_fan_device, ceiling, mock_light_device)}
        entry = MagicMock()
        entry.runtime_data = coordinator
        entry.options = {}
        added: list = []

        with caplog.at_level(logging.DEBUG, logger="custom_components.govee.fan"):
            await fan_mod.async_setup_entry(MagicMock(), entry, added.extend)

        by_type = {type(e).__name__: e for e in added}
        assert set(by_type) == {"GoveeFanEntity", "GoveeCeilingFanEntity"}
        assert by_type["GoveeFanEntity"]._device is mock_fan_device
        assert by_type["GoveeCeilingFanEntity"]._device is ceiling
        assert "Creating fan entity for Living Room Fan (H7101)" in caplog.text
        assert "Creating ceiling fan entity for Office Fan (H1310)" in caplog.text
        assert "Set up 2 Govee fan entities" in caplog.text


# --------------------------------------------------------------------------- #
# workMode capability parsing
# --------------------------------------------------------------------------- #


class TestWorkModeParsing:
    def test_malformed_speed_options_are_skipped(self):
        # gearMode advertises no usable speed (missing, non-numeric, zero), so
        # the entity falls back to the flattened options and finally to the
        # default three speeds; Sleep keeps the one valid speed it has.
        device = _fan(
            work_modes=[{"name": "gearMode", "value": 1}, {"name": "Auto", "value": 3}, {"name": "Sleep", "value": 5}],
            mode_values=[
                {"name": "gearMode", "options": [{}, {"value": "high"}, {"value": 0}]},
                {"name": "Auto", "defaultValue": 0},
                {"name": "Sleep", "options": [{}, {"value": "x"}, {"value": 1}]},
            ],
        )
        entity, _ = _fan_entity(device)
        assert entity._fan_speeds == [1, 2, 3]
        assert entity.speed_count == 3
        assert entity._work_mode_speed_values[5] == [1]
        assert 5 in entity._speed_work_modes

    def test_manual_default_value_becomes_the_only_speed(self):
        # No nested options for gearMode: the flattened fallback carries the
        # capability's defaultValue as the single manual speed.
        device = _fan(
            work_modes=[{"name": "gearMode", "value": 1}, {"name": "Auto", "value": 3}],
            mode_values=[{"name": "gearMode", "defaultValue": 2}, {"name": "Auto", "defaultValue": 0}],
        )
        entity, _ = _fan_entity(device)
        assert entity._fan_speeds == [2]
        assert entity.speed_count == 1
        assert entity.percentage_step == 100

    def test_manual_default_of_none_falls_back_to_three_speeds(self):
        device = _fan(
            work_modes=[{"name": "gearMode", "value": 1}, {"name": "Auto", "value": 3}],
            mode_values=[{"name": "gearMode", "defaultValue": None}, {"name": "Auto", "defaultValue": 0}],
        )
        entity, _ = _fan_entity(device)
        assert entity._fan_speeds == [1, 2, 3]

    def test_auto_with_its_own_speeds_is_speed_bearing(self):
        device = _fan(
            work_modes=[{"name": "gearMode", "value": 1}, {"name": "Auto", "value": 3}],
            mode_values=[_gear_options(), {"name": "Auto", "options": [{"value": 1}, {"value": 2}]}],
        )
        entity, coordinator = _fan_entity(device, _state(device, work_mode=3, mode_value=2))
        assert 3 in entity._speed_work_modes
        assert entity._work_mode_speed_values[3] == [1, 2]
        assert entity._preset_commands[PRESET_MODE_AUTO] == (3, 1)
        # Auto reports a percentage and keeps speed changes in Auto.
        assert entity.percentage == 100

    async def test_set_percentage_stays_in_speed_bearing_auto(self):
        device = _fan(
            work_modes=[{"name": "gearMode", "value": 1}, {"name": "Auto", "value": 3}],
            mode_values=[_gear_options(), {"name": "Auto", "options": [{"value": 1}, {"value": 2}]}],
        )
        entity, coordinator = _fan_entity(device, _state(device, work_mode=3, mode_value=2))
        await entity.async_set_percentage(50)
        cmd = coordinator.async_control_device.call_args[0][1]
        assert isinstance(cmd, WorkModeCommand)
        assert (cmd.work_mode, cmd.mode_value) == (3, 1)

    def test_presets_without_a_name_or_value_are_dropped(self):
        device = _fan(
            work_modes=[
                {"name": "gearMode", "value": 1},
                {"name": "Turbo"},
                {"value": 9},
                {"name": "Auto", "value": 3},
            ],
            mode_values=[_gear_options(), {"name": "Auto", "defaultValue": 0}],
        )
        entity, _ = _fan_entity(device)
        assert entity.preset_modes == [PRESET_MODE_NORMAL, PRESET_MODE_AUTO]

    def test_duplicate_presets_keep_the_first_work_mode(self):
        device = _fan(
            work_modes=[
                {"name": "gearMode", "value": 1},
                {"name": "Nature", "value": 6},
                {"name": "NATURE", "value": 7},
                {"name": "Normal", "value": 8},  # aliases onto the manual preset
                {"name": "Auto", "value": 3},
            ],
            mode_values=[_gear_options(), {"name": "Auto", "defaultValue": 0}],
        )
        entity, _ = _fan_entity(device)
        assert entity.preset_modes == [PRESET_MODE_NORMAL, PRESET_MODE_AUTO, "nature"]
        assert entity._preset_work_modes["nature"] == 6
        assert entity._preset_work_modes[PRESET_MODE_NORMAL] == 1

    def test_fan_without_auto_still_offers_a_default_auto_preset(self):
        device = _fan(
            work_modes=[{"name": "gearMode", "value": 1}, {"name": "Sleep", "value": 5}],
            mode_values=[_gear_options(), {"name": "Sleep", "defaultValue": 0}],
        )
        entity, _ = _fan_entity(device)
        assert entity.preset_modes == [PRESET_MODE_NORMAL, "sleep", PRESET_MODE_AUTO]
        assert entity._preset_commands[PRESET_MODE_AUTO] == (3, 0)
        assert 3 in entity._speedless_work_modes


class TestStaticHelpers:
    @pytest.mark.parametrize(
        ("option", "expected"),
        [
            ({"defaultValue": 4}, 4),
            ({"options": [{"value": 2}]}, 2),
            ({"options": [{}], "value": 3}, 3),
            ({"value": 7}, 7),
            ({}, 0),
            ({"defaultValue": "abc"}, 0),
        ],
    )
    def test_extract_mode_value(self, option, expected):
        assert GoveeFanEntity._extract_mode_value(option) == expected

    @pytest.mark.parametrize(
        ("name", "expected"),
        [(None, None), (5, None), ("", None), ("   ", None), (" Fan  Speed ", "fan speed")],
    )
    def test_normalize_mode_name(self, name, expected):
        assert GoveeFanEntity._normalize_mode_name(name) == expected


# --------------------------------------------------------------------------- #
# State-derived properties
# --------------------------------------------------------------------------- #


class TestStateProperties:
    def test_percentage_and_preset_unknown_without_state(self, mock_fan_device):
        entity, _ = _fan_entity(mock_fan_device, None)
        assert entity.percentage is None
        assert entity.preset_mode is None

    def test_preset_unknown_without_work_mode(self, mock_fan_device):
        entity, _ = _fan_entity(mock_fan_device, _state(mock_fan_device, work_mode=None, mode_value=2))
        assert entity.preset_mode is None

    @pytest.mark.parametrize("mode_value", [None, "abc"])
    def test_unusable_mode_value_falls_back_to_last_command(self, mock_fan_device, mode_value):
        # The device is in manual mode but reports no usable speed: show the
        # speed last commanded (the middle speed until one is sent).
        entity, _ = _fan_entity(mock_fan_device, _state(mock_fan_device, work_mode=1, mode_value=mode_value))
        assert entity.percentage == 66

    def test_unknown_work_mode_reports_normal(self, mock_fan_device):
        entity, _ = _fan_entity(mock_fan_device, _state(mock_fan_device, work_mode=99, mode_value=0))
        assert entity.preset_mode == PRESET_MODE_NORMAL


class TestPresetFallback:
    async def test_unknown_preset_keeps_the_current_manual_speed(self, mock_fan_device, caplog):
        entity, coordinator = _fan_entity(mock_fan_device, _state(mock_fan_device, work_mode=1, mode_value=3))

        with caplog.at_level(logging.WARNING, logger="custom_components.govee.fan"):
            await entity.async_set_preset_mode("bogus")

        cmd = coordinator.async_control_device.call_args[0][1]
        assert isinstance(cmd, WorkModeCommand)
        assert (cmd.work_mode, cmd.mode_value) == (1, 3)
        assert entity._last_manual_mode_value == 3
        assert "Unknown preset mode 'bogus'" in caplog.text


# --------------------------------------------------------------------------- #
# Ceiling fan
# --------------------------------------------------------------------------- #


class TestCeilingFanRestore:
    async def test_restores_the_full_previous_state(self):
        entity, _ = _ceiling_entity(_ceiling_fan(oscillate=True), _state(_ceiling_fan(oscillate=True)))
        entity.async_get_last_state = AsyncMock(
            return_value=State(
                "fan.office_fan", "on", {"percentage": 50, "direction": DIRECTION_REVERSE, "oscillating": True}
            )
        )

        await entity.async_added_to_hass()

        assert entity.is_on is True
        assert entity.percentage == 50  # speed 3 of 6
        assert entity.current_direction == DIRECTION_REVERSE
        assert entity.oscillating is True

    async def test_without_previous_state_stays_off(self):
        entity, _ = _ceiling_entity(_ceiling_fan())
        entity.async_get_last_state = AsyncMock(return_value=None)
        await entity.async_added_to_hass()
        assert entity.is_on is False
        assert entity.percentage == 0

    async def test_unparseable_percentage_is_dropped(self):
        entity, _ = _ceiling_entity(_ceiling_fan())
        entity.async_get_last_state = AsyncMock(return_value=State("fan.office_fan", "on", {"percentage": "abc"}))
        await entity.async_added_to_hass()
        assert entity.is_on is True
        assert entity.percentage is None  # on, speed unknown

    async def test_unknown_direction_and_missing_oscillation_are_ignored(self):
        entity, _ = _ceiling_entity(_ceiling_fan(oscillate=True))
        entity.async_get_last_state = AsyncMock(return_value=State("fan.office_fan", "off", {"direction": "sideways"}))
        await entity.async_added_to_hass()
        assert entity.is_on is False
        assert entity.current_direction == DIRECTION_FORWARD
        assert entity.oscillating is False


class TestCeilingFanProperties:
    def test_percentage_unknown_while_on_without_a_speed(self):
        entity, _ = _ceiling_entity(_ceiling_fan())
        entity._is_on = True
        assert entity.percentage is None

    def test_percentage_unknown_for_a_speed_the_device_no_longer_advertises(self):
        entity, _ = _ceiling_entity(_ceiling_fan())
        entity._is_on = True
        entity._speed_value = 99
        assert entity.percentage is None

    def test_direction_not_reported_without_reverse_airflow(self):
        entity, _ = _ceiling_entity(_ceiling_fan(reverse=False))
        assert entity.current_direction is None
        assert not entity.supported_features & FanEntityFeature.DIRECTION

    @pytest.mark.parametrize("swing", [True, False])
    def test_oscillating_from_pushed_state(self, swing):
        device = _ceiling_fan(oscillate=True)
        state = _state(device)
        state.ceiling_fan_swing = swing
        entity, _ = _ceiling_entity(device, state)
        entity._oscillating = not swing
        assert entity.oscillating is swing

    def test_oscillating_not_reported_without_the_capability(self):
        entity, _ = _ceiling_entity(_ceiling_fan())
        assert entity.oscillating is None

    async def test_turn_on_with_percentage_also_sets_the_speed(self):
        device = _ceiling_fan()
        entity, coordinator = _ceiling_entity(device, _state(device))

        await entity.async_turn_on(percentage=50)

        calls = [c[0][1] for c in coordinator.async_control_device.call_args_list]
        assert isinstance(calls[0], ToggleCommand)
        assert (calls[0].toggle_instance, calls[0].enabled) == (INSTANCE_FAN_TOGGLE, True)
        assert isinstance(calls[1], ModeCommand)
        assert (calls[1].mode_instance, calls[1].value) == (INSTANCE_FAN_SPEED_MODE, 3)
        assert entity.is_on is True
        assert entity.percentage == 50
        assert entity.async_write_ha_state.call_count == 2
