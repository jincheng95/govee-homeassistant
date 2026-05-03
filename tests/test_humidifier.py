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
    return GoveeHumidifierEntity(coordinator, h7150_device)


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

    def test_target_humidity_none_outside_auto(self, entity, coordinator, h7150_state):
        h7150_state.work_mode = 1  # gearMode
        h7150_state.mode_value = 1
        coordinator.get_state.return_value = h7150_state
        assert entity.target_humidity is None


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
        cmd = coordinator.async_control_device.call_args[0][1]
        assert isinstance(cmd, WorkModeCommand)
        assert cmd.work_mode == 3 and cmd.mode_value == 45

    @pytest.mark.asyncio
    async def test_set_humidity_clamps_to_range(self, entity, coordinator):
        await entity.async_set_humidity(10)
        cmd = coordinator.async_control_device.call_args[0][1]
        assert cmd.mode_value == 30  # clamped up
        await entity.async_set_humidity(99)
        cmd = coordinator.async_control_device.call_args[0][1]
        assert cmd.mode_value == 80  # clamped down

    @pytest.mark.asyncio
    async def test_set_mode_rejects_unknown(self, entity):
        with pytest.raises(ValueError):
            await entity.async_set_mode("bogus")


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
