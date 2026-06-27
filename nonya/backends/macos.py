"""macOS backend — osascript (AX/System Events) + screencapture + tesseract.

Ports the v1 bash lib (perms.sh / target.sh / confirm.sh / inject.sh).
Unicode-safe injection = clipboard backup -> set nudge -> raise single window
-> Cmd+V -> send key -> restore clipboard (keyboardSetUnicodeString is unreliable
on macOS 12+, so we paste; see docs/RESEARCH-auto-inject-2026-06-19.md §2-4).
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile

from .base import Backend

ERR_PAT = re.compile(os.environ.get(
    "NONYA_ERR_PAT",
    r"사용 중|busy|overloaded|rate limit|rate_limit|try again|다시 시도|다른 모델|오류|error"), re.I)
BUSY_PAT = re.compile(os.environ.get(
    "NONYA_BUSY_PAT",
    r"esc to interrupt|중지|중단|Generating|Thinking|생성 중|Stop generating"), re.I)


def _osa(script: str, timeout: int = 12) -> str:
    try:
        r = subprocess.run(["osascript", "-e", script], capture_output=True,
                           text=True, timeout=timeout)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _osa_multi(script: str, timeout: int = 12) -> bool:
    try:
        r = subprocess.run(["osascript", "-"], input=script, capture_output=True,
                           text=True, timeout=timeout)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


class MacBackend(Backend):
    name = "macos"

    _AX_VALUE_APPS = {"Claude"}

    # terminal emulators whose CLI can be reached by focusing the window + pasting
    _TERMINALS = ("ghostty", "iterm", "iterm2", "terminal", "warp", "wezterm",
                  "alacritty", "kitty", "hyper", "tabby", "rio")

    def frontmost_terminal(self) -> str:
        """If the frontmost app is a known terminal emulator, return its process
        name (for the focus+paste CLI path); else ''."""
        out = _osa('tell application "System Events" to get name of first '
                   'application process whose frontmost is true')
        name = out.strip()
        low = name.lower()
        return name if any(t in low for t in self._TERMINALS) else ""

    def running_terminals(self) -> list:
        """ALL running terminal emulators (need not be frontmost), in System Events order —
        for AX split injection. Multiple may run at once (e.g. Ghostty + iTerm); the target
        split may live in any of them, so we must try each, not just the first."""
        out = _osa('tell application "System Events" to get name of '
                   '(processes whose background only is false)')
        return [name for name in (n.strip() for n in out.split(","))
                if any(t in name.lower() for t in self._TERMINALS)]

    def running_terminal(self) -> str:
        """First running terminal emulator name, or '' (kept for callers wanting a single hint)."""
        terms = self.running_terminals()
        return terms[0] if terms else ""

    def inject_terminal_split(self, match: str, text: str, send_key: str = "return") -> bool:
        """DISABLED BY DEFAULT — unsafe. The AX helper finds the split by content and posts keys
        via CGEvent.postToPid, but macOS routes posted key events to the app's KEY (active) split,
        NOT to the AXFocused one we selected. Verified live (2026-06-20): when the terminal was
        backgrounded the keys delivered NOTHING; when it was frontmost they landed in the ACTIVE
        split — which can be a DIFFERENT session than the stalled one we targeted (a real misfire:
        a test marker landed in an unrelated live Claude session). So native-split AX injection is
        neither reliable nor safe and must not auto-recover. Safe injection paths: tmux (targets a
        pane by id — deterministic) and a frontmost single-window GUI app. For agents in a raw
        Ghostty/Terminal split, run them inside tmux for safe recovery; otherwise nonya ALERTS.
        Opt back in for research only with NONYA_AX_SPLIT=1 (accepts the misfire risk)."""
        if os.environ.get("NONYA_AX_SPLIT") != "1":
            return False
        helper = os.environ.get("NONYA_AX_HELPER", "")
        terms = self.running_terminals()
        if not (helper and terms and match) or not os.path.exists(helper):
            return False
        for term in terms:                           # rc 0 = injected; rc 3 = no/ambiguous match here, try next
            try:
                r = subprocess.run([helper, "--ax-inject", term, match, text, send_key],
                                   capture_output=True, timeout=25)
                if r.returncode == 0:
                    return True
            except (OSError, subprocess.SubprocessError):
                continue
        return False

    def have_accessibility(self) -> bool:
        # Benign read of UI elements requires AX trust.
        try:
            r = subprocess.run(
                ["osascript", "-e",
                 'tell application "System Events" to get count of UI elements of '
                 '(first application process whose frontmost is true)'],
                capture_output=True, text=True, timeout=8)
            return r.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def _window_count(self, proc: str) -> int:
        out = _osa(
            'tell application "System Events" to if exists process "%s" then '
            'return count of windows of process "%s"\nreturn -1' % (proc, proc))
        try:
            return int(re.sub(r"\s", "", out))
        except ValueError:
            return 0

    def window_gate(self, proc: str) -> str:
        if not self.have_accessibility():
            return "no-accessibility"
        n = self._window_count(proc)
        if n == 1:
            return "ok"
        if n == -1:
            return "not-running"
        if n == 0:
            return "no-ax-window"          # app exposes no AX window (some Electron/Chromium builds)
        return "multi-window:%d" % n        # cannot map window->session

    def _capture_window(self, proc: str, out_png: str) -> bool:
        if not (self._tool("screencapture") and self._tool("tesseract")):
            return False
        geo = _osa(
            'tell application "System Events"\n'
            ' if not (exists process "%s") then return ""\n'
            ' try\n'
            '  set w to window 1 of process "%s"\n'
            '  set p to position of w\n  set s to size of w\n'
            '  return ((item 1 of p) as string) & "," & ((item 2 of p) as string) & "," '
            '& ((item 1 of s) as string) & "," & ((item 2 of s) as string)\n'
            ' on error\n  return ""\n end try\n'
            'end tell' % (proc, proc))
        if not geo:
            return False
        try:
            subprocess.run(["screencapture", "-x", "-R" + geo, out_png],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        except (OSError, subprocess.SubprocessError):
            return False
        return os.path.exists(out_png) and os.path.getsize(out_png) > 0

    def confirm_state(self, proc: str) -> str:
        fd, png = tempfile.mkstemp(prefix="nonya_cap.", suffix=".png")
        os.close(fd)
        try:
            if not self._capture_window(proc, png):
                return "inconclusive"
            try:
                r = subprocess.run(["tesseract", png, "stdout"], capture_output=True,
                                   text=True, timeout=20)
                txt = r.stdout
            except (OSError, subprocess.SubprocessError):
                return "inconclusive"
        finally:
            try:
                os.remove(png)
            except OSError:
                pass
        if BUSY_PAT.search(txt):
            return "busy"
        if ERR_PAT.search(txt):
            return "error"
        return "inconclusive"

    def inject(self, proc: str, text: str, send_key: str = "return", allow_raise: bool = False) -> bool:
        # Belt: re-confirm the gate immediately before touching the keyboard
        # (the window count may have changed since the loop checked it).
        if self.window_gate(proc) != "ok":
            return False
        if send_key == "cmd+return":
            sendline = "key code 36 using command down"
        elif send_key == "ctrl+return":
            sendline = "key code 36 using control down"
        else:
            # Some Electron/WebKit agent composers accept Return as plain newline
            # depending on focus/IME/editor mode. Paste first, then try Return and
            # Command-Return so the message is actually submitted instead of left
            # sitting in the composer. Empty second submits are ignored by agent UIs.
            sendline = "key code 36\n delay 0.45\n key code 36 using command down"
        esc = text.replace("\\", "\\\\").replace('"', '\\"')
        # Everything via System Events (Accessibility) — only Accessibility, never per-app Automation.
        # Single-window gate always applies (can't map multi-window -> session).
        # FOCUS policy:
        #   allow_raise=False (user PRESENT): paste ONLY if the app is ALREADY frontmost — never
        #     raise (would Space/fullscreen-switch and disrupt the user). Misfire-proof, focus-safe.
        #   allow_raise=True  (UNATTENDED — user idle/away): RAISE the app to front, then type. This
        #     is nonya's whole purpose: overnight recovery with nobody at the keyboard. The keystroke
        #     lands in the app's focused conversation/input. (Caller gates this on user-idle.)
        if allow_raise:
            # RAISE and RETRY until the app is actually frontmost (don't give up after one try), with
            # set-frontmost + AXRaise (handles other-Space/fullscreen). CRITICAL: if it never comes
            # front, ABORT with ZERO keys — otherwise the paste lands in whatever IS front (the
            # terminal you launched from). Type only AFTER focus is confirmed.
            front_clause = (
                ' set _ok to false\n'
                ' repeat 15 times\n'
                '  tell process "%s"\n'
                '   set frontmost to true\n'
                '   try\n    perform action "AXRaise" of window 1\n   end try\n'
                '  end tell\n'
                '  delay 0.2\n'
                '  if (frontmost of process "%s") then\n'   # boolean — robust to localized app names
                '   set _ok to true\n   exit repeat\n'
                '  end if\n'
                ' end repeat\n'
                ' if not _ok then return "ABORT-focus"\n'
                ' delay 0.15\n' % (proc, proc))
        else:
            front_clause = (' set fp to name of first application process whose frontmost is true\n'
                            ' if fp is not "%s" then return "ABORT-notfront:" & fp\n' % proc)
        if proc in self._AX_VALUE_APPS:
            ax_script = (
                'tell application "System Events"\n'
                ' if not (exists process "%s") then return "ABORT-noproc"\n'
                ' tell process "%s"\n'
                '  if (count of windows) is not 1 then return "ABORT-windows"\n'
                ' end tell\n'
                '%s'
                ' tell process "%s"\n'
                '  try\n'
                '   set ui to value of attribute "AXFocusedUIElement"\n'
                '   set value of attribute "AXValue" of ui to "%s"\n'
                '   delay 0.15\n'
                '   if (value of attribute "AXValue" of ui) is not "%s" then return "ABORT-axvalue-mismatch"\n'
                '  on error errm\n'
                '   return "ABORT-axvalue:" & errm\n'
                '  end try\n'
                ' end tell\n'
                ' %s\n'
                'end tell\n'
                'delay 0.4\n'
                'return "OK-axvalue"\n' % (proc, proc, front_clause, proc, esc, esc, sendline))
            out = _osa(ax_script)
            if out.startswith("OK"):
                return True
            from ..notify import log
            log("inject ABORT (no keys sent): %s" % (out or "osascript-error"))
            return False
        script = (
            'set prevClip to ""\n'
            'try\n set prevClip to (the clipboard as text)\nend try\n'
            'tell application "System Events"\n'
            ' if not (exists process "%s") then return "ABORT-noproc"\n'
            ' tell process "%s"\n'
            '  if (count of windows) is not 1 then return "ABORT-windows"\n'
            ' end tell\n'
            '%s'
            ' set the clipboard to "%s"\n'
            ' delay 0.2\n'
            ' key code 9 using command down\n'  # Cmd+V — key code paste; `keystroke "v"` is unreliable
            ' delay 0.3\n'
            ' %s\n'
            'end tell\n'
            'delay 0.4\n'
            'try\n set the clipboard to prevClip\nend try\n'
            'return "OK"\n' % (proc, proc, front_clause, esc, sendline))
        out = _osa(script)
        if not out.startswith("OK"):
            from ..notify import log
            log("inject ABORT (no keys sent): %s" % (out or "osascript-error"))
            return False
        return True

    def user_idle_seconds(self) -> float:
        # HIDIdleTime (nanoseconds since last HID input) via ioreg — stdlib, no deps.
        try:
            r = subprocess.run(["ioreg", "-c", "IOHIDSystem"],
                               capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            return -1.0
        m = re.search(r'"HIDIdleTime"\s*=\s*(\d+)', r.stdout)
        if not m:
            return -1.0
        return int(m.group(1)) / 1_000_000_000.0

    def check(self) -> None:
        print("nonya preflight (macOS)")
        print("-" * 40)
        if self.have_accessibility():
            print("[OK ] Accessibility (keystroke injection allowed)")
        else:
            print("[!! ] Accessibility MISSING -> alert-only. Grant: System Settings >")
            print("       Privacy & Security > Accessibility > add your terminal/launchd.")
        print("[%s] screencapture (Screen Recording grant needed for window pixels)"
              % ("OK " if self._tool("screencapture") else "!! "))
        print("[%s] tesseract OCR (optional; Apple Vision via mac-ocr recommended)"
              % ("OK " if self._tool("tesseract") else " - "))
        print("[%s] osascript" % ("OK " if self._tool("osascript") else "!! "))
        print("-" * 40)
