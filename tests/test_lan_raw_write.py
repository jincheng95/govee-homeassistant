"""Tests for the raw-LAN fast path for zone power toggles (fork feature).

The H60B0's ripple / side / bottom switches can be driven by a single UDP frame
instead of a 1-3 second cloud round trip. These tests drive the real
:class:`GoveeNamedLightSwitchEntity` with a recording stand-in for the
write-only UDP client, so no sockets and no cloud are involved:

1. Frames — exact bytes per zone, on and off, cross-checked against the zone
   bytes in the profile table.
2. Gates — option off, device not on the LAN, SKU with no zone-power profile,
   toggle instance that is not a zone. Each falls through to the cloud path
   with behaviour unchanged.
3. Delivery — the idempotent frame goes out :data:`LAN_WRITE_REPEATS` times to
   the correlated IP, and a send failure falls back rather than lying.
4. State — optimistic, applied without waiting for any echo, including the
   zone the hardware displaces under the profile's ``MaxSimultaneousZones``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.govee import lan_udp_health
from custom_components.govee.api import lan_raw_write
from custom_components.govee.api.lan_client import LanDeviceInfo
from custom_components.govee.api.protocol import GoveeProtocolError, get_profile
from custom_components.govee.const import (
    CONF_ENABLE_LAN_RAW_WRITE,
    SUFFIX_BOTTOM_LIGHT,
    SUFFIX_RIPPLE_LIGHT,
    SUFFIX_SIDE_LIGHT,
)
from custom_components.govee.models import GoveeCapability, GoveeDevice
from custom_components.govee.models.device import (
    CAPABILITY_COLOR_SETTING,
    CAPABILITY_ON_OFF,
    CAPABILITY_TOGGLE,
    DEVICE_TYPE_LIGHT,
    INSTANCE_COLOR_RGB,
    INSTANCE_POWER,
)
from custom_components.govee.switch import GoveeNamedLightSwitchEntity
from custom_components.govee.zone_state import register_zone_switch

DEVICE_ID = "AA:BB:CC:DD:EE:FF:60:B0"
IP = "10.20.0.51"

# Golden frames: 33 30 <zone> <00|01>, zero-padded to 19 bytes, XOR checksum.
# The zone bytes are asserted against the profile table separately below, so a
# table edit that changed them would fail loudly instead of silently repainting
# the expectation.
GOLDEN = {
    ("rippleLightToggle", True): "33300101" + "00" * 15 + "03",
    ("rippleLightToggle", False): "33300100" + "00" * 15 + "02",
    ("sideLightToggle", True): "33300201" + "00" * 15 + "00",
    ("sideLightToggle", False): "33300200" + "00" * 15 + "01",
    ("bottomLightToggle", True): "33300301" + "00" * 15 + "01",
    ("bottomLightToggle", False): "33300300" + "00" * 15 + "00",
}

TOGGLE_SUFFIXES = {
    "rippleLightToggle": SUFFIX_RIPPLE_LIGHT,
    "sideLightToggle": SUFFIX_SIDE_LIGHT,
    "bottomLightToggle": SUFFIX_BOTTOM_LIGHT,
}


class _FakeRawClient:
    """Records datagrams instead of sending them."""

    def __init__(self, error: Exception | None = None) -> None:
        self.sends: list[tuple[str, bytes]] = []
        self.error = error

    async def async_send_frames(self, host: str, frames: list[bytes]) -> None:
        for frame in frames:
            self.sends.append((host, frame))
        if self.error is not None:
            raise self.error

    async def async_send_frame(self, host: str, frame: bytes) -> None:
        await self.async_send_frames(host, [frame])


class _FlakyRawClient(_FakeRawClient):
    """Accepts the first ``fail_after`` copies of a burst, then raises OSError."""

    def __init__(self, *, fail_after: int) -> None:
        super().__init__()
        self._fail_after = fail_after
        self._calls = 0

    async def async_send_frames(self, host: str, frames: list[bytes]) -> None:
        self._calls += 1
        if self._calls > self._fail_after:
            raise OSError("network unreachable")
        for frame in frames:
            self.sends.append((host, frame))


def _cap(cap_type: str, instance: str) -> GoveeCapability:
    return GoveeCapability(type=cap_type, instance=instance, parameters={})


def _device(sku: str = "H60B0") -> GoveeDevice:
    return GoveeDevice(
        device_id=DEVICE_ID,
        sku=sku,
        name="Uplighter Floor Lamp",
        device_type=DEVICE_TYPE_LIGHT,
        capabilities=(
            _cap(CAPABILITY_ON_OFF, INSTANCE_POWER),
            _cap(CAPABILITY_COLOR_SETTING, INSTANCE_COLOR_RGB),
            _cap(CAPABILITY_TOGGLE, "rippleLightToggle"),
            _cap(CAPABILITY_TOGGLE, "sideLightToggle"),
            _cap(CAPABILITY_TOGGLE, "bottomLightToggle"),
        ),
    )


def _coordinator(*, enabled: bool = True, on_lan: bool = True, sku: str = "H60B0") -> Any:
    coordinator = MagicMock()
    # A real GoveeCoordinator has no registry attribute until zone_state builds
    # one; MagicMock would otherwise auto-create a Mock and swallow the lookup.
    coordinator._govee_zone_state_registry = None
    coordinator.config_entry.options = {CONF_ENABLE_LAN_RAW_WRITE: enabled}
    coordinator.async_control_device = AsyncMock(return_value=True)
    coordinator._lan_devices = {}
    if on_lan:
        coordinator._lan_devices[DEVICE_ID] = LanDeviceInfo(
            device_id=DEVICE_ID,
            ip=IP,
            mac=DEVICE_ID,
            sku=sku,
            firmware="1.0.0",
            last_correlated_ts=0.0,
        )
    return coordinator


def _entity(coordinator: Any, instance: str, device: GoveeDevice | None = None) -> GoveeNamedLightSwitchEntity:
    entity = GoveeNamedLightSwitchEntity(
        coordinator,
        device or _device(),
        instance,
        "govee_test_light",
        TOGGLE_SUFFIXES.get(instance, "_x"),
        "mdi:shimmer",
    )
    entity.async_write_ha_state = MagicMock()
    return entity


@pytest.fixture(autouse=True)
def _fast_client(monkeypatch: pytest.MonkeyPatch) -> _FakeRawClient:
    """Recording client with the inter-repeat gap taken off the wall clock."""
    client = _FakeRawClient()
    monkeypatch.setattr(lan_raw_write, "_CLIENT", client)
    monkeypatch.setattr(lan_raw_write, "LAN_WRITE_GAP_SECONDS", 0)
    return client


# ==============================================================================
# 1. Frames
# ==============================================================================


class TestFrames:
    """The bytes on the wire, per zone."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("instance,on", sorted(GOLDEN))
    async def test_golden_frame_per_zone(self, _fast_client, instance, on):
        entity = _entity(_coordinator(), instance)

        assert await lan_raw_write.async_zone_power(entity, on=on) is True

        hosts = {host for host, _frame in _fast_client.sends}
        frames = {frame.hex() for _host, frame in _fast_client.sends}
        assert hosts == {IP}
        assert frames == {GOLDEN[(instance, on)]}

    @pytest.mark.parametrize("instance,zone_key", sorted(lan_raw_write.ZONE_KEY_BY_TOGGLE.items()))
    def test_frame_zone_byte_comes_from_the_profile(self, instance, zone_key):
        zone_byte = get_profile("H60B0").zone(zone_key).zone_byte
        assert bytes.fromhex(GOLDEN[(instance, True)])[2] == zone_byte


