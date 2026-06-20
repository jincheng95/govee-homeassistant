"""Read-only Govee LAN (UDP) observation helper.

Govee exposes a local UDP control API (must be toggled on per device in the
Govee app) on a subset of mostly-light SKUs. This module performs ONLY read-only
probes so a user can capture which of their devices answer on the LAN and what
state they report, and attach it to a diagnostics download. That community data
is the prerequisite for the full LAN transport requested in issue #57 (the
maintainer has no LAN hardware to test against).

Two probes, both safe to attach to a diagnostics download:

- ``async_scan_lan_devices`` — one bounded multicast ``scan`` (discovery): which
  devices answer and their identity/firmware metadata.
- ``async_probe_lan_devstatus`` — a unicast ``devStatus`` query per discovered
  device, capturing its full runtime reply so we can measure empirically how
  much state the LAN API actually exposes. Verified against
  ``Galorhallen/govee-local-api`` and ``wez/govee2mqtt``, a ``devStatus`` reply
  carries exactly four runtime fields — ``onOff``, ``brightness``, ``color`` and
  ``colorTemInKelvin`` — but we capture the whole ``data`` dict so a firmware
  that returns more is not silently discarded (the entire point is discovery).

``ptReal`` (the BLE-over-WiFi passthrough that drives scenes/segments/music) is
deliberately NOT probed: both reference libraries send it fire-and-forget with
no response to read back, and emitting one is a state-changing control write —
forbidden in this read-only module. So scene/segment/music/sensor state is
simply not readable over the LAN API; only the four ``devStatus`` fields and the
discovery metadata are.

Deliberately scoped: no control writes, no entities, no persistent socket — each
call opens a socket, collects responses for a short timeout, and returns them.
Protocol per ``docs/govee-protocol-reference.md`` §6:

- Scan request    -> 239.255.255.250:4001  ``{"msg":{"cmd":"scan",...}}``
- Scan response   -> 239.255.255.250:4002  ``{"msg":{"cmd":"scan","data":{...}}}``
- devStatus query -> <device-ip>:4003      ``{"msg":{"cmd":"devStatus","data":{}}}``
- devStatus reply -> our :4002 (unicast OR multicast, firmware-dependent)

Critical protocol detail (the reason early builds returned zero devices, issue
#57): a Govee device sends its scan *response* as **multicast** to the group on
port 4002 — it does NOT unicast the reply back to the sender. So the receive
socket MUST join the ``239.255.255.250`` group via ``IP_ADD_MEMBERSHIP`` or the
kernel silently drops every reply before it reaches us. Binding port 4002 alone
is not enough. The devStatus probe reuses the same group-joined 4002 socket so
it catches replies whether a given firmware answers unicast or multicast. This
mirrors ``govee-local-api`` (the library behind Home Assistant's
``govee_light_local``) and ``wez/govee2mqtt``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
from ipaddress import AddressValueError, IPv4Address, IPv4Network
from typing import Any

_LOGGER = logging.getLogger(__name__)

# A LAN-target CIDR may expand to at most this many addresses (a /24). Larger
# subnets would unicast-sweep thousands of hosts per scan and are rejected by
# ``expand_lan_targets``. Kept here so the parser is self-contained/testable.
MAX_LAN_TARGET_ADDRESSES = 256

# Govee LAN API network parameters (docs/govee-protocol-reference.md §6).
LAN_MULTICAST_GROUP = "239.255.255.250"
LAN_DISCOVERY_PORT = 4001  # devices listen here for the scan request
LAN_RESPONSE_PORT = 4002  # devices multicast scan responses here; we listen
LAN_COMMAND_PORT = 4003  # devices listen here for unicast devStatus/control
LAN_MULTICAST_TTL = 2  # let a scan / reply cross at most one router hop

# devStatus probe budget. All probes share ONE socket and ONE collection window
# (sends are fire-and-forget; replies arrive asynchronously), so total wall time
# is bounded by the window regardless of device_count — 11 devices cost the same
# ~2s as one. The cap bounds send-loop work against a large CIDR sweep, not wait.
LAN_PROBE_WINDOW = 2.0  # seconds to collect all devStatus replies
LAN_PROBE_MAX_DEVICES = 64  # hard cap on how many IPs we probe in one batch

# INADDR_ANY: join/egress on the kernel's default-route interface. Always added
# alongside any explicit interface IPs as a catch-all for single-NIC hosts.
_DEFAULT_INTERFACE = "0.0.0.0"

_SCAN_REQUEST = json.dumps({"msg": {"cmd": "scan", "data": {"account_topic": "reserve"}}}).encode("utf-8")

# Empty-data devStatus query; matches DevStatusMessage in govee-local-api and
# Request::DevStatus{} in wez/govee2mqtt. Sent unicast to <device-ip>:4003.
_DEVSTATUS_REQUEST = json.dumps({"msg": {"cmd": "devStatus", "data": {}}}).encode("utf-8")

# Packed multicast group address, reused for every IP_ADD/DROP_MEMBERSHIP call.
_GROUP_BYTES = socket.inet_aton(LAN_MULTICAST_GROUP)

# Fields a scan response may carry; we surface exactly these (no control data).
_RESPONSE_FIELDS = (
    "ip",
    "device",
    "sku",
    "bleVersionHard",
    "bleVersionSoft",
    "wifiVersionHard",
    "wifiVersionSoft",
)


class LanTargetError(ValueError):
    """A configured LAN-target entry is not a valid IP / subnet (issue #57)."""


def expand_lan_targets(raw: str | None) -> list[str]:
    """Parse the user's LAN-targets option into concrete scan addresses.

    ``raw`` is a free-text list (comma / newline / whitespace separated). Each
    token is one of:

    - a device IP (``10.20.0.51``)        -> that address, unicast
    - a broadcast address (``10.20.0.255``) -> that address, broadcast
    - a CIDR subnet (``10.20.0.0/24``)    -> every host address plus the subnet
      broadcast, so cross-VLAN devices are reached by unicast sweep (inter-VLAN
      firewalls usually drop a single directed broadcast)

    Returns a de-duplicated, order-preserving list of IPv4 address strings.
    Raises ``LanTargetError`` on an unparseable token or a subnet wider than
    ``MAX_LAN_TARGET_ADDRESSES`` (a /24), so the options flow can reject it.
    """
    if not raw:
        return []

    targets: list[str] = []
    seen: set[str] = set()

    def _add(address: str) -> None:
        if address not in seen:
            seen.add(address)
            targets.append(address)

    for token in raw.replace(",", " ").split():
        try:
            if "/" in token:
                network = IPv4Network(token, strict=False)
                if network.num_addresses > MAX_LAN_TARGET_ADDRESSES:
                    raise LanTargetError(
                        f"Subnet {token} is larger than /24 — list device IPs " "or a /24 (or smaller) subnet instead."
                    )
                for host in network.hosts():
                    _add(str(host))
                # The broadcast address covers flat same-VLAN networks cheaply.
                if network.num_addresses > 1:
                    _add(str(network.broadcast_address))
            else:
                _add(str(IPv4Address(token)))
        except LanTargetError:
            raise
        except (AddressValueError, ValueError) as err:
            raise LanTargetError(f"'{token}' is not a valid IP or subnet") from err

    return targets


class _ScanProtocol(asyncio.DatagramProtocol):
    """Collects well-formed Govee ``scan`` responses; ignores everything else."""

    def __init__(self) -> None:
        self.responses: dict[str, dict[str, Any]] = {}

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        try:
            payload = json.loads(data.decode("utf-8", errors="replace"))
            msg = payload.get("msg", {})
            if msg.get("cmd") != "scan":
                return
            body = msg.get("data", {})
            if not isinstance(body, dict):
                return
        except (ValueError, AttributeError):
            return

        record = {field: body[field] for field in _RESPONSE_FIELDS if field in body}
        if not record:
            return
        # Some firmwares omit "ip" in the body; fall back to the datagram source
        # address so every responder has a usable address (redacted downstream).
        record.setdefault("ip", addr[0])
        # Dedupe by the device id (MAC) when present, else by IP.
        key = str(record.get("device") or record["ip"])
        self.responses[key] = record

    def error_received(self, exc: Exception) -> None:  # pragma: no cover - rare
        _LOGGER.debug("LAN scan socket error: %s", exc)


class _DevStatusProtocol(asyncio.DatagramProtocol):
    """Collects raw Govee ``devStatus`` replies, keyed by responder IP.

    Separate from ``_ScanProtocol`` because that one hard-drops ``cmd != "scan"``.
    Captures the ENTIRE ``data`` dict (no field allowlist) — the purpose of the
    probe is to discover what firmware actually returns, so an allowlist would
    throw away exactly the signal we want. Redaction happens downstream in
    diagnostics ``_redact`` (key-name based: any ``ip``/``device``/``mac`` key a
    firmware echoes inside ``data`` is auto-redacted there).
    """

    def __init__(self) -> None:
        self.responses: dict[str, dict[str, Any]] = {}

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        try:
            payload = json.loads(data.decode("utf-8", errors="replace"))
            msg = payload.get("msg", {})
            if msg.get("cmd") != "devStatus":
                return  # ignore scan replies / unrelated multicast noise
            body = msg.get("data", {})
            if not isinstance(body, dict):
                return
        except (ValueError, AttributeError):
            return
        # Key by the datagram SOURCE IP — correct for both reply paths: a
        # unicast reply to our 4002 source and a multicast reply to the group
        # both carry the device's own IP as the UDP source. Last reply wins.
        self.responses[addr[0]] = body

    def error_received(self, exc: Exception) -> None:  # pragma: no cover - rare
        _LOGGER.debug("LAN devStatus socket error: %s", exc)


def _build_socket() -> socket.socket:
    """Create the bound UDP receive socket for the scan (raises ``OSError``).

    Binds the wildcard address on port 4002 so multicast replies are accepted
    regardless of which interface they arrive on, with ``SO_REUSEADDR`` /
    ``SO_REUSEPORT`` so we can co-bind alongside other LAN integrations
    (``govee_light_local``, the official Govee LAN app) already holding 4002.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    # SO_BROADCAST so a configured x.x.x.255 LAN target can be reached (#57).
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    reuse_port = getattr(socket, "SO_REUSEPORT", None)
    if reuse_port is not None:
        try:
            sock.setsockopt(socket.SOL_SOCKET, reuse_port, 1)
        except OSError:  # pragma: no cover - platform without SO_REUSEPORT support
            pass
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, LAN_MULTICAST_TTL)
    sock.setblocking(False)
    try:
        sock.bind(("", LAN_RESPONSE_PORT))
    except OSError:
        sock.close()
        raise
    return sock


def _join_group(sock: socket.socket, interfaces: list[str]) -> list[str]:
    """Join ``239.255.255.250`` on every interface; return those that succeeded.

    Joins per interface IP (plus the default route) so a multi-homed host
    receives the multicast replies on whichever adapter the devices live on.
    Without at least one successful join the kernel drops every reply (#57).
    """
    joined: list[str] = []
    for iface in [*interfaces, _DEFAULT_INTERFACE]:
        if iface in joined:
            continue
        try:
            mreq = _GROUP_BYTES + socket.inet_aton(iface)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            joined.append(iface)
        except OSError as err:  # interface gone / already joined — skip it
            _LOGGER.debug("LAN scan: group join on %s failed: %s", iface, err)
    return joined


def _drop_group(sock: socket.socket, joined: list[str]) -> None:
    """Leave the multicast group on every interface we joined (best effort)."""
    for iface in joined:
        try:
            mreq = _GROUP_BYTES + socket.inet_aton(iface)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_DROP_MEMBERSHIP, mreq)
        except OSError:  # pragma: no cover - teardown best effort
            pass


def _send_scan(
    sock: socket.socket,
    transport: asyncio.DatagramTransport,
    interfaces: list[str],
) -> None:
    """Emit the scan out each interface so devices on every subnet receive it.

    ``IP_MULTICAST_IF`` selects the egress adapter per send; with no explicit
    interfaces we fall back to the default route.
    """
    for iface in interfaces or [_DEFAULT_INTERFACE]:
        try:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(iface))
        except OSError as err:  # pragma: no cover - bad interface, try the send anyway
            _LOGGER.debug("LAN scan: egress select on %s failed: %s", iface, err)
        transport.sendto(_SCAN_REQUEST, (LAN_MULTICAST_GROUP, LAN_DISCOVERY_PORT))


def _send_targets(
    transport: asyncio.DatagramTransport,
    targets: list[str],
) -> None:
    """Unicast / broadcast the scan to explicit targets (other subnets/VLANs).

    Devices reply unicast to our 4002 source, which routes back across the VLAN
    boundary that the local multicast scan cannot cross (issue #57).
    """
    for target in targets:
        transport.sendto(_SCAN_REQUEST, (target, LAN_DISCOVERY_PORT))


async def async_scan_lan_devices(
    timeout: float = 2.0,
    interface_ips: list[str] | None = None,
    extra_targets: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Send one multicast ``scan`` and collect responses for ``timeout`` seconds.

    Returns a list of per-device dicts (deduped) limited to the discovery fields
    above — no control surface is touched. Uses asyncio datagram endpoints so it
    is safe to run on the Home Assistant event loop.

    ``interface_ips`` are the host's enabled LAN source IPs (from HA's network
    helper). The group is joined and the scan emitted on each, plus the default
    route, so multi-homed / multi-VLAN hosts are covered. When omitted, only the
    default-route interface is used.

    ``extra_targets`` are explicit unicast / broadcast addresses (from the user's
    LAN-targets option via ``expand_lan_targets``) for devices the local
    multicast cannot reach — e.g. on another VLAN. The scan is sent to each in
    addition to the multicast; replies return unicast to our 4002 source.

    Raises ``OSError`` if the response socket cannot be bound (e.g. port 4002 in
    use by another local-control app that does not share the port); callers
    should treat that as "no data".
    """
    loop = asyncio.get_running_loop()
    interfaces = list(interface_ips or [])
    targets = list(extra_targets or [])

    sock = _build_socket()  # raises OSError if port 4002 cannot be bound
    joined = _join_group(sock, interfaces)

    transport, protocol = await loop.create_datagram_endpoint(_ScanProtocol, sock=sock)
    assert isinstance(protocol, _ScanProtocol)
    try:
        _send_scan(sock, transport, interfaces)
        _send_targets(transport, targets)
        await asyncio.sleep(timeout)
    finally:
        _drop_group(sock, joined)
        transport.close()

    return list(protocol.responses.values())


async def async_probe_lan_devstatus(
    ips: list[str],
    timeout: float = LAN_PROBE_WINDOW,
    interface_ips: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Unicast ``devStatus`` to each IP and collect raw replies for ``timeout`` s.

    Returns ``{responder_ip: raw_data_dict}`` capturing the WHOLE reply body for
    each device that answers — the probe exists to measure the real LAN data
    surface, so no field allowlist is applied here (redaction is downstream in
    diagnostics). A device that discovers but does not answer ``devStatus`` (LAN
    control disabled in the app, BLE-only SKU) simply has no entry — the caller
    treats a missing IP as "no status".

    Sends are fire-and-forget to ``<ip>:4003``; replies may return unicast to our
    4002 source OR multicast to ``239.255.255.250:4002`` depending on firmware,
    so we reuse the scan socket pattern (bound 4002 + group-joined) to catch
    both. All probes share one socket and one collection window, so total wall
    time is bounded by ``timeout`` regardless of device count. ``ips`` is capped
    at ``LAN_PROBE_MAX_DEVICES`` so a large ``extra_targets`` sweep cannot blow up
    the send loop.

    ``interface_ips`` join the multicast group on each adapter (multi-homed
    coverage), mirroring ``async_scan_lan_devices``.

    Raises ``OSError`` if the response socket cannot be bound (port 4002 held by
    a non-sharing local-control app); callers should treat that as "no data",
    the same contract as ``async_scan_lan_devices``.
    """
    if not ips:
        return {}

    interfaces = list(interface_ips or [])
    targets = ips[:LAN_PROBE_MAX_DEVICES]

    loop = asyncio.get_running_loop()
    sock = _build_socket()  # raises OSError if port 4002 cannot be bound
    joined = _join_group(sock, interfaces)  # catch multicast replies too

    transport, protocol = await loop.create_datagram_endpoint(_DevStatusProtocol, sock=sock)
    assert isinstance(protocol, _DevStatusProtocol)
    try:
        for ip in targets:
            try:
                transport.sendto(_DEVSTATUS_REQUEST, (ip, LAN_COMMAND_PORT))
            except OSError as err:  # one bad/unreachable IP must not abort the batch
                _LOGGER.debug("LAN probe: send to %s failed: %s", ip, err)
        await asyncio.sleep(timeout)
    finally:
        _drop_group(sock, joined)
        transport.close()

    return dict(protocol.responses)
