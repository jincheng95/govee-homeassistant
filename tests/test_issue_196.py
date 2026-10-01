"""Tests for issue #196 — H60B0 uplighter ripple/side/bottom light toggles.

The H60B0 is the naming sibling of the H60B3 (issue #126): the same three
per-part light toggles, but its upper effect is ``rippleLightToggle`` where
the H60B3 says ``nebulaLightToggle``. ``named_light_toggle_instances``
already detected all three; the switch platform had no mapping for the ripple
instance, took the unknown-toggle branch, and skipped the entity.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from custom_components.govee.const import SUFFIX_RIPPLE_LIGHT
from custom_components.govee.models import GoveeCapability, GoveeDevice
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
from custom_components.govee.switch import NAMED_LIGHT_TOGGLE_SPECS

_GOVEE_ROOT = Path(__file__).resolve().parent.parent / "custom_components" / "govee"


def _cap(cap_type: str, instance: str, params: dict | None = None) -> GoveeCapability:
    return GoveeCapability(type=cap_type, instance=instance, parameters=params or {})


def _h60b0() -> GoveeDevice:
    # Capability shape from docs/device-catalog.md (H60B0, issue #83) — the
    # same list the #196 reporter saw, minus the scene/music/segment entries
    # that have their own entities.
    return GoveeDevice(
        device_id="AA:BB:CC:DD:EE:FF:60:B0",
        sku="H60B0",
        name="Uplighter",
        device_type=DEVICE_TYPE_LIGHT,
        capabilities=(
            _cap(CAPABILITY_ON_OFF, INSTANCE_POWER),
            _cap(CAPABILITY_RANGE, INSTANCE_BRIGHTNESS),
            _cap(CAPABILITY_COLOR_SETTING, INSTANCE_COLOR_RGB),
            _cap(CAPABILITY_TOGGLE, "bottomLightToggle"),
            _cap(CAPABILITY_TOGGLE, "dreamViewToggle"),
            _cap(CAPABILITY_TOGGLE, "rippleLightToggle"),
            _cap(CAPABILITY_TOGGLE, "sideLightToggle"),
        ),
    )


class TestH60B0NamedLightToggles:
    def test_detects_ripple_side_and_bottom(self):
        assert _h60b0().named_light_toggle_instances == [
            "bottomLightToggle",
            "rippleLightToggle",
            "sideLightToggle",
        ]

    def test_ripple_toggle_is_mapped(self):
        assert NAMED_LIGHT_TOGGLE_SPECS["rippleLightToggle"] == ("govee_ripple_light", SUFFIX_RIPPLE_LIGHT)

    @pytest.mark.asyncio
    async def test_switch_platform_creates_all_three(self):
        from custom_components.govee import switch as switch_mod

        device = _h60b0()
        coordinator = MagicMock()
        coordinator.devices = {device.device_id: device}
        entry = MagicMock()
        entry.runtime_data = coordinator
        added: list = []
        await switch_mod.async_setup_entry(MagicMock(), entry, lambda ents: added.extend(ents))

        named = {e._toggle_instance: e for e in added if type(e).__name__ == "GoveeNamedLightSwitchEntity"}
        assert sorted(named) == ["bottomLightToggle", "rippleLightToggle", "sideLightToggle"]
        # The reporter's registry already held a ``_ripple_light`` identity;
        # the mapping has to land on it rather than mint a second entity.
        assert named["rippleLightToggle"]._attr_unique_id == "AA:BB:CC:DD:EE:FF:60:B0_ripple_light"
        assert named["rippleLightToggle"]._attr_translation_key == "govee_ripple_light"
        assert named["sideLightToggle"]._attr_unique_id == "AA:BB:CC:DD:EE:FF:60:B0_side_light"
        assert named["bottomLightToggle"]._attr_unique_id == "AA:BB:CC:DD:EE:FF:60:B0_bottom_light"

    def test_strings_and_icons_carry_the_ripple_key(self):
        strings = json.loads((_GOVEE_ROOT / "strings.json").read_text())
        assert strings["entity"]["switch"]["govee_ripple_light"]["name"] == "Ripple light"
        icons = json.loads((_GOVEE_ROOT / "icons.json").read_text())
        assert icons["entity"]["switch"]["govee_ripple_light"]["default"] == "mdi:waves"
