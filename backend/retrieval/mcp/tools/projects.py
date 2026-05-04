"""
MCP tools: list_projects and get_current_project (§21).

Both user-scoped via the ``current_user_email`` ContextVar. The second
tool also reads ``current_project_id`` to answer "what project is this
chat in?" when the model needs to reference the workspace explicitly.

Neither tool mutates state - those live on the HTTP surface, not on
the model's fingertips.
"""

from typing import Optional

from ..context import current_user_email, current_project_id


async def list_projects() -> dict:
    """
    Return the authenticated user's projects. Archived projects are
    excluded - the model should only see what the user is actively
    working on. Includes per-project conversation counts so the
    model can pick a sensible reference ("your biggest project is X
    with 24 conversations").
    """
    import project_store  # lazy - avoids circular init

    user_email = current_user_email.get()
    if not user_email:
        return {
            "projects": [],
            "error": "list_projects requires an authenticated user context",
        }
    return await project_store.list_projects(
        user_email=user_email,
        include_archived=False,
        limit=100,
        offset=0,
    )


async def get_current_project() -> dict:
    """
    Return the project the current conversation is filed into, or
    ``{"project": null}`` if the chat is unfiled or ephemeral. Useful
    for the model to answer "what project am I in?" without having
    to introspect its own system prompt.
    """
    import project_store  # lazy

    user_email = current_user_email.get()
    if not user_email:
        return {
            "project": None,
            "error": "get_current_project requires an authenticated user context",
        }
    pid: Optional[str] = current_project_id.get()
    if not pid:
        return {"project": None}
    proj = await project_store.get_project(pid, user_email)
    if proj is None:
        return {"project": None}
    return {"project": proj}
