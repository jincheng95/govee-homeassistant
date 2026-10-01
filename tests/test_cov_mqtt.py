"""Lifecycle, message-loop and decoder edge cases of the AWS IoT MQTT client.

Fills the gaps left by ``test_mqtt_connection.py`` and friends: the public
start/stop/restart lifecycle run for real against a scripted ``aiomqtt``
stand-in, the in-session message loop (early exit, flap-streak reset, the
error-after-stop path, cancellation), the mutual-TLS context builder against
a throwaway certificate, the malformed-payload guards of ``_handle_message``,
the error paths of the multiSync/probe decoders and the diagnostics buffers.

No socket is ever opened and nothing sleeps for real: ``aiomqtt`` is replaced
by ``FakeAiomqtt`` and the module's ``asyncio``/``time``/``ssl``/``tempfile``
references are wrapped so only the one attribute a test needs is intercepted.
"""

from __future__ import annotations

import asyncio
import base64
import datetime as dt
import json
import logging
import os
import ssl
import stat
import struct
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, PropertyMock

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from custom_components.govee.api import mqtt as mqtt_mod
from custom_components.govee.api.auth import GoveeIotCredentials
from custom_components.govee.api.mqtt import GoveeAwsIotClient, _decode_op_frames

LOGGER_NAME = "custom_components.govee.api.mqtt"
DEVICE_ID = "AA:BB:CC:DD:EE:FF:00:11"
HUB_ID = "07:23:5C:E7:53:5F:6F:0A"
PROBE_ID = "11:22:33:44:55:66:77:88"

# Real H5059-via-H5044 LEAK packet from the #87 diagnostics (slot 0, wet).
LEAK_WET = bytes.fromhex("ee34000200641e14ad6a1f4a58000103018000ff")


def _creds(**over) -> GoveeIotCredentials:
    base = dict(
        token="t",
        refresh_token="r",
        account_topic="GA/account",
        iot_cert="cert",
        iot_key="key",
        iot_ca=None,
        client_id="cid",
        endpoint="endpoint",
    )
    base.update(over)
    return GoveeIotCredentials(**base)


def _msg(payload, *, topic: str = "GA/account") -> MagicMock:
    """An inbound aiomqtt message; dicts/lists are JSON-encoded, bytes/str sent verbatim."""
    message = MagicMock()
    message.topic = topic
    message.payload = json.dumps(payload).encode() if isinstance(payload, (dict, list)) else payload
    return message


def _state_msg(device_id: str = DEVICE_ID, **state) -> MagicMock:
    return _msg({"device": device_id, "sku": "H6072", "state": state or {"onOff": 1}})


def _b64(*frames: bytes) -> list[str]:
    return [base64.b64encode(frame).decode() for frame in frames]


def _probe_msg(cmd: str, *frames: bytes) -> dict:
    return {"device": PROBE_ID, "sku": "H5192", "cmd": cmd, "op": {"command": _b64(*frames)}}


def _multisync(*frames: bytes) -> dict:
    return {"device": HUB_ID, "sku": "H5044", "cmd": "multiSync", "op": {"command": _b64(*frames)}}


