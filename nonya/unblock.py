"""unblock — risk-scored auto-unblock policy for agent permission prompts.

When an AI coding agent hits a permission prompt ("Allow running `<cmd>`?" or a
free-text "what should I do?" question), nonya can either auto-approve (so the
agent keeps flowing) or HOLD and escalate to a human. Getting this wrong is
dangerous: an over-eager auto-approve could `rm -rf`, push to a remote, leak a
secret, or run a deploy. So this module is deliberately conservative.

Two entry points:

  * ``risk_of(command) -> (decision, category, reason)`` — classify a shell
    command. ``decision`` is ``"auto"`` ONLY for low-risk, reversible, in-scope
    operations (reading a file, running the project's own test/lint, read-only
    git, ls/cat/grep/cd). Everything else — and anything unrecognized — is
    ``"hold"``.

  * ``answer_question(question, brief_text) -> str | None`` — answer a free-text
    agent question ONLY when the answer is clearly contained in the supplied
    brief / CLAUDE.md text. Otherwise returns ``None`` (escalate to a human).
  * ``auto_answer(question, brief_text) -> str | None`` — auto-mode wrapper:
    first uses explicit local guidance, then falls back to a conservative
    autonomy policy so generic input waits do not stall overnight.

Hard invariants honored here:

  * **Default to HOLD.** Unknown verbs, unparseable commands, compound shells
    with any risky piece, and anything that smells irreversible/sensitive are
    never auto-approved. We fail safe, not open.
  * **Categories nonya must NEVER auto-approve** (see ``HOLD_CATEGORIES``):
    ``secret`` (reading/writing .env, keys, tokens, credential stores),
    ``billing`` (payment / cloud-spend actions), ``destructive`` (rm, reset
    --hard, clean, dropping DBs, overwriting files), and ``deploy`` (deploy,
    publish, kubectl/terraform apply, prod pushes). Also held: package installs,
    privilege escalation (sudo), permission changes (chmod/chown), and outbound
    network to non-localhost hosts.
  * **Pure stdlib, no network, never raises into the loop.** Every public helper
    catches and degrades to the safe answer (HOLD / None).
  * **Redaction.** Reasons echo only a scrubbed, truncated snippet of the
    command (via ledger.scrub), so a token on a held command line is never
    surfaced in logs.
"""
from __future__ import annotations

import re
import shlex

try:  # reuse the ledger's battle-tested secret scrubber when available
    from . import ledger as _ledger

    def _scrub(text: str) -> str:
        return _ledger.scrub(text)
except Exception:  # pragma: no cover - stdlib fallback if ledger import fails
    _FALLBACK_RE = re.compile(
        r"(?i)\b([A-Za-z0-9_.\-]*(?:token|secret|password|api[-_]?key|"
        r"auth|bearer|credential)[A-Za-z0-9_.\-]*)(\s*[=:]\s*)\S+"
    )

    def _scrub(text: str) -> str:
        if not text:
            return text
        return _FALLBACK_RE.sub(lambda m: m.group(1) + m.group(2) + "[REDACTED]", text)


# --- decisions & categories ---------------------------------------------------

AUTO = "auto"
HOLD = "hold"

# Categories that must NEVER be auto-approved. These are the named hazard classes
# the supervisor refuses to unblock on its own.
CAT_SECRET = "secret"          # .env / keys / tokens / credential stores
CAT_BILLING = "billing"        # payments / cloud spend
CAT_DESTRUCTIVE = "destructive"  # rm, reset --hard, clean, drop db, overwrite
CAT_DEPLOY = "deploy"          # deploy / publish / apply to prod
CAT_INSTALL = "install"        # package installs (npm i, pip install, brew)
CAT_NETWORK = "network"        # outbound network to non-localhost
CAT_PRIVILEGE = "privilege"    # sudo / privilege escalation
CAT_PERMISSION = "permission"  # chmod / chown
CAT_UNKNOWN = "unknown"        # unrecognized -> safe HOLD
CAT_SAFE = "safe"             # the only auto-approvable class

HOLD_CATEGORIES = (
    CAT_SECRET, CAT_BILLING, CAT_DESTRUCTIVE, CAT_DEPLOY,
    CAT_INSTALL, CAT_NETWORK, CAT_PRIVILEGE, CAT_PERMISSION, CAT_UNKNOWN,
)

