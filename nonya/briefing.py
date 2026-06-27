"""briefing — a wake-up after-action report for the human supervisor.

nonya runs unattended (overnight, while you sleep). When you come back you do
not want to replay a poll loop; you want one screen that says: what did nonya
*do*, *why*, what still *needs you*, and was the final work actually *verified*.

This module is PURE FORMATTING over data already on disk. It never touches the
network and never reads the agent's transcript directly — it reads only:

  - the ledger (``ledger.jsonl`` via :mod:`nonya.ledger`): the append-only
    record of every intervention nonya made, with its reason;
  - ``state.json`` (via :mod:`nonya.status`): nonya's last published live state.

Hard invariants honoured here:
  * No network, no subprocess, no agent-file mutation — read + format only.
  * Degrades gracefully: a missing/empty ledger or status yields a valid
    (if sparse) report rather than an exception.
  * Redaction: every string that lands in the output is passed through a
    secret-scrubber so a key/token that leaked into a ledger ``reason`` is
    never re-printed in the briefing.

Ledger record contract (the subset this report uses; extra keys are ignored):

    {"ts": 1718800000,            # unix seconds (int/float)
     "session": "claude:proj-x",  # stable session id; groups the timeline
     "event": "inject",           # see EVENT_* below
     "state": "STALLED",          # nonya.state constant at decision time
     "reason": "no tool output 9m; nudged with file:line",  # WHY (human text)
     "outcome": "recovered"}      # optional: recovered|stuck|pass|fail|...

Recognised ``event`` values (unknown events are still listed, never dropped):
    inject      — nonya sent a corrective instruction
    stall       — nonya observed a stall (recovered or not)
    escalate    — nonya gave up auto-recovery and pinged the human
    verify      — a correctness/verify check ran (outcome pass|fail)
    done        — the session reported completion
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

from .i18n import t

# --- secret redaction -------------------------------------------------------
# Conservative: catch the common shapes (sk-..., ghp_..., AKIA..., bearer
# tokens, KEY=VALUE assignments, long hex/base64 blobs). When unsure we prefer
# to mask — a briefing that over-redacts is safe; one that leaks a key is not.
_SECRET_PATTERNS = [
    re.compile(r"\b(?:sk|rk|pk)-[A-Za-z0-9_-]{8,}", re.I),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}"),
    re.compile(r"\bAKIA[0-9A-Z]{12,}"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{8,}"),
    re.compile(r"(?i)\b(?:bearer|token|api[_-]?key|secret|password|passwd)\b"
               r"\s*[:=]\s*\S+"),
    re.compile(r"(?i)\b[A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD)\s*=\s*\S+"),
]
_REDACTED = "[REDACTED]"


def _redact(text: object) -> str:
    """Return ``text`` as a single-line string with likely secrets masked.

    Always coerces to str and strips newlines (a ledger reason must never
    inject extra markdown lines). Safe on None / numbers / odd types.
    """
    s = "" if text is None else str(text)
    s = s.replace("\r", " ").replace("\n", " ")
    for pat in _SECRET_PATTERNS:
        s = pat.sub(_REDACTED, s)
    return s.strip()


# --- ledger access (graceful) ----------------------------------------------
def _read_ledger(state_dir: str) -> List[dict]:
    """Load ledger records, preferring :mod:`nonya.ledger`.

    Falls back to reading ``ledger.jsonl`` directly so the briefing still works
    if the ledger module is unavailable. Never raises.
    """
    try:
        from . import ledger  # type: ignore
        recs = ledger.read(state_dir)
        return [r for r in recs if isinstance(r, dict)]
    except Exception:
        pass
    return _read_jsonl_fallback(state_dir)


def _read_jsonl_fallback(state_dir: str) -> List[dict]:
    import json
    import os

    path = os.path.join(state_dir, "ledger.jsonl")
    out: List[dict] = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if isinstance(obj, dict):
                    out.append(obj)
    except OSError:
        return []
    return out


# --- event vocabulary -------------------------------------------------------
EVENT_INJECT = "inject"
EVENT_STALL = "stall"
EVENT_ESCALATE = "escalate"
EVENT_VERIFY = "verify"
EVENT_DONE = "done"

# Outcomes that mean "a human should look at this".
_NEEDS_YOU_OUTCOMES = {"stuck", "fail", "failed", "gave_up", "blocked", "error"}


def _ts(rec: dict) -> float:
    try:
        return float(rec.get("ts", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def _session_of(rec: dict) -> str:
    return _redact(rec.get("session") or rec.get("target") or "unknown")


def _group_sessions(recs: List[dict]) -> Dict[str, List[dict]]:
    """Group records by session id, each timeline sorted oldest->newest."""
    groups: Dict[str, List[dict]] = {}
    for r in recs:
        groups.setdefault(_session_of(r), []).append(r)
    for sid in groups:
        groups[sid].sort(key=_ts)
    return groups


# --- per-session classification ---------------------------------------------
# A session is ranked into one of three buckets for ordering:
#   needs-you (2) > stalled (1) > shipped (0).
_RANK_NEEDS_YOU = 2
_RANK_STALLED = 1
_RANK_SHIPPED = 0
_RANK_LABEL = {2: "needs-you", 1: "stalled", 0: "shipped"}


def _classify_session(recs: List[dict]) -> int:
    """Bucket a session's records into the ordering rank (higher = more urgent)."""
    rank = _RANK_SHIPPED
    saw_done = False
    saw_verify_pass = False
    for r in recs:
        event = str(r.get("event") or "").lower()
        outcome = str(r.get("outcome") or "").lower()
        if event == EVENT_ESCALATE:
            return _RANK_NEEDS_YOU
        if outcome in _NEEDS_YOU_OUTCOMES:
            rank = max(rank, _RANK_NEEDS_YOU)
        if event == EVENT_VERIFY:
            if outcome in ("fail", "failed"):
                rank = max(rank, _RANK_NEEDS_YOU)
            elif outcome in ("pass", "passed", "ok"):
                saw_verify_pass = True
        if event == EVENT_STALL and outcome not in ("recovered", "resolved"):
            rank = max(rank, _RANK_STALLED)
        if event == EVENT_DONE or outcome in ("done", "completed"):
            saw_done = True
    # A "done" session that never verified is not yet trustworthy -> stalled,
    # not shipped. nonya verifies before accepting "done".
    if rank == _RANK_SHIPPED and saw_done and not saw_verify_pass:
        rank = max(rank, _RANK_STALLED)
    return rank


