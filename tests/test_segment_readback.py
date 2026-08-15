"""Segment state reconciled from the MQTT `aa a5` push (fork feature).

The H6046 light bar pushes `reference` §6.2 per-segment read frames unsolicited
on its AWS IoT status topic. Decoding them corrects segment entities that are
otherwise optimistic-only — including drift caused by the vendor app.

Organised around the properties the path has to have:

1. Golden decode — the live 2026-08-14 capture, byte for byte.
2. Phantom indices — the frames come in groups of four, the hardware does not.
3. Correction — a vendor-app paint moves the entity.
4. No churn — a reading that agrees writes nothing.
5. Wiring — the transport hook fires, and non-segment SKUs are untouched.
"""

from __future__ import annotations

import base64
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.govee.api.auth import GoveeIotCredentials
from custom_components.govee.api.mqtt import GoveeAwsIotClient
from custom_components.govee.api.protocol import PROFILES
from custom_components.govee.api.segment_readback import (
    MAX_COMMAND_CHARS,
    MAX_COMMANDS,
    SegmentReading,
    decode_frames,
    decode_payload,
    level_to_ha_brightness,
)
from custom_components.govee.const import SIGNAL_SEGMENT_READBACK
from custom_components.govee.coordinator import GoveeCoordinator
from custom_components.govee.models import GoveeCapability, GoveeDevice
from custom_components.govee.models.device import (
    CAPABILITY_ON_OFF,
    DEVICE_TYPE_LIGHT,
    INSTANCE_POWER,
)
from custom_components.govee.platforms.segment import (
    READBACK_WRITE_GRACE_SECONDS,
    GoveeSegmentEntity,
)

DEVICE_ID = "AA:BB:AA:BB:CC:11:22:33"  # synthetic; H6046 prefix

# Captured live 2026-08-14 off the H6046 light bar, seconds after painting the
# segment at index 1 magenta at 70 %. Five frames ride one op.command array:
# `aa 05` sub-mode, three `aa a5` readback groups, `aa 11` settings.
CAPTURED_COMMANDS = [
    "qgUVAAAAAAAAAAAAAAAAAAAAALo=",
    "qqUBZAAAAEb/AP9kAAAAZAAAACw=",
    "qqUCZAAAAGQAAABkAAAAZAAAAA0=",
    "qqUDZAAAAGQAAABkZEZkAGRkZEo=",
    "qhEAHg8PAAAAAAAAAAAAAAAAAKU=",
]

CAPTURED_PAYLOAD: dict[str, Any] = {
    "sku": "H6046",
    "device": DEVICE_ID,
    "op": {"command": CAPTURED_COMMANDS},
    "state": {"onOff": 1, "brightness": 100},
}

H6046_SEGMENTS = 10
"""The profile's verified count: 2 bars x 5. The frames report 12."""

MAGENTA = (255, 0, 255)


def _frame(*body: int) -> bytes:
    """A 20-byte frame from its first bytes, zero-padded, XOR checksum applied."""
    raw = bytearray(20)
    raw[: len(body)] = bytes(body)
    checksum = 0
    for byte in raw[:19]:
        checksum ^= byte
    raw[19] = checksum
    return bytes(raw)


def _device(sku: str = "H6046") -> GoveeDevice:
    return GoveeDevice(
        device_id=DEVICE_ID,
        sku=sku,
        name="Living Room Light Bar",
        device_type=DEVICE_TYPE_LIGHT,
        capabilities=(GoveeCapability(type=CAPABILITY_ON_OFF, instance=INSTANCE_POWER, parameters={}),),
    )


def _iot_credentials() -> GoveeIotCredentials:
    return GoveeIotCredentials(
        token="t",
        refresh_token="r",
        account_topic="GA/account",
        iot_cert="cert",
        iot_key="key",
        iot_ca=None,
        client_id="cid",
        endpoint="endpoint",
    )


def _segment_entity(index: int) -> GoveeSegmentEntity:
    """A mounted segment entity with the optimistic state it starts life with."""
    coordinator = MagicMock()
    coordinator.last_update_success = True
    entity = GoveeSegmentEntity(coordinator=coordinator, device=_device(), segment_index=index)
    entity.hass = MagicMock()
    entity.async_write_ha_state = MagicMock()
    return entity


