"""DIY-effect authoring entities for multi-zone lamps (fork feature).

Gated by the same pair of options as the zone lights (``enable_zone_lights``
*and* ``enable_lan_raw_write``, both default off) and built only for SKUs whose
profile declares a DIY layout — today that is the H60B0 alone.

The shape of the control surface
--------------------------------
A DIY effect is uploaded as one indivisible document (see :mod:`..diy_state`
for why), so these entities do not command anything. They *stage*:

======================  ==========================================
``select``  DIY mode    the zone's effect, by the name the app uses
``number``  DIY speed   transition speed, 1-100
``select``  direction   ripple only — cw / ccw / reverse
``number``  flow rate   ripple only, and NOT the live flow-rate number
``text``    palette     up to 16 ``#rrggbb`` colours
``button``  apply       one per device: assemble, encode, upload
======================  ==========================================

Every staging entity is :class:`~homeassistant.const.EntityCategory.CONFIG`
and writes nothing to the device: setting one touches the store and the state
machine only. That is what makes a half-built effect harmless — "twinkle" with
no colours yet is a legal *draft*, and only the button turns a draft into
frames (and refuses, with a readable message, when the draft is not sendable).

Two consequences worth stating, because both were deliberate:

* **Only the button's availability depends on the LAN.** The staging entities
  stay usable while the lamp is off the network; there is nothing for them to
  fail to send. The button is the one control that needs a route, so it is the
  one that goes unavailable without it.
* **The DIY flow rate is a separate entity from the live zone flow rate** in
  :mod:`.zone_light`. They carry the same units and the same range but they are
  not the same value: one is a property of the running lamp, the other a field
  of a document that has not been sent yet. Merging them would make editing a
  draft change the lamp.

Nothing here names a zone, a mode or a direction. The zones, their mode tables
(the ripple's and the ring's do not share an enum), and whether a zone even has
a direction or flow-rate field all come out of
:class:`~..api.protocol.profiles.DiyEffectSpec`.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any, Final

from homeassistant.components.button import ButtonEntity
from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.components.select import SelectEntity
from homeassistant.components.sensor import SensorEntity
from homeassistant.components.text import TextEntity, TextMode
from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.restore_state import RestoreEntity

from ..api import lan_raw
from ..api.protocol import (
    DIRECTIONS,
    MAX_DIY_COLORS,
    MODE_NONE,
    DeviceProfile,
    DiyZoneSpec,
    GoveeProtocolError,
    resolve_mode,
)
from ..const import (
    SUFFIX_DIY_APPLY,
    SUFFIX_DIY_DIRECTION,
    SUFFIX_DIY_FLOW_RATE,
    SUFFIX_DIY_MODE,
    SUFFIX_DIY_PALETTE,
    SUFFIX_DIY_PREVIEW,
    SUFFIX_DIY_SPEED,
)
from ..diy_previews import preview_map, preview_url  # fork: shipped mode artwork
from ..diy_state import async_send_diy_effect, diy_spec_for, store, zone_lights_enabled
from ..entity import GoveeEntity
from ..models import GoveeDevice

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from ..coordinator import GoveeCoordinator

_LOGGER = logging.getLogger(__name__)

HEX_COLOR: Final = r"#?[0-9a-fA-F]{6}"
PALETTE_PATTERN: Final = (
    rf"^{HEX_COLOR}(?:[\s,]+{HEX_COLOR}){{0,{MAX_DIY_COLORS - 1}}}$"
)
"""One to :data:`MAX_DIY_COLORS` hex colours, separated by commas or spaces.

