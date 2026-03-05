"""Test Govee light entity effect and color mode support."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.light import ColorMode

from custom_components.govee.light import GoveeLightEntity
from custom_components.govee.models import (
    GoveeCapability,
    GoveeDevice,
    RGBColor,
    SceneCommand,
)
from custom_components.govee.models.device import (
    CAPABILITY_ON_OFF,
    CAPABILITY_RANGE,
    INSTANCE_BRIGHTNESS,
    INSTANCE_POWER,
)


@pytest.fixture
def mock_coordinator(mock_light_device, mock_device_state, mock_scenes):
    """Create a mock coordinator for light entity tests."""
    coordinator = MagicMock()
    coordinator.devices = {mock_light_device.device_id: mock_light_device}
    coordinator.get_state.return_value = mock_device_state
    coordinator.async_get_scenes = AsyncMock(return_value=mock_scenes)
    coordinator.async_control_device = AsyncMock(return_value=True)
    coordinator.data = {mock_light_device.device_id: mock_device_state}
    return coordinator


@pytest.fixture
def mock_coordinator_no_scenes(mock_light_device, mock_device_state):
    """Create a mock coordinator with no scenes."""
    coordinator = MagicMock()
    coordinator.devices = {mock_light_device.device_id: mock_light_device}
    coordinator.get_state.return_value = mock_device_state
    coordinator.async_get_scenes = AsyncMock(return_value=[])
    coordinator.async_control_device = AsyncMock(return_value=True)
    coordinator.data = {mock_light_device.device_id: mock_device_state}
    return coordinator


class TestLightEffectSupport:
    """Test effect support on the light entity."""

    def test_effect_feature_enabled_when_scenes_supported_and_enabled(
        self, mock_coordinator, mock_light_device
    ):
        """Test EFFECT feature flag is set when device supports scenes and scenes enabled."""
        from homeassistant.components.light import LightEntityFeature

        entity = GoveeLightEntity(
            mock_coordinator, mock_light_device, enable_scenes=True
        )
        assert entity.supported_features & LightEntityFeature.EFFECT

    def test_effect_feature_disabled_when_scenes_disabled(
        self, mock_coordinator, mock_light_device
    ):
        """Test EFFECT feature flag is NOT set when scenes are disabled in config."""
        from homeassistant.components.light import LightEntityFeature

        entity = GoveeLightEntity(
            mock_coordinator, mock_light_device, enable_scenes=False
        )
        assert not (entity.supported_features & LightEntityFeature.EFFECT)

    def test_effect_feature_disabled_for_device_without_scenes(
        self, mock_coordinator, mock_plug_device
    ):
        """Test EFFECT feature flag is NOT set for devices without scene support."""
        from homeassistant.components.light import LightEntityFeature

        entity = GoveeLightEntity(
            mock_coordinator, mock_plug_device, enable_scenes=True
        )
        assert not (entity.supported_features & LightEntityFeature.EFFECT)

    def test_effect_list_empty_before_added_to_hass(
        self, mock_coordinator, mock_light_device
    ):
        """Test effect_list is None before async_added_to_hass populates it."""
        entity = GoveeLightEntity(
            mock_coordinator, mock_light_device, enable_scenes=True
        )
        assert entity.effect_list is None

    def test_build_effect_mapping(
        self, mock_coordinator, mock_light_device, mock_scenes
    ):
        """Test _build_effect_mapping populates effect names and mappings."""
        entity = GoveeLightEntity(
            mock_coordinator, mock_light_device, enable_scenes=True
        )
        entity._build_effect_mapping(mock_scenes)

        assert entity.effect_list == ["Sunrise", "Sunset", "Party", "Movie"]
        assert entity._effect_to_scene["Sunrise"] == (1, "Sunrise")
        assert entity._scene_id_to_effect["1"] == "Sunrise"
        assert entity._scene_id_to_effect["4"] == "Movie"

    def test_build_effect_mapping_handles_duplicates(
        self, mock_coordinator, mock_light_device
    ):
        """Test duplicate scene names get deduped with counter."""
        scenes = [
            {"name": "Rainbow", "value": {"id": 1}},
            {"name": "Rainbow", "value": {"id": 2}},
            {"name": "Rainbow", "value": {"id": 3}},
        ]
        entity = GoveeLightEntity(
            mock_coordinator, mock_light_device, enable_scenes=True
        )
        entity._build_effect_mapping(scenes)

        assert entity.effect_list == ["Rainbow", "Rainbow (1)", "Rainbow (2)"]
        assert entity._effect_to_scene["Rainbow"] == (1, "Rainbow")
        assert entity._effect_to_scene["Rainbow (1)"] == (2, "Rainbow")
        assert entity._effect_to_scene["Rainbow (2)"] == (3, "Rainbow")

    def test_effect_returns_active_scene_name(
        self, mock_coordinator, mock_light_device, mock_scenes, mock_device_state
    ):
        """Test effect property returns active scene name from mapping."""
        entity = GoveeLightEntity(
            mock_coordinator, mock_light_device, enable_scenes=True
        )
        entity._build_effect_mapping(mock_scenes)

        # Set active scene
        mock_device_state.active_scene = "2"
        mock_device_state.active_scene_name = "Sunset"

        # Mock device_state property
        with patch.object(
            type(entity),
            "device_state",
            new_callable=lambda: property(lambda self: mock_device_state),
        ):
            assert entity.effect == "Sunset"

    def test_effect_returns_none_when_no_scene_active(
        self, mock_coordinator, mock_light_device, mock_scenes, mock_device_state
    ):
        """Test effect property returns None when no scene is active."""
        entity = GoveeLightEntity(
            mock_coordinator, mock_light_device, enable_scenes=True
        )
        entity._build_effect_mapping(mock_scenes)

        mock_device_state.active_scene = None
        mock_device_state.active_scene_name = None

        with patch.object(
            type(entity),
            "device_state",
            new_callable=lambda: property(lambda self: mock_device_state),
        ):
            assert entity.effect is None

    def test_effect_falls_back_to_scene_name(
        self, mock_coordinator, mock_light_device, mock_scenes, mock_device_state
    ):
        """Test effect falls back to active_scene_name if ID not in mapping."""
        entity = GoveeLightEntity(
            mock_coordinator, mock_light_device, enable_scenes=True
        )
        entity._build_effect_mapping(mock_scenes)

        # Set active scene to an ID not in our mapping
        mock_device_state.active_scene = "999"
        mock_device_state.active_scene_name = "Unknown Scene"

        with patch.object(
            type(entity),
            "device_state",
            new_callable=lambda: property(lambda self: mock_device_state),
        ):
            assert entity.effect == "Unknown Scene"

    @pytest.mark.asyncio
    async def test_turn_on_with_effect_sends_scene_command(
        self, mock_coordinator, mock_light_device, mock_scenes
    ):
        """Test async_turn_on with effect sends SceneCommand."""
        entity = GoveeLightEntity(
            mock_coordinator, mock_light_device, enable_scenes=True
        )
        entity._build_effect_mapping(mock_scenes)

        await entity.async_turn_on(effect="Sunrise")

        mock_coordinator.async_control_device.assert_called_once()
        call_args = mock_coordinator.async_control_device.call_args
        assert call_args[0][0] == mock_light_device.device_id
        cmd = call_args[0][1]
        assert isinstance(cmd, SceneCommand)
        assert cmd.scene_id == 1
        assert cmd.scene_name == "Sunrise"

    @pytest.mark.asyncio
    async def test_turn_on_with_unknown_effect_logs_warning(
        self, mock_coordinator, mock_light_device, mock_scenes
    ):
        """Test async_turn_on with unknown effect logs warning."""
        entity = GoveeLightEntity(
            mock_coordinator, mock_light_device, enable_scenes=True
        )
        entity._build_effect_mapping(mock_scenes)

        await entity.async_turn_on(effect="NonExistent")

        # No command should be sent
        mock_coordinator.async_control_device.assert_not_called()

    @pytest.mark.asyncio
    async def test_turn_on_with_effect_returns_early(
        self, mock_coordinator, mock_light_device, mock_scenes
    ):
        """Test async_turn_on with effect returns early without power command."""
        entity = GoveeLightEntity(
            mock_coordinator, mock_light_device, enable_scenes=True
        )
        entity._build_effect_mapping(mock_scenes)

        # Entity says light is off
        with patch.object(
            type(entity), "is_on", new_callable=lambda: property(lambda self: False)
        ):
            await entity.async_turn_on(effect="Sunset")

        # Only one call: the scene command. No separate power command.
        assert mock_coordinator.async_control_device.call_count == 1
        cmd = mock_coordinator.async_control_device.call_args[0][1]
        assert isinstance(cmd, SceneCommand)

    @pytest.mark.asyncio
    async def test_async_added_to_hass_loads_scenes(
        self, mock_coordinator, mock_light_device, mock_scenes
    ):
        """Test async_added_to_hass loads scenes and builds effect mapping."""
        entity = GoveeLightEntity(
            mock_coordinator, mock_light_device, enable_scenes=True
        )

        # Patch super().async_added_to_hass and async_get_last_state
        with (
            patch.object(
                GoveeLightEntity.__bases__[0],
                "async_added_to_hass",
                new_callable=AsyncMock,
            ),
            patch.object(
                entity,
                "async_get_last_state",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            await entity.async_added_to_hass()

        mock_coordinator.async_get_scenes.assert_called_once_with(
            mock_light_device.device_id
        )
        assert entity.effect_list == ["Sunrise", "Sunset", "Party", "Movie"]

    @pytest.mark.asyncio
    async def test_async_added_to_hass_skips_scenes_when_disabled(
        self, mock_coordinator, mock_light_device
    ):
        """Test async_added_to_hass does NOT load scenes when disabled."""
        entity = GoveeLightEntity(
            mock_coordinator, mock_light_device, enable_scenes=False
        )

        with (
            patch.object(
                GoveeLightEntity.__bases__[0],
                "async_added_to_hass",
                new_callable=AsyncMock,
            ),
            patch.object(
                entity,
                "async_get_last_state",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            await entity.async_added_to_hass()

        mock_coordinator.async_get_scenes.assert_not_called()
        assert entity.effect_list is None

    @pytest.mark.asyncio
    async def test_async_added_to_hass_skips_scenes_for_group(
        self, mock_coordinator, mock_group_device
    ):
        """Test async_added_to_hass does NOT load scenes for group devices."""
        entity = GoveeLightEntity(
            mock_coordinator, mock_group_device, enable_scenes=True
        )

        with (
            patch.object(
                GoveeLightEntity.__bases__[0],
                "async_added_to_hass",
                new_callable=AsyncMock,
            ),
            patch.object(
                entity,
                "async_get_last_state",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            await entity.async_added_to_hass()

        mock_coordinator.async_get_scenes.assert_not_called()
        assert entity.effect_list is None


@pytest.fixture
def brightness_only_capabilities():
    """Create capabilities for a brightness-only device (no color)."""
    return (
        GoveeCapability(type=CAPABILITY_ON_OFF, instance=INSTANCE_POWER, parameters={}),
        GoveeCapability(
            type=CAPABILITY_RANGE,
            instance=INSTANCE_BRIGHTNESS,
            parameters={"range": {"min": 0, "max": 100}},
        ),
    )


@pytest.fixture
def mock_brightness_device(brightness_only_capabilities):
    """Create a mock brightness-only device."""
    return GoveeDevice(
        device_id="AA:BB:CC:DD:EE:FF:00:99",
        sku="H6000",
        name="Simple Light",
        device_type="devices.types.light",
        capabilities=brightness_only_capabilities,
        is_group=False,
    )


class TestColorMode:
    """Test color_mode property returns valid modes."""

    def test_color_mode_rgb_when_color_in_state(
        self, mock_coordinator, mock_light_device, mock_device_state
    ):
        """Test color_mode returns RGB when state has color set."""
        mock_device_state.color = RGBColor(r=255, g=0, b=0)
        mock_device_state.color_temp_kelvin = None

        entity = GoveeLightEntity(mock_coordinator, mock_light_device, enable_scenes=True)
        assert entity.color_mode == ColorMode.RGB

    def test_color_mode_color_temp_when_temp_in_state(
        self, mock_coordinator, mock_light_device, mock_device_state
    ):
        """Test color_mode returns COLOR_TEMP when state has color_temp set."""
        mock_device_state.color = None
        mock_device_state.color_temp_kelvin = 4000

        entity = GoveeLightEntity(mock_coordinator, mock_light_device, enable_scenes=True)
        assert entity.color_mode == ColorMode.COLOR_TEMP

    def test_color_mode_color_temp_takes_priority(
        self, mock_coordinator, mock_light_device, mock_device_state
    ):
        """Test COLOR_TEMP takes priority when both color and color_temp are set."""
        mock_device_state.color = RGBColor(r=0, g=0, b=0)
        mock_device_state.color_temp_kelvin = 6667

        entity = GoveeLightEntity(mock_coordinator, mock_light_device, enable_scenes=True)
        assert entity.color_mode == ColorMode.COLOR_TEMP

    def test_color_mode_valid_when_no_state(
        self, mock_coordinator, mock_light_device
    ):
        """Test color_mode returns a valid mode when device_state is None."""
        mock_coordinator.get_state.return_value = None

        entity = GoveeLightEntity(mock_coordinator, mock_light_device, enable_scenes=True)
        assert entity.color_mode in entity.supported_color_modes

    def test_color_mode_valid_when_empty_state(
        self, mock_coordinator, mock_light_device, mock_device_state_off
    ):
        """Test color_mode returns valid mode when color and color_temp are both None."""
        mock_coordinator.get_state.return_value = mock_device_state_off

        entity = GoveeLightEntity(mock_coordinator, mock_light_device, enable_scenes=True)
        assert entity.color_mode in entity.supported_color_modes

    def test_color_mode_onoff_for_onoff_only_device(
        self, mock_coordinator, mock_plug_device
    ):
        """Test color_mode returns ONOFF for device with only power control."""
        mock_coordinator.get_state.return_value = None

        entity = GoveeLightEntity(mock_coordinator, mock_plug_device, enable_scenes=False)
        assert entity.color_mode == ColorMode.ONOFF
        assert entity.supported_color_modes == {ColorMode.ONOFF}

    def test_color_mode_brightness_for_brightness_only_device(
        self, mock_coordinator, mock_brightness_device
    ):
        """Test color_mode returns BRIGHTNESS for brightness-only device."""
        mock_coordinator.get_state.return_value = None

        entity = GoveeLightEntity(
            mock_coordinator, mock_brightness_device, enable_scenes=False
        )
        assert entity.color_mode == ColorMode.BRIGHTNESS
        assert entity.supported_color_modes == {ColorMode.BRIGHTNESS}

    def test_color_mode_always_in_supported_modes(
        self, mock_coordinator, mock_light_device, mock_device_state
    ):
        """Test color_mode is always in supported_color_modes across state transitions."""
        entity = GoveeLightEntity(mock_coordinator, mock_light_device, enable_scenes=True)

        # With RGB color
        mock_device_state.color = RGBColor(r=255, g=0, b=0)
        mock_device_state.color_temp_kelvin = None
        assert entity.color_mode in entity.supported_color_modes

        # With color temp
        mock_device_state.color = None
        mock_device_state.color_temp_kelvin = 5000
        assert entity.color_mode in entity.supported_color_modes

        # With no state
        mock_coordinator.get_state.return_value = None
        assert entity.color_mode in entity.supported_color_modes

        # With empty state
        mock_device_state.color = None
        mock_device_state.color_temp_kelvin = None
        mock_coordinator.get_state.return_value = mock_device_state
        assert entity.color_mode in entity.supported_color_modes
