"""Windows backend — ctypes against user32/kernel32 (no pywin32 dependency).

Window gate: EnumWindows + GetWindowThreadProcessId / GetWindowText, matching by
visible top-level title (titles are all "Claude"/"Codex"/"Antigravity", so a
single match = safe; multiple = alert-only). Injection: clipboard backup ->
SetClipboardData(CF_UNICODETEXT) -> SetForegroundWindow -> SendInput Ctrl+V +
Enter -> restore clipboard.

CAVEATS (see docs/RESEARCH-windows-auto-inject-2026-06-19.md):
  - UIPI: SendInput silently drops into higher-integrity windows. If an agent
    runs elevated and nonya does not, injection fails with NO error signal —
    we detect failure only via the post-inject mtime check in the core loop.
  - OCR confirm is not implemented (Windows.Media.Ocr needs WinRT); returns
    inconclusive, so the window gate is the primary safety.
  - Implemented against Win32 docs; not yet exercised on a Windows host.
"""
from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes

from .base import Backend

# Struct definitions are platform-neutral (ctypes works everywhere); only the
# windll calls below require Windows and are reached at runtime only.
ULONG_PTR = wintypes.WPARAM

KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
INPUT_KEYBOARD = 1
VK_CONTROL = 0x11
VK_RETURN = 0x0D
VK_V = 0x56
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ULONG_PTR)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


def _key_event(vk: int, flags: int = 0, scan: int = 0) -> _INPUT:
    return _INPUT(type=INPUT_KEYBOARD,
                  u=_INPUTUNION(ki=_KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags,
                                               time=0, dwExtraInfo=0)))


class WindowsBackend(Backend):
    name = "windows"

    def __init__(self):
        if sys.platform.startswith("win"):
            self._u = ctypes.WinDLL("user32", use_last_error=True)
            self._k = ctypes.WinDLL("kernel32", use_last_error=True)
        else:
            self._u = self._k = None

    # --- window enumeration ---
    def _find_windows(self, title_substr: str):
        if not self._u:
            return []
        hwnds = []
        substr = title_substr.lower()
        EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def _cb(hwnd, _lparam):
            if not self._u.IsWindowVisible(hwnd):
                return True
            length = self._u.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            self._u.GetWindowTextW(hwnd, buf, length + 1)
            if substr in buf.value.lower():
                hwnds.append(hwnd)
            return True

        self._u.EnumWindows(EnumProc(_cb), 0)
        return hwnds

    def have_accessibility(self) -> bool:
        # Windows has no per-app accessibility consent. Input injection is always
        # *attemptable*; UIPI may still drop it silently (see module docstring).
        return self._u is not None

    def window_gate(self, proc: str) -> str:
        if not self._u:
            return "unsupported-platform"
        wins = self._find_windows(proc)
        n = len(wins)
        if n == 1:
            return "ok"
        if n == 0:
            return "not-running"
        return "multi-window:%d" % n

    def confirm_state(self, proc: str) -> str:
        # TODO: Windows.Media.Ocr via WinRT. For now never veto.
        return "inconclusive"

    # --- clipboard ---
    def _get_clipboard(self) -> str:
        u, k = self._u, self._k
        if not u.OpenClipboard(0):
            return ""
        try:
            h = u.GetClipboardData(CF_UNICODETEXT)
            if not h:
                return ""
            p = k.GlobalLock(h)
            if not p:
                return ""
            try:
                return ctypes.c_wchar_p(p).value or ""
            finally:
                k.GlobalUnlock(h)
        finally:
            u.CloseClipboard()

    def _set_clipboard(self, text: str) -> bool:
        u, k = self._u, self._k
        data = text.encode("utf-16-le") + b"\x00\x00"
        if not u.OpenClipboard(0):
            return False
        try:
            u.EmptyClipboard()
            h = k.GlobalAlloc(GMEM_MOVEABLE, len(data))
            if not h:
                return False
            p = k.GlobalLock(h)
            ctypes.memmove(p, data, len(data))
            k.GlobalUnlock(h)
            if not u.SetClipboardData(CF_UNICODETEXT, h):
                return False
            return True
        finally:
            u.CloseClipboard()

    def _send(self, inputs):
        arr = (_INPUT * len(inputs))(*inputs)
        self._u.SendInput(len(inputs), ctypes.byref(arr), ctypes.sizeof(_INPUT))

    def inject(self, proc: str, text: str, send_key: str = "return", allow_raise: bool = False) -> bool:
        # allow_raise accepted for API parity; Win32 SetForegroundWindow handling lives in _focus.
        if not self._u:
            return False
        wins = self._find_windows(proc)
        if len(wins) != 1:
            return False
        hwnd = wins[0]
        prev = self._get_clipboard()
        if not self._set_clipboard(text):
            return False
        self._u.SetForegroundWindow(hwnd)
        time.sleep(0.25)
        # Ctrl+V
        self._send([_key_event(VK_CONTROL), _key_event(VK_V),
                    _key_event(VK_V, KEYEVENTF_KEYUP),
                    _key_event(VK_CONTROL, KEYEVENTF_KEYUP)])
        time.sleep(0.25)
        if send_key in ("return", "cmd+return", "ctrl+return"):
            if send_key == "ctrl+return":
                self._send([_key_event(VK_CONTROL), _key_event(VK_RETURN),
                            _key_event(VK_RETURN, KEYEVENTF_KEYUP),
                            _key_event(VK_CONTROL, KEYEVENTF_KEYUP)])
            else:
                self._send([_key_event(VK_RETURN),
                            _key_event(VK_RETURN, KEYEVENTF_KEYUP)])
        time.sleep(0.3)
        if prev:
            self._set_clipboard(prev)
        return True

    def check(self) -> None:
        print("nonya preflight (Windows)")
        print("-" * 40)
        if not self._u:
            print("[!! ] not running on Windows")
        else:
            print("[OK ] user32/kernel32 reachable (SendInput available)")
            print("[i  ] UIPI: injection into elevated (High IL) agent windows needs")
            print("       UIAccess (signed + %ProgramFiles% install). Else alert-only.")
            print("[ - ] OCR confirm not implemented (Windows.Media.Ocr TODO)")
        print("-" * 40)
