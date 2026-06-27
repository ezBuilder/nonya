#!/usr/bin/env python3
"""Unit tests for nonya.pacing — rate-limit aware pacing.

Plain asserts, no pytest. Inline JSONL fixtures. House style:

    python3 tests/test_pacing.py
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)

from nonya import pacing  # noqa: E402

_fail = 0
_tmp = []


def check(label, got, want):
    global _fail
    if got == want:
        print("ok    %-40s -> %s" % (label, got))
    else:
        print("FAIL  %-40s -> %s (want %s)" % (label, got, want))
        _fail = 1


def write_jsonl(records):
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")
    _tmp.append(path)
    return path


NOW = 1_700_000_000  # fixed epoch for deterministic arithmetic


# --- is_rate_limited: true on a 429, false on normal -----------------------

p_claude_429 = write_jsonl([
    {"type": "user", "message": {"role": "user", "content": "go"}},
    {"isApiErrorMessage": True, "error": "rate_limit", "apiErrorStatus": 429,
     "message": {"role": "assistant", "content": []}},
])
check("is_rate_limited claude 429", pacing.is_rate_limited("claude", p_claude_429), True)

p_codex_rl = write_jsonl([
    {"payload": {"type": "task_started"}},
    {"type": "event_msg", "payload": {"type": "token_count",
     "info": {"rate_limits": {"rate_limit_reached_type": "primary"}}}},
])
check("is_rate_limited codex", pacing.is_rate_limited("codex", p_codex_rl), True)

p_normal = write_jsonl([
    {"type": "user", "message": {"role": "user", "content": "go"}},
    {"type": "assistant", "message": {"role": "assistant",
     "content": [{"type": "text", "text": "all done"}], "stop_reason": "end_turn"}},
])
check("is_rate_limited normal -> False", pacing.is_rate_limited("claude", p_normal), False)

check("is_rate_limited missing path -> False",
      pacing.is_rate_limited("claude", "/no/such.jsonl"), False)


# --- resume_at WITH explicit reset window ----------------------------------

# codex resets_in_seconds = 120 -> resume at now + 120 + 1s cushion
p_codex_window = write_jsonl([
    {"payload": {"type": "task_started"}},
    {"type": "event_msg", "payload": {"type": "token_count", "info": {"rate_limits": {
        "rate_limit_reached_type": "primary",
        "primary": {"used_percent": 100.0, "window_minutes": 5, "resets_in_seconds": 120},
        "secondary": {"used_percent": 80.0, "window_minutes": 60, "resets_in_seconds": 40},
    }}}},
])
epoch, hhmm = pacing.resume_at("codex", p_codex_window, NOW)
check("resume_at explicit window (max=120 +1)", epoch, NOW + 121)
check("resume_at hhmm is HH:MM",
      bool(len(hhmm) == 5 and hhmm[2] == ":"), True)

# claude retry_after seconds field
p_claude_retry = write_jsonl([
    {"isApiErrorMessage": True, "error": "rate_limit", "apiErrorStatus": 429,
     "retry_after": 300, "message": {"role": "assistant", "content": []}},
])
epoch2, _ = pacing.resume_at("claude", p_claude_retry, NOW)
check("resume_at claude retry_after=300", epoch2, NOW + 301)

# Retry-After embedded in an error string
p_claude_str = write_jsonl([
    {"isApiErrorMessage": True, "apiErrorStatus": 429,
     "error": "429 Too Many Requests; Retry-After: 45",
     "message": {"role": "assistant", "content": []}},
])
epoch3, _ = pacing.resume_at("claude", p_claude_str, NOW)
check("resume_at retry-after string=45", epoch3, NOW + 46)

# absolute resets_at epoch
target = NOW + 600
p_abs = write_jsonl([
    {"type": "event_msg", "payload": {"type": "token_count", "info": {"rate_limits": {
        "rate_limit_reached_type": "primary",
        "primary": {"resets_at": target},
    }}}},
])
epoch4, _ = pacing.resume_at("codex", p_abs, NOW)
check("resume_at absolute resets_at", epoch4, target + 1)


# --- resume_at WITHOUT a window -> backoff increases and caps ---------------

no_window = write_jsonl([
    {"type": "user", "message": {"role": "user", "content": "go"}},
    {"isApiErrorMessage": True, "error": "rate_limit", "apiErrorStatus": 429,
     "message": {"role": "assistant", "content": []}},
])
e0, _ = pacing.resume_at("claude", no_window, NOW, attempt=0)
e1, _ = pacing.resume_at("claude", no_window, NOW, attempt=1)
e2, _ = pacing.resume_at("claude", no_window, NOW, attempt=2)
e3, _ = pacing.resume_at("claude", no_window, NOW, attempt=3)
e4, _ = pacing.resume_at("claude", no_window, NOW, attempt=4)  # past schedule -> cap
check("backoff attempt0 = 60s", e0 - NOW, 60)
check("backoff attempt1 = 5m", e1 - NOW, 300)
check("backoff attempt2 = 15m", e2 - NOW, 900)
check("backoff attempt3 = 1h", e3 - NOW, 3600)
check("backoff increases monotonically",
      e0 < e1 < e2 < e3, True)
check("backoff caps at 1h", e4 - NOW, 3600)
check("backoff cap stays at 1h (attempt99)",
      pacing.resume_at("claude", no_window, NOW, attempt=99)[0] - NOW, 3600)

check("backoff_seconds raw schedule",
      [pacing.backoff_seconds(i) for i in range(5)], [60, 300, 900, 3600, 3600])


# --- should_pace -----------------------------------------------------------

check("should_pace: rate-limited, no schedule -> True",
      pacing.should_pace("claude", no_window, NOW), True)
check("should_pace: rate-limited, before resume -> True",
      pacing.should_pace("claude", no_window, NOW, last_resume_ts=NOW + 100), True)
check("should_pace: rate-limited, past resume -> False",
      pacing.should_pace("claude", no_window, NOW, last_resume_ts=NOW - 100), False)
check("should_pace: not rate-limited -> False",
      pacing.should_pace("claude", p_normal, NOW), False)
check("should_pace: missing path -> False",
      pacing.should_pace("claude", "/no/such.jsonl", NOW), False)


# --- degrade: never raises -------------------------------------------------

check("resume_at bad now_ts degrades to int epoch",
      isinstance(pacing.resume_at("claude", no_window, "not-a-number")[0], int), True)
check("MAX_WINDOW clamps absurd reset",
      pacing.resume_at("codex", write_jsonl([
          {"payload": {"type": "token_count", "info": {"rate_limits": {
              "primary": {"resets_in_seconds": 999999999}}}}}]), NOW)[0] - NOW,
      pacing.MAX_WINDOW + 1)


for p in _tmp:
    try:
        os.remove(p)
    except OSError:
        pass

print("ALL PASS" if _fail == 0 else "SOME FAILED")
sys.exit(_fail)
