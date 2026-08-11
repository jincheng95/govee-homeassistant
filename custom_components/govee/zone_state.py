"""Transport-independent zone state for multi-zone lamps (fork feature).

Why this module exists
----------------------
Nothing reports per-zone state. Not the cloud, not the LAN ``devStatus`` read,
not a raw query — the lamp accepts zone commands and never says a word about
what its zones are doing. Every zone control in this integration is therefore
*optimistic*: the only truth available is "what we last told it".

That truth has to live somewhere both zone platforms can see. The H60B0's zones
are driven from two different entity platforms (the on/off switches in
``switch.py`` and, when the zone-lights option is on, the light entities in
``platforms/zone_light.py``), and the hardware constraint below couples them. An earlier
attempt kept the state on the entities and walked ``entity.platform.entities``
to find siblings, which silently only ever saw the *same* platform — a light
could never correct a switch. This registry is the fix: one per coordinator,
keyed by ``(device_id, zone_key)``, with entities subscribing to the zones they
represent.

The hardware constraint, applied once for every transport
---------------------------------------------------------
The H60B0 can light at most two of its three zones at once: switching one on
knocks another off *inside the lamp*, silently. The limit is declared in the
profile table as :class:`~.api.protocol.profiles.MaxSimultaneousZones` and the
order of its ``zone_keys`` is the displacement order (first-listed yields
first, which is why the H60B0's note reads "enabling the downlight kicks the
ripple off"). Nothing about any specific SKU is encoded here.

Crucially the constraint is applied in :meth:`ZoneStateRegistry.apply`, which
sits *below* the choice of transport. A zone turned on over raw LAN and the
same zone turned on over the cloud both land here, so the displaced zone's UI
is corrected either way. Before this module the correction existed only on the
LAN path and a cloud toggle left the displaced zone's entity lying.

Restore
-------
The registry holds no persistence of its own. Zone entities are
:class:`RestoreEntity` instances; each seeds the registry with its restored
value when it registers, so a restart rebuilds the same optimistic picture
without the registry having to know about HA's state machine.

Fork-maintenance note: the coordinator/entity internals reached into here are
read in ONE place each (the accessor block at the bottom), the same discipline
:mod:`.api.lan_nudge` and :mod:`.api.lan_raw_write` established.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any, Final

from .api.protocol import (
    Capability,
    DeviceProfile,
    GoveeProtocolError,
    MaxSimultaneousZones,
    get_profile,
)
from .const import CONF_ENABLE_ZONE_LIGHTS, DEFAULT_ENABLE_ZONE_LIGHTS

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from .coordinator import GoveeCoordinator

_LOGGER = logging.getLogger(__name__)

# Cloud capability instance -> profile zone key. The cloud names the parts after
# what they look like; the profile table names them after what the firmware
# calls them, so the join has to be explicit.
ZONE_KEY_BY_TOGGLE: Final[dict[str, str]] = {
    "rippleLightToggle": "ripple",
    "sideLightToggle": "ring",
    "bottomLightToggle": "downlight",
}

# Attribute the lazily-built registry is cached under on the coordinator. Kept
# out of the coordinator's __init__ on purpose: no upstream line to conflict.
_REGISTRY_ATTR: Final = "_govee_zone_state_registry"

ZoneSetter = Callable[[bool], None]


def profile_for(sku: str) -> DeviceProfile | None:
    """The raw-protocol profile for ``sku``, or None when the table has no entry."""
    try:
        return get_profile(sku)
    except GoveeProtocolError:
        return None


def modelled_zone_keys(sku: str) -> tuple[str, ...]:
    """Zone keys of ``sku`` that this integration can actually drive.

    A zone qualifies when the profile gives it a zone byte *and* declares
    ``ZONE_POWER`` for it. That filter is what keeps the H60B0's two
    colour-strip pseudo-zones (``top_side``, ``top_side_platform``, both
    declared with an empty capability set) out of the entity model without
    naming them here.
    """
    profile = profile_for(sku)
    if profile is None:
        return ()
    return tuple(
        zone.key for zone in profile.zones if zone.zone_byte is not None and Capability.ZONE_POWER in zone.capabilities
    )


def zone_lights_enabled(entry: ConfigEntry) -> bool:
    """Whether the user opted into the zone-light entity model.

    Strictly a boolean check, not a truthiness one. This option *removes*
    entities when it is on, and the options flow only ever stores a real bool —
    so anything else (a half-migrated entry, a stub) is treated as off. Failing
    towards "keep the switches" is the recoverable direction.
    """
    return entry.options.get(CONF_ENABLE_ZONE_LIGHTS, DEFAULT_ENABLE_ZONE_LIGHTS) is True


def zone_switch_suppressed(entry: ConfigEntry, sku: str, toggle_instance: str) -> bool:
    """Whether ``toggle_instance``'s switch should give way to a zone light.

    Two entities for one zone — ``switch.lamp_ripple_light`` next to
    ``light.lamp_ripple`` — would show up twice in every entity picker, voice
    assistant and area card, and the two optimistic states would drift apart the
    moment one of them was used. So when the option is on, the light wins and
    the switch is not created for the zones the light covers. Switch unique ids
    are *not* reused by the lights, so turning the option back off restores the
    original switch entities, history and all.
    """
    if not zone_lights_enabled(entry):
        return False
    zone_key = ZONE_KEY_BY_TOGGLE.get(toggle_instance)
    return zone_key is not None and zone_key in modelled_zone_keys(sku)


class ZoneStateRegistry:
    """Optimistic per-zone state for one coordinator's devices."""

    def __init__(self, coordinator: GoveeCoordinator) -> None:
        """Initialize an empty registry for ``coordinator``."""
        self._coordinator = coordinator
        self._state: dict[tuple[str, str], bool] = {}
        self._listeners: dict[tuple[str, str], list[ZoneSetter]] = {}

    def is_on(self, device_id: str, zone_key: str) -> bool:
        """Last state commanded for a zone (False when never commanded)."""
        return self._state.get((device_id, zone_key), False)

    def seed(self, device_id: str, zone_key: str, on: bool) -> None:
        """Set a zone's state without notifying or applying the constraint.

        For restore only: the value came *from* the entities, so echoing it back
        at them is noise, and a restored pair of lit zones is a picture the lamp
        already accepted once.
        """
        self._state[(device_id, zone_key)] = on

    def register(self, device_id: str, zone_key: str, setter: ZoneSetter) -> Callable[[], None]:
        """Subscribe ``setter`` to a zone. Returns an unsubscribe callable."""
        listeners = self._listeners.setdefault((device_id, zone_key), [])
        listeners.append(setter)

        def _unsub() -> None:
            try:
                listeners.remove(setter)
            except ValueError:  # pragma: no cover - double removal
                pass

        return _unsub

    def apply(self, device_id: str, sku: str, zone_key: str, on: bool) -> list[str]:
        """Record a zone command and enforce the profile's simultaneity limit.

        Args:
            device_id: The Govee device the zone belongs to.
            sku: Its model, used to look the profile (and constraints) up.
            zone_key: The profile zone key just commanded.
            on: The state commanded.

        Returns:
            The zone keys the hardware just switched off as a side effect, in
            the order the profile displaces them.
        """
        self._state[(device_id, zone_key)] = on
        self._notify(device_id, zone_key, on)
        if not on:
            return []

        profile = profile_for(sku)
        if profile is None:
            return []

        lit = {key for key in modelled_zone_keys(sku) if key != zone_key and self.is_on(device_id, key)}
        displaced = displaced_zone_keys(profile, zone_key, lit)
        for key in displaced:
            _LOGGER.debug(
                "Govee zone state: %s zone %s displaced %s (hardware limit)",
                device_id,
                zone_key,
                key,
            )
            self._state[(device_id, key)] = False
            self._notify(device_id, key, False)
        return displaced

    def _notify(self, device_id: str, zone_key: str, on: bool) -> None:
        for setter in list(self._listeners.get((device_id, zone_key), ())):
            setter(on)


