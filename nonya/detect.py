"""Detection core (OS-shared): locate a session transcript, measure idle, and
classify session STATE from the on-disk log.

Engines & on-disk formats (verified locally 2026-06-19, see docs/RESEARCH-*):
  - claude:      ~/.claude/projects/<proj>/<session_id>.jsonl  (JSONL)
                 .message.stop_reason / .error / .apiErrorStatus / .isApiErrorMessage
  - codex:       ~/.codex/sessions/<Y>/<M>/<D>/rollout-*.jsonl  (JSONL)
                 event_msg.payload.type=task_complete/task_started, token_count.rate_limits
  - antigravity: ~/.gemini/antigravity-cli/conversations/<uuid>.db  (SQLite)
                 steps(status:int, error_details:blob); + cli-*.log HTTP codes

Paths use ~ which maps to %USERPROFILE% on Windows — the format itself is OS
independent so this module is shared. (Windows path parity is unverified; see
docs/RESEARCH-windows-auto-inject-2026-06-19.md.)
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import sqlite3
import time
from typing import List, Optional

from . import state

_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                      r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")

TAIL_LINES = int(os.environ.get("NONYA_TAIL_LINES", "80"))

ENGINES = ("claude", "codex", "antigravity")


def _home(*parts: str) -> str:
    return os.path.join(os.path.expanduser("~"), *parts)


def idle_seconds(path: str) -> int:
    try:
        return int(time.time() - os.path.getmtime(path))
    except OSError:
        return 999999


_ACTIVE_GLOBS = {
    "claude": (".claude", "projects", "*", "*.jsonl"),
    "codex": (".codex", "sessions", "*", "*", "*", "rollout-*.jsonl"),
}


def is_frontmost(engine: str, path: str, slack: float = 3.0) -> bool:
    """Best signal for "is the watched conversation the one ON SCREEN": the desktop
    app writes the visible conversation's transcript as it works, so the most-recently
    modified transcript is (almost always) the front tab. Returns True iff `path` is
    within `slack` seconds of the newest transcript for `engine` -> safe to inject
    (the problem is on screen). False -> another conversation is foreground; the
    problem is in a BACKGROUND tab -> caller alerts instead of typing into the wrong one."""
    parts = _ACTIVE_GLOBS.get(engine)
    if not parts or not path:
        return True                      # unknown engine / no multiplexing signal -> don't block
    try:
        mine = os.path.getmtime(path)
    except OSError:
        return True
    newest = mine
    for f in glob.glob(_home(*parts)):
        try:
            m = os.path.getmtime(f)
            if m > newest:
                newest = m
        except OSError:
            pass
    return (newest - mine) <= slack


def recently_active(engine: str, within: float = 90.0) -> int:
    """How many transcripts of `engine` were written within `within` seconds — i.e.
    how many conversations are concurrently LIVE. The desktop apps multiplex many
    conversations into ONE window; >1 live means we can't know which is on screen,
    so the GUI injector must not type (it could land in the wrong conversation)."""
    parts = _ACTIVE_GLOBS.get(engine)
    if not parts:
        return 1
    now = time.time()
    n = 0
    for f in glob.glob(_home(*parts)):
        try:
            if now - os.path.getmtime(f) <= within:
                n += 1
        except OSError:
            pass
    return n


def active_transcripts(engine: str, within: float = 1800.0) -> List[tuple]:
    """Every transcript of `engine` written within `within` seconds, newest first:
    [(path, mtime, label), ...]. This is how nonya watches ALL live sessions (not just
    the newest one), so a stalled/errored background session is still caught."""
    parts = _ACTIVE_GLOBS.get(engine)
    if not parts:
        return []
    now = time.time()
    out = []
    for f in glob.glob(_home(*parts)):
        try:
            m = os.path.getmtime(f)
        except OSError:
            continue
        if now - m > within:
            continue
        out.append((f, m, _session_label(engine, f)))
    out.sort(key=lambda t: t[1], reverse=True)
    return out


def _codex_meta(path: str) -> dict:
    """Codex records the session's cwd/originator/source in a `session_meta` record at
    the HEAD of the rollout (line 1), NOT the tail — so read the first few lines, not
    _tail_json (which seeks to EOF and would never see it). Returns the payload dict."""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            for _ in range(8):                       # session_meta is line 1, but scan a few in case
                line = fh.readline()
                if not line:
                    break
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if isinstance(obj, dict) and obj.get("type") == "session_meta":
                    p = obj.get("payload")
                    return p if isinstance(p, dict) else {}
    except OSError:
        pass
    return {}


def _claude_cwd_from_head(path: str) -> str:
    """Claude records the session's real cwd in a `cwd` field on its records. Read it from
    the HEAD (launch dir = where the agent process runs = its tmux pane's cwd). This is
    LOSSLESS — unlike decoding the project folder name, which is ambiguous when the path
    itself contains '-' (e.g. .../code-brain decodes to .../code/brain and fails to match)."""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            for _ in range(40):                          # the launch cwd is in the first records
                line = fh.readline()
                if not line:
                    break
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(obj, dict) or obj.get("isSidechain") is True:
                    continue                             # subagents carry their own cwd; want the main chain's
                cwd = obj.get("cwd")
                if isinstance(cwd, str) and cwd and os.path.isdir(cwd):
                    return cwd
    except OSError:
        pass
    return ""


def session_cwd(engine: str, path: str) -> str:
    """The working directory of a session, for matching it to its tmux pane.
    Claude records the real cwd on its records (read from HEAD — lossless); only if that's
    missing do we fall back to decoding the project folder name (lossy: '-' in the path is
    ambiguous). Codex carries it in the session_meta record at the rollout HEAD. Only
    returned if it resolves to a real dir."""
    if engine == "claude":
        cwd = _claude_cwd_from_head(path)
        if cwd:
            return cwd
        enc = os.path.basename(os.path.dirname(path))    # fallback: lossy dir-name decode
        if enc.startswith("-"):
            cand = enc.replace("-", "/")
            if os.path.isdir(cand):
                return cand
    elif engine == "codex":
        cand = _codex_meta(path).get("cwd")
        if isinstance(cand, str) and cand and os.path.isdir(cand):
            return cand
    return ""


def session_id(engine: str, path: str) -> str:
    """The FULL session id used to resume an existing session (claude --resume <id> /
    codex resume <id>). Claude: the transcript filename stem IS the session UUID. Codex:
    the id lives in the session_meta record at the rollout HEAD; fall back to the UUID
    embedded in the rollout filename. Returns "" when no id can be determined — callers
    must then abort (SESSION_NOT_FOUND) rather than guess a session (PRD 금지구현 3)."""
    if not path:
        return ""
    base = os.path.basename(path)
    if engine == "claude":
        return base.rsplit(".", 1)[0]                      # '<uuid>.jsonl' -> the session id
    if engine == "codex":
        sid = _codex_meta(path).get("id")
        if isinstance(sid, str) and sid:
            return sid
        m = _UUID_RE.search(base)                          # 'rollout-<ts>-<uuid>.jsonl'
        return m.group(0) if m else ""
    return ""


_CC_SESSIONS = _home("Library", "Application Support", "Claude", "claude-code-sessions")
_title_cache = {"ts": 0.0, "map": {}}


def _claude_title_index() -> dict:
    """Map cliSessionId -> conversation TITLE from the Claude desktop session store. The store's
    `cliSessionId` IS the transcript uuid, and `title` is exactly the text shown in the desktop
    sidebar — so this lets nonya find a session's clickable sidebar row. Cached ~15s so a poll
    doesn't re-read the store per session. Light coupling, fully fallback-guarded (empty on any
    error / non-macOS). macOS desktop only."""
    now = time.time()
    if now - _title_cache["ts"] < 15 and _title_cache["map"]:
        return _title_cache["map"]
    m = {}
    try:
        for f in glob.glob(os.path.join(_CC_SESSIONS, "**", "local_*.json"), recursive=True):
            try:
                with open(f, encoding="utf-8") as fh:
                    d = json.load(fh)
            except (OSError, ValueError):
                continue
            cli, title = d.get("cliSessionId"), d.get("title")
            if isinstance(cli, str) and isinstance(title, str) and title:
                m[cli] = title
    except OSError:
        pass
    _title_cache["ts"] = now
    _title_cache["map"] = m
    return m


def claude_session_title(session_id: str) -> str:
    """The Claude desktop conversation TITLE for a session (matches the sidebar text), looked up by
    cliSessionId == the transcript uuid. "" if not found -> caller falls back to the project folder
    name. This is the key that lets the OCR resolver target the exact sidebar row reliably."""
    if not session_id:
        return ""
    return _claude_title_index().get(session_id, "")


def transcript_fingerprint(path: str) -> str:
    """Short content hash of the transcript TAIL — changes whenever the session advances.
    Two uses: (FR-003) tell two same-title sessions apart, and (effect verification) bind a
    nudge's proof-of-progress to the SAME session that was targeted, not merely 'some file
    moved'. Cheap (bounded tail read); "" on error."""
    try:
        lines = _tail_lines(path, 6)
    except Exception:
        return ""
    return hashlib.sha256(("\n".join(lines)).encode("utf-8", "ignore")).hexdigest()[:16]


