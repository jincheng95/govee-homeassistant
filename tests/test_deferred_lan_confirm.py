"""Send-blocking LAN writes: the confirm runs behind the caller (fork feature).

Measured live from a lamp-off start: ``child_power.async_ensure_device_powered``
awaited the WHOLE power pipeline — send, the SKU's echo settle (~1.5 s on an
H60B0, see :mod:`.lan_confirm`), then the confirm read — before the zone's own
raw frames were allowed to follow, putting them ~1.6 s after the trigger.

The frames only need the power datagram to have been SENT (~30 ms) ahead of
them. So ``defer_lan_confirm=True`` returns after the send and hands the settle
+ confirm to a coordinator-owned background task. What must NOT change: the
confirm still happens, a genuinely failed confirm still re-sends over MQTT/REST
and still counts toward the issue-#57 write-suppression streak, and the
child-power latch still holds.
"""

from __future__ import annotations

import asyncio
import dataclasses
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.govee.api.protocol import profiles as profiles_mod
from custom_components.govee.models import (
    ColorCommand,
    GoveeCapability,
    GoveeDevice,
    GoveeDeviceState,
    PowerCommand,
    RGBColor,
)
from custom_components.govee.models.device import (
    CAPABILITY_ON_OFF,
    CAPABILITY_RANGE,
    INSTANCE_BRIGHTNESS,
    INSTANCE_POWER,
)

DEVICE_ID = "AA:BB:DD:EE:FF:44:55:66"
OTHER_DEVICE_ID = "AA:BB:CC:DD:EE:FF:00:11"
IP = "10.0.0.5"
SKU = "H60B0"

# Long enough that "did the caller wait for it?" is unambiguous, short enough to
# keep the suite fast. The real H60B0 lag is 1.5 s.
TEST_ECHO_LAG = 0.4
# The caller must return in a small fraction of the settle window.
SEND_ONLY_BUDGET = TEST_ECHO_LAG / 4


class _SlowReadClient:
    """LAN client fake: records sends/reads, answers reads with ``read_reply``."""

    def __init__(self, *, read_reply: Any = None) -> None:
        self.available = True
        self.read_reply = read_reply
        self.send_calls: list[tuple[str, str, dict[str, Any]]] = []
        self.read_calls: list[tuple[str, float]] = []

    async def async_send_command(self, ip: str, cmd: str, data: dict[str, Any]) -> bool:
        self.send_calls.append((ip, cmd, data))
        return True

    async def async_read_one(self, ip: str, timeout: float = 0.5) -> Any:
        self.read_calls.append((ip, timeout))
        return self.read_reply


def _status(**kw: Any) -> Any:
    from custom_components.govee.api.lan_client import LanDevStatus

    defaults = dict(on=True, brightness_0_100=80, color=RGBColor(255, 0, 0), color_temp_kelvin=None)
    defaults.update(kw)
    return LanDevStatus(**defaults)


def _ready_coord() -> tuple[Any, list[asyncio.Task]]:
    """A coordinator wired for a successful LAN write, tracking its bg tasks."""
    import time as _time

    import custom_components.govee.coordinator as coord_mod
    from custom_components.govee.api.lan_client import LanDeviceInfo

    tasks: list[asyncio.Task] = []

    config_entry = MagicMock()
    config_entry.entry_id = "test_entry"
    config_entry.options = {}
    config_entry.async_create_background_task = lambda hass, coro, name=None: tasks.append(
        asyncio.get_running_loop().create_task(coro, name=name)
    )

    coord = coord_mod.GoveeCoordinator(
        hass=MagicMock(),
        config_entry=config_entry,
        api_client=MagicMock(),
        iot_credentials=None,
        poll_interval=60,
    )
    coord._devices[DEVICE_ID] = GoveeDevice(
        device_id=DEVICE_ID,
        sku=SKU,
        name="Uplighter Floor Lamp",
        device_type="devices.types.light",
        capabilities=(
            GoveeCapability(type=CAPABILITY_ON_OFF, instance=INSTANCE_POWER, parameters={}),
            GoveeCapability(
                type=CAPABILITY_RANGE,
                instance=INSTANCE_BRIGHTNESS,
                parameters={"range": {"min": 0, "max": 100}},
            ),
        ),
    )
    coord._states[DEVICE_ID] = GoveeDeviceState.create_empty(DEVICE_ID)
    coord.async_set_updated_data = MagicMock()
    coord._api_client.control_device = AsyncMock(return_value=True)
    coord._lan_devices[DEVICE_ID] = LanDeviceInfo(
        device_id=DEVICE_ID,
        ip=IP,
        mac=DEVICE_ID,
        sku=SKU,
        firmware="1.03.08",
        last_correlated_ts=_time.monotonic(),
    )
    # Mark LAN available so the write-health gate passes (as a prior read would).
    coord._record_transport_success(DEVICE_ID, "lan")
    return coord, tasks


