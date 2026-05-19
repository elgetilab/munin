"""
Standalone tests for concurrency-safety partitioning of tool calls
(mcp/executor.py + the two dispatchers, P0 #5 from munin-audit.md).

Run inside the retrieval container:
    docker exec munin-retrieval python /app/tests/test_tool_concurrency.py
Or locally:
    python backend/retrieval/tests/test_tool_concurrency.py

Hermetic: monkey-patches execute_mcp_tool with a recording stub so the
tests exercise orchestration, not tool bodies.
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
from mcp.executor import partition_by_concurrency_safety  # noqa: E402
from mcp.schemas import MCP_TOOLS  # noqa: E402


def _check(name: str, ok: bool, detail: str = "") -> bool:
    label = "PASS" if ok else "FAIL"
    print(f"[{label}] {name}{(' - ' + detail) if detail and not ok else ''}")
    return ok


# ---------------------------------------------------------------------------
# Test scaffolding — recording fake for execute_mcp_tool
# ---------------------------------------------------------------------------

_real_execute = mcp_executor.execute_mcp_tool


class _Recorder:
    """Replaces execute_mcp_tool. Logs (name, start_t, end_t, args) for each
    invocation. Sleeps for ``delay_s`` to expose ordering."""

    def __init__(self, delay_s: float = 0.05):
        self.delay_s = delay_s
        self.log: list[tuple[str, float, float, dict]] = []

    async def __call__(self, name: str, args: dict) -> dict:
        start = time.monotonic()
        await asyncio.sleep(self.delay_s)
        end = time.monotonic()
        self.log.append((name, start, end, args))
        return {"name": name, "args": args}


def _install_recorder(delay_s: float = 0.05) -> _Recorder:
    rec = _Recorder(delay_s)
    # Patch the symbol both modules pulled in at import time.
    import chat_service  # noqa: E402
    import agents.parallel as agents_parallel  # noqa: E402
    mcp_executor.execute_mcp_tool = rec  # type: ignore[assignment]
    chat_service.execute_mcp_tool = rec  # type: ignore[assignment]
    agents_parallel.execute_mcp_tool = rec  # type: ignore[assignment]
    return rec


def _uninstall_recorder() -> None:
    import chat_service  # noqa: E402
    import agents.parallel as agents_parallel  # noqa: E402
    mcp_executor.execute_mcp_tool = _real_execute  # type: ignore[assignment]
    chat_service.execute_mcp_tool = _real_execute  # type: ignore[assignment]
    agents_parallel.execute_mcp_tool = _real_execute  # type: ignore[assignment]


def _intervals_overlap(
    a: tuple[float, float], b: tuple[float, float]
) -> bool:
    return not (a[1] <= b[0] or b[1] <= a[0])


# ---------------------------------------------------------------------------
# partition_by_concurrency_safety
# ---------------------------------------------------------------------------

def test_partition_marks_known_mutators_unsafe() -> bool:
    mutators = [
        "create_artifact", "update_artifact", "save_artifact_to_documents",
        "remember", "forget",
        "run_python", "sandbox_reset", "compile_latex",
    ]
    calls = [{"id": f"tc{i}", "name": n, "arguments": {}}
             for i, n in enumerate(mutators)]
    safe, unsafe = partition_by_concurrency_safety(calls)
    return _check(
        "partition: all 8 known mutators land in the unsafe bucket",
        len(safe) == 0 and len(unsafe) == len(mutators)
        and [tc["name"] for _, tc in unsafe] == mutators,
        f"safe={[tc['name'] for _, tc in safe]}, unsafe={[tc['name'] for _, tc in unsafe]}",
    )


def test_partition_marks_read_only_safe() -> bool:
    reads = ["paper_search", "web_search", "calculate", "get_citations",
             "read_artifact", "faq"]
    calls = [{"id": f"tc{i}", "name": n, "arguments": {}}
             for i, n in enumerate(reads)]
    safe, unsafe = partition_by_concurrency_safety(calls)
    return _check(
        "partition: read-only tools land in the safe bucket",
        len(unsafe) == 0 and [tc["name"] for _, tc in safe] == reads,
    )


def test_partition_default_is_safe() -> bool:
    """Unknown tool name (not in MCP_TOOLS) gets default-safe treatment.
    Audit's chosen default: missing flag → safe."""
    calls = [{"id": "tc0", "name": "definitely_not_a_real_tool",
              "arguments": {}}]
    safe, unsafe = partition_by_concurrency_safety(calls)
    return _check(
        "partition: unknown tool name defaults to safe",
        len(safe) == 1 and len(unsafe) == 0,
    )


