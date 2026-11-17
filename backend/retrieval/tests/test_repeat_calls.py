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
import sys
import traceback
from pathlib import Path
from typing import Any

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

    async def fake_stream(*args: Any, **kwargs: Any):
        acc = _StreamAccumulator()
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
    ok = (executed == ["tc1"] and calls["tools"] == 1 + MAX_BLOCKED_REPEATS
          and calls["wrap"] == 1 and "Here is what I found." in content)
    return _check("two blocked reruns end the loop in a wrap-up", ok,
                  f"executed={executed} calls={calls} content={content!r}")


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
