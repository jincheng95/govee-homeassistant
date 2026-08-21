"""Echo-lag-aware LAN write confirmation (fork feature).

Covers :mod:`custom_components.govee.lan_confirm` and the two hooks it has in
the coordinator's LAN control tier. The headline test is
``test_two_scene_applies_arm_no_cooldown``: on the H60B0, whose firmware echoes
its pre-command state for ~1.5 s (protocol reference §2.1), every master
power/brightness write used to be a guaranteed confirm miss, and two scene
applies were enough to arm issue #57's 300 s LAN write cooldown.
"""

from __future__ import annotations

import dataclasses
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.govee.api.lan_client import LanDeviceInfo, LanDevStatus
from custom_components.govee.api.protocol import profiles as profiles_mod
from custom_components.govee.const import LAN_WRITE_SUPPRESS_THRESHOLD
from custom_components.govee.models import BrightnessCommand, ColorCommand, PowerCommand, RGBColor
from custom_components.govee.models.device import (
    CAPABILITY_ON_OFF,
    CAPABILITY_RANGE,
    INSTANCE_BRIGHTNESS,
    INSTANCE_POWER,
    GoveeCapability,
    GoveeDevice,
)
from custom_components.govee.models.state import GoveeDeviceState
from custom_components.govee import lan_confirm

DEVICE_ID = "AA:BB:CC:DD:EE:FF:00:11"
IP = "10.0.0.205"

# A shrunk stand-in for the H60B0's real 1.5 s echo lag, so the simulation runs
# in milliseconds. The real table value is asserted separately below.
TEST_ECHO_LAG = 0.05
# The lamp flips its reported state slightly *before* the declared lag expires,
# which is what "the table carries margin" means in practice.
TEST_ECHO_REAL = 0.04


class TestProfileEchoLag:
    """Where the echo-lag knowledge lives: the per-SKU profile table."""

    def test_h60b0_declares_the_measured_lag(self):
        # Reference §2.1: "do not read back sooner than ~1.5 s after a write".
        assert profiles_mod.H60B0.echo_lag_seconds == 1.5
        assert lan_confirm.echo_lag_seconds("H60B0") == 1.5
        assert lan_confirm.echo_lag_seconds("h60b0") == 1.5

    def test_unmeasured_skus_default_to_zero(self):
        # Not "answers instantly" — "nobody has timed it", so upstream's
        # behaviour must be left exactly as it was.
        assert profiles_mod.H6046.echo_lag_seconds == 0.0
        assert profiles_mod.H6076.echo_lag_seconds == 0.0
        assert lan_confirm.echo_lag_seconds("H6046") == 0.0

    def test_sku_without_a_profile_is_zero(self):
        assert lan_confirm.echo_lag_seconds("H6072") == 0.0
        assert lan_confirm.confirm_read_delay("H6072") == 0.0


class TestCountsAsMiss:
    """Which unconfirmed readbacks may arm the issue #57 suppression streak."""

    def test_unprofiled_sku_always_counts(self):
        # Upstream semantics, untouched.
        assert lan_confirm.counts_as_miss("H6072", 0.0) is True
        assert lan_confirm.counts_as_miss("H6072", 99.0) is True

    def test_read_after_the_echo_window_counts(self):
        assert lan_confirm.counts_as_miss("H60B0", 1.5) is True
        assert lan_confirm.counts_as_miss("H60B0", 2.0) is True


class TestAsyncSettle:
    """The settle wait that makes the confirm read mean something."""

    @pytest.mark.asyncio
    async def test_no_wait_for_an_unprofiled_sku(self):
        started = time.monotonic()
        assert await lan_confirm.async_settle("H6072") == 0.0
        assert time.monotonic() - started < 0.2

    @pytest.mark.asyncio
    async def test_waits_the_declared_lag(self, monkeypatch):
        monkeypatch.setitem(
            profiles_mod.PROFILES,
            "H60B0",
            dataclasses.replace(profiles_mod.H60B0, echo_lag_seconds=TEST_ECHO_LAG),
        )
        started = time.monotonic()
        assert await lan_confirm.async_settle("H60B0") == TEST_ECHO_LAG
        assert time.monotonic() - started >= TEST_ECHO_LAG


