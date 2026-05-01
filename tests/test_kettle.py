"""Tests for kettle device support (issue #63 regression)."""

from __future__ import annotations

import pytest

from custom_components.govee.models import GoveeCapability, GoveeDevice
from custom_components.govee.models.device import (
    CAPABILITY_ON_OFF,
    CAPABILITY_TEMPERATURE_SETTING,
    CAPABILITY_WORK_MODE,
    DEVICE_TYPE_KETTLE,
    INSTANCE_POWER,
    INSTANCE_WORK_MODE,
)


@pytest.fixture
def mock_kettle_device() -> GoveeDevice:
    """Create a mock H717A Smart Kettle Pro device (real API shape from issue #63)."""
    return GoveeDevice(
        device_id="AA:BB:CC:DD:EE:FF:00:11",
        sku="H717A",
        name="Smart Kettle Pro",
        device_type=DEVICE_TYPE_KETTLE,
        capabilities=(
            GoveeCapability(
                type=CAPABILITY_ON_OFF,
                instance=INSTANCE_POWER,
                parameters={
                    "dataType": "ENUM",
                    "options": [
                        {"name": "on", "value": 1},
                        {"name": "off", "value": 0},
                    ],
                },
            ),
            GoveeCapability(
                type=CAPABILITY_TEMPERATURE_SETTING,
                instance="sliderTemperature",
                parameters={"dataType": "STRUCT", "fields": []},
            ),
            GoveeCapability(
                type=CAPABILITY_WORK_MODE,
                instance=INSTANCE_WORK_MODE,
                parameters={"dataType": "STRUCT", "fields": []},
            ),
        ),
        is_group=False,
    )


class TestKettleDetection:
    def test_is_kettle(self, mock_kettle_device):
        assert mock_kettle_device.is_kettle is True

    def test_kettle_is_not_a_light(self, mock_kettle_device):
        # Regression guard for issue #63: H717A was being routed to the
        # light platform pre-2026.4.5; after the issue-#54 filter it
        # silently dropped off entirely. It must now route to switch.
        assert mock_kettle_device.is_light_device is False

    def test_kettle_supports_power(self, mock_kettle_device):
        assert mock_kettle_device.supports_power is True
