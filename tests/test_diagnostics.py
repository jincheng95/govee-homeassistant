"""Tests for diagnostics module — verifies PII redaction of device IDs."""

from __future__ import annotations

import json
import re
from unittest.mock import MagicMock

import pytest

from custom_components.govee.diagnostics import (
    TO_REDACT,
    _anonymize_device_id,
    _anonymize_device_keys,
    _looks_like_mac,
    async_get_config_entry_diagnostics,
)

# Govee device-id MAC pattern: 6-8 colon-separated hex octets
_MAC_RE = re.compile(r"\b[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5,7}\b")


class TestMacDetection:
    def test_8_octet_mac_matches(self) -> None:
        assert _looks_like_mac("03:9C:DC:06:75:4B:10:7C")

    def test_6_octet_mac_matches(self) -> None:
        assert _looks_like_mac("AA:BB:CC:DD:EE:FF")

    def test_lowercase_mac_matches(self) -> None:
        assert _looks_like_mac("aa:bb:cc:dd:ee:ff")

    def test_numeric_group_id_does_not_match(self) -> None:
        assert not _looks_like_mac("11825917")

    def test_random_string_does_not_match(self) -> None:
        assert not _looks_like_mac("device_001")

    def test_too_few_octets_does_not_match(self) -> None:
        assert not _looks_like_mac("AA:BB:CC")

    def test_too_many_octets_does_not_match(self) -> None:
        assert not _looks_like_mac("00:11:22:33:44:55:66:77:88")


class TestAnonymizeDeviceId:
    def test_returns_stable_short_hash(self) -> None:
        a = _anonymize_device_id("03:9C:DC:06:75:4B:10:7C")
        b = _anonymize_device_id("03:9C:DC:06:75:4B:10:7C")
        assert a == b
        assert a.startswith("device_")
        assert len(a) == len("device_") + 8

    def test_different_macs_yield_different_hashes(self) -> None:
        a = _anonymize_device_id("03:9C:DC:06:75:4B:10:7C")
        b = _anonymize_device_id("03:9C:DC:06:75:4B:10:7D")
        assert a != b


class TestAnonymizeDeviceKeys:
    def test_replaces_mac_keys(self) -> None:
        out = _anonymize_device_keys({"03:9C:DC:06:75:4B:10:7C": {"sku": "H6601"}})
        keys = list(out.keys())
        assert len(keys) == 1
        assert keys[0].startswith("device_")
        assert "03:9C:DC" not in keys[0]

    def test_preserves_non_mac_keys(self) -> None:
        out = _anonymize_device_keys({"11825917": {"sku": "H6004"}, "summary": "x"})
        assert "11825917" in out
        assert "summary" in out

    def test_preserves_values_verbatim(self) -> None:
        payload = {"sku": "H6601", "name": "Living Room"}
        out = _anonymize_device_keys({"03:9C:DC:06:75:4B:10:7C": payload})
        assert next(iter(out.values())) == payload


class TestRedactionSet:
    def test_includes_device_id(self) -> None:
        assert "device_id" in TO_REDACT

    def test_includes_mac(self) -> None:
        assert "mac" in TO_REDACT


class TestDiagnosticsOutput:
    """Regression test for H4: MAC-format device IDs must not leak."""

    @pytest.mark.asyncio
    async def test_no_mac_in_diagnostics_output(self) -> None:
        """A full diagnostics payload must contain no MAC-format substrings.

        Govee uses MAC-derived device IDs as dict keys. Without anonymization,
        every diagnostics dump leaks user device hardware addresses. This test
        renders a representative diagnostics payload and asserts it is clean.
        """
        # Build a coordinator stub with one MAC-keyed device + one group device
        mac_id = "03:9C:DC:06:75:4B:10:7C"
        group_id = "11825917"

        device_mac = MagicMock()
        device_mac.sku = "H6601"
        device_mac.name = "Living Room Lamp"
        device_mac.device_type = "devices.types.light"
        device_mac.is_group = False
        device_mac.capabilities = []

        device_group = MagicMock()
        device_group.sku = "H6004"
        device_group.name = "Bedroom Group"
        device_group.device_type = "devices.types.light"
        device_group.is_group = True
        device_group.capabilities = []

        state_mock = MagicMock()
        state_mock.online = True
        state_mock.power_state = True
        state_mock.brightness = 80
        state_mock.color = None
        state_mock.color_temp_kelvin = 4000
        state_mock.source = "cloud_api"

        coordinator = MagicMock()
        coordinator.devices = {mac_id: device_mac, group_id: device_group}
        coordinator.get_state = lambda _did: state_mock
        coordinator.mqtt_connected = True
        coordinator.is_ble_available = lambda _did: False
        coordinator.mqtt_client = None
        coordinator.api_rate_limit_remaining = 100
        coordinator.api_rate_limit_total = 100
        coordinator.api_rate_limit_reset = 0
        coordinator.scene_cache_count = 0

        entry = MagicMock()
        entry.entry_id = "test_entry"
        entry.version = 1
        entry.data = {"api_key": "secret", "email": "user@example.com"}
        entry.options = {}
        entry.runtime_data = coordinator

        hass = MagicMock()

        out = await async_get_config_entry_diagnostics(hass, entry)
        rendered = json.dumps(out, default=str)

        # The MAC must not appear anywhere — keys, values, or nested strings.
        assert (
            mac_id not in rendered
        ), f"MAC-format device id leaked into diagnostics: {mac_id} found in {rendered}"
        # No 6-or-more-octet MAC pattern anywhere.
        match = _MAC_RE.search(rendered)
        assert match is None, f"MAC-format substring leaked: {match.group(0)!r}"

        # Numeric group IDs are not PII; preserve them.
        assert group_id in rendered

        # API key and email must also be redacted.
        assert "secret" not in rendered
        assert "user@example.com" not in rendered
