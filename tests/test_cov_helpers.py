"""Coverage tests for the coordinator's extracted helpers.

Covers the branches the dedicated helper test files leave untouched:

- ``TransportHealthTracker`` — the ``health`` view, the "configured but
  disconnected" MQTT reason, and BLE advertisement staleness.
- ``SceneCacheManager`` — cache counts, stale-entry cleanup, TTL expiry, and
  the device-less fetch guard.
- ``BleAdvertisementHandler.setup_subscriptions`` and the cache sweep's
  short-device-id guard.
- ``BlePassthroughManager`` — availability, the raw ptReal send path, and the
  music / DreamView / DIY-scene packet helpers built on it.
- ``diagnostics`` — the source-IP privacy buckets, an invalid ``lan_targets``
  option during the LAN scan, and non-dict probe replies.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from ipaddress import IPv4Address
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.govee.api.ble_packet import (
    build_diy_scene_packet,
    build_dreamview_packet,
    build_music_mode_packet,
    encode_packet_base64,
)
from custom_components.govee.ble_advertisement import _BLE_NAME_PREFIXES, BleAdvertisementHandler
from custom_components.govee.ble_passthrough import BlePassthroughManager
from custom_components.govee.const import GOVEE_BLE_MANUFACTURER_IDS
from custom_components.govee.diagnostics import _classify_ip, _lan_discovery_diag, _reply_cmds, _reply_data
from custom_components.govee.models import TRANSPORT_KINDS
from custom_components.govee.models.device import GoveeDevice
from custom_components.govee.scene_cache import SceneCacheManager
from custom_components.govee.transport_health import BLE_STALE_SECONDS, TransportHealthTracker

_BLE_MODULE = "custom_components.govee.ble_advertisement"
_DIAG_MODULE = "custom_components.govee.diagnostics"

DEVICE_ID = "AA:BB:CC:DD:EE:FF:00:11"
SKU = "H6072"
TOPIC = "GD/device-topic"


# --------------------------------------------------------------------------- #
# TransportHealthTracker
# --------------------------------------------------------------------------- #


class TestTrackerHealthView:
    def test_health_is_the_live_per_device_map(self):
        tracker = TransportHealthTracker()
        tracker.ensure("dev1")

        assert set(tracker.health) == {"dev1"}
        assert set(tracker.health["dev1"]) == set(TRANSPORT_KINDS)
        assert tracker.health["dev1"]["ble"] is tracker.get("dev1", "ble")

    def test_get_unknown_transport_on_a_known_device(self):
        tracker = TransportHealthTracker()
        tracker.ensure("dev1")
        assert tracker.get("dev1", "carrier_pigeon") is None  # type: ignore[arg-type]


class TestRefreshMqttForDevices:
    def test_configured_client_that_dropped_reports_disconnected(self):
        tracker = TransportHealthTracker()
        tracker.refresh_mqtt_for_devices(["dev1"], connected=False, client_configured=True)

        health = tracker.get("dev1", "mqtt")
        assert health is not None
        assert health.is_available is False
        assert health.last_failure_reason == "disconnected"

    def test_missing_client_reports_not_configured(self):
        tracker = TransportHealthTracker()
        tracker.refresh_mqtt_for_devices(["dev1"], connected=False, client_configured=False)
        assert tracker.get("dev1", "mqtt").last_failure_reason == "not_configured"

    def test_reconnect_clears_the_reason_without_backdating_success(self):
        tracker = TransportHealthTracker()
        tracker.refresh_mqtt_for_devices(["dev1"], connected=False, client_configured=True)
        tracker.refresh_mqtt_for_devices(["dev1"], connected=True, client_configured=True)

        health = tracker.get("dev1", "mqtt")
        assert health.is_available is True
        assert health.last_failure_reason is None
        assert health.last_success_ts is None


class TestRefreshBleStaleness:
    def test_device_without_a_ble_transport_is_unavailable(self):
        tracker = TransportHealthTracker()
        tracker.record_success("dev1", "ble")
        tracker.refresh_ble_staleness(["dev1"], set())
        assert tracker.get("dev1", "ble").is_available is False

    def test_enrolled_device_that_never_advertised_is_left_alone(self):
        tracker = TransportHealthTracker()
        tracker.ensure("dev1")
        tracker.refresh_ble_staleness(["dev1"], {"dev1"})

        health = tracker.get("dev1", "ble")
        assert health.is_available is False  # never marked, still the default
        assert health.last_failure_reason is None

    def test_fresh_advertisement_keeps_ble_available(self):
        tracker = TransportHealthTracker()
        tracker.record_success("dev1", "ble")
        tracker.refresh_ble_staleness(["dev1"], {"dev1"})

        health = tracker.get("dev1", "ble")
        assert health.is_available is True
        assert health.last_failure_reason is None

    def test_stale_advertisement_marks_ble_unavailable(self):
        tracker = TransportHealthTracker()
        tracker.ensure("dev1")
        stale = datetime.now(timezone.utc) - timedelta(seconds=BLE_STALE_SECONDS + 5)
        tracker.get("dev1", "ble").mark_success(stale)

        tracker.refresh_ble_staleness(["dev1"], {"dev1"})

        health = tracker.get("dev1", "ble")
        assert health.is_available is False
        assert health.last_failure_reason == "stale_advertisement"
        assert health.last_success_ts == stale  # the stamp itself is kept


# --------------------------------------------------------------------------- #
# SceneCacheManager
# --------------------------------------------------------------------------- #


def _light(device_id: str = DEVICE_ID) -> GoveeDevice:
    return GoveeDevice(device_id=device_id, sku=SKU, name="Lamp", device_type="devices.types.light")


class _Clock:
    """A controllable stand-in for ``time`` inside the scene cache module."""

    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    clock = _Clock()
    monkeypatch.setattr("custom_components.govee.scene_cache.time", clock)
    return clock


@pytest.fixture
def api() -> AsyncMock:
    api = AsyncMock()
    api.get_dynamic_scenes = AsyncMock(return_value=[{"name": "Sunrise", "value": {"id": 1}}])
    api.get_diy_scenes = AsyncMock(return_value=[{"name": "Rainbow", "value": {"id": 100}}])
    return api


class TestSceneCacheCounts:
    @pytest.mark.asyncio
    async def test_counts_track_cached_devices(self, api: AsyncMock):
        manager = SceneCacheManager(api)
        assert (manager.scene_cache_count, manager.diy_scene_cache_count) == (0, 0)

        await manager.async_get_scenes(DEVICE_ID, _light())
        assert (manager.scene_cache_count, manager.diy_scene_cache_count) == (1, 0)

        await manager.async_get_diy_scenes(DEVICE_ID, _light())
        await manager.async_get_diy_scenes("other", _light("other"))
        assert (manager.scene_cache_count, manager.diy_scene_cache_count) == (1, 2)


class TestCleanupStale:
    @pytest.mark.asyncio
    async def test_drops_entries_for_devices_that_disappeared(self, api: AsyncMock):
        manager = SceneCacheManager(api)
        await manager.async_get_scenes(DEVICE_ID, _light())
        await manager.async_get_scenes("gone", _light("gone"))
        await manager.async_get_diy_scenes("gone", _light("gone"))

        manager.cleanup_stale({DEVICE_ID})

        assert manager.scene_cache_count == 1
        assert manager.diy_scene_cache_count == 0
        # The surviving entry is still served from cache.
        await manager.async_get_scenes(DEVICE_ID, _light())
        assert api.get_dynamic_scenes.call_count == 2  # one per device, no refetch

    @pytest.mark.asyncio
    async def test_no_op_when_nothing_is_stale(self, api: AsyncMock):
        manager = SceneCacheManager(api)
        await manager.async_get_scenes(DEVICE_ID, _light())

        manager.cleanup_stale({DEVICE_ID, "unrelated"})

        assert manager.scene_cache_count == 1


class TestCacheExpiry:
    @pytest.mark.asyncio
    async def test_scenes_are_refetched_after_the_ttl(self, api: AsyncMock, clock: _Clock):
        manager = SceneCacheManager(api, cache_ttl=60)
        first = await manager.async_get_scenes(DEVICE_ID, _light())

        clock.now += 59
        assert await manager.async_get_scenes(DEVICE_ID, _light()) is first
        assert api.get_dynamic_scenes.call_count == 1

        clock.now += 2
        api.get_dynamic_scenes.return_value = [{"name": "Sunset", "value": {"id": 2}}]
        assert await manager.async_get_scenes(DEVICE_ID, _light()) == [{"name": "Sunset", "value": {"id": 2}}]
        assert api.get_dynamic_scenes.call_count == 2

    @pytest.mark.asyncio
    async def test_diy_scenes_are_refetched_after_the_ttl(self, api: AsyncMock, clock: _Clock):
        manager = SceneCacheManager(api, cache_ttl=60)
        await manager.async_get_diy_scenes(DEVICE_ID, _light())

        clock.now += 61
        await manager.async_get_diy_scenes(DEVICE_ID, _light())

        assert api.get_diy_scenes.call_count == 2


class TestDeviceRequiredOnMiss:
    @pytest.mark.asyncio
    async def test_unknown_device_yields_no_scenes_and_no_api_call(self, api: AsyncMock):
        manager = SceneCacheManager(api)

        assert await manager.async_get_scenes(DEVICE_ID, None) == []
        assert await manager.async_get_diy_scenes(DEVICE_ID, None) == []

        api.get_dynamic_scenes.assert_not_awaited()
        api.get_diy_scenes.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_fresh_cache_is_served_even_without_a_device(self, api: AsyncMock):
        manager = SceneCacheManager(api)
        cached = await manager.async_get_scenes(DEVICE_ID, _light())

        assert await manager.async_get_scenes(DEVICE_ID, None) is cached
        assert api.get_dynamic_scenes.call_count == 1


# --------------------------------------------------------------------------- #
# BleAdvertisementHandler.setup_subscriptions / enroll_from_cache
# --------------------------------------------------------------------------- #


class TestSetupSubscriptions:
    def test_no_op_without_the_bluetooth_component(self):
        handler = BleAdvertisementHandler(MagicMock())
        with patch(f"{_BLE_MODULE}.HAS_BLUETOOTH", False):
            assert handler.setup_subscriptions() == []

    def test_registers_name_prefix_and_manufacturer_matchers(self):
        coord = MagicMock()
        handler = BleAdvertisementHandler(coord)
        bt = MagicMock()
        unsubs = [
            MagicMock(name=f"unsub{i}") for i in range(len(_BLE_NAME_PREFIXES) + len(GOVEE_BLE_MANUFACTURER_IDS))
        ]
        bt.async_register_callback.side_effect = unsubs
        matcher = MagicMock(side_effect=lambda **kwargs: kwargs)
        scanning_mode = SimpleNamespace(ACTIVE="active")
        advert = MagicMock()

        with (
            patch(f"{_BLE_MODULE}.HAS_BLUETOOTH", True),
            patch(f"{_BLE_MODULE}.bluetooth", bt, create=True),
            patch(f"{_BLE_MODULE}.BluetoothCallbackMatcher", matcher, create=True),
            patch(f"{_BLE_MODULE}.BluetoothScanningMode", scanning_mode, create=True),
            patch.object(handler, "handle_advertisement") as handle,
        ):
            result = handler.setup_subscriptions()
            # Every registration shares one callback that forwards the advert.
            callbacks = {call.args[1] for call in bt.async_register_callback.call_args_list}
            assert len(callbacks) == 1
            callbacks.pop()(advert, "change")

        assert result == unsubs
        handle.assert_called_once_with(advert)
        calls = bt.async_register_callback.call_args_list
        assert all(call.args[0] is coord.hass and call.args[3] == "active" for call in calls)
        matchers = [call.args[2] for call in calls]
        assert matchers == [{"local_name": prefix, "connectable": True} for prefix in _BLE_NAME_PREFIXES] + [
            {"manufacturer_id": mfg_id, "connectable": True} for mfg_id in GOVEE_BLE_MANUFACTURER_IDS
        ]


class TestEnrollFromCacheGuards:
    def test_device_id_without_a_mac_is_skipped(self):
        device = MagicMock()
        device.sku = "H1270"
        device.is_group = False
        coord = MagicMock()
        coord._devices = {"12345678": device}  # group-style numeric id, no MAC to derive
        coord._ble_devices = {}
        handler = BleAdvertisementHandler(coord)
        bt = MagicMock()

        with (
            patch(f"{_BLE_MODULE}.HAS_BLUETOOTH", True),
            patch(f"{_BLE_MODULE}.bt_component", bt, create=True),
            patch(f"{_BLE_MODULE}.BLE_COMMAND_SUPPORTED_MODELS", frozenset({"H1270"}), create=True),
            patch.object(handler, "handle_advertisement") as handle,
        ):
            handler.enroll_from_cache()

        bt.async_last_service_info.assert_not_called()
        handle.assert_not_called()


# --------------------------------------------------------------------------- #
# BlePassthroughManager
# --------------------------------------------------------------------------- #


def _client(*, connected: bool = True, result: bool = True) -> MagicMock:
    client = MagicMock()
    client.connected = connected
    client.async_publish_ptreal = AsyncMock(return_value=result)
    client.async_publish_command = AsyncMock(return_value=True)
    return client


def _manager(client: MagicMock | None) -> BlePassthroughManager:
    return BlePassthroughManager(
        get_mqtt_client=lambda: client,
        device_topics={DEVICE_ID: TOPIC},
        ensure_device_topic=AsyncMock(return_value=TOPIC),
    )


class TestAvailable:
    def test_requires_a_connected_client(self):
        assert _manager(None).available is False
        assert _manager(_client(connected=False)).available is False
        assert _manager(_client(connected=True)).available is True


class TestSendBlePacket:
    @pytest.mark.asyncio
    async def test_without_a_client_nothing_is_sent(self):
        manager = _manager(None)
        assert await manager.async_send_ble_packet(DEVICE_ID, SKU, "AAAA") is False
        manager._ensure_device_topic.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_publishes_on_the_refreshed_device_topic(self):
        client = _client()
        manager = _manager(client)

        assert await manager.async_send_ble_packet(DEVICE_ID, SKU, "AAAA") is True

        manager._ensure_device_topic.assert_awaited_once_with(DEVICE_ID)
        client.async_publish_ptreal.assert_awaited_once_with(DEVICE_ID, SKU, "AAAA", TOPIC)

    @pytest.mark.asyncio
    async def test_publish_result_propagates(self):
        assert await _manager(_client(result=False)).async_send_ble_packet(DEVICE_ID, SKU, "AAAA") is False


class TestPacketHelpers:
    @pytest.mark.asyncio
    async def test_music_mode_sends_the_music_packet(self):
        client = _client()
        manager = _manager(client)

        assert await manager.async_send_music_mode(DEVICE_ID, SKU, True, sensitivity=75) is True

        expected = encode_packet_base64(build_music_mode_packet(True, 75))
        client.async_publish_ptreal.assert_awaited_once_with(DEVICE_ID, SKU, expected, TOPIC)

    @pytest.mark.asyncio
    async def test_music_mode_off_uses_the_default_sensitivity(self):
        client = _client()
        await _manager(client).async_send_music_mode(DEVICE_ID, SKU, False)
        expected = encode_packet_base64(build_music_mode_packet(False, 50))
        assert client.async_publish_ptreal.call_args.args[2] == expected

    @pytest.mark.asyncio
    async def test_dreamview_sends_the_video_packet(self):
        client = _client()

        assert await _manager(client).async_send_dreamview(DEVICE_ID, SKU) is True

        expected = encode_packet_base64(build_dreamview_packet())
        client.async_publish_ptreal.assert_awaited_once_with(DEVICE_ID, SKU, expected, TOPIC)

    @pytest.mark.asyncio
    async def test_diy_scene_sends_the_scene_id(self):
        client = _client()

        assert await _manager(client).async_send_diy_scene(DEVICE_ID, SKU, 42) is True

        expected = encode_packet_base64(build_diy_scene_packet(42))
        client.async_publish_ptreal.assert_awaited_once_with(DEVICE_ID, SKU, expected, TOPIC)

    @pytest.mark.asyncio
    async def test_helpers_report_false_without_a_client(self):
        manager = _manager(None)
        assert await manager.async_send_music_mode(DEVICE_ID, SKU, True) is False
        assert await manager.async_send_dreamview(DEVICE_ID, SKU) is False
        assert await manager.async_send_diy_scene(DEVICE_ID, SKU, 1) is False


# --------------------------------------------------------------------------- #
# diagnostics helpers
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("ip", "bucket"),
    [
        ("127.0.0.1", "loopback"),
        ("169.254.10.1", "link-local"),
        ("172.17.0.2", "private-172 (often container bridge)"),
        ("192.168.1.10", "private-192.168 (typical LAN)"),
        ("10.20.30.40", "private-10"),
        ("192.0.2.1", "private-other"),
        ("8.8.8.8", "public"),
    ],
)
def test_classify_ip_never_reveals_the_address(ip: str, bucket: str) -> None:
    assert _classify_ip(IPv4Address(ip)) == bucket
    assert ip not in bucket


class TestLanDiscoveryTargets:
    @pytest.mark.asyncio
    async def test_invalid_lan_targets_option_is_ignored(self, monkeypatch: pytest.MonkeyPatch):
        scan = AsyncMock(return_value=[])
        monkeypatch.setattr(f"{_DIAG_MODULE}.async_get_lan_interface_ips", AsyncMock(return_value=["192.168.1.5"]))
        monkeypatch.setattr(
            f"{_DIAG_MODULE}.async_get_lan_broadcast_addresses", AsyncMock(return_value=["192.168.1.255"])
        )
        monkeypatch.setattr(f"{_DIAG_MODULE}.async_scan_lan_devices", scan)
        monkeypatch.setattr(f"{_DIAG_MODULE}.async_probe_lan_raw", AsyncMock(return_value={}))

        result = await _lan_discovery_diag(MagicMock(), "not-an-ip")

        assert result["extra_target_count"] == 0
        assert result["error"] is None
        assert result["interface_count"] == 1
        assert result["interface_classes"] == ["private-192.168 (typical LAN)"]
        assert result["broadcast_target_count"] == 1
        scan.assert_awaited_once_with(
            interface_ips=["192.168.1.5"], extra_targets=[], broadcast_targets=["192.168.1.255"]
        )

    @pytest.mark.asyncio
    async def test_valid_lan_targets_are_counted_not_listed(self, monkeypatch: pytest.MonkeyPatch):
        scan = AsyncMock(return_value=[])
        monkeypatch.setattr(f"{_DIAG_MODULE}.async_get_lan_interface_ips", AsyncMock(return_value=[]))
        monkeypatch.setattr(f"{_DIAG_MODULE}.async_get_lan_broadcast_addresses", AsyncMock(return_value=[]))
        monkeypatch.setattr(f"{_DIAG_MODULE}.async_scan_lan_devices", scan)

        result = await _lan_discovery_diag(MagicMock(), "10.0.0.5, 10.0.0.6")

        assert result["extra_target_count"] == 2
        assert scan.await_args.kwargs["extra_targets"] == ["10.0.0.5", "10.0.0.6"]
        assert "10.0.0.5" not in str({k: v for k, v in result.items()})


class TestProbeReplies:
    def test_non_dict_replies_are_ignored(self):
        replies = [
            "garbage",
            42,
            {"msg": "not-a-dict"},
            {"msg": {"cmd": "devStatus", "data": {"onOff": 1}}},
            {"msg": {"cmd": "status", "data": "pt-hex"}},
            {"msg": {"cmd": "devStatus", "data": {"onOff": 0}}},
        ]
        assert _reply_cmds(replies) == {"devStatus", "status"}
        # The last matching reply wins; a non-dict data payload never matches.
        assert _reply_data(replies, "devStatus") == {"onOff": 0}
        assert _reply_data(replies, "status") is None
        assert _reply_data(replies, "scan") is None
