"""Multi-session monitor — watch EVERY live agent session, not just the newest.

The single-session loop (loop.run) follows ONE transcript and can inject. But you
run many agent sessions across windows/projects; the one that stalls or hits a
rate-limit is often NOT the newest, so a single-session watcher misses it. This
scanner enumerates ALL recently-active transcripts for the engine(s) each poll,
classifies every one, and acts on any that need you: it RECOVERS what it can target
SAFELY (stuck / rate-limited — and, in auto mode, a "잠수" session whose turn ended
without the <<DONE>> sentinel) by injecting a resume nudge into the session's tmux
pane (deterministic — targets a pane by id) or, for a frontmost single-window GUI app,
a focus-preserving paste. It ALERTS (no typing) for everything else — looping / waiting,
and any background native-terminal split (raw Ghostty/Terminal without tmux): posted
keystrokes route to the terminal's ACTIVE split, not the targeted one, so auto-injecting
there could hit the WRONG session — nonya refuses and alerts instead (run agents in tmux
for safe auto-recovery).

It writes one state file per session under <state>/sessions/, so the menu-bar eyes
(which surface the most-urgent session) reflect the whole fleet.
"""
from __future__ import annotations

import json
import os
import shlex
import time

from . import detect, pacing, recover, state, supervise
from .backends import tmux
from .i18n import pick_nudge, t
from .notify import escalate, log, notify

SCAN_WITHIN = int(os.environ.get("NONYA_SCAN_WITHIN", "1800"))   # a session quiet > this is "not live"
_PROBLEM = (supervise.STUCK, supervise.LOOPING, supervise.WAITING)


def _safe(label: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in label)[:48] or "x"


def scan_once(engines, within: int, stuck_idle_cap: int) -> list:
    """Classify every live session of each engine. Returns one dict per session."""
    out = []
    for eng in engines:
        for path, mtime, label in detect.active_transcripts(eng, within):
            idle = detect.idle_seconds(path)
            try:
                s4 = supervise.classify4(eng, path, idle=idle, stuck_idle_cap=stuck_idle_cap)
            except Exception:
                continue
            rl = False
            try:
                rl = pacing.is_rate_limited(eng, path)
            except Exception:
                pass
            out.append({"engine": eng, "path": path, "label": label,
                        "state": s4, "idle": idle, "rate_limited": rl})
    return out


# A session counts as actively "working" only if it WROTE to its transcript within this many
# seconds — i.e. it's producing output right now. Quiet longer than this is "idle" (paused
# between turns, a stalled tool, or simply abandoned). We deliberately do NOT call a long-quiet
# session "working": idle time can't tell "user stepped away" from "agent busy", so the honest,
# verifiable signal is "wrote recently or not". (The 30-min scan window is just liveness; it is
# FAR too coarse to mean "working" — that conflated abandoned sessions with active ones.)
ACTIVE_CAP = int(os.environ.get("NONYA_ACTIVE_CAP", "60"))


def _status_for(s: dict, cfg=None) -> str:
    if s["rate_limited"]:
        return "rate-limited"
    st = s["state"]
    if st == supervise.STUCK:
        return "stuck"
    if st == supervise.LOOPING:
        return "looping"
    if st == supervise.WAITING:
        return "waiting"
    if st == supervise.DONE:
        # "done" ONLY when the <<DONE>> sentinel was actually printed (the assigned task is truly
        # finished). A bare end_turn the user simply hasn't replied to yet is NOT "완료" — it's an
        # idle, between-turns session. (This is why an actively-used session showed "완료".)
        sentinel = getattr(cfg, "sentinel", "<<DONE>>") or "<<DONE>>"
        try:
            return "done" if detect.has_done(s["engine"], s["path"], sentinel) else "idle"
        except Exception:
            return "idle"
    if st == supervise.WORKING:
        return "working" if s.get("idle", 0) <= ACTIVE_CAP else "idle"
    return "watching"


