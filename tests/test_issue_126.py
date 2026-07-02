"""Tests for issue #126 — H60B3 uplighter per-part light toggles.

The H60B3 exposes its three physical light parts as named toggles
(nebulaLightToggle / sideLightToggle / bottomLightToggle). Detection is the
generic ``named_light_toggle_instances`` property (which also covers the
H1310/H1370 main/background toggles from issue #114); the switch platform maps
each known instance to a translation key, unique_id suffix, and icon.

Also covers issue #128 — H5075/H5100 thermo-hygrometers report °F under the
°C-tagged unit, so they join FAHRENHEIT_REPORTING_SKUS.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.govee.const import resolve_fahrenheit_conversion
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


def _cap(cap_type: str, instance: str, params: dict | None = None) -> GoveeCapability:
    return GoveeCapability(type=cap_type, instance=instance, parameters=params or {})


def _h60b3() -> GoveeDevice:
    # Capability shape from the issue-#126 diagnostics download.
    return GoveeDevice(
        device_id="AA:BB:CC:DD:EE:FF:60:B3",
        sku="H60B3",
        name="Ecklampe",
        device_type=DEVICE_TYPE_LIGHT,
        capabilities=(
            _cap(CAPABILITY_ON_OFF, INSTANCE_POWER),
            _cap(CAPABILITY_RANGE, INSTANCE_BRIGHTNESS),
            _cap(CAPABILITY_COLOR_SETTING, INSTANCE_COLOR_RGB),
            _cap(CAPABILITY_TOGGLE, "nebulaLightToggle"),
            _cap(CAPABILITY_TOGGLE, "sideLightToggle"),
            _cap(CAPABILITY_TOGGLE, "bottomLightToggle"),
            _cap(CAPABILITY_TOGGLE, "dreamViewToggle"),
        ),
    )


def _h1310() -> GoveeDevice:
    return GoveeDevice(
        device_id="AA:BB:CC:DD:EE:FF:13:10",
        sku="H1310",
        name="Basement Bedroom Fan",
        device_type=DEVICE_TYPE_LIGHT,
        capabilities=(
            _cap(CAPABILITY_ON_OFF, INSTANCE_POWER),
            _cap(CAPABILITY_TOGGLE, "mainLightToggle"),
            _cap(CAPABILITY_TOGGLE, "backgroundLightToggle"),
            _cap(CAPABILITY_TOGGLE, "fanToggle"),
        ),
    )


class TestNamedLightToggleDetection:
    def test_h60b3_three_named_toggles(self):
        assert _h60b3().named_light_toggle_instances == [
            "nebulaLightToggle",
            "sideLightToggle",
            "bottomLightToggle",
        ]

    def test_h1310_main_background(self):
        assert _h1310().named_light_toggle_instances == [
            "mainLightToggle",
            "backgroundLightToggle",
        ]

    def test_excludes_non_light_and_mode_toggles(self):
        # dreamViewToggle / fanToggle / nightlightToggle / light{N}Toggle must
        # not match — they have their own entities.
        dev = GoveeDevice(
            device_id="AA:BB:CC:DD:EE:FF:00:01",
            sku="H0000",
            name="Mixed toggles",
            device_type=DEVICE_TYPE_LIGHT,
            capabilities=(
                _cap(CAPABILITY_TOGGLE, "dreamViewToggle"),
                _cap(CAPABILITY_TOGGLE, "nightlightToggle"),
                _cap(CAPABILITY_TOGGLE, "light1Toggle"),
                _cap(CAPABILITY_TOGGLE, "fanToggle"),
                _cap(CAPABILITY_TOGGLE, "gradientToggle"),
            ),
        )
        assert dev.named_light_toggle_instances == []


class TestSwitchPlatformWiring:
    async def _setup(self, device):
        from custom_components.govee import switch as switch_mod

        coordinator = MagicMock()
        coordinator.devices = {device.device_id: device}
        entry = MagicMock()
        entry.runtime_data = coordinator
        added: list = []
        await switch_mod.async_setup_entry(
            MagicMock(), entry, lambda ents: added.extend(ents)
        )
        return added

    @pytest.mark.asyncio
    async def test_h60b3_creates_three_named_light_switches(self):
        added = await self._setup(_h60b3())
        named = {
            e._toggle_instance: e
            for e in added
            if type(e).__name__ == "GoveeNamedLightSwitchEntity"
        }
        assert sorted(named) == [
            "bottomLightToggle",
            "nebulaLightToggle",
            "sideLightToggle",
        ]
        assert (
            named["nebulaLightToggle"]._attr_unique_id
            == "AA:BB:CC:DD:EE:FF:60:B3_nebula_light"
        )
        assert named["sideLightToggle"]._attr_translation_key == "govee_side_light"
        assert named["bottomLightToggle"]._attr_icon == "mdi:floor-lamp"

    @pytest.mark.asyncio
    async def test_unknown_named_toggle_skipped(self):
        dev = GoveeDevice(
            device_id="AA:BB:CC:DD:EE:FF:00:02",
            sku="H9999",
            name="Future lamp",
            device_type=DEVICE_TYPE_LIGHT,
            capabilities=(_cap(CAPABILITY_TOGGLE, "haloLightToggle"),),
        )
        added = await self._setup(dev)
        named = [e for e in added if type(e).__name__ == "GoveeNamedLightSwitchEntity"]
        assert named == []


class TestFahrenheitAllowlistIssue128:
    def test_h5075_h5100_convert_in_auto_mode(self):
        assert resolve_fahrenheit_conversion("H5075", "auto") is True
        assert resolve_fahrenheit_conversion("H5100", "auto") is True

    def test_explicit_celsius_still_wins(self):
        assert resolve_fahrenheit_conversion("H5075", "celsius") is False
