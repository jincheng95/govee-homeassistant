"""Tests for the per-zone light entities (fork feature, ``enable_zone_lights``).

The H60B0's three zones are not symmetric, so the tests are organised around
proving that the entity model is *derived from the profile table* rather than
written down three times:

1. Wiring — the option gates everything, and when it is on the zone switches
   step aside for the lights (but only for zones the table actually models).
2. Capabilities — three zones, three different colour-mode answers, and a
   kelvin range read from the profile (which clamps 9000 K down on this SKU).
3. Frames — byte-exact per zone for power, colour, brightness, colour
   temperature and flow rate.
4. The displacement constraint — applied through the shared registry, so a zone
   *light* corrects a zone *switch* on the other platform, and applied on the
   cloud path too.
5. Fallback — zone power falls back to the cloud, the LAN-only capabilities
   fail loudly or go unavailable instead of pretending.
6. The whole-device relationship — the master demotion (WLED-style: power and
   brightness only), reported power outranking optimistic zone state, and
   lighting a zone powering the lamp.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# mypy --strict: HA re-exports without __all__.
from homeassistant.components.light import (  # type: ignore[attr-defined]
    ColorMode,
    LightEntityFeature,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.restore_state import RestoreEntity

from custom_components.govee.api import lan_raw_write
from custom_components.govee.platforms import zone_light
from custom_components.govee.api.lan_client import LanDeviceInfo
from custom_components.govee.api.protocol import Capability, UnknownEncodingError, get_profile
from custom_components.govee.const import (
    CONF_ENABLE_LAN_RAW_WRITE,
    CONF_ENABLE_ZONE_LIGHTS,
)
from custom_components.govee.models import (
    GoveeCapability,
    GoveeDevice,
    GoveeDeviceState,
    PowerCommand,
    ToggleCommand,
)
from custom_components.govee.models.device import (
    CAPABILITY_COLOR_SETTING,
    CAPABILITY_ON_OFF,
    CAPABILITY_RANGE,
    CAPABILITY_TOGGLE,
    DEVICE_TYPE_LIGHT,
    INSTANCE_BRIGHTNESS,
    INSTANCE_COLOR_RGB,
    INSTANCE_POWER,
)
from custom_components.govee.light import GoveeLightEntity
from custom_components.govee.switch import GoveeNamedLightSwitchEntity
from custom_components.govee.platforms.zone_light import (
    MASTER_NAME,
    GoveeZoneFlowRateNumber,
    GoveeZoneLightEntity,
    as_master_light,
    async_zone_light_entities,
    async_zone_number_entities,
)
from custom_components.govee.zone_state import register_zone_switch, registry, zone_switch_suppressed

DEVICE_ID = "AA:BB:CC:DD:EE:FF:60:B0"
IP = "10.20.0.51"

# Golden frames, 20 bytes each: 33 05 2c <zone> <attr> ... <xor>. The zone and
# attribute bytes are asserted against the profile table separately, so a table
# edit cannot silently repaint these expectations.
GOLDEN = {
    "ripple_on": "3330010100000000000000000000000000000003",
    "ripple_off": "3330010000000000000000000000000000000002",
    "downlight_on": "3330030100000000000000000000000000000001",
    "downlight_off": "3330030000000000000000000000000000000000",
    "ripple_red": "33052c0101ff00000000000000000000000000e5",
    "ring_blue": "33052c02010000ff0000ff000000000000000019",
    "ring_bright_50": "33052c020232ff000000000000000000000000d7",
    "downlight_bright_50": "33052c0302320000000000000000000000000029",
    "downlight_4000k": "33052c03030fa0000000000000000000000000b5",
    # 9000 K asked for, 6500 K (0x1964) sent — the H60B0 drops the zone above
    # its ceiling, and the ceiling comes from the profile.
    "downlight_clamped": "33052c0303196400000000000000000000000067",
    "ripple_flow_40": "33052c0103280000000000000000000000000030",
}


# ==============================================================================
# Fixtures / builders
# ==============================================================================


class _FakeRawClient:
    """Records envelopes instead of sending them."""

    def __init__(self) -> None:
        self.envelopes: list[tuple[str, list[bytes]]] = []
        self.error: Exception | None = None

    async def async_send_frames(self, host: str, frames: list[bytes]) -> None:
        self.envelopes.append((host, list(frames)))
        if self.error is not None:
            raise self.error

    async def async_send_frame(self, host: str, frame: bytes) -> None:
        await self.async_send_frames(host, [frame])

    @property
    def hexes(self) -> list[str]:
        """Every distinct frame hex from the first envelope, in order."""
        if not self.envelopes:
            return []
        return [frame.hex() for frame in self.envelopes[0][1]]


@pytest.fixture(autouse=True)
def raw_client(monkeypatch: pytest.MonkeyPatch) -> _FakeRawClient:
    """Recording client with the inter-repeat gap taken off the wall clock."""
    client = _FakeRawClient()
    monkeypatch.setattr(lan_raw_write, "_CLIENT", client)
    monkeypatch.setattr(lan_raw_write, "LAN_WRITE_GAP_SECONDS", 0)
    return client


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
            _cap(CAPABILITY_RANGE, INSTANCE_BRIGHTNESS),
            _cap(CAPABILITY_COLOR_SETTING, INSTANCE_COLOR_RGB),
            _cap(CAPABILITY_TOGGLE, "rippleLightToggle"),
            _cap(CAPABILITY_TOGGLE, "sideLightToggle"),
            _cap(CAPABILITY_TOGGLE, "bottomLightToggle"),
        ),
    )


def _coordinator(
    device: GoveeDevice | None = None,
    *,
    lan_raw_write_on: bool = True,
    on_lan: bool = True,
    power_state: bool = True,
) -> Any:
    device = device or _device()
    coordinator = MagicMock()
    # A real GoveeCoordinator has no registry attribute until zone_state builds
    # one; MagicMock would otherwise auto-create a Mock and swallow the lookup.
    coordinator._govee_zone_state_registry = None
    coordinator.devices = {device.device_id: device}
    coordinator.config_entry.options = {CONF_ENABLE_LAN_RAW_WRITE: lan_raw_write_on}
    coordinator.async_control_device = AsyncMock(return_value=True)
    coordinator.last_update_success = True
    state = GoveeDeviceState.create_empty(device.device_id)
    state.power_state = power_state
    state.online = True
    coordinator.get_state = MagicMock(return_value=state)
    coordinator._lan_devices = {}
    if on_lan:
        coordinator._lan_devices[device.device_id] = LanDeviceInfo(
            device_id=device.device_id,
            ip=IP,
            mac=device.device_id,
            sku=device.sku,
            firmware="1.0.0",
            last_correlated_ts=0.0,
        )
    return coordinator


def _entry(coordinator: Any, *, zone_lights: bool = True, lan_raw_write: bool | None = None) -> Any:
    """A config entry whose options match the coordinator's, unless overridden.

    The zone-light model needs BOTH options: the raw transport is the only
    thing that can address a single zone, so the entry and the coordinator have
    to agree or the test is describing a state the integration cannot reach.
    """
    if lan_raw_write is None:
        lan_raw_write = bool(coordinator.config_entry.options.get(CONF_ENABLE_LAN_RAW_WRITE, False))
    entry = MagicMock()
    entry.runtime_data = coordinator
    entry.options = {CONF_ENABLE_ZONE_LIGHTS: zone_lights, CONF_ENABLE_LAN_RAW_WRITE: lan_raw_write}
    return entry


def _lights(coordinator: Any, entry: Any) -> dict[str, GoveeZoneLightEntity]:
    entities = {entity._zone_key: entity for entity in async_zone_light_entities(coordinator, entry)}
    for entity in entities.values():
        entity.async_write_ha_state = MagicMock()
    return entities


def _switch(coordinator: Any, instance: str, device: GoveeDevice) -> GoveeNamedLightSwitchEntity:
    entity = GoveeNamedLightSwitchEntity(coordinator, device, instance, "govee_test", "_x", "mdi:shimmer")
    entity.async_write_ha_state = MagicMock()
    return entity


# ==============================================================================
# 1. Platform wiring
# ==============================================================================


class TestWiring:
    """Which entities the options produce, and which they suppress."""

    def test_option_off_creates_no_zone_entities(self):
        coordinator = _coordinator()
        entry = _entry(coordinator, zone_lights=False)

        assert async_zone_light_entities(coordinator, entry) == []
        assert async_zone_number_entities(coordinator, entry) == []

    def test_option_defaults_off(self):
        coordinator = _coordinator()
        entry = _entry(coordinator)
        entry.options = {}

        assert async_zone_light_entities(coordinator, entry) == []

    def test_one_light_per_modelled_zone(self):
        coordinator = _coordinator()
        lights = _lights(coordinator, _entry(coordinator))

        # The table's two colour-strip pseudo-zones have no capabilities and no
        # zone power, so they must not become entities.
        assert sorted(lights) == ["downlight", "ring", "ripple"]

    def test_unique_ids_are_a_separate_namespace_from_the_switches(self):
        coordinator = _coordinator()
        lights = _lights(coordinator, _entry(coordinator))

        assert lights["ripple"].unique_id == f"{DEVICE_ID}_zone_ripple"
        # The switch keeps its own id, so flipping the option back restores it.
        assert lights["ripple"].unique_id != f"{DEVICE_ID}_ripple_light"

    def test_flow_rate_number_only_for_zones_that_declare_one(self):
        coordinator = _coordinator()
        numbers = async_zone_number_entities(coordinator, _entry(coordinator))

        assert [n._zone_key for n in numbers] == ["ripple"]
        assert isinstance(numbers[0], GoveeZoneFlowRateNumber)
        assert Capability.ZONE_FLOW_RATE in get_profile("H60B0").zone("ripple").capabilities

    def test_unprofiled_sku_gets_no_zone_entities(self):
        device = _device(sku="H60B3")  # same lamp family, not in the table
        coordinator = _coordinator(device)

        assert async_zone_light_entities(coordinator, _entry(coordinator)) == []

    @pytest.mark.asyncio
    async def test_zone_switches_step_aside_when_zone_lights_are_on(self):
        from custom_components.govee import switch as switch_mod

        coordinator = _coordinator()
        entry = _entry(coordinator)
        added: list = []
        await switch_mod.async_setup_entry(MagicMock(), entry, lambda ents: added.extend(ents))

        named = [e for e in added if isinstance(e, GoveeNamedLightSwitchEntity)]
        assert named == []

    @pytest.mark.asyncio
    async def test_zone_switches_survive_when_the_option_is_off(self):
        from custom_components.govee import switch as switch_mod

        coordinator = _coordinator()
        entry = _entry(coordinator, zone_lights=False)
        added: list = []
        await switch_mod.async_setup_entry(MagicMock(), entry, lambda ents: added.extend(ents))

        named = sorted(e._toggle_instance for e in added if isinstance(e, GoveeNamedLightSwitchEntity))
        assert named == ["bottomLightToggle", "rippleLightToggle", "sideLightToggle"]

    @pytest.mark.asyncio
    async def test_only_profiled_zone_switches_are_suppressed(self):
        """A named light toggle the table does not model keeps its switch."""
        from custom_components.govee import switch as switch_mod

        device = _device(sku="H60B3")
        coordinator = _coordinator(device)
        entry = _entry(coordinator)
        added: list = []
        await switch_mod.async_setup_entry(MagicMock(), entry, lambda ents: added.extend(ents))

        named = sorted(e._toggle_instance for e in added if isinstance(e, GoveeNamedLightSwitchEntity))
        assert named == ["bottomLightToggle", "rippleLightToggle", "sideLightToggle"]


# ==============================================================================
# 2. Capabilities — three zones, three answers, all from the table
# ==============================================================================


class TestCapabilities:
    """Each zone's surface, derived from the profile table alone."""

    def test_colour_modes_differ_per_zone(self):
        lights = _lights(_coordinator(), _entry(_coordinator()))

        assert lights["ripple"].supported_color_modes == {ColorMode.RGB}
        assert lights["ring"].supported_color_modes == {ColorMode.RGB}
        # The downlight is white-only: colour temperature, never RGB.
        assert lights["downlight"].supported_color_modes == {ColorMode.COLOR_TEMP}

    def test_colour_modes_are_derived_from_the_profile(self):
        profile = get_profile("H60B0")
        for key, expected in (
            ("ripple", Capability.ZONE_COLOR),
            ("ring", Capability.ZONE_COLOR),
            ("downlight", Capability.ZONE_COLOR_TEMP),
        ):
            assert expected in profile.zone(key).capabilities
        assert Capability.ZONE_COLOR not in profile.zone("downlight").capabilities

    def test_kelvin_range_comes_from_the_profile(self):
        light = _lights(_coordinator(), _entry(_coordinator()))["downlight"]
        kelvin = get_profile("H60B0").kelvin

        assert light.min_color_temp_kelvin == kelvin.minimum == 2000
        # NOT the 9000 K the H6046 reaches — ceilings are per-SKU.
        assert light.max_color_temp_kelvin == kelvin.maximum == 6500

    def test_segment_count_is_reported_per_zone(self):
        lights = _lights(_coordinator(), _entry(_coordinator()))

        assert lights["ring"].extra_state_attributes["zone_segments"] == 8
        assert lights["ripple"].extra_state_attributes["zone_segments"] == 0

    def test_state_is_flagged_optimistic(self):
        light = _lights(_coordinator(), _entry(_coordinator()))["ripple"]

        assert light.extra_state_attributes["optimistic"] is True
        assert light.extra_state_attributes["lan_transport"] is True

    def test_restore_entity_is_in_the_mro(self):
        # Nothing can report zone state, so restore is the only continuity.
        assert issubclass(GoveeZoneLightEntity, RestoreEntity)
        assert issubclass(GoveeZoneFlowRateNumber, RestoreEntity)


