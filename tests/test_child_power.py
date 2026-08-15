"""Child-to-master power sync and its duplicate-send latch (fork feature).

The lamp's own state snapshot is subject to the same echo lag the LAN confirm
read is (protocol reference §2.1): the H60B0 keeps reporting ``onOff: 0`` for
~1.5 s after being told to switch on. Two zones lit back to back therefore both
read "off" and, without a latch, both send a whole-device power-on.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.govee import child_power
from custom_components.govee.models import PowerCommand

DEVICE_ID = "AA:BB:CC:DD:EE:FF:00:11"


def _fake_monotonic(monkeypatch, start: float = 1000.0) -> dict[str, float]:
    """Drive the clock the latch actually reads, returning a mutable ``{"now"}``."""
    clock = {"now": start}
    monkeypatch.setattr(child_power.time, "monotonic", lambda: clock["now"])
    return clock


class _Coordinator:
    """A coordinator stub whose state snapshot lags, exactly like the lamp's.

    ``power_state`` stays False until ``settle()`` is called — the window in
    which a second child turn_on would see stale "off" and duplicate the write.
    """

    def __init__(self) -> None:
        self.reported_on = False
        self.async_control_device = AsyncMock(return_value=True)

    def settle(self) -> None:
        self.reported_on = True

    def get_state(self, device_id: str):
        state = MagicMock()
        state.power_state = self.reported_on
        return state

    @property
    def commands(self) -> list:
        return [call.args[1] for call in self.async_control_device.await_args_list]


class TestEnsureDevicePowered:
    """The one rule: a child turn_on powers the lamp, once."""

    @pytest.mark.asyncio
    async def test_powers_an_off_lamp_on(self):
        coord = _Coordinator()

        await child_power.async_ensure_device_powered(coord, DEVICE_ID)

        assert coord.commands == [PowerCommand(power_on=True)]

    @pytest.mark.asyncio
    async def test_no_command_for_a_lamp_already_on(self):
        coord = _Coordinator()
        coord.settle()

        await child_power.async_ensure_device_powered(coord, DEVICE_ID)

        coord.async_control_device.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_command_when_nothing_is_known(self):
        coord = _Coordinator()
        coord.get_state = MagicMock(return_value=None)  # type: ignore[method-assign]

        await child_power.async_ensure_device_powered(coord, DEVICE_ID)

        coord.async_control_device.assert_not_awaited()


class TestPowerLatch:
    """The latch that keeps a stale snapshot from duplicating the power-on."""

    @pytest.mark.asyncio
    async def test_two_zone_turn_ons_100ms_apart_send_one_power_command(self, monkeypatch):
        coord = _Coordinator()
        clock = _fake_monotonic(monkeypatch)

        # Zone 1 lights the lamp; the lamp is still echoing "off" 100 ms later
        # when zone 2 asks the same question.
        await child_power.async_ensure_device_powered(coord, DEVICE_ID)
        clock["now"] += 0.1
        await child_power.async_ensure_device_powered(coord, DEVICE_ID)

        assert coord.commands == [PowerCommand(power_on=True)]

    @pytest.mark.asyncio
    async def test_concurrent_zone_turn_ons_send_one_power_command(self):
        # Same burst with no ordering at all: the latch is set before the await,
        # so the second coroutine cannot slip through while the first suspends.
        coord = _Coordinator()

        await asyncio.gather(
            child_power.async_ensure_device_powered(coord, DEVICE_ID),
            child_power.async_ensure_device_powered(coord, DEVICE_ID),
            child_power.async_ensure_device_powered(coord, DEVICE_ID),
        )

        assert coord.commands == [PowerCommand(power_on=True)]

    @pytest.mark.asyncio
    async def test_latch_expires_so_a_failed_power_on_is_retried(self, monkeypatch):
        coord = _Coordinator()
        clock = _fake_monotonic(monkeypatch)

        await child_power.async_ensure_device_powered(coord, DEVICE_ID)
        clock["now"] += child_power.POWER_LATCH_SECONDS + 0.01
        # The lamp still reports "off" — this time that is real, not an echo.
        await child_power.async_ensure_device_powered(coord, DEVICE_ID)

        assert coord.commands == [PowerCommand(power_on=True), PowerCommand(power_on=True)]

    @pytest.mark.asyncio
    async def test_latch_is_per_device(self):
        coord = _Coordinator()
        other = "11:22:33:44:55:66:77:88"

        await child_power.async_ensure_device_powered(coord, DEVICE_ID)
        await child_power.async_ensure_device_powered(coord, other)

        assert [call.args[0] for call in coord.async_control_device.await_args_list] == [DEVICE_ID, other]

    @pytest.mark.asyncio
    async def test_latch_is_per_coordinator(self):
        first, second = _Coordinator(), _Coordinator()

        await child_power.async_ensure_device_powered(first, DEVICE_ID)
        await child_power.async_ensure_device_powered(second, DEVICE_ID)

        assert first.commands == [PowerCommand(power_on=True)]
        assert second.commands == [PowerCommand(power_on=True)]

    @pytest.mark.asyncio
    async def test_a_wall_clock_jump_forward_does_not_release_the_latch(self, monkeypatch):
        """An NTP/DST step of the wall clock leaves the latch window intact."""
        coord = _Coordinator()
        clock = _fake_monotonic(monkeypatch)
        wall = {"now": 1_700_000_000.0}
        monkeypatch.setattr(time, "time", lambda: wall["now"])

        await child_power.async_ensure_device_powered(coord, DEVICE_ID)
        # An hour of wall clock, and not one millisecond of monotonic time.
        wall["now"] += 3600.0
        await child_power.async_ensure_device_powered(coord, DEVICE_ID)

        assert coord.commands == [PowerCommand(power_on=True)]
        assert clock["now"] == 1000.0

    @pytest.mark.asyncio
    async def test_a_wall_clock_jump_backward_does_not_extend_the_latch(self, monkeypatch):
        """A backward wall-clock step does not hold the latch past its window."""
        coord = _Coordinator()
        clock = _fake_monotonic(monkeypatch)
        wall = {"now": 1_700_000_000.0}
        monkeypatch.setattr(time, "time", lambda: wall["now"])

        await child_power.async_ensure_device_powered(coord, DEVICE_ID)
        wall["now"] -= 3600.0
        clock["now"] += child_power.POWER_LATCH_SECONDS + 0.01
        await child_power.async_ensure_device_powered(coord, DEVICE_ID)

        assert coord.commands == [PowerCommand(power_on=True), PowerCommand(power_on=True)]
