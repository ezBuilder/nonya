#!/usr/bin/env bash
# Live REAL-injection delivery proofs. Opens DISPOSABLE targets with a unique marker and
# verifies the injected keystrokes are actually DELIVERED (a reader captures the submitted
# line) — never touches a real agent session (unique-match only). macOS.
#
#   bash tests/live_inject.sh
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HELPER="$ROOT/build/Nonya.app/Contents/MacOS/NonyaPet"
fail=0

# 1) tmux: inject -> a `read` captures the SUBMITTED line to a file (proves keystrokes + Enter
#    actually reached the process, not just appeared on screen). Engine-agnostic at this layer.
if command -v tmux >/dev/null 2>&1; then
  UCWD=$(mktemp -d)
  tmux kill-session -t nyli 2>/dev/null
  tmux new-session -d -s nyli -x 80 -y 24 -c "$UCWD"
  tmux send-keys -t nyli 'IFS= read -r L; printf "%s" "$L" > '"$UCWD"'/got.txt' C-m
  sleep 0.5
  P=$(tmux list-panes -t nyli -F '#{pane_id}' | head -1)
  python3 -c "import sys; sys.path.insert(0,'$ROOT'); from nonya.backends import tmux; tmux.inject('$P','TMUX_DELIVERED_OK','return')"
  sleep 0.9
  if grep -q "TMUX_DELIVERED_OK" "$UCWD/got.txt" 2>/dev/null; then
    echo "ok    tmux REAL delivery (reader captured the submitted line)"
  else echo "FAIL tmux delivery"; fail=1; fi
  tmux kill-session -t nyli 2>/dev/null; rm -rf "$UCWD"
else
  echo "skip  tmux not installed"
fi

# 2) cwd->pane match for codex (session_meta head): a codex rollout's cwd must resolve so the
#    pane is targeted by dir (multi-session disambiguation), then deliver.
if command -v tmux >/dev/null 2>&1; then
  UCWD=$(mktemp -d); RF=$(mktemp -d)/rollout.jsonl
  python3 -c "import json; open('$RF','w').write(json.dumps({'type':'session_meta','payload':{'originator':'codex-tui','source':'cli','cwd':'$UCWD'}})+'\n'+json.dumps({'payload':{'type':'task_complete'}})+'\n')"
  tmux kill-session -t nyli2 2>/dev/null
  tmux new-session -d -s nyli2 -x 80 -y 24 -c "$UCWD"
  tmux send-keys -t nyli2 'IFS= read -r L; printf "%s" "$L" > '"$UCWD"'/got.txt' C-m
  sleep 0.5
  ok=$(python3 -c "
import sys; sys.path.insert(0,'$ROOT')
from nonya import detect
from nonya.backends import tmux
cwd = detect.session_cwd('codex','$RF')
p = tmux.pane_for_cwd(cwd) if cwd else None
print('OK' if (cwd=='$UCWD' and p and tmux.inject(p,'CODEX_CWD_DELIVERED','return')) else 'NO')
")
  sleep 0.9
  if [ "$ok" = OK ] && grep -q "CODEX_CWD_DELIVERED" "$UCWD/got.txt" 2>/dev/null; then
    echo "ok    codex cwd->pane match REAL delivery (session_meta head)"
  else echo "FAIL codex cwd delivery"; fail=1; fi
  tmux kill-session -t nyli2 2>/dev/null; rm -rf "$UCWD"
fi

# 3) SAFETY: native-terminal-split AX injection is DISABLED by default. macOS routes posted key
#    events to the terminal's ACTIVE split (not the AXFocused one), so injecting into a background
#    split could hit the WRONG session. inject_terminal_split must return False unless the explicit
#    research opt-in NONYA_AX_SPLIT=1 is set. This is the guard that prevents wrong-session misfires.
off=$(NONYA_AX_SPLIT= python3 -c "import sys; sys.path.insert(0,'$ROOT'); from nonya.backends.macos import MacBackend; print(MacBackend().inject_terminal_split('anything','x'))")
if [ "$off" = "False" ]; then echo "ok    native-split AX injection DISABLED by default (no wrong-session misfire)"
else echo "FAIL native-split AX injection not disabled (off=$off)"; fail=1; fi

# 4) --launch: `nonya --launch codex` must create a named tmux session (proves execvp
#    path — we mock execvp via a wrapper that just records the call, since the real
#    codex binary may not be present in CI). We also verify the exit-2 guard works when
#    the engine is absent.
#
#    For the POSITIVE path we check via the bundled binary's --launch option:
#    if tmux IS available and the engine IS on PATH, the process is exec-replaced by
#    tmux — so we verify that launching "cat" (always on PATH) would reach the execvp
#    point by using the Python source directly with a mock execvp.
if command -v tmux >/dev/null 2>&1; then
  # Negative guard: unknown engine -> exit 2 (no tmux session created, no crash)
  rc=0
  python3 -c "
import sys; sys.path.insert(0,'$ROOT')
from nonya.cli import _launch
rc = _launch('no_such_engine_xyz_abc_99')
print('rc=%d' % rc)
" 2>/dev/null | grep -q "rc=2" && echo "ok    --launch unknown-engine -> exit 2 (no misfire)" || { echo "FAIL --launch bad-engine guard"; fail=1; }

  # Positive path mock: verify _launch calls execvp with correct tmux args
  ok=$(python3 -c "
import sys, os
sys.path.insert(0,'$ROOT')
import nonya.cli as cli

calls = []
def _fake_execvp(prog, argv):
    calls.append((prog, argv))
    raise SystemExit(0)   # don't actually exec

orig = os.execvp
os.execvp = _fake_execvp
try:
    cli._launch('cat')    # 'cat' is always on PATH
except SystemExit:
    pass
finally:
    os.execvp = orig

if (calls and calls[0][0] == 'tmux'
        and calls[0][1][0] == 'tmux'
        and calls[0][1][1] == 'new-session'
        and any('cat' in a for a in calls[0][1])):
    print('OK')
else:
    print('NO calls=%s' % calls)
")
  if echo "$ok" | grep -q "^OK$"; then
    echo "ok    --launch cat -> execvp(tmux new-session ... cat) confirmed"
  else
    echo "FAIL --launch execvp path ($ok)"; fail=1
  fi
else
  echo "skip  --launch live test (tmux not installed)"
fi

echo
[ "$fail" = 0 ] && echo "✅ LIVE-INJECT GREEN" || echo "❌ LIVE-INJECT FAILED"
exit "$fail"
