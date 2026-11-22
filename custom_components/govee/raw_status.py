"""Device state from raw status frames on the AWS IoT channel (fork feature).

Applies the reports :func:`~.api.protocol.decode_status` reads, for the SKUs
whose profile declares them. Power goes into the device state: from the
``ee 30 01 <on>`` push on a ``power_push`` profile, and from the lamp bit of the
zone power report on a ``zone_power_report`` profile. The gradient and
power-off memory settings go into a per-device store that their entities read.
A profile without ``schedule_report`` gets its schedule replies merged into
:mod:`.light_schedule` here. Settings are read with raw queries over MQTT
``ptReal`` only, because a raw query is answered on the device's cloud topic
and nowhere else. :func:`async_query_settings` says when each query goes out.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from time import monotonic
from typing import TYPE_CHECKING, Any, Final

from homeassistant.core import CALLBACK_TYPE
from homeassistant.helpers.event import async_call_later

from .api import mqtt_raw_write
from .api.raw_router import upload_lock
from .api.protocol import SCHEDULE_QUERIES, SETTING_QUERIES, Capability, GoveeCodec, decode_status
from .api.zone_power_report import decode_frames as decode_zone_power_frames
from .const import (
    MQTT_STATUS_QUERY_SPACING,
    SCHEDULE_SENSOR_SUFFIXES,
    SUFFIX_POWER_OFF_MEMORY,
    SUFFIX_SEGMENT_BLENDING,
)
from .light_schedule import apply_schedule_report, schedule_sensors_enabled
from .zone_state import profile_for, raw_only_segment_blending, raw_settings_enabled

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from .coordinator import GoveeCoordinator
    from .models import GoveeDevice

_STORE_ATTR: Final = "_govee_raw_settings"
_READS_ATTR: Final = "_govee_raw_polled_reads"

POLLED_READ_INTERVAL: Final = 600
"""Seconds between two polled reads of a device's settings and schedules."""

READBACK_DELAY: Final = 2.0
"""Seconds from a write to the status request that reads it back."""


@dataclass(frozen=True)
class DeviceSettings:
    """A device's settings as last reported or written. None is not known."""

    gradient: bool | None = None
    power_off_memory: int | None = None


@dataclass
class _PolledReads:
    """When each polled device was last read, and which are due regardless."""

    last: dict[str, float] = field(default_factory=dict)
    stale: set[str] = field(default_factory=set)
    pending: dict[str, CALLBACK_TYPE] = field(default_factory=dict)
    read_at_connect: set[str] = field(default_factory=set)

    def due(self, device_id: str) -> bool:
        last = self.last.get(device_id)
        return device_id in self.stale or last is None or monotonic() - last >= POLLED_READ_INTERVAL

    def done(self, device_id: str) -> None:
        self.last[device_id] = monotonic()
        self.stale.discard(device_id)

    def cancel(self) -> None:
        """Cancel every pending readback."""
        for unsub in self.pending.values():
            unsub()
        self.pending.clear()


def _reads(coordinator: GoveeCoordinator) -> _PolledReads:
    existing = getattr(coordinator, _READS_ATTR, None)
    if isinstance(existing, _PolledReads):
        return existing
    reads = _PolledReads()
    setattr(coordinator, _READS_ATTR, reads)
    entry = getattr(coordinator, "config_entry", None)
    if entry is not None:
        entry.async_on_unload(reads.cancel)
    return reads


def _store(coordinator: GoveeCoordinator) -> dict[str, DeviceSettings]:
    existing = getattr(coordinator, _STORE_ATTR, None)
    if not isinstance(existing, dict):
        existing = {}
        setattr(coordinator, _STORE_ATTR, existing)
    return existing


def device_settings(coordinator: GoveeCoordinator, device_id: str) -> DeviceSettings:
    """The device's settings as last reported or written."""
    return _store(coordinator).get(device_id, DeviceSettings())


def record_settings(coordinator: GoveeCoordinator, device_id: str, **changes: Any) -> None:
    """Store settings a write just set, so a later reading cannot predate them."""
    store = _store(coordinator)
    store[device_id] = replace(store.get(device_id, DeviceSettings()), **changes)


def raw_entity_unwanted(entry: ConfigEntry, device: GoveeDevice, suffix: str) -> bool:
    """Whether the entry no longer builds the device's raw-only entity with ``suffix``.

    Covers the power-off memory select, a blending switch without the cloud
    capability, and the schedule sensors. Any other suffix is False.
    """
    if suffix == SUFFIX_POWER_OFF_MEMORY:
        return not raw_settings_enabled(entry)
    if suffix == SUFFIX_SEGMENT_BLENDING:
        return raw_only_segment_blending(device) and not raw_settings_enabled(entry)
    if suffix in SCHEDULE_SENSOR_SUFFIXES:
        return not schedule_sensors_enabled(entry, device.sku)
    return False