# ==============================================================================
# 3. Frames
# ==============================================================================


class TestFrames:
    """The exact bytes a zone command puts on the wire."""

    @pytest.mark.asyncio
    async def test_plain_turn_on_sends_zone_power(self, raw_client):
        lights = _lights(_coordinator(), _entry(_coordinator()))

        await lights["ripple"].async_turn_on()

        assert raw_client.hexes == [GOLDEN["ripple_on"]]

    @pytest.mark.asyncio
    async def test_turn_off_sends_zone_power_off(self, raw_client):
        lights = _lights(_coordinator(), _entry(_coordinator()))

        await lights["downlight"].async_turn_off()

        assert raw_client.hexes == [GOLDEN["downlight_off"]]

    @pytest.mark.asyncio
    async def test_colour_frame_for_an_rgb_zone(self, raw_client):
        lights = _lights(_coordinator(), _entry(_coordinator()))

        await lights["ripple"].async_turn_on(rgb_color=(255, 0, 0))

        assert raw_client.hexes == [GOLDEN["ripple_on"], GOLDEN["ripple_red"]]

    @pytest.mark.asyncio
    async def test_segmented_zone_paints_every_segment(self, raw_client):
        lights = _lights(_coordinator(), _entry(_coordinator()))

        await lights["ring"].async_turn_on(rgb_color=(0, 0, 255))

        # The 8-segment mask (ff 00) is in the frame; the ripple's is not.
        assert raw_client.hexes[-1] == GOLDEN["ring_blue"]

    @pytest.mark.asyncio
    async def test_brightness_frame_uses_percent(self, raw_client):
        lights = _lights(_coordinator(), _entry(_coordinator()))

        await lights["ring"].async_turn_on(brightness=128)

        assert raw_client.hexes[-1] == GOLDEN["ring_bright_50"]

    @pytest.mark.asyncio
    async def test_downlight_brightness_has_no_mask(self, raw_client):
        lights = _lights(_coordinator(), _entry(_coordinator()))

        await lights["downlight"].async_turn_on(brightness=128)

        assert raw_client.hexes[-1] == GOLDEN["downlight_bright_50"]

    @pytest.mark.asyncio
    async def test_colour_temp_frame(self, raw_client):
        lights = _lights(_coordinator(), _entry(_coordinator()))

        await lights["downlight"].async_turn_on(color_temp_kelvin=4000)

        assert raw_client.hexes[-1] == GOLDEN["downlight_4000k"]

    @pytest.mark.asyncio
    async def test_kelvin_is_clamped_through_the_profile(self, raw_client):
        lights = _lights(_coordinator(), _entry(_coordinator()))

        await lights["downlight"].async_turn_on(color_temp_kelvin=9000)

        assert raw_client.hexes[-1] == GOLDEN["downlight_clamped"]
        # ...and the optimistic state records the clamped value, not the ask.
        assert lights["downlight"].color_temp_kelvin == 6500

    @pytest.mark.asyncio
    async def test_zone_without_a_capability_ignores_it(self, raw_client):
        """The white-only downlight must not emit a colour frame."""
        lights = _lights(_coordinator(), _entry(_coordinator()))

        await lights["downlight"].async_turn_on(rgb_color=(255, 0, 0))

        assert raw_client.hexes == [GOLDEN["downlight_on"]]
        assert lights["downlight"].rgb_color is None

    @pytest.mark.asyncio
    async def test_frames_ride_in_one_envelope_repeated(self, raw_client):
        lights = _lights(_coordinator(), _entry(_coordinator()))

        await lights["ripple"].async_turn_on(rgb_color=(255, 0, 0))

        assert len(raw_client.envelopes) == lan_raw_write.LAN_WRITE_REPEATS
        assert {tuple(f.hex() for f in frames) for _host, frames in raw_client.envelopes} == {
            (GOLDEN["ripple_on"], GOLDEN["ripple_red"])
        }

    @pytest.mark.asyncio
    async def test_flow_rate_frame(self, raw_client):
        coordinator = _coordinator()
        number = async_zone_number_entities(coordinator, _entry(coordinator))[0]
        number.async_write_ha_state = MagicMock()

        await number.async_set_native_value(40)

        assert raw_client.hexes == [GOLDEN["ripple_flow_40"]]
        assert number.native_value == 40.0

    def test_zone_and_attribute_bytes_come_from_the_profile(self):
        profile = get_profile("H60B0")
        colour = profile.capabilities[Capability.ZONE_COLOR]

        assert bytes.fromhex(GOLDEN["ripple_red"])[3] == profile.zone("ripple").zone_byte
        assert bytes.fromhex(GOLDEN["ripple_red"])[2] == colour.constants["sub_mode"]
        assert bytes.fromhex(GOLDEN["ripple_red"])[4] == colour.constants["attribute"]


