"""OpenAI chat-completions <-> Claude Messages translation. No I/O in here.

Retrieval was written against vLLM, which accepts things the Messages API
rejects with a 400: sampling fields, empty text blocks, tool results whose call
was trimmed from history, tool calls whose result never got saved, a final
assistant turn. Everything needed to make such a request valid lives here, so
retrieval and its roughly eight direct callers need no changes.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections import OrderedDict
from typing import Any, Iterable, Optional

# "on" and "off" are the thinking settings each model accepts. Opus 5.5 cannot
# turn thinking off, and the 8 and 40 token calls would spend their whole budget
# on it, so it is not offered here.
_ADAPTIVE = {"type": "adaptive", "display": "summarized"}
MODEL_PROFILES: dict[str, dict[str, Any]] = {
    "claude-haiku-5-5": {"on": _ADAPTIVE, "off": {"type": "disabled"}, "forced_tools": True},
    "claude-sonnet-5": {"on": _ADAPTIVE, "off": {"type": "disabled"}, "forced_tools": True},
    "claude-sonnet-5-5": {"on": _ADAPTIVE, "off": {"type": "between_tools"}, "forced_tools": False},
}

# Thinking counts toward max_tokens, so a smaller cap could end with no answer.
MIN_TOKENS_FOR_THINKING = 1024

# Anthropic's suggested instruction for tool loops running with thinking off.
TOOL_LOOP_INSTRUCTION = (
    "When you use a tool, you may say a brief sentence first. If no tool can "
    "express what the user asked for, say so instead of guessing. Do not "
    "include internal or system XML tags in your response."
)

_DATA_URL = re.compile(r"^data:(image/[\w.+-]+);base64,(.+)$", re.DOTALL)
_BAD_ID_CHARS = re.compile(r"[^a-zA-Z0-9_-]")


def _text_blocks(content: Any) -> list[dict]:
    """User, assistant or tool content as Claude blocks, minus empty text."""
    if content is None:
        return []
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content.strip() else []
    blocks: list[dict] = []
    for part in content if isinstance(content, list) else []:
        if not isinstance(part, dict):
            continue
        kind = part.get("type")
        if kind == "text":
            text = part.get("text") or ""
            if text.strip():
                blocks.append({"type": "text", "text": text})
        elif kind == "image_url":
            url = (part.get("image_url") or {}).get("url") or ""
            match = _DATA_URL.match(url)
            if match:
                source = {"type": "base64", "media_type": match[1], "data": match[2]}
                blocks.append({"type": "image", "source": source})
            elif url.startswith(("http://", "https://")):
                blocks.append({"type": "image", "source": {"type": "url", "url": url}})
    return blocks


def _plain_text(content: Any) -> str:
    return "\n".join(b["text"] for b in _text_blocks(content) if b["type"] == "text")


def _parse_arguments(raw: Any) -> dict:
    """tool_use.input must be an object; saved failed calls may hold anything."""
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {"raw_arguments": str(raw)}
    return parsed if isinstance(parsed, dict) else {"raw_arguments": parsed}


def convert_messages(messages: Iterable[dict]) -> tuple[list[str], list[dict]]:
    """Return (system texts, Claude messages) that satisfy the API's pairing
    rules: every tool_use is answered in the next user turn, every tool_result
    follows its tool_use, ids are unique, and the conversation starts and ends
    on a user turn."""
    system: list[str] = []
    out: list[dict] = []
    open_ids: list[str] = []  # tool_use ids the next user turn must answer
    results: list[dict] = []  # tool_results collected for that user turn
    aliases: dict[str, str] = {}  # id in the request -> id sent to Claude
    seen: set[str] = set()

    def append(role: str, blocks: list[dict]) -> None:
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": role, "content": list(blocks)})

    def close_tool_round() -> None:
        nonlocal results
        for tool_id in open_ids:
            results.append({
                "type": "tool_result",
                "tool_use_id": tool_id,
                "is_error": True,
                "content": "[no result was recorded for this call]",
            })
        open_ids.clear()
        if results:
            append("user", results)
            results = []

    def unique_id(raw: Any) -> str:
        base = _BAD_ID_CHARS.sub("_", str(raw or "")) or "toolu_missing"
        final, n = base, 1
        while final in seen:
            n += 1
            final = f"{base}_{n}"
        seen.add(final)
        return final

    for message in messages:
        role = message.get("role")
        content = message.get("content")

        if role in ("system", "developer"):
            text = _plain_text(content)
            if text:
                system.append(text)
            continue

        if role == "tool":
            raw_id = message.get("tool_call_id")
            final_id = aliases.get(raw_id)
            if final_id in open_ids:
                open_ids.remove(final_id)
                blocks = _text_blocks(content)
                results.append({
                    "type": "tool_result",
                    "tool_use_id": final_id,
                    "content": blocks or "(no output)",
                })
            else:
                # Its call was trimmed from history. Keep the text, not the pairing.
                close_tool_round()
                text = _plain_text(content)
                if text:
                    append("user", [{"type": "text", "text": f"[earlier tool result]\n{text}"}])
            continue

        close_tool_round()

        if role == "assistant":
            blocks = _text_blocks(content)
            for call in message.get("tool_calls") or []:
                # chat_store saves name and arguments at the top level of the call.
                fn = call.get("function") or call
                raw_id = call.get("id")
                final_id = unique_id(raw_id)
                aliases[raw_id] = final_id
                open_ids.append(final_id)
                blocks.append({
                    "type": "tool_use",
                    "id": final_id,
                    "name": fn.get("name") or "unknown_tool",
                    "input": _parse_arguments(fn.get("arguments")),
                })
            if blocks:
                append("assistant", blocks)
        else:
            append("user", _text_blocks(content) or [{"type": "text", "text": "(empty message)"}])

    close_tool_round()

    if not out or out[0]["role"] != "user":
        out.insert(0, {"role": "user", "content": [{"type": "text", "text": "(conversation continues)"}]})
    if out[-1]["role"] != "user":  # a final assistant turn is a prefill: a 400
        out.append({"role": "user", "content": [{"type": "text", "text": "Continue."}]})
    return system, out


def convert_tools(tools: Optional[list[dict]]) -> list[dict]:
    converted: dict[str, dict] = {}
    for tool in tools or []:
        fn = tool.get("function") or {}
        name = fn.get("name")
        if not name or name in converted:
            continue
        schema = dict(fn.get("parameters") or {})
        schema.setdefault("type", "object")
        entry = {"name": name, "input_schema": schema}
        if fn.get("description"):
            entry["description"] = fn["description"]
        converted[name] = entry
    return list(converted.values())


def convert_tool_choice(choice: Any, parallel: Any, forced_allowed: bool) -> dict:
    if isinstance(choice, dict):
        name = (choice.get("function") or {}).get("name")
        result = {"type": "tool", "name": name} if name else {"type": "auto"}
    elif choice == "required":
        result = {"type": "any"}
    elif choice == "none":
        return {"type": "none"}
    else:
        result = {"type": "auto"}
    if not forced_allowed:
        result = {"type": "auto"}
    if parallel is False:
        result["disable_parallel_tool_use"] = True
    return result


def wants_thinking(body: dict, max_tokens: int) -> bool:
    """Retrieval marks mechanical sub-tasks with enable_thinking=false, so this
    needs LLM_THINKING_TOGGLE=1, which is its default."""
    kwargs = body.get("chat_template_kwargs")
    asked_off = isinstance(kwargs, dict) and kwargs.get("enable_thinking") is False
    return not asked_off and max_tokens >= MIN_TOKENS_FOR_THINKING


def build_request(
    body: dict, *, model: str, effort: str, max_output_tokens: int, thinking: bool = True
) -> dict:
    """Messages API parameters for an OpenAI request. Only fields that map
    across are read, so temperature, top_p, top_k, min_p, presence_penalty and
    chat_template_kwargs never reach the API. `model` comes from the operator
    and never from the request: gateway API-key users choose the request body
    and must not be able to choose a costlier model on these credits."""
    profile = MODEL_PROFILES[model]
    system, messages = convert_messages(body.get("messages") or [])
    tools = convert_tools(body.get("tools"))

    requested = body.get("max_tokens") or body.get("max_completion_tokens") or max_output_tokens
    max_tokens = max(1, min(int(requested), max_output_tokens))
    think = thinking and wants_thinking(body, max_tokens)
    params: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
        "thinking": profile["on"] if think else profile["off"],
        "output_config": {"effort": effort},
    }

    if tools:
        if not think:
            system.append(TOOL_LOOP_INSTRUCTION)
        params["tools"] = tools
        params["tool_choice"] = convert_tool_choice(
            body.get("tool_choice"), body.get("parallel_tool_calls"), profile["forced_tools"]
        )
        # Only tool loops resend their prefix. A one-shot title or summary call
        # would pay the cache-write premium and never read the entry back.
        params["cache_control"] = {"type": "ephemeral"}
        # The tools come first in the prefix and are shared across users, while
        # retrieval's system prompt carries the time of day and changes per turn.
        tools[-1]["cache_control"] = {"type": "ephemeral"}

    if system:
        block: dict[str, Any] = {"type": "text", "text": "\n".join(system)}
        if tools:
            block["cache_control"] = {"type": "ephemeral"}
        params["system"] = [block]

    stop = body.get("stop")
    if stop:
        stops = [stop] if isinstance(stop, str) else list(stop)
        stops = [s for s in stops if isinstance(s, str) and s.strip()]
        if stops:
            params["stop_sequences"] = stops
    return params


# ------------------------------------------------------------------- thinking

def _prefix_hashes(params: dict) -> tuple[dict[int, str], str]:
    """Hash of what precedes each assistant message, and of the whole request.

    A thinking block is bound to its system prompt, tool set and earlier
    messages, and sending it after any of those changed is a 400 on newer
    accounts. These hashes let a block be replayed only when its prefix is
    byte-identical, so retrieval trimming old tool results just costs the block.
    Call this before thinking blocks are attached: they are not part of a prefix.
    """
    tools = sorted(
        ({k: v for k, v in t.items() if k != "cache_control"} for t in params.get("tools", [])),
        key=lambda t: t["name"],
    )
    system = [b["text"] for b in params.get("system", [])]
    digest = hashlib.sha256(json.dumps([params["model"], system, tools], sort_keys=True).encode())
    before: dict[int, str] = {}
    for i, message in enumerate(params["messages"]):
        if message["role"] == "assistant":
            before[i] = digest.hexdigest()
        digest.update(json.dumps(message, sort_keys=True).encode())
    return before, digest.hexdigest()


class ThinkingStore:
    """Thinking blocks by first tool_use id, since the OpenAI message shape has
    nowhere to carry them. Losing an entry only costs that block's reasoning, so
    a small in-memory store is enough, and a restart simply starts it empty."""

    def __init__(self, max_entries: int = 2000):
        self._entries: OrderedDict[str, tuple[str, list[dict]]] = OrderedDict()
        self._max_entries = max_entries

    def put(self, tool_id: str, prefix: str, blocks: list[dict]) -> None:
        self._entries[tool_id] = (prefix, blocks)
        self._entries.move_to_end(tool_id)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)

    def get(self, tool_id: str) -> Optional[tuple[str, list[dict]]]:
        return self._entries.get(tool_id)


def attach_thinking(params: dict, store: ThinkingStore) -> str:
    """Put stored blocks back on the newest assistant turns and return the hash
    to store this request's own response under.

    Walks backwards and stops at the first turn it cannot restore: blocks may be
    missing from the start of the conversation but not from the middle. A round
    that really had no thinking is stored with an empty list, so it does not stop
    the walk. A final text answer has no tool id and always does."""
    before, whole = _prefix_hashes(params)
    if params["thinking"] != _ADAPTIVE:
        return whole
    for i in sorted(before, reverse=True):
        content = params["messages"][i]["content"]
        tool_id = next((b["id"] for b in content if b["type"] == "tool_use"), None)
        entry = store.get(tool_id) if tool_id else None
        if entry is None or entry[0] != before[i]:
            break
        content[:0] = entry[1]
    return whole


def strip_thinking(params: dict) -> None:
    """Anthropic's documented recovery from a rejected block."""
    for message in params["messages"]:
        message["content"] = [
            b for b in message["content"] if b["type"] not in ("thinking", "redacted_thinking")
        ]


