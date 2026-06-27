#!/usr/bin/env python3
"""Unit tests for nonya — classifier (claude/codex/antigravity), policy gate,
and tmux pane gate. No apps, no keystrokes. Plain asserts (no pytest needed).

    python3 tests/test_classify.py
"""
import os
import sqlite3
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)

from nonya import detect, state  # noqa: E402
from nonya.policy import Config  # noqa: E402
from nonya.backends import tmux  # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures")
_fail = 0


def check(label, got, want):
    global _fail
    if got == want:
        print("ok    %-30s -> %s" % (label, got))
    else:
        print("FAIL  %-30s -> %s (want %s)" % (label, got, want))
        _fail = 1


# --- classifier (ports tests/run.sh) ---
def classify_fixture(engine, name):
    return detect.classify(engine, os.path.join(FIX, name))


check("claude_complete", classify_fixture("claude", "claude_complete.jsonl"), state.COMPLETED)
check("claude_tool", classify_fixture("claude", "claude_tool.jsonl"), state.TOOL_PENDING)
check("claude_error", classify_fixture("claude", "claude_error.jsonl"), state.ERROR)
check("claude_ratelimit", classify_fixture("claude", "claude_ratelimit.jsonl"), state.RATE_LIMIT)
check("codex_complete", classify_fixture("codex", "codex_complete.jsonl"), state.COMPLETED)
check("codex_stalled", classify_fixture("codex", "codex_stalled.jsonl"), state.STALLED)
check("codex_ratelimit", classify_fixture("codex", "codex_ratelimit.jsonl"), state.RATE_LIMIT)
check("codex_busy", classify_fixture("codex", "codex_busy.jsonl"), state.TOOL_PENDING)

# --- antigravity SQLite (new path) ---
def _make_ag_db(err: bool):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE steps (idx integer, status integer, error_details blob, "
                "step_payload blob, PRIMARY KEY (idx))")
    con.execute("INSERT INTO steps VALUES (0, 0, NULL, ?)", (b"hello",))
    con.execute("INSERT INTO steps VALUES (1, ?, ?, ?)",
                (2, (b"boom" if err else None), b"world"))
    con.commit()
    con.close()
    return path


_ag_err = _make_ag_db(err=True)
_ag_ok = _make_ag_db(err=False)
try:
    check("antigravity_error", detect.classify("antigravity", _ag_err), state.ERROR)
    check("antigravity_idle", detect.classify("antigravity", _ag_ok), state.IDLE_WAIT)
    check("antigravity_has_done", detect.has_done("antigravity", _ag_err, "world"), True)
finally:
    os.remove(_ag_err)
    os.remove(_ag_ok)

# --- policy gate ---
on_err = Config(mode="on-error", hang_cap=1800)
check("actionable ERROR (on-error)", on_err.actionable(state.ERROR, 0), True)
check("actionable COMPLETED (on-error)", on_err.actionable(state.COMPLETED, 0), False)
check("actionable TOOL_PENDING<cap", on_err.actionable(state.TOOL_PENDING, 100), False)
check("actionable TOOL_PENDING>cap", on_err.actionable(state.TOOL_PENDING, 2000), True)
auto = Config(mode="auto")
check("actionable IDLE_WAIT (auto)", auto.actionable(state.IDLE_WAIT, 0), True)

# --- sentinel: assistant standalone line vs inline nudge echo ---
def _write_jsonl(records):
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        import json as _json
        for r in records:
            fh.write(_json.dumps(r) + "\n")
    return path


NUDGE = "계속 진행해. 끝났고 검증됐으면 <<DONE>> 한 줄만 출력해."
# nudge echoed as a user message -> sentinel is INLINE -> must NOT count as done
_p_inline = _write_jsonl([
    {"type": "user", "message": {"role": "user", "content": NUDGE}},
    {"type": "assistant", "message": {"role": "assistant",
     "content": [{"type": "text", "text": "작업 계속 진행 중입니다."}]}},
])
# agent prints the sentinel on its OWN line -> must count as done
_p_done = _write_jsonl([
    {"type": "user", "message": {"role": "user", "content": NUDGE}},
    {"type": "assistant", "message": {"role": "assistant",
     "content": [{"type": "text", "text": "전부 끝났고 검증했습니다.\n<<DONE>>"}]}},
])
try:
    check("sentinel inline-nudge != done", detect.has_done("claude", _p_inline, "<<DONE>>"), False)
    check("sentinel standalone == done", detect.has_done("claude", _p_done, "<<DONE>>"), True)
finally:
    os.remove(_p_inline)
    os.remove(_p_done)

# --- tmux gate (only when tmux present) ---
if tmux.available():
    check("tmux gate fake pane", tmux.gate("nope:99.99"), "pane-not-found")
else:
    print("skip  tmux gate (tmux not installed)")

# --- status feed (M2 pet reads this) ---
from nonya import status  # noqa: E402
_sd = tempfile.mkdtemp()
status.write(_sd, status="scolding", character="cat", nudges=2, scold="야! 일해!")
_rt = status.read(_sd)
check("status round-trip status", _rt.get("status"), "scolding")
check("status round-trip unicode", _rt.get("scold"), "야! 일해!")
check("status has ts", isinstance(_rt.get("ts"), int), True)

# --- detection regressions: deep markers + resumption window ---
import json as _json  # noqa: E402
# Codex: task_started 149 lines from EOF, no task_complete -> STALLED (was IDLE_WAIT under 80-tail)
_codex_deep = _write_jsonl([{"payload": {"type": "task_started"}}]
                           + [{"payload": {"type": "reasoning"}} for _ in range(149)])
