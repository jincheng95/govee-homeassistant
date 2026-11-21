"""Scene settings: speed, direction, palettes and reset (fork feature).

Every entity here edits the scene Home Assistant last started. The settings
live per device and per scene code in one store, and any change uploads the
catalogue blob with all of that scene's settings written in, so one setting
never undoes another. Only the active scene's settings survive a restart,
through each entity's restore data.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from typing import Any, Final

from homeassistant.components.button import ButtonEntity
from homeassistant.components.select import SelectEntity
from homeassistant.components.text import TextEntity, TextMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.restore_state import ExtraStoredData, RestoredExtraData, RestoreEntity

from ..api import raw_router
from ..api.protocol import DeviceProfile, GoveeProtocolError, scenes
from ..const import (
    CONF_ENABLE_SCENES,
    DEFAULT_ENABLE_SCENES,
    DOMAIN,
    SCENE_PALETTE_LAYERS,
    SUFFIX_SCENE_DIRECTION_SELECT,
    SUFFIX_SCENE_PALETTE_TEXT,
    SUFFIX_SCENE_RESET_BUTTON,
    SUFFIX_SCENE_SPEED_SELECT,
)
from ..coordinator import GoveeCoordinator
from ..entity import GoveeEntity
from ..models import GoveeDevice
from ..scene_catalogue import CatalogueScene, catalogue
from ..upstream_access import scene_select_names
from ..zone_state import profile_for

_LOGGER = logging.getLogger(__name__)

_STORE_ATTR: Final = "_govee_scene_adjust_choices"
_THREE_LEVELS: Final = ("Slow", "Medium", "Fast")

_HEX_COLOUR: Final = re.compile(r"#[0-9a-fA-F]{6}")
_PALETTE_SEPARATOR: Final = re.compile(r"\s*,\s*")


@dataclass
class SceneSettings:
    """The user's settings for one scene; None and absent mean the catalogue's."""

    speed: int | None = None
    direction: int | None = None
    palettes: dict[int, tuple[scenes.Colour, ...]] = field(default_factory=dict)
    """Colours by 0-based layer index."""


class SceneSettingsStore:
    """Scene settings by device and scene code, held for the session."""

    def __init__(self) -> None:
        self._settings: dict[str, dict[int, SceneSettings]] = {}

    def get(self, device_id: str, code: int) -> SceneSettings:
        """The settings for one scene, created empty on first use."""
        return self._settings.setdefault(device_id, {}).setdefault(code, SceneSettings())

    def put(self, device_id: str, code: int, settings: SceneSettings) -> None:
        self._settings.setdefault(device_id, {})[code] = settings


def settings_store(coordinator: GoveeCoordinator) -> SceneSettingsStore:
    """The coordinator's scene settings store, built on first use."""
    existing: SceneSettingsStore | None = getattr(coordinator, _STORE_ATTR, None)
    if existing is None:
        existing = SceneSettingsStore()
        setattr(coordinator, _STORE_ATTR, existing)
    return existing


def speed_labels(count: int) -> list[str]:
    """Option labels for ``count`` speed levels, slowest first."""
    if count == len(_THREE_LEVELS):
        return list(_THREE_LEVELS)
    return [str(level) for level in range(1, count + 1)]


def format_palette(colours: tuple[scenes.Colour, ...]) -> str:
    """Lowercase ``#rrggbb`` colours separated by ``", "``."""
    return ", ".join(f"#{r:02x}{g:02x}{b:02x}" for r, g, b in colours)


def scene_blob(scene: CatalogueScene, settings: SceneSettings) -> bytes:
    """The catalogue blob with every setting that fits the scene written in.

    A stored setting the scene does not offer is skipped, so a restored value
    from an older catalogue cannot break the upload.

    Raises:
        GoveeProtocolError: The catalogue blob is malformed.
    """
    blob = scene.blob
    adjust = scene.adjust
    if adjust is not None:
        speed = settings.speed if settings.speed is not None and 0 <= settings.speed < adjust.speed_levels else None
        direction = settings.direction
        if direction is not None and not 0 <= direction < len(adjust.directions):
            direction = None
        if speed is not None or direction is not None:
            blob = scenes.adjust_blob(blob, adjust, speed=speed, direction=direction)
    if settings.palettes:
        located = scenes.scene_palettes(blob)
        wanted = {
            layer: colours
            for layer, colours in settings.palettes.items()
            if 0 <= layer < len(located) and located[layer] is not None
        }
        if wanted:
            blob = scenes.palette_blob(blob, wanted)
    return blob


