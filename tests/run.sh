#!/usr/bin/env bash
# tests/run.sh — run the nonya unit tests (Python; no apps, no keys).
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec python3 "$ROOT/tests/test_classify.py"
