"""persona — nonya's face. A cute watcher character that scolds an idle session
back to work with impact (terminal art + a short sound chime).

This is the USER-facing flair only. The text actually injected into the agent
stays the professional nudge (i18n nudge.default) — we never feed "work!" to the
model. Scolding = what the user sees; nudge = what the agent gets.

All user-facing strings are localized via i18n (NONYA_LANG / OS locale), so the
persona speaks the user's language instead of hard-coded Korean. (No TTS voice —
it sounded tacky; the nudge impact is a single chime, not spoken aloud.)
"""
from __future__ import annotations

import subprocess
import sys

from .i18n import t

# --- character roster (language-neutral ASCII faces; name + lines come from i18n) ---
CHARACTERS = {
    "duck": {"key": "duck", "art": r"""
   _
 <(o )___
  (  ._> /
   `---'  """},
    "cat": {"key": "cat", "art": r"""
  /\_/\
 ( o.o )
  > ^ <  """},
    "robot": {"key": "robot", "art": r"""
  [o_o]
 /|___|\
   | |   """},
}
DEFAULT = "duck"


def pick(name: str = "") -> dict:
    return CHARACTERS.get(name or DEFAULT, CHARACTERS[DEFAULT])


def _name(c: dict) -> str:
    return t("persona.char." + c["key"])


def banner(name: str = "") -> str:
    c = pick(name)
    return "%s %s%s" % (_name(c), t("persona.onduty"), c["art"])


def scold(nudges: int, name: str = "") -> str:
    c = pick(name)
    level = min(max(nudges, 1), 4)
    return "%s: %s" % (_name(c), t("persona.scold.%d" % level))


def impact(text: str, name: str = "", enabled: bool = True) -> None:
    """The 'im-pact': play a short sound chime on a nudge. Best-effort, non-blocking.
    No TTS voice — speaking the scold aloud sounded tacky; the chime is the whole alert."""
    if not enabled:
        return
    if sys.platform == "darwin":
        try:
            subprocess.Popen(["afplay", "/System/Library/Sounds/Funk.aiff"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass
    elif sys.platform.startswith("win"):
        # A single system beep (no spoken voice). Best-effort.
        ps = "[console]::beep(880,200)"
        try:
            subprocess.Popen(["powershell", "-NoProfile", "-Command", ps],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass
