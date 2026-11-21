"""CDN-first DIY preview sources (fork feature).

1. Parsing: both reply shapes, numeric and string gifIds, the host allow-list.
2. Resolution: CDN URL or the shipped copy, in display order.
3. Cache: goods type in the request, failure then recovery, refresh, unload.
4. The string-light sensor: wiring, state, attributes, listeners, cleanup.
5. The floor lamp: ``preview_sources`` beside the unchanged ``previews``.

Every CDN URL here is synthetic and sits on an allowed host.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.const import EntityCategory
from homeassistant.helpers import entity_registry as er

from custom_components.govee import _async_cleanup_orphaned_entities, diy_preview_sources as dps
from custom_components.govee.scene_catalogue import CATALOGUE_TTL, RETRY_AFTER_FAILURE
from custom_components.govee.api.protocol import get_profile
from custom_components.govee.const import CONF_ENABLE_LAN_RAW_WRITE, CONF_ENABLE_ZONE_LIGHTS, SUFFIX_DIY_EFFECT_PREVIEWS
from custom_components.govee.diy_previews import (
    PREVIEW_FILES,
    STRING_EFFECT_FILES,
    preview_map,
    string_effect_preview_map,
)
from custom_components.govee.models import GoveeDevice, GoveeDeviceState
from custom_components.govee.models.device import DEVICE_TYPE_LIGHT
from custom_components.govee.platforms.diy_effect import async_diy_preview_entities
from custom_components.govee.platforms.string_previews import (
    GoveeStringEffectPreviewsSensor,
    async_string_preview_sensor_entities,
)
from tests.coordinator_mocks import follow_online_flag
from tests.test_cov_init import _coordinator as _cleanup_coordinator
from tests.test_cov_init import _entry as _cleanup_entry
from tests.test_cov_init import _seed
from tests.test_scene_adjust import _Response, _Schedule, _session

STRING_ID = "AA:BB:CC:DD:EE:FF:60:B0"  # synthetic; reused from the schedule tests
LAMP_ID = "AA:BB:DD:EE:FF:44:55:66"  # synthetic; reused from the preview tests
FETCHED = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


def _cdn(gif_id: int | str) -> str:
    """A synthetic CDN URL on an allowed host."""
    return f"https://synthetic.cloudfront.net/img/preview-{gif_id}.png"


def _reply(gif_ids: Any, *, wrapped: bool = False) -> dict[str, Any]:
    items = [{"gifId": gif_id, "gifUrl": _cdn(gif_id)} for gif_id in gif_ids]
    return {"status": 200, "data": {"gifs": items, "count": len(items)} if wrapped else items}


ALL_STRING_IDS = sorted(dps.STRING_EFFECT_GIF_IDS.values())


# ==============================================================================
# 1. Parsing
# ==============================================================================


class TestParse:
    @pytest.mark.parametrize("wrapped", [False, True])
    def test_both_reply_shapes(self, wrapped: bool) -> None:
        assert dps.parse_previews(_reply([1, 2], wrapped=wrapped)) == {1: _cdn(1), 2: _cdn(2)}

    def test_string_and_numeric_gif_ids(self) -> None:
        payload = {"data": [{"gifId": "7", "gifUrl": _cdn(7)}, {"gifId": 8, "gifUrl": _cdn(8)}]}
        assert dps.parse_previews(payload) == {7: _cdn(7), 8: _cdn(8)}

    def test_the_first_entry_for_a_gif_id_wins(self) -> None:
        payload = {"data": [{"gifId": 1, "gifUrl": _cdn("a")}, {"gifId": "1", "gifUrl": _cdn("b")}]}
        assert dps.parse_previews(payload) == {1: _cdn("a")}

    @pytest.mark.parametrize(
        "payload",
        [
            None,
            {},
            {"data": None},
            {"data": "x"},
            {"data": {"a": [], "b": []}},
            {"data": {"count": 3}},
            {"data": [None, "x", {"gifId": True, "gifUrl": _cdn(1)}, {"gifId": -1, "gifUrl": _cdn(1)}]},
            {"data": [{"gifId": "1a", "gifUrl": _cdn(1)}, {"gifId": 1.5, "gifUrl": _cdn(1)}]},
            {"data": [{"gifId": 1}, {"gifId": 2, "gifUrl": 5}]},
        ],
    )
    def test_junk_yields_nothing(self, payload: Any) -> None:
        assert dps.parse_previews(payload) == {}

    @pytest.mark.parametrize(
        "url",
        [
            "https://cdn.govee.com/img/synthetic.png",
            "https://SYNTHETIC.CloudFront.net/img/synthetic.png",
        ],
    )
    def test_allowed_hosts(self, url: str) -> None:
        assert dps.cdn_url(url) == url

    @pytest.mark.parametrize(
        "url",
        [
            "http://synthetic.cloudfront.net/img/synthetic.png",
            "https://example.com/img/synthetic.png",
            "https://cloudfront.net.example.com/img/synthetic.png",
            "https://evilcloudfront.net/img/synthetic.png",
            "https://[::1/img/synthetic.png",
            "/govee_diy_previews/h70cx/spiral.png",
            None,
        ],
    )
    def test_other_urls_are_discarded(self, url: Any) -> None:
        assert dps.cdn_url(url) is None
        assert dps.parse_previews({"data": [{"gifId": 1, "gifUrl": url}]}) == {}


# ==============================================================================
# 2. Resolution
# ==============================================================================


class TestResolve:
    def test_every_effect_with_artwork_has_a_gif_id(self) -> None:
        assert set(dps.STRING_EFFECT_GIF_IDS) == set(STRING_EFFECT_FILES)
        assert len(set(dps.STRING_EFFECT_GIF_IDS.values())) == len(dps.STRING_EFFECT_GIF_IDS)

    def test_known_gif_ids_resolve_to_the_cdn(self) -> None:
        resolved = dps.string_effect_sources({gif_id: _cdn(gif_id) for gif_id in ALL_STRING_IDS})
        local = string_effect_preview_map()
        assert list(resolved) == list(local)
        assert resolved["spiral"] == {"url": _cdn(11), "fallback": local["spiral"]}
        assert resolved["spin_3"] == {"url": _cdn(35), "fallback": local["spin_3"]}

    def test_a_missing_gif_id_falls_back_to_the_shipped_copy(self) -> None:
        resolved = dps.string_effect_sources({11: _cdn(11)})
        local = string_effect_preview_map()
        assert resolved["breathe"] == {"url": local["breathe"], "fallback": local["breathe"]}
        assert dps.cdn_count(resolved) == 1

    def test_source_kind(self) -> None:
        every = dps.string_effect_sources({gif_id: _cdn(gif_id) for gif_id in ALL_STRING_IDS})
        assert dps.source_kind(every) == dps.SOURCE_CDN
        assert dps.source_kind(dps.string_effect_sources({11: _cdn(11)})) == dps.SOURCE_MIXED
        assert dps.source_kind(dps.string_effect_sources({})) == dps.SOURCE_LOCAL
        assert dps.source_kind({}) == dps.SOURCE_LOCAL

    def test_lamp_modes_resolve_by_their_file_prefix(self) -> None:
        ring = next(zone for zone in get_profile("H60B0").diy.zones if zone.zone_key == "ring")
        resolved = dps.zone_preview_sources(ring, {22: _cdn(22)})
        local = preview_map(ring)
        assert list(resolved) == list(local)
        assert resolved["flow"] == {"url": _cdn(22), "fallback": local["flow"]}
        assert resolved["none"] == {"url": local["none"], "fallback": local["none"]}
        assert PREVIEW_FILES["ring"]["flow"].startswith("22_")

    def test_string_effect_targets(self) -> None:
        assert dps.takes_string_effects("H80C5") is True
        assert dps.takes_string_effects("H60B0") is False
        assert dps.takes_string_effects("H6076") is False
        assert dps.takes_string_effects("H0000") is False
        assert dps.goods_type("H80C5") == 260
        assert dps.goods_type("H0000") is None


# ==============================================================================
# 3. Cache
# ==============================================================================


class _PreviewSchedule(_Schedule):
    """The scene catalogue's hand-driven schedule, around a preview cache."""

    def __init__(self, session: MagicMock) -> None:
        super().__init__(session)
        self.store = dps.PreviewSources(self.coordinator)
        self._patches += [
            patch.object(dps, "async_get_clientsession", return_value=session),
            patch.object(dps, "dt_util", SimpleNamespace(utcnow=lambda: FETCHED)),
        ]


