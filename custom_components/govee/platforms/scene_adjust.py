"""Scene speed and direction selects (fork feature).

Both read the active scene's options from the app catalogue. Choosing one
rewrites the scene blob and uploads the scene again as raw frames, so the two
choices are kept together per device and sent together.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Final

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from ..api import raw_router
from ..api.protocol import DeviceProfile, GoveeProtocolError, scenes
from ..const import (
    CONF_ENABLE_SCENES,
    DEFAULT_ENABLE_SCENES,
    DOMAIN,
    SUFFIX_SCENE_DIRECTION_SELECT,
    SUFFIX_SCENE_SPEED_SELECT,
)
from ..coordinator import GoveeCoordinator
from ..entity import GoveeEntity
from ..models import GoveeDevice
from ..scene_catalogue import CatalogueScene, catalogue
from ..zone_state import profile_for

_LOGGER = logging.getLogger(__name__)

_CHOICES_ATTR: Final = "_govee_scene_adjust_choices"
_THREE_LEVELS: Final = ("Slow", "Medium", "Fast")


@dataclass
class _Choice:
    """What the user picked for the scene a device is running."""

    scene: str
    speed: int | None = None
    direction: int | None = None


def _choices(coordinator: GoveeCoordinator) -> dict[str, _Choice]:
    existing: dict[str, _Choice] | None = getattr(coordinator, _CHOICES_ATTR, None)
    if existing is None:
        existing = {}
        setattr(coordinator, _CHOICES_ATTR, existing)
    return existing


def speed_labels(count: int) -> list[str]:
    """Option labels for ``count`` speed levels, slowest first."""
    if count == len(_THREE_LEVELS):
        return list(_THREE_LEVELS)
    return [str(level) for level in range(1, count + 1)]


def async_scene_adjust_select_entities(coordinator: GoveeCoordinator, entry: ConfigEntry) -> list[SelectEntity]:
    """Build the speed and direction selects for SKUs that take scene uploads."""
    if entry.options.get(CONF_ENABLE_SCENES, DEFAULT_ENABLE_SCENES) is not True:
        return []
    entities: list[SelectEntity] = []
    for device in coordinator.devices.values():
        if device.is_group:
            continue
        profile = profile_for(device.sku)
        if profile is None or not profile.scene_upload:
            continue
        entities.append(GoveeSceneSpeedSelect(coordinator, device, profile))
        entities.append(GoveeSceneDirectionSelect(coordinator, device, profile))
    if entities:
        _LOGGER.debug("Created %d Govee scene adjustment select entities", len(entities))
    return entities


async def async_start_scene_locally(
    coordinator: GoveeCoordinator, device: GoveeDevice, scene_id: int, scene_name: str
) -> bool:
    """Upload a catalogue scene as raw frames instead of asking the cloud.

    Declined, so the caller falls back to the cloud command, when the SKU
    takes no scene uploads, the light is not known to be on, the catalogue has
    no blob for the scene, or no raw pipe accepts the frames.

    Returns:
        True when the scene was uploaded and the state updated.
    """
    profile = profile_for(device.sku)
    if profile is None or not profile.scene_upload:
        return False
    state = coordinator.get_state(device.device_id)
    if state is None or state.power_state is not True:
        return False
    scene = catalogue(coordinator).scene(device.sku, scene_name)
    if scene is None:
        return False
    try:
        frames = scenes.scene_frames(scene.blob, scene.code)
    except GoveeProtocolError:
        return False
    if not await raw_router.async_route_frames(
        coordinator, device.device_id, device.sku, profile, frames, what="scene start"
    ):
        return False
    state.apply_optimistic_scene(str(scene_id), scene_name)
    coordinator.async_update_listeners()
    return True


class _GoveeSceneAdjustSelect(GoveeEntity, SelectEntity):
    """Shared scene lookup, choice store and upload."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: GoveeCoordinator, device: GoveeDevice, profile: DeviceProfile) -> None:
        super().__init__(coordinator, device)
        self._profile = profile

    async def async_added_to_hass(self) -> None:
        """Load the catalogue in the background; setup does not wait on it."""
        await super().async_added_to_hass()
        self.hass.async_create_task(self._async_load_catalogue())

    async def _async_load_catalogue(self) -> None:
        if await catalogue(self.coordinator).async_load(self._device.sku):
            self.async_write_ha_state()

    def _scene(self) -> CatalogueScene | None:
        """The catalogue entry of the running scene, when it can be adjusted."""
        state = self.coordinator.get_state(self._device_id)
        if state is None or state.power_state is False:
            return None
        scene = catalogue(self.coordinator).scene(self._device.sku, state.active_scene_name)
        if scene is None or scene.adjust is None:
            return None
        return scene

    def _choice(self, scene: CatalogueScene) -> _Choice:
        """This device's choice, reset whenever the running scene changes."""
        store = _choices(self.coordinator)
        choice = store.get(self._device_id)
        if choice is None or choice.scene != scene.name:
            choice = _Choice(scene=scene.name)
            store[self._device_id] = choice
        return choice

    @property
    def available(self) -> bool:
        """Unavailable unless the running scene offers this adjustment."""
        return super().available and bool(self.options)

    def _unknown_option(self, option: str) -> ServiceValidationError:
        return ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="unknown_option",
            translation_placeholders={"option": option, "device": self._device.name},
        )

    async def _async_upload(self, scene: CatalogueScene, choice: _Choice) -> None:
        adjust = scene.adjust
        if adjust is None:  # pragma: no cover - _scene() filtered on this
            raise self._command_failed()
        try:
            blob = scenes.adjust_blob(scene.blob, adjust, speed=choice.speed, direction=choice.direction)
            frames = scenes.scene_frames(blob, scene.code)
        except GoveeProtocolError as err:
            raise HomeAssistantError(f"{self._device.name}: scene '{scene.name}' cannot be adjusted ({err})") from err
        if not await raw_router.async_route_frames(
            self.coordinator, self._device_id, self._device.sku, self._profile, frames, what="scene adjustment"
        ):
            raise self._command_failed()
        self.async_write_ha_state()