def test_partition_preserves_index() -> bool:
    calls = [
        {"id": "tc0", "name": "paper_search", "arguments": {}},
        {"id": "tc1", "name": "update_artifact", "arguments": {}},
        {"id": "tc2", "name": "web_search", "arguments": {}},
        {"id": "tc3", "name": "remember", "arguments": {}},
    ]
    safe, unsafe = partition_by_concurrency_safety(calls)
    safe_indices = [idx for idx, _ in safe]
    unsafe_indices = [idx for idx, _ in unsafe]
    return _check(
        "partition: tagged indices match original positions",
        safe_indices == [0, 2] and unsafe_indices == [1, 3],
    )


# ---------------------------------------------------------------------------
# _run_tool_calls (chat_service)
# ---------------------------------------------------------------------------

def test_unsafe_tools_run_serial_in_declared_order() -> bool:
    """Three update_artifact calls with a 50ms delay each. If gather were
    still used, total wall time would be ~50ms and the intervals would
    overlap. Serial execution gives ~150ms total and strictly ordered
    intervals."""
    rec = _install_recorder(delay_s=0.05)
    try:
        from chat_service import _run_tool_calls  # imports rec via patch

        calls = [
            {"id": "tc0", "name": "update_artifact", "arguments": {"k": 0}},
            {"id": "tc1", "name": "update_artifact", "arguments": {"k": 1}},
            {"id": "tc2", "name": "update_artifact", "arguments": {"k": 2}},
        ]
        t0 = time.monotonic()
        asyncio.run(_run_tool_calls(calls))
        total = time.monotonic() - t0
    finally:
        _uninstall_recorder()

    starts = [start for _, start, _, _ in rec.log]
    args_seq = [args.get("k") for _, _, _, args in rec.log]
    return _check(
        "unsafe tools run serially in declared order",
        total >= 0.14
        and args_seq == [0, 1, 2]
        and starts == sorted(starts)
        and not any(
            _intervals_overlap(
                (rec.log[i][1], rec.log[i][2]),
                (rec.log[j][1], rec.log[j][2]),
            )
            for i in range(len(rec.log))
            for j in range(i + 1, len(rec.log))
        ),
        f"total={total:.3f}s, args_seq={args_seq}",
    )


def test_safe_tools_run_concurrent() -> bool:
    rec = _install_recorder(delay_s=0.05)
    try:
        from chat_service import _run_tool_calls

        calls = [
            {"id": "tc0", "name": "paper_search", "arguments": {}},
            {"id": "tc1", "name": "paper_search", "arguments": {}},
            {"id": "tc2", "name": "paper_search", "arguments": {}},
        ]
        t0 = time.monotonic()
        asyncio.run(_run_tool_calls(calls))
        total = time.monotonic() - t0
    finally:
        _uninstall_recorder()

    # All three intervals should overlap pairwise.
    pairs_overlap = all(
        _intervals_overlap(
            (rec.log[i][1], rec.log[i][2]),
            (rec.log[j][1], rec.log[j][2]),
        )
        for i in range(len(rec.log))
        for j in range(i + 1, len(rec.log))
    )
    return _check(
        "safe tools run concurrently (total time ~ 1 × delay, not 3 ×)",
        total < 0.12 and pairs_overlap,
        f"total={total:.3f}s, overlap={pairs_overlap}",
    )


