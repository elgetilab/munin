"""
Project persistence (§21) — top-level scoped workspaces.

A Project is a user-owned organizational unit that gathers conversations
and documents under one set of instructions and (optionally) a default
persona override. Lives in the same chats.db as conversations, but gets
its own module so the growing pile of helpers in chat_store.py doesn't
swallow the concept.

Design summary (locked 2026-04-14):

- One user, one project, many conversations. No multi-user sharing yet.
- Deleting a project unfiles its conversations and its docs (they
  become user-global again). No tombstones, no reaper - we preserve
  data over tidiness.
- Archived projects are hidden from default listings; opt-in to see
  them via a query param.
- Instructions are capped at 2000 chars for context-budget hygiene.
- Per-user ownership is enforced on every query via a user_email
  check; there is no admin-visible "all projects" listing.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from chat_store import _iso_now, _row_to_dict, get_db


PROJECT_INSTRUCTIONS_LIMIT = 2000
PROJECT_NAME_LIMIT = 200
PROJECT_DESCRIPTION_LIMIT = 1000
PROJECT_FIELDS = (
    "name",
    "description",
    "instructions",
    "default_persona",
    "archived",
)


def _row_to_project(row: Optional[Any]) -> Optional[dict]:
    d = _row_to_dict(row)
    if d is None:
        return None
    d["archived"] = bool(d.get("archived"))
    return d


def validate_project_input(payload: dict) -> dict:
    """
    Sanity-check a POST/PATCH project body. Returns the cleaned dict
    with only the fields we recognise; raises ``ValueError`` on any
    invalid value. Unknown keys are silently dropped for forward-compat.
    """
    if not isinstance(payload, dict):
        raise ValueError("project body must be an object")
    cleaned: dict[str, Any] = {}
    for field in PROJECT_FIELDS:
        if field not in payload:
            continue
        value = payload[field]
        if field == "archived":
            cleaned[field] = bool(value)
            continue
        if value is None or value == "":
            cleaned[field] = None
            continue
        if not isinstance(value, str):
            raise ValueError(f"{field} must be a string")
        if field == "name" and len(value) > PROJECT_NAME_LIMIT:
            raise ValueError(
                f"name exceeds {PROJECT_NAME_LIMIT}-character cap "
                f"(got {len(value)})"
            )
        if field == "description" and len(value) > PROJECT_DESCRIPTION_LIMIT:
            raise ValueError(
                f"description exceeds {PROJECT_DESCRIPTION_LIMIT}-character cap "
                f"(got {len(value)})"
            )
        if field == "instructions" and len(value) > PROJECT_INSTRUCTIONS_LIMIT:
            raise ValueError(
                f"instructions exceed {PROJECT_INSTRUCTIONS_LIMIT}-character cap "
                f"(got {len(value)})"
            )
        cleaned[field] = value
    return cleaned


async def create_project(user_email: str, payload: dict) -> dict:
    if not payload.get("name"):
        raise ValueError("name is required")
    db = await get_db()
    pid = f"proj_{uuid.uuid4().hex[:12]}"
    now = _iso_now()
    await db.execute(
        """
        INSERT INTO projects
            (id, user_email, name, description, instructions,
             default_persona, archived, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)
        """,
        (
            pid,
            user_email,
            payload.get("name"),
            payload.get("description"),
            payload.get("instructions"),
            payload.get("default_persona"),
            now,
            now,
        ),
    )
    await db.commit()
    created = await get_project(pid, user_email)
    assert created is not None
    return created


async def get_project(
    project_id: str,
    user_email: str,
    *,
    include_counts: bool = False,
) -> Optional[dict]:
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM projects WHERE id = ? AND user_email = ?",
        (project_id, user_email),
    )
    row = await cur.fetchone()
    proj = _row_to_project(row)
    if proj is None:
        return None
    if include_counts:
        conv_cur = await db.execute(
            "SELECT COUNT(*) AS n FROM conversations "
            "WHERE user_email = ? AND project_id = ?",
            (user_email, project_id),
        )
        conv_row = await conv_cur.fetchone()
        proj["conversation_count"] = int(conv_row["n"]) if conv_row else 0
        # Document count is sourced from document_store at call time so
        # we don't have to couple project_store to Qdrant. Callers that
        # need the doc count can add it; we leave it out of the default
        # payload to keep this helper free of Qdrant dependencies.
    return proj


async def list_projects(
    user_email: str,
    *,
    include_archived: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    db = await get_db()
    where = ["user_email = ?"]
    params: list[Any] = [user_email]
    if not include_archived:
        where.append("archived = 0")
    where_sql = " AND ".join(where)
    cur = await db.execute(
        f"""
        SELECT p.*,
            (SELECT COUNT(*) FROM conversations c
             WHERE c.user_email = p.user_email AND c.project_id = p.id)
             AS conversation_count
        FROM projects p
        WHERE {where_sql}
        ORDER BY p.updated_at DESC
        LIMIT ? OFFSET ?
        """,
        params + [limit, offset],
    )
    rows = await cur.fetchall()
    projects = []
    for row in rows:
        proj = _row_to_project(row)
        if proj is None:
            continue
        projects.append(proj)
    count_cur = await db.execute(
        f"SELECT COUNT(*) AS n FROM projects WHERE {where_sql}",
        params,
    )
    count_row = await count_cur.fetchone()
    total = int(count_row["n"]) if count_row else 0
    return {"projects": projects, "total": total}


async def update_project(
    project_id: str,
    user_email: str,
    fields: dict,
) -> Optional[dict]:
    if not fields:
        return await get_project(project_id, user_email)
    db = await get_db()
    set_parts: list[str] = []
    params: list[Any] = []
    for field in PROJECT_FIELDS:
        if field not in fields:
            continue
        value = fields[field]
        if field == "archived":
            set_parts.append("archived = ?")
            params.append(1 if value else 0)
            continue
        set_parts.append(f"{field} = ?")
        params.append(value)
    if not set_parts:
        return await get_project(project_id, user_email)
    set_parts.append("updated_at = ?")
    params.append(_iso_now())
    params.extend([project_id, user_email])
    cur = await db.execute(
        f"UPDATE projects SET {', '.join(set_parts)} "
        f"WHERE id = ? AND user_email = ?",
        params,
    )
    await db.commit()
    if cur.rowcount == 0:
        return None
    return await get_project(project_id, user_email)


async def delete_project(project_id: str, user_email: str) -> bool:
    """
    Hard-delete the project row and unfile its conversations. Documents
    are NOT touched here - the caller (main.py) sweeps document_store
    and unfiles Qdrant payloads separately so this module stays free of
    the Qdrant dependency.
    """
    db = await get_db()
    cur = await db.execute(
        "SELECT id FROM projects WHERE id = ? AND user_email = ?",
        (project_id, user_email),
    )
    if await cur.fetchone() is None:
        return False
    # Unfile conversations first so the delete of the project row can
    # succeed cleanly (SQLite FK deferral is fussy and this is trivially
    # equivalent).
    await db.execute(
        "UPDATE conversations SET project_id = NULL "
        "WHERE user_email = ? AND project_id = ?",
        (user_email, project_id),
    )
    await db.execute(
        "DELETE FROM projects WHERE id = ? AND user_email = ?",
        (project_id, user_email),
    )
    await db.commit()
    return True


# ---------------------------------------------------------------------------
# Conversation filing
# ---------------------------------------------------------------------------

async def file_conversation(
    project_id: str,
    conversation_id: str,
    user_email: str,
) -> Optional[dict]:
    """Move a conversation into a project. Returns the updated conversation
    meta row, or None if either the project or the conversation doesn't
    exist / is owned by someone else."""
    proj = await get_project(project_id, user_email)
    if proj is None:
        return None
    db = await get_db()
    cur = await db.execute(
        "UPDATE conversations SET project_id = ?, updated_at = ? "
        "WHERE id = ? AND user_email = ?",
        (project_id, _iso_now(), conversation_id, user_email),
    )
    await db.commit()
    if cur.rowcount == 0:
        return None
    return {
        "conversation_id": conversation_id,
        "project_id": project_id,
    }


async def unfile_conversation(
    conversation_id: str,
    user_email: str,
) -> bool:
    db = await get_db()
    cur = await db.execute(
        "UPDATE conversations SET project_id = NULL, updated_at = ? "
        "WHERE id = ? AND user_email = ?",
        (_iso_now(), conversation_id, user_email),
    )
    await db.commit()
    return cur.rowcount > 0


async def get_project_for_conversation(
    conversation_id: str,
    user_email: str,
) -> Optional[dict]:
    """Load the project (if any) a conversation is filed into. Returns
    None for unfiled conversations."""
    db = await get_db()
    cur = await db.execute(
        "SELECT project_id FROM conversations "
        "WHERE id = ? AND user_email = ?",
        (conversation_id, user_email),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    pid = row["project_id"]
    if not pid:
        return None
    return await get_project(pid, user_email)


# ---------------------------------------------------------------------------
# System prompt rendering
# ---------------------------------------------------------------------------

def build_project_prompt_block(project: Optional[dict]) -> Optional[str]:
    """
    Render a project dict as the ``=== PROJECT CONTEXT ===`` block that
    chat_service prepends to the system prompt. Returns None if the
    project is missing or has no user-visible content, so the caller
    can skip the block rather than emit an empty header.
    """
    if not project:
        return None
    name = (project.get("name") or "").strip()
    description = (project.get("description") or "").strip()
    instructions = (project.get("instructions") or "").strip()
    if not name and not description and not instructions:
        return None
    parts: list[str] = ["=== PROJECT CONTEXT ==="]
    if name:
        parts.append(f'You are working inside project "{name}".')
    if description:
        parts.append("")
        parts.append(f"Description: {description}")
    if instructions:
        parts.append("")
        parts.append(f"Instructions: {instructions}")
    parts.append("")
    parts.append(
        "The user has uploaded project-scoped documents searchable via "
        "search_user_docs (automatically scoped to this project; the "
        "tool falls back to the user's global documents only if no "
        "project-scoped hits are found)."
    )
    parts.append("=== END PROJECT CONTEXT ===")
    return "\n".join(parts)
