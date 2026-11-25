"""Private upstream members the fork reads, one accessor each (fork).

A member that is missing logs one warning. Data accessors then return None, and
function accessors raise ``RuntimeError``. ``tests/test_upstream_access.py``
pins every member against the real upstream objects, so an upstream rename
fails the suite instead of degrading quietly.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING, Any, Final

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError

if TYPE_CHECKING:
    from .coordinator import GoveeCoordinator

_LOGGER = logging.getLogger(__name__)

_MISSING: Final = object()
_warned: set[str] = set()


def _read(owner: Any, name: str, where: str) -> Any:
    """``owner.name``, or ``_MISSING`` after one warning per ``where``."""
    value = getattr(owner, name, _MISSING)
    if value is _MISSING and where not in _warned:
        _warned.add(where)
        _LOGGER.warning("Govee fork: upstream member %s is missing, so the feature that reads it is degraded", where)
    return value


def _services_function(name: str) -> Any:
    from . import services

    function = _read(services, name, f"services.{name}")
    if function is _MISSING:
        raise RuntimeError(f"upstream services.{name} is missing")
    return function


def coordinator_for_device(hass: HomeAssistant, raw_id: str) -> tuple[GoveeCoordinator, str] | None:
    """``services._get_coordinator_for_device``: the coordinator and Govee id, or None."""
    result: tuple[GoveeCoordinator, str] | None = _services_function("_get_coordinator_for_device")(hass, raw_id)
    return result


def device_not_found(raw_id: str) -> ServiceValidationError:
    """``services._device_not_found``: the translated unknown-device error."""
    error: ServiceValidationError = _services_function("_device_not_found")(raw_id)
    return error


def platform_scene_cache(coordinator: GoveeCoordinator) -> Mapping[str, tuple[float, list[Any]]] | None:
    """``coordinator._scene_cache._scene_cache``: platform scenes by device id, or None."""
    manager = _read(coordinator, "_scene_cache", "GoveeCoordinator._scene_cache")
    if manager is _MISSING:
        return None
    cache = _read(manager, "_scene_cache", "SceneCacheManager._scene_cache")
    return cache if isinstance(cache, Mapping) else None


def scene_select_names(entity: Any) -> list[str]:
    """The scene names behind a scene select's options, from ``_scene_map``."""
    scene_map = _read(entity, "_scene_map", "GoveeSceneSelectEntity._scene_map")
    if not isinstance(scene_map, Mapping):
        return []
    return [entry[-1] for entry in scene_map.values() if isinstance(entry, tuple) and entry]


async def bff_call(coordinator: GoveeCoordinator, operation: Callable[[Any, str], Awaitable[Any]], what: str) -> Any:
    """``coordinator._async_bff_call``: run ``operation(auth_client, token)``, logging in again once on a refused token.

    Returns None when the entry holds no account token.
    """
    call = _read(coordinator, "_async_bff_call", "GoveeCoordinator._async_bff_call")
    if call is _MISSING:
        raise RuntimeError("upstream GoveeCoordinator._async_bff_call is missing")
    return await call(operation, what)


def app_headers(email: str | None) -> dict[str, str]:
    """``GoveeAuthClient._build_govee_headers`` with the client id ``auth._derive_client_id`` gives ``email``."""
    from .api import auth

    build = _read(auth.GoveeAuthClient, "_build_govee_headers", "GoveeAuthClient._build_govee_headers")
    derive = _read(auth, "_derive_client_id", "auth._derive_client_id")
    if build is _MISSING or derive is _MISSING:
        raise RuntimeError("upstream app header builders are missing")
    headers: dict[str, str] = build(derive(email) if email else None)
    return headers
