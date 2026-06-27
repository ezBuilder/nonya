#!/usr/bin/env bash
# Read-only live checks for the real Claude/Codex desktop apps.
# This never sends keystrokes and never opens throwaway document apps.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
fail=0

if [ "$(uname -s)" != "Darwin" ]; then
  echo "skip  real app gate checks (not macOS)"
  exit 0
fi

python3 - "$ROOT" <<'PY'
import sys
root = sys.argv[1]
sys.path.insert(0, root)
from nonya.backends.macos import MacBackend

fail = 0
b = MacBackend()
for app in ("Claude", "Codex"):
    gate = b.window_gate(app)
    print("ok    %s app gate read-only -> %s" % (app, gate) if gate == "ok"
          else "FAIL  %s app gate read-only -> %s" % (app, gate))
    if gate != "ok":
        fail = 1
sys.exit(fail)
PY
rc=$?
[ "$rc" = 0 ] || fail=1

for nonya_bin in "$ROOT/bin/nonya" "$ROOT/build/dist/nonya"; do
  [ -x "$nonya_bin" ] || continue
  label="$(basename "$nonya_bin")"
  [ "$nonya_bin" = "$ROOT/build/dist/nonya" ] && label="bundled-nonya"
  for app in Claude Codex; do
    out="$("$nonya_bin" --app "$app" --inject-test "NONYA_PROTECTED_REAL_APP_PROBE" 2>&1)"
    rc=$?
    if [ "$rc" = 2 ] && printf '%s\n' "$out" | grep -q "refusing to type into real $app app"; then
      echo "ok    $label $app inject-test protected (no keys sent)"
    else
      echo "FAIL  $label $app inject-test protection"
      printf '%s\n' "$out" | sed 's/^/      /'
      fail=1
    fi
  done
done

[ "$fail" = 0 ] && echo "✅ REAL-APP READONLY GREEN" || echo "❌ REAL-APP READONLY FAILED"
exit "$fail"
