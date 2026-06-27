"""Verify a project's OWN check command before accepting "done".

nonya is a progress & correctness supervisor: when an agent claims it is
finished, we want to *run the project's real check* (its test/lint/build) and
see it pass before believing it. This module does two small things:

  - discover_check(project_dir) -> (cmd, cwd) | None
        Auto-detect the project's own check command. Sources, in priority:
          1. NONYA_CHECK_CMD env override (user explicitly opts in)
          2. package.json  scripts.test / scripts.lint / scripts.build
          3. Makefile      test / lint target
          4. pyproject.toml / pytest.ini / tests/  -> pytest
        Returns None when nothing is discovered; the caller then SKIPS
        verification (safe default: never invent a command).

  - run_check(cmd, cwd, timeout) -> (passed, exit_code, summary)
        Run the discovered command under a hard timeout, capture output, and
        derive a SHORT human summary line from the output tail.

HARD INVARIANTS upheld here:
  - No network: we never run installs (npm install / pip install / etc). The
    discovered commands are test/lint/build only, and we explicitly refuse to
    return install-shaped commands.
  - No misfire: discovery returns None when uncertain -> caller skips, never
    nudges/accepts on a guess. A timeout counts as NOT passed.
  - Redaction: the summary is scrubbed of token/key/secret-shaped substrings
    before it can reach a ledger or any output.
  - Pure stdlib: json, re, os, shlex, subprocess only.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from typing import List, Optional, Tuple

# Cap on how long a check may run. The caller may pass a smaller timeout; we
# never run unbounded.
DEFAULT_TIMEOUT = 300
MAX_TIMEOUT = 1800

# Commands that fetch from the network / mutate the environment. We refuse to
# run these even if a script points at them — verification must be hermetic.
_INSTALL_RE = re.compile(
    r"\b("
    r"npm\s+(?:ci|install|i)\b|yarn\s+(?:add|install)\b|pnpm\s+(?:add|install|i)\b|"
    r"pip\s+install\b|pip3\s+install\b|poetry\s+(?:add|install)\b|"
    r"go\s+get\b|cargo\s+(?:install|fetch)\b|gem\s+install\b|"
    r"apt(?:-get)?\s+install\b|brew\s+install\b|curl\b|wget\b"
    r")",
    re.IGNORECASE,
)

# Secret-shaped substrings to scrub from any summary line.
_SECRET_RE = re.compile(
    r"(?i)(?:api[_-]?key|secret|token|password|passwd|bearer|authorization|"
    r"[A-Z_]*?(?:KEY|TOKEN|SECRET))\s*[:=]\s*\S+"
)
_LONG_BLOB_RE = re.compile(r"\b[A-Za-z0-9_\-]{32,}\b")


def _is_install(cmd: str) -> bool:
    """True if CMD looks like a network/install command we must refuse."""
    return bool(_INSTALL_RE.search(cmd))


def _read_json(path: str) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _make_target_re(target: str) -> "re.Pattern[str]":
    return re.compile(r"^%s\s*:" % re.escape(target), re.MULTILINE)


def _makefile_has_target(path: str, target: str) -> bool:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return False
    return bool(_make_target_re(target).search(text))


def discover_check(project_dir: str) -> Optional[Tuple[str, str]]:
    """Auto-detect the project's own check command.

    Returns (cmd, cwd) on success, or None when nothing safe is discovered.
    Discovery order (first match wins): env override, package.json scripts,
    Makefile targets, then pytest. Install-shaped commands are rejected.
    """
    if not project_dir or not os.path.isdir(project_dir):
        return None

    # 1. Explicit user override — highest priority, but still install-guarded.
    override = os.environ.get("NONYA_CHECK_CMD", "").strip()
    if override:
        if _is_install(override):
            return None
        return (override, project_dir)

    # 2. package.json scripts: prefer test, then lint, then build.
    pkg_path = os.path.join(project_dir, "package.json")
    if os.path.isfile(pkg_path):
        pkg = _read_json(pkg_path)
        scripts = (pkg or {}).get("scripts")
        if isinstance(scripts, dict):
            for name in ("test", "lint", "build"):
                body = scripts.get(name)
                if isinstance(body, str) and body.strip() and not _is_install(body):
                    return ("npm run %s --silent" % name, project_dir)

    # 3. Makefile targets: prefer test, then lint.
    for makefile in ("Makefile", "makefile", "GNUmakefile"):
        mk_path = os.path.join(project_dir, makefile)
        if os.path.isfile(mk_path):
            for target in ("test", "lint"):
                if _makefile_has_target(mk_path, target):
                    return ("make %s" % target, project_dir)
            break  # found a Makefile but no usable target; stop probing names

    # 4. Python: pyproject/pytest config or a tests dir -> pytest.
    has_py_cfg = any(
        os.path.isfile(os.path.join(project_dir, f))
        for f in ("pyproject.toml", "pytest.ini", "tox.ini", "setup.cfg")
    )
    has_tests = os.path.isdir(os.path.join(project_dir, "tests")) or os.path.isdir(
        os.path.join(project_dir, "test")
    )
    if has_py_cfg or has_tests:
        # only pick pytest if it's actually runnable here — a missing pytest would
        # FALSE-FAIL every "done" claim. If absent, return None (treat done as done).
        try:
            r = subprocess.run(["python3", "-c", "import pytest"],
                               capture_output=True, timeout=10)
            if r.returncode == 0:
                return ("python3 -m pytest -q", project_dir)
        except (OSError, subprocess.SubprocessError):
            pass

    return None


def _redact(line: str) -> str:
    """Scrub secret-shaped substrings from a summary line."""
    line = _SECRET_RE.sub("[redacted]", line)
    line = _LONG_BLOB_RE.sub("[redacted]", line)
    return line


# Patterns that tend to carry the meaningful one-line result, newest tail first.
_SIGNAL_RES = (
    re.compile(r"\b\d+\s+failed\b.*", re.IGNORECASE),
    re.compile(r"\b\d+\s+passed\b.*", re.IGNORECASE),
    re.compile(r"\b\d+\s+errors?\b.*", re.IGNORECASE),
    re.compile(r"\b(?:FAIL|FAILED|ERROR|Error)\b.*"),
    re.compile(r"\b(?:PASS|PASSED|ok|OK|SUCCESS)\b.*"),
)


def _summarize(out: str, exit_code: int, prefix: str) -> str:
    """Extract a SHORT human line from the output tail.

    Scans the last lines (where test runners print their verdict) for a
    signal-bearing line; falls back to the last non-empty line, then to the
    bare exit code. Always redacted and length-capped.
    """
    lines = [ln.strip() for ln in (out or "").splitlines() if ln.strip()]
    tail = lines[-15:]
    picked = ""
    for rx in _SIGNAL_RES:
        for ln in reversed(tail):
            if rx.search(ln):
                picked = ln
                break
        if picked:
            break
    if not picked and tail:
        picked = tail[-1]

    if picked:
        picked = _redact(picked)
        if len(picked) > 120:
            picked = picked[:117] + "..."
        summary = "%s: %s" % (prefix, picked) if prefix else picked
    else:
        summary = "%s: exit %d" % (prefix, exit_code) if prefix else "exit %d" % exit_code

    if len(summary) > 160:
        summary = summary[:157] + "..."
    return summary


def _cmd_prefix(cmd: str) -> str:
    """Short label for the summary, derived from the command's first tokens."""
    try:
        parts = shlex.split(cmd)
    except ValueError:
        parts = cmd.split()
    if not parts:
        return "check"
    head = os.path.basename(parts[0])
    if head in ("python", "python3") and "pytest" in parts:
        return "pytest"
    if head == "npm" and len(parts) >= 3 and parts[1] == "run":
        return parts[2]
    if head == "make" and len(parts) >= 2:
        return parts[1]
    return head


