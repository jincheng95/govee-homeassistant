"""Plaintext BLE raw transport (fork feature, roadmap 2.1).

Why this exists
---------------
The H6046 light bar is on Govee's legacy stack: it accepts the JSON LAN API for
power/brightness/colour and **ignores raw frames over LAN entirely** (seven
envelope shapes tried, none had any effect), and it never answers the encrypted
BLE handshake. Its raw pipe — the only way to address its ten segments — is an
*unencrypted* BLE GATT link that takes the same 20-byte frames with no
handshake and no crypto (reference §6). Until this module the bar had no local
path at all beyond upstream's cloud control.

What the channel is (reference §6)
----------------------------------
* write characteristic ``…2b11``, notify ``…2b10``, under service ``…1910`` —
  the same UUIDs upstream's :mod:`.ble` already uses
* the identical 20-byte frames the LAN and MQTT tiers carry, in the clear
* plain write-with-response is accepted
* reads are answered too (14/16 queries) — not used here; this module writes

Radio, and why nothing new was plumbed
--------------------------------------
Device resolution goes through Home Assistant's Bluetooth helpers
(``async_ble_device_from_address``) and ``bleak-retry-connector``, exactly as
upstream's :mod:`.ble` does. That is what makes an **ESPHome Bluetooth proxy in
active mode** work transparently: a connectable proxy near the light bar
substitutes for a radio on the HA host, with no configuration here.

The BLE address is the **last** six octets of the Govee device id; the leading
two octets are a device-class prefix. (Until 2026-08-14 this module took the
first six, copying the assumption baked into ``ble_advertisement``'s same-SKU
tiebreaker — which only ever ran on a tie and failed by silently declining to
match, so the mistake never surfaced there. Live advertisement history proved
the trailing form; see :func:`ble_address`.) If HA has not seen that address
recently, ``async_ble_device_from_address`` returns None and this tier reports
"not handled" rather than blocking.

Connection policy
-----------------
**BLE is a one-central link: while we hold it, the Govee app cannot connect.**
So the link is opened on demand for a write burst, held for
:data:`BLE_IDLE_DISCONNECT_SECONDS` (so a burst of frames — a scene painting
several segments — reuses one connection), and then dropped. Nothing here
polls: reading state would mean holding the link, which is precisely what locks
the user's phone out (reference §2.3 / §6).

Failure contract
----------------
Every failure path returns ``False`` — "I did not handle it, do what you did
before". Nothing raises at the caller, and a failed connect never blocks: the
next tier (MQTT ptReal, then the cloud command) runs immediately.

State is optimistic, as on every raw pipe: the frames are unacknowledged and
invisible to ``devStatus``.
"""

from __future__ import annotations

import asyncio
import functools
import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Final

from homeassistant.core import CALLBACK_TYPE
from homeassistant.helpers.event import async_call_later

# Guarded exactly like the coordinator's own BLE import: an HA install without
# Bluetooth (or without bleak) must load the integration normally and simply
# never offer this tier. Every entry point checks HAS_BLUETOOTH first.
try:
    from bleak_retry_connector import (  # type: ignore[attr-defined]
        BleakClientWithServiceCache,
        BleakError,
        close_stale_connections_by_address,
        establish_connection,
    )
    from homeassistant.components import bluetooth

    from .ble import WRITE_CHARACTERISTIC_UUID

    HAS_BLUETOOTH = True
except ImportError:  # pragma: no cover - HA installs without Bluetooth
    HAS_BLUETOOTH = False
    WRITE_CHARACTERISTIC_UUID = ""
    BleakError = Exception  # type: ignore[assignment,misc]

from ..const import CONF_ENABLE_BLE_RAW_WRITE, DEFAULT_ENABLE_BLE_RAW_WRITE
from .protocol import DeviceProfile, Transport

if TYPE_CHECKING:
    from ..coordinator import GoveeCoordinator

_LOGGER = logging.getLogger(__name__)

BLE_IDLE_DISCONNECT_SECONDS: Final = 5.0
"""How long a link is held open after the last frame.

Long enough that a multi-frame burst (colour + level, or a scene painting
several segments) reuses one connection instead of reconnecting per frame;
short enough that the vendor app is locked out only for the moment a command
takes. BLE is a one-central link — holding it indefinitely would be a
user-visible regression on the phone.
"""

BLE_CONNECT_ATTEMPTS: Final = 3
"""Retries inside bleak-retry-connector before this tier gives up and falls through."""

# One link per (config entry, BLE address): two entities on the same light bar
# must share the connection, not race for the radio — but a reloaded entry must
# not inherit a link that pins its predecessor's coordinator.
_LINKS: dict[tuple[str, str], _PlaintextLink] = {}


