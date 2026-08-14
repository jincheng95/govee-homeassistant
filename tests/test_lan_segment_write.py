"""Tests for routing per-segment colour over raw LAN (fork feature).

The segment light entities already exist and already have a cloud command that
expresses exactly the same intent, so this path is a pure transport swap: try
the LAN frame, fall back to ``SegmentColorCommand`` for every reason the frame
would be a guess.

The interesting cases are the refusals, and the sharpest one is the H6046: it
has a full segment-colour profile and it ignores raw frames over LAN entirely,
because it is on an older stack whose raw pipe is BLE. Nothing downstream can
notice — the send is fire-and-forget, no reply is expected — so a frame posted
to it would report success, update optimistic state, and change nothing on the
lamp. The gate is therefore the SKU's declared transport, checked before the
datagram is built, and the H6046 must come out on the cloud path.

The H6076 is the SKU this path actually serves: same 0x15 body as the H6046,
but on the modern stack that takes raw LAN writes.

The H60B0 is the third shape, added later (roadmap 2.5): it has **no** segment
capability at all. Its 8 addressable LEDs belong to the ring *zone*, so a
segment paint is the zone-colour frame with a segment mask, and a segment
brightness is the zone-brightness frame with the same mask at a different
offset. The routing is table data — ``profile.segment_zone`` — not a SKU
branch, and every frame below is one of the reference's verified §3.1 rows.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.govee.api import lan_raw, raw_router
from custom_components.govee.api.lan_client import LanDeviceInfo
from custom_components.govee.api.protocol import (
    Capability,
    GoveeCodec,
    Transport,
    get_profile,
)
from custom_components.govee.const import CONF_ENABLE_LAN_RAW_WRITE
from custom_components.govee.models import GoveeDeviceState, SegmentColorCommand
from custom_components.govee.platforms.grouped_segment import GoveeGroupedSegmentEntity
from custom_components.govee.platforms.segment import GoveeSegmentEntity

DEVICE_ID = "AA:BB:CC:DD:EE:FF:60:76"
IP = "10.20.0.52"

# The SKU that carries raw frames over LAN, and its real segment count.
LAN_SKU = "H6076"
LAN_SEGMENTS = 7

# The SKU with the same frame body that does NOT carry raw frames over LAN.
BLE_ONLY_SKU = "H6046"
BLE_ONLY_SEGMENTS = 10

# Golden frames: 33 05 15 01 <R G B> <Khi Klo> <wR wG wB> <mask0 mask1> <xor>.
GOLDEN_SEG0_RED = "33051501ff0000000000000001000000000000dc"
GOLDEN_ALL_BLACK = "3305150100000000000000007f0000000000005d"
GOLDEN_SEG0_BLACK = "3305150100000000000000000100000000000023"
# The level frame that rides with a colour paint when the slider moved:
# 33 05 15 02 <level> <mask0 mask1> — mask straight after the level (§3.2).
GOLDEN_SEG0_LEVEL_32 = "3305150220010000000000000000000000000000"

# The SKU whose segments live on a zone: ring, 8 segments, zone byte 2.
ZONE_SKU = "H60B0"
ZONE_SEGMENTS = 8
ZONE_KEY = "ring"

# Golden frames, all verified rows of `reference/govee-device-protocol.md` §3.1:
#   colour     33 05 2c 02 01 <R G B> <Khi Klo> <mask0 mask1>   (mask at 10)
#   brightness 33 05 2c 02 02 <level> <mask0 mask1>             (mask at 6)
GOLDEN_RING_SEG3_YELLOW = "33052c0201ffff0000000400000000000000001d"  # "ring, seg 3 only yellow"
GOLDEN_RING_ALL_CYAN = "33052c020100ffff0000ff0000000000000000e6"  # "ring, all 8 segs cyan"
GOLDEN_RING_BRIGHT_ALL_100 = "33052c020264ff00000000000000000000000081"  # "ring brightness all 100%"
GOLDEN_RING_BRIGHT_5_8_32 = "33052c020220f0000000000000000000000000ca"  # "ring brightness segs 5-8 at 32%"
# Not a reference row: same colour body as seg-3-yellow with RGB zeroed, which
# is what a segment turn_off paints.
GOLDEN_RING_SEG3_BLACK = "33052c020100000000000400000000000000001d"

# HA brightness values that land on the golden levels (0-255 -> 0-100).
HA_BRIGHTNESS_100_PERCENT = 255
HA_BRIGHTNESS_32_PERCENT = 82


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
    monkeypatch.setattr(lan_raw, "_CLIENT", client)
    monkeypatch.setattr(lan_raw, "LAN_WRITE_GAP_SECONDS", 0)
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
            sku=LAN_SKU,
            firmware="1.0.0",
            last_correlated_ts=0.0,
        )
    return coordinator


def _segment_entity(coordinator: Any, *, sku: str = LAN_SKU, segment_count: int = LAN_SEGMENTS, index: int = 0) -> Any:
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
    entity._segment_index = index
    entity._is_on = True
    entity._brightness = 255
    entity._rgb_color = (255, 255, 255)
    entity.async_write_ha_state = MagicMock()
    return entity


def _grouped_entity(coordinator: Any, *, sku: str = LAN_SKU, segment_count: int = LAN_SEGMENTS) -> Any:
    device = MagicMock()
    device.device_id = DEVICE_ID
    device.sku = sku
    device.segment_count = segment_count
    device.name = "Floor Lamp"

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
    """The exact bytes a segment paint puts on the wire."""

    @pytest.mark.asyncio
    async def test_single_segment_colour_goes_over_lan(self, raw_client):
        coordinator = _coordinator()
        entity = _segment_entity(coordinator)

        await entity.async_turn_on(rgb_color=(255, 0, 0))

        assert raw_client.hexes == [GOLDEN_SEG0_RED]
        coordinator.async_control_device.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_turn_off_paints_black_over_lan(self, raw_client):
        coordinator = _coordinator()
        entity = _segment_entity(coordinator)

        await entity.async_turn_off()

        assert raw_client.hexes == [GOLDEN_SEG0_BLACK]
        coordinator.async_control_device.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_grouped_entity_paints_every_segment(self, raw_client):
        coordinator = _coordinator()
        entity = _grouped_entity(coordinator)

        await entity.async_turn_on(rgb_color=(0, 0, 0))

        assert raw_client.hexes == [GOLDEN_ALL_BLACK]

    @pytest.mark.asyncio
    async def test_frame_is_repeated(self, raw_client):
        coordinator = _coordinator()
        entity = _segment_entity(coordinator)

        await entity.async_turn_on(rgb_color=(255, 0, 0))

        assert len(raw_client.envelopes) == lan_raw.LAN_WRITE_REPEATS

    def test_mask_bit_matches_the_segment_index(self):
        profile = get_profile(LAN_SKU)
        spec = profile.capabilities[Capability.SEGMENT_COLOR]
        frame = bytes.fromhex(GOLDEN_SEG0_RED)
        offset = spec.constants["mask_offset"]

        assert frame[offset] == 0b0000_0001
        assert frame[2] == spec.constants["sub_mode"]


# ==============================================================================
# 2. Fallback — every reason to leave the cloud path alone
# ==============================================================================


class TestFallback:
    """Every reason to leave the cloud command alone."""

    @pytest.mark.asyncio
    async def test_option_off_uses_the_cloud(self, raw_client):
        coordinator = _coordinator(enabled=False)
        entity = _segment_entity(coordinator)

        await entity.async_turn_on(rgb_color=(255, 0, 0))

        assert raw_client.envelopes == []
        command = coordinator.async_control_device.await_args_list[-1].args[1]
        assert isinstance(command, SegmentColorCommand)

    @pytest.mark.asyncio
    async def test_device_not_on_lan_uses_the_cloud(self, raw_client):
        coordinator = _coordinator(on_lan=False)
        entity = _segment_entity(coordinator)

        await entity.async_turn_on(rgb_color=(255, 0, 0))

        assert raw_client.envelopes == []
        coordinator.async_control_device.assert_awaited()

    @pytest.mark.asyncio
    async def test_write_suppression_uses_the_cloud(self, raw_client):
        """The #57 cooldown reroutes segment colour — it has an exact cloud
        fallback (``SegmentColorCommand``), unlike the LAN-only zone writes
        which ignore the flag (narrowed 2026-08-12)."""
        coordinator = _coordinator()
        coordinator._lan_writes_suppressed = MagicMock(return_value=True)
        entity = _segment_entity(coordinator)

        await entity.async_turn_on(rgb_color=(255, 0, 0))

        assert raw_client.envelopes == []
        command = coordinator.async_control_device.await_args_list[-1].args[1]
        assert isinstance(command, SegmentColorCommand)

    @pytest.mark.asyncio
    async def test_unprofiled_sku_uses_the_cloud(self, raw_client):
        coordinator = _coordinator()
        entity = _segment_entity(coordinator, sku="H6199")

        await entity.async_turn_on(rgb_color=(255, 0, 0))

        assert raw_client.envelopes == []
        coordinator.async_control_device.assert_awaited()

    @pytest.mark.asyncio
    async def test_a_ble_only_sku_never_gets_a_lan_frame(self, raw_client):
        """The H6046 has a full SEGMENT_COLOR profile and ignores LAN raw frames.

        Regression test for a silent no-op: routing on "the profile declares
        the capability" sent this SKU a datagram it discards, reported success
        and updated optimistic state, while the lamp did nothing. The gate is
        the SKU's transport list, so this must land on the cloud command.
        """
        # Fully reachable on the LAN — the only thing wrong is the SKU's stack.
        coordinator = _coordinator()
        entity = _segment_entity(coordinator, sku=BLE_ONLY_SKU, segment_count=BLE_ONLY_SEGMENTS)

        await entity.async_turn_on(rgb_color=(255, 0, 0))

        assert raw_client.envelopes == []
        command = coordinator.async_control_device.await_args_list[-1].args[1]
        assert isinstance(command, SegmentColorCommand)

    def test_the_transport_lists_are_what_the_gate_reads(self):
        """The gate is data, not a SKU branch: assert the declared transports."""
        assert get_profile(BLE_ONLY_SKU).carries(Transport.LAN_RAW) is False
        assert get_profile(BLE_ONLY_SKU).carries(Transport.BLE_PLAINTEXT) is True
        assert get_profile(LAN_SKU).carries(Transport.LAN_RAW) is True
        assert get_profile("H60B0").carries(Transport.LAN_RAW) is True

    def test_both_v2_skus_now_encode_the_same_segment_bodies(self):
        """The 0x15 body is shared; the SKUs differ only in transport and width.

        Both constant blocks were UNKNOWN until hardware settled them, so this
        pins that they are settled *and* that they agree.
        """
        for sku in (LAN_SKU, BLE_ONLY_SKU):
            profile = get_profile(sku)
            colour = profile.capabilities[Capability.SEGMENT_COLOR]
            level = profile.capabilities[Capability.SEGMENT_BRIGHTNESS]
            assert colour.constants == {"sub_mode": 0x15, "attribute": 0x01, "mask_offset": 12}
            assert level.constants == {"sub_mode": 0x15, "attribute": 0x02, "mask_offset": 5}
            assert colour.known and level.known
            # Encoding no longer raises for either SKU.
            GoveeCodec(profile).segment_color((255, 0, 0), segments=[0])
            GoveeCodec(profile).segment_brightness(50, segments=[0])

    def test_segment_brightness_mask_follows_the_level_byte(self):
        """mask_offset counts from proType and differs BY ATTRIBUTE.

        Reusing the colour body's offset of 12 here would drop the mask into
        dead padding and the frame would be a silent no-op.
        """
        frame = GoveeCodec(get_profile(BLE_ONLY_SKU)).segment_brightness(25, segments=[1])

        assert frame.hex() == "330515021902000000000000000000000000003a"
        assert frame[4] == 25  # level
        assert frame[5] == 0b0000_0010  # mask, immediately after

    @pytest.mark.asyncio
    async def test_segment_count_mismatch_uses_the_cloud(self, raw_client):
        """The table's mask width and the entity's indices must agree.

        A device reporting FEWER segments than the table expects is a
        different hardware revision, and a mask built for the wrong width
        lights the wrong LEDs — so the paint stays on the cloud.
        """
        coordinator = _coordinator()
        entity = _segment_entity(coordinator, segment_count=4)

        await entity.async_turn_on(rgb_color=(255, 0, 0))

        assert raw_client.envelopes == []
        coordinator.async_control_device.assert_awaited()

    @pytest.mark.asyncio
    async def test_the_cloud_over_report_no_longer_blocks_the_lan_path(self, raw_client):
        """The 15-vs-7 over-report is capped at entity creation, so the gate passes.

        Live 2026-08-14: the cloud advertises 15 segments for the H6076 and the
        lamp has 7. That disagreement used to route every dining-room segment
        paint over the cloud ("segment count disagrees with the profile mask
        width"). The count is now capped at the profile's width before it
        reaches this gate, so the frame is built and goes out over the LAN.
        """
        coordinator = _coordinator()
        entity = _segment_entity(coordinator, segment_count=15)

        await entity.async_turn_on(rgb_color=(255, 0, 0))

        assert raw_client.hexes == [GOLDEN_SEG0_RED]
        coordinator.async_control_device.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_turn_off_still_skips_everything_when_the_device_is_off(self, raw_client):
        """The upstream race guard (issue #16) must survive the LAN routing."""
        coordinator = _coordinator()
        coordinator.get_state.return_value.power_state = False
        entity = _segment_entity(coordinator)

        await entity.async_turn_off()

        assert raw_client.envelopes == []
        coordinator.async_control_device.assert_not_awaited()
        assert entity.is_on is False


# ==============================================================================
# 3. Zone-owned segments — the H60B0's ring (roadmap 2.5)
# ==============================================================================


def _ring_entity(coordinator: Any, *, index: int = 2, segment_count: int = ZONE_SEGMENTS) -> Any:
    """A segment entity on the SKU whose segments belong to the ring zone."""
    return _segment_entity(coordinator, sku=ZONE_SKU, segment_count=segment_count, index=index)


class TestZoneOwnedSegments:
    """The H60B0 paints segments with the ZONE frame plus a mask."""

    def test_the_profile_is_what_selects_the_form(self):
        """No SKU branch anywhere: the table says where the segments live."""
        zone_profile = get_profile(ZONE_SKU)
        mask_profile = get_profile(LAN_SKU)

        assert zone_profile.segment_zone_key == ZONE_KEY
        assert mask_profile.segment_zone_key is None
        # ...and the reason it needs saying: this SKU has no segment capability.
        assert Capability.SEGMENT_COLOR not in zone_profile.capabilities
        assert Capability.SEGMENT_BRIGHTNESS not in zone_profile.capabilities

    def test_the_mask_offset_is_per_attribute_table_data(self):
        """Colour masks at frame index 10, brightness at 6. Same zone, same mask."""
        profile = get_profile(ZONE_SKU)

        colour = profile.capabilities[Capability.ZONE_COLOR].constants
        level = profile.capabilities[Capability.ZONE_BRIGHTNESS].constants
        assert colour == {"sub_mode": 0x2C, "attribute": 0x01, "mask_offset": 10}
        assert level == {"sub_mode": 0x2C, "attribute": 0x02, "mask_offset": 6}

        colour_frame = bytes.fromhex(GOLDEN_RING_SEG3_YELLOW)
        level_frame = bytes.fromhex(GOLDEN_RING_BRIGHT_5_8_32)
        assert colour_frame[colour["mask_offset"]] == 0b0000_0100  # segment 3, 1-based
        assert level_frame[level["mask_offset"]] == 0b1111_0000  # segments 5-8

    @pytest.mark.asyncio
    async def test_single_segment_paint_is_the_zone_colour_frame(self, raw_client):
        coordinator = _coordinator()
        entity = _ring_entity(coordinator)

        await entity.async_turn_on(rgb_color=(255, 255, 0))

        assert raw_client.hexes == [GOLDEN_RING_SEG3_YELLOW]
        coordinator.async_control_device.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_turn_off_paints_the_masked_segment_black(self, raw_client):
        coordinator = _coordinator()
        entity = _ring_entity(coordinator)

        await entity.async_turn_off()

        assert raw_client.hexes == [GOLDEN_RING_SEG3_BLACK]
        coordinator.async_control_device.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_grouped_entity_masks_every_segment(self, raw_client):
        coordinator = _coordinator()
        entity = _grouped_entity(coordinator, sku=ZONE_SKU, segment_count=ZONE_SEGMENTS)

        await entity.async_turn_on(rgb_color=(0, 255, 255))

        assert raw_client.hexes == [GOLDEN_RING_ALL_CYAN]

    @pytest.mark.asyncio
    async def test_brightness_rides_along_as_a_second_frame(self, raw_client):
        """One envelope, colour then level, both masked to the same segments."""
        coordinator = _coordinator()
        entity = _grouped_entity(coordinator, sku=ZONE_SKU, segment_count=ZONE_SEGMENTS)

        await entity.async_turn_on(rgb_color=(0, 255, 255), brightness=HA_BRIGHTNESS_100_PERCENT)

        assert raw_client.hexes == [GOLDEN_RING_ALL_CYAN, GOLDEN_RING_BRIGHT_ALL_100]

    @pytest.mark.asyncio
    async def test_masked_brightness_matches_the_reference_frame(self, raw_client):
        """Segments 5-8 at 32% — the reference's verified ZONE_BRIGHTNESS row."""
        coordinator = _coordinator()
        entity = _ring_entity(coordinator)

        handled = await raw_router.async_segment_color(
            entity, (0, 255, 255), [4, 5, 6, 7], brightness=HA_BRIGHTNESS_32_PERCENT
        )

        assert handled is True
        assert raw_client.hexes[1] == GOLDEN_RING_BRIGHT_5_8_32

    @pytest.mark.asyncio
    async def test_a_colour_only_paint_sends_no_level_frame(self, raw_client):
        """An untouched brightness slider must not overwrite the zone's level."""
        coordinator = _coordinator()
        entity = _ring_entity(coordinator)

        await entity.async_turn_on(rgb_color=(255, 255, 0))

        assert len(raw_client.hexes) == 1

    @pytest.mark.asyncio
    async def test_an_empty_selection_is_never_sent(self, raw_client):
        """An all-zero mask is accepted by the firmware and silently does nothing.

        The codec refuses to build it; this asserts the refusal reaches the
        caller as "not handled" rather than as a datagram or an exception.
        """
        coordinator = _coordinator()
        entity = _ring_entity(coordinator)

        handled = await raw_router.async_segment_color(entity, (255, 0, 0), [], brightness=255)

        assert handled is False
        assert raw_client.envelopes == []

    @pytest.mark.asyncio
    async def test_no_lan_target_falls_back_to_the_cloud(self, raw_client):
        coordinator = _coordinator(on_lan=False)
        entity = _ring_entity(coordinator)

        await entity.async_turn_on(rgb_color=(255, 255, 0), brightness=HA_BRIGHTNESS_100_PERCENT)

        assert raw_client.envelopes == []
        command = coordinator.async_control_device.await_args_list[-1].args[1]
        assert isinstance(command, SegmentColorCommand)

    @pytest.mark.asyncio
    async def test_option_off_falls_back_to_the_cloud(self, raw_client):
        coordinator = _coordinator(enabled=False)
        entity = _ring_entity(coordinator)

        await entity.async_turn_on(rgb_color=(255, 255, 0))

        assert raw_client.envelopes == []
        command = coordinator.async_control_device.await_args_list[-1].args[1]
        assert isinstance(command, SegmentColorCommand)

    @pytest.mark.asyncio
    async def test_segment_count_mismatch_falls_back_to_the_cloud(self, raw_client):
        """The zone's mask width and the entity's indices must agree."""
        coordinator = _coordinator()
        entity = _ring_entity(coordinator, segment_count=4)

        await entity.async_turn_on(rgb_color=(255, 255, 0))

        assert raw_client.envelopes == []
        coordinator.async_control_device.assert_awaited()

    @pytest.mark.asyncio
    async def test_the_mask_only_path_carries_brightness_too(self, raw_client):
        """Both forms now send a level frame when the slider moved.

        The mask-only SKUs shipped colour-only; their ``SEGMENT_BRIGHTNESS``
        body (attribute 0x02, mask straight after the level) is hardware-
        verified on the H6046 and the H6076 alike, and the H6046's segments
        need it — that SKU has no zone form to fall back on.
        """
        coordinator = _coordinator()
        entity = _segment_entity(coordinator)

        await entity.async_turn_on(rgb_color=(255, 0, 0), brightness=HA_BRIGHTNESS_32_PERCENT)

        assert raw_client.hexes == [GOLDEN_SEG0_RED, GOLDEN_SEG0_LEVEL_32]
        coordinator.async_control_device.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_mask_only_path_sends_no_level_frame_without_a_brightness(self, raw_client):
        """An untouched slider must not overwrite the segments' level."""
        coordinator = _coordinator()
        entity = _segment_entity(coordinator)

        await entity.async_turn_on(rgb_color=(255, 0, 0))

        assert raw_client.hexes == [GOLDEN_SEG0_RED]
