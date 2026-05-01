# Research: Govee Login HTTP 454 Error (Issue #28)

**Date**: 2026-03-30
**Type**: Feature Investigation / Bug Root Cause Analysis
**Issue**: [lasswellt/govee-homeassistant#28](https://github.com/lasswellt/govee-homeassistant/issues/28)
**Agents**: 3/3 succeeded

---

## Summary

Govee deprecated their `/v1/login` endpoint around March 23, 2026. Requests to it now return a custom status code 454 (empty message) regardless of credentials or headers. The fix requires three changes: (1) switch to the `/v2/login` endpoint, (2) update `appVersion` from `"6.5.02"` to `"7.3.30"`, and (3) add a `User-Agent` header mimicking the Govee iOS app. The homebridge-govee plugin has already shipped this fix (v11.18.0, March 24) and confirms it works. Password-based login is NOT deprecated — only the v1 endpoint is.

---

## Research Questions & Answers

### Q1: What does HTTP status 454 mean in Govee's API?
**Answer**: It's a custom Govee application-level status code (not standard HTTP). It means "deprecated endpoint" or "unsupported login version." Govee uses non-standard codes: 200=success, 400="app version too low", 401=invalid credentials, 451="email not registered", 454=deprecated/unsupported. The empty message confirms it was recently added without a human-readable description.

### Q2: What did govee2mqtt do to fix their login?
**Answer**: govee2mqtt PR #625 added 6 headers (appVersion, clientId, clientType, iotVersion, timestamp, User-Agent) to the v1 endpoint. This fixed the "app version too low" (400) error but may not fully resolve 454. govee2mqtt is written in Rust and their fix preceded the v1 deprecation — they may still be affected.

### Q3: What did homebridge-govee do differently?
**Answer**: homebridge-govee v11.18.0 (March 24) switched to the **v2 login endpoint** (`/account/rest/account/v2/login`), updated appVersion to `"7.3.30"`, and sends a full iOS User-Agent string. This is the only integration confirmed working as of March 28.

### Q4: Is Govee deprecating password-based login entirely?
**Answer**: No. Password-based login still works on the v2 endpoint. However, the Govee website has moved to email-code/OTP authentication, which may indicate a future direction. The mobile app API still accepts passwords.

### Q5: Why does the error show "check your internet connection"?
**Answer**: HTTP 454 is caught as a generic `GoveeApiError` in `config_flow.py`, which maps to the `"cannot_connect"` error key. This is misleading — 454 is a server-side rejection, not a connectivity issue.

### Q6: Is the password hashing (MD5) the issue?
**Answer**: No. govee2mqtt sends the password in plaintext, and homebridge-govee also sends it in plaintext on the v2 endpoint. MD5 hashing is NOT required. The codebase analyst initially hypothesized this, but cross-referencing with the other agents disproves it.

---

## Findings

### Finding 1: v1 Login Endpoint is Deprecated (Root Cause)

**Source**: Web research (homebridge-govee v11.18.0 changelog, community reports)

Govee deprecated `https://app2.govee.com/account/rest/account/v1/login` around March 23, 2026. All requests to v1 now return `{"status": 454, "message": ""}` regardless of credentials or headers. The working endpoint is:

```
POST https://app2.govee.com/account/rest/account/v2/login
```

The v2 endpoint accepts the same payload format and headers.

### Finding 2: appVersion is Outdated

**Source**: Web research, govee2mqtt comparison

| Integration | appVersion | Status |
|-------------|-----------|--------|
| hacs-govee (current) | `"6.5.02"` | Broken |
| govee2mqtt PR #625 | `"6.5.02"` | Partial fix |
| homebridge-govee v11.18.0 | `"7.3.30"` | Working |
| Current Govee Android APK | `"7.4.01"` | Latest |

The recommended version is `"7.3.30"` (matching homebridge-govee's known-working value) or `"7.4.01"` (latest Android APK).

### Finding 3: Missing User-Agent Header

**Source**: Codebase analysis, govee2mqtt comparison

The login request sends no `User-Agent` header. Both govee2mqtt and homebridge-govee include a Govee-app-mimicking User-Agent:

```
GoveeHome/{appVersion} (com.ihoment.GoVeeSensor; build:2; iOS 16.5.0) Alamofire/5.6.4
```

homebridge-govee uses a newer variant:
```
GoveeHome/7.3.30 (com.ihoment.GoVeeSensor; build:11; iOS 26.4.0) Alamofire/5.11.0
```

### Finding 4: Error Handling Maps 454 to Wrong User Message

**Source**: Codebase analysis (`auth.py`, `config_flow.py`, `strings.json`)

The error handling chain:
1. `auth.py` line 439-453: HTTP 454 (non-200, non-401) raises `GoveeApiError("Login failed: ", code=454)`
2. `config_flow.py`: catches `GoveeApiError` and maps to `errors["base"] = "cannot_connect"`
3. User sees: "Failed to connect to Govee API. Check your internet connection."

This is misleading. A 454 should map to a more descriptive error like "Login service unavailable" or "Authentication method not supported."

### Finding 5: Secondary Endpoints Also Missing Headers

**Source**: Codebase analysis (`auth.py`)

The `get_iot_key()` and `fetch_device_topics()` methods only send `Authorization`, `Content-Type`, and `Accept` headers — they're missing the Govee app headers (appVersion, clientId, etc.). These may break next if Govee enforces headers on all authenticated endpoints.

---

## Compatibility Analysis

### Current State
- **Login endpoint**: `/v1/login` — **deprecated by Govee**
- **appVersion**: `"6.5.02"` — outdated (current app is 7.4.01)
- **User-Agent**: not set — may trigger request rejection
- **Password format**: plaintext — correct, no change needed
- **Other headers**: appVersion, clientId, clientType, iotVersion, timestamp — correct

### Required Changes
All changes are backward-compatible within the integration. No HA API changes needed. No new dependencies.

---

## Recommendation

**Switch to the v2 login endpoint, update appVersion, and add User-Agent header.** This is a 3-line change in `auth.py` constants, plus adding the User-Agent to the headers dict.

This is the same approach homebridge-govee used successfully. It's the lowest-risk fix with the highest confidence of success.

Additionally, improve the error handling for 454 to show a meaningful message instead of "check your internet connection."

---

## Implementation Sketch

### File: `custom_components/govee/api/auth.py`

**Change 1: Update login URL (line 74)**
```python
# Before:
GOVEE_LOGIN_URL = "https://app2.govee.com/account/rest/account/v1/login"
# After:
GOVEE_LOGIN_URL = "https://app2.govee.com/account/rest/account/v2/login"
```

**Change 2: Update app version (line 78)**
```python
# Before:
GOVEE_APP_VERSION = "6.5.02"
# After:
GOVEE_APP_VERSION = "7.3.30"
```

**Change 3: Add User-Agent constant**
```python
GOVEE_USER_AGENT = f"GoveeHome/{GOVEE_APP_VERSION} (com.ihoment.GoVeeSensor; build:2; iOS 16.5.0) Alamofire/5.6.4"
```

**Change 4: Add User-Agent to login headers (around line 407-415)**
```python
headers = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "appVersion": GOVEE_APP_VERSION,
    "clientId": client_id,
    "clientType": GOVEE_CLIENT_TYPE,
    "iotVersion": GOVEE_IOT_VERSION,
    "timestamp": timestamp_ms,
    "User-Agent": GOVEE_USER_AGENT,  # NEW
}
```

### File: `custom_components/govee/api/auth.py` (error handling)

**Change 5: Better error for non-standard status codes (lines 439-453)**
Consider adding a specific check for 454 to raise a more descriptive error, or at minimum improve the message mapping in config_flow.py.

### File: `custom_components/govee/config_flow.py` (error message)

**Change 6: Map server rejections to a better error key**
Currently all `GoveeApiError` map to `"cannot_connect"`. Consider differentiating between actual connection failures (`aiohttp.ClientError`) and API rejections (non-200 status codes).

### Optional: Update secondary endpoints

Add the Govee app headers to `get_iot_key()` and `fetch_device_topics()` proactively, in case Govee enforces them on authenticated endpoints next.

---

## Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| v2 endpoint response format differs from v1 | Medium | Test with real credentials; homebridge-govee uses same field paths |
| appVersion `7.3.30` becomes outdated | Low | This is a known-working value; can be updated later |
| Govee deprecates password login entirely | Low | Would affect all third-party integrations; not imminent |
| v2 endpoint requires additional payload fields | Low | homebridge-govee sends the same payload as v1 |

---

## References

- [lasswellt/govee-homeassistant#28](https://github.com/lasswellt/govee-homeassistant/issues/28) — This issue
- [wez/govee2mqtt#622](https://github.com/wez/govee2mqtt/issues/622) — govee2mqtt "app version too low" issue
- [wez/govee2mqtt#625](https://github.com/wez/govee2mqtt/pull/625) — govee2mqtt header fix PR
- homebridge-govee v11.18.0 — Working v2 login implementation
- `custom_components/govee/api/auth.py` — Current auth implementation
- `custom_components/govee/config_flow.py` — Error handling and UI flow
- `docs/govee-protocol-reference.md` — Protocol documentation