@pytest.fixture
def slow_settle(monkeypatch):
    """Give the SKU a measurable echo lag so the settle wait is observable."""
    monkeypatch.setitem(
        profiles_mod.PROFILES,
        SKU,
        dataclasses.replace(profiles_mod.PROFILES[SKU], echo_lag_seconds=TEST_ECHO_LAG),
    )


class TestCallerIsNotBlockedByTheSettle:
    """The caller resumes on the SEND; the settle happens behind it."""

    @pytest.mark.asyncio
    async def test_the_inline_path_still_waits_out_the_settle(self, slow_settle):
        # The baseline this fix is measured against: upstream's behaviour is
        # unchanged when the flag is not passed.
        coord, tasks = _ready_coord()
        coord._lan_client = _SlowReadClient(read_reply=_status(on=True))

        started = time.monotonic()
        result = await coord.async_control_device(DEVICE_ID, PowerCommand(power_on=True))
        elapsed = time.monotonic() - started

        assert result is True
        assert elapsed >= TEST_ECHO_LAG
        assert tasks == []

    @pytest.mark.asyncio
    async def test_deferred_confirm_returns_on_the_send(self, slow_settle):
        coord, tasks = _ready_coord()
        client = _SlowReadClient(read_reply=_status(on=True))
        coord._lan_client = client

        started = time.monotonic()
        result = await coord.async_control_device(DEVICE_ID, PowerCommand(power_on=True), defer_lan_confirm=True)
        elapsed = time.monotonic() - started

        assert result is True
        # The datagram is on the wire...
        assert client.send_calls == [(IP, "turn", {"value": 1})]
        # ...and the caller is already back, long before the settle could end.
        assert elapsed < SEND_ONLY_BUDGET
        # The confirm read has NOT been taken yet — it is still settling.
        assert client.read_calls == []
        assert len(tasks) == 1

        await asyncio.gather(*tasks)

    @pytest.mark.asyncio
    async def test_optimistic_state_is_applied_before_the_caller_returns(self, slow_settle):
        coord, tasks = _ready_coord()
        coord._lan_client = _SlowReadClient(read_reply=_status(on=True))

        await coord.async_control_device(DEVICE_ID, PowerCommand(power_on=True), defer_lan_confirm=True)

        assert coord._states[DEVICE_ID].power_state is True

        await asyncio.gather(*tasks)


