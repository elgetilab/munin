"""
Versioned document artifacts (§22 Stage A).

An artifact is a versioned document associated with a single
conversation. The chat model creates artifacts via ``create_artifact``,
updates them via ``update_artifact`` (new version), and the user can
edit them directly via ``PATCH /api/chats/{cid}/artifacts/{aid}`` in
the side panel. Every write lands in ``artifact_versions`` as a full
content snapshot (no diff chain): storage is cheap, rollback is
trivial, diff views are a client-side concern.

Scope rules:

- **Conversation-scoped**: artifacts belong to the conversation they
  were created in. Projects (§21) provide organisational context but
  don't share artifacts across conversations. If a user wants the
  same artifact in another chat, they copy the content.
- **Text-only in Stage A**: content is stored as TEXT in SQLite. The
  500 KB per-version cap protects against pathological inputs but
  comfortably fits a full paper draft (~100k words). Binary
  artifacts (PNG plots, PDFs, xlsx) stay on disk via the §2/§3
  sandbox pipeline until Stage C unifies the two concepts.
- **Full-content updates in Stage A**: ``update_artifact`` takes a
  replacement content string. Diff-based updates (to save tokens on
  large documents) are deferred to Stage B.

Context injection is summary-only: chat_service prepends an
``=== ACTIVE ARTIFACTS ===`` block listing title + type + version +
word count per artifact. The model fetches full content on demand
via ``read_artifact`` so context overhead stays bounded regardless
of how many artifacts a conversation accumulates.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from chat_store import _iso_now, _row_to_dict, get_db


# Content cap per version. 500 KB of text is ~100k words, comfortably
# larger than any real paper. Anything over this is almost certainly
# a pathological paste and gets rejected with a clear error rather
# than silently truncated.
MAX_CONTENT_BYTES = 500 * 1024
MAX_TITLE_CHARS = 200
MAX_CHANGE_SUMMARY_CHARS = 500


class ArtifactError(ValueError):
    """Raised for any invalid artifact input. Callers convert to the
    tool-result ``error`` shape or HTTP 400."""


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate_title(title: Any) -> str:
    if not isinstance(title, str):
        raise ArtifactError("title must be a string")
    title = title.strip()
    if not title:
        raise ArtifactError("title must be non-empty")
    if len(title) > MAX_TITLE_CHARS:
        raise ArtifactError(
            f"title exceeds {MAX_TITLE_CHARS}-character cap "
            f"(got {len(title)})"
        )
    return title


def _validate_content_type(content_type: Any) -> str:
    if not isinstance(content_type, str) or not content_type.strip():
        raise ArtifactError("content_type is required")
    return content_type.strip()


def _validate_content(content: Any) -> str:
    if not isinstance(content, str):
        raise ArtifactError("content must be a string")
    # Byte length (not char length) matches the server-side storage cost
    # and protects against unicode pathologies.
    size = len(content.encode("utf-8"))
    if size > MAX_CONTENT_BYTES:
        raise ArtifactError(
            f"content exceeds {MAX_CONTENT_BYTES} byte cap "
            f"(got {size} bytes)"
        )
    return content


def _validate_change_summary(summary: Any) -> Optional[str]:
    if summary is None:
        return None
    if not isinstance(summary, str):
        raise ArtifactError("change_summary must be a string")
    summary = summary.strip()
    if not summary:
        return None
    if len(summary) > MAX_CHANGE_SUMMARY_CHARS:
        raise ArtifactError(
            f"change_summary exceeds {MAX_CHANGE_SUMMARY_CHARS}-character cap"
        )
    return summary


# ---------------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------------

async def _verify_conversation_owned(
    conversation_id: str, user_email: str
) -> bool:
    """Check that ``user_email`` owns the conversation. Used as a guard
    before any artifact write so the caller can't create artifacts
    under someone else's conversation id."""
    db = await get_db()
    cur = await db.execute(
        "SELECT 1 FROM conversations WHERE id = ? AND user_email = ?",
        (conversation_id, user_email),
    )
    row = await cur.fetchone()
    return row is not None