def run_scan(cfg, backend) -> int:
    engines = [e.strip() for e in (cfg.engine or "claude,codex").split(",") if e.strip()]
    sdir = os.path.join(cfg.state_dir, "sessions")
    os.makedirs(sdir, exist_ok=True)
    # No "started watching" banner — it's noise the user just triggered (the eyes already
    # show it). Log only; banners stay reserved for sessions that actually need attention.
    log("nonya scan | watching ALL live sessions: %s" % "+".join(engines))
    last_alert = {}     # label -> last problem-status alerted (dedup; cleared when it recovers)
    resume = {}         # label -> epoch to resume a rate-limited session (backoff over)
    pending = {}        # label -> (inject_epoch, mtime_before, path, why, engine) awaiting EFFECT verification
    retry_at = {}       # label -> epoch of next injection RE-attempt (don't give up after one try)
    backoff = {}        # label -> consecutive no-effect count -> exponential retry spacing (server overload)
    iters = 0
    while True:
        iters += 1
        if cfg.max_iterations and iters > cfg.max_iterations:
            log("scan: max-iterations reached (%d)" % cfg.max_iterations)
            return 4
        try:
            sessions = scan_once(engines, SCAN_WITHIN, cfg.hang_cap)
        except Exception as e:                     # never let a scan crash the monitor
            log("scan error: %s" % e)
            sessions = []
        now = time.time()
        # EFFECT verification: for each session we nudged earlier, did its transcript actually
        # advance? That is the ONLY honest proof a keystroke DID something (vs was merely sent).
        for lbl in list(pending):
            try:
                inj_ts, mt_before, ppath, pwhy, peng = pending[lbl]
            except (ValueError, TypeError):
                pending.pop(lbl, None); continue
            # REAL resumption requires BOTH: the transcript advanced AND it is no longer errored/
            # stalled. mtime alone is a FALSE positive — the injected nudge itself bumps mtime (the
            # user-message echo), so "moved" fired even when the agent never responded (that was the
            # bogus "다시 움직이기 시작했어요"). Only claim recovery when the error/stall actually cleared.
            moved = _mtime(ppath) > mt_before + 0.5
            healthy = False
            if moved:
                try:
                    healthy = detect.classify(peng, ppath) not in (state.ERROR, state.RATE_LIMIT, state.STALLED)
                except Exception:
                    healthy = False
            if moved and healthy:
                _verify_ledger(cfg, lbl, pwhy, True, int(now - inj_ts))
                pending.pop(lbl, None); last_alert.pop(lbl, None); backoff.pop(lbl, None)   # genuinely alive -> reset
            elif now - inj_ts > max(cfg.grace, cfg.poll * 3):   # window passed, still errored/stalled/quiet
                _verify_ledger(cfg, lbl, pwhy, False, int(now - inj_ts))
                pending.pop(lbl, None)                            # leave retry_at armed -> keep retrying (not one-shot)
                backoff[lbl] = min(backoff.get(lbl, 0) + 1, 6)    # space out retries (server overload: 15/30/60…)
        # How many live conversations exist per engine THIS poll. The desktop apps multiplex
        # many conversations into one window, so >1 means a GUI raise-and-type cannot PROVE which
        # conversation is the stalled target -> it must NOT blind-paste (that is the "다른
        # 프로젝트에 주입" misfire). tmux is exempt: it targets a pane by id, deterministically.
        eng_live = {}
        for s in sessions:
            eng_live[s["engine"]] = eng_live.get(s["engine"], 0) + 1
        live = set()
        for s in sessions:
            label = s["label"]
            live.add(label)
            st = _status_for(s, cfg)
            gui_ambiguous = eng_live.get(s["engine"], 1) > 1
            # reachability: can nonya actually AUTO-RECOVER this session, or only ALERT? It can
            # only inject into a tmux pane it can target by cwd. GUI/raw-terminal sessions are
            # watch-only — surfacing this is WHY a user "sees no effect": nothing is injectable.
            reach = "tmux" if (not cfg.dry_run and _reach_pane(s)) else "alert"
            # FR-001 registry fields persisted per session (sessionId for resume, canonical cwd,
            # transcript fingerprint to bind verification to THIS session, not 'some file moved').
            sid = detect.session_id(s["engine"], s["path"])
            cwd = detect.session_cwd(s["engine"], s["path"])
            fp = detect.transcript_fingerprint(s["path"])
            title = detect.claude_session_title(sid) if s["engine"] == "claude" else ""
            disp = title or (os.path.basename(cwd) if cwd else label)   # readable name for notifications
            # a distinctive recent on-screen line — lets the menu "전환" find this session's TERMINAL
            # split by CONTENT (Claude/Codex apps are AX-opaque, so only a real terminal matches).
            try:
                snippet = _session_snippet(s)
            except Exception:
                snippet = ""
            _write_session(sdir, label, st, s["engine"], int(s.get("idle", 0)), reach,
                           sid=sid, cwd=cwd, fp=fp, title=title, snippet=snippet)
            if not (s["rate_limited"] or s["state"] in _PROBLEM):
                resume.pop(label, None); retry_at.pop(label, None); backoff.pop(label, None)   # no longer a problem -> re-arm
                # Autonomous keep-going (auto mode only): a session whose turn ENDED (done) but
                # never printed the <<DONE>> sentinel has stopped WITHOUT finishing — the "잠수"
                # stall (claimed it would continue, then went quiet). The single-session auto loop
                # nudges this; the fleet scanner must too, or a backgrounded agent sits dead all
                # night. Wake it once it's been quiet past the idle gate; re-arm when it moves or
                # truly signals done.
                if (cfg.mode == "auto" and s["state"] == supervise.DONE
                        and s["idle"] > cfg.idle
                        and not detect.has_done(s["engine"], s["path"], cfg.sentinel)):
                    if last_alert.get(label) != "keepgoing":
                        last_alert[label] = "keepgoing"
                        mt = _mtime(s["path"])
                        if _recover(cfg, backend, s, "keep-going", gui_ambiguous=gui_ambiguous):
                            pending[label] = (now, mt, s["path"], "keep-going", s["engine"])
                else:
                    last_alert.pop(label, None)                # recovered / truly <<DONE>> -> re-arm
                continue
            if s["rate_limited"] or s["state"] == supervise.STUCK:
                why = "rate-limited" if s["rate_limited"] else "stuck"
                # rate-limit: hold off until the limit window passes (nudging into it is futile).
                if s["rate_limited"]:
                    rt = resume.get(label)
                    if rt is None:
                        epoch, hhmm = pacing.resume_at(s["engine"], s["path"], now)
                        resume[label] = epoch
                        if last_alert.get(label) != "rate-limited":
                            notify(t("ratelimit.title"), t("ratelimit.body", disp, hhmm))
                            last_alert[label] = "rate-limited"
                        continue
                    if now < rt:
                        continue                                # still within backoff
                # KEEP RETRYING (don't give up after one try): re-attempt injection every RETRY_EVERY
                # so a session becomes recoverable the moment it's reachable (e.g. its tmux pane
                # appears, OR it's brought on-screen for a GUI app). Alert the human only ONCE.
                if now < retry_at.get(label, 0.0):
                    continue
                retry_at[label] = now + min(max(cfg.grace, cfg.poll * 4) * (2 ** backoff.get(label, 0)), 900)
                first = last_alert.get(label) != why
                last_alert[label] = why
                mt = _mtime(s["path"])
                if _recover(cfg, backend, s, why, escalate_blocked=first, gui_ambiguous=gui_ambiguous):
                    pending[label] = (now, mt, s["path"], why, s["engine"])
                    # retry_at already pushed to now+RETRY_EVERY above -> do NOT re-nudge until the
                    # verify window passes (else we'd spam the agent a nudge every single poll).
            else:                                               # waiting / looping -> alert only (never nudge)
                if last_alert.get(label) == st:
                    continue
                last_alert[label] = st
                if s["state"] == supervise.WAITING:
                    notify(t("waiting.title"), t("waiting.body", disp))
                else:
                    escalate(t("looping.title"), t("looping.body", disp))
                _log_ledger(cfg, label, st, "")
        _prune(sdir, live)
        time.sleep(cfg.poll)


