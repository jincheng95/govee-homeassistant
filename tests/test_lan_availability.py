"""Availability is scoped to the whole-device light's LAN controls."""

from unittest.mock import MagicMock

import pytest

from custom_components.govee.light import GoveeLightEntity, GoveeMainLightEntity, GoveeNightLightEntity
from custom_components.govee.models.transport import TransportHealth


@pytest.mark.parametrize(
    "cloud_ok,lan_ok,online,has_state,expected",
    [
        (False, True, True, True, True),
        (False, True, False, True, True),
        (False, False, True, True, False),
        (False, None, True, True, False),
        (True, False, True, True, True),
        (True, False, False, True, False),
        (False, True, True, False, False),
    ],
)
def test_light_availability(mock_light_device, mock_device_state, cloud_ok, lan_ok, online, has_state, expected):
    coord = MagicMock()
    coord.last_update_success = cloud_ok
    mock_device_state.online = online
    coord.get_state.return_value = mock_device_state if has_state else None
    coord.get_transport_health.return_value = (
        None if lan_ok is None else TransportHealth(transport="lan", is_available=lan_ok)
    )
    light = GoveeLightEntity(coord, mock_light_device)
    assert light.available is expected


@pytest.mark.parametrize("entity_class", [GoveeMainLightEntity, GoveeNightLightEntity])
def test_auxiliary_lights_keep_cloud_availability(mock_light_device, mock_device_state, entity_class):
    coord = MagicMock()
    coord.last_update_success = False
    coord.get_state.return_value = mock_device_state
    coord.get_transport_health.return_value = TransportHealth(transport="lan", is_available=True)
    light = entity_class(coord, mock_light_device)
    assert light.available is False


@pytest.mark.parametrize("cloud_ok", [True, False])
def test_group_still_follows_coordinator(mock_group_device, cloud_ok):
    coord = MagicMock()
    coord.last_update_success = cloud_ok
    coord.get_state.return_value = None
    coord.get_transport_health.return_value = TransportHealth(transport="lan", is_available=True)
    assert GoveeLightEntity(coord, mock_group_device).available is cloud_ok
