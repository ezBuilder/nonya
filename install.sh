#!/usr/bin/env bash
# install.sh — integration shim. Symlinks `nonya` onto PATH and (optionally)
# wires Claude Code SessionStart/SessionEnd hooks + a launchd keepalive.
# Idempotent and reversible (see uninstall.sh).
#
#   ./install.sh                 # symlink ~/.local/bin/nonya only
#   ./install.sh --hooks         # + wire SessionStart/SessionEnd into settings.json
#   ./install.sh --launchd       # + install launchd keepalive
#   ./install.sh --all
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
BIN_SRC="$ROOT/bin/nonya"
BIN_DST="${NONYA_BIN_DST:-$HOME/.local/bin/nonya}"
SETTINGS="${NONYA_SETTINGS:-$HOME/.claude/settings.json}"
STATE="${NONYA_STATE:-$HOME/.local/state/nonya}"
SS="$ROOT/hooks/sessionstart-spawn.sh"
SE="$ROOT/hooks/sessionend-stop.sh"
DO_HOOKS=0; DO_LAUNCHD=0
for a in "$@"; do case "$a" in
  --hooks) DO_HOOKS=1;; --launchd) DO_LAUNCHD=1;; --all) DO_HOOKS=1; DO_LAUNCHD=1;;
  *) echo "unknown: $a" >&2; exit 2;; esac; done

chmod +x "$BIN_SRC" "$ROOT"/hooks/*.sh 2>/dev/null || true
mkdir -p "$(dirname "$BIN_DST")" "$STATE"
ln -sf "$BIN_SRC" "$BIN_DST"
echo "linked  $BIN_DST -> $BIN_SRC"
case ":$PATH:" in *":$(dirname "$BIN_DST"):"*) :;; *) echo "NOTE    add $(dirname "$BIN_DST") to your PATH";; esac

if [ "$DO_HOOKS" -eq 1 ]; then
  command -v jq >/dev/null || { echo "jq required for --hooks" >&2; exit 1; }
  [ -f "$SETTINGS" ] || { mkdir -p "$(dirname "$SETTINGS")"; echo '{}' > "$SETTINGS"; }
  cp "$SETTINGS" "$SETTINGS.nonya.bak"
  tmp="$(mktemp)"
  jq --arg ss "$SS" --arg se "$SE" '
    .hooks //= {} | .hooks.SessionStart //= [] | .hooks.SessionEnd //= [] |
    .hooks.SessionStart |= map(select(((.hooks//[])|map(.command)|join(" ")|contains("nonya"))|not)) |
    .hooks.SessionEnd   |= map(select(((.hooks//[])|map(.command)|join(" ")|contains("nonya"))|not)) |
    .hooks.SessionStart += [ {hooks:[{type:"command",command:$ss}]} ] |
    .hooks.SessionEnd   += [ {hooks:[{type:"command",command:$se}]} ]
  ' "$SETTINGS" > "$tmp" && mv "$tmp" "$SETTINGS"
  echo "wired   SessionStart/SessionEnd -> $SETTINGS  (backup $SETTINGS.nonya.bak)"
  echo "        NOTE auto-spawn is opt-in: set NONYA_AUTOSTART=1 to actually start watchers."
fi

if [ "$DO_LAUNCHD" -eq 1 ]; then
  dst="$HOME/Library/LaunchAgents/com.user.nonya.plist"
  sed -e "s#__NONYA_BIN__#$BIN_DST#g" -e "s#__NONYA_STATE__#$STATE#g" \
      "$ROOT/launchd/com.user.nonya.plist.template" > "$dst"
  echo "wrote   $dst  (edit args, then: launchctl load -w \"$dst\")"
fi

echo "done."
