"""Tests for the verbatim-rerun guard (LAB-BUGFIX-PLAN.md F12).

Why: in the lab Bruker case (2026-11-15) the model ran one script six
times in a row, got the same wrong output each time, and used up the
turn without writing an answer. A sandbox call identical to the call
made just before it (nothing run in between) now gets a synthetic
"not run" result, and the second blocked rerun in a message ends the
tool loop so the wrap-up answers from what was gathered.

Run inside the retrieval container:
    docker exec munin-retrieval python /app/tests/test_repeat_calls.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import traceback
from pathlib import Path
from typing import Any

import os as _os
_os.environ.setdefault("MUNIN_SOURCE_LEDGER", "off")   # not a ledger test; the ledger would write sources for a conversation that is not in the db

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import chat_service  # noqa: E402
import mcp.executor as mcp_executor  # noqa: E402
from chat_service import (  # noqa: E402
    MAX_BLOCKED_REPEATS,
    _StreamAccumulator,
    _repeated_call_skips,
    _run_tool_calls,
)


import chat_service as cs  # noqa: E402


def _check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{(' - ' + detail) if detail and not ok else ''}")
    return ok


def _rp(tid: str, code: str = "print(1)") -> dict:
    return {"id": tid, "name": "run_python", "arguments": {"code": code}}


# --- _repeated_call_skips (pure) ----------------------------------------------

def test_identical_call_after_previous_is_skipped() -> bool:
    _, last = _repeated_call_skips([_rp("a")], None, 0)
    last = (last[0], {"stdout": "1"}, last[2])
    skips, last2 = _repeated_call_skips([_rp("b")], last, 1)
    ok = (list(skips) == ["b"] and skips["b"]["repeated_call"] is True
          and "call 1" in skips["b"]["reason"] and last2 == last)
    return _check("identical rerun of the previous call is skipped", ok, f"{skips} {last2}")


def test_changed_code_runs() -> bool:
    last = ("run_python:" + '{"code": "print(1)"}', {"stdout": "1"}, 1)
    skips, _ = _repeated_call_skips([_rp("b", "print(2)")], last, 1)
    return _check("changed code is not skipped", skips == {}, str(skips))


def test_rerun_after_something_else_runs() -> bool:
    _, last = _repeated_call_skips([_rp("a")], None, 0)
    last = (last[0], {"stdout": "1"}, 1)
    other = {"id": "x", "name": "web_search", "arguments": {"query": "q"}}
    skips, _ = _repeated_call_skips([other, _rp("b")], last, 1)
    return _check("rerun after another call in between is allowed", skips == {}, str(skips))


def test_rerun_after_timeout_or_infra_error_runs() -> bool:
    key = _repeated_call_skips([_rp("a")], None, 0)[1][0]
    a, _ = _repeated_call_skips([_rp("b")], (key, {"timed_out": True}, 1), 1)
    b, _ = _repeated_call_skips(
        [_rp("c")], (key, {"error": "tool execution failed: boom"}, 1), 1)
    ok = a == {} and b == {}
    return _check("rerun after a timeout or infrastructure error is allowed", ok, f"{a} {b}")


def test_rerun_after_code_error_is_skipped() -> bool:
    key = _repeated_call_skips([_rp("a")], None, 0)[1][0]
    skips, _ = _repeated_call_skips([_rp("b")], (key, {"error": "NameError: x"}, 1), 1)
    return _check("rerun after a plain code error is skipped", list(skips) == ["b"], str(skips))


def test_blocked_rerun_quotes_the_error() -> bool:
    """Phase 4 run 3 (LABKIT-013-PLAN.md): the block message said "same
    result" without the error, and the Bruker run reran into a wrap-up."""
    key = _repeated_call_skips([_rp("a")], None, 0)[1][0]
    err = ("AttributeError: Spectrum has no attribute 'ppmm'; did you mean 'ppm'? It has: ppm, real\n"
           "---------------------------------------------------------------------------\nTraceback ...")
    skips, _ = _repeated_call_skips([_rp("b")], (key, {"error": err}, 3), 3)
    reason = skips.get("b", {}).get("reason", "")
    ok = ("did you mean 'ppm'" in reason and "call 3" in reason and "Traceback" not in reason
          and "Fix the line" in reason)
    return _check("a blocked rerun after an error quotes the exception line", ok, reason)


