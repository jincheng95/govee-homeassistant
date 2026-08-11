"""Tests for the cloud-push -> LAN read nudge (fork feature).

A cloud push is treated as a content-free "device X changed" signal; the LAN
``devStatus`` read it schedules is the source of truth. These tests drive a
real :class:`GoveeCoordinator` with a fake LAN client and a recording stand-in
for ``async_call_later``, so no timers and no sockets are involved:

1. Gates — disabled by option, device not on LAN, self-echo, cooldown.
2. Debounce — a burst of pushes for one device collapses into ONE read, and
   the coalescing is per device, not global.
3. Read cycle — the reply is overlaid onto state, and a miss never demotes the
   device out of the LAN map (demotion belongs to the interval poll).
4. Wiring — ``_on_mqtt_state_update`` triggers the hook and ``async_shutdown``
   cancels pending reads.
"""

from __future__ import annotations

import time
from datetime import timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest

from custom_components.govee.api import lan_nudge
from custom_components.govee.api.lan_client import LanDevStatus, LanDeviceInfo
from custom_components.govee.const import (
    CONF_ENABLE_LAN_NUDGE,
    LAN_READ_MISS_DEMOTE_THRESHOLD,
)
from custom_components.govee.models import GoveeDeviceState, RGBColor

DEVICE_ID = "AA:BB:CC:DD:EE:FF:00:11"
OTHER_ID = "BB:BB:BB:BB:BB:BB:BB:BB"
IP = "10.0.0.5"
OTHER_IP = "10.0.0.6"


class _FakeNudgeClient:
    """Minimal LAN client exposing only ``async_read_one``.

    ``replies`` maps a queried IP to the :class:`LanDevStatus` it answers with;
    IPs absent from it do not reply (read miss).
    """

    def __init__(self, replies: dict[str, LanDevStatus] | None = None) -> None:
        self.available = True
        self.replies = replies or {}
        self.read_calls: list[str] = []

    async def async_read_one(
        self, ip: str, timeout: float = 0.5
    ) -> LanDevStatus | None:
        self.read_calls.append(ip)
        return self.replies.get(ip)


class _Scheduler:
    """Records ``async_call_later`` calls instead of arming a real timer."""

    def __init__(self) -> None:
        self.calls: list[tuple[float, Any]] = []
        self.cancelled = 0

    def __call__(self, _hass: Any, delay: float, action: Any) -> Any:
        self.calls.append((delay, action))

        def _unsub() -> None:
            self.cancelled += 1

        return _unsub

    async def fire_all(self) -> None:
        """Run every recorded action, oldest first, then forget them."""
        pending, self.calls = self.calls, []
        for _delay, action in pending:
            await action(None)


def _status(**kw: Any) -> LanDevStatus:
    """Build a LAN devStatus reply, on/80%/red unless overridden."""
    defaults = dict(
        on=True,
        brightness_0_100=80,
        color=RGBColor(255, 0, 0),
        color_temp_kelvin=None,
    )
    defaults.update(kw)
    return LanDevStatus(**defaults)


def _info(device_id: str = DEVICE_ID, ip: str = IP) -> LanDeviceInfo:
    """Build a fresh LAN correlation for ``device_id`` at ``ip``."""
    return LanDeviceInfo(
        device_id=device_id,
        ip=ip,
        mac=device_id,
        sku="H6072",
        firmware="1.0.0",
        last_correlated_ts=time.monotonic(),
    )


def _coord(options: dict[str, Any] | None = None) -> Any:
    """Construct a real coordinator (no I/O) with one known device."""
    import custom_components.govee.coordinator as coord_mod

    hass = MagicMock()
    config_entry = MagicMock()
    config_entry.entry_id = "nudge_entry"
    config_entry.options = {} if options is None else options
    coord = coord_mod.GoveeCoordinator(
        hass=hass,
        config_entry=config_entry,
        api_client=MagicMock(),
        iot_credentials=None,
        poll_interval=60,
    )
    for dev_id in (DEVICE_ID, OTHER_ID):
        coord._devices[dev_id] = MagicMock(brightness_range=(0, 100))
        coord._states[dev_id] = GoveeDeviceState.create_empty(dev_id)
        coord._ensure_transport_health(dev_id)
    coord.async_set_updated_data = MagicMock()
    return coord


