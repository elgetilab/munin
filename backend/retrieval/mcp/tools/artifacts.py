"""
MCP tools: create_artifact, read_artifact, update_artifact, list_artifacts (§22).

These let the chat model maintain versioned documents within the
current conversation: papers, grant proposals, LaTeX sources, python
scripts, SVG figures, anything textual the user wants to iterate on
rather than re-scroll through in the chat history.

Stage A scope:

- Text content only. Binary artifacts (PNG plots, PDFs) still flow
  through the §2/§3 sandbox path for now; Stage C will unify them.
- Full-content updates only. Diff-based updates are Stage B.
- Conversation-scoped. An artifact belongs to exactly one
  conversation; there's no sharing across chats.
- Refused in ephemeral chats (matches §9 memory, §25 profile, §2
  sandbox privacy contract).

All four tools read ``current_user_email`` and
``current_conversation_id`` from contextvars chat_service binds
before dispatching. The HTTP-side counterparts live in
``retrieval/main.py`` for user-driven side-panel edits.
"""

from __future__ import annotations

from typing import Optional

from ..context import current_user_email, current_conversation_id


def _require_persistent_conversation() -> tuple[Optional[str], Optional[str], Optional[dict]]:
    """
    Return ``(user_email, conversation_id, None)`` if the current
    request is eligible to use the artifact store, or
    ``(None, None, error_response)`` otherwise. Centralises the
    ephemeral refusal so all four tools share one gate.
    """
    user_email = current_user_email.get()
    if not user_email:
        return None, None, {
            "error": "artifact tools require an authenticated user context",
        }
    conv_id = current_conversation_id.get() or ""
    if not conv_id:
        return None, None, {
            "error": "artifact tools require an active conversation context",
        }
    if conv_id.startswith("ephemeral-"):
        return None, None, {
            "error": (
                "artifacts are not available in ephemeral chats by design. "
                "Switch to a regular (persistent) chat to create or edit "
                "artifacts."
            ),
        }
    return user_email, conv_id, None


async def create_artifact(
    title: str,
    content: str,
    content_type: str,
    language: Optional[str] = None,
    change_summary: Optional[str] = None,
) -> dict:
    """Create a new versioned artifact in the current conversation."""
    import artifact_store  # lazy to avoid circular init

    user_email, conv_id, refusal = _require_persistent_conversation()
    if refusal is not None:
        return refusal
    assert user_email is not None and conv_id is not None
    try:
        return await artifact_store.create_artifact(
            user_email=user_email,
            conversation_id=conv_id,
            title=title,
            content=content,
            content_type=content_type,
            language=language,
            change_summary=change_summary,
        )
    except artifact_store.ArtifactError as exc:
        return {"error": str(exc)}


async def read_artifact(
    artifact_id: str,
    version: Optional[int] = None,
) -> dict:
    """Load the latest (or a specific) version of an artifact."""
    import artifact_store

    user_email, conv_id, refusal = _require_persistent_conversation()
    if refusal is not None:
        return refusal
    assert user_email is not None and conv_id is not None
    if not isinstance(artifact_id, str) or not artifact_id.strip():
        return {"error": "artifact_id must be a non-empty string"}
    row = await artifact_store.get_artifact_version(
        user_email=user_email,
        conversation_id=conv_id,
        artifact_id=artifact_id,
        version=version,
    )
    if row is None:
        return {"error": f"artifact not found: {artifact_id!r}"}
    return row


async def update_artifact(
    artifact_id: str,
    content: str,
    change_summary: Optional[str] = None,
    is_diff: bool = False,
    base_version: Optional[int] = None,
) -> dict:
    """
    Append a new version to an existing artifact.

    Two modes:
      * Full content (default): ``content`` is the complete new text.
      * Diff (``is_diff=True``): ``content`` is a unified diff to
        apply to ``base_version`` (or the current latest if omitted).

    Optional ``base_version`` guards against concurrent edits: if it
    does not match the artifact's current latest_version the call is
    rejected with a stale-base error so the caller can re-read and
    retry instead of silently clobbering whatever landed in between.
    """
    import artifact_store

    user_email, conv_id, refusal = _require_persistent_conversation()
    if refusal is not None:
        return refusal
    assert user_email is not None and conv_id is not None
    if not isinstance(artifact_id, str) or not artifact_id.strip():
        return {"error": "artifact_id must be a non-empty string"}
    try:
        row = await artifact_store.update_artifact(
            user_email=user_email,
            conversation_id=conv_id,
            artifact_id=artifact_id,
            content=content,
            change_summary=change_summary,
            created_by="assistant",
            is_diff=bool(is_diff),
            base_version=base_version,
        )
    except artifact_store.ArtifactError as exc:
        return {"error": str(exc)}
    if row is None:
        return {"error": f"artifact not found: {artifact_id!r}"}
    return row


