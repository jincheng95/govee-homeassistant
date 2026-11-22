"""Local settings of the H60B0, H6076 and H6046 over raw frames (fork feature).

1. Capability table: which settings each of the four profiles encodes.
2. Frames: gradient, power-off memory, timer, sleep and wake-up per light.
3. Replies: the sleep, wake-up and timer replies per light, and the raw path
   that stores them for a profile without ``schedule_report``.
4. Queries: what each light is asked after a status request.
5. Actions: the per-light sleep tail and the H6046 wake-up colour refusal.
6. Entities: schedule sensors, the power-off memory select, the blending
   switch, and their cleanup.

Device ids are the suite's synthetic ones.
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er

from custom_components.govee import _async_cleanup_orphaned_entities, light_schedule, raw_actions, raw_status, services
from custom_components.govee.api import raw_router
from custom_components.govee.api.protocol import (
    Capability,
    CapabilitySpec,
    GoveeCodec,
    LightTimer,
    SleepTimer,
    WakeUp,
    decode_status,
    get_profile,
)
from custom_components.govee.api.protocol.profiles import PROFILES
from custom_components.govee.const import (
    CONF_ENABLE_LAN_RAW_WRITE,
    DOMAIN,
    SCHEDULE_SENSOR_SUFFIXES,
    SUFFIX_SEGMENT_BLENDING,
)
from custom_components.govee.models import GoveeCapability, GoveeDevice
from custom_components.govee.models.device import CAPABILITY_TOGGLE, DEVICE_TYPE_LIGHT, INSTANCE_GRADUAL_ON
from custom_components.govee.platforms.device_settings import (
    async_device_setting_select_entities,
    power_off_memory_options,
)
from custom_components.govee.platforms.light_schedule import async_light_schedule_sensor_entities
from custom_components.govee.switch import GoveeSegmentBlendingSwitchEntity
from custom_components.govee.zone_state import segment_blending_switch
from tests.test_cov_init import _entry as _cleanup_entry
from tests.test_cov_init import _seed

DEVICE_IDS = {
    "H60B0": "AA:BB:CC:DD:EE:FF:60:B0",
    "H6076": "AA:BB:CC:DD:EE:FF:60:76",
    "H6046": "AA:BB:CC:DD:EE:FF:60:46",
    "H80C5": "AA:BB:CC:DD:EE:FF:00:11",
}
LAMPS = ("H60B0", "H6076", "H6046")
"""The three lights this file covers. The H80C5 has its own files."""


def _light(sku: str, *, blending: bool = False) -> GoveeDevice:
    capabilities = (
        (GoveeCapability(type=CAPABILITY_TOGGLE, instance=INSTANCE_GRADUAL_ON, parameters={}),) if blending else ()
    )
    return GoveeDevice(
        device_id=DEVICE_IDS[sku], sku=sku, name="Lamp", device_type=DEVICE_TYPE_LIGHT, capabilities=capabilities
    )


def _coordinator(*devices: GoveeDevice, raw: bool = True) -> Any:
    coordinator = MagicMock()
    coordinator.config_entry.options = {CONF_ENABLE_LAN_RAW_WRITE: raw}
    coordinator.devices = {device.device_id: device for device in devices}
    coordinator.last_update_success = True
    coordinator.get_state = MagicMock(return_value=None)
    return coordinator


def _entry(*, raw: bool) -> Any:
    entry = MagicMock()
    entry.options = {CONF_ENABLE_LAN_RAW_WRITE: raw}
    return entry


def _codec(sku: str) -> GoveeCodec:
    return GoveeCodec(get_profile(sku))


# ==============================================================================
# 1. Capability table
# ==============================================================================

SETTINGS = (
    Capability.GRADIENT,
    Capability.POWER_OFF_MEMORY,
    Capability.LIGHT_TIMER,
    Capability.SLEEP_TIMER,
    Capability.WAKE_UP,
)


def _features(sku: str) -> set[str]:
    profile = get_profile(sku)
    features = {capability.value for capability in SETTINGS if profile.supports(capability)}
    if profile.supports(Capability.WAKE_UP) and GoveeCodec(profile).wake_up_color():
        features.add("wake_up_color")
    if profile.schedule_report:
        features.add("schedule_report")
    return features


class TestCapabilityTable:
    def test_each_light_encodes_exactly_its_settings(self) -> None:
        schedules = {"light_timer", "sleep_timer", "wake_up"}
        assert {sku: _features(sku) for sku in PROFILES} == {
            "H60B0": {"gradient", "power_off_memory", "wake_up_color", *schedules},
            "H6076": {"gradient", "wake_up_color", *schedules},
            "H6046": {"gradient", *schedules},
            "H80C5": {"gradient", "power_off_memory", "wake_up_color", "schedule_report", *schedules},
        }

    def test_power_off_memory_options(self) -> None:
        assert {sku: power_off_memory_options(sku) for sku in PROFILES} == {
            "H60B0": ["last_state", "on", "off"],
            "H6076": [],
            "H6046": [],
            "H80C5": ["last_state", "on", "off"],
        }

    def test_a_behaviours_mask_narrows_the_options(self) -> None:
        profile = get_profile("H60B0")
        spec = CapabilitySpec("power_off_memory", {"sub_type": 0x02, "behaviours": 0b101})
        masked = replace(profile, capabilities={**profile.capabilities, Capability.POWER_OFF_MEMORY: spec})
        assert GoveeCodec(masked).power_off_memory_behaviours() == (0, 2)

    @pytest.mark.parametrize("sku", list(PROFILES))
    def test_every_light_keeps_four_timers(self, sku: str) -> None:
        assert _codec(sku).timer_slots() == 4

    @pytest.mark.parametrize("sku", list(PROFILES))
    def test_every_light_reports_a_schedule(self, sku: str) -> None:
        assert light_schedule.reports_schedule(sku) is True


# ==============================================================================
# 2. Frames
# ==============================================================================

GRADIENT_ON = bytes([0x33, 0xA3, 0x01, *bytes(16), 0x91])
GRADIENT_OFF = bytes([0x33, 0xA3, 0x00, *bytes(16), 0x90])
MEMORY_ON = bytes([0x33, 0x41, 0x02, 0x01, *bytes(15), 0x71])
TIMER_ON_MON_SUN_DISABLED = bytes([0x33, 0x23, 0x01, 0x01, 0x07, 0x23, 0xC1, *bytes(12), 0xF5])
TIMER_CLEARED = bytes([0x33, 0x23, 0x01, 0x00, 0x00, 0x00, 0x80, *bytes(12), 0x91])
SLEEP_LAMP = bytes([0x33, 0x11, 0x00, 0x32, 0x14, 0x14, 0x00, 0xFF, 0x32, 0x00, *bytes(9), 0xDD])
SLEEP_LAMP_30_FOR_15 = bytes([0x33, 0x11, 0x00, 0x1E, 0x0F, 0x0F, 0x00, 0xFF, 0x32, 0x00, *bytes(9), 0xF1])
SLEEP_BAR = bytes([0x33, 0x11, 0x00, 0x32, 0x14, 0x14, 0x00, 0x00, 0x00, 0x00, *bytes(9), 0x10])
WAKE_LAMP = bytes(
    [0x33, 0x12, 0x00, 0x50, 0x07, 0x23, 0x82, 0x0F, 0x00, 0xFF, 0xAE, 0x54, 0x0A, 0x8C, *bytes(5), 0x5B]
)
WAKE_BAR = bytes([0x33, 0x12, 0x00, 0x50, 0x07, 0x23, 0x82, 0x0F, 0x00, *bytes(10), 0xD8])
WAKE_BAR_ONCE = bytes([0x33, 0x12, 0x00, 0x64, 0x00, 0x00, 0x80, 0x0A, 0x00, *bytes(10), 0xCF])

SLEEP_FRAMES = {"H60B0": SLEEP_LAMP, "H6076": SLEEP_LAMP, "H6046": SLEEP_BAR}
WAKE_FRAMES = {"H60B0": WAKE_LAMP, "H6076": WAKE_LAMP, "H6046": WAKE_BAR}


def _wake(sku: str, **overrides: Any) -> bytes:
    values: dict[str, Any] = {
        "enabled": False,
        "end_brightness": 80,
        "hour": 7,
        "minute": 35,
        "weekdays": (1,),
        "duration": 15,
        "kelvin": 2700,
        "white_rgb": (0xFF, 0xAE, 0x54),
        **overrides,
    }
    return _codec(sku).wake_up(**values)


class TestFrames:
    @pytest.mark.parametrize("sku", LAMPS)
    def test_gradient(self, sku: str) -> None:
        assert (_codec(sku).gradient(True), _codec(sku).gradient(False)) == (GRADIENT_ON, GRADIENT_OFF)

    def test_power_off_memory_on(self) -> None:
        assert _codec("H60B0").power_off_memory(1) == MEMORY_ON

    @pytest.mark.parametrize("sku", LAMPS)
    def test_timer(self, sku: str) -> None:
        codec = _codec(sku)
        set_frame = codec.light_timer(1, enabled=False, turn_on=True, hour=7, minute=35, weekdays=(0, 6))
        cleared = codec.light_timer(1, enabled=False, turn_on=False, hour=0, minute=0)
        assert (set_frame, cleared) == (TIMER_ON_MON_SUN_DISABLED, TIMER_CLEARED)

    @pytest.mark.parametrize("sku", LAMPS)
    def test_sleep_carries_the_lights_tail(self, sku: str) -> None:
        assert _codec(sku).sleep_timer(enabled=False, start_brightness=50, minutes=20) == SLEEP_FRAMES[sku]

    def test_the_string_light_tail_is_unchanged(self) -> None:
        frame = _codec("H80C5").sleep_timer(enabled=False, start_brightness=50, minutes=20)
        assert frame[6:10] == bytes([0x00, 0x00, 0x03, 0xE8])

    @pytest.mark.parametrize("sku", LAMPS)
    def test_wake_up(self, sku: str) -> None:
        assert _wake(sku) == WAKE_FRAMES[sku]

    def test_the_light_bar_wake_up_ignores_the_colour(self) -> None:
        assert _wake("H6046", kelvin=6500, white_rgb=(1, 2, 3)) == WAKE_BAR


# ==============================================================================
# 3. Replies
# ==============================================================================

SLEEP_REPLY_LAMP = bytes([0xAA, 0x11, 0x00, 0x1E, 0x0F, 0x0F, 0x00, 0xFF, 0x32, *bytes(10), 0x68])
SLEEP_REPLY_BAR = bytes([0xAA, 0x11, 0x00, 0x1E, 0x0F, 0x0F, *bytes(13), 0xA5])
WAKE_REPLY_LAMP = bytes(
    [0xAA, 0x12, 0x00, 0x50, 0x07, 0x23, 0x82, 0x0F, 0x00, 0xFF, 0xAE, 0x54, 0x0A, 0x8C, *bytes(5), 0xC2]
)
WAKE_REPLY_BAR = bytes([0xAA, 0x12, 0x00, 0x50, 0x07, 0x23, 0x82, 0x0F, *bytes(11), 0x41])
WAKE_REPLY_BAR_NEVER_SET = bytes([0xAA, 0x12, 0xFF, 0x64, 0x00, 0x00, 0x80, 0x0A, *bytes(11), 0xA9])
TIMER_REPLY_SLOT_2 = bytes(
    [0xAA, 0x23, 0xFF, 0x00, 0x00, 0x00, 0x80, 0x01, 0x07, 0x23, 0xC1, *([0x00, 0x00, 0x00, 0x80] * 2), 0x12]
)
MEMORY_REPLY_ON = bytes([0xAA, 0x41, 0x02, 0x01, *bytes(15), 0xE8])

SLEEP_30_FOR_15 = SleepTimer(enabled=False, start_brightness=30, minutes=15, remaining_minutes=15)
WAKE_TUESDAY = WakeUp(
    enabled=False,
    end_brightness=80,
    hour=7,
    minute=35,
    weekdays=(1,),
    duration=15,
    rgb=(0xFF, 0xAE, 0x54),
    kelvin=2700,
)
OFF_TIMER = LightTimer(enabled=False, turn_on=False, hour=0, minute=0, weekdays=())


class TestReplies:
    @pytest.mark.parametrize("reply", [SLEEP_REPLY_LAMP, SLEEP_REPLY_BAR], ids=["lamp-tail", "bar-tail"])
    def test_sleep_decodes_whatever_the_tail(self, reply: bytes) -> None:
        assert decode_status([reply]).sleep == SLEEP_30_FOR_15

    def test_wake_up_with_its_colour(self) -> None:
        assert decode_status([WAKE_REPLY_LAMP]).wake_up == WAKE_TUESDAY

    def test_the_light_bar_wake_up_reads_zero_colour(self) -> None:
        wake = decode_status([WAKE_REPLY_BAR]).wake_up
        assert wake is not None and (wake.rgb, wake.kelvin) == ((0, 0, 0), 0)
        assert (wake.hour, wake.minute, wake.weekdays, wake.duration, wake.end_brightness) == (7, 35, (1,), 15, 80)

    def test_the_second_timer_slot(self) -> None:
        timers = decode_status([TIMER_REPLY_SLOT_2]).timers
        assert timers == (
            OFF_TIMER,
            LightTimer(enabled=False, turn_on=True, hour=7, minute=35, weekdays=(0, 6)),
            OFF_TIMER,
            OFF_TIMER,
        )


class TestRawPathStoresQueriedSchedules:
    @pytest.mark.parametrize("sku", LAMPS)
    def test_a_reply_is_stored_once(self, sku: str) -> None:
        device = _light(sku)
        coordinator = _coordinator(device)
        frames = [SLEEP_REPLY_LAMP, WAKE_REPLY_LAMP, TIMER_REPLY_SLOT_2]
        assert raw_status.apply_raw_status(coordinator, device, frames) is True
        stored = light_schedule.schedule(coordinator, device.device_id)
        assert (stored.sleep, stored.wake) == (SLEEP_30_FOR_15, WAKE_TUESDAY)
        assert stored.timers is not None and stored.timers[1].turn_on is True
        assert raw_status.apply_raw_status(coordinator, device, frames) is False

    def test_a_pushing_profile_leaves_it_to_the_status_path(self) -> None:
        device = _light("H80C5")
        coordinator = _coordinator(device)
        assert raw_status.apply_raw_status(coordinator, device, [SLEEP_REPLY_LAMP]) is False
        assert light_schedule.schedule(coordinator, device.device_id) == light_schedule.LightSchedule()

    def test_the_uplighter_stores_power_off_memory_on(self) -> None:
        device = _light("H60B0")
        coordinator = _coordinator(device)
        raw_status.apply_raw_status(coordinator, device, [MEMORY_REPLY_ON])
        assert raw_status.device_settings(coordinator, device.device_id).power_off_memory == 1

    @pytest.mark.parametrize("sku", ["H6076", "H6046"])
    def test_a_light_without_power_off_memory_ignores_the_reply(self, sku: str) -> None:
        device = _light(sku)
        coordinator = _coordinator(device)
        assert raw_status.apply_raw_status(coordinator, device, [MEMORY_REPLY_ON]) is False
        assert raw_status.device_settings(coordinator, device.device_id) == raw_status.DeviceSettings()


# ==============================================================================
# 4. Queries
# ==============================================================================

QUERY_GRADIENT = bytes([0xAA, 0xA3, *bytes(17), 0x09])
QUERY_MEMORY = bytes([0xAA, 0x41, *bytes(17), 0xEB])
QUERY_SLEEP = bytes([0xAA, 0x11, *bytes(17), 0xBB])
QUERY_WAKE = bytes([0xAA, 0x12, *bytes(17), 0xB8])
QUERY_TIMERS = bytes([0xAA, 0x23, 0xFF, *bytes(16), 0x76])


class TestQueries:
    @pytest.mark.parametrize(
        ("sku", "expected"),
        [
            ("H60B0", [QUERY_GRADIENT, QUERY_MEMORY, QUERY_SLEEP, QUERY_WAKE, QUERY_TIMERS]),
            ("H6076", [QUERY_GRADIENT, QUERY_SLEEP, QUERY_WAKE, QUERY_TIMERS]),
            ("H6046", [QUERY_GRADIENT, QUERY_SLEEP, QUERY_WAKE, QUERY_TIMERS]),
            ("H80C5", [QUERY_GRADIENT, QUERY_MEMORY]),
        ],
    )
    async def test_each_light_is_asked_for_what_it_encodes(self, sku: str, expected: list[bytes]) -> None:
        device = _light(sku)
        coordinator = _coordinator(device)
        with patch.object(raw_status.mqtt_raw_write, "async_send_frames", AsyncMock(return_value=True)) as send:
            assert await raw_status.async_query_settings(coordinator, device.device_id) == len(expected)
        assert [list(call.args[4]) for call in send.await_args_list] == [[frame] for frame in expected]

    async def test_a_profile_with_nothing_to_ask_sends_nothing(self) -> None:
        device = _light("H6076")
        coordinator = _coordinator(device)
        bare = SimpleNamespace(supports=lambda capability: capability is Capability.QUERY, schedule_report=False)
        with (
            patch.object(raw_status, "profile_for", return_value=bare),
            patch.object(raw_status, "GoveeCodec"),
            patch.object(raw_status.mqtt_raw_write, "async_send_frames", AsyncMock(return_value=True)) as send,
        ):
            assert await raw_status.async_query_settings(coordinator, device.device_id) == 0
        send.assert_not_awaited()


# ==============================================================================
# 5. Actions
# ==============================================================================


@pytest.fixture
async def actions(hass, monkeypatch):
    """Register the actions and resolve device ids against a mock coordinator."""
    holder: dict[str, Any] = {"coordinator": _coordinator(*(_light(sku) for sku in LAMPS))}
    services.async_setup_services(hass)
    monkeypatch.setattr(
        services,
        "_get_coordinator_for_device",
        lambda _hass, device_id: (
            (holder["coordinator"], device_id) if device_id in holder["coordinator"].devices else None
        ),
    )
    return holder


@pytest.fixture
def route():
    with patch.object(raw_router, "async_route_frames", AsyncMock(return_value=True)) as mock:
        yield mock


async def _call(hass, service: str, sku: str, **data: Any) -> None:
    await hass.services.async_call(DOMAIN, service, {"device_id": DEVICE_IDS[sku], **data}, blocking=True)


def _sent(route: AsyncMock) -> list[bytes]:
    return list(route.await_args.args[4])


class TestActions:
    @pytest.mark.parametrize("sku", LAMPS)
    async def test_sleep_carries_the_lights_tail(self, hass, actions, route, sku: str) -> None:
        await _call(hass, raw_actions.SERVICE_SET_SLEEP_TIMER, sku, enabled=False, minutes=20, start_brightness=50)
        assert _sent(route) == [SLEEP_FRAMES[sku]]

    async def test_a_timer_on_the_floor_lamp(self, hass, actions, route) -> None:
        await _call(hass, raw_actions.SERVICE_SET_LIGHT_TIMER, "H6076", slot=2, enabled=False, time="00:00")
        assert _sent(route) == [TIMER_CLEARED]

    @pytest.mark.parametrize("sku", ["H60B0", "H6076"])
    async def test_a_wake_up_colour_is_sent_where_it_is_stored(self, hass, actions, route, sku: str) -> None:
        await _call(hass, raw_actions.SERVICE_SET_WAKE_UP, sku, time="07:00", color_temp_kelvin=2700)
        assert _sent(route)[0][12:14] == bytes([0x0A, 0x8C])

    async def test_the_light_bar_refuses_a_colour_temperature(self, hass, actions, route) -> None:
        with pytest.raises(ServiceValidationError) as err:
            await _call(hass, raw_actions.SERVICE_SET_WAKE_UP, "H6046", time="07:00", color_temp_kelvin=2700)
        assert err.value.translation_key == "wake_up_color_unsupported"
        assert err.value.translation_placeholders == {"device": "Lamp"}
        route.assert_not_awaited()

    async def test_the_light_bar_is_sent_zeros_and_stores_them(self, hass, actions, route) -> None:
        await _call(hass, raw_actions.SERVICE_SET_WAKE_UP, "H6046", enabled=False, time="00:00")
        assert _sent(route) == [WAKE_BAR_ONCE]
        stored = light_schedule.schedule(actions["coordinator"], DEVICE_IDS["H6046"]).wake
        assert stored is not None and (stored.rgb, stored.kelvin) == ((0, 0, 0), 0)

    async def test_disabling_a_known_light_bar_wake_up_keeps_zeros(self, hass, actions, route) -> None:
        coordinator = actions["coordinator"]
        light_schedule.apply_schedule_report(coordinator, coordinator.devices[DEVICE_IDS["H6046"]], [WAKE_REPLY_BAR])
        await _call(hass, raw_actions.SERVICE_SET_WAKE_UP, "H6046", enabled=False)
        assert _sent(route) == [WAKE_BAR]

    async def test_a_never_set_light_bar_wake_up_is_known(self, hass, actions, route) -> None:
        coordinator = actions["coordinator"]
        light_schedule.apply_schedule_report(
            coordinator, coordinator.devices[DEVICE_IDS["H6046"]], [WAKE_REPLY_BAR_NEVER_SET]
        )
        await _call(hass, raw_actions.SERVICE_SET_WAKE_UP, "H6046", enabled=False)
        assert _sent(route) == [WAKE_BAR_ONCE]


# ==============================================================================
# 6. Entities
# ==============================================================================


class TestScheduleSensors:
    @pytest.mark.parametrize("sku", LAMPS)
    @pytest.mark.parametrize(("raw", "count"), [(True, 3), (False, 0)])
    def test_a_queried_light_needs_the_raw_option(self, sku: str, raw: bool, count: int) -> None:
        entities = async_light_schedule_sensor_entities(_coordinator(_light(sku)), _entry(raw=raw))
        assert len(entities) == count
        assert {entity.unique_id for entity in entities} == {
            f"{DEVICE_IDS[sku]}{suffix}" for suffix in SCHEDULE_SENSOR_SUFFIXES[:count]
        }

    def test_the_string_lights_keep_theirs_without_it(self) -> None:
        assert len(async_light_schedule_sensor_entities(_coordinator(_light("H80C5")), _entry(raw=False))) == 3

    def test_a_queried_reply_reaches_the_sensor(self) -> None:
        device = _light("H6046")
        coordinator = _coordinator(device)
        sleep, wake, timers = async_light_schedule_sensor_entities(coordinator, _entry(raw=True))
        raw_status.apply_raw_status(coordinator, device, [SLEEP_REPLY_BAR, WAKE_REPLY_BAR, TIMER_REPLY_SLOT_2])
        assert (sleep.native_value, wake.native_value, timers.native_value) == ("off", "off", 0)
        assert wake.extra_state_attributes["time"] == "07:35"


class TestPowerOffMemorySelect:
    def test_only_the_uplighter_gets_one(self) -> None:
        coordinator = _coordinator(*(_light(sku) for sku in LAMPS))
        (entity,) = async_device_setting_select_entities(coordinator, _entry(raw=True))
        assert entity.unique_id == f"{DEVICE_IDS['H60B0']}_power_off_memory"
        assert entity.options == ["last_state", "on", "off"]

    def test_the_floor_lamp_has_no_setting_to_write(self) -> None:
        assert raw_router.supports_setting("H6076", Capability.POWER_OFF_MEMORY) is False


def _blending(device: GoveeDevice, *, raw: bool = True) -> tuple[GoveeSegmentBlendingSwitchEntity, Any]:
    coordinator = _coordinator(device, raw=raw)
    coordinator.async_control_device = AsyncMock(return_value=True)
    entity = GoveeSegmentBlendingSwitchEntity(coordinator, device)
    entity.async_write_ha_state = MagicMock()
    return entity, coordinator


class TestBlendingSwitch:
    @pytest.mark.parametrize(
        ("sku", "blending", "raw", "built"),
        [
            ("H60B0", False, True, True),
            ("H60B0", False, False, False),
            ("H6076", True, False, True),
            ("H6046", True, False, True),
        ],
    )
    def test_who_gets_one(self, sku: str, blending: bool, raw: bool, built: bool) -> None:
        assert segment_blending_switch(_entry(raw=raw), _light(sku, blending=blending)) is built

    @pytest.mark.parametrize(("blending", "built"), [(False, False), (True, True)])
    def test_a_group_gets_one_only_from_the_cloud(self, blending: bool, built: bool) -> None:
        group = replace(_light("H60B0", blending=blending), is_group=True)
        assert segment_blending_switch(_entry(raw=True), group) is built

    async def test_the_platform_builds_the_uplighters_with_the_raw_option(self) -> None:
        from custom_components.govee import switch as switch_mod

        device = _light("H60B0")
        coordinator = _coordinator(device)
        entry = _entry(raw=True)
        entry.runtime_data = coordinator
        added: list[Any] = []
        await switch_mod.async_setup_entry(MagicMock(), entry, lambda entities: added.extend(entities))
        (entity,) = [e for e in added if isinstance(e, GoveeSegmentBlendingSwitchEntity)]
        assert entity.unique_id == f"{DEVICE_IDS['H60B0']}{SUFFIX_SEGMENT_BLENDING}"

    @pytest.mark.parametrize(("sku", "blending"), [("H60B0", False), ("H6076", True), ("H6046", True)])
    async def test_the_raw_frame_goes_first(self, sku: str, blending: bool) -> None:
        entity, coordinator = _blending(_light(sku, blending=blending))
        with patch.object(raw_router, "async_route_frames", AsyncMock(return_value=True)) as route:
            await entity.async_turn_on()
        assert _sent(route) == [GRADIENT_ON]
        coordinator.async_control_device.assert_not_awaited()
        assert entity.is_on is True

    async def test_the_uplighter_has_no_cloud_fallback(self) -> None:
        entity, coordinator = _blending(_light("H60B0"))
        with patch.object(raw_router, "async_route_frames", AsyncMock(return_value=False)):
            await entity.async_turn_on()
        coordinator.async_control_device.assert_not_awaited()
        assert entity.is_on is False
        entity.async_write_ha_state.assert_not_called()

    async def test_the_floor_lamp_falls_back_to_the_cloud(self) -> None:
        entity, coordinator = _blending(_light("H6076", blending=True))
        with patch.object(raw_router, "async_route_frames", AsyncMock(return_value=False)):
            await entity.async_turn_on()
        coordinator.async_control_device.assert_awaited_once()
        assert entity.is_on is True


def _cleanup_coordinator(*devices: GoveeDevice) -> SimpleNamespace:
    return SimpleNamespace(
        devices={device.device_id: device for device in devices},
        leak_sensors={},
        hub_device_ids=set(),
        discovery_incomplete=False,
    )


@pytest.mark.parametrize(("raw", "kept"), [(False, False), (True, True)])
async def test_cleanup_follows_the_raw_option(hass, raw: bool, kept: bool) -> None:
    uplighter, bar, strings = _light("H60B0"), _light("H6046", blending=True), _light("H80C5")
    entry = _cleanup_entry(hass, **{CONF_ENABLE_LAN_RAW_WRITE: raw})
    registry = er.async_get(hass)
    raw_only = [f"{uplighter.device_id}{SUFFIX_SEGMENT_BLENDING}"] + [
        f"{device.device_id}{suffix}" for device in (uplighter, bar) for suffix in SCHEDULE_SENSOR_SUFFIXES
    ]
    always = [f"{bar.device_id}{SUFFIX_SEGMENT_BLENDING}"] + [
        f"{strings.device_id}{suffix}" for suffix in SCHEDULE_SENSOR_SUFFIXES
    ]
    ids = _seed(
        registry,
        entry,
        *(
            ("switch" if unique_id.endswith(SUFFIX_SEGMENT_BLENDING) else "sensor", unique_id)
            for unique_id in raw_only + always
        ),
    )

    await _async_cleanup_orphaned_entities(hass, entry, _cleanup_coordinator(uplighter, bar, strings))

    assert all((registry.async_get(ids[unique_id]) is not None) is kept for unique_id in raw_only)
    assert all(registry.async_get(ids[unique_id]) is not None for unique_id in always)
