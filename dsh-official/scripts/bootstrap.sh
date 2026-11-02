#!/usr/bin/env bash
# Fetch official DeepSeek Harness beside this overlay. Does not touch the
# existing deepseek-harness submodule used by dsh-plugin-desktop 2.0.x.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT/deepseek-harness"
REF="${DSH_OFFICIAL_REF:-master}"
URL="${DSH_OFFICIAL_URL:-https://github.com/deepseek-ai/deepseek-harness.git}"

checkout_ok() {
  [[ -d "$DEST/.git" ]] || [[ -f "$DEST/package.json" ]] || return 1
  git -C "$DEST" rev-parse --verify HEAD >/dev/null 2>&1 || [[ -f "$DEST/apps/desktop/package.json" ]]
}

if checkout_ok && [[ -f "$DEST/apps/desktop/package.json" ]]; then
  echo "official checkout already present: $DEST"
  echo "update it with: bash $ROOT/scripts/update-official.sh"
  exit 0
fi

rm -rf "$DEST"
git -c http.version=HTTP/1.1 clone --depth 1 --branch "$REF" --single-branch "$URL" "$DEST"
echo "cloned $REF into $DEST"

if [[ ! -f "$DEST/apps/desktop/package.json" ]]; then
  echo "official desktop app missing after bootstrap" >&2
  exit 1
fi

echo "next: cd $DEST && corepack pnpm install && corepack pnpm --filter @deepseek-ai/dsh-desktop dev"
