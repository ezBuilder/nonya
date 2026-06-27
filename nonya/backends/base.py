"""Backend interface + a NullBackend for unsupported platforms.

A backend owns the OS-specific half: window gate (can we be sure which window?),
OCR confirm (is it actively generating / showing an error?), and injection
(clipboard-paste the nudge into that window). The core loop is OS-agnostic.
"""
from __future__ import annotations

import shutil


class Backend:
    name = "base"

    # --- permission / preflight ---
    def have_accessibility(self) -> bool:
        """Can we synthesize input at all? (mac: AX trust; win: ~always, but UIPI
        may still silently drop into higher-integrity windows.)"""
        return False

    def check(self) -> None:
        """Print a human-readable preflight report for `nonya --check`."""
        print("nonya preflight (%s)" % self.name)
        print("-" * 40)
        print("[ - ] no backend capabilities on this platform")
        print("-" * 40)

    # --- window safety gate ---
    def window_gate(self, proc: str) -> str:
        """Return "ok" only when a single, certain target window exists.
        Otherwise a reason string (caller alerts instead of injecting)."""
        return "unsupported-platform"

    # --- on-screen confirmation (advisory) ---
    def confirm_state(self, proc: str) -> str:
        """One of: error | busy | inconclusive. 'busy' vetoes an injection."""
        return "inconclusive"

    # --- injection (precondition: window_gate == "ok") ---
    # allow_raise: when the user is away (unattended), the backend MAY raise the app to front before
    # typing; when False it must stay focus-safe (front-only). Backends that can't raise ignore it.
    def inject(self, proc: str, text: str, send_key: str = "return", allow_raise: bool = False) -> bool:
        return False

    def frontmost_terminal(self) -> str:
        """Process name of the frontmost terminal emulator (non-tmux CLI paste path), or ''."""
        return ""

    def running_terminal(self) -> str:
        """Name of a running terminal emulator (for AX split injection), or ''."""
        return ""

    def inject_terminal_split(self, match: str, text: str, send_key: str = "return") -> bool:
        """Inject into a native terminal split identified by on-screen content. Default: unsupported."""
        return False

    # --- user presence (for "normal" mode: don't nudge while the user is at the keyboard) ---
    def user_idle_seconds(self) -> float:
        """Seconds since the last human input (mouse/keyboard). -1 = unknown
        (caller then skips the user-idle gate)."""
        return -1.0

    @staticmethod
    def _tool(name: str) -> bool:
        return shutil.which(name) is not None


class NullBackend(Backend):
    name = "unsupported"

    def check(self) -> None:
        print("nonya preflight (unsupported platform)")
        print("-" * 40)
        print("[!! ] in-window injection is implemented for macOS and Windows only.")
        print("       Detection/notification still work; targeting is alert-only.")
        print("-" * 40)

    def window_gate(self, proc: str) -> str:
        return "unsupported-platform"