# ==============================================================================
# 4. The hardware displacement constraint, across platforms and transports
# ==============================================================================


class TestDisplacement:
    """The hardware's two-of-three limit, applied below the transport."""

    @pytest.mark.asyncio
    async def test_a_zone_light_displaces_a_zone_switch(self, raw_client):
        """The cross-platform case an ``entity.platform.entities`` walk misses."""
        device = _device()
        coordinator = _coordinator(device)
        lights = _lights(coordinator, _entry(coordinator))
        ripple_switch = _switch(coordinator, "rippleLightToggle", device)
        ripple_switch._is_on = True
        register_zone_switch(ripple_switch)
        registry(coordinator).apply(DEVICE_ID, "H60B0", "ring", True)

        await lights["downlight"].async_turn_on()

        # limit=2 with ripple+ring lit: the first-listed zone yields.
        assert ripple_switch.is_on is False
        assert registry(coordinator).is_on(DEVICE_ID, "ring") is True
        assert lights["downlight"].is_on is True

    @pytest.mark.asyncio
    async def test_a_cloud_toggle_also_displaces(self, raw_client):
        """The asymmetry that used to exist: displacement on the LAN path only."""
        device = _device()
        coordinator = _coordinator(device, lan_raw_write_on=False)
        ripple_switch = _switch(coordinator, "rippleLightToggle", device)
        side_switch = _switch(coordinator, "sideLightToggle", device)
        bottom_switch = _switch(coordinator, "bottomLightToggle", device)
        for entity, lit in ((ripple_switch, True), (side_switch, True), (bottom_switch, False)):
            entity._is_on = lit
            register_zone_switch(entity)

        await bottom_switch.async_turn_on()

        coordinator.async_control_device.assert_awaited()
        assert bottom_switch.is_on is True
        assert ripple_switch.is_on is False
        assert side_switch.is_on is True

    @pytest.mark.asyncio
    async def test_turning_a_zone_off_displaces_nothing(self, raw_client):
        device = _device()
        coordinator = _coordinator(device)
        lights = _lights(coordinator, _entry(coordinator))
        for key in ("ripple", "ring"):
            registry(coordinator).apply(DEVICE_ID, "H60B0", key, True)

        await lights["downlight"].async_turn_off()

        assert registry(coordinator).is_on(DEVICE_ID, "ripple") is True
        assert registry(coordinator).is_on(DEVICE_ID, "ring") is True

    def test_the_limit_is_read_from_the_table(self):
        constraint = get_profile("H60B0").constraints[0]

        assert constraint.limit == 2
        assert constraint.zone_keys == ("ripple", "ring", "downlight")


