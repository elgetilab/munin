"""
Context assembly + compaction for chat completions.

Responsibilities:
    * Assemble a vLLM-ready message list from a stored conversation, a system
      prompt, optional RAG snippets, and the incoming user message.
    * Keep the total payload inside the model's context window by summarizing
      older messages via vLLM when the budget is exceeded.
    * Generate conversation titles from the first exchange.

Token accounting uses the real Qwen3 tokenizer when its `tokenizer.json`
is mounted (see `approx_tokens`); it falls back to a ~4-chars-per-token
heuristic when the file isn't reachable. The heuristic alone undercounts
code / LaTeX / JSON by 1.5-2x, which used to let oversized prompts slip
past the budget and hit vLLM as a hard error (P1 #8).
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from database import VLLM_MODEL_NAME
import asyncio

from chat_store import get_conversation, get_messages_after_index, update_summary
from vllm_client import vllm_post_json, VLLMRequestError
from usage_tracker import record_usage

logger = logging.getLogger(__name__)

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


# Path to the Qwen3 tokenizer.json. Option B (see DECISIONS.md
# 2026-05-21): deploy.sh copies just the tokenizer files out of the
# vLLM model dir into a small dedicated dir, mounted read-only into the
# retrieval container — the container never needs the 19 GB of weights.
QWEN_TOKENIZER_PATH = os.getenv(
    "QWEN_TOKENIZER_PATH", "/models/qwen/tokenizer.json"
)

# Lazily-loaded HuggingFace tokenizer. `_tokenizer_tried` ensures one
# load attempt per process: a missing file (e.g. the vLLM model hasn't
# downloaded yet, or a misconfigured deploy) degrades to the char
# heuristic without re-attempting the load on every call.
_tokenizer = None
_tokenizer_tried = False


def _get_tokenizer():
    """Return the loaded Qwen tokenizer, or None if it isn't available.
    First call attempts the load; subsequent calls are cached."""
    global _tokenizer, _tokenizer_tried
    if _tokenizer_tried:
        return _tokenizer
    _tokenizer_tried = True
    try:
        from tokenizers import Tokenizer

        _tokenizer = Tokenizer.from_file(QWEN_TOKENIZER_PATH)
        logger.info("Qwen tokenizer loaded from %s", QWEN_TOKENIZER_PATH)
    except Exception as e:
        logger.warning(
            "Qwen tokenizer unavailable (%s); token budgeting falls back "
            "to the ~4-chars-per-token heuristic, which undercounts code.",
            e,
        )
        _tokenizer = None
    return _tokenizer


def approx_tokens(text: str) -> int:
    """Token count for ``text``.

    Exact when the Qwen3 tokenizer is mounted; otherwise a ~4-chars-per-
    token heuristic. The name stays "approx" because callers add their
    own message-framing overhead (see ``_message_tokens``), so the total
    budget figure is still an estimate either way.
    """
    if not text:
        return 0
    tok = _get_tokenizer()
    if tok is not None:
        return max(1, len(tok.encode(text).ids))
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
            purpose=purpose,
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
) -> tuple[list[dict], Optional[dict]]:
    """
    Build a list of vLLM chat messages for the next completion.

    Returns a tuple ``(messages, compact_info)``. ``compact_info`` is
    ``None`` when no compaction was needed; otherwise a dict shaped::

        {
          "summary_through_index": int,   # index of last folded-in message
          "dropped_messages": int,        # how many turns the model no longer sees
          "summary": str,                 # the actual summary text
          "is_fresh": bool                # True if a new vLLM call ran this turn
        }

    Callers (chat_service today) yield a ``compact_boundary`` SSE event
    from this dict so the frontend can render a visible "earlier N
    messages summarised" divider in the message list.

    Flow:
        1. Compute the overall budget.
        2. Start from system prompt + existing summary + recent messages.
        3. If the total fits, return it.
        4. Otherwise, keep only the tail that fits in half the budget,
           summarize the dropped prefix via vLLM, persist the summary,
           and rebuild.

    The vLLM call in step 4 used to be on the critical path of every
    overflowing turn (~1-3s). P2 #22 mitigates this with an
    opportunistic prefetch (``_maybe_precompact``) that chat_service
    schedules as a background task at end-of-turn — by the time the
    user types the next message, ``conversation["summary"]`` is
    already populated, ``summary_through_index`` already covers the
    dropped range, and step 4 short-circuits via the
    "already covered" filter at ``already_through``.

    When ``ephemeral`` is True, compaction still runs (long ephemeral
    threads must fit in the context window) but the resulting summary
    is NOT written back to the database, since no row exists for the
    conversation.
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
        return build(stored_messages), None

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
        return build(stored_messages), None

    dropped = stored_messages[:drop_count]
    last_dropped_index = dropped[-1].get(
        "index_in_conversation", drop_count - 1
    )

    existing_summary = conversation.get("summary")
    already_through = conversation.get("summary_through_index")
    # Only summarize messages not already covered by the previous summary.
    if already_through is not None:
        dropped_for_summary = [
            m for m in dropped
            if m.get("index_in_conversation", -1) > already_through
        ]
    else:
        dropped_for_summary = list(dropped)

    # If the prefetched summary already covers everything we'd drop,
    # summarize_messages short-circuits and returns existing_summary
    # without a vLLM call. The is_fresh flag tracks whether THIS turn
    # actually paid the latency cost.
    is_fresh_summary = bool(dropped_for_summary)
    new_summary = await summarize_messages(
        conversation_id, existing_summary, dropped_for_summary
    )

    if new_summary:
        if not ephemeral and is_fresh_summary:
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
        messages = [
            {"role": "system", "content": combined_system},
            *[
                {"role": m["role"], "content": _augment_with_attachments(m)}
                for m in kept
            ],
            new_msg,
        ]
        compact_info = {
            "summary_through_index": last_dropped_index,
            "dropped_messages": drop_count,
            "summary": new_summary,
            "is_fresh": is_fresh_summary,
        }
        return messages, compact_info

    # vLLM unreachable — fall back to hard truncation. No summary to
    # show the user, so no compact_boundary event either.
    return build(kept), None


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


