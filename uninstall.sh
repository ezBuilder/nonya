#!/usr/bin/env bash
# uninstall.sh — reverse install.sh: remove symlink, unwire hooks, remove launchd.
# Leaves the rest of settings.json untouched.
set -euo pipefail

BIN_DST="${NONYA_BIN_DST:-$HOME/.local/bin/nonya}"
SETTINGS="${NONYA_SETTINGS:-$HOME/.claude/settings.json}"

if [ -L "$BIN_DST" ] || [ -f "$BIN_DST" ]; then rm -f "$BIN_DST"; echo "removed $BIN_DST"; fi

if [ -f "$SETTINGS" ] && command -v jq >/dev/null; then
  tmp="$(mktemp)"
  jq '
    if .hooks then
      .hooks.SessionStart = ((.hooks.SessionStart//[]) | map(select(((.hooks//[])|map(.command)|join(" ")|contains("nonya"))|not))) |
      .hooks.SessionEnd   = ((.hooks.SessionEnd//[])   | map(select(((.hooks//[])|map(.command)|join(" ")|contains("nonya"))|not)))
    else . end
  ' "$SETTINGS" > "$tmp" && mv "$tmp" "$SETTINGS"
  echo "unwired nonya hooks from $SETTINGS"
fi

plist="$HOME/Library/LaunchAgents/com.user.nonya.plist"
if [ -f "$plist" ]; then
  launchctl unload -w "$plist" 2>/dev/null || true
  rm -f "$plist"; echo "removed $plist"
fi
echo "done."