# ==============================================================================
# 5. Fallback — per capability
# ==============================================================================


class TestFallback:
    """What happens when the local network cannot carry the intent."""

    @pytest.mark.asyncio
    async def test_zone_power_falls_back_to_the_cloud(self, raw_client):
        device = _device()
        coordinator = _coordinator(device, on_lan=False)
        lights = _lights(coordinator, _entry(coordinator))

        await lights["ripple"].async_turn_on()

        assert raw_client.envelopes == []
        command = coordinator.async_control_device.await_args_list[-1].args[1]
        assert isinstance(command, ToggleCommand)
        assert command.toggle_instance == "rippleLightToggle"
        assert command.enabled is True

    @pytest.mark.asyncio
    async def test_zone_power_off_falls_back_to_the_cloud(self, raw_client):
        coordinator = _coordinator(on_lan=False)
        lights = _lights(coordinator, _entry(coordinator))

        await lights["ring"].async_turn_off()

        command = coordinator.async_control_device.await_args_list[-1].args[1]
        assert isinstance(command, ToggleCommand)
        assert command.toggle_instance == "sideLightToggle"
        assert command.enabled is False

    @pytest.mark.parametrize(
        "kwargs",
        [{"rgb_color": (1, 2, 3)}, {"brightness": 128}],
    )
    @pytest.mark.asyncio
    async def test_zone_attributes_fail_loudly_without_lan(self, raw_client, kwargs):
        """No cloud equivalent exists at zone granularity — so say so."""
        coordinator = _coordinator(on_lan=False)
        lights = _lights(coordinator, _entry(coordinator))

        with pytest.raises(HomeAssistantError):
            await lights["ripple"].async_turn_on(**kwargs)
        assert raw_client.envelopes == []

    def test_the_model_needs_the_raw_transport_option_too(self):
        """Zone lights without the raw transport would leave no colour control.

        Every zone capability past on/off is a raw frame; the cloud cannot
        address a zone at all. So with the transport off the zone-light model
        must not be built — otherwise the whole-device light is stripped of
        colour, colour temperature and effects and handed to entities that can
        only raise, and the lamp ends up with no working colour anywhere.
        """
        coordinator = _coordinator(lan_raw_write_on=False)
        entry = _entry(coordinator)  # zone lights ticked, transport off

        assert async_zone_light_entities(coordinator, entry) == []
        assert async_zone_number_entities(coordinator, entry) == []

    def test_the_master_is_not_demoted_without_the_raw_transport(self):
        """The demotion and the zone entities must switch on together."""
        coordinator = _coordinator(lan_raw_write_on=False)
        master = _master(coordinator, _entry(coordinator))

        assert master._attr_name != zone_light.MASTER_NAME
        assert ColorMode.RGB in (master.supported_color_modes or set())

    def test_the_zone_switches_survive_without_the_raw_transport(self):
        """The switches must not disappear when nothing can replace them."""
        coordinator = _coordinator(lan_raw_write_on=False)
        entry = _entry(coordinator)

        assert zone_switch_suppressed(entry, "H60B0", "rippleLightToggle") is False

    @pytest.mark.asyncio
    async def test_an_unencodable_frame_raises_instead_of_traceback(self, raw_client):
        """Codec refusals are not HomeAssistantError and must be translated.

        The codec raises rather than guess a byte it does not know. Unwrapped,
        that surfaces as a traceback in the log rather than a message on the
        service call.
        """
        coordinator = _coordinator()
        light = _lights(coordinator, _entry(coordinator))["ripple"]

        with patch.object(
            zone_light.GoveeCodec,
            "zone_color",
            side_effect=UnknownEncodingError("sub_mode UNKNOWN"),
        ):
            with pytest.raises(HomeAssistantError):
                await light.async_turn_on(rgb_color=(1, 2, 3))

        assert raw_client.envelopes == []

    @pytest.mark.asyncio
    async def test_an_unencodable_frame_does_not_power_the_lamp_on(self, raw_client):
        """Nothing may touch the lamp before every failable step has passed.

        Powering the whole lamp on and then raising leaves the user with a lit
        lamp, an unlit zone and an error — worse than where they started.
        """
        coordinator = _coordinator(power_state=False)
        light = _lights(coordinator, _entry(coordinator))["ripple"]

        with patch.object(
            zone_light.GoveeCodec,
            "zone_power",
            side_effect=UnknownEncodingError("sub_mode UNKNOWN"),
        ):
            with pytest.raises(HomeAssistantError):
                await light.async_turn_on()

        coordinator.async_control_device.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_zone_with_no_cloud_toggle_raises_before_powering_the_lamp(self, raw_client):
        """Same rule on the cloud-fallback branch."""
        device = _device()
        coordinator = _coordinator(device, on_lan=False, power_state=False)
        light = _lights(coordinator, _entry(coordinator))["ripple"]
        light._device = MagicMock(wraps=device)
        light._device.name = device.name
        light._device.named_light_toggle_instances = ()

        with pytest.raises(HomeAssistantError):
            await light.async_turn_on()

        coordinator.async_control_device.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_failed_send_falls_back_for_power(self, raw_client):
        coordinator = _coordinator()
        lights = _lights(coordinator, _entry(coordinator))
        raw_client.error = OSError("network unreachable")

        await lights["ripple"].async_turn_on()

        command = coordinator.async_control_device.await_args_list[-1].args[1]
        assert isinstance(command, ToggleCommand)

    @pytest.mark.asyncio
    async def test_a_failed_send_raises_for_attributes(self, raw_client):
        coordinator = _coordinator()
        lights = _lights(coordinator, _entry(coordinator))
        raw_client.error = OSError("network unreachable")

        with pytest.raises(HomeAssistantError):
            await lights["ripple"].async_turn_on(rgb_color=(1, 2, 3))

    def test_flow_rate_is_unavailable_without_lan(self):
        coordinator = _coordinator(on_lan=False)
        number = async_zone_number_entities(coordinator, _entry(coordinator))[0]

        assert number.available is False

    def test_flow_rate_is_available_with_lan(self):
        coordinator = _coordinator()
        number = async_zone_number_entities(coordinator, _entry(coordinator))[0]

        assert number.available is True

    @pytest.mark.asyncio
    async def test_flow_rate_raises_when_the_send_fails(self, raw_client):
        coordinator = _coordinator()
        number = async_zone_number_entities(coordinator, _entry(coordinator))[0]
        number.async_write_ha_state = MagicMock()
        raw_client.error = OSError("network unreachable")

        with pytest.raises(HomeAssistantError):
            await number.async_set_native_value(40)