def _reach_pane(s: dict):
    """True-ish if this session has a tmux pane nonya can inject into (best-effort; never raises)."""
    try:
        return _pane_for(s)
    except Exception:
        return None


def _pane_for(s: dict):
    """Target the session's tmux pane: by cwd (claude path-encoded) first, else by the
    engine's process (codex paths carry no cwd). Both refuse ambiguity -> no misfire."""
    pane = tmux.pane_for_cwd(detect.session_cwd(s["engine"], s["path"]))
    return pane or tmux.find_pane(s["engine"])


def _session_snippet(s: dict) -> str:
    """A distinctive, currently-on-screen substring of the session's recent output, used to
    locate its terminal split by content. Falls back to the project name."""
    try:
        objs = detect._tail_json(s["path"], n=(detect.CODEX_SCAN_LINES if s["engine"] == "codex"
                                               else detect.TAIL_LINES))
        txt = supervise._last_assistant_text(s["engine"], objs) or ""
    except Exception:
        txt = ""
    for line in reversed(txt.splitlines()):              # last substantial line = most recently rendered
        clean = line.strip().strip("#*->`").strip()
        if 15 <= len(clean) <= 70:
            return clean
    cwd = detect.session_cwd(s["engine"], s["path"])
    return os.path.basename(cwd) if cwd else s["label"].split(":")[0]


