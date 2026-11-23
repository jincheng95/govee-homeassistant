"""The Govee account's saved DIY effects (fork).

Create, list and delete over the app's own endpoints, with the account token
the integration already holds. No lamp is involved in any of them.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any, Final

import aiohttp

from .exceptions import GoveeApiError, GoveeAuthError
from .protocol.saved_diy import LIST_EFFECT_CODES, LIST_MIX_CODES, SavedEffect

_BASE: Final = "https://app2.govee.com"
_CREATE_URL: Final = f"{_BASE}/appsku/v1/diys/new-diys"
_LIST_URL: Final = f"{_BASE}/appsku/v2/diys/groups-diys"
_DELETE_URL: Final = f"{_BASE}/app/rest/v1/diy"

_TIMEOUT: Final = aiohttp.ClientTimeout(total=20)
_NO_GROUP: Final = -1
_OK: Final = 200
_UNAUTHORIZED: Final = 401


async def _request(
    session: aiohttp.ClientSession,
    method: str,
    url: str,
    headers: Mapping[str, str],
    what: str,
    *,
    json: Any = None,
    params: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """One call, returning the JSON body's ``data`` object.

    Raises:
        GoveeAuthError: The token was refused, by HTTP status or in the body.
        GoveeApiError: Any other failure, a connection error included.
    """
    try:
        async with session.request(
            method, url, headers=dict(headers), json=json, params=params, timeout=_TIMEOUT
        ) as response:
            if response.status == _UNAUTHORIZED:
                raise GoveeAuthError(f"{what}: the account token was refused")
            try:
                body = await response.json(content_type=None)
            except ValueError:
                body = None
            status = response.status
    except (aiohttp.ClientError, TimeoutError) as err:
        raise GoveeApiError(f"{what}: {type(err).__name__}") from err
    if not isinstance(body, dict):
        raise GoveeApiError(f"{what}: HTTP {status} with no JSON body", code=status)
    body_status = body.get("status", status)
    if body_status == _UNAUTHORIZED:
        raise GoveeAuthError(f"{what}: the account token was refused")
    if status != _OK or body_status != _OK:
        raise GoveeApiError(f"{what}: {body.get('message') or f'status {body_status}'}", code=status)
    data = body.get("data")
    return data if isinstance(data, dict) else {}


async def async_create(
    session: aiohttp.ClientSession,
    headers: Mapping[str, str],
    *,
    sku: str,
    goods_type: int,
    name: str,
    effect: SavedEffect,
    diy_code: int,
) -> int:
    """Store one effect and return the id the account gave it.

    Raises:
        GoveeAuthError: The token was refused.
        GoveeApiError: The account refused the effect or returned no id.
    """
    body = {
        "transaction": "",
        "key": "",
        "view": 0,
        "sku": sku,
        "goodsType": goods_type,
        "isCopy": False,
        "origin": 0,
        "effects": [
            {
                "effectCode": list(effect.effect_code),
                "effectStr": effect.effect_str,
                "diyName": name,
                "diyCode": diy_code,
                "createTime": int(time.time() * 1000),
                "groupId": _NO_GROUP,
                "coverUrl": "",
                "gifId": effect.gif_id,
                "effectId": 0,
                "diyModeType": effect.mode_type,
                "frameNum": 0,
                "goodsType": 0,
                "on": 0,
                "whiteNoiseId": 0,
                "diyFrom": 0,
                "voiceType": 0,
                "mixedMode": -1,
            }
        ],
    }
    data = await _request(session, "POST", _CREATE_URL, headers, "saving the DIY effect", json=body)
    effect_ids = data.get("effectIds")
    if not isinstance(effect_ids, list) or not effect_ids or not isinstance(effect_ids[0], int):
        raise GoveeApiError("saving the DIY effect: the account returned no effect id")
    return effect_ids[0]


async def async_list(
    session: aiohttp.ClientSession, headers: Mapping[str, str], *, sku: str, goods_type: int
) -> list[dict[str, Any]]:
    """The account's saved effects for one SKU, flattened across its groups.

    Each item is the account's own record with ``groupId`` and ``groupName``
    of its group added.

    Raises:
        GoveeAuthError: The token was refused.
        GoveeApiError: Any other failure.
    """
    body = {
        "transaction": str(int(time.time() * 1000)),
        "key": "",
        "view": 0,
        "sku": sku,
        "goodsType": goods_type,
        "effectCode": list(LIST_EFFECT_CODES),
        "mixEffectCode": list(LIST_MIX_CODES),
        "sortType": 1,
    }
    data = await _request(session, "POST", _LIST_URL, headers, "listing saved DIY effects", json=body)
    found: list[dict[str, Any]] = []
    groups = data.get("diys")
    for group in groups if isinstance(groups, list) else []:
        if not isinstance(group, dict):
            continue
        items = group.get("diys")
        for item in items if isinstance(items, list) else []:
            if isinstance(item, dict):
                found.append({**item, "groupId": group.get("groupId"), "groupName": group.get("groupName")})
    return found


async def async_delete(session: aiohttp.ClientSession, headers: Mapping[str, str], effect_id: int) -> None:
    """Delete one saved effect by its account id.

    Raises:
        GoveeAuthError: The token was refused.
        GoveeApiError: Any other failure.
    """
    await _request(
        session, "DELETE", _DELETE_URL, headers, "deleting the saved DIY effect", params={"effectId": str(effect_id)}
    )