class TestCache:
    async def test_the_request_names_the_goods_type_and_sku(self) -> None:
        session = _session(_Response(200, _reply([1])))
        with _PreviewSchedule(session) as schedule:
            assert await schedule.store.async_load("H80C5") is True
        kwargs = session.get.call_args.kwargs
        assert session.get.call_args.args == (dps.PREVIEWS_URL,)
        assert kwargs["params"] == {"goodsType": "260", "sku": "H80C5"}
        assert kwargs["headers"]["appVersion"]
        assert schedule.store.urls("H80C5") == {1: _cdn(1)}
        assert schedule.store.updated("H80C5") == FETCHED

    async def test_nothing_held_reads_empty(self) -> None:
        store = dps.PreviewSources(MagicMock())
        assert store.urls("H80C5") == {}
        assert store.updated("H80C5") is None

    @pytest.mark.parametrize(
        "response",
        [_Response(503, {}), _Response(200, ValueError("not json")), _Response(200, _reply([])), TimeoutError()],
    )
    async def test_a_failed_fetch_holds_nothing(self, response: Any) -> None:
        with _PreviewSchedule(_session(response)) as schedule:
            assert await schedule.store.async_load("H80C5") is False

    async def test_a_sku_without_a_goods_type_is_never_fetched(self) -> None:
        session = _session()
        with _PreviewSchedule(session) as schedule:
            schedule.store.async_schedule(schedule.hass, "H0000")
            assert schedule.tasks == []
            assert await schedule.store.async_load("H0000") is False
        session.get.assert_not_called()

    async def test_failure_then_recovery_then_daily_refresh(self, caplog: pytest.LogCaptureFixture) -> None:
        session = _session(
            RuntimeError("synthetic"), RuntimeError("again"), _Response(200, _reply([11])), _Response(200, _reply([11, 24]))
        )
        with _PreviewSchedule(session) as schedule, caplog.at_level(logging.DEBUG):
            schedule.store.async_schedule(schedule.hass, "H80C5")
            await schedule.run()
            assert schedule.timers[-1][0] == RETRY_AFTER_FAILURE
            await schedule.fire()
            assert schedule.timers[-1][0] == RETRY_AFTER_FAILURE
            schedule.coordinator.async_update_listeners.assert_not_called()

            await schedule.fire()
            assert schedule.store.urls("H80C5") == {11: _cdn(11)}
            schedule.coordinator.async_update_listeners.assert_called_once()
            assert schedule.timers[-1][0] == CATALOGUE_TTL

            await schedule.fire()
            assert schedule.store.urls("H80C5") == {11: _cdn(11), 24: _cdn(24)}
            assert schedule.coordinator.async_update_listeners.call_count == 2

        warnings = [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING]
        assert warnings == ["DIY preview list load for H80C5 failed with RuntimeError, retrying later"]
        assert "cloudfront" not in caplog.text

    async def test_unloading_cancels_the_schedule(self) -> None:
        with _PreviewSchedule(_session(_Response(503, {}))) as schedule:
            schedule.store.async_schedule(schedule.hass, "H80C5")
            await schedule.run()
            cancel = schedule.timers[-1][2]
            schedule.store.async_stop()
            cancel.assert_called_once()
            schedule.coordinator.config_entry.async_on_unload.assert_called_once_with(schedule.store.async_stop)

    def test_one_store_per_coordinator(self) -> None:
        coordinator = MagicMock()
        first = dps.sources(coordinator)
        assert isinstance(first, dps.PreviewSources)
        assert dps.sources(coordinator) is first


