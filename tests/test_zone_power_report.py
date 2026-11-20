"""Zone power from the ``ee 30 01 <bits>`` report, decoded and applied.

Expected frames are written out byte for byte, checksum included. The device
id is synthetic.
"""

from __future__ import annotations

import base64
from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest

from custom_components.govee.api.protocol import get_profile, profiles
from custom_components.govee.api.protocol.errors import GoveeProtocolError
from custom_components.govee.api.segment_readback import payload_frames
from custom_components.govee.api.zone_power_report import (
    ZonePowerReport,
    decode_frames,
    decode_payload,
)
from custom_components.govee.coordinator import GoveeCoordinator
from custom_components.govee.models import GoveeCapability, GoveeDevice
from custom_components.govee.models.device import (
    CAPABILITY_ON_OFF,
    DEVICE_TYPE_LIGHT,
    INSTANCE_POWER,
)
from custom_components.govee.zone_state import (
    ZoneStateRegistry,
    apply_power_report,
    registry,
)

DEVICE_ID = "AA:BB:CC:DD:EE:FF:60:B0"
H60B0 = get_profile("H60B0")


def _report(bits: int, checksum: int) -> bytes:
    return bytes([0xEE, 0x30, 0x01, bits] + [0x00] * 15 + [checksum])


LAMP_ONLY = _report(0x80, 0x5F)
LAMP_OFF = _report(0x00, 0xDF)
RIPPLE = _report(0xC0, 0x1F)
RIPPLE_RING = _report(0xE0, 0x3F)
RING_DOWNLIGHT = _report(0xB0, 0x6F)
DOWNLIGHT = _report(0x90, 0x4F)
BRIGHTNESS_PUSH = bytes([0xEE, 0x20, 0x64] + [0x00] * 16 + [0xAA])


def _payload(*frames: bytes) -> dict:
    return {"op": {"command": [base64.b64encode(frame).decode() for frame in frames]}}


def _zones(ripple: bool, ring: bool, downlight: bool) -> dict[str, bool]:
    return {"ripple": ripple, "ring": ring, "downlight": downlight}


class TestDecode:
    @pytest.mark.parametrize(
        ("frame", "lamp_on", "zones"),
        [
            (LAMP_ONLY, True, _zones(False, False, False)),
            (LAMP_OFF, False, _zones(False, False, False)),
            (RIPPLE, True, _zones(True, False, False)),
            (RIPPLE_RING, True, _zones(True, True, False)),
            (RING_DOWNLIGHT, True, _zones(False, True, True)),
            (DOWNLIGHT, True, _zones(False, False, True)),
        ],
    )
    def test_bits_map_to_zones(self, frame: bytes, lamp_on: bool, zones: dict[str, bool]) -> None:
        assert decode_frames([frame], H60B0) == ZonePowerReport(lamp_on=lamp_on, zones=zones)

    def test_newest_report_wins(self) -> None:
        report = decode_frames([RING_DOWNLIGHT, DOWNLIGHT], H60B0)
        assert report is not None
        assert report.zones == _zones(False, False, True)

    def test_other_frames_are_skipped(self) -> None:
        assert decode_frames([BRIGHTNESS_PUSH, RIPPLE[:19]], H60B0) is None
        report = decode_frames([BRIGHTNESS_PUSH, RIPPLE], H60B0)
        assert report is not None
        assert report.zones["ripple"] is True

    def test_bad_checksum_is_skipped(self) -> None:
        corrupt = RIPPLE[:19] + bytes([RIPPLE[19] ^ 0x01])
        assert decode_frames([corrupt], H60B0) is None
        report = decode_frames([RING_DOWNLIGHT, corrupt], H60B0)
        assert report is not None
        assert report.zones == _zones(False, True, True)

    def test_a_profile_without_the_report_decodes_nothing(self) -> None:
        h6046 = get_profile("H6046")
        assert decode_frames([RIPPLE], h6046) is None
        assert decode_payload(_payload(RIPPLE), h6046) is None

    def test_payload_round_trip(self) -> None:
        report = decode_payload(_payload(BRIGHTNESS_PUSH, RIPPLE_RING), H60B0)
        assert report == ZonePowerReport(lamp_on=True, zones=_zones(True, True, False))

    @pytest.mark.parametrize("data", [{}, {"op": "x"}, {"op": {"command": "x"}}, {"op": {"command": [1, "%%"]}}])
    def test_malformed_payloads_yield_nothing(self, data: dict) -> None:
        assert decode_payload(data, H60B0) is None

    def test_payload_frames_keep_payload_order(self) -> None:
        assert payload_frames(_payload(RIPPLE, DOWNLIGHT)) == [RIPPLE, DOWNLIGHT]

    def test_only_the_h60b0_declares_the_report(self) -> None:
        assert [sku for sku, profile in profiles.PROFILES.items() if profile.zone_power_report] == ["H60B0"]

    def test_h60b0_power_bits(self) -> None:
        bits = {zone.key: zone.power_bit for zone in H60B0.zones}
        assert bits == {
            "ripple": 0x40,
            "ring": 0x20,
            "downlight": 0x10,
            "top_side_platform": None,
            "top_side": None,
        }