# ==============================================================================
# 6. The whole-device relationship
# ==============================================================================


class TestWholeDeviceRelationship:
    """How a zone light and the whole-device light stay coherent."""

    @pytest.mark.asyncio
    async def test_reported_power_off_beats_optimistic_zone_state(self, raw_client):
        coordinator = _coordinator(power_state=False)
        lights = _lights(coordinator, _entry(coordinator))
        registry(coordinator).apply(DEVICE_ID, "H60B0", "ripple", True)

        # The lamp is reported off, so no zone can be lit — whatever we last
        # commanded. Ground truth wins over optimism.
        assert lights["ripple"].is_on is False

    @pytest.mark.asyncio
    async def test_zone_state_shows_through_when_the_lamp_is_on(self, raw_client):
        coordinator = _coordinator(power_state=True)
        lights = _lights(coordinator, _entry(coordinator))
        registry(coordinator).apply(DEVICE_ID, "H60B0", "ripple", True)

        assert lights["ripple"].is_on is True

    @pytest.mark.asyncio
    async def test_turning_a_zone_on_powers_the_lamp_over_the_cloud(self, raw_client):
        coordinator = _coordinator(power_state=False)
        lights = _lights(coordinator, _entry(coordinator))

        await lights["ripple"].async_turn_on()

        # The whole-device power command is the one with real state behind it,
        # so it goes out on the normal path, not as a raw frame.
        first = coordinator.async_control_device.await_args_list[0].args[1]
        assert isinstance(first, PowerCommand)
        assert first.power_on is True
        assert raw_client.hexes == [GOLDEN["ripple_on"]]

    @pytest.mark.asyncio
    async def test_no_power_command_when_the_lamp_is_already_on(self, raw_client):
        coordinator = _coordinator(power_state=True)
        lights = _lights(coordinator, _entry(coordinator))

        await lights["ripple"].async_turn_on()

        assert coordinator.async_control_device.await_count == 0