def _counts(recs: List[dict]) -> Dict[str, int]:
    c = {"inject": 0, "stall": 0, "recovered": 0, "escalate": 0,
         "verify_pass": 0, "verify_fail": 0}
    for r in recs:
        event = str(r.get("event") or "").lower()
        outcome = str(r.get("outcome") or "").lower()
        if event == EVENT_INJECT:
            c["inject"] += 1
        elif event == EVENT_STALL:
            c["stall"] += 1
            if outcome in ("recovered", "resolved"):
                c["recovered"] += 1
        elif event == EVENT_ESCALATE:
            c["escalate"] += 1
        elif event == EVENT_VERIFY:
            if outcome in ("pass", "passed", "ok"):
                c["verify_pass"] += 1
            elif outcome in ("fail", "failed"):
                c["verify_fail"] += 1
        if outcome in ("recovered", "resolved") and event != EVENT_STALL:
            c["recovered"] += 1
    return c


# --- rendering --------------------------------------------------------------
def _fmt_clock(ts: float) -> str:
    """HH:MM in local time; empty string when unknown. Pure stdlib."""
    if ts <= 0:
        return ""
    try:
        import time
        return time.strftime("%H:%M", time.localtime(ts))
    except (OSError, ValueError, OverflowError):
        return ""


def _render_event(rec: dict) -> str:
    """One timeline bullet: `- HH:MM EVENT [STATE] — reason (-> outcome)`."""
    clock = _fmt_clock(_ts(rec))
    event = _redact(rec.get("event") or "event")
    st = _redact(rec.get("state") or "")
    reason = _redact(rec.get("reason") or "")
    outcome = _redact(rec.get("outcome") or "")

    head = "- "
    if clock:
        head += clock + " "
    head += event.upper()
    if st:
        head += " [%s]" % st
    if reason:
        head += " — " + reason
    if outcome:
        head += " (-> %s)" % outcome
    return head


