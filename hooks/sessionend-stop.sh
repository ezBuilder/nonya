#!/usr/bin/env bash
# Claude Code SessionEnd hook — stop the nonya watcher spawned for this session.
set -uo pipefail
command -v jq >/dev/null 2>&1 || exit 0
input="$(cat 2>/dev/null)"
sid="$(printf '%s' "$input" | jq -r '.session_id // empty' 2>/dev/null)"
STATE="${NONYA_STATE:-$HOME/.local/state/nonya}"
pidf="$STATE/pid-${sid:-unknown}"
if [ -f "$pidf" ]; then kill "$(cat "$pidf" 2>/dev/null)" 2>/dev/null || true; rm -f "$pidf"; fi
exit 0
