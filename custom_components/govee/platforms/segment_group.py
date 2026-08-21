"""User-defined segment-group light entities for RGBIC devices (fork feature).

One light entity per named group of segments (``Left: 1-5``), painted through
the same masked raw dispatch as :mod:`.segment` — one frame per group per
paint, not one command per member segment. Optimistic + ``RestoreEntity``,
readback-aggregated from member segments, same conventions as
:mod:`.segment`: no coordinator subscription except ``SIGNAL_SEGMENT_READBACK``,
and the same in-flight write grace before a readback is trusted.
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
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.restore_state import RestoreEntity

from ..api.protocol import ha_to_percent  # fork: level-space churn guard
from ..api.raw_router import async_segment_color
from ..api.segment_readback import SegmentReading
from ..child_power import async_ensure_device_powered
from ..const import SIGNAL_SEGMENT_READBACK
from ..coordinator import GoveeCoordinator
from ..entity import GoveeEntity
from ..models import GoveeDevice, RGBColor, SegmentColorCommand
from ..segment_groups import group_suffix

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0

READBACK_WRITE_GRACE_SECONDS: Final = 1.5
"""Same window as :data:`.segment.READBACK_WRITE_GRACE_SECONDS` — a push inside
it still carries the group's pre-command state."""


def async_segment_group_entities(
    coordinator: GoveeCoordinator,
    device: GoveeDevice,
    entry: ConfigEntry,
) -> list[LightEntity]:
    """Build one light entity per group defined for ``device`` in options.

    Args:
        coordinator: Govee data coordinator.
        device: The device the groups belong to.
        entry: The config entry holding ``segment_groups_by_device``.

    Returns:
        One :class:`GoveeSegmentGroupEntity` per configured group, in the
        order they were parsed. Empty when the device has no groups saved
        (mode was switched to ``groups`` before a definition was entered).
    """
    device_groups: dict[str, dict[str, list[int]]] = entry.options.get("segment_groups_by_device", {})
    groups = device_groups.get(device.device_id, {})
    return [
        GoveeSegmentGroupEntity(coordinator, device, group_name, tuple(indices))
        for group_name, indices in groups.items()
    ]


class GoveeSegmentGroupEntity(GoveeEntity, LightEntity, RestoreEntity):
    """A user-defined group of segments, controlled as one light entity.

    API Limitation: Govee API returns empty strings for segment colors.
    We use purely optimistic/local state that persists via RestoreEntity.
    This entity intentionally does NOT subscribe to coordinator updates
    to prevent API responses from overwriting local state.

    Fork: it does subscribe to SIGNAL_SEGMENT_READBACK — see
    :class:`.segment.GoveeSegmentEntity` for why that one channel is allowed
    through. is_on aggregates as "any member segment on"; a disagreeing colour
    is adopted only from the first lit member, and only outside the write
    grace window.
    """

    _attr_supported_color_modes = {ColorMode.RGB}
    _attr_color_mode = ColorMode.RGB

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        device: GoveeDevice,
        group_name: str,
        segment_indices: tuple[int, ...],
    ) -> None:
        """Initialize the segment-group entity.

        Args:
            coordinator: Govee data coordinator.
            device: Device the group's segments belong to.
            group_name: The user-given group name — becomes the entity name
                verbatim (HA prefixes the device name automatically).
            segment_indices: 0-based member segment indices, in group order.
        """
        super().__init__(coordinator, device)
        self._group_name = group_name
        self._segment_indices = segment_indices

        self._attr_unique_id = f"{device.device_id}{group_suffix(group_name)}"
        # Plain name, not a translation key: the group name is arbitrary user
        # text, not something a translations file can template.
        self._attr_name = group_name

        # Optimistic state (API doesn't return per-segment state)
        self._is_on = True
        self._brightness = 255
        self._rgb_color: tuple[int, int, int] = (255, 255, 255)
        # ``time.monotonic()`` of this group's last write, 0.0 for never.
        self._written_at: float = 0.0

    @property
    def available(self) -> bool:
        """Return True if entity is available.

        Groups don't depend on coordinator state updates. Just check the
        coordinator is healthy.
        """
        return self.coordinator.last_update_success

    @property
    def is_on(self) -> bool:
        """Return True if the group is on."""
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
        """Turn the group on with optional parameters."""
        if ATTR_BRIGHTNESS in kwargs:
            self._brightness = kwargs[ATTR_BRIGHTNESS]

        if ATTR_RGB_COLOR in kwargs:
            self._rgb_color = kwargs[ATTR_RGB_COLOR]

        r, g, b = self._rgb_color
        color = RGBColor(r=r, g=g, b=b)

        command = SegmentColorCommand(
            segment_indices=self._segment_indices,
            color=color,
        )

        await async_ensure_device_powered(self.coordinator, self._device_id)

        if not await async_segment_color(
            self, self._rgb_color, self._segment_indices, brightness=kwargs.get(ATTR_BRIGHTNESS)
        ):
            await self.coordinator.async_control_device(
                self._device_id,
                command,
            )

        self._is_on = True
        self._written_at = time.monotonic()  # fork: opens the readback grace
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the group off (set its segments to black).

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
                segment_indices=self._segment_indices,
                color=RGBColor(r=0, g=0, b=0),
            )
            if not await async_segment_color(self, (0, 0, 0), self._segment_indices):
                await self.coordinator.async_control_device(self._device_id, command)
        else:
            _LOGGER.debug(
                "Skipping segment group %r turn_off for %s (power_off_pending=%s, device_already_off=%s)",
                self._group_name,
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

        is_on aggregates as "any member segment on" over the readings present
        for this group's indices (members with no reading yet are ignored,
        not treated as off). A disagreeing colour is adopted only from the
        first lit member, in group order, and only once compared in LEVEL
        space (see :meth:`.segment.GoveeSegmentEntity._handle_segment_readback`
        for why brightness space would churn on every push).

        A reading arriving within :data:`READBACK_WRITE_GRACE_SECONDS` of this
        group's own write is dropped entirely — the hardware is still
        reporting pre-command state for every member inside that window.
        """
        if self._written_at and (time.monotonic() - self._written_at) < READBACK_WRITE_GRACE_SECONDS:
            _LOGGER.debug(
                "Ignoring readback for segment group %r of %s — write still in flight",
                self._group_name,
                self._device_id,
            )
            return

        member_readings = [readings[i] for i in self._segment_indices if i in readings]
        if not member_readings:
            return

        aggregated_on = any(reading.is_on for reading in member_readings)

        if not aggregated_on:
            if not self._is_on:
                return
            self._is_on = False
            self.async_write_ha_state()
            return

        lit = next(reading for reading in member_readings if reading.is_on)
        if self._is_on and self._rgb_color == lit.rgb and ha_to_percent(self._brightness) == lit.level:
            return

        _LOGGER.debug(
            "Segment group %r of %s corrected from readback: on=%s rgb=%s brightness=%s",
            self._group_name,
            self._device_id,
            aggregated_on,
            lit.rgb,
            lit.brightness,
        )
        self._is_on = True
        self._rgb_color = lit.rgb
        self._brightness = lit.brightness
        self.async_write_ha_state()
