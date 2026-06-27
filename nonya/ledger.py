"""ledger — append-only, hash-chained trust ledger of supervisor interventions.

Every time nonya intervenes (nudges, scolds, auto-approves, gives up) it records
ONE line here. The lines are chained by SHA-256: each entry stores the hash of the
previous line plus its own canonical hash, so any later tampering or deletion is
detectable by `verify_chain`. This is the project's trust primitive — the audit
trail that proves what the supervisor did and that nobody quietly rewrote history.

File: <state_dir>/ledger.jsonl, one JSON object per line, written atomically.

Hard invariants honored here:
  * pure stdlib only (json, hashlib, os, re, time);
  * no network;
  * secrets are scrubbed out of `evidence` and `injected_text` before they are
    ever written, via `scrub()`.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time

try:
    import fcntl                      # POSIX only; used to serialize concurrent appends
except ImportError:                   # native Windows (nonya targets mac + WSL)
    fcntl = None

FILENAME = "ledger.jsonl"

# Hash of the empty string — the chain's anchor for the very first entry.
GENESIS = hashlib.sha256(b"").hexdigest()

# Fields recorded per entry (besides the chain fields prev_hash/hash).
ENTRY_FIELDS = (
    "ts", "session", "stall_class", "evidence", "injected_text",
    "gates_passed", "outcome",
)

# --- secret scrubbing ---------------------------------------------------------

_REDACTED = "[REDACTED]"

# key=value / key: value where the key smells secret (token, key, secret,
# password, api_key, auth, bearer, ...). Captures the assignment operator so we
# keep "key=" and only blank the value.
_KV_RE = re.compile(
    r"(?i)\b([A-Za-z0-9_.\-]*(?:token|secret|passwd|password|api[-_]?key|"
    r"access[-_]?key|auth|bearer|credential|private[-_]?key)[A-Za-z0-9_.\-]*)"
    r"(\s*[=:]\s*)"
    r"(\"[^\"]*\"|'[^']*'|`[^`]*`|[^\s,;}{\"']+)"
)


def _kv_sub(m):
    val = m.group(3)
    # leave the Bearer scheme keyword + the already-redacted token alone so
    # "Authorization: Bearer X" -> "Authorization: Bearer [REDACTED]" (no double redaction)
    if val == _REDACTED or val.lower() == "bearer":
        return m.group(0)
    return m.group(1) + m.group(2) + _REDACTED

# Bearer/Authorization header style: "Authorization: Bearer xxxxx".
_BEARER_RE = re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._\-]+")

# Standalone high-entropy / known-prefix tokens that appear without a key, e.g.
# sk-..., ghp_..., AKIA..., long base64-ish blobs, JWTs.
_PREFIX_TOKEN_RE = re.compile(
    r"\b("
    r"sk-[A-Za-z0-9_\-]{16,}"          # OpenAI-style
    r"|sk-ant-[A-Za-z0-9_\-]{16,}"     # Anthropic-style
    r"|gh[posru]_[A-Za-z0-9]{20,}"     # GitHub tokens
    r"|xox[baprs]-[A-Za-z0-9\-]{10,}"  # Slack tokens
    r"|AKIA[0-9A-Z]{16}"               # AWS access key id
    r"|eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{6,}"  # JWT
    r")\b"
)


def scrub(text: str) -> str:
    """Redact token-like secrets from free text before it is written anywhere.

    Conservative and lossy on purpose: better to redact a harmless string than to
    leak a credential into the durable trust ledger. Handles key=value pairs with
    secret-looking keys, Authorization: Bearer headers, and standalone tokens with
    well-known prefixes (sk-, ghp_, AKIA, JWTs, ...).
    """
    if not text:
        return text
    # Bearer first: "Authorization:" matches the KV key rule, whose value matcher
    # would stop at the space after "Bearer" and leave the token exposed.
    out = _BEARER_RE.sub(lambda m: m.group(1) + _REDACTED, text)
    out = _KV_RE.sub(_kv_sub, out)
    out = _PREFIX_TOKEN_RE.sub(_REDACTED, out)
    return out


# --- canonical hashing --------------------------------------------------------

def _canon(obj: dict) -> str:
    # sort_keys + compact separators give a stable byte sequence for the same dict.
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _entry_hash(prev_hash: str, entry_without_hash: dict) -> str:
    payload = prev_hash + _canon(entry_without_hash)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _last_hash(state_dir: str) -> str:
    """Return the hash of the last ledger line, or GENESIS if the ledger is empty."""
    path = os.path.join(state_dir, FILENAME)
    last = None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    last = line
    except OSError:
        return GENESIS
    if last is None:
        return GENESIS
    try:
        return json.loads(last).get("hash", GENESIS)
    except ValueError:
        # Corrupt tail — return GENESIS-equivalent so a new append still produces a
        # well-formed line; verify_chain will surface the corruption.
        return GENESIS


# --- public API ---------------------------------------------------------------

def append(state_dir: str, entry: dict) -> dict:
    """Append one entry to the chained ledger and return the stored record.

    The caller passes a plain dict (ts/session/stall_class/evidence/...). This:
      1. scrubs secrets from `evidence` and `injected_text`;
      2. fills `ts` if missing;
      3. sets `prev_hash` to the previous line's hash (GENESIS for the first);
      4. computes `hash = sha256(prev_hash + canonical_json(entry_without_hash))`;
      5. writes the line atomically (append + flush + fsync) under state_dir.

    Returns the full stored record (including prev_hash and hash).
    """
    if not state_dir:
        raise ValueError("state_dir is required")
    os.makedirs(state_dir, exist_ok=True)

    rec = dict(entry)
    rec.setdefault("ts", int(time.time()))
    if "evidence" in rec and isinstance(rec["evidence"], str):
        rec["evidence"] = scrub(rec["evidence"])
    if "injected_text" in rec and isinstance(rec["injected_text"], str):
        rec["injected_text"] = scrub(rec["injected_text"])

    rec.pop("hash", None)   # chain fields are derived here; never trust caller-supplied hash values

    path = os.path.join(state_dir, FILENAME)
    # The read-tail -> compute prev_hash -> append is ONE critical section: two
    # writers must not both read the same tail and chain off it (that forks the
    # chain and breaks verify_chain forever). An exclusive flock serializes the
    # whole read-modify-write; a single O_APPEND write keeps the line itself atomic.
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_EX)
        rec["prev_hash"] = _last_hash(state_dir)
        rec["hash"] = _entry_hash(rec["prev_hash"], _without_hash(rec))
        line = json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n"
        os.write(fd, line.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)        # closing the fd releases the flock
    return rec


def _without_hash(rec: dict) -> dict:
    return {k: v for k, v in rec.items() if k != "hash"}


def read(state_dir: str) -> list:
    """Return all ledger entries as a list of dicts (oldest first). [] if absent."""
    path = os.path.join(state_dir, FILENAME)
    out = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    # Keep a sentinel so verify_chain can detect a corrupt line.
                    out.append({"__corrupt__": line})
    except OSError:
        return []
    return out


def verify_chain(state_dir: str) -> bool:
    """Return True iff the ledger is an intact hash chain with no gaps/tampering.

    Checks, for every line in order:
      * the line is valid JSON with prev_hash + hash present;
      * prev_hash equals the previous line's hash (GENESIS for the first line);
      * the stored hash equals the recomputed hash of (prev_hash + canonical body).
    Any mismatch — edited field, reordered/deleted line, corrupt JSON — returns False.
    An empty or absent ledger is trivially valid (True).
    """
    expected_prev = GENESIS
    try:
        fh = open(os.path.join(state_dir, FILENAME), "r", encoding="utf-8")
    except OSError:
        return True
    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                return False
            if not isinstance(rec, dict):
                return False
            stored_hash = rec.get("hash")
            prev = rec.get("prev_hash")
            if not isinstance(stored_hash, str) or not isinstance(prev, str):
                return False
            if prev != expected_prev:
                return False
            if _entry_hash(prev, _without_hash(rec)) != stored_hash:
                return False
            expected_prev = stored_hash
    return True


def export_markdown(state_dir: str) -> str:
    """Render the ledger as a human-readable Markdown table for review/reports.

    Includes a header noting whether the chain currently verifies, so a reader of
    the exported doc can see at a glance if the audit trail was tampered with.
    """
    entries = read(state_dir)
    intact = verify_chain(state_dir)
    lines = []
    lines.append("# nonya intervention ledger")
    lines.append("")
    lines.append("- entries: %d" % len(entries))
    lines.append("- chain verified: %s" % ("yes" if intact else "NO — TAMPERED"))
    lines.append("")
    lines.append("| # | ts | session | stall_class | gates_passed | outcome | injected_text | evidence | hash |")
    lines.append("|---|----|---------|-------------|--------------|---------|---------------|----------|------|")
    for i, e in enumerate(entries):
        if "__corrupt__" in e:
            lines.append("| %d | | | | | **CORRUPT LINE** | | | |" % i)
            continue
        lines.append("| %d | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            i,
            _cell(e.get("ts")),
            _cell(e.get("session")),
            _cell(e.get("stall_class")),
            _cell(e.get("gates_passed")),
            _cell(e.get("outcome")),
            _cell(e.get("injected_text")),
            _cell(e.get("evidence")),
            _cell((e.get("hash") or "")[:12]),
        ))
    return "\n".join(lines) + "\n"


def _cell(val) -> str:
    """Stringify a value for a Markdown table cell: escape pipes/newlines, truncate."""
    if val is None:
        return ""
    s = str(val).replace("|", "\\|").replace("\n", " ").replace("\r", " ")
    if len(s) > 80:
        s = s[:77] + "..."
    return s
