"""Device state from raw status frames on the AWS IoT channel (fork feature).

Applies the reports :func:`~.api.protocol.decode_status` reads, for the SKUs
whose profile declares them. Power goes into the device state: from the
``ee 30 01 <on>`` push on a ``power_push`` profile, and from the lamp bit of the
zone power report on a ``zone_power_report`` profile. The gradient and
power-off memory settings go into a per-device store that their entities read.
Settings are read with raw queries over MQTT ``ptReal`` only, because a raw
query is answered on the device's cloud topic and nowhere else.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Final

from .api import mqtt_raw_write
from .api.raw_router import upload_in_progress
from .api.protocol import SETTING_QUERIES, Capability, GoveeCodec, decode_status
from .api.zone_power_report import decode_frames as decode_zone_power_frames
from .zone_state import profile_for

if TYPE_CHECKING:
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
    return changed


async def async_query_settings(coordinator: GoveeCoordinator, device_id: str) -> int:
    """Ask the device for every setting its profile encodes, one query per envelope.

    Args:
        coordinator: The coordinator owning the device.
        device_id: The device to ask.

    Returns:
        How many queries went out. 0 when the profile encodes no setting, the
        MQTT ``ptReal`` tier is unavailable, or a fork upload to the device is
        in progress.
    """
    device = coordinator.devices.get(device_id)
    if device is None or device.is_group or upload_in_progress(coordinator, device_id):
        return 0
    profile = profile_for(device.sku)
    if profile is None or not profile.supports(Capability.QUERY):
        return 0
    codec = GoveeCodec(profile)
    sent = 0
    for capability, command_type in SETTING_QUERIES.items():
        if not profile.supports(capability):
            continue
        if await mqtt_raw_write.async_send_frames(
            coordinator, device_id, device.sku, profile, [codec.query(command_type)], what=f"{capability.value} read"
        ):
            sent += 1
    return sent
