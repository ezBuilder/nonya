"""nonya CLI — argument parsing + dispatch. Cross-platform entry point.

  nonya --check                          # permission/tool preflight
  nonya --target claude                  # on-error mode (default), Claude app
  nonya --target codex --mode auto       # keep going until <<DONE>>
  nonya --target antigravity             # Google Antigravity (Gemini) app
  nonya --target cli --tmux %3 --engine claude   # CLI via tmux pane (Win/Mac)
  nonya --target cli --app Ghostty --file <transcript> --engine claude
  nonya --target claude --dry-run        # detect/classify/gate only, no keys
"""
from __future__ import annotations

import argparse
import os
import sys
import time

from . import __version__
from .backends import get_backend, tmux
from .loop import run
from .policy import Config

_APP_DEFAULTS = {"claude": "Claude", "codex": "Codex", "antigravity": "Antigravity"}
_PROTECTED_INJECT_TEST_APPS = {"claude", "codex", "antigravity"}


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="nonya", add_help=True,
                                description='nonya — "놀고 있냐?" session auto-recovery observer (Win+Mac)')
    p.add_argument("--version", action="version", version="nonya " + __version__)
    p.add_argument("--check", action="store_true", help="permission/tool preflight, then exit")
    p.add_argument("--target", choices=["claude", "codex", "antigravity", "cli"])
    p.add_argument("--app", default="", help="process / window-title hint (default per target)")
    p.add_argument("--engine", default="", choices=["", "claude", "codex", "antigravity"],
                   help="detection format (default = target)")
    p.add_argument("--mode", default="on-error", choices=["on-error", "auto"])
    p.add_argument("--file", default="", help="explicit transcript path")
    p.add_argument("--session-id", default="", help="target a specific session (substring of its transcript filename)")
    p.add_argument("--lang", default="", help="UI language (en|ko|ja|zh-Hans|zh-Hant|es|fr|de|pt-BR); default = OS locale")
    p.add_argument("--langs", action="store_true", help="list supported UI languages and exit")
    p.add_argument("--tmux", default="", help="tmux pane target (e.g. %%3 or sess:win.pane)")
    p.add_argument("--nudge", default="", help="text injected to continue the agent (default: localized)")
    p.add_argument("--sentinel", default="<<DONE>>")
    p.add_argument("--send-key", default="return", choices=["return", "cmd+return", "ctrl+return"])
    p.add_argument("--idle", type=int, default=180)
    p.add_argument("--grace", type=int, default=120)
    p.add_argument("--poll", type=int, default=15)
    p.add_argument("--require-user-idle", type=int, default=0,
                   help="normal mode: only inject once the USER has been idle this many seconds (0=off)")
    p.add_argument("--hang-cap", type=int, default=1800)
    p.add_argument("--max-nudges", type=int, default=100)
    p.add_argument("--max-hours", type=int, default=12)
    p.add_argument("--stuck-after", type=int, default=3)
    p.add_argument("--max-iterations", type=int, default=0,
                   help="bound poll cycles then exit (0=infinite; testing)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--no-inject", action="store_true",
                   help="detect + write state only; an external injector does the typing")
    p.add_argument("--shadow", action="store_true",
                   help="decide + RECORD what nonya WOULD do (to the ledger) but send ZERO keys — "
                        "run a while, then `nonya --metrics` to vet false-positives before trusting auto")
    p.add_argument("--relaunch", action="store_true",
                   help="when a session's PROCESS is dead/hung, kill→relaunch→resume the EXACT "
                        "session (claude --resume / codex resume / tmux respawn-pane), not just "
                        "nudge a live one. Opt-in (also via NONYA_RELAUNCH=1).")
    p.add_argument("--metrics", action="store_true",
                   help="print ledger-derived intervention stats (volume, delivery, safety invariant), then exit")
    p.add_argument("--all", action="store_true",
                   help="watch EVERY live session (multi-session monitor, alert-only) instead of one target")
    p.add_argument("--launch", default="", choices=["", "claude", "codex"],
                   help="start the agent CLI inside a fresh tmux session so nonya can recover it "
                        "SAFELY on ANY terminal (Ghostty/Terminal/iTerm) — tmux owns the PTY")
    p.add_argument("--selftest", action="store_true",
                   help="prove the full recovery loop end-to-end on a THROWAWAY tmux session "
                        "(detect stuck -> target by cwd -> inject -> verify delivery), then exit")
    p.add_argument("--inject-test", nargs="?", const="테스트니 무시하세요", default=None, metavar="TEXT",
                   help="DEMO: raise the target app (--app, default Claude) to front and type+SEND "
                   "TEXT via the real recovery inject path, then exit. Verifies live GUI injection "
                   "into your actual desktop app (real Claude/Codex require NONYA_ALLOW_REAL_APP_INJECT=1).")
    p.add_argument("--preview", type=int, default=-1,
                   help="show a cancellable N-second preview before each injection (0=off)")
    # --- Correctness Supervisor ---
    p.add_argument("--no-verify", action="store_true",
                   help="don't run the project's own check before accepting a 'done' claim")
    p.add_argument("--check-cmd", default="", help="explicit verify command (else auto-discover)")
    p.add_argument("--project-dir", default="", help="repo dir for discovering/running the check")
    p.add_argument("--model-cmd", default="", help="optional local model command for corrective text")
    p.add_argument("--briefing", action="store_true", help="print the wake-up after-action report and exit")
    p.add_argument("--router", action="store_true", help="print the multi-session attention queue (JSON) and exit")
    p.add_argument("--character", default="", choices=["", "duck", "cat", "robot"],
                   help="watcher persona (default duck)")
    p.add_argument("--no-persona", action="store_true", help="disable character + scolding")
    p.add_argument("--no-impact", action="store_true", help="disable the sound chime on nudge")
    return p


