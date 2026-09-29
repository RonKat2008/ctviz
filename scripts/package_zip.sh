#!/usr/bin/env bash
# Build dist/ctviz.zip: `git archive HEAD` + the deliverables that may still be untracked
# (README, DESIGN, examples/, evals/report.md, web/), minus internal/secret/cache files, then
# refuse to ship anything containing an OpenAI/OpenRouter-shaped key.
#
#   scripts/package_zip.sh [OUT_DIR]        (default: <repo>/dist; `make zip`)
#   CTVIZ_REPO_ROOT=/path/to/repo           (override the repo, used by tests/test_packaging.py)
#
# It never reads .env files: they are excluded by name before anything is scanned.
set -euo pipefail

ROOT="${CTVIZ_REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
OUT_DIR="${1:-$ROOT/dist}"
ZIP_NAME="ctviz.zip"
mkdir -p "$OUT_DIR"
ZIP="$(cd "$OUT_DIR" && pwd)/$ZIP_NAME"
rm -f "$ZIP"

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

git -C "$ROOT" archive --format=tar HEAD | tar -x -C "$STAGE"

# Deliverables that may not be committed yet: the working-tree copy wins over HEAD.
for path in README.md docs/DESIGN.md examples evals/report.md web; do
  src="$ROOT/$path"
  [ -e "$src" ] || continue
  mkdir -p "$STAGE/$(dirname "$path")"
  rm -rf "${STAGE:?}/$path"
  cp -R "$src" "$STAGE/$path"
done

# Never ship: the internal plan (+ PDFs), plan history, agent scratch, secrets, caches, envs.
rm -rf "$STAGE/docs/plans" "$STAGE/docs/superpowers" "$STAGE/.superpowers" "$STAGE/.claude"
rm -f "$STAGE"/docs/PLAN*.md "$STAGE"/docs/PLAN*.pdf
find "$STAGE" \( -name '.env' -o -name '.env.*' \) ! -name '.env.example' -delete
find "$STAGE" \( -name '__pycache__' -o -name '.pytest_cache' -o -name '.mypy_cache' \
  -o -name '.ruff_cache' -o -name '.venv' -o -name 'node_modules' -o -name '.DS_Store' \
  -o -name '.coverage' -o -name 'htmlcov' -o -name 'dist' \) -prune -exec rm -rf {} +
find "$STAGE" -name '*.zip' -delete

(cd "$STAGE" && zip -qr "$ZIP" .)

if unzip -p "$ZIP" | grep -aE 'sk-(proj|or-v1)-[A-Za-z0-9_-]{20,}' >/dev/null; then
  echo "secret detected in archive — aborting" >&2
  rm -f "$ZIP"
  exit 1
fi

echo "wrote $ZIP ($(unzip -l "$ZIP" | tail -1 | awk '{print $2}') files)"
