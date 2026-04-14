"""
MCP tool: search_past_conversations.

Lets the model look at the user's prior conversations to answer questions
like "didn't we talk about X before?" or "what was that paper I found
last week?". Backed by the FTS5 index already maintained on
``messages.content`` in chat_store. User-scoped via the
``current_user_email`` ContextVar; the current chat is excluded by
default via ``current_conversation_id`` so the model doesn't get
"results" that are already in its own context window.
"""

from typing import Optional

from ..context import current_user_email, current_conversation_id


async def search_past_conversations(
    query: str,
    limit: int = 5,
    persona: Optional[str] = None,
    include_current: bool = False,
) -> dict:
    import chat_store  # lazy import — avoids circular init

    user_email = current_user_email.get()
    if not user_email:
        return {
            "results": [],
            "total_matches": 0,
            "error": (
                "search_past_conversations requires an authenticated "
                "user context"
            ),
        }

    if not isinstance(query, str) or not query.strip():
        return {
            "results": [],
            "total_matches": 0,
            "error": "query must be a non-empty string",
        }

    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 5
    limit = max(1, min(limit, 20))

    exclude_conv = None
    if not include_current:
        # Skip the conversation the user is currently in. Ephemeral chats
        # have an `ephemeral-` id that won't match any FTS row anyway, but
        # the explicit filter avoids surprising the user when they happen
        # to use a real id during testing.
        exclude_conv = current_conversation_id.get()

    return await chat_store.search_user_messages(
        user_email=user_email,
        query=query,
        limit=limit,
        persona=persona,
        exclude_conversation_id=exclude_conv,
    )
