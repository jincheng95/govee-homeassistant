"""Tests for __init__.py — IoT-cred persistence + v1→v2 migration (sprint-4)."""

from __future__ import annotations

from dataclasses import asdict
from unittest.mock import MagicMock

import pytest

from custom_components.govee import (
    _creds_from_dict,
    _creds_to_dict,
    _persist_iot_credentials,
    async_migrate_entry,
)
from custom_components.govee.const import (
    DOMAIN,
    KEY_IOT_CREDENTIALS,
    KEY_IOT_LOGIN_FAILED,
)
from custom_components.govee.api import GoveeIotCredentials


def _make_creds() -> GoveeIotCredentials:
    return GoveeIotCredentials(
        token="t",
        refresh_token="r",
        account_topic="GA/x",
        iot_cert="-----BEGIN CERTIFICATE-----\ntest\n-----END CERTIFICATE-----",
        iot_key="-----BEGIN PRIVATE KEY-----\ntest\n-----END PRIVATE KEY-----",
        iot_ca=None,
        client_id="cid",
        endpoint="iot.example.com",
    )


class TestCredsRoundtrip:
    def test_to_dict_returns_all_fields(self) -> None:
        d = _creds_to_dict(_make_creds())
        for f in (
            "token",
            "refresh_token",
            "account_topic",
            "iot_cert",
            "iot_key",
            "iot_ca",
            "client_id",
            "endpoint",
        ):
            assert f in d

    def test_roundtrip_yields_equivalent_instance(self) -> None:
        original = _make_creds()
        d = _creds_to_dict(original)
        rehydrated = _creds_from_dict(d)
        assert rehydrated is not None
        assert rehydrated.token == original.token
        assert rehydrated.iot_cert == original.iot_cert
        assert rehydrated.endpoint == original.endpoint

    def test_from_dict_none_returns_none(self) -> None:
        assert _creds_from_dict(None) is None

    def test_from_dict_empty_dict_returns_none(self) -> None:
        assert _creds_from_dict({}) is None

    def test_from_dict_passthrough_on_dataclass_instance(self) -> None:
        # Tolerates legacy in-memory shape (pre-v2 hass.data).
        original = _make_creds()
        assert _creds_from_dict(original) is original

    def test_from_dict_malformed_returns_none(self) -> None:
        assert _creds_from_dict({"token": "x"}) is None  # missing required fields

    def test_from_dict_non_mapping_returns_none(self) -> None:
        assert _creds_from_dict("not a dict") is None


