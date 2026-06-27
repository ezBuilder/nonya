"""5-state supervisory classifier — the FOUNDATION other progress/correctness
features route on. (Historically four states; WORKING was added so an in-progress
turn is never mistaken for actionable. The function is still named `classify4`.)

`classify4(engine, path)` reduces an on-disk transcript to exactly one of five
ACTION-shaped states (module constants below). It is intentionally narrower and
more decision-oriented than `detect.classify` (which has 7 diagnostic states);
this layer answers "what should the supervisor DO?" and is built on top of the
existing `detect.classify` signal plus transcript-tail inspection.

    WAITING  — the tail ends on a question / permission-ask / AskUserQuestion.
               Callers must not send a generic nudge here. In auto mode they may
               answer only through a separate conservative auto-unblock policy.
    DONE     — a clean end_turn / task_complete with no pending user-side record.
    STUCK    — error / 429 / rate-limit, or a tool_use with no matching result
               past a time cap (idle seconds).
    LOOPING  — the same tool name + near-identical args repeated K+ times within
               a sliding window of the last N records (fingerprint = normalized
               hash of tool+args; timestamps/ids ignored).
    WORKING  — an in-progress / quiet turn below the hang cap. NEVER actionable
               (never nudged); only becomes STUCK once it is silent past the cap.

HARD INVARIANTS upheld here:
  * No network calls (pure transcript inspection over stdlib).
  * Read-only: never mutates the transcript or any agent file.
  * Safe-by-default: when uncertain we fall back to the closest non-actionable
    or conservative state rather than risking a misfire.
  * No secret leakage: fingerprints are one-way hashes; nothing is written out.
  * Pure stdlib (json, hashlib, re, os + reuse of detect/state).
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import List, Optional, Tuple

from . import detect, state

# ---- 4-state vocabulary (module constants) --------------------------------
DONE = "done"
WAITING = "waiting"
STUCK = "stuck"
LOOPING = "looping"
WORKING = "working"   # in-progress / quiet but NOT past the hang cap -> NOT actionable (never nudge)

ALL = {DONE, WAITING, STUCK, LOOPING, WORKING}

# Loop-detection defaults: K identical fingerprints inside the last N records.
LOOP_WINDOW = 12          # N: sliding window of recent tool calls to inspect
LOOP_THRESHOLD = 4        # K: occurrences of one fingerprint that flag a loop
# Time cap (seconds): a stalled tool_use older than this is STUCK, not pending.
STUCK_IDLE_CAP = 1800

# A question/permission tail looks like one of these (case-insensitive). Kept
# conservative — we only want HIGH-confidence asks, since WAITING suppresses the
# nudge entirely and a false WAITING would silently stall an otherwise-fine run.
_QUESTION_RE = re.compile(
    r"(\?\s*$)"                               # trailing question mark
    r"|\bmay i\b|\bshould i\b|\bdo you want\b|\bwould you like\b"
    r"|\bwhich (one|option)\b|\bplease (confirm|choose|select|clarify)\b"
    r"|\bcan i (proceed|continue|go ahead)\b"
    r"|\bneed your (approval|permission|input|confirmation)\b"
    r"|\bawaiting (your )?(approval|confirmation|input|response)\b",
    re.I,
)

# Tool names that, by their TYPE, are an explicit user-facing prompt. These are
# unambiguous permission/question asks regardless of text content.
_ASK_TOOL_RE = re.compile(
    r"askuserquestion|ask_user|request_(permission|approval|input)"
    r"|user_(question|prompt|input)|elicit",
    re.I,
)


# ---- fingerprint helper ----------------------------------------------------

def loop_fingerprint(tool: str, args) -> str:
    """One-way hash of a tool invocation, normalized so that near-identical
    calls collide while volatile fields (timestamps/ids) are ignored.

    The same (tool, args) repeated verbatim -> identical fingerprint -> loop.
    Distinct args (e.g. editing different files) -> distinct fingerprints.
    Returns a short hex digest; never the raw args (no secret leakage).
    """
    norm = _normalize_args(args)
    payload = (tool or "").strip().lower() + "\x00" + json.dumps(
        norm, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8", "replace")).hexdigest()[:16]


# Keys that vary every call and would defeat loop detection if hashed in.
_VOLATILE_KEYS = frozenset({
    "id", "tool_use_id", "tooluseid", "call_id", "callid", "uuid",
    "timestamp", "ts", "time", "created_at", "request_id", "requestid",
    "session_id", "sessionid", "parent_uuid", "parentuuid",
})


def _normalize_args(value):
    """Recursively strip volatile keys and lowercase scalar strings so that
    cosmetic differences (id churn, whitespace) don't split a real loop."""
    if isinstance(value, dict):
        return {
            k: _normalize_args(v)
            for k, v in sorted(value.items())
            if k.lower() not in _VOLATILE_KEYS
        }
    if isinstance(value, list):
        return [_normalize_args(v) for v in value]
    if isinstance(value, str):
        return value.strip()
    return value