def displaced_zone_keys(profile: DeviceProfile, zone_key: str, lit: Iterable[str]) -> list[str]:
    """Zones the hardware turns off because ``zone_key`` was turned on.

    The limit and the zone set come from the profile's
    :class:`MaxSimultaneousZones` entries; the *order* of ``zone_keys`` in the
    table is the displacement order, first-listed first.
    """
    lit_set = set(lit)
    displaced: list[str] = []
    for constraint in _constraints_for(profile, zone_key):
        on_now = [key for key in constraint.zone_keys if key == zone_key or (key in lit_set and key not in displaced)]
        for key in constraint.zone_keys:
            if len(on_now) <= constraint.limit:
                break
            if key == zone_key or key not in on_now:
                continue
            on_now.remove(key)
            displaced.append(key)
    return displaced


def _constraints_for(profile: DeviceProfile, zone_key: str) -> tuple[MaxSimultaneousZones, ...]:
    """The profile's simultaneity constraints that cover ``zone_key``."""
    return tuple(constraint for constraint in profile.constraints if zone_key in constraint.zone_keys)


# ----------------------------------------------------------------------
# Entry points used by the platforms
# ----------------------------------------------------------------------


def registry(coordinator: GoveeCoordinator) -> ZoneStateRegistry:
    """The coordinator's zone-state registry, built on first use."""
    existing: ZoneStateRegistry | None = getattr(coordinator, _REGISTRY_ATTR, None)
    if existing is None:
        existing = ZoneStateRegistry(coordinator)
        setattr(coordinator, _REGISTRY_ATTR, existing)
    return existing