def _check(backend) -> int:
    backend.check()
    print("[%s] tmux (CLI pane injection, Win via WSL)" % ("OK " if tmux.available() else " - "))
    from . import corrective
    mk = corrective.discover_model()
    if mk:
        print("[OK ] corrective model: %s (refines the nudge text)" % mk)
    else:
        print("[ - ] corrective model: none -> deterministic nudges (works fully without a model).")
        print("       opt-in: set NONYA_MODEL_CMD=<cmd>, or NONYA_MODEL=auto to use ollama / a")
        print("       local OpenAI server (LM Studio at localhost:1234). nonya never downloads a model.")
    return 0


def _launch(engine: str) -> int:
    """Start an agent CLI inside a fresh tmux session so nonya recovers it SAFELY on ANY terminal.
    Why this exists: macOS routes synthetic key events to a terminal's ACTIVE split, so injecting
    into a background raw split (Ghostty/Terminal) can't target the right session — only tmux,
    which owns the PTY, lets send-keys hit a specific pane by id regardless of focus/active split.
    Running the agent in tmux (inside whatever terminal you like, Ghostty included) makes recovery
    deterministic and misfire-proof. Replaces this process with the attached tmux session."""
    import shutil
    if not shutil.which("tmux"):
        print("nonya launch: tmux not found. Install it (e.g. `brew install tmux`) — it is how "
              "nonya delivers recovery to any terminal safely.", file=sys.stderr)
        return 2
    cmd = shutil.which(engine)
    if not cmd:
        print("nonya launch: '%s' CLI not found on PATH." % engine, file=sys.stderr)
        return 2
    sess = "nonya-%s-%d" % (engine, os.getpid())          # unique so several can run side by side
    print("nonya: launching %s in tmux session '%s' — auto-recoverable on any terminal." % (engine, sess))
    # exec: this shell becomes the attached tmux session running the agent; detach with C-b d.
    os.execvp("tmux", ["tmux", "new-session", "-A", "-s", sess, cmd])
    return 0                                               # unreachable (execvp replaces the process)


