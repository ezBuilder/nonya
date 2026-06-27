"""tmux injector — the most reliable, focus-independent CLI path on both OSes
(macOS native, Windows via WSL). Targets a specific pane by id, so multi-pane
is safe (no window mapping needed). `tmux send-keys -l` sends literal text
(Unicode-safe), then a separate Enter.

Used when Config.tmux_target is set (e.g. "%3" or "session:win.pane").
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time


def _cmd_matches(cmd: str, eng: str) -> bool:
    """Does this process command belong to the given engine's CLI? Matches the
    executable leaf (not a loose substring of the whole argv/path), and never
    matches nonya's own supervisor process. Claude Code presents as `node`
    running a `claude` script, so for claude we require the name in the argv."""
    low = (cmd or "").lower()
    if "nonya" in low:
        return False
    toks = low.split()
    base = os.path.basename(toks[0]) if toks else low
    if base == eng:
        return True
    if eng == "claude":
        return "claude" in low          # 'node .../claude/cli.js' -> yes; a bare unrelated 'node' -> no
    if eng == "codex":
        return base.startswith("codex")
    return eng in base


def available() -> bool:
    return shutil.which("tmux") is not None


def pane_exists(target: str) -> bool:
    if not available():
        return False
    try:
        r = subprocess.run(["tmux", "list-panes", "-a", "-F", "#{session_name}:#{window_index}.#{pane_index} #{pane_id}"],
                           capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False
    if r.returncode != 0:
        return False
    return any(target in line.split() for line in r.stdout.splitlines())


def _pane_in_mode(target: str) -> bool:
    """True if the pane is in copy-mode/pager/view-mode — keys would scroll, not submit."""
    try:
        r = subprocess.run(["tmux", "display-message", "-p", "-t", target, "#{pane_in_mode}"],
                           capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0 and r.stdout.strip() == "1"


def gate(target: str) -> str:
    if not available():
        return "no-tmux"
    if not pane_exists(target):
        return "pane-not-found"
    if _pane_in_mode(target):
        return "pane-busy"          # copy-mode/pager — a nudge would misroute; alert only
    return "ok"


def _proc_table() -> dict:
    """pid -> (ppid, command) snapshot, for walking a pane's process subtree."""
    try:
        r = subprocess.run(["ps", "-ax", "-o", "pid=,ppid=,command="],
                           capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return {}
    table = {}
    for line in r.stdout.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 3:
            continue
        try:
            table[int(parts[0])] = (int(parts[1]), parts[2])
        except ValueError:
            pass
    return table


def _subtree_has(root_pid: int, needle: str, table: dict) -> bool:
    children: dict = {}
    for pid, (ppid, _cmd) in table.items():
        children.setdefault(ppid, []).append(pid)
    stack, seen = [root_pid], set()
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        info = table.get(pid)
        if info and _cmd_matches(info[1], needle):
            return True
        stack.extend(children.get(pid, []))
    return False


def find_pane(engine: str):
    """Best-effort: locate the tmux pane running the given engine's CLI.
    Matches the engine name in the pane's foreground command or anywhere in its
    process subtree (Claude Code often shows as 'node'; Codex as 'codex')."""
    if not available():
        return None
    try:
        # SPACE-delimited + split(None) (a literal '\t' in -F does not survive the frozen binary).
        # command is LAST because it's the only field that could contain whitespace; pane_id and
        # pid are whitespace-free, so split(None, 2) cleanly yields [pane_id, pid, command].
        r = subprocess.run(
            ["tmux", "list-panes", "-a", "-F",
             "#{pane_id} #{pane_pid} #{pane_current_command}"],
            capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    eng = engine.lower()
    table = _proc_table()
    direct, subtree = [], []        # prefer an exact foreground-command match over a loose subtree hit
    for line in r.stdout.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 2:
            continue
        pane_id = parts[0]
        cmd = parts[2].lower() if len(parts) > 2 else ""
        if _cmd_matches(cmd, eng):
            direct.append(pane_id)
            continue
        try:
            pid = int(parts[1])
        except ValueError:
            continue
        if _subtree_has(pid, eng, table):
            subtree.append(pane_id)
    cands = direct or subtree
    return cands[0] if len(cands) == 1 else None    # refuse to guess when 0 or >1 match (no misfire)


def pane_for_cwd(cwd: str):
    """Pane id of the UNIQUE pane whose current path is `cwd` — used to target a
    specific background session's pane (matched by its working dir) for overnight
    recovery. Returns None if zero or >1 match (no guessing -> no misfire)."""
    if not cwd or not available():
        return None
    try:
        # SPACE-delimited + split(None): a literal '\t' in the -F format does NOT survive the
        # PyInstaller-frozen binary (it reached tmux mangled, so split('\t') found nothing and
        # pane targeting silently failed in the SHIPPED app — recovery never fired). pane_id is
        # '%<digits>' (no whitespace), so the first token is always the full pane id; the path
        # (which may contain spaces) is the remainder.
        r = subprocess.run(["tmux", "list-panes", "-a", "-F", "#{pane_id} #{pane_current_path}"],
                           capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    cwd = os.path.realpath(cwd)
    hits = []
    for ln in r.stdout.splitlines():
        parts = ln.split(None, 1)
        if len(parts) == 2 and os.path.realpath(parts[1]) == cwd:
            hits.append(parts[0])
    return hits[0] if len(hits) == 1 else None


def pane_dead(target: str) -> bool:
    """True if the pane's command has EXITED (the agent process died) and the pane is held
    open by remain-on-exit — #{pane_dead}==1. A dead pane must be respawned (FR-422), not
    sent keys. A live shell (pane_dead==0) whose agent died is recovered by typing the
    resume command into that shell instead."""
    if not available():
        return False
    try:
        r = subprocess.run(["tmux", "display-message", "-p", "-t", target, "#{pane_dead}"],
                           capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0 and r.stdout.strip() == "1"


def engine_alive_in(target: str, engine: str) -> bool:
    """Is `engine`'s CLI actually RUNNING in pane `target` (foreground command or anywhere
    in its process subtree)? Used to tell 'session stalled but process alive' (nudge) from
    'process dead' (relaunch+resume). Distinct from find_pane (which scans ALL panes): this
    answers about ONE specific pane the session was matched to (by cwd)."""
    if not available():
        return False
    try:
        r = subprocess.run(["tmux", "display-message", "-p", "-t", target,
                            "#{pane_pid} #{pane_current_command}"],
                           capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False
    if r.returncode != 0:
        return False
    parts = r.stdout.split(None, 1)
    if not parts:
        return False
    cmd = parts[1].lower() if len(parts) > 1 else ""
    if _cmd_matches(cmd, engine.lower()):
        return True
    try:
        pid = int(parts[0])
    except ValueError:
        return False
    return _subtree_has(pid, engine.lower(), _proc_table())


def respawn_pane(target: str, argv, cwd: str = "") -> bool:
    """FR-422: KILL the pane's current process tree and start `argv` in the SAME pane —
    one atomic `tmux respawn-pane -k`, which preserves the pane id and (with -c) pins the
    original cwd. This satisfies 'kill target tree' + 'relaunch in original cwd' at once for
    a dead/held pane. argv goes after '--' so it is never parsed as tmux options. Scoped to
    ONE pane id, so it can never touch another session (AC-013)."""
    if not available() or not argv:
        return False
    cmd = ["tmux", "respawn-pane", "-k", "-t", target]
    if cwd:
        cmd += ["-c", cwd]
    cmd += ["--", *[str(a) for a in argv]]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def inject(target: str, text: str, send_key: str = "return") -> bool:
    if not available():
        return False
    try:
        # literal text (no key-name interpretation), then a discrete Enter.
        # `--` ends option parsing so text starting with '-' isn't read as a flag.
        subprocess.run(["tmux", "send-keys", "-t", target, "-l", "--", text],
                       check=True, timeout=5)
        key = "C-m" if send_key in ("return", "ctrl+return", "cmd+return") else send_key
        subprocess.run(["tmux", "send-keys", "-t", target, key],
                       check=True, timeout=5)
        if key == "C-m" and engine_alive_in(target, "codex"):
            # Codex TUI may leave a freshly pasted prompt staged after the first Enter;
            # the second Enter submits it. Empty second submits are ignored after success.
            time.sleep(0.12)
            subprocess.run(["tmux", "send-keys", "-t", target, "C-m"],
                           check=True, timeout=5)
        return True
    except (OSError, subprocess.SubprocessError):
        return False
