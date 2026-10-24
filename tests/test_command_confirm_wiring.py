"""The coordinator's confirm_commands wiring (#245): off by default, MQTT/REST sends only."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.govee.api.exceptions import GoveeApiError
from custom_components.govee.command_confirm import CommandConfirmer
from custom_components.govee.const import CONF_CONFIRM_COMMANDS, CONF_ENABLE_MQTT_CONTROL
from custom_components.govee.models import BrightnessCommand, GoveeDevice, PowerCommand

from .test_cov_coordinator_control import DEV, _add, _coordinator, _device, _FakeWriteClient, _lan_ready, _mqtt


def _confirming(**options):
    coord = _coordinator(options={CONF_CONFIRM_COMMANDS: True, **options})
    coord._config_entry.async_create_background_task = MagicMock(
        side_effect=lambda _hass, coro, name: asyncio.ensure_future(coro)
    )
    state = _add(coord, _device())
    coord._record_transport_success(DEV, "mqtt")  # the light has answered over AWS IoT
    return coord, state


class TestOption:
    def test_off_by_default(self):
        coord = _coordinator()
        assert coord._command_confirmer is None
        assert coord.command_confirm_counters is None

    def test_on(self):
        coord, _state = _confirming()
        assert isinstance(coord._command_confirmer, CommandConfirmer)
        assert coord.command_confirm_counters == {}


class TestWiring:
    @pytest.mark.asyncio
    async def test_a_rest_send_starts_a_confirm(self):
        coord, _state = _confirming()
        assert await coord.async_control_device(DEV, BrightnessCommand(brightness=10)) is True
        assert set(coord._command_confirmer._pending) == {(DEV, "brightness")}
        coord._command_confirmer.cancel_all()

    @pytest.mark.asyncio
    async def test_an_mqtt_send_starts_a_confirm(self, monkeypatch):
        coord, state = _confirming(**{CONF_ENABLE_MQTT_CONTROL: True})
        state.power_state = True
        coord._mqtt_client = _mqtt()
        monkeypatch.setattr(coord, "_try_mqtt_command", AsyncMock(return_value=True))
        assert await coord.async_control_device(DEV, PowerCommand(power_on=False)) is True
        coord._try_mqtt_command.assert_awaited_once()
        coord._api_client.control_device.assert_not_awaited()
        assert set(coord._command_confirmer._pending) == {(DEV, "power")}
        coord._command_confirmer.cancel_all()

    @pytest.mark.asyncio
    async def test_a_rejected_command_starts_nothing(self):
        coord, _state = _confirming()
        coord._api_client.control_device = AsyncMock(return_value=False)
        assert await coord.async_control_device(DEV, BrightnessCommand(brightness=10)) is False
        assert coord._command_confirmer._pending == {}

    @pytest.mark.asyncio
    async def test_a_lan_write_reads_back_itself(self, monkeypatch):
        coord, _state = _confirming()
        _lan_ready(coord, _FakeWriteClient())
        monkeypatch.setattr(coord, "_try_lan_command", AsyncMock(return_value=True))
        assert await coord.async_control_device(DEV, BrightnessCommand(brightness=10)) is True
        assert coord._command_confirmer._pending == {}

    @pytest.mark.asyncio
    async def test_appliances_are_never_confirmed(self):
        coord, _state = _confirming()
        heater = GoveeDevice(
            device_id="AA:BB:CC:DD:EE:FF:71:30",
            sku="H7130",
            name="Heater",
            device_type="devices.types.heater",
            capabilities=_device().capabilities,
        )
        _add(coord, heater)
        coord._record_transport_success(heater.device_id, "mqtt")
        assert await coord.async_control_device(heater.device_id, PowerCommand(power_on=False)) is True
        assert coord._command_confirmer._pending == {}

    @pytest.mark.asyncio
    async def test_a_push_confirms_the_command(self):
        coord, _state = _confirming()
        await coord.async_control_device(DEV, BrightnessCommand(brightness=10))
        pending = coord._command_confirmer._pending[(DEV, "brightness")]

        coord._on_mqtt_state_update(DEV, {"brightness": 10})
        await asyncio.wait_for(pending.task, timeout=10)

        assert coord.command_confirm_counters == {
            DEV: {"confirmed": 1, "resent": 0, "unconfirmed": 0, "unverified": 0, "skipped_budget": 0, "superseded": 0}
        }

    @pytest.mark.asyncio
    async def test_shutdown_cancels_pending_confirms(self):
        coord, _state = _confirming()
        await coord.async_control_device(DEV, BrightnessCommand(brightness=10))
        coord._api_client.close = AsyncMock()
        await coord.async_shutdown()
        assert coord._command_confirmer._pending == {}


class TestResend:
    @pytest.mark.asyncio
    async def test_resend_goes_over_rest_without_a_new_confirm(self):
        coord, state = _confirming()
        assert await coord._async_resend_command(DEV, BrightnessCommand(brightness=10)) is True
        coord._api_client.control_device.assert_awaited_once()
        assert state.brightness == 10
        assert coord._command_confirmer._pending == {}

    @pytest.mark.asyncio
    async def test_resend_failure(self):
        coord, _state = _confirming()
        coord._api_client.control_device = AsyncMock(side_effect=GoveeApiError("boom"))
        assert await coord._async_resend_command(DEV, BrightnessCommand(brightness=10)) is False
        coord._api_client.control_device = AsyncMock(return_value=False)
        assert await coord._async_resend_command(DEV, BrightnessCommand(brightness=10)) is False
        assert await coord._async_resend_command("unknown", BrightnessCommand(brightness=10)) is False

    @pytest.mark.asyncio
    async def test_hooks(self):
        coord, _state = _confirming()
        hooks = coord._command_confirmer._hooks
        assert hooks.device(DEV) is coord._devices[DEV]
        assert hooks.state(DEV) is coord._states[DEV]
        assert hooks.heard_over_mqtt(DEV) is True
        assert hooks.resend_allowed() is coord.extra_cloud_read_allowed()
        coord.async_request_status = AsyncMock(return_value=True)
        assert await hooks.async_request_status(DEV) is True
        coord.async_request_status.assert_awaited_once_with(DEV)
        coord._api_client.control_device = AsyncMock(return_value=True)
        assert await hooks.async_resend(DEV, BrightnessCommand(brightness=5)) is True
