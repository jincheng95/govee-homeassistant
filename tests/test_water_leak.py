"""Tests for standalone water-leak detector support (H5054, issue #62).

The H5054 surfaces in the developer device list with a single
``bodyAppearedEvent`` event capability — distinct from the H5058 leak sensor
(hub/BFF path). Detection is capability-based; the trip normally lands via
MQTT push since the device-state poll only returns ``online``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.govee.binary_sensor import (
    GoveeLeakBinarySensor,
    GoveeWaterLeakBinarySensor,
    async_setup_entry,
)
from custom_components.govee.coordinator import GoveeCoordinator
from custom_components.govee.models import (
    GoveeCapability,
    GoveeDevice,
    GoveeDeviceState,
)
from custom_components.govee.models.device import (
    CAPABILITY_EVENT,
    INSTANCE_BODY_APPEARED_EVENT,
    GoveeLeakSensor,
)

# --------------------------------------------------------------------------- #
# Fixtures — H5054 shape from issue #62 diagnostics
# --------------------------------------------------------------------------- #


@pytest.fixture
def h5054_device() -> GoveeDevice:
    return GoveeDevice(
        device_id="DABFC0D6A5FE0008E8",
        sku="H5054",
        name="Washing Machine",
        device_type="devices.types.sensor",
        capabilities=(
            GoveeCapability(
                type=CAPABILITY_EVENT,
                instance=INSTANCE_BODY_APPEARED_EVENT,
                parameters={},
            ),
        ),
        is_group=False,
    )


# --------------------------------------------------------------------------- #
# Device-model detection
# --------------------------------------------------------------------------- #


class TestDeviceModel:
    def test_water_leak_event_detected(self, h5054_device):
        assert h5054_device.supports_water_leak_event is True

    def test_non_leak_device_not_detected(self):
        device = GoveeDevice(
            device_id="x",
            sku="H6159",
            name="Strip",
            device_type="devices.types.light",
            capabilities=(),
            is_group=False,
        )
        assert device.supports_water_leak_event is False


# --------------------------------------------------------------------------- #
# State parsing — REST device-state poll
# --------------------------------------------------------------------------- #


class TestStateParsing:
    def test_water_leak_from_api_scalar(self):
        state = GoveeDeviceState(device_id="x")
        state.update_from_api(
            {
                "capabilities": [
                    {
                        "type": "devices.capabilities.event",
                        "instance": "bodyAppearedEvent",
                        "state": {"value": 1},
                    }
                ]
            }
        )
        assert state.water_leak is True

    def test_water_leak_from_api_struct(self):
        state = GoveeDeviceState(device_id="x")
        state.update_from_api(
            {
                "capabilities": [
                    {
                        "type": "devices.capabilities.event",
                        "instance": "bodyAppearedEvent",
                        "state": {"value": {"state": True}},
                    }
                ]
            }
        )
        assert state.water_leak is True

    def test_water_leak_defaults_none(self):
        state = GoveeDeviceState(device_id="x")
        assert state.water_leak is None

    def test_water_full_not_set_by_leak_event(self):
        """bodyAppearedEvent must not bleed into the dehumidifier water_full flag."""
        state = GoveeDeviceState(device_id="x")
        state.update_from_api(
            {
                "capabilities": [
                    {
                        "type": "devices.capabilities.event",
                        "instance": "bodyAppearedEvent",
                        "state": {"value": 1},
                    }
                ]
            }
        )
        assert state.water_full is None


# --------------------------------------------------------------------------- #
# State parsing — MQTT push: H5054 has no MQTT topic (issue #62), so an
# unrelated push must never touch the leak flag.
# --------------------------------------------------------------------------- #


class TestMqttParsing:
    def test_unrelated_push_leaves_leak_untouched(self):
        state = GoveeDeviceState(device_id="x")
        state.update_from_mqtt({"onOff": 1, "brightness": 50})
        assert state.water_leak is None


# --------------------------------------------------------------------------- #
# Binary sensor entity
# --------------------------------------------------------------------------- #


class TestBinarySensor:
    def _entity(self, h5054_device, leak_value, last_update_success=True):
        state = GoveeDeviceState(device_id=h5054_device.device_id, online=False)
        state.water_leak = leak_value
        coordinator = MagicMock()
        coordinator.devices = {h5054_device.device_id: h5054_device}
        coordinator.get_state = MagicMock(return_value=state)
        coordinator.last_update_success = last_update_success
        entity = GoveeWaterLeakBinarySensor(coordinator, h5054_device)
        return entity

    def test_unique_id(self, h5054_device):
        entity = self._entity(h5054_device, None)
        assert entity.unique_id == "DABFC0D6A5FE0008E8_water_leak"

    def test_is_on_wet(self, h5054_device):
        assert self._entity(h5054_device, True).is_on is True

    def test_is_on_dry(self, h5054_device):
        assert self._entity(h5054_device, False).is_on is False

    def test_is_on_defaults_dry_before_first_poll(self, h5054_device):
        """water_leak=None (pre-poll) reads as dry, not Unknown (issue #145)."""
        assert self._entity(h5054_device, None).is_on is False

    def test_available_despite_offline_device(self, h5054_device):
        """Entity stays available even though the detector reports online=False."""
        assert self._entity(h5054_device, False).available is True

    def test_unavailable_when_coordinator_failed(self, h5054_device):
        entity = self._entity(h5054_device, None, last_update_success=False)
        assert entity.available is False


# --------------------------------------------------------------------------- #
# Hub-attached sensors must not get a second moisture entity
# --------------------------------------------------------------------------- #


@pytest.fixture
def h5058_device() -> GoveeDevice:
    """H5058 as the developer API returns it — same capability as the H5054."""
    return GoveeDevice(
        device_id="01:32:7A:C4:06:02:1C:42",
        sku="H5058",
        name="Master sink",
        device_type="devices.types.sensor",
        capabilities=(
            GoveeCapability(
                type=CAPABILITY_EVENT,
                instance=INSTANCE_BODY_APPEARED_EVENT,
                parameters={},
            ),
        ),
        is_group=False,
    )


class TestHubAttachedSensorNotDuplicated:
    """H5058 matches the H5054 capability rule but is owned by the BFF path.

    Both SKUs expose ``bodyAppearedEvent``, so a purely capability-based check
    gave hub-attached sensors two moisture entities: this one plus the
    BFF/multiSync ``GoveeLeakBinarySensor``. The BFF entity wins (it is the
    faster of the two pushes), so this platform skips devices already
    discovered as leak sensors.
    """

    @staticmethod
    def _entry(device: GoveeDevice, *, discovered_via_bff: bool) -> MagicMock:
        leak_sensors = {}
        if discovered_via_bff:
            leak_sensors[device.device_id] = GoveeLeakSensor(
                device_id=device.device_id,
                name=device.name,
                sku=device.sku,
                hub_device_id="09:C2:60:74:F4:64:AB:FA",
                sno=14,
            )
        coordinator = MagicMock()
        coordinator.devices = {device.device_id: device}
        coordinator.leak_sensors = leak_sensors
        coordinator._leak_sensors = leak_sensors
        coordinator._config_entry.data = {}
        coordinator.register_leak_hubs = MagicMock()
        coordinator.is_bff_leak_sensor = (
            lambda did: GoveeCoordinator.is_bff_leak_sensor(coordinator, did)
        )
        entry = MagicMock()
        entry.runtime_data = coordinator
        entry.options = {}
        return entry

    @staticmethod
    async def _water_leak_entities(entry: MagicMock) -> list:
        added: list = []
        await async_setup_entry(MagicMock(), entry, lambda e: added.extend(e))
        return [e for e in added if isinstance(e, GoveeWaterLeakBinarySensor)]

    @pytest.mark.asyncio
    async def test_skipped_when_already_discovered_via_bff(self, h5058_device):
        entry = self._entry(h5058_device, discovered_via_bff=True)
        assert await self._water_leak_entities(entry) == []

    @pytest.mark.asyncio
    async def test_still_created_for_standalone_detector(self, h5054_device):
        """H5054 has no BFF/hub entity, so it must keep this one."""
        entry = self._entry(h5054_device, discovered_via_bff=False)
        assert len(await self._water_leak_entities(entry)) == 1

    @pytest.mark.asyncio
    async def test_created_when_bff_discovery_unavailable(self, h5058_device):
        """API-key-only setups have no BFF discovery — don't leave them blind."""
        entry = self._entry(h5058_device, discovered_via_bff=False)
        assert len(await self._water_leak_entities(entry)) == 1

    @pytest.mark.asyncio
    async def test_bff_entity_survives_the_suppression(self, h5058_device):
        """The kept entity must be the BFF one — not zero moisture entities."""
        entry = self._entry(h5058_device, discovered_via_bff=True)
        added: list = []
        await async_setup_entry(MagicMock(), entry, lambda e: added.extend(e))

        moisture = [
            e
            for e in added
            if isinstance(e, (GoveeLeakBinarySensor, GoveeWaterLeakBinarySensor))
        ]
        assert len(moisture) == 1
        assert isinstance(moisture[0], GoveeLeakBinarySensor)

    @pytest.mark.asyncio
    async def test_outage_leaves_a_working_moisture_entity(self, h5058_device):
        """A BFF outage must never leave the device with nothing.

        The standalone entity still receives OpenAPI leak events, so keeping it
        is a working fallback; suppressing it here would mean zero moisture
        entities for a leak sensor.
        """
        entry = self._entry(h5058_device, discovered_via_bff=False)
        assert entry.runtime_data.leak_sensors == {}  # discovery came back empty

        assert len(await self._water_leak_entities(entry)) == 1

    @pytest.mark.asyncio
    async def test_both_discovery_outcomes_add_a_moisture_entity(self, h5058_device):
        """Setup adds a moisture entity whether or not BFF discovery found it."""
        for discovered in (True, False):
            entry = self._entry(h5058_device, discovered_via_bff=discovered)
            added: list = []
            await async_setup_entry(MagicMock(), entry, lambda e: added.extend(e))
            moisture = [
                e
                for e in added
                if isinstance(e, (GoveeLeakBinarySensor, GoveeWaterLeakBinarySensor))
            ]
            assert len(moisture) >= 1, f"no moisture entity when {discovered=}"

    @pytest.mark.asyncio
    async def test_dedupe_survives_id_format_mismatch(self, h5058_device):
        """Govee is inconsistent about colons between its APIs; match anyway."""
        entry = self._entry(h5058_device, discovered_via_bff=True)
        coordinator = entry.runtime_data
        colonless = {
            device_id.replace(":", ""): sensor
            for device_id, sensor in coordinator.leak_sensors.items()
        }
        coordinator.leak_sensors = colonless
        coordinator._leak_sensors = colonless

        assert await self._water_leak_entities(entry) == []


class TestLeakBatteryUniqueIdCollision:
    """A leak sensor must get exactly one battery entity (#145).

    An H5058 that also appears in the Developer API used to get both
    GoveeThermoBatterySensor and GoveeLeakBatterySensor under the identical
    `<device_id>_battery` unique_id; HA drops one, and the dropped one's
    registry entry lingers as an Unavailable row.
    """

    @pytest.mark.asyncio
    async def test_leak_sku_gets_no_thermo_battery_entity(self, monkeypatch):
        from unittest.mock import MagicMock

        from custom_components.govee import sensor as sensor_module

        device = GoveeDevice(
            device_id="01:32:7A:C4:06:02:1C:42",
            sku="H5058",
            name="Master sink",
            device_type="devices.types.sensor",
            capabilities=(
                GoveeCapability(
                    type=CAPABILITY_EVENT,
                    instance=INSTANCE_BODY_APPEARED_EVENT,
                    parameters={},
                ),
            ),
            is_group=False,
        )
        state = GoveeDeviceState.create_empty(device.device_id)
        state.battery = 88

        coordinator = MagicMock()
        coordinator.devices = {device.device_id: device}
        coordinator.get_state = MagicMock(return_value=state)
        coordinator.leak_sensors = {}
        coordinator.mqtt_client = None

        added: list = []
        entry = MagicMock()
        entry.runtime_data = coordinator
        await sensor_module.async_setup_entry(
            MagicMock(), entry, lambda entities: added.extend(entities)
        )

        assert not [
            e for e in added if isinstance(e, sensor_module.GoveeThermoBatterySensor)
        ]

    @pytest.mark.asyncio
    async def test_non_leak_thermometer_still_gets_its_battery_entity(self):
        from unittest.mock import MagicMock

        from custom_components.govee import sensor as sensor_module
        from custom_components.govee.models.device import (
            CAPABILITY_PROPERTY,
            INSTANCE_SENSOR_TEMPERATURE,
        )

        device = GoveeDevice(
            device_id="11:22:33:44:55:66:77:88",
            sku="H5109",
            name="Garage",
            device_type="devices.types.thermometer",
            capabilities=(
                GoveeCapability(
                    type=CAPABILITY_PROPERTY,
                    instance=INSTANCE_SENSOR_TEMPERATURE,
                    parameters={},
                ),
            ),
            is_group=False,
        )
        state = GoveeDeviceState.create_empty(device.device_id)
        state.battery = 70

        coordinator = MagicMock()
        coordinator.devices = {device.device_id: device}
        coordinator.get_state = MagicMock(return_value=state)
        coordinator.leak_sensors = {}
        coordinator.mqtt_client = None

        added: list = []
        entry = MagicMock()
        entry.runtime_data = coordinator
        await sensor_module.async_setup_entry(
            MagicMock(), entry, lambda entities: added.extend(entities)
        )

        assert [
            e for e in added if isinstance(e, sensor_module.GoveeThermoBatterySensor)
        ]
