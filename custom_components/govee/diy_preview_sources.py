"""DIY preview images on the vendor CDN, with the shipped copies as fallback (fork feature).

The app lists one preview image per DIY effect, keyed by gifId, for each
product. The list is fetched per SKU in the background and refreshed daily, as
:mod:`.scene_catalogue` does. Each source pairs a CDN URL with the shipped copy
from :mod:`.diy_previews`, so a dashboard can fall back when the CDN fails.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
import logging
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import urlsplit

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from .api.protocol import DiyZoneSpec
from .diy_previews import PREVIEW_FILES, preview_map, string_effect_preview_map
from .scene_catalogue import APP_TIMEOUT, SkuCache, app_headers
from .zone_state import profile_for

if TYPE_CHECKING:
    from .coordinator import GoveeCoordinator

_LOGGER = logging.getLogger(__name__)

PREVIEWS_URL: Final = "https://app2.govee.com/bff-app/v1/diy/gif/effects"

CDN_HOST_SUFFIXES: Final = (".cloudfront.net", ".govee.com")
"""Host suffixes a CDN preview URL may carry. Any other URL is discarded."""

SOURCE_CDN: Final = "cdn"
SOURCE_MIXED: Final = "mixed"
SOURCE_LOCAL: Final = "local"

_STORE_ATTR: Final = "_govee_diy_preview_sources"

# String-light effect key -> the vendor's gifId for its preview.
STRING_EFFECT_GIF_IDS: Final[Mapping[str, int]] = {
    "stripes_1": 1,
    "stripes_2": 2,
    "bounce_1": 3,
    "bounce_2": 4,
    "bounce_3": 5,
    "whirlpool": 6,
    "diffuse_1": 7,
    "diffuse_2": 8,
    "spin_1": 9,
    "spin_2": 10,
    "spiral": 11,
    "flow_1": 12,
    "flow_2": 13,
    "flow_3": 14,
    "flow_4": 15,
    "rainbow_1": 16,
    "rainbow_2": 17,
    "fade_1": 18,
    "fade_2": 19,
    "fade_3": 20,
    "marquee_1": 21,
    "marquee_2": 22,
    "marquee_3": 23,
    "breathe": 24,
    "ribbon_1": 29,
    "ribbon_2": 30,
    "mountain_peak": 31,
    "candy_crush": 32,
    "smudge": 33,
    "flexing": 34,
    "spin_3": 35,
}


@dataclass(frozen=True)
class PreviewList:
    """The CDN preview URLs of one SKU, by gifId."""

    urls: Mapping[int, str]
    fetched_at: datetime


def cdn_url(url: Any) -> str | None:
    """``url`` when it is ``https`` on a host in :data:`CDN_HOST_SUFFIXES`, else None."""
    if not isinstance(url, str):
        return None
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
    except ValueError:
        return None
    if parts.scheme != "https" or not host.endswith(CDN_HOST_SUFFIXES):
        return None
    return url


def _gif_id(raw: Any) -> int | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw if raw >= 0 else None
    if isinstance(raw, str) and raw.isdecimal():
        return int(raw)
    return None


def parse_previews(payload: Any) -> dict[int, str]:
    """CDN URLs by gifId from a preview-list response.

    The list is ``data`` itself or the one list-valued member of a ``data``
    object. A URL outside :func:`cdn_url`'s rule is left out, and where a gifId
    repeats the first entry keeps it.
    """
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, dict):
        lists = [value for value in data.values() if isinstance(value, list)]
        data = lists[0] if len(lists) == 1 else None
    found: dict[int, str] = {}
    if not isinstance(data, list):
        return found
    for entry in data:
        if not isinstance(entry, dict):
            continue
        gif_id = _gif_id(entry.get("gifId"))
        url = cdn_url(entry.get("gifUrl"))
        if gif_id is not None and url is not None and gif_id not in found:
            found[gif_id] = url
    return found


def goods_type(sku: str) -> int | None:
    """The app's goods type for ``sku``, or None when the profile table lacks it."""
    profile = profile_for(sku)
    return None if profile is None else profile.goods_type


