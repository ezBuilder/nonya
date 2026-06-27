#!/usr/bin/env python3
"""Unit tests for nonya — hash-chained trust ledger. Plain asserts (no pytest).

    python3 tests/test_ledger.py
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)

from nonya import ledger  # noqa: E402

_fail = 0


def check(label, got, want):
    global _fail
    if got == want:
        print("ok    %-34s -> %s" % (label, got))
    else:
        print("FAIL  %-34s -> %s (want %s)" % (label, got, want))
        _fail = 1


# --- empty ledger is trivially valid ---
_sd = tempfile.mkdtemp()
check("empty read", ledger.read(_sd), [])
check("empty verify", ledger.verify_chain(_sd), True)

# --- append a few entries ---
for i in range(3):
    ledger.append(_sd, {
        "session": "sess-%d" % i,
        "stall_class": "stalled",
        "evidence": "agent idle at step %d" % i,
        "injected_text": "run the failing test, then fix the assertion",
        "gates_passed": ["window", "user_idle"],
        "outcome": "nudged",
    })

_entries = ledger.read(_sd)
check("appended count", len(_entries), 3)
check("first prev_hash is genesis", _entries[0]["prev_hash"], ledger.GENESIS)
check("chain links 0->1", _entries[1]["prev_hash"], _entries[0]["hash"])
check("chain links 1->2", _entries[2]["prev_hash"], _entries[1]["hash"])
check("ts auto-filled", isinstance(_entries[0]["ts"], int), True)
check("verify after appends", ledger.verify_chain(_sd), True)

# --- corrupt a MIDDLE line -> verify must fail ---
_path = os.path.join(_sd, ledger.FILENAME)
with open(_path, "r", encoding="utf-8") as fh:
    _lines = fh.readlines()
import json as _json  # noqa: E402
_mid = _json.loads(_lines[1])
_mid["outcome"] = "auto-approved"   # tamper with content, leave hash as-is
_lines[1] = _json.dumps(_mid, ensure_ascii=False, sort_keys=True) + "\n"
with open(_path, "w", encoding="utf-8") as fh:
    fh.writelines(_lines)
check("verify detects tamper", ledger.verify_chain(_sd), False)

# --- deleting a line (gap) -> verify must fail ---
_sd2 = tempfile.mkdtemp()
for i in range(3):
    ledger.append(_sd2, {"session": "s", "stall_class": "stalled", "outcome": "nudged"})
_p2 = os.path.join(_sd2, ledger.FILENAME)
with open(_p2, "r", encoding="utf-8") as fh:
    _l2 = fh.readlines()
del _l2[1]  # drop middle line -> prev_hash gap
with open(_p2, "w", encoding="utf-8") as fh:
    fh.writelines(_l2)
check("verify detects gap", ledger.verify_chain(_sd2), False)

# --- scrub removes fake secrets ---
_token = "api_key=" + ("A" * 32)
_scrubbed = ledger.scrub("calling with " + _token + " now")
check("scrub removes token value", "sk-ant-ABCDEFGH1234567890XYZ" not in _scrubbed, True)
check("scrub keeps redaction marker", "[REDACTED]" in _scrubbed, True)
check("scrub bearer header",
      "secrettoken99" not in ledger.scrub("Authorization: Bearer secrettoken99"), True)
check("scrub standalone ghp",
      "ghp_" not in ledger.scrub("token leaked ghp_0123456789ABCDEFGHIJ extra"), True)
check("scrub leaves plain text", ledger.scrub("just running the tests"), "just running the tests")

# --- secrets never hit the ledger on append ---
_sd3 = tempfile.mkdtemp()
ledger.append(_sd3, {
    "session": "leaky", "stall_class": "error", "outcome": "nudged",
    "evidence": "saw OPENAI_API_KEY=" + ("sk-" + "a" * 32) + " in transcript",
    "injected_text": "set password: hunter2supersecret and retry",
})
_raw = open(os.path.join(_sd3, ledger.FILENAME), "r", encoding="utf-8").read()
check("ledger file has no live key", ("sk-" + "a" * 32) not in _raw, True)
check("ledger file has no password", "hunter2supersecret" not in _raw, True)
check("ledger still verifies after scrub", ledger.verify_chain(_sd3), True)

# --- export_markdown ---
_md = ledger.export_markdown(_sd2)
check("export markdown is str", isinstance(_md, str), True)
check("export flags tamper status", "chain verified: NO" in _md, True)
check("export ok header for clean", "chain verified: yes" in ledger.export_markdown(_sd3), True)

print("ALL PASS" if _fail == 0 else "SOME FAILED")
sys.exit(_fail)
