"""Service actions that write on-device schedules and music mode over raw frames (fork).

- ``govee.set_light_timer``: one of the device's on/off timers.
- ``govee.set_sleep_timer``: dim to off over a number of minutes.
- ``govee.set_wake_up``: brighten to a level by a time of day.
- ``govee.set_music_mode``: one of the device's music effects.

Each action takes one device, checks that its profile encodes the setting, and
sends the frames through :func:`~.api.raw_router.async_device_frames`. No cloud
command carries these settings, so an action fails when no raw pipe takes the
frames. A sent schedule write is stored in :mod:`.light_schedule`, which the
schedule sensors read.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import time
from typing import Any, Final

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .api.protocol import Capability, DeviceProfile, GoveeCodec, LightTimer, SleepTimer, WakeUp
from .api.protocol.encoders import repeat_byte, repeat_days
from .api.raw_router import async_device_frames, white_point
from .const import DOMAIN
from .coordinator import GoveeCoordinator
from .light_schedule import record_sleep, record_timer, record_wake_up, schedule
from .models import GoveeDevice
from .upstream_access import coordinator_for_device, device_not_found
from .zone_state import profile_for

ATTR_DEVICE_ID = "device_id"
ATTR_SLOT = "slot"
ATTR_ENABLED = "enabled"
ATTR_ACTION = "action"
ATTR_TIME = "time"
ATTR_WEEKDAYS = "weekdays"
ATTR_MINUTES = "minutes"
ATTR_START_BRIGHTNESS = "start_brightness"
ATTR_DURATION = "duration"
ATTR_END_BRIGHTNESS = "end_brightness"
ATTR_COLOR_TEMP_KELVIN = "color_temp_kelvin"
ATTR_EFFECT = "effect"
ATTR_SENSITIVITY = "sensitivity"
ATTR_SOFT = "soft"
ATTR_MANUAL_COLOR = "manual_color"
ATTR_PALETTE = "palette"

SERVICE_SET_LIGHT_TIMER = "set_light_timer"
SERVICE_SET_SLEEP_TIMER = "set_sleep_timer"
SERVICE_SET_WAKE_UP = "set_wake_up"
SERVICE_SET_MUSIC_MODE = "set_music_mode"

DEFAULT_WAKE_UP_KELVIN: Final = 2700

WEEKDAYS: Final = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
"""Weekday option keys. The index is the weekday bit."""

TIMER_ACTIONS: Final = ("turn_on", "turn_off")

_WEEKDAY_LIST = vol.All(cv.ensure_list, [vol.In(WEEKDAYS)])
_PERCENT = vol.All(vol.Coerce(int), vol.Range(min=1, max=100))
_MINUTES = vol.All(vol.Coerce(int), vol.Range(min=1, max=255))

SET_LIGHT_TIMER_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Required(ATTR_SLOT): vol.All(vol.Coerce(int), vol.Range(min=1)),
        vol.Optional(ATTR_ENABLED, default=True): cv.boolean,
        vol.Optional(ATTR_ACTION): vol.In(TIMER_ACTIONS),
        vol.Optional(ATTR_TIME): cv.time,
        vol.Optional(ATTR_WEEKDAYS): _WEEKDAY_LIST,
    }
)

SET_SLEEP_TIMER_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Optional(ATTR_ENABLED, default=True): cv.boolean,
        vol.Optional(ATTR_MINUTES, default=15): _MINUTES,
        vol.Optional(ATTR_START_BRIGHTNESS, default=30): _PERCENT,
    }
)

SET_WAKE_UP_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Optional(ATTR_ENABLED, default=True): cv.boolean,
        vol.Optional(ATTR_TIME): cv.time,
        vol.Optional(ATTR_WEEKDAYS, default=[]): _WEEKDAY_LIST,
        vol.Optional(ATTR_DURATION, default=10): _MINUTES,
        vol.Optional(ATTR_END_BRIGHTNESS, default=100): _PERCENT,
        vol.Optional(ATTR_COLOR_TEMP_KELVIN, default=DEFAULT_WAKE_UP_KELVIN): vol.All(
            vol.Coerce(int), vol.Range(min=1)
        ),
    }
)

_RGB = vol.All(vol.ExactSequence((cv.byte, cv.byte, cv.byte)), vol.Coerce(tuple))

SET_MUSIC_MODE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Required(ATTR_EFFECT): vol.All(cv.string, vol.Lower),
        vol.Optional(ATTR_SENSITIVITY, default=99): vol.All(vol.Coerce(int), vol.Range(min=0, max=99)),
        vol.Optional(ATTR_SOFT, default=False): cv.boolean,
        vol.Optional(ATTR_MANUAL_COLOR): _RGB,
        vol.Optional(ATTR_PALETTE): vol.All(cv.ensure_list, vol.Length(min=1), [_RGB]),
    }
)


def _weekday_bits(names: Sequence[str]) -> list[int]:
    return [WEEKDAYS.index(name) for name in names]


def _days(weekdays: Sequence[int]) -> tuple[int, ...]:
    """Weekdays as the decoder reports them, sorted and without repeats."""
    return repeat_days(repeat_byte(weekdays))


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
    raw_id = call.data[ATTR_DEVICE_ID]
    found = coordinator_for_device(hass, raw_id)
    if found is None:
        raise device_not_found(raw_id)
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

    ``slot`` counts from 1. An enabled timer needs a time and an action. A
    disabled one takes each field the call leaves out from the slot as last
    reported or written, and with no time given it needs that slot known. A
    field left out with nothing known is "turn off" or "run once".
    """
    coordinator, device, profile = _resolve(hass, call, lambda profile: profile.supports(Capability.LIGHT_TIMER))
    data: dict[str, Any] = call.data
    slots = GoveeCodec(profile).timer_slots()
    if data[ATTR_SLOT] > slots:
        raise _validation_error("timer_slot_out_of_range", device=device.name, slots=str(slots))
    enabled: bool = data[ATTR_ENABLED]
    if enabled and (ATTR_TIME not in data or ATTR_ACTION not in data):
        raise _validation_error("timer_needs_time_and_action")
    timers = schedule(coordinator, device.device_id).timers
    known = None if enabled or timers is None or data[ATTR_SLOT] > len(timers) else timers[data[ATTR_SLOT] - 1]
    if not enabled and ATTR_TIME not in data and known is None:
        raise _validation_error("timer_slot_unknown", device=device.name, slot=str(data[ATTR_SLOT]))
    at: time = data[ATTR_TIME] if ATTR_TIME in data else time(known.hour, known.minute) if known else time(0, 0)
    if ATTR_ACTION in data:
        turn_on = data[ATTR_ACTION] == "turn_on"
    else:
        turn_on = known is not None and known.turn_on
    if ATTR_WEEKDAYS in data:
        weekdays = _weekday_bits(data[ATTR_WEEKDAYS])
    else:
        weekdays = [] if known is None else list(known.weekdays)

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
    record_timer(
        coordinator,
        device.device_id,
        data[ATTR_SLOT] - 1,
        LightTimer(enabled=enabled, turn_on=turn_on, hour=at.hour, minute=at.minute, weekdays=_days(weekdays)),
    )
    coordinator.async_update_listeners()


