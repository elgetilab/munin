"""
Context assembly + compaction for chat completions.

Responsibilities:
    * Assemble a vLLM-ready message list from a stored conversation, a system
      prompt, optional RAG snippets, and the incoming user message.
    * Keep the total payload inside the model's context window by summarizing
      older messages via vLLM when the budget is exceeded.
    * Generate conversation titles from the first exchange.

Token accounting is approximate (~4 characters per token). This is good enough
for budgeting without pulling in a heavy tokenizer dependency.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from database import VLLM_MODEL_NAME
from chat_store import get_messages_after_index, update_summary
from vllm_client import vllm_post_json, VLLMRequestError
from usage_tracker import record_usage

MAX_CONTEXT = int(os.getenv("VLLM_MAX_CONTEXT", "60000"))
GENERATION_RESERVE = int(os.getenv("VLLM_GENERATION_RESERVE", "8000"))
SUMMARY_TOKEN_BUDGET = 1200

SUMMARIZE_PROMPT = (
    "You are compacting an ongoing conversation so that a language model can "
    "continue it without losing context. Produce a concise third-person "
    "summary that preserves: user intent, stated facts, decisions, named "
    "entities, open questions, and any tool results that influenced the "
    "answer. Keep it under 300 words. Do not add commentary."
)

TITLE_PROMPT = (
    "Generate a 5-8 word title summarizing this conversation. "
    "Return ONLY the title, no quotes or punctuation."
)


def approx_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)


def _content_to_text(content: Any) -> str:
    """
    Flatten a vLLM message ``content`` field (str OR OpenAI-style list of
    typed blocks) into a string for approximate token accounting. The
    image_url blocks themselves cost ~100 vLLM tokens each regardless of
    source, which we can budget for explicitly rather than trying to
    approximate from a data URL length.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                parts.append(block.get("text") or "")
        return "\n".join(p for p in parts if p)
    return ""


def _count_image_blocks(content: Any) -> int:
    if not isinstance(content, list):
        return 0
    return sum(
        1 for block in content
        if isinstance(block, dict) and block.get("type") == "image_url"
    )


# A fixed per-image vLLM token cost. Matches the probe result documented
# in future_features.md §5 (~100 tokens per image regardless of resolution
# because vLLM resizes internally). Budgeted here so multimodal turns
# don't silently overflow the context window.
_PER_IMAGE_TOKENS = 120


def _message_tokens(message: dict) -> int:
    content = message.get("content")
    text_tokens = approx_tokens(_content_to_text(content))
    image_tokens = _count_image_blocks(content) * _PER_IMAGE_TOKENS
    # Add a small framing overhead per message (~4 tokens for role/wrapping).
    return text_tokens + image_tokens + 4


def _augment_with_attachments(message: dict) -> str:
    """
    Return the message content augmented with an inline
    ``[Attachments: ...]`` marker if the stored row has attachments.
    This is how the model discovers the document_ids of past
    attachments so it can reference them via ``view_attachment``
    (§5 re-view). The stored ``messages.content`` column is NOT
    modified - the marker only appears in the assembled context we
    send to vLLM, so FTS search keeps working against the clean text.
    """
    raw = message.get("content") or ""
    if not isinstance(raw, str):
        # Historical rows store text-only content; a non-string is
        # defensive. Fall back to string coercion and skip the marker.
        return str(raw)
    attachments = message.get("attachments") or []
    if not attachments:
        return raw
    parts: list[str] = []
    for att in attachments:
        if not isinstance(att, dict):
            continue
        doc_id = att.get("document_id")
        filename = att.get("filename") or "unknown"
        ctype = att.get("content_type") or "application/octet-stream"
        if doc_id:
            parts.append(f"{doc_id} ({filename}, {ctype})")
    if not parts:
        return raw
    marker = "[Attachments on this message: " + "; ".join(parts) + (
        ". Call view_attachment(document_id=\"...\") to see one again.]"
    )
    return f"{raw}\n\n{marker}" if raw else marker


def _rag_context_to_text(rag_context: Optional[dict]) -> str:
    if not rag_context:
        return ""
    docs = rag_context.get("documents") or []
    if not docs:
        return ""
    lines = ["Relevant context:"]
    for i, d in enumerate(docs, start=1):
        title = d.get("title") or d.get("source") or "doc"
        content = d.get("content") or ""
        lines.append(f"[{i}] {title}: {content}")
    return "\n".join(lines)


async def _call_vllm(
    messages: list[dict], max_tokens: int, *, purpose: str
) -> Optional[str]:
    """
    Call vLLM /v1/chat/completions non-streaming. Returns text or None.

    Qwen3's default reasoning phase would otherwise consume the entire
    token budget for simple tasks like summarisation and title generation,
    returning empty content and silently breaking compaction. We disable
    thinking via chat_template_kwargs — same fix applied to
    query_expansion.py and mcp/tools/llm.py.

    ``purpose`` tags the usage with the call site ("summary" / "title")
    so it shows up under the right bucket in the per-request aggregator.
    """
    try:
        data = await vllm_post_json(
            {
                "model": VLLM_MODEL_NAME,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": 0.3,
                "stream": False,
                "chat_template_kwargs": {"enable_thinking": False},
            },
            timeout=60.0,
            foreground=False,
        )
    except VLLMRequestError:
        return None
    record_usage(purpose, data.get("usage"))
    choices = data.get("choices") or []
    if not choices:
        return None
    content = (choices[0].get("message") or {}).get("content")
    return content if content else None