def note_zone_power(entity: Any, on: bool) -> list[str]:
    """Record a zone power change made by a zone *switch* over any transport.

    Called from ``switch.py`` after the cloud command succeeds, so a cloud
    toggle applies the displacement constraint exactly like the LAN path does.
    A no-op for named-light toggles that are not profile zones (the ceiling-fan
    main/background lights, for instance).

    Returns:
        The zone keys displaced, for logging/tests. Empty when not a zone.
    """
    zone_key = ZONE_KEY_BY_TOGGLE.get(_toggle_instance(entity))
    if zone_key is None:
        return []
    device_id = _device_id(entity)
    if device_id is None:
        return []
    return registry(_coordinator(entity)).apply(device_id, _sku(entity), zone_key, on)


def register_zone_switch(entity: Any) -> Callable[[], None]:
    """Subscribe a zone *switch* entity to its zone. Returns an unsubscribe.

    Called from ``switch.py``'s ``async_added_to_hass``. Without it a switch
    displaced by a command issued from a zone light (or by another switch under
    the option's off/on transition) would keep showing "on".
    """
    zone_key = ZONE_KEY_BY_TOGGLE.get(_toggle_instance(entity))
    device_id = _device_id(entity)
    if zone_key is None or device_id is None:
        return lambda: None

    store = registry(_coordinator(entity))
    store.seed(device_id, zone_key, _switch_is_on(entity))

    def _set(on: bool) -> None:
        _set_switch_state(entity, on)

    return store.register(device_id, zone_key, _set)


# ----------------------------------------------------------------------
# Coordinator / entity internals, read in exactly one place each. An upstream
# rename of any of these is a one-line fix in this block.
# ----------------------------------------------------------------------


def _coordinator(entity: Any) -> GoveeCoordinator:
    """The coordinator owning ``entity``."""
    return entity.coordinator  # type: ignore[no-any-return]


def _device_id(entity: Any) -> str | None:
    """The Govee device id behind ``entity``."""
    return getattr(entity, "_device_id", None)


def _sku(entity: Any) -> str:
    """The device SKU (``H60B0``), used to look the profile up."""
    return str(getattr(getattr(entity, "_device", None), "sku", "") or "")


def _toggle_instance(entity: Any) -> str:
    """The cloud capability instance a switch drives (``rippleLightToggle``)."""
    return str(getattr(entity, "_toggle_instance", "") or "")


def _switch_is_on(entity: Any) -> bool:
    """Current optimistic state of a zone switch."""
    return bool(getattr(entity, "_is_on", False))


def _set_switch_state(entity: Any, on: bool) -> None:
    """Write a zone switch's optimistic state and notify HA."""
    entity._is_on = on
    entity.async_write_ha_state()
