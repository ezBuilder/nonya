"""metrics — honest, ledger-derived stats about what the supervisor has actually done.

The trust question for an auto-intervention tool is not "is it clever?" but "how often
does it act, does it ever fire when it must NOT, and does its acting land?". We answer that
ONLY from the hash-chained ledger (the durable record of every intervention) — no estimates,
no labels we don't have. The headline numbers:

  * volume        — total interventions, broken down by stall_class and by outcome.
  * delivery      — of the times nonya actually sent keys, how many recovered.
  * SHADOW        — how many times it WOULD have acted while in shadow mode (zero keys sent).
  * SAFETY        — the invariant: injections into a WAITING (question/permission) turn MUST be 0.
                    A WAITING nudge fabricates an answer; this count is the proof the rule held.
  * integrity     — the hash chain verifies (nobody rewrote history).

Run for a while in `--shadow` first, read this, and you can see the false-positive surface
(what it would have touched) before ever letting it send a key. Pure stdlib, no network.
"""
from __future__ import annotations

import json
import time

from . import ledger

# outcomes that mean "the session got moving again after we acted"
_RECOVERED = ("recovered", "resolved")
# stall classes that are a user-facing question/permission ask — nonya must NEVER inject here.
_WAITING_MARK = ("waiting", "needs-you", "ask")


def summarize(state_dir: str, window_hours: float = 0) -> dict:
    """Aggregate the ledger into a stats dict. Defensive: tolerates old/partial entries.
    The ledger keeps the FULL history (durable audit); `window_hours` (>0) only filters the VIEW
    to the last N hours (12/24/48…) so the numbers reflect a chosen recent window, not all time."""
    entries = ledger.read(state_dir)
    valid = [e for e in entries if isinstance(e, dict) and "__corrupt__" not in e]
    corrupt = len(entries) - len(valid)
    if window_hours and window_hours > 0:
        cutoff = time.time() - window_hours * 3600
        valid = [e for e in valid if isinstance(e.get("ts"), (int, float)) and e["ts"] >= cutoff]

    by_class, by_outcome = {}, {}
    acted = recovered = shadow = waiting_injections = 0
    first_ts = last_ts = None
    for e in valid:
        sc = str(e.get("stall_class") or "?")
        oc = str(e.get("outcome") or "?").lower()
        by_class[sc] = by_class.get(sc, 0) + 1
        by_outcome[oc] = by_outcome.get(oc, 0) + 1
        injected = bool(e.get("injected_text"))
        if injected:
            acted += 1
        if oc in _RECOVERED:
            recovered += 1
        if oc == "shadow":
            shadow += 1
        # SAFETY invariant: real keys sent on a WAITING/question turn — must stay 0.
        if injected and any(w in sc.lower() for w in _WAITING_MARK):
            waiting_injections += 1
        ts = e.get("ts")
        if isinstance(ts, (int, float)):
            first_ts = ts if first_ts is None else min(first_ts, ts)
            last_ts = ts if last_ts is None else max(last_ts, ts)

    return {
        "entries": len(valid),
        "corrupt_lines": corrupt,
        "chain_intact": ledger.verify_chain(state_dir),
        "acted": acted,                      # interventions where keys were actually sent
        "recovered": recovered,              # of those, sessions that resumed
        "delivery_rate": round(recovered / acted, 3) if acted else None,
        "shadow_would_act": shadow,          # decisions in shadow mode (zero keys sent)
        "waiting_injections": waiting_injections,   # MUST be 0 (never nudge a question)
        "safety_invariant_ok": waiting_injections == 0,
        "by_class": dict(sorted(by_class.items(), key=lambda kv: -kv[1])),
        "by_outcome": dict(sorted(by_outcome.items(), key=lambda kv: -kv[1])),
        "first_ts": first_ts,
        "last_ts": last_ts,
    }


def render(state_dir: str, window_hours: float = 0) -> str:
    """Human-readable one-screen summary (the menu/CLI shows this), localized via i18n."""
    from .i18n import t
    s = summarize(state_dir, window_hours)
    if not s["entries"]:
        return t("metrics.head") + "\n" + t("metrics.none")
    delivery = "%.0f%%" % (s["delivery_rate"] * 100) if s["delivery_rate"] is not None else "n/a"
    lines = [
        t("metrics.head"),
        "-" * 44,
        "%s: %d" % (t("metrics.interventions"), s["entries"]),
        "%s: %d   %s: %d   %s: %s" % (t("metrics.keys"), s["acted"],
                                      t("metrics.recovered"), s["recovered"], t("metrics.delivery"), delivery),
        "%s: %d  (%s)" % (t("metrics.shadow"), s["shadow_would_act"], t("metrics.shadow_note")),
        "%s: %s  (%s)" % (t("metrics.safety"),
                          t("metrics.ok") if s["safety_invariant_ok"] else t("metrics.violated"),
                          t("metrics.safety_note", s["waiting_injections"])),
        "%s: %s" % (t("metrics.chain"), t("metrics.intact") if s["chain_intact"] else t("metrics.tampered")),
    ]
    if s["by_class"]:
        lines.append("%s: %s" % (t("metrics.byclass"), ", ".join("%s=%d" % (k, v) for k, v in s["by_class"].items())))
    if s["by_outcome"]:
        lines.append("%s: %s" % (t("metrics.byoutcome"), ", ".join("%s=%d" % (k, v) for k, v in s["by_outcome"].items())))
    return "\n".join(lines)


def as_json(state_dir: str) -> str:
    return json.dumps(summarize(state_dir), ensure_ascii=False)