Published as the ``text`` entity's ``pattern`` so the UI rejects a bad palette
before the service call, and reused verbatim when parsing — one definition of
what a palette is, not two that can drift.
"""

_PALETTE_SEPARATORS: Final = re.compile(r"[\s,]+")

# Display labels for the direction codes, derived from the protocol mapping so
# a new direction in the table appears here without an edit.
DIRECTION_LABELS: Final[dict[str, str]] = {
    name: name.upper() if len(name) <= 3 else name.title() for name in DIRECTIONS
}
DIRECTION_BY_LABEL: Final[dict[str, int]] = {
    label: DIRECTIONS[name] for name, label in DIRECTION_LABELS.items()
}
LABEL_BY_DIRECTION: Final[dict[int, str]] = {
    code: label for label, code in DIRECTION_BY_LABEL.items()
}


# ==========================================================================
# Platform wiring
# ==========================================================================


def diy_zones(device: GoveeDevice) -> tuple[tuple[DeviceProfile, DiyZoneSpec], ...]:
    """The DIY zones of ``device``, in wire order (empty when it has none)."""
    resolved = diy_spec_for(device.sku)
    if resolved is None:
        return ()
    profile, diy = resolved
    return tuple((profile, zone) for zone in diy.zones)


def _diy_devices(
    coordinator: GoveeCoordinator, entry: ConfigEntry
) -> list[GoveeDevice]:
    """Devices of this entry that can take a DIY effect ([] when gated off)."""
    if not zone_lights_enabled(entry):
        return []
    return [
        device
        for device in coordinator.devices.values()
        if not device.is_group and diy_spec_for(device.sku) is not None
    ]


def async_diy_select_entities(
    coordinator: GoveeCoordinator, entry: ConfigEntry
) -> list[SelectEntity]:
    """Build the DIY mode (and, where declared, direction) selects."""
    entities: list[SelectEntity] = []
    for device in _diy_devices(coordinator, entry):
        for profile, zone in diy_zones(device):
            entities.append(GoveeDiyModeSelect(coordinator, device, profile, zone))
            if zone.has_direction:
                entities.append(
                    GoveeDiyDirectionSelect(coordinator, device, profile, zone)
                )
    if entities:
        _LOGGER.debug("Created %d Govee DIY select entities", len(entities))
    return entities


def async_diy_number_entities(
    coordinator: GoveeCoordinator, entry: ConfigEntry
) -> list[NumberEntity]:
    """Build the DIY speed (and, where declared, flow-rate) numbers."""
    entities: list[NumberEntity] = []
    for device in _diy_devices(coordinator, entry):
        for profile, zone in diy_zones(device):
            entities.append(GoveeDiySpeedNumber(coordinator, device, profile, zone))
            if zone.has_flow_rate:
                entities.append(
                    GoveeDiyFlowRateNumber(coordinator, device, profile, zone)
                )
    if entities:
        _LOGGER.debug("Created %d Govee DIY number entities", len(entities))
    return entities


def async_diy_text_entities(
    coordinator: GoveeCoordinator, entry: ConfigEntry
) -> list[TextEntity]:
    """Build the per-zone palette text entities."""
    entities: list[TextEntity] = []
    for device in _diy_devices(coordinator, entry):
        for profile, zone in diy_zones(device):
            entities.append(GoveeDiyPaletteText(coordinator, device, profile, zone))
    if entities:
        _LOGGER.debug("Created %d Govee DIY palette entities", len(entities))
    return entities


def async_diy_button_entities(
    coordinator: GoveeCoordinator, entry: ConfigEntry
) -> list[ButtonEntity]:
    """Build the apply button — one per device, not one per zone.

    The upload is a whole-device document: both zone records travel in the same
    payload, so a per-zone button would either send a half-empty effect or lie
    about what it did.
    """
    entities: list[ButtonEntity] = []
    for device in _diy_devices(coordinator, entry):
        resolved = diy_spec_for(device.sku)
        if resolved is None:  # pragma: no cover - _diy_devices already filtered
            continue
        entities.append(GoveeDiyApplyButton(coordinator, device, resolved[0]))
    if entities:
        _LOGGER.debug("Created %d Govee DIY apply buttons", len(entities))
    return entities


def async_diy_preview_entities(
    coordinator: GoveeCoordinator, entry: ConfigEntry
) -> list[SensorEntity]:
    """Build the per-zone DIY mode preview sensors.

    One per DIY zone the profile declares, driven entirely by the table — the
    H60B0's ripple and ring today, and any zone a future profile adds, with no
    SKU named here.

    Args:
        coordinator: The coordinator owning the devices.
        entry: The config entry (carries the gate the DIY controls share).

    Returns:
        The sensors to add, empty when DIY authoring is gated off.
    """
    entities: list[SensorEntity] = []
    for device in _diy_devices(coordinator, entry):
        for profile, zone in diy_zones(device):
            entities.append(GoveeDiyModePreviewSensor(coordinator, device, profile, zone))
    if entities:
        _LOGGER.debug("Created %d Govee DIY preview sensors", len(entities))
    return entities


def diy_zone_display_name(zone: DiyZoneSpec) -> str:
    """Human name for a DIY zone, from its profile key ("ripple" -> "Ripple")."""
    return zone.zone_key.replace("_", " ").title()


def mode_label(name: str) -> str:
    """Display label for a mode name from the profile table ("none" -> "None")."""
    return name.replace("_", " ").title()


# ==========================================================================
# Entities
# ==========================================================================


class _GoveeDiyZoneEntityBase(GoveeEntity, RestoreEntity):
    """Shared plumbing for the staging entities of one DIY zone.

    Deliberately does *not* touch the device: every subclass writes the store
    and its own state and stops there. See the module docstring.
    """

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        device: GoveeDevice,
        profile: DeviceProfile,
        zone: DiyZoneSpec,
    ) -> None:
        """Bind the entity to one DIY zone of one device."""
        super().__init__(coordinator, device)
        self._profile = profile
        self._zone = zone
        self._zone_key = zone.zone_key
        self._zone_name = diy_zone_display_name(zone)

    @property
    def _store(self) -> Any:
        """The coordinator's staged-DIY store."""
        return store(self.coordinator)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Zone provenance, plus a marker that nothing here has been sent yet."""
        attrs = dict(super().extra_state_attributes)
        attrs["diy_zone"] = self._zone_key
        attrs["staged"] = True
        return attrs

    async def async_added_to_hass(self) -> None:
        """Seed the store from the restored state and subscribe to the zone."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state not in (
            None,
            "",
            "unknown",
            "unavailable",
        ):
            self._restore(last_state.state)
        self.async_on_remove(
            self._store.register(self._device_id, self._zone_key, self._on_store_change)
        )

    def _restore(self, state: str) -> None:
        """Seed this entity's staged field from ``state`` (no notification)."""
        raise NotImplementedError

    def _on_store_change(self) -> None:
        """Store callback — the service (or a sibling) rewrote this record."""
        self.async_write_ha_state()


