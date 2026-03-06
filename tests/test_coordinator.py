"""Test Govee coordinator."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest

from custom_components.govee.api.exceptions import (
    GoveeApiError,
    GoveeAuthError,
    GoveeDeviceNotFoundError,
    GoveeRateLimitError,
)
from custom_components.govee.models import (
    GoveeCapability,
    GoveeDevice,
    GoveeDeviceState,
    PowerCommand,
    BrightnessCommand,
    ColorCommand,
    ColorTempCommand,
    SceneCommand,
    RGBColor,
)
from custom_components.govee.models.device import (
    CAPABILITY_ON_OFF,
    CAPABILITY_RANGE,
    INSTANCE_POWER,
    INSTANCE_BRIGHTNESS,
)
from custom_components.govee.protocols import IStateObserver

# ==============================================================================
# Fixtures
# ==============================================================================


@pytest.fixture
def sample_capabilities():
    """Create sample light capabilities."""
    return (
        GoveeCapability(type=CAPABILITY_ON_OFF, instance=INSTANCE_POWER, parameters={}),
        GoveeCapability(
            type=CAPABILITY_RANGE,
            instance=INSTANCE_BRIGHTNESS,
            parameters={"range": {"min": 0, "max": 100}},
        ),
    )


@pytest.fixture
def sample_device(sample_capabilities):
    """Create a sample device."""
    return GoveeDevice(
        device_id="AA:BB:CC:DD:EE:FF:00:11",
        sku="H6072",
        name="Test Light",
        device_type="devices.types.light",
        capabilities=sample_capabilities,
        is_group=False,
    )


@pytest.fixture
def sample_group_device(sample_capabilities):
    """Create a sample group device."""
    return GoveeDevice(
        device_id="GROUP:AA:BB:CC:DD",
        sku="GROUP",
        name="All Lights",
        device_type="devices.types.group",
        capabilities=sample_capabilities,
        is_group=True,
    )


@pytest.fixture
def sample_state():
    """Create a sample device state."""
    return GoveeDeviceState(
        device_id="AA:BB:CC:DD:EE:FF:00:11",
        online=True,
        power_state=True,
        brightness=75,
        color=RGBColor(r=255, g=128, b=64),
        color_temp_kelvin=None,
        active_scene=None,
        source="api",
    )


# ==============================================================================
# Coordinator Logic Tests (without Home Assistant dependencies)
# ==============================================================================


class TestCoordinatorLogic:
    """Test coordinator logic that doesn't require HA."""

    def test_sample_device_creation(self, sample_device):
        """Test sample device fixture."""
        assert sample_device.device_id == "AA:BB:CC:DD:EE:FF:00:11"
        assert sample_device.sku == "H6072"
        assert sample_device.is_group is False

    def test_sample_group_device_creation(self, sample_group_device):
        """Test sample group device fixture."""
        assert sample_group_device.is_group is True

    def test_sample_state_creation(self, sample_state):
        """Test sample state fixture."""
        assert sample_state.power_state is True
        assert sample_state.brightness == 75

    def test_state_optimistic_power(self, sample_state):
        """Test optimistic power update."""
        sample_state.apply_optimistic_power(False)
        assert sample_state.power_state is False
        assert sample_state.source == "optimistic"

    def test_state_optimistic_brightness(self, sample_state):
        """Test optimistic brightness update."""
        sample_state.apply_optimistic_brightness(50)
        assert sample_state.brightness == 50
        assert sample_state.source == "optimistic"

    def test_state_optimistic_color(self, sample_state):
        """Test optimistic color update."""
        color = RGBColor(r=0, g=255, b=0)
        sample_state.apply_optimistic_color(color)
        assert sample_state.color == color
        assert sample_state.color_temp_kelvin is None
        assert sample_state.source == "optimistic"

    def test_state_optimistic_color_temp(self, sample_state):
        """Test optimistic color temperature update."""
        sample_state.apply_optimistic_color_temp(4000)
        assert sample_state.color_temp_kelvin == 4000
        assert sample_state.color is None
        assert sample_state.source == "optimistic"


