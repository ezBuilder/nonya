"""The polling loop (OS-agnostic) — the Correctness Supervisor.

poll -> idle gate -> 4-state classify (done|waiting|stuck|looping) -> route:
  waiting  : ended on a question/permission -> never send a generic nudge. In
             auto mode, answer from local project guidance or a conservative
             autonomy default so routine input waits do not stall overnight.
  looping  : repeating the same action -> NEVER nudge (would feed the loop);
             stop + escalate.
  done     : verify against the project's OWN check before believing it. Passed
             (or no check) -> truly done. Failed -> inject a SPECIFIC correction.
  stuck    : error/rate-limit/stalled -> inject a corrective (or generic) nudge.
Every intervention is recorded in the tamper-evident Trust Ledger.

Safety invariants: no network on the default path; when uncertain prefer the
safe action (don't inject); secrets are redacted in the ledger; gate != ok ->
alert only, never inject.
"""
from __future__ import annotations

import os
import shlex
import time

from . import budget as budgetmod
from . import config as configmod
from . import corrective, detect, ledger, pacing, persona, state, status, supervise, unblock, verify
from .i18n import pick_nudge, t
from .backends import tmux
from .notify import escalate, log, notify
from .policy import Config

_PROTECTED_GUI_APPS = {"claude", "codex", "antigravity"}


def _gate(cfg: Config, backend, proc: str) -> str:
    if cfg.tmux_target:
        return tmux.gate(cfg.tmux_target)
    return backend.window_gate(proc)


def _apply_config(cfg: Config) -> int:
    """Load user settings (config.json) and hot-apply them to cfg. Returns the
    preview-countdown seconds. Channel tokens are pushed into the env. Re-called
    each poll so a Settings toggle applies immediately, no restart."""
    conf = configmod.load(cfg.state_dir)
    configmod.apply_env(conf)
    if conf.get("mode") in ("on-error", "auto"):
        cfg.mode = conf["mode"]
    try:
        if int(conf.get("idle") or 0) > 0:
            cfg.idle = int(conf["idle"])
    except (TypeError, ValueError):
        pass
    if conf.get("character") in ("duck", "cat", "robot"):
        cfg.character = conf["character"]
    cfg.impact = bool(conf.get("sound", True))     # sound off -> no chime on nudge
    env_pv = os.environ.get("NONYA_PREVIEW")        # CLI --preview overrides the file
    src = env_pv if env_pv is not None else conf.get("preview_secs")
    try:
        return max(0, int(src or 0))
    except (TypeError, ValueError):
        return 0


def _preview_gate(cfg: Config, char: str, sess: str, s4: str, text: str, secs: int) -> tuple:
    """Show a cancellable pre-injection preview for `secs` seconds. The face reads
    status="preview" (+ the pending text) and writes control files:
      <state>/preview-cancel  -> abort this injection
      <state>/preview-now     -> inject immediately (skip the rest of the countdown)
      <state>/preview-edit    -> file whose contents replace the nudge text
    Returns (proceed: bool, text: str). Pure local files — no network."""
    sd = cfg.state_dir
    for name in ("preview-cancel", "preview-now", "preview-edit"):
        try:
            os.remove(os.path.join(sd, name))
        except OSError:
            pass
    deadline = time.time() + secs
    status.write(sd, status="preview", target=cfg.target, character=char, sess=s4,
                 preview_text=text, preview_secs=secs, deadline=int(deadline))
    log("PREVIEW: injecting in %ds (cancel: touch %s/preview-cancel): %s" % (secs, sd, text[:80]))
    while time.time() < deadline:
        if os.path.exists(os.path.join(sd, "preview-cancel")):
            os.remove(os.path.join(sd, "preview-cancel"))
            return (False, text)
        edit = os.path.join(sd, "preview-edit")
        if os.path.exists(edit):
            try:
                new = open(edit, encoding="utf-8").read().strip()
                if new:
                    text = new
            except OSError:
                pass
            os.remove(edit)
        if os.path.exists(os.path.join(sd, "preview-now")):
            os.remove(os.path.join(sd, "preview-now"))
            break
        time.sleep(0.25)
    return (True, text)


