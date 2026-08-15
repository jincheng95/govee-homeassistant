"""Per-zone light entities for multi-zone lamps (fork feature, OVERRIDE).

Gated by ``enable_zone_lights``, **default off**. With the option off nothing in
this module runs and the zone on/off switches in ``switch.py`` behave exactly as
they always have.

What this replaces
------------------
The H60B0 uplighter has three physically different zones, exposed today as
three identical on/off switches. They are not remotely symmetric:

* **ripple** (zone 1) — colour + brightness, plus a flow rate with no
  light-entity analogue (it becomes a ``number``).
* **ring** (zone 2) — colour + brightness across 8 addressable segments.
* **downlight** (zone 3) — **white only**: colour temperature + brightness, no
  RGB at all.

So this module does not force one entity class onto all three. Every zone's
supported colour modes, its kelvin range and its very existence are read out of
the profile table in :mod:`..api.protocol.profiles`; there is not one SKU name,
zone byte or kelvin constant in this file.

The design question: what does the whole-device light mean now?
---------------------------------------------------------------
The device already has a whole-device ``light`` entity that the coordinator
refreshes from real cloud state (power, brightness, colour). Three optimistic
zone lights sitting beside it would visibly contradict it, so its meaning has to
be settled rather than left to chance.

**Decision: the whole-device entity becomes a WLED-style master — power and
brightness only.** When zone lights exist, colour, colour temperature and the
effect list are stripped from it and belong to the zones, exactly as WLED puts
colour and effects on segments and keeps the master as a level control.

Why that is the right split on this hardware, not just an analogy:

1. **The protocol says so.** Whole-device power (``33 01 <00|01>``) and
   whole-device brightness (``33 04 <0-100>``) are genuine device-level
   operations. Colour and colour temperature are not: on this lamp they are
   only meaningful *per zone*, which is precisely why a master carrying them
   produces the contradiction — a master colour repaints zones that each claim
   their own colour.
2. **Nothing measured is thrown away.** Power and brightness are the two fields
   the cloud (and upstream's LAN read) actually report, and the master keeps
   both. The capabilities removed are the ones with no per-zone feedback
   anyway.
3. **The master stays the only working power control.** A zone cannot be lit
   while the lamp has no power.

Scope of the demotion, deliberately narrow:

* It happens **only when the zone-lights option is on** and **only for devices
  the profile table actually models with zones**. With the option off, the
  whole-device entity keeps every capability it has today, unchanged.
* The master keeps ``name = None`` — upstream's marker for "this entity *is*
  the device". That marker is load-bearing in the frontend: the device page
  lists nameless entities first and draws a divider between them and the named
  ones, so the master stays visually separated from the zones and everything
  else exactly as the whole-device light was before the demotion. (An earlier
  revision renamed it to "<device> Master", which silently erased that divider
  — a name is not free.) WLED draws the same line: master nameless, segments
  named.
* **The effect list moves off the master.** Upstream already exposes the same
  scenes on a separate ``select`` entity, so no capability is lost — but a
  ``light.turn_on`` call with ``effect:`` aimed at the master stops working and
  must be repointed at that select.

Transport for the master is upstream's own: its LAN control tier already routes
whole-device power and brightness over the local network *with verify-by-read*,
which is strictly better than the unconfirmable raw frames used for zones. No
raw-frame path is added for the master; there is no gap to fill.

The remaining coherence rules, all implemented here:

* **Reported power outranks optimistic zone state.** A zone light reports
  ``off`` whenever the device's reported ``power_state`` is off, whatever the
  zone registry last recorded. This is the direction in which ground truth
  exists, so the contradiction is resolved in its favour and can never be seen.
* **Turning a zone on turns the lamp on.** If the device is reported off, a
  zone ``turn_on`` first sends a whole-device ``PowerCommand`` over the normal
  path — deliberately not a raw LAN power frame, because that path is the one
  the master's own state is derived from, so its state stays correct.
* **The hardware's two-of-three limit is applied below the transport**, in
  :mod:`..zone_state`, so a zone displaced by another — over LAN *or* over the
  cloud — has its entity corrected either way.

What the zone lights still cannot do, and say so honestly: their colour and
brightness attributes are "what this zone was last told", never a readback; and
while the lamp is in music mode a zone colour write is accepted, echoed, and
visibly ignored (see :mod:`..api.lan_raw`).

Transport and fallback
----------------------
Zone **power** has a cloud equivalent (the ``…LightToggle`` capability), so it
falls back to the cloud when the LAN path is unavailable.

Zone **colour, brightness, colour temperature and flow rate** have **no cloud
equivalent at zone granularity** — the cloud can only paint the whole lamp.
There is nothing to fall back to, so:

* a colour/brightness/kelvin write with no LAN path raises
  :class:`HomeAssistantError` (an honest, visible failure) rather than silently
  doing nothing or repainting the whole lamp behind the user's back;
* the flow-rate ``number``, whose *only* capability is LAN-only, reports itself
  **unavailable** when the LAN path is not usable.

Fork-maintenance note: this module is additive. ``light.py`` carries one
import line and three call sites (the zone lights, the master demotion, and
the zone-derived segment names); ``number.py`` carries one import and one
line.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Final

# mypy --strict: HA's `light` module re-exports without __all__, so
# `--no-implicit-reexport` raises attr-defined for each member. The
# suppression is upstream-stub-bound, not a real type error here.
from homeassistant.components.light import (  # type: ignore[attr-defined]
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.restore_state import RestoreEntity

from ..api import lan_raw
from ..child_power import async_ensure_device_powered
from ..api.protocol import (
    Capability,
    DeviceProfile,
    GoveeCodec,
    GoveeProtocolError,
    ZoneSpec,
    ha_to_percent,
)
from ..const import SUFFIX_ZONE, SUFFIX_ZONE_FLOW_RATE
from ..entity import GoveeEntity
from ..models import GoveeDevice, ToggleCommand
from ..zone_state import (
    ZONE_KEY_BY_TOGGLE,
    modelled_zone_keys,
    profile_for,
    registry,
    zone_lights_enabled,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from ..coordinator import GoveeCoordinator

_LOGGER = logging.getLogger(__name__)

HA_BRIGHTNESS_MAX: Final = 255

ATTR_ZONE_ON: Final = "zone_on"
"""Unmasked zone state, published so ``RestoreEntity`` can round-trip it."""

# Reverse of the cloud-instance -> zone-key join, for the cloud power fallback.
TOGGLE_BY_ZONE_KEY: Final[dict[str, str]] = {zone: toggle for toggle, zone in ZONE_KEY_BY_TOGGLE.items()}


# ==========================================================================
# Platform wiring
# ==========================================================================


def zone_specs(device: GoveeDevice) -> tuple[tuple[DeviceProfile, ZoneSpec], ...]:
    """The profile zones of ``device`` that deserve an entity, in table order."""
    profile = profile_for(device.sku)
    if profile is None:
        return ()
    return tuple((profile, profile.zone(key)) for key in modelled_zone_keys(device.sku))


def as_master_light(entity: LightEntity, entry: ConfigEntry) -> LightEntity:
    """Demote a whole-device light to a WLED-style master, in place.

    A no-op unless the zone-lights option is on *and* the device has zones in
    the profile table — a lamp with no zone entities has nothing to hand its
    colour to, so it keeps everything.

    Returns the same entity, so the caller stays a one-liner.
    """
    if not zone_lights_enabled(entry):
        return entity
    device = getattr(entity, "_device", None)
    if device is None or not zone_specs(device):
        return entity

    # Level control only: power plus brightness where the device has it.
    supports_brightness = bool(getattr(device, "supports_brightness", False))
    entity._attr_supported_color_modes = {ColorMode.BRIGHTNESS if supports_brightness else ColorMode.ONOFF}
    # Colour and colour temperature are per-zone on this hardware, and the
    # effect list follows them off the master — the scenes stay reachable on
    # upstream's separate scene `select` entity.
    entity._attr_supported_features = LightEntityFeature(0)
    entity._enable_scenes = False
    # Deliberately NOT renamed: `name = None` is what keeps the master above
    # the device page's divider (see the module docstring).
    _LOGGER.debug("Govee zone lights: %s demoted to a master (power + brightness)", getattr(device, "name", "?"))
    return entity


def async_zone_light_entities(coordinator: GoveeCoordinator, entry: ConfigEntry) -> list[LightEntity]:
    """Build every zone light for this entry (empty when the option is off)."""
    if not zone_lights_enabled(entry):
        return []
    entities: list[LightEntity] = []
    for device in coordinator.devices.values():
        if device.is_group:
            continue
        for profile, zone in zone_specs(device):
            entities.append(GoveeZoneLightEntity(coordinator, device, profile, zone))
    if entities:
        _LOGGER.debug("Created %d Govee zone light entities", len(entities))
    return entities


def async_zone_number_entities(coordinator: GoveeCoordinator, entry: ConfigEntry) -> list[NumberEntity]:
    """Build the flow-rate numbers for zones that declare a flow rate."""
    if not zone_lights_enabled(entry):
        return []
    entities: list[NumberEntity] = []
    for device in coordinator.devices.values():
        if device.is_group:
            continue
        for profile, zone in zone_specs(device):
            if Capability.ZONE_FLOW_RATE in zone.capabilities:
                entities.append(GoveeZoneFlowRateNumber(coordinator, device, profile, zone))
    if entities:
        _LOGGER.debug("Created %d Govee zone flow-rate entities", len(entities))
    return entities


def zone_display_name(zone: ZoneSpec) -> str:
    """Human name for a zone, derived from its profile key ("ripple" -> "Ripple")."""
    return zone.key.replace("_", " ").title()


def _segmented_zone(device: GoveeDevice) -> ZoneSpec | None:
    """The one segmented zone of a *multi-zone* device, or None.

    None on purpose in two honest cases: a device the table models with a
    single zone (there "Segment N" is already unambiguous, a prefix would be
    noise), and a device where more than one modelled zone declares segments
    (the cloud capability carries no zone linkage, so the join would be a
    guess).
    """
    keys = modelled_zone_keys(device.sku)
    if len(keys) < 2:
        return None
    profile = profile_for(device.sku)
    if profile is None:  # pragma: no cover - modelled_zone_keys implies a profile
        return None
    segmented = [profile.zone(key) for key in keys if profile.zone(key).segmented]
    return segmented[0] if len(segmented) == 1 else None


def as_zone_named_segment(entity: LightEntity, entry: ConfigEntry) -> LightEntity:
    """Name a segment entity after the zone its segments live on, in place.

    Upstream names RGBIC segments "Segment N", which is fine when the segments
    span the whole device — but on a multi-zone lamp it hides which light they
    address. On the H60B0 every addressable segment is on the ring, so
    "Segment 3" becomes "Ring segment 3" and the grouped entity "Ring
    segments".

    The label comes from :func:`zone_display_name`, the same function the zone
    light and flow-rate entities use, so a segment is always named after the
    entity it belongs to. An earlier revision derived it from ``zone.name``
    (the vendor app's word for the part, "SIDE") and produced "Side light
    segment 3" sitting under a zone light called "Ring" — two names for one
    piece of hardware. One source, one name.

    The zone membership cannot be discovered at runtime: the cloud's
    ``segmentedColorRgb`` capability is a bare index range with no zone
    linkage. The join lives in the profile table — the same per-SKU source the
    zone entities are already built from — via :func:`_segmented_zone`.

    Gated exactly like every other rename in this module: a no-op unless the
    zone-lights option is on, so with the option off upstream's names are
    untouched. Returns the same entity, so the caller stays a one-liner.
    """
    if not zone_lights_enabled(entry):
        return entity
    device = getattr(entity, "_device", None)
    if device is None:
        return entity
    zone = _segmented_zone(device)
    if zone is None:
        return entity
    label = zone_display_name(zone)
    index = getattr(entity, "_segment_index", None)
    entity._attr_name = f"{label} segments" if index is None else f"{label} segment {index + 1}"
    return entity


# ==========================================================================
# Entities
# ==========================================================================


class _GoveeZoneEntityBase(GoveeEntity):
    """Shared plumbing for the per-zone entities of one device."""

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        device: GoveeDevice,
        profile: DeviceProfile,
        zone: ZoneSpec,
    ) -> None:
        """Bind the entity to one profile zone."""
        super().__init__(coordinator, device)
        self._profile = profile
        self._zone = zone
        self._zone_key = zone.key

    @property
    def _codec(self) -> GoveeCodec:
        return GoveeCodec(self._profile)

    def _lan_target(self) -> tuple[str, DeviceProfile] | None:
        """``(ip, profile)`` for a raw write, or None when LAN is not usable.

        ``require_option=False``: raw LAN is the only pipe a zone write has, so
        it follows the zone-lights option that created this entity rather than
        the transport option, which buys a fast path for writes the cloud can
        also make.
        """
        return lan_raw.lan_target(self.coordinator, self._device_id, self._device.sku, require_option=False)

    async def _async_send(self, frames: list[bytes], *, what: str) -> bool:
        """Send frames to this device's LAN address. False when not sent."""
        target = self._lan_target()
        if target is None:
            return False
        ip, _profile = target
        return await lan_raw.async_send_frames(self.coordinator, self._device_id, ip, frames, what=what)


class GoveeZoneLightEntity(_GoveeZoneEntityBase, LightEntity, RestoreEntity):
    """One zone of a multi-zone lamp, with that zone's real capabilities.

    Optimistic by necessity — see the module docstring. ``RestoreEntity`` is
    what makes the optimism survive a restart; without it every zone would come
    back reading "off" while the lamp carried on as it was.
    """

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        device: GoveeDevice,
        profile: DeviceProfile,
        zone: ZoneSpec,
    ) -> None:
        """Initialize a zone light from its profile entry."""
        super().__init__(coordinator, device, profile, zone)
        self._attr_unique_id = f"{device.device_id}{SUFFIX_ZONE}{zone.key}"
        self._attr_name = zone_display_name(zone)
        self._attr_supported_color_modes = _color_modes(zone)
        self._attr_icon = "mdi:lightbulb-multiple"

        # Optimistic attributes: "what this zone was last told", never a read.
        # None until something IS told, so a zone that has never been commanded
        # reports nothing rather than inventing full brightness.
        self._brightness: int | None = None
        self._rgb_color: tuple[int, int, int] | None = None
        self._color_temp_kelvin: int | None = None
        self._unsub_zone: Any = None

    # -- capability surface, straight from the table ------------------------

    @property
    def min_color_temp_kelvin(self) -> int:
        """Lower end of the SKU's usable colour-temperature range."""
        return self._profile.kelvin.minimum

    @property
    def max_color_temp_kelvin(self) -> int:
        """Upper end of the SKU's usable range.

        Read from the profile, never a constant: the H60B0 drops the zone above
        ~6500 K while the H6046 genuinely reaches 9000 K.
        """
        return self._profile.kelvin.maximum

    @property
    def color_mode(self) -> ColorMode:
        """Current colour mode, always one of ``supported_color_modes``."""
        modes = self.supported_color_modes or {ColorMode.ONOFF}
        if self._color_temp_kelvin is not None and ColorMode.COLOR_TEMP in modes:
            return ColorMode.COLOR_TEMP
        if self._rgb_color is not None and ColorMode.RGB in modes:
            return ColorMode.RGB
        for candidate in (ColorMode.RGB, ColorMode.COLOR_TEMP, ColorMode.BRIGHTNESS):
            if candidate in modes:
                return candidate
        return ColorMode(next(iter(modes)))

    # -- state --------------------------------------------------------------

    @property
    def is_on(self) -> bool:
        """Whether this zone is lit.

        Reported whole-device power outranks the optimistic zone state: with the
        lamp off, no zone can be lit whatever we last commanded. See the
        module docstring.
        """
        state = self.device_state
        if state is not None and state.power_state is False:
            return False
        return registry(self.coordinator).is_on(self._device_id, self._zone_key)

    @property
    def brightness(self) -> int | None:
        """Last brightness commanded to this zone (0-255), or None.

        Gated on the zone's *capability*, not on its colour mode: a zone can
        have colour without a brightness channel, and reporting a level it
        cannot set would be the same lie in a less obvious place.
        """
        if Capability.ZONE_BRIGHTNESS not in self._zone.capabilities:
            return None
        return self._brightness

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        """Last RGB colour commanded to this zone."""
        return self._rgb_color

    @property
    def color_temp_kelvin(self) -> int | None:
        """Last colour temperature commanded to this zone."""
        return self._color_temp_kelvin

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Zone provenance, and an explicit "this is not a readback" marker."""
        attrs = dict(super().extra_state_attributes)
        attrs["zone"] = self._zone_key
        attrs["zone_segments"] = self._zone.segments
        attrs["optimistic"] = True
        attrs["lan_transport"] = self._lan_target() is not None
        # The zone's own state, NOT masked by whole-device power. `is_on` is
        # masked (reported power outranks optimism), so the entity's state
        # cannot be used to restore the zone: a restart while the lamp happened
        # to be off would read every zone as off and erase the picture. This is
        # the value `async_added_to_hass` restores from.
        attrs[ATTR_ZONE_ON] = registry(self.coordinator).is_on(self._device_id, self._zone_key)
        return attrs

    # -- commands -----------------------------------------------------------

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Light the zone, applying any attributes the zone actually supports."""
        wants_attributes = any(k in kwargs for k in (ATTR_BRIGHTNESS, ATTR_RGB_COLOR, ATTR_COLOR_TEMP_KELVIN))
        target = self._lan_target()

        if wants_attributes and target is None:
            raise HomeAssistantError(
                f"{self._device.name}: the {zone_display_name(self._zone)} zone's colour and brightness "
                "can only be set over the local network, and this device is not currently reachable there "
                "(check the 'Control light zones over the local network' option and the LAN UDP connectivity sensor)"
            )

        # Everything that can fail without touching the lamp is resolved BEFORE
        # the power command goes out. Powering the whole lamp on and then
        # raising would leave the user with a lit lamp, an unlit zone and an
        # error — a worse state than the one they started in.
        if target is None:
            self._require_cloud_toggle_instance()
        else:
            frames = self._build_frames_or_raise(on=True, **kwargs)

        # A zone cannot be lit while the lamp has no power, and only the
        # whole-device command has real state behind it — so power goes out on
        # the normal path first.
        await self._async_ensure_device_powered()

        # That await yields, and the LAN correlation can expire across it. The
        # target is re-read rather than trusted, so the branch taken below and
        # the target `_async_send` resolves for itself are the same one.
        if target is not None and self._lan_target() is None:
            target = None

        if target is None:
            await self._async_cloud_power_after_power(True)
            return

        if not await self._async_send(frames, what=f"zone {self._zone_key} on"):
            # Past the power command the lamp is lit. Raising here would leave
            # the user a lit lamp, an unlit zone and an error — strictly worse
            # than switching the zone on over the cloud and saying so. The
            # attributes are simply not applied; that is the honest degradation.
            _LOGGER.warning(
                "Govee zone lights: the local-network write to %s's %s zone was not sent — "
                "falling back to cloud power%s",
                self._device.name,
                zone_display_name(self._zone),
                " (colour and brightness not applied)" if wants_attributes else "",
            )
            await self._async_cloud_power_after_power(True)
            return

        self._apply_optimistic(True, **kwargs)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Switch the zone off (LAN when available, cloud toggle otherwise)."""
        if self._lan_target() is None:
            await self._async_cloud_power(False)
            return
        frame = self._build_frames_or_raise(on=False)[0]
        if not await self._async_send([frame], what=f"zone {self._zone_key} off"):
            await self._async_cloud_power(False)
            return
        self._apply_optimistic(False)

    def _build_frames_or_raise(self, *, on: bool, **kwargs: Any) -> list[bytes]:
        """:meth:`_build_frames`, with codec refusals turned into HA errors.

        The codec raises rather than guess a byte it does not know, and those
        exceptions are not :class:`HomeAssistantError` subclasses — unwrapped,
        they surface as a traceback in the log instead of a message on the
        service call. Every codec call site goes through here.

        Args:
            on: Target state for the zone.
            **kwargs: The ``turn_on`` attributes to encode alongside it.

        Returns:
            The frames to send, power first.

        Raises:
            HomeAssistantError: If any frame could not be built.
        """
        try:
            return self._build_frames(on=on, **kwargs)
        except GoveeProtocolError as err:
            raise HomeAssistantError(
                f"{self._device.name}: cannot build a local-network frame for the "
                f"{zone_display_name(self._zone)} zone ({err})"
            ) from err

    def _build_frames(self, *, on: bool, **kwargs: Any) -> list[bytes]:
        """Frames for one turn_on: power first, then the attributes.

        Power leads because an unlit zone has never been observed to accept an
        attribute frame; the opposite order would be a guess. All the frames
        ride in one envelope, so the device applies them back to back.
        """
        codec = self._codec
        frames = [codec.zone_power(self._zone_key, on)]
        supported = self._zone.capabilities

        if ATTR_RGB_COLOR in kwargs and Capability.ZONE_COLOR in supported:
            frames.append(codec.zone_color(self._zone_key, tuple(kwargs[ATTR_RGB_COLOR])))

        if ATTR_COLOR_TEMP_KELVIN in kwargs and Capability.ZONE_COLOR_TEMP in supported:
            # Clamped through the profile — kelvin ceilings are per-SKU.
            frames.append(codec.color_temp(self._zone_key, int(kwargs[ATTR_COLOR_TEMP_KELVIN])))

        if ATTR_BRIGHTNESS in kwargs and Capability.ZONE_BRIGHTNESS in supported:
            frames.append(codec.zone_brightness(self._zone_key, ha_to_percent(kwargs[ATTR_BRIGHTNESS])))

        return frames

    def _apply_optimistic(self, on: bool, **kwargs: Any) -> None:
        """Record what we just commanded, and let the registry displace siblings."""
        supported = self._zone.capabilities
        if ATTR_BRIGHTNESS in kwargs and Capability.ZONE_BRIGHTNESS in supported:
            self._brightness = int(kwargs[ATTR_BRIGHTNESS])
        if ATTR_RGB_COLOR in kwargs and Capability.ZONE_COLOR in supported:
            self._rgb_color = tuple(kwargs[ATTR_RGB_COLOR])
            self._color_temp_kelvin = None
        if ATTR_COLOR_TEMP_KELVIN in kwargs and Capability.ZONE_COLOR_TEMP in supported:
            self._color_temp_kelvin = self._profile.kelvin.clamp(int(kwargs[ATTR_COLOR_TEMP_KELVIN]))
            self._rgb_color = None
        registry(self.coordinator).apply(self._device_id, self._device.sku, self._zone_key, on)
        self.async_write_ha_state()

    async def _async_ensure_device_powered(self) -> None:
        """Power the lamp on over the normal path if it is reported off.

        The rule itself lives in :mod:`..child_power`, which the segment
        entities call too — a child turning on marks its parent on, whatever
        kind of child it is.
        """
        await async_ensure_device_powered(self.coordinator, self._device_id)

    def _require_cloud_toggle_instance(self) -> str:
        """The cloud toggle backing this zone, or raise before anything is sent.

        Returns:
            The cloud capability instance name for this zone.

        Raises:
            HomeAssistantError: If this zone has no cloud equivalent.
        """
        instance = TOGGLE_BY_ZONE_KEY.get(self._zone_key)
        if instance is None or instance not in self._device.named_light_toggle_instances:
            raise HomeAssistantError(
                f"{self._device.name}: the {zone_display_name(self._zone)} zone cannot be switched "
                "without the local network, and this device is not currently reachable there"
            )
        return instance

    async def _async_cloud_power(self, on: bool) -> None:
        """Zone on/off over the cloud — the one zone capability that has an equivalent."""
        instance = self._require_cloud_toggle_instance()
        success = await self.coordinator.async_control_device(
            self._device_id, ToggleCommand(toggle_instance=instance, enabled=on)
        )
        if success:
            registry(self.coordinator).apply(self._device_id, self._device.sku, self._zone_key, on)
        self.async_write_ha_state()

    async def _async_cloud_power_after_power(self, on: bool) -> None:
        """:meth:`_async_cloud_power`, but never raising.

        Only for fallbacks taken *after* the whole-lamp power command has gone
        out. A zone with no cloud equivalent is a real failure, but by this
        point raising it produces a lit lamp, an unlit zone and an error — so
        it is logged instead.
        """
        try:
            await self._async_cloud_power(on)
        except HomeAssistantError as err:
            _LOGGER.warning(
                "Govee zone lights: %s's %s zone could not be switched over the cloud either (%s)",
                self._device.name,
                zone_display_name(self._zone),
                err,
            )

    # -- lifecycle ----------------------------------------------------------

    async def async_added_to_hass(self) -> None:
        """Restore the optimistic picture and subscribe to the zone registry."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        restored_on = False
        if last_state is not None:
            # Prefer the unmasked zone attribute; fall back to the entity state
            # only for entries written before that attribute existed.
            zone_on = last_state.attributes.get(ATTR_ZONE_ON)
            restored_on = bool(zone_on) if zone_on is not None else last_state.state == "on"
            # `is not None`, never truthiness: brightness 0 and rgb (0, 0, 0)
            # are states HA writes, and both are falsy.
            if last_state.attributes.get("brightness") is not None:
                self._brightness = int(last_state.attributes["brightness"])
            if last_state.attributes.get("rgb_color") is not None:
                self._rgb_color = tuple(last_state.attributes["rgb_color"])
            if last_state.attributes.get("color_temp_kelvin") is not None:
                self._color_temp_kelvin = int(last_state.attributes["color_temp_kelvin"])

        store = registry(self.coordinator)
        store.seed(self._device_id, self._zone_key, restored_on)
        self._unsub_zone = store.register(self._device_id, self._zone_key, self._on_zone_change)
        self.async_on_remove(self._unsub_zone)

    def _on_zone_change(self, on: bool) -> None:
        """Registry callback — another entity's command displaced this zone."""
        del on  # is_on reads the registry directly; this is purely a repaint.
        self.async_write_ha_state()


