# Brand assets

Local brand images for HACS / Home Assistant (HA 2026.3+ brand-proxy; no `home-assistant/brands` PR needed for custom integrations).

Drop the following files here. Local images take priority over the brands CDN automatically.

| File | Spec | Required |
|---|---|---|
| `icon.png` | square, 256×256 or 512×512, transparent background | **yes** (HACS default-store blocker) |
| `icon@2x.png` | hi-dpi square, 512×512 | optional |
| `logo.png` | horizontal wordmark, transparent | optional |
| `dark_icon.png` / `dark_logo.png` | dark-theme variants | optional |

Source: https://developers.home-assistant.io/blog/2026/02/24/brands-proxy-api/

> Use the Govee icon for nominative identification only. Keep the "unofficial / not affiliated with Govee" disclaimer in the root README.