# ==============================================================================
# 4. The string-light sensor
# ==============================================================================


def _device(sku: str, device_id: str, *, is_group: bool = False) -> GoveeDevice:
    return GoveeDevice(
        device_id=device_id, sku=sku, name="Lights", device_type=DEVICE_TYPE_LIGHT, capabilities=(), is_group=is_group
    )


def _coordinator(*devices: GoveeDevice) -> Any:
    coordinator = MagicMock()
    coordinator._govee_diy_state_store = None
    coordinator._govee_zone_state_registry = None
    coordinator.devices = {device.device_id: device for device in devices}
    coordinator.last_update_success = True
    coordinator.config_entry.options = {CONF_ENABLE_LAN_RAW_WRITE: True}
    state = GoveeDeviceState.create_empty(devices[0].device_id)
    state.online = True
    coordinator.get_state = MagicMock(return_value=state)
    return follow_online_flag(coordinator)


def _hold(coordinator: Any, sku: str, gif_ids: list[int]) -> None:
    dps.sources(coordinator)._held[sku] = dps.PreviewList({gif_id: _cdn(gif_id) for gif_id in gif_ids}, FETCHED)


def _string_sensor(coordinator: Any) -> GoveeStringEffectPreviewsSensor:
    (sensor,) = async_string_preview_sensor_entities(coordinator)
    assert isinstance(sensor, GoveeStringEffectPreviewsSensor)
    return sensor


