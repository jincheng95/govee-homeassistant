"""Regression tests for non-UTF-8 AWS IoT payload decoding (issue #98).

Older strips (H6117/H6163) push state messages whose JSON string values carry
non-UTF-8 bytes (accented characters in scene/DIY/device names, e.g. 0xb0 '°',
0xfc 'ü' in latin-1). Strict ``bytes.decode()`` raised UnicodeDecodeError,
dropping the ENTIRE state message and breaking on/off feedback. The handler now
decodes with ``errors="replace"`` so a stray byte in a name no longer discards
the whole update.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.govee.api.auth import GoveeIotCredentials
from custom_components.govee.api.mqtt import GoveeAwsIotClient


def _make_client(on_state_update=None) -> GoveeAwsIotClient:
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
    return GoveeAwsIotClient(creds, on_state_update=on_state_update or MagicMock())


def _msg(payload: bytes) -> MagicMock:
    m = MagicMock()
    m.payload = payload
    m.topic = "GA/account"
    return m


class TestNonUtf8PayloadDecode:
    @pytest.mark.asyncio
    async def test_state_message_with_non_utf8_byte_still_dispatched(self):
        cb = MagicMock()
        client = _make_client(cb)
        # 0xb0 ('°' in latin-1) is an invalid UTF-8 start byte — embedded in a
        # scene name string value, the rest of the JSON is well-formed.
        payload = (
            b'{"device":"AA:BB:CC:DD:EE:FF","state":'
            b'{"onOff":1,"brightness":50,"sceneName":"Patio\xb0"}}'
        )

        await client._handle_message(_msg(payload))

        cb.assert_called_once()
        device_id, state = cb.call_args.args
        assert device_id == "AA:BB:CC:DD:EE:FF"
        assert state["onOff"] == 1
        assert state["brightness"] == 50
        # The undecodable byte is replaced (U+FFFD), not fatal.
        assert "�" in state["sceneName"]

    @pytest.mark.asyncio
    async def test_multiple_non_utf8_bytes_do_not_drop_message(self):
        cb = MagicMock()
        client = _make_client(cb)
        # 0xfc ('ü') + 0xb0 ('°') — both invalid UTF-8 starts.
        payload = (
            b'{"device":"11:22:33:44:55:66","state":'
            b'{"onOff":0,"name":"B\xfcro\xb0"}}'
        )

        await client._handle_message(_msg(payload))

        cb.assert_called_once()
        device_id, state = cb.call_args.args
        assert device_id == "11:22:33:44:55:66"
        assert state["onOff"] == 0

    @pytest.mark.asyncio
    async def test_clean_utf8_payload_unaffected(self):
        cb = MagicMock()
        client = _make_client(cb)
        payload = b'{"device":"AA:BB","state":{"onOff":1,"brightness":100}}'

        await client._handle_message(_msg(payload))

        cb.assert_called_once()
        device_id, state = cb.call_args.args
        assert device_id == "AA:BB"
        assert state["brightness"] == 100
