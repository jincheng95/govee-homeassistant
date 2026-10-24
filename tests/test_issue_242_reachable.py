"""Issue #242: a cloud-only "offline" flag no longer hides a device that answers.

GoveeEntity.available = coordinator healthy and (group or device_reachable).
device_reachable: the cloud's online flag, a healthy LAN or BLE transport, or
an AWS IoT message from the device within REACHABLE_PUSH_WINDOW. A total
cloud outage still makes cloud-dependent entities unavailable (#226).
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from homeassistant.util import dt as dt_util

from custom_components.govee.const import REACHABLE_PUSH_WINDOW
from custom_components.govee.entity import GoveeEntity

from .test_cov_coordinator_control import DEV, _add, _coordinator, _device


def _offline():
    coord = _coordinator()
    state = _add(coord, _device())
    state.online = False
    return coord, state


def _mqtt_seen(coord, seconds_ago: float) -> None:
    client = MagicMock()
    client.last_message_ts_for = MagicMock(return_value=dt_util.utcnow() - timedelta(seconds=seconds_ago))
    coord._mqtt_client = client


class TestDeviceReachable:
    def test_online_flag(self):
        coord = _coordinator()
        _add(coord, _device())
        assert coord.device_reachable(DEV) is True

    def test_offline_with_no_other_evidence(self):
        coord, _state = _offline()
        assert coord.device_reachable(DEV) is False

    def test_no_state(self):
        assert _coordinator().device_reachable(DEV) is False

    @pytest.mark.parametrize("kind", ["lan", "ble"])
    def test_a_healthy_local_transport(self, kind):
        coord, _state = _offline()
        coord._record_transport_success(DEV, kind)
        assert coord.device_reachable(DEV) is True

    def test_a_recent_mqtt_message(self):
        coord, _state = _offline()
        _mqtt_seen(coord, REACHABLE_PUSH_WINDOW - 60)
        assert coord.device_reachable(DEV) is True

    def test_an_old_mqtt_message(self):
        coord, _state = _offline()
        _mqtt_seen(coord, REACHABLE_PUSH_WINDOW + 60)
        assert coord.device_reachable(DEV) is False


class TestEntityAvailability:
    def _entity(self, coord) -> GoveeEntity:
        return GoveeEntity(coord, coord._devices[DEV])

    def test_offline_device_with_a_recent_push_is_available(self):
        coord, _state = _offline()
        coord.last_update_success = True
        _mqtt_seen(coord, 30)
        assert self._entity(coord).available is True

    def test_offline_device_without_evidence_is_unavailable(self):
        coord, _state = _offline()
        coord.last_update_success = True
        assert self._entity(coord).available is False

    def test_a_cloud_outage_still_wins(self):
        """#226: with the coordinator failed, only light.py's LAN bypass keeps a light up."""
        coord, _state = _offline()
        coord.last_update_success = False
        coord._record_transport_success(DEV, "lan")
        assert self._entity(coord).available is False
