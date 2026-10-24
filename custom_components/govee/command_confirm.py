"""Confirm light commands against the device's own AWS IoT report, resend dropped ones (#245).

Govee's cloud sometimes answers a control call with HTTP 200 and never
delivers it; an MQTT publish is never acknowledged at all. The LAN tier reads
the device back after each write; this does the same for the cloud and MQTT
tiers, using the status the device pushes over AWS IoT.

After a light command goes out, the confirmer waits briefly for a report,
asks for one status query if none came, and at the end of the window:

- the report shows the new value: confirmed;
- the report still shows the value from before the command: resend once,
  if the request budget allows (a resend is never itself confirmed);
- a report shows some other value (the app, a button, an automation): left
  alone;
- no report mentioned the field: unverified, nothing resent.

Safety and budget rules (security review F-03/F-04):

- Lights only: never kettles, heaters, humidifiers, fans or any other
  appliance, never groups, and never a power-on (a resend must not switch
  anything on by itself).
- At most one status query, sent through the coordinator's throttled path
  (which skips excluded and quarantined models, #259's refusal and a
  re-query of 0), and at most one resend per command.
- A resend spends a cloud request only when the coordinator says one fits
  (daily budget, per-minute reserve, no 429 back-off).
- The latest command for a field supersedes a pending confirm before it is
  dispatched; power supersedes every field, colour and colour temperature
  replace each other.

The module holds no Home Assistant objects: the coordinator provides
:class:`ConfirmHooks` and wires it in.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from collections.abc import Coroutine
from dataclasses import dataclass
from typing import Any, Final, Protocol

from .models import BrightnessCommand, ColorCommand, ColorTempCommand, GoveeDevice, GoveeDeviceState, PowerCommand
from .models.commands import DeviceCommand
from .models.device import DEVICE_TYPE_LIGHT
from .models.state import RGBColor

_LOGGER = logging.getLogger(__name__)

# Seconds to wait for a report before asking for one, and for the whole check.
CONFIRM_QUERY_WAIT: Final = 1.5
CONFIRM_WINDOW: Final = 6.0
# How far a report may differ from the sent value and still confirm it: the
# frame's 1-100 brightness, each 0-255 colour channel, and the colour
# temperature (the LAN readback's tolerance).
BRIGHTNESS_TOLERANCE: Final = 2
COLOR_TOLERANCE: Final = 2
COLOR_TEMP_TOLERANCE: Final = 150

FIELD_POWER: Final = "power"
FIELD_BRIGHTNESS: Final = "brightness"
FIELD_COLOR: Final = "color"
FIELD_COLOR_TEMP: Final = "color_temp"
# Fields one command makes stale: colour and colour temperature are one
# channel on the device; power changes what everything else means.
_SUPERSEDES: Final = {
    FIELD_POWER: (FIELD_POWER, FIELD_BRIGHTNESS, FIELD_COLOR, FIELD_COLOR_TEMP),
    FIELD_BRIGHTNESS: (FIELD_BRIGHTNESS,),
    FIELD_COLOR: (FIELD_COLOR, FIELD_COLOR_TEMP),
    FIELD_COLOR_TEMP: (FIELD_COLOR, FIELD_COLOR_TEMP),
}

COUNTERS: Final = ("confirmed", "resent", "unconfirmed", "unverified", "skipped_budget", "superseded")


class ConfirmHooks(Protocol):
    """What the confirmer needs from the coordinator."""

    def device(self, device_id: str) -> GoveeDevice | None:
        """The device, if known."""

    def state(self, device_id: str) -> GoveeDeviceState | None:
        """The device's current state, if any."""

    def heard_over_mqtt(self, device_id: str) -> bool:
        """Whether the device has pushed over AWS IoT this session."""

    async def async_request_status(self, device_id: str) -> bool:
        """Send one status query through the throttled path; whether it went out."""

    def resend_allowed(self) -> bool:
        """Whether one more cloud request fits the budget and rate limits now."""

    async def async_resend(self, device_id: str, command: DeviceCommand) -> bool:
        """Send the command once more over the cloud API, without confirming it."""

    def create_task(self, coro: Coroutine[Any, Any, None], name: str) -> asyncio.Task[None]:
        """Run ``coro`` in the background, cancelled when the entry unloads."""