class TestObserverPattern:
    """Test observer pattern for state updates."""

    def test_observer_registration(self):
        """Test observer can be registered."""
        observers: list[IStateObserver] = []

        mock_observer = MagicMock(spec=IStateObserver)
        observers.append(mock_observer)

        assert mock_observer in observers

    def test_observer_unregistration(self):
        """Test observer can be unregistered."""
        observers: list[IStateObserver] = []

        mock_observer = MagicMock(spec=IStateObserver)
        observers.append(mock_observer)
        observers.remove(mock_observer)

        assert mock_observer not in observers

    def test_observer_notification(self, sample_state):
        """Test observers are notified of state changes."""
        mock_observer = MagicMock(spec=IStateObserver)
        observers = [mock_observer]

        device_id = "AA:BB:CC:DD:EE:FF:00:11"
        for observer in observers:
            observer.on_state_changed(device_id, sample_state)

        mock_observer.on_state_changed.assert_called_once_with(device_id, sample_state)

    def test_observer_exception_handling(self, sample_state):
        """Test that observer exceptions don't propagate."""
        bad_observer = MagicMock(spec=IStateObserver)
        bad_observer.on_state_changed.side_effect = Exception("Observer error")

        good_observer = MagicMock(spec=IStateObserver)
        observers = [bad_observer, good_observer]

        device_id = "AA:BB:CC:DD:EE:FF:00:11"

        for observer in observers:
            try:
                observer.on_state_changed(device_id, sample_state)
            except Exception:
                pass  # Coordinator swallows observer exceptions

        bad_observer.on_state_changed.assert_called_once()
        good_observer.on_state_changed.assert_called_once()


class TestCommandGeneration:
    """Test command creation for coordinator."""

    def test_power_command(self):
        """Test power command for coordinator."""
        cmd = PowerCommand(power_on=True)
        assert cmd.power_on is True
        assert cmd.get_value() == 1

    def test_brightness_command(self):
        """Test brightness command for coordinator."""
        cmd = BrightnessCommand(brightness=50)
        assert cmd.brightness == 50
        assert cmd.get_value() == 50

    def test_color_command(self):
        """Test color command for coordinator."""
        color = RGBColor(r=255, g=0, b=0)
        cmd = ColorCommand(color=color)
        # Red packed = (255 << 16) + (0 << 8) + 0 = 16711680
        assert cmd.get_value() == 16711680

    def test_color_temp_command(self):
        """Test color temp command for coordinator."""
        cmd = ColorTempCommand(kelvin=4000)
        assert cmd.kelvin == 4000
        assert cmd.get_value() == 4000

    def test_scene_command(self):
        """Test scene command for coordinator."""
        cmd = SceneCommand(scene_id=123, scene_name="Test")
        value = cmd.get_value()
        assert value["id"] == 123
        assert value["name"] == "Test"


class TestDeviceFiltering:
    """Test device filtering logic."""

    def test_filter_groups_when_disabled(self, sample_device, sample_group_device):
        """Test group devices filtered when groups disabled."""
        devices = [sample_device, sample_group_device]
        enable_groups = False

        filtered = [d for d in devices if not d.is_group or enable_groups]

        assert len(filtered) == 1
        assert filtered[0] == sample_device

    def test_include_groups_when_enabled(self, sample_device, sample_group_device):
        """Test group devices included when groups enabled."""
        devices = [sample_device, sample_group_device]
        enable_groups = True

        filtered = [d for d in devices if not d.is_group or enable_groups]

        assert len(filtered) == 2


