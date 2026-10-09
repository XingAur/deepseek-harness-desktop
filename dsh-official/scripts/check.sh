#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
node --experimental-strip-types --test "$ROOT/overlay/scripts/check.ts"
for script in "$ROOT"/scripts/*.mjs; do node --check "$script"; done
for script in "$ROOT"/scripts/*.sh; do bash -n "$script"; done
if command -v pwsh >/dev/null 2>&1; then
  pwsh -NoProfile -Command '$errors=$null; [System.Management.Automation.Language.Parser]::ParseFile($args[0], [ref]$null, [ref]$errors) > $null; if ($errors.Count) { $errors | Out-String | Write-Error; exit 1 }' "$ROOT/scripts/build-portable-windows-x64.ps1"
fi
if grep -R -n -E '/Users/[^/]+/|nvm/versions|ln -sf' "$ROOT/scripts" --include='*.sh' --include='*.ps1' --exclude='check.sh'; then
  echo "portable scripts contain a developer path or source-link operation" >&2
  exit 1
fi
LOCKED_SHA="$(node -p "require('$ROOT/official-lock.json').commit")"
LOCKED_VERSION="$(node -p "require('$ROOT/official-lock.json').version")"
if [[ "$(git -C "$ROOT/deepseek-harness" rev-parse HEAD)" != "$LOCKED_SHA" ]] || [[ "$(node -p "require('$ROOT/deepseek-harness/package.json').version")" != "$LOCKED_VERSION" ]]; then
  echo "official checkout does not match official-lock.json" >&2
  exit 1
fi
if [[ -n "$(git -C "$ROOT/deepseek-harness" status --short)" ]]; then
  echo "official checkout is not clean" >&2
  exit 1
fi
echo "overlay and portable-build checks passed"
