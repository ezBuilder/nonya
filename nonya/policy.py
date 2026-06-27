"""Run configuration + the mode-gated decision of whether a STATE warrants action."""
from __future__ import annotations

from dataclasses import dataclass, field

from . import state

DEFAULT_NUDGE = ("계속 이어서 진행해. 막혔으면 다시 시도해. 전부 끝났고 검증까지 됐으면 "
                 "<<DONE>> 한 줄만 출력해.")


@dataclass
class Config:
    target: str = ""          # claude | codex | antigravity | cli
    app: str = ""             # process / window-title hint (defaults per target)
    engine: str = ""          # claude | codex | antigravity (detection format)
    mode: str = "on-error"    # on-error | auto
    is_app: bool = True       # app window (gated) vs cli terminal/tmux

    nudge: str = DEFAULT_NUDGE
    nudge_rotate: bool = False  # cli turns this ON (when no explicit --nudge) to rotate the playful
                                # 노냐 pool (자냐?/졸아?…); default off so library callers/tests use `nudge`
    sentinel: str = "<<DONE>>"
    send_key: str = "return"  # return | cmd+return (mac) / ctrl+return (win)
    tmux_target: str = ""     # when set, inject via `tmux send-keys -t <target>`

    idle: int = 180
    grace: int = 120
    poll: int = 15
    require_user_idle: int = 0   # normal mode: only inject after the USER is idle this long (mouse/kbd); 0=off
    hang_cap: int = 1800
    max_nudges: int = 100
    max_hours: int = 12
    stuck_after: int = 3            # consecutive no-progress nudges before escalating
    give_up_after: int = 9         # consecutive no-progress nudges before stopping (no more keys)
    escalate_cooldown: int = 600   # min seconds between remote (telegram/slack) escalations
    max_iterations: int = 0   # 0 = infinite; bounds poll cycles (testing)
    dry_run: bool = False
    no_inject: bool = False    # detect + signal only; an external injector types
    relaunch: bool = False     # opt-in: when a session's PROCESS is dead/hung, kill→relaunch→resume
                               # the EXACT session (claude --resume / codex resume / tmux respawn-pane),
                               # not just nudge a live one. Off by default (gated like --shadow).
    shadow: bool = False       # decide + RECORD what we WOULD do (to the ledger) but send ZERO keys
                               # -> run for a while, then `nonya --metrics` to vet false-positives before trusting auto

    # --- Correctness Supervisor ---
    verify: bool = True        # run the project's own check before accepting a "done" claim
    project_dir: str = ""      # where to discover/run the check (default: transcript-derived / cwd)
    check_cmd: str = ""        # explicit check command override (else auto-discover)
    model_cmd: str = ""        # optional local model cmd for corrective text (else deterministic)

    persona: bool = True      # show the watcher character + scold lines
    character: str = ""       # duck | cat | robot (default duck)
    impact: bool = True       # play a sound chime on each nudge (no TTS voice)

    state_dir: str = ""
    transcript: str = ""

    def actionable(self, st: str, idle: int) -> bool:
        """Does STATE warrant a nudge under the current mode?"""
        if st in (state.ERROR, state.RATE_LIMIT, state.STALLED):
            return True
        if st == state.TOOL_PENDING:
            return idle > self.hang_cap
        if st in (state.COMPLETED, state.IDLE_WAIT):
            return self.mode == "auto"
        return False
