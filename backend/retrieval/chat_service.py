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
import sys
import contextlib
import json
import logging
import os
import time
import uuid
from datetime import datetime
from typing import Any, AsyncIterator, Optional

import chat_store
import chat_context
import personas as persona_module
import router as router_module
import agents as agents_pkg
import user_profile_store
import project_store
import memory_store
import artifact_store
import plan_store
import capabilities as capabilities_module
import vision
from database import VLLM_MODEL_NAME
from vllm_client import vllm_post_json, vllm_post_stream, VLLMRequestError
from usage_tracker import (
    current_usage_aggregator,
    record_usage,
    aggregate_totals,
)
from metrics import observe_phantom_urls, observe_turn
from tool_result import truncate_tool_result
from mcp.schemas import MCP_TOOLS, CORE_TOOLS
from mcp.executor import execute_mcp_tool, partition_by_concurrency_safety
import hooks as hooks_module
from mcp.tools import clarification as clarification_tool
from mcp.context import (
    current_user_email,
    current_conversation_id,
    current_project_id,
    current_query_tags,
    current_search_urls,
    current_sse_emitter,
    current_persona,
    current_unlocked_tools,
)

logger = logging.getLogger(__name__)



# A3 (persona -> router migration): up-front per-turn router. When true, each
# turn's PROFILE (chat|research|code) is chosen from the query (router.py),
# biased by the pinned persona; the system prompt is composed as
# base[pin] + fragment[routed], with sampling + tools from the routed profile.
# When false (default until soaked), routed == pin, so compose(pin,pin) ==
# the original prompt and behaviour is unchanged. Flip ROUTER_ENABLED=true +
# restart to activate.
ROUTER_ENABLED = os.getenv("ROUTER_ENABLED", "false").strip().lower() in (
    "1", "true", "yes",
)

_router_index_cache = None


def _bge_embed(queries: list):
    """Embed query strings with the BGE general-text encoder (L2-normalised),
    the same model the router's labelled set is embedded with."""
    from database import get_bge
    import numpy as np
    return np.asarray(get_bge().encode(queries, normalize_embeddings=True))


def _get_router_index():
    """Lazy singleton: embed the committed labelled example set once."""
    global _router_index_cache
    if _router_index_cache is None:
        import router as router_module
        _router_index_cache = router_module.RouterIndex.from_file(_bge_embed)
    return _router_index_cache


# --- SSE helpers --------------------------------------------------------------

def _sse(event: str, payload: dict) -> dict:
    return {"event": event, "data": json.dumps(payload)}


# --- Multimodal content resolution (§5) --------------------------------------

async def _resolve_user_content_images(
    content: Any,
    user_email: str,
    conversation_id: str,
    ephemeral: bool,
) -> tuple[Any, list[dict]]:
    """
    Walk an OpenAI-style multimodal user content list, turn every
    ``image_url`` block into a concrete ``data:image/...;base64,...``
    URL, and (for persistent chats) funnel inline images to the
    documents store so they can be re-viewed later.

    Returns ``(resolved_content, attachments_metadata)``. If the input is
    a plain string, both return values are passed through untouched so
    text-only turns cost nothing extra.

    Accepts three image_url shapes:
      * ``data:image/...;base64,<payload>`` - inline bytes
      * ``document:<doc_id>`` - reference to an already-uploaded doc
      * ``http://sandbox:8090/artifacts/<cid>/<aid>`` - passthrough for
        tests that want to reference a sandbox artifact directly (rare)

    On any validation failure, raises ``vision.VisionError`` so the
    caller can surface it as an ``error`` SSE event.
    """
    if not isinstance(content, list):
        return content, []

    import document_store  # lazy to avoid any startup ordering snags

    resolved_blocks: list[dict] = []
    attachments: list[dict] = []
    image_count = 0

    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            resolved_blocks.append(vision.text_block(block.get("text") or ""))
            continue
        if btype != "image_url":
            continue

        image_count += 1
        if image_count > vision.MAX_IMAGES_PER_TURN:
            raise vision.VisionError(
                f"at most {vision.MAX_IMAGES_PER_TURN} image attachments per turn"
            )

        url = (block.get("image_url") or {}).get("url") or ""
        data_url: Optional[str] = None
        attachment_meta: Optional[dict] = None

        if url.startswith("data:image/"):
            image_bytes, subtype = vision.parse_inline_data_url(url)
            if not ephemeral:
                # Funnel to the documents store so the user can reference
                # this attachment later. We upload synchronously (image
                # uploads skip embedding and finish in <100 ms). Naming:
                # use the message index + subtype as the filename so
                # multiple pastes in one turn don't collide.
                filename = f"pasted-{uuid.uuid4().hex[:8]}.{subtype}"
                try:
                    doc = await document_store.upload_document(
                        filename=filename,
                        file_bytes=image_bytes,
                        user_email=user_email,
                        conversation_id=conversation_id,
                    )
                    attachment_meta = {
                        "document_id": doc.get("document_id"),
                        "filename": doc.get("filename"),
                        "content_type": f"image/{subtype}",
                        "source": "inline",
                    }
                except Exception as exc:
                    # Funnel failure should not block the turn. The
                    # image still reaches the model via data URL; it
                    # just won't be persistently referenceable.
                    logger.warning("inline image funnel failed: %s", exc)
            data_url = url  # already a valid data URL

        elif url.startswith("document:"):
            doc_id = url[len("document:"):]
            path = document_store.get_document_file_path(user_email, doc_id)
            if not path:
                raise vision.VisionError(
                    f"document not found: {doc_id!r}"
                )
            image_bytes, subtype = vision.read_user_document_image(path)
            data_url = vision._build_data_url(image_bytes, subtype)
            attachment_meta = {
                "document_id": doc_id,
                "filename": os.path.basename(path),
                "content_type": f"image/{subtype}",
                "source": "document",
            }

        else:
            raise vision.VisionError(
                f"unsupported image_url scheme: {url[:32]!r}"
            )

        if data_url:
            resolved_blocks.append(vision.image_url_block(data_url))
        if attachment_meta:
            attachments.append(attachment_meta)

    return resolved_blocks, attachments


def _error_sse(message: str) -> dict:
    return _sse("error", {"message": message})


# --- vLLM tool-call converters ------------------------------------------------

def _openai_tools_schema(persona: Optional[dict] = None) -> list[dict]:
    """
    Translate MCP_TOOLS into the OpenAI ``tools`` array vLLM expects.

    Deferred-tool model (P1 #7). Only ``CORE_TOOLS`` — plus whatever
    ``tool_search`` has unlocked this request via
    ``current_unlocked_tools`` — are emitted, intersected with the
    persona's allowlist. The allowlist stays the *authorization*
    boundary (enforced in ``_run_tool_calls``); this function only
    decides what is *visible in the schema*.

    Keeping the schema at ~9 tools instead of 39 keeps prefill well
    clear of the ~40K-token hang cliff (see scripts/repro_vllm_hang.py,
    2026-04-28) and trims ~15K tokens off every turn's prompt.
    """
    allow: Optional[list[str]] = None
    if persona is not None:
        allow = persona_module.tool_allowlist(persona)
        if allow is None:
            pid = persona.get("id") if isinstance(persona, dict) else "?"
            global _UNSCOPED_PERSONA_WARNED
            if pid not in _UNSCOPED_PERSONA_WARNED:
                logger.warning(
                    "persona %r has no params.tool_allowlist; tool_search "
                    "will surface the full %d-tool registry rather than a "
                    "scoped subset.",
                    pid,
                    len(MCP_TOOLS),
                )
                _UNSCOPED_PERSONA_WARNED.add(pid)

    # The persona allowlist is the tool universe; None = the full
    # registry (legacy personas). What ships in the schema is the core
    # set plus tool_search-unlocked tools, clamped to that universe.
    universe: set[str] = (
        set(allow) if allow is not None else set(MCP_TOOLS.keys())
    )
    unlocked = current_unlocked_tools.get() or set()
    visible = (CORE_TOOLS | unlocked) & universe

    tools: list[dict] = []
    for name, spec in MCP_TOOLS.items():  # registry order for stable output
        if name not in visible:
            continue
        params = spec.get("inputSchema", {"type": "object"})
        tools.append({
            "type": "function",
            "function": {
                "name": name,
                "description": spec.get("description", ""),
                "parameters": params,
            },
        })
    return tools


# Tracks personas we've already warned about in this process. Reset
# on module reload (e.g. after a deploy). Module-level so the
# warning fires at most once per persona id per process.
_UNSCOPED_PERSONA_WARNED: set = set()


def _parse_arguments(raw: Any) -> dict:
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {"_raw": str(raw)}


# --- §14 prose-clarification fallback ----------------------------------------

# Phrases that strongly indicate the model wrote a clarification response as
# prose instead of calling the ``ask_clarification`` tool. qwen3-coder has a
# baked-in bias against using tools for conversational clarifications, so even
# with the strongest persona-level instructions it sometimes emits text that
# reads like a clarification card ("Could you clarify...", "What I understood:
# ...", bulleted question list). When that happens we detect it post-hoc and
# re-run the first turn with ``tool_choice`` forced to ``ask_clarification``.
_PROSE_CLARIFICATION_MARKERS = (
    "could you clarify",
    "could you tell me",
    "could you specify",
    "could you provide",
    "could you confirm",
    "let me clarify",
    "let me understand",
    "let me make sure",
    "what i understand",
    "what i understood",
    "i need to clarify",
    "i need more details",
    "i need to know",
    "i need to understand",
    "i need one more",
    "i just need one more",
    "which type of",
    "which kind of",
    "which variant",
    "please clarify",
    "please specify",
    "please tell me",
    "before i proceed",
    "before proceeding",
    "before diving in",
    "before writing",
    "before i write",
    "before i can",
    "one more detail",
    "one more question",
    "one last question",
    "one more thing",
    "to give you the most useful",
    "to provide the most useful",
    "to tailor",
)


# Sequential clarification marker: "Q4: ..." style numbered questions
# are what the model writes when it's mid-Q/A loop and has already
# written an ask_clarification card previously. Any response
# containing this pattern is almost certainly a prose clarification
# attempt the fallback should catch. We accept both "Q4:" and "**Q4:"
# bolded versions.
import re as _re

_NUMBERED_Q_RE = _re.compile(r"(?m)(?:^|\*\*)\s*Q\d+\s*[:.]")


def _count_bulleted_options(stripped: str) -> int:
    """
    Count lines starting with a markdown bullet marker. A response with
    2+ bulleted lines paired with a clarification marker is a strong
    signal the model wrote a multiple-choice card in prose.
    """
    count = 0
    for line in stripped.split("\n"):
        s = line.lstrip()
        if s.startswith(("- ", "* ", "• ")) or (s[:2].isdigit() and s[2:3] in (".", ")")):
            count += 1
    return count