class TestTableSelfCheck:
    @pytest.mark.parametrize("bits", [(0x80, None, None), (0x03, None, None), (0x40, 0x40, None)])
    def test_bad_power_bits_are_refused(self, bits: tuple) -> None:
        zones = tuple(
            replace(zone, power_bit=bit) for zone, bit in zip(H60B0.zones, bits + (None, None), strict=True)
        )
        broken = replace(H60B0, sku="H9999", zones=zones)
        with patch.dict(profiles.PROFILES, {"H9999": broken}):
            with pytest.raises(GoveeProtocolError):
                profiles.validate_table()


class TestReconcile:
    def test_only_changed_zones_notify(self) -> None:
        store = ZoneStateRegistry(MagicMock())
        store.seed(DEVICE_ID, "ripple", True)
        listeners = {key: MagicMock() for key in ("ripple", "ring", "downlight")}
        for key, listener in listeners.items():
            store.register(DEVICE_ID, key, listener)

        changed = store.reconcile(DEVICE_ID, _zones(True, True, False))

        assert changed == ["ring"]
        listeners["ripple"].assert_not_called()
        listeners["ring"].assert_called_once_with(True)
        listeners["downlight"].assert_not_called()
        assert store.is_on(DEVICE_ID, "ring") is True

    def test_no_displacement_rule_runs(self) -> None:
        """The report is the lamp's whole picture, even with three zones lit."""
        store = ZoneStateRegistry(MagicMock())
        store.reconcile(DEVICE_ID, _zones(True, True, True))
        assert all(store.is_on(DEVICE_ID, key) for key in ("ripple", "ring", "downlight"))


class TestApplyPowerReport:
    def test_report_turns_zones_on_and_off(self) -> None:
        coordinator = MagicMock()
        coordinator._govee_zone_state_registry = ZoneStateRegistry(coordinator)
        store = registry(coordinator)
        store.seed(DEVICE_ID, "downlight", True)

        changed = apply_power_report(coordinator, DEVICE_ID, "H60B0", _payload(RIPPLE_RING))

        assert sorted(changed) == ["downlight", "ring", "ripple"]
        assert store.is_on(DEVICE_ID, "ripple") is True
        assert store.is_on(DEVICE_ID, "ring") is True
        assert store.is_on(DEVICE_ID, "downlight") is False

    def test_an_unchanged_report_changes_nothing(self) -> None:
        coordinator = MagicMock()
        coordinator._govee_zone_state_registry = ZoneStateRegistry(coordinator)
        apply_power_report(coordinator, DEVICE_ID, "H60B0", _payload(RIPPLE))
        assert apply_power_report(coordinator, DEVICE_ID, "H60B0", _payload(RIPPLE)) == []

    def test_a_lamp_off_report_keeps_the_zones(self) -> None:
        """The lamp clears every zone bit when it powers off."""
        coordinator = MagicMock()
        coordinator._govee_zone_state_registry = ZoneStateRegistry(coordinator)
        store = registry(coordinator)
        store.seed(DEVICE_ID, "ring", True)

        assert apply_power_report(coordinator, DEVICE_ID, "H60B0", _payload(LAMP_OFF)) == []
        assert store.is_on(DEVICE_ID, "ring") is True

    @pytest.mark.parametrize("sku", ["H6117", "H6046"])
    def test_other_skus_are_ignored(self, sku: str) -> None:
        coordinator = MagicMock()
        coordinator._govee_zone_state_registry = ZoneStateRegistry(coordinator)
        assert apply_power_report(coordinator, DEVICE_ID, sku, _payload(RIPPLE)) == []

    def test_a_payload_without_a_report_changes_nothing(self) -> None:
        coordinator = MagicMock()
        coordinator._govee_zone_state_registry = ZoneStateRegistry(coordinator)
        assert apply_power_report(coordinator, DEVICE_ID, "H60B0", _payload(BRIGHTNESS_PUSH)) == []


class TestCoordinatorHook:
    def test_raw_frames_reach_the_zone_registry(self) -> None:
        coordinator = MagicMock()
        coordinator._devices = {
            DEVICE_ID: GoveeDevice(
                device_id=DEVICE_ID,
                sku="H60B0",
                name="Uplighter",
                device_type=DEVICE_TYPE_LIGHT,
                capabilities=(GoveeCapability(type=CAPABILITY_ON_OFF, instance=INSTANCE_POWER, parameters={}),),
            )
        }
        coordinator._govee_zone_state_registry = ZoneStateRegistry(coordinator)
        listener = MagicMock()
        registry(coordinator).register(DEVICE_ID, "ring", listener)

        with patch("custom_components.govee.coordinator.async_dispatcher_send") as send:
            GoveeCoordinator._on_mqtt_raw_frames(coordinator, DEVICE_ID, _payload(RIPPLE_RING))

        listener.assert_called_once_with(True)
        assert registry(coordinator).is_on(DEVICE_ID, "ripple") is True
        # The H60B0 pushes no segment readback, so the segment signal stays quiet.
        send.assert_not_called()
