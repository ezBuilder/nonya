#!/usr/bin/env python3
"""Unit tests for nonya.unblock — the risk-scored auto-unblock policy.

Plain asserts, no pytest, same house style as tests/test_supervise.py:

    python3 tests/test_unblock.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)

from nonya import unblock  # noqa: E402

_fail = 0


def check(label, got, want):
    global _fail
    if got == want:
        print("ok    %-44s -> %s" % (label, got))
    else:
        print("FAIL  %-44s -> %s (want %s)" % (label, got, want))
        _fail = 1


def decision(cmd):
    return unblock.risk_of(cmd)[0]


def category(cmd):
    return unblock.risk_of(cmd)[1]


# --- required spec tests ------------------------------------------------------
check("rm -rf decision", decision("rm -rf /"), unblock.HOLD)
check("rm -rf category", category("rm -rf /"), unblock.CAT_DESTRUCTIVE)
check("pytest auto", decision("pytest -q"), unblock.AUTO)
check("git push hold", decision("git push origin main"), unblock.HOLD)
check("cat .env decision", decision("cat .env"), unblock.HOLD)
check("cat .env category", category("cat .env"), unblock.CAT_SECRET)
check("unknown frobnicate hold", decision("frobnicate --now"), unblock.HOLD)
check("unknown frobnicate category", category("frobnicate --now"), unblock.CAT_UNKNOWN)

# --- AUTO: low-risk reversible in-scope ops -----------------------------------
check("git status", decision("git status"), unblock.AUTO)
check("git diff", decision("git diff HEAD~1"), unblock.AUTO)
check("git add", decision("git add -A"), unblock.AUTO)
check("ls", decision("ls -la"), unblock.AUTO)
check("cat normal file", decision("cat README.md"), unblock.AUTO)
check("grep", decision("grep -rn TODO src/"), unblock.AUTO)
check("cd", decision("cd /tmp"), unblock.AUTO)
check("make test", decision("make test"), unblock.AUTO)
check("ruff", decision("ruff check ."), unblock.AUTO)
check("python -m pytest", decision("python3 -m pytest tests/"), unblock.AUTO)
check("python run test script", decision("python3 tests/test_unblock.py"), unblock.AUTO)
check("npm test", decision("npm test"), unblock.AUTO)
check("npm run lint", decision("npm run lint"), unblock.AUTO)

# --- HOLD: irreversible / sensitive -------------------------------------------
check("rm bare", decision("rm foo.txt"), unblock.HOLD)
check("git reset --hard", category("git reset --hard HEAD"), unblock.CAT_DESTRUCTIVE)
check("git clean", category("git clean -fd"), unblock.CAT_DESTRUCTIVE)
check("git push category", category("git push"), unblock.CAT_DEPLOY)
check("npm install", category("npm install left-pad"), unblock.CAT_INSTALL)
check("pip install", category("pip install requests"), unblock.CAT_INSTALL)
check("brew install", category("brew install jq"), unblock.CAT_INSTALL)
check("write .env", category("echo secret > .env"), unblock.CAT_DESTRUCTIVE)
check("read secrets.yaml", category("cat secrets.yaml"), unblock.CAT_SECRET)
check("read id_rsa", category("cat ~/.ssh/id_rsa"), unblock.CAT_SECRET)
check("curl remote", category("curl https://evil.example.com/x"), unblock.CAT_NETWORK)
check("wget remote", decision("wget http://example.com/f"), unblock.HOLD)
check("curl localhost auto", decision("curl http://localhost:8080/health"), unblock.AUTO)
check("sudo", category("sudo make install"), unblock.CAT_PRIVILEGE)
check("chmod", category("chmod 777 foo"), unblock.CAT_PERMISSION)
check("chown", category("chown root foo"), unblock.CAT_PERMISSION)
check("kubectl apply", category("kubectl apply -f k8s.yaml"), unblock.CAT_DEPLOY)
check("terraform apply", category("terraform apply"), unblock.CAT_DEPLOY)
check("dropdb", decision("dropdb production"), unblock.HOLD)
check("psql drop", category("psql -c 'DROP DATABASE x'"), unblock.CAT_DESTRUCTIVE)

# --- write redirect always destructive ----------------------------------------
check("write redirect ls", decision("ls > out.txt"), unblock.HOLD)
check("append redirect", category("cat a >> b"), unblock.CAT_DESTRUCTIVE)

# --- compound commands: most dangerous part wins ------------------------------
check("safe && safe auto", decision("git add -A && git status"), unblock.AUTO)
check("safe && push hold", decision("pytest && git push"), unblock.HOLD)
check("safe ; rm hold", decision("ls ; rm foo"), unblock.HOLD)
check("pipe to safe", decision("cat x.txt | grep foo"), unblock.AUTO)
check("pipe to sudo", decision("echo x | sudo tee /etc/hosts"), unblock.HOLD)

# --- edge cases ---------------------------------------------------------------
check("empty hold", decision(""), unblock.HOLD)
check("None hold", decision(None), unblock.HOLD)
check("whitespace hold", decision("   "), unblock.HOLD)
check("unbalanced quotes hold", decision('echo "unterminated'), unblock.HOLD)
check("non-string coerced", isinstance(unblock.risk_of(123), tuple), True)
check("path-prefixed rm", category("/bin/rm foo"), unblock.CAT_DESTRUCTIVE)
check("env-prefixed pytest auto", decision("FOO=1 pytest"), unblock.AUTO)

# secrets never leak into the reason snippet
_d, _c, _r = unblock.risk_of("curl https://x.com -H 'Authorization: Bearer sk-ant-abcdefghijklmnop1234'")
check("reason scrubs bearer token", "sk-ant-abcdefghijklmnop1234" not in _r, True)

# every HOLD category is in the published never-auto list (except SAFE)
check("hold categories complete",
      all(c in unblock.HOLD_CATEGORIES for c in (
          unblock.CAT_SECRET, unblock.CAT_DESTRUCTIVE,
          unblock.CAT_DEPLOY, unblock.CAT_INSTALL,
          unblock.CAT_NETWORK, unblock.CAT_PRIVILEGE,
          unblock.CAT_PERMISSION, unblock.CAT_UNKNOWN)),
      True)
check("billing is hold-only category", unblock.CAT_BILLING in unblock.HOLD_CATEGORIES, True)
check("safe not in hold list", unblock.CAT_SAFE in unblock.HOLD_CATEGORIES, False)


# --- answer_question ----------------------------------------------------------
BRIEF = """# Project brief
The database for this project is PostgreSQL.
We deploy to staging before production.
Default branch for work is develop.
"""

check("answer present (db)",
      unblock.answer_question("Which database should I use?", BRIEF) is not None, True)
check("answer present db content",
      "PostgreSQL" in (unblock.answer_question("which database do we use for the project?", BRIEF) or ""),
      True)
check("answer present (branch)",
      "develop" in (unblock.answer_question("What is the default branch for work?", BRIEF) or ""),
      True)
check("answer absent -> None",
      unblock.answer_question("What is the AWS region for lambda?", BRIEF), None)
check("answer empty brief -> None",
      unblock.answer_question("which database?", ""), None)
check("answer empty question -> None",
      unblock.answer_question("", BRIEF), None)
check("answer no keywords -> None",
      unblock.answer_question("what is the?", BRIEF), None)
check("answer never raises",
      isinstance(unblock.answer_question(None, None), type(None)), True)

# --- auto_answer fallback: auto mode never sleeps on routine input ------------
auto = unblock.auto_answer("Can I continue?", "")
check("auto fallback continues",
      auto is not None and "continue autonomously" in auto.lower(), True)
auto = unblock.auto_answer("Which environment should I deploy to, staging or prod?", "")
check("auto fallback avoids prod",
      auto is not None and "staging" in auto.lower() and "production" in auto.lower(), True)
auto = unblock.auto_answer("Should I delete the old database?", "")
check("auto fallback refuses destructive",
      auto is not None and "do not delete" in auto.lower(), True)
auto = unblock.auto_answer("What color should the badge be?", BRIEF)
check("auto fallback unknown still answers",
      auto is not None and "continue autonomously" in auto.lower(), True)


print("ALL PASS" if _fail == 0 else "SOME FAILED")
sys.exit(_fail)