def _session_label(engine: str, path: str) -> str:
    """Short human label for a session: the project name + a short id tail."""
    base = os.path.basename(path)
    sid = base.replace("rollout-", "").split(".")[0][-6:]
    if engine == "claude":
        proj = os.path.basename(os.path.dirname(path))           # -Users-me-navio
        name = proj.rstrip("-").split("-")[-1] if proj.startswith("-") else proj
        return "%s:%s" % (name or "claude", sid)
    return "%s:%s" % (engine, sid)


def _newest(paths: List[str]) -> Optional[str]:
    best, best_m = None, -1.0
    for p in paths:
        try:
            m = os.path.getmtime(p)
        except OSError:
            continue
        if m > best_m:
            best, best_m = p, m
    return best


def locate_transcript(engine: str) -> Optional[str]:
    """Return the active transcript/session file for an engine, or None.

    Precise targeting (for concurrent same-engine sessions): NONYA_TRANSCRIPT
    (exact path) > a session id (NONYA_SESSION_ID, or CLAUDE_CODE_SESSION_ID for
    claude) matched against the per-session file > newest as the last resort.
    """
    override = os.environ.get("NONYA_TRANSCRIPT")
    if override and os.path.isfile(override):
        return override
    sid = os.environ.get("NONYA_SESSION_ID", "").strip()

    if engine == "claude":
        sid = sid or os.environ.get("CLAUDE_CODE_SESSION_ID", "")
        if sid:   # explicit session: match it or return None — never silently latch a stranger's session
            return _newest(glob.glob(_home(".claude", "projects", "*", "*" + sid + "*.jsonl"))) or None
        return _newest(glob.glob(_home(".claude", "projects", "*", "*.jsonl")))

    if engine == "codex":
        if sid:   # the session UUID is embedded in the rollout filename
            return _newest(glob.glob(_home(".codex", "sessions", "*", "*", "*", "rollout-*" + sid + "*.jsonl"))) or None
        return _newest(glob.glob(_home(".codex", "sessions", "*", "*", "*", "rollout-*.jsonl")))

    if engine == "antigravity":
        return _newest(glob.glob(_home(".gemini", "antigravity-cli", "conversations", "*.db")))

    return None


