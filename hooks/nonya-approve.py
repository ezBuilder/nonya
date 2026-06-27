#!/usr/bin/env python3
"""nonya PreToolUse hook for Claude Code — risk-scored auto-unblock.

Auto-APPROVES low-risk reversible in-scope commands (read a file, run the
project's tests, git status/diff) so a 2am permission prompt never stalls an
overnight run; HOLDS (asks the human) anything destructive / secret / deploy /
install / network / privilege. Defaults to HOLD on anything unrecognized.

Install (~/.claude/settings.json):
  "hooks": { "PreToolUse": [ { "matcher": "Bash",
    "hooks": [ { "type": "command",
      "command": "NONYA_HOME=/path/to/nonya python3 /path/to/nonya/hooks/nonya-approve.py" } ] } ] }

It reads the tool call as JSON on stdin and prints a PreToolUse permission
decision. Read-only + offline; never raises (a failure just defers to the
normal prompt).
"""
import json
import os
import sys

# locate the nonya package (installed, or via NONYA_HOME, or the repo this lives in)
try:
    from nonya import unblock  # type: ignore
except Exception:
    root = os.environ.get("NONYA_HOME") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, root)
    try:
        from nonya import unblock  # type: ignore
    except Exception:
        sys.exit(0)  # can't load nonya -> defer to the normal permission flow


def _decision(decision: str, reason: str) -> str:
    return json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": decision,
        "permissionDecisionReason": reason,
    }})


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    tool = data.get("tool_name", "")
    inp = data.get("tool_input", {}) or {}
    cmd = inp.get("command", "") if tool == "Bash" else ""
    if not cmd:
        return 0  # not a shell command — let Claude Code's normal flow handle it
    try:
        decision, cat, reason = unblock.risk_of(cmd)
    except Exception:
        return 0
    if decision == unblock.AUTO:
        print(_decision("allow", "nonya: low-risk reversible (%s)" % cat))
    else:
        # HOLD -> ask the human (never silently auto-deny; nonya escalates separately)
        print(_decision("ask", "nonya HOLD [%s]: %s" % (cat, reason)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
