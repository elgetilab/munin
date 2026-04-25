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
import os
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
import project_store
import memory_store
import artifact_store
import capabilities as capabilities_module
import vision
from database import VLLM_URL, VLLM_MODEL_NAME
from mcp.schemas import MCP_TOOLS
from mcp.executor import execute_mcp_tool
from mcp.tools import clarification as clarification_tool
from mcp.context import (
    current_user_email,
    current_conversation_id,
    current_project_id,
    current_query_tags,
    current_sse_emitter,
)


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
                    print(f"[WARNING] inline image funnel failed: {exc}")
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


async def _force_clarification_retry(
    messages: list[dict],
    sampling: dict,
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
            "tools": _openai_tools_schema(),
            "tool_choice": {
                "type": "function",
                "function": {"name": "ask_clarification"},
            },
        }
        body.update(sampling)
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                r = await client.post(
                    f"{VLLM_URL}/v1/chat/completions",
                    json=body,
                )
        except httpx.RequestError as e:
            print(
                f"[WARNING] forced clarification retry HTTP error "
                f"(attempt {attempt + 1}/{max_attempts}): {e}"
            )
            continue
        if r.status_code != 200:
            print(
                f"[WARNING] forced clarification retry returned "
                f"{r.status_code} (attempt {attempt + 1}/{max_attempts}): "
                f"{r.text[:200]}"
            )
            continue
        try:
            d = r.json()
        except Exception as e:
            print(
                f"[WARNING] forced clarification retry parse error "
                f"(attempt {attempt + 1}/{max_attempts}): {e}"
            )
            continue
        msg = (d.get("choices") or [{}])[0].get("message") or {}
        tcs = msg.get("tool_calls") or []
        if not tcs:
            print(
                f"[WARNING] forced clarification retry produced no tool "
                f"call (attempt {attempt + 1}/{max_attempts})"
            )
            continue
        fn = tcs[0].get("function") or {}
        if fn.get("name") != "ask_clarification":
            print(
                f"[WARNING] forced clarification retry picked wrong tool "
                f"{fn.get('name')!r} (attempt {attempt + 1}/{max_attempts})"
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
            print(
                f"[WARNING] forced clarification retry payload invalid "
                f"(attempt {attempt + 1}/{max_attempts}): {err}"
            )
            continue
        return {
            "id": tcs[0].get("id") or "tc-forced-clarification",
            "name": "ask_clarification",
            "arguments": args,
        }
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

    # Inject user memory (§9). Model-curated facts persist across chats
    # via the remember/forget/recall MCP tools. The memory block sits
    # between profile and persona in the system prompt order, so the
    # model sees "what the user told you directly" (profile) before
    # "what you've learned while working with them" (memory). Skipped
    # in ephemeral mode - the same privacy contract as profile, and
    # the memory tools themselves are refused there too.
    if not ephemeral:
        try:
            memories = await memory_store.recall_all(user_email)
            memory_block = memory_store.build_memory_block(memories)
        except Exception as e:
            print(f"[WARNING] memory load failed: {e}")
            memory_block = None
        if memory_block:
            system_prompt = (
                f"{memory_block}\n\n{system_prompt}"
                if system_prompt
                else memory_block
            )

    # Inject active artifacts summary (§22). Summary-only by design:
    # title, type, version, word count. The model calls
    # read_artifact(id) when it needs the actual content, so context
    # overhead stays bounded regardless of how many artifacts a
    # conversation accumulates. Only injected for persistent
    # conversations that have at least one artifact. Ephemeral chats
    # never have artifacts (the tools refuse), so the block is
    # skipped there automatically. This block sits BELOW profile /
    # memory but ABOVE the persona prompt so the "what are we working
    # on right now" framing comes just before the persona's stylistic
    # instructions.
    if not ephemeral and conversation_id:
        try:
            artifact_rows = await artifact_store.list_artifacts(
                user_email=user_email,
                conversation_id=conversation_id,
            )
            artifact_block = artifact_store.build_artifact_summary_block(
                artifact_rows
            )
        except Exception as e:
            print(f"[WARNING] artifact summary load failed: {e}")
            artifact_block = None
        if artifact_block:
            system_prompt = (
                f"{artifact_block}\n\n{system_prompt}"
                if system_prompt
                else artifact_block
            )

    # Inject the project context block (§21). Goes ABOVE the persona
    # prompt so the model sees the workspace framing first. Skipped for
    # ephemeral chats and for conversations without a project.
    if not ephemeral and project:
        try:
            project_block = project_store.build_project_prompt_block(project)
        except Exception as e:
            print(f"[WARNING] project block render failed: {e}")
            project_block = None
        if project_block:
            system_prompt = (
                f"{project_block}\n\n{system_prompt}"
                if system_prompt
                else project_block
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

    # §4 passive: capabilities introspection block. Describes every
    # MCP tool, agent, persona, user-facing feature, and FAQ topic
    # in ~500-600 tokens so the model can honestly answer "what can
    # you do?" without hallucinating. Skipped in ephemeral mode
    # alongside profile / memory / project for the same privacy
    # consistency (nothing user-identifying, but the block is a
    # known-answers-for-this-server signal).
    if not ephemeral:
        try:
            capabilities_block = capabilities_module.build_capabilities_block()
        except Exception as e:
            print(f"[WARNING] capabilities block build failed: {e}")
            capabilities_block = None
        if capabilities_block:
            system_prompt = f"{system_prompt}\n\n{capabilities_block}"

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
                print(f"[WARNING] auto-file new conversation failed: {e}")

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

    messages = await chat_context.assemble_context(
        conversation=conversation,
        new_message=new_msg,
        system_prompt=system_prompt,
        rag_context=rag_context,
        ephemeral=ephemeral,
    )

    # --- 5. Streaming loop with tool execution ---
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
            forced = await _force_clarification_retry(messages, sampling)
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
                print(
                    f"[WARNING] organic ask_clarification payload invalid "
                    f"({err}); trying forced-retry fallback"
                )
                forced = await _force_clarification_retry(messages, sampling)
                if forced is None:
                    # Forced retry also failed. Don't break the turn — just
                    # drop the malformed clarification call so the existing
                    # prose content (if any) reaches the user as a normal
                    # response.
                    print(
                        "[WARNING] forced clarification retry also failed; "
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
                    print(
                        f"[WARNING] forced-retry payload still invalid "
                        f"after validation: {err}"
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
                )

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
                                },
                            )
                    except Exception as e:
                        print(f"[WARNING] Auto-title (clarification) failed: {e}")

            yield _sse(
                "done",
                {
                    "usage": final_usage or {},
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
                "content": json.dumps(res["result"])[:8000],
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
            print(f"[WARNING] vision feedback loop failed: {e}")
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
            print(f"[WARNING] view_attachment followup failed: {e}")
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

    # --- 6. Persist assistant message ---
    # Phantom-artifact-URL audit. If the model wrote a
    # `/api/artifacts/<uuid>/...` URL that wasn't produced by any tool
    # this turn, prepend a warning marker and log loudly. Persona
    # CORE RULES forbid this; the audit is a backstop so the saved
    # transcript carries an audit trail when the rule slips.
    final_content, _phantom_urls = audit_artifact_urls_in_content(
        final_content, final_tool_calls
    )
    if _phantom_urls:
        print(
            f"[WARN] phantom artifact URLs in conversation "
            f"{conversation['id']}: {_phantom_urls}"
        )

    if not ephemeral:
        await chat_store.add_message(
            conversation_id=conversation["id"],
            role="assistant",
            content=final_content,
            thinking=final_thinking or None,
            tool_calls=final_tool_calls or None,
            rag_context=rag_context,
        )

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
    if not ephemeral and not conversation.get("title"):
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

    # --- 8. Done ---
    yield _sse(
        "done",
        {
            "usage": final_usage or {},
            "finish_reason": finish_reason or "stop",
        },
    )