def _ready(
    monkeypatch: pytest.MonkeyPatch,
    *,
    options: dict[str, Any] | None = None,
    replies: dict[str, LanDevStatus] | None = None,
    correlate: bool = True,
) -> tuple[Any, _FakeNudgeClient, _Scheduler]:
    """Coordinator + fake LAN client + recording scheduler, wired together."""
    scheduler = _Scheduler()
    monkeypatch.setattr(lan_nudge, "async_call_later", scheduler)
    # One confirming read per cycle is real behaviour; keep its delay off the
    # test's wall clock.
    monkeypatch.setattr(lan_nudge, "NUDGE_CONFIRM_DELAY_SECONDS", 0.0)

    coord = _coord(options)
    client = _FakeNudgeClient(replies if replies is not None else {IP: _status()})
    coord._lan_client = client
    if correlate:
        coord._lan_devices[DEVICE_ID] = _info()
        coord._lan_devices[OTHER_ID] = _info(OTHER_ID, OTHER_IP)
    return coord, client, scheduler


# ==============================================================================
# 1. Gates
# ==============================================================================


class TestNudgeGates:
    """Every reason a push must NOT produce a LAN read."""

    def test_disabled_by_option_is_noop(self, monkeypatch):
        coord, _client, scheduler = _ready(
            monkeypatch, options={CONF_ENABLE_LAN_NUDGE: False}
        )

        lan_nudge.async_note_cloud_push(coord, DEVICE_ID)

        assert scheduler.calls == []

    def test_enabled_by_default(self, monkeypatch):
        coord, _client, scheduler = _ready(monkeypatch, options={})

        lan_nudge.async_note_cloud_push(coord, DEVICE_ID)

        assert len(scheduler.calls) == 1
        assert scheduler.calls[0][0] == lan_nudge.NUDGE_DELAY_SECONDS

    def test_device_not_on_lan_is_noop(self, monkeypatch):
        coord, _client, scheduler = _ready(monkeypatch, correlate=False)

        lan_nudge.async_note_cloud_push(coord, DEVICE_ID)

        assert scheduler.calls == []

    def test_correlation_without_ip_is_noop(self, monkeypatch):
        coord, _client, scheduler = _ready(monkeypatch)
        coord._lan_devices[DEVICE_ID] = _info(ip="")

        lan_nudge.async_note_cloud_push(coord, DEVICE_ID)

        assert scheduler.calls == []

    def test_self_echo_suppressed(self, monkeypatch):
        coord, _client, scheduler = _ready(monkeypatch)
        # Our own command to this device just went out — the push coming back
        # is its echo and the optimistic state is already correct.
        coord._record_transport_send(DEVICE_ID, "mqtt")

        lan_nudge.async_note_cloud_push(coord, DEVICE_ID)

        assert scheduler.calls == []

    def test_self_echo_only_covers_the_commanded_device(self, monkeypatch):
        coord, _client, scheduler = _ready(monkeypatch)
        coord._record_transport_send(OTHER_ID, "mqtt")

        lan_nudge.async_note_cloud_push(coord, DEVICE_ID)

        assert len(scheduler.calls) == 1

    def test_old_local_write_does_not_suppress(self, monkeypatch):
        coord, _client, scheduler = _ready(monkeypatch)
        coord._record_transport_send(DEVICE_ID, "mqtt")
        health = coord.get_transport_health(DEVICE_ID, "mqtt")
        health.last_send_ts -= timedelta(
            seconds=lan_nudge.NUDGE_ECHO_SUPPRESS_SECONDS + 1
        )

        lan_nudge.async_note_cloud_push(coord, DEVICE_ID)

        assert len(scheduler.calls) == 1

    @pytest.mark.asyncio
    async def test_cooldown_drops_later_push(self, monkeypatch):
        coord, client, scheduler = _ready(monkeypatch)

        lan_nudge.async_note_cloud_push(coord, DEVICE_ID)
        await scheduler.fire_all()  # read runs, cooldown starts
        assert client.read_calls == [IP]

        # A push after the debounce window but inside the cooldown is dropped.
        manager = getattr(coord, lan_nudge._MANAGER_ATTR)
        manager._debounce_until[DEVICE_ID] = 0.0
        lan_nudge.async_note_cloud_push(coord, DEVICE_ID)

        assert scheduler.calls == []

    @pytest.mark.asyncio
    async def test_push_after_cooldown_expiry_reads_again(self, monkeypatch):
        coord, client, scheduler = _ready(monkeypatch)

        lan_nudge.async_note_cloud_push(coord, DEVICE_ID)
        await scheduler.fire_all()

        manager = getattr(coord, lan_nudge._MANAGER_ATTR)
        manager._debounce_until[DEVICE_ID] = 0.0
        manager._cooldown_until[DEVICE_ID] = 0.0
        lan_nudge.async_note_cloud_push(coord, DEVICE_ID)
        await scheduler.fire_all()

        assert client.read_calls == [IP, IP]


