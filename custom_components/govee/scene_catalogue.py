"""The app scene catalogue: upload blobs and adjustments per scene (fork feature).

The platform API names scenes but carries neither their upload blob nor their
speed and direction options. The app catalogue carries both and needs no
account, so it is fetched once per SKU and held in memory.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

import aiohttp
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api.protocol import scenes
from .zone_state import profile_for

if TYPE_CHECKING:
    from .coordinator import GoveeCoordinator
    from .models import GoveeDevice, GoveeDeviceState

_LOGGER = logging.getLogger(__name__)

CATALOGUE_URL: Final = "https://app2.govee.com/appsku/v2/scences"
_APP_VERSION: Final = "7.4.10"
_TIMEOUT: Final = aiohttp.ClientTimeout(total=20)

CATALOGUE_TTL: Final = 24 * 3600.0
RETRY_AFTER_FAILURE: Final = 600.0

_STORE_ATTR: Final = "_govee_scene_catalogue"


@dataclass(frozen=True)
class CatalogueScene:
    """One named scene as the device needs it."""

    name: str
    code: int
    blob: bytes
    adjust: scenes.SceneAdjust | None


def _parse_effect(name: str, effect: Any) -> CatalogueScene | None:
    if not isinstance(effect, dict):
        return None
    code = effect.get("sceneCode")
    if not isinstance(code, int) or isinstance(code, bool) or not 0 < code <= 0xFFFF:
        return None
    try:
        blob = base64.b64decode(effect.get("scenceParam") or "", validate=True)
    except (binascii.Error, ValueError, TypeError):
        return None
    if not blob:
        return None

    config: Any = None
    speed_info = effect.get("speedInfo")
    raw_config = speed_info.get("config") if isinstance(speed_info, dict) else None
    if isinstance(raw_config, str) and raw_config:
        try:
            config = json.loads(raw_config)
        except ValueError:
            config = None
    return CatalogueScene(name=name, code=code, blob=blob, adjust=scenes.parse_adjust(config, blob))


def parse_catalogue(payload: Any) -> dict[str, CatalogueScene]:
    """Scenes by name from a catalogue response.

    A scene's first effect is the one the app applies. Where a name repeats,
    the first scene keeps it. Firmware presets carry no blob and are left out.
    """
    found: dict[str, CatalogueScene] = {}
    data = payload.get("data") if isinstance(payload, dict) else None
    categories = data.get("categories") if isinstance(data, dict) else None
    if not isinstance(categories, list):
        return found
    for category in categories:
        entries = category.get("scenes") if isinstance(category, dict) else None
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            name = entry.get("sceneName")
            effects = entry.get("lightEffects")
            if not isinstance(name, str) or not name or name in found:
                continue
            if not isinstance(effects, list) or not effects:
                continue
            scene = _parse_effect(name, effects[0])
            if scene is not None:
                found[name] = scene
    return found


class SceneCatalogue:
    """Per-SKU catalogue cache."""

    def __init__(self, coordinator: GoveeCoordinator) -> None:
        self._coordinator = coordinator
        self._scenes: dict[str, dict[str, CatalogueScene]] = {}
        self._retry_at: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def scene(self, sku: str, name: str | None) -> CatalogueScene | None:
        """The cached scene called ``name``, or None."""
        if not name:
            return None
        return self._scenes.get(sku, {}).get(name)

    def loaded(self, sku: str) -> bool:
        return sku in self._scenes

    def name_for_code(self, sku: str, code: int) -> str | None:
        """The cached scene that uploads with ``code``, or None."""
        for scene in self._scenes.get(sku, {}).values():
            if scene.code == code:
                return scene.name
        return None

    async def async_load(self, sku: str) -> bool:
        """Fetch the catalogue for ``sku`` unless a fresh copy is held.

        Returns:
            True when a catalogue for the SKU is held afterwards.
        """
        lock = self._locks.setdefault(sku, asyncio.Lock())
        async with lock:
            now = time.monotonic()
            if now < self._retry_at.get(sku, 0.0):
                return sku in self._scenes
            fetched = await self._async_fetch(sku)
            if fetched:
                self._scenes[sku] = fetched
                self._retry_at[sku] = now + CATALOGUE_TTL
            else:
                self._retry_at[sku] = now + RETRY_AFTER_FAILURE
            return sku in self._scenes

    async def _async_fetch(self, sku: str) -> dict[str, CatalogueScene]:
        session = async_get_clientsession(self._coordinator.hass)
        headers = {
            "appVersion": _APP_VERSION,
            "clientType": "1",
            "clientId": "0" * 32,
            "timestamp": str(int(time.time() * 1000)),
            "country": "US",
            "envId": "0",
            "iotVersion": "0",
        }
        try:
            async with session.get(CATALOGUE_URL, params={"sku": sku}, headers=headers, timeout=_TIMEOUT) as response:
                if response.status != 200:
                    _LOGGER.debug("Scene catalogue for %s answered HTTP %s", sku, response.status)
                    return {}
                payload = await response.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
            _LOGGER.debug("Scene catalogue fetch for %s failed: %s", sku, err)
            return {}
        found = parse_catalogue(payload)
        _LOGGER.debug("Scene catalogue for %s holds %d uploadable scenes", sku, len(found))
        return found


def catalogue(coordinator: GoveeCoordinator) -> SceneCatalogue:
    """The coordinator's scene catalogue, built on first use."""
    existing: SceneCatalogue | None = getattr(coordinator, _STORE_ATTR, None)
    if existing is None:
        existing = SceneCatalogue(coordinator)
        setattr(coordinator, _STORE_ATTR, existing)
    return existing