def _thinking_block(block: Any) -> Optional[dict]:
    if block.type == "thinking":
        return {"type": "thinking", "thinking": block.thinking, "signature": block.signature}
    if block.type == "redacted_thinking":
        return {"type": "redacted_thinking", "data": block.data}
    return None


def remember_message(message: Any, prefix: str, store: ThinkingStore) -> None:
    """Store a finished message's thinking if it asked for tool results."""
    tool_id = next((b.id for b in message.content if b.type == "tool_use"), None)
    if tool_id:
        blocks = [t for t in map(_thinking_block, message.content) if t]
        store.put(tool_id, prefix, blocks)


# ---------------------------------------------------------------- responses

_FINISH = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "tool_use": "tool_calls",
    "max_tokens": "length",
    "model_context_window_exceeded": "length",
    "refusal": "content_filter",
}


def finish_reason(stop_reason: Optional[str]) -> str:
    return _FINISH.get(stop_reason or "", "stop")


def refusal_notice(stop_details: Any) -> str:
    category = getattr(stop_details, "category", None)
    suffix = f" (category: {category})" if category else ""
    return f"Claude declined to answer this request{suffix}. Try rephrasing it."


def usage_dict(input_tokens: int, output_tokens: int, cache_read: int, cache_write: int) -> dict:
    """prompt_tokens is the whole prompt, as in OpenAI's usage and as the
    gateway's quotas expect. Claude's input_tokens only counts what follows the
    last cache breakpoint, so using it alone would under-report heavy users."""
    prompt = input_tokens + cache_read + cache_write
    return {
        "prompt_tokens": prompt,
        "completion_tokens": output_tokens,
        "total_tokens": prompt + output_tokens,
        "prompt_tokens_details": {"cached_tokens": cache_read},
    }


