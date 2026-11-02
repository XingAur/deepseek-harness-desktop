#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [[ -d "$ROOT/deepseek-harness/.git" && -f "$ROOT/deepseek-harness/apps/desktop/package.json" ]]; then
  echo "official desktop: Git checkout"
  echo "official branch: $(git -C "$ROOT/deepseek-harness" branch --show-current)"
  echo "official commit: $(git -C "$ROOT/deepseek-harness" rev-parse --short HEAD)"
  node -e "const p=require(process.argv[1]); console.log('official version:', p.version)" "$ROOT/deepseek-harness/package.json"
elif [[ -f "$ROOT/deepseek-harness/apps/desktop/package.json" ]]; then
  echo "official desktop: extracted tree (run bootstrap to replace it with a Git checkout)"
else
  echo "official desktop: missing"
fi
bash "$ROOT/scripts/check.sh"
