#!/usr/bin/env python3
"""Generate self-updating status artifacts for the README.

Two modes:

  installs  Fetch analytics.home-assistant.io custom-integration counts, keep
            ONLY the versions this repo actually released (so other `govee`
            forks + the legacy LaggAt installs are excluded), append a daily
            history point, and render a shields endpoint badge + trend SVG.

  uptime    Ping the Govee API hosts this integration talks to, append a
            history point, and render a status badge + uptime-bars SVG.

Pure standard library — no third-party deps, so it runs on a bare CI runner.
Artifacts are written to --data-dir (default: docs/badges) and are intended to
live on an orphan `badges` branch to keep master history clean.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ANALYTICS_URL = "https://analytics.home-assistant.io/custom_integrations.json"
DOMAIN = "govee"

# Hosts this integration actually uses (see custom_components/govee/api/).
UPTIME_TARGETS = [
    ("open", "https://openapi.api.govee.com/router/api/v1"),  # control REST API
    ("app", "https://app2.govee.com/app/v1/account/iot/key"),  # account / MQTT auth
]

# GitHub-dark palette — self-contained card looks native on light AND dark.
BG = "#0d1117"
CARD = "#161b22"
BORDER = "#30363d"
TEXT = "#e6edf3"
MUTED = "#8b949e"
ACCENT = "#41BDF5"  # HA / Govee blue
GREEN = "#3fb950"
AMBER = "#d29922"
RED = "#f85149"

UA = "govee-homeassistant-status/1.0 (+https://github.com/lasswellt/govee-homeassistant)"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _now() -> datetime:
    return datetime.now(timezone.utc)


def stamp() -> str:
    """UTC last-updated timestamp shown in graph footers."""
    return f"updated {_now():%Y-%m-%d %H:%M} UTC"


def human(n: int) -> str:
    if n >= 10000:
        return f"{n / 1000:.0f}k"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def fetch_json(url: str, timeout: int = 30) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def load_history(path: Path) -> list:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (ValueError, OSError):
            return []
    return []


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def shields_endpoint(label: str, message: str, color: str) -> dict:
    return {
        "schemaVersion": 1,
        "label": label,
        "message": message,
        "color": color,
        "labelColor": "#21262d",
        "style": "flat-square",
    }


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# --------------------------------------------------------------------------- #
# version matching (fork-only attribution)
# --------------------------------------------------------------------------- #
def normalize_version(v: str) -> str | None:
    """Canonicalize a tag/version to `YYYY.M.P`, dropping v/dev prefixes.

    `v2026.01.05` -> `2026.1.5`, `2026.1.5` -> `2026.1.5`. Returns None for
    dev / pre-release tags we never shipped to users.
    """
    v = v.strip().lstrip("v")
    if v.startswith("dev") or "-" in v:
        return None
    parts = v.split(".")
    if len(parts) < 2 or not all(p.isdigit() for p in parts):
        return None
    return ".".join(str(int(p)) for p in parts)


def fork_versions(repo_dir: Path) -> set[str]:
    """Normalized set of every version this repo released (git tags)."""
    out = subprocess.run(
        ["git", "-C", str(repo_dir), "tag"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    versions = {nv for t in out.split() if (nv := normalize_version(t))}
    # Always include the current manifest version (latest release may be untagged locally).
    manifest = repo_dir / "custom_components" / DOMAIN / "manifest.json"
    if manifest.exists():
        cur = json.loads(manifest.read_text()).get("version")
        if cur and (nv := normalize_version(cur)):
            versions.add(nv)
    return versions


# --------------------------------------------------------------------------- #
# SVG primitives
# --------------------------------------------------------------------------- #
def card_open(w: int, h: int) -> list[str]:
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}" font-family="-apple-system,Segoe UI,Helvetica,Arial,sans-serif">',
        f'<rect x="0.5" y="0.5" width="{w - 1}" height="{h - 1}" rx="12" '
        f'fill="{CARD}" stroke="{BORDER}"/>',
    ]


def txt(x, y, s, size, color, *, weight=400, anchor="start", spacing=None) -> str:
    extra = f' letter-spacing="{spacing}"' if spacing else ""
    return (
        f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" '
        f'font-weight="{weight}" text-anchor="{anchor}"{extra}>{esc(s)}</text>'
    )


# --------------------------------------------------------------------------- #
# installs mode
# --------------------------------------------------------------------------- #
def render_installs_svg(history: list, fork_total: int, official_total: int) -> str:
    w, h = 480, 150
    s = card_open(w, h)
    pad = 20
    s.append(txt(pad, 34, "ACTIVE INSTALLS", 11, MUTED, weight=600, spacing="1.5"))
    s.append(txt(pad, 78, human(fork_total), 40, ACCENT, weight=700))

    # 7-day delta
    delta_str, delta_col = "", MUTED
    if len(history) >= 2:
        prev = next((p["count"] for p in reversed(history[:-1])), None)
        # find a point ~7 entries back if available
        ref = history[-8]["count"] if len(history) >= 8 else history[0]["count"]
        d = fork_total - ref
        if d > 0:
            delta_str, delta_col = f"▲ +{human(d)} / 7d", GREEN
        elif d < 0:
            delta_str, delta_col = f"▼ {human(d)} / 7d", RED
        else:
            delta_str = "— 0 / 7d"
    if delta_str:
        s.append(txt(pad + 4, 100, delta_str, 12, delta_col, weight=600))

    # sparkline (area) along the right ~60% of the card
    counts = [p["count"] for p in history][-40:]
    gx0, gy0, gw, gh = 190, 28, 270, 92
    if len(counts) >= 2:
        lo, hi = min(counts), max(counts)
        rng = (hi - lo) or 1
        pts = []
        for i, c in enumerate(counts):
            px = gx0 + gw * i / (len(counts) - 1)
            py = gy0 + gh - (gh - 8) * (c - lo) / rng
            pts.append((px, py))
        line = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        area = f"M{gx0},{gy0 + gh} " + " ".join(f"L{x:.1f},{y:.1f}" for x, y in pts) + f" L{pts[-1][0]:.1f},{gy0 + gh} Z"
        s.append(
            f'<defs><linearGradient id="ig" x1="0" x2="0" y1="0" y2="1">'
            f'<stop offset="0" stop-color="{ACCENT}" stop-opacity="0.45"/>'
            f'<stop offset="1" stop-color="{ACCENT}" stop-opacity="0"/></linearGradient></defs>'
        )
        s.append(f'<path d="{area}" fill="url(#ig)"/>')
        s.append(f'<polyline points="{line}" fill="none" stroke="{ACCENT}" stroke-width="2" stroke-linejoin="round"/>')
        s.append(f'<circle cx="{pts[-1][0]:.1f}" cy="{pts[-1][1]:.1f}" r="3.5" fill="{ACCENT}"/>')
    else:
        s.append(txt(gx0 + gw / 2, gy0 + gh / 2, "collecting history…", 12, MUTED, anchor="middle"))

    s.append(txt(pad, h - 14, f"HA analytics · of {human(official_total)} domain total", 10.5, MUTED))
    s.append(txt(w - pad, h - 14, stamp(), 10.5, MUTED, anchor="end"))
    s.append("</svg>")
    return "\n".join(s)


def _vkey(v: str) -> tuple:
    try:
        return tuple(int(x) for x in v.split("."))
    except ValueError:
        return ()


def render_versions_svg(fork_counts: dict[str, int], fork_total: int) -> str:
    # newest release first, oldest rolled up
    items = sorted(fork_counts.items(), key=lambda kv: _vkey(kv[0]), reverse=True)
    top = items[:8]
    rest = items[8:]
    rows = list(top)
    rest_sum = sum(c for _, c in rest)
    if rest_sum:
        rows.append((f"+{len(rest)} older", rest_sum))

    latest = items[0][0] if items else ""

    w = 480
    row_h = 23
    top_pad = 66
    h = top_pad + len(rows) * row_h + 24
    s = card_open(w, h)
    pad = 20
    s.append(txt(pad, 32, "VERSION BREAKDOWN", 11, MUTED, weight=600, spacing="1.5"))
    s.append(txt(pad, 50, "active installs by release", 11.5, MUTED))
    s.append(txt(w - pad, 42, human(fork_total), 24, ACCENT, weight=700, anchor="end"))

    label_w = 78
    bar_x = pad + label_w
    bar_max = w - pad - bar_x - 84  # reserve right column for count + %
    maxc = max((c for _, c in rows), default=1)
    for i, (ver, c) in enumerate(rows):
        y = top_pad + i * row_h
        cy = y + row_h / 2 + 4
        is_latest = ver == latest
        col = GREEN if is_latest else ACCENT
        s.append(txt(bar_x - 8, cy, ver, 11.5, TEXT if is_latest else MUTED,
                     weight=700 if is_latest else 400, anchor="end"))
        bw = max(bar_max * c / maxc, 2)
        s.append(f'<rect x="{bar_x}" y="{y + 4}" width="{bw:.1f}" height="{row_h - 11}" rx="3" '
                 f'fill="{col}" fill-opacity="{0.95 if is_latest else 0.65}"/>')
        pct = c / fork_total * 100 if fork_total else 0
        s.append(txt(w - pad, cy, f"{human(c)} · {pct:.0f}%", 10.5, MUTED, anchor="end"))

    foot = f"newest {latest} · {len(fork_counts)} releases in use" if fork_total else "collecting…"
    s.append(txt(pad, h - 11, foot, 10.5, MUTED))
    s.append(txt(w - pad, h - 11, stamp(), 10.5, MUTED, anchor="end"))
    s.append("</svg>")
    return "\n".join(s)


def run_installs(data_dir: Path, repo_dir: Path) -> None:
    fv = fork_versions(repo_dir)
    blob = fetch_json(ANALYTICS_URL)
    entry = blob.get(DOMAIN, {})
    versions = entry.get("versions", {})
    official_total = int(entry.get("total", 0))
    fork_counts: dict[str, int] = {}
    for ver, c in versions.items():
        nv = normalize_version(ver)
        if nv and nv in fv:
            fork_counts[nv] = fork_counts.get(nv, 0) + int(c)
    fork_total = sum(fork_counts.values())

    today = f"{_now():%Y-%m-%d}"
    hist_path = data_dir / "installs-history.json"
    history = load_history(hist_path)
    if history and history[-1].get("date") == today:
        history[-1] = {"date": today, "count": fork_total, "official": official_total}
    else:
        history.append({"date": today, "count": fork_total, "official": official_total})
    history = history[-400:]
    write_json(hist_path, history)

    write_json(data_dir / "installs.json", shields_endpoint("active installs", human(fork_total), "41BDF5"))
    (data_dir / "installs-trend.svg").write_text(render_installs_svg(history, fork_total, official_total))
    (data_dir / "versions.svg").write_text(render_versions_svg(fork_counts, fork_total))
    print(f"[installs] fork={fork_total} (of {official_total} domain total) "
          f"versions_matched={len(fork_counts)}")


# --------------------------------------------------------------------------- #
# uptime mode
# --------------------------------------------------------------------------- #
def probe(url: str, timeout: int = 12) -> tuple[bool, int | None]:
    """Return (reachable, latency_ms). Any HTTP status < 500 means the server
    is up (401/403/404 are fine — we only care that Govee is responding)."""
    start = _now()
    req = urllib.request.Request(url, method="GET", headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = resp.status
    except urllib.error.HTTPError as e:
        code = e.code
    except Exception:
        return False, None
    ms = int((_now() - start).total_seconds() * 1000)
    return code < 500, ms


def _day_status(entries: list) -> str:
    """Worst status across a day's entries: up / degraded / down / none."""
    if not entries:
        return "none"
    oks = [e["ok"] for e in entries]
    if all(oks):
        return "up"
    if any(oks):
        return "degraded"
    return "down"


