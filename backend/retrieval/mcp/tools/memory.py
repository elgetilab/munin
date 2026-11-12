"""
MCP tool: recall (§9 user memory).

Lets the chat model read the small, user-scoped key-value store of
facts that persist across conversations. The store is backed by
``memory_store.py``. Facts enter it only through an accepted memory
proposal (``hooks/memory_extract.py``); the model-facing remember and
forget tools were retired (2026-10).

Complementary to §25 user profile (user-curated via HTTP). Both are
injected into the system prompt on every persistent chat turn.

Privacy model: the tool is **refused in ephemeral chats**.
If the conversation id starts with ``ephemeral-`` the tool returns
an error without touching the store. This matches §25 profile and
§2 sandbox behaviour and keeps privacy mode honest about not
persisting user-identifying data.
"""

from __future__ import annotations

from typing import Optional

from ..context import current_user_email, current_conversation_id


def _require_persistent_user() -> tuple[Optional[str], Optional[dict]]:
    """
    Return ``(user_email, None)`` if the current request may use the
    memory store, or ``(None, error_response)`` if it cannot. Used at
    the top of the memory tool to enforce the ephemeral refusal
    and authenticated-user requirements in one place.
    """
    user_email = current_user_email.get()
    if not user_email:
        return None, {
            "error": "memory tools require an authenticated user context",
        }
    conv_id = current_conversation_id.get() or ""
    if conv_id.startswith("ephemeral-"):
        return None, {
            "error": (
                "user memory is not available in ephemeral chats. "
                "Switch to a regular (persistent) chat to recall "
                "facts about the user."
            ),
        }
    return user_email, None


async def recall(search: Optional[str] = None) -> dict:
    """
    Return all remembered facts for the current user, newest first.
    Pass a ``search`` string to filter by case-insensitive substring
    match against keys and values.
    """
    import memory_store

    user_email, refusal = _require_persistent_user()
    if refusal is not None:
        return refusal
    assert user_email is not None
    memories = await memory_store.recall_matching(user_email, search)
    return {
        "memories": memories,
        "total": len(memories),
        "search": search,
    }