async def summarize_messages(
    conversation_id: str,
    existing_summary: Optional[str],
    messages_to_summarize: list[dict],
) -> Optional[str]:
    """
    Incrementally update a conversation summary. `messages_to_summarize` is
    the slice of messages that must be folded into the summary. Returns the
    new summary text, or None if vLLM is unreachable.
    """
    if not messages_to_summarize:
        return existing_summary

    transcript_lines: list[str] = []
    for m in messages_to_summarize:
        role = m.get("role", "user")
        content = (m.get("content") or "").strip()
        if not content:
            continue
        transcript_lines.append(f"{role.upper()}: {content}")
    transcript = "\n".join(transcript_lines)

    user_blocks: list[str] = []
    if existing_summary:
        user_blocks.append(f"Existing summary:\n{existing_summary}")
    user_blocks.append(f"New messages to fold in:\n{transcript}")
    user_blocks.append("Return the updated summary.")

    messages = [
        {"role": "system", "content": SUMMARIZE_PROMPT},
        {"role": "user", "content": "\n\n".join(user_blocks)},
    ]
    return await _call_vllm(messages, max_tokens=SUMMARY_TOKEN_BUDGET, purpose="summary")


async def assemble_context(
    conversation: dict,
    new_message: dict,
    system_prompt: str,
    rag_context: Optional[dict] = None,
    ephemeral: bool = False,
) -> list[dict]:
    """
    Build a list of vLLM chat messages for the next completion.

    Flow:
        1. Compute the overall budget.
        2. Start from system prompt + existing summary + recent messages.
        3. If the total fits, return it.
        4. Otherwise, keep only the tail that fits in half the budget,
           summarize the dropped prefix via vLLM, persist the summary, and
           rebuild.

    When ``ephemeral`` is True, compaction still runs (long ephemeral threads
    must fit in the context window) but the resulting summary is NOT written
    back to the database, since no row exists for the conversation.
    """
    conversation_id = conversation["id"]

    system_parts: list[str] = [system_prompt]
    rag_text = _rag_context_to_text(rag_context)
    if rag_text:
        system_parts.append(rag_text)
    if conversation.get("summary"):
        system_parts.append(
            "Summary of earlier conversation:\n" + conversation["summary"]
        )
    combined_system = "\n\n".join(p for p in system_parts if p)

    stored_messages: list[dict] = conversation.get("messages") or []

    new_content = new_message.get("content", "")
    new_msg = {"role": new_message.get("role", "user"), "content": new_content}

    budget = MAX_CONTEXT - GENERATION_RESERVE
    system_tokens = approx_tokens(combined_system) + 4
    new_tokens = _message_tokens(new_msg)
    history_budget = budget - system_tokens - new_tokens

    def build(history: list[dict]) -> list[dict]:
        return [
            {"role": "system", "content": combined_system},
            *[
                {"role": m["role"], "content": _augment_with_attachments(m)}
                for m in history
            ],
            new_msg,
        ]

    history_tokens = sum(_message_tokens(m) for m in stored_messages)
    if history_tokens <= history_budget:
        return build(stored_messages)

    # Need compaction: keep newest messages that fit in half the history
    # budget; summarize everything older.
    tail_budget = max(1000, history_budget // 2)
    kept: list[dict] = []
    running = 0
    for m in reversed(stored_messages):
        t = _message_tokens(m)
        if running + t > tail_budget:
            break
        kept.append(m)
        running += t
    kept.reverse()

    drop_count = len(stored_messages) - len(kept)
    if drop_count <= 0:
        return build(stored_messages)

    dropped = stored_messages[:drop_count]
    last_dropped_index = dropped[-1].get(
        "index_in_conversation", drop_count - 1
    )

    existing_summary = conversation.get("summary")
    already_through = conversation.get("summary_through_index")
    # Only summarize messages not already covered by the previous summary.
    if already_through is not None:
        dropped = [
            m for m in dropped
            if m.get("index_in_conversation", -1) > already_through
        ]

    new_summary = await summarize_messages(
        conversation_id, existing_summary, dropped
    )

    if new_summary:
        if not ephemeral:
            await update_summary(conversation_id, new_summary, last_dropped_index)
        conversation["summary"] = new_summary
        conversation["summary_through_index"] = last_dropped_index

        rebuilt_system_parts = [system_prompt]
        if rag_text:
            rebuilt_system_parts.append(rag_text)
        rebuilt_system_parts.append(
            "Summary of earlier conversation:\n" + new_summary
        )
        combined_system = "\n\n".join(rebuilt_system_parts)
        return [
            {"role": "system", "content": combined_system},
            *[
                {"role": m["role"], "content": _augment_with_attachments(m)}
                for m in kept
            ],
            new_msg,
        ]

    # vLLM unreachable — fall back to hard truncation.
    return build(kept)


async def generate_title(first_user_message: str, first_assistant_response: str) -> str:
    """
    Generate a short title for a new conversation. Uses the first user message
    directly if it's short, otherwise asks vLLM.
    """
    trimmed = (first_user_message or "").strip()
    if trimmed and len(trimmed) <= 60:
        return trimmed

    prompt = (
        f"User: {trimmed}\n\n"
        f"Assistant: {(first_assistant_response or '').strip()[:1200]}"
    )
    messages = [
        {"role": "system", "content": TITLE_PROMPT},
        {"role": "user", "content": prompt},
    ]
    title = await _call_vllm(messages, max_tokens=40, purpose="title")
    if not title:
        return trimmed[:60] if trimmed else "New conversation"
    return title.strip().strip('"').strip("'")[:80]
