"""Issue #242: Govee's "Device is offline" reply to a control command.

The control endpoint answers HTTP 200 with body ``{"code": 400, "msg":
"Device is offline. Please check the Wi-Fi connection."}``. That becomes
GoveeDeviceOfflineError; the coordinator shows the device offline at once and
the entity raises the translated ``device_offline`` error.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import custom_components.govee.coordinator as coord_mod
from custom_components.govee.api.exceptions import GoveeApiError, GoveeDeviceOfflineError
from custom_components.govee.entity import GoveeEntity
from custom_components.govee.models import PowerCommand, RGBColor, SegmentColorCommand

from .test_client_request_policy import _client, _response
from .test_cov_coordinator_control import DEV, _add, _coordinator, _device

OFFLINE = "Device is offline. Please check the Wi-Fi connection."


class TestClient:
    @pytest.mark.asyncio
    async def test_body_code_400_offline(self):
        client = _client(AsyncMock())
        with pytest.raises(GoveeDeviceOfflineError) as err:
            await client._handle_response(_response("POST", 200, {"code": 400, "msg": OFFLINE}))
        assert str(err.value) == OFFLINE
        assert err.value.code == 400

    @pytest.mark.asyncio
    async def test_http_400_offline(self):
        client = _client(AsyncMock())
        with pytest.raises(GoveeDeviceOfflineError):
            await client._handle_response(_response("POST", 400, {"code": 400, "message": OFFLINE}))

    @pytest.mark.asyncio
    async def test_other_errors_stay_generic(self):
        client = _client(AsyncMock())
        with pytest.raises(GoveeApiError) as err:
            await client._handle_response(_response("POST", 200, {"code": 400, "msg": "Parameter value out of range"}))
        assert not isinstance(err.value, GoveeDeviceOfflineError)


class TestCoordinator:
    @pytest.mark.asyncio
    async def test_offline_reply_marks_the_device_offline(self):
        coord = _coordinator()
        state = _add(coord, _device())
        coord._api_client.control_device = AsyncMock(side_effect=GoveeDeviceOfflineError(OFFLINE))

        assert await coord.async_control_device(DEV, PowerCommand(power_on=True)) is False

        assert state.online is False
        coord.async_set_updated_data.assert_called_once()
        assert coord._transport.get(DEV, "cloud_api").last_failure_reason == "device_offline"

    @pytest.mark.asyncio
    async def test_already_offline_does_not_notify_again(self):
        coord = _coordinator()
        state = _add(coord, _device())
        state.online = False
        coord._api_client.control_device = AsyncMock(side_effect=GoveeDeviceOfflineError(OFFLINE))

        assert await coord.async_control_device(DEV, PowerCommand(power_on=False)) is False

        coord.async_set_updated_data.assert_not_called()

    @pytest.mark.asyncio
    async def test_segment_command_offline(self, monkeypatch):
        monkeypatch.setattr(coord_mod, "SEGMENT_COMMAND_PACING_SECONDS", 0)
        coord = _coordinator()
        state = _add(coord, _device())
        coord._api_client.control_device = AsyncMock(side_effect=GoveeDeviceOfflineError(OFFLINE))

        command = SegmentColorCommand(segment_indices=(0,), color=RGBColor(1, 2, 3))
        assert await coord._dispatch_segment_command(DEV, "H6072", command) is False

        assert state.online is False


class TestEntityError:
    @staticmethod
    def _entity(online: bool | None, *, is_group: bool = False) -> GoveeEntity:
        entity = object.__new__(GoveeEntity)
        entity._device_id = DEV
        entity._device = SimpleNamespace(name="Desk", is_group=is_group)
        state = None if online is None else SimpleNamespace(online=online)
        entity.coordinator = MagicMock(get_state=MagicMock(return_value=state))
        return entity

    @pytest.mark.parametrize(
        ("online", "is_group", "key"),
        [
            (False, False, "device_offline"),
            (True, False, "command_failed"),
            (None, False, "command_failed"),
            (False, True, "command_failed"),
        ],
    )
    def test_translation_key(self, online, is_group, key):
        err = self._entity(online, is_group=is_group)._command_failed()
        assert err.translation_key == key
        assert err.translation_placeholders == {"device": "Desk"}
