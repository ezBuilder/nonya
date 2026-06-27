#!/usr/bin/env python3
"""Unit tests for nonya.verify — discover_check + run_check.

Plain asserts (no pytest needed):

    python3 tests/test_verify.py

We build throwaway temp project dirs with trivial passing/failing checks; we
never run heavy installs.
"""
import os
import stat
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)

from nonya import verify  # noqa: E402

_fail = 0


def check(label, got, want):
    global _fail
    if got == want:
        print("ok    %-34s -> %s" % (label, got))
    else:
        print("FAIL  %-34s -> %s (want %s)" % (label, got, want))
        _fail = 1


def _write(path, text, executable=False):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    if executable:
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC | stat.S_IRWXU)


def _scratch_script(d, name, exit_code, msg=""):
    """Create an executable script that prints MSG then exits EXIT_CODE."""
    p = os.path.join(d, name)
    body = "#!/bin/sh\n"
    if msg:
        body += 'echo "%s"\n' % msg
    body += "exit %d\n" % exit_code
    _write(p, body, executable=True)
    return p


# --- discover_check: nothing present -> None (safe skip) ---
_empty = tempfile.mkdtemp()
check("discover empty -> None", verify.discover_check(_empty), None)
check("discover bad dir -> None", verify.discover_check(os.path.join(_empty, "nope")), None)
check("discover empty-string -> None", verify.discover_check(""), None)

# --- discover_check: env override wins ---
os.environ["NONYA_CHECK_CMD"] = "true"
ov = verify.discover_check(_empty)
check("discover env override cmd", ov[0], "true")
check("discover env override cwd", ov[1], _empty)
# install-shaped override is rejected
os.environ["NONYA_CHECK_CMD"] = "npm install && true"
check("discover env install rejected", verify.discover_check(_empty), None)
del os.environ["NONYA_CHECK_CMD"]

# --- discover_check: package.json scripts.test ---
_pkg = tempfile.mkdtemp()
_write(os.path.join(_pkg, "package.json"),
       '{"scripts": {"build": "tsc", "test": "jest"}}')
check("discover package test", verify.discover_check(_pkg),
      ("npm run test --silent", _pkg))
# only build present -> falls through to build
_pkgb = tempfile.mkdtemp()
_write(os.path.join(_pkgb, "package.json"), '{"scripts": {"build": "tsc"}}')
check("discover package build", verify.discover_check(_pkgb)[0],
      "npm run build --silent")
# test script that is an install -> skipped, nothing else -> None
_pkgi = tempfile.mkdtemp()
_write(os.path.join(_pkgi, "package.json"),
       '{"scripts": {"test": "npm ci && jest"}}')
check("discover package install-test skipped", verify.discover_check(_pkgi), None)

# --- discover_check: Makefile target ---
_mk = tempfile.mkdtemp()
_write(os.path.join(_mk, "Makefile"), "lint:\n\tflake8\ntest:\n\tpytest\n")
check("discover makefile test", verify.discover_check(_mk), ("make test", _mk))
_mkl = tempfile.mkdtemp()
_write(os.path.join(_mkl, "Makefile"), "lint:\n\tflake8\n")
check("discover makefile lint", verify.discover_check(_mkl)[0], "make lint")

# --- discover_check: pytest fallback (only when pytest is actually importable;
#     otherwise None so a missing pytest never FALSE-FAILS a "done" claim) ---
import subprocess as _sp
_HAS_PYTEST = _sp.run(["python3", "-c", "import pytest"], capture_output=True).returncode == 0
_py = tempfile.mkdtemp()
_write(os.path.join(_py, "pyproject.toml"), "[project]\nname='x'\n")
check("discover pyproject pytest", verify.discover_check(_py),
      ("python3 -m pytest -q", _py) if _HAS_PYTEST else None)