# ==============================================================================
# 2. Gates — every reason to leave the cloud path alone
# ==============================================================================


class TestGates:
    """Every reason a raw write is refused, each falling back to the cloud."""

    @pytest.mark.asyncio
    async def test_option_off_is_a_noop(self, _fast_client):
        entity = _entity(_coordinator(enabled=False), "rippleLightToggle")

        assert await lan_raw_write.async_zone_power(entity, on=True) is False
        assert _fast_client.sends == []

    @pytest.mark.asyncio
    async def test_option_defaults_off(self, _fast_client):
        coordinator = _coordinator()
        coordinator.config_entry.options = {}
        entity = _entity(coordinator, "rippleLightToggle")

        assert await lan_raw_write.async_zone_power(entity, on=True) is False
        assert _fast_client.sends == []

    @pytest.mark.asyncio
    async def test_device_not_on_lan_falls_back(self, _fast_client):
        entity = _entity(_coordinator(on_lan=False), "rippleLightToggle")

        assert await lan_raw_write.async_zone_power(entity, on=True) is False
        assert _fast_client.sends == []

    @pytest.mark.asyncio
    async def test_sku_without_zone_power_falls_back(self, _fast_client):
        # The H6046 has a raw-LAN profile but no zone-power capability.
        entity = _entity(_coordinator(), "rippleLightToggle", _device(sku="H6046"))

        assert await lan_raw_write.async_zone_power(entity, on=True) is False
        assert _fast_client.sends == []

    @pytest.mark.asyncio
    async def test_sku_without_any_profile_falls_back(self, _fast_client):
        entity = _entity(_coordinator(), "rippleLightToggle", _device(sku="H1310"))

        assert await lan_raw_write.async_zone_power(entity, on=True) is False
        assert _fast_client.sends == []

    @pytest.mark.asyncio
    async def test_non_zone_toggle_falls_back(self, _fast_client):
        entity = _entity(_coordinator(), "mainLightToggle")

        assert await lan_raw_write.async_zone_power(entity, on=True) is False
        assert _fast_client.sends == []

    @pytest.mark.asyncio
    async def test_coordinator_write_suppression_holds_raw_frames(self, _fast_client):
        """Upstream's post-failure LAN-write cooldown covers raw frames too.

        While ``_lan_writes_suppressed`` is set upstream routes that device's
        writes to MQTT/REST. Raw frames ride the same UDP path to the same
        lamp and are unacknowledged, so sending them inside the window is both
        against the flag and unfalsifiable.
        """
        coordinator = _coordinator()
        coordinator._lan_writes_suppressed = MagicMock(return_value=True)
        entity = _entity(coordinator, "rippleLightToggle")

        assert await lan_raw_write.async_zone_power(entity, on=True) is False
        assert lan_raw_write.lan_target(coordinator, DEVICE_ID, "H60B0") is None
        assert _fast_client.sends == []
        coordinator._lan_writes_suppressed.assert_called_with(DEVICE_ID)

    @pytest.mark.asyncio
    async def test_raw_frames_resume_when_the_suppression_clears(self, _fast_client):
        """The cooldown is a window, not a latch — the next write goes out."""
        coordinator = _coordinator()
        suppressed = MagicMock(return_value=True)
        coordinator._lan_writes_suppressed = suppressed
        entity = _entity(coordinator, "rippleLightToggle")
        assert await lan_raw_write.async_zone_power(entity, on=True) is False

        suppressed.return_value = False

        assert await lan_raw_write.async_zone_power(entity, on=True) is True
        assert {frame.hex() for _host, frame in _fast_client.sends} == {GOLDEN[("rippleLightToggle", True)]}

    @pytest.mark.asyncio
    async def test_a_missing_suppression_flag_does_not_block_writes(self, _fast_client):
        """An upstream rename must fail towards the pre-existing behaviour."""
        coordinator = _coordinator()
        del coordinator._lan_writes_suppressed  # MagicMock: attribute now absent
        entity = _entity(coordinator, "rippleLightToggle")

        assert await lan_raw_write.async_zone_power(entity, on=True) is True

    @pytest.mark.asyncio
    async def test_send_failure_falls_back_without_touching_state(self, monkeypatch, _fast_client):
        monkeypatch.setattr(lan_raw_write, "_CLIENT", _FakeRawClient(error=OSError("network unreachable")))
        entity = _entity(_coordinator(), "rippleLightToggle")

        assert await lan_raw_write.async_zone_power(entity, on=True) is False
        assert entity.is_on is False
        entity.async_write_ha_state.assert_not_called()


