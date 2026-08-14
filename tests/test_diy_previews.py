"""DIY mode preview artwork and the sensors that surface it (fork feature).

The mode selects offer bare names; the shipped previews show what each one
renders. The tests are organised around what can go wrong with artwork that
lives in one place and is named in another:

1. Drift — every mode in the profile table has a file, every file has a mode.
   This is the one that catches a profile edit made without artwork.
2. Serving — the static path is registered once, at a stable prefix.
3. The sensors — state follows the mode select, attributes carry the whole map.
4. Gating — a device with no DIY layout gets none of this.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.govee import diy_previews
from custom_components.govee.api.protocol import get_profile
from custom_components.govee.const import (
    CONF_ENABLE_LAN_RAW_WRITE,
    CONF_ENABLE_ZONE_LIGHTS,
    SUFFIX_DIY_PREVIEW,
)
from custom_components.govee.diy_previews import (
    PREVIEW_FILES,
    URL_BASE,
    preview_dir,
    preview_map,
    preview_url,
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

DEVICE_ID = "12:37:DD:EE:FF:44:55:66"  # synthetic; H60B0 prefix


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