class GoveeDiyModeSelect(_GoveeDiyZoneEntityBase, SelectEntity):
    """Which DIY effect this zone plays, by the name the vendor app uses.

    The options come from the zone's own bound table: the ripple and the ring
    do not share an enum (twinkle is 11 on one and 3 on the other), so this is
    two different pickers built from one class.
    """

    _attr_icon = "mdi:auto-fix"

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        device: GoveeDevice,
        profile: DeviceProfile,
        zone: DiyZoneSpec,
    ) -> None:
        """Initialize the mode picker for one DIY zone."""
        super().__init__(coordinator, device, profile, zone)
        self._attr_unique_id = f"{device.device_id}{SUFFIX_DIY_MODE}{zone.zone_key}"
        self._attr_name = f"{self._zone_name} DIY mode"
        # Table order, not alphabetical: it is the app's display order, and the
        # "None" entry is first there for the same reason it is here.
        self._attr_options = [mode_label(name) for name in zone.modes]
        self._label_by_mode = {
            code: mode_label(name) for name, code in zone.modes.items()
        }
        # Labels round-trip to the *table's* spelling, not to a lowercased
        # label: a two-word mode key would come back with a space where the
        # table has an underscore, and resolve_mode would refuse it.
        self._name_by_label = {mode_label(name): name for name in zone.modes}

    @property
    def current_option(self) -> str | None:
        """The staged mode's label, or None for a raw int with no name.

        Raw ints are legal (the ripple accepts ring-enum values the table does
        not bind), and reporting None is the honest answer for one — inventing
        a label would put a value in the dropdown that the user cannot pick.
        """
        return self._label_by_mode.get(
            self._store.mode(self._device_id, self._zone_key)
        )

    async def async_select_option(self, option: str) -> None:
        """Stage a mode. Nothing is sent — the apply button does that."""
        try:
            mode = resolve_mode(self._zone, self._name_by_label.get(option, option))
        except (
            GoveeProtocolError
        ) as err:  # pragma: no cover - HA validates the option first
            raise HomeAssistantError(
                f"{self._device.name}: unknown DIY mode {option!r} ({err})"
            ) from err
        self._store.set_mode(self._device_id, self._zone_key, mode)
        self.async_write_ha_state()

    def _restore(self, state: str) -> None:
        for code, label in self._label_by_mode.items():
            if label == state:
                self._store.seed(self._device_id, self._zone_key, mode=code)
                return


