"""Tests for the DIY-effect authoring entities and service (fork feature).

A DIY effect is uploaded as one indivisible document, so the entity model is a
*staging* model: five config controls per zone write a record held in
``diy_state``, and one button (or the ``apply_diy_effect`` service) turns the
assembled records into frames. The tests are organised around the properties
that design has to have:

1. Wiring — both options gate everything, and only SKUs whose profile declares
   a DIY layout get entities at all.
2. Staging — every control writes the store and nothing else, and a sibling
   entity built from the same store sees it.
3. Palette parsing — the one field with a syntax, so the one field that can be
   given nonsense.
4. Apply — byte-identical to what the codec produces for the staged document,
   and a readable error for every way a draft can be unsendable.
5. Restore — a restart rebuilds the draft, without notifying anything.
6. The service — self-contained (an omitted zone is off, not "whatever was
   staged"), and it leaves the config entities showing what it sent.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.govee import diy_state, services
from custom_components.govee.api import lan_raw_write
from custom_components.govee.api.lan_client import LanDeviceInfo
from custom_components.govee.api.protocol import (
    DiyZoneEffect,
    GoveeCodec,
    get_profile,
)
from custom_components.govee.const import (
    CONF_ENABLE_LAN_RAW_WRITE,
    CONF_ENABLE_ZONE_LIGHTS,
)
from custom_components.govee.models import (
    GoveeCapability,
    GoveeDevice,
    GoveeDeviceState,
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
from custom_components.govee.platforms.diy_effect import (
    GoveeDiyApplyButton,
    GoveeDiyDirectionSelect,
    GoveeDiyFlowRateNumber,
    GoveeDiyPaletteText,
    async_diy_button_entities,
    async_diy_number_entities,
    async_diy_select_entities,
    async_diy_text_entities,
    format_palette,
    parse_palette,
)

DEVICE_ID = "AA:BB:CC:DD:EE:FF:60:B0"
IP = "10.20.0.51"

PROFILE = get_profile("H60B0")


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
        """Every frame hex from the first envelope, in order."""
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
) -> Any:
    device = device or _device()
    coordinator = MagicMock()
    # Real coordinators have neither registry until the modules build them;
    # MagicMock would otherwise auto-create a Mock and swallow the lookup.
    coordinator._govee_zone_state_registry = None
    coordinator._govee_diy_state_store = None
    coordinator.devices = {device.device_id: device}
    coordinator.config_entry.options = {CONF_ENABLE_LAN_RAW_WRITE: lan_raw_write_on}
    coordinator.async_control_device = AsyncMock(return_value=True)
    coordinator.last_update_success = True
    state = GoveeDeviceState.create_empty(device.device_id)
    state.power_state = True
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


def _entry(
    coordinator: Any, *, zone_lights: bool = True, lan_raw_write_on: bool | None = None
) -> Any:
    if lan_raw_write_on is None:
        lan_raw_write_on = bool(
            coordinator.config_entry.options.get(CONF_ENABLE_LAN_RAW_WRITE, False)
        )
    entry = MagicMock()
    entry.runtime_data = coordinator
    entry.options = {
        CONF_ENABLE_ZONE_LIGHTS: zone_lights,
        CONF_ENABLE_LAN_RAW_WRITE: lan_raw_write_on,
    }
    return entry


def _mounted(entities: list[Any]) -> list[Any]:
    """Give entities the two attributes an unmounted entity is missing."""
    for entity in entities:
        entity.hass = MagicMock()
        entity.async_write_ha_state = MagicMock()
    return entities


def _controls(coordinator: Any, entry: Any) -> dict[str, Any]:
    """Every DIY entity of the one device, keyed ``<class>.<zone>``."""
    built = _mounted(
        [
            *async_diy_select_entities(coordinator, entry),
            *async_diy_number_entities(coordinator, entry),
            *async_diy_text_entities(coordinator, entry),
            *async_diy_button_entities(coordinator, entry),
        ]
    )
    controls: dict[str, Any] = {}
    for entity in built:
        key = (
            type(entity).__name__.replace("GoveeDiy", "").replace("Button", "").lower()
        )
        zone = getattr(entity, "_zone_key", None)
        controls[key if zone is None else f"{key}.{zone}"] = entity
    return controls


# ==============================================================================
# 1. Platform wiring
# ==============================================================================


class TestWiring:
    """Which entities the options and the profile table produce."""

    def test_options_off_creates_no_diy_entities(self):
        coordinator = _coordinator()
        entry = _entry(coordinator, zone_lights=False)

        assert async_diy_select_entities(coordinator, entry) == []
        assert async_diy_number_entities(coordinator, entry) == []
        assert async_diy_text_entities(coordinator, entry) == []
        assert async_diy_button_entities(coordinator, entry) == []

    def test_the_raw_lan_option_is_required_too(self):
        """DIY has no cloud equivalent at all, so one checkbox is not enough."""
        coordinator = _coordinator(lan_raw_write_on=False)
        entry = _entry(coordinator, zone_lights=True, lan_raw_write_on=False)

        assert async_diy_select_entities(coordinator, entry) == []
        assert async_diy_button_entities(coordinator, entry) == []

    def test_a_sku_with_no_diy_layout_gets_nothing(self):
        """H6076 is in the profile table but declares no DIY effect spec."""
        coordinator = _coordinator(_device(sku="H6076"))
        entry = _entry(coordinator)

        assert async_diy_select_entities(coordinator, entry) == []
        assert async_diy_number_entities(coordinator, entry) == []
        assert async_diy_text_entities(coordinator, entry) == []
        assert async_diy_button_entities(coordinator, entry) == []

    def test_an_unprofiled_sku_gets_nothing(self):
        coordinator = _coordinator(_device(sku="H60B3"))

        assert async_diy_text_entities(coordinator, _entry(coordinator)) == []

    def test_one_control_set_per_diy_zone(self):
        coordinator = _coordinator()
        controls = _controls(coordinator, _entry(coordinator))

        assert sorted(controls) == [
            "apply",
            "directionselect.ripple",
            "flowratenumber.ripple",
            "modeselect.ring",
            "modeselect.ripple",
            "palettetext.ring",
            "palettetext.ripple",
            "speednumber.ring",
            "speednumber.ripple",
        ]

    def test_direction_and_flow_rate_follow_the_table_not_the_zone_name(self):
        """Only the ripple record carries a direction/flow tail."""
        coordinator = _coordinator()
        controls = _controls(coordinator, _entry(coordinator))

        directional = [z.zone_key for z in PROFILE.diy.zones if z.has_direction]
        assert directional == ["ripple"]
        assert isinstance(controls["directionselect.ripple"], GoveeDiyDirectionSelect)
        assert isinstance(controls["flowratenumber.ripple"], GoveeDiyFlowRateNumber)
        assert "directionselect.ring" not in controls
        assert "flowratenumber.ring" not in controls

    def test_unique_ids_and_names(self):
        coordinator = _coordinator()
        controls = _controls(coordinator, _entry(coordinator))

        assert controls["modeselect.ripple"].unique_id == f"{DEVICE_ID}_diy_mode_ripple"
        assert controls["speednumber.ring"].unique_id == f"{DEVICE_ID}_diy_speed_ring"
        assert (
            controls["directionselect.ripple"].unique_id
            == f"{DEVICE_ID}_diy_direction_ripple"
        )
        assert (
            controls["flowratenumber.ripple"].unique_id
            == f"{DEVICE_ID}_diy_flow_rate_ripple"
        )
        assert controls["palettetext.ring"].unique_id == f"{DEVICE_ID}_diy_palette_ring"
        assert controls["apply"].unique_id == f"{DEVICE_ID}_diy_apply"

        assert controls["modeselect.ripple"].name == "Ripple DIY mode"
        assert controls["speednumber.ring"].name == "Ring DIY speed"
        # Distinct from the live zone flow-rate number in zone_light.py.
        assert controls["flowratenumber.ripple"].name == "Ripple DIY flow rate"
        assert controls["apply"].name == "Apply DIY effect"

    def test_the_apply_button_is_one_per_device(self):
        coordinator = _coordinator()
        buttons = async_diy_button_entities(coordinator, _entry(coordinator))

        assert len(buttons) == 1
        assert isinstance(buttons[0], GoveeDiyApplyButton)

    def test_group_devices_are_skipped(self):
        device = _device()
        coordinator = _coordinator(device)
        coordinator.devices = {
            device.device_id: MagicMock(wraps=device, is_group=True, sku="H60B0")
        }

        assert async_diy_button_entities(coordinator, _entry(coordinator)) == []

    def test_mode_options_are_the_zones_own_table_in_order(self):
        """The two zones do not share an enum — twinkle is 11 here and 3 there."""
        coordinator = _coordinator()
        controls = _controls(coordinator, _entry(coordinator))

        assert controls["modeselect.ripple"].options == [
            "None",
            "Gradient",
            "Breathe",
            "Rainbow",
            "Twinkle",
            "Jumping",
        ]
        assert controls["modeselect.ring"].options[:4] == [
            "None",
            "Gradient",
            "Breathe",
            "Twinkle",
        ]

    @pytest.mark.asyncio
    async def test_the_text_platform_adds_the_palettes(self):
        from custom_components.govee import text as text_mod

        coordinator = _coordinator()
        entry = _entry(coordinator)
        added: list = []
        await text_mod.async_setup_entry(
            MagicMock(), entry, lambda ents: added.extend(ents)
        )

        assert sorted(e._zone_key for e in added) == ["ring", "ripple"]
        assert all(isinstance(e, GoveeDiyPaletteText) for e in added)


# ==============================================================================
# 2. Staging — the controls write the store and nothing else
# ==============================================================================


class TestStaging:
    """Every control is optimistic and device-free until the button is pressed."""

    @pytest.mark.asyncio
    async def test_selecting_a_mode_stages_the_zones_own_int(self, raw_client):
        coordinator = _coordinator()
        controls = _controls(coordinator, _entry(coordinator))

        await controls["modeselect.ripple"].async_select_option("Twinkle")
        await controls["modeselect.ring"].async_select_option("Twinkle")

        store = diy_state.store(coordinator)
        assert store.mode(DEVICE_ID, "ripple") == 11
        assert store.mode(DEVICE_ID, "ring") == 3
        assert raw_client.envelopes == []

    @pytest.mark.asyncio
    async def test_a_sibling_entity_sees_the_staged_mode(self):
        """State lives in the store, not on the entity that happened to set it."""
        coordinator = _coordinator()
        entry = _entry(coordinator)
        first = _controls(coordinator, entry)["modeselect.ripple"]
        second = _controls(coordinator, entry)["modeselect.ripple"]

        await first.async_select_option("Gradient")

        assert second.current_option == "Gradient"

    @pytest.mark.asyncio
    async def test_a_staged_write_repaints_the_zones_other_entities(self):
        """The service rewrites records; the config entities must follow."""
        coordinator = _coordinator()
        controls = _controls(coordinator, _entry(coordinator))
        speed = controls["speednumber.ripple"]
        with patch(
            "homeassistant.helpers.update_coordinator.CoordinatorEntity.async_added_to_hass",
            AsyncMock(),
        ):
            speed.async_on_remove = MagicMock()
            speed.async_get_last_state = AsyncMock(return_value=None)
            await speed.async_added_to_hass()

        diy_state.store(coordinator).set_speed(DEVICE_ID, "ripple", 80)

        assert speed.native_value == 80
        speed.async_write_ha_state.assert_called()

    @pytest.mark.asyncio
    async def test_numbers_stage_speed_and_flow_rate_separately(self, raw_client):
        coordinator = _coordinator()
        controls = _controls(coordinator, _entry(coordinator))

        await controls["speednumber.ripple"].async_set_native_value(70)
        await controls["flowratenumber.ripple"].async_set_native_value(30)

        store = diy_state.store(coordinator)
        assert store.speed(DEVICE_ID, "ripple") == 70
        assert store.flow_rate(DEVICE_ID, "ripple") == 30
        assert controls["speednumber.ripple"].native_value == 70
        assert raw_client.envelopes == []

    def test_number_defaults_are_the_encoders_own_defaults(self):
        coordinator = _coordinator()
        controls = _controls(coordinator, _entry(coordinator))

        assert controls["speednumber.ripple"].native_value == 50
        assert controls["flowratenumber.ripple"].native_value == 50
        assert controls["speednumber.ripple"].native_min_value == 1
        assert controls["speednumber.ripple"].native_max_value == 100

    @pytest.mark.asyncio
    async def test_direction_maps_labels_to_protocol_codes(self, raw_client):
        coordinator = _coordinator()
        direction = _controls(coordinator, _entry(coordinator))[
            "directionselect.ripple"
        ]

        assert direction.options == ["CW", "CCW", "Reverse"]
        assert direction.current_option == "CW"

        await direction.async_select_option("Reverse")

        assert diy_state.store(coordinator).direction(DEVICE_ID, "ripple") == 3
        assert direction.current_option == "Reverse"
        assert raw_client.envelopes == []

    @pytest.mark.asyncio
    async def test_setting_a_palette_stages_rgb_triples(self, raw_client):
        coordinator = _coordinator()
        palette = _controls(coordinator, _entry(coordinator))["palettetext.ring"]

        await palette.async_set_value("#ff0000 #00b0ff")

        assert diy_state.store(coordinator).colors(DEVICE_ID, "ring") == (
            (255, 0, 0),
            (0, 176, 255),
        )
        assert palette.native_value == "#ff0000 #00b0ff"
        assert raw_client.envelopes == []

    def test_an_empty_palette_reports_none_not_an_empty_string(self):
        """HA raises on a state that contradicts the entity's own pattern."""
        coordinator = _coordinator()
        palette = _controls(coordinator, _entry(coordinator))["palettetext.ripple"]

        assert palette.native_value is None

    def test_every_staging_entity_is_a_config_control(self):
        from homeassistant.const import EntityCategory

        coordinator = _coordinator()
        controls = _controls(coordinator, _entry(coordinator))

        assert all(
            entity.entity_category is EntityCategory.CONFIG
            for entity in controls.values()
        )

    def test_staging_entities_do_not_need_the_lan(self):
        """A draft can be edited while the lamp is off the network."""
        coordinator = _coordinator(on_lan=False)
        controls = _controls(coordinator, _entry(coordinator))

        assert controls["modeselect.ripple"].available is True
        assert controls["palettetext.ripple"].available is True
        # ... but the one control that has to send something does.
        assert controls["apply"].available is False


