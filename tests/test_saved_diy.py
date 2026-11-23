"""Saved DIY effects in the Govee account (fork feature).

Every record, id and address here is synthetic.
"""

from __future__ import annotations

import base64
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.govee import diy_actions, saved_diy_actions, upstream_access
from custom_components.govee.api import saved_diy_cloud
from custom_components.govee.api.auth import GoveeAuthClient, _derive_client_id
from custom_components.govee.api.exceptions import GoveeApiError, GoveeAuthError
from custom_components.govee.api.protocol import h70cx, h70cx_basic, saved_diy
from custom_components.govee.api.protocol.errors import FrameError
from custom_components.govee.diy_actions import (
    SERVICE_APPLY_DIY_EFFECT_SCHEMA,
    SERVICE_DELETE_SAVED_DIY_EFFECT_SCHEMA,
    async_apply_diy_effect_handler,
    async_delete_saved_diy_effect_handler,
    async_list_saved_diy_effects_handler,
)
from tests.test_cov_coordinator_setup import _coordinator as _real_coordinator
from tests.test_string_effect_service import COLORS, DEVICE_ID, LOOKUP, OTHER_ID, TARGETS, _coordinator

HEADERS = {"appVersion": "0"}
SAVED = saved_diy.SavedEffect("AAECAw==", (714,), 17, 0)


def _decoded(effect: saved_diy.SavedEffect) -> bytes:
    return base64.b64decode(effect.effect_str)


class TestStoredForm:
    def test_a_composed_effect_carries_its_length_under_share_49(self) -> None:
        record = h70cx.effect_record("rainbow_2", COLORS)
        effect = saved_diy.composed(record, "rainbow_2")
        assert _decoded(effect) == bytes([0x49, len(record), 0]) + record
        assert (effect.effect_code, effect.gif_id, effect.mode_type) == ((714,), 17, saved_diy.MODE_BASIC)

    @pytest.mark.parametrize(
        ("name", "code", "gif"),
        [("spiral", 744, 11), ("ribbon_1", 885, 29), ("flexing", 885, 34), ("spin_3", 885, 35), ("starlight", 714, 0)],
    )
    def test_the_code_follows_the_effect_id(self, name: str, code: int, gif: int) -> None:
        colors = [[1, 2, 3]] * 4
        effect = saved_diy.composed(h70cx.effect_record(name, colors), name)
        assert (effect.effect_code, effect.gif_id) == ((code,), gif)
        assert _decoded(effect)[0] == (0x49 if code == 714 else 0x50)

    @pytest.mark.parametrize(
        ("names", "code"),
        [(("flow_2", "starlight"), 714), (("flow_2", "spiral"), 744), (("spiral", "smudge", "flow_2"), 885)],
    )
    def test_a_layered_effect_takes_the_newest_layers_code(self, names: tuple[str, ...], code: int) -> None:
        record = h70cx.layered_record([h70cx.Layer(name, COLORS) for name in names])
        effect = saved_diy.composed(record, names[0])
        assert effect.effect_code == (code,)
        assert effect.gif_id == saved_diy.GIF_IDS[names[0]]
        assert _decoded(effect)[3:] == record

    @pytest.mark.parametrize(
        ("name", "direction", "code", "gif"),
        [("fade_1", None, 3, 18), ("flow_1", None, 19, 12), ("flow_1", "anticlockwise", 20, 12), ("breathe", None, 9, 24)],
    )
    def test_a_basic_effect_is_its_record_under_share_00(self, name: str, direction: str | None, code: int, gif: int) -> None:
        record = h70cx_basic.effect_record(name, COLORS, direction=direction)
        effect = saved_diy.basic(record, name)
        assert _decoded(effect) == b"\x00" + record
        assert (effect.effect_code, effect.gif_id, effect.mode_type) == ((code,), gif, saved_diy.MODE_BASIC)

    def test_a_combo_lists_its_members_codes(self) -> None:
        members = ["flow_1", "breathe", "marquee_3"]
        record = h70cx_basic.effect_record("combo", COLORS, combine=members)
        effect = saved_diy.basic(record, "combo", members)
        assert _decoded(effect) == b"\x00" + record
        assert (effect.effect_code, effect.mode_type) == ((0, 19, 9, 12), saved_diy.MODE_MIX)

    def test_a_basic_record_with_an_unknown_sub_effect_is_refused(self) -> None:
        with pytest.raises(FrameError, match="fade_1 cannot be saved"):
            saved_diy.basic(bytes([0, 7, 50, 3, 1, 2, 3]), "fade_1")

    def test_every_basic_and_combinable_effect_has_a_code(self) -> None:
        for name, spec in h70cx_basic.EFFECTS.items():
            assert saved_diy.basic(bytes([spec.effect, spec.sub, 1, 3, 1, 2, 3]), name).effect_code[0] > 0
        assert set(saved_diy.LIST_MIX_CODES) == {
            saved_diy.basic(h70cx_basic.effect_record(name, COLORS), name).effect_code[0]
            for name in h70cx_basic.COMBINABLE
        }

    def test_every_named_effect_but_starlight_has_a_preview_tile(self) -> None:
        assert set(saved_diy.GIF_IDS) == (set(h70cx.TEMPLATES) | set(h70cx_basic.EFFECTS)) - {"starlight"}

    def test_decode_splits_the_share_byte(self) -> None:
        assert saved_diy.decode("SQECAw==") == (0x49, bytes([1, 2, 3]))

    @pytest.mark.parametrize("stored", ["not base64!", ""])
    def test_decode_refuses_what_is_not_an_effect(self, stored: str) -> None:
        with pytest.raises(FrameError):
            saved_diy.decode(stored)


