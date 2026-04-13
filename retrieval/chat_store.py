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
    return {
        "id": row["id"],
        "role": row["role"],
        "content": row["content"],
        "thinking": row["thinking"],
        "tool_calls": _parse_json(row["tool_calls"]),
        "rag_context": _parse_json(row["rag_context"]),
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
            summary_through_index INTEGER
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
        """
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
) -> dict:
    db = await get_db()
    cid = str(uuid.uuid4())
    now = _iso_now()
    await db.execute(
        """
        INSERT INTO conversations
            (id, user_email, title, persona, created_at, updated_at, summary, summary_through_index)
        VALUES (?, ?, ?, ?, ?, ?, NULL, NULL)
        """,
        (cid, user_email, title, persona, now, now),
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
        "message_count": 0,
        "preview": None,
    }


async def get_conversations(
    user_email: str,
    limit: int = 20,
    offset: int = 0,
    persona: Optional[str] = None,
    search: Optional[str] = None,
) -> dict:
    """
    List a user's conversations with message_count and preview. Supports FTS5
    search across message content (matches any conversation containing a
    matching message).
    """
    db = await get_db()

    where = ["c.user_email = ?"]
    params: list[Any] = [user_email]
    if persona:
        where.append("c.persona = ?")
        params.append(persona)

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
            c.summary, c.summary_through_index,
            (SELECT COUNT(*) FROM messages WHERE conversation_id = c.id) AS message_count,
            (SELECT content FROM messages
                WHERE conversation_id = c.id AND role = 'user'
                ORDER BY index_in_conversation ASC LIMIT 1) AS preview
        FROM conversations c
        WHERE {where_sql}
        ORDER BY c.updated_at DESC
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
        preview = conv.get("preview")
        if preview and len(preview) > 120:
            conv["preview"] = preview[:117] + "..."
        conversations.append(conv)

    return {"conversations": conversations, "total": total}


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

    cursor = await db.execute(
        """
        SELECT id, role, content, thinking, tool_calls, rag_context,
               created_at, token_count, index_in_conversation
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
) -> Optional[dict]:
    db = await get_db()
    updates: list[str] = []
    params: list[Any] = []
    if title is not None:
        updates.append("title = ?")
        params.append(title)
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
    return _row_to_dict(await cursor.fetchone())


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
             thinking, tool_calls, rag_context, created_at, token_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        "created_at": now,
        "token_count": token_count,
    }


async def get_messages_after_index(conversation_id: str, index: int) -> list[dict]:
    db = await get_db()
    cursor = await db.execute(
        """
        SELECT id, role, content, thinking, tool_calls, rag_context,
               created_at, token_count, index_in_conversation
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