def test_duplicate_within_one_response_is_skipped() -> bool:
    skips, _ = _repeated_call_skips([_rp("a"), _rp("b")], None, 0)
    return _check("a verbatim copy in the same response is skipped", list(skips) == ["b"], str(skips))


def test_edit_python_same_edits_is_skipped_unguarded_tools_are_not() -> bool:
    ed = {"name": "edit_python", "arguments": {"edits": [{"old": "a", "new": "b"}]}}
    _, last = _repeated_call_skips([dict(ed, id="a")], None, 0)
    last = (last[0], {"stdout": ""}, 1)
    s1, _ = _repeated_call_skips([dict(ed, id="b")], last, 1)
    ws = {"name": "web_search", "arguments": {"query": "q"}}
    _, last = _repeated_call_skips([dict(ws, id="c")], None, 0)
    s2, _ = _repeated_call_skips([dict(ws, id="d")], (last[0], {}, 1), 1)
    ok = list(s1) == ["b"] and s2 == {}
    return _check("edit_python reruns are guarded, web_search is not", ok, f"{s1} {s2}")


def test_argument_order_does_not_matter() -> bool:
    a = {"id": "a", "name": "run_python", "arguments": {"code": "x", "timeout_s": 30}}
    b = {"id": "b", "name": "run_python", "arguments": {"timeout_s": 30, "code": "x"}}
    skips, _ = _repeated_call_skips([a, b], None, 0)
    return _check("argument key order does not hide a repeat", list(skips) == ["b"], str(skips))


# --- _run_tool_calls honours skip_results -------------------------------------

def test_run_tool_calls_returns_skip_without_executing() -> bool:
    ran: list[str] = []

    async def rec(name: str, args: dict) -> dict:
        ran.append(name)
        return {"stdout": "ok"}
    real = chat_service.execute_mcp_tool
    chat_service.execute_mcp_tool = rec  # type: ignore[assignment]
    mcp_executor_real = mcp_executor.execute_mcp_tool
    mcp_executor.execute_mcp_tool = rec  # type: ignore[assignment]
    try:
        res = asyncio.run(_run_tool_calls(
            [_rp("a"), _rp("b", "print(2)")],
            skip_results={"a": {"skipped": True, "repeated_call": True}}))
    finally:
        chat_service.execute_mcp_tool = real  # type: ignore[assignment]
        mcp_executor.execute_mcp_tool = mcp_executor_real  # type: ignore[assignment]
    by = {r["id"]: r["result"] for r in res}
    ok = ran == ["run_python"] and by["a"].get("repeated_call") and by["b"] == {"stdout": "ok"}
    return _check("_run_tool_calls returns the skip and runs only the other call", ok, f"{ran} {by}")


# --- the full loop: the second blocked rerun forces the wrap-up ---------------

def test_loop_wraps_up_after_blocked_reruns() -> bool:
    import test_stream_error_persistence as harness

    calls = {"tools": 0, "wrap": 0}
    executed: list[str] = []
    only: list = []

    async def fake_stream(*args: Any, **kwargs: Any):
        acc = _StreamAccumulator()
        only.append(kwargs.get("only_tools"))
        if kwargs.get("enable_tools") is False:
            calls["wrap"] += 1
            acc.content_parts.append("Here is what I found.")
            acc.finish_reason = "stop"
            yield ("token", {"content": "Here is what I found."}, acc)
            return
        calls["tools"] += 1
        if calls["tools"] > 10:
            raise AssertionError("the loop did not stop on blocked reruns")
        n = calls["tools"]
        acc.tool_calls[0] = {"id": f"tc{n}", "name": "run_python",
                             "arguments_raw": '{"code": "print(1)"}'}
        acc.finish_reason = "tool_calls"
        yield ("tool_call", {"id": f"tc{n}", "name": "run_python",
                             "arguments": {"code": "print(1)"}}, acc)

    async def fake_run(tool_calls: list[dict], skip_results=None, **kw: Any):
        out = []
        for tc in tool_calls:
            if skip_results and tc["id"] in skip_results:
                result = skip_results[tc["id"]]
            else:
                executed.append(tc["id"])
                result = {"stdout": "1"}
            out.append({"id": tc["id"], "name": tc["name"],
                        "result": result, "duration_ms": 1})
        return out

    capture: dict = {}
    harness._drive_stream_chat(fake_stream, fake_run, capture)
    saved = [c for c in capture["calls"] if c.get("role") == "assistant"]
    content = saved[-1].get("content", "") if saved else ""
    # 2026-11-21: after the guard trips, one save round (sandbox tools only),
    # then the wrap-up (LABKIT-016-PLAN.md, change 3)
    ok = (executed == ["tc1"] and calls["tools"] == 2 + MAX_BLOCKED_REPEATS
          and calls["wrap"] == 1 and "Here is what I found." in content
          and only[-2] == cs.SAVE_ROUND_TOOLS and only[0] is None)
    return _check("two blocked reruns: a save round, then a wrap-up", ok,
                  f"executed={executed} calls={calls} only={only} content={content!r}")


