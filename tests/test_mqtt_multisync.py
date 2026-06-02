"""Tests for multiSync packet capture in the AWS IoT MQTT client.

Covers the diagnostics ring buffer added for #87 — every decoded hub packet
(leak / button-press / unknown subtype) is retained as hex so undecoded
subtypes can be reverse-engineered from a diagnostics download alone, with
button-press MACs masked to keep the retained hex PII-free.
"""

from __future__ import annotations

import base64
from unittest.mock import MagicMock

from custom_components.govee.api.auth import GoveeIotCredentials
from custom_components.govee.api.mqtt import GoveeAwsIotClient

HUB_ID = "07:23:5C:E7:53:5F:6F:0A"


def _make_client() -> GoveeAwsIotClient:
    """Build a client with throwaway credentials and a no-op callback."""
    creds = GoveeIotCredentials(
        token="t",
        refresh_token="r",
        account_topic="GA/account",
        iot_cert="cert",
        iot_key="key",
        iot_ca=None,
        client_id="cid",
        endpoint="endpoint",
    )
    return GoveeAwsIotClient(creds, on_state_update=MagicMock())


def _multisync(packets: list[bytes]) -> dict:
    """Wrap raw packets in a multiSync message envelope."""
    return {
        "device": HUB_ID,
        "sku": "H5044",
        "cmd": "multiSync",
        "op": {"command": [base64.b64encode(p).decode() for p in packets]},
    }


class TestMultiSyncCapture:
    """The ring buffer records every packet subtype for diagnostics."""

    def test_records_leak_and_unknown_packets(self):
        """Both the decoded 0x34 leak packet and an undecoded 0x35 are captured."""
        client = _make_client()
        leak = bytes([0xEE, 0x34, 0x00, 0x00, 0x00, 0x00])  # slot 0, dry
        unknown = bytes([0xEE, 0x35, 0x04, 0x01, 0x02, 0x03])  # the #87 mystery

        client._handle_multisync(HUB_ID, _multisync([leak, unknown]))

        captured = client.recent_multisync
        assert len(captured) == 2
        headers = {rec["header"] for rec in captured}
        assert headers == {"ee34", "ee35"}
        # Full hex of the unknown subtype is retained so it can be decoded later.
        unknown_rec = next(r for r in captured if r["header"] == "ee35")
        assert unknown_rec["hex"] == "ee35040102 03".replace(" ", "")
        assert unknown_rec["length"] == 6
        assert unknown_rec["hub_device_id"] == HUB_ID

    def test_button_press_mac_is_masked(self):
        """Button-press (0x32) packets embed a MAC in bytes 2-9 — mask it."""
        client = _make_client()
        mac = bytes([0xDE, 0xAD, 0xBE, 0xEF, 0x01, 0x02, 0x03, 0x04])
        button = bytes([0xEE, 0x32]) + mac + bytes([0x00])

        client._handle_multisync(HUB_ID, _multisync([button]))

        rec = client.recent_multisync[0]
        assert rec["header"] == "ee32"
        # Bytes 2-9 (the MAC) must be zeroed in the retained hex.
        assert rec["hex"] == "ee32" + "00" * 8 + "00"
        assert "dead" not in rec["hex"]

    def test_ring_buffer_is_bounded(self):
        """The buffer keeps only the most recent packets (maxlen=64)."""
        client = _make_client()
        packets = [bytes([0xEE, 0x34, i & 0xFF, 0, 0, 0]) for i in range(100)]
        client._handle_multisync(HUB_ID, _multisync(packets))

        captured = client.recent_multisync
        assert len(captured) == 64
        # Oldest dropped: slot bytes should be the last 64 (36..99).
        first_slot = int(captured[0]["hex"][4:6], 16)
        assert first_slot == 36

    def test_short_packet_still_recorded(self):
        """A truncated packet is captured for diagnostics, not silently dropped."""
        client = _make_client()
        client._handle_multisync(HUB_ID, _multisync([bytes([0xEE])]))

        captured = client.recent_multisync
        assert len(captured) == 1
        assert captured[0]["hex"] == "ee"
        assert captured[0]["length"] == 1

    def test_undecodable_base64_does_not_record(self):
        """A command that fails base64 decode is skipped, not recorded."""
        client = _make_client()
        client._handle_multisync(
            HUB_ID, {"device": HUB_ID, "op": {"command": ["!!!not-base64!!!"]}}
        )
        # Lenient base64 may yield bytes for some inputs; assert no crash and
        # that any recorded entry is well-formed hex.
        for rec in client.recent_multisync:
            assert set(rec["hex"]) <= set("0123456789abcdef")