def adjustable(scene: CatalogueScene) -> bool:
    """Whether the scene offers any setting these entities can change."""
    return scene.adjust is not None or any(palette is not None for palette in scenes.scene_palettes(scene.blob))


def _scene_upload_devices(
    coordinator: GoveeCoordinator, entry: ConfigEntry
) -> Iterator[tuple[GoveeDevice, DeviceProfile]]:
    if entry.options.get(CONF_ENABLE_SCENES, DEFAULT_ENABLE_SCENES) is not True:
        return
    for device in coordinator.devices.values():
        if device.is_group:
            continue
        profile = profile_for(device.sku)
        if profile is not None and profile.scene_upload:
            yield device, profile


def async_scene_adjust_select_entities(coordinator: GoveeCoordinator, entry: ConfigEntry) -> list[SelectEntity]:
    """Build the speed and direction selects for SKUs that take scene uploads."""
    entities: list[SelectEntity] = []
    for device, profile in _scene_upload_devices(coordinator, entry):
        entities.append(GoveeSceneSpeedSelect(coordinator, device, profile))
        entities.append(GoveeSceneDirectionSelect(coordinator, device, profile))
    if entities:
        _LOGGER.debug("Created %d Govee scene adjustment select entities", len(entities))
    return entities


def async_scene_palette_text_entities(coordinator: GoveeCoordinator, entry: ConfigEntry) -> list[TextEntity]:
    """Build one palette text per scene layer for SKUs that take scene uploads."""
    return [
        GoveeScenePaletteText(coordinator, device, profile, layer)
        for device, profile in _scene_upload_devices(coordinator, entry)
        for layer in range(SCENE_PALETTE_LAYERS)
    ]


def async_scene_reset_button_entities(coordinator: GoveeCoordinator, entry: ConfigEntry) -> list[ButtonEntity]:
    """Build the reset button for SKUs that take scene uploads."""
    return [
        GoveeSceneResetButton(coordinator, device, profile)
        for device, profile in _scene_upload_devices(coordinator, entry)
    ]


async def async_start_scene_locally(
    coordinator: GoveeCoordinator, device: GoveeDevice, scene_id: int, scene_name: str
) -> bool:
    """Upload a catalogue scene as raw frames instead of asking the cloud.

    The upload carries the settings stored for that scene. Declined, so the
    caller falls back to the cloud command, when the SKU takes no scene
    uploads, the light is not known to be on, the catalogue has no blob for
    the scene, or no raw pipe accepts the frames.

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
        blob = scene_blob(scene, settings_store(coordinator).get(device.device_id, scene.code))
        frames = scenes.scene_frames(blob, scene.code)
    except GoveeProtocolError:
        return False
    if not await raw_router.async_route_frames(
        coordinator, device.device_id, device.sku, profile, frames, what="scene start"
    ):
        return False
    state.apply_optimistic_scene(str(scene_id), scene_name)
    coordinator.async_update_listeners()
    return True


async def async_start_scene_from_select(entity: Any, scene_id: int, scene_name: str) -> bool:
    """Start a scene picked in the scene select locally, when its name is unambiguous.

    A name that two platform scenes share could mean either catalogue blob, so
    it is declined and the caller uses the cloud command.
    """
    if scene_select_names(entity).count(scene_name) != 1:
        return False
    return await async_start_scene_locally(entity.coordinator, entity._device, scene_id, scene_name)


class _GoveeSceneSettingBase(GoveeEntity):
    """Active-scene lookup, catalogue load and the shared upload."""

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
        """The catalogue entry of the scene the light is running, if any."""
        state = self.coordinator.get_state(self._device_id)
        if state is None or state.power_state is False:
            return None
        return catalogue(self.coordinator).scene(self._device.sku, state.active_scene_name)

    def _settings(self, scene: CatalogueScene) -> SceneSettings:
        return settings_store(self.coordinator).get(self._device_id, scene.code)

    def _applies(self, scene: CatalogueScene) -> bool:
        """Whether this entity's setting exists on ``scene``."""
        raise NotImplementedError  # pragma: no cover

    @property
    def available(self) -> bool:
        """Unavailable unless the running scene offers this setting."""
        if not super().available:
            return False
        scene = self._scene()
        return scene is not None and self._applies(scene)

    def _not_adjustable(self) -> ServiceValidationError:
        return ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="scene_setting_unavailable",
            translation_placeholders={"device": self._device.name},
        )

    async def _async_apply(self, scene: CatalogueScene, settings: SceneSettings) -> None:
        """Upload ``scene`` with ``settings`` and keep them only if it was sent."""
        try:
            frames = scenes.scene_frames(scene_blob(scene, settings), scene.code)
        except GoveeProtocolError as err:
            raise HomeAssistantError(f"{self._device.name}: scene '{scene.name}' cannot be adjusted ({err})") from err
        if not await raw_router.async_route_frames(
            self.coordinator, self._device_id, self._device.sku, self._profile, frames, what="scene adjustment"
        ):
            raise self._command_failed()
        settings_store(self.coordinator).put(self._device_id, scene.code, settings)


