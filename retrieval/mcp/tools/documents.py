"""
MCP tool: search_user_docs.

Wraps document_store.search_user_docs and auto-filters by the
authenticated user's email, which chat_service sets on a ContextVar
before dispatching tool calls.

Project scoping (§21): when the current conversation is filed into a
project, the tool auto-binds that project_id unless the caller passes
``project_id=None`` explicitly. The underlying store does two-phase
search (project first, user-global fallback) and returns a
``sources_used`` field so the model can honestly tell the user
whether the result came from their project or from the broader corpus.
"""

from typing import Optional

from ..context import current_user_email, current_project_id


# Sentinel so we can distinguish "caller didn't pass project_id" from
# "caller explicitly passed project_id=None". The model usually won't
# pass this parameter at all, which is the common case.
_UNSET: str = "__unset__"


async def search_user_docs(
    query: str,
    top_k: int = 5,
    project_id: str = _UNSET,
) -> dict:
    import document_store  # lazy import — avoids circular init

    user_email = current_user_email.get()
    if not user_email:
        return {
            "results": [],
            "error": "search_user_docs requires an authenticated user context",
        }

    # Default: inherit the current conversation's project (if any).
    # Explicit None from the caller means "search user-global only".
    effective_project: Optional[str]
    if project_id is _UNSET:
        effective_project = current_project_id.get()
    else:
        effective_project = project_id

    return await document_store.search_user_docs(
        query=query,
        user_email=user_email,
        top_k=top_k,
        project_id=effective_project,
    )
