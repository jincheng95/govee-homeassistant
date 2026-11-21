"""Service actions that write on-device schedules over raw frames (fork).

- ``govee.set_light_timer``: one of the device's on/off timers.

Each action takes one device, checks that its profile encodes the setting, and
sends the frames through :func:`~.api.raw_router.async_device_frames`. No cloud
command carries these settings, so an action fails when no raw pipe takes the
frames.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import time
from typing import Any, Final

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv

from . import services
from .api.protocol import Capability, DeviceProfile, GoveeCodec
from .api.raw_router import async_device_frames
from .const import DOMAIN
from .coordinator import GoveeCoordinator
from .models import GoveeDevice
from .zone_state import profile_for

ATTR_SLOT = "slot"
ATTR_ENABLED = "enabled"
ATTR_ACTION = "action"
ATTR_TIME = "time"
ATTR_WEEKDAYS = "weekdays"

SERVICE_SET_LIGHT_TIMER = "set_light_timer"

WEEKDAYS: Final = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
"""Weekday option keys. The index is the weekday bit."""

TIMER_ACTIONS: Final = ("turn_on", "turn_off")

_WEEKDAY_LIST = vol.All(cv.ensure_list, [vol.In(WEEKDAYS)])

SET_LIGHT_TIMER_SCHEMA = vol.Schema(
    {
        vol.Required(services.ATTR_DEVICE_ID): cv.string,
        vol.Required(ATTR_SLOT): vol.All(vol.Coerce(int), vol.Range(min=1)),
        vol.Optional(ATTR_ENABLED, default=True): cv.boolean,
        vol.Optional(ATTR_ACTION): vol.In(TIMER_ACTIONS),
        vol.Optional(ATTR_TIME): cv.time,
        vol.Optional(ATTR_WEEKDAYS, default=[]): _WEEKDAY_LIST,
    }
)


def _weekday_bits(names: Sequence[str]) -> list[int]:
    return [WEEKDAYS.index(name) for name in names]


def _validation_error(key: str, **placeholders: str) -> ServiceValidationError:
    return ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key=key,
        translation_placeholders=placeholders or None,
    )


def _resolve(
    hass: HomeAssistant,
    call: ServiceCall,
    supported: Callable[[DeviceProfile], bool],
) -> tuple[GoveeCoordinator, GoveeDevice, DeviceProfile]:
    """The call's device and its profile.

    Raises:
        ServiceValidationError: The device is unknown, a group, or its
            profile does not encode the action.
    """
    raw_id = call.data[services.ATTR_DEVICE_ID]
    found = services._get_coordinator_for_device(hass, raw_id)
    if found is None:
        raise services._device_not_found(raw_id)
    coordinator, device_id = found
    device = coordinator.devices[device_id]
    profile = profile_for(device.sku)
    if device.is_group or profile is None or not supported(profile):
        raise _validation_error("raw_action_unsupported", device=device.name, action=f"{DOMAIN}.{call.service}")
    return coordinator, device, profile


async def _async_send(
    coordinator: GoveeCoordinator,
    device: GoveeDevice,
    call: ServiceCall,
    build: Callable[[GoveeCodec], Sequence[bytes]],
) -> None:
    """Route the frames, raising when no raw pipe takes them."""
    if not await async_device_frames(coordinator, device, build, what=call.service):
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="raw_action_unavailable",
            translation_placeholders={"device": device.name, "action": f"{DOMAIN}.{call.service}"},
        )


async def async_set_light_timer_handler(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle ``govee.set_light_timer``.

    ``slot`` counts from 1. An enabled timer needs a time and an action; a
    disabled one writes midnight and "turn off" for any field left out.
    """
    coordinator, device, profile = _resolve(hass, call, lambda profile: profile.supports(Capability.LIGHT_TIMER))
    data: dict[str, Any] = call.data
    slots = GoveeCodec(profile).timer_slots()
    if data[ATTR_SLOT] > slots:
        raise _validation_error("timer_slot_out_of_range", device=device.name, slots=str(slots))
    enabled: bool = data[ATTR_ENABLED]
    if enabled and (ATTR_TIME not in data or ATTR_ACTION not in data):
        raise _validation_error("timer_needs_time_and_action")
    at: time = data.get(ATTR_TIME, time(0, 0))
    turn_on = data.get(ATTR_ACTION) == "turn_on"
    weekdays = _weekday_bits(data[ATTR_WEEKDAYS])

    await _async_send(
        coordinator,
        device,
        call,
        lambda codec: [
            codec.light_timer(
                data[ATTR_SLOT] - 1,
                enabled=enabled,
                turn_on=turn_on,
                hour=at.hour,
                minute=at.minute,
                weekdays=weekdays,
            )
        ],
    )


@callback
def async_setup_raw_actions(hass: HomeAssistant) -> None:
    """Register the raw schedule actions (called from ``async_setup_services``)."""

    async def _set_light_timer(call: ServiceCall) -> None:
        await async_set_light_timer_handler(hass, call)

    hass.services.async_register(DOMAIN, SERVICE_SET_LIGHT_TIMER, _set_light_timer, schema=SET_LIGHT_TIMER_SCHEMA)