class TestStringSensor:
    def test_only_string_light_devices_get_one(self) -> None:
        assert len(async_string_preview_sensor_entities(_coordinator(_device("H80C5", STRING_ID)))) == 1
        for device in (_device("H60B0", LAMP_ID), _device("H0000", STRING_ID), _device("H80C5", STRING_ID, is_group=True)):
            assert async_string_preview_sensor_entities(_coordinator(device)) == []

    def test_identity(self) -> None:
        sensor = _string_sensor(_coordinator(_device("H80C5", STRING_ID)))
        assert sensor.unique_id == f"{STRING_ID}{SUFFIX_DIY_EFFECT_PREVIEWS}"
        assert sensor.translation_key == "diy_effect_previews"
        assert sensor.entity_category is EntityCategory.DIAGNOSTIC
        assert sensor.available is True

    def test_previews_are_not_recorded(self) -> None:
        assert "previews" in GoveeStringEffectPreviewsSensor._Entity__combined_unrecorded_attributes

    def test_local_before_any_list(self) -> None:
        sensor = _string_sensor(_coordinator(_device("H80C5", STRING_ID)))
        attrs = sensor.extra_state_attributes
        local = string_effect_preview_map()
        assert sensor.native_value == 0
        assert attrs["source"] == "local"
        assert attrs["updated"] is None
        assert attrs["previews"] == {effect: {"url": url, "fallback": url} for effect, url in local.items()}

    def test_cdn_when_every_effect_resolves(self) -> None:
        coordinator = _coordinator(_device("H80C5", STRING_ID))
        _hold(coordinator, "H80C5", ALL_STRING_IDS)
        sensor = _string_sensor(coordinator)
        assert sensor.native_value == len(STRING_EFFECT_FILES)
        assert sensor.extra_state_attributes["source"] == "cdn"
        assert sensor.extra_state_attributes["updated"] == FETCHED.isoformat()

    def test_mixed_when_some_effects_resolve(self) -> None:
        coordinator = _coordinator(_device("H80C5", STRING_ID))
        _hold(coordinator, "H80C5", [11, 24])
        sensor = _string_sensor(coordinator)
        previews = sensor.extra_state_attributes["previews"]
        assert sensor.native_value == 2
        assert sensor.extra_state_attributes["source"] == "mixed"
        assert previews["spiral"]["url"] == _cdn(11)
        assert previews["flow_1"]["url"] == previews["flow_1"]["fallback"]

    async def test_adding_schedules_the_list_and_a_fetch_notifies(self) -> None:
        coordinator = _coordinator(_device("H80C5", STRING_ID))
        sensor = _string_sensor(coordinator)
        with _PreviewSchedule(_session(_Response(200, _reply([11])))) as schedule:
            schedule.coordinator = coordinator
            schedule.store = dps.sources(coordinator)
            sensor.hass = schedule.hass
            await sensor.async_added_to_hass()
            await schedule.run()
        coordinator.async_update_listeners.assert_called_once()
        assert sensor.native_value == 1