def test_comments_and_blank_lines_do_not_hide_a_repeat() -> bool:
    _, last = _repeated_call_skips([_rp("a", "x = 1\nprint(x)")], None, 0)
    last = (last[0], {"stdout": "1"}, last[2])
    skips, _ = _repeated_call_skips(
        [_rp("b", "# again\nx = 1\n\nprint(x)   ")], last, 1)
    msg = (skips.get("b") or {}).get("reason", "")
    return _check("a rerun differing in comments only is skipped, and says what to do if "
                  "the result was cut",
                  list(skips) == ["b"] and "cut or not shown" in msg
                  and "same result shown there" not in msg, str(skips))


def test_code_change_inside_a_line_still_runs() -> bool:
    _, last = _repeated_call_skips([_rp("a", "x = 1  # one")], None, 0)
    last = (last[0], {"stdout": ""}, last[2])
    skips, _ = _repeated_call_skips([_rp("b", "x = 2  # one")], last, 1)
    return _check("changed code with the same comment runs", skips == {}, str(skips))


def _budget_run(tool_name: str):
    import test_stream_error_persistence as harness

    seen: list = []

    async def fake_stream(*args: Any, **kwargs: Any):
        acc = _StreamAccumulator()
        msgs = kwargs.get("messages") or []
        seen.append((kwargs.get("only_tools"), kwargs.get("enable_tools"), list(msgs)))
        if kwargs.get("enable_tools") is False:
            acc.content_parts.append("Done.")
            acc.finish_reason = "stop"
            yield ("token", {"content": "Done."}, acc)
            return
        n = len(seen)
        args_ = {"code": f"print({n})"} if tool_name == "run_python" else {"expression": f"{n}+1"}
        acc.tool_calls[0] = {"id": f"tc{n}", "name": tool_name, "arguments_raw": json.dumps(args_)}
        acc.finish_reason = "tool_calls"
        yield ("tool_call", {"id": f"tc{n}", "name": tool_name, "arguments": args_}, acc)

    async def fake_run(tool_calls: list[dict], skip_results=None, **kw: Any):
        return [{"id": tc["id"], "name": tc["name"], "result": {"stdout": "ok"},
                 "duration_ms": 1} for tc in tool_calls]

    capture: dict = {}
    harness._drive_stream_chat(fake_stream, fake_run, capture)
    return seen


def test_budget_end_gets_a_save_round_and_a_warning() -> bool:
    seen = _budget_run("run_python")
    tool_rounds = [x for x in seen if x[1] is not False]
    last_msgs = tool_rounds[-1][2]
    notes = [m for m in last_msgs if m.get("role") == "tool"
             and "2 tool rounds left" in str(m.get("content"))]
    ok = (len(tool_rounds) >= 3 and tool_rounds[-1][0] == cs.SAVE_ROUND_TOOLS
          and all(x[0] is None for x in tool_rounds[:-1])
          and any(m.get("content") == cs.SAVE_ROUND_MESSAGE for m in last_msgs)
          and len(notes) == 1 and seen[-1][1] is False
          and "Do not run more checks" in cs.SAVE_ROUND_MESSAGE
          and cs.WRAP_UP_VALUES_LINE in str(seen[-1][2][-1].get("content")))
    return _check("the last round of a sandbox turn is a save round, warned 2 rounds ahead",
                  ok, f"rounds={len(tool_rounds)} only={[x[0] for x in tool_rounds][-3:]} notes={len(notes)}")