class TestGoldenCapture:
    """The live payload decodes to exactly what the bar was showing."""

    def test_captured_payload_decodes_ten_segments(self):
        readings = decode_payload(CAPTURED_PAYLOAD, segment_count=H6046_SEGMENTS)

        assert sorted(readings) == list(range(H6046_SEGMENTS))

    def test_painted_segment_carries_its_level_and_colour(self):
        readings = decode_payload(CAPTURED_PAYLOAD, segment_count=H6046_SEGMENTS)

        # `46 ff 00 ff` in the first aa a5 frame: level 0x46 = 70, magenta.
        assert readings[1] == SegmentReading(index=1, level=70, rgb=MAGENTA)
        assert readings[1].is_on is True
        assert readings[1].brightness == 178

    def test_unpainted_segments_read_black_and_therefore_off(self):
        readings = decode_payload(CAPTURED_PAYLOAD, segment_count=H6046_SEGMENTS)

        others = [readings[i] for i in range(H6046_SEGMENTS) if i != 1]
        # Level 100 but black: off is painted, not powered, on this hardware.
        assert all(r.level == 100 and r.rgb == (0, 0, 0) for r in others)
        assert not any(r.is_on for r in others)

    def test_non_readback_frames_in_the_same_array_are_ignored(self):
        # aa 05 (sub-mode) and aa 11 (settings) ride the same array and must
        # not be mistaken for segment quads.
        only_readback = [c for c in CAPTURED_COMMANDS if base64.b64decode(c)[1] == 0xA5]

        assert decode_payload({"op": {"command": only_readback}}, segment_count=H6046_SEGMENTS) == decode_payload(
            CAPTURED_PAYLOAD, segment_count=H6046_SEGMENTS
        )

    def test_a_level_converted_to_ha_and_back_is_the_same_level(self):
        """Level -> HA -> level is exact; that direction has no information loss."""
        from custom_components.govee.api.protocol import ha_to_percent

        for level in range(1, 101):
            assert ha_to_percent(level_to_ha_brightness(level)) == level

        assert level_to_ha_brightness(100) == 255
        assert level_to_ha_brightness(0) == 0

    def test_a_brightness_converted_to_the_wire_and_back_is_quantised(self):
        """The other direction is lossy — 255 values do not fit in 101.

        This is why the entity's no-churn check compares levels, not
        brightnesses: 179 comes back as 178, which brightness-space equality
        would read as a change on every single push.
        """
        from custom_components.govee.api.protocol import ha_to_percent

        assert level_to_ha_brightness(ha_to_percent(179)) == 178
        assert level_to_ha_brightness(ha_to_percent(179)) != 179

        # Exactly the brightnesses that are not themselves images of a level.
        exact = {level_to_ha_brightness(level) for level in range(1, 101)}
        lossy = [ha for ha in range(1, 256) if level_to_ha_brightness(ha_to_percent(ha)) != ha]
        assert len(lossy) == 255 - len(exact)
        assert lossy

        # But every one of them agrees in level space, which is what the guard
        # compares.
        for ha in lossy:
            assert ha_to_percent(level_to_ha_brightness(ha_to_percent(ha))) == ha_to_percent(ha)


class TestPhantomIndices:
    """Groups of four, hardware that is not a multiple of four."""

    def test_third_group_contributes_only_the_two_real_segments(self):
        readings = decode_payload(CAPTURED_PAYLOAD, segment_count=H6046_SEGMENTS)

        # aa a5 03 covers segments 9-12 (0-based 8-11); the bar has 10.
        assert 8 in readings and 9 in readings
        assert 10 not in readings and 11 not in readings

    def test_the_phantom_quads_carry_junk_that_would_have_been_believed(self):
        # Proof the cap is load-bearing rather than cosmetic: uncapped, index
        # 10 reads as a lit segment the bar does not have.
        uncapped = decode_payload(CAPTURED_PAYLOAD, segment_count=12)

        assert uncapped[10].rgb == (100, 70, 100)
        assert uncapped[10].is_on is True

    def test_zero_segment_count_decodes_nothing(self):
        assert decode_payload(CAPTURED_PAYLOAD, segment_count=0) == {}


