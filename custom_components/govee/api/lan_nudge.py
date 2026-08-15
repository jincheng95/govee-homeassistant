"""Cloud push as a nudge for a LAN read (fork feature).

Treats the MQTT/IoT push as a content-free "device X changed, go look" signal and
answers it with a LAN ``devStatus`` read, so a LAN-reachable device does not
depend on the cloud payload schema. Degrades to interval polling when the cloud
is unavailable, and to cloud-only when the device is not on the LAN. Four gates
keep it from amplifying a chatty broker: LAN-capable devices only, per-device
coalescing, self-echo suppression after a local write, and a per-device cooldown.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import time
from typing import TYPE_CHECKING, Any, Final

from homeassistant.core import CALLBACK_TYPE, callback
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from ..const import CONF_ENABLE_LAN_NUDGE, DEFAULT_ENABLE_LAN_NUDGE

if TYPE_CHECKING:
    from ..coordinator import GoveeCoordinator
    from .lan_client import GoveeLanClient, LanDevStatus

_LOGGER = logging.getLogger(__name__)

# Delay (seconds) between the push landing and the LAN read going out. A push
# can beat the device's own settling — reading instantly tends to return the
# pre-change values, which the overlay would then apply as truth.
NUDGE_DELAY_SECONDS: Final = 0.25
# Window (seconds) during which further pushes for the same device collapse into
# the already-accepted nudge. Covers the scheduling delay above with a margin,
# so a burst of pushes for one device produces exactly one read.
NUDGE_DEBOUNCE_SECONDS: Final = 0.3
# How long (seconds) after a locally-issued command a push for that device is
# treated as our own echo and ignored.
NUDGE_ECHO_SUPPRESS_SECONDS: Final = 1.0
# Hard per-device floor (seconds) between nudge-driven reads, so a broker
# storm cannot flood the LAN with UDP queries.
NUDGE_COOLDOWN_SECONDS: Final = 2.0
# Delay (seconds) before the optional second read of a nudge cycle. A device
# mid-transition (fade, scene ramp) can answer the first read with an
# intermediate value; one confirming read settles it. Set to 0 to disable.
NUDGE_CONFIRM_DELAY_SECONDS: Final = 1.0

# Attribute the lazily-built manager is cached under on the coordinator. Kept
# out of the coordinator's __init__ on purpose: no upstream line to conflict.
_MANAGER_ATTR: Final = "_lan_nudge_manager"


class LanNudgeManager:
    """Per-coordinator scheduler turning cloud pushes into LAN reads.

    One instance per coordinator, holding all of the feature's state so nothing
    is added to the coordinator's own ``__init__``. All public entry points are
    event-loop-only, matching the coordinator's MQTT callback contract.
    """

    def __init__(self, coordinator: GoveeCoordinator) -> None:
        """Initialize the manager for one coordinator.

        Args:
            coordinator: The owning :class:`GoveeCoordinator`.
        """
        self._coordinator = coordinator
        self._enabled: bool = coordinator.config_entry.options.get(CONF_ENABLE_LAN_NUDGE, DEFAULT_ENABLE_LAN_NUDGE)
        # Scheduled-but-not-yet-fired reads, keyed by device_id.
        self._pending: dict[str, CALLBACK_TYPE] = {}
        # Monotonic deadline until which further pushes coalesce into the nudge
        # already accepted for this device.
        self._debounce_until: dict[str, float] = {}
        # Monotonic deadline until which no further nudge-driven read may start.
        self._cooldown_until: dict[str, float] = {}

    @callback
    def async_note_cloud_push(self, device_id: str) -> None:
        """Schedule a LAN read for ``device_id`` after a cloud push.

        The push payload is deliberately not passed in — this is a change
        *signal*, and the LAN read is the source of truth. Every rejection path
        is a silent no-op (debug-logged), so a nudge can never fail a push.

        Args:
            device_id: The device the cloud pushed state for.
        """
        if not self._enabled:
            return
        if not self._is_lan_reachable(device_id):
            return
        if self._is_self_echo(device_id):
            _LOGGER.debug(
                "Govee LAN nudge: ignoring push for %s — own command within %.1fs",
                device_id,
                NUDGE_ECHO_SUPPRESS_SECONDS,
            )
            return

        now = time.monotonic()
        if device_id in self._pending or now < self._debounce_until.get(device_id, 0.0):
            _LOGGER.debug(
                "Govee LAN nudge: coalescing push for %s into pending read",
                device_id,
            )
            return
        if now < self._cooldown_until.get(device_id, 0.0):
            _LOGGER.debug(
                "Govee LAN nudge: push for %s dropped — cooldown (%.1fs)",
                device_id,
                NUDGE_COOLDOWN_SECONDS,
            )
            return

        self._debounce_until[device_id] = now + NUDGE_DEBOUNCE_SECONDS
        self._pending[device_id] = async_call_later(
            self._coordinator.hass,
            NUDGE_DELAY_SECONDS,
            functools.partial(self._async_fire, device_id),
        )
        _LOGGER.debug(
            "Govee LAN nudge: scheduled read for %s in %.2fs",
            device_id,
            NUDGE_DELAY_SECONDS,
        )

    @callback
    def async_cancel(self) -> None:
        """Cancel every scheduled read (reload / shutdown)."""
        for unsub in self._pending.values():
            unsub()
        self._pending.clear()
        self._debounce_until.clear()
        self._cooldown_until.clear()

    async def _async_fire(self, device_id: str, _now: Any = None) -> None:
        """``async_call_later`` target — a partial binds ``device_id``.

        HA unwraps ``functools.partial`` when classifying the job, so binding a
        coroutine method this way keeps it a coroutine job (a plain object with
        an ``async __call__`` would be misclassified as an executor job).
        """
        await self.async_run_nudge(device_id)

    async def async_run_nudge(self, device_id: str) -> None:
        """Run one nudge cycle: a LAN read, plus an optional confirming read.

        A miss is intentionally NOT counted against
        ``LAN_READ_MISS_DEMOTE_THRESHOLD``: demotion is owned by the interval
        poll, whose cadence that threshold is calibrated for. A nudge read that
        happens to arrive while the device is busy must not evict it from the
        LAN map.

        Args:
            device_id: The device to read.
        """
        self._pending.pop(device_id, None)
        self._cooldown_until[device_id] = time.monotonic() + NUDGE_COOLDOWN_SECONDS

        if not await self._async_read_once(device_id):
            return
        if NUDGE_CONFIRM_DELAY_SECONDS <= 0:
            return
        # Second look: a device caught mid-fade answers the first read with an
        # intermediate value. Bounded to exactly one extra read per cycle, so
        # it cannot chain.
        await asyncio.sleep(NUDGE_CONFIRM_DELAY_SECONDS)
        if self._is_self_echo(device_id):
            return
        await self._async_read_once(device_id)

    async def _async_read_once(self, device_id: str) -> bool:
        """Solicit one ``devStatus`` and overlay it. Returns True on a reply."""
        client = self._lan_client()
        ip = self._lan_ip(device_id)
        if client is None or ip is None:
            return False

        status = await client.async_read_one(ip)
        if status is None:
            _LOGGER.debug("Govee LAN nudge: no devStatus reply from %s (%s)", device_id, ip)
            return False

        self._apply_lan_read(device_id, status)
        _LOGGER.debug("Govee LAN nudge: applied read for %s (%s)", device_id, ip)
        return True

    def _is_self_echo(self, device_id: str) -> bool:
        """Whether a local command to ``device_id`` is recent enough to echo.

        Uses the coordinator's existing outbound bookkeeping — the transport
        health tracker stamps ``last_send_ts`` on every successful command, any
        transport — so nothing new has to be tracked for this.
        """
        last_sent = self._coordinator.device_last_command_sent(device_id)
        if last_sent is None:
            return False
        age = (dt_util.utcnow() - last_sent).total_seconds()
        return bool(0 <= age < NUDGE_ECHO_SUPPRESS_SECONDS)

    # Coordinator internals, read in exactly one place each. An upstream
    # rename of any of these is a one-line fix in this block.

    def _is_lan_reachable(self, device_id: str) -> bool:
        """Whether the device currently has a live LAN correlation."""
        return self._lan_ip(device_id) is not None

    def _lan_ip(self, device_id: str) -> str | None:
        """The correlated LAN IP for ``device_id``, or None if not on LAN."""
        info = self._coordinator._lan_devices.get(device_id)
        return info.ip if info is not None and info.ip else None

    def _lan_client(self) -> GoveeLanClient | None:
        """The coordinator's LAN client, or None when LAN is not running."""
        return self._coordinator._lan_client

    def _apply_lan_read(self, device_id: str, status: LanDevStatus) -> None:
        """Overlay a parsed read, notifying HA only if a value changed."""
        self._coordinator._apply_lan_read(device_id, status)


@callback
def async_note_cloud_push(coordinator: GoveeCoordinator, device_id: str) -> None:
    """Hook called from the coordinator's MQTT push handler.

    The single entry point this feature adds to ``coordinator.py``. Builds the
    per-coordinator manager on first use so nothing has to be initialised in
    the coordinator's ``__init__``.

    Args:
        coordinator: The coordinator that received the push.
        device_id: The device the cloud pushed state for.
    """
    manager: LanNudgeManager | None = getattr(coordinator, _MANAGER_ATTR, None)
    if manager is None:
        manager = LanNudgeManager(coordinator)
        setattr(coordinator, _MANAGER_ATTR, manager)
    manager.async_note_cloud_push(device_id)


@callback
def async_cancel_nudges(coordinator: GoveeCoordinator) -> None:
    """Cancel any scheduled nudge reads for ``coordinator`` (shutdown/reload)."""
    manager: LanNudgeManager | None = getattr(coordinator, _MANAGER_ATTR, None)
    if manager is not None:
        manager.async_cancel()
