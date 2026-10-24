"""Issue #243: an H5054 whose device ID happens to be all digits is not a group.

Group IDs are short decimal numbers (e.g. ``11825917``); device IDs are 16-18
hex characters, and one with no A-F letters is all digits. Treating it as a
group hid the sensor with groups off and dropped its leak, battery and
connectivity entities with groups on.
"""

from __future__ import annotations

import pytest

from custom_components.govee.api.lan_client import correlate_scan
from custom_components.govee.models.device import GoveeDevice, looks_like_group_id

ALL_DIGIT_SENSOR_ID = "123456789012345678"
GROUP_ID = "11825917"


def _device(device_id: str, device_type: str, sku: str = "H5054") -> GoveeDevice:
    return GoveeDevice.from_api_response(
        {"device": device_id, "sku": sku, "deviceName": "Leak", "type": device_type, "capabilities": []}
    )


class TestLooksLikeGroupId:
    @pytest.mark.parametrize(
        ("device_id", "device_type", "expected"),
        [
            (ALL_DIGIT_SENSOR_ID, "devices.types.sensor", False),
            (ALL_DIGIT_SENSOR_ID, "", False),
            (GROUP_ID, "devices.types.light", True),
            (GROUP_ID, "", True),
            ("03:9C:DC:06:75:4B:10:7C", "devices.types.light", False),
            ("03:9C:DC:06:75:4B:10:7C", "devices.types.group", True),
            ("ABCDEF", "devices.types.same_mode_group", True),
            ("12345678901", "", True),
            ("123456789012", "", False),
        ],
    )
    def test_classification(self, device_id: str, device_type: str, expected: bool) -> None:
        assert looks_like_group_id(device_id, device_type) is expected


class TestDiscovery:
    def test_all_digit_h5054_is_a_device(self) -> None:
        device = _device(ALL_DIGIT_SENSOR_ID, "devices.types.sensor")
        assert device.is_group is False

    def test_short_numeric_id_is_still_a_group(self) -> None:
        assert _device(GROUP_ID, "devices.types.light", sku="SameModeGroup").is_group is True


class TestLanScan:
    def test_all_digit_device_id_is_correlated(self) -> None:
        records = [{"device": ALL_DIGIT_SENSOR_ID, "ip": "192.168.1.20", "sku": "H6008"}]
        matched, unmatched = correlate_scan(records, [ALL_DIGIT_SENSOR_ID, GROUP_ID], now=1.0)
        assert list(matched) == [ALL_DIGIT_SENSOR_ID]
        assert unmatched == []

    def test_group_ids_are_still_skipped(self) -> None:
        records = [{"device": GROUP_ID, "ip": "192.168.1.21", "sku": "H6008"}]
        matched, unmatched = correlate_scan(records, [GROUP_ID], now=1.0)
        assert matched == {}
        assert len(unmatched) == 1