def _usage_numbers(usage: Any) -> tuple[int, int, int, int]:
    return (
        getattr(usage, "input_tokens", 0) or 0,
        getattr(usage, "output_tokens", 0) or 0,
        getattr(usage, "cache_read_input_tokens", 0) or 0,
        getattr(usage, "cache_creation_input_tokens", 0) or 0,
    )


def completion_from_message(message: Any, model: str) -> dict:
    """A finished Claude message as a non-streaming chat completion."""
    text: list[str] = []
    calls: list[dict] = []
    for block in message.content:
        if block.type == "text":
            text.append(block.text)
        elif block.type == "tool_use":
            calls.append({
                "id": block.id,
                "type": "function",
                "function": {"name": block.name, "arguments": json.dumps(block.input)},
            })
    if message.stop_reason == "refusal":
        text.append(refusal_notice(message.stop_details))
    reply: dict[str, Any] = {"role": "assistant", "content": "".join(text) or None}
    reasoning = "".join(b.thinking for b in message.content if b.type == "thinking")
    if reasoning:
        reply["reasoning"] = reasoning
    if calls:
        reply["tool_calls"] = calls
    return {
        "id": message.id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": reply, "finish_reason": finish_reason(message.stop_reason)}],
        "usage": usage_dict(*_usage_numbers(message.usage)),
    }