def test_mixed_batch_preserves_output_order() -> bool:
    """[update_artifact, paper_search, update_artifact, web_search] —
    the two updates serialise relative to each other; the searches run
    in the safe gather. Output must come back in declared order."""
    rec = _install_recorder(delay_s=0.03)
    try:
        from chat_service import _run_tool_calls

        calls = [
            {"id": "tc0", "name": "update_artifact", "arguments": {"k": 0}},
            {"id": "tc1", "name": "paper_search", "arguments": {"k": 1}},
            {"id": "tc2", "name": "update_artifact", "arguments": {"k": 2}},
            {"id": "tc3", "name": "web_search", "arguments": {"k": 3}},
        ]
        results = asyncio.run(_run_tool_calls(calls))
    finally:
        _uninstall_recorder()

    ids_in_order = [r["id"] for r in results]
    # The two unsafe ones must have non-overlapping intervals AND
    # appear in declared order in the recorder log.
    unsafe_log = [(name, s, e) for name, s, e, _ in rec.log
                  if name == "update_artifact"]
    unsafe_serial = (
        len(unsafe_log) == 2
        and unsafe_log[0][2] <= unsafe_log[1][1] + 1e-6
    )
    return _check(
        "mixed batch returns results in declared order; unsafe serial preserved",
        ids_in_order == ["tc0", "tc1", "tc2", "tc3"] and unsafe_serial,
        f"ids={ids_in_order}, unsafe_log={unsafe_log}",
    )


def test_empty_batch_returns_empty() -> bool:
    from chat_service import _run_tool_calls
    results = asyncio.run(_run_tool_calls([]))
    return _check("empty tool_calls returns []", results == [])


def test_single_unsafe_tool_works() -> bool:
    rec = _install_recorder(delay_s=0.01)
    try:
        from chat_service import _run_tool_calls

        calls = [{"id": "tc0", "name": "remember", "arguments": {}}]
        results = asyncio.run(_run_tool_calls(calls))
    finally:
        _uninstall_recorder()
    return _check(
        "single unsafe tool dispatches normally",
        len(results) == 1
        and results[0]["id"] == "tc0"
        and results[0]["name"] == "remember",
    )


# ---------------------------------------------------------------------------
# run_tools_parallel (agents/parallel)
# ---------------------------------------------------------------------------

def test_agents_parallel_partitions_too() -> bool:
    """Same policy must apply on the sub-agent path."""
    rec = _install_recorder(delay_s=0.04)
    try:
        from agents.parallel import run_tools_parallel

        calls = [
            {"id": "atc0", "name": "remember", "arguments": {"k": 0}},
            {"id": "atc1", "name": "paper_search", "arguments": {"k": 1}},
            {"id": "atc2", "name": "remember", "arguments": {"k": 2}},
        ]
        allowed = ["remember", "paper_search"]
        results = asyncio.run(run_tools_parallel(calls, allowed))
    finally:
        _uninstall_recorder()

    unsafe_log = [(s, e) for n, s, e, _ in rec.log if n == "remember"]
    unsafe_serial = (
        len(unsafe_log) == 2 and unsafe_log[0][1] <= unsafe_log[1][0] + 1e-6
    )
    ids_in_order = [r["id"] for r in results]
    return _check(
        "agents/parallel: unsafe serialised, output in declared order",
        ids_in_order == ["atc0", "atc1", "atc2"] and unsafe_serial,
        f"ids={ids_in_order}, unsafe_log={unsafe_log}",
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

TESTS = [
    test_partition_marks_known_mutators_unsafe,
    test_partition_marks_read_only_safe,
    test_partition_default_is_safe,
    test_partition_preserves_index,
    test_unsafe_tools_run_serial_in_declared_order,
    test_safe_tools_run_concurrent,
    test_mixed_batch_preserves_output_order,
    test_empty_batch_returns_empty,
    test_single_unsafe_tool_works,
    test_agents_parallel_partitions_too,
]


def main() -> int:
    passed = 0
    failed = 0
    for test in TESTS:
        try:
            ok = test()
        except Exception:
            ok = False
            print(f"[FAIL] {test.__name__} - exception:")
            traceback.print_exc()
        if ok:
            passed += 1
        else:
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
