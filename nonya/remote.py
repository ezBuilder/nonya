"""remote — the ONLY network module: off-hot-path escalation push + reply poll.

When nonya has exhausted its local options (repeated stalls, "N회 먹통") it can
reach the user on their phone. That is the sole reason this module touches the
network, and it does so under strict rules so it can never harm the supervisor
loop:

Hard invariants honored here:
  * Pure stdlib (urllib.request only).
  * OFF the detection/inject hot path — callers invoke this only during
    escalation, never per-tick.
  * SHORT timeout (<= 5s) on every request; no retries, no long blocking.
  * Best-effort: every public function catches everything and degrades. push()
    returns False on any failure and NEVER raises into the loop.
  * Secrets are scrubbed out of the body before it is sent (reuse ledger.scrub
    when importable, else a local fallback regex).

Channels (any subset configured via env):
  * ntfy.sh   — NONYA_NTFY_TOPIC
  * Telegram  — NONYA_TELEGRAM_TOKEN + NONYA_TELEGRAM_CHAT

If no channel is configured, push() is a no-op and returns False.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

# Network budget: small enough that a hung endpoint can never stall the loop.
_TIMEOUT = 5.0


# --- secret scrubbing ---------------------------------------------------------
# Prefer the project's canonical scrubber so redaction stays consistent with the
# trust ledger. Fall back to a local, conservative regex if ledger isn't
# importable (keeps this module standalone and stdlib-only).
try:  # pragma: no cover - import wiring
    from .ledger import scrub as _scrub
except Exception:  # pragma: no cover
    try:
        from ledger import scrub as _scrub  # type: ignore
    except Exception:
        _scrub = None

_REDACTED = "[REDACTED]"

_LOCAL_KV_RE = re.compile(
    r"(?i)\b([A-Za-z0-9_.\-]*(?:token|secret|passwd|password|api[-_]?key|"
    r"access[-_]?key|auth|bearer|credential|private[-_]?key)[A-Za-z0-9_.\-]*)"
    r"(\s*[=:]\s*)"
    r"(?:\"[^\"]*\"|'[^']*'|`[^`]*`|[^\s,;}{\"']+)"
)
_LOCAL_BEARER_RE = re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._\-]+")
_LOCAL_PREFIX_RE = re.compile(
    r"\b("
    r"sk-[A-Za-z0-9_\-]{16,}"
    r"|sk-ant-[A-Za-z0-9_\-]{16,}"
    r"|gh[posru]_[A-Za-z0-9]{20,}"
    r"|xox[baprs]-[A-Za-z0-9\-]{10,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{6,}"
    r")\b"
)


def _local_scrub(text: str) -> str:
    if not text:
        return text
    out = _LOCAL_BEARER_RE.sub(lambda m: m.group(1) + _REDACTED, text)
    out = _LOCAL_KV_RE.sub(lambda m: m.group(1) + m.group(2) + _REDACTED, out)
    out = _LOCAL_PREFIX_RE.sub(_REDACTED, out)
    return out


def scrub(text: str) -> str:
    """Redact token-like secrets from text before it leaves the machine."""
    fn = _scrub or _local_scrub
    try:
        return fn(text)
    except Exception:
        return _local_scrub(text)


# --- low-level transport (best-effort, time-bounded) --------------------------

def _open(req: "urllib.request.Request") -> bool:
    """Fire one request with the short timeout. True on 2xx-ish, False otherwise.

    Catches everything — network errors, timeouts, bad URLs — so callers never
    see an exception. No retries: a single attempt keeps the loop unblocked.
    """
    try:
        resp = urllib.request.urlopen(req, timeout=_TIMEOUT)
    except Exception:
        return False
    try:
        code = getattr(resp, "status", None)
        if code is None:
            code = resp.getcode()
        return code is None or 200 <= int(code) < 300
    except Exception:
        return True
    finally:
        try:
            resp.close()
        except Exception:
            pass


def _ntfy(title: str, body: str) -> bool:
    topic = os.environ.get("NONYA_NTFY_TOPIC", "").strip()
    if not topic:
        return False
    url = "https://ntfy.sh/%s" % topic
    # Title goes in a header; ntfy requires it ASCII-clean.
    headers = {"Title": title.encode("ascii", "replace").decode("ascii")}
    req = urllib.request.Request(url, data=body.encode("utf-8"), headers=headers)
    return _open(req)


def _telegram(title: str, body: str) -> bool:
    token = os.environ.get("NONYA_TELEGRAM_TOKEN", "").strip()
    chat = os.environ.get("NONYA_TELEGRAM_CHAT", "").strip()
    if not (token and chat):
        return False
    url = "https://api.telegram.org/bot%s/sendMessage" % token
    text = "%s\n%s" % (title, body) if title else body
    data = json.dumps({"chat_id": chat, "text": text}).encode("utf-8")
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"})
    return _open(req)


# --- public API ---------------------------------------------------------------

def push(title: str, body: str, ctx=None) -> bool:
    """Send an escalation notification to the user's phone. Best-effort.

    Fans out to whichever channels are configured (ntfy.sh and/or Telegram).
    The body is ALWAYS scrubbed of secrets before sending. Returns True if at
    least one channel accepted the message, False otherwise — including when no
    channel is configured. Never raises.

    `ctx` is an optional dict the caller may pass for future structured fields;
    it is accepted and ignored here so the call site stays stable.
    """
    try:
        safe_title = scrub(title or "")
        safe_body = scrub(body or "")
    except Exception:
        # Scrub itself must never sink the push, but if it somehow fails we will
        # NOT send unredacted text — bail safe instead of leaking.
        return False

    sent = False
    try:
        if _ntfy(safe_title, safe_body):
            sent = True
    except Exception:
        pass
    try:
        if _telegram(safe_title, safe_body):
            sent = True
    except Exception:
        pass
    return sent


def poll_reply(timeout=None) -> "str | None":
    """Best-effort read of one free-text reply the user typed back on Telegram.

    OPTIONAL companion to push(): after escalating, the user may reply on their
    phone with a short redirect ("skip that test", "use sqlite"). This reads the
    latest text via Telegram getUpdates and returns it, or None when there is no
    reply / no Telegram configured / any error.

    The supervisor loop is expected to feed any returned text back through its
    NORMAL inject path (the same channel used for local nudges) rather than
    acting on it directly here — this function only fetches, it does not inject.

    Time-bounded by the same short budget (capped at <= _TIMEOUT) and never
    blocking/retrying, so calling it cannot stall the loop. Never raises.
    """
    token = os.environ.get("NONYA_TELEGRAM_TOKEN", "").strip()
    if not token:
        return None
    try:
        t = float(timeout) if timeout is not None else _TIMEOUT
    except (TypeError, ValueError):
        t = _TIMEOUT
    # Clamp to the network budget: even an explicit large timeout must not stall.
    t = max(0.0, min(t, _TIMEOUT))

    url = "https://api.telegram.org/bot%s/getUpdates?limit=1&offset=-1" % token
    try:
        resp = urllib.request.urlopen(url, timeout=t)
    except Exception:
        return None
    try:
        raw = resp.read()
    except Exception:
        return None
    finally:
        try:
            resp.close()
        except Exception:
            pass

    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        return None
    try:
        results = data.get("result") or []
        if not results:
            return None
        msg = results[-1].get("message") or {}
        text = msg.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()
    except Exception:
        return None
    return None
