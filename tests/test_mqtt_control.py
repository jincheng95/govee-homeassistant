"""Tests for native MQTT control: payload builders + coordinator tier."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.govee.api.mqtt_control import (
    build_brightness_data,
    build_color_data,
    build_color_legacy_data,
    build_turn_data,
    command_to_mqtt,
)
from custom_components.govee.models.commands import (
    BrightnessCommand,
    ColorCommand,
    ColorTempCommand,
    PowerCommand,
    SceneCommand,
    SegmentColorCommand,
)
from custom_components.govee.models.state import RGBColor
from custom_components.govee.transport_health import TransportHealthTracker


# --------------------------------------------------------------------------- #
# Builders (S5-002)
# --------------------------------------------------------------------------- #
class TestBuilders:
    def test_turn_on_standard(self):
        assert build_turn_data(True, "H601F") == {"val": 1}

    def test_turn_off_standard(self):
        assert build_turn_data(False, "H601F") == {"val": 0}

    def test_turn_on_h5083_quirk(self):
        # H5080/H5083 use 17 (on) / 16 (off). Source: protocol-reference:922-928.
        assert build_turn_data(True, "H5083") == {"val": 17}

    def test_turn_off_h5083_quirk(self):
        assert build_turn_data(False, "H5083") == {"val": 16}

    def test_turn_on_h5080_quirk(self):
        assert build_turn_data(True, "H5080") == {"val": 17}

    def test_brightness(self):
        assert build_brightness_data(75) == {"val": 75}

    def test_color_colorwc(self):
        assert build_color_data(255, 0, 128) == {
            "color": {"r": 255, "g": 0, "b": 128},
            "colorTemInKelvin": 0,
        }

    def test_color_legacy(self):
        assert build_color_legacy_data(255, 0, 128) == {"r": 255, "g": 0, "b": 128}


# --------------------------------------------------------------------------- #
# command_to_mqtt dispatch (S5-002)
# --------------------------------------------------------------------------- #
class TestCommandToMqtt:
    def test_power(self):
        assert command_to_mqtt(PowerCommand(power_on=True), "H601F") == (
            "turn",
            {"val": 1},
            0,
        )

    def test_power_quirk_sku(self):
        assert command_to_mqtt(PowerCommand(power_on=False), "H5083") == (
            "turn",
            {"val": 16},
            0,
        )

    def test_brightness(self):
        assert command_to_mqtt(BrightnessCommand(brightness=50), "H601F") == (
            "brightness",
            {"val": 50},
            0,
        )

    def test_color(self):
        cmd = ColorCommand(color=RGBColor(r=10, g=20, b=30))
        assert command_to_mqtt(cmd, "H601F") == (
            "colorwc",
            {"color": {"r": 10, "g": 20, "b": 30}, "colorTemInKelvin": 0},
            0,
        )

    @pytest.mark.parametrize(
        "command",
        [
            ColorTempCommand(kelvin=3000),
            SceneCommand(scene_id=1, scene_name="X"),
            SegmentColorCommand(segment_indices=(0,), color=RGBColor(r=1, g=2, b=3)),
        ],
    )
    def test_non_capable_returns_none(self, command):
        assert command_to_mqtt(command, "H601F") is None


# --------------------------------------------------------------------------- #
# Coordinator _try_mqtt_command (S5-004)
# --------------------------------------------------------------------------- #
def _make_coordinator(*, topic="GD/topic", publish_ok=True):
    from custom_components.govee.coordinator import GoveeCoordinator

    coord = object.__new__(GoveeCoordinator)
    coord._transport = TransportHealthTracker()
    coord._mqtt_client = MagicMock()
    coord._mqtt_client.async_publish_command = AsyncMock(return_value=publish_ok)
    coord._ensure_device_topic = AsyncMock(return_value=topic)
    return coord


class TestTryMqttCommand:
    @pytest.mark.asyncio
    async def test_power_publishes(self):
        coord = _make_coordinator()
        result = await coord._try_mqtt_command(
            "dev1", "H601F", PowerCommand(power_on=True)
        )
        assert result is True
        coord._mqtt_client.async_publish_command.assert_awaited_once_with(
            "GD/topic", "turn", {"val": 1}, cmd_version=0
        )

    @pytest.mark.asyncio
    async def test_non_capable_skips_publish(self):
        coord = _make_coordinator()
        result = await coord._try_mqtt_command(
            "dev1", "H601F", ColorTempCommand(kelvin=3000)
        )
        assert result is False
        coord._mqtt_client.async_publish_command.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_topic_returns_false(self):
        coord = _make_coordinator(topic=None)
        result = await coord._try_mqtt_command(
            "dev1", "H601F", PowerCommand(power_on=True)
        )
        assert result is False
        coord._mqtt_client.async_publish_command.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_mqtt_client_returns_false(self):
        coord = _make_coordinator()
        coord._mqtt_client = None
        result = await coord._try_mqtt_command(
            "dev1", "H601F", PowerCommand(power_on=True)
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_publish_failure_records_transport_failure(self):
        coord = _make_coordinator(publish_ok=False)
        result = await coord._try_mqtt_command(
            "dev1", "H601F", PowerCommand(power_on=True)
        )
        assert result is False
        # A failure was recorded for the mqtt transport.
        health = coord._transport.get("dev1", "mqtt")
        assert health is not None
        assert health.last_failure_reason == "publish_failed"
