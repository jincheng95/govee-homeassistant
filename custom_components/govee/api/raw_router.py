"""Which raw pipe a frame takes, and the entry points entities call (fork).

The 20-byte frames are transport-neutral, so the pipe is a routing decision, not
a codec one: :func:`async_route_frames` tries LAN raw, then plaintext BLE, then
the cloud MQTT ``ptReal`` passthrough, and reports False when no tier accepted
("not handled, use the cloud command you had"). No tier raises at the entity and
no tier confirms, so callers keep optimistic state. Also home to the per-segment
paint (:func:`async_segment_color`), which picks its wire form from the profile,
and to zone power (:func:`async_zone_power`).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Final

from ..segment_limit import manual_segment_count, segment_count
from ..zone_state import ZONE_KEY_BY_TOGGLE, profile_for, registry
from . import ble_raw_write, lan_raw, mqtt_raw_write
from .protocol import (
    Capability,
    DeviceProfile,
    GoveeCodec,
    GoveeProtocolError,
    ha_to_percent,
)

if TYPE_CHECKING:
    from ..coordinator import GoveeCoordinator

_LOGGER = logging.getLogger(__name__)

# Profile zone key used by SKUs whose segments are addressed by mask alone.
SEGMENT_ZONE_KEY: Final = "segments"


# Routing


async def async_route_frames(
    coordinator: GoveeCoordinator,
    device_id: str,
    sku: str,
    profile: DeviceProfile,
    frames: Sequence[bytes],
    *,
    what: str = "frames",
) -> bool:
    """Send pre-built frames down the best raw pipe this device has right now.

    The tiers, in order, each one gated by its own option and by the profile's
    ``transports`` list:

    1. **LAN raw** — one UDP datagram on the local subnet, ~30 ms. The default
       for every SKU on the modern stack.
    2. **BLE plaintext** — the identical bytes over an unencrypted GATT write,
       for SKUs whose only raw pipe that is (the H6046 light bar). Local, but a
       one-central link, so it is tried second and held only briefly.
    3. **MQTT ptReal** — the identical bytes published to the device's cloud
       topic, ~300-500 ms. Covers a lamp with no LAN correlation, and every SKU
       that has no usable local raw pipe.

    Every tier that does not apply — or raises — returns "not handled" and the
    next one is tried; when none of them can, this returns False and the caller
    falls back to whatever cloud *command* it had. Nothing here propagates an
    exception to the entity. No tier confirms — raw frames are unacknowledged
    on all channels — so callers keep optimistic state.

    Args:
        coordinator: The coordinator owning the device.
        device_id: The Govee device id.
        sku: The device model.
        profile: The SKU's profile (already looked up by the caller).
        frames: The frames to deliver, in order.
        what: Human-readable intent, for the debug log.

    Returns:
        True when some tier accepted the frames.
    """
    if not frames:
        return False

    if await _async_lan_tier(coordinator, device_id, sku, frames, what):
        return True

    if await _async_tier("BLE", ble_raw_write.async_send_frames, coordinator, device_id, sku, profile, frames, what):
        return True

    return await _async_tier(
        "MQTT", mqtt_raw_write.async_send_frames, coordinator, device_id, sku, profile, frames, what
    )


async def _async_tier(
    tier: str,
    send: Any,
    coordinator: GoveeCoordinator,
    device_id: str,
    sku: str,
    profile: DeviceProfile,
    frames: Sequence[bytes],
    what: str,
) -> bool:
    """Run one tier, swallowing anything it raises. False means "not handled".

    A tier that raises would propagate out of the entity's service call and
    hide every tier under it, so an unexpected error is a fall-through like any
    other refusal — the same contract the ble/mqtt tiers already keep
    internally, applied here so it holds however a tier is written.
    """
    try:
        return bool(await send(coordinator, device_id, sku, profile, frames, what=what))
    except Exception as err:  # noqa: BLE001 - a tier must never raise at an entity
        _LOGGER.debug("Govee raw router: the %s tier for %s raised (%s) — trying the next tier", tier, device_id, err)
        return False


async def _async_lan_tier(
    coordinator: GoveeCoordinator,
    device_id: str,
    sku: str,
    frames: Sequence[bytes],
    what: str,
) -> bool:
    """The LAN tier of :func:`async_route_frames`. False means "not handled".

    The issue #57 write-suppression cooldown is consulted **here and nowhere
    above it**: the cooldown is a statement about upstream's LAN tier only, so
    a device inside it must still be paintable over BLE and MQTT. Consulting it
    before routing blacked out every raw pipe at once.
    """
    if _writes_suppressed(coordinator, device_id):
        _LOGGER.debug(
            "Govee raw router: LAN writes to %s are in upstream's cooldown — trying the remaining tiers", sku
        )
        return False
    target = _lan_target(coordinator, device_id, sku)
    if target is None:
        return False
    ip, _profile = target
    try:
        return await lan_raw.async_send_frames(coordinator, device_id, ip, frames, what=what)
    except Exception as err:  # noqa: BLE001 - a tier must never raise at an entity
        _LOGGER.debug("Govee raw router: the LAN tier for %s raised (%s) — trying the next tier", device_id, err)
        return False


def raw_write_enabled(coordinator: GoveeCoordinator) -> bool:
    """Whether the user opted into raw frames on any transport.

    The entry gate for the raw path as a whole; each tier then applies its own
    option (see :func:`async_route_frames`). With every raw option off this is
    False and nothing below is even built.
    """
    return (
        lan_raw.lan_write_enabled(coordinator)
        or ble_raw_write.ble_raw_enabled(coordinator)
        or mqtt_raw_write.mqtt_raw_enabled(coordinator)
    )


# Zone power (the switch entities in switch.py)


async def async_zone_power(entity: Any, *, on: bool) -> bool:
    """Try to switch one zone over raw LAN.

    Args:
        entity: The ``GoveeNamedLightSwitchEntity`` being toggled.
        on: Target state for the zone.

    Returns:
        True when the LAN write was sent and the optimistic state applied — the
        caller must then do nothing else. False means "not handled": the caller
        falls through to the cloud path exactly as before.
    """
    coordinator = _coordinator(entity)
    instance = _toggle_instance(entity)
    zone_key = ZONE_KEY_BY_TOGGLE.get(instance)
    if zone_key is None:
        return False

    device_id = _device_id(entity)
    sku = _sku(entity)
    target = _lan_target(coordinator, device_id, sku)
    if target is None:
        return False
    ip, profile = target

    if _writes_suppressed(coordinator, device_id):
        # Upstream holds LAN writes for this device (issue #57 post-failure
        # cooldown). Zone power has an exact cloud fallback (ToggleCommand),
        # so honour the hold and let the caller reroute — nothing is lost.
        _LOGGER.debug("Govee LAN write: writes to %s are in upstream's cooldown — routing zone power via cloud", sku)
        return False

    if not zone_power_supported(profile, zone_key):
        _LOGGER.debug(
            "Govee LAN write: %s/%s has no raw zone-power profile — using cloud transport",
            sku,
            instance,
        )
        return False

    try:
        frame = GoveeCodec(profile).zone_power(zone_key, on)
    except GoveeProtocolError as err:
        _LOGGER.debug("Govee LAN write: cannot encode zone power for %s (%s)", sku, err)
        return False

    if device_id is None or not await lan_raw.async_send_frames(
        coordinator, device_id, ip, [frame], what=f"zone {zone_key} -> {'on' if on else 'off'}"
    ):
        return False

    _set_state(entity, on)
    registry(coordinator).apply(device_id, sku, zone_key, on)
    return True


def zone_power_supported(profile: DeviceProfile, zone_key: str) -> bool:
    """Whether this profile can express zone power for ``zone_key``."""
    try:
        zone = profile.zone(zone_key)
    except GoveeProtocolError:
        return False
    if zone.zone_byte is None:
        return False
    return profile.supports(Capability.ZONE_POWER, zone=zone_key)


# Per-segment colour (the segment light entities)


async def async_segment_color(
    entity: Any,
    rgb: tuple[int, int, int],
    segments: Sequence[int],
    *,
    brightness: int | None = None,
) -> bool:
    """Try to paint segments of an RGBIC device over a raw pipe.

    Which frame goes out is a property of the profile, not of the caller: a SKU
    that declares ``segment_zone`` gets the zone-colour frame plus a segment
    mask (the H60B0's ring), anything else gets the mask-only ``SEGMENT_COLOR``
    frame (the H6076). Callers hold flat 0-based segment indices either way.

    Returns False — "not handled, use the cloud" — for every reason a raw frame
    would be a guess: option off, no profile, neither segment form declared, a
    profile constant still UNKNOWN, an empty selection (an all-zero mask is
    silently ignored by the firmware), a mismatch between the segment count the
    entities index and the mask width the table declares, or no tier that could
    carry the frames. The LAN cooldown is *not* one of them — it is applied to
    the LAN tier alone inside :func:`async_route_frames`.

    The cloud fallback is exact for the colour — ``SegmentColorCommand``
    expresses the same intent — so nothing is lost by refusing.

    Args:
        entity: The segment (or grouped-segment) light entity being written.
        rgb: The colour to paint.
        segments: 0-based segment indices to paint.
        brightness: HA brightness (0-255) when the user moved the slider in
            this call, else None. Only the zone+mask form carries it; the
            mask-only SKUs keep the colour-only behaviour they shipped with.
    """
    coordinator = _coordinator(entity)
    device_id = _device_id(entity)
    sku = _sku(entity)
    if device_id is None:
        return False
    profile = profile_for(sku)
    if profile is None or not raw_write_enabled(coordinator):
        return False

    zone_key = profile.segment_zone_key
    if zone_key is not None:
        frames = _zone_segment_frames(entity, profile, zone_key, rgb, segments, brightness)
    else:
        frames = _masked_segment_frames(entity, profile, rgb, segments, brightness)
    if not frames:
        return False

    return await async_route_frames(
        coordinator, device_id, sku, profile, frames, what=f"segments {list(segments)} -> {rgb}"
    )


def _masked_segment_frames(
    entity: Any,
    profile: DeviceProfile,
    rgb: tuple[int, int, int],
    segments: Sequence[int],
    brightness: int | None = None,
) -> list[bytes]:
    """The mask-only ``SEGMENT_COLOR`` (+level) frames, or [] to fall back.

    An explicit brightness rides along as a second frame under attribute 0x02,
    where the mask sits at a different offset — both offsets are table data on
    the two capabilities, so the codec builds both. A colour-only paint sends no
    level frame, so an untouched slider never overwrites what the segments are
    at.
    """
    if not profile.supports(Capability.SEGMENT_COLOR, zone=SEGMENT_ZONE_KEY):
        return []
    if not _segment_count_matches(entity, profile, SEGMENT_ZONE_KEY):
        _LOGGER.debug(
            "Govee LAN write: %s segment count disagrees with the profile mask width — using cloud transport",
            profile.sku,
        )
        return []

    codec = GoveeCodec(profile)
    chosen = list(segments)
    try:
        frames = [codec.segment_color(rgb, segments=chosen, zone=SEGMENT_ZONE_KEY)]
        if brightness is not None and profile.supports(Capability.SEGMENT_BRIGHTNESS, zone=SEGMENT_ZONE_KEY):
            frames.append(codec.segment_brightness(ha_to_percent(brightness), segments=chosen))
        return frames
    except GoveeProtocolError as err:
        # UnknownEncodingError lands here: the table refuses to guess a byte.
        _LOGGER.debug(
            "Govee LAN write: cannot encode segment colour for %s (%s) — using cloud transport", profile.sku, err
        )
        return []


def _zone_segment_frames(
    entity: Any,
    profile: DeviceProfile,
    zone_key: str,
    rgb: tuple[int, int, int],
    segments: Sequence[int],
    brightness: int | None,
) -> list[bytes]:
    """The zone-colour(+brightness) frames for a SKU whose segments live on a zone.

    The mask offsets differ by attribute and are table data on the two
    capabilities, so both frames are built by the codec — never assembled here.
    """
    if not profile.supports(Capability.ZONE_COLOR, zone=zone_key):
        return []
    if not _segment_count_matches(entity, profile, zone_key):
        _LOGGER.debug(
            "Govee LAN write: %s segment count disagrees with the %s zone's mask width — using cloud transport",
            profile.sku,
            zone_key,
        )
        return []

    codec = GoveeCodec(profile)
    chosen = list(segments)
    try:
        frames = [codec.zone_color(zone_key, rgb, segments=chosen)]
        if brightness is not None and profile.supports(Capability.ZONE_BRIGHTNESS, zone=zone_key):
            frames.append(codec.zone_brightness(zone_key, ha_to_percent(brightness), segments=chosen))
    except GoveeProtocolError as err:
        # SegmentMaskError (empty selection) and UnknownEncodingError both land
        # here; either way the frame would be a guess or a silent no-op.
        _LOGGER.debug(
            "Govee LAN write: cannot encode a %s segment paint for %s (%s) — using cloud transport",
            zone_key,
            profile.sku,
            err,
        )
        return []
    return frames


def _segment_count_matches(entity: Any, profile: DeviceProfile, zone_key: str) -> bool:
    """Whether the device's reported segment count matches the profile's mask.

    The entities index segments from the cloud's ``segmentedColorRgb`` count; the
    mask width comes from the table. If they disagree the table is describing a
    different revision of the hardware and the bits would land on the wrong
    LEDs, so the write is refused rather than approximated.
    """
    try:
        zone = profile.zone(zone_key)
    except GoveeProtocolError:
        return False
    return bool(zone.segments) and zone.segments == _segment_count(entity)


# Coordinator / entity internals, read in exactly one place each. An upstream
# rename of any of these is a one-line fix in this block.


def _coordinator(entity: Any) -> GoveeCoordinator:
    """The coordinator owning ``entity``."""
    return entity.coordinator  # type: ignore[no-any-return]


def _device_id(entity: Any) -> str | None:
    """The Govee device id behind ``entity``."""
    return getattr(entity, "_device_id", None)


def _sku(entity: Any) -> str:
    """The device SKU (``H60B0``), used to look the raw-LAN profile up."""
    return str(getattr(getattr(entity, "_device", None), "sku", "") or "")


def _segment_count(entity: Any) -> int:
    """How many segments the device has, capped per :mod:`..segment_limit`."""
    device = getattr(entity, "_device", None)
    coordinator = getattr(entity, "coordinator", None)
    options = getattr(getattr(coordinator, "config_entry", None), "options", None)
    override = manual_segment_count(options, str(getattr(device, "device_id", "") or ""))
    return segment_count(device, override)


def _toggle_instance(entity: Any) -> str:
    """The cloud capability instance the switch drives (``rippleLightToggle``)."""
    return str(getattr(entity, "_toggle_instance", "") or "")


def _set_state(entity: Any, on: bool) -> None:
    """Write a zone switch's optimistic state and notify HA."""
    entity._is_on = on
    entity.async_write_ha_state()


def _lan_target(coordinator: GoveeCoordinator, device_id: str | None, sku: str) -> tuple[str, DeviceProfile] | None:
    """:func:`lan_raw.lan_target`, degraded to "no target" if it raises.

    It reaches private coordinator state (``_lan_devices``), so an upstream
    rename must show up here as "this tier does not apply" and let the next one
    run — never as an exception at the entity, which is the contract the tier
    wrapper keeps for everything else.
    """
    try:
        return lan_raw.lan_target(coordinator, device_id, sku)
    except Exception as err:  # noqa: BLE001 - a tier must never raise at an entity
        _LOGGER.debug("Govee raw router: the LAN target for %s could not be resolved (%s)", device_id, err)
        return None


def _writes_suppressed(coordinator: GoveeCoordinator, device_id: str | None) -> bool:
    """Whether upstream is currently holding LAN writes for ``device_id``.

    Reads upstream's own cooldown predicate, which also re-arms an expired
    window — the same call upstream's ``_try_lan_command`` makes, so the fork
    and upstream's LAN tier come out of the cooldown together.

    Consulted by the router's LAN tier and by zone power — the two places that
    have somewhere else to go. It is a statement about upstream's LAN tier
    only, so it never stands down BLE or MQTT, and the LAN-only writes (zone
    colour/CT/flow-rate, DIY uploads) never consult it at all: for them
    standing down is a total outage, and the cooldown is armed by
    confirm-misses on upstream's *verified* tier.

    Strictly ``is True``: this reaches a private upstream method, and anything
    that is not a real ``True`` (a rename leaving None, a stub) must mean "not
    suppressed", i.e. the behaviour this module had before the check existed.
    """
    if device_id is None:
        return False
    suppressed = getattr(coordinator, "_lan_writes_suppressed", None)
    if suppressed is None:
        return False
    return suppressed(device_id) is True
