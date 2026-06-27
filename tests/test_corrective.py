#!/usr/bin/env python3
"""Unit tests for nonya.corrective — build a SPECIFIC corrective nudge from the
agent's last claim + an optional verify_summary. Plain asserts (no pytest).

    python3 tests/test_corrective.py

Covers: claimed-done + failing verify -> mentions the failure; no claim ->
returns the generic default nudge; NO model/network invoked when NONYA_MODEL_CMD
is unset; secret redaction; optional-model path is used only when configured.
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)

from nonya import corrective  # noqa: E402

_fail = 0


def check(label, got, want):
    global _fail
    if got == want:
        print("ok    %-34s -> %r" % (label, got))
    else:
        print("FAIL  %-34s -> %r (want %r)" % (label, got, want))
        _fail = 1


def _write_jsonl(records):
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")
    return path


def _asst(text):
    return {"type": "assistant", "message": {"role": "assistant",
            "content": [{"type": "text", "text": text}]}}


def _user(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


DEFAULT = "계속 이어서 진행해. <<DONE>> 한 줄만 출력해."

# Guard: ensure no model is configured for the deterministic cases.
os.environ.pop("NONYA_MODEL_CMD", None)

# --- 1. claimed done + failing verify -> names the failure ---
p_done = _write_jsonl([
    _user("fix the auth bug"),
    _asst("All tests pass now, the bug is fixed."),
])
vs_fail = {"ok": False, "detail": "pytest: 3 failed in test_auth.py"}
out = corrective.build_nudge("claude", p_done, verify_summary=vs_fail, default_nudge=DEFAULT)
check("claimed-done+fail mentions detail", "test_auth.py" in out, True)
check("claimed-done+fail not default", out != DEFAULT, True)

# --- 2. no claim -> returns the default nudge unchanged ---
p_working = _write_jsonl([
    _user("fix the auth bug"),
    _asst("Looking into the auth module, reading the handler now."),
])
out2 = corrective.build_nudge("claude", p_working, verify_summary=vs_fail, default_nudge=DEFAULT)
check("no-claim returns default", out2, DEFAULT)

# --- 3. claimed done, verify unknown (None) -> 'prove it', still specific ---
out3 = corrective.build_nudge("claude", p_done, verify_summary=None, default_nudge=DEFAULT)
check("claim+no-verify not default", out3 != DEFAULT, True)
check("claim+no-verify says run checks", "run" in out3.lower(), True)

# --- 4. claimed done + verify PASSED -> still ask to confirm, not a hard fail ---
out4 = corrective.build_nudge("claude", p_done, verify_summary={"ok": True}, default_nudge=DEFAULT)
check("claim+pass not default", out4 != DEFAULT, True)

# --- 5. NO network/model invoked when NONYA_MODEL_CMD unset ---
_spawned = {"n": 0}
import subprocess as _subprocess  # noqa: E402
_orig_run = _subprocess.run


def _trip_run(*a, **k):
    _spawned["n"] += 1
    return _orig_run(*a, **k)


_subprocess.run = _trip_run
try:
    corrective.build_nudge("claude", p_done, verify_summary=vs_fail, default_nudge=DEFAULT)
    corrective.build_nudge("claude", p_working, verify_summary=None, default_nudge=DEFAULT)
finally:
    _subprocess.run = _orig_run
check("no subprocess when model unset", _spawned["n"], 0)

# --- 6. call_local_model returns None with no command configured ---
check("call_local_model None unconfigured", corrective.call_local_model("hi", 1), None)

# --- 7. optional local model IS used when NONYA_MODEL_CMD set (deterministic echo) ---
os.environ["NONYA_MODEL_CMD"] = "cat"   # echoes the prompt back -> non-None refinement
try:
    out7 = corrective.build_nudge("claude", p_done, verify_summary=vs_fail, default_nudge=DEFAULT)
    check("model path returns non-empty", bool(out7) and out7 != DEFAULT, True)
    # model refinement still carries the concrete fact (we feed it into the prompt)
    check("model path keeps detail", "test_auth.py" in out7, True)
    # model failure (bad command) degrades gracefully to the template
    os.environ["NONYA_MODEL_CMD"] = "false"   # exits non-zero -> None -> template kept
    out7b = corrective.build_nudge("claude", p_done, verify_summary=vs_fail, default_nudge=DEFAULT)
    check("model failure degrades", "test_auth.py" in out7b, True)
finally:
    os.environ.pop("NONYA_MODEL_CMD", None)

# --- 8. secret redaction: a leaked token in the claim must not survive ---
p_secret = _write_jsonl([
    _asst("Done. Set api_key=" + ("sk-" + "a" * 32) + " and tests pass."),
])
out8 = corrective.build_nudge("claude", p_secret, verify_summary={"ok": False, "detail": "boom"},
                              default_nudge=DEFAULT)
check("secret redacted in nudge", ("sk-" + "a" * 32) not in out8, True)

# --- 9. Korean claim is detected ---
p_kr = _write_jsonl([_asst("전부 수정했고 테스트 통과했습니다.")])
out9 = corrective.build_nudge("claude", p_kr, verify_summary={"ok": False, "detail": "1 failed"},
                              default_nudge=DEFAULT)
check("korean claim detected", out9 != DEFAULT, True)

# --- 10. verify_summary as attr-object (duck-typed, not a dict) ---
class _VS:
    ok = False
    detail = "lint: 2 errors in cli.py"


out10 = corrective.build_nudge("claude", p_done, verify_summary=_VS(), default_nudge=DEFAULT)
check("attr-object verify_summary", "cli.py" in out10, True)

# --- 11. missing transcript -> safe default ---
out11 = corrective.build_nudge("claude", "/nonexistent/path.jsonl",
                               verify_summary=vs_fail, default_nudge=DEFAULT)
check("missing transcript -> default", out11, DEFAULT)

for _p in (p_done, p_working, p_secret, p_kr):
    os.remove(_p)

print("ALL PASS" if _fail == 0 else "SOME FAILED")
sys.exit(_fail)
