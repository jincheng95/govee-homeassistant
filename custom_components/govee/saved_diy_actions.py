"""Saved DIY effects in the Govee account, for the DIY service actions (fork)."""

from __future__ import annotations

import logging
import secrets
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Final

import aiohttp
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import saved_diy_cloud
from .api.exceptions import GoveeApiError
from .api.protocol import GoveeProtocolError, saved_diy
from .const import CONF_EMAIL, DOMAIN
from .coordinator import GoveeCoordinator
from .models import GoveeDevice
from .upstream_access import app_headers, bff_call

_LOGGER = logging.getLogger(__name__)

_KINDS: Final[Mapping[int, str]] = {0x00: "basic", 0x49: "composed", 0x50: "composed"}


def new_diy_code() -> int:
    """A commit code for a new saved effect, drawn as the app draws it."""
    return saved_diy.MIN_DIY_CODE + secrets.randbelow(saved_diy.MAX_DIY_CODE - saved_diy.MIN_DIY_CODE + 1)


def _error(key: str, device: GoveeDevice, reason: str = "") -> HomeAssistantError:
    return HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key=key,
        translation_placeholders={"device": device.name, "reason": reason},
    )


async def _account_call(
    coordinator: GoveeCoordinator,
    device: GoveeDevice,
    call: Callable[[aiohttp.ClientSession, Mapping[str, str]], Awaitable[Any]],
    what: str,
) -> Any:
    """Run ``call(session, headers)`` with the entry's account token.

    Raises:
        HomeAssistantError: The entry has no account login, or the account
            refused the call.
    """
    session = async_get_clientsession(coordinator.hass)
    email = coordinator.config_entry.data.get(CONF_EMAIL)

    async def _operation(_client: Any, token: str) -> tuple[Any]:
        headers = {**app_headers(email), "Authorization": f"Bearer {token}"}
        return (await call(session, headers),)

    try:
        result = await bff_call(coordinator, _operation, what)
    except GoveeApiError as err:
        _LOGGER.debug("Account call failed (%s): %s", what, err)
        raise _error("saved_diy_failed", device, str(err)) from err
    if result is None:
        raise _error("saved_diy_no_account", device)
    return result[0]


async def async_save_effect(
    coordinator: GoveeCoordinator, device: GoveeDevice, name: str, effect: saved_diy.SavedEffect, diy_code: int
) -> int:
    """Store ``effect`` in the account under ``name`` and return its account id.

    Raises:
        HomeAssistantError: As :func:`_account_call`.
    """

    async def _create(session: aiohttp.ClientSession, headers: Mapping[str, str]) -> int:
        return await saved_diy_cloud.async_create(
            session,
            headers,
            sku=device.sku,
            goods_type=saved_diy.GOODS_TYPES[device.sku],
            name=name,
            effect=effect,
            diy_code=diy_code,
        )

    effect_id: int = await _account_call(coordinator, device, _create, "saved DIY create")
    return effect_id


def _summary(item: Mapping[str, Any]) -> dict[str, Any]:
    """The fields of one account record that the list action returns."""
    effect_str = item.get("effectStr")
    kind = "unknown"
    if isinstance(effect_str, str):
        try:
            kind = _KINDS.get(saved_diy.decode(effect_str)[0], "other")
        except GoveeProtocolError:
            kind = "unreadable"
    return {
        "effect_id": item.get("effectId"),
        "name": item.get("diyName"),
        "diy_code": item.get("diyCode"),
        "effect_code": item.get("effectCode"),
        "gif_id": item.get("gifId"),
        "group": item.get("groupName"),
        "kind": kind,
        "effect_str": effect_str,
    }


async def async_list_effects(coordinator: GoveeCoordinator, device: GoveeDevice) -> list[dict[str, Any]]:
    """The account's saved effects for the device's SKU.

    Raises:
        HomeAssistantError: As :func:`_account_call`.
    """

    async def _list(session: aiohttp.ClientSession, headers: Mapping[str, str]) -> list[dict[str, Any]]:
        return await saved_diy_cloud.async_list(
            session, headers, sku=device.sku, goods_type=saved_diy.GOODS_TYPES[device.sku]
        )

    items: list[dict[str, Any]] = await _account_call(coordinator, device, _list, "saved DIY list")
    return [_summary(item) for item in items]


async def async_delete_effect(coordinator: GoveeCoordinator, device: GoveeDevice, effect_id: int) -> None:
    """Delete one saved effect, after checking it is one of this SKU's.

    Raises:
        HomeAssistantError: The id is not among the SKU's saved effects, or as
            :func:`_account_call`.
    """
    known = {item["effect_id"] for item in await async_list_effects(coordinator, device)}
    if effect_id not in known:
        raise _error("saved_diy_unknown", device, str(effect_id))

    async def _delete(session: aiohttp.ClientSession, headers: Mapping[str, str]) -> None:
        await saved_diy_cloud.async_delete(session, headers, effect_id)

    await _account_call(coordinator, device, _delete, "saved DIY delete")
