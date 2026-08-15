"""DIY mode preview artwork, served from the integration (fork feature).

HA ``select`` entities cannot render an image in their options, so the vendor's
preview stills are shipped in ``diy_mode_previews/`` and served as URLs a picture
card can point at — deliberately outside ``/api/``, since they hold no secrets
and a card has no bearer token. Nothing fetches at runtime. The mode-name to
filename map below is asserted both ways by the tests, so a mode added to a
profile without artwork fails the suite rather than shipping a dead URL.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Final

from homeassistant.core import HomeAssistant

from .api.protocol import DiyZoneSpec
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

PREVIEW_DIR_NAME: Final = "diy_mode_previews"
"""Directory under the integration holding the shipped PNGs."""

URL_BASE: Final = "/govee_diy_previews"
"""Stable URL prefix the artwork is served under.

Deliberately not under ``/api/`` — these are static images with no secrets in
them, and a dashboard card must be able to render one without a bearer token.
"""

_REGISTERED_KEY: Final = f"{DOMAIN}_diy_previews_registered"
"""hass.data flag: the static path is per-HA, not per-config-entry."""

_REGISTER_LOCK_KEY: Final = f"{DOMAIN}_diy_previews_lock"
"""hass.data lock serialising the registration; see async_register_previews."""

# mode name (as the profile table spells it) -> shipped filename, per DIY zone.
# The numeric prefixes are the vendor's gifIds and are kept so a file can be
# traced back to reference/diy-mode-gifs/index.json, which carries the
# provenance and evidence level for each identification.
PREVIEW_FILES: Final[dict[str, dict[str, str]]] = {
    "ripple": {
        "none": "16_ripple_none.png",
        "gradient": "11_ripple_gradient.png",
        "breathe": "12_ripple_breathe.png",
        "rainbow": "13_ripple_rainbow.png",
        "twinkle": "14_ripple_twinkle.png",
        "jumping": "15_ripple_jumping.png",
    },
    "ring": {
        "none": "27_ring_none.png",
        "gradient": "17_ring_gradient.png",
        "breathe": "18_ring_breathe.png",
        "twinkle": "19_ring_twinkle.png",
        "rainbow": "20_ring_rainbow.png",
        "graffiti": "21_ring_graffiti.png",
        "flow": "22_ring_flow.png",
        "alternate": "23_ring_alternate.png",
        "gleam": "24_ring_gleam.png",
        "cover": "25_ring_cover.png",
        "colorful": "26_ring_colorful.png",
    },
}

FALLBACK_MODE: Final = "none"
"""What an unknown or unset mode previews as: the zone's "off" placeholder."""


def preview_dir() -> Path:
    """Absolute path of the shipped artwork directory."""
    return Path(__file__).parent / PREVIEW_DIR_NAME


def preview_url(zone_key: str, mode_name: str | None) -> str | None:
    """The URL path of one mode's preview.

    Args:
        zone_key: DIY zone key (``ripple`` / ``ring``).
        mode_name: The mode's name as the profile table spells it. ``None``,
            an empty string, or a name with no artwork (the ripple accepts
            ring-enum ints the table does not bind) all fall back to the
            zone's "none" placeholder.

    Returns:
        A URL path, or ``None`` for a zone with no artwork at all.
    """
    files = PREVIEW_FILES.get(zone_key)
    if not files:
        return None
    filename = files.get(mode_name or "") or files.get(FALLBACK_MODE)
    if filename is None:  # pragma: no cover - every zone ships a "none"
        return None
    return f"{URL_BASE}/{filename}"


def preview_map(zone: DiyZoneSpec) -> dict[str, str]:
    """Every mode in ``zone``'s table mapped to its preview URL.

    Ordered as the table is (the app's display order, "None" first), so a
    dashboard rendering the whole map gets the picker's own order for free.

    Args:
        zone: The DIY zone spec whose mode table to walk.

    Returns:
        Mode name → URL path. Empty for a zone with no shipped artwork.
    """
    files = PREVIEW_FILES.get(zone.zone_key)
    if not files:
        return {}
    return {name: f"{URL_BASE}/{files[name]}" for name in zone.modes if name in files}


async def async_register_previews(hass: HomeAssistant) -> None:
    """Serve the shipped artwork under :data:`URL_BASE`.

    Per-HA rather than per-entry: the path is a constant and registering it
    twice raises. The flag alone is not enough — the registration await is a
    yield point, so two entries setting up concurrently would both pass the
    check and the second would fail its whole setup. The lock closes that
    window, and a waiter re-checks the flag rather than registering again.

    Args:
        hass: The Home Assistant instance.
    """
    if hass.data.get(_REGISTERED_KEY):
        return

    lock = hass.data.setdefault(_REGISTER_LOCK_KEY, asyncio.Lock())
    async with lock:
        if hass.data.get(_REGISTERED_KEY):
            return

        # Imported here, not at module scope: `http` is a soft dependency of
        # this integration and the import should not run during a
        # manifest-only load.
        from homeassistant.components.http import StaticPathConfig

        await hass.http.async_register_static_paths(
            [
                StaticPathConfig(
                    URL_BASE,
                    str(preview_dir()),
                    # The artwork is immutable — it ships with the version.
                    cache_headers=True,
                )
            ]
        )
        hass.data[_REGISTERED_KEY] = True

    _LOGGER.debug("Serving Govee DIY mode previews at %s", URL_BASE)
