"""Write-only UDP sender for raw ``ptReal`` frames (port 4003).

**This client is deliberately write-only. Do not add reads to it.**

Two things were settled empirically during the reverse-engineering session, and
both are counter-intuitive enough that they get re-litigated every few weeks:

1. **The device ignores the source port.** It always replies to ``:4002``,
   whatever port the request came from. Binding 4002 here would therefore
   collide with the integration's existing :class:`GoveeLanClient` (and with
   any other Govee LAN tool on the host) for no gain — so this client binds
   nothing and never listens.

2. **Raw ``ptReal`` queries get no reply at all.** Eleven different ``0xAA``
   query command types were sent to all three lamps (H60B0, H6046, H6076);
   zero produced a datagram on any port. The ``aa`` frames are a BLE-era
   facility that the LAN firmware accepts and drops.

Consequently the raw path is fire-and-forget, and **reads stay with upstream's
``devStatus`` code** (``api/lan.py`` / ``api/lan_client.py``), which returns the
only four fields the LAN transport exposes: ``onOff``, ``brightness``,
``color`` and ``colorTemInKelvin``. Zone power, per-segment colour and the
active effect are readable by nothing, cloud included — consumers of this
module must track intent client-side and behave optimistically.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from .frames import encode_message, ptreal_message

LAN_RAW_COMMAND_PORT = 4003
"""Devices listen for commands here. Replies (if any) would go to :4002."""

_LOGGER = logging.getLogger(__name__)

EndpointFactory = Callable[[str, int], Awaitable["DatagramSender"]]


class DatagramSender:
    """The slice of :class:`asyncio.DatagramTransport` this client uses."""

    def sendto(self, data: bytes, addr: tuple[str, int] | None = None) -> None:  # pragma: no cover
        raise NotImplementedError

    def close(self) -> None:  # pragma: no cover
        raise NotImplementedError


async def _default_endpoint_factory(host: str, port: int) -> DatagramSender:
    """Open a short-lived unicast UDP endpoint to ``host:port``."""
    loop = asyncio.get_running_loop()
    transport, _protocol = await loop.create_datagram_endpoint(asyncio.DatagramProtocol, remote_addr=(host, port))
    return transport  # type: ignore[return-value]


class LanRawClient:
    """Send ``ptReal`` frames to a device. Never reads, never binds a port."""

    def __init__(
        self,
        *,
        port: int = LAN_RAW_COMMAND_PORT,
        endpoint_factory: EndpointFactory | None = None,
    ) -> None:
        self._port = port
        self._endpoint_factory = endpoint_factory or _default_endpoint_factory

    async def async_send_message(self, host: str, message: Mapping[str, Any]) -> None:
        """Serialise and send one LAN JSON message."""
        await self.async_send_bytes(host, encode_message(message))

    async def async_send_frames(self, host: str, frames: Sequence[bytes]) -> None:
        """Send frames in one ``ptReal`` envelope; the device applies them in order."""
        await self.async_send_message(host, ptreal_message(frames))

    async def async_send_frame(self, host: str, frame: bytes) -> None:
        await self.async_send_frames(host, [frame])

    async def async_send_bytes(self, host: str, payload: bytes) -> None:
        """Fire one datagram at ``host:4003``. No reply is expected or read."""
        sender = await self._endpoint_factory(host, self._port)
        try:
            sender.sendto(payload)
            _LOGGER.debug("lan_raw -> %s:%s %d bytes", host, self._port, len(payload))
        finally:
            sender.close()