class TestTheConfirmStillHappens:
    """Deferred is not skipped: the background task does the whole tail."""

    @pytest.mark.asyncio
    async def test_confirm_read_runs_after_the_settle(self, slow_settle):
        coord, tasks = _ready_coord()
        client = _SlowReadClient(read_reply=_status(on=True))
        coord._lan_client = client

        await coord.async_control_device(DEVICE_ID, PowerCommand(power_on=True), defer_lan_confirm=True)
        await asyncio.gather(*tasks)

        assert client.read_calls and client.read_calls[0][0] == IP
        # A confirmed write clears the miss streak and stamps the LAN send.
        assert coord._lan_write_misses.get(DEVICE_ID, 0) == 0
        health = coord._transport.get(DEVICE_ID, "lan")
        assert health is not None and health.last_send_ts is not None
        # No cloud re-send: the LAN write was confirmed.
        coord._api_client.control_device.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_failed_confirm_still_falls_back_to_the_cloud(self, slow_settle):
        coord, tasks = _ready_coord()
        client = _SlowReadClient(read_reply=None)  # never confirms
        coord._lan_client = client

        result = await coord.async_control_device(DEVICE_ID, PowerCommand(power_on=True), defer_lan_confirm=True)
        # The caller was told "sent" — the confirm is no longer part of the answer.
        assert result is True
        coord._api_client.control_device.assert_not_awaited()

        await asyncio.gather(*tasks)

        # ...and the re-send happened behind it, exactly as the inline path does.
        coord._api_client.control_device.assert_awaited_once()
        assert coord._api_client.control_device.await_args.args[0] == DEVICE_ID

    @pytest.mark.asyncio
    async def test_a_failed_confirm_still_counts_toward_write_suppression(self, slow_settle):
        # lan_confirm's miss semantics are unchanged: the read is taken AFTER the
        # settle, so an unanswered one is genuine evidence and arms issue #57.
        coord, tasks = _ready_coord()
        coord._lan_client = _SlowReadClient(read_reply=None)

        await coord.async_control_device(DEVICE_ID, PowerCommand(power_on=True), defer_lan_confirm=True)
        await asyncio.gather(*tasks)

        assert coord._lan_write_misses.get(DEVICE_ID) == 1

    @pytest.mark.asyncio
    async def test_a_cloud_failure_in_the_background_does_not_escape(self, slow_settle):
        # GoveeAuthError, not GoveeApiError: the cloud tier converts it to
        # ConfigEntryAuthFailed and re-raises, so this is the one fallback
        # failure that actually reaches the background task's own handler.
        from homeassistant.exceptions import ConfigEntryAuthFailed

        from custom_components.govee.api.exceptions import GoveeAuthError

        coord, tasks = _ready_coord()
        coord._lan_client = _SlowReadClient(read_reply=None)
        coord._api_client.control_device = AsyncMock(side_effect=GoveeAuthError("bad key"))

        await coord.async_control_device(DEVICE_ID, PowerCommand(power_on=True), defer_lan_confirm=True)

        # Nothing awaits this task, so an escaping exception would only ever
        # surface as an unhandled-task traceback — gather it non-fatally and
        # look at what came back rather than letting it fail the test as an error.
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # The raising path was genuinely taken...
        coord._api_client.control_device.assert_awaited_once()
        # ...and it raises ConfigEntryAuthFailed rather than being swallowed
        # by the cloud tier, so the task's handler is what contained it.
        with pytest.raises(ConfigEntryAuthFailed):
            await coord._async_control_via_cloud(
                DEVICE_ID, coord._devices[DEVICE_ID], PowerCommand(power_on=True)
            )
        assert results == [None]
        assert tasks[0].exception() is None


class TestTheFallbackNeverOverridesANewerCommand:
    """The deferred re-send carries the ORIGINAL command, so it must expire."""

    @pytest.mark.asyncio
    async def test_a_superseded_command_is_not_re_sent(self, slow_settle):
        # The hazard: a deferred power-ON whose confirm fails re-lights a lamp
        # the user turned off while the settle was still running.
        coord, tasks = _ready_coord()
        coord._lan_client = _SlowReadClient(read_reply=None)  # never confirms

        await coord.async_control_device(DEVICE_ID, PowerCommand(power_on=True), defer_lan_confirm=True)
        # A newer command for the same device, issued during the settle. Colour
        # has no LAN mapping, so it goes straight out over the cloud.
        await coord.async_control_device(DEVICE_ID, ColorCommand(color=RGBColor(0, 0, 255)))

        await asyncio.gather(*tasks)

        # Only the newer command reached the cloud; the stale power-on did not.
        sent = [call.args[2] for call in coord._api_client.control_device.await_args_list]
        assert sent == [ColorCommand(color=RGBColor(0, 0, 255))]

    @pytest.mark.asyncio
    async def test_an_unsuperseded_command_still_re_sends(self, slow_settle):
        # The generation guard must not disarm the fallback it guards.
        coord, tasks = _ready_coord()
        coord._lan_client = _SlowReadClient(read_reply=None)

        await coord.async_control_device(DEVICE_ID, PowerCommand(power_on=True), defer_lan_confirm=True)
        await asyncio.gather(*tasks)

        sent = [call.args[2] for call in coord._api_client.control_device.await_args_list]
        assert sent == [PowerCommand(power_on=True)]

    @pytest.mark.asyncio
    async def test_a_command_for_another_device_does_not_expire_it(self, slow_settle):
        # The counter is per device: unrelated traffic must not cancel a
        # legitimate fallback.
        coord, tasks = _ready_coord()
        coord._lan_client = _SlowReadClient(read_reply=None)

        await coord.async_control_device(DEVICE_ID, PowerCommand(power_on=True), defer_lan_confirm=True)
        coord._command_generation[OTHER_DEVICE_ID] = 99

        await asyncio.gather(*tasks)

        coord._api_client.control_device.assert_awaited_once()


