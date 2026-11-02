#!/usr/bin/env bash
# Add the overlay plugin to the official desktop profile after that app has
# created ~/.dsh/profiles/desktop. Does not modify 2.0.x.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OVERLAY="$ROOT/overlay"
OFFICIAL="$ROOT/deepseek-harness"
PROFILE="${DSH_OVERLAY_PROFILE:-desktop}"
PROFILE_HOME="${DSH_HOME:-$HOME/.dsh}/profiles/$PROFILE"
echo "DSH_HOME=${DSH_HOME:-"(unset, dsh default ~/.dsh)"}"
echo "Install overlay only into the official profile home (example: DSH_HOME=\$HOME/.dsh-official-dev)."

if [[ -f "$PROFILE_HOME/package.json" ]] && node -e "const p=require(process.argv[1]); process.exit(p.dependencies?.['dsh-overlay-custom'] ? 0 : 1)" "$PROFILE_HOME/package.json"; then
  echo "overlay already installed in profile $PROFILE"
  exit 0
fi

if [[ -f "$PROFILE_HOME/package.json" ]] && node -e "const p=require(process.argv[1]); process.exit(p.name === 'dsh-profile-desktop' ? 0 : 1)" "$PROFILE_HOME/package.json"; then
  echo "the Electron-managed desktop profile only accepts changes through Plugin Manager" >&2
  echo "open Plugin Manager and add: file:$OVERLAY" >&2
  exit 1
fi

if command -v dsh >/dev/null 2>&1; then
  dsh plugin --profile "$PROFILE" add "file:$OVERLAY"
elif [[ -f "$OFFICIAL/package.json" ]]; then
  NODE_BIN="$(command -v node)"
  export PATH="$(dirname "$NODE_BIN"):/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
  (cd "$OFFICIAL" && pnpm dsh plugin --profile "$PROFILE" add "file:$OVERLAY")
else
  echo "official checkout is missing; run bootstrap and build first" >&2
  exit 1
fi
echo "installed overlay into profile $PROFILE"