async def async_set_sleep_timer_handler(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle ``govee.set_sleep_timer``."""
    coordinator, device, _profile = _resolve(hass, call, lambda profile: profile.supports(Capability.SLEEP_TIMER))
    data: dict[str, Any] = call.data
    await _async_send(
        coordinator,
        device,
        call,
        lambda codec: [
            codec.sleep_timer(
                enabled=data[ATTR_ENABLED],
                start_brightness=data[ATTR_START_BRIGHTNESS],
                minutes=data[ATTR_MINUTES],
            )
        ],
    )
    record_sleep(
        coordinator,
        device.device_id,
        SleepTimer(
            enabled=data[ATTR_ENABLED],
            start_brightness=data[ATTR_START_BRIGHTNESS],
            minutes=data[ATTR_MINUTES],
            remaining_minutes=data[ATTR_MINUTES],
        ),
    )
    coordinator.async_update_listeners()


async def async_set_wake_up_handler(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle ``govee.set_wake_up``.

    An enabled wake-up needs a time. The colour temperature must lie in the
    profile's range and is sent with HA's RGB approximation of it.
    """
    coordinator, device, profile = _resolve(hass, call, lambda profile: profile.supports(Capability.WAKE_UP))
    data: dict[str, Any] = call.data
    enabled: bool = data[ATTR_ENABLED]
    if enabled and ATTR_TIME not in data:
        raise _validation_error("wake_up_needs_time")
    kelvin: int = data[ATTR_COLOR_TEMP_KELVIN]
    if not profile.kelvin.minimum <= kelvin <= profile.kelvin.maximum:
        raise _validation_error(
            "kelvin_out_of_range",
            device=device.name,
            kelvin=str(kelvin),
            minimum=str(profile.kelvin.minimum),
            maximum=str(profile.kelvin.maximum),
        )
    at: time = data.get(ATTR_TIME, time(0, 0))
    weekdays = _weekday_bits(data[ATTR_WEEKDAYS])

    await _async_send(
        coordinator,
        device,
        call,
        lambda codec: [
            codec.wake_up(
                enabled=enabled,
                end_brightness=data[ATTR_END_BRIGHTNESS],
                hour=at.hour,
                minute=at.minute,
                weekdays=weekdays,
                duration=data[ATTR_DURATION],
                kelvin=kelvin,
                white_rgb=white_point(kelvin),
            )
        ],
    )
    record_wake_up(
        coordinator,
        device.device_id,
        WakeUp(
            enabled=enabled,
            end_brightness=data[ATTR_END_BRIGHTNESS],
            hour=at.hour,
            minute=at.minute,
            weekdays=_days(weekdays),
            duration=data[ATTR_DURATION],
            rgb=white_point(kelvin),
            kelvin=kelvin,
        ),
    )
    coordinator.async_update_listeners()


async def async_set_music_mode_handler(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle ``govee.set_music_mode``.

    An effect that takes a palette uploads the call's, else the profile's
    default; with neither, the commit goes alone. An effect without a palette
    refuses one, and a profile whose commit carries no options refuses a
    manual colour.
    """
    coordinator, device, profile = _resolve(hass, call, lambda profile: profile.music is not None)
    spec = profile.music
    assert spec is not None
    data: dict[str, Any] = call.data
    effect = spec.effects.get(data[ATTR_EFFECT])
    if effect is None:
        raise _validation_error(
            "music_effect_unsupported", device=device.name, effect=data[ATTR_EFFECT], effects=", ".join(spec.effects)
        )
    if ATTR_MANUAL_COLOR in data and not spec.commit_options:
        raise _validation_error("music_manual_color_unsupported", device=device.name)
    palette: Sequence[tuple[int, int, int]] | None = data.get(ATTR_PALETTE)
    if effect not in spec.palette_effects:
        if palette is not None:
            raise _validation_error("music_palette_unsupported", effect=data[ATTR_EFFECT])
    elif palette is None:
        palette = spec.default_palette or None
    if palette is not None and len(palette) > spec.max_colors:
        raise _validation_error("music_palette_too_long", maximum=str(spec.max_colors))

    await _async_send(
        coordinator,
        device,
        call,
        lambda codec: codec.music_effect(
            effect,
            sensitivity=data[ATTR_SENSITIVITY],
            colors=palette,
            soft=data[ATTR_SOFT],
            manual_color=data.get(ATTR_MANUAL_COLOR),
        ),
    )


@callback
def async_setup_raw_actions(hass: HomeAssistant) -> None:
    """Register the raw schedule and music actions."""

    async def _set_light_timer(call: ServiceCall) -> None:
        await async_set_light_timer_handler(hass, call)

    async def _set_sleep_timer(call: ServiceCall) -> None:
        await async_set_sleep_timer_handler(hass, call)

    async def _set_wake_up(call: ServiceCall) -> None:
        await async_set_wake_up_handler(hass, call)

    async def _set_music_mode(call: ServiceCall) -> None:
        await async_set_music_mode_handler(hass, call)

    hass.services.async_register(DOMAIN, SERVICE_SET_LIGHT_TIMER, _set_light_timer, schema=SET_LIGHT_TIMER_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_SET_SLEEP_TIMER, _set_sleep_timer, schema=SET_SLEEP_TIMER_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_SET_WAKE_UP, _set_wake_up, schema=SET_WAKE_UP_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_SET_MUSIC_MODE, _set_music_mode, schema=SET_MUSIC_MODE_SCHEMA)