class TestFrameFiltering:
    """A mixed stream, parsed as a filter rather than as a grammar."""

    def test_short_and_foreign_frames_are_skipped(self):
        frames = [
            b"\xaa\xa5",  # truncated
            _frame(0xEE, 0x34, 0x01),  # leak hub report
            _frame(0xAA, 0x05, 0x15),  # sub-mode read
        ]

        assert decode_frames(frames, segment_count=H6046_SEGMENTS) == {}

    def test_a_bad_checksum_is_discarded(self):
        good = bytearray(_frame(0xAA, 0xA5, 0x01, 100, 255, 0, 0))
        bad = bytes(good[:19] + bytearray([good[19] ^ 0xFF]))

        assert decode_frames([bytes(good)], segment_count=4)[0].rgb == (255, 0, 0)
        assert decode_frames([bad], segment_count=4) == {}

    def test_group_zero_is_not_a_valid_index(self):
        assert decode_frames([_frame(0xAA, 0xA5, 0x00, 100, 1, 2, 3)], segment_count=4) == {}

    def test_payload_without_op_yields_nothing(self):
        assert decode_payload({"state": {"onOff": 1}}, segment_count=H6046_SEGMENTS) == {}
        assert decode_payload({"op": {}}, segment_count=H6046_SEGMENTS) == {}
        assert decode_payload({"op": {"command": "notalist"}}, segment_count=4) == {}

    def test_undecodable_base64_does_not_lose_its_neighbours(self):
        payload = {"op": {"command": ["!!!not base64!!!", CAPTURED_COMMANDS[1]]}}

        readings = decode_payload(payload, segment_count=H6046_SEGMENTS)

        assert readings[1].rgb == MAGENTA