# Claude: end_turn then a newer queued prompt -> pending, must NOT read as COMPLETED
_claude_resume = _write_jsonl([
    {"type": "user", "message": {"role": "user", "content": "hi"}},
    {"type": "assistant", "message": {"role": "assistant", "stop_reason": "end_turn",
     "content": [{"type": "text", "text": "done"}]}},
    {"type": "queue-operation", "message": {}},
])
try:
    check("codex deep task_started", detect.classify("codex", _codex_deep), state.STALLED)
    check("claude resumption != done", detect.classify("claude", _claude_resume), state.TOOL_PENDING)
finally:
    os.remove(_codex_deep)
    os.remove(_claude_resume)

# --- tmux find_pane subtree matcher (direct/deep/miss/cycle-safe) ---
_tbl = {100: (1, "login -fp user"), 101: (100, "node"), 102: (101, "claude --x"), 200: (1, "vim CLAUDE.md")}
check("subtree direct", tmux._subtree_has(102, "claude", _tbl), True)
check("subtree deep", tmux._subtree_has(100, "claude", _tbl), True)
check("subtree miss", tmux._subtree_has(200, "codex", _tbl), False)
check("subtree cycle-safe", tmux._subtree_has(1, "zzz", {1: (2, "a"), 2: (1, "b")}), False)

# --- macOS window gate branches (monkeypatched; no real osascript) ---
if sys.platform == "darwin":
    from nonya.backends.macos import MacBackend  # noqa: E402
    _mb = MacBackend()
    _mb.have_accessibility = lambda: True
    for _n, _want in [(1, "ok"), (0, "no-ax-window"), (-1, "not-running"), (3, "multi-window:3")]:
        _mb._window_count = (lambda n: (lambda proc: n))(_n)
        check("window_gate n=%d" % _n, _mb.window_gate("X"), _want)
    _mb.have_accessibility = lambda: False
    check("window_gate no-accessibility", _mb.window_gate("X"), "no-accessibility")
else:
    print("skip  window_gate (not macOS)")

# --- loop hardening: give-up stops injection; escalate is throttled (no flood) ---
from nonya import loop as _loop  # noqa: E402


class _FakeBackend:
    name = "fake"
    def __init__(self, user_idle=-1.0): self.injects = 0; self._uidle = user_idle
    def window_gate(self, proc): return "ok"
    def confirm_state(self, proc): return "inconclusive"
    def have_accessibility(self): return True
    def user_idle_seconds(self): return self._uidle
    def inject(self, proc, text, send_key="return", allow_raise=False): self.injects += 1; return True


def _run_loop(give_up_after, stuck_after, max_iter, cooldown=600, require_user_idle=0, user_idle=-1.0, app="X"):
    esc = {"n": 0}
    saved = (_loop.escalate, _loop.notify, _loop.log)
    _loop.escalate = lambda *a, **k: esc.__setitem__("n", esc["n"] + 1)
    _loop.notify = lambda *a, **k: None
    _loop.log = lambda *a, **k: None
    sd = tempfile.mkdtemp()
    cfg = Config(target="claude", engine="claude", app=app, mode="on-error",
                 idle=0, grace=0, poll=0, stuck_after=stuck_after, give_up_after=give_up_after,
                 escalate_cooldown=cooldown, max_iterations=max_iter, persona=False, impact=False,
                 require_user_idle=require_user_idle,
                 state_dir=sd, transcript=os.path.join(FIX, "claude_error.jsonl"))
    be = _FakeBackend(user_idle=user_idle)
    try:
        rc = _loop.run(cfg, be)
    finally:
        _loop.escalate, _loop.notify, _loop.log = saved
    return rc, be.injects, esc["n"], status.read(sd).get("status")


_rc, _inj, _esc, _st = _run_loop(give_up_after=3, stuck_after=2, max_iter=20)
check("loop give-up rc", _rc, 3)
check("loop give-up inject bounded", _inj, 3)
check("loop give-up status stopped", _st, "stopped")
_, _, _esc2, _ = _run_loop(give_up_after=100, stuck_after=2, max_iter=6)
check("loop escalate throttled", _esc2, 1)
# normal mode: user actively at keyboard (idle 2s < required 12s) -> never inject
_, _inj_active, _, _ = _run_loop(give_up_after=100, stuck_after=2, max_iter=4,
                                 require_user_idle=12, user_idle=2.0)
check("user-active blocks inject", _inj_active, 0)
# user stepped away (idle 30s >= 12s) -> injects normally
_, _inj_away, _, _ = _run_loop(give_up_after=100, stuck_after=2, max_iter=3,
                               require_user_idle=12, user_idle=30.0)
check("user-idle allows inject", _inj_away > 0, True)
os.environ.pop("NONYA_ALLOW_REAL_APP_INJECT", None)
_, _inj_protected, _, _ = _run_loop(give_up_after=100, stuck_after=2, max_iter=3, app="Claude")
check("real app protected blocks inject", _inj_protected, 0)
os.environ["NONYA_ALLOW_REAL_APP_INJECT"] = "1"
try:
    _, _inj_real_allowed, _, _ = _run_loop(give_up_after=100, stuck_after=2, max_iter=3, app="Claude")
    check("real app opt-in allows inject", _inj_real_allowed > 0, True)
finally:
    os.environ.pop("NONYA_ALLOW_REAL_APP_INJECT", None)

print("ALL PASS" if _fail == 0 else "SOME FAILED")
sys.exit(_fail)
