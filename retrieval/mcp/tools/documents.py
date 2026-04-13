"""
MCP tool: search_user_docs.

Wraps document_store.search_user_docs and auto-filters by the authenticated
user's email, which chat_service sets on a ContextVar before dispatching
tool calls. The search intentionally spans every conversation the user owns
— filtering by conversation would surprise users who expect to find a doc
they uploaded in an earlier chat.
"""

from ..context import current_user_email


async def search_user_docs(query: str, top_k: int = 5) -> dict:
    import document_store  # lazy import — avoids circular init

    user_email = current_user_email.get()
    if not user_email:
        return {
            "results": [],
            "error": "search_user_docs requires an authenticated user context",
        }

    return await document_store.search_user_docs(
        query=query,
        user_email=user_email,
        top_k=top_k,
    )
