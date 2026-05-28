"""
Chat persistence store (SQLite + FTS5) for the Munin retrieval service.

Schema: two tables (`conversations`, `messages`) + an FTS5 virtual table
(`messages_fts`) synced via triggers. All access is async via aiosqlite.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import aiosqlite

CHATS_DB_PATH = os.getenv("CHATS_DB_PATH", "/data/chats.db")

_db: Optional[aiosqlite.Connection] = None


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _row_to_dict(row: aiosqlite.Row | None) -> Optional[dict]:
    return dict(row) if row is not None else None


def _parse_json(value: Any) -> Any:
    if value is None or value == "":
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def _message_row_to_dict(row: aiosqlite.Row) -> dict:
    # attachments column was added after the initial schema (§5). Older
    # rows simply don't have the key; we surface None in that case so
    # callers can treat "missing" and "null" the same way.
    try:
        attachments = _parse_json(row["attachments"])
    except (IndexError, KeyError):
        attachments = None
    return {
        "id": row["id"],
        "role": row["role"],
        "content": row["content"],
        "thinking": row["thinking"],
        "tool_calls": _parse_json(row["tool_calls"]),
        "rag_context": _parse_json(row["rag_context"]),
        "attachments": attachments,
        "created_at": row["created_at"],
        "token_count": row["token_count"],
        "index_in_conversation": row["index_in_conversation"],
    }


async def init_db() -> aiosqlite.Connection:
    """Initialize the chat database (schema + indexes + FTS triggers)."""
    global _db
    if _db is not None:
        return _db

    parent = os.path.dirname(CHATS_DB_PATH)
    if parent:
        os.makedirs(parent, exist_ok=True)

    _db = await aiosqlite.connect(CHATS_DB_PATH)
    _db.row_factory = aiosqlite.Row
    await _db.execute("PRAGMA journal_mode=WAL")
    await _db.execute("PRAGMA foreign_keys=ON")

    await _db.executescript(
        """
        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY,
            user_email TEXT NOT NULL,
            title TEXT,
            persona TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            summary TEXT,
            summary_through_index INTEGER,
            pinned INTEGER NOT NULL DEFAULT 0,
            pinned_at TEXT
        );

        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            index_in_conversation INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            thinking TEXT,
            tool_calls TEXT,
            rag_context TEXT,
            attachments TEXT,
            created_at TEXT NOT NULL,
            token_count INTEGER,
            FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_messages_conversation
            ON messages(conversation_id, index_in_conversation);

        CREATE INDEX IF NOT EXISTS idx_conversations_user
            ON conversations(user_email, updated_at DESC);

        CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
            content,
            conversation_id UNINDEXED,
            tokenize = 'porter unicode61'
        );

        CREATE TRIGGER IF NOT EXISTS messages_fts_insert
        AFTER INSERT ON messages BEGIN
            INSERT INTO messages_fts (rowid, content, conversation_id)
            VALUES (new.rowid, new.content, new.conversation_id);
        END;

        CREATE TRIGGER IF NOT EXISTS messages_fts_delete
        AFTER DELETE ON messages BEGIN
            DELETE FROM messages_fts WHERE rowid = old.rowid;
        END;

        CREATE TRIGGER IF NOT EXISTS messages_fts_update
        AFTER UPDATE OF content ON messages BEGIN
            UPDATE messages_fts SET content = new.content WHERE rowid = new.rowid;
        END;

        CREATE TABLE IF NOT EXISTS user_profiles (
            user_email TEXT PRIMARY KEY,
            about_me TEXT,
            response_format TEXT,
            default_persona TEXT,
            default_rag_sources TEXT,
            timezone TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            user_email TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            instructions TEXT,
            default_persona TEXT,
            archived INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_projects_user
            ON projects(user_email, archived, updated_at DESC);

        CREATE TABLE IF NOT EXISTS user_memory (
            user_email TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (user_email, key)
        );

        CREATE INDEX IF NOT EXISTS idx_user_memory_updated
            ON user_memory(user_email, updated_at DESC);

        -- P2 #25: auto-extracted memory proposals awaiting user
        -- accept/reject. Separate from user_memory so the LRU cap
        -- on accepted memories isn't competed against by pending
        -- ones, and so build_memory_block doesn't have to filter.
        CREATE TABLE IF NOT EXISTS proposed_memories (
            id TEXT PRIMARY KEY,
            user_email TEXT NOT NULL,
            conversation_id TEXT,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            reason TEXT,
            proposed_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_proposed_memories_user
            ON proposed_memories(user_email, proposed_at DESC);

        -- P2 #25: keys the user explicitly rejected so the classifier
        -- doesn't re-propose them. Cap'd at 50/user with FIFO eviction.
        CREATE TABLE IF NOT EXISTS rejected_memory_keys (
            user_email TEXT NOT NULL,
            key TEXT NOT NULL,
            rejected_at TEXT NOT NULL,
            PRIMARY KEY (user_email, key)
        );

        CREATE INDEX IF NOT EXISTS idx_rejected_memory_keys_user
            ON rejected_memory_keys(user_email, rejected_at DESC);

        -- P2 #24 Phase 1: one plan per conversation. Items live as a
        -- JSON list in the `items` column (cap 20 items, 200 chars
        -- per title, 500 chars per notes — enforced at the store
        -- layer). Phase 2 columns (requires_approval, approved_at,
        -- approval_mode) are declared upfront with safe defaults so
        -- the gate can be added without a second migration.
        CREATE TABLE IF NOT EXISTS conversation_plans (
            conversation_id   TEXT PRIMARY KEY,
            user_email        TEXT NOT NULL,
            items             TEXT NOT NULL,
            created_at        TEXT NOT NULL,
            updated_at        TEXT NOT NULL,
            requires_approval INTEGER NOT NULL DEFAULT 0,
            approved_at       TEXT,
            approval_mode     TEXT NOT NULL DEFAULT 'each',
            FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_conversation_plans_user
            ON conversation_plans(user_email, updated_at DESC);

        CREATE TABLE IF NOT EXISTS artifacts (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            user_email TEXT NOT NULL,
            title TEXT NOT NULL,
            content_type TEXT NOT NULL,
            language TEXT,
            latest_version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_artifacts_conversation
            ON artifacts(conversation_id, updated_at DESC);

        CREATE TABLE IF NOT EXISTS artifact_versions (
            artifact_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            content TEXT NOT NULL,
            change_summary TEXT,
            created_at TEXT NOT NULL,
            created_by TEXT NOT NULL,
            PRIMARY KEY (artifact_id, version),
            FOREIGN KEY (artifact_id) REFERENCES artifacts(id) ON DELETE CASCADE
        );
        """
    )

    # Additive migrations for DBs created before columns were introduced.
    # SQLite's CREATE TABLE IF NOT EXISTS won't add new columns to an
    # existing table, so we inspect PRAGMA table_info and ALTER TABLE for
    # anything missing. Idempotent across restarts.
    cur = await _db.execute("PRAGMA table_info(conversations)")
    existing_cols = {row["name"] for row in await cur.fetchall()}
    if "pinned" not in existing_cols:
        await _db.execute(
            "ALTER TABLE conversations ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0"
        )
    if "pinned_at" not in existing_cols:
        await _db.execute(
            "ALTER TABLE conversations ADD COLUMN pinned_at TEXT"
        )
    if "project_id" not in existing_cols:
        await _db.execute(
            "ALTER TABLE conversations ADD COLUMN project_id TEXT"
        )
    # §28 follow-up: conversation-level `default_tags` so #tag chips
    # pinned on turn 1 stick through follow-up turns without the
    # frontend resubmitting. Stored as a JSON-serialised
    # list[{kind,value}]; NULL means no default.
    if "default_tags" not in existing_cols:
        await _db.execute(
            "ALTER TABLE conversations ADD COLUMN default_tags TEXT"
        )
    await _db.execute(
        "CREATE INDEX IF NOT EXISTS idx_conversations_pinned "
        "ON conversations(user_email, pinned DESC, updated_at DESC)"
    )
    await _db.execute(
        "CREATE INDEX IF NOT EXISTS idx_conversations_project "
        "ON conversations(user_email, project_id, updated_at DESC)"
    )

    # §22 Stage C: unify sandbox-generated artifacts with the
    # model-written ones by adding source/filename/external_url
    # columns to the existing artifacts table. Sandbox rows carry
    # source='sandbox_generated' and point external_url at the
    # existing /api/artifacts/{cid}/{aid} proxy endpoint.
    cur = await _db.execute("PRAGMA table_info(artifacts)")
    artifact_cols = {row["name"] for row in await cur.fetchall()}
    if "source" not in artifact_cols:
        await _db.execute(
            "ALTER TABLE artifacts ADD COLUMN source TEXT NOT NULL "
            "DEFAULT 'model_written'"
        )
    if "filename" not in artifact_cols:
        await _db.execute(
            "ALTER TABLE artifacts ADD COLUMN filename TEXT"
        )
    if "external_url" not in artifact_cols:
        await _db.execute(
            "ALTER TABLE artifacts ADD COLUMN external_url TEXT"
        )

    # Multimodal attachments metadata (§5). JSON list of
    # {document_id, filename, content_type} per message. Nullable.
    cur = await _db.execute("PRAGMA table_info(messages)")
    message_cols = {row["name"] for row in await cur.fetchall()}
    if "attachments" not in message_cols:
        await _db.execute(
            "ALTER TABLE messages ADD COLUMN attachments TEXT"
        )

    await _db.commit()
    return _db


async def get_db() -> aiosqlite.Connection:
    if _db is None:
        return await init_db()
    return _db


async def close_db() -> None:
    global _db
    if _db is not None:
        await _db.close()
        _db = None


# ==============================================================================
# Conversations
# ==============================================================================

async def create_conversation(
    user_email: str,
    persona: str,
    title: Optional[str] = None,
    default_tags: Optional[list[dict]] = None,
) -> dict:
    db = await get_db()
    cid = str(uuid.uuid4())
    now = _iso_now()
    # §28 follow-up: persist the first request's #tag chips so
    # subsequent turns fall back to them when the frontend omits
    # `tags`. An explicit empty list and None both store as NULL
    # (= no default).
    default_tags_json = (
        json.dumps(default_tags) if default_tags else None
    )
    await db.execute(
        """
        INSERT INTO conversations
            (id, user_email, title, persona, created_at, updated_at,
             summary, summary_through_index, default_tags)
        VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?)
        """,
        (cid, user_email, title, persona, now, now, default_tags_json),
    )
    await db.commit()
    return {
        "id": cid,
        "user_email": user_email,
        "title": title,
        "persona": persona,
        "created_at": now,
        "updated_at": now,
        "summary": None,
        "summary_through_index": None,
        "default_tags": default_tags or None,
        "message_count": 0,
        "preview": None,
    }


_UNFILED_SENTINEL = "__unfiled__"


async def get_conversations(
    user_email: str,
    limit: int = 20,
    offset: int = 0,
    persona: Optional[str] = None,
    search: Optional[str] = None,
    pinned_only: bool = False,
    project_id: Optional[str] = None,
) -> dict:
    """
    List a user's conversations with message_count and preview. Supports FTS5
    search across message content (matches any conversation containing a
    matching message). Pinned conversations float to the top of the listing;
    set ``pinned_only=True`` to limit the result to pinned rows. Set
    ``project_id`` to a concrete project id to list only that project's
    conversations, or to ``__unfiled__`` to list conversations with no
    project at all.
    """
    db = await get_db()

    where = ["c.user_email = ?"]
    params: list[Any] = [user_email]
    if persona:
        where.append("c.persona = ?")
        params.append(persona)
    if pinned_only:
        where.append("c.pinned = 1")
    if project_id == _UNFILED_SENTINEL:
        where.append("c.project_id IS NULL")
    elif project_id:
        where.append("c.project_id = ?")
        params.append(project_id)

    if search:
        where.append(
            "c.id IN (SELECT DISTINCT m.conversation_id FROM messages m "
            "JOIN messages_fts fts ON fts.rowid = m.rowid WHERE fts.content MATCH ?)"
        )
        params.append(search)

    where_sql = " AND ".join(where)

    list_sql = f"""
        SELECT
            c.id, c.user_email, c.title, c.persona, c.created_at, c.updated_at,
            c.summary, c.summary_through_index, c.pinned, c.pinned_at,
            c.project_id,
            (SELECT COUNT(*) FROM messages WHERE conversation_id = c.id) AS message_count,
            (SELECT content FROM messages
                WHERE conversation_id = c.id AND role = 'user'
                ORDER BY index_in_conversation ASC LIMIT 1) AS preview
        FROM conversations c
        WHERE {where_sql}
        ORDER BY c.pinned DESC, c.updated_at DESC
        LIMIT ? OFFSET ?
    """
    list_params = list(params) + [limit, offset]

    cursor = await db.execute(list_sql, list_params)
    rows = await cursor.fetchall()

    count_sql = f"SELECT COUNT(*) AS total FROM conversations c WHERE {where_sql}"
    cursor = await db.execute(count_sql, params)
    count_row = await cursor.fetchone()
    total = count_row["total"] if count_row else 0

    conversations = []
    for row in rows:
        conv = dict(row)
        conv["pinned"] = bool(conv.get("pinned"))
        preview = conv.get("preview")
        if preview and len(preview) > 120:
            conv["preview"] = preview[:117] + "..."
        conversations.append(conv)

    return {"conversations": conversations, "total": total}


async def search_user_messages(
    user_email: str,
    query: str,
    limit: int = 5,
    persona: Optional[str] = None,
    exclude_conversation_id: Optional[str] = None,
) -> dict:
    """
    Full-text search over a user's own past messages. Reuses the FTS5
    virtual table built for /api/chats search, but returns per-message
    rows with snippets and conversation metadata so an MCP tool can
    surface them to the model. Pinned conversations are boosted to the
    top of the result set; within a pin tier, ranking is FTS5 BM25
    (lower = more relevant).

    ``query`` is passed through to FTS5 MATCH unchanged. Plain words
    work; advanced FTS5 syntax (phrase queries, NEAR, OR) also works.
    """
    db = await get_db()

    where = ["c.user_email = ?", "fts.content MATCH ?"]
    params: list[Any] = [user_email, query]
    if persona:
        where.append("c.persona = ?")
        params.append(persona)
    if exclude_conversation_id:
        where.append("c.id != ?")
        params.append(exclude_conversation_id)
    where_sql = " AND ".join(where)

    list_sql = f"""
        SELECT
            m.id AS message_id,
            m.role AS matching_message_role,
            m.created_at AS message_created_at,
            m.index_in_conversation AS message_index,
            c.id AS conversation_id,
            c.title AS conversation_title,
            c.persona AS persona,
            c.created_at AS conversation_created_at,
            c.pinned AS pinned,
            snippet(messages_fts, 0, '<<', '>>', '...', 16) AS snippet
        FROM messages_fts fts
        JOIN messages m ON m.rowid = fts.rowid
        JOIN conversations c ON c.id = m.conversation_id
        WHERE {where_sql}
        ORDER BY c.pinned DESC, bm25(messages_fts) ASC
        LIMIT ?
    """
    cursor = await db.execute(list_sql, list(params) + [limit])
    rows = await cursor.fetchall()

    count_sql = f"""
        SELECT COUNT(*) AS total
        FROM messages_fts fts
        JOIN messages m ON m.rowid = fts.rowid
        JOIN conversations c ON c.id = m.conversation_id
        WHERE {where_sql}
    """
    cursor = await db.execute(count_sql, params)
    count_row = await cursor.fetchone()
    total = int(count_row["total"]) if count_row else 0

    results = []
    for row in rows:
        results.append({
            "conversation_id": row["conversation_id"],
            "conversation_title": row["conversation_title"],
            "persona": row["persona"],
            "created_at": row["conversation_created_at"],
            "matching_message_role": row["matching_message_role"],
            "matching_message_index": row["message_index"],
            "matching_message_created_at": row["message_created_at"],
            "pinned": bool(row["pinned"]),
            "snippet": row["snippet"],
        })

    return {"results": results, "total_matches": total}


async def set_conversation_pinned(
    conversation_id: str,
    user_email: str,
    pinned: bool,
) -> Optional[dict]:
    """
    Pin or unpin a conversation. Returns the updated meta row (with
    ``pinned`` and ``pinned_at`` fields), or None if the conversation is
    not owned by the user. Idempotent.
    """
    db = await get_db()
    pinned_at = _iso_now() if pinned else None
    cursor = await db.execute(
        "UPDATE conversations SET pinned = ?, pinned_at = ? "
        "WHERE id = ? AND user_email = ?",
        (1 if pinned else 0, pinned_at, conversation_id, user_email),
    )
    await db.commit()
    if cursor.rowcount == 0:
        return None
    meta = await _get_conversation_meta(conversation_id, user_email)
    if meta is not None and "pinned" in meta:
        meta["pinned"] = bool(meta["pinned"])
    return meta


async def get_conversation(conversation_id: str, user_email: str) -> Optional[dict]:
    """Load a conversation with all of its messages. Enforces user ownership."""
    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM conversations WHERE id = ? AND user_email = ?",
        (conversation_id, user_email),
    )
    row = await cursor.fetchone()
    if row is None:
        return None

    conversation = dict(row)
    if "pinned" in conversation:
        conversation["pinned"] = bool(conversation["pinned"])
    # §28 follow-up: deserialize default_tags from its JSON column.
    # Malformed JSON (shouldn't happen — we wrote it) → treated as
    # unset rather than crashing.
    raw = conversation.get("default_tags")
    if raw:
        try:
            conversation["default_tags"] = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            conversation["default_tags"] = None
    else:
        conversation["default_tags"] = None

    cursor = await db.execute(
        """
        SELECT id, role, content, thinking, tool_calls, rag_context,
               attachments, created_at, token_count, index_in_conversation
        FROM messages
        WHERE conversation_id = ?
        ORDER BY index_in_conversation ASC
        """,
        (conversation_id,),
    )
    msg_rows = await cursor.fetchall()
    conversation["messages"] = [_message_row_to_dict(r) for r in msg_rows]
    return conversation


async def update_conversation(
    conversation_id: str,
    user_email: str,
    title: Optional[str] = None,
    persona: Optional[str] = None,
) -> Optional[dict]:
    """Patch an existing conversation row. Pass any subset of
    ``title`` / ``persona``. Empty-set call is a no-op fast path
    that just returns the current row.

    ``persona`` is used by the §X delegation flow so a chat that the
    model hands off to ``code`` mid-conversation persists as belonging
    to the new persona; future turns then resolve to the delegated
    persona's prompt + tool subset without paying the delegation
    round-trip again."""
    db = await get_db()
    updates: list[str] = []
    params: list[Any] = []
    if title is not None:
        updates.append("title = ?")
        params.append(title)
    if persona is not None:
        updates.append("persona = ?")
        params.append(persona)
    if not updates:
        return await _get_conversation_meta(conversation_id, user_email)

    updates.append("updated_at = ?")
    params.append(_iso_now())
    params.extend([conversation_id, user_email])

    cursor = await db.execute(
        f"UPDATE conversations SET {', '.join(updates)} "
        f"WHERE id = ? AND user_email = ?",
        params,
    )
    await db.commit()
    if cursor.rowcount == 0:
        return None
    return await _get_conversation_meta(conversation_id, user_email)


async def _get_conversation_meta(
    conversation_id: str, user_email: str
) -> Optional[dict]:
    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM conversations WHERE id = ? AND user_email = ?",
        (conversation_id, user_email),
    )
    meta = _row_to_dict(await cursor.fetchone())
    if meta is not None and "pinned" in meta:
        meta["pinned"] = bool(meta["pinned"])
    return meta


async def delete_conversation(conversation_id: str, user_email: str) -> bool:
    db = await get_db()
    cursor = await db.execute(
        "SELECT id FROM conversations WHERE id = ? AND user_email = ?",
        (conversation_id, user_email),
    )
    if await cursor.fetchone() is None:
        return False

    # Explicit delete for the FTS trigger and ordering guarantees — we don't
    # rely on ON DELETE CASCADE since SQLite's FK cascades don't fire the
    # per-row delete triggers on the child table consistently across versions.
    await db.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
    await db.execute(
        "DELETE FROM conversations WHERE id = ? AND user_email = ?",
        (conversation_id, user_email),
    )
    await db.commit()
    return True


async def count_conversations(user_email: str) -> int:
    db = await get_db()
    cursor = await db.execute(
        "SELECT COUNT(*) AS total FROM conversations WHERE user_email = ?",
        (user_email,),
    )
    row = await cursor.fetchone()
    return int(row["total"]) if row else 0


# ==============================================================================
# Messages
# ==============================================================================

async def add_message(
    conversation_id: str,
    role: str,
    content: str,
    thinking: Optional[str] = None,
    tool_calls: Optional[list] = None,
    rag_context: Optional[dict] = None,
    attachments: Optional[list] = None,
    token_count: Optional[int] = None,
) -> dict:
    db = await get_db()
    msg_id = str(uuid.uuid4())
    now = _iso_now()

    cursor = await db.execute(
        "SELECT COALESCE(MAX(index_in_conversation), -1) + 1 AS next_idx "
        "FROM messages WHERE conversation_id = ?",
        (conversation_id,),
    )
    row = await cursor.fetchone()
    next_index = int(row["next_idx"]) if row else 0

    await db.execute(
        """
        INSERT INTO messages
            (id, conversation_id, index_in_conversation, role, content,
             thinking, tool_calls, rag_context, attachments, created_at,
             token_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            msg_id,
            conversation_id,
            next_index,
            role,
            content,
            thinking,
            json.dumps(tool_calls) if tool_calls is not None else None,
            json.dumps(rag_context) if rag_context is not None else None,
            json.dumps(attachments) if attachments is not None else None,
            now,
            token_count,
        ),
    )
    await db.execute(
        "UPDATE conversations SET updated_at = ? WHERE id = ?",
        (now, conversation_id),
    )
    await db.commit()

    return {
        "id": msg_id,
        "conversation_id": conversation_id,
        "index_in_conversation": next_index,
        "role": role,
        "content": content,
        "thinking": thinking,
        "tool_calls": tool_calls,
        "rag_context": rag_context,
        "attachments": attachments,
        "created_at": now,
        "token_count": token_count,
    }


async def get_messages_after_index(conversation_id: str, index: int) -> list[dict]:
    db = await get_db()
    cursor = await db.execute(
        """
        SELECT id, role, content, thinking, tool_calls, rag_context,
               attachments, created_at, token_count, index_in_conversation
        FROM messages
        WHERE conversation_id = ? AND index_in_conversation > ?
        ORDER BY index_in_conversation ASC
        """,
        (conversation_id, index),
    )
    rows = await cursor.fetchall()
    return [_message_row_to_dict(r) for r in rows]


async def update_summary(
    conversation_id: str, summary: str, through_index: int
) -> None:
    db = await get_db()
    await db.execute(
        "UPDATE conversations SET summary = ?, summary_through_index = ?, updated_at = ? "
        "WHERE id = ?",
        (summary, through_index, _iso_now(), conversation_id),
    )
    await db.commit()