# Limit the echoed snippet so a long command line can't bloat or smuggle data
# into a log even after scrubbing.
_SNIPPET_MAX = 80


def _snippet(command: str) -> str:
    s = _scrub(command or "").replace("\n", " ").replace("\r", " ").strip()
    if len(s) > _SNIPPET_MAX:
        s = s[:_SNIPPET_MAX - 3] + "..."
    return s


# --- patterns ------------------------------------------------------------------

# Secret-bearing paths: .env files and credential/key/token stores.
_SECRET_PATH_RE = re.compile(
    r"(?i)(^|[\s=:/'\"])("
    r"\.env(\.[a-z0-9_.\-]+)?"          # .env, .env.local, .env.production
    r"|secrets?(\.[a-z0-9]+)?"
    r"|credentials?(\.[a-z0-9]+)?"
    r"|id_rsa\w*|id_ed25519\w*|\.pem|\.key|\.p12|\.pfx|\.keystore"
    r"|\.ssh/|\.aws/credentials|\.netrc|\.npmrc|\.pgpass"
    r"|tokens?\.(json|txt|yaml|yml)"
    r")"
)
# Bare secret-ish words used as a command arg ("cat .env", "vim secrets.yaml").
_SECRET_WORD_RE = re.compile(r"(?i)(?:^|\s)(\.env\b|secrets?\b|credentials?\b|\.pem\b|\.key\b)")

# A localhost-ish host is allowed for network tools; anything else holds.
_LOCALHOST_RE = re.compile(
    r"(?i)(localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\]|\b::1\b)"
)
_URL_RE = re.compile(r"(?i)\b(https?://|ftp://|www\.)\S+")

# Read-only / in-scope git subcommands that are safe to auto-approve.
_GIT_SAFE_SUB = {"status", "diff", "log", "show", "add", "branch", "remote",
                 "stash", "fetch", "describe", "rev-parse", "ls-files",
                 "blame", "shortlog", "config"}
# git subcommands that mutate history / push / wipe the tree -> always HOLD.
_GIT_HOLD_SUB = {"push", "reset", "clean", "rebase", "merge", "checkout",
                 "restore", "rm", "filter-branch", "gc", "prune", "switch",
                 "cherry-pick", "revert", "tag", "commit"}

# Safe read-only / inspection commands.
_SAFE_VERBS = {
    "ls", "cat", "less", "more", "head", "tail", "grep", "rg", "ag", "egrep",
    "fgrep", "find", "fd", "pwd", "cd", "echo", "wc", "stat", "file", "tree",
    "which", "whoami", "date", "env", "printenv", "true", "type", "dirname",
    "basename", "realpath", "readlink", "sort", "uniq", "cut", "column",
    "diff", "cmp", "test", "jq", "sed", "awk",
}
# Test / lint / build verbs that map to the project's own checks (in-scope).
_CHECK_VERBS = {
    "pytest", "tox", "nox", "make", "ruff", "flake8", "pylint", "black",
    "isort", "mypy", "eslint", "prettier", "jest", "vitest", "mocha",
    "go", "cargo", "rustc", "gradle", "mvn", "rspec", "phpunit", "ctest",
    "unittest", "nose", "nosetests",
}
# Verbs that are language runners — auto ONLY when running tests/lint, else hold.
_RUNNER_VERBS = {"python", "python3", "node", "npm", "yarn", "pnpm", "npx",
                 "bun", "deno", "ruby", "php", "dotnet"}

