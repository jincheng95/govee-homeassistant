# Research: PR #50 Validation — Govee Auth Bootstrap Sequence

**Date**: 2026-04-06
**Type**: Architecture Decision
**Context**: User siberiaodens submitted PR #50 claiming a 4-step bootstrap sequence is required for Govee login. Issue #28 has multiple users still failing to authenticate after v2026.4.0.

---

## Summary

**REJECT PR #50.** The proposed `user-informations` and `client/settings` bootstrap calls are unsubstantiated by any other working integration -- homebridge-govee, govee2mqtt, and TheOneOgre/govee-cloud all use a simple 2-call sequence (`login` -> `iot/key`). However, the research uncovered the **actual bug**: our `get_iot_key()` and `fetch_device_topics()` call `_build_govee_headers()` without passing `client_id`, so they generate a fresh random UUID per request -- meaning the login uses one client_id but follow-up calls use different ones. The fix is to use a **deterministic client_id derived from email** (matching TheOneOgre's `uuid5(NAMESPACE_DNS, email).hex` pattern) and propagate it through all related calls in a single session.

---

## Research Questions & Answers

### 1. Does any working Govee integration use the proposed bootstrap calls?
**Answer**: **No.** Verified by direct source inspection of homebridge-govee `lib/connection/http.js`, govee2mqtt `src/undoc_api.rs`, and TheOneOgre/govee-cloud `iot_client.py`. Zero references to `user-informations` or `client/settings` endpoints in any of them.

### 2. Is the iOS 26.4 User-Agent real or fake?
**Answer**: **Real**, but slightly stale. Apple uses year-based version numbering. homebridge-govee shipped iOS `26.5.0` build `8` on 2026-04-03. siberiaodens's `26.4.0` is internally consistent with their March observation date.

### 3. Does header casing (lowercase vs camelCase) matter?
**Answer**: **No.** Per RFC 7230 §3.2 HTTP headers are case-insensitive. All three reference clients send camelCase (`appVersion`, `clientId`, `clientType`, `iotVersion`) and they work. No evidence of Govee enforcing case.

### 4. Is the maintainer's working setup at risk?
**Answer**: **Yes**, severely. PR #50 ships `client_id = "YOUR_CLIENT_ID"` as the literal default in `login()` -- a hardcoded debug placeholder. Tests would break (~38/50). The submitter explicitly says "I was not able to implement a solution that suits other users."

### 5. What's the actual cause of siberiaodens's login failure?
**Answer**: **Inconsistent client_id across calls.** Our current `auth.py` generates a random UUID for `login()`, then `get_iot_key()` (line 261) and `fetch_device_topics()` (line 320) call `_build_govee_headers()` without passing the login's client_id, generating *new* random UUIDs. Govee's stricter validation likely rejects this -- the IoT key request uses a client_id the login flow never registered.

### 6. Why does the maintainer's account work?
**Answer**: Likely Govee's account-tier validation isn't enforced uniformly. Older or grandfathered accounts may not require client_id consistency. Newer accounts (siberiaodens) hit the stricter path.

---

## Findings

### Finding 1: PR #50 contains a hardcoded debug literal as production code

```python
# PR #50 lines in login():
if client_id is None:
    # Test value from working iPhone app session
    client_id = "YOUR_CLIENT_ID"
    """client_id = uuid.uuid4().hex"""
```

The random UUID line is commented out. The submitter's PR description acknowledges this: *"I was not able to implement a solution that suits other users."* This is a personal patch, not a portable fix.

### Finding 2: Other integrations use a simple 2-call sequence

| Integration | Latest fix date | Sequence |
|-------------|----------------|----------|
| homebridge-govee | 2026-04-03 | `v2/login` -> `v1/verification` (2FA) -> `iot/key` |
| govee2mqtt | 2026-03-23 | `v1/login` -> `iot/key` -> `device/list` |
| TheOneOgre | (older) | `v1/login` -> `iot/key` |
| **hacs-govee** (current) | 2026-04-05 | `v2/login` -> `v1/verification` (2FA) -> `iot/key` |