def render_uptime_svg(history: list) -> str:
    w, h = 480, 150
    s = card_open(w, h)
    pad = 20

    # group last 45 days
    by_day: dict[str, list] = {}
    for e in history:
        by_day.setdefault(e["ts"][:10], []).append(e)
    days = sorted(by_day)[-45:]

    recent_ok = history[-1]["ok"] if history else None
    if recent_ok is True:
        status, scol = "OPERATIONAL", GREEN
    elif recent_ok is False:
        status, scol = "DOWN", RED
    else:
        status, scol = "UNKNOWN", MUTED
    # degraded if up now but a down in last 6 checks
    if recent_ok and any(not e["ok"] for e in history[-6:]):
        status, scol = "DEGRADED", AMBER

    s.append(txt(pad, 34, "GOVEE API", 11, MUTED, weight=600, spacing="1.5"))
    # status pill (width sized to dot + label so text never overflows the border)
    pill_w = 32 + len(status) * 7.2
    s.append(f'<rect x="{pad}" y="44" width="{pill_w:.0f}" height="22" rx="11" fill="{scol}" fill-opacity="0.16" stroke="{scol}" stroke-opacity="0.5"/>')
    s.append(f'<circle cx="{pad + 13}" cy="55" r="4" fill="{scol}"/>')
    s.append(txt(pad + 23, 59, status, 12, scol, weight=700))

    # uptime % over window
    total = sum(len(by_day[d]) for d in days)
    up = sum(1 for d in days for e in by_day[d] if e["ok"])
    pct = (up / total * 100) if total else 0.0
    s.append(txt(w - pad, 40, f"{pct:.1f}%", 26, TEXT, weight=700, anchor="end"))
    s.append(txt(w - pad, 58, f"uptime / {len(days)}d", 10.5, MUTED, anchor="end"))

    # daily bars
    bx0, by0, bw, bh = pad, 84, w - 2 * pad, 34
    n = max(len(days), 1)
    gap = 3
    bar_w = max((bw - gap * (n - 1)) / n, 2)
    cmap = {"up": GREEN, "degraded": AMBER, "down": RED, "none": "#21262d"}
    for i, d in enumerate(days):
        st = _day_status(by_day[d])
        x = bx0 + i * (bar_w + gap)
        s.append(f'<rect x="{x:.1f}" y="{by0}" width="{bar_w:.1f}" height="{bh}" rx="2" fill="{cmap[st]}"/>')
    if not days:
        s.append(txt(w / 2, by0 + bh / 2 + 4, "collecting history…", 12, MUTED, anchor="middle"))

    s.append(txt(pad, h - 12, "openapi + app2 · hourly", 10.5, MUTED))
    s.append(txt(w - pad, h - 12, stamp(), 10.5, MUTED, anchor="end"))
    s.append("</svg>")
    return "\n".join(s)


