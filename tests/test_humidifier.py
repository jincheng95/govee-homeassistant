"""Tests for the Govee humidifier / dehumidifier platform (issue #54).

Exercises the H7150 diagnostic shape contributed by @raixer: workMode
STRUCT with gearMode (Low/High), Auto (humidity target), Dryer.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.govee.humidifier import (
    MODE_AUTO,
    MODE_DRYER,
    MODE_HIGH,
    MODE_LOW,
    GoveeHumidifierEntity,
)
from custom_components.govee.models import (
    GoveeCapability,
    GoveeDevice,
    GoveeDeviceState,
    PowerCommand,
    RangeCommand,
    WorkModeCommand,
)
from custom_components.govee.models.device import (
    CAPABILITY_EVENT,
    CAPABILITY_ON_OFF,
    CAPABILITY_RANGE,
    CAPABILITY_WORK_MODE,
    DEVICE_TYPE_DEHUMIDIFIER,
    INSTANCE_HUMIDITY,
    INSTANCE_POWER,
    INSTANCE_WATER_FULL_EVENT,
    INSTANCE_WORK_MODE,
)

# --------------------------------------------------------------------------- #
# Fixtures — H7150 shape from real diagnostic
# --------------------------------------------------------------------------- #


@pytest.fixture
def h7150_capabilities() -> tuple[GoveeCapability, ...]:
    """Capabilities matching the H7150 diagnostic dump."""
    return (
        GoveeCapability(
            type=CAPABILITY_ON_OFF,
            instance=INSTANCE_POWER,
            parameters={},
        ),
        GoveeCapability(
            type=CAPABILITY_RANGE,
            instance=INSTANCE_HUMIDITY,
            parameters={
                "unit": "unit.percent",
                "dataType": "INTEGER",
                "range": {"min": 30, "max": 80, "precision": 1},
            },
        ),
        GoveeCapability(
            type=CAPABILITY_WORK_MODE,
            instance=INSTANCE_WORK_MODE,
            parameters={
                "dataType": "STRUCT",
                "fields": [
                    {
                        "fieldName": "workMode",
                        "dataType": "ENUM",
                        "options": [
                            {"name": "gearMode", "value": 1},
                            {"name": "Auto", "value": 3},
                            {"name": "Dryer", "value": 8},
                        ],
                    },
                    {
                        "fieldName": "modeValue",
                        "dataType": "ENUM",
                        "options": [
                            {
                                "name": "gearMode",
                                "options": [
                                    {"name": "Low", "value": 1},
                                    {"name": "High", "value": 3},
                                ],
                            },
                            {"name": "Auto", "range": {"min": 30, "max": 80}},
                            {"name": "Dryer", "value": 0},
                        ],
                    },
                ],
            },
        ),
        GoveeCapability(
            type=CAPABILITY_EVENT,
            instance=INSTANCE_WATER_FULL_EVENT,
            parameters={},
        ),
    )


@pytest.fixture
def h7150_device(h7150_capabilities) -> GoveeDevice:
    return GoveeDevice(
        device_id="0A:E8:D4:AD:FC:7A:05:2A",
        sku="H7150",
        name="Dehumidifier",
        device_type=DEVICE_TYPE_DEHUMIDIFIER,
        capabilities=h7150_capabilities,
        is_group=False,
    )


@pytest.fixture
def h7150_state(h7150_device) -> GoveeDeviceState:
    state = GoveeDeviceState(device_id=h7150_device.device_id, online=True)
    state.power_state = True
    state.work_mode = 3  # Auto
    state.mode_value = 55
    return state


@pytest.fixture
def coordinator(h7150_device, h7150_state):
    c = MagicMock()
    c.devices = {h7150_device.device_id: h7150_device}
    c.get_state = MagicMock(return_value=h7150_state)
    c.async_control_device = AsyncMock(return_value=True)
    return c


@pytest.fixture
def entity(coordinator, h7150_device) -> GoveeHumidifierEntity:
    ent = GoveeHumidifierEntity(coordinator, h7150_device)
    # Entity is not added to hass in unit tests — stub the state write that
    # async_set_humidity performs on success.
    ent.async_write_ha_state = MagicMock()
    return ent


# --------------------------------------------------------------------------- #
# Fixtures — H7152-style shape (issue #114 regression)
# --------------------------------------------------------------------------- #


@pytest.fixture
def h7152_capabilities() -> tuple[GoveeCapability, ...]:
    """H7152-style shape: Auto modeValue pinned (80..80), setpoint lives in
    the separate ``range::humidity`` capability instead (issue #114)."""
    return (
        GoveeCapability(
            type=CAPABILITY_ON_OFF,
            instance=INSTANCE_POWER,
            parameters={},
        ),
        GoveeCapability(
            type=CAPABILITY_RANGE,
            instance=INSTANCE_HUMIDITY,
            parameters={
                "unit": "unit.percent",
                "dataType": "INTEGER",
                "range": {"min": 30, "max": 80, "precision": 1},
            },
        ),
        GoveeCapability(
            type=CAPABILITY_WORK_MODE,
            instance=INSTANCE_WORK_MODE,
            parameters={
                "dataType": "STRUCT",
                "fields": [
                    {
                        "fieldName": "workMode",
                        "dataType": "ENUM",
                        "options": [
                            {"name": "gearMode", "value": 1},
                            {"name": "Auto", "value": 3},
                            {"name": "Dryer", "value": 8},
                        ],
                    },
                    {
                        "fieldName": "modeValue",
                        "dataType": "ENUM",
                        "options": [
                            {
                                "name": "gearMode",
                                "options": [
                                    {"name": "Low", "value": 1},
                                    {"name": "High", "value": 3},
                                ],
                            },
                            {"name": "Auto", "range": {"min": 80, "max": 80}},
                            {"name": "Dryer", "value": 0},
                        ],
                    },
                ],
            },
        ),
    )


@pytest.fixture
def h7152_device(h7152_capabilities) -> GoveeDevice:
    return GoveeDevice(
        device_id="1B:F9:E5:BE:0D:8B:16:3B",
        sku="H7152",
        name="Dehumidifier Pro",
        device_type=DEVICE_TYPE_DEHUMIDIFIER,
        capabilities=h7152_capabilities,
        is_group=False,
    )


@pytest.fixture
def h7152_coordinator(h7152_device):
    c = MagicMock()
    c.devices = {h7152_device.device_id: h7152_device}
    state = GoveeDeviceState(device_id=h7152_device.device_id, online=True)
    state.power_state = True
    c.get_state = MagicMock(return_value=state)
    c.async_control_device = AsyncMock(return_value=True)
    return c


@pytest.fixture
def h7152_entity(h7152_coordinator, h7152_device) -> GoveeHumidifierEntity:
    ent = GoveeHumidifierEntity(h7152_coordinator, h7152_device)
    ent.async_write_ha_state = MagicMock()
    return ent


# --------------------------------------------------------------------------- #
# Device-model helpers
# --------------------------------------------------------------------------- #


class TestDeviceModel:
    def test_dehumidifier_type_flags(self, h7150_device):
        assert h7150_device.is_humidifier is True
        assert h7150_device.is_dehumidifier is True

    def test_light_filter_excludes_dehumidifier(self, h7150_device):
        assert h7150_device.is_light_device is False

    def test_water_full_event_detected(self, h7150_device):
        assert h7150_device.supports_water_full_event is True

    def test_humidity_range(self, h7150_device):
        assert h7150_device.get_humidity_range() == (30, 80)

    def test_work_mode_options(self, h7150_device):
        opts = h7150_device.get_humidifier_work_mode_options()
        names = {o["name"]: o["value"] for o in opts}
        assert names == {"gearMode": 1, "Auto": 3, "Dryer": 8}

    def test_gear_options(self, h7150_device):
        opts = h7150_device.get_humidifier_gear_options()
        names = {o["name"]: o["value"] for o in opts}
        assert names == {"Low": 1, "High": 3}


# --------------------------------------------------------------------------- #
# Entity properties
# --------------------------------------------------------------------------- #


class TestEntityProperties:
    def test_device_class(self, entity):
        from homeassistant.components.humidifier import HumidifierDeviceClass

        assert entity.device_class == HumidifierDeviceClass.DEHUMIDIFIER

    def test_humidity_range_from_device(self, entity):
        assert entity.min_humidity == 30
        assert entity.max_humidity == 80

    def test_available_modes(self, entity):
        assert entity.available_modes == [MODE_LOW, MODE_HIGH, MODE_AUTO, MODE_DRYER]

    def test_is_on(self, entity):
        assert entity.is_on is True

    def test_mode_auto(self, entity):
        assert entity.mode == MODE_AUTO

    def test_mode_low(self, entity, coordinator, h7150_state):
        h7150_state.work_mode = 1
        h7150_state.mode_value = 1
        coordinator.get_state.return_value = h7150_state
        assert entity.mode == MODE_LOW

    def test_mode_high(self, entity, coordinator, h7150_state):
        h7150_state.work_mode = 1
        h7150_state.mode_value = 3
        coordinator.get_state.return_value = h7150_state
        assert entity.mode == MODE_HIGH

    def test_mode_dryer(self, entity, coordinator, h7150_state):
        h7150_state.work_mode = 8
        h7150_state.mode_value = 0
        coordinator.get_state.return_value = h7150_state
        assert entity.mode == MODE_DRYER

    def test_target_humidity_in_auto(self, entity):
        assert entity.target_humidity == 55

    def test_target_falls_back_outside_auto(self, entity, coordinator, h7150_state):
        # Outside Auto there is no live setpoint; the entity falls back to the
        # last user-set target (none here) then the range minimum, so HA's
        # humidity dial never disappears (#118 follow-up).
        h7150_state.work_mode = 1  # gearMode
        h7150_state.mode_value = 1
        coordinator.get_state.return_value = h7150_state
        assert entity.target_humidity == 30

    def test_target_falls_back_when_auto_modevalue_unreported(
        self, entity, coordinator, h7150_state
    ):
        # Govee's /device/state poll returns modeValue 0 for Auto — it never
        # populates the live setpoint (issue #118, cross-validated against
        # govee2mqtt #413). The bogus 0 must never surface; with no user-set
        # target the entity falls back to the range minimum instead of None
        # (None hides HA's humidity dial, making the target unsettable).
        h7150_state.work_mode = 3  # Auto
        h7150_state.mode_value = 0
        coordinator.get_state.return_value = h7150_state
        assert entity.target_humidity == 30

    def test_target_falls_back_when_auto_modevalue_below_min(
        self, entity, coordinator, h7150_state
    ):
        # Any value outside the advertised [min, max] Auto range is treated as
        # "not reported" rather than a literal setpoint (issue #118).
        h7150_state.work_mode = 3  # Auto
        h7150_state.mode_value = 10  # below min_humidity (30)
        coordinator.get_state.return_value = h7150_state
        assert entity.target_humidity == 30

    def test_optimistic_target_beats_min_fallback(
        self, entity, coordinator, h7150_state
    ):
        # After the user sets a target, it is remembered (and restored across
        # restarts) even though the poll never reports it back (#118).
        entity._optimistic_target = 55
        h7150_state.work_mode = 3  # Auto
        h7150_state.mode_value = 0  # unreported
        coordinator.get_state.return_value = h7150_state
        assert entity.target_humidity == 55


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #


class TestEntityCommands:
    @pytest.mark.asyncio
    async def test_turn_on(self, entity, coordinator):
        await entity.async_turn_on()
        cmd = coordinator.async_control_device.call_args[0][1]
        assert isinstance(cmd, PowerCommand) and cmd.power_on is True

    @pytest.mark.asyncio
    async def test_turn_off(self, entity, coordinator):
        await entity.async_turn_off()
        cmd = coordinator.async_control_device.call_args[0][1]
        assert isinstance(cmd, PowerCommand) and cmd.power_on is False

    @pytest.mark.asyncio
    async def test_set_mode_low(self, entity, coordinator):
        await entity.async_set_mode(MODE_LOW)
        cmd = coordinator.async_control_device.call_args[0][1]
        assert isinstance(cmd, WorkModeCommand)
        assert cmd.work_mode == 1 and cmd.mode_value == 1

    @pytest.mark.asyncio
    async def test_set_mode_high(self, entity, coordinator):
        await entity.async_set_mode(MODE_HIGH)
        cmd = coordinator.async_control_device.call_args[0][1]
        assert cmd.work_mode == 1 and cmd.mode_value == 3

    @pytest.mark.asyncio
    async def test_set_mode_dryer(self, entity, coordinator):
        await entity.async_set_mode(MODE_DRYER)
        cmd = coordinator.async_control_device.call_args[0][1]
        assert cmd.work_mode == 8 and cmd.mode_value == 0

    @pytest.mark.asyncio
    async def test_set_mode_auto_preserves_setpoint(
        self, entity, coordinator, h7150_state
    ):
        # Current state is Auto/55 — switching back to Auto keeps 55.
        await entity.async_set_mode(MODE_AUTO)
        cmd = coordinator.async_control_device.call_args[0][1]
        assert cmd.work_mode == 3 and cmd.mode_value == 55

    @pytest.mark.asyncio
    async def test_set_mode_auto_from_gear_uses_min(
        self, entity, coordinator, h7150_state
    ):
        h7150_state.work_mode = 1  # gearMode
        h7150_state.mode_value = 1
        coordinator.get_state.return_value = h7150_state
        await entity.async_set_mode(MODE_AUTO)
        cmd = coordinator.async_control_device.call_args[0][1]
        assert cmd.work_mode == 3 and cmd.mode_value == 30

    @pytest.mark.asyncio
    async def test_set_humidity(self, entity, coordinator):
        await entity.async_set_humidity(45)
        # Dual write (#118): WorkModeCommand first, RangeCommand second.
        cmd = coordinator.async_control_device.call_args_list[0][0][1]
        assert isinstance(cmd, WorkModeCommand)
        assert cmd.work_mode == 3 and cmd.mode_value == 45

    @pytest.mark.asyncio
    async def test_set_humidity_sends_workmode_and_range(self, entity, coordinator):
        # H7150 hardening (#118): the Auto+setpoint work_mode write is
        # reinforced by the canonical range::humidity write (govee2mqtt's
        # field-proven path), both carrying the SAME clamped value.
        await entity.async_set_humidity(45)
        calls = coordinator.async_control_device.call_args_list
        assert len(calls) == 2
        work_cmd = calls[0][0][1]
        assert isinstance(work_cmd, WorkModeCommand)
        assert work_cmd.work_mode == 3 and work_cmd.mode_value == 45
        range_cmd = calls[1][0][1]
        assert isinstance(range_cmd, RangeCommand)
        assert range_cmd.range_instance == INSTANCE_HUMIDITY
        assert range_cmd.value == 45

    @pytest.mark.asyncio
    async def test_set_humidity_clamps_to_range(self, entity, coordinator):
        await entity.async_set_humidity(10)
        calls = coordinator.async_control_device.call_args_list
        assert calls[0][0][1].mode_value == 30  # clamped up
        assert calls[1][0][1].value == 30  # range write carries same value
        await entity.async_set_humidity(99)
        calls = coordinator.async_control_device.call_args_list
        assert calls[2][0][1].mode_value == 80  # clamped down
        assert calls[3][0][1].value == 80  # range write carries same value

    @pytest.mark.asyncio
    async def test_set_humidity_commits_optimistic_on_partial_success(
        self, entity, coordinator
    ):
        # If EITHER write is accepted, the setpoint reached the device —
        # commit the optimistic target and write state (#118).
        coordinator.async_control_device = AsyncMock(side_effect=[False, True])
        await entity.async_set_humidity(45)
        assert entity._optimistic_target == 45
        entity.async_write_ha_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_set_humidity_no_commit_when_both_fail(self, entity, coordinator):
        coordinator.async_control_device = AsyncMock(side_effect=[False, False])
        await entity.async_set_humidity(45)
        assert entity._optimistic_target is None
        entity.async_write_ha_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_set_humidity_skips_range_when_capability_absent(
        self, h7150_capabilities
    ):
        # A device whose Auto modeValue is the setpoint but which does NOT
        # advertise range::humidity must receive only the work_mode write.
        caps = tuple(
            c
            for c in h7150_capabilities
            if not (c.type == CAPABILITY_RANGE and c.instance == INSTANCE_HUMIDITY)
        )
        device = GoveeDevice(
            device_id="0A:E8:D4:AD:FC:7A:05:2A",
            sku="H7150",
            name="Dehumidifier",
            device_type=DEVICE_TYPE_DEHUMIDIFIER,
            capabilities=caps,
            is_group=False,
        )
        c = MagicMock()
        c.devices = {device.device_id: device}
        c.get_state = MagicMock(return_value=None)
        c.async_control_device = AsyncMock(return_value=True)
        ent = GoveeHumidifierEntity(c, device)
        ent.async_write_ha_state = MagicMock()

        await ent.async_set_humidity(45)

        calls = c.async_control_device.call_args_list
        assert len(calls) == 1
        cmd = calls[0][0][1]
        assert isinstance(cmd, WorkModeCommand)
        assert cmd.work_mode == 3 and cmd.mode_value == 45
        assert ent._optimistic_target == 45

    @pytest.mark.asyncio
    async def test_set_mode_rejects_unknown(self, entity):
        with pytest.raises(ValueError):
            await entity.async_set_mode("bogus")


# --------------------------------------------------------------------------- #
# H7152-style pinned-Auto regression (issue #114 discrimination)
# --------------------------------------------------------------------------- #


class TestH7152PinnedAuto:
    """Pinned-Auto SKUs (H7151/H7152) must NEVER receive the dual write.

    Their Auto modeValue is pinned (80..80); sending an arbitrary modeValue is
    rejected by Govee with "Parameter value out of range" (govee2mqtt #145).
    Locks in the #114 discrimination so the #118 H7150 reinforcement write can
    never leak to these devices.
    """

    def test_auto_modevalue_is_not_setpoint(self, h7152_device):
        assert h7152_device.auto_mode_value_is_setpoint() is False
        assert h7152_device.supports_humidity_range is True

    @pytest.mark.asyncio
    async def test_set_humidity_sends_only_range_command(
        self, h7152_entity, h7152_coordinator
    ):
        await h7152_entity.async_set_humidity(55)
        calls = h7152_coordinator.async_control_device.call_args_list
        assert len(calls) == 1
        cmd = calls[0][0][1]
        assert isinstance(cmd, RangeCommand)
        assert cmd.range_instance == INSTANCE_HUMIDITY
        assert cmd.value == 55
        assert not any(isinstance(call[0][1], WorkModeCommand) for call in calls)


# --------------------------------------------------------------------------- #
# State parsing — water-full event
# --------------------------------------------------------------------------- #


class TestStateParsing:
    def test_water_full_from_api_scalar(self):
        state = GoveeDeviceState(device_id="x")
        state.update_from_api(
            {
                "capabilities": [
                    {
                        "type": "devices.capabilities.event",
                        "instance": "waterFullEvent",
                        "state": {"value": 1},
                    }
                ]
            }
        )
        assert state.water_full is True

    def test_water_full_from_api_struct(self):
        state = GoveeDeviceState(device_id="x")
        state.update_from_api(
            {
                "capabilities": [
                    {
                        "type": "devices.capabilities.event",
                        "instance": "waterFullEvent",
                        "state": {"value": {"state": True}},
                    }
                ]
            }
        )
        assert state.water_full is True

    def test_work_mode_parsed_for_dehumidifier(self):
        state = GoveeDeviceState(device_id="x")
        state.update_from_api(
            {
                "capabilities": [
                    {
                        "type": "devices.capabilities.work_mode",
                        "instance": "workMode",
                        "state": {"value": {"workMode": 3, "modeValue": 55}},
                    }
                ]
            }
        )
        assert state.work_mode == 3
        assert state.mode_value == 55
