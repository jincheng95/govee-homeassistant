<!-- no-registry: informational research; no quantified work-scope (files/components/routes/etc). "9213 installs" is an external metric, not implementation scope. -->
# Research: Install-Count Reporting for HACS + Home Assistant Analytics

**Date:** 2026-06-04
**Type:** Feature Investigation
**Topic:** How to obtain and surface a real install count for the `govee` custom integration
**Status:** Verified (key endpoints fetched live during research)

## Summary

Home Assistant **already tracks and publishes** install counts for this integration. The public endpoint `https://analytics.home-assistant.io/custom_integrations.json` lists `govee` at **`total: 9213`** with a 26-version breakdown (this fork's `2026.6.1: 1376`, etc.). No code, no telemetry, no registration is required — counts come from HA users who opt into **Usage**-level analytics, which reports custom-integration domains + versions. HACS itself has **no** per-repo download counter, and a GitHub `releases/downloads` shields badge would read ~0 (HACS clones the repo; releases carry no zip assets). **Recommendation:** add a shields.io dynamic-JSON badge querying `$.govee.total` from the HA analytics endpoint — the only honest, zero-maintenance live install metric available. Caveat: domain `govee` is shared with the deprecated `LaggAt/hacs-govee` fork, so `total` is the combined domain install base, not this fork alone.

## Research Questions

1. **Does HACS track/report install counts for custom (non-default) repos?**
   No. No HACS author dashboard, no public per-repo download API. HACS installs via git clone of the tagged commit, not release-asset download. Confirmed across library-docs + web-researcher; zero GitHub issues requesting it.

2. **Does Home Assistant analytics count custom integrations, and is `govee` in it?**
   **Yes.** `https://analytics.home-assistant.io/custom_integrations.json` returns a flat `{domain: {total, versions:{ver:count}}}` map. `govee` present: `total: 9213`. Verified live 2026-06-04.

3. **What does a custom integration need to be counted?**
   Nothing on the author side. HA instances on **Usage** analytics level report "the names and versions of all your custom integrations." Domain appears once installed on opted-in instances. No `home-assistant/brands` PR and no HACS default-store membership required for the *count* (those affect logo/name on the rendered analytics page and discoverability, not inclusion in the JSON).

4. **Can the count be surfaced as a README badge?**
   Yes — shields.io dynamic JSON badge against the analytics endpoint (`query=$.govee.total`). No GitHub-downloads badge (assets empty → always 0).

5. **Can the integration read its own install count at runtime?**
   Not meaningfully / not advisable. The number is an external aggregate; surfacing it is a README/docs concern, not integration code. Confirmed no telemetry code exists in `custom_components/govee/` (correct — HA analytics is entirely core-side).

## Findings

### F1 — HA publishes custom-integration installs (primary source)
- Endpoint: `https://analytics.home-assistant.io/custom_integrations.json` (live, 200, ~500+ domains).
- Shape:
  ```json
  "govee": { "total": 9213, "versions": { "2025.1.1": 5269, "2026.6.1": 1376, "0.3.4-TheBigActualFix": 327, "...": 0 } }
  ```
- Source: live WebFetch, 2026-06-04.

### F2 — Inclusion mechanism = Usage analytics level
- HA analytics levels: **basic** (install id/version/type/country) → **usage** (integration + custom-integration names/versions) → **statistics** (counts only) → **diagnostics** (Sentry).
- Custom integrations reported at **Usage** level: "The names and versions of all your custom integrations, if you have any."
- Source: https://www.home-assistant.io/integrations/analytics/ (verified live).
- Implication: `total` counts only opt-in Usage-level instances → true install base is **higher** than 9213 (HA analytics opt-in is a minority of installs; commonly cited <25%, exact rate unverified).

### F3 — HACS has no native install counter
- No author-facing download stats for custom repos; no public HACS analytics API for per-repo installs.
- Sources: hacs.xyz docs (library-docs), community threads (web-researcher). The `analytics.hacs.xyz/data/integration.json` endpoint guessed by codebase-analyst was **not** verified and is not the HA dataset — do not rely on it.

### F4 — GitHub downloads badge would mislead
- Latest releases (`v2026.6.3` … `v2026.5.15`) have `"assets": []` — no zip attached.
- `img.shields.io/github/downloads/lasswellt/govee-homeassistant/total` → 0, because HACS clones source rather than downloading assets. Structurally dishonest as an install metric. Avoid.

### F5 — Domain-collision caveat on the 9213 figure
- Version map mixes lineages: CalVer `2025.1.1`/`2026.6.1` (this fork) alongside `0.3.4-TheBigActualFix` (legacy `LaggAt/hacs-govee`, deprecated, same `domain: govee`).
- `total: 9213` = **combined** install base for all integrations claiming `domain govee`. This fork's share is isolable by summing CalVer versions in `.versions`, but the headline `total` cannot be cleanly attributed to this fork alone.

### F6 — Current repo state (readiness)
- `manifest.json`: complete — `domain govee`, `version 2026.6.3`, `quality_scale silver`, `config_flow`, `codeowners`, docs/issue_tracker all present.
- `hacs.json`: valid custom-repo config (`name`, `homeassistant: 2024.11.0`, `render_readme`). Not in default store (badge reads `HACS-Custom`).
- README badges: only static `HACS-Custom` + `quality_scale-silver`. No install badge.
- CI: `hacs-hass.yaml` runs `hacs/action` + `hassfest` on every push (manifest already valid for both).
- No telemetry code in integration (correct).
- Source: codebase-analyst, manifest.json:1-27, hacs.json, README.md:5-6, .github/workflows/.

## Dissent / Contradictory Evidence

- **library-docs + web-researcher** concluded "custom integrations are invisible to HA analytics / endpoint 404s." **This is wrong** — disproven by live fetch (F1). Both agents under-searched and asserted a negative. Treated as refuted, not consensus.
- **codebase-analyst** correctly inferred tracking exists but cited the wrong endpoint (`analytics.hacs.xyz`). Directionally right, citation unverified — superseded by F1.
- Lesson: the single load-bearing fact was resolved by orchestrator direct verification, not agent consensus.

## Compatibility Analysis

- Zero integration-code impact. README/docs-only change.
- shields.io dynamic-JSON badge fetches the endpoint server-side → no CORS concern. Endpoint is unauthenticated public JSON.
- HA analytics dataset refreshes periodically (~weekly cadence; exact schedule unverified) → badge value lags real-time, acceptable for an install metric.
- `manifest.json` already passes hassfest; no field changes needed for the count to keep flowing.

## Recommendation

**Add a HA-analytics install badge to README.md now** (cheapest, honest, already-live data). Optionally pursue brands + default-store for discoverability later.

| Option | Effort | Honesty | Action |
|---|---|---|---|
| **HA analytics badge** (recommended) | trivial | High — real opt-in installs | Add shields dynamic-JSON badge, `$.govee.total` |
| GitHub downloads badge | trivial | **Bad** — reads 0 | Do **not** use |
| HACS install count | n/a | — | Does not exist |
| GitHub stars badge | trivial | Medium — interest, not installs | Optional complement |
| `home-assistant/brands` PR | low (external PR) | — | Optional: adds name/logo on analytics page |
| HACS default-store PR (`hacs/default`) | medium (quality bar) | — | Optional: discoverability + cleaner badge |

## Implementation Sketch

1. **README badge** — add to badge block (README.md:5-6). Recommended label "active installs" (clarifies it's opt-in instances, not total):
   ```markdown
   [![Active installs](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fanalytics.home-assistant.io%2Fcustom_integrations.json&query=%24.govee.total&label=active%20installs&color=41BDF5)](https://analytics.home-assistant.io/)
   ```
   Renders current `$.govee.total` (9213 at research time).

2. **Optional — footnote the domain-collision caveat** (F5) under the badge or in a "Stats" section, so the number isn't misread as fork-exclusive.

3. **Optional — `home-assistant/brands` PR**: add `custom_integrations/govee/` (icon.png, logo.png) so the analytics.home-assistant.io rendered page shows name/logo. Does not change the count.

4. **Optional — HACS default store** (`hacs/default` PR): requires meeting HACS quality requirements; yields default-store discoverability and lets the README badge switch `HACS-Custom` → `HACS-Default`.

5. **Do not** add release zip assets or a `github/downloads` badge — wrong metric for HACS distribution (F4).

## Risks

- **Attribution risk (medium):** `total: 9213` includes the deprecated `LaggAt/hacs-govee` legacy installs sharing `domain govee`. Presenting it as this fork's count overstates it. Mitigation: label as "active installs (domain `govee`)" and/or footnote; or compute fork-only sum from CalVer entries in `.versions` if a precise figure is ever needed.
- **Undercount (low/expected):** counts only Usage-level opt-in instances; true deployment base is larger. This is inherent to all HA analytics and is the honest, accepted convention — not a defect.
- **Badge staleness (low):** dataset updates on HA's cadence, not live. Acceptable.
- **Endpoint stability (low):** relies on HA continuing to publish `custom_integrations.json` at this path. It is the same dataset powering analytics.home-assistant.io; low churn risk, but a third-party dependency outside the maintainer's control.

## Open Questions

- Exact HA analytics opt-in rate (used to extrapolate true install base) — commonly cited <25%, not authoritatively verified this session.
- Exact refresh cadence of `custom_integrations.json`.
- Precise fork-vs-legacy split of the 9213 — derivable from `.versions` CalVer entries but not computed here.

## References

- https://analytics.home-assistant.io/custom_integrations.json — live install dataset; `govee.total: 9213` (verified 2026-06-04)
- https://www.home-assistant.io/integrations/analytics/ — analytics levels; custom integrations reported at Usage level (verified)
- https://analytics.home-assistant.io/ — rendered analytics site
- https://shields.io/badges/dynamic-json-badge — dynamic JSON badge syntax
- `custom_components/govee/manifest.json:1-27` — manifest (complete, hassfest-valid)
- `hacs.json` — custom-repo config
- `README.md:5-6` — existing static badges
- `.github/workflows/hacs-hass.yaml` — HACS + hassfest CI
