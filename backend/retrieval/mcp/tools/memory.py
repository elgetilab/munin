"""
MCP tools: remember, forget, recall (§9 user memory).

These let the chat model maintain a small, user-scoped key-value
store of facts that persist across conversations. The store is
backed by ``memory_store.py`` which handles the SQLite side,
validation, and LRU eviction when the 20-entry cap is hit.

Complementary to §25 user profile (user-curated via HTTP) vs §9
memory (model-curated via these tools). Both are injected into the
system prompt on every persistent chat turn.

Privacy model: all three tools are **refused in ephemeral chats**.
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
    the top of every memory tool to enforce the ephemeral refusal
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
                "Switch to a regular (persistent) chat to remember "
                "or recall facts about the user."
            ),
        }
    return user_email, None


async def remember(key: str, value: str) -> dict:
    """
    Store a key-value fact about the current user. Upserts if the
    key already exists. If the store is at the 20-entry cap and the
    key is new, the oldest-touched entry is LRU-evicted and its key
    is returned in ``evicted`` so the model can notify the user.
    """
    import memory_store  # lazy — avoids circular init

    user_email, refusal = _require_persistent_user()
    if refusal is not None:
        return refusal
    assert user_email is not None
    try:
        return await memory_store.remember(user_email, key, value)
    except memory_store.MemoryError as exc:
        return {"error": str(exc)}


async def forget(key: str) -> dict:
    """Delete a specific memory. No-op if the key doesn't exist."""
    import memory_store

    user_email, refusal = _require_persistent_user()
    if refusal is not None:
        return refusal
    assert user_email is not None
    try:
        return await memory_store.forget(user_email, key)
    except memory_store.MemoryError as exc:
        return {"error": str(exc)}


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
