"""Staged DIY-effect parameters for multi-zone lamps (fork feature).

A DIY effect is a document, not a command: two zone records uploaded as one
multipacket blob and then committed, with no partial write and no way to read the
lamp's current one back. So the ``select`` / ``number`` / ``text`` entities stage
fields into a per-``(device_id, zone_key)`` record held here, and a button (or
``govee.apply_diy_effect``) uploads the assembled document. One store per
coordinator, with listeners so the staging entities repaint after a service call.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Final

from homeassistant.exceptions import HomeAssistantError

from .api.protocol import (
    DIRECTION_CW,
    MODE_NONE,
    DeviceProfile,
    DiyEffectSpec,
    DiyZoneEffect,
    GoveeCodec,
    GoveeProtocolError,
)
from .zone_state import profile_for, zone_lights_enabled

if TYPE_CHECKING:
    from .coordinator import GoveeCoordinator
    from .models import GoveeDevice

_LOGGER = logging.getLogger(__name__)

# Attribute the lazily-built store is cached under on the coordinator. Kept out
# of the coordinator's __init__ on purpose: no upstream line to conflict.
_STORE_ATTR: Final = "_govee_diy_state_store"

DEFAULT_SPEED: Final = 50
"""Mid-scale transition speed — what the app itself shows on a fresh DIY."""

DEFAULT_FLOW_RATE: Final = 50
"""Mid-scale ripple flow rate, matching the encoder's own fallback."""

DiyListener = Callable[[], None]

__all__ = [
    "DEFAULT_FLOW_RATE",
    "DEFAULT_SPEED",
    "DiyStateStore",
    "DiyZoneParams",
    "async_send_diy_effect",
    "diy_spec_for",
    "store",
    "zone_lights_enabled",
]


@dataclass(frozen=True)
class DiyZoneParams:
    """One zone's staged DIY record.

    Frozen so a caller cannot mutate the copy it reads out of the store and
    silently change what the next upload sends; :meth:`DiyStateStore._update`
    swaps a new instance in instead.
    """

    mode: int = MODE_NONE
    speed: int = DEFAULT_SPEED
    colors: tuple[tuple[int, int, int], ...] = field(default_factory=tuple)
    direction: int = DIRECTION_CW
    flow_rate: int = DEFAULT_FLOW_RATE

    def as_effect(
        self, has_direction: bool, has_flow_rate: bool
    ) -> DiyZoneEffect | None:
        """This record as the codec wants it, or None when the zone is off.

        ``direction`` and ``flow_rate`` are passed only for zones whose spec
        declares them: the encoder rejects them outright on a zone that has no
        tail, and staging a value for a field that does not exist is not a
        reason to fail the upload.

        Args:
            has_direction: Whether the zone's wire record carries a direction.
            has_flow_rate: Whether it carries a flow rate.

        Returns:
            The effect for this zone, or None for the empty "off" record.
        """
        if self.mode == MODE_NONE:
            return None
        return DiyZoneEffect(
            mode=self.mode,
            speed=self.speed,
            colors=self.colors,
            direction=self.direction if has_direction else None,
            flow_rate=self.flow_rate if has_flow_rate else None,
        )