def _entry_id(coordinator: GoveeCoordinator) -> str:
    """The config entry id owning ``coordinator`` ("" when it has none)."""
    entry = getattr(coordinator, "config_entry", None)
    return str(getattr(entry, "entry_id", "") or "")


def ble_address(device_id: str) -> str | None:
    """The BLE MAC behind a Govee device id, or None when it is not MAC-shaped.

    Govee device ids are eight colon-separated octets whose **last six** are the
    BLE address; the leading two are a device-class prefix, not part of the MAC.
    Confirmed against Home Assistant's own advertisement tracker on 2026-08-14 —
    every lamp here advertises as the trailing six and none as the leading six:

    ==========================  =====  ===================
    Device id                   SKU    Advertised address
    ==========================  =====  ===================
    ``AA:BB:AA:BB:CC:11:22:33``  H6046  ``AA:BB:CC:11:22:33``
    ``AA:BB:DD:EE:FF:44:55:66``  H60B0  ``DD:EE:FF:44:55:66``
    ``AA:BB:77:88:99:AA:BB:CC``  H6076  ``77:88:99:AA:BB:CC``
    ==========================  =====  ===================

    Args:
        device_id: The Govee device id (``AA:BB:77:88:99:AA:BB:CC``).

    Returns:
        The BLE address in upper case, or None.
    """
    parts = str(device_id or "").split(":")
    if len(parts) < 6 or not all(len(part) == 2 for part in parts[-6:]):
        return None
    return ":".join(parts[-6:]).upper()


def ble_raw_enabled(coordinator: GoveeCoordinator) -> bool:
    """Whether the user turned the plaintext BLE transport on (default off)."""
    entry = getattr(coordinator, "config_entry", None)
    options = getattr(entry, "options", None) or {}
    return bool(options.get(CONF_ENABLE_BLE_RAW_WRITE, DEFAULT_ENABLE_BLE_RAW_WRITE))


def ble_raw_target(coordinator: GoveeCoordinator, device_id: str, profile: DeviceProfile) -> str | None:
    """The BLE address a raw write to this device would use, or None.

    None means one of the gates failed: the option is off, the SKU is not on
    the plaintext stack, the device id is not MAC-shaped, HA has no Bluetooth,
    or no adapter/proxy has this address in range.

    Args:
        coordinator: The coordinator owning the device.
        device_id: The Govee device id.
        profile: The device's SKU profile.

    Returns:
        The resolvable BLE address, or None to fall through.
    """
    if not HAS_BLUETOOTH or not ble_raw_enabled(coordinator):
        return None
    if not profile.carries(Transport.BLE_PLAINTEXT):
        # The frames are the same bytes, but a SKU on the encrypted stack drops
        # them unencrypted — silently, exactly like a wrong sub-mode byte.
        return None
    address = ble_address(device_id)
    if address is None:
        return None
    if _resolve(coordinator, address) is None:
        _LOGGER.debug("Govee BLE raw: no connectable device at %s — falling through", address)
        return None
    return address


async def async_send_frames(
    coordinator: GoveeCoordinator,
    device_id: str,
    sku: str,
    profile: DeviceProfile,
    frames: Sequence[bytes],
    *,
    what: str = "frames",
) -> bool:
    """Write pre-built frames to the device's plaintext GATT characteristic.

    Args:
        coordinator: The coordinator owning the device.
        device_id: The Govee device id.
        sku: The device model (log only).
        profile: The device's SKU profile (read for the transport gate).
        frames: The frames to write, in order.
        what: Human-readable intent, for the debug log.

    Returns:
        True when every frame was written — the caller must then do nothing
        else. False means "not handled": fall through to the next tier.
    """
    if not frames:
        return False
    address = ble_raw_target(coordinator, device_id, profile)
    if address is None:
        return False

    key = (_entry_id(coordinator), address)
    link = _LINKS.get(key)
    if link is None:
        link = _LINKS[key] = _PlaintextLink(coordinator, address)

    if not await link.async_write(frames):
        return False

    _record_send(coordinator, device_id)
    _LOGGER.debug(
        "Govee BLE raw: %s (%s) %s via plaintext BLE %s (%s)",
        device_id,
        sku,
        what,
        address,
        " | ".join(frame.hex(" ") for frame in frames),
    )
    return True


async def async_disconnect_all(entry_id: str | None = None) -> None:
    """Drop held links and forget them.

    Args:
        entry_id: Drop only the links owned by this config entry (unload).
            None drops every link this integration holds.
    """
    keys = [key for key in _LINKS if entry_id is None or key[0] == entry_id]
    for key in keys:
        link = _LINKS.pop(key, None)
        if link is not None:
            await link.async_disconnect()