class StreamTranslator:
    """Turns Claude stream events into chat.completion.chunk dicts."""

    def __init__(self, model: str, include_usage: bool):
        self.model = model
        self.include_usage = include_usage
        self.created = int(time.time())
        self.id = "chatcmpl-claude"
        self.input = self.output = self.cache_read = self.cache_write = 0
        self.stop_reason: Optional[str] = None
        self.stop_details: Any = None
        self.finished = False
        self._tools = 0
        self._tool_index: dict[int, int] = {}  # content block index -> tool index
        self._tool_has_args: dict[int, bool] = {}
        self.first_tool_id: Optional[str] = None
        self.sent_output = False  # text or a tool call the client cannot unsee
        self.thinking_blocks: list[dict] = []
        self._open_thinking: dict[int, dict] = {}

    def _chunk(self, delta: dict, finish: Optional[str] = None) -> dict:
        return {
            "id": self.id,
            "object": "chat.completion.chunk",
            "created": self.created,
            "model": self.model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }

    def _tool_delta(self, index: int, **fields: Any) -> dict:
        return self._chunk({"tool_calls": [{"index": index, **fields}]})

    def feed(self, event: Any) -> list[dict]:
        kind = event.type
        if kind == "message_start":
            self.id = event.message.id
            self.input, _, self.cache_read, self.cache_write = _usage_numbers(event.message.usage)
            return [self._chunk({"role": "assistant", "content": ""})]

        if kind == "content_block_start" and event.content_block.type == "tool_use":
            index = self._tools
            self._tools += 1
            self._tool_index[event.index] = index
            self._tool_has_args[index] = False
            self.first_tool_id = self.first_tool_id or event.content_block.id
            self.sent_output = True
            return [self._tool_delta(
                index,
                id=event.content_block.id,
                type="function",
                function={"name": event.content_block.name, "arguments": ""},
            )]

        if kind == "content_block_start":
            block = _thinking_block(event.content_block)
            if block:
                self.thinking_blocks.append(block)
                self._open_thinking[event.index] = block
            return []

        if kind == "content_block_delta":
            if event.delta.type == "text_delta":
                self.sent_output = self.sent_output or bool(event.delta.text)
                return [self._chunk({"content": event.delta.text})]
            if event.delta.type == "thinking_delta":
                self._open_thinking[event.index]["thinking"] += event.delta.thinking
                return [self._chunk({"reasoning": event.delta.thinking})]
            if event.delta.type == "signature_delta":
                self._open_thinking[event.index]["signature"] = event.delta.signature
                return []
            if event.delta.type == "input_json_delta" and event.delta.partial_json:
                index = self._tool_index[event.index]
                self._tool_has_args[index] = True
                return [self._tool_delta(index, function={"arguments": event.delta.partial_json})]
            return []

        if kind == "content_block_stop":
            index = self._tool_index.get(event.index)
            if index is not None and not self._tool_has_args[index]:
                return [self._tool_delta(index, function={"arguments": "{}"})]
            return []

        if kind == "message_delta":
            self.stop_reason = event.delta.stop_reason
            self.stop_details = getattr(event.delta, "stop_details", None)
            self.output = getattr(event.usage, "output_tokens", 0) or 0
            return []

        if kind == "message_stop":
            self.finished = True
            chunks = []
            if self.stop_reason == "refusal":
                chunks.append(self._chunk({"content": refusal_notice(self.stop_details)}))
            chunks.append(self._chunk({}, finish_reason(self.stop_reason)))
            if self.include_usage:
                last = self._chunk({})
                last["choices"] = []
                last["usage"] = self.usage()
                chunks.append(last)
            return chunks
        return []

    def usage(self) -> dict:
        return usage_dict(self.input, self.output, self.cache_read, self.cache_write)