class TestTheWindowIsMeasuredFromTheSend:
    """``since=`` is what makes the echo window a real interval.

    Timed from the start of the confirm, "elapsed" always contained the settle
    wait, so :func:`counts_as_miss` could only ever answer True and the settle
    was served again in full however long the write had already been out.
    """

    @pytest.fixture(autouse=True)
    def _shrink_the_echo_lag(self, monkeypatch):
        monkeypatch.setitem(
            profiles_mod.PROFILES,
            "H60B0",
            dataclasses.replace(profiles_mod.H60B0, echo_lag_seconds=TEST_ECHO_LAG),
        )

    @pytest.mark.asyncio
    async def test_a_window_already_passed_is_not_waited_again(self):
        started = time.monotonic()

        waited = await lan_confirm.async_settle("H60B0", since=started - TEST_ECHO_LAG * 10)

        assert waited == 0.0
        assert time.monotonic() - started < TEST_ECHO_LAG

    @pytest.mark.asyncio
    async def test_only_the_remainder_of_the_window_is_waited(self):
        waited = await lan_confirm.async_settle("H60B0", since=time.monotonic() - TEST_ECHO_LAG / 2)

        assert 0 < waited <= TEST_ECHO_LAG / 2

    @pytest.mark.asyncio
    async def test_the_confirm_dates_its_window_from_the_send(self):
        # A deferred confirm runs behind its own datagram. Handed the real send
        # time, it reads immediately instead of settling a second time.
        coord = _h60b0_coord()
        lamp = _EchoingLamp()
        # The write landed while the task was still queued, so the lamp already
        # reports post-command state and its echo window is long gone.
        lamp.on = True
        coord._lan_client = lamp
        sent_at = time.monotonic() - TEST_ECHO_LAG * 10

        started = time.monotonic()
        confirmed = await coord._async_confirm_lan_write(
            DEVICE_ID, coord._devices[DEVICE_ID], PowerCommand(power_on=True), IP, sent_at
        )

        assert confirmed is True
        assert time.monotonic() - started < TEST_ECHO_LAG

    @pytest.mark.asyncio
    async def test_a_real_miss_is_still_counted(self):
        # The window excuses a read taken too early, never a write that did not
        # land: the integrated flow's elapsed is past the lag, so it counts.
        coord = _h60b0_coord()
        lamp = _EchoingLamp()
        lamp.async_read_one = _DeafRead(lamp)  # type: ignore[method-assign]
        coord._lan_client = lamp

        await coord._try_lan_command(DEVICE_ID, coord._devices[DEVICE_ID], PowerCommand(power_on=True))

        assert coord._lan_write_misses[DEVICE_ID] == 1


class _EchoingLamp:
    """An H60B0 stand-in: ``devStatus`` repeats the PRE-command state.

    Answers a read promptly (the real lamp replies in ~28 ms) and answers it
    *wrong* until ``TEST_ECHO_REAL`` has elapsed since the write — which is the
    whole reason an immediate confirm read could only ever fail.
    """

    def __init__(self) -> None:
        self.on = False
        self.brightness_0_100 = 1
        self.color = RGBColor(255, 0, 0)
        self.available = True
        self.send_calls: list[tuple[str, str, dict[str, Any]]] = []
        self.read_calls: list[tuple[str, float]] = []
        self._pending: list[tuple[str, dict[str, Any]]] = []
        self._written_at = 0.0

    async def async_send_command(self, ip: str, cmd: str, data: dict[str, Any]) -> bool:
        self.send_calls.append((ip, cmd, data))
        self._pending.append((cmd, data))
        self._written_at = time.monotonic()
        return True

    async def async_read_one(self, ip: str, timeout: float = 0.5) -> LanDevStatus:
        self.read_calls.append((ip, timeout))
        if self._pending and time.monotonic() - self._written_at >= TEST_ECHO_REAL:
            for cmd, data in self._pending:
                if cmd == "turn":
                    self.on = bool(data["value"])
                elif cmd == "brightness":
                    self.brightness_0_100 = int(data["value"])
                elif cmd == "colorwc":
                    rgb = data["color"]
                    self.color = RGBColor(rgb["r"], rgb["g"], rgb["b"])
            self._pending.clear()
        return LanDevStatus(
            on=self.on,
            brightness_0_100=self.brightness_0_100,
            color=self.color,
            color_temp_kelvin=None,
        )


