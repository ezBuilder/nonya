#!/usr/bin/env python3
"""End-to-end verification of the Correctness Supervisor under EVERY condition.

Drives the real nonya.loop.run() through each scenario with a fake backend that
records exactly what would be typed, asserting the correct decision; plus a REAL
tmux-pane injection so the actual keystroke path is proven, not mocked.

    python3 tests/test_e2e_supervisor.py
"""
import json
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)

from nonya import loop, ledger, status  # noqa: E402
from nonya.backends import tmux  # noqa: E402
from nonya.policy import Config  # noqa: E402

_fail = 0


def check(label, cond, detail=""):
    global _fail
    if cond:
        print("ok    %-40s %s" % (label, detail))
    else:
        print("FAIL  %-40s %s" % (label, detail))
        _fail = 1


def write_jsonl(records):
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")
    return path


# --- fixtures (claude engine) for each 4-state + special cases ---
def f_done(claim="All tests pass and the auth bug is fixed. Everything is done."):
    return [{"type": "user", "message": {"role": "user", "content": "fix auth"}},
            {"type": "assistant", "message": {"role": "assistant", "stop_reason": "end_turn",
             "content": [{"type": "text", "text": claim}]}}]

def f_done_noclaim():
    return [{"type": "user", "message": {"role": "user", "content": "hi"}},
            {"type": "assistant", "message": {"role": "assistant", "stop_reason": "end_turn",
             "content": [{"type": "text", "text": "Here is the summary of the code."}]}}]

def f_error():
    return [{"type": "user", "message": {"role": "user", "content": "go"}},
            {"isApiErrorMessage": True, "error": "internal error", "apiErrorStatus": 500}]

def f_ratelimit():
    return [{"type": "user", "message": {"role": "user", "content": "go"}},
            {"error": "rate_limit", "apiErrorStatus": 429}]

def f_waiting():
    return [{"type": "user", "message": {"role": "user", "content": "deploy?"}},
            {"type": "assistant", "message": {"role": "assistant", "stop_reason": "end_turn",
             "content": [{"type": "text", "text": "Which environment should I deploy to, staging or prod?"}]}}]

def f_looping():
    recs = [{"type": "user", "message": {"role": "user", "content": "fix it"}}]
    for _ in range(5):
        recs.append({"type": "assistant", "message": {"role": "assistant", "stop_reason": "tool_use",
                     "content": [{"type": "tool_use", "name": "Edit",
                                  "input": {"file_path": "auth.py", "old_string": "x", "new_string": "y"}}]}})
    return recs


class FakeBackend:
    name = "fake"
    def __init__(self, user_idle=-1.0, gate="ok"):
        self.injects = []
        self._uidle = user_idle
        self._gate = gate
    def window_gate(self, proc): return self._gate
    def confirm_state(self, proc): return "inconclusive"
    def have_accessibility(self): return True
    def user_idle_seconds(self): return self._uidle
    def inject(self, proc, text, send_key="return", allow_raise=False): self.injects.append((proc, text)); return True


def drive(records, *, mode="on-error", check_cmd="", verify=False, budget=None,
          user_idle=-1.0, require_user_idle=0, gate="ok", give_up=9, stuck_after=2,
          max_iter=4, dry_run=False, tmux_target=""):
    sd = tempfile.mkdtemp()
    if budget is not None:
        with open(os.path.join(sd, "budget.json"), "w") as fh:
            json.dump(budget, fh)
    fixture = write_jsonl(records)
    esc = {"n": 0}
    saved = (loop.escalate, loop.notify, loop.log)
    loop.escalate = lambda *a, **k: esc.__setitem__("n", esc["n"] + 1)
    loop.notify = lambda *a, **k: None
    loop.log = lambda *a, **k: None
    cfg = Config(target="claude", engine="claude", app="Claude", mode=mode,
                 idle=0, grace=0, poll=0, stuck_after=stuck_after, give_up_after=give_up,
                 escalate_cooldown=0, max_iterations=max_iter, persona=False, impact=False,
                 require_user_idle=require_user_idle, verify=verify, check_cmd=check_cmd,
                 project_dir=sd, state_dir=sd, transcript=fixture, dry_run=dry_run,
                 tmux_target=tmux_target, is_app=(tmux_target == ""))
    be = FakeBackend(user_idle=user_idle, gate=gate)
    try:
        rc = loop.run(cfg, be)
    finally:
        loop.escalate, loop.notify, loop.log = saved
    return {"rc": rc, "injects": be.injects, "status": status.read(sd).get("status"),
            "ledger": ledger.read(sd), "esc": esc["n"], "sd": sd}


# ============ scenarios ============
PASS_CMD = "sh -c 'exit 0'"
FAIL_CMD = "sh -c 'echo \"3 failed in test_auth.py\"; exit 1'"

# 1. done + verify PASS -> accept, NO inject
r = drive(f_done(), verify=True, check_cmd=PASS_CMD, max_iter=2)
check("1 done+verifyPASS: no inject", r["injects"] == [], "injects=%d" % len(r["injects"]))
check("1 done+verifyPASS: status done", r["status"] == "done", r["status"])

