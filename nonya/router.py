"""router — multi-session attention router.

When nonya supervises several agent sessions at once, each session writes its
own status file. `status.write(state_dir, ...)` already emits the legacy single
file `<state_dir>/state.json`; for multi-session runs each session additionally
writes `<state_dir>/sessions/<id>.json` with the same shape (at minimum a
`status` field plus a `ts`). This module is the READ side: it scans every
session file (plus the legacy `state.json`), ranks them by how urgently they
need a human, and answers three questions a face / dashboard / CLI cares about:

    rank(state_dir)   -> [{session, status, rank, ts}, ...]  (most urgent first)
    top(state_dir)    -> the single most urgent item, or None
    counts(state_dir) -> {handled, needs_you, looping, stuck, ...}

Priority (most-urgent first), per the supervisor's vocabulary:

    needs-you (waiting) > looping > stuck > verify-failed > working > done

`waiting` means the agent asked a question / permission — a human MUST answer,
so it sorts to the very top. `done` is the least urgent (nothing to do).

HARD INVARIANTS upheld here:
  * No network — pure local file reads over stdlib.
  * Read-only — never writes or mutates any session file.
  * Safe-by-default — an unknown/unreadable status sorts as low urgency rather
    than masquerading as "needs you"; a corrupt or missing file is SKIPPED, not
    crashed on.
  * Never raises into the loop — every public helper catches + degrades to an
    empty / None result.
  * Pure stdlib (json, os + reuse of status/supervise constants).
"""
from __future__ import annotations

import json
import os
import time
from typing import Dict, List, Optional

from . import status, supervise

# Subdirectory (under state_dir) holding one JSON per supervised session.
SESSIONS_DIRNAME = "sessions"

# A session file untouched longer than this = a dead/crashed core; don't route it.
STALE_SECS = 1800
# ts below this isn't a real unix epoch (test fixtures use tiny ts) -> never aged out.
_EPOCH_FLOOR = 1_000_000_000

# ---- status vocabulary -----------------------------------------------------
# The router ranks on these canonical status strings. WAITING/STUCK/LOOPING are
# reused verbatim from supervise so the two layers never drift. The extra
# strings cover states a session file may carry that supervise.classify4 does
# not emit directly (verify-failed from the verify step; an explicit working /
# done heartbeat).
WAITING = supervise.WAITING        # "waiting"  — needs-you: a human must answer
LOOPING = supervise.LOOPING        # "looping"
STUCK = supervise.STUCK            # "stuck"
DONE = supervise.DONE              # "done"
VERIFY_FAILED = "verify-failed"
WORKING = "working"

# Higher number == MORE urgent (sorts first). Anything not in this map is
# treated as the lowest urgency (see _rank_of) so an unexpected/garbage status
# can never outrank a real "needs you".
STATUS_RANK: Dict[str, int] = {
    WAITING: 60,           # needs-you — top priority, a human is blocking
    LOOPING: 50,
    STUCK: 40,
    VERIFY_FAILED: 30,
    WORKING: 20,
    DONE: 10,
}

# Aliases mapping other vocabularies (e.g. status.py's face words, or hyphen /
# underscore spellings) onto the canonical statuses above. Normalized lower.
_STATUS_ALIASES: Dict[str, str] = {
    "needs-you": WAITING,
    "needs_you": WAITING,
    "needsyou": WAITING,
    "ask": WAITING,
    "question": WAITING,
    "scolding": LOOPING,     # the pet "scolds" on a detected loop
    "loop": LOOPING,
    "verify_failed": VERIFY_FAILED,
    "verifyfailed": VERIFY_FAILED,
    "failed": VERIFY_FAILED,
    "watching": WORKING,     # pet "watching" == an active, in-progress run
    "running": WORKING,
    "complete": DONE,
    "completed": DONE,
    "stopped": DONE,
}

# Lowest possible urgency for an unknown status — below every known rank.
_UNKNOWN_RANK = 0


def canonical_status(raw) -> str:
    """Normalize a raw status string to a canonical vocabulary word.

    Lower-cases, strips, and applies known aliases. An unrecognized value is
    returned normalized (lower/stripped) so it is still displayable but will
    score `_UNKNOWN_RANK` in `_rank_of` (safe: never outranks a real signal).
    """
    if not isinstance(raw, str):
        return ""
    s = raw.strip().lower()
    if s in STATUS_RANK:
        return s
    return _STATUS_ALIASES.get(s, s)


