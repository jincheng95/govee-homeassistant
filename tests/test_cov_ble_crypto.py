"""Handshake, session and identity-check tests for the encrypted BLE transport.

Complements ``test_ble_crypto.py`` (key-derivation known-answer tests and the
timeout cleanup) with the successful handshake driven end to end against a
fake GATT client that plays the device's half of the protocol, the
post-handshake notification path into ``on_frame``, the advisory identity
check, and the session's failure modes (bad key length, wrong IV key,
truncated or tampered packets).
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from custom_components.govee.api.ble_crypto import (
    VERSION_CHARACTERISTIC_UUID,
    GoveeBLESession,
    _log_identity,
    async_establish_session,
    async_supports_encryption,
    derive_device_key,
)

LOGGER_NAME = "custom_components.govee.api.ble_crypto"

# Captured from a real H1270 (MAC C0:EB:32:C1:19:FC): "H1270" + reversed MAC.
_ADDRESS = "C0:EB:32:C1:19:FC"
_DEVICE_INFO = bytes.fromhex("4831323730fc19c132ebc0")
_DEVICE_KEY = bytes.fromhex("6180f19566dedc80a3adef2f8b936184")
_HANDSHAKE_KEY = bytes.fromhex("fc03783c7c42cb83e202a1643648aff6")
_RX_IV_KEY = bytes.fromhex("8a10ed17775ef1f4")
_HANDSHAKE_RESPONSE_HEADER = bytes((0xE7, 0x11, 0x00))

WRITE_UUID = "write-uuid"
NOTIFY_UUID = "notify-uuid"


class FakeDevice:
    """GATT client stand-in that answers the handshake like a real H1270.

    On the handshake write it decrypts our request (learning ``tx_iv_key``
    exactly as the device does), then notifies an encrypted reply carrying
    ``_RX_IV_KEY`` and its identity. ``push`` sends an encrypted data frame.
    """

    def __init__(
        self,
        *,
        device_info: bytes = _DEVICE_INFO,
        address: str = _ADDRESS,
        handshake_key: bytes = _HANDSHAKE_KEY,
        pre_notify: bytes | None = None,
    ) -> None:
        self.address = address
        self.device_info = device_info
        self.handshake_key = handshake_key
        self.pre_notify = pre_notify
        self.notify = None
        self.tx_iv_key: bytes | None = None
        self.writes: list[bytes] = []
        self.start_notify = AsyncMock(side_effect=self._start_notify)
        self.stop_notify = AsyncMock()
        self.write_gatt_char = AsyncMock(side_effect=self._write)

    async def _start_notify(self, uuid, callback) -> None:
        self.notify = callback

    async def _write(self, uuid, data, response=True) -> None:
        data = bytes(data)
        self.writes.append(data)
        header, iv = data[:16], data[3:15]
        self.tx_iv_key = AESGCM(_HANDSHAKE_KEY).decrypt(iv, data[16:], header)
        if self.pre_notify is not None:
            self.notify(0, bytearray(self.pre_notify))
        reply_iv = bytes(range(12))
        reply_header = _HANDSHAKE_RESPONSE_HEADER + reply_iv
        body = AESGCM(self.handshake_key).encrypt(reply_iv, _RX_IV_KEY + self.device_info, reply_header)
        self.notify(0, bytearray(reply_header + body))

    def push(self, frame: bytes, counter: int) -> None:
        """Notify one data frame encrypted the way the device does after the handshake."""
        ctr = counter.to_bytes(4, "big")
        sealed = AESGCM(derive_device_key(self.device_info)).encrypt(_RX_IV_KEY + ctr, frame, ctr)
        self.notify(0, bytearray(ctr + sealed))


def _frame(first: int) -> bytes:
    return bytes([first] + [0] * 19)


# ==============================================================================
# Handshake
# ==============================================================================


class TestHandshake:
    @pytest.mark.asyncio
    async def test_session_is_built_from_the_device_reply(self):
        device = FakeDevice()

        session = await async_establish_session(device, WRITE_UUID, NOTIFY_UUID)

        assert session.rx_iv_key == _RX_IV_KEY
        assert session.tx_iv_key == device.tx_iv_key  # the device learnt exactly what we sent
        assert len(session.tx_iv_key) == 8
        assert session.device_key == _DEVICE_KEY  # known answer from the real H1270
        device.start_notify.assert_awaited_once()
        assert device.start_notify.await_args.args[0] == NOTIFY_UUID
        device.write_gatt_char.assert_awaited_once()
        assert device.write_gatt_char.await_args.args[0] == WRITE_UUID
        assert device.write_gatt_char.await_args.kwargs == {"response": False}
        assert device.writes[0][:3] == bytes((0xE7, 0x11, 0x01))
        device.stop_notify.assert_not_awaited()  # notifications stay on: they carry the replies

    @pytest.mark.asyncio
    async def test_device_can_open_what_the_session_wraps(self):
        device = FakeDevice()
        session = await async_establish_session(device, WRITE_UUID, NOTIFY_UUID)
        frame = _frame(0x33)

        packet = session.wrap(frame)

        counter = packet[:4]
        opened = AESGCM(derive_device_key(_DEVICE_INFO)).decrypt(device.tx_iv_key + counter, packet[4:], counter)
        assert opened == frame
        assert counter == (1).to_bytes(4, "big")

    @pytest.mark.asyncio
    async def test_handshake_ignores_a_stray_notification_before_the_reply(self):
        on_frame = MagicMock()
        device = FakeDevice(pre_notify=bytes([0xAA, 0x01, 0x02, 0x03]))

        session = await async_establish_session(device, WRITE_UUID, NOTIFY_UUID, on_frame=on_frame)

        assert session.rx_iv_key == _RX_IV_KEY
        on_frame.assert_not_called()

    @pytest.mark.asyncio
    async def test_reply_under_the_wrong_key_fails_and_releases_notify(self):
        device = FakeDevice(handshake_key=bytes(16))

        with pytest.raises(InvalidTag):
            await async_establish_session(device, WRITE_UUID, NOTIFY_UUID)

        device.stop_notify.assert_awaited_once_with(NOTIFY_UUID)

    @pytest.mark.asyncio
    async def test_write_failure_propagates_even_if_stop_notify_fails(self):
        device = FakeDevice()
        device.write_gatt_char = AsyncMock(side_effect=OSError("gatt write failed"))
        device.stop_notify = AsyncMock(side_effect=RuntimeError("busy"))

        with pytest.raises(OSError, match="gatt write failed"):
            await async_establish_session(device, WRITE_UUID, NOTIFY_UUID)

        device.stop_notify.assert_awaited_once_with(NOTIFY_UUID)

    @pytest.mark.asyncio
    async def test_non_mac_address_does_not_block_the_session(self, caplog):
        """macOS hands out CoreBluetooth UUIDs: the identity check is advisory only."""
        device = FakeDevice(address="6C5D7C1E-0000-4F5A-9B1C-2D3E4F5A6B7C")

        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            session = await async_establish_session(device, WRITE_UUID, NOTIFY_UUID)

        assert session.device_key == _DEVICE_KEY
        assert caplog.records == []


# ==============================================================================
# Post-handshake notifications
# ==============================================================================


class TestNotifications:
    @pytest.mark.asyncio
    async def test_data_frames_are_decrypted_into_on_frame(self):
        frames: list[bytes] = []
        device = FakeDevice()
        await async_establish_session(device, WRITE_UUID, NOTIFY_UUID, on_frame=frames.append)

        device.push(_frame(0xAA), counter=1)
        device.push(_frame(0xEE), counter=2)

        assert frames == [_frame(0xAA), _frame(0xEE)]

    @pytest.mark.asyncio
    async def test_undecryptable_frames_are_logged_not_raised(self, caplog):
        frames: list[bytes] = []
        device = FakeDevice()
        await async_establish_session(device, WRITE_UUID, NOTIFY_UUID, on_frame=frames.append)

        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            device.notify(0, bytearray(b"\x00\x00\x00\x07garbage"))
            device.notify(0, bytearray(b"\x01"))  # shorter than a counter
            device.notify(0, bytearray())
            # A second handshake header after the reply is done is treated as data.
            device.notify(0, bytearray(_HANDSHAKE_RESPONSE_HEADER + bytes(12)))

        assert frames == []
        assert sum("Undecryptable" in rec.getMessage() for rec in caplog.records) == 4

    @pytest.mark.asyncio
    async def test_tampered_frame_is_rejected(self, caplog):
        frames: list[bytes] = []
        device = FakeDevice()
        await async_establish_session(device, WRITE_UUID, NOTIFY_UUID, on_frame=frames.append)
        ctr = (1).to_bytes(4, "big")
        sealed = bytearray(AESGCM(_DEVICE_KEY).encrypt(_RX_IV_KEY + ctr, _frame(0xAA), ctr))
        sealed[5] ^= 0x01

        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            device.notify(0, bytearray(ctr) + sealed)

        assert frames == []
        assert any("Undecryptable" in rec.getMessage() for rec in caplog.records)

    @pytest.mark.asyncio
    async def test_frames_are_dropped_without_an_on_frame_callback(self, caplog):
        device = FakeDevice()
        await async_establish_session(device, WRITE_UUID, NOTIFY_UUID)

        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            device.push(_frame(0xAA), counter=1)
            device.notify(0, bytearray(b"garbage"))

        assert caplog.records == []  # nothing to deliver, nothing to complain about


# ==============================================================================
# Identity check
# ==============================================================================


class TestIdentityCheck:
    def test_matching_identity_is_silent(self, caplog):
        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            _log_identity(_ADDRESS, _DEVICE_INFO)
        assert caplog.records == []

    def test_mismatch_is_logged(self, caplog):
        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            _log_identity("AA:BB:CC:DD:EE:FF", _DEVICE_INFO)
        assert [rec.getMessage() for rec in caplog.records] == [
            f"Govee BLE handshake identity mismatch for AA:BB:CC:DD:EE:FF: {_DEVICE_INFO.hex()}"
        ]

    def test_short_reply_is_logged(self, caplog):
        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            _log_identity(_ADDRESS, b"H1270")
        assert [rec.getMessage() for rec in caplog.records] == ["Govee BLE handshake reply is short: 4831323730"]

    def test_non_mac_address_is_skipped(self, caplog):
        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            _log_identity("6C5D7C1E-0000-4F5A-9B1C-2D3E4F5A6B7C", _DEVICE_INFO)
            _log_identity("", _DEVICE_INFO)  # decodes to zero bytes: a mismatch, not an error
        assert [rec.getMessage() for rec in caplog.records] == [
            f"Govee BLE handshake identity mismatch for : {_DEVICE_INFO.hex()}"
        ]


# ==============================================================================
# Session primitives
# ==============================================================================


class TestSessionPrimitives:
    def test_wrap_rejects_a_bad_key_length(self):
        session = GoveeBLESession(device_key=bytes(5), tx_iv_key=bytes(8), rx_iv_key=bytes(8))
        with pytest.raises(ValueError, match="key must be"):
            session.wrap(_frame(0x33))
        assert session._tx_counter == 1  # the counter was consumed before the key was checked

    def test_unwrap_with_the_wrong_iv_key_fails(self):
        sender = GoveeBLESession(device_key=_DEVICE_KEY, tx_iv_key=bytes(8), rx_iv_key=bytes(8))
        receiver = GoveeBLESession(device_key=_DEVICE_KEY, tx_iv_key=bytes(8), rx_iv_key=bytes([1] * 8))
        with pytest.raises(InvalidTag):
            receiver.unwrap(sender.wrap(_frame(0x33)))

    def test_unwrap_of_a_truncated_packet_fails(self):
        session = GoveeBLESession(device_key=_DEVICE_KEY, tx_iv_key=bytes(8), rx_iv_key=bytes(8))
        with pytest.raises(InvalidTag):
            session.unwrap((1).to_bytes(4, "big"))  # counter only, no ciphertext or tag

    def test_device_key_pads_short_identity_and_is_deterministic(self):
        empty = derive_device_key(b"")
        assert len(empty) == 16
        assert empty == derive_device_key(bytes(16))  # zero padding is explicit, not incidental
        assert derive_device_key(b"H1270") != derive_device_key(b"H1271")


# ==============================================================================
# Protocol probe
# ==============================================================================


class TestEncryptionProbe:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("info", [bytes([0x01, 0x01, 0x00]), bytes([0x02]), b""])
    async def test_non_v2_reads_mean_plaintext(self, info):
        client = MagicMock()
        client.services.get_characteristic.return_value = object()
        client.read_gatt_char = AsyncMock(return_value=info)

        assert await async_supports_encryption(client) is False

        client.services.get_characteristic.assert_called_once_with(VERSION_CHARACTERISTIC_UUID)
        client.read_gatt_char.assert_awaited_once_with(VERSION_CHARACTERISTIC_UUID)
