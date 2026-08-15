"""Service-handler behaviour that is not covered by the YAML schema tests.

The services are registered on a real ``hass`` and driven through
``hass.services.async_call``, so every payload passes the real schema and
reaches the handler as a real ``ServiceCall``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.exceptions import ServiceValidationError

from custom_components.govee import services
from custom_components.govee.const import DOMAIN
from custom_components.govee.models import RGBColor, SegmentColorCommand

DEVICE_ID = "AA:BB:CC:DD:EE:FF:60:B0"


def _coordinator() -> MagicMock:
    coordinator = MagicMock()
    coordinator.devices = {DEVICE_ID: MagicMock()}
    coordinator.async_control_device = AsyncMock(return_value=True)
    return coordinator


async def _register(monkeypatch, hass, coordinator) -> None:
    await services.async_setup_services(hass)
    monkeypatch.setattr(
        services,
        "_get_coordinator_for_device",
        lambda _hass, device_id: (coordinator if device_id in coordinator.devices else None),
    )


class TestSetSegmentColor:
    """``govee.set_segment_color`` — an unknown device is a user error."""

    @pytest.mark.asyncio
    async def test_an_unknown_device_raises_service_validation_error(self, monkeypatch, hass):
        """A wrong device id used to return silently, so the automation looked fine."""
        coordinator = _coordinator()
        await _register(monkeypatch, hass, coordinator)

        with pytest.raises(ServiceValidationError):
            await hass.services.async_call(
                DOMAIN,
                services.SERVICE_SET_SEGMENT_COLOR,
                {"device_id": "NO:SUCH:DEVICE", "segments": [1], "rgb_color": [255, 0, 0]},
                blocking=True,
            )

        coordinator.async_control_device.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_known_device_still_sends_the_command(self, monkeypatch, hass):
        coordinator = _coordinator()
        await _register(monkeypatch, hass, coordinator)

        await hass.services.async_call(
            DOMAIN,
            services.SERVICE_SET_SEGMENT_COLOR,
            {"device_id": DEVICE_ID, "segments": [1, 3], "rgb_color": [255, 0, 0]},
            blocking=True,
        )

        coordinator.async_control_device.assert_awaited_once_with(
            DEVICE_ID,
            SegmentColorCommand(segment_indices=(1, 3), color=RGBColor(r=255, g=0, b=0)),
        )
