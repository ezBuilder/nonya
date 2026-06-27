"""Platform backend selection. Each backend implements window-gate / OCR confirm /
injection for one OS. Only the current platform's backend is imported, so
OS-specific modules (ctypes.windll, osascript) never load on the wrong OS."""
from __future__ import annotations

import sys

from .base import Backend


def get_backend() -> Backend:
    if sys.platform == "darwin":
        from .macos import MacBackend
        return MacBackend()
    if sys.platform.startswith("win"):
        from .windows import WindowsBackend
        return WindowsBackend()
    from .base import NullBackend
    return NullBackend()
