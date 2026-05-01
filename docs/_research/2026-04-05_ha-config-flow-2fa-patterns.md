# Research: Home Assistant Config Flow 2FA Patterns

**Date**: 2026-04-05
**Type**: Feature Investigation
**Context**: Informing Sprint 2 story S2-004 (config flow 2FA step) implementation

---

## Summary

Home Assistant core integrations (Ring, Blink, Nest) have established patterns for 2FA in config flows. **Ring is the canonical reference**: it catches a `Requires2FAError`, stores credentials in instance variables, transitions to an `async_step_2fa()` that shows a single code input form, then "trampolines" back to the originating step. State is always stored in instance variables (never `self.context`). All entry points (user, reauth, reconfigure) share the same 2FA step, using `self.source` to route back correctly. No HA integration implements "resend code" -- users restart the flow instead.

---

## Research Questions & Answers

### 1. How do Ring, Blink, etc. handle 2FA in config flows?
**Answer**: Ring catches `Requires2FAError`, stores credentials in `self.user_pass`, transitions to `async_step_2fa()`. The 2FA step merges the code with stored credentials and "trampolines" back to the originating step for validation. Blink uses a different pattern where the 2FA step calls the API directly. Both work; Ring's trampoline is more common in HA core.

### 2. What is the correct pattern for multi-step state passing?
**Answer**: Always use instance variables (`self._email`, `self._password`, `self._client_id`). Never use `self.context` for credentials -- it's reserved for HA framework use (source, entry_id).

### 3. How should reconfigure handle 2FA?
**Answer**: Same 2FA step shared across all entry points. Use `self.source` (SOURCE_REAUTH, SOURCE_RECONFIGURE) or a `self._reconfigure_entry` instance var to route back to the correct completion logic (`_create_entry` vs `async_update_reload_and_abort`).

### 4. What about "resend code" functionality?
**Answer**: No HA core integration implements it. Users restart the flow to trigger a new code.

### 5. Should we use async_show_progress for the code request?
**Answer**: No. `async_show_progress` is for background tasks. Use `async_show_form` for user input like verification codes.

---

## Findings

### The Ring Trampoline Pattern

Ring is the canonical HA 2FA pattern. The 2FA step is a pure UI step that collects the code and bounces back:

```python
async def async_step_2fa(self, user_input=None):
    if user_input:
        if self.source == SOURCE_REAUTH:
            return await self.async_step_reauth_confirm({**self.user_pass, **user_input})
        if self.source == SOURCE_RECONFIGURE:
            return await self.async_step_reconfigure({**self.user_pass, **user_input})
        return await self.async_step_user({**self.user_pass, **user_input})
    return self.async_show_form(
        step_id="2fa",
        data_schema=vol.Schema({vol.Required(CONF_2FA): str}),
    )
```

**Pros**: Validation logic stays in one place; error handling natural; reauth/reconfigure get 2FA free.
**Cons**: Re-runs full validation on trampoline return; requires the originating step to handle the merged `user_input` dict containing the code.

### The Blink Direct Pattern

Blink's 2FA step calls the API directly instead of trampolining:

```python
async def async_step_2fa(self, user_input=None):
    if user_input is not None:
        try:
            await _send_blink_2fa_pin(self.blink, user_input.get(CONF_PIN))
        except BlinkSetupError:
            errors["base"] = "cannot_connect"
        else:
            return self._async_finish_flow()
    return self.async_show_form(step_id="2fa", ...)
```

**Pros**: Cleaner separation; verify step owns its own error handling.
**Cons**: Duplicate completion logic across steps.

### State Management: Instance Variables Only

All HA core integrations use instance variables:

| Integration | State Storage |
|-------------|--------------|
| Ring | `self.user_pass` (dict), `self.hardware_id` (str) |
| Blink | `self.auth` (Auth), `self.blink` (Blink) |
| Nest | `self._data` (dict), `self._admin_client` |

### Strings Pattern for 2FA Steps

