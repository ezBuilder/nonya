"""nonya — "놀고 있냐?" cross-platform (macOS + Windows) session auto-recovery observer.

Watches a live Claude / Codex / Antigravity(Gemini) session and, when it stalls
(error / overload / hang / crash), re-sends a nudge into the SAME window so the
conversation continues in place. Subscription billing, no headless resume.

Architecture (see docs/ARCH-cross-platform.md):
  - core (this package, OS-shared): detect + classify + policy + loop + notify
  - backends/<os>: window-gate, OCR confirm, key/paste injection (OS-specific)

Safety invariant: never send keys unless the target window is certain and the
platform's permission/integrity gate is satisfied; otherwise alert only.
"""

__version__ = "0.2.5"