async def _verify_artifact_owned(
    artifact_id: str,
    conversation_id: str,
    user_email: str,
) -> Optional[dict]:
    """
    Return the artifact row as a dict if it exists, belongs to the
    given conversation, and the conversation belongs to the user.
    Returns None otherwise — the caller converts None into a 404.
    """
    db = await get_db()
    cur = await db.execute(
        "SELECT a.* FROM artifacts a "
        "WHERE a.id = ? AND a.conversation_id = ? AND a.user_email = ?",
        (artifact_id, conversation_id, user_email),
    )
    return _row_to_dict(await cur.fetchone())


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

async def create_artifact(
    user_email: str,
    conversation_id: str,
    title: str,
    content: str,
    content_type: str,
    language: Optional[str] = None,
    change_summary: Optional[str] = None,
) -> dict:
    """
    Create a brand-new artifact with a single v1 snapshot. Returns the
    artifact metadata + version info suitable for an SSE event.
    """
    if not await _verify_conversation_owned(conversation_id, user_email):
        raise ArtifactError("conversation not found or not owned by user")

    title = _validate_title(title)
    content_type = _validate_content_type(content_type)
    content = _validate_content(content)
    summary = _validate_change_summary(change_summary)
    if language is not None and not isinstance(language, str):
        raise ArtifactError("language must be a string or null")

    db = await get_db()
    aid = f"art_{uuid.uuid4().hex[:12]}"
    now = _iso_now()

    await db.execute(
        """
        INSERT INTO artifacts
            (id, conversation_id, user_email, title, content_type,
             language, latest_version, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
        """,
        (
            aid, conversation_id, user_email, title, content_type,
            language, now, now,
        ),
    )
    await db.execute(
        """
        INSERT INTO artifact_versions
            (artifact_id, version, content, change_summary,
             created_at, created_by)
        VALUES (?, 1, ?, ?, ?, 'assistant')
        """,
        (aid, content, summary, now),
    )
    await db.commit()
    return {
        "id": aid,
        "conversation_id": conversation_id,
        "title": title,
        "content_type": content_type,
        "language": language,
        "version": 1,
        "created_at": now,
        "updated_at": now,
    }


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