def _inject_test(text: str, app: str) -> int:
    """DEMO of the REAL GUI injection: raise `app` to front and type+SEND `text` using the exact
    code path nonya uses to recover a stalled desktop-app session (MacBackend.inject with
    allow_raise=True). Lands in the app's focused conversation. Single-window gate still applies."""
    if app.strip().lower() in _PROTECTED_INJECT_TEST_APPS and os.environ.get("NONYA_ALLOW_REAL_APP_INJECT") != "1":
        print("inject-test: refusing to type into real %s app without NONYA_ALLOW_REAL_APP_INJECT=1." % app)
        print("  -> use read-only gate checks for Claude/Codex app verification; no keys sent.")
        return 2
    from .backends import get_backend
    b = get_backend()
    gate = getattr(b, "window_gate", lambda p: "n/a")(app)
    print("inject-test: app=%s  window_gate=%s" % (app, gate))
    if gate != "ok":
        # accurate, gate-specific guidance — NOT a generic "make it single-window".
        hint = {
            "no-accessibility": ("손쉬운 사용(Accessibility) 권한이 없습니다. 이건 창 개수 문제가 아닙니다.\n"
                                 "     → 메뉴바 노냐의 '주입 테스트' 버튼으로 실행하세요(앱에 부여된 권한을 씁니다).\n"
                                 "     → 또는 시스템 설정 > 개인정보 보호 > 손쉬운 사용에 이 터미널(또는 이 바이너리)을 추가."),
            "not-running": "%s 앱이 실행 중이 아닙니다." % app,
            "no-ax-window": "%s 앱이 AX 창을 0개 노출 — 이 빌드는 주입 불가(알림만)." % app,
        }.get(gate, "")
        if gate.startswith("multi-window"):
            hint = ("%s 앱이 창 %s개 — 여러 '윈도우'는 어느 창이 어느 세션인지 매핑 불가라 안전상 거부합니다.\n"
                    "     (한 윈도우 안의 탭/화면분할은 OK. 윈도우 자체를 여러 개 띄운 경우만 막힙니다.)"
                    % (app, gate.split(":")[-1]))
        print("  -> %s" % (hint or "게이트가 ok가 아닙니다(%s)." % gate))
        return 2
    ok = b.inject(app, text, "return", allow_raise=True)
    print("  -> inject(allow_raise=True) = %s  | %s" % (ok, ("입력+전송: %r" % text) if ok else "전송 안 됨"))
    return 0 if ok else 2