def _render_session(sid: str, recs: List[dict], rank: int) -> List[str]:
    c = _counts(recs)
    label = t("briefing.rank." + {2: "needs", 1: "stalled", 0: "shipped"}[rank])
    summary = t("briefing.summary", c["inject"], c["stall"], c["recovered"],
                c["escalate"], c["verify_pass"], c["verify_fail"])
    lines = ["### %s  _(%s)_" % (sid, label), "", summary, ""]
    for r in recs:
        lines.append(_render_event(r))
    lines.append("")
    return lines


def top_verdict(state_dir: str) -> str:
    """One-line headline for the whole night. Safe on empty data.

    Reflects the most urgent bucket present so a glance tells you whether to
    get up: needs-you wins over stalled wins over shipped.
    """
    recs = _read_ledger(state_dir)
    if not recs:
        st = _read_status(state_dir)
        live = _redact(st.get("status") or "")
        if live:
            return t("briefing.verdict.nolive", live)
        return t("briefing.verdict.nothing")

    groups = _group_sessions(recs)
    ranks = {sid: _classify_session(rs) for sid, rs in groups.items()}
    n_needs = sum(1 for v in ranks.values() if v == _RANK_NEEDS_YOU)
    n_stalled = sum(1 for v in ranks.values() if v == _RANK_STALLED)
    n_shipped = sum(1 for v in ranks.values() if v == _RANK_SHIPPED)
    total = len(groups)

    if n_needs:
        return t("briefing.verdict.needs", n_needs, total)
    if n_stalled:
        return t("briefing.verdict.stalled", n_stalled, total, n_shipped)
    return t("briefing.verdict.shipped", total)


def _read_status(state_dir: str) -> dict:
    try:
        from . import status
        st = status.read(state_dir)
        return st if isinstance(st, dict) else {}
    except Exception:
        return {}


def build_briefing(state_dir: str) -> str:
    """Assemble the markdown wake-up after-action report.

    Ordering: sessions are grouped and ranked needs-you > stalled > shipped;
    within a rank, the most recently active session comes first. Pure
    formatting over the ledger + status.json — no network, no transcript reads.
    """
    recs = _read_ledger(state_dir)
    status_obj = _read_status(state_dir)

    out: List[str] = ["# " + t("briefing.title"), "", top_verdict(state_dir), ""]

    live = _redact(status_obj.get("status") or "")
    target = _redact(status_obj.get("target") or "")
    if live or target:
        bits = []
        if target:
            bits.append("target %s" % target)
        if live:
            bits.append("last state %s" % live)
        out.append("_" + ", ".join(bits) + "_")
        out.append("")

    if not recs:
        out.append(t("briefing.empty"))
        out.append("")
        return "\n".join(out)

    groups = _group_sessions(recs)
    # Rank by urgency desc, then by most-recent activity desc.
    def _last_ts(sid: str) -> float:
        rs = groups[sid]
        return _ts(rs[-1]) if rs else 0.0

    ordered = sorted(
        groups.keys(),
        key=lambda sid: (_classify_session(groups[sid]), _last_ts(sid)),
        reverse=True,
    )

    out.append("## " + t("briefing.sessions", len(ordered)))
    out.append("")
    for sid in ordered:
        rank = _classify_session(groups[sid])
        out.extend(_render_session(sid, groups[sid], rank))

    return "\n".join(out).rstrip() + "\n"