class _PlaintextLink:
    """One on-demand, briefly-held GATT link to a single device."""

    def __init__(self, coordinator: GoveeCoordinator, address: str) -> None:
        self._coordinator = coordinator
        self._address = address
        self._client: Any | None = None
        self._lock = asyncio.Lock()
        self._idle: CALLBACK_TYPE | None = None

    async def async_write(self, frames: Sequence[bytes]) -> bool:
        """Connect if needed, write every frame in order, then arm the idle timer.

        The idle timer is cancelled before the lock is taken, so a disconnect
        cannot fire while this write waits behind another one.
        """
        self._cancel_idle_timer()
        async with self._lock:
            try:
                client = await self._async_connect()
                for frame in frames:
                    await client.write_gatt_char(WRITE_CHARACTERISTIC_UUID, frame, response=True)
            except (BleakError, OSError, asyncio.TimeoutError, ValueError) as err:
                _LOGGER.debug("Govee BLE raw: write to %s failed (%s) — falling through", self._address, err)
                await self._async_drop_client()
                return False
            except Exception as err:  # noqa: BLE001 - never raise at an entity
                _LOGGER.debug("Govee BLE raw: unexpected error on %s (%s) — falling through", self._address, err)
                await self._async_drop_client()
                return False
            self._arm_idle_timer()
            return True

    async def _async_connect(self) -> Any:
        """Return the live client, opening the link when there is none."""
        if self._client is not None and self._client.is_connected:
            return self._client

        # Free any GATT handle a crashed HA process left behind — the single
        # highest-impact defensive call in HA's BLE integrations.
        await close_stale_connections_by_address(self._address)

        device = _resolve(self._coordinator, self._address)
        if device is None:
            raise BleakError(f"no connectable BLE device at {self._address}")

        self._client = await establish_connection(
            BleakClientWithServiceCache,
            device=device,
            name=self._address,
            disconnected_callback=self._on_disconnected,
            ble_device_callback=lambda: _resolve(self._coordinator, self._address) or device,
            max_attempts=BLE_CONNECT_ATTEMPTS,
            use_services_cache=True,
        )
        return self._client

    def _on_disconnected(self, _client: Any) -> None:
        """bleak's callback when the link drops from the other end."""
        self._client = None

    def _arm_idle_timer(self) -> None:
        """(Re)start the hold window; when it expires the link is dropped.

        ``async_call_later`` is what makes the disconnect *tracked*: Home
        Assistant owns the resulting task, so it cannot be garbage-collected
        mid-flight the way a bare ``ensure_future`` can, and it is cancelled
        with the entry.
        """
        self._cancel_idle_timer()
        self._idle = async_call_later(
            self._coordinator.hass,
            BLE_IDLE_DISCONNECT_SECONDS,
            functools.partial(self._async_idle_disconnect),
        )

    def _cancel_idle_timer(self) -> None:
        """Disarm the hold window if it is armed (idempotent)."""
        unsub, self._idle = self._idle, None
        if unsub is not None:
            unsub()

    async def _async_idle_disconnect(self, _now: Any = None) -> None:
        """``async_call_later`` target: the hold window expired."""
        self._idle = None
        await self.async_disconnect()

    async def async_disconnect(self) -> None:
        """Close the link if it is open (idempotent).

        Takes the same lock ``async_write`` holds: the idle drop and a write
        must never overlap, or the write lands on a client being torn down.
        """
        self._cancel_idle_timer()
        async with self._lock:
            await self._async_drop_client()

    async def _async_drop_client(self) -> None:
        client, self._client = self._client, None
        if client is None:
            return
        try:
            await client.disconnect()
        except Exception as err:  # noqa: BLE001 - a failed disconnect is not the caller's problem
            _LOGGER.debug("Govee BLE raw: disconnect from %s failed (%s)", self._address, err)


# ----------------------------------------------------------------------
# HA internals, read in exactly one place each.
# ----------------------------------------------------------------------


def _resolve(coordinator: GoveeCoordinator, address: str) -> Any:
    """The current ``BLEDevice`` for ``address``, via HA's Bluetooth helpers.

    Routes through whatever radio HA has — a local adapter or an ESPHome proxy
    in active mode — and returns None when nothing connectable has seen it.
    """
    if not HAS_BLUETOOTH:
        return None
    try:
        return bluetooth.async_ble_device_from_address(coordinator.hass, address, connectable=True)
    except Exception as err:  # noqa: BLE001 - a Bluetooth stack that is not set up
        _LOGGER.debug("Govee BLE raw: address lookup for %s failed (%s)", address, err)
        return None


def _record_send(coordinator: GoveeCoordinator, device_id: str) -> None:
    """Stamp the BLE transport's outbound activity, as upstream's tier does."""
    record = getattr(coordinator, "_record_transport_send", None)
    if record is not None:
        record(device_id, "ble")
