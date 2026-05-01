# Research: Govee 2FA Login Requirement

**Date**: 2026-04-05
**Type**: Feature Investigation
**Issue**: [lasswellt/govee-homeassistant#28](https://github.com/lasswellt/govee-homeassistant/issues/28)
**Agents**: 3/3 succeeded

---

## Summary

Govee has added mandatory two-factor authentication (2FA) to their undocumented account API (`app2.govee.com`). When logging in via email/password, the `/v2/login` endpoint now returns JSON `{"status": 454}` for affected accounts, meaning a verification code must be sent to the user's email before login can complete. This breaks MQTT credential retrieval for all third-party integrations. The only integration with a working fix is **homebridge-govee** (commit `25f9e52`). Our integration needs a new config flow step, updated auth client, and app version bump to support 2FA. The official Platform API (API key auth) is unaffected.

---

## Research Questions & Answers

### 1. What exactly changed in Govee's auth flow?
**Answer**: Two-phase change. Phase 1 (March 23-25): deprecated `/v1/login`, required updated headers on `/v2/login` -- already fixed in v2026.3.6. Phase 2 (rolling out): `/v2/login` now returns JSON status 454 for some accounts, requiring an email verification code. The 454 is in the **JSON response body** (`data.status`), NOT the HTTP status code (HTTP is 200).

### 2. What is the complete 2FA authentication flow?
**Answer**: Three-step flow documented below. Login attempt returns 454 -> request verification code via separate endpoint -> retry login with code in payload.

### 3. How has homebridge-govee implemented the fix?
**Answer**: Detects 454 in JSON body, calls `/v1/verification` endpoint with `{"type": 8, "email": "..."}`, prompts user for 4-digit code, retries login with `"code"` field added to payload. Distinguishes "need code" (no code sent) from "invalid code" (code sent but still 454).

### 4. Is govee2mqtt also fixed?
**Answer**: No. As of 2026-04-03, govee2mqtt has NOT implemented 2FA. Issues #647 and #649 are open. PR #650 only bumps app version but does not add 2FA. Users are stuck on API-key-only mode.

### 5. Are API-key-only users affected?
**Answer**: No. Our codebase correctly guards login behind `if email and password:` (`__init__.py:92`). Users seeing 454 errors with "API-key-only" likely have stale email/password in their config entry from before 2FA enforcement.

### 6. What is the scope of changes needed in our codebase?
**Answer**: 6 files, ~200-300 lines of new/modified code. HIGH: `auth.py` (new method + modified login), `config_flow.py` (new verification step). MEDIUM: `__init__.py` (startup graceful degradation). LOW: `exceptions.py`, `strings.json`, `translations/en.json`.

---

## Findings

### The 2FA Authentication Protocol

All three research agents confirmed the same protocol, cross-verified against homebridge-govee's implementation:

#### Step 1: Initial Login Attempt

```
POST https://app2.govee.com/account/rest/account/v2/login
```

**Payload:**
```json
{
  "email": "user@example.com",
  "password": "userpassword",
  "client": "generated-uuid-hex-32-chars"
}
```

**Required Headers:**
```
appVersion: 7.4.10
clientId: <same-uuid-as-client-field>
clientType: 1
iotVersion: 0
timestamp: <epoch-millis>
User-Agent: GoveeHome/7.4.10 (com.ihoment.GoVeeSensor; build:2; iOS 18.4.0) Alamofire/5.10.2
Content-Type: application/json
```

**Response when 2FA required:**
- HTTP status: **200** (NOT 454 -- the 454 is in the JSON body)
- Body: `{"status": 454, "message": ""}`

#### Step 2: Request Verification Code

```
POST https://app2.govee.com/account/rest/account/v1/verification
```

**Payload:**
```json
{
  "type": 8,
  "email": "user@example.com"
}
```

**Headers:** Same standard Govee app headers as Step 1.

**Result:** Govee sends a 4-digit verification code to the user's registered email. Code expires in ~15 minutes. No authentication token required for this endpoint.

#### Step 3: Login with Verification Code

Same endpoint as Step 1, with `code` field added:

```
POST https://app2.govee.com/account/rest/account/v2/login
```

**Payload:**
```json
{
  "email": "user@example.com",
  "password": "userpassword",
  "client": "generated-uuid-hex-32-chars",
  "code": "1234"
}
```

**On success:** Normal login response with token, client data, etc.
**On invalid/expired code:** `{"status": 454, "message": ""}` again.

### App Version Update Required

| Field | Current (our code) | Required |
|-------|-------------------|----------|
| `appVersion` | `7.3.30` | `7.4.10` |
| `User-Agent` | `GoveeHome/7.3.30 (... iOS 16.5.0) Alamofire/5.6.4` | `GoveeHome/7.4.10 (... iOS 18.4.0) Alamofire/5.10.2` |

### Two Distinct Error States for Status 454

| Scenario | Code Provided? | Status | Action |
|----------|---------------|--------|--------|
| 2FA required | No | 454 | Request verification code, show code entry step |
| Invalid/expired code | Yes | 454 | Show error, let user retry with new code |

### 2FA Rollout Status

The 2FA requirement is rolling out gradually -- not all accounts are affected yet. The pattern is unclear (may be region-based, account-age-based, or random). Accounts not yet requiring 2FA can still log in with just email+password on `/v2/login`.

---

## Compatibility Analysis

### Platform API (API Key) -- Unaffected
The official Govee Developer API at `openapi.api.govee.com` uses API key authentication and is completely unaffected. Device control, state queries, and scene discovery continue to work.

### Account API (Email/Password) -- Affected
Only the undocumented account API at `app2.govee.com` is affected. This API is used solely for:
- Retrieving MQTT/IoT credentials (certificates for AWS IoT Core)
- Real-time push updates via MQTT

### Existing Code Compatibility
- Our `/v2/login` endpoint is correct (already upgraded)
- Our header structure is correct (just needs version bump)
- `auth.py:458-475` already checks `data.get("status")` -- status 454 currently falls through to `GoveeLoginRejectedError`
- `config_flow.py` supports multi-step flows natively -- much better UX than homebridge-govee's config-file approach
- No dependency additions needed

---

## Recommendation

**Implement 2FA support in a single release** with the following approach:

1. **Update app version strings** to `7.4.10` (immediate, low-risk)
2. **Add 2FA auth flow** to `GoveeAuthClient` (new method + modified login)
3. **Add interactive config flow step** for verification code entry (HA native multi-step)
4. **Graceful startup degradation** when stored credentials require 2FA (fall back to polling, prompt reconfigure)

This makes us the **first Home Assistant integration** with Govee 2FA support (govee2mqtt is still broken). The HA config flow system gives us a significantly better UX than homebridge-govee's approach.

---

## Implementation Sketch

### 1. `api/exceptions.py` -- Add new exception

```python
class Govee2FARequiredError(GoveeApiError):
    """Raised when Govee requires a 2FA verification code."""

class Govee2FACodeInvalidError(GoveeApiError):
    """Raised when the provided 2FA code is invalid or expired."""
```

### 2. `api/auth.py` -- Update auth client

- **Bump constants:**
  - `GOVEE_APP_VERSION = "7.4.10"`
  - `GOVEE_USER_AGENT = "GoveeHome/7.4.10 (com.ihoment.GoVeeSensor; build:2; iOS 18.4.0) Alamofire/5.10.2"`

- **Add verification endpoint:**
  - `GOVEE_VERIFICATION_URL = "https://app2.govee.com/account/rest/account/v1/verification"`

- **Add `request_verification_code(email, client_id)` method:**
  - POST to verification endpoint with `{"type": 8, "email": email}`
  - Use same Govee headers

- **Modify `login()` method:**
  - Add optional `code: str | None = None` parameter
  - Include `"code": code` in payload when provided
  - On status 454 without code: raise `Govee2FARequiredError`
  - On status 454 with code: raise `Govee2FACodeInvalidError`

### 3. `config_flow.py` -- Add verification code step

- **`async_step_account()`**: On `Govee2FARequiredError`, call `request_verification_code()`, store email/password/client_id in `self.context`, return `self.async_show_form(step_id="verification_code")`
- **`async_step_verification_code()`**: New step with single text input for 4-digit code. Retry login with code. On success, proceed to entry creation. On `Govee2FACodeInvalidError`, show error and let user retry.
- **`async_step_reconfigure()`**: Same 2FA flow when re-entering credentials.
- **`client_id` must persist** across config flow steps (same ID for code request and login).

### 4. `__init__.py` -- Startup graceful degradation

- On `Govee2FARequiredError` during startup login:
  - Log warning: "Govee account requires 2FA verification. Use Reconfigure to re-enter credentials."
  - Skip MQTT setup, continue with polling-only mode
  - Consider creating a repair issue to guide user

### 5. `strings.json` + `translations/en.json` -- New strings

```json
{
  "step": {
    "verification_code": {
      "title": "Govee Verification Code",
      "description": "A verification code has been sent to your Govee account email. Enter the 4-digit code below.",
      "data": {
        "verification_code": "Verification code"
      }
    }
  },
  "error": {
    "invalid_verification_code": "Invalid or expired verification code. A new code has been sent.",
    "verification_failed": "Failed to send verification code. Please try again."
  }
}
```

### 6. Files changed summary

| File | Complexity | Changes |
|------|-----------|---------|
| `api/auth.py` | HIGH | Bump version, add verification method, modify login for code param, handle 454 states |
| `config_flow.py` | HIGH | New verification_code step, modify account/reconfigure flows, persist state across steps |
| `api/exceptions.py` | LOW | Add `Govee2FARequiredError`, `Govee2FACodeInvalidError` |
| `api/__init__.py` | LOW | Export new exceptions |
| `__init__.py` | MEDIUM | Handle 2FA during startup, graceful degradation |
| `strings.json` | LOW | New step and error strings |
| `translations/en.json` | LOW | Mirror strings.json changes |

---

## Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Govee changes 2FA flow again | Medium | HIGH | Follow homebridge-govee and community for changes; use flexible error handling |
| Verification code format changes (not always 4 digits) | Low | Low | Accept any string input, don't enforce 4-digit validation in UI |
| 2FA becomes mandatory for ALL accounts | High | Medium | Already handled -- this is what we're implementing |
| Token refresh also requires 2FA | Low | HIGH | Monitor; current token refresh endpoint (`/v1/first/refresh-tokens`) is separate from login |
| Code expiry before user enters it (~15 min) | Medium | Low | Show clear messaging; allow retry which re-sends code |
| Govee rate-limits verification code requests | Low | Medium | Don't auto-retry aggressively; show "resend code" guidance |

---

## References

- [homebridge-govee 2FA commit (25f9e52)](https://github.com/homebridge-plugins/homebridge-govee/commit/25f9e52b32c80e4c22d561d43e5f16753f91f71f)
- [homebridge-govee 2FA wiki](https://github.com/homebridge-plugins/homebridge-govee/wiki/AWS-Control#two-factor-authentication-2fa)
- [homebridge-govee lib/connection/http.js](https://github.com/homebridge-plugins/homebridge-govee/blob/latest/lib/connection/http.js) -- Reference implementation
- [govee2mqtt #647](https://github.com/wez/govee2mqtt/issues/647) -- API login broken (454)
- [govee2mqtt #649](https://github.com/wez/govee2mqtt/issues/649) -- Starting not possible due to API changes
- [govee2mqtt #637](https://github.com/wez/govee2mqtt/issues/637) -- Crash: app version too low
- [govee2mqtt #650](https://github.com/wez/govee2mqtt/pull/650) -- PR bumps version (no 2FA)
- [homebridge-govee #1253](https://github.com/homebridge-plugins/homebridge-govee/issues/1253) -- Accessories not displaying
- [lasswellt/govee-homeassistant#28](https://github.com/lasswellt/govee-homeassistant/issues/28) -- This issue
- [Govee Account Security Research](https://tightropemonkey.dev/posts/govee-part-1/) -- Security analysis of verification codes