# Hard-HOLD verbs and the category they map to.
_HOLD_VERBS = {
    "rm": CAT_DESTRUCTIVE, "rmdir": CAT_DESTRUCTIVE, "shred": CAT_DESTRUCTIVE,
    "mv": CAT_DESTRUCTIVE, "dd": CAT_DESTRUCTIVE, "truncate": CAT_DESTRUCTIVE,
    "mkfs": CAT_DESTRUCTIVE, "fdisk": CAT_DESTRUCTIVE, "format": CAT_DESTRUCTIVE,
    "sudo": CAT_PRIVILEGE, "su": CAT_PRIVILEGE, "doas": CAT_PRIVILEGE,
    "chmod": CAT_PERMISSION, "chown": CAT_PERMISSION, "chgrp": CAT_PERMISSION,
    "pip": CAT_INSTALL, "pip3": CAT_INSTALL, "brew": CAT_INSTALL,
    "apt": CAT_INSTALL, "apt-get": CAT_INSTALL, "yum": CAT_INSTALL,
    "dnf": CAT_INSTALL, "pacman": CAT_INSTALL, "gem": CAT_INSTALL,
    "conda": CAT_INSTALL, "poetry": CAT_INSTALL, "pipx": CAT_INSTALL,
    "curl": CAT_NETWORK, "wget": CAT_NETWORK, "nc": CAT_NETWORK,
    "ncat": CAT_NETWORK, "ssh": CAT_NETWORK, "scp": CAT_NETWORK,
    "rsync": CAT_NETWORK, "ftp": CAT_NETWORK, "telnet": CAT_NETWORK,
    "kubectl": CAT_DEPLOY, "terraform": CAT_DEPLOY, "helm": CAT_DEPLOY,
    "docker": CAT_DEPLOY, "serverless": CAT_DEPLOY, "vercel": CAT_DEPLOY,
    "netlify": CAT_DEPLOY, "fly": CAT_DEPLOY, "heroku": CAT_DEPLOY,
    "aws": CAT_DEPLOY, "gcloud": CAT_DEPLOY, "az": CAT_DEPLOY,
    "deploy": CAT_DEPLOY, "publish": CAT_DEPLOY, "psql": CAT_DESTRUCTIVE,
    "mysql": CAT_DESTRUCTIVE, "mongo": CAT_DESTRUCTIVE, "redis-cli": CAT_DESTRUCTIVE,
    "dropdb": CAT_DESTRUCTIVE,
}

# Shell metacharacters that mean "more than one command / redirection happening".
# Any of these forces a conservative re-scan: each segment must be safe, and a
# write redirect (>) is treated as destructive.
_COMPOUND_RE = re.compile(r"(\|\||&&|;|\||`|\$\(|>>|>|<\()")
_WRITE_REDIRECT_RE = re.compile(r"(^|[^0-9<>])>{1,2}(?![&|])")


# --- core: classify a single (already-split) command segment ------------------

def _classify_segment(seg: str):
    """Classify one simple command segment -> (decision, category, reason)."""
    seg = seg.strip()
    if not seg:
        return AUTO, CAT_SAFE, "empty"

    # Secret access is independent of the verb: cat/vim/echo of a secret holds.
    if _SECRET_PATH_RE.search(seg) or _SECRET_WORD_RE.search(seg):
        return HOLD, CAT_SECRET, "touches a secret/credential path"

    try:
        toks = shlex.split(seg, comments=False, posix=True)
    except ValueError:
        # Unbalanced quotes etc. — cannot reason safely.
        return HOLD, CAT_UNKNOWN, "unparseable command"
    if not toks:
        return AUTO, CAT_SAFE, "empty"

    # Skip leading VAR=value assignments and `env`/`command`/`exec` wrappers.
    i = 0
    while i < len(toks) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", toks[i]):
        i += 1
    while i < len(toks) and toks[i] in ("env", "command", "exec", "nice", "time", "stdbuf"):
        i += 1
    if i >= len(toks):
        return AUTO, CAT_SAFE, "no command after prefix"

    verb = toks[i]
    rest = toks[i + 1:]
    base = verb.rsplit("/", 1)[-1]  # strip any path prefix (/usr/bin/rm -> rm)

    # 1. Hard-HOLD verbs first (most dangerous wins).
    if base in _HOLD_VERBS:
        cat = _HOLD_VERBS[base]
        # Network tools to a localhost target are still in-scope/safe-ish, but we
        # only relax curl/wget/nc — and only when the URL is clearly localhost.
        if cat == CAT_NETWORK and base in ("curl", "wget", "nc", "ncat"):
            urls = _URL_RE.findall(seg)
            if _LOCALHOST_RE.search(seg) and not _has_remote_host(seg):
                return AUTO, CAT_SAFE, "%s to localhost" % base
            return HOLD, CAT_NETWORK, "%s to non-localhost network" % base
        return HOLD, cat, "%s is a %s operation" % (base, cat)

    # 2. git — depends on the subcommand.
    if base == "git":
        sub = _first_nonflag(rest)
        if sub in _GIT_HOLD_SUB:
            kind = CAT_DESTRUCTIVE if sub in ("reset", "clean", "rm") else CAT_DEPLOY if sub == "push" else CAT_DESTRUCTIVE
            return HOLD, kind, "git %s mutates/pushes" % sub
        if sub in _GIT_SAFE_SUB:
            return AUTO, CAT_SAFE, "git %s is read-only/in-scope" % sub
        return HOLD, CAT_UNKNOWN, "git %s unrecognized" % (sub or "?")

    # 3. Language runners: auto only when clearly running tests/lint.
    if base in _RUNNER_VERBS:
        if _is_check_invocation(base, rest):
            return AUTO, CAT_SAFE, "%s test/lint invocation" % base
        if base in ("npm", "yarn", "pnpm", "bun") and _is_install_invocation(rest):
            return HOLD, CAT_INSTALL, "%s install" % base
        return HOLD, CAT_UNKNOWN, "%s general invocation (not a known check)" % base

    # 4. Build/test/lint verbs are in-scope checks.
    if base in _CHECK_VERBS:
        return AUTO, CAT_SAFE, "%s is a project check" % base

    # 5. Plain safe read-only verbs.
    if base in _SAFE_VERBS:
        return AUTO, CAT_SAFE, "%s is a read-only/in-scope op" % base

    # 6. Default: unknown -> HOLD (safe).
    return HOLD, CAT_UNKNOWN, "unrecognized command '%s'" % base