def test_no_save_round_without_the_sandbox() -> bool:
    seen = _budget_run("calculate")
    tool_rounds = [x for x in seen if x[1] is not False]
    return _check("a turn that never ran code gets no save round",
                  all(x[0] is None for x in tool_rounds) and seen[-1][1] is False
                  and cs.WRAP_UP_VALUES_LINE not in str(seen[-1][2][-1].get("content")),
                  str([x[0] for x in tool_rounds]))


# --- the named-file nudge (LABKIT-018-PLAN.md, change 3) ------------------------

ASK = ("Fit the decay. Once you have it, save your final numbers with Python "
       "as 'results.json' in the working directory.")
ANSWER = "The half-life is 4.2 min (95% CI 3.9 to 4.5)."


def test_requested_file_names() -> bool:
    fire = {
        ASK: ["results.json"],
        "Save the fit to fit_results.csv": ["fit_results.csv"],
        "write results.json": ["results.json"],
        "export the table as a file outputs/table.xlsx": ["table.xlsx"],
        "Please save it as results.json.": ["results.json"],
    }
    quiet = ["read results.json", "Read results.json and summarise it",
             "Write a summary of data.csv", "The file plate.csv was saved by the reader",
             "Don't save results.json", "save the figure to disk", "write it in python 3.12",
             "Save the data in data.csv to a plot"]
    got = {t: cs.requested_file_names(t, {"data.csv"}) for t in list(fire) + quiet}
    ok = all(got[t] == v for t, v in fire.items()) and all(got[t] == [] for t in quiet)
    return _check("a verb plus a file name asks for a file; reading or describing one does not",
                  ok, str({t: v for t, v in got.items() if v != fire.get(t, [])}))


def _nudge_run(user_text: str, first_writes: list[str], nudge_writes: list[str],
               nudge_calls_tool: bool = True, tool: str = "run_python"):
    """Round 1 runs code (writing first_writes), round 2 answers. If a nudge
    round follows, it writes nudge_writes (or answers in text)."""
    import test_stream_error_persistence as harness

    seen: list = []

    async def fake_stream(*args: Any, **kwargs: Any):
        acc = _StreamAccumulator()
        msgs = list(kwargs.get("messages") or [])
        seen.append((kwargs.get("only_tools"), kwargs.get("enable_tools"), msgs))
        n = len(seen)
        if kwargs.get("enable_tools") is False:
            acc.content_parts.append("WRAP-UP")
            acc.finish_reason = "stop"
            yield ("token", {"content": "WRAP-UP"}, acc)
            return
        if n == 2 or (n == 3 and not nudge_calls_tool):
            text = ANSWER if n == 2 else "I wrote it."
            acc.content_parts.append(text)
            acc.finish_reason = "stop"
            yield ("token", {"content": text}, acc)
            return
        if n > 3:
            raise AssertionError("more rounds than expected")
        args_ = {"code": f"print({n})"} if tool == "run_python" else {"expression": "1+1"}
        acc.content_parts.append("Writing the file." if n == 3 else "")
        acc.tool_calls[0] = {"id": f"tc{n}", "name": tool, "arguments_raw": json.dumps(args_)}
        acc.finish_reason = "tool_calls"
        yield ("tool_call", {"id": f"tc{n}", "name": tool, "arguments": args_}, acc)

    async def fake_run(tool_calls: list[dict], skip_results=None, **kw: Any):
        out = []
        for tc in tool_calls:
            names = first_writes if tc["id"] == "tc1" else nudge_writes
            out.append({"id": tc["id"], "name": tc["name"], "duration_ms": 1, "result": {
                "stdout": "ok", "artifacts": [{"id": f"a-{f}", "filename": f} for f in names]}})
        return out

    capture: dict = {}
    events = harness._drive_stream_chat(fake_stream, fake_run, capture, user_text=user_text)
    saved = [c for c in capture["calls"] if c.get("role") == "assistant"]
    return seen, events, (saved[-1] if saved else {})


