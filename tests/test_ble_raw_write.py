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
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.govee.api import ble_raw_write
from custom_components.govee.api.protocol import Transport, get_profile
from custom_components.govee.const import (
    CONF_ENABLE_BLE_RAW_WRITE,
    CONF_ENABLE_LAN_RAW_WRITE,
    CONF_ENABLE_MQTT_CONTROL,
)
from custom_components.govee.models import SegmentColorCommand
from custom_components.govee.platforms.segment import GoveeSegmentEntity

# Synthetic. The structural property under test: an 8-octet Govee id whose
# **last** six octets are the BLE address.
DEVICE_ID = "AA:BB:AA:BB:CC:11:22:33"
BLE_MAC = "AA:BB:CC:11:22:33"
SKU = "H6046"
SEGMENTS = 10
ENTRY_ID = "entry_one"

# reference §3.2 "segment 1 red", and the level frame that rides with it.
GOLDEN_SEG0_RED = "33051501ff0000000000000001000000000000dc"
GOLDEN_SEG0_LEVEL_32 = "3305150220010000000000000000000000000000"

HA_BRIGHTNESS_32_PERCENT = 82


class _FakeClient:
    """A connected BleakClient stand-in that records its GATT writes."""

    def __init__(self) -> None:
        self.writes: list[tuple[str, bytes]] = []
        self.responses: list[bool] = []
        self.is_connected = True
        self.disconnects = 0
        self.error: Exception | None = None

    async def write_gatt_char(self, uuid: str, frame: bytes, response: bool = True) -> None:
        if self.error is not None:
            raise self.error
        self.writes.append((uuid, bytes(frame)))
        self.responses.append(response)

    async def disconnect(self) -> None:
        self.disconnects += 1
        self.is_connected = False

    @property
    def hexes(self) -> list[str]:
        return [frame.hex() for _uuid, frame in self.writes]


@pytest.fixture(autouse=True)
def ble_stack(monkeypatch: pytest.MonkeyPatch) -> _FakeClient:
    """A resolvable BLE device, a fake client, and no held links from before."""
    ble_raw_write._LINKS.clear()

    client = _FakeClient()
    monkeypatch.setattr(ble_raw_write, "HAS_BLUETOOTH", True)
    monkeypatch.setattr(ble_raw_write, "_resolve", lambda coordinator, address: MagicMock(address=address))
    monkeypatch.setattr(ble_raw_write, "close_stale_connections_by_address", AsyncMock())
    monkeypatch.setattr(ble_raw_write, "establish_connection", AsyncMock(return_value=client))
    return client


async def _expire_idle_window(hass: Any) -> None:
    """Fire the idle-disconnect timer on the loop's clock, not the wall's."""
    async_fire_time_changed(
        hass,
        dt_util.utcnow() + timedelta(seconds=ble_raw_write.BLE_IDLE_DISCONNECT_SECONDS + 1),
    )
    await hass.async_block_till_done()