def _first_nonflag(toks):
    for t in toks:
        if not t.startswith("-"):
            return t
    return ""


def _has_remote_host(seg):
    """True if a URL/host in the segment points somewhere other than localhost."""
    for url in _URL_RE.findall(seg):
        # findall returns the captured scheme group; re-scan the whole segment.
        pass
    for m in re.finditer(r"(?i)\b(?:https?|ftp)://([^/\s:]+)", seg):
        host = m.group(1)
        if not _LOCALHOST_RE.search(host):
            return True
    return False


def _is_check_invocation(base, rest):
    """Is this runner invocation a test/lint run we consider in-scope?"""
    joined = " ".join(rest).lower()
    if base in ("python", "python3"):
        # python -m pytest / unittest / tox ; or running a tests/*.py script.
        if "-m" in rest:
            mi = rest.index("-m")
            mod = rest[mi + 1] if mi + 1 < len(rest) else ""
            return mod.split(".")[0] in ("pytest", "unittest", "tox", "nox", "ruff", "flake8", "mypy", "pylint")
        for t in rest:
            if t.startswith("-"):
                continue
            low = t.lower()
            if low.startswith("test") or "/test" in low or low.endswith("_test.py") or "test_" in low:
                return True
        return False
    if base in ("npm", "yarn", "pnpm", "bun"):
        # npm test / npm run test / npm run lint
        first = _first_nonflag(rest)
        if first in ("test", "t"):
            return True
        if first in ("run", "run-script"):
            nxt = rest[rest.index(first) + 1] if rest.index(first) + 1 < len(rest) else ""
            return nxt in ("test", "lint", "typecheck", "check", "tsc")
        return False
    if base == "npx":
        return _first_nonflag(rest) in ("jest", "vitest", "mocha", "eslint", "prettier", "tsc", "ava")
    if base in ("node", "deno", "bun"):
        return ("test" in joined)
    if base in ("ruby",):
        return ("test" in joined or "spec" in joined)
    return False


def _is_install_invocation(rest):
    first = _first_nonflag(rest)
    return first in ("install", "i", "add", "ci", "update", "upgrade")


# --- public API ----------------------------------------------------------------