def takes_string_effects(sku: str) -> bool:
    """Whether ``sku`` takes the whole-light string effects of ``apply_diy_effect``."""
    profile = profile_for(sku)
    return profile is not None and profile.scene_upload and profile.diy is None


class PreviewSources(SkuCache[PreviewList]):
    """Per-SKU cache of the vendor's preview list."""

    label = "DIY preview list"

    def urls(self, sku: str) -> Mapping[int, str]:
        """The held CDN URLs by gifId, empty when none are held."""
        held = self._held.get(sku)
        return {} if held is None else held.urls

    def updated(self, sku: str) -> datetime | None:
        """When the held list was fetched, or None."""
        held = self._held.get(sku)
        return None if held is None else held.fetched_at

    def async_schedule(self, hass: HomeAssistant, sku: str) -> None:
        """Schedule the list as :meth:`SkuCache.async_schedule` does, for a SKU with a goods type."""
        if goods_type(sku) is None:
            return
        super().async_schedule(hass, sku)

    async def _async_fetch(self, sku: str) -> PreviewList | None:
        goods = goods_type(sku)
        if goods is None:
            return None
        session = async_get_clientsession(self._coordinator.hass)
        params = {"goodsType": str(goods), "sku": sku}
        try:
            async with session.get(PREVIEWS_URL, params=params, headers=app_headers(), timeout=APP_TIMEOUT) as response:
                if response.status != 200:
                    _LOGGER.debug("DIY preview list for %s answered HTTP %s", sku, response.status)
                    return None
                payload = await response.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
            _LOGGER.debug("DIY preview list fetch for %s failed with %s", sku, type(err).__name__)
            return None
        urls = parse_previews(payload)
        _LOGGER.debug("DIY preview list for %s holds %d CDN previews", sku, len(urls))
        if not urls:
            return None
        return PreviewList(urls=urls, fetched_at=dt_util.utcnow())


def sources(coordinator: GoveeCoordinator) -> PreviewSources:
    """The coordinator's preview-list cache, built on first use."""
    existing = getattr(coordinator, _STORE_ATTR, None)
    if not isinstance(existing, PreviewSources):
        existing = PreviewSources(coordinator)
        setattr(coordinator, _STORE_ATTR, existing)
    return existing


def _source(cdn: str | None, local: str) -> dict[str, str]:
    return {"url": cdn or local, "fallback": local}


def string_effect_sources(urls: Mapping[int, str]) -> dict[str, dict[str, str]]:
    """``{"url", "fallback"}`` for every string-light effect with shipped artwork.

    ``url`` is the CDN copy when ``urls`` holds the effect's gifId, else the
    shipped copy that ``fallback`` always names. Ordered as the action lists
    the effects.
    """
    return {
        effect: _source(urls.get(STRING_EFFECT_GIF_IDS[effect]), local)
        for effect, local in string_effect_preview_map().items()
    }


def zone_preview_sources(zone: DiyZoneSpec, urls: Mapping[int, str]) -> dict[str, dict[str, str]]:
    """``{"url", "fallback"}`` for every mode of a lamp DIY zone with shipped artwork.

    A shipped file's name starts with the vendor's gifId. Keys and order match
    :func:`.diy_previews.preview_map`.
    """
    files = PREVIEW_FILES.get(zone.zone_key, {})
    return {
        mode: _source(urls.get(int(files[mode].split("_", 1)[0])), local)
        for mode, local in preview_map(zone).items()
    }


def cdn_count(previews: Mapping[str, Mapping[str, str]]) -> int:
    """How many sources resolved to a CDN URL."""
    return sum(1 for source in previews.values() if source["url"] != source["fallback"])


def source_kind(previews: Mapping[str, Mapping[str, str]]) -> str:
    """:data:`SOURCE_CDN` when every source is a CDN URL, :data:`SOURCE_LOCAL` when none is, else mixed."""
    count = cdn_count(previews)
    if count == 0:
        return SOURCE_LOCAL
    return SOURCE_CDN if count == len(previews) else SOURCE_MIXED