def _disp_label(s: dict) -> str:
    """Human-readable session name for NOTIFICATIONS (not the 'engine:idtail' code the user couldn't
    read): the Claude desktop conversation title, else the project folder name (Codex has no title
    store), else the raw label. The LEDGER keeps the precise label for dedup/metrics."""
    try:
        if s["engine"] == "claude":
            title = detect.claude_session_title(detect.session_id(s["engine"], s["path"]))
            if title:
                return title
        cwd = detect.session_cwd(s["engine"], s["path"])
        if cwd:
            return os.path.basename(cwd)
    except Exception:
        pass
    return s.get("label", "?")


_APP_PROC = {"claude": "Claude", "codex": "Codex"}     # engine -> desktop-app process name


# Seconds of no keyboard/mouse input before we treat the machine as UNATTENDED — at which point
# it's safe to RAISE a GUI app to front and type (nobody to disrupt). nonya's whole reason to exist
# is overnight recovery, so when you're away we bring the app forward and inject; when you're at the
# keyboard we stay focus-safe (front-only, no raise).
GUI_AWAY_IDLE = int(os.environ.get("NONYA_GUI_AWAY_IDLE", "60"))


def _gui_recover(backend, s: dict, nudge: str, ambiguous: bool = False) -> bool:
    """Recover a GUI desktop-app session (Claude/Codex .app) with no tmux pane / terminal split.

    SAFETY (PRD FR-204/지시문 7 — "잘못된 프로젝트에 Enter를 보내는 것보다 복구를 실패시키는 것이 낫다"):
    raising the app and blindly typing lands keys in whatever conversation is FOCUSED — that caused
    "다른 프로젝트에 주입". The fix:
      * USER AWAY (unattended) + Vision OCR helper available -> `--resolve-inject`: the helper PROVES
        the exact target (OCR the sidebar, find the row matching this session's project, click it,
        read back the typed text, then submit). It refuses (AMBIGUOUS_TARGET) rather than guess.
      * USER AWAY, NO helper -> can't prove the target by OCR; only safe when there is exactly ONE
        live conversation (`not ambiguous`), else alert.
      * USER PRESENT -> act only if this IS the on-screen front conversation (focus-safe paste).
    window_gate must be `ok` (single OS window)."""
    if os.environ.get("NONYA_NO_GUI_INJECT") == "1":
        return False                                    # test isolation: never touch a real .app window
    proc = _APP_PROC.get(s["engine"], "")
    gate = getattr(backend, "window_gate", None)
    if not (proc and callable(gate)):
        return False
    try:
        if gate(proc) != "ok":
            return False
        idle = 0.0
        getter = getattr(backend, "user_idle_seconds", None)
        if callable(getter):
            try:
                idle = getter()
            except Exception:
                idle = 0.0
        away = idle is not None and idle >= GUI_AWAY_IDLE
        # may act only if unattended (raise OK) OR the session is the on-screen FRONT conversation.
        if not away and not detect.is_frontmost(s["engine"], s["path"]):
            return False                                # user present + background conversation -> alert
        helper = os.environ.get("NONYA_AX_HELPER", "")
        if helper and os.path.exists(helper):
            import subprocess
            if away:
                # UNATTENDED: PROVE the exact target by OCR before typing (disambiguates among many
                # conversations). Hint = the Claude desktop conversation TITLE (what the sidebar shows);
                # fall back to the project folder name when there's no desktop record.
                sid = detect.session_id(s["engine"], s["path"])
                hint = (detect.claude_session_title(sid) if s["engine"] == "claude" else "") \
                    or os.path.basename(detect.session_cwd(s["engine"], s["path"])) or s["label"].split(":")[0]
                try:
                    r = subprocess.run([helper, "--resolve-inject", proc, hint, nudge],
                                       capture_output=True, timeout=60)
                    return r.returncode == 0                # OK only if the resolver proved+verified+submitted
                except (OSError, subprocess.SubprocessError):
                    return False
            # USER PRESENT + this is the FRONT conversation (gated above): paste into focused.
            try:
                r = subprocess.run([helper, "--inject-app", proc, nudge], capture_output=True, timeout=25)
                if r.returncode == 0:
                    return True
            except (OSError, subprocess.SubprocessError):
                pass
        # no helper (osascript fallback): can't prove the exact conversation -> refuse on ambiguity.
        if ambiguous:
            return False
        inj = getattr(backend, "inject", None)
        return bool(callable(inj) and inj(proc, nudge, "return", allow_raise=away))
    except Exception:
        return False


