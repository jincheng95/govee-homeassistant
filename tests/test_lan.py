"""Tests for the read-only Govee LAN discovery helper (issue #57)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from custom_components.govee.api import lan


def _install_fake_endpoint(monkeypatch, responses, *, bind_error=None):
    """Patch socket + event loop so async_scan_lan_devices runs offline.

    Returns the MagicMock transport so tests can assert on sendto. ``responses``
    are fed to the real _ScanProtocol during the (stubbed) scan wait.
    """
    sock = MagicMock()
    if bind_error is not None:
        sock.bind.side_effect = bind_error
    monkeypatch.setattr(lan.socket, "socket", lambda *a, **k: sock)

    proto = lan._ScanProtocol()
    transport = MagicMock()

    async def _create_endpoint(_factory, sock=None):
        return transport, proto

    mock_loop = MagicMock()
    mock_loop.create_datagram_endpoint = _create_endpoint
    monkeypatch.setattr(lan.asyncio, "get_running_loop", lambda: mock_loop)

    async def _sleep(_timeout):
        for resp in responses:
            proto.datagram_received(json.dumps(resp).encode("utf-8"), ("192.168.1.50", 4002))

    monkeypatch.setattr(lan.asyncio, "sleep", _sleep)
    return transport, sock


def _scan_response(**data):
    return {"msg": {"cmd": "scan", "data": data}}


def _devstatus_response(**data):
    return {"msg": {"cmd": "devStatus", "data": data}}


def _install_fake_probe_endpoint(monkeypatch, replies, *, bind_error=None):
    """Patch socket + loop so async_probe_lan_devstatus runs offline.

    ``replies`` is a list of ``(source_ip, data_dict)`` — each is fed to the real
    _DevStatusProtocol as a devStatus packet from that source during the stubbed
    collection window. Returns the MagicMock transport for sendto assertions.
    """
    sock = MagicMock()
    if bind_error is not None:
        sock.bind.side_effect = bind_error
    monkeypatch.setattr(lan.socket, "socket", lambda *a, **k: sock)

    proto = lan._DevStatusProtocol()
    transport = MagicMock()

    async def _create_endpoint(_factory, sock=None):
        return transport, proto

    mock_loop = MagicMock()
    mock_loop.create_datagram_endpoint = _create_endpoint
    monkeypatch.setattr(lan.asyncio, "get_running_loop", lambda: mock_loop)

    async def _sleep(_timeout):
        for source_ip, data in replies:
            proto.datagram_received(
                json.dumps(_devstatus_response(**data)).encode("utf-8"),
                (source_ip, lan.LAN_RESPONSE_PORT),
            )

    monkeypatch.setattr(lan.asyncio, "sleep", _sleep)
    return transport, sock


@pytest.mark.asyncio
async def test_sends_scan_request_to_multicast(monkeypatch):
    transport, _ = _install_fake_endpoint(monkeypatch, [])

    await lan.async_scan_lan_devices(timeout=0.01)

    transport.sendto.assert_called_once()
    payload, addr = transport.sendto.call_args[0]
    assert addr == (lan.LAN_MULTICAST_GROUP, lan.LAN_DISCOVERY_PORT)
    sent = json.loads(payload.decode())
    assert sent == {"msg": {"cmd": "scan", "data": {"account_topic": "reserve"}}}


@pytest.mark.asyncio
async def test_parses_and_returns_devices(monkeypatch):
    responses = [
        _scan_response(
            ip="192.168.1.23",
            device="1F:80:C5:32:32:36:72:4E",
            sku="H6072",
            wifiVersionSoft="1.02.03",
        )
    ]
    _install_fake_endpoint(monkeypatch, responses)

    devices = await lan.async_scan_lan_devices(timeout=0.01)

    assert len(devices) == 1
    assert devices[0]["sku"] == "H6072"
    assert devices[0]["ip"] == "192.168.1.23"
    assert devices[0]["device"] == "1F:80:C5:32:32:36:72:4E"
    assert devices[0]["wifiVersionSoft"] == "1.02.03"


@pytest.mark.asyncio
async def test_dedupes_by_device_id(monkeypatch):
    dup = _scan_response(ip="192.168.1.23", device="AA:BB:CC:DD:EE:FF:00:11", sku="H6072")
    _install_fake_endpoint(monkeypatch, [dup, dup])

    devices = await lan.async_scan_lan_devices(timeout=0.01)

    assert len(devices) == 1


@pytest.mark.asyncio
async def test_no_responses_returns_empty(monkeypatch):
    _install_fake_endpoint(monkeypatch, [])

    devices = await lan.async_scan_lan_devices(timeout=0.01)

    assert devices == []


@pytest.mark.asyncio
async def test_ignores_malformed_and_non_scan(monkeypatch):
    proto = lan._ScanProtocol()
    # Garbage bytes.
    proto.datagram_received(b"not-json", ("192.168.1.9", 4002))
    # Valid JSON but a different command.
    proto.datagram_received(
        json.dumps({"msg": {"cmd": "devStatus", "data": {"onOff": 1}}}).encode(),
        ("192.168.1.9", 4002),
    )
    # scan but empty data -> no usable record.
    proto.datagram_received(json.dumps(_scan_response()).encode(), ("192.168.1.9", 4002))

    assert proto.responses == {}


@pytest.mark.asyncio
async def test_bind_failure_raises_oserror(monkeypatch):
    _install_fake_endpoint(monkeypatch, [], bind_error=OSError("port in use"))

    with pytest.raises(OSError):
        await lan.async_scan_lan_devices(timeout=0.01)


def _setsockopt_args(sock):
    """All (level, optname, value) tuples passed to the mock socket."""
    return [call.args for call in sock.setsockopt.call_args_list]


@pytest.mark.asyncio
async def test_joins_multicast_group(monkeypatch):
    # The fix for #57: device replies are multicast to 239.255.255.250:4002, so
    # the receive socket MUST join the group or the kernel drops every reply.
    _, sock = _install_fake_endpoint(monkeypatch, [])

    await lan.async_scan_lan_devices(timeout=0.01)

    joins = [
        value
        for level, name, value in _setsockopt_args(sock)
        if level == lan.socket.IPPROTO_IP and name == lan.socket.IP_ADD_MEMBERSHIP
    ]
    assert joins, "scan socket never joined the multicast group"
    # mreq = group bytes + default-interface (INADDR_ANY) bytes.
    assert joins[0] == lan._GROUP_BYTES + lan.socket.inet_aton("0.0.0.0")


@pytest.mark.asyncio
async def test_sets_reuse_and_ttl_options(monkeypatch):
    _, sock = _install_fake_endpoint(monkeypatch, [])

    await lan.async_scan_lan_devices(timeout=0.01)

    names = [name for _level, name, _value in _setsockopt_args(sock)]
    assert lan.socket.SO_REUSEADDR in names
    assert lan.socket.IP_MULTICAST_TTL in names


@pytest.mark.asyncio
async def test_multi_interface_joins_and_sends_per_nic(monkeypatch):
    transport, sock = _install_fake_endpoint(monkeypatch, [])

    await lan.async_scan_lan_devices(timeout=0.01, interface_ips=["192.168.1.50", "10.0.0.5"])

    # One scan emitted per interface (no extra default send when NICs given).
    assert transport.sendto.call_count == 2
    addrs = {call.args[1] for call in transport.sendto.call_args_list}
    assert addrs == {(lan.LAN_MULTICAST_GROUP, lan.LAN_DISCOVERY_PORT)}

    # Group joined on each interface plus the default-route catch-all.
    joins = {value for _level, name, value in _setsockopt_args(sock) if name == lan.socket.IP_ADD_MEMBERSHIP}
    assert joins == {lan._GROUP_BYTES + lan.socket.inet_aton(ip) for ip in ("192.168.1.50", "10.0.0.5", "0.0.0.0")}


@pytest.mark.asyncio
async def test_fills_ip_from_source_when_missing(monkeypatch):
    # A response that omits "ip" gets the datagram source address (192.168.1.50).
    resp = _scan_response(device="AA:BB:CC:DD:EE:FF:00:11", sku="H6072")
    _install_fake_endpoint(monkeypatch, [resp])

    devices = await lan.async_scan_lan_devices(timeout=0.01)

    assert devices[0]["ip"] == "192.168.1.50"


@pytest.mark.asyncio
async def test_leaves_group_on_teardown(monkeypatch):
    _, sock = _install_fake_endpoint(monkeypatch, [])

    await lan.async_scan_lan_devices(timeout=0.01)

    drops = [name for _level, name, _value in _setsockopt_args(sock) if name == lan.socket.IP_DROP_MEMBERSHIP]
    assert drops, "scan socket never left the multicast group on teardown"


@pytest.mark.asyncio
async def test_sets_broadcast_option(monkeypatch):
    # SO_BROADCAST lets a configured x.x.x.255 LAN target be reached (#57).
    _, sock = _install_fake_endpoint(monkeypatch, [])

    await lan.async_scan_lan_devices(timeout=0.01)

    names = [name for _level, name, _value in _setsockopt_args(sock)]
    assert lan.socket.SO_BROADCAST in names


@pytest.mark.asyncio
async def test_sends_scan_to_extra_targets(monkeypatch):
    transport, _ = _install_fake_endpoint(monkeypatch, [])

    await lan.async_scan_lan_devices(timeout=0.01, extra_targets=["10.20.0.51", "10.20.0.255"])

    # One multicast (default interface) plus one send per explicit target.
    addrs = [call.args[1] for call in transport.sendto.call_args_list]
    assert (lan.LAN_MULTICAST_GROUP, lan.LAN_DISCOVERY_PORT) in addrs
    assert ("10.20.0.51", lan.LAN_DISCOVERY_PORT) in addrs
    assert ("10.20.0.255", lan.LAN_DISCOVERY_PORT) in addrs
    assert len(addrs) == 3


# --- devStatus probe: _DevStatusProtocol parsing ---------------------------


def test_devstatus_parses_and_keys_by_source_ip():
    proto = lan._DevStatusProtocol()
    proto.datagram_received(
        json.dumps(_devstatus_response(onOff=1, brightness=80)).encode(),
        ("192.168.1.23", 4002),
    )
    proto.datagram_received(
        json.dumps(_devstatus_response(onOff=0)).encode(),
        ("192.168.1.24", 4002),
    )

    assert set(proto.responses) == {"192.168.1.23", "192.168.1.24"}
    assert proto.responses["192.168.1.23"]["brightness"] == 80
    assert proto.responses["192.168.1.24"]["onOff"] == 0


def test_devstatus_captures_whole_data_dict_no_allowlist():
    # The probe's purpose is discovery: an unknown field MUST be preserved.
    proto = lan._DevStatusProtocol()
    proto.datagram_received(
        json.dumps(
            _devstatus_response(
                onOff=1,
                brightness=100,
                color={"r": 1, "g": 2, "b": 3},
                colorTemInKelvin=4000,
                mysteryField=7,
            )
        ).encode(),
        ("192.168.1.23", 4002),
    )

    body = proto.responses["192.168.1.23"]
    assert body["mysteryField"] == 7
    assert body["color"] == {"r": 1, "g": 2, "b": 3}
    assert body["colorTemInKelvin"] == 4000


def test_devstatus_ignores_scan_and_malformed():
    proto = lan._DevStatusProtocol()
    # A scan reply (wrong cmd) is dropped.
    proto.datagram_received(json.dumps(_scan_response(sku="H6072")).encode(), ("192.168.1.9", 4002))
    # Garbage bytes.
    proto.datagram_received(b"not-json", ("192.168.1.9", 4002))
    # devStatus but data is a list, not a dict.
    proto.datagram_received(
        json.dumps({"msg": {"cmd": "devStatus", "data": []}}).encode(),
        ("192.168.1.9", 4002),
    )

    assert proto.responses == {}


def test_devstatus_last_reply_wins_per_ip():
    proto = lan._DevStatusProtocol()
    proto.datagram_received(json.dumps(_devstatus_response(brightness=10)).encode(), ("192.168.1.23", 4002))
    proto.datagram_received(json.dumps(_devstatus_response(brightness=90)).encode(), ("192.168.1.23", 4002))

    assert len(proto.responses) == 1
    assert proto.responses["192.168.1.23"]["brightness"] == 90


# --- devStatus probe: async_probe_lan_devstatus ----------------------------


@pytest.mark.asyncio
async def test_probe_sends_devstatus_to_each_ip_on_4003(monkeypatch):
    transport, _ = _install_fake_probe_endpoint(monkeypatch, [])

    await lan.async_probe_lan_devstatus(["10.0.0.5", "10.0.0.6"], timeout=0.01)

    addrs = [call.args[1] for call in transport.sendto.call_args_list]
    assert addrs == [("10.0.0.5", lan.LAN_COMMAND_PORT), ("10.0.0.6", lan.LAN_COMMAND_PORT)]
    payload = transport.sendto.call_args_list[0].args[0]
    assert json.loads(payload.decode()) == {"msg": {"cmd": "devStatus", "data": {}}}


@pytest.mark.asyncio
async def test_probe_returns_responses_keyed_by_ip(monkeypatch):
    _install_fake_probe_endpoint(
        monkeypatch,
        [("10.0.0.5", {"onOff": 1, "brightness": 42})],
    )

    result = await lan.async_probe_lan_devstatus(["10.0.0.5"], timeout=0.01)

    assert result == {"10.0.0.5": {"onOff": 1, "brightness": 42}}


@pytest.mark.asyncio
async def test_probe_empty_ips_returns_empty_no_socket(monkeypatch):
    called = MagicMock()
    monkeypatch.setattr(lan.socket, "socket", called)

    result = await lan.async_probe_lan_devstatus([], timeout=0.01)

    assert result == {}
    called.assert_not_called()


@pytest.mark.asyncio
async def test_probe_caps_device_count(monkeypatch):
    transport, _ = _install_fake_probe_endpoint(monkeypatch, [])
    ips = [f"10.0.0.{i}" for i in range(lan.LAN_PROBE_MAX_DEVICES + 10)]

    await lan.async_probe_lan_devstatus(ips, timeout=0.01)

    assert transport.sendto.call_count == lan.LAN_PROBE_MAX_DEVICES


@pytest.mark.asyncio
async def test_probe_bind_failure_raises_oserror(monkeypatch):
    _install_fake_probe_endpoint(monkeypatch, [], bind_error=OSError("port in use"))

    with pytest.raises(OSError):
        await lan.async_probe_lan_devstatus(["10.0.0.5"], timeout=0.01)


@pytest.mark.asyncio
async def test_probe_one_send_failure_does_not_abort_batch(monkeypatch):
    transport, _ = _install_fake_probe_endpoint(monkeypatch, [("10.0.0.6", {"onOff": 1})])
    transport.sendto.side_effect = [OSError("unreachable"), None]

    result = await lan.async_probe_lan_devstatus(["10.0.0.5", "10.0.0.6"], timeout=0.01)

    # First send raised, second still attempted; the reply is still collected.
    assert transport.sendto.call_count == 2
    assert result == {"10.0.0.6": {"onOff": 1}}


@pytest.mark.asyncio
async def test_probe_joins_multicast_group(monkeypatch):
    # devStatus replies can arrive multicast, so the probe socket must join the
    # group too (same #57 fix as the scan path).
    _, sock = _install_fake_probe_endpoint(monkeypatch, [])

    await lan.async_probe_lan_devstatus(["10.0.0.5"], timeout=0.01, interface_ips=["192.168.1.50"])

    joins = [
        value
        for level, name, value in _setsockopt_args(sock)
        if level == lan.socket.IPPROTO_IP and name == lan.socket.IP_ADD_MEMBERSHIP
    ]
    assert joins, "probe socket never joined the multicast group"


# --- expand_lan_targets ----------------------------------------------------


@pytest.mark.parametrize("raw", ["", None, "   ", "\n,\n"])
def test_expand_lan_targets_empty(raw):
    assert lan.expand_lan_targets(raw) == []


def test_expand_lan_targets_single_ip():
    assert lan.expand_lan_targets("10.20.0.51") == ["10.20.0.51"]


def test_expand_lan_targets_mixed_separators_and_dedupe():
    raw = "10.20.0.5,10.20.0.6 10.20.0.7\n10.20.0.5"
    assert lan.expand_lan_targets(raw) == [
        "10.20.0.5",
        "10.20.0.6",
        "10.20.0.7",
    ]


def test_expand_lan_targets_small_cidr_sweeps_hosts_plus_broadcast():
    # /30 -> usable hosts .1/.2 plus the .3 broadcast.
    assert lan.expand_lan_targets("10.20.0.0/30") == [
        "10.20.0.1",
        "10.20.0.2",
        "10.20.0.3",
    ]


def test_expand_lan_targets_24_is_allowed():
    targets = lan.expand_lan_targets("10.20.0.0/24")
    assert len(targets) == 255  # 254 hosts + broadcast
    assert targets[0] == "10.20.0.1"
    assert targets[-1] == "10.20.0.255"


def test_expand_lan_targets_rejects_large_subnet():
    with pytest.raises(lan.LanTargetError):
        lan.expand_lan_targets("10.20.0.0/16")


def test_expand_lan_targets_rejects_garbage():
    with pytest.raises(lan.LanTargetError):
        lan.expand_lan_targets("not-an-ip")