# ==============================================================================
# 7. Restore
# ==============================================================================


class TestRestore:
    """Optimistic state surviving a restart without inventing anything."""

    @pytest.mark.asyncio
    async def test_restored_state_seeds_the_registry(self):
        coordinator = _coordinator()
        light = _lights(coordinator, _entry(coordinator))["ripple"]
        light.async_on_remove = MagicMock()
        last = MagicMock()
        last.state = "on"
        last.attributes = {"brightness": 200, "rgb_color": [1, 2, 3]}
        light.async_get_last_state = AsyncMock(return_value=last)

        with patch(
            "homeassistant.helpers.update_coordinator.CoordinatorEntity.async_added_to_hass",
            AsyncMock(),
        ):
            await light.async_added_to_hass()

        assert registry(coordinator).is_on(DEVICE_ID, "ripple") is True
        assert light.brightness == 200
        assert light.rgb_color == (1, 2, 3)

    @pytest.mark.asyncio
    async def test_restore_survives_the_lamp_having_been_off(self):
        """The saved entity state is masked by power; the zone attribute is not.

        `is_on` reports off whenever the lamp is reported off, whatever the
        zone was told. Restoring from that would mean any restart while the
        lamp happened to be off erased every zone's state — so the unmasked
        value is published as an attribute and restored from there.
        """
        coordinator = _coordinator(power_state=False)
        light = _lights(coordinator, _entry(coordinator))["ripple"]
        light.async_on_remove = MagicMock()
        last = MagicMock()
        last.state = "off"  # what `is_on` reported while the lamp was off
        last.attributes = {zone_light.ATTR_ZONE_ON: True, "brightness": 200}
        light.async_get_last_state = AsyncMock(return_value=last)

        with patch(
            "homeassistant.helpers.update_coordinator.CoordinatorEntity.async_added_to_hass",
            AsyncMock(),
        ):
            await light.async_added_to_hass()

        assert registry(coordinator).is_on(DEVICE_ID, "ripple") is True

    @pytest.mark.asyncio
    async def test_the_unmasked_zone_state_is_published_for_restore(self):
        """The attribute restore reads must actually be written."""
        coordinator = _coordinator(power_state=False)
        light = _lights(coordinator, _entry(coordinator))["ripple"]
        registry(coordinator).apply(DEVICE_ID, "H60B0", "ripple", True)

        assert light.is_on is False  # masked by reported power
        assert light.extra_state_attributes[zone_light.ATTR_ZONE_ON] is True

    @pytest.mark.asyncio
    async def test_a_restored_zero_brightness_is_not_discarded(self):
        """0 and (0, 0, 0) are states HA writes, and both are falsy."""
        coordinator = _coordinator()
        light = _lights(coordinator, _entry(coordinator))["ripple"]
        light.async_on_remove = MagicMock()
        last = MagicMock()
        last.state = "on"
        last.attributes = {"brightness": 0, "rgb_color": [0, 0, 0]}
        light.async_get_last_state = AsyncMock(return_value=last)

        with patch(
            "homeassistant.helpers.update_coordinator.CoordinatorEntity.async_added_to_hass",
            AsyncMock(),
        ):
            await light.async_added_to_hass()

        assert light.brightness == 0
        assert light.rgb_color == (0, 0, 0)

    def test_an_uncommanded_zone_reports_no_brightness(self):
        """Optimistic means "what it was told" — not an invented 255."""
        coordinator = _coordinator()
        lights = _lights(coordinator, _entry(coordinator))

        assert lights["ripple"].brightness is None
        assert lights["ripple"].rgb_color is None

    @pytest.mark.asyncio
    async def test_no_previous_state_restores_off(self):
        coordinator = _coordinator()
        light = _lights(coordinator, _entry(coordinator))["ripple"]
        light.async_on_remove = MagicMock()
        light.async_get_last_state = AsyncMock(return_value=None)

        with patch(
            "homeassistant.helpers.update_coordinator.CoordinatorEntity.async_added_to_hass",
            AsyncMock(),
        ):
            await light.async_added_to_hass()

        assert registry(coordinator).is_on(DEVICE_ID, "ripple") is False