class _Response:
    def __init__(self, status: int, body: Any) -> None:
        self.status = status
        self._body = body

    async def json(self, content_type: Any = None) -> Any:
        if isinstance(self._body, Exception):
            raise self._body
        return self._body

    async def __aenter__(self) -> _Response:
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        return None


def _session(status: int = 200, body: Any = None, error: Exception | None = None) -> MagicMock:
    session = MagicMock()
    session.request = MagicMock(side_effect=error, return_value=_Response(status, body))
    return session


def _ok(data: Any) -> dict[str, Any]:
    return {"status": 200, "message": "", "data": data}


async def _create(session: MagicMock) -> int:
    return await saved_diy_cloud.async_create(
        session, HEADERS, sku="H80C5", goods_type=260, name="Candy", effect=SAVED, diy_code=77
    )


class TestAccountCalls:
    async def test_create_posts_the_effect_and_returns_its_id(self) -> None:
        session = _session(body=_ok({"effectIds": [4321], "diyIds": [9]}))
        assert await _create(session) == 4321

        method, url = session.request.call_args.args
        sent = session.request.call_args.kwargs
        assert (method, url) == ("POST", "https://app2.govee.com/appsku/v1/diys/new-diys")
        assert sent["headers"] == HEADERS
        body = sent["json"]
        assert (body["sku"], body["goodsType"], body["isCopy"]) == ("H80C5", 260, False)
        (effect,) = body["effects"]
        assert effect["createTime"] > 1_600_000_000_000
        del effect["createTime"]
        assert effect == {
            "effectCode": [714],
            "effectStr": "AAECAw==",
            "diyName": "Candy",
            "diyCode": 77,
            "groupId": -1,
            "coverUrl": "",
            "gifId": 17,
            "effectId": 0,
            "diyModeType": 0,
            "frameNum": 0,
            "goodsType": 0,
            "on": 0,
            "whiteNoiseId": 0,
            "diyFrom": 0,
            "voiceType": 0,
            "mixedMode": -1,
        }

    @pytest.mark.parametrize("data", [{}, {"effectIds": []}, {"effectIds": ["x"]}, None])
    async def test_create_without_an_id_fails(self, data: Any) -> None:
        with pytest.raises(GoveeApiError, match="no effect id"):
            await _create(_session(body=_ok(data)))

    @pytest.mark.parametrize(
        ("status", "body"),
        [(401, None), (200, {"status": 401, "message": "expired"})],
    )
    async def test_a_refused_token_is_an_auth_error(self, status: int, body: Any) -> None:
        with pytest.raises(GoveeAuthError):
            await _create(_session(status, body))

    @pytest.mark.parametrize(
        ("session", "reason"),
        [
            (_session(body={"status": 400, "message": "name taken"}), "name taken"),
            (_session(body={"status": 500}), "status 500"),
            (_session(500, {"message": "down"}), "down"),
            (_session(body=[1]), "no JSON body"),
            (_session(body=ValueError("html")), "no JSON body"),
            (_session(error=aiohttp.ClientConnectionError()), "ClientConnectionError"),
            (_session(error=TimeoutError()), "TimeoutError"),
        ],
    )
    async def test_any_other_failure_is_an_api_error(self, session: MagicMock, reason: str) -> None:
        with pytest.raises(GoveeApiError, match=reason) as err:
            await _create(session)
        assert not isinstance(err.value, GoveeAuthError)

    async def test_list_flattens_the_groups(self) -> None:
        groups = [
            {"groupId": -1, "groupName": "", "diys": [{"effectId": 1}, "junk"]},
            {"groupId": 5, "groupName": "Xmas", "diys": [{"effectId": 2, "diyName": "Tree"}]},
            {"groupId": 6, "groupName": "Empty", "diys": None},
            "junk",
        ]
        session = _session(body=_ok({"diys": groups, "maxNums": 200}))
        found = await saved_diy_cloud.async_list(session, HEADERS, sku="H80C5", goods_type=260)

        assert found == [
            {"effectId": 1, "groupId": -1, "groupName": ""},
            {"effectId": 2, "diyName": "Tree", "groupId": 5, "groupName": "Xmas"},
        ]
        body = session.request.call_args.kwargs["json"]
        assert (body["sku"], body["goodsType"], body["sortType"]) == ("H80C5", 260, 1)
        assert {714, 744, 885, 0} <= set(body["effectCode"])
        assert body["mixEffectCode"] == list(saved_diy.LIST_MIX_CODES)

    async def test_list_of_an_empty_account_is_empty(self) -> None:
        assert await saved_diy_cloud.async_list(_session(body=_ok({})), HEADERS, sku="H80C5", goods_type=260) == []

    @pytest.mark.parametrize("body", [None, ValueError("empty")])
    async def test_delete_takes_a_bodyless_200_as_done(self, body: Any) -> None:
        await saved_diy_cloud.async_delete(_session(body=body), HEADERS, 4321)

    async def test_delete_with_a_bodyless_error_status_fails(self) -> None:
        with pytest.raises(GoveeApiError, match="HTTP 500"):
            await saved_diy_cloud.async_delete(_session(500, None), HEADERS, 4321)

    async def test_delete_names_the_effect_in_the_query(self) -> None:
        session = _session(body={"status": 200})
        await saved_diy_cloud.async_delete(session, HEADERS, 4321)
        assert session.request.call_args.args == ("DELETE", "https://app2.govee.com/app/rest/v1/diy")
        assert session.request.call_args.kwargs["params"] == {"effectId": "4321"}