# ---------------------------------------------------------------------------
# Opportunistic compaction (P2 #22)
# ---------------------------------------------------------------------------

# Fire summarization in the background at the end of a turn IF the
# conversation's history is at or above this fraction of the history
# budget. 70% leaves enough headroom that the next turn's user message
# + RAG context can usually fit without forcing an extra compaction.
# Tune-once constant; below the threshold we save vLLM tokens that
# would be overwritten next turn anyway.
PRECOMPACT_THRESHOLD = 0.70

# Per-conversation lock so two consecutive turns don't both fire the
# prefetch. Module-level set; the retrieval container is single-
# process and asyncio is single-threaded, so a plain set is safe
# without a lock primitive.
_precompact_inflight: set[str] = set()


async def _maybe_precompact(conversation_id: str, user_email: str) -> None:
    """Background task: pre-generate the summary for a conversation
    that's near the budget, so the user's next turn doesn't pay the
    sync vLLM cost.

    Failures (vLLM unreachable, DB error, race) are logged and
    swallowed. The next user turn's sync path will simply do the
    summarization itself if this background pass didn't complete.

    Called from ``chat_service.stream_chat_completion`` as
    ``asyncio.create_task(_maybe_precompact(...))`` after the
    ``done`` event has been yielded — out of the user's perceived
    latency."""
    if conversation_id in _precompact_inflight:
        # A previous turn already kicked one off; let it finish.
        return
    _precompact_inflight.add(conversation_id)
    try:
        conversation = await get_conversation(conversation_id, user_email)
        if conversation is None:
            return
        stored_messages: list[dict] = conversation.get("messages") or []
        if not stored_messages:
            return
        # Approximate budget using the same MAX_CONTEXT / GENERATION_RESERVE
        # math as assemble_context. We don't have the next user message
        # in hand here, so use a placeholder reserve of ~500 tokens for
        # the next turn's user input on top of GENERATION_RESERVE.
        budget = MAX_CONTEXT - GENERATION_RESERVE - 500
        history_tokens = sum(_message_tokens(m) for m in stored_messages)
        if history_tokens < int(budget * PRECOMPACT_THRESHOLD):
            return
        # Same tail-keep logic as assemble_context. Recompute here
        # rather than refactor because the chat_service-side path
        # uses RAG context + system prompt that vary per request.
        tail_budget = max(1000, budget // 2)
        kept_running = 0
        kept_count = 0
        for m in reversed(stored_messages):
            t = _message_tokens(m)
            if kept_running + t > tail_budget:
                break
            kept_running += t
            kept_count += 1
        drop_count = len(stored_messages) - kept_count
        if drop_count <= 0:
            return
        dropped = stored_messages[:drop_count]
        last_dropped_index = dropped[-1].get(
            "index_in_conversation", drop_count - 1
        )
        existing_summary = conversation.get("summary")
        already_through = conversation.get("summary_through_index")
        if already_through is not None:
            dropped = [
                m for m in dropped
                if m.get("index_in_conversation", -1) > already_through
            ]
        if not dropped:
            return  # already covered; nothing to do
        new_summary = await summarize_messages(
            conversation_id, existing_summary, dropped
        )
        if not new_summary:
            return
        # asyncio.shield: a process shutdown landing mid-write would
        # otherwise truncate the summary row. The shield lets the
        # update complete in the background even if our wrapping task
        # gets cancelled.
        await asyncio.shield(
            update_summary(conversation_id, new_summary, last_dropped_index)
        )
        logger.info(
            "precompact: persisted summary for conv %s (through index %d)",
            conversation_id, last_dropped_index,
        )
    except Exception:
        logger.exception(
            "precompact: background summarization failed for conv %s",
            conversation_id,
        )
    finally:
        _precompact_inflight.discard(conversation_id)


# ---------------------------------------------------------------------------
# Test helpers — not part of the public surface.
# ---------------------------------------------------------------------------


def _precompact_inflight_for_tests() -> set[str]:
    return _precompact_inflight