def _coordinator(hass: Any = None, *, ble: bool = True, lan_raw: bool = False, mqtt: bool = False) -> Any:
    coordinator = MagicMock()
    coordinator._govee_zone_state_registry = None
    if hass is not None:
        coordinator.hass = hass
    coordinator.config_entry.entry_id = ENTRY_ID
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

        await _send(coordinator, [bytes.fromhex(GOLDEN_SEG0_RED), bytes.fromhex(GOLDEN_SEG0_LEVEL_32)])

        assert ble_stack.responses == [True, True]


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
    async def test_the_link_drops_after_the_idle_window(self, hass, ble_stack):
        coordinator = _coordinator(hass)

        await _send(coordinator, [bytes.fromhex(GOLDEN_SEG0_RED)])
        assert ble_stack.disconnects == 0

        await _expire_idle_window(hass)

        assert ble_stack.disconnects == 1

    @pytest.mark.asyncio
    async def test_a_write_after_the_idle_window_reconnects(self, hass, ble_stack):
        coordinator = _coordinator(hass)

        await _send(coordinator, [bytes.fromhex(GOLDEN_SEG0_RED)])
        await _expire_idle_window(hass)
        await _send(coordinator, [bytes.fromhex(GOLDEN_SEG0_RED)])

        assert ble_raw_write.establish_connection.await_count == 2

    @pytest.mark.asyncio
    async def test_a_new_write_disarms_the_pending_idle_disconnect(self, hass, ble_stack):
        """A burst spread over more than one call must not lose its link mid-way."""
        coordinator = _coordinator(hass)

        await _send(coordinator, [bytes.fromhex(GOLDEN_SEG0_RED)])
        link = ble_raw_write._LINKS[(ENTRY_ID, BLE_MAC)]

        armed = MagicMock()
        link._idle = armed
        await _send(coordinator, [bytes.fromhex(GOLDEN_SEG0_LEVEL_32)])

        # Disarmed on entry, then re-armed on the way out.
        armed.assert_called_once_with()
        assert link._idle is not armed
        assert ble_stack.disconnects == 0

    @pytest.mark.asyncio
    async def test_an_idle_disconnect_cannot_land_inside_a_write(self, hass, ble_stack):
        """The drop takes the write lock, so it waits for the frames in flight."""
        coordinator = _coordinator(hass)
        await _send(coordinator, [bytes.fromhex(GOLDEN_SEG0_RED)])
        link = ble_raw_write._LINKS[(ENTRY_ID, BLE_MAC)]

        gate = asyncio.Event()
        entered = asyncio.Event()

        async def _blocking_write(uuid: str, frame: bytes, response: bool = True) -> None:
            entered.set()
            await gate.wait()
            ble_stack.writes.append((uuid, bytes(frame)))

        ble_stack.write_gatt_char = _blocking_write
        writing = asyncio.create_task(link.async_write([bytes.fromhex(GOLDEN_SEG0_RED)]))
        await entered.wait()

        dropping = asyncio.create_task(link.async_disconnect())
        for _ in range(10):
            await asyncio.sleep(0)
        # Queued behind the lock, not applied to a client mid-write.
        assert ble_stack.disconnects == 0
        assert link._client is not None

        gate.set()
        assert await writing is True
        await dropping
        assert ble_stack.disconnects == 1

    @pytest.mark.asyncio
    async def test_unloading_an_entry_drops_only_its_own_links(self, hass, ble_stack):
        other = _coordinator(hass)
        other.config_entry.entry_id = "entry_two"

        await _send(_coordinator(hass), [bytes.fromhex(GOLDEN_SEG0_RED)])
        await _send(other, [bytes.fromhex(GOLDEN_SEG0_RED)])
        assert set(ble_raw_write._LINKS) == {(ENTRY_ID, BLE_MAC), ("entry_two", BLE_MAC)}

        await ble_raw_write.async_disconnect_all(ENTRY_ID)

        assert set(ble_raw_write._LINKS) == {("entry_two", BLE_MAC)}

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

    def test_the_ble_address_is_the_last_six_octets(self):
        assert ble_raw_write.ble_address(DEVICE_ID) == BLE_MAC
        # The leading two octets are a device-class prefix, never the MAC.
        assert not ble_raw_write.ble_address(DEVICE_ID).startswith("91:C4")
        assert ble_raw_write.ble_address("11825917") is None
        assert ble_raw_write.ble_address("") is None

    @pytest.mark.parametrize(
        "device_id",
        [
            "AA:BB:CC:DD:EE:FF",  # six octets — no device-class prefix
            "AA:BB:CC:DD:EE:FF:00",  # seven
            "AA:BB:CC:DD:EE:FF:00:11:22",  # nine
            "AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99",  # extended id
            "AA:BB:CC:DD:EE:FF:00:GG",  # non-hex octet
            "AA:BB:CC:DD:EE:FF:00:1z",  # non-hex digit
            "ZZ:BB:CC:DD:EE:FF:00:11",  # non-hex in the class prefix
            "AA:BB:CC:DD:EE:FF:0:11",  # one-digit octet
        ],
    )
    def test_an_id_that_is_not_eight_hex_octets_is_not_handled(self, device_id):
        """Truncating an unfamiliar id shape would target an unrelated radio."""
        assert ble_raw_write.ble_address(device_id) is None

    @pytest.mark.parametrize(
        ("device_id", "expected"),
        [
            ("AA:BB:AA:BB:CC:11:22:33", "AA:BB:CC:11:22:33"),  # H6046 light bar
            ("AA:BB:DD:EE:FF:44:55:66", "DD:EE:FF:44:55:66"),  # H60B0 uplighter
            ("AA:BB:77:88:99:AA:BB:CC", "77:88:99:AA:BB:CC"),  # H6076 lamp
        ],
    )
    def test_the_ble_address_matches_the_advertised_mac(self, device_id, expected):
        """Pinned to HA's live advertisement tracker, read 2026-08-14.

        All three lamps advertise the trailing six octets of their Govee id and
        none advertise the leading six — the derivation this module used until
        that day, which made every BLE attempt fall through.
        """
        assert ble_raw_write.ble_address(device_id) == expected

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
