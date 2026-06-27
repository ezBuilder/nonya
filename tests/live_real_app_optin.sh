#!/usr/bin/env bash
# Explicit opt-in smoke for real Claude/Codex app injection.
# This intentionally does NOT run from e2e. It types into the selected real app
# only when both confirmation variables are present.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="${1:-}"
TEXT="${2:-NONYA_REAL_APP_OPTIN_PROBE}"
CONFIRM_TEXT="TYPE_INTO_REAL_AGENT_APP"

if [ "$(uname -s)" != "Darwin" ]; then
  echo "skip  real app opt-in smoke (not macOS)"
  exit 0
fi

case "$APP" in
  Claude|Codex) ;;
  *)
    echo "usage: NONYA_ALLOW_REAL_APP_INJECT=1 NONYA_REAL_APP_INJECT_CONFIRM=$CONFIRM_TEXT $0 Claude|Codex [TEXT]" >&2
    exit 2
    ;;
esac

if [ "${NONYA_ALLOW_REAL_APP_INJECT:-}" != "1" ] || [ "${NONYA_REAL_APP_INJECT_CONFIRM:-}" != "$CONFIRM_TEXT" ]; then
  echo "refusing: this would type into the real $APP app."
  echo "set NONYA_ALLOW_REAL_APP_INJECT=1 and NONYA_REAL_APP_INJECT_CONFIRM=$CONFIRM_TEXT to run it."
  exit 2
fi

python3 - "$ROOT" "$APP" <<'PY'
import sys
root, app = sys.argv[1], sys.argv[2]
sys.path.insert(0, root)
from nonya.backends.macos import MacBackend

gate = MacBackend().window_gate(app)
print("%s gate=%s" % (app, gate))
sys.exit(0 if gate == "ok" else 2)
PY
rc=$?
[ "$rc" = 0 ] || exit "$rc"

exec "$ROOT/bin/nonya" --app "$APP" --inject-test "$TEXT"