def _looks_like_prose_clarification(content: str) -> bool:
    """
    Return True if ``content`` looks like the model wrote a clarification
    response as prose instead of calling ``ask_clarification``. Length-
    bounded so we don't false-positive on normal long answers that happen
    to include a follow-up question.

    Fires when any of the following holds:

    - ``markers >= 2`` — at least two distinct clarification phrases
      overlap in the same response (e.g. "could you clarify" +
      "to tailor").
    - ``markers >= 1 AND question_marks >= 2`` — one phrase + two or
      more questions lined up.
    - ``markers >= 1 AND bulleted_options >= 2`` — one phrase followed
      by a bullet list of two or more options. Catches sequential
      multi-turn clarifications like "I need one more detail: Q4: ... -
      option A - option B" where the model writes the next round of
      the Q/A loop as prose.
    - ``numbered_Q_markers >= 1 AND bulleted_options >= 2`` — "Q1:"
      / "Q2:" / ... patterns are almost always prose clarification on
      follow-up turns, even when no other marker phrase is present.
    """
    if not content:
        return False
    stripped = content.strip()
    if len(stripped) > 4000 or len(stripped) < 40:
        return False
    low = stripped.lower()
    marker_hits = sum(1 for m in _PROSE_CLARIFICATION_MARKERS if m in low)
    q_count = stripped.count("?")
    bullet_count = _count_bulleted_options(stripped)
    numbered_q_count = len(_NUMBERED_Q_RE.findall(stripped))

    if marker_hits >= 2:
        return True
    if marker_hits >= 1 and q_count >= 2:
        return True
    if marker_hits >= 1 and bullet_count >= 2:
        return True
    if numbered_q_count >= 1 and bullet_count >= 2:
        return True
    return False


# --- prose-only action promise detection -------------------------------------
#
# Regression for chat cbb006ff (2026-04-27) and the broader pattern surfaced
# by scripts/flakiness-suite.py latex_compile multi-turn variants: on follow-
# up turns ("change the colour theme", "make it 16:9"), qwen3 sometimes
# writes a short future-tense announcement of intent ("I'll update the
# Beamer presentation to use 16:9 format and compile it for you.") and then
# stops without emitting any tool call. The model's thinking trace in the
# original chat explicitly says "I need to call compile_latex" — so this is
# a tool-emission failure, not a reasoning failure. The recovery hook in
# the streaming loop calls `_force_required_tool_retry` whenever this
# heuristic fires and the streaming pass produced no tool calls.
#
# Pattern: a short response opening with future-tense self-reference
# ("I'll", "Let me", "I will", "I'm going to") followed by an action verb
# the model has tools for (compile, recompile, update, run, fix, create,
# write, generate, regenerate). Bounded length so we don't false-positive
# on legitimate long answers that happen to include "I'll explain..."
# alongside the actual answer.
_PROSE_ACTION_OPENERS = (
    "i'll ",
    "i will ",
    "let me ",
    "i'm going to ",
    "i am going to ",
    "i'll go ahead and ",
    "i'll now ",
    "now i'll ",
    "now let me ",
    "let me now ",
)

# Action verbs that imply a tool call should follow. Deliberately narrow:
# verbs like "explain", "describe", "show you", "walk through" are pure
# prose and must NOT trigger recovery.
_PROSE_ACTION_VERBS = (
    "compile",
    "recompile",
    "update",
    "regenerate",
    "rewrite",
    "rerun",
    "re-run",
    "run ",
    "execute",
    "fix ",
    "create ",
    "generate ",
    "write ",
    "build ",
    "rebuild",
    "modify ",
    "change ",
    "edit ",
    "save ",
    "search ",
    "look up",
    "fetch ",
    "download",
    "read the ",
    "load the ",
    "compute",
    "calculate",
    "plot ",
    "render",
)


def _looks_like_prose_action_promise(content: str) -> bool:
    """
    Return True if ``content`` looks like the model announced an action
    in future tense without actually calling a tool. Length-bounded so a
    long answer that happens to begin "I'll explain..." doesn't trip.

    Examples that fire (from observed failures):
        "I'll update the Beamer presentation to use 16:9 widescreen
         format by adding aspectratio=16 to the document class options,
         then recompile it."
        "I'll update the Beamer presentation to use 16:9 format and
         compile it for you."
        "Let me read the current LaTeX source file and update it."

    Examples that do NOT fire:
        "I'll explain how the Schrödinger equation governs..." (no
         action verb the model has tools for)
        "I've compiled the deck. [Download](...)" (completion tense, not
         future tense)
        Long multi-paragraph responses (length cap)
    """
    if not content:
        return False
    stripped = content.strip()
    # Length window. Lower bound rejects empty/near-empty content; upper
    # bound rejects long answers that legitimately discuss future work
    # alongside their main response.
    if len(stripped) > 600 or len(stripped) < 20:
        return False
    low = stripped.lower()
    has_opener = any(low.startswith(o) or f" {o}" in low[:200] for o in _PROSE_ACTION_OPENERS)
    if not has_opener:
        return False
    has_verb = any(v in low for v in _PROSE_ACTION_VERBS)
    if not has_verb:
        return False
    # Reject completion-tense ("I've compiled", "I have updated") even
    # when an action verb is present — those describe finished work,
    # not pending work.
    completion_markers = ("i've ", "i have ", "i've just ", "just compiled", "just updated")
    if any(m in low for m in completion_markers):
        return False
    return True


async def _force_clarification_retry(
    messages: list[dict],
    sampling: dict,
    persona: Optional[dict] = None,
    max_attempts: int = 3,
) -> Optional[dict]:
    """
    Non-streaming vLLM call with ``tool_choice`` forced to
    ``ask_clarification``. Returns a finalised tool-call dict shaped like
    ``_StreamAccumulator.finalized_tool_calls()`` entries, or None if the
    retry failed to produce a parseable AND schema-valid call after
    ``max_attempts`` tries.

    This is the qwen3-coder escape hatch: when the model wrote a prose
    clarification in the first pass (detected by
    ``_looks_like_prose_clarification``), the direct verification shows the
    model will emit a perfectly structured tool call when forced. But the
    forced call sometimes comes back malformed (e.g. ``questions`` as an
    object instead of a list) due to sampling variance, so we retry with
    fresh samplings up to ``max_attempts`` times and run the same
    validation the chat_service intercept uses so we never bubble a bad
    payload up to the SSE event.
    """
    for attempt in range(max_attempts):
        body: dict[str, Any] = {
            "model": VLLM_MODEL_NAME,
            "messages": messages,
            "stream": False,
            "tools": _openai_tools_schema(persona),
            "tool_choice": {
                "type": "function",
                "function": {"name": "ask_clarification"},
            },
            # Clarification cards are short (≤6 questions, each ≤200
            # chars) so a tight cap is safe and protects against the
            # same runaway-decode failure mode the streaming path now
            # bounds.
            "max_tokens": 2048,
        }
        body.update(sampling)
        try:
            d = await vllm_post_json(
                body, timeout=60.0, purpose="forced_clarification"
            )
        except VLLMRequestError as e:
            logger.warning(
                "forced clarification retry failed (attempt %d/%d): %s",
                attempt + 1, max_attempts, e,
            )
            continue
        record_usage("forced_clarification", d.get("usage"))
        msg = (d.get("choices") or [{}])[0].get("message") or {}
        tcs = msg.get("tool_calls") or []
        if not tcs:
            logger.warning(
                "forced clarification retry produced no tool call "
                "(attempt %d/%d)",
                attempt + 1, max_attempts,
            )
            continue
        fn = tcs[0].get("function") or {}
        if fn.get("name") != "ask_clarification":
            logger.warning(
                "forced clarification retry picked wrong tool %r "
                "(attempt %d/%d)",
                fn.get("name"), attempt + 1, max_attempts,
            )
            continue
        args = _parse_arguments(fn.get("arguments"))
        # Validate the payload against the §14 schema before trusting it.
        # qwen3 sometimes emits ``questions`` as a dict, a string, or None
        # under forced tool_choice. When validation fails we burn another
        # attempt with fresh sampling variance.
        _, err = clarification_tool.validate_clarification_payload(
            args.get("what_i_understood"),
            args.get("questions"),
        )
        if err is not None:
            logger.warning(
                "forced clarification retry payload invalid "
                "(attempt %d/%d): %s",
                attempt + 1, max_attempts, err,
            )
            continue
        return {
            "id": tcs[0].get("id") or "tc-forced-clarification",
            "name": "ask_clarification",
            "arguments": args,
        }
    return None


async def _force_required_tool_retry(
    messages: list[dict],
    sampling: dict,
    nudge_text: str,
    persona: Optional[dict] = None,
    max_attempts: int = 2,
) -> Optional[list[dict]]:
    """
    Non-streaming vLLM call with ``tool_choice="required"`` so the model
    MUST emit at least one tool call instead of plain prose. Used as the
    recovery path when the streaming pass produced a prose-only action
    promise (`_looks_like_prose_action_promise`) or hallucinated artifact
    URLs without any matching tool call.

    Appends ``nudge_text`` as a synthetic user-role turn before the
    retry. The original user message is already in ``messages``; the
    nudge reinforces "use your tool, don't promise". Returns a list of
    finalised tool-call dicts (same shape as
    ``_StreamAccumulator.finalized_tool_calls()`` entries) or None if
    every attempt failed to produce parseable tool calls.

    Distinct from ``_force_clarification_retry`` in two ways:
    - No specific tool name is forced — vLLM picks any. This matters
      because the recovery path doesn't know which tool the model
      should have called (compile_latex, update_artifact, run_python,
      paper_search, ...).
    - Returns all tool calls, not just the first. The recovered turn
      may legitimately want multiple calls (e.g. read_artifact +
      compile_latex).
    """
    nudged_messages = list(messages) + [{"role": "user", "content": nudge_text}]
    for attempt in range(max_attempts):
        body: dict[str, Any] = {
            "model": VLLM_MODEL_NAME,
            "messages": nudged_messages,
            "stream": False,
            "tools": _openai_tools_schema(persona),
            "tool_choice": "required",
            # Cap generation. A forced tool call should resolve in well
            # under this; without a cap, an off-the-rails generation on
            # a long multi-turn context can run vLLM until the model
            # length limit, blocking the engine for minutes. 8k tokens
            # comfortably fits compile_latex/run_python source payloads
            # which are the largest realistic argument size.
            "max_tokens": 8000,
        }
        body.update(sampling)
        try:
            d = await vllm_post_json(
                body, timeout=60.0, purpose="forced_required"
            )
        except VLLMRequestError as e:
            logger.warning(
                "forced-required retry failed (attempt %d/%d): %s",
                attempt + 1, max_attempts, e,
            )
            continue
        record_usage("forced_required", d.get("usage"))
        msg = (d.get("choices") or [{}])[0].get("message") or {}
        tcs = msg.get("tool_calls") or []
        if not tcs:
            logger.warning(
                "forced-required retry produced no tool calls "
                "(attempt %d/%d)",
                attempt + 1, max_attempts,
            )
            continue
        out: list[dict] = []
        for i, tc in enumerate(tcs):
            fn = tc.get("function") or {}
            name = fn.get("name")
            if not name:
                continue
            args = _parse_arguments(fn.get("arguments"))
            out.append({
                "id": tc.get("id") or f"tc-forced-required-{i}",
                "name": name,
                "arguments": args,
            })
        if out:
            return out
    return None


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
    persona: Optional[dict] = None,
    *,
    purpose: str = "chat_turn",
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
        # Without include_usage the final chunk of the SSE stream
        # carries no ``usage`` field and all token counts reach the
        # gateway as 0 — breaking per-user quota enforcement and the
        # admin usage dashboard. vLLM (like the OpenAI API) requires
        # an explicit opt-in.
        "stream_options": {"include_usage": True},
        # Cap generation. Without this vLLM defaults to
        # ``max_model_len - prompt_tokens`` (≈30-60K tokens on a
        # 65K-context model after a few multi-turn iterations). A
        # degenerate decode can then park one of the
        # ``--max-num-seqs 2`` slots for 10+ minutes while it walks the
        # full budget — the symptom we hit on 2026-04-27 when the
        # flakiness suite stalled mid-run. 16K leaves plenty of room
        # for thinking traces + tool-call payloads (compile_latex
        # source ≈ 9KB ≈ 3K tokens, so even argument-heavy turns fit
        # comfortably) while bounding worst-case occupancy. Personas
        # can override via params.max_tokens.
        "max_tokens": 16384,
    }
    body.update(sampling)
    if enable_tools:
        body["tools"] = _openai_tools_schema(persona)
        body["tool_choice"] = "auto"

    try:
        async with vllm_post_stream(body, purpose=purpose) as line_iter:
            emitted_tool_ids: set[int] = set()

            async for line in line_iter:
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

    except VLLMRequestError as e:
        yield ("error", {"message": str(e)}, acc)
    except Exception as e:
        yield ("error", {"message": f"vLLM streaming failed: {e}"}, acc)


