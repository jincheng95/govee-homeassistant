"""CommandConfirmer: confirm light commands from the AWS IoT report, resend dropped ones (#245)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from custom_components.govee.command_confirm import (
    COUNTERS,
    CommandConfirmer,
    eligible,
    matches,
    report_values,
)
from custom_components.govee.models import (
    BrightnessCommand,
    ColorCommand,
    ColorTempCommand,
    GoveeDevice,
    GoveeDeviceState,
    PowerCommand,
    RGBColor,
    SceneCommand,
)
from custom_components.govee.models.commands import DeviceCommand

LIGHT = "AA:BB:CC:DD:EE:FF:00:01"
WINDOW = 0.05
QUERY_WAIT = 0.01


def _device(device_id: str = LIGHT, *, sku: str = "H6008", device_type: str = "devices.types.light", group=False):
    return GoveeDevice(
        device_id=device_id, sku=sku, name="Lamp", device_type=device_type, capabilities=(), is_group=group
    )


class FakeHooks:
    """Stands in for the coordinator; records what the confirmer asked for."""

    def __init__(self, device: GoveeDevice | None = None, *, heard: bool = True, budget: bool = True) -> None:
        self.devices = {} if device is None else {device.device_id: device}
        self.states = {d: GoveeDeviceState.create_empty(d) for d in self.devices}
        self.heard = heard
        self.budget = budget
        self.queries: list[str] = []
        self.resends: list[tuple[str, DeviceCommand]] = []
        self.resend_result = True
        self.on_query: Any = None

    def device(self, device_id: str) -> GoveeDevice | None:
        return self.devices.get(device_id)

    def state(self, device_id: str) -> GoveeDeviceState | None:
        return self.states.get(device_id)

    def heard_over_mqtt(self, device_id: str) -> bool:
        return self.heard

    async def async_request_status(self, device_id: str) -> bool:
        self.queries.append(device_id)
        if self.on_query is not None:
            self.on_query()
        return True

    def resend_allowed(self) -> bool:
        return self.budget

    async def async_resend(self, device_id: str, command: DeviceCommand) -> bool:
        self.resends.append((device_id, command))
        return self.resend_result

    def create_task(self, coro, name: str) -> asyncio.Task[None]:
        return asyncio.ensure_future(coro)


def _confirmer(hooks: FakeHooks) -> CommandConfirmer:
    return CommandConfirmer(hooks, window=WINDOW, query_wait=QUERY_WAIT)


async def _send(confirmer: CommandConfirmer, command: DeviceCommand, device_id: str = LIGHT):
    """prepare -> (dispatch) -> start, as the coordinator does."""
    pending = confirmer.prepare(device_id, command)
    confirmer.start(pending)
    return pending


async def _settle(pending) -> None:
    if pending is not None and pending.task is not None:
        await asyncio.wait_for(pending.task, timeout=1)


def _counts(confirmer: CommandConfirmer, device_id: str = LIGHT) -> dict[str, int]:
    return {k: v for k, v in confirmer.counters().get(device_id, dict.fromkeys(COUNTERS, 0)).items() if v}


class TestParsing:
    def test_report_values(self):
        assert report_values(
            {"onOff": 1, "brightness": 40, "color": {"r": 1, "g": 2, "b": 3}, "colorTemInKelvin": 3000}
        ) == {"power": True, "brightness": 40, "color": RGBColor(1, 2, 3), "color_temp": 3000}
        assert report_values({"color": 0x010203, "colorTemInKelvin": 0, "brightness": True}) == {
            "color": RGBColor(1, 2, 3)
        }
        assert report_values({"other": 1}) == {}

    @pytest.mark.parametrize(
        ("field", "reported", "wanted", "expected"),
        [
            ("brightness", 52, 50, True),
            ("brightness", 53, 50, False),
            ("color", RGBColor(12, 20, 30), RGBColor(10, 20, 30), True),
            ("color", RGBColor(13, 20, 30), RGBColor(10, 20, 30), False),
            ("color_temp", 3150, 3000, True),
            ("color_temp", 3151, 3000, False),
            ("power", True, True, True),
            ("power", False, True, False),
            ("color", None, None, True),
            ("color", None, RGBColor(1, 1, 1), False),
        ],
    )
    def test_tolerances(self, field, reported, wanted, expected):
        assert matches(field, reported, wanted) is expected


class TestEligibility:
    def test_never_resends_to_appliance(self):
        """Security review F-03: heaters, kettles, humidifiers, fans and groups are never confirmed."""
        for device in (
            _device(sku="H7130", device_type="devices.types.heater"),
            _device(sku="H7141", device_type="devices.types.humidifier"),
            _device(sku="H7102", device_type="devices.types.fan"),
            _device(sku="H7175", device_type="devices.types.kettle"),
            _device(device_id="11825917", group=True),
        ):
            assert eligible(device, PowerCommand(power_on=False)) is False
            assert eligible(device, BrightnessCommand(brightness=10)) is False
        assert eligible(None, BrightnessCommand(brightness=10)) is False

    def test_never_power_on(self):
        assert eligible(_device(), PowerCommand(power_on=True)) is False
        assert eligible(_device(), PowerCommand(power_on=False)) is True

    def test_unconfirmable_commands(self):
        assert eligible(_device(), SceneCommand(scene_id=1, scene_name="x")) is False

    @pytest.mark.asyncio
    async def test_appliance_command_starts_nothing(self):
        hooks = FakeHooks(_device(sku="H7130", device_type="devices.types.heater"))
        confirmer = _confirmer(hooks)
        assert confirmer.prepare(LIGHT, PowerCommand(power_on=False)) is None

    @pytest.mark.asyncio
    async def test_devices_not_on_aws_iot_are_left_alone(self):
        assert _confirmer(FakeHooks(_device(), heard=False)).prepare(LIGHT, BrightnessCommand(brightness=10)) is None

    @pytest.mark.asyncio
    async def test_a_no_op_command_is_not_confirmed(self):
        hooks = FakeHooks(_device())
        hooks.states[LIGHT].brightness = 40
        assert _confirmer(hooks).prepare(LIGHT, BrightnessCommand(brightness=41)) is None

    @pytest.mark.asyncio
    async def test_unknown_device_or_state(self):
        assert _confirmer(FakeHooks()).prepare(LIGHT, BrightnessCommand(brightness=10)) is None
        hooks = FakeHooks(_device())
        hooks.states.clear()
        assert _confirmer(hooks).prepare(LIGHT, BrightnessCommand(brightness=10)) is None


class TestConfirm:
    @pytest.mark.asyncio
    async def test_a_matching_report_confirms(self):
        hooks = FakeHooks(_device())
        confirmer = _confirmer(hooks)
        pending = await _send(confirmer, BrightnessCommand(brightness=10))
        confirmer.on_report(LIGHT, {"brightness": 11})
        await _settle(pending)
        assert _counts(confirmer) == {"confirmed": 1}
        assert hooks.queries == [] and hooks.resends == []

    @pytest.mark.asyncio
    async def test_reports_before_the_command_do_not_count(self):
        hooks = FakeHooks(_device())
        confirmer = _confirmer(hooks)
        pending = confirmer.prepare(LIGHT, BrightnessCommand(brightness=10))
        confirmer.on_report(LIGHT, {"brightness": 10})  # still in flight: not this command's answer
        confirmer.start(pending)
        await _settle(pending)
        assert hooks.queries == [LIGHT]
        assert _counts(confirmer) == {"unverified": 1}

    @pytest.mark.asyncio
    async def test_old_value_resends_once(self):
        hooks = FakeHooks(_device())
        hooks.states[LIGHT].brightness = 100
        confirmer = _confirmer(hooks)
        hooks.on_query = lambda: confirmer.on_report(LIGHT, {"brightness": 100})
        command = BrightnessCommand(brightness=10)
        pending = await _send(confirmer, command)
        await _settle(pending)
        assert hooks.queries == [LIGHT]
        assert hooks.resends == [(LIGHT, command)]
        assert _counts(confirmer) == {"resent": 1}

    @pytest.mark.asyncio
    async def test_single_resend_not_reconfirmed(self):
        hooks = FakeHooks(_device())
        confirmer = _confirmer(hooks)
        hooks.on_query = lambda: confirmer.on_report(LIGHT, {"colorTemInKelvin": 0, "color": {"r": 0, "g": 0, "b": 0}})
        hooks.states[LIGHT].color = RGBColor(0, 0, 0)
        pending = await _send(confirmer, ColorCommand(color=RGBColor(255, 0, 0)))
        await _settle(pending)
        assert len(hooks.resends) == 1
        # Nothing is pending after the resend, so later reports start nothing.
        assert confirmer._pending == {}
        confirmer.on_report(LIGHT, {"color": {"r": 0, "g": 0, "b": 0}})
        await asyncio.sleep(WINDOW * 2)
        assert len(hooks.resends) == 1
        assert len(hooks.queries) == 1

    @pytest.mark.asyncio
    async def test_a_failed_resend_is_counted(self):
        hooks = FakeHooks(_device())
        hooks.resend_result = False
        confirmer = _confirmer(hooks)
        hooks.on_query = lambda: confirmer.on_report(LIGHT, {"onOff": 1})
        hooks.states[LIGHT].power_state = True
        pending = await _send(confirmer, PowerCommand(power_on=False))
        await _settle(pending)
        assert _counts(confirmer) == {"unconfirmed": 1}

    @pytest.mark.asyncio
    async def test_budget_skips_the_resend(self):
        hooks = FakeHooks(_device(), budget=False)
        hooks.states[LIGHT].brightness = 100
        confirmer = _confirmer(hooks)
        hooks.on_query = lambda: confirmer.on_report(LIGHT, {"brightness": 100})
        pending = await _send(confirmer, BrightnessCommand(brightness=10))
        await _settle(pending)
        assert hooks.resends == []
        assert _counts(confirmer) == {"skipped_budget": 1}

    @pytest.mark.asyncio
    async def test_someone_elses_change_is_not_undone(self):
        hooks = FakeHooks(_device())
        hooks.states[LIGHT].brightness = 100
        confirmer = _confirmer(hooks)
        hooks.on_query = lambda: confirmer.on_report(LIGHT, {"brightness": 60})
        pending = await _send(confirmer, BrightnessCommand(brightness=10))
        await _settle(pending)
        assert hooks.resends == []
        assert _counts(confirmer) == {"unverified": 1}

    @pytest.mark.asyncio
    async def test_no_report_at_all_is_unverified_without_a_resend(self):
        hooks = FakeHooks(_device())
        confirmer = _confirmer(hooks)
        pending = await _send(confirmer, ColorTempCommand(kelvin=3000))
        await _settle(pending)
        assert hooks.queries == [LIGHT]  # exactly one status query
        assert hooks.resends == []
        assert _counts(confirmer) == {"unverified": 1}

    @pytest.mark.asyncio
    async def test_an_early_wrong_report_waits_for_a_later_one(self):
        hooks = FakeHooks(_device())
        hooks.states[LIGHT].brightness = 100
        confirmer = _confirmer(hooks)
        pending = await _send(confirmer, BrightnessCommand(brightness=10))
        confirmer.on_report(LIGHT, {"brightness": 100})  # the device had not applied it yet
        await asyncio.sleep(QUERY_WAIT * 2)
        confirmer.on_report(LIGHT, {"brightness": 10})
        await _settle(pending)
        assert hooks.queries == []  # a report came, so no query
        assert _counts(confirmer) == {"confirmed": 1}


class TestSupersession:
    @pytest.mark.asyncio
    async def test_power_supersedes_every_field(self):
        hooks = FakeHooks(_device())
        hooks.states[LIGHT].power_state = True
        confirmer = _confirmer(hooks)
        first = await _send(confirmer, BrightnessCommand(brightness=10))
        second = await _send(confirmer, PowerCommand(power_on=False))
        await asyncio.sleep(0)
        assert first.task.cancelled() or first.task.done()
        confirmer.on_report(LIGHT, {"onOff": 0})
        await _settle(second)
        assert _counts(confirmer) == {"superseded": 1, "confirmed": 1}
        assert hooks.resends == []

    @pytest.mark.asyncio
    async def test_a_power_on_still_supersedes(self):
        """Power-on is never confirmed, but it still makes a pending confirm stale."""
        hooks = FakeHooks(_device())
        confirmer = _confirmer(hooks)
        await _send(confirmer, BrightnessCommand(brightness=10))
        assert await _send(confirmer, PowerCommand(power_on=True)) is None
        assert confirmer._pending == {}
        assert _counts(confirmer) == {"superseded": 1}

    @pytest.mark.asyncio
    async def test_colour_and_colour_temperature_replace_each_other(self):
        hooks = FakeHooks(_device())
        confirmer = _confirmer(hooks)
        await _send(confirmer, ColorCommand(color=RGBColor(1, 2, 3)))
        kelvin = await _send(confirmer, ColorTempCommand(kelvin=3000))
        assert set(confirmer._pending) == {(LIGHT, "color_temp")}
        brightness = await _send(confirmer, BrightnessCommand(brightness=10))
        assert set(confirmer._pending) == {(LIGHT, "color_temp"), (LIGHT, "brightness")}
        confirmer.cancel_all()
        await asyncio.sleep(0)
        assert confirmer._pending == {}
        assert kelvin.task.cancelled() and brightness.task.cancelled()
        # Unload is not a supersession.
        assert _counts(confirmer) == {"superseded": 1}

    def test_start_without_a_pending_does_nothing(self):
        confirmer = _confirmer(FakeHooks(_device()))
        confirmer.start(None)
        assert confirmer._pending == {}

    @pytest.mark.asyncio
    async def test_restarting_the_same_pending_counts_once(self):
        hooks = FakeHooks(_device())
        confirmer = _confirmer(hooks)
        pending = confirmer.prepare(LIGHT, BrightnessCommand(brightness=10))
        confirmer.start(pending)
        first_task = pending.task
        confirmer.start(pending)
        await asyncio.sleep(0)
        assert first_task.cancelled()
        confirmer.cancel_all()