def _inject(cfg: Config, backend, proc: str, text: str) -> bool:
    if cfg.tmux_target:
        return tmux.inject(cfg.tmux_target, text, cfg.send_key)
    # UNATTENDED (user idle/away) -> allow the GUI backend to RAISE the app to front and type;
    # otherwise stay focus-safe (front-only). nonya exists to recover sessions overnight.
    away = False
    try:
        idle = backend.user_idle_seconds()
        away = idle is not None and idle >= int(os.environ.get("NONYA_GUI_AWAY_IDLE", "60"))
    except Exception:
        away = False
    return backend.inject(proc, text, cfg.send_key, allow_raise=away)


def _real_app_inject_allowed(proc: str) -> bool:
    if proc.strip().lower() not in _PROTECTED_GUI_APPS:
        return True
    return os.environ.get("NONYA_ALLOW_REAL_APP_INJECT") == "1"


def _project_dir(cfg: Config, file: str) -> str:
    """Best-effort repo dir for running the project's check."""
    if cfg.project_dir:
        return cfg.project_dir
    # Claude encodes the cwd in the project folder name: -Users-me-proj -> /Users/me/proj
    if cfg.engine == "claude":
        enc = os.path.basename(os.path.dirname(file))
        if enc.startswith("-"):
            cand = enc.replace("-", "/")
            if os.path.isdir(cand):
                return cand
    return os.getcwd()


def _read_brief_file(path: str, max_bytes: int = 24000) -> str:
    try:
        with open(path, "rb") as fh:
            data = fh.read(max_bytes + 1)
    except OSError:
        return ""
    if len(data) > max_bytes:
        data = data[:max_bytes]
    return data.decode("utf-8", "replace")


def _autonomy_brief(cfg: Config, file: str) -> str:
    """Local, non-secret guidance used for safe auto-answering in auto mode.

    Keep this intentionally narrow: only repo guidance files that humans expect
    agents to read. Never scan arbitrary paths or credential-looking names.
    """
    root = _project_dir(cfg, file)
    names = (
        "AGENTS.md",
        "CLAUDE.md",
        "README.md",
        os.path.join("docs", "README.ko.md"),
        os.path.join("docs", "README.en.md"),
    )
    parts = []
    for name in names:
        path = os.path.join(root, name)
        if os.path.isfile(path):
            txt = _read_brief_file(path)
            if txt:
                parts.append("# %s\n%s" % (name, txt))
    return "\n\n".join(parts)


def _ledger(cfg: Config, **fields) -> None:
    try:
        ledger.append(cfg.state_dir, fields)
    except Exception:  # the ledger must never break the loop
        pass