class TestMigrateEntry:
    @pytest.mark.asyncio
    async def test_migrate_v1_to_v2_with_cached_creds(self) -> None:
        creds = _make_creds()
        hass = MagicMock()
        hass.data = {DOMAIN: {KEY_IOT_CREDENTIALS: {"entry_x": creds}}}

        captured: dict = {}

        def update(_e, **kw):
            captured.update(kw)

        hass.config_entries.async_update_entry = update

        entry = MagicMock()
        entry.entry_id = "entry_x"
        entry.version = 1
        entry.data = {"api_key": "k"}

        result = await async_migrate_entry(hass, entry)

        assert result is True
        assert captured["version"] == 2
        new_data = captured["data"]
        assert KEY_IOT_CREDENTIALS in new_data
        assert new_data[KEY_IOT_CREDENTIALS]["token"] == "t"
        # Legacy hass.data entry was popped.
        assert "entry_x" not in hass.data[DOMAIN][KEY_IOT_CREDENTIALS]

    @pytest.mark.asyncio
    async def test_migrate_v1_to_v2_with_dict_shape_legacy(self) -> None:
        # If a prior in-process upgrade left a dict (rather than a dataclass)
        # in hass.data, it should pass through verbatim.
        cred_dict = asdict(_make_creds())
        hass = MagicMock()
        hass.data = {DOMAIN: {KEY_IOT_CREDENTIALS: {"entry_x": cred_dict}}}
        captured: dict = {}
        hass.config_entries.async_update_entry = lambda _e, **kw: captured.update(kw)

        entry = MagicMock()
        entry.entry_id = "entry_x"
        entry.version = 1
        entry.data = {}

        result = await async_migrate_entry(hass, entry)

        assert result is True
        assert captured["data"][KEY_IOT_CREDENTIALS] == cred_dict

    @pytest.mark.asyncio
    async def test_migrate_v1_to_v2_with_login_failure_marker(self) -> None:
        hass = MagicMock()
        hass.data = {
            DOMAIN: {KEY_IOT_LOGIN_FAILED: {"entry_x": "2FA verification required"}}
        }
        captured: dict = {}
        hass.config_entries.async_update_entry = lambda _e, **kw: captured.update(kw)

        entry = MagicMock()
        entry.entry_id = "entry_x"
        entry.version = 1
        entry.data = {}

        result = await async_migrate_entry(hass, entry)

        assert result is True
        assert captured["data"][KEY_IOT_LOGIN_FAILED] == "2FA verification required"
        assert "entry_x" not in hass.data[DOMAIN][KEY_IOT_LOGIN_FAILED]

    @pytest.mark.asyncio
    async def test_migrate_v1_to_v2_no_cached_data(self) -> None:
        hass = MagicMock()
        hass.data = {}
        captured: dict = {}
        hass.config_entries.async_update_entry = lambda _e, **kw: captured.update(kw)

        entry = MagicMock()
        entry.entry_id = "entry_x"
        entry.version = 1
        entry.data = {"api_key": "k"}

        result = await async_migrate_entry(hass, entry)

        assert result is True
        assert captured["version"] == 2
        # No IoT keys added since none were cached.
        assert KEY_IOT_CREDENTIALS not in captured["data"]
        assert KEY_IOT_LOGIN_FAILED not in captured["data"]

    @pytest.mark.asyncio
    async def test_migrate_skips_v2_entry(self) -> None:
        hass = MagicMock()
        hass.data = {}
        update_called = False

        def update(_e, **kw):
            nonlocal update_called
            update_called = True

        hass.config_entries.async_update_entry = update

        entry = MagicMock()
        entry.entry_id = "entry_x"
        entry.version = 2
        entry.data = {}

        result = await async_migrate_entry(hass, entry)

        assert result is True
        assert update_called is False

    @pytest.mark.asyncio
    async def test_migrate_rejects_downgrade(self) -> None:
        hass = MagicMock()
        hass.data = {}
        entry = MagicMock()
        entry.entry_id = "entry_x"
        entry.version = 99  # future schema version
        entry.data = {}

        result = await async_migrate_entry(hass, entry)

        assert result is False


class TestPersistIotCredentials:
    def test_persist_writes_creds_and_clears_failure(self) -> None:
        hass = MagicMock()
        captured: dict = {}
        hass.config_entries.async_update_entry = lambda _e, **kw: captured.update(kw)

        entry = MagicMock()
        entry.data = {"api_key": "k", KEY_IOT_LOGIN_FAILED: "stale failure"}

        creds = _make_creds()
        _persist_iot_credentials(hass, entry, creds, None)

        new_data = captured["data"]
        assert new_data[KEY_IOT_CREDENTIALS]["token"] == "t"
        assert KEY_IOT_LOGIN_FAILED not in new_data

    def test_persist_writes_failure_marker(self) -> None:
        hass = MagicMock()
        captured: dict = {}
        hass.config_entries.async_update_entry = lambda _e, **kw: captured.update(kw)

        entry = MagicMock()
        entry.data = {"api_key": "k"}

        _persist_iot_credentials(hass, entry, None, "boom")

        assert captured["data"][KEY_IOT_LOGIN_FAILED] == "boom"

    def test_persist_clears_both_when_called_with_no_args(self) -> None:
        hass = MagicMock()
        captured: dict = {}
        hass.config_entries.async_update_entry = lambda _e, **kw: captured.update(kw)

        entry = MagicMock()
        entry.data = {
            KEY_IOT_CREDENTIALS: {"token": "old"},
            KEY_IOT_LOGIN_FAILED: "old",
        }

        _persist_iot_credentials(hass, entry, None, None)

        assert KEY_IOT_CREDENTIALS not in captured["data"]
        assert KEY_IOT_LOGIN_FAILED not in captured["data"]