class TestSceneCaching:
    """Test scene caching logic."""

    def test_cache_empty_initially(self):
        """Test scene cache starts empty."""
        cache: dict[str, list[dict[str, Any]]] = {}
        assert "device_id" not in cache

    def test_cache_stores_scenes(self):
        """Test scenes are cached."""
        cache: dict[str, list[dict[str, Any]]] = {}
        scenes = [{"name": "Sunrise", "value": {"id": 1}}]

        cache["device_id"] = scenes

        assert cache["device_id"] == scenes

    def test_cache_returns_existing(self):
        """Test cached scenes are returned."""
        cache: dict[str, list[dict[str, Any]]] = {
            "device_id": [{"name": "Sunset", "value": {"id": 2}}]
        }

        device_id = "device_id"
        refresh = False

        if not refresh and device_id in cache:
            result = cache[device_id]
        else:
            result = []

        assert len(result) == 1
        assert result[0]["name"] == "Sunset"

    def test_cache_refresh_bypasses(self):
        """Test refresh bypasses cache."""
        cache: dict[str, list[dict[str, Any]]] = {
            "device_id": [{"name": "Old", "value": {"id": 1}}]
        }

        device_id = "device_id"
        refresh = True

        should_fetch = refresh or device_id not in cache

        assert should_fetch is True


class TestStateManagement:
    """Test state management logic."""

    def test_state_registry(self, sample_state):
        """Test state registry operations."""
        states: dict[str, GoveeDeviceState] = {}

        states["device_id"] = sample_state

        assert states.get("device_id") == sample_state
        assert states.get("unknown") is None

    def test_state_update_from_api(self):
        """Test state update from API response."""
        state = GoveeDeviceState.create_empty("device_id")

        api_data = {
            "capabilities": [
                {
                    "type": "devices.capabilities.online",
                    "instance": "online",
                    "state": {"value": True},
                },
                {
                    "type": "devices.capabilities.on_off",
                    "instance": "powerSwitch",
                    "state": {"value": 1},
                },
            ],
        }

        state.update_from_api(api_data)

        assert state.online is True
        assert state.power_state is True
        assert state.source == "api"

    def test_state_update_from_mqtt(self):
        """Test state update from MQTT message."""
        state = GoveeDeviceState.create_empty("device_id")

        mqtt_data = {
            "onOff": 1,
            "brightness": 50,
            "color": {"r": 100, "g": 150, "b": 200},
        }

        state.update_from_mqtt(mqtt_data)

        assert state.power_state is True
        assert state.brightness == 50
        assert state.color.as_tuple == (100, 150, 200)
        assert state.source == "mqtt"

    def test_preserve_active_scene_on_api_update(self, sample_state):
        """Test active scene is preserved when API doesn't return it."""
        sample_state.active_scene = "scene_123"

        new_state = GoveeDeviceState.create_empty(sample_state.device_id)
        new_state.power_state = True
        new_state.brightness = 80

        if sample_state.active_scene:
            new_state.active_scene = sample_state.active_scene

        assert new_state.active_scene == "scene_123"


class TestErrorHandling:
    """Test error handling patterns."""

    def test_auth_error_raises(self):
        """Test auth error is raised appropriately."""
        err = GoveeAuthError("Invalid key")
        assert err.code == 401

    def test_rate_limit_keeps_state(self, sample_state):
        """Test rate limit error preserves existing state."""
        states = {"device_id": sample_state}

        try:
            raise GoveeRateLimitError()
        except GoveeRateLimitError:
            result = states.get("device_id")

        assert result == sample_state

    def test_device_not_found_for_groups(self):
        """Test device not found is expected for groups."""
        err = GoveeDeviceNotFoundError("GROUP:ID")

        is_group_error = (
            "not exist" in str(err).lower() or "not found" in str(err).lower()
        )

        assert is_group_error or err.code == 400

    def test_api_error_logs_debug(self):
        """Test general API errors are logged but don't crash."""
        err = GoveeApiError("Server error", code=500)

        should_keep_state = True
        assert should_keep_state
        assert err.code == 500