# ==============================================================================
# 3. Delivery
# ==============================================================================


class TestDelivery:
    """How the datagrams reach the wire: repeats, partial bursts, failures."""

    @pytest.mark.asyncio
    async def test_frame_is_repeated(self, _fast_client):
        entity = _entity(_coordinator(), "bottomLightToggle")

        await lan_raw_write.async_zone_power(entity, on=True)

        assert len(_fast_client.sends) == lan_raw_write.LAN_WRITE_REPEATS
        assert lan_raw_write.LAN_WRITE_REPEATS in (2, 3)

    @pytest.mark.asyncio
    async def test_a_partly_delivered_burst_still_counts_as_sent(self, _fast_client, monkeypatch):
        """The repeats are redundancy, not a sequence.

        The frames are absolute, so one copy reaching the socket says exactly
        what three do. Reporting failure would send the caller to the cloud
        with a second, contradictory command for something already commanded.
        """
        client = _FlakyRawClient(fail_after=1)
        monkeypatch.setattr(lan_raw_write, "_CLIENT", client)
        coordinator = _coordinator()
        entity = _entity(coordinator, "rippleLightToggle")

        assert await lan_raw_write.async_zone_power(entity, on=True) is True
        assert entity._is_on is True
        coordinator.async_control_device.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_wholly_failed_burst_falls_back(self, _fast_client, monkeypatch):
        client = _FlakyRawClient(fail_after=0)
        monkeypatch.setattr(lan_raw_write, "_CLIENT", client)
        entity = _entity(_coordinator(), "rippleLightToggle")

        assert await lan_raw_write.async_zone_power(entity, on=True) is False

    @pytest.mark.asyncio
    async def test_an_envelope_failure_is_not_a_transport_failure(self, _fast_client, monkeypatch):
        """A codec bug must not mark the device's transport unavailable.

        `refresh` never clears a hard send failure, so attributing an
        integration bug to the network would stick for the life of the entry.
        """
        client = _FakeRawClient(error=GoveeProtocolError("cannot build envelope"))
        monkeypatch.setattr(lan_raw_write, "_CLIENT", client)
        coordinator = _coordinator()
        entity = _entity(coordinator, "rippleLightToggle")

        with patch.object(lan_udp_health, "note_failure") as note_failure:
            assert await lan_raw_write.async_zone_power(entity, on=True) is False

        note_failure.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_socket_failure_is_a_transport_failure(self, _fast_client, monkeypatch):
        client = _FakeRawClient(error=OSError("network unreachable"))
        monkeypatch.setattr(lan_raw_write, "_CLIENT", client)
        entity = _entity(_coordinator(), "rippleLightToggle")

        with patch.object(lan_udp_health, "note_failure") as note_failure:
            assert await lan_raw_write.async_zone_power(entity, on=True) is False

        note_failure.assert_called_once()

    @pytest.mark.asyncio
    async def test_repeats_are_identical(self, _fast_client):
        entity = _entity(_coordinator(), "sideLightToggle")

        await lan_raw_write.async_zone_power(entity, on=False)

        assert len({send for send in _fast_client.sends}) == 1


