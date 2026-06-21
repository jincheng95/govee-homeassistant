"""Tests for issue #114 — multi-SKU feature support (Phase A + B).

Phase A: AQI + filter-life sensors (H5106/H7124/H7126); H7152 dehumidifier
configured-humidity setpoint (range::humidity) + Medium gear mode.
Phase B: H5089 per-outlet switches; H1310/H1370 main/background light toggles;
H7124 fan presets (Sleep/Auto/Turbo).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.govee.models import (
    GoveeCapability,
    GoveeDevice,
    GoveeDeviceState,
    ModeCommand,
    RangeCommand,
    SnapshotCommand,
    ToggleCommand,
    WorkModeCommand,
)
from custom_components.govee.models.device import (
    CAPABILITY_COLOR_SETTING,
    CAPABILITY_DYNAMIC_SCENE,
    CAPABILITY_EVENT,
    CAPABILITY_MODE,
    CAPABILITY_ON_OFF,
    CAPABILITY_PROPERTY,
    CAPABILITY_RANGE,
    CAPABILITY_TOGGLE,
    CAPABILITY_WORK_MODE,
    DEVICE_TYPE_DEHUMIDIFIER,
    DEVICE_TYPE_LIGHT,
    DEVICE_TYPE_PLUG,
    DEVICE_TYPE_PURIFIER,
    DEVICE_TYPE_THERMOMETER,
    INSTANCE_BRIGHTNESS,
    INSTANCE_COLOR_RGB,
    INSTANCE_HUMIDITY,
    INSTANCE_POWER,
    INSTANCE_WORK_MODE,
)


def _cap(cap_type: str, instance: str, params: dict | None = None) -> GoveeCapability:
    return GoveeCapability(type=cap_type, instance=instance, parameters=params or {})


# --------------------------------------------------------------------------- #
# Device fixtures (shapes from issue-#114 diagnostics)
# --------------------------------------------------------------------------- #

_H7124_WORKMODE = {
    "dataType": "STRUCT",
    "fields": [
        {
            "fieldName": "workMode",
            "options": [
                {"name": "gearMode", "value": 1},
                {"name": "Sleep", "value": 5},
                {"name": "Auto", "value": 3},
                {"name": "Turbo", "value": 7},
            ],
        },
        {
            "fieldName": "modeValue",
            "options": [
                {
                    "name": "gearMode",
                    "options": [
                        {"name": "Low", "value": 1},
                        {"name": "Medium", "value": 2},
                        {"name": "High", "value": 3},
                    ],
                },
                {"defaultValue": 0, "name": "Sleep"},
                {"defaultValue": 0, "name": "Auto"},
                {"defaultValue": 0, "name": "Turbo"},
            ],
        },
    ],
}

_H7152_WORKMODE = {
    "dataType": "STRUCT",
    "fields": [
        {
            "fieldName": "workMode",
            "options": [
                {"name": "gearMode", "value": 1},
                {"name": "Auto", "value": 3},
                {"name": "Dryer", "value": 8},
            ],
        },
        {
            "fieldName": "modeValue",
            "options": [
                {
                    "name": "gearMode",
                    "options": [
                        {"name": "Low", "value": 1},
                        {"name": "Medium", "value": 2},
                        {"name": "High", "value": 3},
                    ],
                },
                {"name": "Auto", "range": {"min": 80, "max": 80}},
                {"defaultValue": 0, "name": "Dryer"},
            ],
        },
    ],
}

_HUMIDITY_RANGE = {
    "unit": "unit.percent",
    "dataType": "INTEGER",
    "range": {"min": 30, "max": 80, "precision": 1},
}

# nightlightScene options differ per SKU (H5089 0-based, H7124 1-based), so the
# select must read them from the capability rather than hardcoding.
_H5089_NIGHTLIGHT_SCENE = {
    "dataType": "ENUM",
    "options": [
        {"name": "Forest", "value": 0},
        {"name": "Ocean", "value": 1},
        {"name": "Wetland", "value": 2},
        {"name": "Leisurely", "value": 3},
        {"name": "Asleep", "value": 4},
    ],
}
_H7124_NIGHTLIGHT_SCENE = {
    "dataType": "ENUM",
    "options": [
        {"name": "Forest", "value": 1},
        {"name": "Ocean", "value": 2},
        {"name": "Wetland", "value": 3},
        {"name": "Leisurely", "value": 4},
        {"name": "Asleep", "value": 5},
    ],
}


def _h5106() -> GoveeDevice:
    return GoveeDevice(
        device_id="AA:BB:CC:DD:EE:FF:51:06",
        sku="H5106",
        name="Garage AQI Monitor",
        device_type=DEVICE_TYPE_THERMOMETER,
        capabilities=(
            _cap(CAPABILITY_PROPERTY, "sensorTemperature"),
            _cap(CAPABILITY_PROPERTY, "sensorHumidity"),
            _cap(CAPABILITY_PROPERTY, "airQuality"),
        ),
    )


def _h7124() -> GoveeDevice:
    return GoveeDevice(
        device_id="AA:BB:CC:DD:EE:FF:71:24",
        sku="H7124",
        name="Sunroom Air Purifier",
        device_type=DEVICE_TYPE_PURIFIER,
        capabilities=(
            _cap(CAPABILITY_ON_OFF, INSTANCE_POWER),
            _cap(CAPABILITY_WORK_MODE, INSTANCE_WORK_MODE, _H7124_WORKMODE),
            _cap(CAPABILITY_TOGGLE, "nightlightToggle"),
            _cap(CAPABILITY_RANGE, INSTANCE_BRIGHTNESS),
            _cap(CAPABILITY_COLOR_SETTING, INSTANCE_COLOR_RGB),
            _cap(CAPABILITY_MODE, "nightlightScene", _H7124_NIGHTLIGHT_SCENE),
            _cap(CAPABILITY_PROPERTY, "filterLifeTime"),
            _cap(CAPABILITY_PROPERTY, "airQuality"),
        ),
    )


def _h7152() -> GoveeDevice:
    return GoveeDevice(
        device_id="AA:BB:CC:DD:EE:FF:71:52",
        sku="H7152",
        name="Basement Dehumidifier",
        device_type=DEVICE_TYPE_DEHUMIDIFIER,
        capabilities=(
            _cap(CAPABILITY_ON_OFF, INSTANCE_POWER),
            _cap(CAPABILITY_RANGE, INSTANCE_HUMIDITY, _HUMIDITY_RANGE),
            _cap(CAPABILITY_WORK_MODE, INSTANCE_WORK_MODE, _H7152_WORKMODE),
            _cap(CAPABILITY_EVENT, "waterFullEvent"),
        ),
    )


def _h5089() -> GoveeDevice:
    return GoveeDevice(
        device_id="AA:BB:CC:DD:EE:FF:50:89",
        sku="H5089",
        name="Smart Outlet Extender",
        device_type=DEVICE_TYPE_PLUG,
        capabilities=(
            _cap(CAPABILITY_ON_OFF, INSTANCE_POWER),
            _cap(CAPABILITY_TOGGLE, "nightlightToggle"),
            _cap(CAPABILITY_RANGE, INSTANCE_BRIGHTNESS),
            _cap(CAPABILITY_COLOR_SETTING, INSTANCE_COLOR_RGB),
            _cap(CAPABILITY_MODE, "nightlightScene", _H5089_NIGHTLIGHT_SCENE),
            # Order shuffled to prove sorting by socket number.
            _cap(CAPABILITY_TOGGLE, "socketToggle2"),
            _cap(CAPABILITY_TOGGLE, "socketToggle1"),
        ),
    )


def _h1310() -> GoveeDevice:
    return GoveeDevice(
        device_id="AA:BB:CC:DD:EE:FF:13:10",
        sku="H1310",
        name="Basement Bedroom Fan",
        device_type=DEVICE_TYPE_LIGHT,
        capabilities=(
            _cap(CAPABILITY_ON_OFF, INSTANCE_POWER),
            _cap(CAPABILITY_RANGE, INSTANCE_BRIGHTNESS),
            _cap(CAPABILITY_TOGGLE, "mainLightToggle"),
            _cap(CAPABILITY_TOGGLE, "backgroundLightToggle"),
            _cap(CAPABILITY_TOGGLE, "fanToggle"),
            _cap(CAPABILITY_MODE, "fanSpeedMode"),
            _cap(
                CAPABILITY_DYNAMIC_SCENE,
                "snapshot",
                {"options": [{"name": "Ambient Light w/ Fan", "value": 3862070}]},
            ),
        ),
    )


# --------------------------------------------------------------------------- #
# Device-model helpers
# --------------------------------------------------------------------------- #


class TestDeviceModelHelpers:
    def test_air_quality_and_filter_life_detection(self):
        assert _h5106().supports_air_quality is True
        assert _h5106().supports_filter_life is False
        assert _h7124().supports_air_quality is True
        assert _h7124().supports_filter_life is True

    def test_socket_toggle_instances_sorted(self):
        assert _h5089().socket_toggle_instances == ["socketToggle1", "socketToggle2"]

    def test_plain_light_has_no_socket_toggles(self):
        assert _h1310().socket_toggle_instances == []

    def test_main_background_light_toggles(self):
        dev = _h1310()
        assert dev.supports_main_light_toggle is True
        assert dev.supports_background_light_toggle is True

    def test_socket_plug_has_no_named_light_toggles(self):
        dev = _h5089()
        assert dev.supports_main_light_toggle is False
        assert dev.supports_background_light_toggle is False

    def test_humidity_range_capability(self):
        assert _h7152().supports_humidity_range is True
        assert _h7124().supports_humidity_range is False

    def test_auto_modevalue_is_setpoint_h7152_false(self):
        # H7152 pins Auto modeValue to 80/80 -> setpoint lives in range::humidity.
        assert _h7152().auto_mode_value_is_setpoint() is False

    def test_auto_modevalue_is_setpoint_h7150_true(self):
        # Real-range Auto modeValue (30-80) -> setpoint IS the modeValue.
        dev = GoveeDevice(
            device_id="x",
            sku="H7150",
            name="d",
            device_type=DEVICE_TYPE_DEHUMIDIFIER,
            capabilities=(
                _cap(
                    CAPABILITY_WORK_MODE,
                    INSTANCE_WORK_MODE,
                    {
                        "fields": [
                            {"fieldName": "workMode", "options": [{"name": "Auto", "value": 3}]},
                            {
                                "fieldName": "modeValue",
                                "options": [{"name": "Auto", "range": {"min": 30, "max": 80}}],
                            },
                        ]
                    },
                ),
            ),
        )
        assert dev.auto_mode_value_is_setpoint() is True


# --------------------------------------------------------------------------- #
# State parsing
# --------------------------------------------------------------------------- #


class TestStateParsing:
    def test_air_quality_and_humidity_parsed(self):
        state = GoveeDeviceState(device_id="x")
        state.update_from_api(
            {
                "capabilities": [
                    {"type": CAPABILITY_PROPERTY, "instance": "airQuality", "state": {"value": 1}},
                    {"type": CAPABILITY_PROPERTY, "instance": "sensorHumidity", "state": {"value": 64.2}},
                ]
            }
        )
        assert state.air_quality == 1
        assert state.sensor_humidity == 64.2

    def test_filter_life_parsed(self):
        state = GoveeDeviceState(device_id="x")
        state.update_from_api(
            {"capabilities": [{"type": CAPABILITY_PROPERTY, "instance": "filterLifeTime", "state": {"value": 89}}]}
        )
        assert state.filter_life == 89

    def test_configured_humidity_parsed(self):
        state = GoveeDeviceState(device_id="x")
        state.update_from_api(
            {"capabilities": [{"type": CAPABILITY_RANGE, "instance": "humidity", "state": {"value": 60}}]}
        )
        assert state.configured_humidity == 60

    def test_socket_toggles_parsed(self):
        state = GoveeDeviceState(device_id="x")
        state.update_from_api(
            {
                "capabilities": [
                    {"type": CAPABILITY_TOGGLE, "instance": "socketToggle1", "state": {"value": 1}},
                    {"type": CAPABILITY_TOGGLE, "instance": "socketToggle2", "state": {"value": 0}},
                ]
            }
        )
        assert state.toggles == {"socketToggle1": True, "socketToggle2": False}

    def test_empty_toggle_value_preserves_last(self):
        state = GoveeDeviceState(device_id="x")
        state.toggles["socketToggle1"] = True
        state.update_from_api(
            {"capabilities": [{"type": CAPABILITY_TOGGLE, "instance": "socketToggle1", "state": {"value": ""}}]}
        )
        # "" (offline / no value) must not clobber the last-known True.
        assert state.toggles["socketToggle1"] is True


# --------------------------------------------------------------------------- #
# Sensor entities
# --------------------------------------------------------------------------- #


def _coordinator_with_state(device: GoveeDevice, state: GoveeDeviceState) -> MagicMock:
    c = MagicMock()
    c.devices = {device.device_id: device}
    c.get_state = MagicMock(return_value=state)
    c.async_control_device = AsyncMock(return_value=True)
    c.is_bff_thermometer = MagicMock(return_value=False)
    return c


class TestSensorEntities:
    def test_air_quality_native_value(self):
        from custom_components.govee.sensor import GoveeAirQualitySensor

        dev = _h5106()
        state = GoveeDeviceState(device_id=dev.device_id)
        state.air_quality = 3
        entity = GoveeAirQualitySensor(_coordinator_with_state(dev, state), dev)
        assert entity.native_value == 3
        assert entity.unique_id == f"{dev.device_id}_air_quality"

    def test_filter_life_native_value(self):
        from custom_components.govee.sensor import GoveeFilterLifeSensor

        dev = _h7124()
        state = GoveeDeviceState(device_id=dev.device_id)
        state.filter_life = 89
        entity = GoveeFilterLifeSensor(_coordinator_with_state(dev, state), dev)
        assert entity.native_value == 89
        assert entity.unique_id == f"{dev.device_id}_filter_life"

    async def test_setup_creates_sensors(self):
        from custom_components.govee import sensor as sensor_mod

        dev = _h7124()
        state = GoveeDeviceState(device_id=dev.device_id)
        coordinator = _coordinator_with_state(dev, state)
        coordinator.mqtt_client = None
        coordinator.leak_sensors = {}
        coordinator.register_thermo_hubs = MagicMock()
        coordinator.register_leak_hubs = MagicMock()
        entry = MagicMock()
        entry.runtime_data = coordinator
        added: list = []
        await sensor_mod.async_setup_entry(MagicMock(), entry, lambda e: added.extend(e))

        names = {type(e).__name__ for e in added}
        assert "GoveeAirQualitySensor" in names
        assert "GoveeFilterLifeSensor" in names


# --------------------------------------------------------------------------- #
# Switch entities
# --------------------------------------------------------------------------- #


class TestSocketSwitch:
    @pytest.fixture
    def device(self):
        return _h5089()

    @pytest.fixture
    def state(self, device):
        s = GoveeDeviceState(device_id=device.device_id, online=True)
        s.toggles = {"socketToggle1": True, "socketToggle2": False}
        return s

    @pytest.fixture
    def entity(self, device, state):
        from custom_components.govee.switch import GoveeSocketSwitchEntity

        e = GoveeSocketSwitchEntity(
            _coordinator_with_state(device, state), device, "socketToggle1", 0
        )
        e.async_write_ha_state = MagicMock()
        return e

    def test_is_on_reads_live_state(self, entity):
        assert entity.is_on is True

    def test_name_placeholder(self, entity):
        assert entity._attr_translation_placeholders == {"socket": "1"}

    @pytest.mark.asyncio
    async def test_turn_off_sends_toggle(self, entity):
        await entity.async_turn_off()
        cmd = entity.coordinator.async_control_device.call_args[0][1]
        assert isinstance(cmd, ToggleCommand)
        assert cmd.toggle_instance == "socketToggle1"
        assert cmd.enabled is False
        assert entity.is_on is False  # written back to live state

    @pytest.mark.asyncio
    async def test_failure_does_not_flip(self, entity):
        entity.coordinator.async_control_device.return_value = False
        await entity.async_turn_off()
        assert entity.is_on is True


class TestNamedLightSwitch:
    @pytest.fixture
    def device(self):
        return _h1310()

    @pytest.fixture
    def entity(self, device):
        from custom_components.govee.const import SUFFIX_MAIN_LIGHT
        from custom_components.govee.switch import GoveeNamedLightSwitchEntity
        from custom_components.govee.models.device import INSTANCE_MAIN_LIGHT_TOGGLE

        state = GoveeDeviceState(device_id=device.device_id, online=True)
        e = GoveeNamedLightSwitchEntity(
            _coordinator_with_state(device, state),
            device,
            INSTANCE_MAIN_LIGHT_TOGGLE,
            "govee_main_light",
            SUFFIX_MAIN_LIGHT,
            "mdi:ceiling-light",
        )
        e.async_write_ha_state = MagicMock()
        return e

    def test_starts_off_optimistic(self, entity):
        assert entity.is_on is False

    @pytest.mark.asyncio
    async def test_turn_on_sends_main_toggle(self, entity):
        await entity.async_turn_on()
        cmd = entity.coordinator.async_control_device.call_args[0][1]
        assert isinstance(cmd, ToggleCommand)
        assert cmd.toggle_instance == "mainLightToggle"
        assert cmd.enabled is True
        assert entity.is_on is True


class TestSwitchPlatformWiring:
    async def _setup(self, device):
        from custom_components.govee import switch as switch_mod

        coordinator = MagicMock()
        coordinator.devices = {device.device_id: device}
        entry = MagicMock()
        entry.runtime_data = coordinator
        added: list = []
        await switch_mod.async_setup_entry(
            MagicMock(), entry, lambda ents: added.extend(ents)
        )
        return added

    async def test_h5089_creates_two_outlet_switches(self):
        added = await self._setup(_h5089())
        sockets = [e for e in added if type(e).__name__ == "GoveeSocketSwitchEntity"]
        assert len(sockets) == 2
        assert sorted(e._toggle_instance for e in sockets) == [
            "socketToggle1",
            "socketToggle2",
        ]

    async def test_h1310_creates_main_and_background_switches(self):
        added = await self._setup(_h1310())
        named = [
            e._toggle_instance
            for e in added
            if type(e).__name__ == "GoveeNamedLightSwitchEntity"
        ]
        assert sorted(named) == ["backgroundLightToggle", "mainLightToggle"]


# --------------------------------------------------------------------------- #
# Fan presets (H7124)
# --------------------------------------------------------------------------- #


class TestH7124FanPresets:
    @pytest.fixture
    def device(self):
        return _h7124()

    @pytest.fixture
    def fan(self, device):
        from custom_components.govee.fan import GoveeFanEntity

        state = GoveeDeviceState(device_id=device.device_id, online=True)
        return GoveeFanEntity(_coordinator_with_state(device, state), device)

    def test_preset_modes_include_sleep_turbo(self, fan):
        from custom_components.govee.fan import PRESET_MODE_AUTO, PRESET_MODE_NORMAL

        assert fan.preset_modes == [PRESET_MODE_NORMAL, PRESET_MODE_AUTO, "Sleep", "Turbo"]

    def test_preset_mode_maps_sleep(self, device):
        from custom_components.govee.fan import GoveeFanEntity

        state = GoveeDeviceState(device_id=device.device_id, online=True)
        state.work_mode = 5  # Sleep
        fan = GoveeFanEntity(_coordinator_with_state(device, state), device)
        assert fan.preset_mode == "Sleep"

    def test_preset_mode_maps_turbo(self, device):
        from custom_components.govee.fan import GoveeFanEntity

        state = GoveeDeviceState(device_id=device.device_id, online=True)
        state.work_mode = 7  # Turbo
        fan = GoveeFanEntity(_coordinator_with_state(device, state), device)
        assert fan.preset_mode == "Turbo"

    @pytest.mark.asyncio
    async def test_set_preset_turbo_sends_work_mode(self, fan):
        await fan.async_set_preset_mode("Turbo")
        cmd = fan.coordinator.async_control_device.call_args[0][1]
        assert isinstance(cmd, WorkModeCommand)
        assert cmd.work_mode == 7
        assert cmd.mode_value == 0


# --------------------------------------------------------------------------- #
# Dehumidifier setpoint + Medium (H7152)
# --------------------------------------------------------------------------- #


class TestH7152Humidifier:
    @pytest.fixture
    def device(self):
        return _h7152()

    @pytest.fixture
    def state(self, device):
        s = GoveeDeviceState(device_id=device.device_id, online=True)
        s.power_state = True
        s.work_mode = 3  # Auto (pinned modeValue=0 on this SKU)
        s.mode_value = 0
        s.configured_humidity = 60
        return s

    @pytest.fixture
    def entity(self, device, state):
        from custom_components.govee.humidifier import GoveeHumidifierEntity

        return GoveeHumidifierEntity(_coordinator_with_state(device, state), device)

    def test_available_modes_include_medium(self, entity):
        from custom_components.govee.humidifier import (
            MODE_AUTO,
            MODE_DRYER,
            MODE_HIGH,
            MODE_LOW,
            MODE_MEDIUM,
        )

        assert entity.available_modes == [
            MODE_LOW,
            MODE_MEDIUM,
            MODE_HIGH,
            MODE_AUTO,
            MODE_DRYER,
        ]

    def test_target_humidity_from_range_capability(self, entity):
        # Real setpoint is range::humidity=60, NOT the pinned Auto modeValue (0).
        assert entity.target_humidity == 60

    @pytest.mark.asyncio
    async def test_set_humidity_uses_range_command(self, entity):
        await entity.async_set_humidity(55)
        cmd = entity.coordinator.async_control_device.call_args[0][1]
        assert isinstance(cmd, RangeCommand)
        assert cmd.range_instance == INSTANCE_HUMIDITY
        assert cmd.value == 55

    @pytest.mark.asyncio
    async def test_set_humidity_clamps(self, entity):
        await entity.async_set_humidity(10)
        cmd = entity.coordinator.async_control_device.call_args[0][1]
        assert cmd.value == 30  # clamped to min

    @pytest.mark.asyncio
    async def test_set_mode_medium(self, entity):
        from custom_components.govee.humidifier import MODE_MEDIUM

        await entity.async_set_mode(MODE_MEDIUM)
        cmd = entity.coordinator.async_control_device.call_args[0][1]
        assert isinstance(cmd, WorkModeCommand)
        assert cmd.work_mode == 1  # gearMode
        assert cmd.mode_value == 2  # Medium


# --------------------------------------------------------------------------- #
# Phase C — nightlight controls (light entity + scene select)
# --------------------------------------------------------------------------- #


def _rgb_light_with_nightlight() -> GoveeDevice:
    """A real RGB light that also has a nightlightToggle.

    Its brightness/colour belong to the MAIN light, so it must NOT get a
    nightlight light entity (the nightlight stays a simple on/off switch).
    """
    return GoveeDevice(
        device_id="AA:BB:CC:DD:EE:FF:60:01",
        sku="H6001",
        name="Desk Lamp",
        device_type=DEVICE_TYPE_LIGHT,
        capabilities=(
            _cap(CAPABILITY_ON_OFF, INSTANCE_POWER),
            _cap(CAPABILITY_RANGE, INSTANCE_BRIGHTNESS),
            _cap(CAPABILITY_COLOR_SETTING, INSTANCE_COLOR_RGB),
            _cap(CAPABILITY_TOGGLE, "nightlightToggle"),
        ),
    )


class TestNightlightDeviceModel:
    def test_h5089_is_not_a_main_light(self):
        # Outlet extender colour belongs to the nightlight (refines #59).
        assert _h5089().is_light_device is False

    def test_h5089_has_nightlight_light(self):
        assert _h5089().has_nightlight_light is True

    def test_h7124_has_nightlight_light(self):
        assert _h7124().has_nightlight_light is True

    def test_real_light_keeps_main_light_not_nightlight_entity(self):
        dev = _rgb_light_with_nightlight()
        assert dev.is_light_device is True
        assert dev.has_nightlight_light is False

    def test_plain_purifier_without_nightlight_has_none(self):
        assert _h7152().has_nightlight_light is False

    def test_nightlight_scene_options(self):
        opts = _h7124().get_nightlight_scene_options()
        names = {o["name"]: o["value"] for o in opts}
        assert names["Forest"] == 1 and names["Asleep"] == 5
        assert _h7124().supports_nightlight_scene is True


class TestNightlightStateParsing:
    def test_nightlight_scene_parsed(self):
        state = GoveeDeviceState(device_id="x")
        state.update_from_api(
            {"capabilities": [{"type": CAPABILITY_MODE, "instance": "nightlightScene", "state": {"value": 4}}]}
        )
        assert state.nightlight_scene == 4

    def test_nightlight_toggle_parsed_into_toggles(self):
        state = GoveeDeviceState(device_id="x")
        state.update_from_api(
            {"capabilities": [{"type": CAPABILITY_TOGGLE, "instance": "nightlightToggle", "state": {"value": 1}}]}
        )
        assert state.toggles["nightlightToggle"] is True


class TestNightlightLightEntity:
    @pytest.fixture
    def device(self):
        return _h7124()

    @pytest.fixture
    def state(self, device):
        s = GoveeDeviceState(device_id=device.device_id, online=True)
        s.brightness = 100
        s.toggles = {"nightlightToggle": True}
        return s

    @pytest.fixture
    def entity(self, device, state):
        from custom_components.govee.light import GoveeNightLightEntity

        e = GoveeNightLightEntity(_coordinator_with_state(device, state), device)
        e.async_write_ha_state = MagicMock()
        return e

    def test_unique_id(self, entity, device):
        assert entity.unique_id == f"{device.device_id}_nightlight"

    def test_is_on_from_nightlight_toggle(self, entity):
        assert entity.is_on is True

    @pytest.mark.asyncio
    async def test_turn_off_sends_nightlight_toggle(self, entity):
        await entity.async_turn_off()
        cmd = entity.coordinator.async_control_device.call_args[0][1]
        assert isinstance(cmd, ToggleCommand)
        assert cmd.toggle_instance == "nightlightToggle"
        assert cmd.enabled is False
        assert entity.is_on is False

    @pytest.mark.asyncio
    async def test_turn_on_with_brightness_sends_brightness(self, device, state):
        from homeassistant.components.light import ATTR_BRIGHTNESS
        from custom_components.govee.light import GoveeNightLightEntity
        from custom_components.govee.models import BrightnessCommand

        state.toggles = {"nightlightToggle": False}
        entity = GoveeNightLightEntity(_coordinator_with_state(device, state), device)
        entity.async_write_ha_state = MagicMock()
        await entity.async_turn_on(**{ATTR_BRIGHTNESS: 255})

        cmds = [c.args[1] for c in entity.coordinator.async_control_device.call_args_list]
        assert any(isinstance(c, BrightnessCommand) for c in cmds)
        # Off -> also sends nightlightToggle on.
        assert any(isinstance(c, ToggleCommand) and c.enabled for c in cmds)


class TestNightlightSceneSelect:
    @pytest.fixture
    def device(self):
        return _h5089()

    @pytest.fixture
    def entity(self, device):
        from custom_components.govee.select import GoveeNightlightSceneSelectEntity

        state = GoveeDeviceState(device_id=device.device_id, online=True)
        state.nightlight_scene = 2  # Wetland
        e = GoveeNightlightSceneSelectEntity(
            _coordinator_with_state(device, state),
            device,
            device.get_nightlight_scene_options(),
        )
        e.async_write_ha_state = MagicMock()
        return e

    def test_options(self, entity):
        assert entity.options == ["Forest", "Ocean", "Wetland", "Leisurely", "Asleep"]

    def test_current_option_from_state(self, entity):
        assert entity.current_option == "Wetland"

    @pytest.mark.asyncio
    async def test_select_sends_mode_command(self, entity):
        await entity.async_select_option("Ocean")
        cmd = entity.coordinator.async_control_device.call_args[0][1]
        assert isinstance(cmd, ModeCommand)
        assert cmd.mode_instance == "nightlightScene"
        assert cmd.value == 1  # H5089 Ocean = 1


class TestNightlightPlatformWiring:
    async def _setup_light(self, device):
        from custom_components.govee import light as light_mod

        coordinator = MagicMock()
        coordinator.devices = {device.device_id: device}
        entry = MagicMock()
        entry.runtime_data = coordinator
        entry.options = {}
        added: list = []
        await light_mod.async_setup_entry(
            MagicMock(), entry, lambda ents: added.extend(ents)
        )
        return [type(e).__name__ for e in added]

    async def _setup_switch(self, device):
        from custom_components.govee import switch as switch_mod

        coordinator = MagicMock()
        coordinator.devices = {device.device_id: device}
        entry = MagicMock()
        entry.runtime_data = coordinator
        added: list = []
        await switch_mod.async_setup_entry(
            MagicMock(), entry, lambda ents: added.extend(ents)
        )
        return [type(e).__name__ for e in added]

    async def _setup_select(self, device):
        from custom_components.govee import select as select_mod

        coordinator = MagicMock()
        coordinator.devices = {device.device_id: device}
        entry = MagicMock()
        entry.runtime_data = coordinator
        entry.options = {}
        added: list = []
        await select_mod.async_setup_entry(
            MagicMock(), entry, lambda ents: added.extend(ents)
        )
        return [type(e).__name__ for e in added]

    async def test_h5089_light_wiring(self):
        names = await self._setup_light(_h5089())
        # Nightlight light entity, but NOT the conflated main GoveeLightEntity.
        assert "GoveeNightLightEntity" in names
        assert "GoveeLightEntity" not in names

    async def test_h5089_no_redundant_night_light_switch(self):
        names = await self._setup_switch(_h5089())
        assert "GoveeNightLightSwitchEntity" not in names

    async def test_h5089_scene_select(self):
        names = await self._setup_select(_h5089())
        assert "GoveeNightlightSceneSelectEntity" in names

    async def test_h7124_nightlight_wiring(self):
        light_names = await self._setup_light(_h7124())
        switch_names = await self._setup_switch(_h7124())
        select_names = await self._setup_select(_h7124())
        assert "GoveeNightLightEntity" in light_names
        assert "GoveeNightLightSwitchEntity" not in switch_names
        assert "GoveeNightlightSceneSelectEntity" in select_names

    async def test_real_light_keeps_switch_no_nightlight_entity(self):
        dev = _rgb_light_with_nightlight()
        light_names = await self._setup_light(dev)
        switch_names = await self._setup_switch(dev)
        assert "GoveeNightLightEntity" not in light_names
        assert "GoveeLightEntity" in light_names
        assert "GoveeNightLightSwitchEntity" in switch_names


# --------------------------------------------------------------------------- #
# Phase D — snapshots (dynamic_scene::snapshot)
# --------------------------------------------------------------------------- #


class TestSnapshotDeviceModel:
    def test_supports_snapshots(self):
        assert _h1310().supports_snapshots is True
        assert _h5089().supports_snapshots is False

    def test_get_snapshot_options(self):
        opts = _h1310().get_snapshot_options()
        assert opts == [{"name": "Ambient Light w/ Fan", "value": 3862070}]


class TestSnapshotState:
    def test_snapshot_parsed_when_present(self):
        state = GoveeDeviceState(device_id="x")
        state.update_from_api(
            {"capabilities": [{"type": CAPABILITY_DYNAMIC_SCENE, "instance": "snapshot", "state": {"value": 3862070}}]}
        )
        assert state.active_snapshot == 3862070

    def test_empty_snapshot_value_ignored(self):
        state = GoveeDeviceState(device_id="x")
        state.update_from_api(
            {"capabilities": [{"type": CAPABILITY_DYNAMIC_SCENE, "instance": "snapshot", "state": {"value": ""}}]}
        )
        assert state.active_snapshot is None


class TestSnapshotCommand:
    def test_payload(self):
        cmd = SnapshotCommand(snapshot_value=3862070)
        payload = cmd.to_api_payload()
        assert payload == {
            "type": "devices.capabilities.dynamic_scene",
            "instance": "snapshot",
            "value": 3862070,
        }


class TestSnapshotSelect:
    @pytest.fixture
    def device(self):
        return _h1310()

    @pytest.fixture
    def entity(self, device):
        from custom_components.govee.select import GoveeSnapshotSelectEntity

        state = GoveeDeviceState(device_id=device.device_id, online=True)
        e = GoveeSnapshotSelectEntity(
            _coordinator_with_state(device, state), device, device.get_snapshot_options()
        )
        e.async_write_ha_state = MagicMock()
        return e

    def test_options(self, entity):
        assert entity.options == ["Ambient Light w/ Fan"]

    def test_current_option_none_until_selected(self, entity):
        assert entity.current_option is None

    @pytest.mark.asyncio
    async def test_select_sends_snapshot_command_and_tracks(self, entity):
        await entity.async_select_option("Ambient Light w/ Fan")
        cmd = entity.coordinator.async_control_device.call_args[0][1]
        assert isinstance(cmd, SnapshotCommand)
        assert cmd.snapshot_value == 3862070
        # Optimistically tracked.
        assert entity.current_option == "Ambient Light w/ Fan"


class TestSnapshotPlatformWiring:
    async def _setup_select(self, device, enable_scenes=True):
        from custom_components.govee import select as select_mod

        coordinator = MagicMock()
        coordinator.devices = {device.device_id: device}
        coordinator.async_get_scenes = AsyncMock(return_value=[])
        coordinator.async_get_diy_scenes = AsyncMock(return_value=[])
        entry = MagicMock()
        entry.runtime_data = coordinator
        entry.options = {"enable_scenes": enable_scenes, "enable_diy_scenes": False}
        added: list = []
        await select_mod.async_setup_entry(
            MagicMock(), entry, lambda ents: added.extend(ents)
        )
        return [type(e).__name__ for e in added]

    async def test_h1310_gets_snapshot_select(self):
        names = await self._setup_select(_h1310())
        assert "GoveeSnapshotSelectEntity" in names

    async def test_snapshot_select_gated_on_enable_scenes(self):
        names = await self._setup_select(_h1310(), enable_scenes=False)
        assert "GoveeSnapshotSelectEntity" not in names