async def get_artifact_version(
    user_email: str,
    conversation_id: str,
    artifact_id: str,
    version: Optional[int] = None,
) -> Optional[dict]:
    """
    Load the latest (or a specific) version of an artifact owned by
    this user within this conversation. Returns None for any ownership
    or lookup failure so callers can distinguish 404 paths.
    """
    meta = await _verify_artifact_owned(artifact_id, conversation_id, user_email)
    if meta is None:
        return None

    target_version = version if version is not None else int(meta["latest_version"])
    db = await get_db()
    cur = await db.execute(
        """
        SELECT version, content, change_summary, created_at, created_by
        FROM artifact_versions
        WHERE artifact_id = ? AND version = ?
        """,
        (artifact_id, target_version),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    return {
        "id": artifact_id,
        "conversation_id": conversation_id,
        "title": meta["title"],
        "content_type": meta["content_type"],
        "language": meta["language"],
        "latest_version": int(meta["latest_version"]),
        "version": int(row["version"]),
        "content": row["content"],
        "change_summary": row["change_summary"],
        "created_at": row["created_at"],
        "created_by": row["created_by"],
        "artifact_created_at": meta["created_at"],
        "artifact_updated_at": meta["updated_at"],
    }


async def list_artifacts(
    user_email: str,
    conversation_id: str,
) -> list[dict]:
    """
    Return one row per artifact in the conversation (latest-version
    metadata only, no content). Used for the context-injection
    summary block and for the HTTP list endpoint.
    """
    if not await _verify_conversation_owned(conversation_id, user_email):
        return []
    db = await get_db()
    cur = await db.execute(
        """
        SELECT
            a.id, a.title, a.content_type, a.language,
            a.latest_version, a.created_at, a.updated_at,
            v.content AS latest_content
        FROM artifacts a
        JOIN artifact_versions v
          ON v.artifact_id = a.id AND v.version = a.latest_version
        WHERE a.conversation_id = ? AND a.user_email = ?
        ORDER BY a.updated_at DESC
        """,
        (conversation_id, user_email),
    )
    rows = await cur.fetchall()
    out = []
    for row in rows:
        content = row["latest_content"] or ""
        out.append({
            "id": row["id"],
            "title": row["title"],
            "content_type": row["content_type"],
            "language": row["language"],
            "latest_version": int(row["latest_version"]),
            "word_count": _count_words(content),
            "byte_size": len(content.encode("utf-8")),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        })
    return out


def _count_words(content: str) -> int:
    # Simple whitespace-split word count. Matches what researchers
    # mean by "how long is my draft" and is cheap to compute.
    return len([w for w in content.split() if w])


# ---------------------------------------------------------------------------
# Update (full content — diffs deferred to Stage B)
# ---------------------------------------------------------------------------

async def update_artifact(
    user_email: str,
    conversation_id: str,
    artifact_id: str,
    content: str,
    change_summary: Optional[str] = None,
    created_by: str = "assistant",
) -> Optional[dict]:
    """
    Append a new version to an existing artifact with full replacement
    content. ``created_by`` is "assistant" when called from the MCP
    tool and "user" when called from the HTTP PATCH path.
    """
    meta = await _verify_artifact_owned(artifact_id, conversation_id, user_email)
    if meta is None:
        return None

    content = _validate_content(content)
    summary = _validate_change_summary(change_summary)
    if created_by not in ("assistant", "user"):
        raise ArtifactError("created_by must be 'assistant' or 'user'")

    db = await get_db()
    now = _iso_now()
    next_version = int(meta["latest_version"]) + 1

    await db.execute(
        """
        INSERT INTO artifact_versions
            (artifact_id, version, content, change_summary,
             created_at, created_by)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (artifact_id, next_version, content, summary, now, created_by),
    )
    await db.execute(
        "UPDATE artifacts SET latest_version = ?, updated_at = ? "
        "WHERE id = ?",
        (next_version, now, artifact_id),
    )
    await db.commit()
    return {
        "id": artifact_id,
        "conversation_id": conversation_id,
        "title": meta["title"],
        "content_type": meta["content_type"],
        "language": meta["language"],
        "version": next_version,
        "change_summary": summary,
        "created_by": created_by,
        "updated_at": now,
    }


# ---------------------------------------------------------------------------
# System prompt rendering
# ---------------------------------------------------------------------------

def build_artifact_summary_block(artifacts: list[dict]) -> Optional[str]:
    """
    Render the ``=== ACTIVE ARTIFACTS ===`` block chat_service
    prepends to the system prompt. Summary-only by design:
    title + type + version + word count per artifact. The model
    calls ``read_artifact(id)`` when it needs the actual content.
    Returns None when there are no artifacts so the caller can skip
    the block entirely rather than emit an empty header.
    """
    if not artifacts:
        return None
    lines = ["=== ACTIVE ARTIFACTS ==="]
    for i, a in enumerate(artifacts, start=1):
        title = (a.get("title") or "").strip() or "untitled"
        ctype = a.get("content_type") or "text/plain"
        version = a.get("latest_version") or 1
        words = a.get("word_count") or 0
        lines.append(
            f'{i}. "{title}" ({ctype}, v{version}, {words} words)'
        )
    lines.append("")
    lines.append(
        "Use read_artifact(artifact_id) to see the current content of "
        "any of these. Use update_artifact(artifact_id, content) to "
        "produce a new version after editing. Use create_artifact(...) "
        "to start a new document. Only reference an artifact by its id "
        "when interacting with these tools."
    )
    lines.append("=== END ACTIVE ARTIFACTS ===")
    return "\n".join(lines)
