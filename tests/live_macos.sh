#!/usr/bin/env bash
# Compatibility wrapper. The old version used TextEdit; keep this entrypoint
# safe by delegating to the disposable NonyaProbe app instead.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec bash "$ROOT/tests/live_gui_probe.sh" "$@"