# ==============================================================================
# 4. Optimistic state, including the displaced zone
# ==============================================================================


class TestOptimisticState:
    """State applied without an echo, including displaced siblings."""

    @pytest.mark.asyncio
    async def test_state_set_without_any_cloud_call(self, _fast_client):
        coordinator = _coordinator()
        entity = _entity(coordinator, "rippleLightToggle")

        assert await lan_raw_write.async_zone_power(entity, on=True) is True
        assert entity.is_on is True
        entity.async_write_ha_state.assert_called()
        coordinator.async_control_device.assert_not_called()

    @pytest.mark.asyncio
    async def test_turn_off_sets_state(self, _fast_client):
        entity = _entity(_coordinator(), "rippleLightToggle")
        entity._is_on = True

        await lan_raw_write.async_zone_power(entity, on=False)

        assert entity.is_on is False

    def _lamp(self, lit: tuple[str, ...] = ()) -> dict[str, GoveeNamedLightSwitchEntity]:
        """All three zone switches of one lamp, joined by the zone registry.

        Siblings are found through :mod:`custom_components.govee.zone_state`,
        not through ``entity.platform.entities`` — that lookup only ever saw the
        same platform, so it could never correct a zone light.
        """
        coordinator = _coordinator()
        device = _device()
        entities = {instance: _entity(coordinator, instance, device) for instance in lan_raw_write.ZONE_KEY_BY_TOGGLE}
        for instance, entity in entities.items():
            entity._is_on = instance in lit
            register_zone_switch(entity)
        return entities

    @pytest.mark.asyncio
    async def test_third_zone_displaces_the_first(self, _fast_client):
        # MaxSimultaneousZones(limit=2) on the H60B0: with ripple and ring lit,
        # switching the downlight on drops the ripple inside the lamp.
        zones = self._lamp(lit=("rippleLightToggle", "sideLightToggle"))

        await lan_raw_write.async_zone_power(zones["bottomLightToggle"], on=True)

        assert zones["bottomLightToggle"].is_on is True
        assert zones["rippleLightToggle"].is_on is False
        assert zones["sideLightToggle"].is_on is True

    @pytest.mark.asyncio
    async def test_no_displacement_below_the_limit(self, _fast_client):
        zones = self._lamp(lit=("rippleLightToggle",))

        await lan_raw_write.async_zone_power(zones["bottomLightToggle"], on=True)

        assert zones["rippleLightToggle"].is_on is True

    @pytest.mark.asyncio
    async def test_turning_off_never_displaces(self, _fast_client):
        zones = self._lamp()
        for entity in zones.values():
            entity._is_on = True

        await lan_raw_write.async_zone_power(zones["bottomLightToggle"], on=False)

        assert zones["rippleLightToggle"].is_on is True
        assert zones["sideLightToggle"].is_on is True

    def test_constraint_is_read_from_the_profile(self):
        # The displacement above must be derived, not hardcoded: same call with
        # a profile whose limit covers all three zones displaces nothing.
        profile = get_profile("H60B0")
        unconstrained = type(profile)(
            sku=profile.sku,
            goods_type=profile.goods_type,
            name=profile.name,
            kelvin=profile.kelvin,
            transports=profile.transports,
            ble_manufacturer_id=profile.ble_manufacturer_id,
            zones=profile.zones,
            capabilities=profile.capabilities,
            constraints=(),
        )
        lit = {"ripple", "ring"}

        assert lan_raw_write._displaced_zone_keys(profile, "downlight", lit) == ["ripple"]
        assert lan_raw_write._displaced_zone_keys(unconstrained, "downlight", lit) == []