def _account_coordinator(*, token: str | None = "tok") -> MagicMock:
    coordinator = _coordinator()
    coordinator.config_entry.data = {"email": "user@example.com"}

    async def _bff_call(operation: Any, _what: str) -> Any:
        return None if token is None else await operation(None, token)

    coordinator._async_bff_call = AsyncMock(side_effect=_bff_call)
    return coordinator


@pytest.fixture
def session() -> Any:
    with patch.object(saved_diy_actions, "async_get_clientsession", return_value=MagicMock(name="session")) as get:
        yield get.return_value


def _call(schema: Any = SERVICE_APPLY_DIY_EFFECT_SCHEMA, **data: Any) -> SimpleNamespace:
    return SimpleNamespace(data=schema({"device_id": [DEVICE_ID], **data}))


async def _handle(handler: Any, coordinator: MagicMock, call: SimpleNamespace, *, routed: bool = True) -> tuple[Any, AsyncMock]:
    route = AsyncMock(return_value=routed)

    def _lookup(_hass: Any, raw_id: str) -> Any:
        return (coordinator, raw_id) if raw_id in coordinator.devices else None

    with patch(TARGETS, return_value=list(coordinator.devices)), patch(LOOKUP, side_effect=_lookup), patch.object(
        diy_actions.raw_router, "async_route_frames", route
    ):
        return await handler(MagicMock(), call), route