def run(cfg: Config, backend) -> int:
    proc = cfg.app
    file = cfg.transcript or detect.locate_transcript(cfg.engine)
    if not file or not os.path.isfile(file):
        log("error: transcript not found for engine=%s (use --file)" % cfg.engine)
        return 2

    preview_secs = _apply_config(cfg)        # user settings (config.json): sound/mode/idle/lang/tokens/preview
    if cfg.dry_run:
        log("DRY-RUN: no keystrokes will be sent.")
    log('nonya start | target=%s proc="%s" engine=%s mode=%s | watching: %s'
        % (cfg.target, proc, cfg.engine, cfg.mode, file))
    mode_label = t("mode.auto") if cfg.mode == "auto" else t("mode.on_error")
    # No "started watching" banner — it's noise the user just triggered; the menu-bar eyes
    # waking up already confirm it. Banners are reserved for things that need attention.
    log("watch start | %s — %s" % (proc or cfg.engine, mode_label))
    if cfg.persona:
        log(persona.banner(cfg.character))
    char = cfg.character or "duck"
    sess = proc or cfg.engine
    status.write(cfg.state_dir, status="watching", target=cfg.target,
                 engine=cfg.engine, character=char, nudges=0)

    # autonomy 'leash': an OPT-IN pre-AFK budget. Absent budget.json => behave as before
    # (starting the watch is consent to inject); a budget file can tighten or switch to alert-only.
    bud = budgetmod.load_budget(cfg.state_dir)
    bpath = os.environ.get(budgetmod.ENV_PATH) or os.path.join(cfg.state_dir, budgetmod.FILENAME)
    budget_set = bool(os.path.exists(bpath))
    alert_only = budget_set and not budgetmod.allow_inject(bud)
    give_up_after = min(cfg.give_up_after, bud.give_up_after()) if budget_set else cfg.give_up_after
    max_nudges = min(cfg.max_nudges, bud.spend_ceiling) if budget_set else cfg.max_nudges
    if alert_only:
        log("budget: alert-only (auto_inject off) — detect + escalate, never type")

    start_ts = time.time()
    start_ppid = os.getppid()      # watchdog baseline: if our parent dies we must not keep injecting headless
    nudges = 0
    consec_stuck = 0
    last_alert = ""
    last_escalate_ts = 0.0
    verified_mtime = -1.0          # cache: don't re-run the project check until the transcript changes
    verify_summary = None
    iters = 0

    while True:
        iters += 1
        if cfg.max_iterations and iters > cfg.max_iterations:
            log("max-iterations reached (%d) — exiting" % cfg.max_iterations)
            return 4
        # parent-liveness watchdog: if the app/shell that spawned us died, the OS reparents us to
        # launchd (ppid 1). Don't keep watching/injecting headless — exit. (Skip if we started orphaned,
        # e.g. nohup/launchd, where ppid is 1 from the outset.)
        if start_ppid != 1 and os.getppid() == 1:
            log("parent process gone — exiting (no headless injection)")
            status.write(cfg.state_dir, status="stopped", target=cfg.target, character=char)
            return 3
        preview_secs = _apply_config(cfg)    # hot-apply Settings changes (sound/mode/preview/tokens) without a restart
        now = time.time()
        # quiet hours: recover SILENTLY (suppress the chime that would wake you);
        # escalations (true blockers) still fire. now_hhmm from local time.
        quiet = budget_set and budgetmod.in_quiet_hours(bud, time.strftime("%H:%M", time.localtime(now)))
        if (now - start_ts) / 3600 >= cfg.max_hours:
            notify(t("stop.title"), t("stop.maxhours"), "Basso")
            status.write(cfg.state_dir, status="stopped", target=cfg.target, character=char)
            return 3
        if nudges >= max_nudges:
            notify(t("stop.title"), t("stop.spend", max_nudges), "Basso")
            status.write(cfg.state_dir, status="stopped", target=cfg.target, character=char)
            return 3

        # panic word in the transcript -> force immediate escalation + stop (the kill switch)
        if bud.panic_word:
            try:
                tail = "\n".join(detect._tail_lines(file, 40))
            except Exception:
                tail = ""
            if budgetmod.has_panic(bud, tail):
                escalate(t("panic.title"), t("panic.body", proc or cfg.engine))
                _ledger(cfg, session=sess, stall_class="panic", outcome="stopped",
                        injected_text="", evidence="panic word in transcript", gates_passed="n/a")
                status.write(cfg.state_dir, status="stopped", target=cfg.target, character=char)
                return 3

        if detect.has_done(cfg.engine, file, cfg.sentinel):
            notify(t("done.title"), t("done.sentinel", proc or cfg.engine), "Hero")
            status.write(cfg.state_dir, status="done", target=cfg.target, character=char)
            return 0

        idle = detect.idle_seconds(file)
        if idle < cfg.idle:
            time.sleep(cfg.poll)
            continue

        s4 = supervise.classify4(cfg.engine, file, idle, stuck_idle_cap=cfg.hang_cap)

        # --- WORKING: in-progress / quiet but within the hang cap. NOT actionable — a long-but-live
        #     turn (slow tool, deep reasoning) must never be nudged. Mirror the live state and wait. ---
        if s4 == supervise.WORKING:
            status.write(cfg.state_dir, status="working", target=cfg.target,
                         character=char, nudges=nudges, sess=s4)
            last_alert = ""
            time.sleep(cfg.poll)
            continue

        waiting_answer = ""

        # --- WAITING: a pending question/permission. Generic nudges are unsafe.
        #     In auto mode, answer from local repo guidance when possible; if the
        #     guidance is silent, send a conservative local-only autonomy policy
        #     instead of leaving the run asleep on routine input. ---
        if s4 == supervise.WAITING:
            if cfg.mode == "auto":
                q = supervise.waiting_text(cfg.engine, file)
                waiting_answer = unblock.auto_answer(q, _autonomy_brief(cfg, file)) or ""
            if not waiting_answer:
                status.write(cfg.state_dir, status="waiting", target=cfg.target,
                             character=char, nudges=nudges, sess=s4)
                if last_alert != "waiting" and (now - last_escalate_ts) >= cfg.escalate_cooldown:
                    escalate(t("waiting.title"), t("waiting.body", sess))
                    _ledger(cfg, session=sess, stall_class="waiting", outcome="escalated",
                            injected_text="", evidence="ended on a question / permission prompt", gates_passed="n/a")
                    last_escalate_ts = now; last_alert = "waiting"
                time.sleep(cfg.poll)
                continue

        # --- LOOPING: repeating the same action. A nudge would deepen the loop. Stop + escalate. ---
        if s4 == supervise.LOOPING:
            status.write(cfg.state_dir, status="looping", target=cfg.target,
                         character=char, nudges=nudges, sess=s4)
            if last_alert != "looping" and (now - last_escalate_ts) >= cfg.escalate_cooldown:
                escalate(t("looping.title"), t("looping.body", sess))
                _ledger(cfg, session=sess, stall_class="looping", outcome="escalated",
                        injected_text="", evidence="repeated near-identical tool calls", gates_passed="n/a")
                last_escalate_ts = now; last_alert = "looping"
            time.sleep(cfg.poll)
            continue

        # --- DONE: don't trust it. Verify against the project's own check. ---
        if s4 == supervise.DONE:
            verify_summary = None
            if cfg.verify:
                try:
                    m = os.path.getmtime(file)
                except OSError:
                    m = now
                if m != verified_mtime:                       # only (re)verify when the transcript changed
                    verified_mtime = m
                    pdir = cfg.project_dir or _project_dir(cfg, file)
                    found = verify.discover_check(pdir) if not cfg.check_cmd else (cfg.check_cmd, pdir)
                    if found:
                        cmd, cwd = found
                        passed, code, summary = verify.run_check(cmd, cwd)
                        verify_summary = {"passed": passed, "summary": summary, "exit": code, "cmd": cmd}
                        log("verify: %s -> %s (exit %s) %s" % (cmd, "PASS" if passed else "FAIL", code, summary))
                    else:
                        verify_summary = None
            if verify_summary is None or verify_summary.get("passed"):
                status.write(cfg.state_dir, status="done", target=cfg.target,
                             character=char, nudges=nudges, sess=s4)
                if verify_summary is not None and last_alert != "done":
                    notify(t("done.verified.title"), t("done.verified", sess), "Hero")
                    _ledger(cfg, session=sess, stall_class="done", outcome="verified",
                            injected_text="", evidence=verify_summary.get("summary", "check passed"),
                            gates_passed="verify")
                    last_alert = "done"
                if cfg.mode != "auto":
                    time.sleep(cfg.poll)
                    continue
                # auto mode: keep it going with a generic nudge (falls through)
            else:
                last_alert = ""   # claimed done but the check FAILED — this is the high-value correction

        # reaching here => STUCK, DONE+verify-failed, or DONE+auto-keep-going: we will inject.

        # rate-limited? a nudge is futile (the limit is shared, not a stall) — schedule a resume.
        if s4 == supervise.STUCK and pacing.is_rate_limited(cfg.engine, file):
            _, hhmm = pacing.resume_at(cfg.engine, file, now)
            status.write(cfg.state_dir, status="rate-limited", target=cfg.target,
                         character=char, sess=s4, nudges=nudges, resume=hhmm)
            if last_alert != "rate-limited":
                log("rate-limited — not nudging; resume ~%s" % hhmm)
                last_alert = "rate-limited"
            time.sleep(cfg.poll)
            continue

        # alert-only budget: never type — escalate the actionable state and keep watching.
        if alert_only:
            status.write(cfg.state_dir, status="scolding", target=cfg.target,
                         character=char, sess=s4, nudges=nudges)
            if (now - last_escalate_ts) >= cfg.escalate_cooldown:
                escalate(t("alertonly.title"), t("alertonly.body", sess, s4))
                _ledger(cfg, session=sess, stall_class=s4, outcome="alert-only",
                        injected_text="", evidence="auto_inject disabled", gates_passed="n/a")
                last_escalate_ts = now
            time.sleep(cfg.poll)
            continue

        # --- compute the nudge: a SPECIFIC correction if there's a claim, else the generic default.
        #     The generic default rotates the playful 노냐 pool (자냐?/졸아?…) unless --nudge was given. ---
        generic = pick_nudge(cfg.sentinel) if getattr(cfg, "nudge_rotate", False) else cfg.nudge
        if waiting_answer:
            nudge_text = waiting_answer
        else:
            nudge_text = corrective.build_nudge(cfg.engine, file, verify_summary=verify_summary,
                                                state=s4, default_nudge=generic)

        # --- user-idle gate: don't type while the human is at the keyboard ---
        if cfg.require_user_idle > 0:
            uidle = backend.user_idle_seconds()
            if 0 <= uidle < cfg.require_user_idle:
                if last_alert != "user-active":
                    notify(t("useractive.title"), t("useractive.body", sess, cfg.require_user_idle))
                    status.write(cfg.state_dir, status="scolding", target=cfg.target, character=char, sess=s4)
                    last_alert = "user-active"
                time.sleep(cfg.poll)
                continue

        # --- GUI front-tab guard: the desktop app multiplexes many conversations in ONE
        #     window. Inject ONLY when the problem conversation is the one ON SCREEN (front);
        #     if the problem is in a BACKGROUND tab, a nudge would hit the wrong conversation
        #     -> alert only. (front = the most-recently-written transcript.) ---
        if cfg.is_app and not cfg.tmux_target and not cfg.transcript \
                and not detect.is_frontmost(cfg.engine, file):
            status.write(cfg.state_dir, status=s4, target=cfg.target, character=char, sess=s4, nudges=nudges)
            if last_alert != "ambiguous":
                escalate(t("ambiguous.title"), t("ambiguous.body", sess))
                _ledger(cfg, session=sess, stall_class=s4, outcome="alert-only",
                        injected_text="", evidence="multiple live conversations in one app window",
                        gates_passed="n/a")
                last_alert = "ambiguous"
            time.sleep(cfg.poll)
            continue

        # Real logged-in agent apps can contain account menus, permission prompts, and
        # non-chat focus. Default to alert-only unless the operator explicitly opts in.
        if cfg.is_app and not cfg.tmux_target and not _real_app_inject_allowed(proc):
            status.write(cfg.state_dir, status=s4, target=cfg.target, character=char, sess=s4, nudges=nudges)
            key = "%s:real-app-protected" % s4
            if last_alert != key:
                notify(t("gate.title"), "%s %s — real app injection requires NONYA_ALLOW_REAL_APP_INJECT=1" % (sess, s4), "Basso")
                _ledger(cfg, session=sess, stall_class=s4, outcome="alert-only",
                        injected_text="", evidence="real app injection not explicitly enabled",
                        gates_passed="n/a")
                last_alert = key
            time.sleep(cfg.poll)
            continue

        # --- OCR busy veto (apps only; advisory) ---
        conf = "n/a"
        if cfg.is_app and not cfg.tmux_target:
            conf = backend.confirm_state(proc)
            if conf == "busy":
                log("state=%s idle=%ds but OCR=busy -> skip" % (s4, idle))
                time.sleep(cfg.poll)
                continue

        # --- window/tmux gate: never inject unless we're sure of the single target ---
        gate = _gate(cfg, backend, proc)
        if gate != "ok":
            # can't type safely, but the session IS in an actionable state -> reflect it
            # in the eyes (don't leave them calm/"watching" while a problem is unhandled).
            status.write(cfg.state_dir, status=s4, target=cfg.target, character=char, sess=s4, nudges=nudges)
            key = "%s:%s" % (s4, gate)
            if last_alert != key:
                notify(t("gate.title"), t("gate.body", sess, s4, gate), "Basso")
                last_alert = key
            time.sleep(cfg.poll)
            continue

        # --- inject the (corrective) nudge ---
        nudges += 1
        last_alert = ""
        before_m = time.time() - detect.idle_seconds(file)
        sc = persona.scold(nudges, cfg.character)
        status.write(cfg.state_dir, status="scolding", target=cfg.target,
                     character=char, nudges=nudges, scold=sc, sess=s4)
        if cfg.persona:
            log(sc)
            if not cfg.dry_run and not cfg.shadow and not quiet:   # no chime in shadow (no nudge happens)
                persona.impact(sc, cfg.character, cfg.impact)
        log("nudge #%d | state=%s idle=%ds ocr=%s -> inject into \"%s\" [%s]: %s"
            % (nudges, s4, idle, conf, proc or cfg.tmux_target, cfg.send_key, nudge_text[:80]))

        # --- pre-injection preview countdown (opt-in via Settings) ---
        if preview_secs > 0 and not cfg.dry_run and not cfg.no_inject and not cfg.shadow:
            proceed, nudge_text = _preview_gate(cfg, char, sess, s4, nudge_text, preview_secs)
            if not proceed:
                nudges -= 1                  # cancelled -> don't count it as a spent nudge
                log("PREVIEW: cancelled by user — not injecting")
                status.write(cfg.state_dir, status="working", target=cfg.target, character=char, sess=s4)
                _ledger(cfg, session=sess, stall_class=s4, outcome="preview-cancelled",
                        injected_text=nudge_text, evidence="user cancelled the injection preview",
                        gates_passed="preview")
                last_alert = ""
                time.sleep(cfg.poll)
                continue

        injected_ok = True
        if getattr(cfg, "shadow", False):
            # SHADOW: record what we WOULD do, send zero keys (vet false-positives via --metrics).
            log("SHADOW: would inject (state=%s) — no keys sent" % s4)
            outcome = "shadow"
        elif cfg.dry_run:
            log("DRY-RUN: (would paste corrective + %s)" % cfg.send_key)
            outcome = "dry-run"
        elif cfg.no_inject:
            # detect + signal only — an EXTERNAL injector does the typing (reads the
            # ledger/state). nonya itself must NOT send keystrokes here.
            log("NO-INJECT: state/ledger signaled, external injector types (+%s)" % cfg.send_key)
            outcome = "no-inject"
        elif not _inject(cfg, backend, proc, nudge_text):
            injected_ok = False
            log("inject returned failure (gate was ok) — treating as no-progress")
            outcome = "inject-failed"
        else:
            outcome = "injected"
        # injected_text records ONLY keys actually sent (so metrics' "acted"/safety counts are honest);
        # shadow/dry-run/no-inject send nothing -> empty.
        _ledger(cfg, session=sess, stall_class=s4, outcome=outcome,
                injected_text=(nudge_text if outcome == "injected" else ""),
                evidence=(verify_summary.get("summary") if verify_summary else "idle %ds, state=%s" % (idle, s4)),
                gates_passed="idle,user-idle,ocr,window" if outcome == "injected" else outcome)

        # --- verify progress within grace ---
        waited = 0
        progressed = False
        while waited < cfg.grace:
            time.sleep(cfg.poll)
            waited += cfg.poll
            if detect.has_done(cfg.engine, file, cfg.sentinel):
                notify(t("done.title"), t("done.sentinel", sess), "Hero")
                status.write(cfg.state_dir, status="done", target=cfg.target, character=char)
                return 0
            if cfg.dry_run:
                progressed = True
                break
            try:
                if os.path.getmtime(file) > before_m:
                    st2 = detect.classify(cfg.engine, file)
                    if st2 not in (state.ERROR, state.RATE_LIMIT, state.STALLED):
                        progressed = True
                        break
                    before_m = time.time()
            except OSError:
                pass

        if progressed:
            if consec_stuck > 0:
                notify(t("recovered.title"), t("recovered.body", sess), "Glass")
            consec_stuck = 0
            last_escalate_ts = 0.0
            log("progress after nudge #%d" % nudges)
            status.write(cfg.state_dir, status="working", target=cfg.target, character=char, nudges=nudges)
        else:
            consec_stuck += 1
            log("no progress after nudge #%d (stuck=%d)" % (nudges, consec_stuck))
            status.write(cfg.state_dir, status="stuck", target=cfg.target, character=char,
                         nudges=nudges, stuck=consec_stuck)
            if consec_stuck >= give_up_after:
                escalate(t("giveup.title"), t("giveup.body", sess, consec_stuck))
                # phone redirect: if the user typed a free-text instruction on their phone, inject it
                # and un-stick the session instead of giving up. Off the hot path; best-effort.
                reply = None
                try:
                    from . import remote
                    reply = remote.poll_reply(timeout=3)
                except Exception:
                    reply = None
                if reply and not cfg.dry_run and _gate(cfg, backend, proc) == "ok" and _inject(cfg, backend, proc, reply):
                    log("phone redirect injected: %s" % reply[:80])
                    _ledger(cfg, session=sess, stall_class=s4, outcome="phone-redirect",
                            injected_text=reply, evidence="user reply from phone", gates_passed="window")
                    consec_stuck = 0
                    status.write(cfg.state_dir, status="working", target=cfg.target, character=char, nudges=nudges)
                    continue
                # RELAUNCH (opt-in --relaunch): the session may be DEAD, not just stuck. If its tmux
                # pane no longer runs the engine, kill+resume the EXACT session there instead of
                # giving up (FR-420/421/422). Refuses to guess when no session id is known.
                if (cfg.relaunch and cfg.tmux_target and not cfg.dry_run
                        and not tmux.engine_alive_in(cfg.tmux_target, cfg.engine)):
                    from . import recover
                    sid = detect.session_id(cfg.engine, file)
                    argv = recover.resume_cmd(cfg.engine, sid)
                    cwd = detect.session_cwd(cfg.engine, file) or _project_dir(cfg, file)
                    relaunched = False
                    if argv:
                        if tmux.pane_dead(cfg.tmux_target):
                            relaunched = tmux.respawn_pane(cfg.tmux_target, argv, cwd)
                        else:
                            relaunched = tmux.inject(cfg.tmux_target,
                                                     " ".join(shlex.quote(a) for a in argv), "return")
                    if relaunched:
                        log("relaunch: resumed session %s… in pane %s" % (sid[:8], cfg.tmux_target))
                        _ledger(cfg, session=sess, stall_class="process-dead", outcome="relaunched",
                                injected_text="", evidence="kill+resume %s via tmux" % sid[:8],
                                gates_passed="relaunch")
                        consec_stuck = 0
                        last_escalate_ts = 0.0
                        status.write(cfg.state_dir, status="working", target=cfg.target,
                                     character=char, nudges=nudges)
                        continue
                _ledger(cfg, session=sess, stall_class=s4, outcome="gave_up",
                        injected_text="", evidence="no progress after %d nudges" % consec_stuck, gates_passed="n/a")
                status.write(cfg.state_dir, status="stopped", target=cfg.target, character=char,
                             nudges=nudges, stuck=consec_stuck)
                return 3
            if consec_stuck >= cfg.stuck_after and (now - last_escalate_ts) >= cfg.escalate_cooldown:
                escalate(t("stuck.title"), t("stuck.body", sess, consec_stuck))
                last_escalate_ts = now