# ==============================================================================
# 2. Debounce / coalescing
# ==============================================================================


class TestNudgeDebounce:
    """A burst of pushes for one device becomes exactly one LAN read."""

    @pytest.mark.asyncio
    async def test_burst_collapses_into_one_read(self, monkeypatch):
        coord, client, scheduler = _ready(monkeypatch)

        for _ in range(5):
            lan_nudge.async_note_cloud_push(coord, DEVICE_ID)

        assert len(scheduler.calls) == 1
        await scheduler.fire_all()
        assert client.read_calls == [IP]

    @pytest.mark.asyncio
    async def test_debounce_is_per_device_not_global(self, monkeypatch):
        coord, client, scheduler = _ready(
            monkeypatch, replies={IP: _status(), OTHER_IP: _status()}
        )

        lan_nudge.async_note_cloud_push(coord, DEVICE_ID)
        lan_nudge.async_note_cloud_push(coord, OTHER_ID)

        assert len(scheduler.calls) == 2
        await scheduler.fire_all()
        assert sorted(client.read_calls) == sorted([IP, OTHER_IP])

    @pytest.mark.asyncio
    async def test_push_while_read_pending_does_not_double_schedule(self, monkeypatch):
        coord, client, scheduler = _ready(monkeypatch)

        lan_nudge.async_note_cloud_push(coord, DEVICE_ID)
        # Pending, but the debounce window has already lapsed: the pending
        # read itself must still absorb the push.
        manager = getattr(coord, lan_nudge._MANAGER_ATTR)
        manager._debounce_until[DEVICE_ID] = 0.0
        lan_nudge.async_note_cloud_push(coord, DEVICE_ID)

        assert len(scheduler.calls) == 1
        await scheduler.fire_all()
        assert client.read_calls == [IP]


# ==============================================================================
# 3. The read cycle itself
# ==============================================================================


