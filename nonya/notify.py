"""Logging + notifications, cross-platform.

macOS: the menu-bar app posts banners NATIVELY (UNUserNotifications) by draining a queue file —
the core NEVER calls `osascript display notification` (macOS would attribute it to "Script Editor":
wrong icon, and a click launches Script Editor). No app running => no banner (queued line skipped).
Windows: PowerShell balloon/toast (best effort) + console bell.
Both: optional phone push via ntfy.sh when NONYA_NTFY_TOPIC is set.
Network push is best-effort and never blocks the hot path for long.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

def log(msg: str) -> None:
    line = "%s | %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, file=sys.stderr, flush=True)
    logpath = os.environ.get("NONYA_LOG", "")
    if logpath:
        try:
            with open(logpath, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass


def _play_sound(sound: str) -> None:
    """Play a short system sound. No banner — see notify()'s comment on why we never osascript."""
    try:
        subprocess.Popen(["afplay", "/System/Library/Sounds/%s.aiff" % sound],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


def _notify_windows(title: str, msg: str) -> None:
    # Best-effort balloon via PowerShell + Windows Forms. Degrades to console bell.
    ps = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$n=New-Object System.Windows.Forms.NotifyIcon;"
        "$n.Icon=[System.Drawing.SystemIcons]::Information;"
        "$n.BalloonTipTitle=%r;$n.BalloonTipText=%r;"
        "$n.Visible=$true;$n.ShowBalloonTip(8000);Start-Sleep -Milliseconds 200"
    ) % (title, msg)
    try:
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    except (OSError, subprocess.SubprocessError):
        sys.stderr.write("\a")


def _push(title: str, msg: str) -> None:
    topic = os.environ.get("NONYA_NTFY_TOPIC", "")
    if not topic:
        return
    url = "https://ntfy.sh/%s" % topic
    try:
        import urllib.request
        req = urllib.request.Request(url, data=msg.encode("utf-8"),
                                     headers={"Title": title.encode("ascii", "replace").decode()})
        urllib.request.urlopen(req, timeout=8).close()
    except Exception:
        pass


def _post(url: str, data: bytes, headers=None) -> bool:
    try:
        import urllib.request
        req = urllib.request.Request(url, data=data, headers=headers or {})
        urllib.request.urlopen(req, timeout=8).close()
        return True
    except Exception:
        return False


def _telegram(title: str, msg: str) -> bool:
    token = os.environ.get("NONYA_TG_TOKEN", "")
    chat = os.environ.get("NONYA_TG_CHAT", "")
    if not (token and chat):
        return False
    import json
    body = json.dumps({"chat_id": chat, "text": "%s\n%s" % (title, msg)}).encode("utf-8")
    return _post("https://api.telegram.org/bot%s/sendMessage" % token, body,
                 {"Content-Type": "application/json"})


def _slack(title: str, msg: str) -> bool:
    hook = os.environ.get("NONYA_SLACK_WEBHOOK", "")
    if not hook:
        return False
    import json
    body = json.dumps({"text": "*%s*\n%s" % (title, msg)}).encode("utf-8")
    return _post(hook, body, {"Content-Type": "application/json"})


def _state_dir() -> str:
    return os.path.expanduser(os.environ.get("NONYA_STATE", "~/.local/state/nonya"))


def _app_alive() -> bool:
    """The menu-bar app touches <state>/.app-alive while running. If fresh, IT posts
    notifications natively (proper nonya attribution + click opens the briefing), so
    the core must NOT also fire osascript (which macOS attributes to Script Editor)."""
    try:
        return (time.time() - os.path.getmtime(os.path.join(_state_dir(), ".app-alive"))) < 12
    except OSError:
        return False


def _queue(title: str, msg: str, sound: str) -> None:
    import json
    sd = _state_dir()
    try:
        os.makedirs(sd, exist_ok=True)
        with open(os.path.join(sd, "notifications.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": int(time.time() * 1000), "title": title,
                                 "msg": msg, "sound": sound}, ensure_ascii=False) + "\n")
    except OSError:
        pass


def notify(title: str, msg: str, sound: str = "Glass") -> None:
    if sys.platform == "darwin":
        # ALWAYS queue for the menu-bar app to post NATIVELY (UNUserNotifications): branded "노냐?"
        # icon, and a click opens the briefing. We must NEVER `osascript display notification` —
        # macOS attributes those to "Script Editor" (wrong icon, and clicking them LAUNCHES Script
        # Editor). When the app isn't running the queued line is simply skipped on its next launch
        # (no banner, no Script Editor) — a clean degrade. The app itself plays the sound on post.
        _queue(title, msg, sound)
        if not _app_alive():
            _play_sound(sound)               # no app to post the banner -> at least chime (no osascript)
    elif sys.platform.startswith("win"):
        _notify_windows(title, msg)
    else:
        sys.stderr.write("\a")
    _push(title, msg)
    log("NOTIFY[%s] %s" % (title, msg))


def escalate(title: str, msg: str) -> None:
    """High-priority remote alert for the give-up / blocker case — fans out to the
    phone (ntfy + Telegram, with secret redaction) via nonya.remote, plus Slack.
    Also does a local notify. Best-effort; never raises into the loop."""
    notify(title, msg, "Basso")
    chans = []
    try:
        from . import remote
        if remote.push(title, msg):
            chans.append("phone")
    except Exception:
        pass
    if _slack(title, msg):
        chans.append("slack")
    if chans:
        log("ESCALATED -> %s" % ", ".join(chans))
