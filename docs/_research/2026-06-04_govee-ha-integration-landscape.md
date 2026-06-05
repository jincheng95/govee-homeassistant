<!-- no-registry: device-coverage findings are advisory; no quantified files/tests/endpoints scope claim -->
# Govee ↔ Home Assistant Integration Landscape

**Type:** Feature Investigation (competitive + device-coverage)
**Date:** 2026-06-04
**Source:** Reddit r/homeassistant — [Govee and Home Assistant Integration](https://www.reddit.com/r/homeassistant/comments/1mpi2cw/govee_and_home_assistant_integration/) (u/TheImaginear, 2025-08-13)
**Subject repo:** `lasswellt/govee-homeassistant` (this project — Govee Cloud API v2.0 + AWS IoT MQTT)

---

## 1. Summary

OP could not reliably connect Govee lights to HA and found two official integrations — `govee_light_local` (LAN) and `govee_ble` (Bluetooth) — but hit a coverage gap on four SKUs: **H7172, H6099, H5083, H6004**. Research confirms those official integrations are structurally feature-poor: LAN covers <50% of models and exposes no scenes/segments; BLE is sensor/discovery-only with no light control. This integration (capability-driven cloud API + MQTT push) already covers **H6099, H6004, H5083** (the bulbs + plug) where LAN/BLE cannot, and is the correct answer to OP. Two concrete gaps remain in this codebase: **H5083 plug on/off uses wrong power values** (bug) and **H7172 ice-maker has no platform wiring** (unsupported device type). Recommendation: fix the H5083 power-value bug, decide whether to wire `ice_maker`, and position the README against the now-confirmed competitive landscape (govee2mqtt, goveelife, govee_light_local).

## 2. Research Questions

1. **What do `govee_light_local` and `govee_ble` actually support?**
   - `govee_light_local`: LAN UDP (`local_push`), on/off + brightness + color + color-temp only. No scenes, no RGBIC segments, no music/DreamView. 200+ SKUs but requires per-device "LAN Control" app toggle; multicast discovery (`239.255.255.250:4001`) breaks across VLANs / multi-NIC hosts.
   - `govee_ble`: sensors only (temp/humidity, motion, door). **No light control whatsoever.**

2. **Are the 4 SKUs supported anywhere, and by this repo?**
   - H6099 (RGBWW bulb): cloud-only (no LAN) → **this repo: SUPPORTED** (in README, full capability set).
   - H6004 (RGBWW bulb 800lm): cloud primary → **this repo: discovered if API returns it** (capability-driven).
   - H5083 (**smart plug**, not a sensor): cloud on/off → **this repo: routed to switch.py but has a power-value bug**.
   - H7172 (**ice maker**, appliance): cloud on/off at best → **this repo: NOT wired** (`devices.types.ice_maker` maps to no platform).

3. **Does Matter give FULL control of H6099?** No. HA Matter for Govee = on/off + brightness + color only (no scenes/segments/effects). Only a "handful" of newer Govee models expose Matter as of early 2026; H6099 Matter support not confirmed. Matter is not a full-feature path.

4. **How does this repo decide device support — SKU allowlist or capability-driven?** Capability-driven. `models/device.py:773-820` `from_api_response()` parses capabilities generically; `coordinator.py:485-528` adds all API devices with no allowlist filter. SKU strings appear only in the BLE passthrough allowlist and leak-sensor hub fallback.

5. **Competitive landscape?** govee2mqtt (most capable, most complex), goveelife (best non-lighting coverage), govee_light_local (built-in but feature-poor), LaggAt/hacs-govee (unmaintained, 2FA-broken).

## 3. Findings

### Official HA integrations are structurally limited
`govee_light_local` is LAN-only and exposes no scenes/segments/effects regardless of model; discovery is multicast-only and fails on VLAN/multi-NIC networks with no manual-IP fallback ([HA forum](https://community.home-assistant.io/t/having-problems-getting-the-govee-lights-local-integration-working/775190)). `govee_ble` is sensor/discovery-only — it does not control lights at all ([govee_ble docs](https://www.home-assistant.io/integrations/govee_ble/)). This explains OP's repeated setup failures: the two integrations they found cannot deliver full control of their bulbs.

### The 4 SKUs, resolved
| SKU | Type | LAN | BLE ctrl | Matter | This repo |
|---|---|---|---|---|---|
| **H6099** | RGBWW bulb (single-zone) | No | discovery only | unconfirmed | **Supported** — README, full caps |
| **H6004** | RGBWW bulb 800lm | unconfirmed | discovery only | No | **Discovered if API returns** (capability-driven) |
| **H5083** | Smart plug | No | No | No | **Partial** — switch.py, power-value bug |
| **H7172** | Ice maker (appliance) | No | No | No | **Unsupported** — `ice_maker` type unwired |

(H5083 was initially assumed to be a sensor — both web + codebase agents independently corrected this to **smart plug**.)

### This repo's H5083 bug (concrete)
`docs/govee-protocol-reference.md:926` documents plug power values **17/16**, but `PowerCommand.get_value()` (`models/commands.py:91-93`) sends **1/0**. On/off to H5083-class plugs may malfunction. Verify against a live plug before claiming support.

### This repo's H7172 gap (concrete)
`devices.types.ice_maker` is not in any `DEVICE_TYPE_*` constant and wires to no platform; already noted as a gap in `docs/_research/2026-04-08_pr-37-validation.md:77`. Supporting it needs a new device-type constant + platform (likely a `switch` or a custom appliance entity) — low value (single niche SKU) unless requested.

### Cross-cutting Govee API limitations (affect every cloud integration, including this one)
- Rate limits **100 req/min, 10,000 req/day** — real ceiling for 10+ device setups at short poll intervals ([LaggAt #129](https://github.com/LaggAt/hacs-govee/issues/129), [HA forum](https://community.home-assistant.io/t/govee-rate-limited-exceeded/646945)).
- Active scene + RGBIC segment colors **not reliably returned** in polls → optimistic state + RestoreEntity required (already this repo's documented approach, CLAUDE.md "API Limitations").
- Cloud latency 200–800ms; offline = uncontrollable.

### 2FA is the dominant new breakage vector
Govee added mandatory email 2FA for account login (since March 2026), documented in this repo's `CLAUDE.md` ("2FA Authentication Flow": login → status 454 → email code → retry). Account-login integrations (which need login for MQTT/IoT certs) can't re-auth interactively at startup, so the flow must live in a config/reconfigure step. This repo handles it (interactive config/reconfigure flow + pre-cached IoT creds in `hass.data[DOMAIN][KEY_IOT_CREDENTIALS]`, per CLAUDE.md) — a genuine differentiator vs the unmaintained LaggAt integration, which breaks on HA Core updates generally ([LaggAt #264](https://github.com/LaggAt/hacs-govee/issues/264) — HA Core 2025.11.3 compatibility failure; not 2FA-specific).

## 4. Compatibility Analysis

This repo is the strongest fit for OP's stated devices:
- **H6099 / H6004** — cloud + MQTT push is the *only* full-control path (LAN excluded, BLE sensor-only, Matter partial). Capability-driven discovery means no code change needed once the API returns them.
- **H5083** — covered in principle; needs the power-value fix to be reliable.
- **H7172** — out of scope without new platform wiring; low priority.

No dependency conflicts — this is the subject project itself. The integration's MQTT-push architecture also mitigates the rate-limit pain that hurts polling-only competitors (goveelife) by reducing reliance on frequent polls.

## 5. Recommendation

**For OP (community answer):** Use a cloud-API integration, not `govee_light_local`/`govee_ble`, for H6099/H6004/H5083 — those bulbs have no LAN API and BLE gives no light control. This repo (or goveelife/govee2mqtt) is the correct path. H7172 ice-maker is appliance-tier: expect on/off at best from any integration.

**For this project (actionable):**
| Action | Priority | Rationale |
|---|---|---|
| Fix `PowerCommand.get_value()` 17/16 vs 1/0 for plugs (H5083 class) | **High** | Confirmed bug; affects advertised plug support |
| Verify H6004 end-to-end against live API/device | Medium | Capability-driven path is untested for this exact SKU |
| README competitive positioning (vs govee2mqtt/goveelife/local) | Medium | 2FA handling + MQTT push + full caps are real differentiators |
| Decide on `ice_maker` (H7172) platform wiring | Low | Single niche SKU; wire only on request |

**Comparison matrix:**
| Integration | Arch | Scenes/Segments | Non-light devices | 2FA-safe | Setup complexity |
|---|---|---|---|---|---|
| **this repo** | cloud v2 + AWS IoT MQTT | Yes | plugs, fans, humidifier, sensors | Yes (interactive) | Medium |
| govee2mqtt | LAN-first + cloud + MQTT | Yes | wide | partial | High (needs MQTT broker) |
| goveelife | cloud OpenAPI v2 | Yes | best (heaters/fans/humidifiers) | n/a (API key) | Low |
| govee_light_local | LAN UDP | **No** | lights only | n/a | Low |
| govee_ble | BLE | No | sensors only | n/a | Low |
| LaggAt/hacs-govee | cloud v1 | partial | limited | **broken** | Low |

## 6. Implementation Sketch

**H5083 power-value fix** (highest-value action):
1. Confirm against `docs/govee-protocol-reference.md:926` (values 17/16 for plug power).
2. In `custom_components/govee/models/commands.py` `PowerCommand.get_value()` (~L91-93), branch plug/appliance device types to emit 16/17 instead of 0/1 — gate on the device-type the H5083 routes through (`DEVICE_TYPE_PLUG`/`is_plug`).
3. Add a unit test in `tests/test_models.py` asserting plug PowerCommand serializes to 17 (on) / 16 (off), regular light stays 1/0.
4. Verify on a live plug (or capture from Govee app traffic) — do not claim fixed on code inspection alone.

**Optional H7172 ice-maker wiring:**
1. Add `DEVICE_TYPE_ICE_MAKER` to `const.py` mapping `devices.types.ice_maker`.
2. Add detection in `models/device.py` and route to a `switch` (on/off) or new appliance entity.
3. Expose only capabilities the API actually returns; do not fabricate status fields the API doesn't provide.

**README positioning:** add a "Why this integration" table (the matrix in §5) emphasizing full-control of cloud-only bulbs, MQTT push (lower rate-limit pressure), and resilient 2FA handling.

## 7. Risks

- **H5083 fix is unverified until tested on hardware.** The 17/16 values come from a protocol-reference doc, not a live capture in this session. Treat as a strong hypothesis; a wrong value silently no-ops the plug. Mitigation: verify against live device or app-captured traffic before shipping, and keep the test asserting the exact serialized payload.
- **Capability-driven discovery assumes the Govee API returns these SKUs with standard capability types.** H6004 has not been exercised end-to-end; if Govee returns a non-standard capability shape, discovery could add a device with no working entities. Mitigation: log unrecognized capability types (CLAUDE.md debug-logging pattern) and test against a real account.
- **Rate limits remain a structural ceiling.** Even with MQTT push, large installs can exhaust 10,000 req/day if polling cadence is aggressive. Mitigation: keep poll interval user-configurable (already present) and prefer MQTT-driven updates over polling where possible.
- **Comments on the source Reddit thread were not captured** (loaded async, not in server HTML; Reddit + archive proxies blocked). Community-sentiment findings are sourced from adjacent HA-forum/GitHub threads, not the 15 comments on this specific post — directionally reliable but not a verbatim read of that thread's replies.

## 8. References

- Source thread: https://www.reddit.com/r/homeassistant/comments/1mpi2cw/govee_and_home_assistant_integration/
- govee_light_local docs: https://www.home-assistant.io/integrations/govee_light_local/
- govee_ble docs: https://www.home-assistant.io/integrations/govee_ble/
- Govee Developer API supported models: https://developer.govee.com/docs/support-product-model
- govee2mqtt: https://github.com/wez/govee2mqtt
- goveelife: https://github.com/disforw/goveelife
- LaggAt/hacs-govee (#264 — HA Core 2025.11.3 compatibility failure; integration unmaintained): https://github.com/LaggAt/hacs-govee/issues/264
- 2FA-flow fact grounded in repo `CLAUDE.md` §"2FA Authentication Flow" (primary source)
- Rate-limit reports: https://github.com/LaggAt/hacs-govee/issues/129 · https://community.home-assistant.io/t/govee-rate-limited-exceeded/646945
- LAN discovery problems: https://community.home-assistant.io/t/having-problems-getting-the-govee-lights-local-integration-working/775190
- Missing scenes via LAN: https://community.home-assistant.io/t/new-govee-local-integration-missing-scenes-effects/687698
- Internal: `custom_components/govee/models/device.py:773-820`, `coordinator.py:485-528`, `models/commands.py:91-93`, `docs/govee-protocol-reference.md:926`, `docs/_research/2026-04-08_pr-37-validation.md:77`