# ==============================================================================
# 3. Palette parsing
# ==============================================================================


class TestPalette:
    """The one staged field with a syntax of its own."""

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("#ff0000", ((255, 0, 0),)),
            ("ff0000", ((255, 0, 0),)),
            ("#FF0000 #00B0FF", ((255, 0, 0), (0, 176, 255))),
            ("#ff0000,#00b0ff", ((255, 0, 0), (0, 176, 255))),
            ("#ff0000, #00b0ff", ((255, 0, 0), (0, 176, 255))),
            ("  #ff0000   #00b0ff  ", ((255, 0, 0), (0, 176, 255))),
        ],
    )
    def test_accepted_spellings(self, text, expected):
        assert parse_palette(text, "Lamp") == expected

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "   ",
            "#ff00",
            "#ff00zz",
            "red",
            "#ff0000 #12345",
            " ".join(["#ff0000"] * 17),
        ],
    )
    def test_rejected_spellings(self, text):
        with pytest.raises(HomeAssistantError):
            parse_palette(text, "Lamp")

    def test_the_cap_is_the_protocols_cap(self):
        assert parse_palette(" ".join(["#010203"] * 16), "Lamp") == ((1, 2, 3),) * 16

    def test_the_canonical_form_is_lowercase_and_hash_prefixed(self):
        assert format_palette(((255, 0, 0), (0, 176, 255))) == "#ff0000 #00b0ff"

    def test_the_published_pattern_matches_the_canonical_form(self):
        import re

        from custom_components.govee.platforms.diy_effect import PALETTE_PATTERN

        coordinator = _coordinator()
        palette = _controls(coordinator, _entry(coordinator))["palettetext.ripple"]

        assert palette.pattern == PALETTE_PATTERN
        assert re.match(PALETTE_PATTERN, format_palette(((1, 2, 3),) * 16))
        assert not re.match(PALETTE_PATTERN, format_palette(((1, 2, 3),) * 17))
        assert palette.native_max >= len(format_palette(((1, 2, 3),) * 16))

    @pytest.mark.asyncio
    async def test_bad_input_raises_and_stages_nothing(self):
        coordinator = _coordinator()
        palette = _controls(coordinator, _entry(coordinator))["palettetext.ripple"]

        with pytest.raises(HomeAssistantError):
            await palette.async_set_value("not a colour")

        assert diy_state.store(coordinator).colors(DEVICE_ID, "ripple") == ()