class TestRemoteInputIsBounded:
    """`op.command` is remote input; decoding it is capped before it runs."""

    def test_only_the_first_entries_are_decoded(self):
        good = base64.b64encode(_frame(0xAA, 0xA5, 1, 70, 255, 0, 255)).decode()
        payload = {"op": {"command": ["A" * 4] * MAX_COMMANDS + [good]}}

        # The one real frame sits past the cap, so nothing is decoded.
        assert decode_payload(payload, segment_count=H6046_SEGMENTS) == {}

    def test_an_entry_at_the_cap_is_still_decoded(self):
        good = base64.b64encode(_frame(0xAA, 0xA5, 1, 70, 255, 0, 255)).decode()
        payload = {"op": {"command": ["A" * 4] * (MAX_COMMANDS - 1) + [good]}}

        assert decode_payload(payload, segment_count=H6046_SEGMENTS)[0].rgb == MAGENTA

    def test_an_oversized_entry_is_skipped_without_decoding_it(self):
        good = base64.b64encode(_frame(0xAA, 0xA5, 1, 70, 255, 0, 255)).decode()
        payload = {"op": {"command": ["A" * (MAX_COMMAND_CHARS + 4), good]}}

        # Skipped, and its neighbours still decode.
        assert decode_payload(payload, segment_count=H6046_SEGMENTS)[0].rgb == MAGENTA

    def test_an_entry_at_the_length_cap_is_still_decoded(self):
        padding = base64.b64encode(b"\x00" * (MAX_COMMAND_CHARS // 4 * 3)).decode()
        assert len(padding) <= MAX_COMMAND_CHARS
        good = base64.b64encode(_frame(0xAA, 0xA5, 1, 70, 255, 0, 255)).decode()
        payload = {"op": {"command": [padding, good]}}

        assert decode_payload(payload, segment_count=H6046_SEGMENTS)[0].rgb == MAGENTA


class TestEntityCorrection:
    """What the segment entity does with a reading."""

    @pytest.mark.asyncio
    async def test_vendor_app_paint_corrects_the_entity(self):
        entity = _segment_entity(1)
        # HA believes the segment is white at full brightness; the bar says
        # magenta at 70 % because someone used the Govee app.
        assert entity.rgb_color == (255, 255, 255)

        entity._handle_segment_readback(decode_payload(CAPTURED_PAYLOAD, segment_count=H6046_SEGMENTS))

        assert entity.is_on is True
        assert entity.rgb_color == MAGENTA
        assert entity.brightness == 178
        entity.async_write_ha_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_a_black_reading_turns_the_entity_off(self):
        entity = _segment_entity(0)

        entity._handle_segment_readback(decode_payload(CAPTURED_PAYLOAD, segment_count=H6046_SEGMENTS))

        assert entity.is_on is False
        entity.async_write_ha_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_off_reading_keeps_the_colour_to_return_to(self):
        entity = _segment_entity(0)
        entity._rgb_color = (12, 34, 56)

        entity._handle_segment_readback(decode_payload(CAPTURED_PAYLOAD, segment_count=H6046_SEGMENTS))

        # Off is expressed as black on the wire; adopting it would destroy the
        # colour the segment should come back on with.
        assert entity.rgb_color == (12, 34, 56)

    @pytest.mark.asyncio
    async def test_agreement_writes_nothing(self):
        entity = _segment_entity(1)
        entity._is_on = True
        entity._rgb_color = MAGENTA
        entity._brightness = 178

        entity._handle_segment_readback(decode_payload(CAPTURED_PAYLOAD, segment_count=H6046_SEGMENTS))

        entity.async_write_ha_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_readback_of_our_own_write_is_not_a_change(self):
        """179 is one of the 155 brightnesses the wire cannot carry exactly."""
        entity = _segment_entity(1)
        entity._is_on = True
        entity._rgb_color = MAGENTA
        entity._brightness = 179  # writes as level 70, reads back as 178

        entity._handle_segment_readback(decode_payload(CAPTURED_PAYLOAD, segment_count=H6046_SEGMENTS))

        assert entity.brightness == 179
        entity.async_write_ha_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_genuine_level_change_still_applies(self):
        entity = _segment_entity(1)
        entity._is_on = True
        entity._rgb_color = MAGENTA
        entity._brightness = 255  # level 100, the bar reports 70

        entity._handle_segment_readback(decode_payload(CAPTURED_PAYLOAD, segment_count=H6046_SEGMENTS))

        assert entity.brightness == 178
        entity.async_write_ha_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_agreement_on_an_off_segment_writes_nothing(self):
        entity = _segment_entity(0)
        entity._is_on = False

        entity._handle_segment_readback(decode_payload(CAPTURED_PAYLOAD, segment_count=H6046_SEGMENTS))

        entity.async_write_ha_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_push_racing_a_fresh_paint_does_not_revert_it(self):
        """Inside the echo window the bar still reports its pre-paint colour."""
        entity = _segment_entity(1)
        entity._rgb_color = (0, 255, 0)
        entity._written_at = time.monotonic()

        entity._handle_segment_readback(decode_payload(CAPTURED_PAYLOAD, segment_count=H6046_SEGMENTS))

        assert entity.rgb_color == (0, 255, 0)
        entity.async_write_ha_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_push_after_the_grace_window_applies(self):
        entity = _segment_entity(1)
        entity._rgb_color = (0, 255, 0)
        entity._written_at = time.monotonic() - (READBACK_WRITE_GRACE_SECONDS + 0.1)

        entity._handle_segment_readback(decode_payload(CAPTURED_PAYLOAD, segment_count=H6046_SEGMENTS))

        assert entity.rgb_color == MAGENTA
        entity.async_write_ha_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_a_paint_stamps_the_grace_window(self):
        entity = _segment_entity(1)
        entity.coordinator.async_control_device = AsyncMock(return_value=True)
        assert entity._written_at == 0.0

        with patch("custom_components.govee.platforms.segment.async_segment_color", AsyncMock(return_value=True)):
            with patch(
                "custom_components.govee.platforms.segment.async_ensure_device_powered",
                AsyncMock(),
            ):
                await entity.async_turn_on(rgb_color=MAGENTA)

        assert entity._written_at > 0.0

    @pytest.mark.asyncio
    async def test_a_reading_for_another_segment_is_not_this_entity_s(self):
        entity = _segment_entity(5)
        entity._is_on = True
        entity._rgb_color = (1, 2, 3)

        entity._handle_segment_readback({1: SegmentReading(1, 70, MAGENTA)})

        assert entity.rgb_color == (1, 2, 3)
        entity.async_write_ha_state.assert_not_called()


class TestCoordinatorDispatch:
    """The coordinator resolves the profile and fans out on the signal."""

    def _coordinator(self, device: GoveeDevice | None = None) -> Any:
        coordinator = MagicMock()
        device = device or _device()
        coordinator._devices = {device.device_id: device}
        coordinator.hass = MagicMock()
        return coordinator

    def test_readback_is_dispatched_for_the_device(self):
        coordinator = self._coordinator()

        with patch("custom_components.govee.coordinator.async_dispatcher_send") as send:
            GoveeCoordinator._on_mqtt_raw_frames(coordinator, DEVICE_ID, CAPTURED_PAYLOAD)

        send.assert_called_once()
        _hass, signal, readings = send.call_args.args
        assert signal == SIGNAL_SEGMENT_READBACK.format(device_id=DEVICE_ID)
        assert readings[1].rgb == MAGENTA
        assert len(readings) == H6046_SEGMENTS

    def test_unprofiled_sku_dispatches_nothing(self):
        # An H6117 strip has no profile, so nothing knows how many segments it
        # has and no frame from it is trusted.
        coordinator = self._coordinator(_device(sku="H6117"))

        with patch("custom_components.govee.coordinator.async_dispatcher_send") as send:
            GoveeCoordinator._on_mqtt_raw_frames(coordinator, DEVICE_ID, CAPTURED_PAYLOAD)

        send.assert_not_called()

    def test_profiled_sku_with_no_readback_frames_dispatches_nothing(self):
        coordinator = self._coordinator()

        with patch("custom_components.govee.coordinator.async_dispatcher_send") as send:
            GoveeCoordinator._on_mqtt_raw_frames(
                coordinator,
                DEVICE_ID,
                {"op": {"command": [CAPTURED_COMMANDS[0]]}},
            )

        send.assert_not_called()

    def test_a_profiled_sku_that_does_not_push_readback_dispatches_nothing(self):
        """Having segments is not the same as pushing `aa a5` for them.

        The H6076 is profiled and segmented, but `aa a5` is a BLE query answer
        on that SKU — a push carrying those two bytes is something else.
        """
        coordinator = self._coordinator(_device(sku="H6076"))

        with patch("custom_components.govee.coordinator.async_dispatcher_send") as send:
            GoveeCoordinator._on_mqtt_raw_frames(coordinator, DEVICE_ID, CAPTURED_PAYLOAD)

        send.assert_not_called()

    def test_only_the_h6046_is_marked_as_pushing_readback(self):
        assert [sku for sku, profile in PROFILES.items() if profile.segment_readback] == ["H6046"]

    def test_unknown_device_dispatches_nothing(self):
        coordinator = self._coordinator()

        with patch("custom_components.govee.coordinator.async_dispatcher_send") as send:
            GoveeCoordinator._on_mqtt_raw_frames(coordinator, "99:99:99:99:99:99:99:99", CAPTURED_PAYLOAD)

        send.assert_not_called()


class TestTransportHook:
    """The one hook line in the inbound MQTT path."""

    def _message(self, payload: bytes) -> Any:
        message = MagicMock()
        message.payload = payload
        message.topic = "GA/account"
        return message

    @pytest.mark.asyncio
    async def test_status_push_with_op_reaches_the_raw_frame_callback(self):
        import json

        raw_frames = MagicMock()
        client = GoveeAwsIotClient(
            _iot_credentials(),
            on_state_update=MagicMock(),
            on_raw_frames=raw_frames,
        )

        await client._handle_message(self._message(json.dumps(CAPTURED_PAYLOAD).encode()))

        raw_frames.assert_called_once()
        device_id, payload = raw_frames.call_args.args
        assert device_id == DEVICE_ID
        assert payload["op"]["command"] == CAPTURED_COMMANDS

    @pytest.mark.asyncio
    async def test_an_ordinary_state_push_does_not_fire_the_callback(self):
        raw_frames = MagicMock()
        client = GoveeAwsIotClient(
            _iot_credentials(),
            on_state_update=MagicMock(),
            on_raw_frames=raw_frames,
        )

        await client._handle_message(self._message(b'{"device":"' + DEVICE_ID.encode() + b'","state":{"onOff":1}}'))

        raw_frames.assert_not_called()

    @pytest.mark.asyncio
    async def test_the_state_branch_still_runs_for_a_payload_with_op(self):
        import json

        state_update = MagicMock()
        client = GoveeAwsIotClient(
            _iot_credentials(),
            on_state_update=state_update,
            on_raw_frames=MagicMock(),
        )

        await client._handle_message(self._message(json.dumps(CAPTURED_PAYLOAD).encode()))

        state_update.assert_called_once()
        assert state_update.call_args.args[1]["onOff"] == 1

    @pytest.mark.asyncio
    async def test_no_callback_configured_is_not_an_error(self):
        import json

        client = GoveeAwsIotClient(_iot_credentials(), on_state_update=MagicMock())

        await client._handle_message(self._message(json.dumps(CAPTURED_PAYLOAD).encode()))