class GoveeZoneFlowRateNumber(_GoveeZoneEntityBase, NumberEntity, RestoreEntity):
    """Flow rate of an animated zone, 0-100 %.

    A rate has no light-entity analogue, so it is a ``number``. It is also
    LAN-only — the cloud API cannot express it at all — which is why this entity
    reports itself unavailable rather than pretending, when the raw LAN path is
    not usable for the device.
    """

    _attr_icon = "mdi:speedometer"
    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = 0.0
    _attr_native_max_value = 100.0
    _attr_native_step = 1.0
    _attr_native_unit_of_measurement = "%"

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        device: GoveeDevice,
        profile: DeviceProfile,
        zone: ZoneSpec,
    ) -> None:
        """Initialize the flow-rate number for one zone."""
        super().__init__(coordinator, device, profile, zone)
        self._attr_unique_id = f"{device.device_id}{SUFFIX_ZONE_FLOW_RATE}{zone.key}"
        self._attr_name = f"{zone_display_name(zone)} flow rate"
        self._value: float | None = None

    @property
    def available(self) -> bool:
        """Unavailable without the LAN path — there is no cloud equivalent."""
        return super().available and self._lan_target() is not None

    @property
    def native_value(self) -> float | None:
        """Last flow rate commanded (never a readback)."""
        return self._value

    async def async_set_native_value(self, value: float) -> None:
        """Send a flow-rate frame for this zone."""
        try:
            frame = self._codec.flow_rate(self._zone_key, int(value))
        except GoveeProtocolError as err:
            raise HomeAssistantError(f"{self._device.name}: cannot encode zone flow rate ({err})") from err
        if not await self._async_send([frame], what=f"zone {self._zone_key} flow rate {int(value)}"):
            raise HomeAssistantError(
                f"{self._device.name}: the local-network flow-rate write could not be sent "
                "(this control has no cloud equivalent)"
            )
        self._value = float(int(value))
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Restore the last commanded rate."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state not in (None, "", "unknown", "unavailable"):
            try:
                self._value = float(last_state.state)
            except ValueError:  # pragma: no cover - malformed restore
                self._value = None


# ==========================================================================
# Helpers
# ==========================================================================


def _color_modes(zone: ZoneSpec) -> set[ColorMode]:
    """Colour modes a zone actually has, derived from its profile capabilities.

    The three H60B0 zones land on three different answers, which is the whole
    point: RGB for ripple and ring, COLOR_TEMP for the white-only downlight.
    """
    modes: set[ColorMode] = set()
    if Capability.ZONE_COLOR in zone.capabilities:
        modes.add(ColorMode.RGB)
    if Capability.ZONE_COLOR_TEMP in zone.capabilities:
        modes.add(ColorMode.COLOR_TEMP)
    if not modes and Capability.ZONE_BRIGHTNESS in zone.capabilities:
        modes.add(ColorMode.BRIGHTNESS)
    if not modes:
        modes.add(ColorMode.ONOFF)
    return modes