# ==============================================================================
# 4. Apply
# ==============================================================================


def _expected_hexes(effects: dict[str, DiyZoneEffect | None]) -> list[str]:
    """What the codec produces for a document, as hex — the only expectation."""
    return [frame.hex() for frame in GoveeCodec(PROFILE).diy_effect(effects)]


class TestApply:
    """The button turns the staged document into exactly the codec's frames."""

    @pytest.mark.asyncio
    async def test_press_sends_the_codecs_frames_for_the_staged_document(
        self, raw_client
    ):
        coordinator = _coordinator()
        controls = _controls(coordinator, _entry(coordinator))

        await controls["modeselect.ripple"].async_select_option("Twinkle")
        await controls["speednumber.ripple"].async_set_native_value(60)
        await controls["flowratenumber.ripple"].async_set_native_value(30)
        await controls["directionselect.ripple"].async_select_option("CCW")
        await controls["palettetext.ripple"].async_set_value("#ff0000 #00b0ff")
        await controls["apply"].async_press()

        assert raw_client.hexes == _expected_hexes(
            {
                "ripple": DiyZoneEffect(
                    mode=11,
                    speed=60,
                    colors=((255, 0, 0), (0, 176, 255)),
                    direction=2,
                    flow_rate=30,
                ),
                "ring": None,
            }
        )

    @pytest.mark.asyncio
    async def test_both_zones_ride_one_upload(self, raw_client):
        coordinator = _coordinator()
        controls = _controls(coordinator, _entry(coordinator))

        await controls["modeselect.ripple"].async_select_option("Gradient")
        await controls["palettetext.ripple"].async_set_value("#ff0000")
        await controls["modeselect.ring"].async_select_option("Cover")
        await controls["palettetext.ring"].async_set_value("#00b0ff #ffffff")
        await controls["apply"].async_press()

        assert raw_client.hexes == _expected_hexes(
            {
                "ripple": DiyZoneEffect(
                    mode=1, speed=50, colors=((255, 0, 0),), direction=1, flow_rate=50
                ),
                "ring": DiyZoneEffect(
                    mode=9, speed=50, colors=((0, 176, 255), (255, 255, 255))
                ),
            }
        )
        # One envelope: the 0xA3 chunks and their commit frame must not be
        # split across datagrams.
        assert len(raw_client.envelopes[0][1]) == len(raw_client.hexes)

    @pytest.mark.asyncio
    async def test_the_ring_never_gets_a_direction_or_flow_tail(self, raw_client):
        """Staged values for fields the ring has no room for are dropped, not sent."""
        coordinator = _coordinator()
        controls = _controls(coordinator, _entry(coordinator))
        diy_state.store(coordinator).set_direction(DEVICE_ID, "ring", 3)
        diy_state.store(coordinator).set_flow_rate(DEVICE_ID, "ring", 90)

        await controls["modeselect.ring"].async_select_option("Rainbow")
        await controls["palettetext.ring"].async_set_value("#ff0000")
        await controls["apply"].async_press()

        assert raw_client.hexes == _expected_hexes(
            {
                "ripple": None,
                "ring": DiyZoneEffect(mode=4, speed=50, colors=((255, 0, 0),)),
            }
        )

    @pytest.mark.asyncio
    async def test_all_zones_none_raises_and_sends_nothing(self, raw_client):
        coordinator = _coordinator()
        controls = _controls(coordinator, _entry(coordinator))

        with pytest.raises(HomeAssistantError, match="cannot build this DIY effect"):
            await controls["apply"].async_press()

        assert raw_client.envelopes == []

    @pytest.mark.asyncio
    async def test_a_mode_with_no_palette_raises(self, raw_client):
        coordinator = _coordinator()
        controls = _controls(coordinator, _entry(coordinator))

        await controls["modeselect.ripple"].async_select_option("Twinkle")

        with pytest.raises(HomeAssistantError, match="palette colour"):
            await controls["apply"].async_press()

        assert raw_client.envelopes == []

    @pytest.mark.asyncio
    async def test_without_a_lan_target_the_press_raises(self, raw_client):
        coordinator = _coordinator(on_lan=False)
        controls = _controls(coordinator, _entry(coordinator))

        await controls["modeselect.ring"].async_select_option("Gradient")
        await controls["palettetext.ring"].async_set_value("#ff0000")

        with pytest.raises(HomeAssistantError, match="local network"):
            await controls["apply"].async_press()

        assert raw_client.envelopes == []

    @pytest.mark.asyncio
    async def test_a_failed_send_raises(self, raw_client):
        coordinator = _coordinator()
        controls = _controls(coordinator, _entry(coordinator))
        raw_client.error = OSError("network unreachable")

        await controls["modeselect.ring"].async_select_option("Gradient")
        await controls["palettetext.ring"].async_set_value("#ff0000")

        with pytest.raises(HomeAssistantError, match="could not be sent"):
            await controls["apply"].async_press()


