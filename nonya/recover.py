"""recover — exact-resume command construction + duplicate-submission guards.

The detection engine already finds a stalled/dead session and its (engine, transcript);
detect.session_id() turns the transcript into the exact session id. This module:

  * resume_cmd(engine, sid)  -> the EXACT argv to resume an EXISTING session by id
    (FR-420/421). It NEVER falls back to a name-less `--last` when an id is known
    (PRD 금지구현 3) — no id -> [] -> caller aborts to SESSION_NOT_FOUND.
  * submission_fingerprint / claim_submission -> idempotency so the same nudge is not
    submitted twice into the same session, even across a nonya restart (FR-600/601,
    AC-012). File-based + time-bucketed, so it survives process death (unlike flock).

The tmux respawn path (FR-422) lives in backends/tmux.py; this module only builds argv
and decides idempotency. No network, stdlib only.
"""
from __future__ import annotations

import hashlib
import os
import time


def resume_cmd(engine: str, session_id: str, *, noninteractive: bool = False,
               nudge: str = "") -> list:
    """Exact argv to RESUME an existing session by id. Interactive by default (the user
    keeps the TUI; nonya types the nudge separately once it's ready — FR-420). With
    noninteractive=True, build the headless variant that carries the nudge inline.
    Returns [] when no session id is known (caller must abort, never guess)."""
    sid = (session_id or "").strip()
    if not sid:
        return []
    if engine == "claude":
        if noninteractive:                          # claude -p --resume <id> "<nudge>" --output-format stream-json
            cmd = ["claude", "-p", "--resume", sid]
            if nudge:
                cmd.append(nudge)
            return cmd + ["--output-format", "stream-json"]
        return ["claude", "--resume", sid]
    if engine == "codex":
        if noninteractive:                          # codex exec resume <id> "<nudge>" --json
            cmd = ["codex", "exec", "resume", sid]
            if nudge:
                cmd.append(nudge)
            return cmd + ["--json"]
        return ["codex", "resume", sid]
    return []


def submission_fingerprint(agent: str, cwd: str, session_id: str, prompt: str,
                           trigger: str, ts: float, bucket: int = 300) -> str:
    """FR-601: hash(agent + canonical path + sessionId + prompt + triggerError +
    triggerTimeBucket). Same inputs within the same time bucket -> same fingerprint ->
    treated as one logical submission (so a retry/restart never double-sends)."""
    key = "|".join([agent or "", cwd or "", session_id or "", prompt or "",
                    trigger or "", str(int(ts // max(1, bucket)))])
    return hashlib.sha256(key.encode("utf-8", "ignore")).hexdigest()[:16]


def recently_submitted(state_dir: str, fp: str, ttl: int = 300) -> bool:
    """True if this exact submission fingerprint was marked sent within `ttl` seconds —
    persists on disk, so it holds across a nonya restart (AC-012). Check this BEFORE
    sending; only call mark_submitted() once keys are ACTUALLY sent, so a failed/aborted
    attempt never blocks a legitimate retry."""
    if not (state_dir and fp):
        return False
    p = os.path.join(state_dir, "locks", fp)
    try:
        return os.path.exists(p) and (time.time() - os.path.getmtime(p) < ttl)
    except OSError:
        return False


def mark_submitted(state_dir: str, fp: str) -> None:
    """Record that this submission fingerprint was just sent (atomic; refreshes the window)."""
    if not (state_dir and fp):
        return
    d = os.path.join(state_dir, "locks")
    try:
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, fp)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(str(int(time.time())))
        os.replace(tmp, p)
    except OSError:
        pass


def sweep_locks(state_dir: str, ttl: int = 300) -> None:
    """Remove stale submission-claim files (older than ttl). Cheap; call occasionally."""
    if not state_dir:
        return
    d = os.path.join(state_dir, "locks")
    now = time.time()
    try:
        for name in os.listdir(d):
            fp = os.path.join(d, name)
            try:
                if now - os.path.getmtime(fp) >= ttl:
                    os.remove(fp)
            except OSError:
                pass
    except OSError:
        pass
