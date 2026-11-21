"""DIY preview artwork, served from the integration (fork feature).

HA ``select`` entities cannot render an image in their options, so the vendor's
preview images are shipped in ``diy_mode_previews/`` and served as URLs a
picture card can point at. The multi-zone lamp's mode stills sit at the top of
the directory and the H70CX string-light effects in ``h70cx/``. The URLs are
outside ``/api/`` because the images hold no secrets and a card has no bearer
token. The vendor's CDN copies are resolved in :mod:`.diy_preview_sources`.
The tests assert both name-to-file maps both ways, so a mode or effect added
without artwork fails the suite.
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

STRING_PREVIEW_SUBDIR: Final = "h70cx"
"""Subdirectory of :data:`PREVIEW_DIR_NAME` holding the string-light effects."""

# String-light effect key -> shipped filename, in the action's display order.
# Effects absent here have no vendor artwork.
STRING_EFFECT_FILES: Final[dict[str, str]] = {
    effect: f"{effect}.png"
    for effect in (
        "bounce_1",
        "bounce_2",
        "bounce_3",
        "breathe",
        "candy_crush",
        "diffuse_1",
        "diffuse_2",
        "fade_1",
        "fade_2",
        "fade_3",
        "flexing",
        "flow_1",
        "flow_2",
        "flow_3",
        "flow_4",
        "marquee_1",
        "marquee_2",
        "marquee_3",
        "mountain_peak",
        "rainbow_1",
        "rainbow_2",
        "ribbon_1",
        "ribbon_2",
        "smudge",
        "spin_1",
        "spin_2",
        "spin_3",
        "spiral",
        "stripes_1",
        "stripes_2",
        "whirlpool",
    )
}


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


def string_preview_dir() -> Path:
    """Absolute path of the shipped string-light artwork directory."""
    return preview_dir() / STRING_PREVIEW_SUBDIR


def string_effect_preview_url(effect: str | None) -> str | None:
    """The URL path of one string-light effect's preview.

    Args:
        effect: An effect key the ``apply_diy_effect`` action accepts.

    Returns:
        A URL path, or ``None`` for an effect with no artwork.
    """
    filename = STRING_EFFECT_FILES.get(effect or "")
    if filename is None:
        return None
    return f"{URL_BASE}/{STRING_PREVIEW_SUBDIR}/{filename}"


def string_effect_preview_map() -> dict[str, str]:
    """Every string-light effect with artwork mapped to its preview URL.

    Returns:
        Effect key → URL path, in the action's display order.
    """
    return {
        effect: f"{URL_BASE}/{STRING_PREVIEW_SUBDIR}/{filename}"
        for effect, filename in STRING_EFFECT_FILES.items()
    }


async def async_register_previews(hass: HomeAssistant) -> None:
    """Serve the shipped artwork under :data:`URL_BASE`.

    One registration covers both families, since a static path serves the
    files in its subdirectories too.

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
