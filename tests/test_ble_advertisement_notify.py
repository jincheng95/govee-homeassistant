"""The BLE advertisement handler must not wake entities on every advert.

Advertisements arrive unthrottled, often every second per device. Calling
``async_set_updated_data`` for each one re-armed the coordinator's poll timer
indefinitely and made every entity of every device write state per frame.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import custom_components.govee.ble_advertisement as ble_mod
from custom_components.govee.ble_advertisement import BleAdvertisementHandler
from custom_components.govee.coordinator import GoveeCoordinator
from custom_components.govee.models import GoveeDeviceState
from custom_components.govee.transport_health import TransportHealthTracker

DEVICE_ID = "11:66:AA:BB:CC:DD:EE:FF"


@pytest.fixture
def coordinator(monkeypatch, mock_light_device):
    """A coordinator shell with one BLE-capable H6072 and mocked notifiers."""
    device = mock_light_device
    object.__setattr__(device, "device_id", DEVICE_ID)
    # These module globals only exist when Home Assistant's Bluetooth stack
    # imported cleanly, so set them regardless of the environment.
    monkeypatch.setattr(ble_mod, "GoveeBLEDevice", MagicMock(), raising=False)
    monkeypatch.setattr(ble_mod, "SEGMENTED_MODELS", frozenset(), raising=False)
    monkeypatch.setattr(ble_mod, "BLE_COMMAND_SUPPORTED_MODELS", frozenset({device.sku}), raising=False)
    bt = MagicMock()
    bt.async_scanner_count = MagicMock(return_value=1)
    monkeypatch.setattr(ble_mod, "bt_component", bt, raising=False)

    coord = object.__new__(GoveeCoordinator)
    coord.hass = MagicMock()
    coord._devices = {DEVICE_ID: device}
    coord._ble_devices = {}
    coord._transport = TransportHealthTracker()
    coord._transport.ensure(DEVICE_ID)
    coord._states = {DEVICE_ID: GoveeDeviceState.create_empty(DEVICE_ID)}
    coord._states[DEVICE_ID].online = True
    coord.data = coord._states
    coord._ble_ignored_skus_logged = set()
    coord._ble_handler = BleAdvertisementHandler(coord)
    coord.async_update_listeners = MagicMock()
    coord.async_set_updated_data = MagicMock()
    return coord


def _advert(sku: str = "H6072") -> MagicMock:
    info = MagicMock()
    info.name = f"Govee_{sku}_754B"
    info.address = "AA:BB:CC:DD:EE:FF"
    info.device = MagicMock()
    info.advertisement = MagicMock()
    return info


def test_first_enrolment_notifies_once_without_rescheduling_the_poll(coordinator, mock_light_device):
    coordinator._ble_handler.handle_advertisement(_advert(mock_light_device.sku))

    assert DEVICE_ID in coordinator._ble_devices
    coordinator.async_update_listeners.assert_called_once()
    coordinator.async_set_updated_data.assert_not_called()


def test_repeated_advertisement_is_silent(coordinator, mock_light_device):
    advert = _advert(mock_light_device.sku)
    coordinator._ble_handler.handle_advertisement(advert)
    coordinator.async_update_listeners.reset_mock()

    for _ in range(5):
        coordinator._ble_handler.handle_advertisement(advert)

    coordinator.async_update_listeners.assert_not_called()
    coordinator.async_set_updated_data.assert_not_called()
    # The transport stamp still advances silently.
    assert coordinator._transport.get(DEVICE_ID, "ble").last_success_ts is not None


def test_online_flip_notifies(coordinator, mock_light_device):
    advert = _advert(mock_light_device.sku)
    coordinator._ble_handler.handle_advertisement(advert)
    coordinator.async_update_listeners.reset_mock()

    coordinator._states[DEVICE_ID].online = False
    coordinator._ble_handler.handle_advertisement(advert)

    assert coordinator._states[DEVICE_ID].online is True
    coordinator.async_update_listeners.assert_called_once()