def _h60b0_coord():
    """A coordinator wired for a LAN-correlated, LAN-available H60B0."""
    import custom_components.govee.coordinator as coord_mod

    hass = MagicMock()
    config_entry = MagicMock()
    config_entry.entry_id = "test_entry"
    config_entry.options = {}
    coord = coord_mod.GoveeCoordinator(
        hass=hass,
        config_entry=config_entry,
        api_client=MagicMock(),
        iot_credentials=None,
        poll_interval=60,
    )
    coord._devices[DEVICE_ID] = GoveeDevice(
        device_id=DEVICE_ID,
        sku="H60B0",
        name="Uplighter",
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
    coord._lan_devices[DEVICE_ID] = LanDeviceInfo(
        device_id=DEVICE_ID,
        ip=IP,
        mac=DEVICE_ID,
        sku="H60B0",
        firmware="1.0.0",
        last_correlated_ts=time.monotonic(),
    )
    # A prior successful LAN read, so the write-health gate passes.
    coord._record_transport_success(DEVICE_ID, "lan")
    return coord


async def _scene_apply(coord, *, brightness: int) -> None:
    """One scene apply's worth of writes on the verified LAN tier.

    A real H60B0 scene apply is five writes: whole-device power, whole-device
    brightness, and three colour paints. ``command_to_lan`` now maps power,
    brightness, colour and colour-temp — the four ``devStatus``-readable
    fields — so all five of these map to LAN.
    """
    device = coord._devices[DEVICE_ID]
    for command in (
        PowerCommand(power_on=True),
        BrightnessCommand(brightness=brightness),
        ColorCommand(color=RGBColor(255, 120, 0)),
        ColorCommand(color=RGBColor(0, 120, 255)),
        ColorCommand(color=RGBColor(120, 255, 0)),
    ):
        await coord._try_lan_command(DEVICE_ID, device, command)


class TestScenePipelineDoesNotArmTheCooldown:
    """The regression this whole change exists for."""

    @pytest.fixture(autouse=True)
    def _shrink_the_echo_lag(self, monkeypatch):
        # Same code path, milliseconds instead of seconds.
        monkeypatch.setitem(
            profiles_mod.PROFILES,
            "H60B0",
            dataclasses.replace(profiles_mod.H60B0, echo_lag_seconds=TEST_ECHO_LAG),
        )

    @pytest.mark.asyncio
    async def test_two_scene_applies_arm_no_cooldown(self):
        coord = _h60b0_coord()
        lamp = _EchoingLamp()
        coord._lan_client = lamp

        await _scene_apply(coord, brightness=60)
        await _scene_apply(coord, brightness=35)

        # Ten LAN-mapped writes went out (power + brightness + 3 colour, twice)...
        assert [cmd for _, cmd, _ in lamp.send_calls] == [
            "turn",
            "brightness",
            "colorwc",
            "colorwc",
            "colorwc",
            "turn",
            "brightness",
            "colorwc",
            "colorwc",
            "colorwc",
        ]
        # ...every one of them confirmed after the echo settled...
        assert len(lamp.read_calls) == 10
        # ...so nothing was counted toward issue #57 and no cooldown is armed.
        assert coord._lan_write_misses == {}
        assert coord.lan_write_suppressed(DEVICE_ID) is False
        assert coord._lan_writes_suppressed(DEVICE_ID) is False
        # And the lamp really did end up where the last write put it, i.e. the
        # confirms are real confirmations, not a disabled check.
        assert lamp.on is True
        assert lamp.brightness_0_100 == 35

    @pytest.mark.asyncio
    async def test_confirmed_write_needs_no_cloud_resend(self):
        # The flicker: an unconfirmed write is re-sent over MQTT/REST ~1.9s
        # later as a second, visible brightness step. A confirmed one is not.
        coord = _h60b0_coord()
        coord._lan_client = _EchoingLamp()
        coord._api_client.control_device = AsyncMock(return_value=True)

        # The whole dispatch, not just the LAN tier: only async_control_device
        # can fall through to MQTT/REST, so only it can be shown not to.
        confirmed = await coord.async_control_device(DEVICE_ID, PowerCommand(power_on=True))

        assert confirmed is True
        coord._api_client.control_device.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_lamp_that_never_confirms_still_arms_the_cooldown(self):
        # The safety valve: the echo window excuses reads taken too early, not
        # a device whose writes genuinely do not land.
        coord = _h60b0_coord()
        lamp = _EchoingLamp()
        # Deaf: the pending write is never applied, so every read mismatches
        # *after* the echo window, which is real evidence.
        lamp.async_read_one = _DeafRead(lamp)  # type: ignore[method-assign]
        coord._lan_client = lamp

        for _ in range(LAN_WRITE_SUPPRESS_THRESHOLD):
            await coord._try_lan_command(DEVICE_ID, coord._devices[DEVICE_ID], PowerCommand(power_on=True))

        assert coord._lan_write_misses[DEVICE_ID] == LAN_WRITE_SUPPRESS_THRESHOLD
        assert coord.lan_write_suppressed(DEVICE_ID) is True


class _DeafRead:
    """A read that always reports the lamp's stale, unchanged state."""

    def __init__(self, lamp: _EchoingLamp) -> None:
        self._lamp = lamp

    async def __call__(self, ip: str, timeout: float = 0.5) -> LanDevStatus:
        self._lamp.read_calls.append((ip, timeout))
        return LanDevStatus(on=False, brightness_0_100=1, color=None, color_temp_kelvin=None)
