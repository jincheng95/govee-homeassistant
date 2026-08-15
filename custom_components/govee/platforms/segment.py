"""Segment light entities for RGBIC devices.

Each segment of an RGBIC LED strip is exposed as a separate light entity,
following the WLED pattern for segment control.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Final

# mypy --strict: HA's `light` module re-exports without __all__, so
# `--no-implicit-reexport` raises attr-defined for each member. The
# suppression is upstream-stub-bound, not a real type error here.
from homeassistant.components.light import (  # type: ignore[attr-defined]
    ATTR_BRIGHTNESS,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
)
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.restore_state import RestoreEntity

from ..api.protocol import ha_to_percent  # fork: level-space churn guard
from ..api.segment_readback import SegmentReading
from ..child_power import async_ensure_device_powered
from ..const import SIGNAL_SEGMENT_READBACK, SUFFIX_SEGMENT
from ..coordinator import GoveeCoordinator
from ..entity import GoveeEntity
from ..models import GoveeDevice, RGBColor, SegmentColorCommand
from ..api.raw_router import async_segment_color

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0

READBACK_WRITE_GRACE_SECONDS: Final = 1.5
"""How long after our own write a readback for that segment is ignored.

The hardware keeps reporting its PRE-command state for well over a second
after a write (the same echo lag the profile table records, §2.1: "do not read
back sooner than ~1.5 s"). A status push landing inside that window carries
the colour the segment had *before* the paint, so applying it would revert the
paint the user just made. The next push, outside the window, corrects for real.
"""


class GoveeSegmentEntity(GoveeEntity, LightEntity, RestoreEntity):
    """Govee segment light entity.

    Represents a single segment of an RGBIC LED strip.

    API Limitation: Govee API returns empty strings for segment colors.
    We use purely optimistic/local state that persists via RestoreEntity.
    This entity intentionally does NOT subscribe to coordinator updates
    to prevent API responses from overwriting local state.

    Fork: it does subscribe to SIGNAL_SEGMENT_READBACK, and to nothing else.
    That signal carries decoded `reference` §6.2 `aa a5` frames — the
    hardware's own per-segment level and RGB, pushed over MQTT — which is a
    genuine reading rather than an API response that does not know the answer.
    The distinction the "no coordinator updates" rule is protecting is
    optimism-versus-ignorance, not optimism-versus-any-input, so this one
    channel is allowed in and the coordinator door stays shut.
    """

    _attr_translation_key = "govee_segment"
    _attr_supported_color_modes = {ColorMode.RGB}
    _attr_color_mode = ColorMode.RGB

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        device: GoveeDevice,
        segment_index: int,
    ) -> None:
        """Initialize the segment entity.

        Args:
            coordinator: Govee data coordinator.
            device: Device this segment belongs to.
            segment_index: Zero-based segment index.
        """
        super().__init__(coordinator, device)
        self._segment_index = segment_index

        # Unique ID combines device and segment
        self._attr_unique_id = f"{device.device_id}{SUFFIX_SEGMENT}{segment_index}"

        # Segment name with 1-based index for user display
        self._attr_name = f"Segment {segment_index + 1}"

        # Translation placeholders
        self._attr_translation_placeholders = {
            "device_name": device.name,
            "segment_index": str(segment_index + 1),
        }

        # Optimistic state (API doesn't return per-segment state)
        self._is_on = True
        self._brightness = 255
        self._rgb_color: tuple[int, int, int] = (255, 255, 255)
        # ``time.monotonic()`` of this segment's last write, 0.0 for never.
        self._written_at: float = 0.0

    @property
    def available(self) -> bool:
        """Return True if entity is available.

        Segments don't depend on coordinator state updates.
        Just check the coordinator is healthy.
        """
        return self.coordinator.last_update_success

    @property
    def is_on(self) -> bool:
        """Return True if segment is on."""
        return self._is_on

    @property
    def brightness(self) -> int:
        """Return brightness (0-255)."""
        return self._brightness

    @property
    def rgb_color(self) -> tuple[int, int, int]:
        """Return RGB color."""
        return self._rgb_color

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the segment on with optional parameters."""
        # Update brightness if provided
        if ATTR_BRIGHTNESS in kwargs:
            self._brightness = kwargs[ATTR_BRIGHTNESS]

        # Update color if provided
        if ATTR_RGB_COLOR in kwargs:
            self._rgb_color = kwargs[ATTR_RGB_COLOR]

        # Create segment color command
        r, g, b = self._rgb_color
        color = RGBColor(r=r, g=g, b=b)

        command = SegmentColorCommand(
            segment_indices=(self._segment_index,),
            color=color,
        )

        await async_ensure_device_powered(self.coordinator, self._device_id)

        if not await async_segment_color(
            self, self._rgb_color, (self._segment_index,), brightness=kwargs.get(ATTR_BRIGHTNESS)
        ):
            await self.coordinator.async_control_device(
                self._device_id,
                command,
            )

        self._is_on = True
        self._written_at = time.monotonic()  # fork: opens the readback grace
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the segment off (set to black).

        Skips the API call if a power-off is already in flight or the device
        is already off — prevents race conditions in area-targeted turn_off
        that cause firmware glitches on RGBIC devices (issue #16).
        """
        # Yield to the event loop so that a concurrent PowerCommand (from the
        # main light entity in an area-targeted turn_off) gets a chance to set
        # the _pending_power_off flag before we check it.
        await asyncio.sleep(0)

        device_state = self.coordinator.get_state(self._device_id)
        device_already_off = device_state is not None and not device_state.power_state
        power_off_pending = self.coordinator.is_power_off_pending(self._device_id)

        if not device_already_off and not power_off_pending:
            command = SegmentColorCommand(
                segment_indices=(self._segment_index,),
                color=RGBColor(r=0, g=0, b=0),
            )
            if not await async_segment_color(self, (0, 0, 0), (self._segment_index,)):
                await self.coordinator.async_control_device(self._device_id, command)
        else:
            _LOGGER.debug(
                "Skipping segment %d turn_off for %s (power_off_pending=%s, device_already_off=%s)",
                self._segment_index,
                self._device_id,
                power_off_pending,
                device_already_off,
            )

        self._is_on = False
        self._written_at = time.monotonic()  # fork: opens the readback grace
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Restore previous state and subscribe to hardware segment readback."""
        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()
        if last_state:
            self._is_on = last_state.state == "on"

            if last_state.attributes.get("brightness"):
                self._brightness = last_state.attributes["brightness"]

            if last_state.attributes.get("rgb_color"):
                self._rgb_color = tuple(last_state.attributes["rgb_color"])

        # Fork: the ONE correction path this entity accepts. Not a coordinator
        # subscription — see the class docstring for why that stays shut — but
        # a dedicated signal carrying decoded `reference` §6.2 `aa a5` frames,
        # which are per-segment truth read back off the hardware.
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_SEGMENT_READBACK.format(device_id=self._device_id),
                self._handle_segment_readback,
            )
        )

    @callback
    def _handle_segment_readback(self, readings: dict[int, SegmentReading]) -> None:
        """Reconcile optimistic state with what the hardware reports.

        The push arrives ~1-2 s after a write and reflects applied state, so it
        is trusted over local optimism — which is the point: it also catches
        paints made from the vendor app, and writes that never landed.

        Writes nothing when the reading agrees with what this entity already
        shows. Segment readback arrives on every status push, and a segment
        that has not moved must not produce a state change each time.

        An OFF reading updates on/off only. Off is expressed on the wire as
        black (there is no per-segment power command), so adopting its RGB
        would overwrite the colour this segment should return to when it is
        turned back on with the black that means "off".

        A reading arriving within :data:`READBACK_WRITE_GRACE_SECONDS` of this
        segment's own write is dropped: inside that window the hardware is
        still reporting its pre-command state, so applying it would revert the
        paint the user just made.
        """
        reading = readings.get(self._segment_index)
        if reading is None:
            return

        # The `_written_at` truth test matters: monotonic() counts from boot,
        # so on a freshly-booted host 0.0 is inside every window.
        if self._written_at and (time.monotonic() - self._written_at) < READBACK_WRITE_GRACE_SECONDS:
            _LOGGER.debug(
                "Ignoring readback for segment %d of %s — write still in flight",
                self._segment_index,
                self._device_id,
            )
            return

        if not reading.is_on:
            if not self._is_on:
                return
            self._is_on = False
            self.async_write_ha_state()
            return

        # Compared in LEVEL space, not brightness space: the wire quantises 255
        # values into 101, so a readback of our own write comes back snapped to
        # a neighbouring brightness (179 -> 70 % -> 178). Comparing brightness
        # would read that snap as a change and churn the entity on every push.
        if self._is_on and self._rgb_color == reading.rgb and ha_to_percent(self._brightness) == reading.level:
            return

        _LOGGER.debug(
            "Segment %d of %s corrected from readback: on=%s rgb=%s brightness=%s",
            self._segment_index,
            self._device_id,
            reading.is_on,
            reading.rgb,
            reading.brightness,
        )
        self._is_on = True
        self._rgb_color = reading.rgb
        self._brightness = reading.brightness
        self.async_write_ha_state()
