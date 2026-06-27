#!/usr/bin/env python3
"""Unit tests for nonya.remote — the off-hot-path escalation push module.

No real network: urllib.request.urlopen is monkeypatched to capture each
request. Plain asserts, house style (check(label, got, want)):

    python3 tests/test_remote.py
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)

import urllib.request  # noqa: E402

from nonya import remote  # noqa: E402

_fail = 0


def check(label, got, want):
    global _fail
    if got == want:
        print("ok    %-40s -> %s" % (label, got))
    else:
        print("FAIL  %-40s -> %s (want %s)" % (label, got, want))
        _fail = 1


# --- capture harness ----------------------------------------------------------

class _Resp:
    """Minimal urlopen() return: context-manager-ish, status 200, optional body."""

    def __init__(self, body=b""):
        self._body = body
        self.status = 200

    def read(self):
        return self._body

    def getcode(self):
        return 200

    def close(self):
        pass


_captured = []


def _fake_urlopen_ok(req, timeout=None, body=b""):
    _captured.append({"req": req, "timeout": timeout})
    return _Resp(body)


def install(urlopen):
    remote.urllib.request.urlopen = urlopen
    urllib.request.urlopen = urlopen


def reset_env():
    for k in ("NONYA_NTFY_TOPIC", "NONYA_TELEGRAM_TOKEN", "NONYA_TELEGRAM_CHAT"):
        os.environ.pop(k, None)


def req_url(req):
    return req.full_url if hasattr(req, "full_url") else req.get_full_url()


def req_body(req):
    data = req.data
    return data.decode("utf-8") if isinstance(data, (bytes, bytearray)) else data


_orig_urlopen = urllib.request.urlopen


# --- scrub: a fake token in the body is redacted before sending ---------------
secret_body = "deploy failed, token=" + ("ghp_" + "a" * 36) + " here"
scrubbed = remote.scrub(secret_body)
check("scrub removes token", ("ghp_" + "a" * 36) in scrubbed, False)
check("scrub keeps surrounding text", "deploy failed" in scrubbed, True)


# --- no channel configured -> push returns False, no request sent -------------
reset_env()
_captured.clear()
install(_fake_urlopen_ok)
check("push no channel -> False", remote.push("t", "b"), False)
check("push no channel -> no request", len(_captured), 0)


# --- ntfy: URL carries the topic, body is the (scrubbed) message --------------
reset_env()
os.environ["NONYA_NTFY_TOPIC"] = "my-secret-topic"
_captured.clear()
install(_fake_urlopen_ok)
ok = remote.push("nonya alert", "agent stuck; key=" + ("sk-" + "a" * 32) + " leaked")
check("ntfy push -> True", ok, True)
check("ntfy URL has topic", req_url(_captured[0]["req"]), "https://ntfy.sh/my-secret-topic")
check("ntfy body redacted", ("sk-" + "a" * 32) in req_body(_captured[0]["req"]), False)
check("ntfy body redaction marker", "[REDACTED]" in req_body(_captured[0]["req"]), True)
check("ntfy timeout bounded", _captured[0]["timeout"] <= 5.0, True)


# --- telegram: chat URL + JSON text body, redacted ----------------------------
reset_env()
os.environ["NONYA_TELEGRAM_TOKEN"] = "111:AAA"
os.environ["NONYA_TELEGRAM_CHAT"] = "99887766"
_captured.clear()
install(_fake_urlopen_ok)
ok = remote.push("alert", "secret=password123 in logs")
check("telegram push -> True", ok, True)
tg_url = req_url(_captured[0]["req"])
check("telegram URL has token", "/bot111:AAA/sendMessage" in tg_url, True)
payload = json.loads(req_body(_captured[0]["req"]))
check("telegram chat_id", payload["chat_id"], "99887766")
check("telegram text redacted", "password123" in payload["text"], False)
check("telegram timeout bounded", _captured[0]["timeout"] <= 5.0, True)


# --- both channels configured -> both fire, push True -------------------------
reset_env()
os.environ["NONYA_NTFY_TOPIC"] = "topicX"
os.environ["NONYA_TELEGRAM_TOKEN"] = "222:BBB"
os.environ["NONYA_TELEGRAM_CHAT"] = "42"
_captured.clear()
install(_fake_urlopen_ok)
check("both channels -> True", remote.push("hi", "body"), True)
check("both channels -> 2 requests", len(_captured), 2)


# --- failure is graceful: urlopen raises -> push False, never raises ----------
def _fake_urlopen_boom(req, timeout=None):
    raise OSError("network down")


reset_env()
os.environ["NONYA_NTFY_TOPIC"] = "topicY"
install(_fake_urlopen_boom)
try:
    res = remote.push("t", "b")
    raised = False
except Exception:
    res = None
    raised = True
check("push on error -> False", res, False)
check("push on error never raises", raised, False)


# --- poll_reply: returns latest Telegram text, clamps timeout, never raises ---
reset_env()
os.environ["NONYA_TELEGRAM_TOKEN"] = "333:CCC"
updates = {"ok": True, "result": [
    {"message": {"text": "old"}},
    {"message": {"text": "use sqlite instead"}},
]}


def _fake_poll(req, timeout=None):
    _captured.append({"req": req, "timeout": timeout})
    return _Resp(json.dumps(updates).encode("utf-8"))


_captured.clear()
install(_fake_poll)
check("poll_reply returns latest", remote.poll_reply(3), "use sqlite instead")
check("poll_reply timeout clamped", _captured[0]["timeout"] <= 5.0, True)
check("poll_reply clamps big timeout", (
    _fake_poll, remote.poll_reply(9999), _captured[-1]["timeout"] <= 5.0)[2], True)

# poll_reply with no token -> None, no request
reset_env()
_captured.clear()
install(_fake_poll)
check("poll_reply no token -> None", remote.poll_reply(1), None)
check("poll_reply no token -> no request", len(_captured), 0)

# poll_reply: empty result set -> None
reset_env()
os.environ["NONYA_TELEGRAM_TOKEN"] = "444:DDD"


def _fake_poll_empty(req, timeout=None):
    return _Resp(json.dumps({"ok": True, "result": []}).encode("utf-8"))


install(_fake_poll_empty)
check("poll_reply empty -> None", remote.poll_reply(1), None)

# poll_reply: urlopen raises -> None, never raises
install(_fake_urlopen_boom)
try:
    pr = remote.poll_reply(1)
    pr_raised = False
except Exception:
    pr = "EXC"
    pr_raised = True
check("poll_reply on error -> None", pr, None)
check("poll_reply on error never raises", pr_raised, False)


# --- restore + report ---------------------------------------------------------
install(_orig_urlopen)
reset_env()

print("ALL PASS" if _fail == 0 else "SOME FAILED")
sys.exit(_fail)
