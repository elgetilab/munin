"""
MCP tools: search_user_docs and view_attachment.

``search_user_docs`` wraps document_store.search_user_docs and
auto-filters by the authenticated user's email, which chat_service
sets on a ContextVar before dispatching tool calls.

Project scoping (§21): when the current conversation is filed into a
project, the tool auto-binds that project_id unless the caller passes
``project_id=None`` explicitly. The underlying store does two-phase
search (project first, user-global fallback) and returns a
``sources_used`` field so the model can honestly tell the user
whether the result came from their project or from the broader corpus.

``view_attachment`` (§5 re-view) resolves a document_id to an image
the user uploaded (or the model pasted back into documents via the
§5 funnel path) and schedules it for injection as a multimodal user
message on the next tool-loop iteration. The tool's own return value
is a small metadata marker - the actual bytes are read separately by
vision.build_view_attachment_followup so they never travel through
the tool-result text channel (which the model would otherwise see as
a wall of base64 garbage).
"""

import os
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


# ---------------------------------------------------------------------------
# view_attachment (§5 deferred re-view capability)
# ---------------------------------------------------------------------------

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


async def view_attachment(document_id: str) -> dict:
    """
    Re-attach a previously uploaded image to the next model turn so it
    can answer follow-up questions that reference it ("what was that
    screenshot I sent earlier?"). The model learns document_ids from
    inline ``[Attachments: ...]`` markers injected into past-turn
    content by chat_context.assemble_context.

    The tool does NOT return the image bytes - that would produce a
    base64 wall of garbage in the tool_result text. Instead it returns
    a small marker and chat_service's follow-up builder reads the
    bytes from disk and appends a synthetic multimodal user message
    on the next tool-loop iteration. This mirrors the §3 plot-critique
    path that already injects sandbox artifacts the same way.
    """
    import document_store  # lazy import — avoids circular init

    user_email = current_user_email.get()
    if not user_email:
        return {
            "error": "view_attachment requires an authenticated user context",
        }
    if not isinstance(document_id, str) or not document_id.strip():
        return {"error": "document_id must be a non-empty string"}

    path = document_store.get_document_file_path(user_email, document_id)
    if not path:
        return {
            "error": f"document not found: {document_id!r}",
        }

    ext = os.path.splitext(path)[1].lower()
    if ext not in _IMAGE_EXTS:
        return {
            "error": (
                f"view_attachment only supports image documents; "
                f"{os.path.basename(path)} is {ext!r}"
            ),
        }

    # Return a compact marker. The actual bytes are fetched again by
    # vision.build_view_attachment_followup, which is called by
    # chat_service after the tool result is yielded. Duplicating the
    # disk read costs nothing (OS page cache) and keeps the bytes out
    # of the JSON blob that becomes a role:tool message.
    return {
        "viewing": True,
        "document_id": document_id,
        "filename": os.path.basename(path),
        "content_type": f"image/{'jpeg' if ext in ('.jpg', '.jpeg') else ext.lstrip('.')}",
        "message": (
            "The attachment has been scheduled for injection. On the "
            "next turn you will see it as a multimodal user message "
            "preceded by '[view_attachment]'."
        ),
    }
