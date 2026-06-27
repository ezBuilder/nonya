#!/usr/bin/env python3
"""CLI safety regressions for commands that could type into real logged-in apps."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)

from nonya.cli import _inject_test  # noqa: E402

_fail = 0


def check(label, cond):
    global _fail
    if cond:
        print("ok    %s" % label)
    else:
        print("FAIL  %s" % label)
        _fail = 1


os.environ.pop("NONYA_ALLOW_REAL_APP_INJECT", None)
for app in ("Claude", "Codex", "Antigravity"):
    check("inject-test refuses real %s app" % app, _inject_test("NONYA_TEST", app) == 2)

print("ALL PASS" if _fail == 0 else "SOME FAILED")
sys.exit(_fail)
