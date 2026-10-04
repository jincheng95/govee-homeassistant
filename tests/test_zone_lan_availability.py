"""LAN-written fork entities stay available through a cloud outage.

Covers ``lan_udp_health.lan_writable`` and the ``available`` overrides on the
zone, DIY and segment entities. Device ids and addresses are synthetic.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from custom_components.govee import lan_udp_health
from custom_components.govee.api import raw_router
from custom_components.govee.api.lan_client import LanDeviceInfo
from custom_components.govee.const import CONF_ENABLE_LAN_RAW_WRITE, CONF_ENABLE_ZONE_LIGHTS
from custom_components.govee.models import GoveeCapability, GoveeDevice, GoveeDeviceState
from custom_components.govee.models.device import (
    CAPABILITY_COLOR_SETTING,
    CAPABILITY_ON_OFF,
    CAPABILITY_TOGGLE,
    DEVICE_TYPE_LIGHT,
    INSTANCE_COLOR_RGB,
    INSTANCE_POWER,
)
from custom_components.govee.models.transport import TransportHealth
from custom_components.govee.platforms.diy_effect import (
    async_diy_button_entities,
    async_diy_number_entities,
    async_diy_preview_entities,
    async_diy_select_entities,
    async_diy_text_entities,
)
from custom_components.govee.platforms.grouped_segment import GoveeGroupedSegmentEntity
from custom_components.govee.platforms.segment import GoveeSegmentEntity
from custom_components.govee.platforms.segment_group import GoveeSegmentGroupEntity
from custom_components.govee.platforms.zone_light import async_zone_light_entities, async_zone_number_entities

DEVICE_ID = "AA:BB:CC:DD:EE:FF:60:B0"
IP = "10.20.0.51"

SEGMENTS = {"H60B0": 8, "H6076": 7, "H6046": 10}


def _device(sku: str = "H60B0", *, is_group: bool = False) -> GoveeDevice:
    caps = (
        GoveeCapability(type=CAPABILITY_ON_OFF, instance=INSTANCE_POWER, parameters={}),
        GoveeCapability(type=CAPABILITY_COLOR_SETTING, instance=INSTANCE_COLOR_RGB, parameters={}),
        GoveeCapability(type=CAPABILITY_TOGGLE, instance="rippleLightToggle", parameters={}),
        GoveeCapability(type=CAPABILITY_TOGGLE, instance="sideLightToggle", parameters={}),
        GoveeCapability(type=CAPABILITY_TOGGLE, instance="bottomLightToggle", parameters={}),
    )
    return GoveeDevice(
        device_id=DEVICE_ID,
        sku=sku,
        name="Test Lamp",
        device_type=DEVICE_TYPE_LIGHT,
        capabilities=caps,
        is_group=is_group,
    )


def _coordinator(
    device: GoveeDevice,
    *,
    lan_udp: bool | None = True,
    lan: bool | None = False,
    zone_lights: bool = True,
    lan_raw_write: bool = True,
    has_state: bool = True,
) -> Any:
    """A coordinator in a total cloud outage, with the given LAN health."""
    coordinator = MagicMock()
    coordinator._govee_zone_state_registry = None
    coordinator._govee_diy_state_store = None
    coordinator.devices = {device.device_id: device}
    coordinator.last_update_success = False
    coordinator.config_entry.options = {
        CONF_ENABLE_ZONE_LIGHTS: zone_lights,
        CONF_ENABLE_LAN_RAW_WRITE: lan_raw_write,
    }
    state = GoveeDeviceState.create_empty(device.device_id)
    state.online = True
    coordinator.get_state = MagicMock(return_value=state if has_state else None)
    health = {
        kind: TransportHealth(transport=kind, is_available=ok)
        for kind, ok in (("lan_udp", lan_udp), ("lan", lan))
        if ok is not None
    }
    coordinator.get_transport_health = MagicMock(side_effect=lambda _id, kind: health.get(kind))
    coordinator._lan_devices = {
        device.device_id: LanDeviceInfo(
            device_id=device.device_id,
            ip=IP,
            mac=device.device_id,
            sku=device.sku,
            firmware="1.0.0",
            last_correlated_ts=0.0,
        )
    }
    return coordinator


def _zone_and_diy(coordinator: Any) -> list[Any]:
    entry = coordinator.config_entry
    entities = [
        *async_zone_light_entities(coordinator, entry),
        *async_zone_number_entities(coordinator, entry),
        *async_diy_select_entities(coordinator, entry),
        *async_diy_number_entities(coordinator, entry),
        *async_diy_text_entities(coordinator, entry),
        *async_diy_button_entities(coordinator, entry),
        *async_diy_preview_entities(coordinator, entry),
    ]
    assert entities
    return entities


def _segment_entities(coordinator: Any, device: GoveeDevice) -> list[Any]:
    return [
        GoveeSegmentEntity(coordinator, device, 0),
        GoveeGroupedSegmentEntity(coordinator, device),
        GoveeSegmentGroupEntity(coordinator, device, "Left", (0, 1)),
    ]


@pytest.fixture
def segment_count():
    """The device's segment count, matched to the profile mask unless overridden."""
    with patch.object(raw_router, "_segment_count", side_effect=lambda entity: SEGMENTS[entity._device.sku]) as mock:
        yield mock


# Predicate


