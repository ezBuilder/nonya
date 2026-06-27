#!/usr/bin/env python3
"""Audit that the supported Claude/Codex app+CLI cases have explicit tests/docs.

This is intentionally structural: it prevents future edits from silently
dropping a target from the verification matrix while green tests still pass.
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
_fail = 0


def check(label, cond, detail=""):
    global _fail
    if cond:
        print("ok    %-42s %s" % (label, detail))
    else:
        print("FAIL  %-42s %s" % (label, detail))
        _fail = 1


files = {
    "e2e": (ROOT / "tests" / "e2e.sh").read_text(encoding="utf-8"),
    "readonly": (ROOT / "tests" / "live_agent_apps_readonly.sh").read_text(encoding="utf-8"),
    "probe": (ROOT / "tests" / "live_gui_probe.sh").read_text(encoding="utf-8"),
    "inject": (ROOT / "tests" / "live_inject.sh").read_text(encoding="utf-8"),
    "matrix": (ROOT / "docs" / "TARGET-MATRIX.md").read_text(encoding="utf-8"),
    "readme": (ROOT / "README.md").read_text(encoding="utf-8"),
    "loop": (ROOT / "nonya" / "loop.py").read_text(encoding="utf-8"),
    "cli": (ROOT / "nonya" / "cli.py").read_text(encoding="utf-8"),
    "real_optin": (ROOT / "tests" / "live_real_app_optin.sh").read_text(encoding="utf-8"),
}

for target in ("Claude App", "Claude CLI", "Codex App", "Codex CLI"):
    check("matrix contains %s" % target, target in files["matrix"])

check("source app gate covers Claude", "for app in (\"Claude\", \"Codex\")" in files["readonly"])
check("source app gate covers Codex", "for app in (\"Claude\", \"Codex\")" in files["readonly"])
check("bundled app protection checked", "\"$ROOT/build/dist/nonya\"" in files["readonly"])
check("real app opt-in required in loop", "NONYA_ALLOW_REAL_APP_INJECT" in files["loop"])
check("real app inject-test protected", "_PROTECTED_INJECT_TEST_APPS" in files["cli"])
check("real app opt-in smoke exists", "NONYA_REAL_APP_INJECT_CONFIRM" in files["real_optin"])
check("real app opt-in smoke covers Claude", "Claude|Codex" in files["real_optin"])
check("real app opt-in smoke covers Codex", "Claude|Codex" in files["real_optin"])

check("bundled Claude CLI delivery checked", "bundled_cli_case claude" in files["e2e"])
check("bundled Codex CLI delivery checked", "bundled_cli_case codex" in files["e2e"])
check("codex cwd pane delivery checked", "codex cwd->pane match REAL delivery" in files["inject"])
check("disposable GUI probe used", "NonyaProbe" in files["probe"] and "TextEdit" not in files["probe"])
check("legacy live_macos delegates safely",
      "live_gui_probe.sh" in (ROOT / "tests" / "live_macos.sh").read_text(encoding="utf-8"))
check("README documents real-app opt-in", "NONYA_ALLOW_REAL_APP_INJECT=1" in files["readme"])
check("matrix documents disposable probe", "NonyaProbe" in files["matrix"])

print("ALL PASS" if _fail == 0 else "SOME FAILED")
sys.exit(_fail)