# ==============================================================================
# 8. Naming helpers
# ==============================================================================


class TestNaming:
    """Entity names and unique ids, derived from the profile keys."""

    def test_zone_names_come_from_the_profile_keys(self):
        profile = get_profile("H60B0")

        assert zone_light.zone_display_name(profile.zone("ripple")) == "Ripple"
        assert zone_light.zone_display_name(profile.zone("downlight")) == "Downlight"

    def test_flow_rate_name(self):
        coordinator = _coordinator()
        number = async_zone_number_entities(coordinator, _entry(coordinator))[0]

        assert number.name == "Ripple flow rate"
        assert number.unique_id == f"{DEVICE_ID}_zone_flow_rate_ripple"


# ==============================================================================
# 9. The master demotion (WLED model)
# ==============================================================================


def _master(coordinator: Any, entry: Any, device: GoveeDevice | None = None) -> GoveeLightEntity:
    device = device or _device()
    return as_master_light(GoveeLightEntity(coordinator, device, True), entry)


class TestMasterDemotion:
    """The WLED-style reduction of the whole-device light."""

    def test_master_keeps_power_and_brightness_only(self):
        coordinator = _coordinator()
        master = _master(coordinator, _entry(coordinator))

        assert master.supported_color_modes == {ColorMode.BRIGHTNESS}
        assert master.color_mode == ColorMode.BRIGHTNESS

    def test_master_loses_the_effect_list(self):
        """The 111 named scenes move to upstream's scene select entity."""
        coordinator = _coordinator()
        master = _master(coordinator, _entry(coordinator))

        assert master.supported_features == LightEntityFeature(0)
        assert master.effect_list is None
        assert master._enable_scenes is False

    def test_master_is_renamed(self):
        coordinator = _coordinator()
        master = _master(coordinator, _entry(coordinator))

        assert master.name == MASTER_NAME

    def test_option_off_leaves_the_whole_device_entity_untouched(self):
        coordinator = _coordinator()
        entry = _entry(coordinator, zone_lights=False)
        plain = _master(coordinator, entry)

        assert plain.name is None
        assert ColorMode.RGB in (plain.supported_color_modes or set())
        # Untouched, whatever the device happens to support.
        assert plain._enable_scenes is (_device().supports_scenes and True)

    def test_a_device_without_profile_zones_is_not_demoted(self):
        device = _device(sku="H60B3")
        coordinator = _coordinator(device)
        plain = _master(coordinator, _entry(coordinator), device)

        assert plain.name is None
        assert ColorMode.RGB in (plain.supported_color_modes or set())

    @pytest.mark.asyncio
    async def test_light_platform_wires_the_master(self):
        from custom_components.govee import light as light_mod

        coordinator = _coordinator()
        entry = _entry(coordinator)
        entry.options["segment_mode_by_device"] = {}  # the segment-mode lookup upstream does on the same entry
        added: list = []
        await light_mod.async_setup_entry(MagicMock(), entry, lambda e: added.extend(e))

        whole = [e for e in added if isinstance(e, GoveeLightEntity)]
        zones = [e for e in added if isinstance(e, GoveeZoneLightEntity)]
        assert len(whole) == 1
        assert whole[0].name == MASTER_NAME
        assert sorted(z._zone_key for z in zones) == ["downlight", "ring", "ripple"]

    def test_master_transport_is_upstreams_lan_control_tier(self):
        """No raw-frame path is added for the master — there is no gap.

        Upstream's ``command_to_lan`` already routes whole-device power and
        brightness over the LAN with verify-by-read, which beats an
        unconfirmable raw frame. This test pins that assumption so a future
        upstream narrowing of that scope shows up here.
        """
        from custom_components.govee.api.lan_control import command_to_lan

        assert command_to_lan(PowerCommand(power_on=True), "H60B0", (1, 100)) is not None