class DiyStateStore:
    """Staged DIY records for one coordinator's devices."""

    def __init__(self, coordinator: GoveeCoordinator) -> None:
        """Initialize an empty store for ``coordinator``."""
        self._coordinator = coordinator
        self._params: dict[tuple[str, str], DiyZoneParams] = {}
        self._listeners: dict[tuple[str, str], list[DiyListener]] = {}

    # -- reads --------------------------------------------------------------

    def params(self, device_id: str, zone_key: str) -> DiyZoneParams:
        """The staged record for a zone (all defaults when never touched)."""
        return self._params.get((device_id, zone_key), DiyZoneParams())

    def mode(self, device_id: str, zone_key: str) -> int:
        """Staged effect mode int (0 = the zone plays no part in the DIY)."""
        return self.params(device_id, zone_key).mode

    def speed(self, device_id: str, zone_key: str) -> int:
        """Staged transition speed, 1-100."""
        return self.params(device_id, zone_key).speed

    def colors(self, device_id: str, zone_key: str) -> tuple[tuple[int, int, int], ...]:
        """Staged palette, in the order the effect cycles it."""
        return self.params(device_id, zone_key).colors

    def direction(self, device_id: str, zone_key: str) -> int:
        """Staged direction code — only meaningful when the zone declares one."""
        return self.params(device_id, zone_key).direction

    def flow_rate(self, device_id: str, zone_key: str) -> int:
        """Staged flow rate — only meaningful when the zone declares one."""
        return self.params(device_id, zone_key).flow_rate

    # -- writes -------------------------------------------------------------

    def set_mode(self, device_id: str, zone_key: str, mode: int) -> None:
        """Stage a zone's effect mode and notify its entities."""
        self._update(device_id, zone_key, mode=int(mode))

    def set_speed(self, device_id: str, zone_key: str, speed: int) -> None:
        """Stage a zone's transition speed and notify its entities."""
        self._update(device_id, zone_key, speed=int(speed))

    def set_colors(
        self, device_id: str, zone_key: str, colors: tuple[tuple[int, int, int], ...]
    ) -> None:
        """Stage a zone's palette and notify its entities."""
        self._update(device_id, zone_key, colors=tuple(tuple(c) for c in colors))  # type: ignore[arg-type]

    def set_direction(self, device_id: str, zone_key: str, direction: int) -> None:
        """Stage a zone's direction code and notify its entities."""
        self._update(device_id, zone_key, direction=int(direction))

    def set_flow_rate(self, device_id: str, zone_key: str, flow_rate: int) -> None:
        """Stage a zone's flow rate and notify its entities."""
        self._update(device_id, zone_key, flow_rate=int(flow_rate))

    def seed(self, device_id: str, zone_key: str, **fields: object) -> None:
        """Write staged fields without notifying — for restore only.

        The values came *from* the entities, so echoing them back at the
        entity that is already about to write its own state is noise.

        Args:
            device_id: The Govee device the zone belongs to.
            zone_key: The DIY zone key ("ripple", "ring").
            **fields: Any subset of :class:`DiyZoneParams`' fields.
        """
        self._write(device_id, zone_key, fields)

    def update(self, device_id: str, zone_key: str, **fields: object) -> None:
        """Write several staged fields at once and notify the zone's entities.

        Used by the service, which applies a whole record in one go and must
        leave the config entities showing what was actually sent.
        """
        self._update(device_id, zone_key, **fields)

    def _update(self, device_id: str, zone_key: str, **fields: object) -> None:
        self._write(device_id, zone_key, fields)
        self._notify(device_id, zone_key)

    def _write(self, device_id: str, zone_key: str, fields: dict[str, object]) -> None:
        current = self.params(device_id, zone_key)
        self._params[(device_id, zone_key)] = replace(current, **fields)  # type: ignore[arg-type]

    # -- subscriptions ------------------------------------------------------

    def register(
        self, device_id: str, zone_key: str, listener: DiyListener
    ) -> Callable[[], None]:
        """Subscribe ``listener`` to a zone's record. Returns an unsubscribe."""
        listeners = self._listeners.setdefault((device_id, zone_key), [])
        listeners.append(listener)

        def _unsub() -> None:
            try:
                listeners.remove(listener)
            except ValueError:  # pragma: no cover - double removal
                pass

        return _unsub

    def _notify(self, device_id: str, zone_key: str) -> None:
        for listener in list(self._listeners.get((device_id, zone_key), ())):
            listener()

    # -- assembly -----------------------------------------------------------

    def effects(
        self, device_id: str, diy: DiyEffectSpec
    ) -> dict[str, DiyZoneEffect | None]:
        """The staged document, keyed the way :meth:`GoveeCodec.diy_effect` wants.

        Every zone the spec declares is present, in wire order; a zone staged
        at mode 0 maps to None, which the encoder writes as the empty record.

        Args:
            device_id: The Govee device to assemble the document for.
            diy: The SKU's DIY layout, which decides the zones and their tails.

        Returns:
            Zone key -> effect (or None), for every zone in the layout.
        """
        return {
            zone.zone_key: self.params(device_id, zone.zone_key).as_effect(
                zone.has_direction, zone.has_flow_rate
            )
            for zone in diy.zones
        }


# Entry points used by the platforms and the service


def store(coordinator: GoveeCoordinator) -> DiyStateStore:
    """The coordinator's staged-DIY store, built on first use."""
    existing: DiyStateStore | None = getattr(coordinator, _STORE_ATTR, None)
    if existing is None:
        existing = DiyStateStore(coordinator)
        setattr(coordinator, _STORE_ATTR, existing)
    return existing


def diy_spec_for(sku: str) -> tuple[DeviceProfile, DiyEffectSpec] | None:
    """``(profile, diy layout)`` for a SKU, or None when it has no DIY.

    None covers both honest cases with one answer: a SKU the profile table does
    not know at all, and a profiled SKU whose table entry declares no DIY
    layout. Neither can be given DIY entities.
    """
    profile = profile_for(sku)
    if profile is None or profile.diy is None:
        return None
    return profile, profile.diy


async def async_send_diy_effect(
    coordinator: GoveeCoordinator,
    device: GoveeDevice,
    profile: DeviceProfile,
    effects: dict[str, DiyZoneEffect | None],
) -> None:
    """Encode and upload one assembled DIY document, or raise.

    Shared by the apply button and the ``apply_diy_effect`` service so the two
    entry points cannot drift on error wording or on the frame order (the
    ``0xA3`` chunks and their commit frame must ride in ONE envelope, which is
    what a single :func:`async_send_frames` call gives them).

    Args:
        coordinator: The coordinator owning the device.
        device: The device to upload to.
        profile: Its raw-protocol profile.
        effects: Zone key -> effect (or None), as :meth:`DiyStateStore.effects`
            builds it.

    Raises:
        HomeAssistantError: If the document cannot be encoded, the device has
            no usable raw-LAN path, or the frames could not be sent.
    """
    # Imported here rather than at module scope: lan_raw imports zone_state,
    # and keeping the transport out of this module's import graph is what lets
    # the store be unit-tested with no coordinator at all.
    from .api import lan_raw

    try:
        frames = GoveeCodec(profile).diy_effect(effects)
    except GoveeProtocolError as err:
        raise HomeAssistantError(
            f"{device.name}: cannot build this DIY effect ({err})"
        ) from err

    # require_option=False: this is the only pipe a DIY upload has.
    target = lan_raw.lan_target(
        coordinator, device.device_id, device.sku, require_option=False
    )
    if target is None:
        raise HomeAssistantError(
            f"{device.name}: DIY effects can only be uploaded over the local network, and this device "
            "is not currently reachable there (check the LAN UDP connectivity sensor)"
        )
    ip, _profile = target

    if not await lan_raw.async_send_frames(
        coordinator, device.device_id, ip, frames, what="diy effect upload"
    ):
        raise HomeAssistantError(
            f"{device.name}: the local-network DIY effect upload could not be sent"
        )

    _LOGGER.debug(
        "Govee DIY effect uploaded to %s (%d frames)", device.name, len(frames)
    )
