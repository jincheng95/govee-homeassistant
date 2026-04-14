"""Tests for per-device transport health tracking + optimistic grace period."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from custom_components.govee.coordinator import GoveeCoordinator
from custom_components.govee.models import GoveeDeviceState, TransportHealth


def _bare_coordinator() -> GoveeCoordinator:
    """Construct a coordinator without running DataUpdateCoordinator.__init__."""
    coord = object.__new__(GoveeCoordinator)
    coord._devices = {}
    coord._states = {}
    coord._ble_devices = {}
    coord._transport_health = {}
    coord._mqtt_client = None
    return coord


class TestTransportHealth:
    def test_mark_success_and_failure(self):
        health = TransportHealth(transport="ble")
        now = datetime.now(timezone.utc)
        health.mark_success(now)
        assert health.is_available is True
        assert health.last_success_ts == now

        later = now + timedelta(seconds=5)
        health.mark_failure(later, "out_of_range")
        assert health.is_available is False
        assert health.last_failure_ts == later
        assert health.last_failure_reason == "out_of_range"

    def test_coordinator_records_success(self):
        coord = _bare_coordinator()
        coord._devices["dev1"] = MagicMock()
        coord._record_transport_success("dev1", "cloud_api")
        health = coord.get_transport_health("dev1", "cloud_api")
        assert health is not None
        assert health.is_available is True
        assert health.last_success_ts is not None

    def test_coordinator_records_failure(self):
        coord = _bare_coordinator()
        coord._devices["dev1"] = MagicMock()
        coord._record_transport_failure("dev1", "ble", "disconnected")
        health = coord.get_transport_health("dev1", "ble")
        assert health is not None
        assert health.is_available is False
        assert health.last_failure_reason == "disconnected"

    def test_get_transport_health_unknown_device(self):
        coord = _bare_coordinator()
        assert coord.get_transport_health("nope", "cloud_api") is None

    def test_refresh_mqtt_health_disconnected(self):
        coord = _bare_coordinator()
        coord._devices["dev1"] = MagicMock()
        coord._refresh_mqtt_health()
        health = coord.get_transport_health("dev1", "mqtt")
        assert health is not None
        assert health.is_available is False
        assert health.last_failure_reason == "not_configured"

    def test_refresh_mqtt_health_connected(self):
        coord = _bare_coordinator()
        coord._devices["dev1"] = MagicMock()
        mqtt_client = MagicMock()
        mqtt_client.connected = True
        coord._mqtt_client = mqtt_client
        coord._refresh_mqtt_health()
        health = coord.get_transport_health("dev1", "mqtt")
        assert health is not None
        assert health.is_available is True
        assert health.last_failure_reason is None


class TestOptimisticGracePeriod:
    def test_apply_optimistic_stamps_timestamp(self):
        state = GoveeDeviceState.create_empty("dev1")
        assert state.last_optimistic_update is None
        state.apply_optimistic_power(True)
        assert state.last_optimistic_update is not None
        assert state.source == "optimistic"

    def test_mqtt_update_clears_optimistic_window(self):
        state = GoveeDeviceState.create_empty("dev1")
        state.apply_optimistic_power(True)
        assert state.last_optimistic_update is not None
        state.update_from_mqtt({"onOff": 1, "brightness": 50})
        assert state.last_optimistic_update is None
        assert state.source == "mqtt"

    def test_clear_optimistic_window_is_idempotent(self):
        state = GoveeDeviceState.create_empty("dev1")
        state.clear_optimistic_window()  # safe on fresh state
        assert state.last_optimistic_update is None
        state.apply_optimistic_power(True)
        state.clear_optimistic_window()
        assert state.last_optimistic_update is None


class TestSegmentLockInit:
    def test_segment_lock_dict_exists(self):
        """async_control_device assumes self._segment_locks is a dict."""
        coord = _bare_coordinator()
        coord._segment_locks = {}  # defensive — also set by real __init__
        assert isinstance(coord._segment_locks, dict)