class GoveeSceneSpeedSelect(_GoveeSceneAdjustSelect):
    """Speed level of the running scene."""

    _attr_icon = "mdi:speedometer"

    def __init__(self, coordinator: GoveeCoordinator, device: GoveeDevice, profile: DeviceProfile) -> None:
        super().__init__(coordinator, device, profile)
        self._attr_unique_id = f"{device.device_id}{SUFFIX_SCENE_SPEED_SELECT}"
        self._attr_name = "Scene speed"

    @property
    def options(self) -> list[str]:
        scene = self._scene()
        if scene is None or scene.adjust is None:
            return []
        return speed_labels(len(scene.adjust.speeds))

    @property
    def current_option(self) -> str | None:
        scene = self._scene()
        if scene is None or scene.adjust is None:
            return None
        index = self._choice(scene).speed
        if index is None:
            index = scene.adjust.default_speed
        labels = speed_labels(len(scene.adjust.speeds))
        return labels[index] if index is not None and index < len(labels) else None

    async def async_select_option(self, option: str) -> None:
        scene = self._scene()
        options = self.options
        if scene is None or option not in options:
            raise self._unknown_option(option)
        choice = self._choice(scene)
        previous = choice.speed
        choice.speed = options.index(option)
        try:
            await self._async_upload(scene, choice)
        except HomeAssistantError:
            choice.speed = previous
            raise


class GoveeSceneDirectionSelect(_GoveeSceneAdjustSelect):
    """Direction of the running scene."""

    _attr_icon = "mdi:rotate-3d-variant"

    def __init__(self, coordinator: GoveeCoordinator, device: GoveeDevice, profile: DeviceProfile) -> None:
        super().__init__(coordinator, device, profile)
        self._attr_unique_id = f"{device.device_id}{SUFFIX_SCENE_DIRECTION_SELECT}"
        self._attr_name = "Scene direction"

    @property
    def options(self) -> list[str]:
        scene = self._scene()
        if scene is None or scene.adjust is None:
            return []
        return [scenes.direction_name(direction) for direction in scene.adjust.directions]

    @property
    def current_option(self) -> str | None:
        scene = self._scene()
        if scene is None or scene.adjust is None:
            return None
        index = self._choice(scene).direction
        if index is None:
            index = scene.adjust.default_direction
        options = self.options
        return options[index] if index is not None and index < len(options) else None

    async def async_select_option(self, option: str) -> None:
        scene = self._scene()
        options = self.options
        if scene is None or option not in options:
            raise self._unknown_option(option)
        choice = self._choice(scene)
        previous = choice.direction
        choice.direction = options.index(option)
        try:
            await self._async_upload(scene, choice)
        except HomeAssistantError:
            choice.direction = previous
            raise