def _selftest() -> int:
    """Prove the WHOLE recovery loop end-to-end on a throwaway session the user can watch:
    spin up a tmux pane, plant a 'stuck' (API-error) transcript whose cwd points at that pane,
    run the REAL recovery path, and confirm the resume nudge was actually DELIVERED into the
    pane. Touches nothing real (unique throwaway names). Exit 0 = nonya recovers; 2 = it didn't."""
    import json as _json
    import shutil as _sh
    import subprocess as _sp
    import tempfile as _tf
    from . import i18n
    _t = i18n.t
    if not _sh.which("tmux"):
        print(_t("selftest.notmux"))
        return 2
    from . import detect, scan, supervise
    from .backends import get_backend
    from .policy import Config

    sess = "nonya-selftest-%d" % os.getpid()
    cwd = _tf.mkdtemp(prefix="nonya-selftest-")
    state_dir = _tf.mkdtemp(prefix="nonya-selftest-state-")
    marker = "NONYA_SELFTEST_OK_%d" % os.getpid()
    tx = os.path.join(cwd, "transcript.jsonl")
    got = os.path.join(cwd, "got.txt")
    ok = False
    try:
        with open(tx, "w", encoding="utf-8") as fh:                 # stuck transcript, cwd -> our pane
            fh.write(_json.dumps({"type": "user", "cwd": cwd,
                                  "message": {"role": "user", "content": "go"}}) + "\n")
            fh.write(_json.dumps({"isApiErrorMessage": True, "error": "overloaded_error: busy"}) + "\n")
        s4 = supervise.classify4("claude", tx, idle=0)
        print("1) %-22s classify4=%-7s %s" % (_t("selftest.detect"), s4, "OK" if s4 == supervise.STUCK else "FAIL"))
        _sp.run(["tmux", "kill-session", "-t", sess], capture_output=True)
        _sp.run(["tmux", "new-session", "-d", "-s", sess, "-x", "100", "-y", "30", "-c", cwd], check=True)
        reader = 'IFS= read -r L; printf %s "$L" > ' + got    # concat (avoid %s/%-format collision)
        _sp.run(["tmux", "send-keys", "-t", sess, "-l", "--", reader], check=True)
        _sp.run(["tmux", "send-keys", "-t", sess, "C-m"], check=True)
        time.sleep(0.6)
        s = {"engine": "claude", "path": tx, "label": "selftest:x",
             "state": supervise.STUCK, "idle": 0, "rate_limited": False}
        cfg = Config(target="scan", engine="claude", mode="auto", nudge=marker, state_dir=state_dir)
        scan._recover(cfg, get_backend(), s, "selftest")            # the REAL recovery path
        time.sleep(1.0)
        try:
            with open(got, encoding="utf-8") as fh:
                delivered = fh.read()
        except OSError:
            delivered = ""
        ok = marker in delivered
        print("2) %-22s %s" % (_t("selftest.target"), "OK" if ok else "FAIL"))
        print("3) %-22s %s" % (_t("selftest.inject"), "OK" if ok else "FAIL"))
    finally:
        _sp.run(["tmux", "kill-session", "-t", sess], capture_output=True)
        for d in (cwd, state_dir):
            try:
                _sh.rmtree(d)
            except OSError:
                pass
    print()
    print(_t("selftest.pass") if ok else _t("selftest.fail"))
    print(_t("selftest.note"))
    return 0 if ok else 2


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)

    from . import i18n
    if args.lang:
        os.environ["NONYA_LANG"] = args.lang
    if args.langs:
        print("supported UI languages (NONYA_LANG / --lang):")
        for code in i18n.SUPPORTED:
            print("  %-8s %s" % (code, "(default OS locale)" if code == i18n.resolve_lang() else ""))
        return 0

    if args.launch:                          # start an agent inside tmux -> universally recoverable
        return _launch(args.launch)

    if args.selftest:                        # prove the recovery loop end-to-end, then exit
        return _selftest()

    if args.inject_test is not None:         # DEMO: raise app + type+send the text, then exit
        return _inject_test(args.inject_test, args.app or "Claude")

    state_dir = os.environ.get("NONYA_STATE", os.path.expanduser("~/.local/state/nonya"))
    os.makedirs(state_dir, exist_ok=True)
    backend = get_backend()

    if args.check:
        return _check(backend)

    if args.briefing:                       # wake-up after-action report, then exit
        from . import briefing
        print(briefing.build_briefing(state_dir))
        return 0

    if args.router:                         # multi-session attention queue (for the menu-bar UI)
        import json as _json
        from . import router
        print(_json.dumps({"top": router.top(state_dir), "counts": router.counts(state_dir),
                           "queue": router.rank(state_dir)}, ensure_ascii=False))
        return 0

    if args.metrics:                        # ledger-derived intervention stats, then exit
        from . import metrics
        print(metrics.render(state_dir))
        return 0

    if args.model_cmd:
        os.environ["NONYA_MODEL_CMD"] = args.model_cmd
    if args.session_id:
        os.environ["NONYA_SESSION_ID"] = args.session_id
    nudge_rotate = not bool(args.nudge)      # user gave explicit --nudge -> respect it (no rotation)
    if not args.nudge:                       # localized continue-nudge (English default; universal for agents)
        args.nudge = i18n.t("nudge.default")
    if args.preview >= 0:                    # CLI override of the Settings preview countdown
        os.environ["NONYA_PREVIEW"] = str(args.preview)

    if args.all:                             # multi-session monitor: watch EVERY live session (alert-only)
        from . import scan
        from .policy import Config as _Cfg
        relaunch = args.relaunch or os.environ.get("NONYA_RELAUNCH") == "1"
        cfg = _Cfg(target="scan", app="", engine=(args.engine or "claude,codex"),
                   mode=args.mode, nudge=args.nudge, nudge_rotate=nudge_rotate,
                   state_dir=state_dir, idle=args.idle, grace=args.grace,
                   poll=args.poll, hang_cap=args.hang_cap, max_iterations=args.max_iterations,
                   impact=not args.no_impact, shadow=args.shadow, relaunch=relaunch)
        return scan.run_scan(cfg, backend)

    if not args.target:
        print("error: --target is required (claude|codex|antigravity|cli), or --all to watch every session", file=sys.stderr)
        return 2

    cfg = Config(
        target=args.target, app=args.app, engine=args.engine, mode=args.mode,
        nudge=args.nudge, nudge_rotate=nudge_rotate, sentinel=args.sentinel, send_key=args.send_key,
        tmux_target=args.tmux, idle=args.idle, grace=args.grace, poll=args.poll,
        require_user_idle=args.require_user_idle,
        hang_cap=args.hang_cap, max_nudges=args.max_nudges, max_hours=args.max_hours,
        stuck_after=args.stuck_after, max_iterations=args.max_iterations,
        dry_run=args.dry_run, no_inject=args.no_inject, shadow=args.shadow,
        relaunch=(args.relaunch or os.environ.get("NONYA_RELAUNCH") == "1"),
        state_dir=state_dir, transcript=args.file,
        persona=not args.no_persona, character=args.character, impact=not args.no_impact,
        verify=not args.no_verify, check_cmd=args.check_cmd, project_dir=args.project_dir, model_cmd=args.model_cmd,
    )

    if args.target == "cli":
        cfg.is_app = False
        cfg.engine = cfg.engine or "claude"
        if not cfg.tmux_target and not cfg.app:        # 1) auto-find the tmux pane running this engine
            cfg.tmux_target = tmux.find_pane(cfg.engine) or ""
        if not cfg.tmux_target and not cfg.app:        # 2) else fall back to the frontmost terminal (focus+paste)
            term = backend.frontmost_terminal()
            if term:
                cfg.app = term
                print("no tmux pane for %s; using frontmost terminal '%s' (focus+paste; "
                      "run in tmux for precise multi-session targeting)." % (cfg.engine, term), file=sys.stderr)
        if not (cfg.tmux_target or cfg.app):
            print("error: --target cli: no tmux pane running %s and no terminal frontmost — run the CLI "
                  "inside tmux, or pass --tmux <pane> / --app <Terminal>." % cfg.engine, file=sys.stderr)
            return 2
        cfg.app = cfg.app or "cli"
        cfg.is_app = (cfg.tmux_target == "")           # terminal-app paste reuses the single-window GUI gate
    else:
        cfg.is_app = True
        cfg.app = cfg.app or _APP_DEFAULTS[args.target]
        cfg.engine = cfg.engine or args.target

    if cfg.transcript:
        os.environ["NONYA_TRANSCRIPT"] = cfg.transcript

    run_id = "%s-%d" % (time.strftime("%Y%m%d-%H%M%S"), os.getpid())
    os.environ.setdefault("NONYA_LOG", os.path.join(state_dir, "%s-%s.log" % (args.target, run_id)))

    try:
        return run(cfg, backend)
    finally:
        from . import status                       # remove this run's per-pid state file on exit
        status.cleanup(state_dir)                   # (no phantom 'stuck' session lingering in the menu)


if __name__ == "__main__":
    sys.exit(main())