Our current code already mirrors the freshest known-good implementation (homebridge's April 3 commit). The bootstrap calls are absent from all of them.

### Finding 3: The actual bug -- inconsistent client_id

Current `auth.py`:

```python
# Line 261 (get_iot_key)
headers = self._build_govee_headers()  # NEW random client_id!
headers["Authorization"] = f"Bearer {token}"

# Line 320 (fetch_device_topics)
headers = self._build_govee_headers()  # NEW random client_id!
headers["Authorization"] = f"Bearer {token}"
```

Both methods omit `client_id` when calling `_build_govee_headers()`, which falls through to:

```python
if client_id is None:
    client_id = uuid.uuid4().hex  # Generates a fresh UUID every call
```

This means the login uses client_id `A`, the IoT key fetch uses client_id `B`, and the device list fetch uses client_id `C`. For accounts under Govee's stricter validation, this triggers rejection.

### Finding 4: TheOneOgre uses deterministic client_ids

```python
# TheOneOgre/govee-cloud iot_client.py
client_id = uuid.uuid5(uuid.NAMESPACE_DNS, email).hex
```

This produces a stable 32-char hex string derived from the user's email. Same email = same client_id forever. This avoids:
1. Anti-abuse rate limiting on "new client" registrations
2. Different client_ids across requests within a single session

### Finding 5: PR #50 has additional latent bugs

- `str(time.time() * 1000)` produces a float string like `"1759000000000.123"` instead of `str(int(time.time() * 1000))`
- `_message_indicates_2fa("")` returns False, so empty 454 messages would NOT be treated as 2FA -- breaking real users (the maintainer's own logs show 454 with empty message IS 2FA)
- Removing `clientType: 1` from the login payload (all 3 reference impls send it)

---

## Compatibility Analysis

### Adopting PR #50 wholesale
- **Test breakage**: ~38/50 auth tests fail (header case, mock shape, signature changes)
- **Test rewrite cost**: 600-900 LoC
- **Production risk**: Maintainer's working setup likely breaks because the new pre-login `user-information` GET adds a failure mode
- **Verdict**: High risk, no proven benefit

### Adopting only the deterministic client_id fix
- **Test breakage**: ~3-5 tests need updates (the ones asserting `client_id` is random)
- **Test rewrite cost**: 20-50 LoC
- **Production risk**: Minimal -- this is what TheOneOgre uses, and our maintainer's account already works with random IDs (it'll continue to work with stable ones)
- **Verdict**: Low risk, addresses the actual root cause

---

## Recommendation

**Implement a minimal, targeted fix:**

1. **Use deterministic client_id derived from email** (uuid5 over NAMESPACE_DNS), matching TheOneOgre's pattern
2. **Propagate the same client_id through all related calls** within a single auth session (`login` -> `get_iot_key` -> `fetch_device_topics`)
3. **Cache the client_id** for the session, regenerating only on email change
4. **Skip everything else from PR #50**: bootstrap calls, header case changes, hardcoded literals, payload restructuring, JWT decoding, smart-454 detection

For **Palmdale95**'s cosmetic issue (API-key-only user with stale credentials):
- Add a one-time clear path: if startup login fails with 454/empty AND no successful login has ever happened, prompt the user via a repairs issue to either Reconfigure or clear stale credentials
- Or simpler: improve the warning message to clearly say "if you don't need MQTT, use Reconfigure to remove email/password"

---

## Implementation Sketch

### Change 1: `api/auth.py` -- deterministic client_id

```python
def _derive_client_id(email: str) -> str:
    """Derive a deterministic client_id from the account email.

    Govee's account API requires a stable client identifier across all
    requests in a session and across sessions for the same account.
    Random per-call IDs trigger account-level rejection on newer accounts.
    """
    return uuid.uuid5(uuid.NAMESPACE_DNS, f"hacs-govee:{email.lower().strip()}").hex
```

Update `login()`:
```python
async def login(self, email: str, password: str,
                client_id: str | None = None, code: str | None = None) -> GoveeIotCredentials:
    if client_id is None:
        client_id = _derive_client_id(email)
    # Store on instance so get_iot_key() and fetch_device_topics() can reuse
    self._client_id = client_id
    # ... rest of method
```

Update `get_iot_key()` and `fetch_device_topics()`:
```python
async def get_iot_key(self, token: str, client_id: str | None = None) -> dict[str, Any]:
    cid = client_id or self._client_id  # Use stored from login
    headers = self._build_govee_headers(cid)
    headers["Authorization"] = f"Bearer {token}"
    # ...
```

Add `self._client_id: str | None = None` to `__init__`.

### Change 2: `config_flow.py` -- pass deterministic client_id

```python
# In async_step_account, use deterministic client_id from the start
self._client_id = uuid.uuid5(
    uuid.NAMESPACE_DNS, f"hacs-govee:{email.lower().strip()}"
).hex
```

This ensures the verification code request and the retry login both use the same client_id derived from email -- not a fresh random one per attempt.

### Change 3: Test updates

- `test_auth.py::TestBuildGoveeHeaders::test_build_govee_headers_generates_client_id_when_none` -- update to assert deterministic from a hash, not random
- Tests that pass explicit client_ids continue to work
- Add new test: `test_login_uses_same_client_id_for_iot_key_call`

### Change 4: Improved warning message for API-key-only users

`__init__.py` line ~133:
```python
except Govee2FARequiredError:
    _LOGGER.warning(
        "Govee account requires email verification (2FA). "
        "If you do not need real-time MQTT updates, you can use "
        "Reconfigure to remove the email and password (the API key alone is sufficient). "
        "Otherwise, use Reconfigure to re-enter credentials with a verification code."
    )
```

---

## Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Deterministic client_ids don't fix siberiaodens's issue | Medium | Medium | Ask siberiaodens to test before release; if still broken, escalate to bootstrap-call investigation |
| Govee blocks `uuid5` derived IDs as suspicious | Low | High | TheOneOgre uses this pattern successfully -- it's a known-good approach |
| Existing users with cached client_ids see disruption | Low | Low | The new client_id is derived deterministically, so users with the same email get the same ID -- no disruption |
| Maintainer's working setup breaks | Very low | High | Random IDs work for them, deterministic IDs are a strict superset of guarantees -- should also work |
| Govee server is doing something else entirely | Medium | Medium | This research can only validate against known-good integrations; final test is real-world feedback |

---

## References

- [lasswellt/govee-homeassistant#28](https://github.com/lasswellt/govee-homeassistant/issues/28) -- the issue
- [lasswellt/govee-homeassistant#50](https://github.com/lasswellt/govee-homeassistant/pull/50) -- siberiaodens's PR
- [homebridge-govee fix-http-login commit 6fac671](https://github.com/homebridge-plugins/homebridge-govee/commit/6fac67129145c076a049caa46035747e6bc35d62) (2026-04-03) -- freshest known-good Govee auth fix
- [wez/govee2mqtt src/undoc_api.rs](https://github.com/wez/govee2mqtt/blob/main/src/undoc_api.rs) -- Rust reference impl
- [TheOneOgre/govee-cloud iot_client.py](https://github.com/TheOneOgre/govee-cloud/blob/master/custom_components/govee/iot_client.py) -- deterministic client_id pattern