class GoveeDiyDirectionSelect(_GoveeDiyZoneEntityBase, SelectEntity):
    """Which way this zone's effect travels (zones that declare a direction).

    ``Reverse`` is a third code, not a toggle: the LAN path is write-only, so
    the device cannot be asked to invert a state it never reports.
    """

    _attr_icon = "mdi:rotate-right"

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        device: GoveeDevice,
        profile: DeviceProfile,
        zone: DiyZoneSpec,
    ) -> None:
        """Initialize the direction picker for one DIY zone."""
        super().__init__(coordinator, device, profile, zone)
        self._attr_unique_id = (
            f"{device.device_id}{SUFFIX_DIY_DIRECTION}{zone.zone_key}"
        )
        self._attr_name = f"{self._zone_name} DIY direction"
        self._attr_options = list(DIRECTION_BY_LABEL)

    @property
    def current_option(self) -> str | None:
        """The staged direction's label."""
        return LABEL_BY_DIRECTION.get(
            self._store.direction(self._device_id, self._zone_key)
        )

    async def async_select_option(self, option: str) -> None:
        """Stage a direction."""
        code = DIRECTION_BY_LABEL.get(option)
        if code is None:  # pragma: no cover - HA validates the option first
            raise HomeAssistantError(
                f"{self._device.name}: unknown DIY direction {option!r}"
            )
        self._store.set_direction(self._device_id, self._zone_key, code)
        self.async_write_ha_state()

    def _restore(self, state: str) -> None:
        code = DIRECTION_BY_LABEL.get(state)
        if code is not None:
            self._store.seed(self._device_id, self._zone_key, direction=code)


class _GoveeDiyZoneNumber(_GoveeDiyZoneEntityBase, NumberEntity):
    """Shared body for the 1-100 staged numbers (speed, flow rate)."""

    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = 1.0
    _attr_native_max_value = 100.0
    _attr_native_step = 1.0
    _attr_native_unit_of_measurement = "%"

    @property
    def native_value(self) -> float | None:
        """The staged value for this field."""
        return float(self._read())

    async def async_set_native_value(self, value: float) -> None:
        """Stage a value. Nothing is sent — the apply button does that."""
        self._write(int(value))
        self.async_write_ha_state()

    def _read(self) -> int:
        raise NotImplementedError

    def _write(self, value: int) -> None:
        raise NotImplementedError

    def _restore(self, state: str) -> None:
        try:
            value = int(float(state))
        except ValueError:  # pragma: no cover - malformed restore
            return
        if 1 <= value <= 100:
            self._seed(value)

    def _seed(self, value: int) -> None:
        raise NotImplementedError


class GoveeDiySpeedNumber(_GoveeDiyZoneNumber):
    """How fast this zone's staged effect transitions, 1-100."""

    _attr_icon = "mdi:play-speed"

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        device: GoveeDevice,
        profile: DeviceProfile,
        zone: DiyZoneSpec,
    ) -> None:
        """Initialize the speed slider for one DIY zone."""
        super().__init__(coordinator, device, profile, zone)
        self._attr_unique_id = f"{device.device_id}{SUFFIX_DIY_SPEED}{zone.zone_key}"
        self._attr_name = f"{self._zone_name} DIY speed"

    def _read(self) -> int:
        return self._store.speed(self._device_id, self._zone_key)

    def _write(self, value: int) -> None:
        self._store.set_speed(self._device_id, self._zone_key, value)

    def _seed(self, value: int) -> None:
        self._store.seed(self._device_id, self._zone_key, speed=value)


class GoveeDiyFlowRateNumber(_GoveeDiyZoneNumber):
    """Flow rate carried *inside* the staged DIY record, 1-100.

    Not the same entity as the live zone flow rate in :mod:`.zone_light`, and
    deliberately so — see the module docstring.
    """

    _attr_icon = "mdi:speedometer"

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        device: GoveeDevice,
        profile: DeviceProfile,
        zone: DiyZoneSpec,
    ) -> None:
        """Initialize the DIY flow-rate slider for one DIY zone."""
        super().__init__(coordinator, device, profile, zone)
        self._attr_unique_id = (
            f"{device.device_id}{SUFFIX_DIY_FLOW_RATE}{zone.zone_key}"
        )
        self._attr_name = f"{self._zone_name} DIY flow rate"

    def _read(self) -> int:
        return self._store.flow_rate(self._device_id, self._zone_key)

    def _write(self, value: int) -> None:
        self._store.set_flow_rate(self._device_id, self._zone_key, value)

    def _seed(self, value: int) -> None:
        self._store.seed(self._device_id, self._zone_key, flow_rate=value)


