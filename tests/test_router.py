#!/usr/bin/env python3
"""Unit tests for nonya.router — the multi-session attention router.

Seeds a temp <state_dir>/sessions/ with several JSON files of differing
statuses (plus a legacy state.json and a corrupt file), then asserts ranking,
top(), and counts(). Plain asserts, no pytest, same house style as
tests/test_supervise.py:

    python3 tests/test_router.py
"""
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)

from nonya import router  # noqa: E402

_fail = 0


def check(label, got, want):
    global _fail
    if got == want:
        print("ok    %-40s -> %s" % (label, got))
    else:
        print("FAIL  %-40s -> %s (want %s)" % (label, got, want))
        _fail = 1


def seed_session(state_dir, sid, status, ts=None, extra=None):
    sess_dir = os.path.join(state_dir, router.SESSIONS_DIRNAME)
    os.makedirs(sess_dir, exist_ok=True)
    rec = {"status": status}
    if ts is not None:
        rec["ts"] = ts
    if extra:
        rec.update(extra)
    with open(os.path.join(sess_dir, sid + ".json"), "w", encoding="utf-8") as fh:
        json.dump(rec, fh)


# --- STATUS_RANK ordering matches the documented priority ---
order = [router.WAITING, router.LOOPING, router.STUCK,
         router.VERIFY_FAILED, router.WORKING, router.DONE]
ranks = [router.STATUS_RANK[s] for s in order]
check("STATUS_RANK strictly descending", ranks == sorted(ranks, reverse=True), True)
check("STATUS_RANK all distinct", len(set(ranks)), len(ranks))

# --- canonical_status aliases / normalization ---
check("canon needs-you -> waiting", router.canonical_status("needs-you"), router.WAITING)
check("canon SCOLDING -> looping", router.canonical_status("SCOLDING"), router.LOOPING)
check("canon watching -> working", router.canonical_status(" Watching "), router.WORKING)
check("canon completed -> done", router.canonical_status("completed"), router.DONE)
check("canon unknown stays normalized", router.canonical_status("Weird"), "weird")
check("canon non-string -> empty", router.canonical_status(None), "")


# --- seed a realistic multi-session dir ---
sd = tempfile.mkdtemp(prefix="nonya-router-")
try:
    seed_session(sd, "s-working", router.WORKING, ts=100)
    seed_session(sd, "s-stuck", router.STUCK, ts=200)
    seed_session(sd, "s-loop", "scolding", ts=300)        # alias -> looping
    seed_session(sd, "s-wait", "needs-you", ts=150)       # alias -> waiting (top)
    seed_session(sd, "s-done", router.DONE, ts=50)
    seed_session(sd, "s-verify", router.VERIFY_FAILED, ts=120)

    # legacy single-file state.json (status module FILENAME), id "_legacy"
    from nonya import status as _status
    with open(os.path.join(sd, _status.FILENAME), "w", encoding="utf-8") as fh:
        json.dump({"status": "working", "ts": 90}, fh)

    # corrupt file -> must be SKIPPED, never crash
    with open(os.path.join(sd, router.SESSIONS_DIRNAME, "s-corrupt.json"), "w",
              encoding="utf-8") as fh:
        fh.write("{ this is : not json,,,")

    # a non-json file in the sessions dir -> ignored
    with open(os.path.join(sd, router.SESSIONS_DIRNAME, "notes.txt"), "w",
              encoding="utf-8") as fh:
        fh.write("ignore me")

    # a session with no status field -> skipped
    seed_session(sd, "s-nostatus", "ignored", extra={"status": None})

    ranked = router.rank(sd)

    # top is the waiting / needs-you session
    t = router.top(sd)
    check("top is needs-you session", t["session"], "s-wait")
    check("top status is waiting", t["status"], router.WAITING)

    # full ranking order by priority (waiting > looping > stuck > verify > working > done)
    statuses_in_order = [it["status"] for it in ranked]
    expected = [router.WAITING, router.LOOPING, router.STUCK, router.VERIFY_FAILED,
                router.WORKING, router.WORKING, router.DONE]
    check("ranking order by priority", statuses_in_order, expected)

    # corrupt + non-json + no-status all skipped: 6 sessions + 1 legacy = 7
    check("corrupt/invalid skipped (count)", len(ranked), 7)

    # tie-break: two WORKING (s-working ts=100, _legacy ts=90) -> newer first
    working_sessions = [it["session"] for it in ranked if it["status"] == router.WORKING]
    check("working tie-break newer ts first", working_sessions, ["s-working", "_legacy"])

    # every item carries the 4 expected keys with an int rank
    keys_ok = all(set(it) == {"session", "status", "rank", "ts"} for it in ranked)
    check("items have exact keys", keys_ok, True)

    # --- counts ---
    c = router.counts(sd)
    check("counts total", c["total"], 7)
    check("counts needs_you", c["needs_you"], 1)
    check("counts waiting", c["waiting"], 1)
    check("counts looping", c["looping"], 1)
    check("counts stuck", c["stuck"], 1)
    check("counts verify-failed", c["verify-failed"], 1)
    check("counts working (incl legacy)", c["working"], 2)
    check("counts done", c["done"], 1)
    check("counts handled (working+done)", c["handled"], 3)

finally:
    shutil.rmtree(sd, ignore_errors=True)


# --- robustness: missing dir, empty string, nonexistent path ---
check("rank missing dir -> []", router.rank("/no/such/dir/xyz"), [])
check("top missing dir -> None", router.top("/no/such/dir/xyz"), None)
check("rank empty state_dir -> []", router.rank(""), [])
empty = router.counts("/no/such/dir/xyz")
check("counts missing dir total 0", empty["total"], 0)
check("counts missing dir needs_you 0", empty["needs_you"], 0)

# --- only a legacy state.json, no sessions/ dir ---
sd2 = tempfile.mkdtemp(prefix="nonya-router2-")
try:
    from nonya import status as _status2
    with open(os.path.join(sd2, _status2.FILENAME), "w", encoding="utf-8") as fh:
        json.dump({"status": "stuck", "ts": 5}, fh)
    check("legacy-only top", router.top(sd2)["status"], router.STUCK)
    check("legacy-only count", router.counts(sd2)["total"], 1)
finally:
    shutil.rmtree(sd2, ignore_errors=True)


print("ALL PASS" if _fail == 0 else "SOME FAILED")
sys.exit(_fail)
