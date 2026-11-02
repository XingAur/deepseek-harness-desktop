#!/usr/bin/env bash
# Compatibility entrypoint: official sources now stay in an isolated Git checkout.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
echo "tarball extraction is disabled; creating the updateable official Git checkout"
exec bash "$ROOT/scripts/bootstrap.sh"