class GoveeDiyPaletteText(_GoveeDiyZoneEntityBase, TextEntity):
    """The colours this zone's staged effect cycles through.

    A ``text`` entity rather than N colour pickers because the palette is an
    ordered list of *variable* length (1 to 16), which no colour-capable entity
    class expresses. The canonical form is lowercase ``#rrggbb`` separated by
    single spaces; input is more forgiving (commas, extra whitespace, missing
    ``#``) and is normalised on the way in, so the state never disagrees with
    the published pattern.
    """

    _attr_icon = "mdi:palette"
    _attr_mode = TextMode.TEXT
    _attr_pattern = PALETTE_PATTERN
    _attr_native_min = 0
    # "#rrggbb" plus a separator, sixteen times, less the trailing separator.
    _attr_native_max = MAX_DIY_COLORS * 8 - 1

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        device: GoveeDevice,
        profile: DeviceProfile,
        zone: DiyZoneSpec,
    ) -> None:
        """Initialize the palette editor for one DIY zone."""
        super().__init__(coordinator, device, profile, zone)
        self._attr_unique_id = f"{device.device_id}{SUFFIX_DIY_PALETTE}{zone.zone_key}"
        self._attr_name = f"{self._zone_name} DIY palette"

    @property
    def native_value(self) -> str | None:
        """The staged palette, or None when the zone has no colours yet.

        None rather than ``""``: an empty string does not match the published
        pattern, and HA raises on a state that contradicts its own capability
        attributes.
        """
        colors = self._store.colors(self._device_id, self._zone_key)
        return format_palette(colors) if colors else None

    async def async_set_value(self, value: str) -> None:
        """Stage a palette, normalising whatever spelling was given."""
        self._store.set_colors(
            self._device_id, self._zone_key, parse_palette(value, self._device.name)
        )
        self.async_write_ha_state()

    def _restore(self, state: str) -> None:
        try:
            colors = parse_palette(state, self._device.name)
        except HomeAssistantError:  # pragma: no cover - malformed restore
            return
        self._store.seed(self._device_id, self._zone_key, colors=colors)


class GoveeDiyApplyButton(GoveeEntity, ButtonEntity):
    """Assemble the staged records for this device and upload them.

    The one entity here that talks to the lamp, and therefore the one whose
    availability depends on the raw-LAN route existing. Every refusal is a
    :class:`HomeAssistantError` with the reason in it: a draft that cannot be
    encoded (no zone switched on, a zone with a mode but no colours) fails
    here, at the moment the user asked for it, rather than silently.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:upload"

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        device: GoveeDevice,
        profile: DeviceProfile,
    ) -> None:
        """Initialize the apply button for one device."""
        super().__init__(coordinator, device)
        self._profile = profile
        self._attr_unique_id = f"{device.device_id}{SUFFIX_DIY_APPLY}"
        self._attr_name = "Apply DIY effect"

    @property
    def available(self) -> bool:
        """Unavailable without the LAN path — DIY has no cloud equivalent."""
        return super().available and self._lan_target() is not None

    def _lan_target(self) -> tuple[str, DeviceProfile] | None:
        return lan_raw.lan_target(
            self.coordinator, self._device_id, self._device.sku
        )

    async def async_press(self) -> None:
        """Upload the staged DIY document."""
        resolved = diy_spec_for(self._device.sku)
        if resolved is None:  # pragma: no cover - the builder filtered on this
            raise HomeAssistantError(f"{self._device.name} has no DIY effect support")
        profile, diy = resolved
        effects = store(self.coordinator).effects(self._device_id, diy)
        await async_send_diy_effect(self.coordinator, self._device, profile, effects)


# ==========================================================================
# Palette parsing
# ==========================================================================


def parse_palette(value: str, device_name: str) -> tuple[tuple[int, int, int], ...]:
    """Turn user palette text into RGB triples, or raise.

    Accepts the colours separated by commas, whitespace or both, with or
    without a leading ``#``.

    Args:
        value: The text the user typed.
        device_name: Named in the error, so the message is actionable.

    Returns:
        The colours as ``(r, g, b)`` triples, in the order given.

    Raises:
        HomeAssistantError: If the text is not 1 to :data:`MAX_DIY_COLORS`
            six-digit hex colours.
    """
    tokens = [token for token in _PALETTE_SEPARATORS.split(value.strip()) if token]
    if not tokens:
        raise HomeAssistantError(
            f"{device_name}: a DIY palette needs at least one colour, e.g. '#ff0000 #00b0ff'"
        )
    if len(tokens) > MAX_DIY_COLORS:
        raise HomeAssistantError(
            f"{device_name}: {len(tokens)} palette colours, the maximum is {MAX_DIY_COLORS}"
        )
    colors: list[tuple[int, int, int]] = []
    for token in tokens:
        if not re.fullmatch(HEX_COLOR, token):
            raise HomeAssistantError(
                f"{device_name}: {token!r} is not a #rrggbb colour (a DIY palette is 1-{MAX_DIY_COLORS} "
                "hex colours separated by spaces or commas, e.g. '#ff0000 #00b0ff')"
            )
        digits = token.lstrip("#")
        colors.append(
            (int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16))
        )
    return tuple(colors)


def format_palette(colors: tuple[tuple[int, int, int], ...]) -> str:
    """The canonical text for a palette: lowercase ``#rrggbb``, space separated."""
    return " ".join(f"#{r:02x}{g:02x}{b:02x}" for r, g, b in colors)