async def list_artifacts() -> dict:
    """List every artifact in the current conversation (metadata only)."""
    import artifact_store

    user_email, conv_id, refusal = _require_persistent_conversation()
    if refusal is not None:
        return refusal
    assert user_email is not None and conv_id is not None
    artifacts = await artifact_store.list_artifacts(
        user_email=user_email, conversation_id=conv_id,
    )
    return {
        "artifacts": artifacts,
        "total": len(artifacts),
        "conversation_id": conv_id,
    }


# ---------------------------------------------------------------------------
# save_artifact_to_documents (§22 Stage C)
# ---------------------------------------------------------------------------

# Map content types to sensible filename extensions when the caller
# doesn't provide an explicit filename. Covers the common model-written
# types; anything unknown falls back to ``.txt``.
_EXT_BY_CONTENT_TYPE = {
    "text/markdown": ".md",
    "text/latex": ".tex",
    "text/plain": ".txt",
    "text/html": ".html",
    "application/python": ".py",
    "application/json": ".json",
    "image/svg+xml": ".svg",
}


def _derive_filename(title: str, content_type: str) -> str:
    """
    Turn an artifact title + content_type into a sensible filename for
    the documents store. Strips path separators, caps length at 120
    chars, appends the extension for known content types.
    """
    base = (title or "artifact").strip().replace("/", "_").replace("\\", "_")
    base = base[:120] or "artifact"
    # Don't append an extension if the title already has one matching
    # the content type.
    wanted_ext = _EXT_BY_CONTENT_TYPE.get(
        (content_type or "").lower(), ".txt"
    )
    if base.lower().endswith(wanted_ext):
        return base
    return base + wanted_ext


async def save_artifact_to_documents(
    artifact_id: str,
    filename: Optional[str] = None,
) -> dict:
    """
    Promote an artifact (model-written OR sandbox-generated) into the
    user's persistent document store so it can be RAG-searched in
    future conversations and referenced via ``document:<doc_id>``
    image_url inputs.

    For model-written artifacts the latest version's content is
    uploaded inline. For sandbox-generated artifacts the bytes are
    fetched from the sandbox sidecar via the existing
    ``/api/artifacts/{cid}/{aid}`` proxy path. ``filename`` is
    optional - if omitted we derive one from the artifact's title +
    content_type.
    """
    import artifact_store
    import document_store
    import os as _os

    user_email, conv_id, refusal = _require_persistent_conversation()
    if refusal is not None:
        return refusal
    assert user_email is not None and conv_id is not None
    if not isinstance(artifact_id, str) or not artifact_id.strip():
        return {"error": "artifact_id must be a non-empty string"}

    row = await artifact_store.get_artifact_version(
        user_email=user_email,
        conversation_id=conv_id,
        artifact_id=artifact_id,
    )
    if row is None:
        return {"error": f"artifact not found: {artifact_id!r}"}

    source = row.get("source") or "model_written"
    content_type = row.get("content_type") or "text/plain"
    title = row.get("title") or "artifact"

    # Resolve bytes + effective filename per source.
    if source == "sandbox_generated":
        # Fetch the real bytes from the sandbox container via the
        # same proxy path the HTTP surface uses. The sandbox URL is
        # controlled by env var; do not trust client-provided URLs.
        import httpx
        sandbox_url = _os.environ.get("SANDBOX_URL", "http://sandbox:8090")
        sandbox_artifact_id = ""
        external_url = row.get("external_url") or ""
        # external_url has the shape /api/artifacts/{cid}/{aid};
        # extract the trailing segment as the sandbox artifact id.
        if "/" in external_url:
            sandbox_artifact_id = external_url.rsplit("/", 1)[-1]
        if not sandbox_artifact_id:
            return {
                "error": (
                    "sandbox artifact has no resolvable external id; "
                    "cannot fetch bytes"
                )
            }
        fetch_url = (
            f"{sandbox_url}/artifacts/{conv_id}/{sandbox_artifact_id}"
        )
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.get(fetch_url)
        except httpx.RequestError as exc:
            return {
                "error": f"sandbox unreachable: {type(exc).__name__}: {exc}"
            }
        if r.status_code != 200:
            return {
                "error": (
                    f"sandbox returned {r.status_code} when fetching "
                    f"artifact bytes"
                )
            }
        file_bytes = r.content
        effective_filename = (
            filename or row.get("filename") or _derive_filename(title, content_type)
        )
    else:
        # Model-written: content lives inline in artifact_versions.
        text_content = row.get("content") or ""
        file_bytes = text_content.encode("utf-8")
        effective_filename = filename or _derive_filename(title, content_type)

    try:
        uploaded = await document_store.upload_document(
            filename=effective_filename,
            file_bytes=file_bytes,
            user_email=user_email,
            conversation_id=conv_id,
        )
    except ValueError as exc:
        return {"error": f"upload rejected: {exc}"}
    except Exception as exc:
        return {
            "error": f"upload failed: {type(exc).__name__}: {exc}"
        }

    return {
        "saved": True,
        "artifact_id": artifact_id,
        "source": source,
        "document_id": uploaded.get("document_id"),
        "filename": uploaded.get("filename"),
        "status": uploaded.get("status"),
        "chunks": uploaded.get("chunks"),
    }