def run_check(
    cmd: str, cwd: str, timeout: int = DEFAULT_TIMEOUT
) -> Tuple[bool, int, str]:
    """Run CMD in CWD under a hard timeout; return (passed, exit_code, summary).

    Safety:
      - Refuses install/network-shaped commands (returns passed=False).
      - Clamps timeout into (0, MAX_TIMEOUT]; a timeout => passed=False.
      - Captures combined stdout/stderr; summary is short + secret-redacted.
    """
    if not cmd or not cmd.strip():
        return (False, -1, "no command")
    if _is_install(cmd):
        return (False, -1, "refused install/network command")

    try:
        timeout = int(timeout)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT
    if timeout <= 0:
        timeout = DEFAULT_TIMEOUT
    timeout = min(timeout, MAX_TIMEOUT)

    run_cwd = cwd if cwd and os.path.isdir(cwd) else None
    prefix = _cmd_prefix(cmd)

    # Hermetic-ish env: no interactive pagers, no color noise.
    env = dict(os.environ)
    env.setdefault("CI", "1")
    env.setdefault("NO_COLOR", "1")

    try:
        argv = shlex.split(cmd)
    except ValueError:
        argv = None

    try:
        if argv:
            proc = subprocess.run(
                argv,
                cwd=run_cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                timeout=timeout,
                env=env,
            )
        else:
            proc = subprocess.run(
                cmd,
                cwd=run_cwd,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                timeout=timeout,
                env=env,
            )
    except subprocess.TimeoutExpired:
        return (False, -1, "%s: timed out after %ds" % (prefix, timeout))
    except (OSError, ValueError) as exc:
        return (False, -1, _redact("%s: %s" % (prefix, exc)))

    out = proc.stdout.decode("utf-8", "replace") if proc.stdout else ""
    passed = proc.returncode == 0
    summary = _summarize(out, proc.returncode, prefix)
    return (passed, proc.returncode, summary)