class TestPendingPowerOffOutlivesTheCaller:
    """Segment entities read this flag to avoid racing a power-off (issue #16)."""

    @pytest.mark.asyncio
    async def test_the_flag_survives_until_the_deferred_path_concludes(self, slow_settle):
        coord, tasks = _ready_coord()
        coord._lan_client = _SlowReadClient(read_reply=None)

        await coord.async_control_device(DEVICE_ID, PowerCommand(power_on=False), defer_lan_confirm=True)

        # The caller has returned, but the cloud re-send is still to come.
        assert coord.is_power_off_pending(DEVICE_ID) is True

        await asyncio.gather(*tasks)

        assert coord.is_power_off_pending(DEVICE_ID) is False
        assert coord._deferred_power_off == set()

    @pytest.mark.asyncio
    async def test_a_confirmed_deferred_power_off_clears_the_flag(self, slow_settle):
        coord, tasks = _ready_coord()
        coord._lan_client = _SlowReadClient(read_reply=_status(on=False))

        await coord.async_control_device(DEVICE_ID, PowerCommand(power_on=False), defer_lan_confirm=True)
        await asyncio.gather(*tasks)

        assert coord.is_power_off_pending(DEVICE_ID) is False

    @pytest.mark.asyncio
    async def test_the_inline_path_still_clears_it_itself(self, slow_settle):
        coord, tasks = _ready_coord()
        coord._lan_client = _SlowReadClient(read_reply=_status(on=False))

        await coord.async_control_device(DEVICE_ID, PowerCommand(power_on=False))

        assert coord.is_power_off_pending(DEVICE_ID) is False
        assert tasks == []


class TestChildPowerUsesIt:
    """The one caller this was built for asks for the send-only contract."""

    @pytest.mark.asyncio
    async def test_ensure_power_defers_the_confirm(self):
        from custom_components.govee import child_power

        coordinator = MagicMock()
        coordinator.async_control_device = AsyncMock(return_value=True)
        state = MagicMock()
        state.power_state = False
        coordinator.get_state = MagicMock(return_value=state)

        await child_power.async_ensure_device_powered(coordinator, DEVICE_ID)

        call = coordinator.async_control_device.await_args
        assert call.args[0] == DEVICE_ID
        assert call.args[1] == PowerCommand(power_on=True)
        assert call.kwargs["defer_lan_confirm"] is True

    @pytest.mark.asyncio
    async def test_the_latch_still_suppresses_the_second_call(self):
        from custom_components.govee import child_power

        coordinator = MagicMock()
        coordinator.async_control_device = AsyncMock(return_value=True)
        state = MagicMock()
        state.power_state = False  # still echoing "off" after the first send
        coordinator.get_state = MagicMock(return_value=state)

        await child_power.async_ensure_device_powered(coordinator, DEVICE_ID)
        await child_power.async_ensure_device_powered(coordinator, DEVICE_ID)

        coordinator.async_control_device.assert_awaited_once()
