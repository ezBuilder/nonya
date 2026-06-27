#!/usr/bin/env python3
"""Unit tests for nonya — briefing (wake-up after-action report).

Pure formatting over ledger.jsonl + state.json. No apps, no network, no
keystrokes. Plain asserts (house style, no pytest):

    python3 tests/test_briefing.py
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)

os.environ["NONYA_LANG"] = "en"        # deterministic English assertions (ignore host Mac language)
os.environ["NONYA_NO_OS_LANG"] = "1"

from nonya import briefing, status  # noqa: E402

_fail = 0


def check(label, got, want):
    global _fail
    if got == want:
        print("ok    %-40s -> %s" % (label, got))
    else:
        print("FAIL  %-40s -> %s (want %s)" % (label, got, want))
        _fail = 1


# Seed a ledger record. Prefer the real nonya.ledger.append (sibling module);
# fall back to writing the documented JSONL shape directly so this test is
# self-contained and does not hard-depend on the sibling landing first.
def append_ledger(state_dir, **rec):
    try:
        from nonya import ledger  # type: ignore
        ledger.append(state_dir, **rec)
        return
    except Exception:
        pass
    with open(os.path.join(state_dir, "ledger.jsonl"), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def seed(state_dir):
    # Session A: shipped & verified (least urgent).
    append_ledger(state_dir, ts=1000, session="claude:proj-a", event="inject",
                  state="STALLED", reason="no tool output for 9m; nudged toward test:42")
    append_ledger(state_dir, ts=1010, session="claude:proj-a", event="stall",
                  state="STALLED", reason="retry churn on API error", outcome="recovered")
    append_ledger(state_dir, ts=1020, session="claude:proj-a", event="verify",
                  state="COMPLETED", reason="ran make test", outcome="pass")
    append_ledger(state_dir, ts=1030, session="claude:proj-a", event="done",
                  state="COMPLETED", reason="end_turn after green tests")

    # Session B: NEEDS YOU — escalation (most urgent).
    append_ledger(state_dir, ts=2000, session="codex:proj-b", event="inject",
                  state="ERROR", reason="repeated 429; asked it to back off")
    append_ledger(state_dir, ts=2050, session="codex:proj-b", event="escalate",
                  state="ERROR", reason="nudged 5x with no progress; human needed",
                  outcome="stuck")

    # Session C: stalled / unverified (middle).
    append_ledger(state_dir, ts=1500, session="claude:proj-c", event="stall",
                  state="STALLED", reason="hung mid tool-use 12m")

    status.write(state_dir, status="stuck", target="codex:proj-b", nudges=5)


def main():
    with tempfile.TemporaryDirectory() as d:
        seed(d)
        md = briefing.build_briefing(d)

        # contains each session id
        check("has session A", "claude:proj-a" in md, True)
        check("has session B", "codex:proj-b" in md, True)
        check("has session C", "claude:proj-c" in md, True)

        # contains intervention reasons (the WHY)
        check("has reason A", "nudged toward test:42" in md, True)
        check("has escalate reason B", "human needed" in md, True)

        # verify pass/fail surfaced
        check("verify pass shown", "1x pass" in md, True)

        # ordering: needs-you session B must appear before stalled C and shipped A
        i_b = md.index("codex:proj-b")
        i_c = md.index("claude:proj-c")
        i_a = md.index("claude:proj-a")
        check("needs-you first", i_b < i_c and i_b < i_a, True)
        check("stalled before shipped", i_c < i_a, True)
        hdr_b = next(ln for ln in md.splitlines()
                     if ln.startswith("###") and "codex:proj-b" in ln)
        check("B labelled needs-you", "needs-you" in hdr_b, True)

        # top verdict points at the human
        verdict = briefing.top_verdict(d)
        check("verdict needs-you", "NEED YOU" in verdict, True)

    # empty state_dir degrades gracefully (no exception, valid markdown)
    with tempfile.TemporaryDirectory() as d2:
        md2 = briefing.build_briefing(d2)
        check("empty has header", md2.startswith("# nonya"), True)
        check("empty no-intervention note", "No interventions" in md2, True)
        check("empty verdict safe", "nonya:" in briefing.top_verdict(d2), True)

    # secret redaction: a leaked key in a reason must never reach the output
    with tempfile.TemporaryDirectory() as d3:
        append_ledger(d3, ts=5, session="s:x", event="inject",
                      reason="env had " + ("sk-" + "a" * 32) + " leaked")
        md3 = briefing.build_briefing(d3)
        check("secret redacted out", ("sk-" + "a" * 32) not in md3, True)
        check("redaction marker present", "[REDACTED]" in md3, True)

    # all-shipped verdict
    with tempfile.TemporaryDirectory() as d4:
        append_ledger(d4, ts=1, session="s:ok", event="verify",
                      reason="tests", outcome="pass")
        append_ledger(d4, ts=2, session="s:ok", event="done", reason="finished")
        check("all-shipped verdict", "shipped and verified" in briefing.top_verdict(d4), True)

    print("ALL PASS" if _fail == 0 else "SOME FAILED")
    sys.exit(_fail)


if __name__ == "__main__":
    main()