_pyt = tempfile.mkdtemp()
os.makedirs(os.path.join(_pyt, "tests"))
check("discover tests-dir pytest", (verify.discover_check(_pyt) or (None,))[0],
      "python3 -m pytest -q" if _HAS_PYTEST else None)

# --- discover priority: package.json beats Makefile beats pytest ---
_prio = tempfile.mkdtemp()
_write(os.path.join(_prio, "package.json"), '{"scripts": {"test": "jest"}}')
_write(os.path.join(_prio, "Makefile"), "test:\n\tpytest\n")
_write(os.path.join(_prio, "pyproject.toml"), "[project]\nname='x'\n")
check("discover priority package", verify.discover_check(_prio)[0],
      "npm run test --silent")

# --- run_check: passing command ---
_run = tempfile.mkdtemp()
ok_sh = _scratch_script(_run, "ok.sh", 0, "3 passed in test_auth.py")
passed, code, summary = verify.run_check(ok_sh, _run, timeout=10)
check("run passing -> passed", passed, True)
check("run passing -> exit 0", code, 0)
check("run passing summary has passed", "passed" in summary, True)

# bare `true` passes
p2, c2, _ = verify.run_check("true", _run, timeout=10)
check("run true passed", p2, True)
check("run true exit", c2, 0)

# --- run_check: failing command ---
fail_sh = _scratch_script(_run, "fail.sh", 1, "3 failed in test_auth.py")
passed, code, summary = verify.run_check(fail_sh, _run, timeout=10)
check("run failing -> not passed", passed, False)
check("run failing -> exit 1", code, 1)
check("run failing summary has failed", "failed" in summary, True)

# bare `false` fails
p3, c3, _ = verify.run_check("false", _run, timeout=10)
check("run false not passed", p3, False)
check("run false exit", c3, 1)

# --- run_check: install command refused (no network) ---
pr, cr, sr = verify.run_check("npm install", _run, timeout=10)
check("run install refused passed", pr, False)
check("run install refused exit", cr, -1)
check("run install refused summary", "refused" in sr, True)

# --- run_check: empty command ---
pe, ce, _ = verify.run_check("", _run, timeout=10)
check("run empty not passed", pe, False)

# --- run_check: timeout bound (sleep beyond hard timeout) ---
slow_sh = _scratch_script(_run, "slow.sh", 0, "")
with open(slow_sh, "w", encoding="utf-8") as fh:
    fh.write("#!/bin/sh\nsleep 5\nexit 0\n")
os.chmod(slow_sh, os.stat(slow_sh).st_mode | stat.S_IRWXU)
pt, ct, st_ = verify.run_check(slow_sh, _run, timeout=1)
check("run timeout not passed", pt, False)
check("run timeout exit", ct, -1)
check("run timeout summary", "timed out" in st_, True)

# --- run_check: secret redaction in summary ---
sec_sh = _scratch_script(_run, "sec.sh", 1, "FAILED api_key=sk-ABC123secretvalue99")
ps, cs, ss = verify.run_check(sec_sh, _run, timeout=10)
check("run secret redacted", "sk-ABC123secretvalue99" not in ss, True)
check("run secret has marker", "[redacted]" in ss, True)

# --- run_check: bad cwd degrades to None cwd, still runs ---
pb, cb, _ = verify.run_check("true", os.path.join(_run, "nope"), timeout=10)
check("run bad cwd still runs", pb, True)

# --- end-to-end: discover then run a real passing pytest-free check ---
_e2e = tempfile.mkdtemp()
_write(os.path.join(_e2e, "Makefile"), "test:\n\ttrue\n")
disc = verify.discover_check(_e2e)
check("e2e discover", disc[0], "make test")
# only run make if available
import shutil  # noqa: E402
if shutil.which("make"):
    pe2, ce2, _ = verify.run_check(disc[0], disc[1], timeout=20)
    check("e2e make test passes", pe2, True)
else:
    print("skip  e2e make test (make not installed)")

print("ALL PASS" if _fail == 0 else "SOME FAILED")
sys.exit(_fail)
