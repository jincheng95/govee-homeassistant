"""Raw frames over the cloud MQTT ``ptReal`` passthrough (fork feature).

Why this exists
---------------
The 20-byte frame layout is transport-neutral: the identical bytes travel over
LAN ``ptReal``, over BLE, and inside Govee's cloud MQTT passthrough (reference
§1.3). The fork's AWS IoT client already publishes ``ptReal`` envelopes for
upstream's music-mode / DreamView / DIY-scene packets, so a codec-built frame
needs no new plumbing — only a decision about *when* the cloud pipe is the
right one.

It is the last raw tier, below LAN and BLE:

* it is the slowest (~300-500 ms, and the bytes leave the house), and
* it is the only one that works when nothing local does — a lamp with no LAN
  correlation, an ESPHome proxy that is out of range, or an H6046 whose BLE
  option is off.

Gating — deliberately no new option
-----------------------------------
The tier is active when **both** existing options are on:

* ``enable_mqtt_control`` — the user's opt-in to sending commands over Govee's
  MQTT channel at all (an undocumented channel; the option's description
  already says so), and
* ``enable_lan_raw_write`` — the user's opt-in to *raw frames*, which are
  unconfirmable and therefore leave entity state optimistic.

A third checkbox would ask the user a question they have already answered
twice, and neither existing option alone implies the other's risk. The
trade-off is that a user who wants raw frames over MQTT but not upstream's
MQTT-native power/brightness path cannot express that; nobody has asked for it,
and the combination is easy to add later without moving anything here.

The profile table still decides *which SKUs* may use the pipe
(:attr:`~.protocol.profiles.Transport.MQTT_PTREAL` in ``transports``), exactly
as it does for LAN.

No confirmation
---------------
There is no ACK on any channel (reference §2.1), and a raw write is invisible
to ``devStatus`` regardless, so this path is fire-and-forget with optimistic
state, the same contract as the LAN raw tier. A publish that the broker accepts
is reported as success; nothing here waits for the lamp.

Fork-maintenance note: the coordinator internals are read in ONE place each,
the accessor block at the bottom — the discipline :mod:`.lan_raw` and
:mod:`.lan_nudge` established.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Final

from ..const import (
    CONF_ENABLE_LAN_RAW_WRITE,
    CONF_ENABLE_MQTT_CONTROL,
    DEFAULT_ENABLE_LAN_RAW_WRITE,
    DEFAULT_ENABLE_MQTT_CONTROL,
)
from .protocol import DeviceProfile, Transport, frame_to_base64

if TYPE_CHECKING:
    from ..coordinator import GoveeCoordinator

_LOGGER = logging.getLogger(__name__)

TOPIC_PATTERN: Final = re.compile(r"^G[AD]/[A-Za-z0-9:_\-./]{1,128}$")
"""The shape of a Govee device command topic: ``GA/`` or ``GD/`` + an id."""

MQTT_WILDCARDS: Final = ("#", "+")
"""Never publishable. ``GA/#`` addresses every device on the account at once."""


def mqtt_raw_enabled(coordinator: GoveeCoordinator) -> bool:
    """Whether both options that make up this tier's opt-in are on."""
    options = _options(coordinator)
    return bool(options.get(CONF_ENABLE_MQTT_CONTROL, DEFAULT_ENABLE_MQTT_CONTROL)) and bool(
        options.get(CONF_ENABLE_LAN_RAW_WRITE, DEFAULT_ENABLE_LAN_RAW_WRITE)
    )


def mqtt_raw_target(coordinator: GoveeCoordinator, profile: DeviceProfile) -> bool:
    """Whether a raw frame for this SKU could go out over MQTT right now.

    Args:
        coordinator: The coordinator owning the device.
        profile: The device's SKU profile.

    Returns:
        True when the options are on, the table declares the pipe for this SKU,
        and the MQTT client is connected.
    """
    if not mqtt_raw_enabled(coordinator):
        return False
    if not profile.carries(Transport.MQTT_PTREAL):
        return False
    return _mqtt_connected(coordinator)


