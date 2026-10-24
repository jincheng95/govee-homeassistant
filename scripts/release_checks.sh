# Helpers sourced by .github/workflows/daily-release.yml (needs GH_TOKEN,
# GITHUB_REPOSITORY and REQUIRED_CHECKS in the environment).

# checks_state SHA -> prints success | pending | failure:<names>
checks_state() {
  local runs failed missing line n
  runs=$(gh api "repos/$GITHUB_REPOSITORY/commits/$1/check-runs?per_page=100" \
    --jq '.check_runs[] | "\(.name)\t\(.status)\t\(.conclusion)"')
  failed=""; missing=""
  IFS='|' read -ra names <<< "$REQUIRED_CHECKS"
  for n in "${names[@]}"; do
    line=$(printf '%s\n' "$runs" | awk -F'\t' -v n="$n" '$1==n' | head -1)
    if [ -z "$line" ] || [ "$(cut -f2 <<< "$line")" != "completed" ]; then missing="$missing $n;"
    elif [ "$(cut -f3 <<< "$line")" != "success" ]; then failed="$failed $n;"; fi
  done
  if [ -n "$failed" ]; then echo "failure:$failed"; elif [ -n "$missing" ]; then echo pending; else echo success; fi
}

# wait_checks SHA -> returns 0 when all required checks passed
wait_checks() {
  local s
  for _ in $(seq 1 50); do
    s=$(checks_state "$1")
    case "$s" in
      success) return 0 ;;
      failure:*) echo "::error::Required checks failed on $1:${s#failure:}"; return 1 ;;
    esac
    sleep 30
  done
  echo "::error::Timed out waiting for required checks on $1"; return 1
}
