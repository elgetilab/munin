"""
Parallel MCP tool execution for agents.

Tool calls emitted by an agent's vLLM loop are dispatched concurrently
where the framework knows it's safe, and serialised otherwise. Tools
declaring ``is_concurrency_safe: False`` in MCP_TOOLS (the artifact /
memory / sandbox mutators) run one-at-a-time in declared order so they
can't race against each other. Everything else (paper_search,
web_search, calculate, ...) fans out via asyncio.gather. The two groups
run concurrently with each other — they don't share state by definition
of "safe".
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

from mcp.executor import execute_mcp_tool, partition_by_concurrency_safety


async def run_tools_parallel(
    tool_calls: list[dict],
    allowed_tools: list[str],
) -> list[dict]:
    """
    Execute a batch of tool calls with concurrency-safety partitioning.
    Tools not in `allowed_tools` are returned with a permission error
    instead of being executed. Output preserves declared order.

    Each input dict should have: id, name, arguments.
    Each output dict has: id, name, result, duration_ms.
    """
    async def one(tc: dict) -> dict:
        name = tc.get("name", "")
        tc_id = tc.get("id") or f"tc-{id(tc)}"
        args = tc.get("arguments") or {}

        started = time.monotonic()
        if name not in allowed_tools:
            result: Any = {
                "error": f"tool '{name}' is not allowed for this agent",
            }
        else:
            try:
                result = await execute_mcp_tool(name, args)
            except Exception as e:
                result = {"error": f"tool execution failed: {e}"}
        duration_ms = int((time.monotonic() - started) * 1000)

        return {
            "id": tc_id,
            "name": name,
            "arguments": args,
            "result": result,
            "duration_ms": duration_ms,
        }

    if not tool_calls:
        return []

    safe, unsafe = partition_by_concurrency_safety(tool_calls)
    results: list[Optional[dict]] = [None] * len(tool_calls)

    async def run_one(idx: int, tc: dict) -> None:
        results[idx] = await one(tc)

    async def run_unsafe_serial() -> None:
        for idx, tc in unsafe:
            await run_one(idx, tc)

    await asyncio.gather(
        *(run_one(idx, tc) for idx, tc in safe),
        run_unsafe_serial(),
    )
    return [r for r in results if r is not None]
