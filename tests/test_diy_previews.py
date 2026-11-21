"""DIY mode preview artwork and the sensors that surface it (fork feature).

The mode selects offer bare names; the shipped previews show what each one
renders. The tests are organised around what can go wrong with artwork that
lives in one place and is named in another:

1. Drift — every mode in the profile table has a file, every file has a mode.
   This is the one that catches a profile edit made without artwork.
2. Serving — the static path is registered once, at a stable prefix.
3. The sensors — state follows the mode select, attributes carry the whole map.
4. Gating — a device with no DIY layout gets none of this.
5. String lights — the same drift and URL checks for the H70CX effects.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml
from aiohttp.test_utils import make_mocked_request
from homeassistant.components.http.static import CachingStaticResource

from custom_components.govee import diy_previews
from custom_components.govee.api.protocol import get_profile, h70cx, h70cx_basic
from custom_components.govee.const import (
    CONF_ENABLE_LAN_RAW_WRITE,
    CONF_ENABLE_ZONE_LIGHTS,
    SUFFIX_DIY_PREVIEW,
)
from custom_components.govee.diy_previews import (
    PREVIEW_FILES,
    STRING_EFFECT_FILES,
    STRING_PREVIEW_SUBDIR,
    URL_BASE,
    preview_dir,
    preview_map,
    preview_url,
    string_effect_preview_map,
    string_effect_preview_url,
    string_preview_dir,
)
from custom_components.govee.diy_state import store
from custom_components.govee.models import GoveeCapability, GoveeDevice
from custom_components.govee.models.device import (
    CAPABILITY_ON_OFF,
    DEVICE_TYPE_LIGHT,
    INSTANCE_POWER,
)
from custom_components.govee.platforms.diy_effect import (
    async_diy_preview_entities,
    async_diy_select_entities,
)

DEVICE_ID = "AA:BB:DD:EE:FF:44:55:66"  # synthetic; H60B0 prefix


def _device(sku: str = "H60B0") -> GoveeDevice:
    return GoveeDevice(
        device_id=DEVICE_ID,
        sku=sku,
        name="Uplighter Floor Lamp",
        device_type=DEVICE_TYPE_LIGHT,
        capabilities=(GoveeCapability(type=CAPABILITY_ON_OFF, instance=INSTANCE_POWER, parameters={}),),
    )


def _coordinator(device: GoveeDevice | None = None) -> Any:
    device = device or _device()
    coordinator = MagicMock()
    coordinator._govee_diy_state_store = None
    coordinator._govee_zone_state_registry = None
    coordinator.devices = {device.device_id: device}
    coordinator.last_update_success = True
    coordinator.config_entry.options = {CONF_ENABLE_LAN_RAW_WRITE: True}
    return coordinator


def _entry(*, zone_lights: bool = True) -> Any:
    entry = MagicMock()
    entry.options = {
        CONF_ENABLE_ZONE_LIGHTS: zone_lights,
        CONF_ENABLE_LAN_RAW_WRITE: True,
    }
    return entry


def _mounted(entities: list[Any]) -> list[Any]:
    for entity in entities:
        entity.hass = MagicMock()
        entity.async_write_ha_state = MagicMock()
    return entities


def _previews(coordinator: Any, entry: Any) -> dict[str, Any]:
    """The preview sensors of the one device, keyed by zone."""
    return {entity._zone_key: entity for entity in _mounted(async_diy_preview_entities(coordinator, entry))}


class TestArtworkDrift:
    """The join between the profile table and the shipped files, both ways."""

    def test_every_mode_in_every_diy_zone_has_a_shipped_file(self):
        profile = get_profile("H60B0")
        assert profile.diy is not None

        missing: list[str] = []
        for zone in profile.diy.zones:
            files = PREVIEW_FILES.get(zone.zone_key, {})
            for mode in zone.modes:
                if mode not in files:
                    missing.append(f"{zone.zone_key}.{mode}")
                elif not (preview_dir() / files[mode]).is_file():
                    missing.append(f"{zone.zone_key}.{mode} -> {files[mode]} (absent)")

        assert not missing, f"DIY modes with no preview artwork: {missing}"

    def test_every_shipped_file_is_claimed_by_a_mode(self):
        claimed = {name for zone in PREVIEW_FILES.values() for name in zone.values()}
        on_disk = {path.name for path in preview_dir().glob("*.png")}

        assert on_disk == claimed

    def test_the_map_names_no_mode_the_profile_does_not_have(self):
        profile = get_profile("H60B0")
        assert profile.diy is not None
        table = {zone.zone_key: set(zone.modes) for zone in profile.diy.zones}

        assert set(PREVIEW_FILES) == set(table)
        for zone_key, files in PREVIEW_FILES.items():
            assert set(files) == table[zone_key]

    def test_the_simple_authoring_block_is_not_shipped(self):
        # ~21 MB of vendor artwork for a mode the integration does not expose.
        assert not list(preview_dir().glob("*_simple_*.png"))

    def test_filenames_carry_their_zone(self):
        # Guards a copy/paste between the two tables: the ripple's twinkle and
        # the ring's twinkle are different images with different mode ints.
        for zone_key, files in PREVIEW_FILES.items():
            for mode, filename in files.items():
                assert f"_{zone_key}_" in filename, (zone_key, mode, filename)


class TestUrls:
    """What a URL is, and what an unnameable mode previews as."""

    def test_url_is_the_stable_prefix_plus_the_filename(self):
        assert preview_url("ring", "graffiti") == f"{URL_BASE}/21_ring_graffiti.png"

    def test_the_two_zones_answer_differently_for_the_same_mode_name(self):
        assert preview_url("ripple", "twinkle") != preview_url("ring", "twinkle")

    @pytest.mark.parametrize("mode", [None, "", "not_a_mode", "graffiti"])
    def test_a_mode_the_ripple_cannot_name_falls_back_to_none(self, mode):
        # The ripple accepts ring-enum ints the table does not bind, so an
        # unnameable staged mode is legal and must still preview as something.
        assert preview_url("ripple", mode) == f"{URL_BASE}/16_ripple_none.png"

    def test_an_unknown_zone_has_no_artwork(self):
        assert preview_url("downlight", "gradient") is None

    def test_preview_map_follows_the_tables_order(self):
        zone = get_profile("H60B0").diy.zones[1]
        assert zone.zone_key == "ring"

        urls = preview_map(zone)

        assert list(urls) == list(zone.modes)
        assert urls["none"] == f"{URL_BASE}/27_ring_none.png"


class TestStaticPath:
    """Registration is once per HA, at a constant prefix."""

    @pytest.mark.asyncio
    async def test_registers_the_directory_at_the_url_base(self):
        hass = MagicMock()
        hass.data = {}
        hass.http.async_register_static_paths = AsyncMock()

        await diy_previews.async_register_previews(hass)

        hass.http.async_register_static_paths.assert_awaited_once()
        (configs,) = hass.http.async_register_static_paths.await_args.args
        assert len(configs) == 1
        assert configs[0].url_path == URL_BASE
        assert configs[0].path == str(preview_dir())

    @pytest.mark.asyncio
    async def test_a_second_call_does_not_register_again(self):
        hass = MagicMock()
        hass.data = {}
        hass.http.async_register_static_paths = AsyncMock()

        await diy_previews.async_register_previews(hass)
        await diy_previews.async_register_previews(hass)

        assert hass.http.async_register_static_paths.await_count == 1

    @pytest.mark.asyncio
    async def test_concurrent_setups_register_once_and_both_succeed(self):
        """The registration await is a yield point two entries can both reach."""
        hass = MagicMock()
        hass.data = {}

        async def slow_register(configs):
            await asyncio.sleep(0)
            if hass.http.async_register_static_paths.await_count > 1:
                raise RuntimeError("Path already registered")

        hass.http.async_register_static_paths = AsyncMock(side_effect=slow_register)

        await asyncio.gather(
            diy_previews.async_register_previews(hass),
            diy_previews.async_register_previews(hass),
        )

        assert hass.http.async_register_static_paths.await_count == 1

    def test_the_served_directory_exists_and_holds_the_artwork(self):
        assert preview_dir().is_dir()
        assert len(list(preview_dir().glob("*.png"))) == 17


class TestPreviewSensors:
    """One sensor per DIY zone, following the zone's mode select."""

    def test_one_sensor_per_declared_diy_zone(self):
        coordinator = _coordinator()

        sensors = _previews(coordinator, _entry())

        assert sorted(sensors) == ["ring", "ripple"]

    def test_unique_ids_use_the_diy_preview_suffix(self):
        coordinator = _coordinator()

        sensors = _previews(coordinator, _entry())

        assert sensors["ripple"].unique_id == (f"{DEVICE_ID}{SUFFIX_DIY_PREVIEW}ripple")

    def test_an_unstaged_zone_previews_as_none(self):
        coordinator = _coordinator()

        sensors = _previews(coordinator, _entry())

        assert sensors["ripple"].native_value == f"{URL_BASE}/16_ripple_none.png"
        assert sensors["ring"].native_value == f"{URL_BASE}/27_ring_none.png"

    def test_state_follows_the_mode_select(self):
        coordinator = _coordinator()
        entry = _entry()
        selects = {
            entity._zone_key: entity
            for entity in _mounted(async_diy_select_entities(coordinator, entry))
            if type(entity).__name__ == "GoveeDiyModeSelect"
        }
        sensors = _previews(coordinator, entry)

        selects["ring"]._store.set_mode(DEVICE_ID, "ring", 5)  # graffiti

        assert selects["ring"].current_option == "Graffiti"
        assert sensors["ring"].native_value == f"{URL_BASE}/21_ring_graffiti.png"
        assert sensors["ring"].extra_state_attributes["mode"] == "graffiti"

    def test_the_two_zones_track_independently(self):
        coordinator = _coordinator()
        sensors = _previews(coordinator, _entry())

        store(coordinator).set_mode(DEVICE_ID, "ripple", 11)  # ripple twinkle
        store(coordinator).set_mode(DEVICE_ID, "ring", 3)  # ring twinkle

        assert sensors["ripple"].native_value == f"{URL_BASE}/14_ripple_twinkle.png"
        assert sensors["ring"].native_value == f"{URL_BASE}/19_ring_twinkle.png"

    def test_a_raw_int_the_table_cannot_name_previews_as_none(self):
        coordinator = _coordinator()
        sensors = _previews(coordinator, _entry())

        # The ripple tolerates ring-enum ints (the app's Simple mode writes
        # them); 5 is "graffiti" on the ring and unbound on the ripple.
        store(coordinator).set_mode(DEVICE_ID, "ripple", 5)

        assert sensors["ripple"].native_value == f"{URL_BASE}/16_ripple_none.png"
        assert sensors["ripple"].extra_state_attributes["mode"] is None

    def test_attributes_carry_the_whole_zone_map(self):
        coordinator = _coordinator()
        profile = get_profile("H60B0")

        sensors = _previews(coordinator, _entry())

        for zone in profile.diy.zones:
            previews = sensors[zone.zone_key].extra_state_attributes["previews"]
            assert list(previews) == list(zone.modes)
            assert all(url.startswith(f"{URL_BASE}/") for url in previews.values())

    def test_attributes_name_the_zone(self):
        coordinator = _coordinator()

        sensors = _previews(coordinator, _entry())

        assert sensors["ring"].extra_state_attributes["diy_zone"] == "ring"

    @pytest.mark.asyncio
    async def test_added_to_hass_subscribes_to_the_zone_record(self):
        coordinator = _coordinator()
        sensor = _previews(coordinator, _entry())["ring"]
        sensor.async_on_remove = MagicMock()

        await sensor.async_added_to_hass()
        store(coordinator).set_mode(DEVICE_ID, "ring", 6)

        sensor.async_write_ha_state.assert_called()
        assert sensor.native_value == f"{URL_BASE}/22_ring_flow.png"

    def test_the_sensor_is_diagnostic(self):
        from homeassistant.const import EntityCategory

        coordinator = _coordinator()

        sensor = _previews(coordinator, _entry())["ring"]

        assert sensor.entity_category is EntityCategory.DIAGNOSTIC


