"""budget — the autonomy 'leash' you set before going AFK.

A tiny, pure-config primitive that bounds how much the supervisor is allowed to
do on its own while you are away. Nothing here touches the network or the
detection/inject hot path; it only reads a small JSON file and answers a few
yes/no questions about it.

The leash file (JSON object) lives at $NONYA_BUDGET, else <state_dir>/budget.json:

    {
      "auto_inject":    true,                 # false => alert-only (never type)
      "max_recoveries": 5,                     # mandatory escalation after N auto
                                               #   recoveries this session
                                               #   (maps onto loop give_up_after)
      "spend_ceiling":  100,                   # hard max number of nudges
      "quiet_hours":    {"start": "01:00",     # during this window, recover
                         "end":   "07:00"},    #   SILENTLY; only escalate true
                                               #   blockers
      "panic_word":     "STOP"                 # if seen in transcript => force
                                               #   immediate escalation
    }

Every field is optional; a missing/garbage file yields safe defaults. The
guiding bias when anything is uncertain is the SAFE action: do not auto-inject,
do not assume we are outside quiet hours by accident, and never raise out of
these helpers into the loop (catch + degrade).
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Optional

FILENAME = "budget.json"
ENV_PATH = "NONYA_BUDGET"

# Safe defaults. The leash is "tight" by default: we DON'T auto-inject unless
# the operator opted in, recoveries are capped low, and there is no quiet
# window or panic word until one is configured.
DEFAULT_AUTO_INJECT = False
DEFAULT_MAX_RECOVERIES = 3
DEFAULT_SPEND_CEILING = 50

_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


@dataclass
class Budget:
    """The decoded autonomy leash. All fields have safe defaults."""

    auto_inject: bool = DEFAULT_AUTO_INJECT
    max_recoveries: int = DEFAULT_MAX_RECOVERIES
    spend_ceiling: int = DEFAULT_SPEND_CEILING
    # {"start": "HH:MM", "end": "HH:MM"} or None when no quiet window is set.
    quiet_hours: Optional[dict] = None
    panic_word: str = ""

    def give_up_after(self) -> int:
        """Map the leash onto the loop's give_up_after: escalate after N auto
        recoveries. Kept as a method so the caller has one obvious hook."""
        return self.max_recoveries


# --- loading ------------------------------------------------------------------

def _budget_path(state_dir: str = "") -> str:
    """Resolve the leash file path: $NONYA_BUDGET wins, else <state_dir>/budget.json."""
    env = os.environ.get(ENV_PATH, "").strip()
    if env:
        return env
    if state_dir:
        return os.path.join(state_dir, FILENAME)
    return FILENAME


def _coerce_hhmm(val) -> str:
    """Return val as a valid HH:MM string, or '' if it is not one."""
    if not isinstance(val, str):
        return ""
    s = val.strip()
    return s if _HHMM_RE.match(s) else ""


def _coerce_quiet(raw) -> Optional[dict]:
    """Validate a quiet_hours object. Both ends must be valid HH:MM, else None.

    Partial/garbage windows are dropped entirely so an ambiguous config can
    never accidentally silence escalations.
    """
    if not isinstance(raw, dict):
        return None
    start = _coerce_hhmm(raw.get("start"))
    end = _coerce_hhmm(raw.get("end"))
    if not start or not end:
        return None
    return {"start": start, "end": end}


def _coerce_int(val, default: int, minimum: int = 0) -> int:
    """Best-effort non-negative int with a floor; falls back to default on junk."""
    try:
        n = int(val)
    except (TypeError, ValueError):
        return default
    return n if n >= minimum else default


def load_budget(state_dir: str = "") -> Budget:
    """Load the leash from JSON, returning a Budget with safe defaults filled in.

    Resolution order for the path: $NONYA_BUDGET, then <state_dir>/budget.json.
    A missing file, unreadable file, non-object JSON, or any parse error all
    degrade to the all-defaults Budget — never raises. Per-field garbage is
    coerced or dropped individually, biased toward the safe action.
    """
    path = _budget_path(state_dir)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return Budget()
    if not isinstance(raw, dict):
        return Budget()

    # auto_inject: only the literal True (or a truthy explicit bool) enables it;
    # anything ambiguous stays alert-only.
    ai = raw.get("auto_inject", DEFAULT_AUTO_INJECT)
    auto_inject = ai is True

    panic = raw.get("panic_word", "")
    panic_word = panic.strip() if isinstance(panic, str) else ""

    return Budget(
        auto_inject=auto_inject,
        max_recoveries=_coerce_int(raw.get("max_recoveries"), DEFAULT_MAX_RECOVERIES, minimum=1),
        spend_ceiling=_coerce_int(raw.get("spend_ceiling"), DEFAULT_SPEND_CEILING, minimum=0),
        quiet_hours=_coerce_quiet(raw.get("quiet_hours")),
        panic_word=panic_word,
    )


# --- helpers ------------------------------------------------------------------

def _to_minutes(hhmm: str) -> Optional[int]:
    """'HH:MM' -> minutes since midnight, or None if malformed."""
    m = _HHMM_RE.match(hhmm.strip()) if isinstance(hhmm, str) else None
    if not m:
        return None
    return int(m.group(1)) * 60 + int(m.group(2))


def in_quiet_hours(budget: Budget, now_hhmm: str) -> bool:
    """Is now_hhmm ('HH:MM') inside the configured quiet window?

    Handles windows that wrap past midnight (e.g. 23:00-06:00 contains 02:00 and
    23:30 but not 12:00). The window is treated as [start, end): start inclusive,
    end exclusive. Returns False when no quiet_hours are configured or when any
    input is malformed (safe: a bad clock never silences escalation).
    """
    if budget is None or not budget.quiet_hours:
        return False
    start = _to_minutes(budget.quiet_hours.get("start", ""))
    end = _to_minutes(budget.quiet_hours.get("end", ""))
    now = _to_minutes(now_hhmm)
    if start is None or end is None or now is None:
        return False
    if start == end:
        # Degenerate zero-length window: treat as "no quiet hours".
        return False
    if start < end:
        # Same-day window, e.g. 01:00-07:00.
        return start <= now < end
    # Wraps past midnight, e.g. 23:00-06:00.
    return now >= start or now < end


def allow_inject(budget: Budget) -> bool:
    """True only if the leash explicitly permits auto-injection. Safe default: False."""
    if budget is None:
        return False
    return bool(budget.auto_inject)


def has_panic(budget: Budget, text: str) -> bool:
    """True if the configured panic word appears in text (case-insensitive substring).

    A panic word forces immediate escalation regardless of mode or quiet hours.
    Returns False when no panic word is configured or text is empty. Never raises.
    """
    if budget is None or not budget.panic_word:
        return False
    if not isinstance(text, str) or not text:
        return False
    return budget.panic_word.lower() in text.lower()
