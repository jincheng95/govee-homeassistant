<!-- no-registry: discoverability/marketing strategy research; action items are repo/ops tasks, not code-scope items -->
# Research: SEO, Discoverability & Becoming the Best Govee × Home Assistant Integration

_Date: 2026-06-04 · Repo: `lasswellt/govee-homeassistant` · 57★ · HACS custom · domain `govee` · quality_scale silver_

## Summary

The single biggest lever is **HACS default-store inclusion** — most users never leave the HACS in-app search, and the repo is currently invisible there. The one hard blocker is a missing `custom_components/govee/brand/icon.png`; CI (HACS Action + Hassfest), releases, `hacs.json`, and `manifest.json` already satisfy every other default-store requirement. Discoverability (Google + community) is a secondary, parallel track: add ~9 more GitHub topics, submit to `frenck/awesome-home-assistant`, and post in the 228K-view legacy `LaggAt` forum thread as the maintained successor. Being "the best option" technically is largely already true for cloud multi-device control (AWS IoT MQTT push, 2FA, capability-based, broadest device-type coverage) — but the field leader `wez/govee2mqtt` (1,400★) wins on **LAN/offline control**, which this repo lacks. Closing the LAN gap + winning discoverability is the path to #1.

## Research Questions

1. **What makes this rank on Google for "govee home assistant"?** Repo name (already optimal), About description (already leads with "Govee Integration for Home Assistant"), topics (underfilled — 6 of 20), README first-160-chars + question-format headings, star count (CTR signal). GitHub's domain authority (~DA 95) does the heavy lifting; the repo page README is the indexed body copy.
2. **How do users actually discover HA integrations?** HACS in-app search (largest, default-store only) > Google (github.com + `community.home-assistant.io` threads) > Reddit r/homeassistant > YouTube > `awesome-ha.com`. The repo is absent from the #1 channel (not in default store).
3. **What backlinks matter?** Ranked: HACS default store (hacs.xyz) → `awesome-home-assistant` (DR~60, `awesome-ha.com`) → HA Community forum thread (DA~75) → Reddit/YouTube (nofollow but traffic+stars).
4. **Does a docs site / custom domain help?** Not near-term. README-on-github already inherits GitHub's DA. GitHub Pages subdomain is weaker unless attached to a custom domain (3-6 mo to mature). Defer until after default-store + backlinks.
5. **Who are the competitors and where does this lose?** `wez/govee2mqtt` (1,400★, LAN-first, community #1), `disforw/goveelife` (185★, cloud v2, direct competitor, 3× the stars), `LaggAt/hacs-govee` (353★, abandoned, still the HACS search result for "govee"), core `govee_light_local` / `govee_ble` (built-in, LAN/BLE, lights/sensors only). This repo loses on LAN control, star count, and community visibility — not on technical merit for cloud.

## Findings

### F1 — HACS default store is the highest-leverage action (and nearly unblocked)
Requirements per https://www.hacs.xyz/docs/publish/include/ and `/integration/`:
- [x] Public repo w/ description + topics + README
- [x] `hacs.json` with `name` (`render_readme: true` present)
- [x] `manifest.json` with `domain`, `documentation`, `issue_tracker`, `codeowners`, `name`, `version` — all present
- [x] ≥1 GitHub Release — `v2026.6.3` latest
- [x] HACS Action CI — present in `.github/workflows/hacs-hass.yaml`
- [x] Hassfest CI — present in same workflow
- [ ] **`brand/icon.png` in `custom_components/govee/` — MISSING (the one blocker)**
- Submission: PR to https://github.com/hacs/default adding repo URL alphabetically to `./integration`, from a personal fork branch, by repo owner.
- Timeline: **months** (volunteer backlog: https://github.com/hacs/default/pulls).
- Status today: neither `LaggAt/hacs-govee` nor this repo is in the default store.

### F2 — Brand image: ship locally, no brands-repo PR needed (CONTRADICTION resolved)
Two agents disagreed. Resolution: as of **HA 2026.3**, custom integrations ship brand images **locally** — no PR to `home-assistant/brands` required (source: https://developers.home-assistant.io/blog/2026/02/24/brands-proxy-api/). Place `icon.png` (+ optional `logo.png`, `dark_*`, `@2x` variants) in `custom_components/govee/brand/`. Local images take priority over the brands CDN. The older "submit to home-assistant/brands first" guidance applies only to HA **core** inclusion (`core_integrations/govee/`).

### F3 — On-page SEO: already strong, three cheap wins remain
- Repo name `govee-homeassistant` — optimal, slug-matches query.
- About description (97 chars) already leads with "Govee Integration for Home Assistant" — **already optimal**, no change needed (web-seo agent's flagged gap is a false positive).
- Topics: only 6 set (`govee, hacs, home-assistant, home-automation, led-lights, smart-home`). Add up to ~14 more: `govee-api, home-assistant-component, hacs-integration, custom-component, smart-lights, iot, cloud-api, mqtt, python, rgbic, led-control`. Topic pages (https://github.com/topics/govee) rank on Google and sort by stars.
- README: question-format H2s match search intent better than flat nouns — `## How to install Govee in Home Assistant`, `## HACS installation`, `## Supported Govee devices`. First 160 chars feed the Google snippet + AI Overview.

### F4 — Backlinks: 2 quick PRs + 1 forum post
- `frenck/awesome-home-assistant` — repo qualifies NOW (≥6 mo old, OSI license, README mentions Home Assistant, 57★ > 10 soft threshold). PR to Custom Integrations section. Contributing: https://github.com/frenck/awesome-home-assistant/blob/main/.github/CONTRIBUTING.md
- Legacy forum thread https://community.home-assistant.io/t/govee-integration/228516 (228K views) — post as the maintained successor to the abandoned `LaggAt` integration. No queue, highest short-term ROI for human discovery.
- Optionally start a dedicated thread in https://community.home-assistant.io/c/custom-integrations and link it from the README.

### F5 — Competitive position: best cloud option, loses on LAN
- **Wins vs. field:** AWS IoT MQTT push (real-time, not polling — beats `goveelife` and `LaggAt`), 2FA flow, capability-based (no SKU hardcoding), broadest device-type coverage (lights/plugs/fans/humidifiers/heaters/thermometers/leak sensors), RGBIC segments, no extra infra (unlike `govee2mqtt`'s MQTT broker), clean architecture, active maintenance.
- **Loses on:** LAN/offline control (the #1 community-valued trait — `govee2mqtt` and core `govee_light_local` have it), star count (57 vs 1,400), zero community-recommendation presence, not built into core (core integrations need no HACS install).
- **Nuance:** Many Govee SKUs (humidifiers, fans, heaters, sensors) have **no LAN API** at all → cloud-only is the only option for them, narrowing the LAN gap for multi-device-type users. LAN matters mainly for the lights subset (~200 models, UDP 4001-4003).

### F6 — Dissent / contradictory evidence
- Brand-image requirement (F2): web-seo agent said brands-repo PR required; hacs-ecosystem agent said local `brand/` dir since HA 2026.3. Resolved in favor of the latter (dated, cited source).
- `goveelife` default-store status: web-seo implied it's "~HACS default"; hacs-ecosystem confirmed neither `LaggAt` nor this repo is in default and did not verify `goveelife`. Treat `goveelife`'s default-store status as **unconfirmed**.
- `govee2mqtt` star count: web-seo cited ~224★ (likely confused with `govee-lan-hass`); competitors agent cited ~1,400★ for `govee2mqtt`. Use **1,400** (the dedicated competitor agent is authoritative here).

## Compatibility Analysis

All recommendations are repo-config / ops actions — no code-architecture impact except the optional LAN feature.
- **HACS default + brand:** Adding `custom_components/govee/brand/icon.png` is additive; HACS spec + HA 2026.3 brand-proxy both support it. No manifest change needed (already has all 6 required keys).
- **Topics / description / README:** Pure metadata + docs; zero code risk.
- **LAN support (strategic, larger):** Would integrate at the API-layer (new `api/lan.py` UDP client alongside `api/client.py`/`api/mqtt.py`), surfaced through the existing coordinator transport-selection logic. Govee LAN API: UDP multicast `239.255.255.250:4001`, device listens `4003`, status `4002`. Fits the existing multi-transport pattern (cloud + MQTT already coexist). Scoped to ~200 light models; cloud remains fallback for non-LAN SKUs. This is a roadmap-sized epic, not a quick win.

## Recommendation

Run two parallel tracks.

**Track A — Discoverability (do this week, hours of effort):**
| # | Action | Effort | Impact |
|---|---|---|---|
| A1 | Add `custom_components/govee/brand/icon.png` (+ `logo.png`) | 30 min | Unblocks A2 |
| A2 | Submit PR to `hacs/default` `./integration` | 30 min | **Highest** — in-app discovery for ~1M+ HACS users |
| A3 | Expand topics 6 → ~15 | 5 min | GitHub search + topics-page Google ranking |
| A4 | PR to `frenck/awesome-home-assistant` | 20 min | High-DR backlink + curated traffic |
| A5 | Post in `LaggAt` forum thread (228K views) as maintained successor | 30 min | Human discovery, no queue |
| A6 | README: question-format H2s + tighten first 160 chars | 30 min | Google snippet / AI Overview |

**Track B — Be the best option (roadmap):**
| # | Action | Effort | Impact |
|---|---|---|---|
| B1 | Optional LAN API transport (lights) | Epic | Neutralizes `govee2mqtt`'s main edge |
| B2 | Pursue HA core inclusion (push quality_scale → gold) | Large | Built-in = zero HACS friction, ranks above all |
| B3 | Market device-breadth (fans/heaters/humidifiers/leak) — competitors don't surface this | Low | Differentiates from `goveelife`/`govee_light_local` |
| B4 | One YouTube setup guide + DEV.to article | Hours | Long-tail backlinks, non-dev reach |

Do not build a docs site / custom domain yet (defer until A1-A6 land).

## Implementation Sketch

### A1 — brand image
```
custom_components/govee/brand/
  icon.png        # 256x256 or 512x512, square, transparent
  icon@2x.png     # optional hi-dpi
  logo.png        # optional, horizontal
```
Source the Govee logo/icon (respect trademark — use for identification). Local images auto-prioritized over brands CDN per HA 2026.3.

### A2 — HACS default PR
```bash
# fork hacs/default, then on a new branch:
# edit ./integration — add alphabetically:
#   lasswellt/govee-homeassistant
gh pr create --repo hacs/default --title "Add lasswellt/govee-homeassistant" \
  --body "Govee Cloud integration; HACS Action + Hassfest passing; release v2026.6.3"
```
Pre-flight: confirm latest `hacs-hass.yaml` run is green on `master`.

### A3 — topics
```bash
gh repo edit lasswellt/govee-homeassistant \
  --add-topic govee-api --add-topic hacs-integration \
  --add-topic home-assistant-component --add-topic custom-component \
  --add-topic smart-lights --add-topic iot --add-topic cloud-api \
  --add-topic mqtt --add-topic python --add-topic rgbic --add-topic led-control
```

### A6 — README headings (`README.md`)
Rename for search intent: `## How to install Govee in Home Assistant`, `## HACS installation`, `## Supported Govee devices`. Keep the existing rich content beneath.

## Risks

- **HACS default review is slow (months) and can reject "override of a core integration."** Today there is no `govee` (cloud) domain in HA core, so this is safe. Risk materializes only if HA later merges a cloud `govee` to core — at which point this repo would be barred from the default store and could remain only as a manual custom repo. Mitigation: pursue HA-core inclusion (B2) proactively so this repo *becomes* that core integration rather than being displaced by it.
- **Domain shadowing with legacy `LaggAt`.** Both use domain `govee`; if a user installs this after `LaggAt`, last-installed wins and `custom_components` shadows core. Mitigation: README must instruct users to fully remove `LaggAt/hacs-govee` before/while migrating. Install-count analytics aggregate correctly by domain.
- **AWS IoT MQTT is undocumented/unsupported by Govee** (same risk `govee2mqtt` discloses) — Govee can break push silently. The existing polling fallback contains the blast radius; LAN support (B1) would add a second non-cloud resilience path.
- **Trademark.** Using the Govee name/logo for an unofficial integration is identification/nominative use; keep "unofficial / not affiliated with Govee" language in the README to avoid takedown risk.
- **Star count is a slow-moving signal.** Discoverability actions raise traffic, but topic-page position and GitHub-search rank are stars-weighted, so ranking improves with a lag after installs grow. Set expectations: weeks-to-months, not immediate.

## Open Questions

- Is `disforw/goveelife` in the HACS default store? Unconfirmed — affects how contested the "default-store Govee cloud integration" slot is. Verify before/after submitting A2.
- Which exact Govee SKUs in this repo's supported set expose the LAN API? Determines B1's real device coverage and whether LAN is worth the epic.
- Would the HA core team accept a cloud `govee` domain given `govee_ble` + `govee_light_local` already exist? Worth an early, low-cost architecture-discussion issue on the HA forum before investing in B2.

## References

- HACS publish/include: https://www.hacs.xyz/docs/publish/include/
- HACS publish/integration: https://www.hacs.xyz/docs/publish/integration/
- HACS default repo: https://github.com/hacs/default
- HA brands-proxy API (local brand images, 2026.3): https://developers.home-assistant.io/blog/2026/02/24/brands-proxy-api/
- HA integration quality scale: https://developers.home-assistant.io/docs/core/integration-quality-scale/
- awesome-home-assistant: https://github.com/frenck/awesome-home-assistant
- Legacy Govee forum thread (228K views): https://community.home-assistant.io/t/govee-integration/228516
- HA custom-integrations forum category: https://community.home-assistant.io/c/custom-integrations
- Competitor — wez/govee2mqtt (1,400★, LAN-first): https://github.com/wez/govee2mqtt
- Competitor — disforw/goveelife (185★, cloud v2): https://github.com/disforw/goveelife
- Competitor — LaggAt/hacs-govee (353★, abandoned): https://github.com/LaggAt/hacs-govee
- Core — govee_light_local: https://www.home-assistant.io/integrations/govee_light_local/
- Core — govee_ble: https://www.home-assistant.io/integrations/govee_ble/
- GitHub topics (govee): https://github.com/topics/govee
- GitHub SEO: https://www.markepear.dev/blog/github-search-engine-optimization