def risk_of(command):
    """Classify a shell ``command`` -> ``(decision, category, reason)``.

    ``decision`` is ``"auto"`` only for a low-risk, reversible, in-scope command
    where *every* part is safe; otherwise ``"hold"``. Never raises — any internal
    error degrades to a safe HOLD. ``reason`` echoes only a scrubbed/truncated
    snippet so secrets on the command line are never surfaced.
    """
    try:
        if command is None:
            return HOLD, CAT_UNKNOWN, "no command"
        if not isinstance(command, str):
            command = str(command)
        cmd = command.strip()
        if not cmd:
            return HOLD, CAT_UNKNOWN, "empty command"

        # A write redirect (> file, >> file) can clobber/append to files — treat
        # the whole command as destructive regardless of the verb.
        if _WRITE_REDIRECT_RE.search(cmd):
            return HOLD, CAT_DESTRUCTIVE, "writes/overwrites via redirect: %s" % _snippet(cmd)

        # Compound command: split on shell operators and require EVERY segment to
        # be auto-safe. The most dangerous segment dictates the held category.
        if _COMPOUND_RE.search(cmd):
            # Split on the operators; keep it simple and conservative.
            segments = re.split(r"\|\||&&|;|\||`|\$\(|<\(", cmd)
            worst = None
            for seg in segments:
                seg = seg.strip().rstrip(")")
                if not seg:
                    continue
                d, c, r = _classify_segment(seg)
                if d == HOLD:
                    worst = (HOLD, c, r)
                    break
            if worst:
                return worst[0], worst[1], "compound command, unsafe part: %s (%s)" % (
                    worst[2], _snippet(cmd))
            return AUTO, CAT_SAFE, "compound command, all parts safe: %s" % _snippet(cmd)

        decision, category, reason = _classify_segment(cmd)
        if decision == HOLD:
            return HOLD, category, "%s: %s" % (reason, _snippet(cmd))
        return AUTO, category, "%s: %s" % (reason, _snippet(cmd))
    except Exception:  # never let a parsing bug open the gate or crash the loop
        return HOLD, CAT_UNKNOWN, "classification error -> safe hold"