def _self_signed_pem() -> tuple[str, str]:
    """A throwaway EC key + self-signed certificate as PEM text (milliseconds to make)."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "govee-test-client")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return cert_pem, key_pem


class ScriptedMessages:
    """Async iterator that yields scripted messages, then ends, raises or parks forever."""

    def __init__(self, items=(), *, error: Exception | None = None, block: bool = False) -> None:
        self._items = list(items)
        self._error = error
        self._block = block

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._items:
            return self._items.pop(0)
        if self._block:
            await asyncio.Event().wait()  # parked until the owning task is cancelled
        if self._error is not None:
            raise self._error
        raise StopAsyncIteration


class FakeAiomqtt:
    """Stand-in for the aiomqtt module: one scripted session per ``Client()``.

    Session specs: ``connect_error`` (raised on enter), ``granted`` (SUBACK),
    ``messages`` (yielded in order), ``drop_error`` (raised after them) and
    ``block`` (park after them until cancelled).
    """

    class MqttError(Exception):
        pass

    def __init__(self, sessions) -> None:
        self.sessions = list(sessions)
        self.clients: list[MagicMock] = []

    def Client(self, **kwargs):  # noqa: N802 - mimics aiomqtt.Client
        spec = self.sessions.pop(0) if self.sessions else {"drop_error": self.MqttError("no more sessions")}
        client = MagicMock()
        client.kwargs = kwargs
        connect_error = spec.get("connect_error")

        async def aenter():
            if connect_error is not None:
                raise connect_error
            return client

        client.__aenter__ = AsyncMock(side_effect=aenter)
        client.__aexit__ = AsyncMock(return_value=False)
        client.subscribe = AsyncMock(return_value=spec.get("granted", (1,)))
        client.messages = ScriptedMessages(
            spec.get("messages", ()),
            error=spec.get("drop_error"),
            block=spec.get("block", False),
        )
        client.publish = AsyncMock()
        self.clients.append(client)
        return client


class _ModuleProxy:
    """Forward attribute access to a real module except for explicit overrides.

    Lets a test swap ``asyncio.sleep`` or ``time.monotonic`` *as seen from
    mqtt.py* without patching the global module, so the test body keeps the
    real ``asyncio.sleep`` for yielding to the loop.
    """

    def __init__(self, module, **overrides) -> None:
        self._module = module
        self._overrides = overrides

    def __getattr__(self, name):
        if name in self._overrides:
            return self._overrides[name]
        return getattr(self._module, name)


class Harness:
    """A client wired to a FakeAiomqtt with instant sleeps and stop conditions."""

    def __init__(
        self,
        monkeypatch,
        sessions,
        *,
        clock=None,
        stop_after_sleeps: int | None = None,
        stop_after_updates: int | None = None,
        update_error: Exception | None = None,
        **client_kwargs,
    ) -> None:
        self.fake = FakeAiomqtt(sessions)
        self.sleeps: list[float] = []
        self.updates: list[tuple[str, dict]] = []
        self.stop_after_sleeps = stop_after_sleeps
        self.stop_after_updates = stop_after_updates
        self.update_error = update_error
        self.client = GoveeAwsIotClient(_creds(), on_state_update=self._on_update, **client_kwargs)
        # The TLS context is built elsewhere (TestSslContext); keep the loop single-threaded here.
        self.client._create_ssl_context = AsyncMock(return_value=MagicMock())
        monkeypatch.setattr(mqtt_mod, "aiomqtt", self.fake)
        monkeypatch.setattr(mqtt_mod, "AIOMQTT_AVAILABLE", True)
        monkeypatch.setattr(mqtt_mod, "asyncio", _ModuleProxy(asyncio, sleep=self._sleep))
        if clock is not None:
            monkeypatch.setattr(mqtt_mod, "time", _ModuleProxy(time, monotonic=clock))

    def _on_update(self, device_id: str, state: dict) -> None:
        self.updates.append((device_id, state))
        if self.stop_after_updates is not None and len(self.updates) >= self.stop_after_updates:
            self.client._running = False
        if self.update_error is not None:
            raise self.update_error

    async def _sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        if self.stop_after_sleeps is not None and len(self.sleeps) >= self.stop_after_sleeps:
            self.client._running = False

    async def run(self) -> None:
        """Drive ``_connection_loop`` directly until a stop condition fires."""
        self.client._running = True
        await self.client._connection_loop()


@pytest.fixture
def harness(monkeypatch):
    return lambda sessions, **kwargs: Harness(monkeypatch, sessions, **kwargs)


async def _until(predicate, *, attempts: int = 500) -> None:
    """Yield to the loop until ``predicate()`` holds (bounded, no real sleeping)."""
    for _ in range(attempts):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition not reached")


# ==============================================================================
# Helpers and properties
# ==============================================================================


class TestHelpers:
    def test_decode_op_frames_tolerates_bad_shapes(self):
        """A missing/odd op block or a bad entry never costs the good frames."""
        good = bytes([0xAA, 0x1D, 0x01, 1, 2, 3, 4])
        assert _decode_op_frames(None) == []
        assert _decode_op_frames({"command": "not-a-list"}) == []
        assert _decode_op_frames({"command": [7, None, "A", base64.b64encode(good).decode()]}) == [good]


class TestProperties:
    def test_available_reflects_library_presence(self, monkeypatch):
        client = GoveeAwsIotClient(_creds(), on_state_update=MagicMock())
        monkeypatch.setattr(mqtt_mod, "AIOMQTT_AVAILABLE", True)
        assert client.available is True
        monkeypatch.setattr(mqtt_mod, "AIOMQTT_AVAILABLE", False)
        assert client.available is False

    def test_connected_since_is_none_until_connected(self):
        client = GoveeAwsIotClient(_creds(), on_state_update=MagicMock())
        assert client.connected_since is None
        client._connected_since = time.monotonic()
        assert client.connected_since is None  # a start time without a live session is meaningless

    def test_connected_since_is_wall_clock_minus_session_age(self):
        client = GoveeAwsIotClient(_creds(), on_state_update=MagicMock())
        client._connected = True
        client._connected_since = time.monotonic() - 90
        since = client.connected_since
        assert since is not None and since.tzinfo is dt.timezone.utc
        age = (dt.datetime.now(dt.timezone.utc) - since).total_seconds()
        assert 89 <= age <= 95

    def test_last_message_ts_for_unknown_device_is_none(self):
        client = GoveeAwsIotClient(_creds(), on_state_update=MagicMock())
        assert client.last_message_ts is None
        assert client.last_message_ts_for(DEVICE_ID) is None
        assert client.fan_swing_tail(DEVICE_ID) is None
        assert client.recent_probe_frames == []


# ==============================================================================
# Public lifecycle: start / stop / restart
# ==============================================================================


class TestStartStop:
    @pytest.mark.asyncio
    async def test_start_without_aiomqtt_is_a_no_op(self, monkeypatch, caplog):
        monkeypatch.setattr(mqtt_mod, "AIOMQTT_AVAILABLE", False)
        client = GoveeAwsIotClient(_creds(), on_state_update=MagicMock())
        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            await client.async_start()
        assert client._task is None
        assert client._running is False
        assert any("aiomqtt" in rec.getMessage() for rec in caplog.records)

    @pytest.mark.asyncio
    async def test_start_twice_keeps_the_first_task(self, harness):
        h = harness([{"block": True}])
        await h.client.async_start()
        first = h.client._task
        assert first is not None
        await h.client.async_start()
        assert h.client._task is first
        await _until(lambda: h.client.connected)
        assert len(h.fake.clients) == 1
        await h.client.async_stop()

    @pytest.mark.asyncio
    async def test_stop_cancels_the_live_session(self, harness, caplog):
        h = harness([{"block": True}])
        await h.client.async_start()
        await _until(lambda: h.client.connected)
        assert h.client._client is h.fake.clients[0]
        assert h.client.connected_since is not None

        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            await h.client.async_stop()

        assert h.client._task is None
        assert h.client.connected is False
        assert h.client._client is None
        assert h.client.connected_since is None
        h.fake.clients[0].__aexit__.assert_awaited_once()  # the broker session was closed
        assert any("cancelled" in rec.getMessage() for rec in caplog.records)
        assert h.sleeps == []  # a cancel is not a failure: no backoff, no reconnect
        assert len(h.fake.clients) == 1

    @pytest.mark.asyncio
    async def test_stop_while_not_running_removes_leftover_temp_dir(self, tmp_path):
        client = GoveeAwsIotClient(_creds(), on_state_update=MagicMock())
        leftover = tempfile.TemporaryDirectory(dir=tmp_path)
        client._temp_dir = leftover
        client._client = MagicMock()
        client._connected = True
        assert Path(leftover.name).is_dir()

        await client.async_stop()

        assert not Path(leftover.name).exists()
        assert client._temp_dir is None
        assert client._client is None
        assert client.connected is False
        assert client._task is None

    @pytest.mark.asyncio
    async def test_stop_swallows_temp_dir_cleanup_error(self, caplog):
        client = GoveeAwsIotClient(_creds(), on_state_update=MagicMock())
        broken = MagicMock()
        broken.cleanup.side_effect = OSError("busy")
        client._temp_dir = broken

        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            await client.async_stop()

        broken.cleanup.assert_called_once()
        assert client._temp_dir is None
        assert any("cleanup" in rec.getMessage() for rec in caplog.records)


class TestRestart:
    @pytest.mark.asyncio
    async def test_rotated_material_reconnects_with_the_new_endpoint(self, harness):
        h = harness([{"block": True}, {"block": True}])
        h.client._ssl_context = MagicMock()
        await h.client.async_start()
        await _until(lambda: h.client.connected)

        changed = await h.client.async_restart(_creds(iot_key="key2", endpoint="endpoint2"))

        assert changed is True
        assert h.client._ssl_context is None  # the cached context held the old key
        await _until(lambda: h.client.connected and len(h.fake.clients) == 2)
        h.fake.clients[0].__aexit__.assert_awaited_once()  # old session closed
        assert h.fake.clients[0].kwargs["hostname"] == "endpoint"
        assert h.fake.clients[1].kwargs["hostname"] == "endpoint2"
        assert h.client._credentials.iot_key == "key2"
        await h.client.async_stop()

    @pytest.mark.asyncio
    async def test_same_material_keeps_the_session_and_adopts_the_token(self, harness):
        h = harness([{"block": True}])
        await h.client.async_start()
        await _until(lambda: h.client.connected)

        assert await h.client.async_restart(_creds(token="fresh")) is False

        assert h.client.connected is True
        assert len(h.fake.clients) == 1
        assert h.client._credentials.token == "fresh"
        await h.client.async_stop()

    @pytest.mark.asyncio
    async def test_restart_of_a_stopped_client_swaps_material_without_starting(self, harness):
        h = harness([])
        h.client._consecutive_failures = 4
        h.client._unhealthy_reported = True

        assert await h.client.async_restart(_creds(account_topic="GA/other")) is True

        assert h.client._task is None
        assert h.fake.clients == []
        assert h.client._credentials.account_topic == "GA/other"
        assert h.client.consecutive_failures == 0
        assert h.client._unhealthy_reported is False


# ==============================================================================
# The connection loop around a live session
# ==============================================================================


class TestConnectionLoop:
    @pytest.mark.asyncio
    async def test_messages_are_dispatched_until_stopped(self, harness):
        """Stopping mid-stream drops the rest of the stream without a reconnect."""
        h = harness([{"messages": [_state_msg(onOff=1), _state_msg(onOff=0)]}], stop_after_updates=1)

        await h.run()

        assert h.updates == [(DEVICE_ID, {"onOff": 1})]
        assert h.client.last_messages[DEVICE_ID] == {"onOff": 1}
        assert h.client.last_message_ts_for(DEVICE_ID) == h.client.last_message_ts
        assert h.client.connected is False
        assert h.client._client is None
        assert h.sleeps == []
        assert len(h.fake.clients) == 1

    @pytest.mark.asyncio
    async def test_stable_session_clears_the_failure_streak_mid_session(self, harness, monkeypatch):
        """A session alive for STABLE_SESSION_SECONDS proves itself: streak and repair flag reset."""
        monkeypatch.setattr(mqtt_mod, "MAX_RECONNECT_ATTEMPTS", 1)
        now = [1000.0]

        def clock():
            now[0] += mqtt_mod.STABLE_SESSION_SECONDS
            return now[0]

        give_up = MagicMock()
        h = harness(
            [{"connect_error": OSError("x")}, {"messages": [_state_msg(), _state_msg()]}],
            clock=clock,
            stop_after_updates=1,
            on_give_up=give_up,
        )

        await h.run()

        give_up.assert_called_once_with(1, "x")
        assert h.sleeps == [mqtt_mod.RECONNECT_BASE]
        assert h.updates == [(DEVICE_ID, {"onOff": 1})]
        assert h.client.consecutive_failures == 0
        assert h.client._unhealthy_reported is False

    @pytest.mark.asyncio
    async def test_drop_after_stop_neither_sleeps_nor_reconnects(self, harness):
        disconnected = MagicMock()
        h = harness(
            [{"messages": [_state_msg()], "drop_error": FakeAiomqtt.MqttError("kicked")}],
            stop_after_updates=1,
            on_disconnected=disconnected,
        )

        await h.run()

        disconnected.assert_called_once()
        assert "kicked" in (h.client.last_error or "")
        assert h.client.connected is False
        assert h.sleeps == []
        assert len(h.fake.clients) == 1

    @pytest.mark.asyncio
    async def test_stream_ending_cleanly_reconnects(self, harness):
        """An exhausted message stream is not an error but the loop must not stall on it."""
        h = harness([{"messages": [_state_msg()]}, {"connect_error": OSError("down")}], stop_after_sleeps=1)

        await h.run()

        assert len(h.updates) == 1
        assert len(h.fake.clients) == 2
        assert h.sleeps == [mqtt_mod.RECONNECT_BASE]
        assert h.client.connected is False

    @pytest.mark.asyncio
    async def test_mqtt5_reason_code_refusal_is_a_failure(self, harness):
        connected = MagicMock()
        refused = [MagicMock(is_failure=True)]
        h = harness([{"granted": refused}], stop_after_sleeps=1, on_connected=connected)

        await h.run()

        connected.assert_not_called()
        h.fake.clients[0].__aexit__.assert_awaited_once()
        assert h.client.consecutive_failures == 1
        assert "refused" in (h.client.last_error or "")

    @pytest.mark.asyncio
    async def test_give_up_carries_the_last_error_and_fires_once(self, harness, monkeypatch, caplog):
        monkeypatch.setattr(mqtt_mod, "MAX_RECONNECT_ATTEMPTS", 2)
        give_up = MagicMock()
        h = harness([{"connect_error": OSError("tls handshake")}] * 3, stop_after_sleeps=3, on_give_up=give_up)

        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            await h.run()

        give_up.assert_called_once_with(2, "tls handshake")
        assert h.client.last_error == "OSError: tls handshake"
        levels = [rec.levelno for rec in caplog.records if "reconnecting in" in rec.getMessage()]
        assert levels == [logging.WARNING, logging.DEBUG, logging.DEBUG]
        assert [rec for rec in caplog.records if rec.levelno == logging.ERROR and "2 times" in rec.getMessage()]


# ==============================================================================
# Publishing
# ==============================================================================


class TestPublishCommand:
    def _connected_client(self, publish=None) -> GoveeAwsIotClient:
        client = GoveeAwsIotClient(_creds(), on_state_update=MagicMock())
        client._connected = True
        client._client = MagicMock()
        client._client.publish = publish or AsyncMock()
        return client

    @pytest.mark.asyncio
    async def test_not_connected_returns_false(self, caplog):
        client = GoveeAwsIotClient(_creds(), on_state_update=MagicMock())
        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            assert await client.async_publish_command("GD/topic", "turn", {"val": 1}) is False
        assert any("not connected" in rec.getMessage() for rec in caplog.records)

    @pytest.mark.asyncio
    async def test_missing_topic_returns_false_without_publishing(self):
        client = self._connected_client()
        assert await client.async_publish_command(None, "turn", {"val": 1}) is False
        assert await client.async_publish_command("", "turn", {"val": 1}) is False
        client._client.publish.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_broker_error_returns_false(self):
        client = self._connected_client(AsyncMock(side_effect=FakeAiomqtt.MqttError("gone")))
        assert await client.async_publish_command("GD/topic", "turn", {"val": 1}) is False

    @pytest.mark.asyncio
    async def test_envelope_carries_cmd_version_and_control_type(self):
        client = self._connected_client()
        assert await client.async_publish_command("GD/topic", "color", {"r": 1}, cmd_version=1) is True
        args, kwargs = client._client.publish.call_args
        msg = json.loads(args[1])["msg"]
        assert msg["cmd"] == "color"
        assert msg["data"] == {"r": 1}
        assert msg["cmdVersion"] == 1
        assert msg["type"] == 1
        assert msg["transaction"].startswith("v_")
        assert kwargs == {"qos": 1, "timeout": mqtt_mod.ACK_TIMEOUT}

    @pytest.mark.asyncio
    async def test_ptreal_addresses_the_device_inside_the_data_block(self):
        client = self._connected_client()
        assert await client.async_publish_ptreal(DEVICE_ID, "H7107", "qgEA", device_topic="GD/topic") is True
        msg = json.loads(client._client.publish.call_args.args[1])["msg"]
        assert msg["cmd"] == "ptReal"
        assert msg["data"] == {"command": ["qgEA"], "device": DEVICE_ID, "sku": "H7107"}

        assert await client.async_publish_ptreal(DEVICE_ID, "H7107", ["a", "b"], device_topic="GD/topic") is True
        msg = json.loads(client._client.publish.call_args.args[1])["msg"]
        assert msg["data"]["command"] == ["a", "b"]


# ==============================================================================
# _handle_message guards
# ==============================================================================


class TestHandleMessageGuards:
    def _client(self, callback=None) -> GoveeAwsIotClient:
        return GoveeAwsIotClient(_creds(), on_state_update=callback or MagicMock())

    @pytest.mark.asyncio
    async def test_non_object_payload_is_ignored(self):
        client = self._client()
        await client._handle_message(_msg([1, 2, 3]))
        client._on_state_update.assert_not_called()
        assert client.last_message_ts is None

    @pytest.mark.asyncio
    async def test_msg_wrapper_with_invalid_json_is_ignored(self):
        client = self._client()
        await client._handle_message(_msg({"msg": "{not json"}))
        client._on_state_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_device_id_is_ignored(self):
        client = self._client()
        await client._handle_message(_msg({"sku": "H6072", "state": {"onOff": 1}}))
        client._on_state_update.assert_not_called()
        assert client.last_message_ts is None

    @pytest.mark.asyncio
    async def test_state_less_message_is_ignored_but_counts_as_activity(self):
        client = self._client()
        await client._handle_message(_msg({"device": DEVICE_ID, "cmd": "status"}))
        client._on_state_update.assert_not_called()
        assert client.last_message_ts_for(DEVICE_ID) is not None

    @pytest.mark.asyncio
    async def test_invalid_json_is_a_warning(self, caplog):
        client = self._client()
        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            await client._handle_message(_msg(b"{bad json"))
        assert any(rec.levelno == logging.WARNING and "parse" in rec.getMessage() for rec in caplog.records)
        client._on_state_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_unexpected_error_is_contained(self, caplog):
        client = self._client()
        message = MagicMock()
        type(message).payload = PropertyMock(side_effect=RuntimeError("no payload"))
        with caplog.at_level(logging.ERROR, logger=LOGGER_NAME):
            await client._handle_message(message)
        assert any("Error handling" in rec.getMessage() for rec in caplog.records)

    @pytest.mark.asyncio
    async def test_callback_error_does_not_lose_the_diagnostics_copy(self, caplog):
        client = self._client(MagicMock(side_effect=RuntimeError("entity gone")))
        with caplog.at_level(logging.ERROR, logger=LOGGER_NAME):
            await client._handle_message(_state_msg(onOff=1, brightness=42))
        assert client.last_messages[DEVICE_ID] == {"onOff": 1, "brightness": 42}
        assert any("callback failed" in rec.getMessage() for rec in caplog.records)

    @pytest.mark.asyncio
    async def test_string_payload_is_accepted(self):
        client = self._client()
        message = MagicMock()
        message.topic = "GA/account"
        message.payload = json.dumps({"device": DEVICE_ID, "state": {"onOff": 0}})
        await client._handle_message(message)
        client._on_state_update.assert_called_once_with(DEVICE_ID, {"onOff": 0})

    @pytest.mark.asyncio
    async def test_freshness_is_stamped_per_device(self):
        client = self._client()
        other = "22:22:22:22:22:22:22:22"
        await client._handle_message(_state_msg(DEVICE_ID))
        first = client.last_message_ts
        await client._handle_message(_state_msg(other))
        assert client.last_message_ts_for(DEVICE_ID) == first
        assert client.last_message_ts_for(other) == client.last_message_ts
        assert client.last_message_ts >= first

    @pytest.mark.asyncio
    async def test_op_frames_ride_along_on_state_and_feed_the_fan_tail(self):
        client = self._client()
        frame = bytes([0xAA, 0x1D, 0x00, 0x10, 0x20, 0x30, 0x40] + [0] * 13)
        payload = {"device": DEVICE_ID, "sku": "H7107", "cmd": "status", "op": {"command": _b64(frame)}}
        payload["state"] = {"onOff": 1}
        await client._handle_message(_msg(payload))
        _, state = client._on_state_update.call_args.args
        assert state["_op_frames"] == [frame.hex()]
        assert client.fan_swing_tail(DEVICE_ID) == [0x10, 0x20, 0x30, 0x40]


# ==============================================================================
# Probe-thermometer frames (H5192)
# ==============================================================================


class TestProbeFrames:
    def _client(self, callback=None) -> GoveeAwsIotClient:
        return GoveeAwsIotClient(_creds(), on_state_update=callback or MagicMock())

    @staticmethod
    def _limits_frame(probe: int, core_max: int, core_min: int, ambient_max: int, ambient_min: int) -> bytes:
        body = bytes([0xAA, 0x12, probe]) + struct.pack(">hhhh", core_max, core_min, ambient_max, ambient_min)
        return body + bytes(20 - len(body))

    @pytest.mark.asyncio
    async def test_empty_command_list_is_not_recorded(self):
        client = self._client()
        await client._handle_message(_msg(_probe_msg("status")))
        client._on_state_update.assert_not_called()
        assert client.recent_probe_frames == []

    @pytest.mark.asyncio
    async def test_limits_reply_is_decoded_per_probe(self):
        client = self._client()
        frame = self._limits_frame(1, 8800, 3300, 7000, -1000)

        await client._handle_message(_msg(_probe_msg("ptReal", frame)))

        client._on_state_update.assert_called_once_with(
            PROBE_ID,
            {
                "_probe_frame": True,
                "probes": {1: {"core_max": 88.0, "core_min": 33.0, "ambient_max": 70.0, "ambient_min": -10.0}},
            },
        )
        record = client.recent_probe_frames[0]
        assert record["device_id"] == PROBE_ID
        assert record["sku"] == "H5192"
        assert record["cmd"] == "ptReal"
        assert record["header"] == "aa12"
        assert record["hex"] == frame.hex()
        assert record["length"] == 20

    @pytest.mark.asyncio
    async def test_probe_callback_error_is_contained(self, caplog):
        client = self._client(MagicMock(side_effect=RuntimeError("sensor gone")))
        frame = self._limits_frame(2, 8800, 3300, 7000, -1000)
        with caplog.at_level(logging.ERROR, logger=LOGGER_NAME):
            await client._handle_message(_msg(_probe_msg("ptReal", frame)))
        assert any("probe callback failed" in rec.getMessage() for rec in caplog.records)
        assert len(client.recent_probe_frames) == 1  # still captured for diagnostics

    @pytest.mark.asyncio
    async def test_unknown_register_is_only_buffered(self, caplog):
        client = self._client()
        frame = bytes([0xAA, 0x99, 0x01]) + bytes(17)
        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            await client._handle_message(_msg(_probe_msg("ptReal", frame)))
        client._on_state_update.assert_not_called()
        assert client.recent_probe_frames[0]["header"] == "aa99"
        assert any("Unhandled probe frame" in rec.getMessage() for rec in caplog.records)

    @pytest.mark.asyncio
    async def test_probe_frame_buffer_is_bounded(self):
        client = self._client()
        for i in range(70):
            frame = bytes([0xAA, 0x99, i]) + bytes(17)
            await client._handle_message(_msg(_probe_msg("ptReal", frame)))
        frames = client.recent_probe_frames
        assert len(frames) == 64
        assert int(frames[0]["hex"][4:6], 16) == 6  # the six oldest were dropped

    @pytest.mark.asyncio
    async def test_light_strip_op_frames_never_reach_the_probe_decoder(self):
        client = self._client()
        frame = self._limits_frame(1, 8800, 3300, 7000, -1000)
        payload = {"device": DEVICE_ID, "sku": "H6072", "cmd": "ptReal", "op": {"command": _b64(frame)}}
        await client._handle_message(_msg(payload))
        assert client.recent_probe_frames == []
        client._on_state_update.assert_not_called()  # no state either: nothing to dispatch


# ==============================================================================
# multiSync hub frames
# ==============================================================================


class TestMultiSyncErrors:
    def _client(self, callback=None) -> GoveeAwsIotClient:
        return GoveeAwsIotClient(_creds(), on_state_update=callback or MagicMock())

    @pytest.mark.asyncio
    async def test_presence_callback_error_is_contained(self, caplog):
        client = self._client(MagicMock(side_effect=RuntimeError("boom")))
        presence = bytes([0xAA, 0x01, 0x01] + [0] * 13 + [0x01])
        with caplog.at_level(logging.ERROR, logger=LOGGER_NAME):
            await client._handle_message(_msg(_multisync(presence)))
        client._on_state_update.assert_called_once_with(HUB_ID, {"triSta": 1})
        assert any("presence callback failed" in rec.getMessage() for rec in caplog.records)
        assert client.recent_multisync[0]["header"] == "aa01"

    @pytest.mark.asyncio
    async def test_frame_with_foreign_header_is_only_buffered(self):
        client = self._client()
        foreign = bytes([0x33, 0x05, 0x04, 0x00, 0x00, 0x00])
        await client._handle_message(_msg(_multisync(foreign)))
        client._on_state_update.assert_not_called()
        assert client.recent_multisync[0]["hex"] == foreign.hex()

    @pytest.mark.asyncio
    async def test_leak_callback_error_is_contained(self, caplog):
        client = self._client(MagicMock(side_effect=RuntimeError("boom")))
        with caplog.at_level(logging.ERROR, logger=LOGGER_NAME):
            await client._handle_message(_msg(_multisync(LEAK_WET)))
        event = client._on_state_update.call_args.args[1]
        assert event["_leak_event"] is True and event["is_wet"] is True
        assert any("multiSync callback failed" in rec.getMessage() for rec in caplog.records)

    @pytest.mark.asyncio
    async def test_hub_message_counts_as_transport_activity(self):
        client = self._client()
        await client._handle_message(_msg(_multisync(LEAK_WET)))
        assert client.last_message_ts_for(HUB_ID) is not None
        assert HUB_ID not in client.last_messages  # events are not state snapshots


# ==============================================================================
# Mutual-TLS context
# ==============================================================================


class TestSslContext:
    @pytest.fixture
    def scratch_tempdir(self, monkeypatch, tmp_path):
        """Route the builder's private temp directory under tmp_path so leftovers are visible."""

        def temporary_directory(**kwargs):
            return tempfile.TemporaryDirectory(dir=tmp_path, **kwargs)

        monkeypatch.setattr(mqtt_mod, "tempfile", _ModuleProxy(tempfile, TemporaryDirectory=temporary_directory))
        return tmp_path

    def test_builds_a_verified_mutual_tls_context(self, monkeypatch, scratch_tempdir):
        cert_pem, key_pem = _self_signed_pem()
        seen: dict[str, tuple[int, str]] = {}

        class RecordingContext(ssl.SSLContext):
            def load_cert_chain(self, certfile, keyfile=None, password=None):
                for label, path in (("cert", certfile), ("key", keyfile)):
                    seen[label] = (stat.S_IMODE(os.stat(path).st_mode), Path(path).read_text())
                return super().load_cert_chain(certfile, keyfile, password)

        monkeypatch.setattr(mqtt_mod, "ssl", _ModuleProxy(ssl, SSLContext=RecordingContext))
        client = GoveeAwsIotClient(_creds(iot_cert=cert_pem, iot_key=key_pem), on_state_update=MagicMock())

        context = client._create_ssl_context_sync()

        assert isinstance(context, RecordingContext)
        assert seen["cert"] == (0o600, cert_pem)
        assert seen["key"] == (0o600, key_pem)
        assert list(scratch_tempdir.iterdir()) == []  # key material does not outlive the call
        assert client._temp_dir is None
        assert context.minimum_version == ssl.TLSVersion.TLSv1_2
        assert context.verify_mode == ssl.CERT_REQUIRED
        assert context.check_hostname is True
        roots = {ca["subject"][-1][0][1] for ca in context.get_ca_certs()}
        assert roots == {f"Amazon Root CA {n}" for n in (1, 2, 3, 4)}

    def test_invalid_material_raises_and_leaves_no_files(self, scratch_tempdir):
        client = GoveeAwsIotClient(_creds(), on_state_update=MagicMock())  # "cert"/"key" are not PEM
        with pytest.raises(ssl.SSLError):
            client._create_ssl_context_sync()
        assert list(scratch_tempdir.iterdir()) == []

    @pytest.mark.parametrize("cleanup_error", [None, OSError("already gone")])
    def test_leftover_temp_dir_is_cleaned_first(self, scratch_tempdir, cleanup_error):
        cert_pem, key_pem = _self_signed_pem()
        client = GoveeAwsIotClient(_creds(iot_cert=cert_pem, iot_key=key_pem), on_state_update=MagicMock())
        leftover = MagicMock()
        leftover.cleanup.side_effect = cleanup_error
        client._temp_dir = leftover

        context = client._create_ssl_context_sync()

        leftover.cleanup.assert_called_once()
        assert client._temp_dir is None
        assert isinstance(context, ssl.SSLContext)

    @pytest.mark.asyncio
    async def test_cached_context_is_built_once(self):
        client = GoveeAwsIotClient(_creds(), on_state_update=MagicMock())
        built = MagicMock()
        client._create_ssl_context_sync = MagicMock(return_value=built)

        assert await client._create_ssl_context() is built
        assert await client._create_ssl_context() is built

        client._create_ssl_context_sync.assert_called_once()