# --- Tool execution -----------------------------------------------------------

def _duplicate_create_artifact_ids(tool_calls: list[dict]) -> set[str]:
    """IDs of redundant ``create_artifact`` calls within ONE response.

    The model sometimes emits several ``create_artifact`` calls with the
    same title in a single response (chat d28ef78e: 3x "Pong Game"),
    minting duplicate artifacts. Keep only the LAST call per normalised
    title (the most refined draft); return the earlier ones so the
    executor can skip the actual creation and synthesise a "duplicate"
    result. Untitled calls are never deduped (no reliable key); distinct
    titles are untouched, so a response that legitimately creates several
    different artifacts is unaffected.
    """
    ids_by_title: dict[str, list[str]] = {}
    for tc in tool_calls:
        if tc.get("name") != "create_artifact":
            continue
        title = ((tc.get("arguments") or {}).get("title") or "").strip().lower()
        tid = tc.get("id")
        if title and tid:
            ids_by_title.setdefault(title, []).append(tid)
    skip: set[str] = set()
    for ids in ids_by_title.values():
        if len(ids) > 1:
            skip.update(ids[:-1])  # keep the last occurrence
    return skip


async def _run_tool_calls(
    tool_calls: list[dict],
    persona_id: Optional[str] = None,
    allowed_tools: Optional[set[str]] = None,
) -> list[dict]:
    """Execute all tool calls for a turn, preserving declared order.

    Concurrency-safe tools (paper_search, web_search, calculate, ...)
    fan out via asyncio.gather. Unsafe tools (``is_concurrency_safe:
    False`` in MCP_TOOLS — the artifact/memory/sandbox mutators) run
    serially in declared order so two ``update_artifact`` calls on
    the same id, or ``run_python`` + ``compile_latex`` writing the
    same sandbox /scratch, can't race. The two groups run
    concurrently with each other — they don't share state by
    definition of "safe".

    When ``allowed_tools`` is supplied, any tool call whose name is
    not in the set is short-circuited with a synthetic error result
    instead of being dispatched to the executor. qwen3 emits tool
    calls from training memory regardless of the schema we send (the
    schema is guidance, not enforcement), so a research-persona user
    asking for "run this Python script" still produces a run_python
    call even though the persona's allowlist excludes it. The
    synthetic error tells the model to answer with the tools it has,
    without executing the off-allowlist tool. (A4b retires the
    allowlist entirely, at which point this reject path goes away.)
    """
    # Within this response, collapse same-title create_artifact calls to
    # one real creation (chat d28ef78e). The skipped ones get a synthetic
    # result below so every tool_call_id still has a result.
    dup_skip_ids = _duplicate_create_artifact_ids(tool_calls)

    async def one(tc: dict) -> dict:
        name = tc.get("name") or ""
        if allowed_tools is not None and name not in allowed_tools:
            logger.info(
                "persona-allowlist reject (persona=%r, tool=%r)",
                persona_id, name,
            )
            err = (
                f"The {name!r} tool is not available in the "
                f"{persona_id!r} persona's tool set. Answer the "
                f"request using only the tools you do have."
            )
            return {
                "id": tc["id"],
                "name": name,
                "result": {"error": err},
                "duration_ms": 0,
            }
        # Duplicate create_artifact (same title, same response): skip the
        # actual creation but still return a result so the model gets
        # feedback and the tool_call_id is satisfied.
        if tc.get("id") in dup_skip_ids:
            title = ((tc.get("arguments") or {}).get("title") or "").strip()
            logger.info(
                "deduped duplicate create_artifact in one response (title=%r)",
                title,
            )
            return {
                "id": tc["id"],
                "name": name,
                "result": {
                    "skipped": True,
                    "reason": (
                        f"Duplicate create_artifact: an artifact titled "
                        f"{title!r} was created more than once in this "
                        f"response; only one was kept. Use update_artifact "
                        f"to revise it instead of creating duplicates."
                    ),
                },
                "duration_ms": 0,
            }
        # P2 #23 preToolUse: a hook can short-circuit dispatch by
        # returning a synthetic result (e.g. per-user gating, test
        # fakes, redaction-on-input). Runs after the persona-allowlist
        # check above because persona enforcement is non-negotiable.
        started = time.monotonic()
        try:
            pre_result = await hooks_module.dispatch_pre_tool_use(
                name, tc["arguments"],
            )
        except Exception as e:
            # Dispatcher catches per-hook exceptions; this guards
            # against a bug in the dispatcher itself.
            logger.exception("preToolUse dispatch failed for %r: %s", name, e)
            pre_result = None
        if pre_result is not None:
            duration_ms = int((time.monotonic() - started) * 1000)
            return {
                "id": tc["id"],
                "name": name,
                "result": pre_result,
                "duration_ms": duration_ms,
            }
        try:
            result = await execute_mcp_tool(name, tc["arguments"])
        except Exception as e:
            result = {"error": f"tool execution failed: {e}"}
        duration_ms = int((time.monotonic() - started) * 1000)
        # P2 #23 postToolUse: hooks chain; each non-None return
        # replaces the result for the next hook in the chain. Useful
        # for audit logs (return None, observe only) and output
        # redaction (return a sanitised dict).
        try:
            result = await hooks_module.dispatch_post_tool_use(
                name, tc["arguments"], result, duration_ms,
            )
        except Exception as e:
            logger.exception("postToolUse dispatch failed for %r: %s", name, e)
        return {
            "id": tc["id"],
            "name": name,
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
    # results slots are filled by the time gather returns; the cast
    # placates the type checker without runtime overhead.
    return [r for r in results if r is not None]


_ALLOWED_TAG_KINDS = {"topic", "group", "contributor"}


def _normalize_query_tags(raw) -> Optional[list[dict]]:
    """Drop anything that isn't a well-formed {kind, value} tag so a bad
    request body doesn't reach paper_search. Returns None on empty input
    (lets the ContextVar default propagate) or a cleaned list."""
    if not raw or not isinstance(raw, list):
        return None
    out: list[dict] = []
    for tag in raw:
        if not isinstance(tag, dict):
            continue
        kind = (tag.get("kind") or "").strip().lower()
        value = tag.get("value")
        if kind not in _ALLOWED_TAG_KINDS:
            continue
        if not isinstance(value, str) or not value.strip():
            continue
        out.append({"kind": kind, "value": value.strip().lower()})
    return out or None


def _resolve_effective_tags(
    body_tags: Optional[list[dict]],
    conversation_default_tags: Optional[list[dict]],
) -> Optional[list[dict]]:
    """§28 follow-up: decide which tag list a given turn uses.

    Precedence:
      1. Body tags (this turn's `tags` field) win when present. Lets
         the user change scope mid-conversation.
      2. Conversation's persisted `default_tags` (set on turn 1) is
         the fallback when body tags are absent or empty.
      3. None → unscoped search.

    Pure function so it can be unit-tested without a running service.
    """
    if body_tags:
        return body_tags
    if conversation_default_tags:
        return conversation_default_tags
    return None


# --- Phantom-artifact-URL audit (regression guard for chat 25e3f2b) ---
#
# The model occasionally narrates "I've updated the file" and includes
# a fabricated /api/artifacts/<uuid>/<file> URL without actually
# calling the tool that would have produced it. The chat persona's
# CORE RULES forbid this, but the failure shape is severe enough
# (broken downloads, hallucinated success) that we run a post-turn
# audit as a backstop. Phantom URLs get a warning marker prepended
# to the saved content + a loud log line for triage.

_ARTIFACT_URL_RE = _re.compile(r"/api/artifacts/[\w-]+/[\w.\-+%]+")


def _collect_artifact_urls(obj) -> set:
    """Walk a tool result (string / dict / list) and collect every
    `/api/artifacts/...` URL it contains, anywhere in the structure."""
    found: set = set()
    if isinstance(obj, str):
        for m in _ARTIFACT_URL_RE.findall(obj):
            found.add(m)
    elif isinstance(obj, dict):
        for v in obj.values():
            found.update(_collect_artifact_urls(v))
    elif isinstance(obj, list):
        for v in obj:
            found.update(_collect_artifact_urls(v))
    return found


# Bug 4b (2026-06-01): web_fetch URL allowlist. The web_fetch_content
# tool rejects URLs not in `current_search_urls`; this regex extracts
# URLs from arbitrary strings (user messages, tool result payloads,
# assistant prose) so a URL the user pasted or that surfaced in a
# prior search result can be fetched. Greedy-ish but stops at quote /
# bracket / whitespace, which is enough for the chat transcripts we
# see in practice.
_HTTP_URL_RE = _re.compile(r"https?://[^\s)<>\"'\]]+")


def _extract_urls(obj) -> set[str]:
    """Walk a string / dict / list and collect every `http(s)://...`
    URL. Used to seed the per-request URL allowlist from the
    conversation's persisted tool_calls AND from the current user
    message text. Trailing punctuation that the regex picked up is
    stripped via a small denylist (periods, commas, semicolons) so the
    set keys match exactly what tools will pass back."""
    found: set[str] = set()
    if isinstance(obj, str):
        for m in _HTTP_URL_RE.findall(obj):
            url = m.rstrip(".,;:!?")
            if url:
                found.add(url)
    elif isinstance(obj, dict):
        for v in obj.values():
            found.update(_extract_urls(v))
    elif isinstance(obj, list):
        for v in obj:
            found.update(_extract_urls(v))
    return found


def _seed_search_urls(
    prior_messages: list,
    user_text: str,
    bucket: Optional[set],
) -> None:
    """Populate the per-request URL allowlist from the conversation's
    prior tool results + the current user message text. Silently
    no-op when the bucket is not bound (standalone tool unit tests,
    legacy callers). Mutates `bucket` in place."""
    if bucket is None:
        return
    if isinstance(user_text, str) and user_text:
        bucket.update(_extract_urls(user_text))
    for msg in prior_messages:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if isinstance(content, str) and content:
            bucket.update(_extract_urls(content))
        for tc in (msg.get("tool_calls") or []):
            if not isinstance(tc, dict):
                continue
            bucket.update(_extract_urls(tc.get("result")))
            args = tc.get("arguments")
            if isinstance(args, dict):
                bucket.update(_extract_urls(args.get("url")))


def apply_stream_error_marker(final_content: str, error_message: str) -> str:
    """
    Append a stream-interrupted marker to the assistant content that will be
    persisted, so the saved transcript explains why the turn cuts off where
    it does. Extracted as a pure helper so test_stream_error_persistence can
    verify marker shape without spinning up the full streaming machinery.

    Empty error_message degrades to a generic "vLLM stream error" so the
    marker is still informative if the upstream payload has no message field.
    """
    msg = (error_message or "").strip() or "vLLM stream error"
    marker = f"_(stream interrupted: {msg})_"
    if final_content and final_content.strip():
        return f"{final_content.rstrip()}\n\n{marker}"
    return marker


def audit_artifact_urls_in_content(
    content: str, tool_calls: Optional[list]
) -> tuple:
    """Return (content, phantom_urls).

    `phantom_urls` lists every `/api/artifacts/...` URL that appears
    in `content` but does NOT appear in any of `tool_calls`' result
    payloads from this turn. When non-empty, the returned content
    is prepended with a [backend warning] marker so the saved
    message carries the audit trail through to chat_store and
    future replays.

    Pure function — no I/O, safe to unit-test.
    """
    if not isinstance(content, str) or not content:
        return content, []
    found = set(_ARTIFACT_URL_RE.findall(content))
    if not found:
        return content, []
    legit: set = set()
    for tc in tool_calls or []:
        if isinstance(tc, dict):
            legit.update(_collect_artifact_urls(tc.get("result")))
    phantoms = sorted(found - legit)
    if not phantoms:
        return content, []
    notice_lines = [
        "**[backend warning]** This response references "
        f"{len(phantoms)} artifact URL(s) that were NOT created by a "
        "tool call in this turn. The link(s) below may be invalid, "
        "stale, or hallucinated:"
    ]
    for u in phantoms:
        notice_lines.append(f"- `{u}`")
    notice_lines.extend(["", "---", ""])
    return "\n".join(notice_lines) + "\n" + content, phantoms


# --- Phantom-paper-URL audit (regression guard for chat a42384f0) ---
#
# Reported chat a42384f0 (2026-05-05): on a vLLM/SLURM infra question,
# the model fabricated `[HPCS 2005](https://search.muninai.org/paper/
# 10.1109%2Fhpcs.2005.55/pdf)` to support a load-bearing technical
# claim. No paper_search / paper_lookup / semantic_scholar_search /
# deep_research call ran on that turn, so the URL was invented from
# whole cloth and the citation was fabricated. The `search.muninai.org/
# paper/<encoded_doi>/...` pattern is exclusively constructed by the
# paper tools (see mcp/tools/papers.py PUBLIC_URL); the model has no
# legitimate reason to type one without a tool result. Same backstop
# shape as the artifact audit above.

_PAPER_URL_RE = _re.compile(
    r"https?://(?:www\.)?search\.muninai\.org/paper/[^\s)>\"\]]+"
)


def _collect_paper_urls(obj) -> set:
    """Walk a tool result (string / dict / list) and collect every
    `search.muninai.org/paper/...` URL it contains, anywhere in the
    structure. Mirrors `_collect_artifact_urls`."""
    found: set = set()
    if isinstance(obj, str):
        for m in _PAPER_URL_RE.findall(obj):
            found.add(m)
    elif isinstance(obj, dict):
        for v in obj.values():
            found.update(_collect_paper_urls(v))
    elif isinstance(obj, list):
        for v in obj:
            found.update(_collect_paper_urls(v))
    return found


def audit_paper_urls_in_content(
    content: str, tool_calls: Optional[list]
) -> tuple:
    """Return (content, phantom_urls).

    `phantom_urls` lists every `https://search.muninai.org/paper/...`
    URL that appears in `content` but does NOT appear in any of
    `tool_calls`' result payloads from this turn. When non-empty, the
    returned content is prepended with a [backend warning] marker so
    the saved message carries the audit trail through to chat_store
    and future replays.

    The paper-URL pattern is only ever produced legitimately by the
    paper tools (paper_search, paper_lookup, semantic_scholar_search,
    deep_research) which embed `download_url` fields in their results.
    A paper URL in assistant prose without a backing tool call this
    turn is a fabricated citation.

    Pure function — no I/O, safe to unit-test.
    """
    if not isinstance(content, str) or not content:
        return content, []
    found = set(_PAPER_URL_RE.findall(content))
    if not found:
        return content, []
    legit: set = set()
    for tc in tool_calls or []:
        if isinstance(tc, dict):
            legit.update(_collect_paper_urls(tc.get("result")))
    phantoms = sorted(found - legit)
    if not phantoms:
        return content, []
    notice_lines = [
        "**[backend warning]** This response cites "
        f"{len(phantoms)} paper URL(s) that were NOT returned by any "
        "tool call in this turn. The citation(s) below may be "
        "fabricated:"
    ]
    for u in phantoms:
        notice_lines.append(f"- `{u}`")
    notice_lines.extend(["", "---", ""])
    return "\n".join(notice_lines) + "\n" + content, phantoms


async def _build_full_system_prompt(
    persona: dict,
    user_email: str,
    conversation_id: Optional[str],
    ephemeral: bool,
    project: Optional[dict],
    routed_persona: Optional[dict] = None,
) -> str:
    """
    Assemble the full system prompt for a persona by stacking the
    persona's own ``params.system`` text under the standard set of
    ambient blocks: user profile, user memory, active artifacts,
    project context, current-date, agent summaries, capabilities.

    Factored out of ``stream_chat_completion``. The A3 router composes the
    persona text as base[pin] + fragment[routed] via ``routed_persona`` (see
    ``compose_system_prompt``); the ambient blocks stack underneath unchanged.

    Block stacking order (top to bottom):

        project_block                  -- §21
        artifact_block                 -- §22
        plan_block                     -- P2 #24 Phase 1
        memory_block                   -- §9
        profile_block                  -- §25
        <persona system prompt>
        ambient (current date)
        agent_hint                     -- agent registry
        capabilities_block             -- §4 passive

    Each block is gated by ``ephemeral``/conversation_id/project
    presence the same way the original inline code was — so an
    ephemeral chat gets the same minimal stack as before.
    """
    # A3 layered prompt: when the router picked a different profile than the
    # pin, compose base[pin] + fragment[routed]. compose(pin, pin) reconstructs
    # the original prompt, so the pin==routed case is unchanged.
    if routed_persona is not None and routed_persona is not persona:
        system_prompt = persona_module.compose_system_prompt(persona, routed_persona)
    else:
        system_prompt = persona_module.build_system_prompt(persona)

    # P2 #21: schedule the three SQLite fetches in parallel. Saves
    # ~2× SQLite-roundtrips per turn vs sequential awaits (~3-9ms in
    # practice). Block-render helpers are sync and run after the
    # gather; exception isolation matches the prior per-block
    # try/except pattern via ``return_exceptions=True``.
    want_profile = not ephemeral
    want_memory = not ephemeral
    want_artifact = not ephemeral and bool(conversation_id)
    want_plan = not ephemeral and bool(conversation_id)

    fetches: list[tuple[str, Any]] = []
    if want_profile:
        fetches.append(("profile", user_profile_store.get_profile(user_email)))
    if want_memory:
        fetches.append(("memory", memory_store.recall_all(user_email)))
    if want_artifact:
        fetches.append(("artifact", artifact_store.list_artifacts(
            user_email=user_email,
            conversation_id=conversation_id,
        )))
    if want_plan:
        fetches.append(("plan", plan_store.get_plan(conversation_id)))

    if fetches:
        results = await asyncio.gather(
            *(coro for _, coro in fetches), return_exceptions=True,
        )
        by_name: dict[str, Any] = {
            name: result for (name, _), result in zip(fetches, results)
        }
    else:
        by_name = {}

    def _render_block(name: str, builder) -> Optional[str]:
        """Apply the sync block builder to the fetched value, mirroring
        the original try/except shape. A fetch-time exception (from
        gather) and a render-time exception are both logged + skipped."""
        if name not in by_name:
            return None
        value = by_name[name]
        if isinstance(value, Exception):
            logger.warning("%s load failed: %s", name, value)
            return None
        try:
            return builder(value)
        except Exception as e:
            logger.warning("%s block render failed: %s", name, e)
            return None

    profile_block = _render_block("profile", user_profile_store.build_profile_block)
    if profile_block:
        system_prompt = (
            f"{profile_block}\n\n{system_prompt}"
            if system_prompt
            else profile_block
        )

    memory_block = _render_block("memory", memory_store.build_memory_block)
    if memory_block:
        system_prompt = (
            f"{memory_block}\n\n{system_prompt}"
            if system_prompt
            else memory_block
        )

    artifact_block = _render_block(
        "artifact", artifact_store.build_artifact_summary_block,
    )
    if artifact_block:
        system_prompt = (
            f"{artifact_block}\n\n{system_prompt}"
            if system_prompt
            else artifact_block
        )

    # P2 #24 Phase 1: plan block sits between artifact (workspace
    # state) and the persona prompt because it describes "what we're
    # actively doing now" — sequence-of-action context rather than
    # long-lived workspace state. plan_store.get_plan returned the
    # raw dict; build_plan_block is sync, so _render_block applies.
    plan_block = _render_block("plan", plan_store.build_plan_block)
    if plan_block:
        system_prompt = (
            f"{plan_block}\n\n{system_prompt}"
            if system_prompt
            else plan_block
        )

    if not ephemeral and project:
        try:
            project_block = project_store.build_project_prompt_block(project)
        except Exception as e:
            logger.warning("project block render failed: %s", e)
            project_block = None
        if project_block:
            system_prompt = (
                f"{project_block}\n\n{system_prompt}"
                if system_prompt
                else project_block
            )

    now_local = datetime.now().astimezone()
    ambient = (
        f"Current date: {now_local.strftime('%A, %B %d, %Y')} "
        f"({now_local.strftime('%Y-%m-%d %H:%M %Z')}). "
        "Use this directly — do not search the web for the date. "
        "The full history of THIS conversation is provided above; you can "
        "read every earlier turn in it. Do not tell the user you have no "
        "memory of the current conversation (you may lack access to other, "
        "separate chats, but not this one)."
    )
    system_prompt = (
        f"{system_prompt}\n\n{ambient}" if system_prompt else ambient
    )

    agent_hint = agents_pkg.agent_summaries_for_prompt()
    if agent_hint:
        system_prompt = f"{system_prompt}\n\n{agent_hint}"

    if not ephemeral:
        try:
            capabilities_block = capabilities_module.build_capabilities_block()
        except Exception as e:
            logger.warning("capabilities block build failed: %s", e)
            capabilities_block = None
        if capabilities_block:
            system_prompt = f"{system_prompt}\n\n{capabilities_block}"

    return system_prompt


def build_active_tags_block(tags: Optional[list[dict]]) -> Optional[str]:
    """Format a short system-prompt block describing currently-active
    `#tag` scope filters so the model knows what knowledge is attached.

    Without this, the ContextVar flows silently into paper_search's
    Qdrant filter — the user sees scoped results, but when they ask
    "what knowledge do I have attached?" the model has no awareness
    and incorrectly reports "nothing". The block makes the scope
    visible at every turn.
    """
    if not tags:
        return None
    lines = ["=== ACTIVE SCOPE TAGS ==="]
    lines.append(
        "The user has attached the following knowledge scope filters to "
        "this chat. Every paper_search / deep_research call automatically "
        "scopes Qdrant results to papers matching ALL of these filters:"
    )
    lines.append("")
    for tag in tags:
        kind = tag.get("kind")
        value = tag.get("value")
        if kind == "group":
            lines.append(f"- #{value} (research-group scope)")
        elif kind == "contributor":
            lines.append(f"- #@{value} (individual-contributor scope)")
        elif kind == "topic":
            lines.append(f"- #{value} (topic-cluster scope)")
    lines.append("")
    lines.append(
        "When the user asks 'what knowledge do I have attached', 'what can "
        "you see', 'what am I scoped to', or similar — tell them about "
        "these active scope tags explicitly. When citing papers surfaced "
        "by a scoped search, mention the scope ('I searched the Zeitler "
        "Lab corpus for...'). Do NOT silently claim corpus-wide coverage "
        "when the scope was narrower."
    )
    lines.append("=== END ACTIVE SCOPE TAGS ===")
    return "\n".join(lines)


# --- Main entry point ---------------------------------------------------------

async def stream_chat_completion(
    user_email: str,
    persona_id: str,
    conversation_id: Optional[str],
    user_message: dict,
    rag_config: Optional[dict],
    ephemeral: bool = False,
    prior_messages: Optional[list[dict]] = None,
    project: Optional[dict] = None,
    file_into_project_id: Optional[str] = None,
    query_tags: Optional[list[dict]] = None,
    cancel_event: Optional[asyncio.Event] = None,
    stream_id: Optional[str] = None,
) -> AsyncIterator[dict]:
    """
    Orchestrate a single /api/chat/completions request. Yields SSE events.

    When ``ephemeral`` is True, no rows are written to ``chats.db``: the
    conversation is synthesised in-memory with an ``ephemeral-`` id, history
    comes from ``prior_messages`` (frontend echoes the full thread on each
    turn), and persistence/title/summary side effects are all skipped. The
    model still has full access to tools — "ephemeral" means not stored by
    Munin, not untrackable by the world.

    ``cancel_event`` is set by main.py's disconnect watchdog when the client
    drops. We poll it at safe seams (turn boundaries, before wrap-up, before
    auto-title) to skip work no one's waiting for, and use it to proactively
    cancel the in-flight tool runner task so a long ``run_python`` doesn't
    keep a vLLM slot held after disconnect.
    """

    def _cancelled() -> bool:
        return cancel_event is not None and cancel_event.is_set()

    persona = persona_module.get_persona(persona_id)
    if persona is None:
        yield _error_sse(f"Unknown persona: {persona_id}")
        return

    # --- A3: up-front per-turn router ---
    # `persona`/`persona_id` resolved above is the PIN (the user's selector).
    # Route THIS turn from the query, biased by the pin. The routed profile
    # drives sampling + tools + tool scoping (Q5); the pin supplies only the
    # base voice via compose_system_prompt below. When ROUTER_ENABLED is
    # false, routed == pin so nothing changes (compose(pin,pin) == original).
    pin_persona = persona
    pin_id = persona_id
    routing_method = "pin"
    routing_confidence = 1.0
    if ROUTER_ENABLED:
        try:
            _q = (user_message or {}).get("content") or ""
            _d = router_module.route(_q, pin_id, _get_router_index(), _bge_embed)
            _routed = persona_module.get_persona(_d.profile)
            if _routed is not None:
                persona, persona_id = _routed, _d.profile
                routing_method, routing_confidence = _d.method, _d.confidence
                # Slash commands strip the leading `/<profile>` token so the
                # model never sees the command. New dict, don't mutate caller's.
                if _d.stripped_query is not None:
                    user_message = {**user_message, "content": _d.stripped_query}
            else:
                logger.warning("router picked unknown profile %r; staying on pin %r",
                               _d.profile, pin_id)
        except Exception as e:
            # Surface failure in the routing event ("error" != "pin") so a
            # broken router under ROUTER_ENABLED=true is diagnosable, not
            # silently mistaken for the router being off.
            logger.warning("router failed, staying on pin %r: %s", pin_id, e)
            persona, persona_id = pin_persona, pin_id
            routing_method = "error"

    # Bind per-request context for MCP tool dispatch (e.g. search_user_docs).
    current_user_email.set(user_email)
    current_conversation_id.set(conversation_id)
    # Logging + tool scoping pick up the ROUTED profile via this ContextVar
    # (Q5: the routed profile's allowlist applies, not the pin's).
    current_persona.set(persona_id)
    # Routing decision SSE — emitted before the first model call so the
    # frontend + routing eval can read the active profile. Replaces the
    # retired persona_changed/delegated events (A4).
    yield _sse("routing", {
        "profile": persona_id,
        "pin": pin_id,
        "method": routing_method,
        "confidence": round(routing_confidence, 3),
    })
    # Deferred-tool unlock set (P1 #7). Fresh per request: tool_search
    # adds discovered tools here and _openai_tools_schema unions them
    # into the schema for the rest of the request.
    current_unlocked_tools.set(set())
    # Bug 4b (2026-06-01): URL allowlist for web_fetch. Seeded later
    # from the conversation's prior tool_calls + the current user
    # message's text. web_search adds new URLs in place as it runs;
    # web_fetch consults the set before any HTTP call.
    current_search_urls.set(set())
    # Project context (§21): bind the project_id so search_user_docs
    # auto-scopes via contextvar. Ephemeral chats never have a project,
    # so the contextvar stays None in that branch.
    current_project_id.set(
        (project or {}).get("id") if not ephemeral else None
    )
    # §28: tag-scoped search. Frontend maps `#zeitler` / `#nmr` chips to
    # the request body's `tags` field; paper_search and deep_research
    # read current_query_tags ContextVar to scope Qdrant queries.
    #
    # We defer the ContextVar set until AFTER conversation resolution
    # below — for existing conversations with no body tags, we fall
    # back to the conversation's persisted `default_tags` (§28
    # follow-up). `effective_tags` picks the right one.
    normalized_tags = _normalize_query_tags(query_tags)
    effective_tags: Optional[list[dict]] = normalized_tags

    # Persona system prompt + all ambient blocks (profile, memory,
    # artifacts, project, current date, agents, capabilities).
    # A3: base voice from the PIN, task fragment from the ROUTED profile.
    # When routed == pin, compose() reconstructs the pin's original prompt.
    system_prompt = await _build_full_system_prompt(
        persona=pin_persona,
        routed_persona=persona,
        user_email=user_email,
        conversation_id=conversation_id,
        ephemeral=ephemeral,
        project=project,
    )

    # §28: active-tags block is injected further down, AFTER conversation
    # resolution, because `effective_tags` depends on the conversation's
    # persisted default_tags. See the block marker further below.

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
        # §28 follow-up: persist the first request's tags as the
        # conversation's default_tags so future turns that omit `tags`
        # still get scoped. `normalized_tags` may be None here — in
        # that case nothing is persisted and default_tags stays NULL.
        created = await chat_store.create_conversation(
            user_email,
            persona_id,
            title=None,
            default_tags=normalized_tags,
        )
        conversation = await chat_store.get_conversation(created["id"], user_email)
        is_new = True
        # §21: if the caller asked to file this brand-new conversation
        # into a project, do it now so the project context block (and
        # the per-turn project_id contextvar set further down) both
        # see the correct workspace. We've already validated the
        # project belongs to the user in api_chat_completions, so a
        # failure here is a race condition and we just log and
        # proceed (the conversation remains unfiled).
        if file_into_project_id and conversation is not None:
            try:
                filed = await project_store.file_conversation(
                    project_id=file_into_project_id,
                    conversation_id=conversation["id"],
                    user_email=user_email,
                )
                if filed is not None:
                    conversation["project_id"] = file_into_project_id
            except Exception as e:
                logger.warning("auto-file new conversation failed: %s", e)

    assert conversation is not None
    # Now that we know the concrete id, re-bind the MCP context var.
    current_conversation_id.set(conversation["id"])

    # §28 follow-up: fold conversation default_tags into effective_tags.
    # Body tags (when present) win; otherwise inherit the persisted
    # default. Ephemeral conversations never have default_tags (no
    # persistence layer), so ephemeral → body tags only.
    if not ephemeral:
        effective_tags = _resolve_effective_tags(
            normalized_tags, conversation.get("default_tags")
        )
    current_query_tags.set(effective_tags)

    # §28: active-tags block. Injected on every turn that has tags so
    # the model can (a) honestly answer "what knowledge is attached"
    # and (b) cite the scope in its final prose. No PII in the block,
    # so it's fine for ephemeral mode too.
    active_tags_block = build_active_tags_block(effective_tags)
    if active_tags_block:
        system_prompt = f"{system_prompt}\n\n{active_tags_block}"

    yield _sse(
        "conversation",
        {
            "id": conversation["id"],
            "title": conversation.get("title"),
            "is_new": is_new,
            "ephemeral": ephemeral,
            # P1 #10: lets the frontend persist + resume via Last-Event-ID
            # on disconnect or browser refresh. None on legacy callers.
            "stream_id": stream_id,
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

    # --- 3. Multimodal resolution (§5) ---
    # If the user's trailing message contains image_url content blocks,
    # resolve them to concrete data URLs, funnel inline pastes to the
    # documents store on persistent chats, and collect attachment
    # metadata for the chat_store row. Text-only content passes through
    # as-is. Errors are surfaced as an SSE error frame and the stream
    # terminates so the frontend can show the 400-style reason.
    raw_content = user_message.get("content", "")
    try:
        resolved_content, user_attachments = await _resolve_user_content_images(
            content=raw_content,
            user_email=user_email,
            conversation_id=conversation["id"],
            ephemeral=ephemeral,
        )
    except vision.VisionError as exc:
        yield _error_sse(str(exc))
        return

    # Derive the text-only form once so it can be persisted in the
    # messages.content column (which is FTS5-indexed and therefore
    # must stay a flat string). The full resolved_content goes to vLLM.
    if isinstance(resolved_content, list):
        persisted_text = "\n".join(
            block.get("text", "") for block in resolved_content
            if isinstance(block, dict) and block.get("type") == "text"
        ).strip()
    else:
        persisted_text = raw_content if isinstance(raw_content, str) else ""

    # --- 4. Persist the user message ---
    if not ephemeral:
        await chat_store.add_message(
            conversation_id=conversation["id"],
            role="user",
            content=persisted_text,
            attachments=user_attachments or None,
            persona=persona_id,
        )
        # Reload so the new message is part of the context assembly.
        conversation = await chat_store.get_conversation(conversation["id"], user_email)
        assert conversation is not None
        # The user message is already persisted and included in
        # conversation.messages. Pop it back off so assemble_context doesn't
        # double-count it. We replace the persisted string content with
        # the multimodal content on the way to vLLM so the model sees
        # the images on this turn (one-shot).
        if conversation["messages"]:
            conversation["messages"].pop()
        new_msg = {
            "role": "user",
            "content": resolved_content if resolved_content else persisted_text,
        }
    else:
        # Ephemeral: history is already in conversation["messages"] from the
        # synthetic build above; nothing to persist or reload.
        new_msg = {
            "role": "user",
            "content": resolved_content if resolved_content else persisted_text,
        }

    # Bug 4b (2026-06-01): seed the web_fetch URL allowlist from the
    # conversation's prior tool results AND from the current user
    # message's text. The set lives in `current_search_urls` and is
    # also updated in place by web_search as it runs. Scope is whole-
    # conversation so URLs surfaced in earlier turns remain fetchable.
    _seed_search_urls(
        conversation.get("messages") or [],
        persisted_text,
        current_search_urls.get(),
    )

    # --- Save-always wrapper (chat 3951063c, 2026-05-08) ---
    # Persist the assistant turn no matter how this function exits:
    # normal completion, unhandled exception, or GeneratorExit thrown
    # by the consumer (FastAPI break-on-disconnect at main.py:1297).
    # GeneratorExit derives from BaseException so it would bypass any
    # `except Exception:` guard; the only mechanism that catches it
    # is `try:`/`finally:` -- hence this wrapper. Without it, a user
    # closing the tab after seeing the "stream interrupted" banner
    # caused the assistant turn to vanish from the saved transcript.
    assistant_persisted = False
    final_content = ""
    final_thinking = ""
    final_tool_calls: list[dict] = []
    finish_reason: Optional[str] = None
    had_stream_error: Optional[str] = None
    # P1 #12 chat-turn outcome label. Default to "error" so that an
    # unhandled exception path (we crash before classifying) is visible
    # in the metric instead of silently counted as a success.
    terminal_reason: str = "error"
    # The current turn's stream accumulator + a flag for whether its
    # content has been folded into final_content/final_thinking.
    # Hoisted here (not loop-local) so the save-always finally can read
    # them directly without locals() introspection, even when the
    # function raises before the streaming loop runs.
    acc: Optional[_StreamAccumulator] = None
    acc_transferred = True
    # Per-request token usage aggregator. Every vLLM call site folds its
    # usage into this dict via record_usage(purpose, ...). The done event
    # emits the aggregated totals (gateway-shaped) plus the per-purpose
    # map. Bound here so it covers everything including the helpers
    # invoked from assemble_context (history summarisation).
    usage_agg: dict[str, dict] = {}
    usage_token = current_usage_aggregator.set(usage_agg)
    try:
        messages, compact_info = await chat_context.assemble_context(
            conversation=conversation,
            new_message=new_msg,
            system_prompt=system_prompt,
            rag_context=rag_context,
            ephemeral=ephemeral,
            active_persona_id=persona_id,
        )
        # P2 #22: emit a compact_boundary SSE so the UI can render a
        # visible "earlier N messages summarised" divider in the chat
        # log. Fires whether the summary was generated this turn
        # (is_fresh=True) or reused from a prior background prefetch
        # (is_fresh=False) — either way the user should see the
        # boundary in their transcript.
        if compact_info is not None:
            yield _sse("compact_boundary", compact_info)

        # --- 5. Streaming loop with tool execution ---
        final_content = ""
        final_thinking = ""
        final_tool_calls: list[dict] = []
        finish_reason: Optional[str] = None

        # Per-persona tool-use turn budget (P1 #16): research personas
        # doing deep multi-call exploration want more than a chat
        # persona. personas.max_turns clamps to [1, 30].
        MAX_TURNS = persona_module.max_turns(persona)
        hit_turn_cap = True  # assume exhaustion unless we break cleanly below
        # Set by the loop body when a vLLM stream error or empty response forces
        # us to abandon the current turn. Triggers the partial-state persistence
        # path below (§6) instead of returning silently. Losing partial state on
        # error was the cause of the "vanishing agent" symptom in chat 676a3238
        # on 2026-05-05, where the user saw invoke_agent fire and the agent run,
        # but a vLLM "Error in input stream" on the follow-up summarisation turn
        # made the entire assistant message disappear from the saved transcript.
        had_stream_error: Optional[str] = None
        # `acc` / `acc_transferred` are hoisted above the try: block.
        # If GeneratorExit fires inside the inner async-for (client
        # disconnect during streaming), the per-turn transfer is skipped,
        # leaving partial content in `acc` only; the save-always finally
        # folds the remainder so we never lose tokens the user already saw.
        for turn in range(MAX_TURNS):
            if _cancelled():
                logger.info(
                    "client disconnected during turn %d; stopping early",
                    turn,
                )
                hit_turn_cap = False
                break
            acc = None
            stream_error: Optional[str] = None
            recovery_used_this_turn = False
            acc_transferred = False  # reset per-turn

            # Consume one streaming pass. An *empty* completion (the
            # iterator yields nothing, so `acc` stays None, yet no error
            # fired) is usually transient: the model emitted an immediate
            # stop with no content/thinking/tool-call, a shape we saw right
            # after a tool-validation error knocked the model off its plan
            # (chat 5e27dfa4, 2026-06-16). Retry the turn once before giving
            # up; bounded by range(2) so it can never loop. A *hard* stream
            # error is NOT retried here; vllm_client already ran its own
            # transient-error backoff before surfacing it.
            for empty_attempt in range(2):
                acc = None
                stream_error = None
                if empty_attempt > 0:
                    logger.info(
                        "empty completion on turn %d; retrying once", turn
                    )
                    yield _sse(
                        "retrying",
                        {
                            "attempt": 1,
                            "max_attempts": 1,
                            "delay_s": 0,
                            "reason": "empty_response",
                        },
                    )
                async for event_name, payload, accumulator in _stream_vllm_once(
                    messages=messages,
                    sampling=sampling,
                    enable_tools=True,
                    persona=persona,
                ):
                    acc = accumulator
                    if event_name == "error":
                        stream_error = payload.get("message")
                        yield _sse("error", payload)
                        continue
                    yield _sse(event_name, payload)
                # Got output, or a hard error that owns its own handling
                # below; either way stop retrying. Only a clean-but-empty
                # pass falls through to attempt 1.
                if stream_error or acc is not None:
                    break

            if stream_error:
                had_stream_error = stream_error
                if acc is not None:
                    final_thinking += acc.thinking
                    final_content += acc.content
                acc_transferred = True
                hit_turn_cap = False
                break
            if acc is None:
                yield _error_sse("vLLM produced no output")
                had_stream_error = "vLLM produced no output"
                acc_transferred = True
                hit_turn_cap = False
                break

            final_thinking += acc.thinking
            final_content += acc.content
            acc_transferred = True
            record_usage("main_turn", acc.usage)
            finish_reason = acc.finish_reason

            tool_calls = acc.finalized_tool_calls()

            # §14 prose-clarification fallback: qwen3-coder sometimes writes
            # clarification questions as prose text instead of calling the
            # ``ask_clarification`` tool, even with the strongest persona-level
            # instructions - it's a baked-in training bias ("tools are for
            # actions, prose is for talking to the user"). When that happens on
            # the FIRST turn of a user message, detect the prose pattern and
            # retry with tool_choice forced to ask_clarification. The forced
            # retry always produces a proper structured call because the model
            # absolutely knows how to use the tool - it just won't pick it on
            # its own. Restricted to turn==0 so we don't interfere with
            # follow-up turns where the model is mid-task.
            if (
                not tool_calls
                and turn == 0
                and _looks_like_prose_clarification(acc.content)
            ):
                forced = await _force_clarification_retry(
                    messages, sampling, persona=persona
                )
                if forced is not None:
                    tool_calls = [forced]
                    # Wipe the prose content we accumulated during the
                    # streamed first pass so the persisted assistant message
                    # shows the clarification fallback markdown instead of
                    # the rejected prose draft. The tokens have already been
                    # streamed to the client, so the frontend needs to drop
                    # its in-progress buffer when the ``clarification`` SSE
                    # event arrives (see FRONTEND-TASKS entry #10).
                    acc.content_parts.clear()
                    final_content = ""

            # Prose-action recovery: when the streaming pass produced no
            # tool calls but the model wrote a future-tense action promise
            # ("I'll compile and recompile it") or fabricated artifact URLs
            # without a backing tool call, force a non-streaming retry with
            # tool_choice="required". Catches the cbb006ff (2026-04-27)
            # failure shape: the model knows it needs to call compile_latex
            # (the thinking trace says so) but emits prose instead. Unlike
            # the clarification fallback above, this fires on ANY turn —
            # the failure can appear on follow-up modify-and-recompile
            # turns, not just turn 0. Single recovery attempt per turn.
            if not tool_calls and not recovery_used_this_turn:
                _, _phantoms_inline = audit_artifact_urls_in_content(
                    acc.content, []
                )
                phantom_trigger = bool(_phantoms_inline)
                prose_trigger = _looks_like_prose_action_promise(acc.content)
                if phantom_trigger or prose_trigger:
                    trigger_label = (
                        "phantom_url" if phantom_trigger else "prose_action_promise"
                    )
                    logger.info(
                        "prose-action recovery firing "
                        "(trigger=%s, turn=%d, content_len=%d)",
                        trigger_label, turn, len(acc.content),
                    )
                    nudge = (
                        "Your previous response described what you were going "
                        "to do but did not actually call any tool. Call the "
                        "appropriate tool now to fulfil the user's request. "
                        "Do not write prose explaining your plan — emit the "
                        "tool call directly."
                    )
                    if phantom_trigger:
                        nudge += (
                            " Note: any /api/artifacts/... URLs in your "
                            "previous response were not produced by a tool "
                            "call this turn and must not be referenced again "
                            "until a tool actually creates them."
                        )
                    recovered = await _force_required_tool_retry(
                        messages, sampling, nudge, persona=persona
                    )
                    recovery_used_this_turn = True
                    if recovered:
                        tool_calls = recovered
                        # NOTE: We deliberately do NOT clear acc.content /
                        # final_content here. Unlike the clarification
                        # fallback above (which replaces the prose entirely
                        # with a substitute card), the prose-action draft
                        # is a future-tense announcement that reads
                        # naturally followed by the recovered tool calls
                        # + their results. Keeping it preserves the live-
                        # streamed UX (user sees "I'll update..." then the
                        # tool card spins up) and keeps the persisted
                        # transcript coherent on reload.
                        for tc in tool_calls:
                            yield _sse(
                                "tool_call",
                                {
                                    "id": tc["id"],
                                    "name": tc["name"],
                                    "arguments": tc["arguments"],
                                },
                            )

            if not tool_calls:
                hit_turn_cap = False
                break

            # --- §14 ask_clarification intercept ---
            # If the model called ask_clarification, short-circuit the whole
            # turn: emit a single `clarification` SSE event with the full
            # card payload, persist an assistant message with a markdown
            # fallback, emit `done`, and return. We do NOT run any other
            # tool calls from the same turn (the decision was to honor
            # clarification and silently drop the rest), and we do NOT
            # invoke the wrap-up synthesis pass.
            clar_tc = next(
                (tc for tc in tool_calls if tc.get("name") == "ask_clarification"),
                None,
            )
            if clar_tc is not None:
                args = clar_tc.get("arguments") or {}
                normalised, err = clarification_tool.validate_clarification_payload(
                    args.get("what_i_understood"),
                    args.get("questions"),
                )
                if err is not None:
                    # qwen3 sometimes emits ``ask_clarification`` with malformed
                    # arguments under normal tool_choice="auto" sampling (e.g.
                    # ``questions`` as an object instead of a list, or an option
                    # list of size 1 or 7). Rather than bail the whole turn with
                    # an error SSE, fall through to the forced-retry path: it
                    # runs up to 3 attempts with fresh samplings and validates
                    # each, so a schema-valid call almost always drops out.
                    logger.warning(
                        "organic ask_clarification payload invalid "
                        "(%s); trying forced-retry fallback",
                        err,
                    )
                    forced = await _force_clarification_retry(
                        messages, sampling, persona=persona
                    )
                    if forced is None:
                        # Forced retry also failed. Don't break the turn — just
                        # drop the malformed clarification call so the existing
                        # prose content (if any) reaches the user as a normal
                        # response.
                        logger.warning(
                            "forced clarification retry also failed; "
                            "dropping malformed clarification and continuing"
                        )
                        tool_calls = [
                            tc for tc in tool_calls
                            if tc.get("name") != "ask_clarification"
                        ]
                        if not tool_calls:
                            hit_turn_cap = False
                            break
                        continue  # re-enter loop to run the remaining tool calls
                    clar_tc = forced
                    args = forced.get("arguments") or {}
                    normalised, err = clarification_tool.validate_clarification_payload(
                        args.get("what_i_understood"),
                        args.get("questions"),
                    )
                    if err is not None:
                        # Should be unreachable - force retry validates already.
                        logger.warning(
                            "forced-retry payload still invalid after "
                            "validation: %s",
                            err,
                        )
                        hit_turn_cap = False
                        break
                    # Wipe streamed prose so the persisted message is the
                    # card's markdown fallback, not the rejected draft.
                    acc.content_parts.clear()
                    final_content = ""

                fallback_md = clarification_tool.render_markdown_fallback(normalised)

                yield _sse(
                    "clarification",
                    {
                        "tool_call_id": clar_tc["id"],
                        "conversation_id": conversation["id"],
                        "what_i_understood": normalised["what_i_understood"],
                        "questions": normalised["questions"],
                    },
                )

                final_content += fallback_md
                final_tool_calls.append({
                    "id": clar_tc["id"],
                    "name": "ask_clarification",
                    "arguments": normalised,
                    "result": {"status": "awaiting_user_response"},
                    "duration_ms": 0,
                })

                if not ephemeral:
                    await chat_store.add_message(
                        conversation_id=conversation["id"],
                        role="assistant",
                        content=final_content,
                        thinking=final_thinking or None,
                        tool_calls=final_tool_calls or None,
                        rag_context=rag_context,
                        persona=persona_id,
                    )
                    # Without this, the finally block's save-always re-persists
                    # the same turn (chat 9dd753e5, 2026-05-29: two assistant
                    # rows 12ms apart, second one carrying a spurious
                    # "stream interrupted" marker).
                    assistant_persisted = True

                    if not conversation.get("title"):
                        # Feed what_i_understood as the stand-in assistant
                        # response so clarification-first conversations get
                        # a title that reflects the user's intent rather
                        # than the clarification questions themselves.
                        try:
                            title = await chat_context.generate_title(
                                user_message.get("content", ""),
                                normalised["what_i_understood"],
                            )
                            if title:
                                await chat_store.update_conversation(
                                    conversation_id=conversation["id"],
                                    user_email=user_email,
                                    title=title,
                                )
                                yield _sse(
                                    "conversation",
                                    {
                                        "id": conversation["id"],
                                        "title": title,
                                        "is_new": False,
                                        "stream_id": stream_id,
                                    },
                                )
                        except Exception as e:
                            logger.warning("Auto-title (clarification) failed: %s", e)

                yield _sse(
                    "done",
                    {
                        "usage": aggregate_totals(usage_agg),
                        "usage_by_purpose": usage_agg,
                        "finish_reason": "clarification",
                    },
                )
                return

            # Execute all tool calls for this turn in parallel, with a side
            # channel queue so nested agent events can stream to the client
            # while the tools are still running.
            event_queue: asyncio.Queue = asyncio.Queue()
            SENTINEL = object()

            def _push(event_name: str, data: dict) -> None:
                event_queue.put_nowait(_sse(event_name, data))

            current_sse_emitter.set(_push)

            # Build the persona's effective allowlist so _run_tool_calls
            # can reject off-allowlist calls with a synthetic error
            # tool_result. ask_clarification is always permitted (control-flow
            # tool). Personas without an explicit allowlist (back-compat path)
            # get None, which means "all tools allowed" inside _run_tool_calls.
            # (A4b retires the allowlist entirely; this whole path goes away.)
            _allow_list = persona_module.tool_allowlist(persona)
            allowed_tools_set: Optional[set[str]] = None
            if _allow_list is not None:
                allowed_tools_set = set(_allow_list) | {"ask_clarification"}

            async def _runner() -> list[dict]:
                try:
                    return await _run_tool_calls(
                        tool_calls,
                        persona_id=persona_id,
                        allowed_tools=allowed_tools_set,
                    )
                finally:
                    # put_nowait is sync — safe to call after CancelledError
                    # in the inner await. The queue is unbounded so it never
                    # raises QueueFull.
                    event_queue.put_nowait(SENTINEL)

            run_task = asyncio.create_task(_runner())

            # When the client disconnects mid-tool, the watchdog in main.py
            # sets cancel_event. We translate that into an explicit
            # run_task.cancel() so a single long-running tool (run_python,
            # deep_research) doesn't hold a vLLM slot for its remaining
            # budget after the user is gone.
            cancel_listener: Optional[asyncio.Task] = None
            if cancel_event is not None:
                async def _cancel_on_event() -> None:
                    await cancel_event.wait()
                    if not run_task.done():
                        run_task.cancel()
                cancel_listener = asyncio.create_task(_cancel_on_event())

            try:
                while True:
                    item = await event_queue.get()
                    if item is SENTINEL:
                        break
                    yield item
                results = await run_task
            finally:
                # Always tear down both helpers. Reached on:
                #   - normal completion (run_task done → no-op)
                #   - GeneratorExit at the yield above (consumer closed us)
                #   - cancel_event firing mid-tool (CancelledError from
                #     await run_task)
                if cancel_listener is not None and not cancel_listener.done():
                    cancel_listener.cancel()
                    with contextlib.suppress(BaseException):
                        await cancel_listener
                if not run_task.done():
                    run_task.cancel()
                    with contextlib.suppress(BaseException):
                        await run_task
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

                # §22 Stage C: sandbox-generated files (run_python outputs,
                # compile_latex outputs) are registered in the unified
                # artifacts table by the tool itself, so the result dict
                # already carries a ``registered_artifact_id`` alongside
                # the legacy sandbox id. We just fan out an
                # `artifact_created` SSE event per artifact for the side
                # panel to pick up. The old standalone `artifact` SSE
                # event is deprecated and no longer fires (§22 Stage C
                # migration). §18 added compile_latex which reuses the
                # same payload shape - it returns a `.tex` artifact
                # always and a `.pdf` artifact on success, both via the
                # same `artifacts` list.
                if res.get("name") in ("run_python", "compile_latex"):
                    tool_result = res.get("result") or {}
                    for art in (tool_result.get("artifacts") or []):
                        registered_id = art.get("registered_artifact_id")
                        if not registered_id:
                            continue  # registration failed; nothing to emit
                        yield _sse(
                            "artifact_created",
                            {
                                "id": registered_id,
                                "source": art.get("source") or "sandbox_generated",
                                "title": art.get("filename") or "unnamed",
                                "content_type": art.get("content_type"),
                                "filename": art.get("filename"),
                                "size_bytes": art.get("size_bytes"),
                                "external_url": art.get("external_url"),
                                "version": 1,
                                "conversation_id": tool_result.get("conversation_id"),
                                "tool_call_id": res["id"],
                            },
                        )

                # §22: versioned-document artifacts. Shared event type with
                # the sandbox registrations above - both model-written and
                # sandbox-generated artifacts use `artifact_created` (Stage
                # C unification). Only emitted on successful tool results.
                if res.get("name") == "create_artifact":
                    tr = res.get("result") or {}
                    if isinstance(tr, dict) and not tr.get("error"):
                        yield _sse(
                            "artifact_created",
                            {
                                "id": tr.get("id"),
                                "source": tr.get("source") or "model_written",
                                "title": tr.get("title"),
                                "content_type": tr.get("content_type"),
                                "language": tr.get("language"),
                                "version": tr.get("version"),
                                "conversation_id": tr.get("conversation_id"),
                                "tool_call_id": res["id"],
                            },
                        )
                elif res.get("name") == "update_artifact":
                    tr = res.get("result") or {}
                    if isinstance(tr, dict) and not tr.get("error"):
                        yield _sse(
                            "artifact_updated",
                            {
                                "id": tr.get("id"),
                                "source": tr.get("source") or "model_written",
                                "title": tr.get("title"),
                                "version": tr.get("version"),
                                "change_summary": tr.get("change_summary"),
                                "created_by": tr.get("created_by"),
                                "conversation_id": tr.get("conversation_id"),
                                "tool_call_id": res["id"],
                                "applied_hunks": tr.get("applied_hunks"),
                                "lines_added": tr.get("lines_added"),
                                "lines_removed": tr.get("lines_removed"),
                                "base_version": tr.get("base_version"),
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
                    "content": truncate_tool_result(res["result"]),
                })

            # §3 plot-critique feedback loop: if any of the results were
            # run_python calls that produced image artifacts, inject a
            # synthetic user turn carrying the image so the model can look
            # at its own output and decide whether to iterate. vision.py
            # fetches the bytes from the sandbox sidecar and returns a
            # multimodal content block; we append it to the message list so
            # the next vLLM iteration sees it. No-op if the results had no
            # image artifacts.
            try:
                followup = await vision.build_tool_result_followup(
                    tool_results=results,
                    conversation_id=conversation["id"],
                )
            except Exception as e:
                logger.warning("vision feedback loop failed: %s", e)
                followup = None
            if followup is not None:
                messages.append(followup)

            # §5 view_attachment follow-up: if the model called
            # view_attachment, inject the referenced image(s) as a separate
            # synthetic user message (parallel to the sandbox feedback loop
            # above). We run this AFTER the sandbox followup so a turn that
            # happens to do both ends up with run_python artifacts first
            # and re-viewed attachments second in the context.
            try:
                view_followup = vision.build_view_attachment_followup(
                    tool_results=results,
                    user_email=user_email,
                )
            except Exception as e:
                logger.warning("view_attachment followup failed: %s", e)
                view_followup = None
            if view_followup is not None:
                messages.append(view_followup)

        # --- 5b. Wrap-up: force a final synthesis if the loop exhausted its
        # turn budget, OR the last turn produced no real content. The empty-last-
        # turn case is important: the model sometimes emits only a "let me look
        # up X..." preamble on turn N, then stalls with a zero-content turn
        # N+1 (no tool_calls, no text), which naturally breaks the loop. We
        # must detect that and force a synthesis — checking `final_content`
        # (cumulative) would miss it because the preamble already populated
        # final_content on turn N. Mirrors agents/executor.py::execute_agent.
        last_turn_content = (acc.content if acc is not None else "").strip()
        # Skip the wrap-up synthesis on a stream error: vLLM is broken right
        # now, so calling it again to rewrite the final answer would almost
        # certainly fail too, and the second failure would either overwrite or
        # truncate the partial state we already captured. Persist what we have.
        if _cancelled():
            # Client is gone — don't burn another vLLM slot on a synthesis
            # the user will never see. Save-always finally still runs.
            logger.info(
                "client disconnected before wrap-up; skipping synthesis",
            )
        elif (hit_turn_cap or not last_turn_content) and not had_stream_error:
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
                messages=wrap_up_messages,
                sampling=sampling,
                enable_tools=False,
                purpose="chat_wrap_up",
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
                record_usage("wrap_up", wrap_acc.usage)
                finish_reason = wrap_acc.finish_reason or finish_reason

        # --- 6. Persist assistant message ---
        # Stream-error marker. When the loop bailed because vLLM yielded an
        # `error` event mid-stream, append a small italic note so the saved
        # transcript explains why the assistant turn cuts off where it does.
        # The tool calls that fired before the error are already in
        # `final_tool_calls` and will be persisted alongside this content.
        if had_stream_error:
            final_content = apply_stream_error_marker(final_content, had_stream_error)
        # Phantom-artifact-URL audit. If the model wrote a
        # `/api/artifacts/<uuid>/...` URL that wasn't produced by any tool
        # this turn, prepend a warning marker and log loudly. Persona
        # CORE RULES forbid this; the audit is a backstop so the saved
        # transcript carries an audit trail when the rule slips.
        final_content, _phantom_urls = audit_artifact_urls_in_content(
            final_content, final_tool_calls
        )
        if _phantom_urls:
            logger.warning(
                "phantom artifact URLs in conversation: %s",
                _phantom_urls,
            )
            observe_phantom_urls("artifact", len(_phantom_urls))
        # Phantom-paper-URL audit. Same backstop shape but for fabricated
        # `search.muninai.org/paper/<doi>/...` citations (chat a42384f0,
        # 2026-05-05: model invented a `[HPCS 2005](https://search.muninai
        # .org/paper/10.1109%2Fhpcs.2005.55/pdf)` link to support a wrong
        # technical claim, with no paper-tool call on the turn).
        final_content, _phantom_paper_urls = audit_paper_urls_in_content(
            final_content, final_tool_calls
        )
        if _phantom_paper_urls:
            logger.warning(
                "phantom paper URLs in conversation: %s",
                _phantom_paper_urls,
            )
            observe_phantom_urls("paper", len(_phantom_paper_urls))

        if not ephemeral:
            await chat_store.add_message(
                conversation_id=conversation["id"],
                role="assistant",
                content=final_content,
                thinking=final_thinking or None,
                tool_calls=final_tool_calls or None,
                rag_context=rag_context,
                persona=persona_id,
            )
            assistant_persisted = True

        # --- 7. Auto-title on untitled conversations ---
        # Relaxed from ``is_new and not title`` to just ``not title`` so
        # auto-title also fires on the retry turn after a first-request
        # failure. Scenario: user sends a message → backend creates the
        # conversation row and persists the user message → vLLM errors
        # before producing an assistant response → user re-sends → the
        # retry arrives with the existing conversation_id (is_new=False)
        # and no assistant response was ever generated, so the title is
        # still empty. Without this fix, auto-title was permanently
        # skipped for that conversation.
        if not ephemeral and not conversation.get("title") and not _cancelled():
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
                        {
                            "id": conversation["id"],
                            "title": title,
                            "is_new": False,
                            "stream_id": stream_id,
                        },
                    )
            except Exception as e:
                logger.warning("Auto-title failed: %s", e)

        # --- 8. Done ---
        # P1 #12: classify terminal reason. Precedence matters because
        # multiple flags can be true at once — cancellation trumps
        # stream errors trumps turn-cap trumps the clean done path.
        if _cancelled():
            terminal_reason = "cancelled"
        elif had_stream_error:
            terminal_reason = "stream_error"
        elif hit_turn_cap:
            terminal_reason = "max_turns"
        else:
            terminal_reason = "done"

        # P2 #23/#25: fire stop hooks BEFORE yielding `done`. The
        # per-iteration tool emitter (lines ~2200) has already been
        # torn down, so we install a turn-final emitter that buffers
        # into a local list. We then yield each buffered event on
        # the still-open stream, immediately before `done`. Without
        # this, hooks that emit_sse (today: memory_extract) would
        # write into a None emitter because dispatch_stop used to
        # run in the outer finally — past the last yield.
        _stop_events: list[tuple[str, dict]] = []

        def _stop_push(event_name: str, data: dict) -> None:
            _stop_events.append((event_name, data))

        _stop_emitter_token = current_sse_emitter.set(_stop_push)
        try:
            await hooks_module.dispatch_stop(terminal_reason)
        except Exception as e:
            logger.exception("stop hook dispatch failed: %s", e)
        finally:
            with contextlib.suppress(ValueError, LookupError):
                current_sse_emitter.reset(_stop_emitter_token)
        for _ev_name, _ev_data in _stop_events:
            yield _sse(_ev_name, _ev_data)

        yield _sse(
            "done",
            {
                "usage": aggregate_totals(usage_agg),
                "usage_by_purpose": usage_agg,
                "finish_reason": finish_reason or "stop",
            },
        )

        # P2 #22: schedule opportunistic compaction for the NEXT turn.
        # Fires only on a clean exit (terminal_reason == "done") and
        # for persistent conversations; ephemeral chats have no row
        # to persist a summary against. The task runs detached — we
        # don't await it. Failures are logged inside the helper and
        # never affect this turn's response. The user's perceived
        # turn duration is unaffected because by this point all SSE
        # events the client cares about have already been yielded.
        if (
            terminal_reason == "done"
            and not ephemeral
            and conversation_id
            and user_email
        ):
            asyncio.create_task(
                chat_context._maybe_precompact(conversation_id, user_email),
                name=f"precompact-{conversation_id[:8]}",
            )
    finally:
        # Save-always (chat 3951063c, 2026-05-08): the assistant turn
        # MUST be persisted even if the consumer disconnected (yielding
        # GeneratorExit at the current yield) or any helper raised
        # unhandled. We persist whatever partial state we have, with a
        # marker that explains the gap. Skipped for ephemeral chats
        # (no persistence to begin with) and when the explicit persist
        # call above already succeeded (assistant_persisted == True).
        if not ephemeral and not assistant_persisted:
            try:
                # Fold any in-progress turn's accumulator into the
                # final_* totals if the inner streaming loop got
                # interrupted before its own transfer. `acc` and
                # `acc_transferred` are hoisted to the top of the
                # function, so they are always defined here even if we
                # never reached the streaming loop (acc stays None).
                if acc is not None and not acc_transferred:
                    final_thinking += acc.thinking
                    final_content += acc.content
                # Pick a marker text that actually explains what
                # happened on transcript reload (chat 56b39f33,
                # 2026-06-03 — users were seeing the literal
                # "_(stream interrupted: stream interrupted)_" and
                # had no idea whether it meant a server crash or
                # they themselves closed the tab).
                #
                # We treat the turn as a disconnect when EITHER
                # cancel_event was explicitly set by main.py's
                # disconnect watchdog OR the in-flight exception is
                # GeneratorExit / CancelledError (the consumer
                # aclose()'d this generator without the watchdog
                # participating; happens in tests + during shutdown).
                _exc_type = sys.exc_info()[0]
                _disconnect_seen = _cancelled() or _exc_type in (
                    GeneratorExit, asyncio.CancelledError
                )
                if had_stream_error:
                    _marker_reason = had_stream_error
                elif _disconnect_seen:
                    _marker_reason = "client disconnected before completion"
                else:
                    _marker_reason = "server error during stream"
                marker_content = apply_stream_error_marker(
                    final_content,
                    _marker_reason,
                )
                # `conversation` is guaranteed defined here: the early
                # returns at lines 1338/1413/1504 are above the user-
                # message persist (which is in turn above the try block
                # this finally pairs with). If we are inside this try,
                # user-persist already ran and conversation is set.
                # asyncio.shield: a second cancellation arriving during
                # save-always (e.g. process shutdown right after a client
                # disconnect) would otherwise truncate the write mid-row.
                # The shield lets add_message run to completion in the
                # background even if our await is cancelled.
                await asyncio.shield(
                    chat_store.add_message(
                        conversation_id=conversation["id"],
                        role="assistant",
                        content=marker_content,
                        thinking=final_thinking or None,
                        tool_calls=final_tool_calls or None,
                        rag_context=rag_context,
                        persona=persona_id,
                    )
                )
            except Exception as _save_always_exc:
                # Last-ditch: log and swallow. We cannot crash the
                # finally block -- doing so would mask the original
                # exception (or GeneratorExit) that triggered us.
                logger.error(
                    "save-always finally persistence failed for conv %s: %s: %s",
                    conversation.get("id") if isinstance(conversation, dict) else "?",
                    type(_save_always_exc).__name__,
                    _save_always_exc,
                )
        # Unbind the usage aggregator regardless of how we exited. Suppress
        # ValueError in the (impossible-in-practice) case where the token
        # was already reset by an outer scope.
        with contextlib.suppress(ValueError, LookupError):
            current_usage_aggregator.reset(usage_token)
        # P1 #12 turn-counter. Always emit, including the "error" default
        # when an unhandled exception punched out of the try without
        # classification. Persona name is best-effort.
        try:
            _persona_label = (persona or {}).get("id") if isinstance(persona, dict) else None
        except Exception:
            _persona_label = None
        observe_turn(_persona_label, terminal_reason)
        # P2 #23 stop hooks now fire BEFORE the `done` yield (see the
        # try-block block above) so their emit_sse calls actually
        # reach the client. Nothing to do here.