class TestGating:
    """Devices and options that get no previews at all."""

    def test_a_non_diy_sku_gets_no_sensors(self):
        # The H6046 light bar is profiled but declares no DIY layout.
        coordinator = _coordinator(_device(sku="H6046"))

        assert async_diy_preview_entities(coordinator, _entry()) == []

    def test_an_unprofiled_sku_gets_no_sensors(self):
        coordinator = _coordinator(_device(sku="H6117"))

        assert async_diy_preview_entities(coordinator, _entry()) == []

    def test_zone_lights_off_gets_no_sensors(self):
        coordinator = _coordinator()

        assert async_diy_preview_entities(coordinator, _entry(zone_lights=False)) == []


# The string-light effects the vendor ships no preview for: the generic names,
# whose parameter blocks come from stock scenes, and the combo.
STRING_EFFECTS_WITHOUT_ARTWORK = frozenset(
    {
        "blend",
        "combo",
        "concentric",
        "diffuse",
        "flame",
        "flow",
        "layered",
        "rainbow",
        "rotate",
        "starlight",
        "vortex",
        "wave",
    }
)


def _accepted_string_effects() -> set[str]:
    """Every effect key ``apply_diy_effect`` accepts for a string light."""
    return {*h70cx.TEMPLATES, *h70cx_basic.NAMES}