# ---- tool-call extraction (engine-aware, read-only) ------------------------

def _iter_tool_calls(engine: str, objs: List[dict]) -> List[Tuple[str, object]]:
    """Return [(tool_name, args), ...] in transcript order for the given tail.

    Supports the two JSONL engines whose formats are verified in detect.py.
    Antigravity is SQLite (no per-call args reverse-engineered yet) so it
    yields nothing here -> loop detection degrades gracefully to "no signal".
    """
    out: List[Tuple[str, object]] = []
    if engine == "claude":
        for o in objs:
            if o.get("isSidechain") is True:
                continue
            msg = o.get("message")
            content = msg.get("content") if isinstance(msg, dict) else None
            if not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    out.append((block.get("name") or "", block.get("input")))
    elif engine == "codex":
        for o in objs:
            payload = o.get("payload")
            if not isinstance(payload, dict):
                continue
            ptype = payload.get("type")
            if ptype in ("function_call", "tool_call", "local_shell_call", "custom_tool_call"):
                name = payload.get("name") or payload.get("tool") or ""
                args = payload.get("arguments")
                if args is None:
                    args = payload.get("args") or payload.get("input")
            elif ptype in ("mcp_tool_call_begin", "mcp_tool_call_end"):
                inv = payload.get("invocation")
                inv = inv if isinstance(inv, dict) else {}
                name = inv.get("tool") or inv.get("server") or payload.get("name") or ""
                args = inv.get("arguments") if inv else payload.get("arguments")
            else:
                continue
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    pass  # keep the raw string; still fingerprints fine
            out.append((name, args))
    return out


# ---- WAITING detection -----------------------------------------------------

def _last_assistant_text(engine: str, objs: List[dict]) -> Optional[str]:
    """Concatenated text of the LAST assistant turn (claude/codex), or None."""
    if engine == "claude":
        for o in reversed(objs):
            if o.get("isSidechain") is True:
                continue
            msg = o.get("message")
            if not isinstance(msg, dict) or msg.get("role") != "assistant":
                continue
            content = msg.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts = [b.get("text", "") for b in content
                         if isinstance(b, dict) and b.get("type") == "text"]
                joined = "\n".join(p for p in parts if p)
                if joined:
                    return joined
            return None
    elif engine == "codex":
        for o in reversed(objs):
            payload = o.get("payload")
            if not isinstance(payload, dict):
                continue
            ptype = payload.get("type")
            if ptype not in ("agent_message", "message", "assistant_message"):
                continue
            if ptype == "message" and payload.get("role") not in (None, "assistant"):
                continue
            msg = payload.get("message") or payload.get("text")
            if isinstance(msg, str) and msg.strip():
                return msg
            content = payload.get("content")   # canonical codex assistant turn: content[].output_text
            if isinstance(content, list):
                joined = "\n".join(
                    b.get("text", "") for b in content
                    if isinstance(b, dict) and b.get("type") in ("output_text", "text") and b.get("text"))
                if joined.strip():
                    return joined
    return None


def _tail_is_question(engine: str, objs: List[dict]) -> bool:
    """True iff the transcript tail ends on a user-facing question/permission
    ask. Two signals: an explicit ask-tool as the last tool call, or the last
    assistant text reading as a question. Both are HIGH-confidence so WAITING
    suppresses the nudge only when an answer is genuinely expected."""
    calls = _iter_tool_calls(engine, objs)
    if calls:
        last_name = (calls[-1][0] or "")
        if _ASK_TOOL_RE.search(last_name):
            # Only treat as WAITING if no tool RESULT followed it (claude only;
            # codex args don't carry a paired result in this tail cheaply).
            if engine == "claude" and _has_unmatched_tool_use(objs):
                return True
            if engine != "claude":
                return True
    text = _last_assistant_text(engine, objs)
    if text and _QUESTION_RE.search(text.strip()):
        return True
    return False