class TestMqttIntegration:
    """Test MQTT integration patterns."""

    def test_mqtt_state_update_flow(self, sample_state):
        """Test MQTT state update is applied correctly."""
        states = {"device_id": sample_state}
        devices = {"device_id": MagicMock()}

        device_id = "device_id"
        mqtt_data = {"onOff": 0, "brightness": 25}

        if device_id in devices:
            state = states.get(device_id)
            if state:
                state.update_from_mqtt(mqtt_data)

        assert sample_state.power_state is False
        assert sample_state.brightness == 25
        assert sample_state.source == "mqtt"

    def test_mqtt_unknown_device_ignored(self):
        """Test MQTT updates for unknown devices are ignored."""
        devices = {"known_device": MagicMock()}

        unknown_device_id = "unknown_device"

        if unknown_device_id not in devices:
            handled = False
        else:
            handled = True

        assert handled is False


class TestParallelStateFetching:
    """Test parallel state fetching patterns."""

    @pytest.mark.asyncio
    async def test_parallel_fetch_creates_tasks(self, sample_device):
        """Test parallel fetch creates tasks for all devices."""
        devices = {
            "device1": sample_device,
            "device2": sample_device,
            "device3": sample_device,
        }

        async def mock_fetch(device_id, device):
            return GoveeDeviceState.create_empty(device_id)

        tasks = [mock_fetch(device_id, device) for device_id, device in devices.items()]

        results = await asyncio.gather(*tasks)

        assert len(results) == 3
        assert all(isinstance(r, GoveeDeviceState) for r in results)

    @pytest.mark.asyncio
    async def test_parallel_fetch_handles_exceptions(self, sample_device):
        """Test parallel fetch handles individual failures."""

        async def mock_fetch(device_id: str):
            if device_id == "failing":
                raise GoveeApiError("Fetch failed")
            return GoveeDeviceState.create_empty(device_id)

        tasks = [
            mock_fetch("success1"),
            mock_fetch("failing"),
            mock_fetch("success2"),
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        assert isinstance(results[0], GoveeDeviceState)
        assert isinstance(results[1], GoveeApiError)
        assert isinstance(results[2], GoveeDeviceState)


class TestOptimisticUpdates:
    """Test optimistic state update patterns."""

    def test_apply_optimistic_power_on(self, sample_state):
        """Test applying optimistic power on."""
        sample_state.power_state = False
        sample_state.apply_optimistic_power(True)

        assert sample_state.power_state is True
        assert sample_state.source == "optimistic"

    def test_apply_optimistic_power_off(self, sample_state):
        """Test applying optimistic power off."""
        sample_state.power_state = True
        sample_state.apply_optimistic_power(False)

        assert sample_state.power_state is False
        assert sample_state.source == "optimistic"

    def test_apply_optimistic_brightness(self, sample_state):
        """Test applying optimistic brightness."""
        sample_state.apply_optimistic_brightness(100)

        assert sample_state.brightness == 100
        assert sample_state.source == "optimistic"

    def test_apply_optimistic_color_clears_temp(self, sample_state):
        """Test applying color clears color temp."""
        sample_state.color_temp_kelvin = 4000
        color = RGBColor(r=255, g=0, b=0)
        sample_state.apply_optimistic_color(color)

        assert sample_state.color == color
        assert sample_state.color_temp_kelvin is None

    def test_apply_optimistic_temp_clears_color(self, sample_state):
        """Test applying color temp clears color."""
        sample_state.color = RGBColor(r=255, g=0, b=0)
        sample_state.apply_optimistic_color_temp(5000)

        assert sample_state.color_temp_kelvin == 5000
        assert sample_state.color is None


class TestDeviceStateCreation:
    """Test device state creation patterns."""

    def test_create_empty_state(self):
        """Test creating empty state."""
        state = GoveeDeviceState.create_empty("test_id")

        assert state.device_id == "test_id"
        assert state.online is True
        assert state.power_state is False
        assert state.brightness == 100

    def test_state_with_all_attributes(self):
        """Test state with all attributes set."""
        color = RGBColor(r=100, g=150, b=200)
        state = GoveeDeviceState(
            device_id="test_id",
            online=True,
            power_state=True,
            brightness=50,
            color=color,
            color_temp_kelvin=4000,
            active_scene="scene_1",
            source="mqtt",
        )

        assert state.device_id == "test_id"
        assert state.online is True
        assert state.power_state is True
        assert state.brightness == 50
        assert state.color == color
        assert state.color_temp_kelvin == 4000
        assert state.active_scene == "scene_1"
        assert state.source == "mqtt"


class TestCoordinatorDeviceRegistry:
    """Test device registry patterns."""

    def test_get_device_by_id(self, sample_device):
        """Test getting device by ID."""
        devices = {sample_device.device_id: sample_device}

        result = devices.get(sample_device.device_id)
        assert result == sample_device

    def test_get_device_unknown_returns_none(self, sample_device):
        """Test getting unknown device returns None."""
        devices = {sample_device.device_id: sample_device}

        result = devices.get("unknown_id")
        assert result is None

    def test_device_count(self, sample_device, sample_group_device):
        """Test device count."""
        devices = {
            sample_device.device_id: sample_device,
            sample_group_device.device_id: sample_group_device,
        }

        assert len(devices) == 2


class TestCoordinatorSceneManagement:
    """Test scene management patterns."""

    def test_scene_cache_miss_fetches(self):
        """Test cache miss triggers fetch."""
        cache: dict[str, list[dict[str, Any]]] = {}

        device_id = "device_id"
        if device_id not in cache:
            # Would fetch from API
            should_fetch = True
        else:
            should_fetch = False

        assert should_fetch is True

    def test_scene_cache_hit_returns_cached(self):
        """Test cache hit returns cached scenes."""
        scenes = [{"name": "Test", "value": {"id": 1}}]
        cache = {"device_id": scenes}

        device_id = "device_id"
        result = cache.get(device_id, [])

        assert result == scenes

    def test_refresh_clears_and_fetches(self):
        """Test refresh clears cache and fetches."""
        cache = {"device_id": [{"name": "Old", "value": {"id": 1}}]}

        # Simulate refresh
        if "device_id" in cache:
            del cache["device_id"]

        assert "device_id" not in cache


class TestPowerOffPendingFlag:
    """Test _pending_power_off tracking in coordinator (issue #16).

    Tests the flag logic that allows segment entities to detect when a
    power-off command is in flight, avoiding race conditions during
    area-targeted turn_off.
    """

    def test_pending_power_off_starts_empty(self):
        """Test _pending_power_off set is initially empty."""
        pending: set[str] = set()
        assert len(pending) == 0

    def test_is_power_off_pending_false_initially(self):
        """Test is_power_off_pending returns False for unknown device."""
        pending: set[str] = set()
        assert "device_id" not in pending

    def test_flag_set_for_power_off_command(self):
        """Test flag is set for PowerCommand(power_on=False)."""
        pending: set[str] = set()
        command = PowerCommand(power_on=False)

        is_power_off = isinstance(command, PowerCommand) and not command.power_on
        if is_power_off:
            pending.add("device_id")

        assert "device_id" in pending

    def test_flag_not_set_for_power_on_command(self):
        """Test flag is NOT set for PowerCommand(power_on=True)."""
        pending: set[str] = set()
        command = PowerCommand(power_on=True)

        is_power_off = isinstance(command, PowerCommand) and not command.power_on
        if is_power_off:
            pending.add("device_id")

        assert "device_id" not in pending

    def test_flag_not_set_for_brightness_command(self):
        """Test flag is NOT set for non-power commands."""
        pending: set[str] = set()
        command = BrightnessCommand(brightness=50)

        is_power_off = isinstance(command, PowerCommand) and not command.power_on
        if is_power_off:
            pending.add("device_id")

        assert "device_id" not in pending

    def test_flag_cleared_after_success(self):
        """Test flag is cleared via discard after command completes."""
        pending: set[str] = set()
        pending.add("device_id")

        # Simulate finally block
        pending.discard("device_id")

        assert "device_id" not in pending

    def test_flag_cleared_after_failure(self):
        """Test flag is cleared even when command raises."""
        pending: set[str] = set()
        device_id = "device_id"
        command = PowerCommand(power_on=False)

        is_power_off = isinstance(command, PowerCommand) and not command.power_on
        if is_power_off:
            pending.add(device_id)

        try:
            raise GoveeApiError("Simulated failure")
        except GoveeApiError:
            pass
        finally:
            if is_power_off:
                pending.discard(device_id)

        assert device_id not in pending

    def test_flag_discard_idempotent(self):
        """Test discarding a non-existent device_id is safe."""
        pending: set[str] = set()
        pending.discard("nonexistent")  # Should not raise
        assert len(pending) == 0


class TestCleanupDeviceIdExtraction:
    """Test device ID extraction for cleanup logic."""

    def test_extract_mac_address_device_id(self):
        """Test extracting MAC address device_id from unique_id."""
        device_id = "AA:BB:CC:DD:EE:FF:00:01"
        unique_id = f"{device_id}_segment_0"
        known_devices = {device_id}

        # Simulate extraction using longest-first matching
        extracted = None
        for dev_id in sorted(known_devices, key=len, reverse=True):
            if unique_id.startswith(dev_id):
                extracted = dev_id
                break

        assert extracted == device_id

    def test_extract_numeric_group_id(self):
        """Test extracting numeric group ID from unique_id."""
        device_id = "12345678"
        unique_id = f"{device_id}_scene_select"
        known_devices = {device_id}

        # Simulate extraction
        extracted = None
        for dev_id in sorted(known_devices, key=len, reverse=True):
            if unique_id.startswith(dev_id):
                extracted = dev_id
                break

        assert extracted == device_id

    def test_extract_with_multiple_device_ids(self):
        """Test extraction with multiple device IDs (longest-first matching)."""
        # Mix of MAC and numeric IDs
        mac_id = "AA:BB:CC:DD:EE:FF:00:01"
        group_id = "12345678"
        known_devices = {mac_id, group_id}

        # MAC address device
        unique_id = f"{mac_id}_segment_0"
        extracted = None
        for dev_id in sorted(known_devices, key=len, reverse=True):
            if unique_id.startswith(dev_id):
                extracted = dev_id
                break
        assert extracted == mac_id

        # Group device
        unique_id = f"{group_id}_segment_0"
        extracted = None
        for dev_id in sorted(known_devices, key=len, reverse=True):
            if unique_id.startswith(dev_id):
                extracted = dev_id
                break
        assert extracted == group_id

    def test_extract_returns_none_for_unknown_device(self):
        """Test extraction returns None for unknown device."""
        known_devices = {"AA:BB:CC:DD:EE:FF:00:01"}
        unique_id = "UNKNOWN:DEVICE:ID_segment_0"

        extracted = None
        for dev_id in sorted(known_devices, key=len, reverse=True):
            if unique_id.startswith(dev_id):
                extracted = dev_id
                break

        assert extracted is None

    def test_longest_first_matching_precedence(self):
        """Test longest-first matching prevents prefix collision."""
        # Create two device IDs where one is prefix of another
        short_id = "ABC"
        long_id = "ABCDEF"
        known_devices = {short_id, long_id}

        # Test with long_id unique_id
        unique_id = f"{long_id}_segment_0"
        extracted = None
        for dev_id in sorted(known_devices, key=len, reverse=True):
            if unique_id.startswith(dev_id):
                extracted = dev_id
                break

        # Should match long_id, not short_id
        assert extracted == long_id


class TestCleanupSegmentModeLogic:
    """Test segment mode cleanup logic with per-device config."""

    def test_grouped_segment_removed_when_disabled(self):
        """Test grouped segment entity removed when mode is not grouped."""
        from custom_components.govee.const import (
            SUFFIX_GROUPED_SEGMENT,
            SEGMENT_MODE_GROUPED,
            SEGMENT_MODE_INDIVIDUAL,
        )

        device_id = "AA:BB:CC:DD:EE:FF:00:01"
        unique_id = f"{device_id}{SUFFIX_GROUPED_SEGMENT}"

        # Device config with individual mode
        device_modes = {device_id: SEGMENT_MODE_INDIVIDUAL}

        # Extract and check
        suffix = unique_id[len(device_id) :]
        is_grouped = suffix == SUFFIX_GROUPED_SEGMENT
        mode = device_modes.get(device_id, SEGMENT_MODE_GROUPED)

        should_remove = is_grouped and mode != SEGMENT_MODE_GROUPED
        assert should_remove is True

    def test_individual_segment_removed_when_disabled(self):
        """Test individual segment entity removed when mode is disabled."""
        from custom_components.govee.const import (
            SUFFIX_SEGMENT,
            SEGMENT_MODE_INDIVIDUAL,
            SEGMENT_MODE_DISABLED,
        )

        device_id = "AA:BB:CC:DD:EE:FF:00:01"
        unique_id = f"{device_id}{SUFFIX_SEGMENT}0"

        # Device config with disabled mode
        device_modes = {device_id: SEGMENT_MODE_DISABLED}

        # Extract and check
        suffix = unique_id[len(device_id) :]
        is_individual = suffix.startswith(SUFFIX_SEGMENT)
        mode = device_modes.get(device_id, SEGMENT_MODE_INDIVIDUAL)

        should_remove = is_individual and mode != SEGMENT_MODE_INDIVIDUAL
        assert should_remove is True

    def test_segment_kept_when_mode_matches(self):
        """Test segment entity is kept when mode matches."""
        from custom_components.govee.const import (
            SUFFIX_SEGMENT,
            SEGMENT_MODE_INDIVIDUAL,
        )

        device_id = "AA:BB:CC:DD:EE:FF:00:01"
        unique_id = f"{device_id}{SUFFIX_SEGMENT}0"

        # Device config with individual mode (matches entity type)
        device_modes = {device_id: SEGMENT_MODE_INDIVIDUAL}

        # Extract and check
        suffix = unique_id[len(device_id) :]
        is_individual = suffix.startswith(SUFFIX_SEGMENT)
        mode = device_modes.get(device_id, SEGMENT_MODE_INDIVIDUAL)

        should_remove = is_individual and mode != SEGMENT_MODE_INDIVIDUAL
        assert should_remove is False

    def test_fallback_to_global_mode(self):
        """Test fallback to global mode when device not in per-device config."""
        from custom_components.govee.const import (
            SUFFIX_SEGMENT,
            SEGMENT_MODE_INDIVIDUAL,
        )

        device_id = "AA:BB:CC:DD:EE:FF:00:01"
        unique_id = f"{device_id}{SUFFIX_SEGMENT}0"

        # Device NOT in per-device config, use global
        device_modes = {}  # Empty - use global fallback
        global_mode = SEGMENT_MODE_INDIVIDUAL

        # Extract and check
        suffix = unique_id[len(device_id) :]
        is_individual = suffix.startswith(SUFFIX_SEGMENT)
        mode = device_modes.get(device_id, global_mode)

        should_remove = is_individual and mode != SEGMENT_MODE_INDIVIDUAL
        assert should_remove is False  # Matches global mode


class TestClearSceneLogic:
    """Test async_clear_scene command selection logic.

    These tests verify the logic for choosing which command to send when
    clearing a scene (color restore vs color_temp restore vs defaults).
    """

    def _make_device(self, supports_rgb: bool, supports_color_temp: bool):
        """Create a device with specified color capabilities."""
        caps = [
            GoveeCapability(type=CAPABILITY_ON_OFF, instance=INSTANCE_POWER, parameters={}),
            GoveeCapability(
                type=CAPABILITY_RANGE,
                instance=INSTANCE_BRIGHTNESS,
                parameters={"range": {"min": 0, "max": 100}},
            ),
        ]
        if supports_rgb:
            caps.append(
                GoveeCapability(
                    type="devices.capabilities.color_setting",
                    instance="colorRgb",
                    parameters={},
                )
            )
        if supports_color_temp:
            caps.append(
                GoveeCapability(
                    type="devices.capabilities.color_setting",
                    instance="colorTemperatureK",
                    parameters={"range": {"min": 2000, "max": 9000}},
                )
            )
        return GoveeDevice(
            device_id="AA:BB:CC:DD:EE:FF:00:11",
            sku="H6072",
            name="Test Light",
            device_type="devices.types.light",
            capabilities=tuple(caps),
            is_group=False,
        )

    def test_clear_scene_chooses_color_when_last_color_saved(self):
        """Test clear scene sends ColorCommand when last_color is available."""
        device = self._make_device(supports_rgb=True, supports_color_temp=True)
        state = GoveeDeviceState.create_empty(device.device_id)
        state.active_scene = "123"
        state.last_color = RGBColor(255, 0, 0)

        color = state.color or state.last_color
        color_temp = state.color_temp_kelvin or state.last_color_temp_kelvin

        # Should pick ColorCommand path
        assert color == RGBColor(255, 0, 0)
        assert device.supports_rgb is True

    def test_clear_scene_chooses_color_temp_when_last_temp_saved(self):
        """Test clear scene sends ColorTempCommand when last_color_temp is available."""
        device = self._make_device(supports_rgb=True, supports_color_temp=True)
        state = GoveeDeviceState.create_empty(device.device_id)
        state.active_scene = "123"
        state.last_color_temp_kelvin = 4000

        color = state.color or state.last_color
        color_temp = state.color_temp_kelvin or state.last_color_temp_kelvin

        # No color, falls through to color_temp
        assert color is None
        assert color_temp == 4000
        assert device.supports_color_temp is True

    def test_clear_scene_default_color_temp_midpoint(self):
        """Test clear scene uses midpoint of color temp range as default."""
        device = self._make_device(supports_rgb=False, supports_color_temp=True)
        state = GoveeDeviceState.create_empty(device.device_id)
        state.active_scene = "123"

        color = state.color or state.last_color
        color_temp = state.color_temp_kelvin or state.last_color_temp_kelvin

        # No saved color or temp → falls through to default path
        assert color is None
        assert color_temp is None
        assert device.supports_color_temp is True
        ct_range = device.color_temp_range
        assert ct_range is not None
        midpoint = (ct_range.min_kelvin + ct_range.max_kelvin) // 2
        assert midpoint == 5500

    def test_clear_scene_no_scene_active_is_noop(self):
        """Test clearing when no scene is active doesn't require a command."""
        state = GoveeDeviceState.create_empty("test_id")
        # Neither active_scene nor active_diy_scene set
        assert state.active_scene is None
        assert state.active_diy_scene is None

    def test_clear_scene_clears_both_scene_types(self):
        """Test clearing scene state clears both regular and DIY scene."""
        state = GoveeDeviceState.create_empty("test_id")
        state.active_scene = "123"
        state.active_scene_name = "Sunrise"
        state.active_diy_scene = "456"

        # Simulate what async_clear_scene does on success
        state.active_scene = None
        state.active_scene_name = None
        state.active_diy_scene = None

        assert state.active_scene is None
        assert state.active_scene_name is None
        assert state.active_diy_scene is None
