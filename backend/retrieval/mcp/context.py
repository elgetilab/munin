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
