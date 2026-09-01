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
# list_documents (2026-09-01)
# ---------------------------------------------------------------------------

# Cap on documents returned in one call. A user with hundreds of uploads would
# otherwise blow the tool-result budget, and the model only needs enough to
# answer "what have I got" honestly. `total` always reports the real figure so
# a truncated page can never be mistaken for the whole store.
_LIST_LIMIT_DEFAULT = 50
_LIST_LIMIT_MAX = 200


async def list_documents(limit: int = _LIST_LIMIT_DEFAULT) -> dict:
    """Enumerate the user's uploaded documents. No query, no ranking.

    WHY this exists. `search_user_docs` is semantic: it needs a query and
    returns matching chunks. Asked "how many papers did I upload?" or "list my
    documents", the model could only guess topics and search for them, and a
    2026-08-20 user watched it run four different searches, get zero results
    each time, and conclude his store was probably empty. Its own summary of
    the options was accurate and useless: vocabulary mismatch, an empty store,
    or a different account, with no way to tell which. An inventory answers
    that question directly, and `GET /api/documents` has served exactly this to
    the web UI the whole time.
    """
    import document_store  # lazy import — avoids circular init

    user_email = current_user_email.get()
    if not user_email:
        return {
            "documents": [],
            "error": "list_documents requires an authenticated user context",
        }
    try:
        limit = max(1, min(_LIST_LIMIT_MAX, int(limit)))
    except (TypeError, ValueError):
        limit = _LIST_LIMIT_DEFAULT

    docs = await document_store.list_documents(user_email=user_email)
    total = len(docs)
    page = docs[:limit]
    out: dict = {
        "documents": [
            {
                "document_id": d.get("document_id"),
                "filename": d.get("filename"),
                "chunks": d.get("chunks"),
                # "embedded" = searchable via search_user_docs. "stored" = the
                # file is on disk with no embeddings, which is correct for an
                # image and a defect for anything else. Surfaced rather than
                # flattened, because a document that exists but cannot be
                # searched is precisely the case the model must not describe
                # as "you have this" or as "you have nothing".
                "status": d.get("status"),
                "uploaded_at": d.get("upload_time"),
            }
            for d in page
        ],
        "total": total,
        "returned": len(page),
    }
    n_unsearchable = sum(1 for d in page if d.get("status") != "embedded")
    if n_unsearchable:
        out["note_unsearchable"] = (
            f"{n_unsearchable} of these are stored but not embedded, so "
            f"search_user_docs cannot reach them. Images are expected here; "
            f"a text document with status 'stored' failed extraction."
        )
    if total == 0:
        # Say what an empty result MEANS. The store being genuinely empty and
        # the tool being unable to see it are different situations, and the
        # model has no other way to tell them apart.
        out["note"] = (
            "This account has no uploaded documents. This is an inventory of "
            "the store, not a search, so an empty result means the store is "
            "empty rather than that a query missed."
        )
    elif total > len(page):
        out["note"] = (
            f"Showing {len(page)} of {total} documents. Raise `limit` or say "
            f"so when reporting, rather than implying this is the full list."
        )
    return out


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