# 2. done + verify FAIL -> corrective naming the failure injected
r = drive(f_done(), verify=True, check_cmd=FAIL_CMD, max_iter=2)
txt = r["injects"][0][1] if r["injects"] else ""
check("2 done+verifyFAIL: injected", len(r["injects"]) >= 1, "n=%d" % len(r["injects"]))
check("2 done+verifyFAIL: names failure", "failed in test_auth.py" in txt or "verification failed" in txt, txt[:60])

# 3. stuck(error) -> inject (generic, no claim)
r = drive(f_error(), max_iter=2)
check("3 stuck/error: injected", len(r["injects"]) >= 1, "n=%d" % len(r["injects"]))

# 4. waiting(question) -> NEVER inject, escalate, status waiting
r = drive(f_waiting(), max_iter=2)
check("4 waiting: NO inject", r["injects"] == [], "n=%d" % len(r["injects"]))
check("4 waiting: status waiting", r["status"] == "waiting", r["status"])
check("4 waiting: escalated", r["esc"] >= 1, "esc=%d" % r["esc"])

# 5. looping -> NEVER inject, escalate, status looping
r = drive(f_looping(), max_iter=2)
check("5 looping: NO inject", r["injects"] == [], "n=%d" % len(r["injects"]))
check("5 looping: status looping", r["status"] == "looping", r["status"])

# 6. rate-limited -> NO nudge, status rate-limited
r = drive(f_ratelimit(), max_iter=2)
check("6 rate-limited: NO inject", r["injects"] == [], "n=%d" % len(r["injects"]))
check("6 rate-limited: status", r["status"] == "rate-limited", r["status"])

# 7. user ACTIVE (mouse idle 2s < 12) -> NO inject (wait)
r = drive(f_error(), require_user_idle=12, user_idle=2.0, max_iter=3)
check("7 user-active: NO inject", r["injects"] == [], "n=%d" % len(r["injects"]))
# 7b user AWAY (idle 30 >= 12) -> inject
r = drive(f_error(), require_user_idle=12, user_idle=30.0, max_iter=2)
check("7b user-away: inject", len(r["injects"]) >= 1, "n=%d" % len(r["injects"]))

# 8. alert-only budget -> NEVER inject, escalate
r = drive(f_error(), budget={"auto_inject": False}, max_iter=3)
check("8 alert-only: NO inject", r["injects"] == [], "n=%d" % len(r["injects"]))
check("8 alert-only: escalated", r["esc"] >= 1, "esc=%d" % r["esc"])

# 9. panic word in transcript -> immediate stop, NO inject
r = drive(f_done(claim="STOPNOW please halt"), budget={"auto_inject": True, "panic_word": "STOPNOW"}, max_iter=5)
check("9 panic: rc stop(3)", r["rc"] == 3, "rc=%s" % r["rc"])
check("9 panic: status stopped", r["status"] == "stopped", r["status"])
check("9 panic: NO inject", r["injects"] == [], "n=%d" % len(r["injects"]))

# 10. give-up after N stuck nudges -> stop, bounded injects, ledger gave_up
r = drive(f_error(), give_up=3, stuck_after=2, max_iter=20)
check("10 give-up: rc stop(3)", r["rc"] == 3, "rc=%s" % r["rc"])
check("10 give-up: injects bounded", len(r["injects"]) == 3, "n=%d" % len(r["injects"]))
check("10 give-up: ledger gave_up", any(e.get("outcome") == "gave_up" for e in r["ledger"]), "")

# 11. multi-window gate -> alert-only, NO inject (no misfire)
r = drive(f_error(), gate="multi-window:3", max_iter=3)
check("11 multi-window: NO inject", r["injects"] == [], "n=%d" % len(r["injects"]))

# 12. done WITHOUT a claim + verify fail -> generic nudge (safe), still injects something
r = drive(f_done_noclaim(), verify=True, check_cmd=FAIL_CMD, max_iter=2)
check("12 done-noclaim+fail: generic inject", len(r["injects"]) >= 1, "n=%d" % len(r["injects"]))

# 13. ledger chain integrity after a full stuck run
r = drive(f_error(), give_up=3, max_iter=20)
check("13 ledger chain valid", ledger.verify_chain(r["sd"]), "entries=%d" % len(r["ledger"]))

# 14. REAL tmux injection (not mocked) — proves the actual keystroke path
if tmux.available():
    tmux._run = None
    import subprocess
    subprocess.run(["tmux", "kill-session", "-t", "nonyae2e"], capture_output=True)
    subprocess.run(["tmux", "new-session", "-d", "-s", "nonyae2e", "-x", "120", "-y", "30"], capture_output=True)
    pane = subprocess.run(["tmux", "list-panes", "-t", "nonyae2e", "-F", "#{pane_id}"],
                          capture_output=True, text=True).stdout.strip().splitlines()[0]
    r = drive(f_error(), tmux_target=pane, give_up=2, max_iter=3)
    time.sleep(0.4)
    cap = subprocess.run(["tmux", "capture-pane", "-t", pane, "-p"], capture_output=True, text=True).stdout
    subprocess.run(["tmux", "kill-session", "-t", "nonyae2e"], capture_output=True)
    check("14 REAL tmux: nudge keystrokes landed", "계속" in cap or "진행" in cap, repr(cap[-60:]))
else:
    print("skip  14 REAL tmux (tmux not installed)")

print("\nALL PASS" if _fail == 0 else "\nSOME FAILED")
sys.exit(_fail)
