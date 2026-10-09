#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
node --experimental-strip-types --test "$ROOT/overlay/scripts/check.ts"
node --check "$ROOT/scripts/prepare-portable-stage.mjs"
for script in "$ROOT"/scripts/*.sh; do bash -n "$script"; done
if grep -R -n -E '/Users/[^/]+/|nvm/versions|ln -sf' "$ROOT/scripts" --include='*.sh' --exclude='check.sh'; then
  echo "portable scripts contain a developer path or source-link operation" >&2
  exit 1
fi
if [[ -n "$(git -C "$ROOT/deepseek-harness" status --short)" ]]; then
  echo "official checkout is not clean" >&2
  exit 1
fi
echo "overlay and portable-build checks passed"
