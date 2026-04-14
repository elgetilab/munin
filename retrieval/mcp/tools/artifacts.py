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
