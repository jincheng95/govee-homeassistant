#!/usr/bin/env bash
# One-time bootstrap of the orphan `badges` branch that holds the README's
# live status artifacts (install-count badge/graph + Govee API uptime
# badge/graph). After this, .github/workflows/{uptime,install-stats}.yml keep
# it updated automatically. Safe to re-run.
#
# Usage:  bash scripts/bootstrap_badges_branch.sh [remote]   # remote default: origin
set -euo pipefail

REMOTE="${1:-origin}"
ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

git fetch --tags --force "$REMOTE" >/dev/null 2>&1 || true

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

# Carry forward existing artifacts if the branch already exists.
if git rev-parse --verify "$REMOTE/badges" >/dev/null 2>&1; then
  git --work-tree="$STAGE" checkout "$REMOTE/badges" -- . || true
  git reset -q
fi

REMOTE_URL="$(git remote get-url "$REMOTE")"

python3 scripts/status_badges.py installs --data-dir "$STAGE" --repo-dir "$ROOT"
python3 scripts/status_badges.py uptime   --data-dir "$STAGE" --repo-dir "$ROOT"
GITHUB_TOKEN="${GITHUB_TOKEN:-$(gh auth token 2>/dev/null || true)}" \
  python3 scripts/status_badges.py stars --data-dir "$STAGE"

pushd "$STAGE" >/dev/null
git init -q -b badges
git add -A
git -c user.name='github-actions[bot]' \
    -c user.email='41898282+github-actions[bot]@users.noreply.github.com' \
    commit -q -m "chore(badges): bootstrap status artifacts [skip ci]"
git push -f "$REMOTE_URL" badges
popd >/dev/null

echo "✅ badges branch pushed. README live-status images will resolve within ~1 min (GitHub camo cache)."