def _tail_lines(path: str, n: int) -> List[str]:
    """Last n lines of a text file (bounded read)."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            block = min(size, max(n * 400, 65536))
            fh.seek(size - block)
            data = fh.read()
    except OSError:
        return []
    return data.decode("utf-8", "replace").splitlines()[-n:]


def _tail_json(path: str, n: int = TAIL_LINES) -> List[dict]:
    out = []
    for line in _tail_lines(path, n):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


# ---- sentinel ("<<DONE>>") -------------------------------------------------
# The watched agent signals completion by printing the sentinel on its OWN line
# (the nudge says "<<DONE>> 한 줄만 출력"). nonya's own nudge text also contains
# the sentinel inline — so a raw byte scan false-positives the moment we paste.
# Fix: parse the records, collect every string value, and only accept the
# sentinel when it appears as a standalone line. The nudge's inline occurrence
# ("...<<DONE>> 한 줄만 출력해.") is never a standalone line, so it never matches.

def _collect_strings(value, out):
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        for v in value.values():
            _collect_strings(v, out)
    elif isinstance(value, list):
        for v in value:
            _collect_strings(v, out)


def _standalone_sentinel(strings, sentinel: str) -> bool:
    target = sentinel.strip()
    for s in strings:
        for line in s.splitlines():
            if line.strip() == target:
                return True
    return False


def has_done(engine: str, path: str, sentinel: str) -> bool:
    strings = []
    if engine == "antigravity":
        try:
            con = sqlite3.connect("file:%s?mode=ro" % path, uri=True, timeout=1)
            try:
                rows = con.execute(
                    "SELECT step_payload FROM steps ORDER BY idx DESC LIMIT 12").fetchall()
            finally:
                con.close()
            for (blob,) in rows:
                if blob is None:
                    continue
                if isinstance(blob, bytes):
                    strings.append(blob.decode("utf-8", "ignore"))
                else:
                    strings.append(str(blob))
        except sqlite3.Error:
            return False
    else:
        for obj in _tail_json(path, n=160):
            _collect_strings(obj, strings)
    return _standalone_sentinel(strings, sentinel)


# ---- classifiers -----------------------------------------------------------

_RATE_RE = re.compile(r"rate", re.I)


def _classify_claude(path: str) -> str:
    objs = _tail_json(path)
    # Single positional pass. Track, skipping subagent (isSidechain) records throughout:
    #   * last rate-limit / error record index (a SUBAGENT's transient 500/429 must NOT
    #     mask the main turn; and a STALE error before a later clean end_turn must not stick)
    #   * last clean completion index (end_turn/stop_sequence/max_tokens)
    #   * last user-side record (a queued/new prompt -> a turn is pending, not COMPLETED)
    stop = None
    last_stop_idx = last_userside_idx = last_complete_idx = last_err_idx = last_rate_idx = -1
    for i, o in enumerate(objs):
        if o.get("isSidechain") is True:
            continue
        err = o.get("error")
        st = o.get("apiErrorStatus")
        is_rate = (st == 429 or err == "rate_limit" or (isinstance(err, str) and _RATE_RE.search(err)))
        is_err = (o.get("isApiErrorMessage") is True or err is not None or (isinstance(st, int) and st >= 400))
        if is_rate:
            last_rate_idx = i
        elif is_err:                  # >=400 only: apiErrorStatus:0 is a placeholder, not an error
            last_err_idx = i
        if o.get("type") in ("user", "queue-operation", "last-prompt"):
            last_userside_idx = i
        msg = o.get("message")
        if isinstance(msg, dict) and msg.get("stop_reason"):
            stop = msg["stop_reason"]
            last_stop_idx = i
            # a record carrying an error/rate-limit is NOT a clean completion, even though it
            # also has a stop_reason (Claude logs a rate-limited turn as stop_sequence + error=rate_limit).
            if stop in ("end_turn", "stop_sequence", "max_tokens") and not (is_rate or is_err):
                last_complete_idx = i
    # rate-limit / error win only when newer than the most recent clean completion
    if last_rate_idx > last_complete_idx:
        return state.RATE_LIMIT
    if last_err_idx > last_complete_idx:
        return state.ERROR
    if last_stop_idx >= 0 and last_userside_idx > last_stop_idx:
        return state.TOOL_PENDING
    if stop == "tool_use":
        return state.TOOL_PENDING
    if stop in ("end_turn", "stop_sequence", "max_tokens"):
        return state.COMPLETED
    return state.IDLE_WAIT


# Codex emits dozens of reasoning/function_call/token_count records per turn, so the
# last task_started/task_complete marker is routinely >80 lines (often >200) from EOF.
# Scan a wide tail and judge by the POSITION of the last marker, not a count in a tiny
# window (else an active or just-finished turn misclassifies as IDLE_WAIT). The loop's
# idle gate (idle >= cfg.idle, default 180s) means classify only runs once writing has
# stopped, so STALLED here = a turn that started and then went silent = genuinely stuck.
CODEX_SCAN_LINES = 4000


def _classify_codex(path: str) -> str:
    objs = _tail_json(path, n=CODEX_SCAN_LINES)
    for o in objs:
        payload = o.get("payload")
        if not isinstance(payload, dict):        # a list/str payload would crash .get() below
            continue
        info = payload.get("info")
        rl = (info.get("rate_limits") if isinstance(info, dict) else None) or payload.get("rate_limits") or {}
        if isinstance(rl, dict) and rl.get("rate_limit_reached_type") is not None:
            return state.RATE_LIMIT
    last_started = last_completed = -1
    realtime = None
    for i, o in enumerate(objs):
        payload = o.get("payload")
        if not isinstance(payload, dict):
            continue
        t = payload.get("type")
        if t == "task_complete":
            last_completed = i
        elif t == "task_started":
            last_started = i
        if payload.get("realtime_active") is not None:
            realtime = payload.get("realtime_active")
    if realtime is True:
        return state.TOOL_PENDING
    if last_started > last_completed:        # a turn started after the last completion -> not done
        return state.STALLED
    if last_completed >= 0:
        return state.COMPLETED
    return state.IDLE_WAIT


_AG_RATE_RE = re.compile(r"\b(429|503)\b|rate.?limit|overload", re.I)
_AG_ERR_RE = re.compile(r"\b5\d\d\b|\berror\b|exception|failed", re.I)


def _antigravity_log_state() -> Optional[str]:
    """Peek the (recent) antigravity-cli log for HTTP-level signals. Conservative."""
    logp = _home(".gemini", "antigravity-cli", "cli.log")
    if not os.path.exists(logp) or idle_seconds(logp) > 1800:
        return None
    tail = "\n".join(_tail_lines(logp, 120))
    if _AG_RATE_RE.search(tail):
        return state.RATE_LIMIT
    return None


def _classify_antigravity(path: str) -> str:
    """SQLite-based. status enum is not reverse-engineered yet; we lean on the
    strongest unambiguous signal (error_details blob present) + the HTTP log.
    Conservative on purpose — a wrong ERROR would cause a misfire."""
    log_state = _antigravity_log_state()
    if log_state == state.RATE_LIMIT:
        return state.RATE_LIMIT
    try:
        con = sqlite3.connect("file:%s?mode=ro" % path, uri=True, timeout=1)
        try:
            row = con.execute(
                "SELECT status, length(error_details) FROM steps "
                "ORDER BY idx DESC LIMIT 1").fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return state.UNKNOWN
    if row:
        _status, err_len = row
        if err_len and err_len > 0:
            return state.ERROR
    # status int meaning unverified -> do not guess COMPLETED/TOOL_PENDING
    return state.IDLE_WAIT


def classify(engine: str, path: str) -> str:
    if not path or not os.path.exists(path):
        return state.UNKNOWN
    if engine == "claude":
        return _classify_claude(path)
    if engine == "codex":
        return _classify_codex(path)
    if engine == "antigravity":
        return _classify_antigravity(path)
    return state.UNKNOWN
