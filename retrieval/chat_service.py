"""
Streaming chat completions for /api/chat/completions.

This module wraps vLLM's OpenAI-compatible streaming API and produces the
Server-Sent Events that the Munin frontend expects. It handles:

* Persona system prompt injection and sampling parameter propagation.
* Optional RAG retrieval across configured sources (executed in parallel).
* vLLM SSE parsing for `delta.content`, `delta.reasoning_content`, and
  `delta.tool_calls` (the qwen3 reasoning parser / qwen3_coder tool parser).
* Mid-stream tool execution via the existing MCP executor. Tool calls from
  a single assistant turn are executed in parallel via `asyncio.gather`.
* Persistence of the final assistant message (content, thinking, tool calls,
  rag_context) via `chat_store`.
* Auto-generation of a conversation title after the first assistant response.

SSE payloads are yielded as `{"event": name, "data": json}` dicts compatible
with `sse_starlette.EventSourceResponse`.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime
from typing import Any, AsyncIterator, Optional

import httpx

import chat_store
import chat_context
import personas as persona_module
import agents as agents_pkg
import user_profile_store
from database import VLLM_URL, VLLM_MODEL_NAME
from mcp.schemas import MCP_TOOLS
from mcp.executor import execute_mcp_tool
from mcp.context import (
    current_user_email,
    current_conversation_id,
    current_sse_emitter,
)


# --- SSE helpers --------------------------------------------------------------

def _sse(event: str, payload: dict) -> dict:
    return {"event": event, "data": json.dumps(payload)}


def _error_sse(message: str) -> dict:
    return _sse("error", {"message": message})


# --- vLLM tool-call converters ------------------------------------------------

def _openai_tools_schema() -> list[dict]:
    """Translate MCP_TOOLS into the OpenAI `tools` array vLLM expects."""
    tools: list[dict] = []
    for name, spec in MCP_TOOLS.items():
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


# --- vLLM streaming -----------------------------------------------------------

class _StreamAccumulator:
    """Accumulates deltas from a single vLLM streaming completion."""

    def __init__(self) -> None:
        self.content_parts: list[str] = []
        self.thinking_parts: list[str] = []
        # tool calls indexed by their streaming `index` field
        self.tool_calls: dict[int, dict] = {}
        self.finish_reason: Optional[str] = None
        self.usage: Optional[dict] = None

    @property
    def content(self) -> str:
        return "".join(self.content_parts)

    @property
    def thinking(self) -> str:
        return "".join(self.thinking_parts)

    def finalized_tool_calls(self) -> list[dict]:
        out: list[dict] = []
        for idx in sorted(self.tool_calls.keys()):
            tc = self.tool_calls[idx]
            out.append({
                "id": tc.get("id") or f"tc-{idx}",
                "name": tc.get("name") or "",
                "arguments": _parse_arguments(tc.get("arguments_raw")),
            })
        return out


async def _stream_vllm_once(
    messages: list[dict],
    sampling: dict,
    enable_tools: bool,
) -> AsyncIterator[tuple[str, dict, _StreamAccumulator]]:
    """
    Make one streaming call to vLLM and yield (event_name, payload, acc)
    tuples. The accumulator is the same object across all yields so the
    caller can read the finalized state once the iterator finishes.
    """
    acc = _StreamAccumulator()

    body: dict[str, Any] = {
        "model": VLLM_MODEL_NAME,
        "messages": messages,
        "stream": True,
    }
    body.update(sampling)
    if enable_tools:
        body["tools"] = _openai_tools_schema()
        body["tool_choice"] = "auto"

    try:
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "POST",
                f"{VLLM_URL}/v1/chat/completions",
                json=body,
                headers={"Accept": "text/event-stream"},
            ) as response:
                if response.status_code != 200:
                    text = await response.aread()
                    yield (
                        "error",
                        {"message": f"vLLM returned {response.status_code}: {text.decode(errors='ignore')[:300]}"},
                        acc,
                    )
                    return

                emitted_tool_ids: set[int] = set()

                async for line in response.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        chunk = json.loads(payload)
                    except json.JSONDecodeError:
                        continue

                    if chunk.get("usage"):
                        acc.usage = chunk["usage"]

                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    choice = choices[0]
                    delta = choice.get("delta") or {}

                    # vLLM's qwen3 reasoning parser emits reasoning on
                    # `delta.reasoning` (NOT `delta.reasoning_content`).
                    # Accept both names so we survive a future vLLM rename.
                    reasoning = delta.get("reasoning") or delta.get("reasoning_content")
                    if reasoning:
                        acc.thinking_parts.append(reasoning)
                        yield ("thinking", {"content": reasoning}, acc)

                    content = delta.get("content")
                    if content:
                        acc.content_parts.append(content)
                        yield ("token", {"content": content}, acc)

                    for delta_tc in (delta.get("tool_calls") or []):
                        idx = delta_tc.get("index", 0)
                        slot = acc.tool_calls.setdefault(idx, {})
                        if delta_tc.get("id"):
                            slot["id"] = delta_tc["id"]
                        fn = delta_tc.get("function") or {}
                        if fn.get("name"):
                            slot["name"] = fn["name"]
                        if "arguments" in fn:
                            slot["arguments_raw"] = (
                                slot.get("arguments_raw", "") + (fn.get("arguments") or "")
                            )

                    finish = choice.get("finish_reason")
                    if finish:
                        acc.finish_reason = finish

                # Emit a tool_call SSE for each finalized tool call — we
                # delay this until the stream ends so arguments are complete.
                for tc in acc.finalized_tool_calls():
                    tc_id = id(tc)
                    if tc_id in emitted_tool_ids:
                        continue
                    emitted_tool_ids.add(tc_id)
                    yield (
                        "tool_call",
                        {"id": tc["id"], "name": tc["name"], "arguments": tc["arguments"]},
                        acc,
                    )

    except Exception as e:
        yield ("error", {"message": f"vLLM streaming failed: {e}"}, acc)


# --- Tool execution -----------------------------------------------------------

async def _run_tool_calls(tool_calls: list[dict]) -> list[dict]:
    """Execute all tool calls in parallel, preserving order."""
    async def one(tc: dict) -> dict:
        started = time.monotonic()
        try:
            result = await execute_mcp_tool(tc["name"], tc["arguments"])
        except Exception as e:
            result = {"error": f"tool execution failed: {e}"}
        duration_ms = int((time.monotonic() - started) * 1000)
        return {
            "id": tc["id"],
            "name": tc["name"],
            "result": result,
            "duration_ms": duration_ms,
        }

    return await asyncio.gather(*(one(tc) for tc in tool_calls))


# --- Main entry point ---------------------------------------------------------

async def stream_chat_completion(
    user_email: str,
    persona_id: str,
    conversation_id: Optional[str],
    user_message: dict,
    rag_config: Optional[dict],
    ephemeral: bool = False,
    prior_messages: Optional[list[dict]] = None,
) -> AsyncIterator[dict]:
    """
    Orchestrate a single /api/chat/completions request. Yields SSE events.

    When ``ephemeral`` is True, no rows are written to ``chats.db``: the
    conversation is synthesised in-memory with an ``ephemeral-`` id, history
    comes from ``prior_messages`` (frontend echoes the full thread on each
    turn), and persistence/title/summary side effects are all skipped. The
    model still has full access to tools — "ephemeral" means not stored by
    Munin, not untrackable by the world.
    """
    persona = persona_module.get_persona(persona_id)
    if persona is None:
        yield _error_sse(f"Unknown persona: {persona_id}")
        return

    # Bind per-request context for MCP tool dispatch (e.g. search_user_docs).
    current_user_email.set(user_email)
    current_conversation_id.set(conversation_id)

    system_prompt = persona_module.build_system_prompt(persona)

    # Inject the user profile (§25). Profile is user-curated and goes at the
    # very top of the system prompt so the model sees it before persona
    # instructions, ambient context, and agent hints. Skipped for ephemeral
    # chats so privacy-mode requests don't quietly carry user-identifying
    # preferences into the model. If the user has no profile (or only empty
    # fields) build_profile_block returns None and nothing is prepended.
    if not ephemeral:
        try:
            profile = await user_profile_store.get_profile(user_email)
            profile_block = user_profile_store.build_profile_block(profile)
        except Exception as e:
            print(f"[WARNING] profile load failed: {e}")
            profile_block = None
        if profile_block:
            system_prompt = (
                f"{profile_block}\n\n{system_prompt}"
                if system_prompt
                else profile_block
            )

    # Inject an ambient-context block so the model doesn't waste a tool call
    # on trivia it should just know (today's date, etc.). Placed before the
    # agent summaries so the persona prompt still leads.
    now_local = datetime.now().astimezone()
    ambient = (
        f"Current date: {now_local.strftime('%A, %B %d, %Y')} "
        f"({now_local.strftime('%Y-%m-%d %H:%M %Z')}). "
        "Use this directly — do not search the web for the date."
    )
    system_prompt = (
        f"{system_prompt}\n\n{ambient}" if system_prompt else ambient
    )

    agent_hint = agents_pkg.agent_summaries_for_prompt()
    if agent_hint:
        system_prompt = f"{system_prompt}\n\n{agent_hint}"
    sampling = persona_module.sampling_params(persona)

    # --- 1. Resolve the conversation ---
    is_new = False
    conversation: Optional[dict]
    if ephemeral:
        # Synthesise an in-memory conversation. History comes from the
        # request body (frontend echoes prior turns) since nothing is stored.
        synthetic_history: list[dict] = []
        for i, m in enumerate(prior_messages or []):
            if not isinstance(m, dict):
                continue
            role = m.get("role")
            content = m.get("content")
            if role not in ("user", "assistant") or not isinstance(content, str):
                continue
            synthetic_history.append({
                "role": role,
                "content": content,
                "index_in_conversation": i,
            })
        conversation = {
            "id": f"ephemeral-{uuid.uuid4().hex[:12]}",
            "user_email": user_email,
            "persona_id": persona_id,
            "title": None,
            "summary": None,
            "summary_through_index": None,
            "messages": synthetic_history,
        }
        is_new = True
    elif conversation_id:
        conversation = await chat_store.get_conversation(conversation_id, user_email)
        if conversation is None:
            yield _error_sse("Conversation not found")
            return
    else:
        created = await chat_store.create_conversation(user_email, persona_id, title=None)
        conversation = await chat_store.get_conversation(created["id"], user_email)
        is_new = True

    assert conversation is not None
    # Now that we know the concrete id, re-bind the MCP context var.
    current_conversation_id.set(conversation["id"])
    yield _sse(
        "conversation",
        {
            "id": conversation["id"],
            "title": conversation.get("title"),
            "is_new": is_new,
            "ephemeral": ephemeral,
        },
    )

    # --- 2. RAG is model-driven ---
    # The main model has paper_search / semantic_scholar_search / web_search /
    # search_user_docs as MCP tools and picks them itself. We no longer run
    # a deterministic paper_search before the first turn — that forced every
    # message to hit the paper corpus even for "what day is it today" style
    # prompts and surprised the user with irrelevant RAG context. The
    # `rag_config` argument is accepted for backwards-compat but ignored.
    _ = rag_config  # kept in signature for API stability
    rag_context: Optional[dict] = None

    # --- 3. Persist the user message ---
    if not ephemeral:
        await chat_store.add_message(
            conversation_id=conversation["id"],
            role="user",
            content=user_message.get("content", ""),
        )
        # Reload so the new message is part of the context assembly.
        conversation = await chat_store.get_conversation(conversation["id"], user_email)
        assert conversation is not None
        # The user message is already persisted and included in
        # conversation.messages. Pop it back off so assemble_context doesn't
        # double-count it.
        persisted_user = conversation["messages"].pop() if conversation["messages"] else None
        new_msg = {
            "role": "user",
            "content": (persisted_user or user_message).get("content", ""),
        }
    else:
        # Ephemeral: history is already in conversation["messages"] from the
        # synthetic build above; nothing to persist or reload.
        new_msg = {
            "role": "user",
            "content": user_message.get("content", ""),
        }

    messages = await chat_context.assemble_context(
        conversation=conversation,
        new_message=new_msg,
        system_prompt=system_prompt,
        rag_context=rag_context,
        ephemeral=ephemeral,
    )

    # --- 4. Streaming loop with tool execution ---
    final_content = ""
    final_thinking = ""
    final_tool_calls: list[dict] = []
    final_usage: Optional[dict] = None
    finish_reason: Optional[str] = None

    MAX_TURNS = 10
    hit_turn_cap = True  # assume exhaustion unless we break cleanly below
    for turn in range(MAX_TURNS):
        acc: Optional[_StreamAccumulator] = None
        stream_error: Optional[str] = None

        async for event_name, payload, accumulator in _stream_vllm_once(
            messages=messages, sampling=sampling, enable_tools=True
        ):
            acc = accumulator
            if event_name == "error":
                stream_error = payload.get("message")
                yield _sse("error", payload)
                continue
            yield _sse(event_name, payload)

        if stream_error:
            return
        if acc is None:
            yield _error_sse("vLLM produced no output")
            return

        final_thinking += acc.thinking
        final_content += acc.content
        if acc.usage:
            final_usage = acc.usage
        finish_reason = acc.finish_reason

        tool_calls = acc.finalized_tool_calls()
        if not tool_calls:
            hit_turn_cap = False
            break

        # Execute all tool calls for this turn in parallel, with a side
        # channel queue so nested agent events can stream to the client
        # while the tools are still running.
        event_queue: asyncio.Queue = asyncio.Queue()
        SENTINEL = object()

        def _push(event_name: str, data: dict) -> None:
            event_queue.put_nowait(_sse(event_name, data))

        current_sse_emitter.set(_push)

        async def _runner() -> list[dict]:
            try:
                return await _run_tool_calls(tool_calls)
            finally:
                await event_queue.put(SENTINEL)

        run_task = asyncio.create_task(_runner())

        while True:
            item = await event_queue.get()
            if item is SENTINEL:
                break
            yield item

        results = await run_task
        current_sse_emitter.set(None)

        for res in results:
            final_tool_calls.append({
                "id": res["id"],
                "name": res["name"],
                "arguments": next(
                    (tc["arguments"] for tc in tool_calls if tc["id"] == res["id"]), {}
                ),
                "result": res["result"],
                "duration_ms": res["duration_ms"],
            })
            yield _sse("tool_result", res)

            # If this was a run_python call that produced artifacts (plots,
            # files), surface them as separate `artifact` SSE events so the
            # frontend can render them inline at the right place in the
            # transcript instead of digging them out of the tool_result blob.
            if res.get("name") == "run_python":
                tool_result = res.get("result") or {}
                for art in (tool_result.get("artifacts") or []):
                    yield _sse(
                        "artifact",
                        {
                            "id": art.get("id"),
                            "filename": art.get("filename"),
                            "content_type": art.get("content_type"),
                            "size_bytes": art.get("size_bytes"),
                            "display_url": art.get("display_url"),
                            "conversation_id": tool_result.get("conversation_id"),
                            "tool_call_id": res["id"],
                        },
                    )

        # Append the assistant turn (with tool_calls) and each tool result to
        # the message list so vLLM can continue generating.
        messages.append({
            "role": "assistant",
            "content": acc.content or "",
            "tool_calls": [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": json.dumps(tc["arguments"]),
                    },
                }
                for tc in tool_calls
            ],
        })
        for res in results:
            messages.append({
                "role": "tool",
                "tool_call_id": res["id"],
                "content": json.dumps(res["result"])[:8000],
            })

    # --- 4b. Wrap-up: force a final synthesis if the loop exhausted its
    # turn budget, OR the last turn produced no real content. The empty-last-
    # turn case is important: the model sometimes emits only a "let me look
    # up X..." preamble on turn N, then stalls with a zero-content turn
    # N+1 (no tool_calls, no text), which naturally breaks the loop. We
    # must detect that and force a synthesis — checking `final_content`
    # (cumulative) would miss it because the preamble already populated
    # final_content on turn N. Mirrors agents/executor.py::execute_agent.
    last_turn_content = (acc.content if acc is not None else "").strip()
    if hit_turn_cap or not last_turn_content:
        wrap_up_messages = list(messages) + [
            {
                "role": "user",
                "content": (
                    "You have reached your tool-use budget. Based on the "
                    "tool results you have gathered so far, write your "
                    "final answer to the user now. Do not call any more "
                    "tools. Be specific and cite sources (DOIs, URLs) from "
                    "the tool results where possible."
                ),
            }
        ]

        wrap_acc: Optional[_StreamAccumulator] = None
        async for event_name, payload, accumulator in _stream_vllm_once(
            messages=wrap_up_messages, sampling=sampling, enable_tools=False
        ):
            wrap_acc = accumulator
            if event_name == "error":
                yield _sse("error", payload)
                continue
            # `tool_call` frames can't happen here because enable_tools=False,
            # but we pass everything else (thinking/token) straight through.
            yield _sse(event_name, payload)

        if wrap_acc is not None:
            final_thinking += wrap_acc.thinking
            final_content += wrap_acc.content
            if wrap_acc.usage:
                final_usage = wrap_acc.usage
            finish_reason = wrap_acc.finish_reason or finish_reason

    # --- 5. Persist assistant message ---
    if not ephemeral:
        await chat_store.add_message(
            conversation_id=conversation["id"],
            role="assistant",
            content=final_content,
            thinking=final_thinking or None,
            tool_calls=final_tool_calls or None,
            rag_context=rag_context,
        )

    # --- 6. Auto-title on brand-new conversations ---
    if not ephemeral and is_new and not conversation.get("title"):
        try:
            title = await chat_context.generate_title(
                user_message.get("content", ""), final_content
            )
            if title:
                await chat_store.update_conversation(
                    conversation_id=conversation["id"],
                    user_email=user_email,
                    title=title,
                )
                yield _sse(
                    "conversation",
                    {"id": conversation["id"], "title": title, "is_new": False},
                )
        except Exception as e:
            print(f"[WARNING] Auto-title failed: {e}")

    # --- 7. Done ---
    yield _sse(
        "done",
        {
            "usage": final_usage or {},
            "finish_reason": finish_reason or "stop",
        },
    )
