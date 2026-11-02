#!/usr/bin/env bash
# Launch the official DeepSeek Harness desktop (Harness Dev) with its own home.
# Never shares ~/.dsh with the 2.0.x DSH Desktop.
set -euo pipefail

NODE_BIN="$(command -v node)"
export PATH="$(dirname "$NODE_BIN"):/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export CI=true
export ELECTRON_MIRROR="https://npmmirror.com/mirrors/electron/"
export DSH_HOME="$HOME/.dsh-official-dev"
mkdir -p "$DSH_HOME"

OFFICIAL_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ROOT="$OFFICIAL_ROOT/deepseek-harness"
LOG_FILE="$DSH_HOME/official-desktop.log"

cd "$ROOT"
echo "[dsh-official] starting; log: $LOG_FILE"
exec pnpm run start:desktop >"$LOG_FILE" 2>&1