class TestSaveAs:
    async def test_the_effect_is_committed_and_saved_under_one_new_code(self, session: Any) -> None:
        coordinator = _account_coordinator()
        create = AsyncMock(return_value=4321)
        with patch.object(saved_diy_cloud, "async_create", create), patch.object(
            diy_actions, "new_diy_code", return_value=77
        ):
            response, route = await _handle(
                async_apply_diy_effect_handler,
                coordinator,
                _call(effect="spiral", colors=COLORS, save_as="  Candy cane "),
            )

        record = h70cx.effect_record("spiral", COLORS)
        assert list(route.await_args.args[4]) == h70cx.upload_frames(record, 77)
        assert response == {"effect_id": 4321, "diy_code": 77, "name": "Candy cane"}
        assert create.await_args.args[0] is session
        headers = create.await_args.args[1]
        assert headers["Authorization"] == "Bearer tok"
        assert headers["clientId"] == _derive_client_id("user@example.com")
        assert create.await_args.kwargs == {
            "sku": "H80C5",
            "goods_type": 260,
            "name": "Candy cane",
            "effect": saved_diy.composed(record, "spiral"),
            "diy_code": 77,
        }

    @pytest.mark.parametrize(
        ("data", "saved"),
        [
            (
                {"effect": "combo", "colors": COLORS, "combine": ["flow_1", "breathe"]},
                saved_diy.basic(
                    h70cx_basic.effect_record("combo", COLORS, combine=["flow_1", "breathe"]), "combo", ["flow_1", "breathe"]
                ),
            ),
            (
                {"layers": [{"effect": "flow_2", "colors": COLORS}, {"effect": "starlight", "colors": COLORS}]},
                saved_diy.composed(
                    h70cx.layered_record([h70cx.Layer("flow_2", COLORS), h70cx.Layer("starlight", COLORS)]), "flow_2"
                ),
            ),
        ],
    )
    async def test_a_combo_and_a_layered_effect_save_their_own_form(self, session: Any, data: Any, saved: Any) -> None:
        create = AsyncMock(return_value=1)
        with patch.object(saved_diy_cloud, "async_create", create):
            _response, route = await _handle(
                async_apply_diy_effect_handler, _account_coordinator(), _call(**data, save_as="Mix")
            )
        assert create.await_args.kwargs["effect"] == saved
        code = create.await_args.kwargs["diy_code"]
        assert saved_diy.MIN_DIY_CODE <= code <= saved_diy.MAX_DIY_CODE
        assert bytes(route.await_args.args[4][-1][:5]) == bytes([0x33, 0x05, 0x0A, code, 0])

    async def test_without_save_as_nothing_reaches_the_account(self, session: Any) -> None:
        coordinator = _account_coordinator()
        response, route = await _handle(
            async_apply_diy_effect_handler, coordinator, _call(effect="rainbow", colors=COLORS)
        )
        assert response == {}
        assert list(route.await_args.args[4]) == h70cx.effect_frames("rainbow", COLORS)
        coordinator._async_bff_call.assert_not_awaited()

    async def test_an_effect_the_lamp_did_not_take_is_not_saved(self, session: Any) -> None:
        coordinator = _account_coordinator()
        with pytest.raises(HomeAssistantError) as err:
            await _handle(
                async_apply_diy_effect_handler,
                coordinator,
                _call(effect="rainbow", colors=COLORS, save_as="X"),
                routed=False,
            )
        assert err.value.translation_key == "command_failed"
        coordinator._async_bff_call.assert_not_awaited()

    @pytest.mark.parametrize("sku", ["H60B0", "H70C5"])
    async def test_a_light_that_keeps_no_saved_effects_refuses_save_as(self, sku: str, monkeypatch: Any) -> None:
        coordinator = _coordinator({DEVICE_ID: sku})
        if sku == "H70C5":
            profile = MagicMock(scene_upload=True)
            monkeypatch.setattr(diy_actions, "profile_for", lambda _sku: profile)
            data = {"effect": "rainbow", "colors": COLORS}
        else:
            data = {"ring_mode": "gradient", "ring_colors": COLORS}
        with pytest.raises(ServiceValidationError) as err:
            await _handle(async_apply_diy_effect_handler, coordinator, _call(**data, save_as="X"))
        assert err.value.translation_key == "save_diy_unsupported"

    @pytest.mark.parametrize("name", ["", "   ", "n" * 31])
    def test_the_name_is_1_to_30_characters(self, name: str) -> None:
        with pytest.raises(Exception, match="length"):
            SERVICE_APPLY_DIY_EFFECT_SCHEMA({"effect": "rainbow", "colors": COLORS, "save_as": name})

    async def test_a_login_that_stopped_working_says_so(self, session: Any) -> None:
        with pytest.raises(HomeAssistantError) as err:
            await _handle(
                async_apply_diy_effect_handler,
                _account_coordinator(token=None),
                _call(effect="rainbow", colors=COLORS, save_as="X"),
            )
        assert err.value.translation_key == "saved_diy_login_failed"

    async def test_an_entry_without_an_account_email_is_told_to_add_one(self, session: Any) -> None:
        coordinator = _account_coordinator(token=None)
        coordinator.config_entry.data = {}
        with pytest.raises(HomeAssistantError) as err:
            await _handle(
                async_list_saved_diy_effects_handler, coordinator, _call(diy_actions.SERVICE_LIST_SAVED_DIY_EFFECTS_SCHEMA)
            )
        assert err.value.translation_key == "saved_diy_no_account"

    async def test_a_refusal_by_the_account_carries_its_reason(self, session: Any) -> None:
        create = AsyncMock(side_effect=GoveeApiError("saving the DIY effect: name taken"))
        with patch.object(saved_diy_cloud, "async_create", create), pytest.raises(HomeAssistantError) as err:
            await _handle(
                async_apply_diy_effect_handler,
                _account_coordinator(),
                _call(effect="rainbow", colors=COLORS, save_as="X"),
            )
        assert err.value.translation_key == "save_diy_failed"
        assert err.value.translation_placeholders["reason"] == "saving the DIY effect: name taken"

    async def test_a_plain_apply_never_builds_the_account_form(self, session: Any) -> None:
        with patch.object(saved_diy, "basic", side_effect=AssertionError), patch.object(
            saved_diy, "composed", side_effect=AssertionError
        ):
            await _handle(async_apply_diy_effect_handler, _account_coordinator(), _call(effect="fade_1", colors=COLORS))
            await _handle(async_apply_diy_effect_handler, _account_coordinator(), _call(effect="spiral", colors=COLORS))

    async def test_an_effect_with_no_account_form_is_refused_before_the_lamp_changes(self, session: Any) -> None:
        with patch.object(saved_diy, "basic", side_effect=FrameError("fade_1 cannot be saved")), pytest.raises(
            ServiceValidationError
        ) as err:
            route = AsyncMock()
            with patch.object(diy_actions.raw_router, "async_route_frames", route):
                await _handle(
                    async_apply_diy_effect_handler,
                    _account_coordinator(),
                    _call(effect="fade_1", colors=COLORS, save_as="X"),
                )
        assert err.value.translation_key == "invalid_string_effect"

    async def test_a_failed_list_names_the_account_error(self, session: Any) -> None:
        with patch.object(saved_diy_cloud, "async_list", AsyncMock(side_effect=GoveeApiError("down"))), pytest.raises(
            HomeAssistantError
        ) as err:
            await _handle(
                async_list_saved_diy_effects_handler,
                _account_coordinator(),
                _call(diy_actions.SERVICE_LIST_SAVED_DIY_EFFECTS_SCHEMA),
            )
        assert err.value.translation_key == "saved_diy_failed"

    def test_a_new_code_is_in_the_apps_range(self) -> None:
        codes = {saved_diy_actions.new_diy_code() for _ in range(2000)}
        assert min(codes) >= saved_diy.MIN_DIY_CODE and max(codes) <= saved_diy.MAX_DIY_CODE
        assert len(codes) > 200


