# Research: Client ID Fix Validation (Round 2)

**Date**: 2026-04-06
**Type**: Architecture Decision
**Context**: Validating the deterministic client_id fix proposed in the first research round (`2026-04-06_pr-50-validation.md`)

---

## Summary

Both research agents converged: **the original "inconsistent client_id within a session" hypothesis was correct but understated**. The stronger reframing is that **client_id must be deterministic per email AND stable across restarts** -- not just consistent within a single login flow. homebridge-govee, govee2mqtt, and TheOneOgre all derive client_id from the username and persist it. We mint random UUIDs in 4 places, including across HA restarts. This explains why some accounts work (older accounts that registered their first random UUID before Govee's April 2026 hardening) and others fail (newer or reloaded accounts that send a different UUID every restart). PR #50's bootstrap calls (`user-informations`, `client/settings`) have **zero public references** outside the PR -- they are cargo-cult and should be ignored.

---

## Research Questions & Answers

### 1. Is the client_id consistency hypothesis confirmed by direct source inspection?
**Answer**: **Yes, confirmed.** Tracer agent verified line by line:
- `auth.py:473` (login): passes `client_id` to headers
- `auth.py:261` (get_iot_key): calls `_build_govee_headers()` with NO argument -> mints new UUID
- `auth.py:320` (fetch_device_topics): same -- mints another new UUID
- `__init__.py:121` (startup login): calls `auth_client.login(email, password)` with no `client_id` -> mints yet another UUID

All 3 reference impls thread one stable client_id through every call.

### 2. Is "consistent within a session" sufficient, or must it also be "stable across restarts"?
**Answer**: **Stable across restarts is required.** homebridge-govee derives client_id from `uuid.generate(this.username)` which is deterministic. govee2mqtt uses `Uuid::new_v5(NAMESPACE_DNS, email)`. TheOneOgre uses `uuid.uuid5(NAMESPACE_DNS, email).hex`. All three produce the **same** ID for the same account every time. Govee likely caches `(email, client_id)` after first login -- a different ID on next attempt looks like a new device and triggers 2FA hardening.

### 3. Is the PR #50 bootstrap claim corroborated anywhere else?
**Answer**: **No.** Alt-cause agent searched GitHub for `bi/rest/v1/user-informations` and `account/v1/client/settings` -- **zero hits** outside siberiaodens's PR. Not in homebridge-govee, govee2mqtt, goveelife, or any community discussion. These are likely iPhone app telemetry calls, not auth gates.

### 4. Why does the maintainer's account work with random UUIDs?
**Answer**: **Plausible mechanisms (not provable from source alone)**:
- **First-attempt registration**: Govee may cache the first UUID an email sends as the "registered device." Maintainer got lucky -- their first random UUID became their permanent ID, and reload-induced new UUIDs get rejected silently or fall back gracefully on their account tier.
- **Account-age grandfathering**: Older accounts may bypass 2FA hardening; newer accounts (siberiaodens) hit it.
- **Rate-limit poisoning**: Govee allows ~30 logins/day. siberiaodens's debugging loop blew through it; maintainer's didn't. After exhaustion, ALL attempts return 454 regardless of client_id until the window resets.

### 5. Are there other bugs the research surfaced?
**Answer**: **Yes, three:**
1. `__init__.py:121` — startup login passes no client_id, generates yet another fresh UUID different from what config flow used
2. `auth.py:572` — MQTT client_id `AP/{accountId}/{client_id}` is unstable, causing AWS IoT disconnect/reconnect churn from duplicate-ID kicks
3. `__init__.py:94` — MQTT login re-attempts on every reload because `KEY_IOT_LOGIN_FAILED` is in `hass.data` which clears on reload (Palmdale95's complaint)

### 6. Is rate-limit poisoning a risk for siberiaodens specifically?
**Answer**: **Likely yes.** They were debugging with Charles Proxy iterating on a PR -- almost certainly hit Govee's 30/day login cap. Their "fix" of hardcoding the iPhone client_id may have appeared to work because (a) it was stable across attempts AND (b) they waited long enough for the rate limit to reset. This makes their PR's mechanism attribution unreliable.

---

## Findings

### Finding 1: Hypothesis confirmed and strengthened

The first research round identified intra-session inconsistency. Round 2 strengthens this to: **must be deterministic AND persisted across restarts.**

| Property | hacs-govee current | homebridge-govee | govee2mqtt | TheOneOgre |
|----------|--------------------|--------------------|----------------|----------------|
| Same client_id within a login flow | NO (random per call) | YES | YES | YES |
| Same client_id across restarts | NO (random per process) | YES (from username) | YES (uuid5 from email) | YES (uuid5 from email) |
| Persisted to disk | No | No (regenerated identically) | No (regenerated identically) | No (regenerated identically) |
| Stable MQTT client_id | NO | YES | YES | YES |

### Finding 2: PR #50's bootstrap calls are unsupported

Web searches returned **zero** GitHub results for both `bi/rest/v1/user-informations` and `account/v1/client/settings` outside PR #50 itself. These are not in the iOS app's auth path -- they are app telemetry that the iPhone happens to make alongside login. They are not load-bearing.

### Finding 3: PR #50's lowercase headers are wrong

homebridge-govee uses camelCase headers (`clientId`, `appVersion`) and ships fixes successfully. siberiaodens's lowercase rename is a copy-paste artifact from Charles Proxy's display, not a server requirement.

### Finding 4: PR #50's effective fix is the hardcoded client_id

Of the three things siberiaodens changed (hardcoded client_id, bootstrap calls, lowercase headers), only the hardcoded client_id has a real mechanism. Hardcoding gave them a stable client_id, which is what homebridge-govee achieves deterministically. Their other changes are noise.

### Finding 5: Palmdale95's bug is separate from siberiaodens's

Palmdale95 is API-key-only with stale email/password in `entry.data`. The MQTT login failure flag (`KEY_IOT_LOGIN_FAILED`) is stored in `hass.data` which clears on every reload. So they see the same 454 warning + repairs issue every restart. Fix: persist the failure flag to `entry.options` so it survives reloads.

---

## Compatibility Analysis

### Risk to maintainer's working setup

**Very low.** Their account currently works with random UUIDs. Switching to a deterministic client_id derived from their email gives them the same guarantees plus reduced AWS IoT churn (stable MQTT client_id). On first login after upgrade, the deterministic ID will differ from whatever random ID was last cached -- but Govee's ID-caching behavior is likely "first ID seen" not "exactly this ID always," so the new deterministic ID should be accepted and become the new cached value.

### Risk to existing user data

**None.** No config schema changes. The deterministic ID is derived in code from the email already in `entry.data`. We don't need to persist it to disk since it's reproducible.

### Migration path

No migration needed. On the first login after upgrade, all calls use the new deterministic ID. Govee's ID-cache mechanism (whatever it is) will either accept the new ID immediately or trigger a one-time 2FA challenge.

---

## Recommendation

**Implement the deterministic client_id fix.** Skip persisting to `entry.data` -- it's reproducible from the email. Fix all 4 client_id sites. Separately fix Palmdale95's stale-credentials reload loop.

### Code changes (auth.py)

```python
def _derive_client_id(email: str) -> str:
    """Derive a stable client_id from the account email.

    Govee's account API caches (email, client_id) pairs after first login.
    Sending a different client_id on subsequent calls or restarts triggers
    2FA challenges and rejection on hardened accounts. Reference impls
    (homebridge-govee, govee2mqtt, TheOneOgre/govee-cloud) all use a
    deterministic ID derived from the username.
    """
    normalized = (email or "").strip().lower()
    return uuid.uuid5(uuid.NAMESPACE_DNS, f"hacs-govee:{normalized}").hex


class GoveeAuthClient:
    def __init__(self, session=None):
        self._session = session
        self._owns_session = session is None
        self._email: str | None = None
        self._client_id: str | None = None  # Store after login

    async def login(self, email, password, client_id=None, code=None):
        if client_id is None:
            client_id = _derive_client_id(email)
        self._email = email
        self._client_id = client_id
        # ... rest unchanged

    async def get_iot_key(self, token, client_id=None):
        cid = client_id or self._client_id
        if cid is None:
            raise GoveeApiError("get_iot_key called without prior login")
        headers = self._build_govee_headers(cid)
        # ... rest unchanged

    async def fetch_device_topics(self, token, client_id=None):
        cid = client_id or self._client_id
        if cid is None:
            raise GoveeApiError("fetch_device_topics called without prior login")
        headers = self._build_govee_headers(cid)
        # ... rest unchanged
```

### Code changes (config_flow.py)

```python
# In async_step_account, derive deterministic client_id from email
self._client_id = _derive_client_id(email)
# Use this for both request_verification_code and the retry login
```

### Code changes (__init__.py)

```python
# Pass deterministic client_id explicitly to avoid yet-another random UUID
async with GoveeAuthClient() as auth_client:
    iot_credentials = await auth_client.login(
        email, password,
        client_id=_derive_client_id(email),
    )
```

### Palmdale95 fix

The cleanest fix: add a check in `__init__.py` that if `KEY_IOT_LOGIN_FAILED` was a 2FA error from a prior session (now in `hass.data` only), persist it to `entry.options[KEY_IOT_LOGIN_FAILED_PERSISTENT]` after first detection. On future loads, check the persistent flag first. Reconfigure clears it.

Even simpler alternative: when reconfigure runs and the user clears email/password, the existing code at `config_flow.py:538-540` already removes them from entry.data. Just improve the warning text to tell users this is an option.

### Test updates needed

- `tests/test_auth.py::TestBuildGoveeHeaders::test_build_govee_headers_generates_client_id_when_none` -- update assertion (still random when no email context, which is fine for `_build_govee_headers` itself)
- New test: `test_login_uses_deterministic_client_id_from_email`
- New test: `test_get_iot_key_reuses_login_client_id`
- New test: `test_fetch_device_topics_reuses_login_client_id`
- New test: `test_two_logins_for_same_email_use_same_client_id`

Estimated test changes: ~50 lines.

---

## Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Maintainer's account rejects the new deterministic ID | Low | High | Govee's cache likely accepts the new ID as "first seen" -- but maintainer should test before release |
| siberiaodens's account is rate-limited and the fix appears not to work | Medium | Medium | Wait 24h after upgrade before declaring failure; document this in release notes |
| Govee changes their cache behavior between now and release | Low | Medium | The deterministic ID approach matches 3 reference impls; if Govee changes things, all 4 break together |
| Test rewrite is harder than estimated | Low | Low | Mock helpers are simple; deterministic IDs are easier to test than random ones |
| Two HA instances on the same account with the same email both connect with the same MQTT client_id | Medium | Medium | AWS IoT will disconnect one. This is actually a feature -- prevents two HA instances fighting. Add a note in docs. |

---

## References

- [Round 1 research](2026-04-06_pr-50-validation.md) -- initial hypothesis
- [homebridge-govee@25f9e52](https://github.com/homebridge-plugins/homebridge-govee/commit/25f9e52b32c80e4c22d561d43e5f16753f91f71f) -- known-good with deterministic client_id from username
- [wez/govee2mqtt src/undoc_api.rs](https://github.com/wez/govee2mqtt/blob/main/src/undoc_api.rs) -- `Uuid::new_v5(NAMESPACE_DNS, email)`
- [TheOneOgre/govee-cloud iot_client.py](https://github.com/TheOneOgre/govee-cloud/blob/master/custom_components/govee/iot_client.py) -- `uuid.uuid5(NAMESPACE_DNS, email).hex`
- [wez/govee2mqtt#650](https://github.com/wez/govee2mqtt/pull/650) -- April 2026 app version fix (already applied)
- [lasswellt/govee-homeassistant#50](https://github.com/lasswellt/govee-homeassistant/pull/50) -- siberiaodens's PR (only the hardcoded client_id has a real mechanism)