def _relaunch_session(cfg, s: dict, pane: str) -> bool:
    """Kill a DEAD agent's pane process and resume the EXACT same session in that pane
    (FR-420/421/422). Dead pane (#{pane_dead}) -> `tmux respawn-pane -k` with the resume
    argv (kills tree + relaunches in the original cwd, same pane id). Live shell whose agent
    died -> type the resume command into that shell. Interactive resume; the NEXT poll's
    nudge continues the work. Refuses (no guess) when no session id is known. Records the
    action in the ledger. Returns True iff the resume command was issued."""
    eng = s["engine"]
    sid = detect.session_id(eng, s["path"])
    cwd = detect.session_cwd(eng, s["path"])
    argv = recover.resume_cmd(eng, sid)
    if not argv:                                         # no session id -> SESSION_NOT_FOUND, never guess
        log("scan RELAUNCH: %s -> no session id; refusing to guess (SESSION_NOT_FOUND)" % s["label"])
        return False
    if tmux.pane_dead(pane):
        ok, how = tmux.respawn_pane(pane, argv, cwd), "respawn-pane"
    else:                                                # live shell -> type the resume command + Enter
        ok, how = tmux.inject(pane, " ".join(shlex.quote(a) for a in argv), "return"), "shell"
    if ok:
        log("scan RELAUNCH (%s…): %s -> %s '%s' in pane %s" % (sid[:8], s["label"], how, eng, pane))
        notify(t("nudge.sent.title"), t("nudge.sent.body", _disp_label(s)))
        try:
            from . import ledger
            ledger.append(cfg.state_dir, {"session": s["label"], "stall_class": "process-dead",
                          "outcome": "relaunched", "injected_text": "",
                          "evidence": "kill+resume exact session %s via %s" % (sid[:8], how),
                          "gates_passed": "relaunch"})
        except Exception:
            pass
    return ok


def _recover(cfg, backend, s: dict, why: str, escalate_blocked: bool = True,
             gui_ambiguous: bool = False) -> str:
    """Wake a stalled/rate-limited session. RELAUNCH (opt-in --relaunch) when the session's
    PROCESS is dead; else NUDGE a live one. Nudge order: its tmux pane; a native terminal
    SPLIT matched by on-screen content; else — for a single (unambiguous) GUI desktop-app
    window — a focus-safe paste. If none can be targeted safely, escalate (only when
    `escalate_blocked`). Returns the action text ("" if nothing was sent) so the caller can
    verify the EFFECT."""
    label = s["label"]
    # rotating playful 노냐 nudge (자냐?/졸아?…) unless the user pinned an explicit --nudge.
    nudge = (pick_nudge(getattr(cfg, "sentinel", "<<DONE>>")) if getattr(cfg, "nudge_rotate", False)
             else (cfg.nudge or t("nudge.default")))
    injected = ""
    # SHADOW: decide + RECORD what we WOULD do, but send ZERO keys. Lets the user vet the
    # false-positive surface (`nonya --metrics`) before trusting auto-injection.
    if getattr(cfg, "shadow", False):
        reach = "tmux" if _reach_pane(s) else "alert"
        log("scan SHADOW (%s): %s -> would-recover (reach=%s), no keys sent" % (why, label, reach))
        try:
            from . import ledger
            ledger.append(cfg.state_dir, {"session": label, "stall_class": why,
                                          "outcome": "shadow", "injected_text": "",
                                          "evidence": "shadow would-recover; reach=%s" % reach,
                                          "gates_passed": "scan-shadow"})
        except Exception:
            pass
        return ""
    pane = _pane_for(s)
    # RELAUNCH: process dead but its pane is targetable -> kill+resume the exact session id.
    if (pane and getattr(cfg, "relaunch", False) and not cfg.dry_run
            and not tmux.engine_alive_in(pane, s["engine"])):
        if _relaunch_session(cfg, s, pane):
            return "<relaunch>"                          # next poll nudges the resumed session
    # IDEMPOTENCY (FR-600/601, AC-012): suppress a duplicate nudge for the SAME session within
    # the verify window — survives a nonya restart mid-verify. Checked before sending; the claim
    # is recorded only when keys are ACTUALLY sent, so a failed/aborted attempt never blocks retry.
    ttl = max(cfg.grace, cfg.poll * 3)
    sid = detect.session_id(s["engine"], s["path"])
    cwd = detect.session_cwd(s["engine"], s["path"])
    claim_fp = recover.submission_fingerprint(s["engine"], cwd, sid, nudge, why, 0, bucket=1)
    if not cfg.dry_run and recover.recently_submitted(cfg.state_dir, claim_fp, ttl):
        log("scan: %s %s — duplicate submission suppressed (already sent this window)" % (label, why))
        return ""
    if pane and not cfg.dry_run and tmux.gate(pane) == "ok" and tmux.inject(pane, nudge, "return"):
        injected = nudge
        log("scan RECOVER (%s): %s -> tmux pane %s" % (why, label, pane))
    elif not cfg.dry_run and backend is not None:        # no tmux pane -> native terminal split (AX)
        snip = _session_snippet(s)
        if snip and backend.inject_terminal_split(snip, nudge, "return"):
            injected = nudge
            log("scan RECOVER (%s): %s -> terminal split (match '%s')" % (why, label, snip[:30]))
        elif _gui_recover(backend, s, nudge, ambiguous=gui_ambiguous):   # single GUI window, focus-safe
            injected = nudge
            log("scan RECOVER (%s): %s -> GUI app window (frontmost)" % (why, label))
    if injected:
        recover.mark_submitted(cfg.state_dir, claim_fp)            # record only on a real send
        notify(t("nudge.sent.title"), t("nudge.sent.body", _disp_label(s)))
    else:
        if escalate_blocked:
            escalate(t("cantact.title"), t("cantact.body", _disp_label(s)))   # readable name, no nonsensical "0회"
        log("scan: %s %s — couldn't target (alert%s)" % (label, why, "ed" if escalate_blocked else " suppressed, will retry"))
    _log_ledger(cfg, label, why, injected)
    return injected                                              # caller verifies the EFFECT next poll