def test_named_file_not_written_gets_one_save_round() -> bool:
    seen, events, saved = _nudge_run(ASK, [], ["results.json"])
    msg = cs.NAMED_FILE_MESSAGE.format(name="results.json")
    tokens = [json.loads(e["data"]).get("content") for e in events if e.get("event") == "token"]
    ok = (len(seen) == 3 and seen[2][0] == cs.SAVE_ROUND_TOOLS
          and seen[2][2][-1].get("content") == msg
          and seen[2][2][-2] == {"role": "assistant", "content": ANSWER}
          and all(x[1] is not False for x in seen)            # no wrap-up
          and saved.get("content") == ANSWER                   # the first answer, once
          and "Writing the file." not in tokens                # the save round's text not streamed
          and tokens[-1] == ANSWER)                            # the answer sent again after the tool card
    return _check("a named file the turn did not write gets one save round; the answer is kept",
                  ok, f"rounds={len(seen)} only={[x[0] for x in seen]} saved={saved.get('content')!r} "
                      f"tokens={tokens}")


def test_no_nudge_when_the_file_was_written() -> bool:
    seen, _, saved = _nudge_run(ASK, ["outputs/results.json"], [])
    return _check("no nudge when this turn wrote the named file",
                  len(seen) == 2 and saved.get("content") == ANSWER, f"rounds={len(seen)}")


def test_no_nudge_for_reading_a_file_or_without_the_sandbox() -> bool:
    read_seen, _, _ = _nudge_run("Read results.json and tell me the half-life.", [], [])
    calc_seen, _, _ = _nudge_run(ASK, [], [], tool="calculate")
    return _check("no nudge for 'read results.json', nor in a turn that ran no code",
                  len(read_seen) == 2 and len(calc_seen) == 2,
                  f"read={len(read_seen)} calc={len(calc_seen)}")


def test_nudge_once_and_a_text_reply_keeps_the_answer() -> bool:
    seen, events, saved = _nudge_run(ASK, [], [], nudge_calls_tool=False)
    tokens = [json.loads(e["data"]).get("content") for e in events if e.get("event") == "token"]
    ok = (len(seen) == 3 and saved.get("content") == ANSWER and "I wrote it." not in tokens
          and all(x[1] is not False for x in seen))
    return _check("the nudge fires once; a text-only reply to it is dropped and the answer kept",
                  ok, f"rounds={len(seen)} saved={saved.get('content')!r} tokens={tokens}")


TESTS = [
    test_identical_call_after_previous_is_skipped,
    test_changed_code_runs,
    test_rerun_after_something_else_runs,
    test_rerun_after_timeout_or_infra_error_runs,
    test_rerun_after_code_error_is_skipped,
    test_blocked_rerun_quotes_the_error,
    test_duplicate_within_one_response_is_skipped,
    test_edit_python_same_edits_is_skipped_unguarded_tools_are_not,
    test_argument_order_does_not_matter,
    test_run_tool_calls_returns_skip_without_executing,
    test_loop_wraps_up_after_blocked_reruns,
    test_comments_and_blank_lines_do_not_hide_a_repeat,
    test_code_change_inside_a_line_still_runs,
    test_budget_end_gets_a_save_round_and_a_warning,
    test_no_save_round_without_the_sandbox,
    test_requested_file_names,
    test_named_file_not_written_gets_one_save_round,
    test_no_nudge_when_the_file_was_written,
    test_no_nudge_for_reading_a_file_or_without_the_sandbox,
    test_nudge_once_and_a_text_reply_keeps_the_answer,
]


def main() -> int:
    passed = 0
    for t in TESTS:
        try:
            passed += bool(t())
        except Exception:
            traceback.print_exc()
            print(f"[FAIL] {t.__name__} raised")
    print(f"\n{passed}/{len(TESTS)} passed")
    return 0 if passed == len(TESTS) else 1


if __name__ == "__main__":
    sys.exit(main())
