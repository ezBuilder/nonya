"""Corrective nudge builder: turn a claimed-but-unverified "done" into a
SPECIFIC instruction instead of a generic "keep going".

Flow (deterministic, no network on the default path):
  1. Extract the agent's LAST CLAIM from the transcript tail
     (it said "done" / "tests pass" / "fixed" / "<<DONE>>"-style language).
  2. Combine that with an optional verify_summary (from verify.py): if the
     verifier found a failure, produce a pointed correction naming it.
  3. If no claim is found (genuine idle / mid-work), fall back to the caller's
     generic default_nudge — never invent a correction we can't justify.

HARD INVARIANTS upheld here:
  - No network on the default path. A local model is OPTIONAL, behind the
    NONYA_MODEL_CMD env var, time-bounded, and degrades to the deterministic
    template when absent or on any error.
  - Never misfire: when we cannot extract a claim AND have no failing verify
    signal, we return the safe default nudge unchanged.
  - Redact secrets: any text we lift from the transcript or the model is run
    through _redact() before it lands in the returned instruction.
  - Pure stdlib, read-only of transcripts (we only call detect._tail_json).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from typing import List, Optional

from . import detect

# How many transcript records to scan for a claim. Bounded read via detect.
_CLAIM_SCAN = int(os.environ.get("NONYA_CLAIM_SCAN", "40"))

# A hard ceiling so a pasted instruction never balloons (and never floods a pane).
_MAX_LEN = 600

# Default wall-clock budget for an optional local-model call.
_MODEL_TIMEOUT = float(os.environ.get("NONYA_MODEL_TIMEOUT", "8"))


# ---- claim extraction ------------------------------------------------------
# Phrases an agent uses to assert completion/correctness. English + Korean
# (this repo's nudges are Korean). Matched case-insensitively against the
# agent's own (assistant-side) text only.
_CLAIM_PATTERNS = [
    r"\ball (?:the )?tests? (?:are )?pass(?:ing|ed|es)?\b",
    r"\btests? (?:now )?pass(?:ing|ed|es)?\b",
    r"\bpytest .*pass",
    r"\ball green\b",
    r"\b(?:it'?s |task |work )?(?:is )?(?:now )?(?:done|complete|completed|finished)\b",
    r"\b(?:i(?:'ve| have)? )?fixed\b",
    r"\bbug (?:is )?fixed\b",
    r"\bshould (?:now )?work\b",
    r"\bverified\b",
    r"\bno (?:more )?(?:errors|failures)\b",
    # Korean
    r"완료(?:했|됐|돼|됨|입니다|했습니다)",
    r"끝났",
    r"수정(?:했|됐|완료)",
    r"테스트.*(?:통과|성공)",
    r"검증(?:했|됨|완료|됐)",
    r"고쳤",
]
_CLAIM_RE = re.compile("|".join(_CLAIM_PATTERNS), re.I)

# Secret-shaped tokens we must never echo into the ledger / pane.
_SECRET_RES = [
    re.compile(r"\b(sk|pk|rk)-[A-Za-z0-9_\-]{12,}\b"),          # api-key style
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),               # github tokens
    re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b"),            # slack tokens
    re.compile(r"\bAKIA[0-9A-Z]{12,}\b"),                        # aws access key id
    re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"),  # jwt
    re.compile(r"(?i)\b(?:api[_-]?key|token|secret|password|passwd|bearer)\b"
               r"\s*[:=]\s*[^\s'\"]{6,}"),                       # key: value
]
_REDACTED = "[REDACTED]"


def _redact(text: str) -> str:
    """Strip secret-shaped tokens from any text bound for output."""
    if not text:
        return text
    for rx in _SECRET_RES:
        text = rx.sub(_REDACTED, text)
    return text


def _assistant_texts(engine: str, path: str) -> List[str]:
    """Return assistant-authored text strings from the transcript tail, oldest
    first. Read-only; bounded by _CLAIM_SCAN. Non-claude engines: best-effort
    over JSONL text fields (we never mutate the file)."""
    out: List[str] = []
    if not path or not os.path.exists(path):
        return out
    try:
        objs = detect._tail_json(path, n=_CLAIM_SCAN)
    except Exception:
        return out
    for o in objs:
        if not isinstance(o, dict):
            continue
        # Claude shape: {"type":"assistant","message":{"role":"assistant",
        #   "content":[{"type":"text","text":...}]}}
        if o.get("type") == "user" or o.get("isSidechain") is True:
            continue
        msg = o.get("message")
        if isinstance(msg, dict):
            if msg.get("role") not in (None, "assistant"):
                continue
            content = msg.get("content")
            if isinstance(content, str):
                out.append(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        t = part.get("text")
                        if isinstance(t, str):
                            out.append(t)
        # Codex / event-style: collect any plain text payloads conservatively.
        payload = o.get("payload")
        if isinstance(payload, dict):
            for key in ("text", "message", "content"):
                v = payload.get(key)
                if isinstance(v, str):
                    out.append(v)
    return out


def extract_last_claim(engine: str, path: str) -> Optional[str]:
    """Return a short snippet of the agent's most recent completion CLAIM, or
    None if it made no such claim. Safe default is None (-> no correction)."""
    texts = _assistant_texts(engine, path)
    for text in reversed(texts):          # most recent first
        for line in reversed(text.splitlines()):
            line = line.strip()
            if not line:
                continue
            if _CLAIM_RE.search(line):
                snippet = line if len(line) <= 160 else line[:157] + "..."
                return _redact(snippet)
    return None


# ---- verify_summary normalisation ------------------------------------------
# verify.py is built in parallel; accept either a mapping or an attr object.
# We only need: did it FAIL, and a one-line human detail of the failure.

def _vget(vs, key):
    if vs is None:
        return None
    if isinstance(vs, dict):
        return vs.get(key)
    return getattr(vs, key, None)


def _verify_failed(vs) -> Optional[bool]:
    """True=failed, False=passed, None=unknown/not run."""
    if vs is None:
        return None
    ok = _vget(vs, "ok")
    if ok is None:
        ok = _vget(vs, "passed")
    if ok is None:
        ok = _vget(vs, "success")
    if ok is not None:
        return not bool(ok)
    failed = _vget(vs, "failed")
    if failed is not None:
        return bool(failed)
    return None


def _verify_detail(vs) -> str:
    """A short, redacted, human-readable description of the failure."""
    for key in ("detail", "summary", "failures", "output", "message"):
        v = _vget(vs, key)
        if isinstance(v, str) and v.strip():
            detail = v.strip()
            break
        if isinstance(v, (list, tuple)) and v:
            detail = ", ".join(str(x) for x in v)
            break
    else:
        detail = "verification reported a failure"
    detail = detail.replace("\n", " ").strip()
    if len(detail) > 240:
        detail = detail[:237] + "..."
    return _redact(detail)


# ---- optional local model (OFF by default; opt-in; time-bounded; never downloads) --
import shutil          # noqa: E402
import urllib.request  # noqa: E402

def discover_model() -> Optional[str]:
    """Which LOCAL model backend to use, or None. nonya NEVER downloads a model —
    it only USES one you already have. Resolution order:
      1. NONYA_MODEL_CMD set        -> "cmd"  (a shell command reading the prompt on stdin)
      2. NONYA_MODEL=auto opt-in, then auto-detect a running local backend:
         - ollama (a model already pulled)            -> "ollama"
         - an OpenAI-compatible local server (LM Studio at localhost:1234, etc.) -> "openai:<base>"
    Without NONYA_MODEL_CMD and without NONYA_MODEL=auto, returns None and the
    corrective text stays fully deterministic + network-free (the default)."""
    if os.environ.get("NONYA_MODEL_CMD"):
        return "cmd"
    if os.environ.get("NONYA_MODEL", "").strip().lower() != "auto":
        return None
    if shutil.which("ollama"):
        try:
            r = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=5)
            if r.returncode == 0 and len(r.stdout.strip().splitlines()) > 1:
                return "ollama"
        except (OSError, subprocess.SubprocessError):
            pass
    base = os.environ.get("NONYA_OPENAI_BASE", "http://localhost:1234")
    try:
        with urllib.request.urlopen(base + "/v1/models", timeout=2) as resp:
            if getattr(resp, "status", 200) == 200:
                return "openai:" + base
    except Exception:
        pass
    return None


def _trim(out: str) -> Optional[str]:
    out = (out or "").strip()
    if not out:
        return None
    return _redact(out[:_MAX_LEN].rstrip())


def call_local_model(prompt: str, timeout: float = _MODEL_TIMEOUT) -> Optional[str]:
    """Run the resolved local backend with a hard timeout; redacted output or None.
    Invoked by build_nudge only when a backend resolves; the default path never
    reaches here, so per-poll detection stays network-free."""
    kind = discover_model()
    if kind is None:
        return None
    try:
        if kind == "cmd":
            proc = subprocess.run(os.environ["NONYA_MODEL_CMD"], shell=True, input=prompt,
                                  capture_output=True, text=True, timeout=timeout)
            return _trim(proc.stdout) if proc.returncode == 0 else None
        if kind == "ollama":
            ml = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=5)
            model = ml.stdout.strip().splitlines()[1].split()[0]
            proc = subprocess.run(["ollama", "run", model], input=prompt,
                                  capture_output=True, text=True, timeout=timeout)
            return _trim(proc.stdout) if proc.returncode == 0 else None
        if kind.startswith("openai:"):
            base = kind.split(":", 1)[1]
            body = json.dumps({"model": os.environ.get("NONYA_MODEL_NAME", "local-model"),
                               "messages": [{"role": "user", "content": prompt}],
                               "max_tokens": 200, "temperature": 0.2}).encode()
            req = urllib.request.Request(base + "/v1/chat/completions", data=body,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode())
            return _trim(data["choices"][0]["message"]["content"])
    except (subprocess.TimeoutExpired, OSError, ValueError, KeyError, IndexError, Exception):
        return None
    return None


# ---- public entry point ----------------------------------------------------

def _template(claim: str, verify_failed: Optional[bool], detail: str) -> str:
    """Deterministic corrective instruction. No network, no model."""
    if verify_failed is True:
        return (
            'You said "%s" but verification failed: %s. '
            "Fix that before continuing, then re-run the check."
            % (claim, detail)
        )
    # Claimed done, but verification was not run / inconclusive.
    return (
        'You said "%s". Before stopping, actually run the relevant tests/checks '
        "and confirm they pass; if anything fails, fix it first." % claim
    )


def build_nudge(engine: str, path: str, verify_summary=None, state=None,
                default_nudge: str = "") -> str:
    """Return the instruction to inject this cycle.

    - If the agent made a completion CLAIM and verification FAILED -> a specific
      correction naming the failure (the high-value case).
    - If the agent claimed done but verify is unknown -> a "prove it" nudge.
    - If NO claim was found -> the generic default_nudge (safe; never misfire).

    A local model (NONYA_MODEL_CMD) only REFINES the deterministic text when
    present; on its absence or any failure we keep the template. `state` is
    accepted for signature stability / future routing but is not required.

    Return value is always redacted and length-bounded.
    """
    claim = extract_last_claim(engine, path)
    if not claim:
        # Genuine idle / mid-work / no assertion to correct -> safe generic path.
        return default_nudge

    vfailed = _verify_failed(verify_summary)
    detail = _verify_detail(verify_summary) if vfailed is True else ""
    base = _template(claim, vfailed, detail)

    # Optional local-model refinement. Default (no NONYA_MODEL_CMD, no NONYA_MODEL=auto)
    # skips this entirely -> no process spawned, no network.
    if discover_model():
        prompt = (
            "Rewrite this supervisor instruction to a coding agent as one short, "
            "specific, imperative sentence. Keep all concrete facts.\n\n" + base
        )
        refined = call_local_model(prompt, _MODEL_TIMEOUT)
        if refined:
            base = refined

    base = _redact(base)
    if len(base) > _MAX_LEN:
        base = base[:_MAX_LEN].rstrip()
    return base