# ==============================================================================
# 5. Restore
# ==============================================================================


async def _restore(entity: Any, state: str | None) -> None:
    """Run ``async_added_to_hass`` with a restored state, off the state machine."""
    entity.async_on_remove = MagicMock()
    last = None
    if state is not None:
        last = MagicMock()
        last.state = state
    entity.async_get_last_state = AsyncMock(return_value=last)
    with patch(
        "homeassistant.helpers.update_coordinator.CoordinatorEntity.async_added_to_hass",
        AsyncMock(),
    ):
        await entity.async_added_to_hass()


class TestRestore:
    """A restart rebuilds the draft, because nothing else can."""

    @pytest.mark.asyncio
    async def test_every_staged_field_comes_back(self):
        coordinator = _coordinator()
        controls = _controls(coordinator, _entry(coordinator))

        await _restore(controls["modeselect.ripple"], "Twinkle")
        await _restore(controls["speednumber.ripple"], "60")
        await _restore(controls["flowratenumber.ripple"], "30")
        await _restore(controls["directionselect.ripple"], "CCW")
        await _restore(controls["palettetext.ripple"], "#ff0000 #00b0ff")

        params = diy_state.store(coordinator).params(DEVICE_ID, "ripple")
        assert params.mode == 11
        assert params.speed == 60
        assert params.flow_rate == 30
        assert params.direction == 2
        assert params.colors == ((255, 0, 0), (0, 176, 255))

    @pytest.mark.asyncio
    async def test_restore_does_not_notify(self):
        """The value came from the entity; echoing it back is noise."""
        coordinator = _coordinator()
        controls = _controls(coordinator, _entry(coordinator))
        other = controls["speednumber.ripple"]
        await _restore(other, "60")
        other.async_write_ha_state.reset_mock()

        await _restore(controls["modeselect.ripple"], "Gradient")

        other.async_write_ha_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_nothing_to_restore_leaves_the_defaults(self):
        coordinator = _coordinator()
        controls = _controls(coordinator, _entry(coordinator))

        await _restore(controls["speednumber.ripple"], None)
        await _restore(controls["modeselect.ripple"], "unavailable")

        params = diy_state.store(coordinator).params(DEVICE_ID, "ripple")
        assert params.mode == 0
        assert params.speed == 50

    @pytest.mark.asyncio
    async def test_a_restored_draft_uploads_unchanged(self, raw_client):
        coordinator = _coordinator()
        controls = _controls(coordinator, _entry(coordinator))

        await _restore(controls["modeselect.ring"], "Gradient")
        await _restore(controls["palettetext.ring"], "#ff0000")
        await controls["apply"].async_press()

        assert raw_client.hexes == _expected_hexes(
            {
                "ripple": None,
                "ring": DiyZoneEffect(mode=1, speed=50, colors=((255, 0, 0),)),
            }
        )


