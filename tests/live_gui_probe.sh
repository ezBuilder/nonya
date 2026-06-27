#!/usr/bin/env bash
# Live macOS GUI injection proof against a disposable local app, never real
# Claude/Codex accounts and never document editors.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BIN="$ROOT/build/NonyaProbe"
APP="NonyaProbe"
MARK="NONYA_PROBE_GUI_DELIVERED_OK"
SENT="<<NONYA_PROBE_NEVER_DONE>>"
G="--idle 0 --poll 1 --grace 2 --max-nudges 1 --max-iterations 4 --no-impact"
fail=0
probe_pid=""

ok(){ printf 'ok    %s\n' "$*"; }
bad(){ printf 'FAIL  %s\n' "$*"; fail=1; }

if [ "$(uname -s)" != "Darwin" ]; then
  echo "skip  GUI probe (not macOS)"
  exit 0
fi

swiftc "$ROOT/tests/NonyaProbe.swift" -o "$BIN" || exit 1
killall "$APP" >/dev/null 2>&1 || true

state_dir="$(mktemp -d)"
tx="$(mktemp -t nonya_probe_tx).jsonl"
submit_a="$(mktemp -t nonya_probe_submit_a)"
submit_b="$(mktemp -t nonya_probe_submit_b)"
submit_c="$(mktemp -t nonya_probe_submit_c)"
printf '%s\n' '{"isApiErrorMessage":true,"apiErrorStatus":503,"error":"overloaded"}' > "$tx"
export NONYA_STATE="$state_dir"

probe_count(){ out="$(osascript -e 'tell application "System Events" to if exists process "NonyaProbe" then return count of windows of process "NonyaProbe"' 2>/dev/null || true)"; printf '%s\n' "${out:-0}"; }
probe_text(){ osascript -e 'tell application "System Events" to tell process "NonyaProbe"
  set out to ""
  repeat with w in windows
    try
      set out to out & (value of text area 1 of scroll area 1 of w) & "||"
    end try
  end repeat
  return out
 end tell' 2>/dev/null; }
wait_count(){ want="$1"; i=0; while [ "$i" -lt 50 ]; do [ "$(probe_count)" = "$want" ] && return 0; sleep 0.2; i=$((i+1)); done; return 1; }
start_probe(){ "$BIN" "$@" >/tmp/nonya_probe_app.log 2>&1 & probe_pid=$!; }
probe_submit(){ file="$1"; [ -s "$file" ] && cat "$file"; }
stop_probe(){
  if [ -n "${probe_pid:-}" ]; then
    kill "$probe_pid" >/dev/null 2>&1 || true
    wait "$probe_pid" >/dev/null 2>&1 || true
    probe_pid=""
  fi
  killall "$APP" >/dev/null 2>&1 || true
  i=0
  while [ "$i" -lt 50 ]; do [ "$(probe_count)" = 0 ] && return 0; sleep 0.1; i=$((i+1)); done
  return 0
}

echo "=== Probe A: multi-window gate blocks keys ==="
start_probe --multi --submit-file "$submit_a"
wait_count 2 || bad "probe multi-window did not start"
"$ROOT/bin/nonya" --target cli --app "$APP" --engine claude --file "$tx" --sentinel "$SENT" --nudge "$MARK" --mode on-error $G >/tmp/nonya_probe_A.log 2>&1
if grep -q "알림만\\|alert" /tmp/nonya_probe_A.log; then ok "A: gate reported alert-only"; else bad "A: no alert-only log"; fi
if probe_text | grep -q "$MARK"; then bad "A: marker reached multi-window probe"; else ok "A: zero keys in multi-window probe"; fi
if probe_submit "$submit_a" | grep -q "$MARK"; then bad "A: marker submitted in multi-window probe"; else ok "A: zero submit in multi-window probe"; fi
stop_probe

echo "=== Probe B: single-window submits with Return ==="
start_probe --submit-file "$submit_b"
wait_count 1 || bad "probe single-window did not start"
"$ROOT/bin/nonya" --target cli --app "$APP" --engine claude --file "$tx" --sentinel "$SENT" --nudge "$MARK" --mode on-error $G >/tmp/nonya_probe_B.log 2>&1
if grep -q "nudge #1" /tmp/nonya_probe_B.log; then ok "B: nudge attempted"; else bad "B: no nudge log"; fi
if probe_submit "$submit_b" | grep -q "$MARK"; then ok "B: marker submitted by disposable GUI probe"; else bad "B: marker was only pasted, not submitted"; fi
stop_probe

echo "=== Probe C: single-window submits with Command-Return fallback ==="
start_probe --cmd-submit-only --submit-file "$submit_c"
wait_count 1 || bad "probe cmd-submit-only did not start"
"$ROOT/bin/nonya" --target cli --app "$APP" --engine claude --file "$tx" --sentinel "$SENT" --nudge "$MARK" --mode on-error $G >/tmp/nonya_probe_C.log 2>&1
if grep -q "nudge #1" /tmp/nonya_probe_C.log; then ok "C: nudge attempted"; else bad "C: no nudge log"; fi
if probe_submit "$submit_c" | grep -q "$MARK"; then ok "C: marker submitted via Command-Return fallback"; else bad "C: fallback submit missing"; fi
stop_probe

rm -rf "$state_dir" "$tx" "$submit_a" "$submit_b" "$submit_c"
[ "$fail" = 0 ] && echo "✅ GUI-PROBE GREEN" || echo "❌ GUI-PROBE FAILED"
exit "$fail"