def valid_topic(topic: str) -> bool:
    """Whether ``topic`` is safe to publish a device command to.

    The topic is coordinator-supplied and goes straight onto the wire, so it is
    checked rather than trusted: a wildcard would broadcast a raw frame to
    every device on the account, and a malformed one would publish into a topic
    tree that is not ours at all.

    Args:
        topic: The topic the coordinator returned for this device.

    Returns:
        True when it matches :data:`TOPIC_PATTERN` and carries no wildcard.
    """
    if any(wildcard in topic for wildcard in MQTT_WILDCARDS):
        # Redundant against TOPIC_PATTERN's character class, and deliberately
        # so: widening that class later must not quietly admit a wildcard.
        return False
    return TOPIC_PATTERN.fullmatch(topic) is not None


async def async_send_frames(
    coordinator: GoveeCoordinator,
    device_id: str,
    sku: str,
    profile: DeviceProfile,
    frames: Sequence[bytes],
    *,
    what: str = "frames",
) -> bool:
    """Publish pre-built frames as one ``ptReal`` envelope. True when sent.

    Every frame goes in a single envelope's ``command`` array, which the device
    applies in order — the same guarantee the LAN envelope gives, so a
    "colour then level" pair cannot be split.

    Args:
        coordinator: The coordinator owning the device.
        device_id: The Govee device id.
        sku: The device model, carried in the ptReal data block.
        profile: The device's SKU profile (read for the transport gate).
        frames: The frames to publish.
        what: Human-readable intent, for the debug log.

    Returns:
        True when the publish succeeded — the caller must then do nothing else.
        False means "not handled": fall through to the next tier.
    """
    if not frames or not mqtt_raw_target(coordinator, profile):
        return False

    client = _mqtt_client(coordinator)
    if client is None:
        return False

    topic = await _device_topic(coordinator, device_id)
    if not topic:
        _LOGGER.debug("Govee MQTT raw: no device topic for %s — falling through", device_id)
        return False
    if not valid_topic(topic):
        _LOGGER.debug("Govee MQTT raw: refusing to publish %s to topic %r — falling through", device_id, topic)
        return False

    packets = [frame_to_base64(frame) for frame in frames]
    try:
        sent = await client.async_publish_ptreal(device_id, sku, packets, topic)
    except Exception as err:  # noqa: BLE001 - the publish must never raise at an entity
        _LOGGER.debug("Govee MQTT raw: publish for %s failed (%s) — falling through", device_id, err)
        return False

    if not sent:
        return False

    _record_send(coordinator, device_id)
    _LOGGER.debug(
        "Govee MQTT raw: %s %s via ptReal passthrough (%s)",
        device_id,
        what,
        " | ".join(frame.hex(" ") for frame in frames),
    )
    return True


# ----------------------------------------------------------------------
# Coordinator internals, read in exactly one place each.
# ----------------------------------------------------------------------


def _options(coordinator: GoveeCoordinator) -> Any:
    """The config entry's options mapping (empty when there is no entry)."""
    entry = getattr(coordinator, "config_entry", None)
    return getattr(entry, "options", None) or {}


def _mqtt_client(coordinator: GoveeCoordinator) -> Any:
    """The AWS IoT client, or None when MQTT was never started."""
    return getattr(coordinator, "_mqtt_client", None)


def _mqtt_connected(coordinator: GoveeCoordinator) -> bool:
    """Upstream's own "is the MQTT channel live" predicate."""
    return bool(getattr(coordinator, "mqtt_connected", False))


async def _device_topic(coordinator: GoveeCoordinator, device_id: str) -> str | None:
    """The device's MQTT command topic, fetched on demand by upstream.

    Guarded like the other accessors here: an upstream rename must make this
    tier report "not handled", not raise at an entity.
    """
    ensure = getattr(coordinator, "_ensure_device_topic", None)
    if ensure is None:
        return None
    topic = await ensure(device_id)
    return str(topic) if topic else None


def _record_send(coordinator: GoveeCoordinator, device_id: str) -> None:
    """Stamp the MQTT transport's outbound activity, as upstream's tier does."""
    record = getattr(coordinator, "_record_transport_send", None)
    if record is not None:
        record(device_id, "mqtt")