def _mode_report(frames: Iterable[bytes]) -> bytes | None:
    """The first ``aa 05`` mode report, or None when it fails its checksum."""
    for raw in frames:
        if len(raw) != 20 or raw[0] != 0xAA or raw[1] != 0x05:
            continue
        checksum = 0
        for byte in raw[:19]:
            checksum ^= byte
        return bytes(raw) if checksum == raw[19] else None
    return None


def reported_scene_code(frames: Iterable[bytes]) -> int | None:
    """The scene code in an ``aa 05 04 <lo> <hi>`` mode report, or None.

    None also covers a report of any other mode and a frame that fails its
    checksum.
    """
    raw = _mode_report(frames)
    if raw is None or raw[2] != scenes.SCENE_SUB_MODE:
        return None
    return raw[3] | (raw[4] << 8)


def _platform_scene_id(coordinator: GoveeCoordinator, device_id: str, name: str) -> str | None:
    """The platform API id of the scene called ``name``, from the scene cache."""
    cache = getattr(getattr(coordinator, "_scene_cache", None), "_scene_cache", None)
    entry = cache.get(device_id) if isinstance(cache, dict) else None
    if not entry:
        return None
    for scene in entry[1]:
        if isinstance(scene, dict) and scene.get("name") == name:
            value = scene.get("value")
            scene_id = value.get("id") if isinstance(value, dict) else None
            return None if scene_id is None else str(scene_id)
    return None


def apply_scene_report(
    coordinator: GoveeCoordinator,
    device: GoveeDevice,
    state: GoveeDeviceState,
    frames: Iterable[bytes],
) -> bool:
    """Follow the scene the device says it is running.

    A report of any other mode clears the active scene. Of scene reports, only
    a code the catalogue knows changes anything. A scene started through the
    platform API reports a code outside the catalogue, and that leaves the
    state as the command set it.

    Returns:
        True when the active scene changed.
    """
    profile = profile_for(device.sku)
    if profile is None or not profile.scene_upload:
        return False
    raw = _mode_report(frames)
    if raw is None:
        return False
    if raw[2] != scenes.SCENE_SUB_MODE:
        if state.active_scene is None and state.active_scene_name is None:
            return False
        state.active_scene = None
        state.active_scene_name = None
        return True
    code = raw[3] | (raw[4] << 8)
    name = catalogue(coordinator).name_for_code(device.sku, code)
    if name is None or name == state.active_scene_name:
        return False
    state.active_scene = _platform_scene_id(coordinator, device.device_id, name)
    state.active_scene_name = name
    return True