def command_field(command: DeviceCommand) -> tuple[str, Any] | None:
    """The field a command sets and the value it sets it to, or None if not confirmable."""
    if isinstance(command, PowerCommand):
        return FIELD_POWER, command.power_on
    if isinstance(command, BrightnessCommand):
        return FIELD_BRIGHTNESS, command.brightness
    if isinstance(command, ColorCommand):
        return FIELD_COLOR, command.color
    if isinstance(command, ColorTempCommand):
        return FIELD_COLOR_TEMP, command.kelvin
    return None


def state_value(state: GoveeDeviceState, name: str) -> Any:
    """The field's value in a state."""
    if name == FIELD_POWER:
        return state.power_state
    if name == FIELD_BRIGHTNESS:
        return state.brightness
    if name == FIELD_COLOR:
        return state.color
    return state.color_temp_kelvin


def report_values(data: dict[str, Any]) -> dict[str, Any]:
    """The confirmable fields an AWS IoT state push carries (keys as update_from_mqtt reads them)."""
    values: dict[str, Any] = {}
    if "onOff" in data:
        values[FIELD_POWER] = bool(data["onOff"])
    brightness = data.get("brightness")
    if isinstance(brightness, int) and not isinstance(brightness, bool):
        values[FIELD_BRIGHTNESS] = brightness
    color = data.get("color")
    if isinstance(color, dict):
        values[FIELD_COLOR] = RGBColor.from_dict(color)
    elif isinstance(color, int) and not isinstance(color, bool):
        values[FIELD_COLOR] = RGBColor.from_packed_int(color)
    kelvin = data.get("colorTemInKelvin")
    if isinstance(kelvin, int) and not isinstance(kelvin, bool) and kelvin > 0:
        values[FIELD_COLOR_TEMP] = kelvin
    return values


def matches(name: str, reported: Any, wanted: Any) -> bool:
    """Whether a reported value is the wanted one, within the field's tolerance."""
    if reported is None or wanted is None:
        return reported is wanted
    if name == FIELD_POWER:
        return bool(reported) == bool(wanted)
    if name == FIELD_COLOR:
        return all(
            abs(a - b) <= COLOR_TOLERANCE
            for a, b in zip((reported.r, reported.g, reported.b), (wanted.r, wanted.g, wanted.b), strict=True)
        )
    tolerance = BRIGHTNESS_TOLERANCE if name == FIELD_BRIGHTNESS else COLOR_TEMP_TOLERANCE
    return abs(int(reported) - int(wanted)) <= tolerance


def eligible(device: GoveeDevice | None, command: DeviceCommand) -> bool:
    """Whether a command to this device is confirmed at all (lights only, never power-on)."""
    if device is None or device.is_group or device.device_type != DEVICE_TYPE_LIGHT or device.decodes_kettle_frames:
        return False
    if isinstance(command, PowerCommand) and command.power_on:
        return False
    return command_field(command) is not None


@dataclass
class PendingConfirm:
    """One command waiting for its confirmation."""

    device_id: str
    command: DeviceCommand
    field: str
    wanted: Any
    previous: Any
    reported: Any = None
    has_report: bool = False
    report_event: asyncio.Event = dataclasses.field(default_factory=asyncio.Event)
    task: asyncio.Task[None] | None = None