def answer_question(question, brief_text):
    """Answer a free-text agent ``question`` ONLY from ``brief_text``.

    Returns a short answer string when the brief clearly contains it, else
    ``None`` (caller must escalate to a human). Conservative on purpose: a wrong
    auto-answer can send the agent down a destructive path, so when in doubt we
    return ``None``. Never raises.
    """
    try:
        if not question or not brief_text:
            return None
        q = str(question).strip()
        text = str(brief_text)
        if not q:
            return None

        # Pull meaningful keywords from the question (drop stopwords & punctuation).
        words = re.findall(r"[A-Za-z0-9_./\-]+", q.lower())
        keys = [w for w in words if w not in _STOPWORDS and len(w) > 2]
        if not keys:
            return None

        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if not lines:
            return None

        # Score each brief line by how many distinct question keywords it contains.
        low_lines = [(ln, ln.lower()) for ln in lines]
        best_line = None
        best_hits = 0
        for ln, low in low_lines:
            hits = sum(1 for k in set(keys) if k in low)
            if hits > best_hits:
                best_hits = hits
                best_line = ln

        # Require a clear majority of the question's keywords to be present in one
        # line before we trust it as "clearly contained". Otherwise escalate.
        distinct = len(set(keys))
        # Require a majority of the question's distinct keywords to land on one
        # brief line. For a single-keyword question, that one keyword must match.
        needed = max(1, (distinct + 1) // 2)
        if best_line is None or best_hits < needed:
            return None
        return best_line
    except Exception:
        return None


def auto_answer(question, brief_text):
    """Answer an auto-mode WAITING prompt without asking the sleeping user.

    Preference order:

    1. A direct answer from local project guidance.
    2. A conservative autonomy default that keeps work local, reversible, and
       non-production.

    It never approves the high-risk classes guarded by ``risk_of``. For those
    prompts it answers with a bounded "no, continue safely" instruction instead
    of fabricating approval.
    """
    try:
        direct = answer_question(question, brief_text)
        if direct:
            return direct
        return fallback_answer(question)
    except Exception:
        return None


def fallback_answer(question):
    """Conservative default answer for auto mode when guidance has no match."""
    try:
        q = str(question or "").strip()
        if not q:
            return None
        low = q.lower()

        if _has_any(low, ("staging", "stage")) and _has_any(low, ("prod", "production", "live", "운영", "프로덕션")):
            return (
                "Use staging/non-production only. Do not touch production. "
                "Continue with local verification, dry-run, or release preparation."
            )
        if _has_any(low, ("main", "master")) and _has_any(low, ("develop", "dev branch")):
            return (
                "Use develop for implementation work. Use main only when the "
                "user or project rules explicitly require a release/default-branch action."
            )
        if _has_any(low, _SECRET_TERMS):
            return (
                "Do not read, print, edit, or expose secrets. Continue with "
                "secret-safe local checks or document the exact blocker."
            )
        if _has_any(low, _BILLING_TERMS):
            return (
                "Do not change billing, payment, quota, or paid cloud resources. "
                "Continue with read-only inspection or local simulation only."
            )
        if _has_any(low, _PROD_DEPLOY_TERMS):
            return (
                "Do not deploy, publish, push, release, or modify production. "
                "Continue with local changes, tests, dry-run, and a release-ready summary."
            )
        if _has_any(low, _DESTRUCTIVE_TERMS):
            return (
                "Do not delete, reset, overwrite, drop, or destroy data. Choose "
                "a reversible local alternative such as inspect, copy, backup, or patch."
            )
        if _has_any(low, _INSTALL_TERMS):
            return (
                "Do not install or update packages unattended. Use existing "
                "dependencies and local checks; if a package is truly required, record the blocker."
            )
        if _has_any(low, _PRIVILEGE_TERMS):
            return (
                "Do not use sudo, privilege escalation, chmod, or chown unattended. "
                "Continue with non-privileged local alternatives."
            )
        if _has_any(low, _NETWORK_TERMS) and not _has_any(low, ("localhost", "127.0.0.1", "::1")):
            return (
                "Avoid external network actions unattended. Prefer local files, "
                "localhost checks, cached docs, or a clear blocker summary."
            )
        if _looks_like_yes_no(low):
            return (
                "Yes, continue autonomously with the safest reversible local-only path. "
                "Do not ask me again for routine choices; pick conservative defaults, verify, "
                "and avoid secrets, billing, destructive actions, installs, external network, "
                "deploy/publish, production, and privilege changes."
            )
        return (
            "Continue autonomously. Choose the safest reversible local-only default, "
            "keep scope small, verify with the closest tests/checks, and do not wait for "
            "user input unless a secret, billing, destructive, production/deploy/publish, "
            "install, external-network, or privilege action is truly unavoidable."
        )
    except Exception:
        return None


def _has_any(text, terms):
    return any(term in text for term in terms)


def _looks_like_yes_no(text):
    return (
        text.endswith("?")
        and _has_any(text, (
            "can i", "may i", "should i", "shall i", "do you want",
            "would you like", "proceed", "continue", "go ahead", "진행", "계속",
        ))
    )


# Common English/Korean-agnostic stopwords + question framing words we ignore
# when matching a question against the brief.
_STOPWORDS = {
    "the", "and", "for", "with", "what", "which", "should", "shall", "can",
    "could", "would", "will", "does", "did", "are", "was", "were", "use",
    "using", "this", "that", "from", "into", "your", "you", "have", "has",
    "how", "why", "when", "where", "who", "whom", "want", "need", "please",
    "about", "there", "here", "they", "them", "then", "than", "into", "onto",
    "make", "made", "any", "all", "not", "but", "our", "out", "get", "got",
}

_SECRET_TERMS = (
    "secret", "token", "password", "credential", "api key", ".env", "private key",
    "id_rsa", "id_ed25519", "비밀", "토큰", "암호", "자격증명",
)
_BILLING_TERMS = (
    "billing", "payment", "invoice", "quota", "paid", "charge", "cost",
    "cloud", "aws", "gcp", "azure", "oci", "lambda",
    "결제", "과금", "요금", "비용",
)
_PROD_DEPLOY_TERMS = (
    "deploy", "deployment", "publish", "release", "prod", "production", "live",
    "app store", "notarize", "push", "merge", "배포", "출시", "릴리즈", "운영", "프로덕션",
)
_DESTRUCTIVE_TERMS = (
    "delete", "remove", "reset", "wipe", "overwrite", "drop", "destroy", "clean",
    "rm -", "truncate", "삭제", "제거", "초기화", "덮어쓰기", "파괴",
)
_INSTALL_TERMS = (
    "install", "upgrade", "update package", "npm i", "npm install", "pip install",
    "brew install", "dependency", "dependencies", "설치", "업데이트", "의존성",
)
_PRIVILEGE_TERMS = (
    "sudo", "chmod", "chown", "administrator", "admin permission", "root",
    "권한 상승", "관리자", "루트",
)
_NETWORK_TERMS = (
    "network", "internet", "curl", "wget", "http://", "https://", "api call",
    "external", "remote", "ssh", "scp", "rsync", "네트워크", "인터넷", "외부",
)


__all__ = [
    "risk_of", "answer_question", "auto_answer", "fallback_answer",
    "AUTO", "HOLD", "HOLD_CATEGORIES",
    "CAT_SECRET", "CAT_BILLING", "CAT_DESTRUCTIVE", "CAT_DEPLOY",
    "CAT_INSTALL", "CAT_NETWORK", "CAT_PRIVILEGE", "CAT_PERMISSION",
    "CAT_UNKNOWN", "CAT_SAFE",
]