def run_uptime(data_dir: Path) -> None:
    results = {name: probe(url) for name, url in UPTIME_TARGETS}
    ok = all(r[0] for r in results.values())
    entry = {
        "ts": _now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ok": ok,
        "open_ms": results["open"][1],
        "app_ms": results["app"][1],
    }
    hist_path = data_dir / "api-history.json"
    history = load_history(hist_path)
    history.append(entry)
    history = history[-2200:]  # ~90 days hourly
    write_json(hist_path, history)

    if ok:
        msg, col = "operational", "3fb950"
    elif any(r[0] for r in results.values()):
        msg, col = "degraded", "d29922"
    else:
        msg, col = "down", "f85149"
    write_json(data_dir / "api-status.json", shields_endpoint("Govee API", msg, col))
    (data_dir / "api-uptime.svg").write_text(render_uptime_svg(history))
    print(f"[uptime] ok={ok} open={results['open']} app={results['app']}")


# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["installs", "uptime"])
    ap.add_argument("--data-dir", default="docs/badges", type=Path)
    ap.add_argument("--repo-dir", default=".", type=Path)
    args = ap.parse_args()
    args.data_dir.mkdir(parents=True, exist_ok=True)
    if args.mode == "installs":
        run_installs(args.data_dir, args.repo_dir)
    else:
        run_uptime(args.data_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
