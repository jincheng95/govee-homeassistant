"""Remaining branch coverage for small API-layer helpers.

Targets the last uncovered guards in ``api/lan.py`` (scan-response body
shape, multicast group-join dedupe and failure), ``api/probe_thermometer.py``
(short-buffer guards and probe-number validation) and ``api/mqtt_control.py``
(wide native brightness ranges rescaled onto the 1-100 MQTT scale).
"""

from __future__ import annotations

import json
import logging
import struct
from unittest.mock import MagicMock

import pytest

from custom_components.govee.api import lan
from custom_components.govee.api.mqtt_control import command_to_mqtt, device_brightness_to_mqtt
from custom_components.govee.api.probe_thermometer import (
    REGISTER_LIMITS,
    ProbeLimits,
    _read_temperature,
    decode_limits,
    verify_checksum,
)
from custom_components.govee.models.commands import BrightnessCommand

DEVICE_ID = "AA:BB:CC:DD:EE:FF:00:11"


# ==============================================================================
# lan.py
# ==============================================================================


class TestScanProtocol:
    def test_non_object_data_body_is_ignored(self):
        proto = lan._ScanProtocol()
        proto.datagram_received(json.dumps({"msg": {"cmd": "scan", "data": [1, 2]}}).encode(), ("192.168.1.9", 4002))
        proto.datagram_received(json.dumps({"msg": {"cmd": "scan", "data": "x"}}).encode(), ("192.168.1.9", 4002))
        assert proto.responses == {}

        proto.datagram_received(
            json.dumps({"msg": {"cmd": "scan", "data": {"device": DEVICE_ID, "sku": "H6072"}}}).encode(),
            ("192.168.1.9", 4002),
        )
        assert proto.responses == {DEVICE_ID: {"device": DEVICE_ID, "sku": "H6072", "ip": "192.168.1.9"}}


class TestJoinGroup:
    @staticmethod
    def _joined_interfaces(sock) -> list[str]:
        return [
            lan.socket.inet_ntoa(value[len(lan._GROUP_BYTES) :])
            for level, name, value in (call.args for call in sock.setsockopt.call_args_list)
            if level == lan.socket.IPPROTO_IP and name == lan.socket.IP_ADD_MEMBERSHIP
        ]

    def test_duplicate_and_default_interfaces_are_joined_once(self):
        sock = MagicMock()
        joined = lan._join_group(sock, ["0.0.0.0", "10.0.0.5", "10.0.0.5"])
        assert joined == ["0.0.0.0", "10.0.0.5"]
        assert self._joined_interfaces(sock) == ["0.0.0.0", "10.0.0.5"]

    def test_failed_join_is_skipped_and_logged(self, caplog):
        bad = lan._GROUP_BYTES + lan.socket.inet_aton("10.0.0.5")

        def setsockopt(level, name, value):
            if name == lan.socket.IP_ADD_MEMBERSHIP and value == bad:
                raise OSError(19, "No such device")

        sock = MagicMock()
        sock.setsockopt.side_effect = setsockopt

        with caplog.at_level(logging.DEBUG, logger="custom_components.govee.api.lan"):
            joined = lan._join_group(sock, ["192.168.1.2", "10.0.0.5"])

        assert joined == ["192.168.1.2", "0.0.0.0"]
        assert self._joined_interfaces(sock) == ["192.168.1.2", "10.0.0.5", "0.0.0.0"]  # every join attempted
        assert any("group join on 10.0.0.5 failed" in rec.getMessage() for rec in caplog.records)

    def test_no_successful_join_returns_empty(self):
        sock = MagicMock()
        sock.setsockopt.side_effect = OSError("multicast unsupported")
        assert lan._join_group(sock, ["192.168.1.2"]) == []
        lan._drop_group(sock, [])  # nothing joined, nothing to leave
        assert all(call.args[1] != lan.socket.IP_DROP_MEMBERSHIP for call in sock.setsockopt.call_args_list)


# ==============================================================================
# probe_thermometer.py
# ==============================================================================


def _limits_frame(probe: int, *values: int) -> bytes:
    body = bytes([0xAA, REGISTER_LIMITS, probe]) + struct.pack(">hhhh", *values)
    return body + bytes(20 - len(body))


class TestProbeGuards:
    def test_read_temperature_beyond_the_buffer_is_none(self):
        assert _read_temperature(b"", 0) is None
        assert _read_temperature(b"\x0d", 0) is None
        assert _read_temperature(bytes(4), 3) is None
        assert _read_temperature(b"\x0d\x48", 0) == 34.0
        assert _read_temperature(b"\xff\xff", 0) is None  # the sentinel, checked unsigned

    def test_verify_checksum_rejects_short_frames(self):
        assert verify_checksum(bytes(19)) is False
        assert verify_checksum(bytes(20)) is True
        frame = bytearray(bytes([0xAA, 0x12, 0x01]) + bytes(17))
        frame[19] = 0xAA ^ 0x12 ^ 0x01
        assert verify_checksum(bytes(frame)) is True

    @pytest.mark.parametrize("probe", [0, 5, 0xFF])
    def test_decode_limits_rejects_unknown_probe_numbers(self, probe):
        """3 and 4 are valid probe numbers since the H5194 (issue #197)."""
        assert decode_limits(_limits_frame(probe, 8800, 3300, 7000, -1000)) is None

    def test_decode_limits_rejects_short_or_foreign_frames(self):
        assert decode_limits(bytes([0xAA, REGISTER_LIMITS, 1]) + bytes(7)) is None  # one byte short
        assert decode_limits(bytes([0xAA, 0x24, 1]) + bytes(17)) is None  # a probe-data frame

    def test_decode_limits_accepts_both_probes(self):
        assert decode_limits(_limits_frame(2, 8800, 3300, 7000, -1000)) == (
            2,
            ProbeLimits(core_max=88.0, core_min=33.0, ambient_max=70.0, ambient_min=-10.0),
        )


# ==============================================================================
# mqtt_control.py
# ==============================================================================


class TestBrightnessScale:
    def test_wide_native_range_is_rescaled(self):
        assert device_brightness_to_mqtt(254, (0, 254)) == 100
        assert device_brightness_to_mqtt(127, (0, 254)) == 50
        assert device_brightness_to_mqtt(0, (0, 254)) == 1  # floor of the MQTT scale, never 0
        assert device_brightness_to_mqtt(1, (1, 254)) == 1

    def test_hundred_scale_passes_through_and_clamps(self):
        assert device_brightness_to_mqtt(50, (0, 100)) == 50
        assert device_brightness_to_mqtt(50, (1, 100)) == 50
        assert device_brightness_to_mqtt(0, (0, 100)) == 1
        assert device_brightness_to_mqtt(150, (0, 100)) == 100

    def test_degenerate_wide_range_is_clamped_not_rescaled(self):
        assert device_brightness_to_mqtt(120, (200, 200)) == 100

    def test_command_mapping_uses_the_device_range(self):
        command = BrightnessCommand(brightness=127)
        assert command_to_mqtt(command, "H6072", (0, 254)) == ("brightness", {"val": 50}, 0)
        assert command_to_mqtt(command, "H6072") == ("brightness", {"val": 100}, 0)