ACCOUNT_ITEMS = [
    {"effectId": 11, "diyName": "A", "diyCode": 5, "effectCode": [714], "gifId": 17, "groupName": "", "effectStr": "SQECAw=="},
    {"effectId": 12, "diyName": "B", "effectStr": "AAECAw=="},
    {"effectId": 13, "diyName": "C", "effectStr": "KAECAw=="},
    {"effectId": 14, "diyName": "D", "effectStr": "???"},
    {"effectId": 15, "diyName": "E"},
]


class TestListAndDelete:
    async def test_list_returns_a_summary_per_saved_effect(self, session: Any) -> None:
        listing = AsyncMock(return_value=ACCOUNT_ITEMS)
        with patch.object(saved_diy_cloud, "async_list", listing):
            response, _route = await _handle(
                async_list_saved_diy_effects_handler,
                _account_coordinator(),
                _call(diy_actions.SERVICE_LIST_SAVED_DIY_EFFECTS_SCHEMA),
            )

        assert listing.await_args.kwargs == {"sku": "H80C5", "goods_type": 260}
        assert response["effects"][0] == {
            "effect_id": 11,
            "name": "A",
            "diy_code": 5,
            "effect_code": [714],
            "gif_id": 17,
            "group": "",
            "kind": "composed",
            "effect_str": "SQECAw==",
        }
        assert [item["kind"] for item in response["effects"]] == ["composed", "basic", "other", "unreadable", "unknown"]

    async def test_delete_removes_one_of_the_lights_effects(self, session: Any) -> None:
        delete = AsyncMock()
        with patch.object(saved_diy_cloud, "async_list", AsyncMock(return_value=ACCOUNT_ITEMS)), patch.object(
            saved_diy_cloud, "async_delete", delete
        ):
            await _handle(
                async_delete_saved_diy_effect_handler,
                _account_coordinator(),
                _call(SERVICE_DELETE_SAVED_DIY_EFFECT_SCHEMA, effect_id="12"),
            )
        assert delete.await_args.args[2] == 12
        assert delete.await_args.args[1]["Authorization"] == "Bearer tok"

    async def test_delete_refuses_an_id_the_light_does_not_have(self, session: Any) -> None:
        delete = AsyncMock()
        with patch.object(saved_diy_cloud, "async_list", AsyncMock(return_value=ACCOUNT_ITEMS)), patch.object(
            saved_diy_cloud, "async_delete", delete
        ), pytest.raises(ServiceValidationError) as err:
            await _handle(
                async_delete_saved_diy_effect_handler,
                _account_coordinator(),
                _call(SERVICE_DELETE_SAVED_DIY_EFFECT_SCHEMA, effect_id=99),
            )
        assert err.value.translation_key == "saved_diy_unknown"
        delete.assert_not_awaited()

    @pytest.mark.parametrize("skus", [{DEVICE_ID: "H60B0"}, {DEVICE_ID: "H6076"}, {DEVICE_ID: "H80C5", OTHER_ID: "H60B0"}])
    async def test_the_target_must_be_one_light_that_keeps_saved_effects(self, skus: dict[str, str]) -> None:
        with pytest.raises(HomeAssistantError) as err:
            await _handle(
                async_list_saved_diy_effects_handler,
                _coordinator(skus),
                _call(diy_actions.SERVICE_LIST_SAVED_DIY_EFFECTS_SCHEMA),
            )
        assert err.value.translation_key in {"saved_diy_no_target", "diy_target_ambiguous"}

    def test_the_actions_are_registered_with_their_response_modes(self) -> None:
        hass = MagicMock()
        diy_actions.async_setup_diy_actions(hass)
        modes = {
            call.args[1]: call.kwargs.get("supports_response", SupportsResponse.NONE)
            for call in hass.services.async_register.call_args_list
        }
        assert modes == {
            "apply_diy_effect": SupportsResponse.OPTIONAL,
            "list_saved_diy_effects": SupportsResponse.ONLY,
            "delete_saved_diy_effect": SupportsResponse.NONE,
        }

    async def test_the_registered_actions_reach_their_handlers(self, session: Any) -> None:
        hass = MagicMock()
        diy_actions.async_setup_diy_actions(hass)
        registered = {call.args[1]: call.args[2] for call in hass.services.async_register.call_args_list}
        coordinator = _account_coordinator()
        with patch.object(saved_diy_cloud, "async_list", AsyncMock(return_value=ACCOUNT_ITEMS)), patch.object(
            saved_diy_cloud, "async_delete", AsyncMock()
        ) as delete:
            listed, _ = await _handle(
                lambda _hass, call: registered["list_saved_diy_effects"](call),
                coordinator,
                _call(diy_actions.SERVICE_LIST_SAVED_DIY_EFFECTS_SCHEMA),
            )
            await _handle(
                lambda _hass, call: registered["delete_saved_diy_effect"](call),
                coordinator,
                _call(SERVICE_DELETE_SAVED_DIY_EFFECT_SCHEMA, effect_id=11),
            )
        assert len(listed["effects"]) == len(ACCOUNT_ITEMS)
        delete.assert_awaited_once()


