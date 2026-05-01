# Research: Govee AWS IoT Core MQTT Integration

**Date**: 2026-03-30
**Type**: Feature Investigation / Architecture Analysis
**Issue Context**: [lasswellt/govee-homeassistant#28](https://github.com/lasswellt/govee-homeassistant/issues/28) — login fix cascading effects on MQTT pipeline
**Agents**: 3/3 succeeded

---

## Summary

Govee uses AWS IoT Core (us-east-1) for real-time device state push via MQTT over mutual TLS. The flow is: account login → IoT key retrieval (P12 certificate) → SSL context with mTLS → MQTT subscription on account topic. This integration's implementation is architecturally correct and matches the pattern used by govee2mqtt and homebridge-govee. The v1→v2 login endpoint change (issue #28) only affects the first step — the IoT key endpoint (`/app/v1/account/iot/key`) and device topic endpoint are unchanged. However, several enhancement opportunities exist: sending control commands via MQTT (not just ptReal), implementing MQTT status requests, adding certificate caching, and proactively adding app headers to secondary endpoints before Govee enforces them.

---

## Research Questions & Answers

### Q1: How does the full MQTT connection flow work end-to-end?
**Answer**: Four stages: (1) POST to `/account/rest/account/v2/login` with email/password returns a JWT token, account topic (`GA/{uuid}`), and accountId. (2) GET to `/app/v1/account/iot/key` with Bearer token returns a base64-encoded PKCS#12 certificate container + password + AWS IoT endpoint hostname. (3) Extract PEM cert and PKCS8 private key from P12 using the `cryptography` library. (4) Connect via `aiomqtt` to `aqm3wd1qlc3dy-ats.iot.us-east-1.amazonaws.com:8883` with mutual TLS, subscribe to the account topic. A single subscription receives state updates for ALL devices on the account.

### Q2: How do govee2mqtt and homebridge-govee implement this?
**Answer**: All implementations follow the identical 4-stage flow. Key differences: govee2mqtt uses `mosquitto_rs` (Rust), homebridge-govee uses `aws-iot-device-sdk` (JS). Both send full command sets via MQTT (power, brightness, color), while we only send ptReal. Both cache certificates on disk (12h TTL in govee2mqtt). homebridge-govee already uses `/v2/login` with appVersion `7.3.30`.

### Q3: What is the current state of our mqtt.py implementation?
**Answer**: Solid foundation — uses `aiomqtt` with mTLS on port 8883, Amazon Root CA 1 hardcoded, 120s keepalive, exponential backoff reconnection (5s→300s, max 50 attempts). Subscribes to `GA/` account topic, publishes `ptReal` BLE passthrough commands to `GD/` device topics. Filters out `msg`-wrapped echo messages. State updates parsed from flat JSON keys (`onOff`, `brightness`, `color`, `colorTemInKelvin`).

### Q4: What are the MQTT topic patterns and message formats?
**Answer**: Two topic types: `GA/{account-uuid}` (subscribe, receives all device state updates) and `GD/{device-uuid}` (publish, sends commands to specific devices). State updates contain `device`, `sku`, and `state` dict with flat keys. Commands use a `msg` wrapper with `cmd`, `data`, `cmdVersion`, `transaction`, `type` fields. Device-specific topic subscription is explicitly blocked by Govee's server (govee2mqtt discovered this causes disconnection).

### Q5: Are there known issues with IoT certificate provisioning?
**Answer**: The `/app/v1/account/iot/key` endpoint is **unchanged** and still works. Certificates are PKCS#12 format with long validity. Token expiry (~7 days, `tokenExpireCycle: 604800`) requires re-login to refresh IoT credentials. Login is rate-limited to ~30 attempts per 24 hours. No certificate rotation issues reported.

### Q6: How does the v1→v2 login change affect the IoT key chain?
**Answer**: Only the first step (login) is affected. The IoT key endpoint and device topic endpoint still use v1 paths and only require Bearer token auth. However, these secondary endpoints currently only send `Authorization`, `Content-Type`, and `Accept` headers — if Govee enforces app headers on authenticated endpoints in the future, they would break.

---

## Findings

### Finding 1: Architecture is Correct, Implementation Matches Community Standard

**Source**: All three agents (codebase, govee2mqtt comparison, web research)

Our 4-stage MQTT flow matches all other working integrations. The same P12→PEM extraction, the same mTLS setup, the same topic structure. The `GoveeAwsIotClient` class in `mqtt.py` is well-structured with proper reconnection logic, error handling, and clean shutdown.

### Finding 2: Two Separate MQTT Systems Exist

**Source**: Web research

Govee has two distinct MQTT systems:
1. **Official MQTT** (`mqtt.openapi.govee.com:8883`): API-key auth, EVENT capabilities only (sensors/alerts). Documented.
2. **AWS IoT MQTT** (`aqm3wd1qlc3dy-ats.iot.us-east-1.amazonaws.com:8883`): Certificate auth from account login, full state push. Undocumented.

This integration uses AWS IoT MQTT for real-time state. The official MQTT is not used.

### Finding 3: We Only Send ptReal Commands via MQTT

**Source**: Codebase analysis, govee2mqtt comparison

| Command Type | govee2mqtt | homebridge-govee | Our integration |
|---|---|---|---|
| Power (turn) | MQTT | MQTT | REST API only |
| Brightness | MQTT | MQTT | REST API only |
| Color/temp (colorwc) | MQTT | MQTT | REST API only |
| BLE passthrough (ptReal) | MQTT | MQTT | MQTT |
| Status request | MQTT | MQTT | REST API polling |

govee2mqtt and homebridge-govee send ALL commands via MQTT for lower latency (~50ms vs 2-4s REST). We only use MQTT for ptReal commands (music mode, DreamView, DIY scenes) and fall back to REST API for everything else.

### Finding 4: We Filter Messages That Other Integrations Parse

**Source**: Codebase analysis, govee2mqtt comparison

Our `_handle_message()` ignores messages with a `"msg"` key (treating them as command echoes). However, homebridge-govee parses `msg`-wrapped messages for older device compatibility:
```javascript
if (payload.msg) {
  payload = JSON.parse(payload.msg);
}
```
Some older devices wrap their state updates inside a `msg` string field. We may be missing state updates from these devices.

### Finding 5: Secondary Endpoints Lack App Headers

**Source**: Codebase analysis

| Endpoint | Has App Headers? | Risk |
|---|---|---|
| `/account/rest/account/v2/login` | Yes (fixed) | None |
| `/app/v1/account/iot/key` | No — only Authorization, Content-Type, Accept | Medium |
| `/device/rest/devices/v1/list` | No — only Authorization, Content-Type, Accept | Medium |

If Govee tightens enforcement on authenticated endpoints (as they did for login), IoT key retrieval and device topic fetching would break silently.

### Finding 6: No Certificate or Token Caching

**Source**: Codebase analysis, govee2mqtt comparison

govee2mqtt caches certificates on disk with 12-hour TTL, reusing them across restarts. Our integration fetches fresh credentials on every HA restart/reload, consuming login rate limit quota (~30/day). With frequent HA restarts during development, this could exhaust the daily allowance.

### Finding 7: No Token Refresh Implementation

**Source**: Web research

The login response includes `refreshToken` and `tokenExpireCycle: 604800` (7 days). Govee has a refresh endpoint (`/account/rest/v1/first/refresh-tokens`). Our integration stores `refresh_token` in the credentials but never uses it. When the token expires after 7 days, MQTT silently breaks until the next full login.

---

## Compatibility Analysis

### Current State
- **MQTT connection**: Working correctly when login succeeds
- **P12 extraction**: Handles both P12 and PEM formats, URL-safe base64 variants
- **Reconnection**: Exponential backoff (5s→300s, 50 max attempts)
- **Fallback**: Graceful degradation to REST polling when MQTT unavailable
- **Observer pattern**: Clean entity notification via `IStateObserver` protocol

### Impact of v1→v2 Login Fix
The login endpoint change ONLY affects Stage 1. Stages 2-4 (IoT key, certificate extraction, MQTT connection) are unaffected. Once login returns a valid token, the entire MQTT pipeline works as before.

### Dependencies
| Dependency | Version | Purpose |
|---|---|---|
| `aiomqtt` | >=2.0.0 | Async MQTT client |
| `cryptography` | >=41.0.0 | P12/PKCS12 certificate extraction |
| `ssl` (stdlib) | Python 3.12+ | TLS context for mTLS |

No new dependencies needed for any recommended changes.

---

## Recommendation

**Priority 1 (Issue #28 fix)**: Switch login to `/v2/login`, update `appVersion` to `7.3.30`, add `User-Agent` header. This is documented in the companion research: `docs/_research/2026-03-30_govee-login-454-error.md`.

**Priority 2 (Hardening)**: Add app headers to `get_iot_key()` and `fetch_device_topics()` proactively. This is a low-effort change that prevents future breakage.

**Priority 3 (Enhancement)**: Consider sending power/brightness/color commands via MQTT instead of REST for lower latency. This would benefit users with many devices (avoids REST rate limits).

**Priority 4 (Robustness)**: Implement token refresh using the stored `refreshToken` to extend sessions beyond 7 days without re-login.

---

## Implementation Sketch

### Priority 1: Login fix (see companion document)
File: `custom_components/govee/api/auth.py` — 3 constant changes + 1 header addition.

### Priority 2: Add app headers to secondary endpoints

**File**: `custom_components/govee/api/auth.py`

Create a shared helper for Govee app headers:
```python
def _govee_headers(self, client_id: str | None = None) -> dict[str, str]:
    """Build standard Govee app headers."""
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "appVersion": GOVEE_APP_VERSION,
        "clientId": client_id or uuid.uuid4().hex,
        "clientType": GOVEE_CLIENT_TYPE,
        "iotVersion": GOVEE_IOT_VERSION,
        "timestamp": str(int(time.time() * 1000)),
        "User-Agent": GOVEE_USER_AGENT,
    }
```

Apply to `get_iot_key()` (~line 459) and `fetch_device_topics()` (~line 534), adding the app headers alongside the existing `Authorization: Bearer` header.

### Priority 3: MQTT command publishing (future)

**File**: `custom_components/govee/api/mqtt.py`

Add methods alongside existing `async_publish_ptreal()`:
```python
async def async_publish_turn(self, device_topic: str, device_id: str, sku: str, on: bool) -> None:
    """Send power command via MQTT."""
    ...

async def async_publish_brightness(self, device_topic: str, device_id: str, sku: str, val: int) -> None:
    """Send brightness command via MQTT."""
    ...

async def async_publish_color(self, device_topic: str, device_id: str, sku: str, color: dict, kelvin: int) -> None:
    """Send color/temp command via MQTT."""
    ...
```

### Priority 4: Token refresh (future)

**File**: `custom_components/govee/api/auth.py`

Implement a `refresh_token()` method using `GET /account/rest/v1/first/refresh-tokens` with the stored refresh token. Call from coordinator when token nears expiry (check `tokenExpireCycle`).

---

## Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| Govee enforces app headers on `/app/v1/account/iot/key` | Medium | Priority 2 fix (add headers proactively) |
| `appVersion` `7.3.30` becomes insufficient | Low | Monitor Govee app releases; update as needed |
| Govee deprecates password login entirely | Low | Would affect all third-party integrations; watch for OTP/code auth |
| MQTT command publishing has different error handling than REST | Low | Test thoroughly; fall back to REST on MQTT publish failure |
| Token refresh endpoint may require app headers | Low | Use the same `_govee_headers()` helper |
| Device topic subscription causes disconnection | Known | Already not attempted; documented by govee2mqtt |
| Older devices wrap state in `msg` field | Low | Consider parsing `msg`-wrapped messages in `_handle_message()` |

---

## References

### Internal
- `custom_components/govee/api/auth.py` — Login, IoT key, device topics, P12 extraction
- `custom_components/govee/api/mqtt.py` — AWS IoT MQTT client
- `custom_components/govee/coordinator.py` — MQTT orchestration, state merging
- `custom_components/govee/models/state.py` — `update_from_mqtt()` state application
- `custom_components/govee/ble_passthrough.py` — BLE command publishing
- `docs/govee-protocol-reference.md` — Protocol documentation (PCAP-validated)
- `docs/_research/2026-03-30_govee-login-454-error.md` — Login fix research

### External
- [govee2mqtt](https://github.com/wez/govee2mqtt) — Key files: `src/undoc_api.rs`, `src/service/iot.rs`
- [homebridge-govee](https://github.com/homebridge-plugins/homebridge-govee) — Key files: `lib/connection/aws.js`, `lib/connection/http.js`
- [homebridge-ultimate-govee](https://github.com/constructorfleet/homebridge-ultimate-govee) — Key files: `lib/data/iot/iot.client.ts`
- [govee2mqtt #622](https://github.com/wez/govee2mqtt/issues/622) — "App version too low"
- [govee2mqtt #625](https://github.com/wez/govee2mqtt/pull/625) — Header fix PR
- [govee2mqtt #628](https://github.com/wez/govee2mqtt/issues/628) — "Service not enabled"
- [homebridge-govee #1247](https://github.com/homebridge-plugins/homebridge-govee/issues/1247) — Login fix
- [homebridge-govee AWS Control wiki](https://github.com/homebridge-plugins/homebridge-govee/wiki/AWS-Control)
- [Govee Developer API Reference (PDF)](https://govee-public.s3.amazonaws.com/developer-docs/GoveeDeveloperAPIReference.pdf)
- [Govee Developer Portal](https://developer.govee.com/)
- [AWS IoT Core endpoints](https://docs.aws.amazon.com/general/latest/gr/iot-core.html)