class GoveeDiyModePreviewSensor(GoveeEntity, SensorEntity):
    """The preview artwork URL for this zone's currently staged DIY mode.

    Reads the same store the mode select writes, so it follows the picker with
    no wiring between the two entities. Its state is the URL of one image; the
    ``previews`` attribute is the whole zone's map, so a dashboard can render
    the current mode, a gallery of all of them, or both, from this one entity.

    Diagnostic, and not a RestoreEntity: every field is derived from the store
    and the profile table, both of which are available the moment the entity is
    added. Restoring a URL would only risk showing a stale one.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:image-multiple"
    _attr_translation_key = "diy_mode_preview"

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        device: GoveeDevice,
        profile: DeviceProfile,
        zone: DiyZoneSpec,
    ) -> None:
        """Bind the preview to one DIY zone of one device."""
        super().__init__(coordinator, device)
        self._profile = profile
        self._zone = zone
        self._zone_key = zone.zone_key
        self._zone_name = diy_zone_display_name(zone)
        self._attr_unique_id = f"{device.device_id}{SUFFIX_DIY_PREVIEW}{zone.zone_key}"
        self._attr_translation_placeholders = {"zone_name": self._zone_name}
        # Mode int -> the table's spelling of its name, so the store's int can
        # be turned back into the key the artwork map is filed under.
        self._name_by_mode = {code: name for name, code in zone.modes.items()}
        self._previews = preview_map(zone)

    @property
    def _store(self) -> Any:
        """The coordinator's staged-DIY store."""
        return store(self.coordinator)

    @property
    def _mode_name(self) -> str | None:
        """The staged mode's name, or None for a raw int the table cannot name."""
        return self._name_by_mode.get(self._store.mode(self._device_id, self._zone_key))

    @property
    def native_value(self) -> str | None:
        """URL path of the staged mode's preview.

        An unset or unnameable mode previews as the zone's "none" placeholder
        rather than going unknown: there is always something true to show, and
        "none" is what the lamp does with that zone.
        """
        return preview_url(self._zone_key, self._mode_name)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Zone provenance, the staged mode's name, and the full artwork map."""
        attrs = dict(super().extra_state_attributes)
        attrs["diy_zone"] = self._zone_key
        attrs["mode"] = self._mode_name
        attrs["previews"] = dict(self._previews)
        return attrs

    async def async_added_to_hass(self) -> None:
        """Follow the zone's staged record."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self._store.register(self._device_id, self._zone_key, self._on_store_change)
        )

    def _on_store_change(self) -> None:
        """Store callback — the mode select (or the service) moved this zone."""
        self.async_write_ha_state()


__all__ = [
    "MODE_NONE",
    "PALETTE_PATTERN",
    "GoveeDiyApplyButton",
    "GoveeDiyDirectionSelect",
    "GoveeDiyFlowRateNumber",
    "GoveeDiyModePreviewSensor",
    "GoveeDiyModeSelect",
    "GoveeDiyPaletteText",
    "GoveeDiySpeedNumber",
    "async_diy_button_entities",
    "async_diy_number_entities",
    "async_diy_preview_entities",
    "async_diy_select_entities",
    "async_diy_text_entities",
    "format_palette",
    "parse_palette",
]