class TestUpstreamMembers:
    async def test_the_token_call_is_the_coordinators(self) -> None:
        coordinator = _real_coordinator()
        coordinator._iot_credentials = None
        assert await upstream_access.bff_call(coordinator, AsyncMock(), "test") is None

    async def test_the_real_token_call_hands_over_a_client_and_the_token(self) -> None:
        coordinator = _real_coordinator()
        coordinator._iot_credentials = SimpleNamespace(token="tok")
        seen: list[Any] = []

        async def _operation(client: Any, token: str) -> str:
            seen.append((type(client), token))
            return "done"

        assert await upstream_access.bff_call(coordinator, _operation, "test") == "done"
        assert seen == [(GoveeAuthClient, "tok")]

    async def test_the_real_token_call_logs_in_again_once_on_a_refused_token(self) -> None:
        coordinator = _real_coordinator()
        coordinator._iot_credentials = SimpleNamespace(token="old")
        tokens: list[str] = []

        async def _refresh() -> bool:
            coordinator._iot_credentials = SimpleNamespace(token="new")
            return True

        async def _operation(_client: Any, token: str) -> str:
            tokens.append(token)
            if token == "old":
                raise GoveeAuthError("refused")
            return "done"

        with patch.object(coordinator, "_async_refresh_iot_credentials", _refresh):
            assert await upstream_access.bff_call(coordinator, _operation, "test") == "done"
        assert tokens == ["old", "new"]

    async def test_the_real_token_call_gives_none_when_the_new_login_fails(self) -> None:
        coordinator = _real_coordinator()
        coordinator._iot_credentials = SimpleNamespace(token="old")
        with patch.object(coordinator, "_async_refresh_iot_credentials", AsyncMock(return_value=False)):
            result = await upstream_access.bff_call(coordinator, AsyncMock(side_effect=GoveeAuthError("refused")), "test")
        assert result is None

    def test_the_headers_are_the_auth_clients(self) -> None:
        headers = upstream_access.app_headers("user@example.com")
        assert headers["clientId"] == upstream_access.app_headers("user@example.com")["clientId"]
        assert headers["clientId"] != upstream_access.app_headers(None)["clientId"]
        assert set(headers) == set(GoveeAuthClient._build_govee_headers())

    async def test_a_missing_token_call_raises(self) -> None:
        with pytest.raises(RuntimeError, match="_async_bff_call"):
            await upstream_access.bff_call(SimpleNamespace(), AsyncMock(), "test")

    def test_missing_header_builders_raise(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from custom_components.govee.api import auth

        monkeypatch.delattr(auth, "_derive_client_id")
        with pytest.raises(RuntimeError, match="header builders"):
            upstream_access.app_headers("user@example.com")
