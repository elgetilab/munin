"""
Parallel MCP tool execution for agents.

Tool calls emitted by an agent's vLLM loop are fanned out via asyncio.gather
so multiple independent lookups don't run serially. Each result is timed
individually so we can report a realistic duration back to the frontend.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from mcp.executor import execute_mcp_tool


async def run_tools_parallel(
    tool_calls: list[dict],
    allowed_tools: list[str],
) -> list[dict]:
    """
    Execute a batch of tool calls concurrently. Tools not in `allowed_tools`
    are returned with a permission error instead of being executed.

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
    return await asyncio.gather(*(one(tc) for tc in tool_calls))