class TestPredicate:
    @pytest.mark.parametrize(
        "lan_udp,lan,expected",
        [
            (True, False, True),
            (False, True, True),
            (True, None, True),
            (None, True, True),
            (False, False, False),
            (None, None, False),
        ],
    )
    def test_either_lan_health_counts(self, lan_udp, lan, expected):
        device = _device()
        coordinator = _coordinator(device, lan_udp=lan_udp, lan=lan)
        assert lan_udp_health.lan_writable(coordinator, device, option_on=True) is expected

    def test_consults_lan_udp_for_a_lan_raw_profile(self):
        device = _device()
        coordinator = _coordinator(device, lan_udp=True, lan=False)
        assert lan_udp_health.lan_writable(coordinator, device, option_on=True) is True
        coordinator.get_transport_health.assert_any_call(DEVICE_ID, "lan_udp")

    def test_option_off(self):
        device = _device()
        assert lan_udp_health.lan_writable(_coordinator(device), device, option_on=False) is False

    def test_no_state(self):
        device = _device()
        assert lan_udp_health.lan_writable(_coordinator(device, has_state=False), device, option_on=True) is False

    def test_group_device(self):
        device = _device(is_group=True)
        assert lan_udp_health.lan_writable(_coordinator(device), device, option_on=True) is False

    def test_profile_without_lan_raw(self):
        device = _device("H6046")
        assert lan_udp_health.lan_writable(_coordinator(device, lan=True), device, option_on=True) is False

    def test_unprofiled_sku(self):
        device = _device("H6199")
        assert lan_udp_health.lan_writable(_coordinator(device, lan=True), device, option_on=True) is False

    def test_mock_health_is_not_trusted(self):
        device = _device()
        coordinator = _coordinator(device)
        coordinator.get_transport_health = MagicMock()
        assert lan_udp_health.lan_writable(coordinator, device, option_on=True) is False

    def test_zone_helper_without_config_entry(self):
        device = _device()
        coordinator = _coordinator(device)
        coordinator.config_entry = None
        entity = MagicMock(coordinator=coordinator, _device=device)
        assert lan_udp_health.zone_lan_writable(entity) is False


# Zone and DIY entities


class TestZoneAndDiy:
    def test_outage_lan_healthy_available(self):
        for entity in _zone_and_diy(_coordinator(_device())):
            assert entity.available is True, type(entity).__name__

    def test_outage_lan_unhealthy_unavailable(self):
        for entity in _zone_and_diy(_coordinator(_device(), lan_udp=False, lan=False)):
            assert entity.available is False, type(entity).__name__

    def test_option_off_unavailable(self):
        device = _device()
        coordinator = _coordinator(device)
        entities = _zone_and_diy(coordinator)
        coordinator.config_entry.options = {CONF_ENABLE_ZONE_LIGHTS: False}
        for entity in entities:
            assert entity.available is False, type(entity).__name__

    def test_cloud_up_keeps_coordinator_rule(self):
        coordinator = _coordinator(_device(), lan_udp=False, lan=False)
        coordinator.last_update_success = True
        for entity in _zone_and_diy(coordinator):
            assert entity.available is True, type(entity).__name__

    def test_lan_only_controls_still_need_a_target(self):
        coordinator = _coordinator(_device())
        entities = _zone_and_diy(coordinator)
        coordinator._lan_devices = {}
        needing_target = [e for e in entities if type(e).__name__ in ("GoveeZoneFlowRateNumber", "GoveeDiyApplyButton")]
        assert needing_target
        for entity in needing_target:
            assert entity.available is False, type(entity).__name__


# Segment entities


class TestSegments:
    @pytest.mark.parametrize("sku", ["H60B0", "H6076"])
    def test_outage_lan_healthy_available(self, segment_count, sku):
        device = _device(sku)
        for entity in _segment_entities(_coordinator(device), device):
            assert entity.available is True, type(entity).__name__

    def test_outage_lan_unhealthy_unavailable(self, segment_count):
        device = _device()
        for entity in _segment_entities(_coordinator(device, lan_udp=False, lan=False), device):
            assert entity.available is False, type(entity).__name__

    def test_lan_raw_write_off_unavailable(self, segment_count):
        device = _device()
        for entity in _segment_entities(_coordinator(device, lan_raw_write=False), device):
            assert entity.available is False, type(entity).__name__

    def test_no_lan_raw_transport_keeps_coordinator_rule(self, segment_count):
        device = _device("H6046")
        for entity in _segment_entities(_coordinator(device, lan=True), device):
            assert entity.available is False, type(entity).__name__

    def test_segment_count_mismatch_unavailable(self, segment_count):
        segment_count.side_effect = lambda entity: 15
        device = _device()
        for entity in _segment_entities(_coordinator(device), device):
            assert entity.available is False, type(entity).__name__

    def test_profile_without_the_segment_form_unavailable(self, segment_count):
        device = _device()
        with patch.object(raw_router.DeviceProfile, "supports", return_value=False):
            for entity in _segment_entities(_coordinator(device), device):
                assert entity.available is False, type(entity).__name__

    def test_unknown_segment_zone_unavailable(self, segment_count):
        device = _device()
        error = raw_router.GoveeProtocolError("no zone")
        with patch.object(raw_router.DeviceProfile, "supports", side_effect=error):
            assert raw_router.segments_lan_writable(GoveeSegmentEntity(_coordinator(device), device, 0)) is False

    def test_group_device_follows_coordinator(self, segment_count):
        device = _device(is_group=True)
        coordinator = _coordinator(device)
        for entity in _segment_entities(coordinator, device):
            assert entity.available is False, type(entity).__name__
        coordinator.last_update_success = True
        for entity in _segment_entities(coordinator, device):
            assert entity.available is True, type(entity).__name__
