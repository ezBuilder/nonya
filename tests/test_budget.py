#!/usr/bin/env python3
"""Unit tests for nonya.budget — the autonomy 'leash'.

Tiny JSON fixtures written inline (no fixture files). Plain asserts, no pytest,
same house style as tests/test_supervise.py:

    python3 tests/test_budget.py
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)

from nonya import budget  # noqa: E402

_fail = 0
_tmp = []


def check(label, got, want):
    global _fail
    if got == want:
        print("ok    %-40s -> %s" % (label, got))
    else:
        print("FAIL  %-40s -> %s (want %s)" % (label, got, want))
        _fail = 1


def write_json(obj):
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(obj, fh)
    _tmp.append(path)
    return path


def clear_env():
    os.environ.pop(budget.ENV_PATH, None)


# --- quiet-hours wrap past midnight (23:00-06:00) ---
b_wrap = budget.Budget(quiet_hours={"start": "23:00", "end": "06:00"})
check("wrap contains 02:00", budget.in_quiet_hours(b_wrap, "02:00"), True)
check("wrap contains 23:30", budget.in_quiet_hours(b_wrap, "23:30"), True)
check("wrap contains 23:00 (start incl)", budget.in_quiet_hours(b_wrap, "23:00"), True)
check("wrap excludes 12:00", budget.in_quiet_hours(b_wrap, "12:00"), False)
check("wrap excludes 06:00 (end excl)", budget.in_quiet_hours(b_wrap, "06:00"), False)
check("wrap excludes 22:59", budget.in_quiet_hours(b_wrap, "22:59"), False)

# --- same-day window (01:00-07:00) ---
b_day = budget.Budget(quiet_hours={"start": "01:00", "end": "07:00"})
check("day contains 02:00", budget.in_quiet_hours(b_day, "02:00"), True)
check("day excludes 12:00", budget.in_quiet_hours(b_day, "12:00"), False)
check("day excludes 00:59", budget.in_quiet_hours(b_day, "00:59"), False)
check("day end exclusive 07:00", budget.in_quiet_hours(b_day, "07:00"), False)

# --- no quiet hours / malformed inputs are safe (not quiet) ---
check("no quiet window -> False", budget.in_quiet_hours(budget.Budget(), "02:00"), False)
check("malformed now -> False", budget.in_quiet_hours(b_wrap, "2am"), False)
check("None budget -> False", budget.in_quiet_hours(None, "02:00"), False)

# --- alert-only blocks inject; auto_inject enables ---
check("default alert-only blocks inject", budget.allow_inject(budget.Budget()), False)
check("auto_inject True allows", budget.allow_inject(budget.Budget(auto_inject=True)), True)
check("None budget blocks inject", budget.allow_inject(None), False)

# --- panic-word detection (case-insensitive substring) ---
b_panic = budget.Budget(panic_word="STOP")
check("panic exact", budget.has_panic(b_panic, "please STOP now"), True)
check("panic case-insensitive", budget.has_panic(b_panic, "I said stop it"), True)
check("panic absent", budget.has_panic(b_panic, "all good, continue"), False)
check("panic empty text", budget.has_panic(b_panic, ""), False)
check("no panic word configured", budget.has_panic(budget.Budget(), "STOP"), False)
check("None budget no panic", budget.has_panic(None, "STOP"), False)

# --- defaults when no file ---
clear_env()
fresh = budget.load_budget("/no/such/dir")
check("missing file auto_inject default", fresh.auto_inject, budget.DEFAULT_AUTO_INJECT)
check("missing file max_recoveries default", fresh.max_recoveries, budget.DEFAULT_MAX_RECOVERIES)
check("missing file spend_ceiling default", fresh.spend_ceiling, budget.DEFAULT_SPEND_CEILING)
check("missing file no quiet hours", fresh.quiet_hours, None)
check("missing file no panic word", fresh.panic_word, "")

# --- load from a real file (full leash) ---
clear_env()
full = write_json({
    "auto_inject": True,
    "max_recoveries": 5,
    "spend_ceiling": 100,
    "quiet_hours": {"start": "01:00", "end": "07:00"},
    "panic_word": "HALT",
})
os.environ[budget.ENV_PATH] = full
loaded = budget.load_budget()
check("env path auto_inject", loaded.auto_inject, True)
check("env path max_recoveries", loaded.max_recoveries, 5)
check("env path give_up_after maps", loaded.give_up_after(), 5)
check("env path spend_ceiling", loaded.spend_ceiling, 100)
check("env path quiet_hours", loaded.quiet_hours, {"start": "01:00", "end": "07:00"})
check("env path panic_word", loaded.panic_word, "HALT")

# --- env path wins over state_dir ---
sd = tempfile.mkdtemp()
with open(os.path.join(sd, budget.FILENAME), "w", encoding="utf-8") as fh:
    json.dump({"spend_ceiling": 7}, fh)
loaded2 = budget.load_budget(sd)  # env still points at `full`
check("env beats state_dir", loaded2.spend_ceiling, 100)
clear_env()
loaded3 = budget.load_budget(sd)  # now reads <state_dir>/budget.json
check("state_dir file read", loaded3.spend_ceiling, 7)

# --- garbage / partial fields degrade safely ---
clear_env()
junk = write_json({
    "auto_inject": "yes",          # not literal True -> stays alert-only
    "max_recoveries": "lots",       # junk -> default
    "spend_ceiling": -5,            # below floor -> default
    "quiet_hours": {"start": "25:00", "end": "07:00"},  # bad start -> dropped
    "panic_word": 123,              # non-str -> ""
})
os.environ[budget.ENV_PATH] = junk
g = budget.load_budget()
check("junk auto_inject stays False", g.auto_inject, False)
check("junk max_recoveries -> default", g.max_recoveries, budget.DEFAULT_MAX_RECOVERIES)
check("junk spend_ceiling -> default", g.spend_ceiling, budget.DEFAULT_SPEND_CEILING)
check("bad quiet_hours dropped", g.quiet_hours, None)
check("non-str panic_word -> ''", g.panic_word, "")

# --- non-object JSON degrades to defaults ---
clear_env()
arr = write_json([1, 2, 3])
os.environ[budget.ENV_PATH] = arr
check("array JSON -> defaults", budget.load_budget().spend_ceiling, budget.DEFAULT_SPEND_CEILING)
clear_env()


for p in _tmp:
    try:
        os.remove(p)
    except OSError:
        pass
try:
    os.remove(os.path.join(sd, budget.FILENAME))
    os.rmdir(sd)
except OSError:
    pass

print("ALL PASS" if _fail == 0 else "SOME FAILED")
sys.exit(_fail)
