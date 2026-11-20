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
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

import aiohttp
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api.protocol import scenes

if TYPE_CHECKING:
    from .coordinator import GoveeCoordinator

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
