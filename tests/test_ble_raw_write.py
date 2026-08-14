"""Tests for the plaintext BLE raw transport (fork feature, roadmap 2.1).

The H6046 light bar ignores raw frames over LAN entirely and never answers the
encrypted handshake, so its ten segments are reachable only over an
unencrypted GATT link that takes the same 20-byte frames (reference §6). What
has to hold:

* **The bytes.** Byte-identical to what the other tiers carry — the golden
  ``33 05 15 01 …`` segment frame from reference §3.2, plus the level frame.
* **The lifecycle.** Connect on demand, reuse the link for a burst, disconnect
  after the idle window. BLE is one-central: holding it locks the vendor app
  out, so the hold must end.
* **Falling through.** No adapter, no advertisement, a failed write — every one
  of them is "not handled", never an exception and never a block.
* **Gating.** The new ``enable_ble_raw_write`` option (default off) and the
  profile's ``BLE_PLAINTEXT`` transport.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.govee.api import ble_raw_write, lan_raw_write
from custom_components.govee.api.protocol import Transport, get_profile
from custom_components.govee.const import (
    CONF_ENABLE_BLE_RAW_WRITE,
    CONF_ENABLE_LAN_RAW_WRITE,
    CONF_ENABLE_MQTT_CONTROL,
)
from custom_components.govee.models import SegmentColorCommand
from custom_components.govee.platforms.segment import GoveeSegmentEntity

# The light bar: 8-octet Govee id whose first six octets are the BLE address.
DEVICE_ID = "AA:BB:CC:DD:EE:FF:60:46"
BLE_MAC = "AA:BB:CC:DD:EE:FF"
SKU = "H6046"
SEGMENTS = 10

# reference §3.2 "segment 1 red", and the level frame that rides with it.
GOLDEN_SEG0_RED = "33051501ff0000000000000001000000000000dc"
GOLDEN_SEG0_LEVEL_32 = "3305150220010000000000000000000000000000"

HA_BRIGHTNESS_32_PERCENT = 82


class _FakeClient:
    """A connected BleakClient stand-in that records its GATT writes."""

    def __init__(self) -> None:
        self.writes: list[tuple[str, bytes]] = []
        self.is_connected = True
        self.disconnects = 0
        self.error: Exception | None = None

    async def write_gatt_char(self, uuid: str, frame: bytes, response: bool = True) -> None:
        if self.error is not None:
            raise self.error
        self.writes.append((uuid, bytes(frame)))

    async def disconnect(self) -> None:
        self.disconnects += 1
        self.is_connected = False

    @property
    def hexes(self) -> list[str]:
        return [frame.hex() for _uuid, frame in self.writes]


@pytest.fixture(autouse=True)
def ble_stack(monkeypatch: pytest.MonkeyPatch) -> _FakeClient:
    """A resolvable BLE device, a fake client, and no held links from before."""
    asyncio.get_event_loop_policy()  # no-op; keeps the fixture ordering explicit
    ble_raw_write._LINKS.clear()

    client = _FakeClient()
    monkeypatch.setattr(ble_raw_write, "HAS_BLUETOOTH", True)
    monkeypatch.setattr(ble_raw_write, "_resolve", lambda coordinator, address: MagicMock(address=address))
    monkeypatch.setattr(ble_raw_write, "close_stale_connections_by_address", AsyncMock())
    monkeypatch.setattr(ble_raw_write, "establish_connection", AsyncMock(return_value=client))
    monkeypatch.setattr(ble_raw_write, "BLE_IDLE_DISCONNECT_SECONDS", 0.01)
    return client


class _FakeRawClient:
    def __init__(self) -> None:
        self.envelopes: list[tuple[str, list[bytes]]] = []

    async def async_send_frames(self, host: str, frames: list[bytes]) -> None:
        self.envelopes.append((host, list(frames)))


@pytest.fixture(autouse=True)
def raw_client(monkeypatch: pytest.MonkeyPatch) -> _FakeRawClient:
    client = _FakeRawClient()
    monkeypatch.setattr(lan_raw_write, "_CLIENT", client)
    monkeypatch.setattr(lan_raw_write, "LAN_WRITE_GAP_SECONDS", 0)
    return client


def _coordinator(*, ble: bool = True, lan_raw: bool = False, mqtt: bool = False) -> Any:
    coordinator = MagicMock()
    coordinator._govee_zone_state_registry = None
    coordinator.config_entry.options = {
        CONF_ENABLE_BLE_RAW_WRITE: ble,
        CONF_ENABLE_LAN_RAW_WRITE: lan_raw,
        CONF_ENABLE_MQTT_CONTROL: mqtt,
    }
    coordinator.async_control_device = AsyncMock(return_value=True)
    coordinator.is_power_off_pending = MagicMock(return_value=False)
    coordinator.get_state = MagicMock(return_value=None)
    coordinator.mqtt_connected = False
    coordinator._lan_devices = {}
    coordinator._mqtt_client = None
    coordinator._ensure_device_topic = AsyncMock(return_value=None)
    return coordinator


def _segment_entity(coordinator: Any, *, sku: str = SKU, segment_count: int = SEGMENTS) -> Any:
    device = MagicMock()
    device.device_id = DEVICE_ID
    device.sku = sku
    device.segment_count = segment_count
    device.name = "Light Bar"

    with patch.object(GoveeSegmentEntity, "__init__", lambda self, *a, **kw: None):
        entity = GoveeSegmentEntity.__new__(GoveeSegmentEntity)
    entity.coordinator = coordinator
    entity._device = device
    entity._device_id = DEVICE_ID
    entity._segment_index = 0
    entity._is_on = True
    entity._brightness = 255
    entity._rgb_color = (255, 255, 255)
    entity.async_write_ha_state = MagicMock()
    return entity


async def _send(coordinator: Any, frames: list[bytes]) -> bool:
    return await ble_raw_write.async_send_frames(coordinator, DEVICE_ID, SKU, get_profile(SKU), frames)


# ==============================================================================
# 1. The bytes
# ==============================================================================


class TestFrames:
    """Byte-identical to every other tier — only the pipe differs."""

    @pytest.mark.asyncio
    async def test_a_segment_paint_reaches_the_write_characteristic(self, ble_stack):
        coordinator = _coordinator()
        entity = _segment_entity(coordinator)

        await entity.async_turn_on(rgb_color=(255, 0, 0))

        assert ble_stack.hexes == [GOLDEN_SEG0_RED]
        assert ble_stack.writes[0][0] == ble_raw_write.WRITE_CHARACTERISTIC_UUID
        coordinator.async_control_device.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_brightness_rides_along_as_a_second_frame(self, ble_stack):
        """Attribute 0x02, mask straight after the level — reference §3.2."""
        coordinator = _coordinator()
        entity = _segment_entity(coordinator)

        await entity.async_turn_on(rgb_color=(255, 0, 0), brightness=HA_BRIGHTNESS_32_PERCENT)

        assert ble_stack.hexes == [GOLDEN_SEG0_RED, GOLDEN_SEG0_LEVEL_32]

    @pytest.mark.asyncio
    async def test_a_colour_only_paint_sends_no_level_frame(self, ble_stack):
        coordinator = _coordinator()
        entity = _segment_entity(coordinator)

        await entity.async_turn_on(rgb_color=(255, 0, 0))

        assert len(ble_stack.hexes) == 1

    @pytest.mark.asyncio
    async def test_the_frames_are_written_with_response(self, ble_stack):
        """Plain write-with-response is what the channel accepts (reference §6)."""
        coordinator = _coordinator()

        await _send(coordinator, [bytes.fromhex(GOLDEN_SEG0_RED)])

        assert ble_stack.writes


# ==============================================================================
# 2. Connection lifecycle
# ==============================================================================


class TestLifecycle:
    """Connect on demand, hold briefly, disconnect — BLE is one-central."""

    @pytest.mark.asyncio
    async def test_one_connection_serves_a_burst(self, ble_stack):
        coordinator = _coordinator()

        await _send(coordinator, [bytes.fromhex(GOLDEN_SEG0_RED)])
        await _send(coordinator, [bytes.fromhex(GOLDEN_SEG0_LEVEL_32)])

        assert ble_raw_write.establish_connection.await_count == 1
        assert len(ble_stack.hexes) == 2

    @pytest.mark.asyncio
    async def test_the_link_drops_after_the_idle_window(self, ble_stack):
        coordinator = _coordinator()

        await _send(coordinator, [bytes.fromhex(GOLDEN_SEG0_RED)])
        assert ble_stack.disconnects == 0

        await asyncio.sleep(ble_raw_write.BLE_IDLE_DISCONNECT_SECONDS * 4)

        assert ble_stack.disconnects == 1

    @pytest.mark.asyncio
    async def test_a_write_after_the_idle_window_reconnects(self, ble_stack):
        coordinator = _coordinator()

        await _send(coordinator, [bytes.fromhex(GOLDEN_SEG0_RED)])
        await asyncio.sleep(ble_raw_write.BLE_IDLE_DISCONNECT_SECONDS * 4)
        await _send(coordinator, [bytes.fromhex(GOLDEN_SEG0_RED)])

        assert ble_raw_write.establish_connection.await_count == 2

    @pytest.mark.asyncio
    async def test_stale_handles_are_cleared_before_connecting(self, ble_stack):
        """The one defensive call every HA BLE integration makes."""
        coordinator = _coordinator()

        await _send(coordinator, [bytes.fromhex(GOLDEN_SEG0_RED)])

        ble_raw_write.close_stale_connections_by_address.assert_awaited_with(BLE_MAC)

    @pytest.mark.asyncio
    async def test_disconnect_all_releases_the_radio(self, ble_stack):
        coordinator = _coordinator()
        await _send(coordinator, [bytes.fromhex(GOLDEN_SEG0_RED)])

        await ble_raw_write.async_disconnect_all()

        assert ble_stack.disconnects == 1
        assert ble_raw_write._LINKS == {}


# ==============================================================================
# 3. Falling through
# ==============================================================================


class TestFallThrough:
    """Every failure is "not handled", never an exception and never a block."""

    @pytest.mark.asyncio
    async def test_no_ble_device_found_is_not_handled(self, monkeypatch, ble_stack):
        monkeypatch.setattr(ble_raw_write, "_resolve", lambda coordinator, address: None)
        coordinator = _coordinator()
        entity = _segment_entity(coordinator)

        await entity.async_turn_on(rgb_color=(255, 0, 0))

        assert ble_stack.writes == []
        command = coordinator.async_control_device.await_args_list[-1].args[1]
        assert isinstance(command, SegmentColorCommand)

    @pytest.mark.asyncio
    async def test_no_bluetooth_in_this_install_is_not_handled(self, monkeypatch, ble_stack):
        monkeypatch.setattr(ble_raw_write, "HAS_BLUETOOTH", False)
        coordinator = _coordinator()

        assert await _send(coordinator, [bytes.fromhex(GOLDEN_SEG0_RED)]) is False

    @pytest.mark.asyncio
    async def test_a_failed_connect_falls_through(self, monkeypatch, ble_stack):
        monkeypatch.setattr(
            ble_raw_write,
            "establish_connection",
            AsyncMock(side_effect=ble_raw_write.BleakError("no route")),
        )
        coordinator = _coordinator()
        entity = _segment_entity(coordinator)

        await entity.async_turn_on(rgb_color=(255, 0, 0))

        coordinator.async_control_device.assert_awaited()

    @pytest.mark.asyncio
    async def test_a_failed_write_drops_the_link_and_falls_through(self, ble_stack):
        ble_stack.error = OSError("gatt gone")
        coordinator = _coordinator()

        assert await _send(coordinator, [bytes.fromhex(GOLDEN_SEG0_RED)]) is False
        assert ble_stack.disconnects == 1

    @pytest.mark.asyncio
    async def test_an_unexpected_error_never_reaches_the_entity(self, ble_stack):
        ble_stack.error = RuntimeError("bleak internals")
        coordinator = _coordinator()
        entity = _segment_entity(coordinator)

        await entity.async_turn_on(rgb_color=(255, 0, 0))

        coordinator.async_control_device.assert_awaited()

    @pytest.mark.asyncio
    async def test_a_failed_disconnect_is_swallowed(self, ble_stack):
        coordinator = _coordinator()
        await _send(coordinator, [bytes.fromhex(GOLDEN_SEG0_RED)])
        ble_stack.disconnect = AsyncMock(side_effect=RuntimeError("already gone"))

        await ble_raw_write.async_disconnect_all()

    @pytest.mark.asyncio
    async def test_no_frames_never_connects(self, ble_stack):
        assert await _send(_coordinator(), []) is False
        assert ble_raw_write.establish_connection.await_count == 0


# ==============================================================================
# 4. Gates and routing order
# ==============================================================================


class TestGates:
    """The option, the table, and the address derivation."""

    def test_the_option_defaults_off(self):
        coordinator = _coordinator()
        coordinator.config_entry.options = {}

        assert ble_raw_write.ble_raw_enabled(coordinator) is False

    @pytest.mark.asyncio
    async def test_the_option_off_uses_the_cloud(self, ble_stack):
        coordinator = _coordinator(ble=False)
        entity = _segment_entity(coordinator)

        await entity.async_turn_on(rgb_color=(255, 0, 0))

        assert ble_stack.writes == []
        command = coordinator.async_control_device.await_args_list[-1].args[1]
        assert isinstance(command, SegmentColorCommand)

    def test_only_the_plaintext_stack_may_use_the_pipe(self):
        """The H6076 answers nothing unencrypted — resolved 2026-08-13."""
        coordinator = _coordinator()

        assert ble_raw_write.ble_raw_target(coordinator, DEVICE_ID, get_profile(SKU)) == BLE_MAC
        assert ble_raw_write.ble_raw_target(coordinator, DEVICE_ID, get_profile("H6076")) is None
        assert get_profile("H6076").carries(Transport.BLE_PLAINTEXT) is False

    def test_the_ble_address_is_the_first_six_octets(self):
        assert ble_raw_write.ble_address(DEVICE_ID) == BLE_MAC
        assert ble_raw_write.ble_address("11825917") is None
        assert ble_raw_write.ble_address("") is None

    @pytest.mark.asyncio
    async def test_lan_still_wins_where_a_device_has_both(self, ble_stack, raw_client):
        """Routing order: a LAN target is used before the BLE link is opened."""
        from custom_components.govee.api.lan_client import LanDeviceInfo

        coordinator = _coordinator(lan_raw=True)
        coordinator._lan_devices["AA:BB:CC:DD:EE:FF:60:76"] = LanDeviceInfo(
            device_id="AA:BB:CC:DD:EE:FF:60:76",
            ip="10.20.0.52",
            mac="AA:BB:CC:DD:EE:FF:60:76",
            sku="H6076",
            firmware="1.0.0",
            last_correlated_ts=0.0,
        )
        entity = _segment_entity(coordinator, sku="H6076", segment_count=7)
        entity._device.device_id = "AA:BB:CC:DD:EE:FF:60:76"
        entity._device_id = "AA:BB:CC:DD:EE:FF:60:76"

        await entity.async_turn_on(rgb_color=(255, 0, 0))

        assert raw_client.envelopes
        assert ble_stack.writes == []

    @pytest.mark.asyncio
    async def test_ble_is_tried_before_mqtt(self, ble_stack):
        """The local pipe beats the cloud passthrough for the same frames."""
        coordinator = _coordinator(lan_raw=True, mqtt=True)
        coordinator.mqtt_connected = True
        coordinator._mqtt_client = MagicMock()
        coordinator._mqtt_client.async_publish_ptreal = AsyncMock(return_value=True)
        coordinator._ensure_device_topic = AsyncMock(return_value="GA/topic")
        entity = _segment_entity(coordinator)

        await entity.async_turn_on(rgb_color=(255, 0, 0))

        assert ble_stack.hexes == [GOLDEN_SEG0_RED]
        coordinator._mqtt_client.async_publish_ptreal.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_mqtt_takes_over_when_ble_cannot(self, monkeypatch, ble_stack):
        monkeypatch.setattr(ble_raw_write, "_resolve", lambda coordinator, address: None)
        coordinator = _coordinator(lan_raw=True, mqtt=True)
        coordinator.mqtt_connected = True
        coordinator._mqtt_client = MagicMock()
        coordinator._mqtt_client.async_publish_ptreal = AsyncMock(return_value=True)
        coordinator._ensure_device_topic = AsyncMock(return_value="GA/topic")
        entity = _segment_entity(coordinator)

        await entity.async_turn_on(rgb_color=(255, 0, 0))

        coordinator._mqtt_client.async_publish_ptreal.assert_awaited()
        coordinator.async_control_device.assert_not_awaited()
