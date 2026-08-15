"""Tests for raw frames over the cloud MQTT ``ptReal`` passthrough (roadmap 2.6).

The 20-byte frames are transport-neutral (reference §1.3), so this tier adds no
new bytes — only a new pipe and the decision of when to use it. What has to
hold:

* **Routing order.** LAN raw first; MQTT only when there is no LAN target;
  the cloud *command* only when neither raw tier can.
* **The envelope.** Byte-for-byte the shape the reference documents, including
  the base64 of a verified frame.
* **Gating.** Two existing options (``enable_mqtt_control`` +
  ``enable_lan_raw_write``), the profile's ``transports`` list, and a connected
  client — no new checkbox.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.govee.api import lan_raw, mqtt_raw_write
from custom_components.govee.api.lan_client import LanDeviceInfo
from custom_components.govee.api.protocol import Transport, get_profile
from custom_components.govee.const import (
    CONF_ENABLE_LAN_RAW_WRITE,
    CONF_ENABLE_MQTT_CONTROL,
)
from custom_components.govee.models import SegmentColorCommand
from custom_components.govee.platforms.segment import GoveeSegmentEntity

DEVICE_ID = "AA:BB:CC:DD:EE:FF:60:76"
IP = "10.20.0.52"
TOPIC = "GA/1234/topic"

LAN_SKU = "H6076"
LAN_SEGMENTS = 7
BLE_ONLY_SKU = "H6046"
BLE_ONLY_SEGMENTS = 10

# The same golden frame the LAN path sends for "segment 1 red" on the 0x15 body.
GOLDEN_SEG0_RED = "33051501ff0000000000000001000000000000dc"
GOLDEN_SEG0_RED_B64 = "MwUVAf8AAAAAAAAAAQAAAAAAANw="

# reference §1.2's worked example: the H60B0 "zone1 ripple power ON" frame in a
# ptReal envelope, base64-encoded.
REFERENCE_FRAME = "33300101000000000000000000000000000000 03".replace(" ", "")
REFERENCE_B64 = "MzABAQAAAAAAAAAAAAAAAAAAAAM="


class _FakeRawClient:
    def __init__(self) -> None:
        self.envelopes: list[tuple[str, list[bytes]]] = []

    async def async_send_frames(self, host: str, frames: list[bytes]) -> None:
        self.envelopes.append((host, list(frames)))

    @property
    def hexes(self) -> list[str]:
        if not self.envelopes:
            return []
        return [frame.hex() for frame in self.envelopes[0][1]]


@pytest.fixture(autouse=True)
def raw_client(monkeypatch: pytest.MonkeyPatch) -> _FakeRawClient:
    client = _FakeRawClient()
    monkeypatch.setattr(lan_raw, "_CLIENT", client)
    monkeypatch.setattr(lan_raw, "LAN_WRITE_GAP_SECONDS", 0)
    return client


def _coordinator(
    *,
    lan_raw: bool = True,
    mqtt_control: bool = True,
    on_lan: bool = False,
    mqtt_connected: bool = True,
    topic: str | None = TOPIC,
) -> Any:
    coordinator = MagicMock()
    coordinator._govee_zone_state_registry = None
    coordinator.config_entry.options = {
        CONF_ENABLE_LAN_RAW_WRITE: lan_raw,
        CONF_ENABLE_MQTT_CONTROL: mqtt_control,
    }
    coordinator.async_control_device = AsyncMock(return_value=True)
    coordinator.is_power_off_pending = MagicMock(return_value=False)
    coordinator.get_state = MagicMock(return_value=None)
    coordinator.mqtt_connected = mqtt_connected
    coordinator._lan_devices = {}
    if on_lan:
        coordinator._lan_devices[DEVICE_ID] = LanDeviceInfo(
            device_id=DEVICE_ID,
            ip=IP,
            mac=DEVICE_ID,
            sku=LAN_SKU,
            firmware="1.0.0",
            last_correlated_ts=0.0,
        )
    coordinator._mqtt_client = MagicMock()
    coordinator._mqtt_client.async_publish_ptreal = AsyncMock(return_value=True)
    coordinator._ensure_device_topic = AsyncMock(return_value=topic)
    return coordinator


def _segment_entity(coordinator: Any, *, sku: str = LAN_SKU, segment_count: int = LAN_SEGMENTS) -> Any:
    device = MagicMock()
    device.device_id = DEVICE_ID
    device.sku = sku
    device.segment_count = segment_count
    device.name = "Floor Lamp"

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


def _published(coordinator: Any) -> Any:
    return coordinator._mqtt_client.async_publish_ptreal.await_args


# ==============================================================================
# 1. Routing order
# ==============================================================================


class TestRoutingOrder:
    """LAN raw -> MQTT ptReal -> the cloud command."""

    @pytest.mark.asyncio
    async def test_lan_is_preferred_when_the_device_is_on_the_lan(self, raw_client):
        coordinator = _coordinator(on_lan=True)
        entity = _segment_entity(coordinator)

        await entity.async_turn_on(rgb_color=(255, 0, 0))

        assert raw_client.hexes == [GOLDEN_SEG0_RED]
        coordinator._mqtt_client.async_publish_ptreal.assert_not_awaited()
        coordinator.async_control_device.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_mqtt_carries_the_frame_when_there_is_no_lan_target(self, raw_client):
        coordinator = _coordinator(on_lan=False)
        entity = _segment_entity(coordinator)

        await entity.async_turn_on(rgb_color=(255, 0, 0))

        assert raw_client.envelopes == []
        assert _published(coordinator).args[2] == [GOLDEN_SEG0_RED_B64]
        coordinator.async_control_device.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_cloud_command_runs_when_mqtt_is_down(self, raw_client):
        coordinator = _coordinator(on_lan=False, mqtt_connected=False)
        entity = _segment_entity(coordinator)

        await entity.async_turn_on(rgb_color=(255, 0, 0))

        assert raw_client.envelopes == []
        coordinator._mqtt_client.async_publish_ptreal.assert_not_awaited()
        command = coordinator.async_control_device.await_args_list[-1].args[1]
        assert isinstance(command, SegmentColorCommand)

    @pytest.mark.asyncio
    async def test_a_failed_publish_falls_through_to_the_cloud(self, raw_client):
        coordinator = _coordinator(on_lan=False)
        coordinator._mqtt_client.async_publish_ptreal = AsyncMock(return_value=False)
        entity = _segment_entity(coordinator)

        await entity.async_turn_on(rgb_color=(255, 0, 0))

        command = coordinator.async_control_device.await_args_list[-1].args[1]
        assert isinstance(command, SegmentColorCommand)

    @pytest.mark.asyncio
    async def test_a_raising_publish_never_reaches_the_entity(self, raw_client):
        coordinator = _coordinator(on_lan=False)
        coordinator._mqtt_client.async_publish_ptreal = AsyncMock(side_effect=RuntimeError("broker gone"))
        entity = _segment_entity(coordinator)

        await entity.async_turn_on(rgb_color=(255, 0, 0))

        coordinator.async_control_device.assert_awaited()

    @pytest.mark.asyncio
    async def test_a_missing_device_topic_falls_through(self, raw_client):
        coordinator = _coordinator(on_lan=False, topic=None)
        entity = _segment_entity(coordinator)

        await entity.async_turn_on(rgb_color=(255, 0, 0))

        coordinator._mqtt_client.async_publish_ptreal.assert_not_awaited()
        coordinator.async_control_device.assert_awaited()

    @pytest.mark.parametrize(
        "topic",
        [
            "GA/#",  # every device on the account, in one publish
            "GA/+",
            "GA/topic/#",
            "GB/1234/topic",  # not a Govee command-topic root
            "1234/topic",
            "GA/",  # root with no device part
            "GA/topic with spaces",
            "GA/topic\n",
            "GA/" + "x" * 129,  # past the length bound
        ],
    )
    @pytest.mark.asyncio
    async def test_a_malformed_topic_publishes_nothing(self, raw_client, topic):
        """The topic is coordinator-supplied and goes straight onto the wire."""
        coordinator = _coordinator(on_lan=False, topic=topic)
        entity = _segment_entity(coordinator)

        await entity.async_turn_on(rgb_color=(255, 0, 0))

        coordinator._mqtt_client.async_publish_ptreal.assert_not_awaited()
        coordinator.async_control_device.assert_awaited()

    @pytest.mark.parametrize(
        "topic",
        ["GA/1234/topic", "GD/1234/topic", "GA/AA:BB:CC:DD:EE:FF", "GA/a_b-c.d/e"],
    )
    def test_a_well_formed_topic_is_accepted(self, topic):
        assert mqtt_raw_write.valid_topic(topic) is True

    @pytest.mark.asyncio
    async def test_a_valid_topic_still_publishes(self, raw_client):
        coordinator = _coordinator(on_lan=False, topic=TOPIC)
        entity = _segment_entity(coordinator)

        await entity.async_turn_on(rgb_color=(255, 0, 0))

        assert _published(coordinator).args[3] == TOPIC
        coordinator.async_control_device.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_renamed_topic_accessor_falls_through(self, raw_client):
        """An upstream rename must report "not handled", not raise."""
        coordinator = _coordinator(on_lan=False)
        del coordinator._ensure_device_topic  # MagicMock: attribute now absent
        entity = _segment_entity(coordinator)

        await entity.async_turn_on(rgb_color=(255, 0, 0))

        coordinator._mqtt_client.async_publish_ptreal.assert_not_awaited()
        coordinator.async_control_device.assert_awaited()

    @pytest.mark.asyncio
    async def test_the_sku_with_no_local_raw_pipe_reaches_mqtt(self, raw_client):
        """The H6046 ignores LAN raw frames — MQTT is its only raw tier here."""
        coordinator = _coordinator(on_lan=True)
        entity = _segment_entity(coordinator, sku=BLE_ONLY_SKU, segment_count=BLE_ONLY_SEGMENTS)

        await entity.async_turn_on(rgb_color=(255, 0, 0))

        assert raw_client.envelopes == []
        assert _published(coordinator).args[1] == BLE_ONLY_SKU
        coordinator.async_control_device.assert_not_awaited()


# ==============================================================================
# 2. The envelope
# ==============================================================================


class TestEnvelope:
    """Byte-for-byte what the reference documents."""

    @pytest.mark.asyncio
    async def test_the_envelope_matches_the_reference_example(self):
        """`{"command":["MzABAQAAAAAAAAAAAAAAAAAAAAM="]}` — reference §1.2."""
        coordinator = _coordinator()
        frame = bytes.fromhex(REFERENCE_FRAME)

        sent = await mqtt_raw_write.async_send_frames(
            coordinator, DEVICE_ID, "H60B0", get_profile("H60B0"), [frame], what="zone1 on"
        )

        assert sent is True
        args = _published(coordinator).args
        assert args[0] == DEVICE_ID
        assert args[1] == "H60B0"
        assert args[2] == [REFERENCE_B64]
        assert args[3] == TOPIC

    @pytest.mark.asyncio
    async def test_several_frames_travel_in_one_envelope(self):
        """The device applies a command array in order, as it does over LAN."""
        coordinator = _coordinator()
        frames = [bytes.fromhex(GOLDEN_SEG0_RED), bytes.fromhex(REFERENCE_FRAME)]

        await mqtt_raw_write.async_send_frames(coordinator, DEVICE_ID, LAN_SKU, get_profile(LAN_SKU), frames)

        assert _published(coordinator).args[2] == [GOLDEN_SEG0_RED_B64, REFERENCE_B64]
        assert coordinator._mqtt_client.async_publish_ptreal.await_count == 1

    @pytest.mark.asyncio
    async def test_a_successful_publish_stamps_the_mqtt_transport(self):
        coordinator = _coordinator()

        await mqtt_raw_write.async_send_frames(
            coordinator, DEVICE_ID, LAN_SKU, get_profile(LAN_SKU), [bytes.fromhex(GOLDEN_SEG0_RED)]
        )

        coordinator._record_transport_send.assert_called_once_with(DEVICE_ID, "mqtt")

    @pytest.mark.asyncio
    async def test_no_frames_is_never_published(self):
        coordinator = _coordinator()

        assert (
            await mqtt_raw_write.async_send_frames(coordinator, DEVICE_ID, LAN_SKU, get_profile(LAN_SKU), []) is False
        )


# ==============================================================================
# 3. Gating
# ==============================================================================


class TestGates:
    """Two existing options, the table, and a connected client."""

    @pytest.mark.asyncio
    async def test_both_options_are_required(self):
        profile = get_profile(LAN_SKU)

        assert mqtt_raw_write.mqtt_raw_target(_coordinator(), profile) is True
        assert mqtt_raw_write.mqtt_raw_target(_coordinator(mqtt_control=False), profile) is False
        assert mqtt_raw_write.mqtt_raw_target(_coordinator(lan_raw=False), profile) is False

    @pytest.mark.asyncio
    async def test_a_disconnected_client_is_not_a_target(self):
        assert mqtt_raw_write.mqtt_raw_target(_coordinator(mqtt_connected=False), get_profile(LAN_SKU)) is False

    @pytest.mark.asyncio
    async def test_a_client_that_never_started_is_not_a_target(self):
        coordinator = _coordinator()
        coordinator._mqtt_client = None

        sent = await mqtt_raw_write.async_send_frames(
            coordinator, DEVICE_ID, LAN_SKU, get_profile(LAN_SKU), [bytes.fromhex(GOLDEN_SEG0_RED)]
        )

        assert sent is False

    def test_the_table_says_which_skus_may_use_the_pipe(self):
        for sku in ("H60B0", "H6046", "H6076"):
            assert get_profile(sku).carries(Transport.MQTT_PTREAL) is True
        # ...and every one of them also has a local pipe the router tries
        # first. That ordering is the router's, not the table's.
        assert get_profile("H60B0").carries(Transport.LAN_RAW) is True
        assert get_profile("H6046").carries(Transport.BLE_PLAINTEXT) is True

    @pytest.mark.asyncio
    async def test_every_raw_option_off_builds_nothing(self, raw_client):
        coordinator = _coordinator(lan_raw=False, mqtt_control=False)
        entity = _segment_entity(coordinator)

        await entity.async_turn_on(rgb_color=(255, 0, 0))

        assert raw_client.envelopes == []
        coordinator._mqtt_client.async_publish_ptreal.assert_not_awaited()
        coordinator.async_control_device.assert_awaited()