class _GoveeSceneSettingEntity(_GoveeSceneSettingBase, RestoreEntity):
    """A scene setting whose value for the active scene survives a restart."""

    async def async_added_to_hass(self) -> None:
        """Seed the store from the restore data, then load the catalogue."""
        data = await self.async_get_last_extra_data()
        payload = data.as_dict() if data is not None else {}
        code = payload.get("scene")
        if isinstance(code, int) and not isinstance(code, bool):
            self._restore(settings_store(self.coordinator).get(self._device_id, code), payload)
        await super().async_added_to_hass()

    @property
    def extra_restore_state_data(self) -> ExtraStoredData | None:
        scene = self._scene()
        if scene is None:
            return None
        return RestoredExtraData({"scene": scene.code, **self._saved(self._settings(scene))})

    def _saved(self, settings: SceneSettings) -> dict[str, Any]:
        """This entity's part of ``settings`` as JSON."""
        raise NotImplementedError  # pragma: no cover

    def _restore(self, settings: SceneSettings, payload: dict[str, Any]) -> None:
        """Put this entity's part of ``payload`` into ``settings`` unless set."""
        raise NotImplementedError  # pragma: no cover


def _index(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


class _GoveeSceneAdjustSelect(_GoveeSceneSettingEntity, SelectEntity):
    """A choice of one of the scene's offered values."""

    _field: str

    def _labels(self, scene: CatalogueScene) -> list[str]:
        raise NotImplementedError  # pragma: no cover

    def _default(self, scene: CatalogueScene) -> int | None:
        raise NotImplementedError  # pragma: no cover

    def _applies(self, scene: CatalogueScene) -> bool:
        return bool(self._labels(scene))

    @property
    def options(self) -> list[str]:
        scene = self._scene()
        return [] if scene is None else self._labels(scene)

    @property
    def current_option(self) -> str | None:
        scene = self._scene()
        if scene is None:
            return None
        index = getattr(self._settings(scene), self._field)
        if index is None:
            index = self._default(scene)
        labels = self._labels(scene)
        return labels[index] if index is not None and index < len(labels) else None

    async def async_select_option(self, option: str) -> None:
        scene = self._scene()
        labels = [] if scene is None else self._labels(scene)
        if scene is None or option not in labels:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_option",
                translation_placeholders={"option": option, "device": self._device.name},
            )
        changes: dict[str, Any] = {self._field: labels.index(option)}
        await self._async_apply(scene, replace(self._settings(scene), **changes))
        self.async_write_ha_state()

    def _saved(self, settings: SceneSettings) -> dict[str, Any]:
        return {self._field: getattr(settings, self._field)}

    def _restore(self, settings: SceneSettings, payload: dict[str, Any]) -> None:
        if getattr(settings, self._field) is None:
            setattr(settings, self._field, _index(payload.get(self._field)))


class GoveeSceneSpeedSelect(_GoveeSceneAdjustSelect):
    """Speed level of the running scene."""

    _attr_icon = "mdi:speedometer"
    _field = "speed"

    def __init__(self, coordinator: GoveeCoordinator, device: GoveeDevice, profile: DeviceProfile) -> None:
        super().__init__(coordinator, device, profile)
        self._attr_unique_id = f"{device.device_id}{SUFFIX_SCENE_SPEED_SELECT}"
        self._attr_name = "Scene speed"

    def _labels(self, scene: CatalogueScene) -> list[str]:
        return [] if scene.adjust is None else speed_labels(scene.adjust.speed_levels)

    def _default(self, scene: CatalogueScene) -> int | None:
        return None if scene.adjust is None else scene.adjust.default_speed


