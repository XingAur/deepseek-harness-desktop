#!/usr/bin/env bash
# Build a local ad-hoc/unsigned portable macOS arm64 app from a disposable
# official source stage. The official checkout is never modified.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OFFICIAL="$ROOT/deepseek-harness"
OVERLAY="$ROOT/overlay"
LOCK="$ROOT/official-lock.json"
STAGE="$ROOT/.portable-stage/macos-arm64"
DIST="$ROOT/dist"
NODE_BIN="${NODE_BIN:-$(command -v node)}"
PNPM="${PNPM:-corepack pnpm}"

if [[ -n "$(git -C "$OFFICIAL" status --short)" ]]; then
  echo "portable build: official checkout must be clean" >&2
  exit 1
fi
OFFICIAL_SHA="$(git -C "$OFFICIAL" rev-parse HEAD)"
OFFICIAL_VERSION="$($NODE_BIN -p "require('$OFFICIAL/package.json').version")"
LOCKED_SHA="$($NODE_BIN -p "require('$LOCK').commit")"
LOCKED_VERSION="$($NODE_BIN -p "require('$LOCK').version")"
if [[ "$OFFICIAL_SHA" != "$LOCKED_SHA" || "$OFFICIAL_VERSION" != "$LOCKED_VERSION" ]]; then
  echo "portable build: official checkout does not match official-lock.json" >&2
  exit 1
fi
BUILD_VERSION="${DSH_PORTABLE_BUILD_VERSION:-${OFFICIAL_VERSION}.$(TZ=Asia/Shanghai date +%Y%m%d).1}"

export PATH="$(dirname "$NODE_BIN"):/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export ELECTRON_MIRROR="${ELECTRON_MIRROR:-https://npmmirror.com/mirrors/electron/}"
export DSH_GITHUB_ASSET_MIRROR="${DSH_GITHUB_ASSET_MIRROR-}"
(cd "$OVERLAY" && npm ci && npm run bundle:host && npm run bundle:client && npm run check)

rm -rf "$STAGE"
mkdir -p "$STAGE" "$DIST"
git -C "$OFFICIAL" archive HEAD | tar -x -C "$STAGE"
"$NODE_BIN" "$ROOT/scripts/prepare-portable-stage.mjs" "$STAGE" "$OVERLAY"

(cd "$STAGE" && $PNPM install --no-frozen-lockfile)
(cd "$STAGE/packages/external/overlay-custom" && $PNPM run bundle:host && $PNPM run bundle:client)
packaged=false
for attempt in 1 2 3; do
  mkdir -p "$STAGE/packages/external/overlay-custom/lib/types"
  printf "%s\n" "export * from '../../src/index.ts'" >"$STAGE/packages/external/overlay-custom/lib/types/index.js"
  if (cd "$STAGE" && $PNPM --dir apps/desktop run package -- mac-arm64 --unsigned --dir --build-version "$BUILD_VERSION"); then
    packaged=true
    break
  fi
  echo "portable build: package attempt $attempt failed" >&2
done
if [[ "$packaged" != true ]]; then exit 1; fi

APP="$STAGE/apps/desktop/.desktop-build/targets/mac-arm64/unsigned-artifacts/mac-arm64/DeepSeek Harness.app"
if [[ ! -d "$APP" ]]; then
  echo "portable build: expected app missing: $APP" >&2
  exit 1
fi
"$NODE_BIN" "$ROOT/scripts/verify-portable-paths.mjs" "$APP/Contents/Resources" "$ROOT"
/usr/bin/codesign --force --deep --sign - --preserve-metadata=entitlements,requirements,flags "$APP"
/usr/bin/codesign --verify --deep --strict "$APP"

ZIP="$DIST/deepseek-harness-overlay-${BUILD_VERSION}-mac-arm64-adhoc-manual.zip"
rm -f "$ZIP" "$ZIP.sha256" "$ZIP.build.json"
/usr/bin/ditto -c -k --sequesterRsrc --keepParent "$APP" "$ZIP"
METADATA="$($NODE_BIN "$ROOT/scripts/write-portable-metadata.mjs" "$ZIP" "$STAGE/packages/external/overlay-custom" "$OFFICIAL_SHA" "$OFFICIAL_VERSION" "$BUILD_VERSION" darwin arm64 ad-hoc)"
SHA256="$(printf '%s\n' "$METADATA" | tail -n 1)"

if [[ -n "$(git -C "$OFFICIAL" status --short)" ]] || [[ "$(git -C "$OFFICIAL" rev-parse HEAD)" != "$OFFICIAL_SHA" ]]; then
  echo "portable build: official checkout changed during staging" >&2
  exit 1
fi
printf 'portable app: %s\nmanual update ZIP: %s\nSHA-256: %s\n' "$APP" "$ZIP" "$SHA256"