def _rank_of(status_str: str) -> int:
    """Urgency score for a canonical status; unknown -> lowest (safe)."""
    return STATUS_RANK.get(status_str, _UNKNOWN_RANK)


def _ts_of(data: dict) -> int:
    """Best-effort integer timestamp from a session record (0 if absent)."""
    ts = data.get("ts")
    try:
        return int(ts)
    except (TypeError, ValueError):
        return 0


def _read_json_file(path: str) -> Optional[dict]:
    """Read one JSON object file. Returns the dict, or None when the file is
    missing, unreadable, or corrupt (caller SKIPS None — never crashes)."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _session_files(state_dir: str) -> List[tuple]:
    """Yield (session_id, path) for every session file plus the legacy state.

    The legacy `<state_dir>/state.json` is included as session id "_legacy" so
    a single-session run is still routed. Per-session files live under
    `<state_dir>/sessions/<id>.json` and take their id from the filename.
    Returns [] on any directory error (degrade, don't raise).
    """
    out: List[tuple] = []
    if not state_dir:
        return out
    # Legacy single-file state (status.FILENAME == "state.json").
    legacy = os.path.join(state_dir, status.FILENAME)
    if os.path.isfile(legacy):
        out.append(("_legacy", legacy))
    sess_dir = os.path.join(state_dir, SESSIONS_DIRNAME)
    try:
        names = sorted(os.listdir(sess_dir))
    except OSError:
        names = []
    for name in names:
        if not name.endswith(".json"):
            continue
        out.append((name[:-len(".json")], os.path.join(sess_dir, name)))
    return out


def _items(state_dir: str) -> List[dict]:
    """Build the raw, unsorted list of routable items. Corrupt/missing files
    and records with no usable status are silently skipped."""
    now = int(time.time())
    by_sid: Dict[str, dict] = {}   # dedup: the legacy state.json mirrors one live
    order: List[str] = []          # session, so same sid -> keep only the freshest
    for session_id, path in _session_files(state_dir):
        data = _read_json_file(path)
        if data is None:
            continue
        canon = canonical_status(data.get("status"))
        if not canon:
            continue  # no status at all -> nothing to route on; skip
        ts = _ts_of(data)
        if ts > _EPOCH_FLOOR and (now - ts) > STALE_SECS:
            continue  # stale/crashed session file — don't route a dead session
        # Prefer an explicit session id in the file, else the filename-derived one.
        sid = data.get("session") or data.get("id") or session_id
        item = {"session": sid, "status": canon, "rank": _rank_of(canon), "ts": ts}
        prev = by_sid.get(sid)
        if prev is None:
            by_sid[sid] = item
            order.append(sid)
        elif ts >= prev["ts"]:
            by_sid[sid] = item     # a fresher signal for the same session wins (no double-count)
    return [by_sid[s] for s in order]


def rank(state_dir: str) -> List[dict]:
    """Return all routable session items, most-urgent first.

    Each item is {session, status, rank, ts}. Sort key: higher `rank` first;
    ties broken by NEWER `ts` first (a fresher signal wins), then session id
    for stable ordering. Never raises — returns [] on any failure.
    """
    try:
        items = _items(state_dir)
    except Exception:
        return []
    items.sort(key=lambda it: (-it["rank"], -it["ts"], str(it["session"])))
    return items


def top(state_dir: str) -> Optional[dict]:
    """The single most-urgent item, or None when nothing is routable."""
    ranked = rank(state_dir)
    return ranked[0] if ranked else None


def counts(state_dir: str) -> Dict[str, int]:
    """Tally sessions for a dashboard summary.

    Returns a dict with one key per canonical status (count of sessions in that
    state) plus two roll-ups:
        total     — total routable sessions
        needs_you — sessions a human must act on now (waiting)
        handled   — sessions needing no human action right now (working/done)
    Unknown statuses are counted under their normalized name but excluded from
    the needs_you / handled roll-ups (safe: not claimed as either). Never raises.
    """
    out: Dict[str, int] = {
        "total": 0,
        "needs_you": 0,
        "handled": 0,
        WAITING: 0,
        LOOPING: 0,
        STUCK: 0,
        VERIFY_FAILED: 0,
        WORKING: 0,
        DONE: 0,
    }
    try:
        items = _items(state_dir)
    except Exception:
        return out
    for it in items:
        st = it["status"]
        out["total"] += 1
        out[st] = out.get(st, 0) + 1
        if st == WAITING:
            out["needs_you"] += 1
        elif st in (WORKING, DONE):
            out["handled"] += 1
    return out