def apply_raw_status(coordinator: GoveeCoordinator, device: GoveeDevice, frames: Iterable[bytes]) -> bool:
    """Apply the reports in ``frames`` to the device's state and settings.

    Args:
        coordinator: The coordinator owning the device.
        device: The device the frames came from.
        frames: Raw 20-byte frames, in any mix.

    Returns:
        True when anything changed, so the caller notifies listeners.
    """
    profile = profile_for(device.sku)
    if profile is None or device.is_group:
        return False
    frames = list(frames)
    report = decode_status(frames)
    power = report.power if profile.power_push else None
    zone_report = decode_zone_power_frames(frames, profile)
    if zone_report is not None:
        power = zone_report.lamp_on
    changed = False
    state = coordinator.get_state(device.device_id)
    if power is not None and state is not None and state.power_state != power:
        state.power_state = power
        changed = True

    changes: dict[str, Any] = {}
    if report.gradient is not None and profile.supports(Capability.GRADIENT):
        changes["gradient"] = report.gradient
    if report.power_off_memory is not None and profile.supports(Capability.POWER_OFF_MEMORY):
        changes["power_off_memory"] = report.power_off_memory
    before = device_settings(coordinator, device.device_id)
    after = replace(before, **changes)
    if after != before:
        _store(coordinator)[device.device_id] = after
        changed = True
    if not profile.schedule_report and apply_schedule_report(coordinator, device, frames):
        changed = True
    return changed


def request_readback(coordinator: GoveeCoordinator, device_id: str) -> None:
    """Read a polled device back shortly after this integration wrote to it.

    The device is marked due, and a status request follows after
    :data:`READBACK_DELAY` (:func:`async_query_settings_on_request`). Writes
    inside that delay share one request. A ``schedule_report`` profile pushes
    its schedule itself, so nothing is scheduled for it.
    """
    device = coordinator.devices.get(device_id)
    profile = None if device is None else profile_for(device.sku)
    if profile is None or profile.schedule_report:
        return
    reads = _reads(coordinator)
    reads.stale.add(device_id)
    if device_id in reads.pending:
        return

    async def _read_back(_now: Any) -> None:
        reads.pending.pop(device_id, None)
        await coordinator.async_request_status(device_id, user=True)

    reads.pending[device_id] = async_call_later(coordinator.hass, READBACK_DELAY, _read_back)


async def async_read_once_at_connect(coordinator: GoveeCoordinator) -> int:
    """Read each raw-profile device once, for an entry with the status re-query off.

    Runs on every MQTT connect. A device whose request went out is not asked
    again by a later connect, so a reconnect only retries the ones that failed.

    Returns:
        How many devices were asked.
    """
    reads = _reads(coordinator)
    asked = 0
    for device_id, device in list(coordinator.devices.items()):
        if device_id in reads.read_at_connect or device.is_group:
            continue
        profile = profile_for(device.sku)
        if profile is None or not profile.supports(Capability.QUERY):
            continue
        if await coordinator.async_request_status(device_id, user=True):
            reads.read_at_connect.add(device_id)
            asked += 1
    return asked


async def async_query_settings_on_request(coordinator: GoveeCoordinator, device_id: str, *, user: bool) -> int:
    """Raw queries that follow a status request outside the sweep.

    The polled reads go out only for a user's request (the service, or a
    :func:`request_readback`), never for a burst tick or an automatic
    follow-up, and then only when due.

    Args:
        coordinator: The coordinator owning the device.
        device_id: The device just asked for its status.
        user: The request came from the user, as in ``async_request_status``.

    Returns:
        How many queries went out, as :func:`async_query_settings`.
    """
    burst = getattr(coordinator, "status_burst", None)
    polled = user and not (burst is not None and burst.running(device_id))
    return await async_query_settings(coordinator, device_id, polled=polled)


async def async_query_settings(coordinator: GoveeCoordinator, device_id: str, *, polled: bool = True) -> int:
    """Ask the device for its settings and schedules, one query per envelope.

    A ``schedule_report`` profile is asked for its settings on every call. A
    profile without it pushes neither, so its settings and schedules are
    polled. They go out when ``polled`` is set and the device is due, and the
    device is due once per :data:`POLLED_READ_INTERVAL`, after
    :func:`request_readback`, and before its first read.

    Each publish is followed by ``MQTT_STATUS_QUERY_SPACING``. The caller
    holds upstream's status query lock and in-flight marker for the device, so
    a session drop during these queries is blamed on it.

    Args:
        coordinator: The coordinator owning the device.
        device_id: The device to ask.
        polled: Whether the polled reads may go out on this call.

    Returns:
        How many queries went out. 0 when nothing is due, the MQTT ``ptReal``
        tier is unavailable, or a fork upload to the device is in progress.

    The device's upload lock is taken without waiting and held for this
    device's queries only, so an upload that starts mid-sweep waits for them.
    """
    device = coordinator.devices.get(device_id)
    if device is None or device.is_group:
        return 0
    profile = profile_for(device.sku)
    if profile is None or not profile.supports(Capability.QUERY):
        return 0
    reads = _reads(coordinator)
    queries: dict[Capability, tuple[int, ...]] = {}
    if profile.schedule_report or (polled and reads.due(device_id)):
        queries = {capability: (command_type,) for capability, command_type in SETTING_QUERIES.items()}
    if queries and not profile.schedule_report:
        queries.update(SCHEDULE_QUERIES)
    queries = {capability: query for capability, query in queries.items() if profile.supports(capability)}
    if not queries:
        return 0
    lock = upload_lock(coordinator, device_id)
    if lock.locked():
        return 0
    codec = GoveeCodec(profile)
    sent = 0

    # An unheld lock with no waiters is taken without yielding, so the check
    # above and this acquire cannot be split by an upload.

    async with lock:
        for capability, query in queries.items():
            if await mqtt_raw_write.async_send_frames(
                coordinator,
                device_id,
                device.sku,
                profile,
                [codec.query(*query)],
                what=f"{capability.value} read",
            ):
                sent += 1
                await asyncio.sleep(MQTT_STATUS_QUERY_SPACING)
    if sent and not profile.schedule_report:
        reads.done(device_id)
    return sent