Ring's pattern:
```json
"2fa": {
    "title": "Two-factor authentication",
    "data": { "2fa": "Two-factor code" },
    "data_description": { "2fa": "Account verification code via the method selected..." }
}
```

---

## Recommendation for Govee

**Use a hybrid approach**: Blink's direct-call pattern (since Govee has a separate verify endpoint) with Ring's shared-step routing.

### Why Not Pure Ring Trampoline

Govee's 2FA flow is different from Ring's:
- Ring: login(email, password, code) -- code is part of the login payload
- Govee: login(email, password) -> request_verification_code(email) -> login(email, password, code) -- separate endpoint to trigger code send

The trampoline pattern works when the code is simply added to the login call. But Govee requires an intermediate API call to request the code, making the direct pattern cleaner.

### Why Not Pure Blink Direct

Blink stores the entire auth client object. Govee's API is stateless per-request -- only `client_id` needs to persist, not an HTTP session. Fresh `GoveeAuthClient` instances are lightweight and preferred.

### Recommended Pattern

```python
class GoveeConfigFlow(ConfigFlow, domain=DOMAIN):
    def __init__(self):
        self._api_key: str | None = None
        self._email: str | None = None
        self._password: str | None = None
        self._client_id: str | None = None  # Persists for 2FA
        self._iot_credentials: GoveeIotCredentials | None = None

    async def async_step_account(self, user_input=None):
        # ... existing validation ...
        except Govee2FARequiredError:
            self._email = email
            self._password = password
            self._client_id = uuid.uuid4().hex
            async with GoveeAuthClient() as client:
                await client.request_verification_code(email, self._client_id)
            return await self.async_step_verification_code()

    async def async_step_verification_code(self, user_input=None):
        errors = {}
        if user_input is not None:
            code = user_input["verification_code"].strip()
            try:
                self._iot_credentials = await validate_govee_credentials(
                    self._email, self._password,
                    code=code, client_id=self._client_id,
                )
                # Route based on entry point
                if self.source == SOURCE_RECONFIGURE:
                    return self._finish_reconfigure()
                return self._create_entry()
            except Govee2FACodeInvalidError:
                errors["base"] = "invalid_verification_code"
            except GoveeApiError:
                errors["base"] = "cannot_connect"
        return self.async_show_form(
            step_id="verification_code",
            data_schema=vol.Schema({
                vol.Required("verification_code"): str,
            }),
            errors=errors,
            description_placeholders={"email": self._email},
        )
```

### Key Design Decisions

1. **Instance variables** for email, password, client_id -- standard HA pattern
2. **Shared verification step** across account + reconfigure -- use `self.source` to route completion
3. **Fresh GoveeAuthClient per call** -- stateless API, only client_id must persist
4. **No resend code button** -- matches HA conventions; user restarts flow
5. **Direct-call in verify step** -- Govee has separate verify endpoint, not part of login payload
6. **`description_placeholders={"email": self._email}`** -- tell user which email to check

---

## Impact on Sprint 2 Stories

This research refines S2-004 implementation:

- **S2-003**: `validate_govee_credentials()` should accept `code` and `client_id` params (confirmed)
- **S2-004**: Use direct-call pattern in verify step, shared across account/reconfigure, `self.source` for routing
- **S2-005**: Startup catches `Govee2FARequiredError` same as `GoveeAuthError` (confirmed by both agents)

---

## References

- [Ring config_flow.py](https://github.com/home-assistant/core/blob/dev/homeassistant/components/ring/config_flow.py) -- Canonical 2FA trampoline
- [Blink config_flow.py](https://github.com/home-assistant/core/blob/dev/homeassistant/components/blink/config_flow.py) -- Direct-call 2FA
- [HA Config Flow docs](https://developers.home-assistant.io/docs/config_entries_config_flow_handler) -- Official documentation
- [HA Data Entry Flow docs](https://developers.home-assistant.io/docs/data_entry_flow_index/) -- Multi-step flow reference
