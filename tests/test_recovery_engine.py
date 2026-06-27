#!/usr/bin/env python3
"""Exact Resume & Recovery engine — unit + live tests for the deterministic core:
session-id extraction, resume-command construction (FR-420/421), tmux respawn/pane_dead
(FR-422/502), the kill→relaunch→resume escalation, the wrong-project safety gate
(FR-204), and submission idempotency (FR-600/601, AC-012). Plain asserts (no pytest).

    python3 tests/test_recovery_engine.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)

os.environ["NONYA_NO_GUI_INJECT"] = ""          # we test the ambiguity gate explicitly below
os.environ.pop("NONYA_AX_HELPER", None)          # don't shell out to a Swift helper in tests
os.environ["NONYA_ALLOW_REAL_APP_INJECT"] = "1"  # this suite exercises the real-app inject path (opt in)
# ISOLATION: notify/escalate/ledger resolve their dir from NONYA_STATE — pin it to a throwaway so
# this test NEVER writes notifications/ledger into the real ~/.local/state/nonya (that pollution
# surfaced as bogus "proj:sid" notifications in the menu-bar app).
os.environ["NONYA_STATE"] = tempfile.mkdtemp(prefix="nonya-test-state-")

from nonya import detect, recover, scan, supervise   # noqa: E402
from nonya.backends import tmux                        # noqa: E402
from nonya.policy import Config                         # noqa: E402

_fail = 0


def check(label, cond, detail=""):
    global _fail
    if cond:
        print("ok    %-46s %s" % (label, detail))
    else:
        print("FAIL  %-46s %s" % (label, detail))
        _fail = 1


def _claude_tx(sid="11111111-2222-3333-4444-555555555555", err=True):
    d = tempfile.mkdtemp()
    p = os.path.join(d, sid + ".jsonl")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "user", "cwd": d, "message": {"role": "user", "content": "go"}}) + "\n")
        if err:
            fh.write(json.dumps({"isApiErrorMessage": True, "error": "overloaded_error: busy"}) + "\n")
    return d, p


# ---- detect.session_id -----------------------------------------------------
_d, _p = _claude_tx()
check("session_id claude = filename stem", detect.session_id("claude", _p) == "11111111-2222-3333-4444-555555555555")

_cdir = tempfile.mkdtemp()
_cx = os.path.join(_cdir, "rollout-2026-06-23T01-02-03-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.jsonl")
open(_cx, "w").write(json.dumps({"type": "session_meta", "payload": {"id": "CDX-META-9", "cwd": _cdir}}) + "\n")
check("session_id codex from session_meta", detect.session_id("codex", _cx) == "CDX-META-9")
open(_cx, "w").write(json.dumps({"type": "other"}) + "\n")
check("session_id codex falls back to filename uuid",
      detect.session_id("codex", _cx) == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")

# ---- transcript_fingerprint ------------------------------------------------
fp1 = detect.transcript_fingerprint(_p)
with open(_p, "a", encoding="utf-8") as fh:
    fh.write(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": "more"}}) + "\n")
fp2 = detect.transcript_fingerprint(_p)
check("fingerprint is 16 hex", len(fp1) == 16 and all(c in "0123456789abcdef" for c in fp1))
check("fingerprint changes when transcript advances", fp1 != fp2, "%s -> %s" % (fp1, fp2))

# ---- recover.resume_cmd (FR-420/421) ---------------------------------------
check("resume claude interactive", recover.resume_cmd("claude", "S") == ["claude", "--resume", "S"])
check("resume codex interactive", recover.resume_cmd("codex", "S") == ["codex", "resume", "S"])
check("resume claude noninteractive",
      recover.resume_cmd("claude", "S", noninteractive=True, nudge="일해!")
      == ["claude", "-p", "--resume", "S", "일해!", "--output-format", "stream-json"])
check("resume codex noninteractive",
      recover.resume_cmd("codex", "S", noninteractive=True, nudge="일해!")
      == ["codex", "exec", "resume", "S", "일해!", "--json"])
check("resume no id -> [] (never --last)", recover.resume_cmd("claude", "") == [])
check("resume no id codex -> []", recover.resume_cmd("codex", "  ") == [])

# ---- submission idempotency (FR-601, AC-012) -------------------------------
_sd = tempfile.mkdtemp()
_fp = recover.submission_fingerprint("claude", "/proj", "SID", "일해!", "stuck", 0, bucket=1)
check("not submitted initially", recover.recently_submitted(_sd, _fp, 300) is False)
recover.mark_submitted(_sd, _fp)
check("submitted within window", recover.recently_submitted(_sd, _fp, 300) is True)
check("expired window re-allows", recover.recently_submitted(_sd, _fp, 0) is False)
check("different prompt -> different fp",
      recover.submission_fingerprint("claude", "/proj", "SID", "OTHER", "stuck", 0, bucket=1) != _fp)


# ===================== scan-level behavior (monkeypatched) =====================
class FakeBackend:
    name = "fake"
    def __init__(self): self.injects = []
    def window_gate(self, p): return "ok"
    def confirm_state(self, p): return "inconclusive"
    def have_accessibility(self): return True
    def user_idle_seconds(self): return 999.0          # away -> raise allowed
    def inject(self, proc, text, send_key="return", allow_raise=False):
        self.injects.append((proc, text, allow_raise)); return True
    def inject_terminal_split(self, needle, text, send_key="return"): return False


def _sess(engine="claude", path="/x", label="proj:abc123"):
    return {"engine": engine, "path": path, "label": label,
            "state": supervise.STUCK, "idle": 300, "rate_limited": False}


# --- FR-204 wrong-project gate: ambiguous GUI recovery must NOT type ---
be = FakeBackend()
d2, p2 = _claude_tx()
s = _sess(path=p2, label="proj:abc")
check("GUI ambiguous -> refuse to type (alert)", scan._gui_recover(be, s, "일해!", ambiguous=True) is False)
check("GUI ambiguous -> zero keys sent", be.injects == [], "injects=%d" % len(be.injects))
# unambiguous + away -> it DOES inject (single conversation is provably the target)
be2 = FakeBackend()
ok_single = scan._gui_recover(be2, s, "일해!", ambiguous=False)
check("GUI single+away -> injects", ok_single is True and len(be2.injects) == 1, "injects=%d" % len(be2.injects))

# --- relaunch escalation: process dead -> respawn+resume, not nudge (FR-420/422) ---
calls = {"respawn": None, "inject": []}
_orig = (scan._pane_for, tmux.engine_alive_in, tmux.pane_dead, tmux.respawn_pane, tmux.inject, tmux.gate)
scan._pane_for = lambda s: "%9"
tmux.engine_alive_in = lambda pane, eng: False          # the engine PROCESS is dead in the pane
tmux.pane_dead = lambda pane: True                      # dead pane -> respawn-pane path
tmux.respawn_pane = lambda pane, argv, cwd="": calls.__setitem__("respawn", (pane, list(argv), cwd)) or True
tmux.inject = lambda pane, text, send="return": calls["inject"].append((pane, text)) or True
tmux.gate = lambda pane: "ok"
try:
    sd = tempfile.mkdtemp()
    cfg = Config(target="scan", engine="claude", mode="auto", nudge="일해!",
                 state_dir=sd, relaunch=True, grace=0, poll=0)
    out = scan._recover(cfg, be, _sess(path=p2, label="proj:dead"), "stuck")
    check("relaunch path taken (returns <relaunch>)", out == "<relaunch>", repr(out))
    check("respawn-pane called with claude --resume <id>",
          calls["respawn"] is not None and calls["respawn"][1][:2] == ["claude", "--resume"],
          str(calls["respawn"]))
    check("respawn resume id == filename stem",
          calls["respawn"] and calls["respawn"][1][2] == "11111111-2222-3333-4444-555555555555")
    from nonya import ledger
    relaunched = any(e.get("outcome") == "relaunched" for e in ledger.read(sd))
    check("ledger records 'relaunched'", relaunched)

    # --- idempotency: same nudge into same session within window is suppressed (AC-012) ---
    calls["inject"] = []
    tmux.engine_alive_in = lambda pane, eng: True       # engine ALIVE now -> nudge path (not relaunch)
    sd2 = tempfile.mkdtemp()
    cfg2 = Config(target="scan", engine="claude", mode="auto", nudge="일해!",
                  state_dir=sd2, relaunch=False, grace=120, poll=15)
    s_live = _sess(path=p2, label="proj:live")
    out1 = scan._recover(cfg2, be, s_live, "stuck")
    out2 = scan._recover(cfg2, be, s_live, "stuck")     # immediate retry -> must be suppressed
    check("first nudge sent", out1 == "일해!" and len(calls["inject"]) == 1, "n=%d" % len(calls["inject"]))
    check("duplicate nudge suppressed (AC-012)", out2 == "" and len(calls["inject"]) == 1, "n=%d" % len(calls["inject"]))
finally:
    (scan._pane_for, tmux.engine_alive_in, tmux.pane_dead, tmux.respawn_pane, tmux.inject, tmux.gate) = _orig


# ===================== LIVE tmux respawn (real, not mocked) =====================
if tmux.available():
    sess = "nonya-respawn-test-%d" % os.getpid()
    cwd = tempfile.mkdtemp(prefix="nonya-respawn-")
    marker = os.path.join(cwd, "marker.txt")
    subprocess.run(["tmux", "kill-session", "-t", sess], capture_output=True)
    subprocess.run(["tmux", "new-session", "-d", "-s", sess, "-x", "100", "-y", "30", "-c", cwd,
                    "sh", "-c", "sleep 1000"], check=True)
    pane = subprocess.run(["tmux", "list-panes", "-t", sess, "-F", "#{pane_id}"],
                          capture_output=True, text=True).stdout.strip().splitlines()[0]
    # the sleep process is NOT 'claude' -> engine_alive_in must be False (i.e. needs relaunch)
    check("LIVE engine_alive_in(non-engine pane)==False", tmux.engine_alive_in(pane, "claude") is False)
    # respawn that exact pane with a marker-writing command -> proves kill+relaunch in SAME pane
    ok = tmux.respawn_pane(pane, ["sh", "-c", "echo RESPAWNED_OK > %s; sleep 1000" % marker], cwd)
    time.sleep(0.8)
    pane_after = subprocess.run(["tmux", "list-panes", "-t", sess, "-F", "#{pane_id}"],
                                capture_output=True, text=True).stdout.strip().splitlines()[0]
    delivered = ""
    try:
        delivered = open(marker, encoding="utf-8").read()
    except OSError:
        pass
    subprocess.run(["tmux", "kill-session", "-t", sess], capture_output=True)
    try:
        import shutil
        shutil.rmtree(cwd)
    except OSError:
        pass
    check("LIVE respawn_pane returns True", ok is True)
    check("LIVE respawn ran new cmd in SAME pane id", pane_after == pane, "%s == %s" % (pane, pane_after))
    check("LIVE respawn killed old + started new (marker written)", "RESPAWNED_OK" in delivered, repr(delivered))
else:
    print("skip  LIVE tmux respawn (tmux not installed)")

print("\nALL PASS" if _fail == 0 else "\nSOME FAILED")
sys.exit(_fail)
