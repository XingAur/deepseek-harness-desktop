#!/usr/bin/env bash
# Fast-forward the isolated official checkout, then refresh dependencies and build artifacts.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT/deepseek-harness"
REF="${DSH_OFFICIAL_REF:-master}"
FETCH_SOURCE="${DSH_OFFICIAL_FETCH_URL:-origin}"

if [[ ! -d "$DEST/.git" ]]; then
  echo "official checkout is not a Git clone; run: bash $ROOT/scripts/bootstrap.sh" >&2
  exit 1
fi

if pgrep -f "$DEST/apps/desktop" >/dev/null 2>&1; then
  echo "official desktop is running; quit Harness Dev before updating" >&2
  exit 1
fi

if [[ -n "$(git -C "$DEST" status --porcelain --untracked-files=no)" ]]; then
  echo "official checkout has tracked local changes; refusing to overwrite them" >&2
  git -C "$DEST" status --short
  exit 1
fi

current_branch="$(git -C "$DEST" branch --show-current)"
if [[ "$current_branch" != "$REF" ]]; then
  echo "official checkout is on '$current_branch', expected '$REF'" >&2
  exit 1
fi

before="$(git -C "$DEST" rev-parse HEAD)"
if [[ "$FETCH_SOURCE" == "origin" ]]; then
  git -C "$DEST" -c http.version=HTTP/1.1 fetch --prune origin "$REF"
else
  git -C "$DEST" -c http.version=HTTP/1.1 fetch "$FETCH_SOURCE" "$REF"
  git -C "$DEST" update-ref "refs/remotes/origin/$REF" FETCH_HEAD
fi
git -C "$DEST" merge --ff-only "origin/$REF"
after="$(git -C "$DEST" rev-parse HEAD)"

if [[ "$before" == "$after" && -d "$DEST/node_modules" && -f "$DEST/apps/web/dist/index.html" ]]; then
  echo "official checkout already current: $after"
  exit 0
fi

NODE_BIN="$(command -v node)"
export PATH="$(dirname "$NODE_BIN"):/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
cd "$DEST"
pnpm install --frozen-lockfile
pnpm run build
echo "official checkout updated: $before -> $after"