class GoveeSceneDirectionSelect(_GoveeSceneAdjustSelect):
    """Direction of the running scene."""

    _attr_icon = "mdi:rotate-3d-variant"
    _field = "direction"

    def __init__(self, coordinator: GoveeCoordinator, device: GoveeDevice, profile: DeviceProfile) -> None:
        super().__init__(coordinator, device, profile)
        self._attr_unique_id = f"{device.device_id}{SUFFIX_SCENE_DIRECTION_SELECT}"
        self._attr_name = "Scene direction"

    def _labels(self, scene: CatalogueScene) -> list[str]:
        if scene.adjust is None:
            return []
        return [scenes.direction_name(direction) for direction in scene.adjust.directions]

    def _default(self, scene: CatalogueScene) -> int | None:
        return None if scene.adjust is None else scene.adjust.default_direction


class GoveeScenePaletteText(_GoveeSceneSettingEntity, TextEntity):
    """Colours of one layer of the running scene, as ``#rrggbb, #rrggbb``.

    Accepts 1 to :data:`scenes.MAX_SCENE_COLOURS` colours. The light may
    ignore a palette shorter than the layer's effect needs.
    """

    _attr_translation_key = "scene_palette"
    _attr_mode = TextMode.TEXT
    _attr_native_min = 0
    # Validation happens in async_set_value, so its error is the translated one.
    _attr_native_max = 255

    def __init__(
        self, coordinator: GoveeCoordinator, device: GoveeDevice, profile: DeviceProfile, layer: int
    ) -> None:
        super().__init__(coordinator, device, profile)
        self._layer = layer
        self._attr_unique_id = f"{device.device_id}{SUFFIX_SCENE_PALETTE_TEXT}{layer + 1}"
        self._attr_translation_placeholders = {"layer": str(layer + 1)}

    def _catalogue_palette(self, scene: CatalogueScene) -> tuple[scenes.Colour, ...] | None:
        located = scenes.scene_palettes(scene.blob)
        return located[self._layer] if self._layer < len(located) else None

    def _applies(self, scene: CatalogueScene) -> bool:
        return self._catalogue_palette(scene) is not None

    @property
    def native_value(self) -> str | None:
        scene = self._scene()
        if scene is None or not self._applies(scene):
            return None
        colours = self._settings(scene).palettes.get(self._layer) or self._catalogue_palette(scene)
        return format_palette(colours) if colours else None

    def _parse(self, value: str) -> tuple[scenes.Colour, ...]:
        tokens = _PALETTE_SEPARATOR.split(value.strip())
        if not 1 <= len(tokens) <= scenes.MAX_SCENE_COLOURS or not all(_HEX_COLOUR.fullmatch(t) for t in tokens):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_scene_palette",
                translation_placeholders={"value": value, "maximum": str(scenes.MAX_SCENE_COLOURS)},
            )
        return tuple((int(t[1:3], 16), int(t[3:5], 16), int(t[5:7], 16)) for t in tokens)

    async def async_set_value(self, value: str) -> None:
        colours = self._parse(value)
        scene = self._scene()
        if scene is None or not self._applies(scene):
            raise self._not_adjustable()
        current = self._settings(scene)
        await self._async_apply(scene, replace(current, palettes={**current.palettes, self._layer: colours}))
        self.async_write_ha_state()

    def _saved(self, settings: SceneSettings) -> dict[str, Any]:
        colours = settings.palettes.get(self._layer)
        return {"palette": None if colours is None else [list(rgb) for rgb in colours]}

    def _restore(self, settings: SceneSettings, payload: dict[str, Any]) -> None:
        if self._layer in settings.palettes:
            return
        raw = payload.get("palette")
        if not isinstance(raw, list) or not 1 <= len(raw) <= scenes.MAX_SCENE_COLOURS:
            return
        colours: list[scenes.Colour] = []
        for rgb in raw:
            if not (
                isinstance(rgb, list)
                and len(rgb) == 3
                and all(isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 0xFF for v in rgb)
            ):
                return
            colours.append((rgb[0], rgb[1], rgb[2]))
        settings.palettes[self._layer] = tuple(colours)


class GoveeSceneResetButton(_GoveeSceneSettingBase, ButtonEntity):
    """Put the running scene's speed, direction and palettes back to the catalogue's."""

    _attr_translation_key = "reset_scene_settings"

    def __init__(self, coordinator: GoveeCoordinator, device: GoveeDevice, profile: DeviceProfile) -> None:
        super().__init__(coordinator, device, profile)
        self._attr_unique_id = f"{device.device_id}{SUFFIX_SCENE_RESET_BUTTON}"

    def _applies(self, scene: CatalogueScene) -> bool:
        return adjustable(scene)

    async def async_press(self) -> None:
        scene = self._scene()
        if scene is None or not adjustable(scene):
            raise self._not_adjustable()
        await self._async_apply(scene, SceneSettings())
        self.coordinator.async_update_listeners()
