"""User settings shared by the menu-bar app and the core via <state_dir>/config.json.

The Swift Settings panel writes this file; the loop re-reads it every poll so a
toggle (sound off, mode, preview seconds, channel tokens) applies IMMEDIATELY
without a restart. Pure stdlib, atomic write, 0600 (tokens are the user's own and
live only under ~/.local/state — never in the repo, never logged).
"""
from __future__ import annotations

import json
import os

FILENAME = "config.json"

# "" / 0 means "don't override" (fall back to the CLI/default). Channel fields map
# to the env vars notify.py / remote.py already read, so a saved token just works.
DEFAULTS = {
    "sound": True,          # play a sound chime on a nudge (False = silent; no TTS voice)
    "preview_secs": 0,      # 0 = inject immediately; >0 = cancellable preview countdown
    "mode": "",             # "" keep CLI choice; "on-error" | "auto"
    "idle": 0,              # 0 keep default; else idle-gate seconds
    "character": "",        # "" default; "duck" | "cat" | "robot"
    "lang": "",             # "" OS locale; else a NONYA_LANG code
    "slack_webhook": "",
    "telegram_token": "",
    "telegram_chat": "",
    "ntfy_topic": "",
}

# config key -> environment variable consumed elsewhere (notify/remote/i18n).
_ENV = {
    "slack_webhook": "NONYA_SLACK_WEBHOOK",
    "telegram_token": "NONYA_TELEGRAM_TOKEN",
    "telegram_chat": "NONYA_TELEGRAM_CHAT",
    "ntfy_topic": "NONYA_NTFY_TOPIC",
    "lang": "NONYA_LANG",
}


def path(state_dir: str) -> str:
    return os.path.join(state_dir, FILENAME)


def load(state_dir: str) -> dict:
    """Return settings (defaults merged with config.json). Never raises."""
    out = dict(DEFAULTS)
    try:
        with open(path(state_dir), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return out
    if isinstance(data, dict):
        for k in DEFAULTS:
            if k in data and data[k] is not None:
                out[k] = data[k]
    return out


def save(state_dir: str, settings: dict) -> dict:
    """Atomically write the settings (only known keys), 0600. Returns what was written."""
    os.makedirs(state_dir, exist_ok=True)
    rec = {k: settings.get(k, DEFAULTS[k]) for k in DEFAULTS}
    p = path(state_dir)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(rec, fh, ensure_ascii=False)
    os.replace(tmp, p)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    return rec


def apply_env(settings: dict) -> None:
    """Push channel tokens + language into the env vars notify/remote/i18n read,
    so a saved token takes effect without threading it through every call site."""
    for key, env in _ENV.items():
        val = str(settings.get(key, "") or "").strip()
        if val:
            os.environ[env] = val
