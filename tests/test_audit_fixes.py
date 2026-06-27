"""Regression tests for the full-audit bug fixes (2026-06-20).

Each test pins one confirmed bug so it cannot silently come back. Plain asserts,
no pytest, run with: python3 tests/test_audit_fixes.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# ISOLATION: pin NONYA_STATE so scan._recover's notify/escalate never write into the real
# ~/.local/state/nonya (a missing-state-dir test polluted it with "proj:sid" notifications).
os.environ["NONYA_STATE"] = tempfile.mkdtemp(prefix="nonya-test-state-")
from nonya import detect, ledger, router, scan, supervise  # noqa: E402
from nonya.backends import tmux  # noqa: E402
from nonya import policy  # noqa: E402

_D = tempfile.mkdtemp()


def _w(name, lines):
    p = os.path.join(_D, name)
    with open(p, "w") as f:
        f.write("\n".join(lines) + "\n")
    return p


def test_session_id_no_match_returns_none():
    # B1: an explicit --session-id that matches nothing must NOT latch a stranger's session
    os.environ["NONYA_SESSION_ID"] = "zzz_no_such_session_999"
    try:
        assert detect.locate_transcript("claude") is None
        assert detect.locate_transcript("codex") is None
    finally:
        os.environ.pop("NONYA_SESSION_ID", None)


def test_sidechain_error_does_not_mask_main_completion():
    # B2: a subagent (isSidechain) API error must not override the main turn's end_turn
    p = _w("side.jsonl", [
        '{"type":"assistant","message":{"role":"assistant","stop_reason":"tool_use","content":[{"type":"tool_use","id":"t1","name":"Task","input":{}}]}}',
        '{"type":"assistant","isSidechain":true,"isApiErrorMessage":true,"message":{"role":"assistant","content":[{"type":"text","text":"sub 500"}]}}',
        '{"type":"user","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"t1","content":"ok"}]}}',
        '{"type":"assistant","message":{"role":"assistant","stop_reason":"end_turn","content":[{"type":"text","text":"done"}]}}',
    ])
    assert detect.classify("claude", p) == "COMPLETED"
    assert supervise.classify4("claude", p, idle=0) == supervise.DONE


def test_api_error_status_zero_is_not_an_error():
    # B7: apiErrorStatus 0 is a placeholder, not an HTTP error
    p = _w("zero.jsonl", [
        '{"apiErrorStatus":0,"type":"assistant","message":{"role":"assistant","stop_reason":"end_turn","content":[{"type":"text","text":"hi"}]}}'])
    assert detect.classify("claude", p) == "COMPLETED"


def test_error_recency_relative_to_completion():
    # B8: stale error BEFORE a later clean end_turn -> done; error AFTER completion -> stuck
    before = _w("before.jsonl", [
        '{"isApiErrorMessage":true,"error":"500"}',
        '{"type":"assistant","message":{"role":"assistant","stop_reason":"end_turn","content":[{"type":"text","text":"recovered"}]}}'])
    after = _w("after.jsonl", [
        '{"type":"assistant","message":{"role":"assistant","stop_reason":"end_turn","content":[{"type":"text","text":"x"}]}}',
        '{"isApiErrorMessage":true,"error":"500"}'])
    assert detect.classify("claude", before) == "COMPLETED"
    assert detect.classify("claude", after) == "ERROR"


def test_codex_message_text_and_waiting():
    # B3: codex assistant text lives in a response_item 'message' payload (content[].output_text)
    p = _w("cdxq.jsonl", [
        '{"type":"event_msg","payload":{"type":"task_started"}}',
        '{"type":"event_msg","payload":{"type":"agent_message","message":"exploring"}}',
        '{"type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"Should I proceed with deleting the database?"}]}}'])
    objs = detect._tail_json(p, n=detect.CODEX_SCAN_LINES)
    assert "deleting the database" in (supervise._last_assistant_text("codex", objs) or "")
    assert supervise.classify4("codex", p, idle=5) == supervise.WAITING


def test_codex_custom_tool_call_loops():
    # B9: custom_tool_call repeated K+ times is a loop (was previously invisible)
    recs = ['{"type":"response_item","payload":{"type":"custom_tool_call","name":"apply_patch","input":"same"}}'] * 5
    p = _w("cdxloop.jsonl", ["{\"payload\":{\"type\":\"task_started\"}}"] + recs)
    assert supervise.classify4("codex", p, idle=60) == supervise.LOOPING


def test_nondict_payload_no_crash():
    # B19: a list/str payload must not crash classification
    p = _w("bad.jsonl", ['{"payload":["not","dict"]}', '{"payload":"str"}'])
    detect.classify("codex", p)            # must not raise
    supervise.classify4("codex", p, idle=0)


def test_tmux_cmd_match_excludes_nonya_and_uses_basename():
    # B6: don't match nonya's own process; match the executable leaf, not a path substring
    assert tmux._cmd_matches("/usr/bin/codex serve", "codex") is True
    assert tmux._cmd_matches("node /opt/claude/cli.js", "claude") is True
    assert tmux._cmd_matches("python -m nonya --target codex", "codex") is False  # our own supervisor
    assert tmux._cmd_matches("node /some/other/app.js", "claude") is False        # bare unrelated node


def test_router_dedup_legacy_and_per_session():
    # B18: the legacy state.json mirrors one live session -> must not be double-counted
    import json
    sd = tempfile.mkdtemp()
    os.makedirs(os.path.join(sd, "sessions"))
    rec = {"status": "working", "session": "claude", "ts": 2_000_000_000}
    with open(os.path.join(sd, "state.json"), "w") as f:
        json.dump(rec, f)
    with open(os.path.join(sd, "sessions", "123.json"), "w") as f:
        json.dump(dict(rec, ts=2_000_000_001), f)
    items = router.rank(sd)
    assert len([i for i in items if i["session"] == "claude"]) == 1, items


def test_ledger_authorization_single_redaction():
    # B25: Authorization: Bearer <token> redacts ONCE, keeping the scheme word
    assert ledger.scrub("Authorization: Bearer sk-abc123def456ghi789") == "Authorization: Bearer [REDACTED]"
    assert "[REDACTED] [REDACTED]" not in ledger.scrub("Authorization: Bearer sk-abc123def456ghi789")


def test_ledger_chain_intact_after_appends():
    # B5 (sequential proxy): appends keep a verifiable hash chain
    sd = tempfile.mkdtemp()
    for i in range(8):
        ledger.append(sd, {"session": "s", "outcome": "n%d" % i, "evidence": "token=secret%d" % i})
    assert ledger.verify_chain(sd) is True
    assert "secret" not in open(os.path.join(sd, "ledger.jsonl")).read()  # KV with 'token' key scrubbed


def test_no_inject_sends_zero_keystrokes():
    # plan verify-codex step 2: --no-inject must DETECT + signal only, never type.
    from nonya import loop, policy

    class FakeBackend:
        name = "fake"
        def __init__(self): self.injects = 0
        def have_accessibility(self): return True
        def window_gate(self, proc): return "ok"
        def confirm_state(self, proc): return "inconclusive"
        def user_idle_seconds(self): return 9999.0
        def frontmost_terminal(self): return ""
        def inject(self, proc, text, send_key="return", allow_raise=False):
            self.injects += 1
            return True

    sd = tempfile.mkdtemp()
    tf = _w("ni_stuck.jsonl", [
        '{"type":"assistant","message":{"role":"assistant","stop_reason":"end_turn","content":[{"type":"text","text":"x"}]}}',
        '{"isApiErrorMessage":true,"error":"500"}'])
    cfg = policy.Config(target="claude", app="Claude", engine="claude", mode="on-error",
                        nudge="GO", sentinel="<<DONE>>", state_dir=sd, transcript=tf,
                        idle=0, grace=0, poll=0, stuck_after=1, max_iterations=1,
                        no_inject=True, persona=False, impact=False, verify=False)
    be = FakeBackend()
    loop.run(cfg, be)
    assert be.injects == 0, "no_inject still sent %d keystroke batch(es)" % be.injects


def test_in_progress_not_stuck_until_hang_cap():
    # gpt-5.5 blocker 1: a started-but-quiet turn must be WORKING (not stuck) until the hang cap,
    # so a long-but-live turn is never misfired on. A hard error is still stuck immediately.
    started = _w("started.jsonl", [
        '{"payload":{"type":"task_complete"}}', '{"payload":{"type":"task_started"}}'])
    assert supervise.classify4("codex", started, idle=0, stuck_idle_cap=1800) == supervise.WORKING
    assert supervise.classify4("codex", started, idle=1799, stuck_idle_cap=1800) == supervise.WORKING
    assert supervise.classify4("codex", started, idle=1801, stuck_idle_cap=1800) == supervise.STUCK
    err = _w("err2.jsonl", ['{"isApiErrorMessage":true,"error":"500"}'])
    assert supervise.classify4("claude", err, idle=0) == supervise.STUCK   # hard error -> immediate


def test_config_roundtrip_and_env():
    from nonya import config
    sd = tempfile.mkdtemp()
    config.save(sd, {"sound": False, "mode": "auto", "preview_secs": 5,
                     "ntfy_topic": "my-topic", "telegram_token": "T", "telegram_chat": "C"})
    c = config.load(sd)
    assert c["sound"] is False and c["mode"] == "auto" and c["preview_secs"] == 5
    config.apply_env(c)
    assert os.environ.get("NONYA_NTFY_TOPIC") == "my-topic"
    import stat
    assert stat.S_IMODE(os.stat(config.path(sd)).st_mode) == 0o600   # tokens 0600, never world-readable


def test_preview_gate_cancel_and_edit():
    import threading
    import time as _t
    from nonya import loop, policy
    sd = tempfile.mkdtemp()
    cfg = policy.Config(target="codex", app="Codex", engine="codex", mode="on-error", nudge="x", state_dir=sd)

    def touch(name, delay, body=""):
        def f():
            _t.sleep(delay)
            open(os.path.join(sd, name), "w").write(body)
        threading.Thread(target=f, daemon=True).start()

    touch("preview-cancel", 0.3)
    assert loop._preview_gate(cfg, "duck", "codex", "stuck", "T", 5)[0] is False   # cancel -> don't inject
    touch("preview-edit", 0.2, "FIXED")
    touch("preview-now", 0.4)
    assert loop._preview_gate(cfg, "duck", "codex", "stuck", "ORIG", 5) == (True, "FIXED")  # edit + inject now


def test_recently_active_counts_live_conversations():
    # GUI apps multiplex many conversations in one window; >1 live -> can't target safely.
    import time as _t
    home = tempfile.mkdtemp()
    proj = os.path.join(home, ".claude", "projects", "p")
    os.makedirs(proj)
    old_home = os.environ.get("HOME")
    os.environ["HOME"] = home
    try:
        for i in range(2):
            open(os.path.join(proj, "c%d.jsonl" % i), "w").write("{}\n")
        assert detect.recently_active("claude", within=90) == 2     # two live -> ambiguous
        old = os.path.join(proj, "c0.jsonl")
        os.utime(old, (_t.time() - 7200, _t.time() - 7200))         # age one out
        assert detect.recently_active("claude", within=90) == 1     # one live -> safe to target
    finally:
        if old_home is not None:
            os.environ["HOME"] = old_home


def test_keepgoing_requires_explicit_done_contract():
    cfg = policy.Config(mode="auto", idle=180, sentinel="<<DONE>>")

    def sess(path, engine="codex"):
        return {"engine": engine, "path": path, "label": "%s:test" % engine,
                "state": supervise.DONE, "idle": 999, "rate_limited": False}

    plain_done = _w("codex_done_no_contract.jsonl", [
        '{"type":"event_msg","payload":{"type":"user_message","message":"UI 서버도 꺼줘."}}',
        '{"type":"event_msg","payload":{"type":"task_started"}}',
        '{"type":"event_msg","payload":{"type":"agent_message","message":"전부 종료했어."}}',
        '{"type":"event_msg","payload":{"type":"task_complete"}}',
    ])
    assert scan._should_keepgoing(cfg, sess(plain_done)) is False

    contracted_done = _w("codex_done_contract.jsonl", [
        '{"type":"event_msg","payload":{"type":"user_message","message":"끝났고 검증됐으면 <<DONE>> 한 줄만."}}',
        '{"type":"event_msg","payload":{"type":"task_started"}}',
        '{"type":"event_msg","payload":{"type":"agent_message","message":"검증 끝났어."}}',
        '{"type":"event_msg","payload":{"type":"task_complete"}}',
    ])
    assert scan._should_keepgoing(cfg, sess(contracted_done)) is True

    claude_contract = _w("claude_done_contract.jsonl", [
        '{"type":"user","message":{"role":"user","content":[{"type":"text","text":"끝났고 검증됐으면 <<DONE>> 한 줄만."}]}}',
        '{"type":"assistant","message":{"role":"assistant","stop_reason":"end_turn","content":[{"type":"text","text":"검증 완료."}]}}',
    ])
    assert scan._should_keepgoing(cfg, sess(claude_contract, "claude")) is True

    claude_tool_result_noise = _w("claude_tool_result_noise.jsonl", [
        '{"type":"assistant","message":{"role":"assistant","stop_reason":"tool_use","content":[{"type":"tool_use","id":"t1","name":"Read","input":{}}]}}',
        '{"type":"user","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"t1","content":"문서에 <<DONE>> 문자열이 있음"}]}}',
        '{"type":"assistant","message":{"role":"assistant","stop_reason":"end_turn","content":[{"type":"text","text":"끝."}]}}',
    ])
    assert scan._should_keepgoing(cfg, sess(claude_tool_result_noise, "claude")) is False


def test_is_frontmost_front_vs_background_tab():
    # user spec: inject only when the problem conversation is the FRONT tab (newest write);
    # a background tab -> alert only (don't type into the wrong conversation).
    import time as _t
    home = tempfile.mkdtemp()
    proj = os.path.join(home, ".claude", "projects", "p")
    os.makedirs(proj)
    front = os.path.join(proj, "front.jsonl")
    other = os.path.join(proj, "other.jsonl")
    open(front, "w").write("{}\n")
    open(other, "w").write("{}\n")
    old_home = os.environ.get("HOME")
    os.environ["HOME"] = home
    try:
        now = _t.time()
        os.utime(front, (now, now)); os.utime(other, (now - 100, now - 100))
        assert detect.is_frontmost("claude", front) is True      # watched IS newest -> on screen -> inject
        assert detect.is_frontmost("claude", other) is False     # watched is older -> background tab -> alert
        os.utime(other, (now + 50, now + 50))                    # another conversation becomes foreground
        assert detect.is_frontmost("claude", front) is False     # now the watched one is in the background
    finally:
        if old_home is not None:
            os.environ["HOME"] = old_home


def test_scan_catches_all_sessions():
    # the core gap: a stalled/rate-limited BACKGROUND session (not the newest) must be caught.
    import json
    from nonya import scan
    home = tempfile.mkdtemp()
    old_home = os.environ.get("HOME")
    os.environ["HOME"] = home
    os.environ["NONYA_NO_OS_LANG"] = "1"
    try:
        def w(proj, sid, recs):
            d = os.path.join(home, ".claude", "projects", proj)
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, sid + ".jsonl"), "w") as f:
                f.write("\n".join(json.dumps(r) for r in recs) + "\n")
        w("-Users-me-navio", "a1", [{"apiErrorStatus": 429, "error": "rate_limit"}])
        w("-Users-me-vaela", "b2", [{"isApiErrorMessage": True, "error": "500"}])
        w("-Users-me-omni", "c3", [{"type": "assistant", "message": {"role": "assistant", "stop_reason": "end_turn", "content": [{"type": "text", "text": "x"}]}}])
        res = {s["label"]: s for s in scan.scan_once(["claude"], within=3600, stuck_idle_cap=1800)}
        assert res["navio:a1"]["rate_limited"] is True            # background rate-limit caught
        assert res["vaela:b2"]["state"] == "stuck"                # background error caught
        assert res["omni:c3"]["state"] == "done"                  # clean session not flagged
        problems = [l for l, s in res.items() if s["rate_limited"] or s["state"] in scan._PROBLEM]
        assert set(problems) == {"navio:a1", "vaela:b2"}, problems
    finally:
        if old_home is not None:
            os.environ["HOME"] = old_home


def test_codex_session_cwd_from_session_meta_head():
    # Codex carries cwd in a session_meta record at the rollout HEAD (line 1), not the tail.
    # Resolving it lets scan._pane_for pin a codex CLI session to its tmux pane by cwd
    # (and disambiguate multiple concurrent codex sessions) — previously codex cwd was always "".
    import json
    from nonya import detect
    real = tempfile.mkdtemp()                      # a dir that actually exists -> passes isdir guard
    d = tempfile.mkdtemp()
    fp = os.path.join(d, "rollout-x.jsonl")
    with open(fp, "w") as f:
        f.write(json.dumps({"type": "session_meta",
                            "payload": {"originator": "codex-tui", "source": "cli", "cwd": real}}) + "\n")
        f.write(json.dumps({"payload": {"type": "task_complete"}}) + "\n")
    assert detect.session_cwd("codex", fp) == real, detect.session_cwd("codex", fp)
    # no session_meta -> "" (no guess); nonexistent dir -> "" (isdir guard)
    fp2 = os.path.join(d, "rollout-y.jsonl")
    with open(fp2, "w") as f:
        f.write(json.dumps({"payload": {"type": "task_started"}}) + "\n")
    assert detect.session_cwd("codex", fp2) == ""
    fp3 = os.path.join(d, "rollout-z.jsonl")
    with open(fp3, "w") as f:
        f.write(json.dumps({"type": "session_meta", "payload": {"cwd": "/no/such/dir/xyz"}}) + "\n")
    assert detect.session_cwd("codex", fp3) == ""


def test_scan_keepgoing_wakes_jamsu_session():
    # "잠수": only a session with an explicit <<DONE>> completion contract should be nudged after
    # a clean end_turn without the sentinel. Plain completed/abandoned sessions are idle, not work.
    import json
    import time as _time
    from nonya import scan
    from nonya.policy import Config

    DONE = {"type": "assistant", "message": {"role": "assistant", "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "검증하고 보고할게. 이어서 진행한다."}]}}
    USER_CONTRACT = {"type": "user", "message": {"role": "user",
            "content": [{"type": "text", "text": "끝났고 검증됐으면 <<DONE>> 한 줄만."}]}}
    DONE_SENTINEL = {"type": "assistant", "message": {"role": "assistant", "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "<<DONE>>"}]}}

    class MB:                                   # mock backend: record AX terminal-split injections
        def __init__(self, ok=True): self.hits = []; self.ok = ok
        def inject_terminal_split(self, match, text, key):
            if self.ok: self.hits.append(text)
            return self.ok

    def run_one(mode, recs, backend_ok=True):
        home = tempfile.mkdtemp(); sd = tempfile.mkdtemp()
        old_home = os.environ.get("HOME"); os.environ["HOME"] = home
        os.environ["NONYA_NO_OS_LANG"] = "1"
        orig_pane = scan._pane_for
        scan._pane_for = lambda s: None         # force the AX/terminal-split path (no real tmux)
        try:
            d = os.path.join(home, ".claude", "projects", "-Users-me-navio")
            os.makedirs(d, exist_ok=True)
            fp = os.path.join(d, "a1.jsonl")
            with open(fp, "w") as f:
                f.write("\n".join(json.dumps(r) for r in recs) + "\n")
            past = _time.time() - 600
            os.utime(fp, (past, past))           # idle ~600s > gate(180)
            mb = MB(ok=backend_ok)
            cfg = Config(target="scan", engine="claude", mode=mode, nudge="GO",
                         sentinel="<<DONE>>", state_dir=sd, idle=180, poll=0, max_iterations=1)
            scan.run_scan(cfg, mb)
            notes = os.path.join(sd, "notifications.jsonl")
            body = open(notes, encoding="utf-8").read() if os.path.exists(notes) else ""
            return mb.hits, body
        finally:
            scan._pane_for = orig_pane
            if old_home is not None: os.environ["HOME"] = old_home

    assert run_one("auto", [DONE])[0] == [], "auto must not wake a plain finished session"
    assert run_one("auto", [USER_CONTRACT, DONE])[0] == ["GO"], "auto wakes a contracted <<DONE>>-less session"
    assert run_one("on-error", [USER_CONTRACT, DONE])[0] == [], "on-error must NOT wake a finished session"
    assert run_one("auto", [USER_CONTRACT, DONE_SENTINEL])[0] == [], "a real <<DONE>> is truly done -> leave it"
    hits, body = run_one("auto", [USER_CONTRACT, DONE], backend_ok=False)
    assert hits == [] and "couldn't wake" not in body and "자동으로 깨우지 못" not in body


def test_launch_requires_tmux_and_engine_on_path():
    # --launch starts an agent inside tmux for safe recovery on ANY terminal. Without tmux or the
    # engine CLI on PATH it exits 2 cleanly, never blocking on a missing tool.
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from nonya.cli import _launch
    import shutil
    if shutil.which("tmux"):
        rc = _launch("no_such_engine_xyz_abc")
        assert rc == 2, "no engine on PATH -> exit 2"
    else:
        rc = _launch("claude")
        assert rc == 2, "no tmux -> exit 2"


def test_native_split_injection_disabled_by_default():
    # SAFETY: macOS routes posted key events to a terminal's ACTIVE split, not the AXFocused one
    # we select — so auto-injecting into a background split could type into the WRONG session
    # (verified live: a marker landed in an unrelated Claude session). inject_terminal_split must
    # be OFF unless the explicit NONYA_AX_SPLIT=1 research opt-in is set.
    import sys
    if sys.platform != "darwin":
        return
    from nonya.backends import macos
    old = os.environ.pop("NONYA_AX_SPLIT", None)
    try:
        assert macos.MacBackend().inject_terminal_split("some-match", "nudge") is False
    finally:
        if old is not None:
            os.environ["NONYA_AX_SPLIT"] = old


def test_scan_gui_recover_present_focus_safe_vs_away_raise():
    # GUI desktop-app recovery (Claude/Codex .app, no tmux pane):
    #   USER PRESENT (idle low): focus-safe — type ONLY into the FRONT conversation; background = alert.
    #   USER AWAY (idle high):   RAISE the app to front and type (allow_raise=True) — overnight recovery,
    #                            nobody to disrupt — regardless of which conversation was front.
    #   0-AX-window app (no single window) -> never inject.
    from nonya import scan, detect, supervise
    from nonya.policy import Config

    class MB:
        def __init__(self, gate="ok", idle=0.0): self.injected = []; self._gate = gate; self._idle = idle
        def inject_terminal_split(self, m, t, k): return False
        def window_gate(self, proc): return self._gate
        def user_idle_seconds(self): return self._idle
        def inject(self, proc, text, key, allow_raise=False):
            self.injected.append((proc, text, allow_raise)); return True

    orig_pane = scan._pane_for; orig_front = detect.is_frontmost
    scan._pane_for = lambda s: None
    try:
        def recover(engine, frontmost, idle, gate="ok"):
            detect.is_frontmost = lambda e, p, slack=3.0: frontmost
            mb = MB(gate=gate, idle=idle)
            s = {"engine": engine, "path": "/tmp/x.jsonl", "label": "proj:sid",
                 "state": supervise.DONE, "idle": 600, "rate_limited": False}
            cfg = Config(target="scan", engine=engine, mode="auto", nudge="GO", state_dir=tempfile.mkdtemp())
            scan._recover(cfg, mb, s, "keep-going")
            return mb.injected

        big = scan.GUI_AWAY_IDLE + 5
        assert recover("claude", True, 0) == [("Claude", "GO", False)], "present+front -> focus-safe paste"
        assert recover("claude", False, 0) == [], "present+background -> alert only, never type"
        assert recover("claude", False, big) == [("Claude", "GO", True)], "AWAY -> raise app + type even if not front"
        assert recover("codex", True, big, gate="no-ax-window") == [], "0-AX-window -> cannot GUI-inject"
    finally:
        scan._pane_for = orig_pane; detect.is_frontmost = orig_front


def test_claude_cwd_from_content_handles_dash_paths():
    # B: the cwd decoder used project-dir-name.replace("-","/"), which is WRONG when the path
    # itself contains '-' (e.g. .../code-brain -> .../code/brain -> not a dir -> unmatched pane,
    # so the session could never be targeted/recovered even in tmux). Fix: read the real `cwd`
    # field from the transcript content (lossless). Verify with an actual dash-containing dir.
    import json as _json
    real = tempfile.mkdtemp(prefix="nonya-dash-test-")            # a real dir we control
    proj = os.path.join(_D, "-tmp-bogus-code-brain")             # lossy decode would give /tmp/bogus/code/brain
    os.makedirs(proj, exist_ok=True)
    tx = os.path.join(proj, "sess.jsonl")
    with open(tx, "w", encoding="utf-8") as fh:
        fh.write(_json.dumps({"type": "user", "cwd": real, "message": {"role": "user", "content": "hi"}}) + "\n")
        fh.write(_json.dumps({"type": "assistant", "message": {"role": "assistant",
                                                               "content": [{"type": "text", "text": "ok"}]}}) + "\n")
    assert detect.session_cwd("claude", tx) == real, "must read real cwd from content, not lossy dir decode"
    # sidechain-only cwd must not hijack the main-chain cwd
    tx2 = os.path.join(proj, "sess2.jsonl")
    with open(tx2, "w", encoding="utf-8") as fh:
        fh.write(_json.dumps({"isSidechain": True, "cwd": "/no/such/sub", "message": {}}) + "\n")
        fh.write(_json.dumps({"type": "user", "cwd": real, "message": {"role": "user", "content": "hi"}}) + "\n")
    assert detect.session_cwd("claude", tx2) == real, "skip sidechain cwd; use main-chain cwd"


def test_scan_status_active_idle_and_done_needs_sentinel():
    # The menu/eyes status must be HONEST: "working" only while actually producing output NOW,
    # and "done" only when <<DONE>> was really printed. Before this fix a quiet-but-not-finished
    # session showed "working" (so abandoned sessions looked busy) and any end_turn showed "done"
    # (so an actively-used session looked finished). idle time can't tell "you stepped away" from
    # "agent busy", so the honest signal is wrote-recently vs not.
    from nonya import scan

    class _Cfg:
        sentinel = "<<DONE>>"
    cfg = _Cfg()

    def disp(state, idle, path="/nope", rl=False):
        s = {"engine": "claude", "path": path, "label": "x:1",
             "state": state, "idle": idle, "rate_limited": rl}
        return scan._status_for(s, cfg)

    assert disp(supervise.WORKING, 5) == "working"                    # writing now -> working
    assert disp(supervise.WORKING, scan.ACTIVE_CAP + 1) == "idle"     # quiet -> idle, NOT "working"
    assert disp(supervise.WAITING, 999) == "waiting"
    assert disp(supervise.STUCK, 5) == "stuck"
    assert disp(supervise.LOOPING, 5) == "looping"
    assert disp(supervise.WORKING, 5, rl=True) == "rate-limited"      # rate-limit wins

    txt = lambda t: '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"%s"}]}}' % t
    no_sent = _w("st_done_nosent.jsonl", [txt("all set, finishing up")])
    sent = _w("st_done_sent.jsonl", [txt("done"), txt("<<DONE>>")])
    s_no = {"engine": "claude", "path": no_sent, "label": "x:1", "state": supervise.DONE, "idle": 300, "rate_limited": False}
    s_se = {"engine": "claude", "path": sent, "label": "x:2", "state": supervise.DONE, "idle": 300, "rate_limited": False}
    assert scan._status_for(s_no, cfg) == "idle", "bare end_turn (no <<DONE>>) is idle, not 완료"
    assert scan._status_for(s_se, cfg) == "done", "printed <<DONE>> is truly done"


def test_status_writes_pid_and_cleanup_removes_phantom():
    # Phantom "stuck" sessions in the fleet menu = a single-session run's per-pid state file that
    # lingered after the run died. Fix: write a `pid` (so the menu can drop dead-pid files) and
    # remove the per-pid file on clean exit so it never shows as a ghost.
    from nonya import status
    sd = tempfile.mkdtemp()
    status.write(sd, status="stuck", target="cli")
    pidfile = os.path.join(sd, "sessions", "%d.json" % os.getpid())
    assert os.path.exists(pidfile), "per-pid session file must be written"
    import json as _json
    rec = _json.load(open(pidfile))
    assert rec.get("pid") == os.getpid(), "must record pid so a reader can detect a dead run"
    status.cleanup(sd)
    assert not os.path.exists(pidfile), "cleanup must remove the per-pid file on exit (no phantom)"


def test_notify_never_osascripts_and_queues():
    # The "click a notification -> Script Editor opens" complaint: it was the osascript fallback.
    # notify() must NEVER spawn `osascript display notification` (that's what macOS attributes to
    # Script Editor). It queues for the menu-bar app to post natively instead. Guard both.
    import sys as _sys
    if _sys.platform != "darwin":
        return
    import subprocess as _sp
    from nonya import notify as _n
    sd = tempfile.mkdtemp()
    prev_state = os.environ.get("NONYA_STATE")        # RESTORE, don't pop — popping would leave later
    os.environ["NONYA_STATE"] = sd                    # tests writing notifications into the REAL dir
    calls = []
    orig_run, orig_popen = _sp.run, _sp.Popen
    _sp.run = lambda *a, **k: calls.append(("run", a[0] if a else k.get("args"))) or type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
    _sp.Popen = lambda *a, **k: calls.append(("popen", a[0] if a else k.get("args"))) or type("P", (), {})()
    try:
        _n.notify("nonya: stuck", "navio:a1 — please check", "Basso")
    finally:
        _sp.run, _sp.Popen = orig_run, orig_popen
        if prev_state is None:
            os.environ.pop("NONYA_STATE", None)
        else:
            os.environ["NONYA_STATE"] = prev_state
    flat = " ".join(str(c[1]) for c in calls)
    assert "osascript" not in flat, "notify must not osascript (Script Editor attribution): %s" % flat
    with open(os.path.join(sd, "notifications.jsonl"), encoding="utf-8") as f:
        assert "navio:a1" in f.read(), "notify must queue for the app to post natively"


def test_metrics_summarize_counts_safety_and_shadow():
    # Metrics must be HONEST from the ledger: count keys-actually-sent (acted), shadow would-haves,
    # and the safety invariant = ZERO injections into a WAITING/question turn. Recompute from entries.
    from nonya import metrics
    sd = tempfile.mkdtemp()
    ledger.append(sd, {"session": "a:1", "stall_class": "stuck", "outcome": "recovered",
                       "injected_text": "go on", "gates_passed": "scan"})
    ledger.append(sd, {"session": "b:2", "stall_class": "stuck", "outcome": "shadow",
                       "injected_text": "", "gates_passed": "scan-shadow"})
    ledger.append(sd, {"session": "c:3", "stall_class": "rate-limited", "outcome": "alerted",
                       "injected_text": "", "gates_passed": "scan"})
    s = metrics.summarize(sd)
    assert s["entries"] == 3
    assert s["acted"] == 1 and s["recovered"] == 1, "only the delivered one counts as acted/recovered"
    assert s["delivery_rate"] == 1.0
    assert s["shadow_would_act"] == 1
    assert s["safety_invariant_ok"] and s["waiting_injections"] == 0
    assert s["chain_intact"] is True
    assert s["by_outcome"].get("shadow") == 1 and s["by_class"].get("stuck") == 2
    # an injection into a WAITING turn must be FLAGGED as a safety violation
    ledger.append(sd, {"session": "d:4", "stall_class": "waiting", "outcome": "injected",
                       "injected_text": "do X", "gates_passed": "scan"})
    s2 = metrics.summarize(sd)
    assert s2["waiting_injections"] == 1 and s2["safety_invariant_ok"] is False, \
        "keys sent on a WAITING turn must trip the safety invariant"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print("ok", fn.__name__)
    print("audit-fixes: all %d tests passed" % len(fns))