# ==============================================================================
# 5. Switch wiring — the hook in switch.py
# ==============================================================================


class TestSwitchHook:
    """The two call lines this feature adds to the switch platform."""

    @pytest.mark.asyncio
    async def test_lan_path_skips_the_cloud_command(self, _fast_client):
        coordinator = _coordinator()
        entity = _entity(coordinator, "rippleLightToggle")

        await entity.async_turn_on()

        coordinator.async_control_device.assert_not_called()
        assert entity.is_on is True
        assert len(_fast_client.sends) == lan_raw_write.LAN_WRITE_REPEATS

    @pytest.mark.asyncio
    async def test_option_off_uses_the_cloud_command_unchanged(self, _fast_client):
        coordinator = _coordinator(enabled=False)
        entity = _entity(coordinator, "rippleLightToggle")

        await entity.async_turn_on()

        assert _fast_client.sends == []
        command = coordinator.async_control_device.call_args[0][1]
        assert command.toggle_instance == "rippleLightToggle"
        assert command.enabled is True
        assert entity.is_on is True

    @pytest.mark.asyncio
    async def test_option_off_turn_off_uses_the_cloud_command(self, _fast_client):
        coordinator = _coordinator(enabled=False)
        entity = _entity(coordinator, "sideLightToggle")
        entity._is_on = True

        await entity.async_turn_off()

        assert _fast_client.sends == []
        command = coordinator.async_control_device.call_args[0][1]
        assert command.enabled is False
        assert entity.is_on is False

    @pytest.mark.asyncio
    async def test_cloud_failure_still_does_not_flip_state(self, _fast_client):
        coordinator = _coordinator(enabled=False)
        coordinator.async_control_device = AsyncMock(return_value=False)
        entity = _entity(coordinator, "bottomLightToggle")

        await entity.async_turn_on()

        assert entity.is_on is False
