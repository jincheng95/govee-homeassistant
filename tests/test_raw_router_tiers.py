"""Tier selection in :mod:`custom_components.govee.api.raw_router`.

The issue #57 LAN write-suppression cooldown stands down the LAN tier and
nothing else: BLE and MQTT carry the same frames and know nothing about it.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.govee.api import ble_raw_write, lan_raw
from custom_components.govee.api.lan_client import LanDeviceInfo
from custom_components.govee.api.protocol import Transport, get_profile
from custom_components.govee.const import (
    CONF_ENABLE_BLE_RAW_WRITE,
    CONF_ENABLE_LAN_RAW_WRITE,
    CONF_ENABLE_MQTT_CONTROL,
)
from custom_components.govee.platforms.segment import GoveeSegmentEntity

# Synthetic id whose last six octets are the BLE address.
DEVICE_ID = "AA:BB:AA:BB:CC:11:22:33"
# The H6046 light bar: BLE plaintext + MQTT ptReal, and no LAN raw at all.
BLE_SKU = "H6046"
BLE_SEGMENTS = 10
# The H6076: LAN raw + MQTT, used where the LAN tier itself must be exercised.
LAN_DEVICE_ID = "AA:BB:CC:DD:EE:FF:60:76"
LAN_SKU = "H6076"
LAN_SEGMENTS = 7
IP = "10.20.0.52"

GOLDEN_SEG0_RED = "33051501ff0000000000000001000000000000dc"


class _FakeBleClient:
    def __init__(self) -> None:
        self.writes: list[bytes] = []
        self.is_connected = True

    async def write_gatt_char(self, uuid: str, frame: bytes, response: bool = True) -> None:
        self.writes.append(bytes(frame))

    async def disconnect(self) -> None:
        self.is_connected = False


@pytest.fixture(autouse=True)
def ble_stack(monkeypatch: pytest.MonkeyPatch) -> _FakeBleClient:
    """A resolvable BLE device and a fake GATT client, with no links held."""
    ble_raw_write._LINKS.clear()
    client = _FakeBleClient()
    monkeypatch.setattr(ble_raw_write, "HAS_BLUETOOTH", True)
    monkeypatch.setattr(ble_raw_write, "_resolve", lambda coordinator, address: MagicMock(address=address))
    monkeypatch.setattr(ble_raw_write, "close_stale_connections_by_address", AsyncMock())
    monkeypatch.setattr(ble_raw_write, "establish_connection", AsyncMock(return_value=client))
    monkeypatch.setattr(ble_raw_write, "BLE_IDLE_DISCONNECT_SECONDS", 0.01)
    return client


class _FakeRawClient:
    """The UDP client, optionally raising something the LAN tier never catches."""

    def __init__(self, error: Exception | None = None) -> None:
        self.envelopes: list[tuple[str, list[bytes]]] = []
        self.error = error

    async def async_send_frames(self, host: str, frames: list[bytes]) -> None:
        if self.error is not None:
            raise self.error
        self.envelopes.append((host, list(frames)))


@pytest.fixture(autouse=True)
def raw_client(monkeypatch: pytest.MonkeyPatch) -> _FakeRawClient:
    client = _FakeRawClient()
    monkeypatch.setattr(lan_raw, "_CLIENT", client)
    monkeypatch.setattr(lan_raw, "LAN_WRITE_GAP_SECONDS", 0)
    return client


def _coordinator(*, device_id: str = DEVICE_ID, sku: str = BLE_SKU, on_lan: bool = False, **options: bool) -> Any:
    coordinator = MagicMock()
    coordinator._govee_zone_state_registry = None
    coordinator.config_entry.options = {
        CONF_ENABLE_LAN_RAW_WRITE: options.get("lan", True),
        CONF_ENABLE_BLE_RAW_WRITE: options.get("ble", True),
        CONF_ENABLE_MQTT_CONTROL: options.get("mqtt", True),
    }
    coordinator.async_control_device = AsyncMock(return_value=True)
    coordinator.is_power_off_pending = MagicMock(return_value=False)
    coordinator.get_state = MagicMock(return_value=None)
    coordinator.mqtt_connected = True
    coordinator._mqtt_client = MagicMock()
    coordinator._mqtt_client.async_publish_ptreal = AsyncMock(return_value=True)
    coordinator._ensure_device_topic = AsyncMock(return_value="GA/device-topic")
    coordinator._lan_devices = {}
    if on_lan:
        coordinator._lan_devices[device_id] = LanDeviceInfo(
            device_id=device_id,
            ip=IP,
            mac=device_id,
            sku=sku,
            firmware="1.0.0",
            last_correlated_ts=0.0,
        )
    return coordinator


def _segment_entity(coordinator: Any, *, device_id: str = DEVICE_ID, sku: str = BLE_SKU, count: int = BLE_SEGMENTS):
    device = MagicMock()
    device.device_id = device_id
    device.sku = sku
    device.segment_count = count
    device.name = "Light Bar"

    with patch.object(GoveeSegmentEntity, "__init__", lambda self, *a, **kw: None):
        entity = GoveeSegmentEntity.__new__(GoveeSegmentEntity)
    entity.coordinator = coordinator
    entity._device = device
    entity._device_id = device_id
    entity._segment_index = 0
    entity._is_on = True
    entity._brightness = 255
    entity._rgb_color = (255, 255, 255)
    entity.async_write_ha_state = MagicMock()
    return entity


class TestCooldownGatesOnlyTheLanTier:
    """Issue #57's cooldown is about upstream's LAN tier, not about raw frames."""

    def test_the_test_sku_has_no_lan_raw_pipe(self):
        profile = get_profile(BLE_SKU)

        assert profile.carries(Transport.BLE_PLAINTEXT) is True
        assert profile.carries(Transport.MQTT_PTREAL) is True
        assert profile.carries(Transport.LAN_RAW) is False

    @pytest.mark.asyncio
    async def test_ble_still_paints_during_the_cooldown(self, ble_stack):
        coordinator = _coordinator()
        coordinator._lan_writes_suppressed = MagicMock(return_value=True)
        entity = _segment_entity(coordinator)

        await entity.async_turn_on(rgb_color=(255, 0, 0))

        assert [frame.hex() for frame in ble_stack.writes] == [GOLDEN_SEG0_RED]
        coordinator.async_control_device.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_mqtt_still_publishes_during_the_cooldown(self, monkeypatch, ble_stack):
        monkeypatch.setattr(ble_raw_write, "_resolve", lambda coordinator, address: None)
        coordinator = _coordinator()
        coordinator._lan_writes_suppressed = MagicMock(return_value=True)
        entity = _segment_entity(coordinator)

        await entity.async_turn_on(rgb_color=(255, 0, 0))

        coordinator._mqtt_client.async_publish_ptreal.assert_awaited()
        coordinator.async_control_device.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_lan_tier_itself_stands_down(self, raw_client, ble_stack):
        coordinator = _coordinator(device_id=LAN_DEVICE_ID, sku=LAN_SKU, on_lan=True)
        coordinator._lan_writes_suppressed = MagicMock(return_value=True)
        entity = _segment_entity(coordinator, device_id=LAN_DEVICE_ID, sku=LAN_SKU, count=LAN_SEGMENTS)

        await entity.async_turn_on(rgb_color=(255, 0, 0))

        assert raw_client.envelopes == []
        coordinator._mqtt_client.async_publish_ptreal.assert_awaited()