def _selector_effects() -> list[str]:
    """The ``effect`` selector's options, in the order the action shows them."""
    services = yaml.safe_load((Path(diy_previews.__file__).parent / "services.yaml").read_text())
    field = services["apply_diy_effect"]["fields"]["effect_section"]["fields"]["effect"]
    return field["selector"]["select"]["options"]


class TestStringEffectArtwork:
    """The join between the string-light effect keys and the shipped files."""

    def test_every_accepted_effect_has_artwork_or_is_listed_without(self):
        accepted = _accepted_string_effects()

        assert set(STRING_EFFECT_FILES) | STRING_EFFECTS_WITHOUT_ARTWORK == accepted

    def test_no_effect_is_both_mapped_and_listed_without_artwork(self):
        assert not set(STRING_EFFECT_FILES) & STRING_EFFECTS_WITHOUT_ARTWORK

    def test_every_mapped_file_exists(self):
        missing = [name for name in STRING_EFFECT_FILES.values() if not (string_preview_dir() / name).is_file()]

        assert not missing

    def test_every_shipped_file_is_claimed_by_an_effect(self):
        on_disk = {path.name for path in string_preview_dir().iterdir()}

        assert on_disk == set(STRING_EFFECT_FILES.values())
        assert len(on_disk) == 31

    def test_files_are_named_by_effect_key(self):
        for effect, filename in STRING_EFFECT_FILES.items():
            assert filename == f"{effect}.png"

    def test_the_files_are_pngs(self):
        for filename in STRING_EFFECT_FILES.values():
            assert (string_preview_dir() / filename).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"

    def test_the_directory_sits_inside_the_served_one(self):
        assert string_preview_dir().parent == preview_dir()
        assert string_preview_dir().name == STRING_PREVIEW_SUBDIR


