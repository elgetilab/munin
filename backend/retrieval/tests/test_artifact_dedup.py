"""
Tests for same-response create_artifact dedup (chat d28ef78e, Option C).

The model emitted 3 create_artifact calls with the same title ("Pong
Game") in one response, minting 3 redundant artifacts. `_run_tool_calls`
now keeps only the LAST create_artifact per normalised title and returns
a synthetic "duplicate" result for the earlier ones (so every tool_call
still has a result). Distinct titles are untouched.

Run inside the retrieval container:
    docker exec munin-retrieval python /app/tests/test_artifact_dedup.py
"""
from __future__ import annotations

import asyncio
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mcp.executor as mcp_executor  # noqa: E402
import chat_service  # noqa: E402
from chat_service import _duplicate_create_artifact_ids, _run_tool_calls  # noqa: E402


def _check(name: str, ok: bool, detail: str = "") -> bool:
    label = "PASS" if ok else "FAIL"
    print(f"[{label}] {name}{(' - ' + detail) if detail and not ok else ''}")
    return ok


def _ca(tid: str, title) -> dict:
    return {"id": tid, "name": "create_artifact", "arguments": {"title": title}}


# ---------------------------------------------------------------------------
# _duplicate_create_artifact_ids (pure)
# ---------------------------------------------------------------------------

def test_three_same_title_skips_all_but_last() -> bool:
    calls = [
        _ca("tc0", "Pong Game"),
        {"id": "tc1", "name": "set_plan", "arguments": {}},
        _ca("tc2", "Pong Game"),
        _ca("tc3", "Pong Game"),
    ]
    skip = _duplicate_create_artifact_ids(calls)
    return _check(
        "3 same-title create_artifact -> skip all but the last",
        skip == {"tc0", "tc2"}, f"skip={skip}",
    )


def test_distinct_titles_not_deduped() -> bool:
    calls = [_ca("a", "index.html"), _ca("b", "style.css")]
    skip = _duplicate_create_artifact_ids(calls)
    return _check("distinct titles are never deduped", skip == set(), f"skip={skip}")


def test_title_normalisation() -> bool:
    calls = [_ca("a", "Pong Game"), _ca("b", "  pong game "), _ca("c", "PONG GAME")]
    skip = _duplicate_create_artifact_ids(calls)
    return _check(
        "title match is case/whitespace-insensitive",
        skip == {"a", "b"}, f"skip={skip}",
    )


def test_untitled_not_deduped() -> bool:
    calls = [_ca("a", ""), _ca("b", None), _ca("c", "   ")]
    skip = _duplicate_create_artifact_ids(calls)
    return _check("untitled create_artifact calls are not deduped", skip == set(), f"skip={skip}")


# ---------------------------------------------------------------------------
# _run_tool_calls integration (recorder stub for execute_mcp_tool)
# ---------------------------------------------------------------------------

class _Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def __call__(self, name: str, args: dict) -> dict:
        await asyncio.sleep(0)
        self.calls.append((name, args))
        return {"name": name, "ok": True}


def _with_recorder(coro_factory):
    rec = _Recorder()
    real = mcp_executor.execute_mcp_tool
    mcp_executor.execute_mcp_tool = rec  # type: ignore[assignment]
    chat_service.execute_mcp_tool = rec  # type: ignore[assignment]
    try:
        return rec, asyncio.run(coro_factory())
    finally:
        mcp_executor.execute_mcp_tool = real  # type: ignore[assignment]
        chat_service.execute_mcp_tool = real  # type: ignore[assignment]


def test_run_tool_calls_executes_one_skips_duplicates() -> bool:
    calls = [_ca("tc0", "Pong Game"), _ca("tc1", "Pong Game"), _ca("tc2", "Pong Game")]
    rec, results = _with_recorder(lambda: _run_tool_calls(calls))
    # execute_mcp_tool ran exactly once (only the kept create_artifact).
    executed = [n for n, _ in rec.calls]
    by_id = {r["id"]: r for r in results}
    kept_ok = by_id["tc2"].get("result", {}).get("ok") is True
    skipped_ok = (
        by_id["tc0"]["result"].get("skipped") is True
        and by_id["tc1"]["result"].get("skipped") is True
    )
    ok = executed == ["create_artifact"] and kept_ok and skipped_ok and len(results) == 3
    return _check(
        "run_tool_calls: one real create, two synthetic 'duplicate' results",
        ok, f"executed={executed} results={[(r['id'], list(r['result'])) for r in results]}",
    )


def test_run_tool_calls_keeps_distinct_titles() -> bool:
    calls = [_ca("a", "index.html"), _ca("b", "style.css")]
    rec, results = _with_recorder(lambda: _run_tool_calls(calls))
    executed = sorted(n for n, _ in rec.calls)
    ok = executed == ["create_artifact", "create_artifact"] and all(
        r["result"].get("ok") is True for r in results
    )
    return _check("run_tool_calls: distinct-title artifacts both created", ok, f"executed={executed}")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

TESTS = [
    test_three_same_title_skips_all_but_last,
    test_distinct_titles_not_deduped,
    test_title_normalisation,
    test_untitled_not_deduped,
    test_run_tool_calls_executes_one_skips_duplicates,
    test_run_tool_calls_keeps_distinct_titles,
]


def main() -> int:
    passed = failed = 0
    for t in TESTS:
        try:
            ok = t()
        except Exception:
            ok = False
            print(f"[FAIL] {t.__name__} - exception:")
            traceback.print_exc()
        passed += int(ok)
        failed += int(not ok)
    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
