"""Device state from raw status frames on the AWS IoT channel (fork feature).

Applies the reports :func:`~.api.protocol.decode_status` reads, for the SKUs
whose profile declares them. Power goes into the device state: from the
``ee 30 01 <on>`` push on a ``power_push`` profile, and from the lamp bit of the
zone power report on a ``zone_power_report`` profile. The gradient and
power-off memory settings go into a per-device store that their entities read.
A profile without ``schedule_report`` gets its schedule replies merged into
:mod:`.light_schedule` here. Settings are read with raw queries over MQTT
``ptReal`` only, because a raw query is answered on the device's cloud topic
and nowhere else.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Final

from .api import mqtt_raw_write
from .api.raw_router import upload_lock
from .api.protocol import SCHEDULE_QUERIES, SETTING_QUERIES, Capability, GoveeCodec, decode_status
from .api.zone_power_report import decode_frames as decode_zone_power_frames
from .const import SCHEDULE_SENSOR_SUFFIXES, SUFFIX_POWER_OFF_MEMORY, SUFFIX_SEGMENT_BLENDING
from .light_schedule import apply_schedule_report, schedule_sensors_enabled
from .zone_state import profile_for, raw_only_segment_blending, raw_settings_enabled

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from .coordinator import GoveeCoordinator
    from .models import GoveeDevice

_STORE_ATTR: Final = "_govee_raw_settings"


@dataclass(frozen=True)
class DeviceSettings:
    """A device's settings as last reported or written. None is not known."""

    gradient: bool | None = None
    power_off_memory: int | None = None


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


async def async_query_settings(coordinator: GoveeCoordinator, device_id: str) -> int:
    """Ask the device for every setting its profile encodes, one query per envelope.

    A profile without ``schedule_report`` is also asked for each schedule
    capability it encodes.

    Args:
        coordinator: The coordinator owning the device.
        device_id: The device to ask.

    Returns:
        How many queries went out. 0 when the profile encodes no setting, the
        MQTT ``ptReal`` tier is unavailable, or a fork upload to the device is
        in progress.

    The device's upload lock is taken without waiting and held for this
    device's queries only, so an upload that starts mid-sweep waits for them.
    """
    device = coordinator.devices.get(device_id)
    if device is None or device.is_group:
        return 0
    profile = profile_for(device.sku)
    if profile is None or not profile.supports(Capability.QUERY):
        return 0
    lock = upload_lock(coordinator, device_id)
    if lock.locked():
        return 0
    codec = GoveeCodec(profile)
    sent = 0
    queries: dict[Capability, tuple[int, ...]] = {
        capability: (command_type,) for capability, command_type in SETTING_QUERIES.items()
    }
    if not profile.schedule_report:
        queries.update(SCHEDULE_QUERIES)

    # An unheld lock with no waiters is taken without yielding, so the check
    # above and this acquire cannot be split by an upload.

    async with lock:
        for capability, query in queries.items():
            if not profile.supports(capability):
                continue
            if await mqtt_raw_write.async_send_frames(
                coordinator,
                device_id,
                device.sku,
                profile,
                [codec.query(*query)],
                what=f"{capability.value} read",
            ):
                sent += 1
    return sent
