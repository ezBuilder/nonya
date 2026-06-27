"""status — nonya publishes its live state to a small JSON file so a separate
face (the native menubar pet / overlay) can animate it without coupling.

The pet polls ~/.local/state/nonya/state.json. nonya is the muscle; the pet just
mirrors `status`: watching | scolding | stuck | working | done | stopped.
Written atomically (tmp + os.replace) so the reader never sees a half file.
"""
from __future__ import annotations

import json
import os
import time

FILENAME = "state.json"


def _atomic(path: str, fields: dict) -> None:
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(fields, fh, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        pass


def write(state_dir: str, **fields) -> None:
    if not state_dir:
        return
    fields.setdefault("ts", int(time.time()))
    fields.setdefault("pid", os.getpid())                      # so a reader can drop the file when this run dies
    fields.setdefault("session", "%s:%d" % (fields.get("target", "nonya"), os.getpid()))
    _atomic(os.path.join(state_dir, FILENAME), fields)         # legacy single-session feed
    # per-session feed for the multi-session attention router
    sdir = os.path.join(state_dir, "sessions")
    try:
        os.makedirs(sdir, exist_ok=True)
        _atomic(os.path.join(sdir, "%d.json" % os.getpid()), fields)
    except OSError:
        pass


def cleanup(state_dir: str) -> None:
    """Best-effort: a single-session run removes its own per-pid state file on exit, so it does
    not linger as a phantom 'stuck' session in the fleet menu. (Killed runs miss this — the menu
    also drops dead-pid files defensively.)"""
    if not state_dir:
        return
    try:
        os.remove(os.path.join(state_dir, "sessions", "%d.json" % os.getpid()))
    except OSError:
        pass


def read(state_dir: str) -> dict:
    try:
        with open(os.path.join(state_dir, FILENAME), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}
