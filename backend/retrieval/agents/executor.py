"""
Agent execution loop.

An agent run is a bounded tool-calling loop over vLLM:

    1. Start with [system, user] messages.
    2. Call vLLM non-streamed with the agent's tool allowlist.
    3. If the model emitted tool_calls: execute them in parallel (respecting
       the allowlist), append the assistant+tool messages to the history,
       and loop.
    4. If the model answered without tool_calls, or a guardrail (iterations,
       tool-call total, wall clock) trips, return the final answer.

Throughout, we emit SSE events through the `current_sse_emitter` ContextVar
so the main chat stream can bubble progress up to the frontend without the
frontend having to know this is a nested call.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

from database import VLLM_MODEL_NAME
from mcp.schemas import MCP_TOOLS
from mcp.context import current_sse_emitter
from vllm_client import vllm_post_json, VLLMRequestError
from usage_tracker import record_usage
from tool_result import truncate_tool_result

from .parallel import run_tools_parallel

logger = logging.getLogger(__name__)


def _tools_openai_schema(allowlist: list[str]) -> list[dict]:
    """Translate the allowlist into the OpenAI `tools` array vLLM expects."""
    tools: list[dict] = []
    for name in allowlist:
        spec = MCP_TOOLS.get(name)
        if not spec:
            continue
        tools.append({
            "type": "function",
            "function": {
                "name": name,
                "description": spec.get("description", ""),
                "parameters": spec.get("inputSchema", {"type": "object"}),
            },
        })
    return tools


def _parse_arguments(raw: Any) -> dict:
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {"_raw": str(raw)}


def _normalize_tool_calls(raw_tool_calls: list[dict]) -> list[dict]:
    out: list[dict] = []
    for i, tc in enumerate(raw_tool_calls or []):
        fn = tc.get("function") or {}
        out.append({
            "id": tc.get("id") or f"agent-tc-{i}",
            "name": fn.get("name") or "",
            "arguments": _parse_arguments(fn.get("arguments")),
        })
    return out


async def _emit(event: str, data: dict) -> None:
    """Push an SSE event through the active emitter (no-op if unset)."""
    emitter = current_sse_emitter.get()
    if emitter is not None:
        try:
            emitter(event, data)
        except Exception:
            pass


async def _call_vllm(
    messages: list[dict], tools: list[dict], *, purpose: str
) -> Optional[dict]:
    """
    Non-streaming vLLM call. Returns the raw `message` dict from the first
    choice, or None on failure.

    ``purpose`` tags usage with "agent_turn" or "agent_wrap_up" so the
    per-request aggregator credits agent tokens to the right bucket.
    """
    try:
        data = await vllm_post_json(
            {
                "model": VLLM_MODEL_NAME,
                "messages": messages,
                "tools": tools,
                "tool_choice": "auto" if tools else "none",
                "temperature": 0.7,
                "stream": False,
            },
            timeout=120.0,
            purpose=purpose,
        )
    except VLLMRequestError as e:
        logger.error("Agent vLLM call failed: %s", e)
        return None
    record_usage(purpose, data.get("usage"))
    choices = data.get("choices") or []
    if not choices:
        return None
    return choices[0].get("message") or {}


async def execute_agent(agent_config: dict, query: str) -> dict:
    """
    Run an agent against a user query. Emits progress events via the
    ContextVar-bound SSE emitter. Returns:

        {
          "agent": name,
          "result": final_text,
          "tool_calls": total_tool_call_count,
          "duration_seconds": int,
          "stopped_reason": "done"|"max_iterations"|"max_tool_calls"|"timeout"|"error",
        }
    """
    name = agent_config["name"]
    tool_schema = _tools_openai_schema(agent_config["tools"])
    allowlist = agent_config["tools"]

    messages: list[dict] = [
        {"role": "system", "content": agent_config["system"]},
        {"role": "user", "content": query},
    ]

    await _emit("agent_start", {"agent": name, "query": query})

    started = time.monotonic()
    total_tool_calls = 0
    final_text = ""
    stopped_reason = "done"

    for iteration in range(agent_config["max_iterations"]):
        if time.monotonic() - started > agent_config["timeout_seconds"]:
            stopped_reason = "timeout"
            break

        message = await _call_vllm(messages, tool_schema, purpose="agent_turn")
        if message is None:
            stopped_reason = "error"
            final_text = "Agent failed: vLLM unreachable."
            break

        # vLLM's qwen3 reasoning parser emits `reasoning` (non-streaming)
        # or used to emit `reasoning_content` (older builds). Accept either.
        reasoning = message.get("reasoning") or message.get("reasoning_content")
        if reasoning:
            await _emit("agent_thinking", {"content": reasoning})

        content = message.get("content") or ""
        raw_tool_calls = message.get("tool_calls") or []

        if not raw_tool_calls:
            final_text = content
            stopped_reason = "done"
            break

        normalized = _normalize_tool_calls(raw_tool_calls)

        remaining = agent_config["max_tool_calls"] - total_tool_calls
        if remaining <= 0:
            stopped_reason = "max_tool_calls"
            if content:
                final_text = content
            break
        if len(normalized) > remaining:
            normalized = normalized[:remaining]

        for tc in normalized:
            await _emit(
                "agent_tool_call",
                {"id": tc["id"], "name": tc["name"], "arguments": tc["arguments"]},
            )

        results = await run_tools_parallel(normalized, allowlist)
        total_tool_calls += len(results)

        for res in results:
            await _emit(
                "agent_tool_result",
                {
                    "id": res["id"],
                    "name": res["name"],
                    "result": res["result"],
                    "duration_ms": res["duration_ms"],
                },
            )

        messages.append({
            "role": "assistant",
            "content": content,
            "tool_calls": [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": json.dumps(tc["arguments"]),
                    },
                }
                for tc in normalized
            ],
        })
        for res in results:
            messages.append({
                "role": "tool",
                "tool_call_id": res["id"],
                "content": truncate_tool_result(res["result"]),
            })

        if total_tool_calls >= agent_config["max_tool_calls"]:
            stopped_reason = "max_tool_calls"
            break
    else:
        stopped_reason = "max_iterations"

    # If we bailed out without a final answer, ask vLLM for one without tools.
    if not final_text and stopped_reason in ("max_iterations", "max_tool_calls", "timeout"):
        wrap_up = await _call_vllm(
            messages + [
                {
                    "role": "user",
                    "content": (
                        "You've hit an execution limit. Summarize what you've "
                        "found so far and answer as completely as you can "
                        "without calling any more tools."
                    ),
                }
            ],
            tools=[],
            purpose="agent_wrap_up",
        )
        if wrap_up is not None:
            final_text = wrap_up.get("content") or final_text

    duration_seconds = int(time.monotonic() - started)

    await _emit(
        "agent_done",
        {
            "agent": name,
            "tool_calls": total_tool_calls,
            "duration_seconds": duration_seconds,
            "stopped_reason": stopped_reason,
        },
    )

    return {
        "agent": name,
        "result": final_text or "(agent returned no output)",
        "tool_calls": total_tool_calls,
        "duration_seconds": duration_seconds,
        "stopped_reason": stopped_reason,
    }