def waiting_text(engine: str, path: str) -> str:
    """Return the last assistant question text when the tail is WAITING.

    Empty means either the tail is not waiting, or the waiting signal came from a
    structured permission tool without a plain text question. That path should be
    handled by tool-specific hooks, not by guessing.
    """
    if not path or not detect.os.path.exists(path):
        return ""
    objs = detect._tail_json(path, n=detect.CODEX_SCAN_LINES if engine == "codex"
                             else detect.TAIL_LINES)
    if not _tail_is_question(engine, objs):
        return ""
    return (_last_assistant_text(engine, objs) or "").strip()


# ---- STUCK: unmatched tool_use past the time cap ---------------------------

def _has_unmatched_tool_use(objs: List[dict]) -> bool:
    """Claude: a tool_use block with no later tool_result for its id => the
    agent asked to run a tool and nothing came back. Read-only id pairing."""
    pending = set()
    for o in objs:
        if o.get("isSidechain") is True:
            continue
        msg = o.get("message")
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                tid = block.get("id")
                if tid:
                    pending.add(tid)
            elif block.get("type") == "tool_result":
                tid = block.get("tool_use_id")
                pending.discard(tid)
    return bool(pending)


# ---- LOOPING ---------------------------------------------------------------

def _detect_loop(engine: str, objs: List[dict],
                 window: int = LOOP_WINDOW, threshold: int = LOOP_THRESHOLD) -> bool:
    """True iff one tool fingerprint occurs `threshold`+ times within the last
    `window` tool calls."""
    calls = _iter_tool_calls(engine, objs)
    if not calls:
        return False
    recent = calls[-window:]
    counts = {}
    for name, args in recent:
        fp = loop_fingerprint(name, args)
        counts[fp] = counts.get(fp, 0) + 1
        if counts[fp] >= threshold:
            return True
    return False


# ---- top-level 4-state classifier -----------------------------------------

def classify4(engine: str, path: str,
              idle: Optional[int] = None,
              window: int = LOOP_WINDOW,
              threshold: int = LOOP_THRESHOLD,
              stuck_idle_cap: int = STUCK_IDLE_CAP) -> str:
    """Reduce a transcript to one of DONE | WAITING | STUCK | LOOPING | WORKING.

    Routing (priority order, safest-first):
      1. STUCK  — base classifier reports ERROR/RATE_LIMIT (hard failure), OR
                  a tool_use is unmatched past the idle time cap.
      2. WAITING — tail ends on a question / permission-ask / AskUserQuestion.
                   Checked BEFORE LOOPING so a legitimate repeated *ask* is not
                   mislabeled. Generic nudges are unsafe here; any auto-answer
                   must come from a conservative auto-unblock policy.
      3. LOOPING — same tool+args repeated K+ times in the last N records.
      4. DONE    — base classifier reports COMPLETED with no pending user record.
    Fallback when none apply: STUCK (the conservative, surfaceable state — a run
    that is neither done, asking, nor visibly looping but also not progressing
    deserves human/escalation attention rather than a blind nudge).

    `idle` is the transcript idle seconds; when None it is measured from mtime.
    No network, no mutation, degrades gracefully on unknown engines.
    """
    if not path or not detect.os.path.exists(path):
        return STUCK
    if idle is None:
        idle = detect.idle_seconds(path)

    base = detect.classify(engine, path)
    objs = detect._tail_json(path, n=detect.CODEX_SCAN_LINES if engine == "codex"
                             else detect.TAIL_LINES)

    # 1) Hard-failure signals (error / rate-limit) are ALWAYS immediately STUCK —
    #    a real failure is actionable now, not after the hang cap.
    if base in (state.ERROR, state.RATE_LIMIT):
        return STUCK

    # 2) WAITING before LOOPING: a pending question must never be nudged.
    if _tail_is_question(engine, objs):
        return WAITING

    # 3) LOOPING: repeated identical tool calls (actionable regardless of idle).
    if _detect_loop(engine, objs, window=window, threshold=threshold):
        return LOOPING

    # 4) DONE: a clean completion with nothing pending (base already guards the
    #    "newer user-side record" resumption case -> TOOL_PENDING, not COMPLETED).
    if base == state.COMPLETED:
        return DONE

    # 5) Everything else is an IN-PROGRESS / quiet turn (STALLED, TOOL_PENDING, an
    #    unmatched tool_use, IDLE_WAIT). It is NOT stuck until it has been silent
    #    PAST the hang cap — a long-but-live turn (slow tool, deep reasoning) must
    #    not be misfired on. Below the cap it is WORKING (never nudged); only once
    #    idle exceeds the cap does a started-but-silent turn become STUCK.
    if idle > stuck_idle_cap:
        return STUCK
    return WORKING