class TestNudgeReadCycle:
    """What the scheduled read does with (and without) a reply."""

    @pytest.mark.asyncio
    async def test_reply_is_overlaid_onto_state(self, monkeypatch):
        coord, _client, scheduler = _ready(
            monkeypatch, replies={IP: _status(on=True, brightness_0_100=40)}
        )
        state = coord._states[DEVICE_ID]
        state.power_state = False

        lan_nudge.async_note_cloud_push(coord, DEVICE_ID)
        await scheduler.fire_all()

        assert state.power_state is True
        assert state.brightness == 40
        coord.async_set_updated_data.assert_called_once()

    @pytest.mark.asyncio
    async def test_confirming_read_runs_after_a_reply(self, monkeypatch):
        coord, client, scheduler = _ready(monkeypatch)
        monkeypatch.setattr(lan_nudge, "NUDGE_CONFIRM_DELAY_SECONDS", 0.01)

        lan_nudge.async_note_cloud_push(coord, DEVICE_ID)
        await scheduler.fire_all()

        assert client.read_calls == [IP, IP]

    @pytest.mark.asyncio
    async def test_confirming_read_skipped_when_we_command_meanwhile(self, monkeypatch):
        coord, client, scheduler = _ready(monkeypatch)
        monkeypatch.setattr(lan_nudge, "NUDGE_CONFIRM_DELAY_SECONDS", 0.01)

        lan_nudge.async_note_cloud_push(coord, DEVICE_ID)
        # A local command lands between the first read and the confirming one:
        # the device is now mid-write, so re-reading would fight the optimistic
        # state the write tier already owns.
        coord._record_transport_send(DEVICE_ID, "mqtt")
        await scheduler.fire_all()

        assert client.read_calls == [IP]

    @pytest.mark.asyncio
    async def test_miss_never_demotes_the_device(self, monkeypatch):
        coord, client, scheduler = _ready(monkeypatch, replies={})  # silent device
        manager_attr = lan_nudge._MANAGER_ATTR

        for _ in range(LAN_READ_MISS_DEMOTE_THRESHOLD + 2):
            lan_nudge.async_note_cloud_push(coord, DEVICE_ID)
            await scheduler.fire_all()
            manager = getattr(coord, manager_attr)
            manager._debounce_until[DEVICE_ID] = 0.0
            manager._cooldown_until[DEVICE_ID] = 0.0

        # Demotion is owned by the interval poll, whose cadence the threshold
        # is calibrated for — a nudge miss must not evict the device.
        assert DEVICE_ID in coord._lan_devices
        assert coord._lan_read_misses.get(DEVICE_ID, 0) == 0
        coord.async_set_updated_data.assert_not_called()

    @pytest.mark.asyncio
    async def test_read_without_client_is_safe(self, monkeypatch):
        coord, _client, scheduler = _ready(monkeypatch)

        lan_nudge.async_note_cloud_push(coord, DEVICE_ID)
        coord._lan_client = None  # LAN torn down between push and read
        await scheduler.fire_all()

        coord.async_set_updated_data.assert_not_called()


# ==============================================================================
# 4. Coordinator wiring
# ==============================================================================


class TestNudgeWiring:
    """The two lines this feature adds to the coordinator."""

    @pytest.mark.asyncio
    async def test_mqtt_push_schedules_a_read(self, monkeypatch):
        coord, client, scheduler = _ready(monkeypatch)

        coord._on_mqtt_state_update(DEVICE_ID, {"onOff": 1})

        assert len(scheduler.calls) == 1
        await scheduler.fire_all()
        assert client.read_calls == [IP]

    def test_mqtt_push_for_unknown_device_is_noop(self, monkeypatch):
        coord, _client, scheduler = _ready(monkeypatch)

        coord._on_mqtt_state_update("NOT:A:DEVICE", {"onOff": 1})

        assert scheduler.calls == []

    def test_cancel_releases_pending_reads(self, monkeypatch):
        coord, _client, scheduler = _ready(monkeypatch)

        lan_nudge.async_note_cloud_push(coord, DEVICE_ID)
        lan_nudge.async_cancel_nudges(coord)

        assert scheduler.cancelled == 1
        manager = getattr(coord, lan_nudge._MANAGER_ATTR)
        assert manager._pending == {}

    def test_cancel_without_any_push_is_safe(self, monkeypatch):
        coord, _client, _scheduler = _ready(monkeypatch)

        lan_nudge.async_cancel_nudges(coord)  # manager never built