class CommandConfirmer:
    """Confirms light commands from the device's AWS IoT report and resends dropped ones once."""

    def __init__(
        self,
        hooks: ConfirmHooks,
        *,
        window: float = CONFIRM_WINDOW,
        query_wait: float = CONFIRM_QUERY_WAIT,
    ) -> None:
        """Initialize with the coordinator's hooks."""
        self._hooks = hooks
        self._window = window
        self._query_wait = min(query_wait, window)
        self._pending: dict[tuple[str, str], PendingConfirm] = {}
        self._counters: dict[str, dict[str, int]] = {}

    def prepare(self, device_id: str, command: DeviceCommand) -> PendingConfirm | None:
        """Note a command before it is dispatched.

        Cancels the pending confirms it makes stale, then snapshots the
        field's current value as the "before" to compare against. Returns
        None when the command is not confirmed (not a light, power-on,
        unconfirmable, not on AWS IoT, or a no-op).
        """
        found = command_field(command)
        if found is not None:
            for stale in _SUPERSEDES[found[0]]:
                self._cancel((device_id, stale), count=True)
        if found is None or not eligible(self._hooks.device(device_id), command):
            return None
        if not self._hooks.heard_over_mqtt(device_id):
            return None
        state = self._hooks.state(device_id)
        if state is None:
            return None
        name, wanted = found
        previous = state_value(state, name)
        if matches(name, previous, wanted):
            return None
        return PendingConfirm(device_id=device_id, command=command, field=name, wanted=wanted, previous=previous)

    def start(self, pending: PendingConfirm | None) -> None:
        """Start confirming a command that was sent (``pending`` from :meth:`prepare`)."""
        if pending is None:
            return
        key = (pending.device_id, pending.field)
        self._cancel(key, count=True)
        self._pending[key] = pending
        pending.task = self._hooks.create_task(
            self._async_confirm(pending), name=f"govee_confirm_{pending.device_id}_{pending.field}"
        )

    def on_report(self, device_id: str, data: dict[str, Any]) -> None:
        """Feed an AWS IoT state push from the device (only reports after a command count)."""
        values = report_values(data)
        for name, value in values.items():
            pending = self._pending.get((device_id, name))
            if pending is None:
                continue
            pending.reported = value
            pending.has_report = True
            pending.report_event.set()

    def counters(self) -> dict[str, dict[str, int]]:
        """Per-device counters, for diagnostics."""
        return {device_id: dict(counts) for device_id, counts in self._counters.items()}

    def cancel_all(self) -> None:
        """Cancel every pending confirm (entry unload)."""
        for key in list(self._pending):
            self._cancel(key, count=False)

    def _count(self, device_id: str, name: str) -> None:
        counts = self._counters.setdefault(device_id, dict.fromkeys(COUNTERS, 0))
        counts[name] += 1

    def _cancel(self, key: tuple[str, str], *, count: bool) -> None:
        pending = self._pending.pop(key, None)
        if pending is None:
            return
        if pending.task is not None and not pending.task.done():
            pending.task.cancel()
        if count:
            self._count(pending.device_id, "superseded")

    async def _wait_for_report(self, pending: PendingConfirm, seconds: float) -> None:
        if seconds <= 0 or pending.report_event.is_set():
            return
        try:
            await asyncio.wait_for(pending.report_event.wait(), timeout=seconds)
        except TimeoutError:
            return

    async def _async_confirm(self, pending: PendingConfirm) -> None:
        key = (pending.device_id, pending.field)
        try:
            await self._wait_for_report(pending, self._query_wait)
            if not (pending.has_report and matches(pending.field, pending.reported, pending.wanted)):
                if not pending.has_report:
                    await self._hooks.async_request_status(pending.device_id)
                # Give the device the rest of the window to report (again).
                pending.report_event.clear()
                await self._wait_for_report(pending, self._window - self._query_wait)
            await self._settle(pending)
        finally:
            if self._pending.get(key) is pending:
                del self._pending[key]

    async def _settle(self, pending: PendingConfirm) -> None:
        device_id = pending.device_id
        if not pending.has_report:
            _LOGGER.debug("No report confirmed %s on %s", pending.field, device_id)
            self._count(device_id, "unverified")
            return
        if matches(pending.field, pending.reported, pending.wanted):
            self._count(device_id, "confirmed")
            return
        if not matches(pending.field, pending.reported, pending.previous):
            # Something else changed it since: not ours to undo.
            _LOGGER.debug("%s on %s changed to something else; not resending", pending.field, device_id)
            self._count(device_id, "unverified")
            return
        if not self._hooks.resend_allowed():
            self._count(device_id, "skipped_budget")
            return
        _LOGGER.debug("%s on %s still shows the old value; resending once", pending.field, device_id)
        if await self._hooks.async_resend(device_id, pending.command):
            self._count(device_id, "resent")
        else:
            self._count(device_id, "unconfirmed")