def _log_ledger(cfg, label: str, st: str, injected: str) -> None:
    # HONEST outcomes: "injected" = keys were actually SENT (effect confirmed later as
    # "recovered"/"no-effect" by the verify pass). When we could NOT act (no tmux pane, or a
    # never-nudge state like waiting/looping) the outcome is "alert-only" — NOT the stall class,
    # which would read on the dashboard as if nonya had done something. It didn't.
    try:
        from . import ledger
        ledger.append(cfg.state_dir, {"session": label, "stall_class": st,
                                      "outcome": ("injected" if injected else "alert-only"),
                                      "injected_text": injected,
                                      "evidence": ("scan inject" if injected else "detected %s; could not act (alert-only)" % st),
                                      "gates_passed": "scan"})
    except Exception:
        pass
    log("scan: %s -> %s%s" % (label, st, " (injected)" if injected else ""))


def _mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def _verify_ledger(cfg, label: str, why: str, worked: bool, secs: int) -> None:
    """Record the CONFIRMED effect of an earlier nudge: did the session's transcript actually
    advance after we typed? 'recovered' = it moved (the keys DID something); 'no-effect' = keys
    were sent but nothing changed within the grace window (delivered but did not resume)."""
    outcome = "recovered" if worked else "no-effect"
    ev = ("transcript advanced %ds after nudge" % secs) if worked else \
         ("no transcript change %ds after nudge (delivered, no resume)" % secs)
    try:
        from . import ledger
        ledger.append(cfg.state_dir, {"session": label, "stall_class": why, "outcome": outcome,
                                      "injected_text": "", "evidence": ev, "gates_passed": "scan-verify"})
    except Exception:
        pass
    log("scan VERIFY: %s %s -> %s (%ds)" % (label, why, outcome, secs))
    if worked:
        notify(t("recovered.title"), t("recovered.body", label))   # only NOW claim recovery


def _write_session(sdir: str, label: str, st: str, engine: str, idle: int = 0, reach: str = "alert",
                   sid: str = "", cwd: str = "", fp: str = "", title: str = "", snippet: str = "") -> None:
    path = os.path.join(sdir, "scan-%s.json" % _safe(label))
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"status": st, "session": label, "engine": engine,
                       "idle": idle, "reach": reach,
                       "session_id": sid, "cwd": cwd, "fingerprint": fp, "title": title,
                       "snippet": snippet, "ts": int(time.time())}, fh, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        pass


def _prune(sdir: str, live: set) -> None:
    live_files = {"scan-%s.json" % _safe(l) for l in live}
    try:
        for name in os.listdir(sdir):
            if name.startswith("scan-") and name not in live_files:
                try:
                    os.remove(os.path.join(sdir, name))
                except OSError:
                    pass
    except OSError:
        pass
