"""``services.yaml`` validated against Home Assistant's own description schema.

Why this file exists
--------------------
A service description that HA's schema rejects is not a loud failure: HA logs
it and drops **the whole service's UI schema**, so the service still exists but
its dialog loses every field and target. Nothing in a normal unit test touches
that file, so a rejected block ships silently — which is exactly how a
`target: {device: {filter: [...]}}` block (a form HA does not accept; the flat
`device: {integration: govee}` is the right one) reached production and took
the service dialog with it.

The schema is the real one — ``homeassistant.helpers.service._SERVICES_SCHEMA``,
what ``async_get_all_descriptions`` applies when it loads the file — reached
through a private name on purpose: there is no public export, and a rename
failing this test loudly is the desired outcome, not a reason to loosen it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import voluptuous as vol
from homeassistant.helpers.service import _SERVICES_SCHEMA
from homeassistant.util.yaml import load_yaml_dict

SERVICES_YAML = Path(__file__).resolve().parents[1] / "custom_components" / "govee" / "services.yaml"


def _descriptions() -> dict:
    return load_yaml_dict(str(SERVICES_YAML))


class TestServicesYaml:
    """The shipped file, exactly as HA reads it."""

    def test_services_yaml_matches_home_assistants_schema(self):
        """Every service description survives validation with nothing dropped."""
        validated = _SERVICES_SCHEMA(_descriptions())

        assert set(validated) == set(_descriptions())

    def test_every_service_registered_in_code_is_described(self):
        """A service with no description gets an empty dialog in the UI."""
        from custom_components.govee import services

        registered = {
            value for name, value in vars(services).items() if name.startswith("SERVICE_") and isinstance(value, str)
        }

        assert registered <= set(_descriptions())

    def test_the_schema_rejects_the_nested_device_filter_form(self):
        """Guard on the guard: this is the shape that shipped and was dropped.

        If HA ever stopped rejecting it the test above would keep passing for
        the wrong reason, so the rejection itself is pinned here.
        """
        broken = {"apply_diy_effect": {"target": {"device": {"filter": [{"integration": "govee"}]}}}}

        with pytest.raises(vol.Invalid):
            _SERVICES_SCHEMA(broken)

        # The flat form is the accepted one.
        assert _SERVICES_SCHEMA({"apply_diy_effect": {"target": {"device": {"integration": "govee"}}}})
