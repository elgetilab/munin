"""
Per-request context for MCP tool execution.

Some tools need access to the authenticated user (e.g. search_user_docs must
filter Qdrant by user_email). Rather than threading context through every
tool signature, we expose it via a ContextVar that chat_service sets before
dispatching tool calls.
"""

from contextvars import ContextVar
from typing import Any, Callable, Optional

current_user_email: ContextVar[Optional[str]] = ContextVar(
    "current_user_email", default=None
)
current_conversation_id: ContextVar[Optional[str]] = ContextVar(
    "current_conversation_id", default=None
)
current_project_id: ContextVar[Optional[str]] = ContextVar(
    "current_project_id", default=None
)
# Active persona id (e.g. "chat", "code", "research"). Set by
# chat_service.stream_chat_completion alongside the other request-scoped
# vars; read by logging_config so every log line in the request carries
# the persona without per-call-site plumbing.
current_persona: ContextVar[Optional[str]] = ContextVar(
    "current_persona", default=None
)

# Deferred-tool unlock set (P1 #7 / tool_search). The vLLM `tools` schema
# only carries a small core set; `tool_search` discovers other tools and
# adds their names here. `_openai_tools_schema` unions core + this set so
# a discovered tool stays in the schema for the rest of the request.
# chat_service binds a fresh set() per request; the tool_search handler
# mutates it in place. None means "no deferral wired" (the schema builder
# then falls back to core-only, which is still safe).
current_unlocked_tools: ContextVar[Optional[set]] = ContextVar(
    "current_unlocked_tools", default=None
)

# §28 Sprint B: tag-scoped search. chat_service.stream_chat_completion
# reads the `tags` field from the request body and sets this ContextVar
# before dispatching tool calls. Search tools (paper_search,
# semantic_scholar_search, deep_research) read it to scope their Qdrant
# queries. Shape: a list of {"kind": "topic"|"group"|"contributor",
# "value": str}; empty list or None means unfiltered.
current_query_tags: ContextVar[Optional[list[dict]]] = ContextVar(
    "current_query_tags", default=None
)

# Synchronous push-style SSE emitter. Signature: `emit(event_name, data_dict)`.
# chat_service sets this before dispatching tool calls so that nested async
# work (notably the agent executor) can stream progress without changing tool
# return shapes. Expected to be non-blocking (put-no-wait on a queue).
EmitterFn = Callable[[str, dict], None]
current_sse_emitter: ContextVar[Optional[EmitterFn]] = ContextVar(
    "current_sse_emitter", default=None
)

# URL allowlist for web_fetch (Bug 4b, 2026-06-01). web_search and
# paper_search add every result URL here as they fan out; web_fetch
# rejects URLs not in the set with a synthetic error nudging the model
# to search first. Seeded per request from the conversation's persisted
# tool_calls so URLs surfaced in earlier turns remain fetchable.
# `None` means "no gate wired" (open mode, no rejection); chat_service
# always binds a fresh set per request.
current_search_urls: ContextVar[Optional[set]] = ContextVar(
    "current_search_urls", default=None
)