class TestStringEffectUrls:
    """What a string-light URL is, and what an effect with no artwork gets."""

    def test_url_is_the_stable_prefix_plus_the_subdirectory_and_key(self):
        assert string_effect_preview_url("spiral") == f"{URL_BASE}/h70cx/spiral.png"

    @pytest.mark.parametrize("effect", sorted(STRING_EFFECTS_WITHOUT_ARTWORK))
    def test_an_effect_without_artwork_has_no_url(self, effect):
        assert string_effect_preview_url(effect) is None

    @pytest.mark.parametrize("effect", [None, "", "not_an_effect"])
    def test_an_unknown_or_unset_effect_has_no_url(self, effect):
        assert string_effect_preview_url(effect) is None

    def test_the_map_holds_every_effect_with_artwork(self):
        urls = string_effect_preview_map()

        assert set(urls) == set(STRING_EFFECT_FILES)
        for effect, url in urls.items():
            assert url == string_effect_preview_url(effect)

    def test_the_map_follows_the_actions_display_order(self):
        urls = string_effect_preview_map()

        assert list(urls) == [effect for effect in _selector_effects() if effect in urls]

    def test_the_selector_offers_exactly_the_accepted_effects(self):
        assert set(_selector_effects()) == _accepted_string_effects()

    def test_the_map_is_a_fresh_copy(self):
        string_effect_preview_map()["spiral"] = "elsewhere"

        assert string_effect_preview_url("spiral") == f"{URL_BASE}/h70cx/spiral.png"

    def test_the_lamp_urls_do_not_move(self):
        assert preview_url("ring", "graffiti") == f"{URL_BASE}/21_ring_graffiti.png"
        assert preview_url("ripple", None) == f"{URL_BASE}/16_ripple_none.png"


class TestStringEffectServing:
    """The one registered directory serves the subdirectory's files."""

    @staticmethod
    async def _served_path(resource: CachingStaticResource, url: str) -> Path:
        match, _ = await resource.resolve(make_mocked_request("GET", url))
        assert match is not None, url
        response = await match.handler(make_mocked_request("GET", url, match_info=dict(match)))
        return Path(response._path)

    @pytest.mark.asyncio
    async def test_the_registered_resource_serves_a_subdirectory_file(self):
        hass = MagicMock()
        hass.data = {}
        hass.http.async_register_static_paths = AsyncMock()
        await diy_previews.async_register_previews(hass)
        (configs,) = hass.http.async_register_static_paths.await_args.args
        assert len(configs) == 1

        resource = CachingStaticResource(configs[0].url_path, configs[0].path)

        served = await self._served_path(resource, string_effect_preview_url("spiral"))
        assert served.resolve() == (string_preview_dir() / "spiral.png").resolve()
        lamp = await self._served_path(resource, preview_url("ring", "graffiti"))
        assert lamp.resolve() == (preview_dir() / "21_ring_graffiti.png").resolve()
