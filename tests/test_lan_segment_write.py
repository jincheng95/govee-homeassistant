"""Tests for routing per-segment colour over raw LAN (fork feature).

The segment light entities already exist and already have a cloud command that
expresses exactly the same intent, so this path is a pure transport swap: try
the LAN frame, fall back to ``SegmentColorCommand`` for every reason the frame
would be a guess.

The interesting cases are the refusals. The H6076's segment sub-mode byte is
UNKNOWN in the profile table and the codec raises rather than emit a frame that
the firmware would silently drop — indistinguishable, from the outside, from a
dead network. That refusal has to reach the cloud path, not the user.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.govee import lan_write
from custom_components.govee.api.lan_client import LanDeviceInfo
from custom_components.govee.api.protocol import (
    Capability,
    GoveeCodec,
    UnknownEncodingError,
    get_profile,
)
from custom_components.govee.const import CONF_ENABLE_LAN_RAW_WRITE
from custom_components.govee.models import GoveeDeviceState, SegmentColorCommand
from custom_components.govee.platforms.grouped_segment import GoveeGroupedSegmentEntity
from custom_components.govee.platforms.segment import GoveeSegmentEntity

DEVICE_ID = "AA:BB:CC:DD:EE:FF:60:46"
IP = "10.20.0.52"

# Golden frames: 33 05 15 01 <R G B> <Khi Klo> <wR wG wB> <mask0 mask1> <xor>.
GOLDEN_SEG0_RED = "33051501ff0000000000000001000000000000dc"
GOLDEN_ALL_BLACK = "330515010000000000000000ff030000000000de"
GOLDEN_SEG0_BLACK = "3305150100000000000000000100000000000023"


class _FakeRawClient:
    def __init__(self) -> None:
        self.envelopes: list[tuple[str, list[bytes]]] = []

    async def async_send_frames(self, host: str, frames: list[bytes]) -> None:
        self.envelopes.append((host, list(frames)))

    async def async_send_frame(self, host: str, frame: bytes) -> None:
        await self.async_send_frames(host, [frame])

    @property
    def hexes(self) -> list[str]:
        if not self.envelopes:
            return []
        return [frame.hex() for frame in self.envelopes[0][1]]


@pytest.fixture(autouse=True)
def raw_client(monkeypatch: pytest.MonkeyPatch) -> _FakeRawClient:
    client = _FakeRawClient()
    monkeypatch.setattr(lan_write, "_CLIENT", client)
    monkeypatch.setattr(lan_write, "LAN_WRITE_GAP_SECONDS", 0)
    return client


def _coordinator(*, enabled: bool = True, on_lan: bool = True) -> Any:
    coordinator = MagicMock()
    coordinator._govee_zone_state_registry = None
    coordinator.config_entry.options = {CONF_ENABLE_LAN_RAW_WRITE: enabled}
    coordinator.async_control_device = AsyncMock(return_value=True)
    coordinator.is_power_off_pending = MagicMock(return_value=False)
    state = GoveeDeviceState.create_empty(DEVICE_ID)
    state.power_state = True
    coordinator.get_state = MagicMock(return_value=state)
    coordinator._lan_devices = {}
    if on_lan:
        coordinator._lan_devices[DEVICE_ID] = LanDeviceInfo(
            device_id=DEVICE_ID,
            ip=IP,
            mac=DEVICE_ID,
            sku="H6046",
            firmware="1.0.0",
            last_correlated_ts=0.0,
        )
    return coordinator


def _segment_entity(coordinator: Any, *, sku: str = "H6046", segment_count: int = 10, index: int = 0) -> Any:
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
    entity._segment_index = index
    entity._is_on = True
    entity._brightness = 255
    entity._rgb_color = (255, 255, 255)
    entity.async_write_ha_state = MagicMock()
    return entity


def _grouped_entity(coordinator: Any, *, segment_count: int = 10) -> Any:
    device = MagicMock()
    device.device_id = DEVICE_ID
    device.sku = "H6046"
    device.segment_count = segment_count
    device.name = "Light Bar"

    with patch.object(GoveeGroupedSegmentEntity, "__init__", lambda self, *a, **kw: None):
        entity = GoveeGroupedSegmentEntity.__new__(GoveeGroupedSegmentEntity)
    entity.coordinator = coordinator
    entity._device = device
    entity._device_id = DEVICE_ID
    entity._segment_indices = tuple(range(segment_count))
    entity._is_on = True
    entity._brightness = 255
    entity._rgb_color = (255, 255, 255)
    entity.async_write_ha_state = MagicMock()
    return entity


# ==============================================================================
# 1. Frames
# ==============================================================================


class TestFrames:
    async def test_single_segment_colour_goes_over_lan(self, raw_client):
        coordinator = _coordinator()
        entity = _segment_entity(coordinator)

        await entity.async_turn_on(rgb_color=(255, 0, 0))

        assert raw_client.hexes == [GOLDEN_SEG0_RED]
        coordinator.async_control_device.assert_not_awaited()

    async def test_turn_off_paints_black_over_lan(self, raw_client):
        coordinator = _coordinator()
        entity = _segment_entity(coordinator)

        await entity.async_turn_off()

        assert raw_client.hexes == [GOLDEN_SEG0_BLACK]
        coordinator.async_control_device.assert_not_awaited()

    async def test_grouped_entity_paints_every_segment(self, raw_client):
        coordinator = _coordinator()
        entity = _grouped_entity(coordinator)

        await entity.async_turn_on(rgb_color=(0, 0, 0))

        assert raw_client.hexes == [GOLDEN_ALL_BLACK]

    async def test_frame_is_repeated(self, raw_client):
        coordinator = _coordinator()
        entity = _segment_entity(coordinator)

        await entity.async_turn_on(rgb_color=(255, 0, 0))

        assert len(raw_client.envelopes) == lan_write.LAN_WRITE_REPEATS

    def test_mask_bit_matches_the_segment_index(self):
        profile = get_profile("H6046")
        spec = profile.capabilities[Capability.SEGMENT_COLOR]
        frame = bytes.fromhex(GOLDEN_SEG0_RED)
        offset = spec.constants["mask_offset"]

        assert frame[offset] == 0b0000_0001
        assert frame[2] == spec.constants["sub_mode"]


# ==============================================================================
# 2. Fallback — every reason to leave the cloud path alone
# ==============================================================================


class TestFallback:
    async def test_option_off_uses_the_cloud(self, raw_client):
        coordinator = _coordinator(enabled=False)
        entity = _segment_entity(coordinator)

        await entity.async_turn_on(rgb_color=(255, 0, 0))

        assert raw_client.envelopes == []
        command = coordinator.async_control_device.await_args_list[-1].args[1]
        assert isinstance(command, SegmentColorCommand)

    async def test_device_not_on_lan_uses_the_cloud(self, raw_client):
        coordinator = _coordinator(on_lan=False)
        entity = _segment_entity(coordinator)

        await entity.async_turn_on(rgb_color=(255, 0, 0))

        assert raw_client.envelopes == []
        coordinator.async_control_device.assert_awaited()

    async def test_unprofiled_sku_uses_the_cloud(self, raw_client):
        coordinator = _coordinator()
        entity = _segment_entity(coordinator, sku="H6199")

        await entity.async_turn_on(rgb_color=(255, 0, 0))

        assert raw_client.envelopes == []
        coordinator.async_control_device.assert_awaited()

    async def test_unknown_constant_refuses_and_uses_the_cloud(self, raw_client):
        """H6076's segment sub-mode is UNKNOWN — refuse, never guess."""
        coordinator = _coordinator()
        entity = _segment_entity(coordinator, sku="H6076", segment_count=7)

        await entity.async_turn_on(rgb_color=(255, 0, 0))

        assert raw_client.envelopes == []
        command = coordinator.async_control_device.await_args_list[-1].args[1]
        assert isinstance(command, SegmentColorCommand)

    def test_the_codec_really_does_refuse_for_h6076(self):
        with pytest.raises(UnknownEncodingError):
            GoveeCodec(get_profile("H6076")).segment_color((255, 0, 0), segments=[0])

    def test_segment_brightness_is_still_refused(self):
        """H6046's segment-brightness attribute is UNKNOWN, and stays UNKNOWN.

        There is no cloud equivalent either, so nothing exposes it at all — the
        integration would rather offer no control than a control that silently
        does nothing.
        """
        with pytest.raises(UnknownEncodingError):
            GoveeCodec(get_profile("H6046")).segment_brightness(50, segments=[0])
        spec = get_profile("H6046").capabilities[Capability.SEGMENT_BRIGHTNESS]
        assert spec.known is False

    async def test_segment_count_mismatch_uses_the_cloud(self, raw_client):
        """The table's mask width and the entity's indices must agree."""
        coordinator = _coordinator()
        entity = _segment_entity(coordinator, segment_count=15)

        await entity.async_turn_on(rgb_color=(255, 0, 0))

        assert raw_client.envelopes == []
        coordinator.async_control_device.assert_awaited()

    async def test_turn_off_still_skips_everything_when_the_device_is_off(self, raw_client):
        """The upstream race guard (issue #16) must survive the LAN routing."""
        coordinator = _coordinator()
        coordinator.get_state.return_value.power_state = False
        entity = _segment_entity(coordinator)

        await entity.async_turn_off()

        assert raw_client.envelopes == []
        coordinator.async_control_device.assert_not_awaited()
        assert entity.is_on is False