# ==============================================================================
# 6. The govee.apply_diy_effect service
# ==============================================================================


class TestService:
    """``govee.apply_diy_effect`` — deterministic, and it updates the draft.

    The handler is a closure inside ``async_setup_services``, so it is captured
    by registering the services against a stub hass. Payloads go through the
    *real* schema before reaching it, so no test can pass data the service
    would have rejected.
    """

    @staticmethod
    async def _handler(monkeypatch, coordinator):
        hass = MagicMock()
        registered: dict[str, tuple[Any, Any]] = {}
        hass.services.async_register = (
            lambda domain, name, handler, schema=None: registered.__setitem__(
                name, (handler, schema)
            )
        )
        await services.async_setup_services(hass)
        monkeypatch.setattr(
            services,
            "_get_coordinator_for_device",
            lambda _hass, _device_id: coordinator,
        )
        handler, schema = registered[services.SERVICE_APPLY_DIY_EFFECT]

        async def _call(payload: dict[str, Any]) -> None:
            await handler(MagicMock(data=schema(payload)))

        return _call

    @pytest.mark.asyncio
    async def test_happy_path_sends_the_codecs_frames(self, monkeypatch, raw_client):
        coordinator = _coordinator()
        call = await self._handler(monkeypatch, coordinator)

        await call(
            {
                "device_id": DEVICE_ID,
                "ripple": {
                    "mode": "twinkle",
                    "speed": 60,
                    "colors": [[255, 0, 0], [0, 176, 255]],
                    "direction": "ccw",
                    "flow_rate": 30,
                },
                "ring": {"mode": "gradient", "colors": [[255, 255, 255]]},
            }
        )

        assert raw_client.hexes == _expected_hexes(
            {
                "ripple": DiyZoneEffect(
                    mode=11,
                    speed=60,
                    colors=((255, 0, 0), (0, 176, 255)),
                    direction=2,
                    flow_rate=30,
                ),
                "ring": DiyZoneEffect(mode=1, speed=50, colors=((255, 255, 255),)),
            }
        )

    @pytest.mark.asyncio
    async def test_a_raw_int_mode_is_accepted(self, monkeypatch, raw_client):
        """The ripple's table is known incomplete; ints must pass through."""
        coordinator = _coordinator()
        call = await self._handler(monkeypatch, coordinator)

        await call(
            {"device_id": DEVICE_ID, "ripple": {"mode": 3, "colors": [[1, 2, 3]]}}
        )

        assert raw_client.hexes == _expected_hexes(
            {
                "ripple": DiyZoneEffect(
                    mode=3, speed=50, colors=((1, 2, 3),), direction=1, flow_rate=50
                ),
                "ring": None,
            }
        )

    @pytest.mark.asyncio
    async def test_an_omitted_zone_is_switched_off_not_left_staged(
        self, monkeypatch, raw_client
    ):
        """The call is self-contained: what is not named is off."""
        coordinator = _coordinator()
        diy_state.store(coordinator).set_mode(DEVICE_ID, "ring", 9)
        diy_state.store(coordinator).set_colors(DEVICE_ID, "ring", ((1, 2, 3),))
        call = await self._handler(monkeypatch, coordinator)

        await call(
            {
                "device_id": DEVICE_ID,
                "ripple": {"mode": "gradient", "colors": [[255, 0, 0]]},
            }
        )

        assert raw_client.hexes == _expected_hexes(
            {
                "ripple": DiyZoneEffect(
                    mode=1, speed=50, colors=((255, 0, 0),), direction=1, flow_rate=50
                ),
                "ring": None,
            }
        )
        assert diy_state.store(coordinator).mode(DEVICE_ID, "ring") == 0

    @pytest.mark.asyncio
    async def test_the_config_entities_show_what_was_sent(
        self, monkeypatch, raw_client
    ):
        coordinator = _coordinator()
        controls = _controls(coordinator, _entry(coordinator))
        call = await self._handler(monkeypatch, coordinator)

        await call(
            {
                "device_id": DEVICE_ID,
                "ripple": {
                    "mode": "rainbow",
                    "speed": 80,
                    "colors": [[255, 0, 0]],
                    "direction": "reverse",
                },
            }
        )

        assert controls["modeselect.ripple"].current_option == "Rainbow"
        assert controls["speednumber.ripple"].native_value == 80
        assert controls["directionselect.ripple"].current_option == "Reverse"
        assert controls["palettetext.ripple"].native_value == "#ff0000"
        assert controls["modeselect.ring"].current_option == "None"

    @pytest.mark.asyncio
    async def test_a_bad_mode_name_raises_and_sends_nothing(
        self, monkeypatch, raw_client
    ):
        coordinator = _coordinator()
        call = await self._handler(monkeypatch, coordinator)

        with pytest.raises(HomeAssistantError, match="no DIY mode"):
            await call(
                {
                    "device_id": DEVICE_ID,
                    "ring": {"mode": "sparkle", "colors": [[1, 2, 3]]},
                }
            )

        assert raw_client.envelopes == []

    @pytest.mark.asyncio
    async def test_a_sku_without_diy_raises(self, monkeypatch, raw_client):
        coordinator = _coordinator(_device(sku="H6076"))
        call = await self._handler(monkeypatch, coordinator)

        with pytest.raises(HomeAssistantError, match="does not support DIY effects"):
            await call(
                {
                    "device_id": DEVICE_ID,
                    "ring": {"mode": "gradient", "colors": [[1, 2, 3]]},
                }
            )

    @pytest.mark.asyncio
    async def test_an_unknown_device_raises(self, monkeypatch, raw_client):
        call = await self._handler(monkeypatch, None)

        with pytest.raises(HomeAssistantError, match="not known"):
            await call({"device_id": DEVICE_ID})

    @pytest.mark.asyncio
    async def test_naming_no_zone_at_all_raises(self, monkeypatch, raw_client):
        coordinator = _coordinator()
        call = await self._handler(monkeypatch, coordinator)

        with pytest.raises(HomeAssistantError, match="cannot build this DIY effect"):
            await call({"device_id": DEVICE_ID})

    @pytest.mark.asyncio
    async def test_direction_and_flow_are_not_offered_for_the_ring(self, monkeypatch):
        """Schema-level: the ring's wire record has no tail to put them in."""
        import voluptuous as vol

        with pytest.raises(vol.Invalid):
            services.SERVICE_APPLY_DIY_EFFECT_SCHEMA(
                {
                    "device_id": DEVICE_ID,
                    "ring": {"mode": "gradient", "direction": "cw"},
                }
            )

    @pytest.mark.asyncio
    async def test_the_service_is_unregistered_on_unload(self):
        hass = MagicMock()
        removed: list[str] = []
        hass.services.async_remove = lambda domain, name: removed.append(name)

        await services.async_unload_services(hass)

        assert services.SERVICE_APPLY_DIY_EFFECT in removed