async def test_cleanup_drops_the_sensor_of_a_device_without_string_effects(hass, mock_light_device) -> None:
    device_id = mock_light_device.device_id
    entry = _cleanup_entry(hass)
    registry = er.async_get(hass)
    ids = _seed(registry, entry, ("light", device_id), ("sensor", f"{device_id}{SUFFIX_DIY_EFFECT_PREVIEWS}"))
    coordinator = _cleanup_coordinator([device_id])

    coordinator.devices[device_id].sku = "H80C5"
    await _async_cleanup_orphaned_entities(hass, entry, coordinator)
    assert all(registry.async_get(eid) is not None for eid in ids.values())

    coordinator.devices[device_id].sku = "H60B0"
    await _async_cleanup_orphaned_entities(hass, entry, coordinator)
    assert {uid for uid, eid in ids.items() if registry.async_get(eid) is not None} == {device_id}


# ==============================================================================
# 5. The floor lamp
# ==============================================================================


def _lamp_sensors(coordinator: Any) -> dict[str, Any]:
    entry = MagicMock()
    entry.options = {CONF_ENABLE_ZONE_LIGHTS: True, CONF_ENABLE_LAN_RAW_WRITE: True}
    return {sensor._zone_key: sensor for sensor in async_diy_preview_entities(coordinator, entry)}


class TestLampSources:
    def test_preview_sources_sit_beside_the_unchanged_previews(self) -> None:
        coordinator = _coordinator(_device("H60B0", LAMP_ID))
        before = {key: dict(sensor.extra_state_attributes["previews"]) for key, sensor in _lamp_sensors(coordinator).items()}
        _hold(coordinator, "H60B0", [22, 16])

        sensors = _lamp_sensors(coordinator)
        ring = sensors["ring"].extra_state_attributes
        ripple = sensors["ripple"].extra_state_attributes
        assert {key: sensor.extra_state_attributes["previews"] for key, sensor in sensors.items()} == before
        assert list(ring["preview_sources"]) == list(ring["previews"])
        assert ring["preview_sources"]["flow"] == {"url": _cdn(22), "fallback": ring["previews"]["flow"]}
        assert ring["preview_sources"]["none"]["url"] == ring["previews"]["none"]
        assert ripple["preview_sources"]["none"]["url"] == _cdn(16)
        assert sensors["ring"].native_value == ring["previews"]["none"]

    def test_preview_sources_are_not_recorded(self) -> None:
        sensor = _lamp_sensors(_coordinator(_device("H60B0", LAMP_ID)))["ring"]
        assert "preview_sources" in type(sensor)._Entity__combined_unrecorded_attributes
        assert "previews" not in type(sensor)._Entity__combined_unrecorded_attributes

    async def test_adding_schedules_the_lamps_list(self) -> None:
        coordinator = _coordinator(_device("H60B0", LAMP_ID))
        sensor = _lamp_sensors(coordinator)["ring"]
        sensor.hass = MagicMock()
        sensor.async_on_remove = MagicMock()
        with patch.object(dps.PreviewSources, "async_schedule") as schedule:
            await sensor.async_added_to_hass()
        schedule.assert_called_once_with(sensor.hass, "H60B0")
