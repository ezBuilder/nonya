"""Rate-limit aware pacing (pure parsing + arithmetic, NO network).

Folds into the supervise loop so a rate-limited session is NOT re-nudged: when
the watched engine is throttled, nudging it again just burns quota and noise.
Instead we compute *when* to resume and let the loop wait until then.

Two sources for the resume time, in priority order:
  1. An EXPLICIT reset window parsed from the transcript — codex
     `token_count…rate_limits.{primary,secondary}.resets_in_seconds`
     (or `resets_at` epoch), or a claude error carrying a `retry-after` /
     `retryAfter` seconds field or an HTTP-style "Retry-After" string.
  2. Otherwise an EXPONENTIAL BACKOFF schedule keyed by how many times we have
     already been throttled this stretch: 60s, 5m, 15m, 1h (capped).

Design invariants honored here:
  - NO network, no I/O beyond reading the transcript file. Pure stdlib.
  - Never raise into the loop: every public helper catches + degrades to the
    SAFE answer (rate-limited? -> treat conservatively; resume? -> backoff).
  - Detection reuses ``detect.classify`` so the RATE_LIMIT verdict stays in one
    place; we only parse extra *reset-window* fields here.

Public API:
  is_rate_limited(engine, path) -> bool
  resume_at(engine, path, now_ts, attempt=0, base=...) -> (epoch_seconds, "HH:MM")
  should_pace(engine, path, now_ts, last_resume_ts=None) -> bool
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import List, Optional, Tuple

from . import detect, state

# Exponential-ish backoff schedule (seconds) when no explicit window is given.
# Index by throttle attempt count; clamp to the last (the cap).
BACKOFF_SCHEDULE = (60, 300, 900, 3600)  # 1m, 5m, 15m, 1h
BACKOFF_CAP = BACKOFF_SCHEDULE[-1]

# A sane ceiling on any explicit window we trust (24h) so a bogus huge value
# can't wedge the loop forever.
MAX_WINDOW = 24 * 3600

_RETRY_AFTER_RE = re.compile(r"retry[\s_-]?after[\"'\s:=]+(\d+(?:\.\d+)?)", re.I)


# --- transcript tail (bounded, best-effort) --------------------------------

def _tail_records(engine: str, path: str) -> List[dict]:
    """Reuse detect's bounded JSONL tail. antigravity is SQLite -> no records
    (we degrade to backoff for it, which is the safe behavior)."""
    try:
        if engine == "antigravity":
            return []
        n = detect.CODEX_SCAN_LINES if engine == "codex" else detect.TAIL_LINES
        return detect._tail_json(path, n=n)
    except Exception:
        return []


# --- reset-window extraction ------------------------------------------------

def _from_rate_limits(rl: dict, now_ts: float) -> Optional[float]:
    """Pull a relative seconds-until-reset from a codex rate_limits block.

    Real shape: {"primary": {"used_percent": .., "window_minutes": ..,
                             "resets_in_seconds": N}, "secondary": {...}}.
    We take the MAX across windows (must clear the longest one to resume) but
    only over windows that actually report a reset. Each window may carry a
    relative `resets_in_seconds` OR an absolute `resets_at` epoch; both are
    normalized to a relative delta vs ``now_ts``.
    """
    if not isinstance(rl, dict):
        return None
    best: Optional[float] = None
    for key in ("primary", "secondary"):
        win = rl.get(key)
        if not isinstance(win, dict):
            continue
        secs = _coerce_secs(win.get("resets_in_seconds"))
        if secs is None:
            secs = _coerce_secs(win.get("reset_in_seconds"))
        if secs is None:
            secs = _coerce_secs(win.get("resets_in"))
        if secs is None:
            secs = _from_resets_at(win, now_ts)
        if secs is not None:
            best = secs if best is None else max(best, secs)
    # also tolerate a flat resets_in_seconds at the top level
    flat = _coerce_secs(rl.get("resets_in_seconds"))
    if flat is not None:
        best = flat if best is None else max(best, flat)
    return best


def _coerce_secs(v) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f < 0:
        return None
    return f


def _explicit_window(engine: str, path: str, now_ts: float) -> Optional[float]:
    """Return seconds-until-resume from an explicit reset signal, or None.

    Returns a RELATIVE seconds delta (>= 0). Absolute `resets_at` epochs are
    converted to a delta against now_ts here. Scans only the rate-limit /
    error records, newest-wins.
    """
    records = _tail_records(engine, path)
    if not records:
        return None

    found: Optional[float] = None
    for o in records:
        if not isinstance(o, dict):
            continue

        # codex: payload.info.rate_limits or payload.rate_limits
        payload = o.get("payload")
        if isinstance(payload, dict):
            info = payload.get("info")
            rl = None
            if isinstance(info, dict):
                rl = info.get("rate_limits")
            if rl is None:
                rl = payload.get("rate_limits")
            secs = _from_rate_limits(rl, now_ts) if isinstance(rl, dict) else None
            if secs is not None:
                found = secs  # newest wins (later records overwrite)

        # claude / generic: retry-after style fields anywhere on the record
        for fld in ("retry_after", "retryAfter", "retry-after", "retry_after_seconds"):
            secs = _coerce_secs(o.get(fld))
            if secs is not None:
                found = secs
        # a header-ish string embedded in an error message
        err = o.get("error")
        if isinstance(err, str):
            m = _RETRY_AFTER_RE.search(err)
            if m:
                found = float(m.group(1))
        # absolute reset epoch at record level
        abs_secs = _from_resets_at(o, now_ts)
        if abs_secs is not None:
            found = abs_secs

    if found is None:
        return None
    return _clamp_window(found)


def _from_resets_at(d: dict, now_ts: float) -> Optional[float]:
    """Convert an absolute reset epoch field to a relative delta vs now_ts."""
    for fld in ("resets_at", "reset_at", "resetAt"):
        v = d.get(fld)
        epoch = _coerce_secs(v)
        if epoch is None:
            continue
        # Treat values that look like epoch seconds (far in the future of 2020)
        # as absolute; convert to a delta. Guard negatives.
        if epoch > 1_500_000_000:  # ~2017-07, clearly an absolute epoch
            delta = epoch - now_ts
            return delta if delta > 0 else 0.0
    return None


def _clamp_window(secs: float) -> float:
    if secs < 0:
        return 0.0
    if secs > MAX_WINDOW:
        return float(MAX_WINDOW)
    return secs


# --- backoff ----------------------------------------------------------------

def backoff_seconds(attempt: int, schedule=BACKOFF_SCHEDULE) -> int:
    """Exponential backoff: clamp attempt index into the schedule, cap at last."""
    try:
        i = int(attempt)
    except (TypeError, ValueError):
        i = 0
    if i < 0:
        i = 0
    if i >= len(schedule):
        return int(schedule[-1])
    return int(schedule[i])


# --- public API -------------------------------------------------------------

def is_rate_limited(engine: str, path: str) -> bool:
    """True iff the session is currently rate-limited. Reuses
    detect.classify so the verdict matches the rest of the system; degrades to
    False (the SAFE 'not throttled, proceed normally' answer) on any error."""
    try:
        return detect.classify(engine, path) == state.RATE_LIMIT
    except Exception:
        return False


def resume_at(engine: str, path: str, now_ts: float,
              attempt: int = 0,
              base: int = 60) -> Tuple[int, str]:
    """When to resume a rate-limited session.

    Returns (epoch_seconds, "HH:MM") in local time.
      - If the transcript carries an explicit reset window, resume right after
        it (+ a small 1s cushion so the window has truly elapsed).
      - Otherwise use the exponential backoff schedule keyed by ``attempt``.

    Never raises; on any failure it falls back to ``base`` seconds from now.
    ``base`` only matters when the schedule is unavailable.
    """
    try:
        now = float(now_ts)
    except (TypeError, ValueError):
        now = time.time()

    delta: Optional[float] = None
    try:
        delta = _explicit_window(engine, path, now)
    except Exception:
        delta = None

    if delta is not None:
        wait = delta + 1.0  # cushion: resume just after the window clears
    else:
        try:
            wait = float(backoff_seconds(attempt))
        except Exception:
            wait = float(base)

    epoch = int(now + max(0.0, wait))
    try:
        hhmm = time.strftime("%H:%M", time.localtime(epoch))
    except Exception:
        hhmm = "00:00"
    return epoch, hhmm


def should_pace(engine: str, path: str, now_ts: float,
                last_resume_ts: Optional[float] = None) -> bool:
    """True iff the loop should PAUSE (skip nudging) this cycle.

    Pace when the session is rate-limited AND we have not yet reached a
    previously-computed resume time. If no resume time was scheduled yet, a
    fresh rate-limit means we should pace (the loop will then call resume_at to
    schedule one). Degrades to False (do not pace) on any error — but note the
    loop's own safety still applies; False here just means 'no pacing override'.
    """
    try:
        if not is_rate_limited(engine, path):
            return False
        if last_resume_ts is None:
            return True
        return float(now_ts) < float(last_resume_ts)
    except Exception:
        return False
