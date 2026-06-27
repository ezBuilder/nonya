#!/usr/bin/env bash
# e2e — full verification suite.
#   bash tests/e2e.sh          # unit + bundled-core inject + app-bundle (fast, no GUI)
#   bash tests/e2e.sh --live   # + disposable GUI probe + read-only Claude/Codex app gate + tmux delivery checks
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LIVE=0; [ "${1:-}" = "--live" ] && LIVE=1
fail=0
hr(){ printf '\n### %s\n' "$*"; }

hr "unit (classify / policy / sentinel / status / tmux gate)"
python3 "$ROOT/tests/test_classify.py" | tail -1 || fail=1
python3 "$ROOT/tests/test_cli_safety.py" | tail -1 || fail=1
python3 "$ROOT/tests/test_support_matrix.py" | tail -1 || fail=1

hr "bundled core (PyInstaller binary == source behavior)"
[ -x "$ROOT/build/dist/nonya" ] || bash "$ROOT/packaging/build-core.sh" >/dev/null
BIN="$ROOT/build/dist/nonya"
if "$BIN" --version >/dev/null 2>&1; then echo "ok    bundled --version"; else echo "FAIL bundled run"; fail=1; fi
# NONYA_STATE -> throwaway so test injections NEVER pollute the user's real intervention ledger
# (those would inflate "keys sent" in the metrics dashboard with fake test events).
export NONYA_STATE="$(mktemp -d)"
if command -v tmux >/dev/null 2>&1; then
  bundled_cli_case(){
    local engine="$1" tx="$2" marker="$3" dir pane sess
    sess="nxe-${engine}-$$"
    dir="$(mktemp -d)"
    tmux kill-session -t "$sess" 2>/dev/null || true
    tmux new-session -d -s "$sess" -x 80 -y 24 -c "$dir"
    tmux send-keys -t "$sess" 'IFS= read -r L; printf "%s" "$L" > got.txt' C-m
    pane=$(tmux list-panes -t "$sess" -F '#{session_name}:#{window_index}.#{pane_index}' | head -1)
    "$BIN" --target cli --tmux "$pane" --engine "$engine" --file "$tx" --sentinel "<<NXE>>" --nudge "$marker" \
      --idle 0 --poll 1 --grace 2 --max-nudges 1 --max-iterations 4 --no-impact >/dev/null 2>&1
    if grep -q "$marker" "$dir/got.txt" 2>/dev/null; then
      echo "ok    bundled ${engine} cli tmux inject"
    else
      echo "FAIL bundled ${engine} cli tmux inject"; fail=1
    fi
    tmux kill-session -t "$sess" 2>/dev/null || true
    rm -rf "$dir"
  }
  TXC=$(mktemp).jsonl; printf '%s\n' '{"isApiErrorMessage":true,"error":"x"}' >"$TXC"
  TXD=$(mktemp).jsonl; printf '%s\n' '{"type":"event_msg","payload":{"type":"task_started","turn_id":"t2"}}' '{"type":"event_msg","payload":{"type":"agent_message","message":"working"}}' >"$TXD"
  touch -t 200001010000 "$TXC" "$TXD"
  bundled_cli_case claude "$TXC" "E2E_CLAUDE_주입_OK"
  bundled_cli_case codex "$TXD" "E2E_CODEX_주입_OK"
  rm -f "$TXC" "$TXD"
  # FULL recovery loop through the SHIPPED binary: detect stuck -> find the pane BY CWD ->
  # inject -> verify delivery. This guards the bundle-only failure where a '\t' in the tmux
  # -F format was mangled by PyInstaller, so pane discovery silently returned nothing and the
  # app could never recover anything (passed via source, failed in the actual .app).
  if "$BIN" --selftest >/dev/null 2>&1; then echo "ok    bundled --selftest (end-to-end cwd->pane recovery)"; else echo "FAIL bundled --selftest (pane discovery broken in frozen binary)"; fail=1; fi
fi
if NONYA_LANG=en "$BIN" --metrics 2>/dev/null | grep -q "nonya metrics"; then echo "ok    bundled --metrics renders (en)"; else echo "FAIL bundled --metrics"; fail=1; fi
if NONYA_LANG=ko "$BIN" --metrics 2>/dev/null | grep -q "개입 지표"; then echo "ok    bundled --metrics localized (ko)"; else echo "FAIL bundled --metrics i18n"; fail=1; fi

hr "app bundle (노냐.app + embedded core)"
APP_BUNDLE="$ROOT/build/Nonya.app"
[ -d "$APP_BUNDLE" ] || bash "$ROOT/packaging/build-app.sh" >/dev/null
if [ -x "$APP_BUNDLE/Contents/Resources/core/nonya" ]; then echo "ok    .app assembled w/ embedded core"; else echo "FAIL .app bundle"; fail=1; fi
# eyes are NATIVE Core Graphics (no WebView). Verify the bundled shell renders every
# supervisor state to a non-empty PNG, and that the dead 3D/web pet assets are NOT shipped.
APP_BIN="$APP_BUNDLE/Contents/MacOS/NonyaPet"
if lipo -archs "$APP_BIN" 2>/dev/null | grep -q arm64; then echo "ok    .app shell built"; else echo "FAIL .app shell"; fail=1; fi
EYES=$(mktemp -d)
"$APP_BIN" --render-states "$EYES" >/dev/null 2>&1 || true
sleep 1
n=$(ls "$EYES"/eye-*.png 2>/dev/null | wc -l | tr -d ' ')
if [ "${n:-0}" -ge 8 ] && [ -s "$EYES/eye-stuck.png" ]; then echo "ok    native eyes render ($n states, non-empty)"; else echo "FAIL native eyes render (got ${n:-0})"; fail=1; fi
rm -r "$EYES" 2>/dev/null
if [ -e "$APP_BUNDLE/Contents/Resources/web" ]; then echo "FAIL dead web/3D assets re-bundled"; fail=1; else echo "ok    no dead web/3D assets in bundle"; fi

if [ "$LIVE" = 1 ]; then
  hr "live macOS GUI injection (disposable NonyaProbe, no real accounts)"
  bash "$ROOT/tests/live_gui_probe.sh" || fail=1

  hr "live Claude/Codex app gate (read-only, no keystrokes)"
  bash "$ROOT/tests/live_agent_apps_readonly.sh" || fail=1

  hr "live REAL-injection delivery (tmux/codex deliver; Terminal.app honest non-delivery)"
  bash "$ROOT/tests/live_inject.sh" || fail=1
fi

echo
[ "$fail" = 0 ] && echo "✅ E2E ALL GREEN" || echo "❌ E2E FAILED"
exit "$fail"
