#!/usr/bin/env python3
"""Unit tests for nonya.supervise — the 4-state supervisory classifier.

Fixtures are tiny JSONL written inline (no fixture files). Plain asserts, no
pytest, same house style as tests/test_classify.py:

    python3 tests/test_supervise.py
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)

from nonya import supervise  # noqa: E402

_fail = 0
_tmp = []


def check(label, got, want):
    global _fail
    if got == want:
        print("ok    %-34s -> %s" % (label, got))
    else:
        print("FAIL  %-34s -> %s (want %s)" % (label, got, want))
        _fail = 1


def write_jsonl(records):
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")
    _tmp.append(path)
    return path


def claude_asst(blocks, stop_reason=None):
    msg = {"role": "assistant", "content": blocks}
    if stop_reason:
        msg["stop_reason"] = stop_reason
    return {"type": "assistant", "message": msg}


def claude_user(content):
    return {"type": "user", "message": {"role": "user", "content": content}}


def tool_use(name, input_, tid):
    return {"type": "tool_use", "name": name, "input": input_, "id": tid}


def tool_result(tid, text="ok"):
    return {"type": "tool_result", "tool_use_id": tid, "content": text}


# --- fingerprint helper: identical args collide, varied args differ ---
fp_a = supervise.loop_fingerprint("Edit", {"file": "a.py", "old": "x", "new": "y"})
fp_a2 = supervise.loop_fingerprint("edit", {"new": "y", "file": "a.py", "old": " x "})
fp_b = supervise.loop_fingerprint("Edit", {"file": "b.py", "old": "x", "new": "y"})
check("fingerprint identical collide", fp_a, fp_a2)
check("fingerprint varied differ", fp_a != fp_b, True)
# volatile keys (id/ts) ignored
fp_c = supervise.loop_fingerprint("Edit", {"file": "a.py", "old": "x", "new": "y", "id": "1", "ts": 9})
check("fingerprint ignores volatile", fp_a, fp_c)


# --- DONE: clean end_turn, nothing pending ---
p_done = write_jsonl([
    claude_user("do the thing"),
    claude_asst([{"type": "text", "text": "All finished and verified."}],
                stop_reason="end_turn"),
])
check("DONE clean end_turn", supervise.classify4("claude", p_done), supervise.DONE)


# --- WAITING: tail ends on a question ---
p_wait_q = write_jsonl([
    claude_user("which db?"),
    claude_asst([{"type": "text", "text": "Should I use Postgres or SQLite?"}],
                stop_reason="end_turn"),
])
check("WAITING trailing question", supervise.classify4("claude", p_wait_q), supervise.WAITING)

# --- WAITING: AskUserQuestion tool with no result (permission ask) ---
p_wait_tool = write_jsonl([
    claude_user("deploy?"),
    claude_asst([tool_use("AskUserQuestion", {"question": "Proceed with deploy?"}, "t1")],
                stop_reason="tool_use"),
])
check("WAITING AskUserQuestion tool", supervise.classify4("claude", p_wait_tool), supervise.WAITING)
check("WAITING tool text extracted", supervise.waiting_text("claude", p_wait_tool), "Proceed with deploy?")


# --- LOOPING: same Edit -> same args x4 within the window ---
loop_edit = tool_use("Edit", {"file": "main.py", "old": "foo", "new": "bar"}, "x")
p_loop = write_jsonl([
    claude_user("fix it"),
    claude_asst([loop_edit], stop_reason="tool_use"),
    claude_asst([loop_edit], stop_reason="tool_use"),
    claude_asst([loop_edit], stop_reason="tool_use"),
    claude_asst([loop_edit], stop_reason="tool_use"),
])
check("active same edit x4 is working", supervise.classify4("claude", p_loop, idle=0),
      supervise.WORKING)
check("quiet same edit x4 is LOOPING", supervise.classify4("claude", p_loop, idle=60),
      supervise.LOOPING)

# --- NON-looping varied: different files each edit, ends completed ---
p_varied = write_jsonl([
    claude_user("refactor"),
    claude_asst([tool_use("Edit", {"file": "a.py", "old": "1", "new": "2"}, "a")],
                stop_reason="tool_use"),
    claude_asst([tool_use("Edit", {"file": "b.py", "old": "3", "new": "4"}, "b")],
                stop_reason="tool_use"),
    claude_asst([tool_use("Edit", {"file": "c.py", "old": "5", "new": "6"}, "c")],
                stop_reason="tool_use"),
    claude_asst([tool_use("Edit", {"file": "d.py", "old": "7", "new": "8"}, "d")],
                stop_reason="tool_use"),
    claude_asst([{"type": "text", "text": "Refactor complete."}], stop_reason="end_turn"),
])
check("NON-looping varied edits", supervise.classify4("claude", p_varied), supervise.DONE)


# --- STUCK: API error ---
p_err = write_jsonl([
    claude_user("go"),
    {"type": "assistant", "isApiErrorMessage": True, "error": "boom",
     "message": {"role": "assistant", "content": []}},
])
check("STUCK api error", supervise.classify4("claude", p_err), supervise.STUCK)

# --- STUCK: rate limit (429) ---
p_429 = write_jsonl([
    claude_user("go"),
    {"type": "assistant", "apiErrorStatus": 429,
     "message": {"role": "assistant", "content": []}},
])
check("STUCK 429", supervise.classify4("claude", p_429), supervise.STUCK)

# --- STUCK: unmatched tool_use past the time cap (idle forced high) ---
p_hang = write_jsonl([
    claude_user("run it"),
    claude_asst([tool_use("Bash", {"cmd": "sleep 999"}, "h1")], stop_reason="tool_use"),
])
check("STUCK unmatched tool past cap",
      supervise.classify4("claude", p_hang, idle=99999), supervise.STUCK)
# below the cap, the same hang is NOT stuck (still pending) -> falls through, not STUCK-via-cap.
# (it has no completion/question/loop so the safe fallback is STUCK anyway; assert the cap path
#  separately by confirming a matched tool below the cap that completed is DONE)
p_hang_done = write_jsonl([
    claude_user("run it"),
    claude_asst([tool_use("Bash", {"cmd": "ls"}, "h2")], stop_reason="tool_use"),
    {"type": "user", "message": {"role": "user", "content": [tool_result("h2")]}},
    claude_asst([{"type": "text", "text": "done"}], stop_reason="end_turn"),
])
check("matched tool then done", supervise.classify4("claude", p_hang_done, idle=0),
      supervise.DONE)


# --- codex LOOPING: identical function_call x4, after the transcript quiets ---
def codex_call(name, args):
    return {"payload": {"type": "function_call", "name": name,
                        "arguments": json.dumps(args)}}


def codex_mcp_call(phase, tool, args):
    return {"payload": {"type": phase, "invocation": {"tool": tool, "arguments": args}}}


p_codex_loop = write_jsonl(
    [{"payload": {"type": "task_started"}}]
    + [codex_call("shell", {"command": "make test"}) for _ in range(4)]
)
check("codex active repeated call is working", supervise.classify4("codex", p_codex_loop, idle=0),
      supervise.WORKING)
check("codex quiet repeated call is LOOPING", supervise.classify4("codex", p_codex_loop, idle=60),
      supervise.LOOPING)

p_codex_done_after_repeat = write_jsonl(
    [{"payload": {"type": "task_started"}}]
    + [codex_call("shell", {"command": "make test"}) for _ in range(4)]
    + [{"payload": {"type": "task_complete"}}])
check("codex repeated then DONE wins",
      supervise.classify4("codex", p_codex_done_after_repeat, idle=60),
      supervise.DONE)

p_codex_mcp_pairs = write_jsonl(
    [{"payload": {"type": "task_started"}}]
    + [codex_mcp_call("mcp_tool_call_begin", "read_status", {"thread": "x"}),
       codex_mcp_call("mcp_tool_call_end", "read_status", {"thread": "x"}),
       codex_mcp_call("mcp_tool_call_begin", "read_status", {"thread": "x"}),
       codex_mcp_call("mcp_tool_call_end", "read_status", {"thread": "x"})])
check("codex MCP begin/end not double counted",
      supervise.classify4("codex", p_codex_mcp_pairs, idle=60),
      supervise.WORKING)

# --- codex DONE ---
p_codex_done = write_jsonl([
    {"payload": {"type": "task_started"}},
    codex_call("shell", {"command": "ls"}),
    {"payload": {"type": "task_complete"}},
])
check("codex DONE task_complete", supervise.classify4("codex", p_codex_done, idle=0),
      supervise.DONE)


# --- missing path -> STUCK (safe) ---
check("missing path STUCK", supervise.classify4("claude", "/no/such/file.jsonl"),
      supervise.STUCK)


for p in _tmp:
    try:
        os.remove(p)
    except OSError:
        pass

print("ALL PASS" if _fail == 0 else "SOME FAILED")
sys.exit(_fail)
