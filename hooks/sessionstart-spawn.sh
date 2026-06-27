#!/usr/bin/env bash
# Claude Code SessionStart hook — spawn a nonya watcher for this session.
# OPT-IN: only runs when NONYA_AUTOSTART=1, so installing the hook does not
# silently start watching every interactive session.
set -uo pipefail
[ "${NONYA_AUTOSTART:-0}" = "1" ] || exit 0
command -v jq >/dev/null 2>&1 || exit 0

input="$(cat 2>/dev/null)"
sid="$(printf '%s'   "$input" | jq -r '.session_id // empty' 2>/dev/null)"
tpath="$(printf '%s' "$input" | jq -r '.transcript_path // empty' 2>/dev/null)"
[ -n "$tpath" ] && [ -f "$tpath" ] || exit 0   # need the exact transcript; else no-op

STATE="${NONYA_STATE:-$HOME/.local/state/nonya}"; mkdir -p "$STATE"
pidf="$STATE/pid-${sid:-unknown}"
if [ -f "$pidf" ] && kill -0 "$(cat "$pidf" 2>/dev/null)" 2>/dev/null; then exit 0; fi

NONYA="$(command -v nonya 2>/dev/null || echo "$HOME/.local/bin/nonya")"
[ -x "$NONYA" ] || exit 0
nohup "$NONYA" --target claude --mode "${NONYA_MODE:-on-error}" --file "$tpath" >/dev/null 2>&1 &
echo $! > "$pidf"
exit 0
