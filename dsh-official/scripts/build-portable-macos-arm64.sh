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
export DSH_PORTABLE_ALLOW_MAC_ARM64_CROSS=1
export DSH_PORTABLE_SOURCE_ROOT="$ROOT"
(cd "$OVERLAY" && npm ci && npm run bundle:host && npm run bundle:client && npm run check)

rm -rf "$STAGE"
mkdir -p "$STAGE" "$DIST"
git -C "$OFFICIAL" archive HEAD | tar -x -C "$STAGE"
printf 'gitdir: %s\n' "$OFFICIAL/.git" >"$STAGE/.git"
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
ASAR="$APP/Contents/Resources/app.asar"
ASAR_CLI="$STAGE/node_modules/.pnpm/node_modules/.bin/asar"
ASAR_TREE="$STAGE/.asar-sanitize"
rm -rf "$ASAR_TREE"
"$ASAR_CLI" extract "$ASAR" "$ASAR_TREE"
"$NODE_BIN" "$ROOT/scripts/sanitize-portable-paths.mjs" "$ASAR_TREE" "$ROOT" "$STAGE"
"$ASAR_CLI" pack "$ASAR_TREE" "$ASAR.sanitized"
mv "$ASAR.sanitized" "$ASAR"
rm -rf "$ASAR_TREE"
if [[ -d "$ASAR.unpacked" ]]; then "$NODE_BIN" "$ROOT/scripts/sanitize-portable-paths.mjs" "$ASAR.unpacked" "$ROOT" "$STAGE"; fi
"$NODE_BIN" "$ROOT/scripts/verify-portable-paths.mjs" "$APP/Contents/Resources" "$ROOT" "$STAGE"
/usr/bin/codesign --force --deep --sign - --preserve-metadata=entitlements,requirements,flags "$APP"
/usr/bin/codesign --verify --deep --strict "$APP"

ZIP="$DIST/deepseek-harness-overlay-${BUILD_VERSION}-mac-arm64-adhoc-manual.zip"
DMG="$DIST/deepseek-harness-overlay-${BUILD_VERSION}-mac-arm64-adhoc-manual.dmg"
DMG_STAGE="$STAGE/apps/desktop/.desktop-build/portable-dmg"
rm -f "$ZIP" "$ZIP.sha256" "$ZIP.build.json" "$DMG" "$DMG.sha256" "$DMG.build.json"
rm -rf "$DMG_STAGE"
/usr/bin/ditto -c -k --sequesterRsrc --keepParent "$APP" "$ZIP"
(
  cd "$STAGE/apps/desktop"
  set -a
  source .env.macos
  set +a
  DSH_DESKTOP_TARGET_PLATFORM=darwin DSH_DESKTOP_TARGET_ARCH=arm64 DSH_DESKTOP_UNSIGNED=1 \
    $PNPM exec electron-builder --config electron-builder.config.mjs --mac dmg --arm64 --publish never \
      --prepackaged "$APP" --config.directories.output "$DMG_STAGE" --config.mac.notarize=false
)
DMG_COUNT="$(find "$DMG_STAGE" -maxdepth 1 -type f -name '*.dmg' | wc -l | tr -d ' ')"
if [[ "$DMG_COUNT" != 1 ]]; then
  echo "portable build: expected exactly one DMG in $DMG_STAGE" >&2
  exit 1
fi
DMG_SOURCE="$(find "$DMG_STAGE" -maxdepth 1 -type f -name '*.dmg' -print -quit)"
mv "$DMG_SOURCE" "$DMG"
/usr/bin/hdiutil verify "$DMG"
ZIP_METADATA="$($NODE_BIN "$ROOT/scripts/write-portable-metadata.mjs" "$ZIP" "$STAGE/packages/external/overlay-custom" "$OFFICIAL_SHA" "$OFFICIAL_VERSION" "$BUILD_VERSION" darwin arm64 ad-hoc)"
DMG_METADATA="$($NODE_BIN "$ROOT/scripts/write-portable-metadata.mjs" "$DMG" "$STAGE/packages/external/overlay-custom" "$OFFICIAL_SHA" "$OFFICIAL_VERSION" "$BUILD_VERSION" darwin arm64 ad-hoc)"
ZIP_SHA256="$(printf '%s\n' "$ZIP_METADATA" | tail -n 1)"
DMG_SHA256="$(printf '%s\n' "$DMG_METADATA" | tail -n 1)"

if [[ -n "$(git -C "$OFFICIAL" status --short)" ]] || [[ "$(git -C "$OFFICIAL" rev-parse HEAD)" != "$OFFICIAL_SHA" ]]; then
  echo "portable build: official checkout changed during staging" >&2
  exit 1
fi
printf 'portable app: %s\nmanual update ZIP: %s\nZIP SHA-256: %s\ninstaller DMG: %s\nDMG SHA-256: %s\n' "$APP" "$ZIP" "$ZIP_SHA256" "$DMG" "$DMG_SHA256"
