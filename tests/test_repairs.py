"""Repair issues and their fix flows.

Both remaining repairs are actionable: the rate-limit flow raises the polling
interval and the MQTT flow clears the stored sign-in failure and reloads the
entry. The flows are exercised directly with a real issue registry.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.govee.const import (
    CONF_API_KEY,
    CONF_POLL_INTERVAL,
    DOMAIN,
    KEY_IOT_LOGIN_FAILED,
    MAX_POLL_INTERVAL,
)
from custom_components.govee.repairs import (
    ISSUE_MQTT_DISCONNECTED,
    ISSUE_RATE_LIMITED,
    MqttReconnectRepairFlow,
    RateLimitRepairFlow,
    async_cleanup_legacy_issues,
    async_create_fix_flow,
    async_create_mqtt_issue,
    async_create_rate_limit_issue,
    async_delete_mqtt_issue,
    async_delete_rate_limit_issue,
)


def _entry(hass: HomeAssistant, **kwargs) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_API_KEY: "key", **kwargs.pop("data", {})}, **kwargs)
    entry.add_to_hass(hass)
    return entry


def _flow(flow_cls, hass: HomeAssistant, issue_id: str, entry_id: str | None, **extra: str):
    flow = flow_cls()
    flow.hass = hass
    flow.handler = DOMAIN
    flow.flow_id = "test-flow"
    flow.issue_id = issue_id
    flow.data = {"entry_id": entry_id, **extra} if entry_id else {}
    return flow


async def test_rate_limit_issue_is_fixable_and_deletable(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    registry = ir.async_get(hass)

    async_create_rate_limit_issue(hass, entry, "120 seconds")
    issue = registry.async_get_issue(DOMAIN, f"{ISSUE_RATE_LIMITED}_{entry.entry_id}")
    assert issue is not None
    assert issue.is_fixable is True
    assert issue.translation_placeholders == {"entry_title": entry.title}
    assert issue.data == {"entry_id": entry.entry_id, "reset_time": "120 seconds"}

    async_delete_rate_limit_issue(hass, entry)
    assert registry.async_get_issue(DOMAIN, f"{ISSUE_RATE_LIMITED}_{entry.entry_id}") is None


async def test_mqtt_issue_is_fixable_and_deletable(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    registry = ir.async_get(hass)

    async_create_mqtt_issue(hass, entry, "3 reconnect attempts failed")
    issue = registry.async_get_issue(DOMAIN, f"{ISSUE_MQTT_DISCONNECTED}_{entry.entry_id}")
    assert issue is not None
    assert issue.is_fixable is True
    assert issue.translation_placeholders == {"entry_title": entry.title}
    assert issue.data == {"entry_id": entry.entry_id, "reason": "3 reconnect attempts failed"}

    async_delete_mqtt_issue(hass, entry)
    assert registry.async_get_issue(DOMAIN, f"{ISSUE_MQTT_DISCONNECTED}_{entry.entry_id}") is None


async def test_fix_flow_dispatch(hass: HomeAssistant) -> None:
    assert isinstance(await async_create_fix_flow(hass, f"{ISSUE_RATE_LIMITED}_x", None), RateLimitRepairFlow)
    assert isinstance(await async_create_fix_flow(hass, f"{ISSUE_MQTT_DISCONNECTED}_x", None), MqttReconnectRepairFlow)


async def test_rate_limit_flow_doubles_the_polling_interval(hass: HomeAssistant) -> None:
    entry = _entry(hass, options={CONF_POLL_INTERVAL: 60})
    flow = _flow(
        RateLimitRepairFlow, hass, f"{ISSUE_RATE_LIMITED}_{entry.entry_id}", entry.entry_id, reset_time="120 seconds"
    )

    result = await flow.async_step_init()
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "confirm"
    assert result["description_placeholders"] == {
        "entry_title": entry.title,
        "reset_time": "120 seconds",
        "current": "60",
        "proposed": "120",
    }

    result = await flow.async_step_confirm({})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_POLL_INTERVAL] == 120


async def test_rate_limit_flow_caps_at_the_maximum(hass: HomeAssistant) -> None:
    entry = _entry(hass, options={CONF_POLL_INTERVAL: MAX_POLL_INTERVAL})
    flow = _flow(RateLimitRepairFlow, hass, f"{ISSUE_RATE_LIMITED}_{entry.entry_id}", entry.entry_id)

    result = await flow.async_step_confirm()
    assert result["description_placeholders"]["proposed"] == str(MAX_POLL_INTERVAL)
    # An issue created without a reset time still renders the step.
    assert result["description_placeholders"]["reset_time"] == "a few minutes"

    await flow.async_step_confirm({})
    assert entry.options[CONF_POLL_INTERVAL] == MAX_POLL_INTERVAL


async def test_mqtt_flow_clears_marker_and_reloads(hass: HomeAssistant) -> None:
    entry = _entry(hass, data={KEY_IOT_LOGIN_FAILED: "wrong password"})
    flow = _flow(
        MqttReconnectRepairFlow,
        hass,
        f"{ISSUE_MQTT_DISCONNECTED}_{entry.entry_id}",
        entry.entry_id,
        reason="login failed",
    )

    result = await flow.async_step_init()
    assert result["type"] is FlowResultType.FORM
    assert result["description_placeholders"] == {"entry_title": entry.title, "reason": "login failed"}

    with patch.object(hass.config_entries, "async_reload", AsyncMock(return_value=True)) as reload:
        result = await flow.async_step_confirm({})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert KEY_IOT_LOGIN_FAILED not in entry.data
    reload.assert_awaited_once_with(entry.entry_id)


async def test_cleanup_deletes_pre_rename_legacy_issues(hass: HomeAssistant) -> None:
    """rate_limit_minute/poll_interval_unsustainable predate ISSUE_RATE_LIMITED.

    An install that hit either before the rename carries the orphaned entry
    forever, since current code never creates one to let it auto-clear.
    """
    entry = _entry(hass)
    registry = ir.async_get(hass)
    for legacy_id in (f"rate_limit_minute_{entry.entry_id}", f"poll_interval_unsustainable_{entry.entry_id}"):
        ir.async_create_issue(
            hass,
            DOMAIN,
            legacy_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=legacy_id,
        )

    async_cleanup_legacy_issues(hass, entry)

    assert registry.async_get_issue(DOMAIN, f"rate_limit_minute_{entry.entry_id}") is None
    assert registry.async_get_issue(DOMAIN, f"poll_interval_unsustainable_{entry.entry_id}") is None


def test_cleanup_is_a_no_op_when_nothing_legacy_exists(hass: HomeAssistant) -> None:
    """Called on every setup, so it must not raise when there is nothing to delete."""
    entry = _entry(hass)

    async_cleanup_legacy_issues(hass, entry)


@pytest.mark.parametrize("flow_cls", [RateLimitRepairFlow, MqttReconnectRepairFlow])
async def test_flows_abort_when_the_entry_is_gone(hass: HomeAssistant, flow_cls) -> None:
    flow = _flow(flow_cls, hass, "issue", "missing-entry")
    result = await flow.async_step_confirm()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "entry_not_found"

    flow = _flow(flow_cls, hass, "issue", None)
    result = await flow.async_step_confirm()
    assert result["type"] is FlowResultType.ABORT
