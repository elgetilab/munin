"""
Durable Deep Research job + event log (SQLite, shares chats.db).

Deep Research runs as a detached background job; this store is the source of
truth for its progress. The manager appends render-ready EVENTS (plan,
tool_call/tool_result per search+read, notes, artifact, done) as the job runs;
the frontend reads the log and renders it inline in the conversation - exactly
like normal tool use. Because the log is durable and ordered, a client that
disconnects and returns just re-reads it (every read is a replay), and a service
restart doesn't lose history. This is the "background work, mirrored onto the
stream" model: the job is the source of truth, the UI is a view onto its log.

Shares the aiosqlite connection from chat_store (like the other *_store modules).
Tables are created lazily so no change to chat_store.init_db is needed.
"""

from __future__ import annotations

import json
import time
from typing import Any, Optional

from chat_store import get_db

_tables_ready = False


async def _ensure_tables() -> None:
    global _tables_ready
    if _tables_ready:
        return
    db = await get_db()
    await db.executescript(
        """
        CREATE TABLE IF NOT EXISTS research_jobs (
            job_id          TEXT PRIMARY KEY,
            conversation_id TEXT,
            user_email      TEXT,
            question        TEXT,
            status          TEXT,
            artifact_id     TEXT,
            error           TEXT,
            created_at      REAL,
            updated_at      REAL
        );
        CREATE INDEX IF NOT EXISTS idx_research_jobs_conv
            ON research_jobs(conversation_id, created_at DESC);
        CREATE TABLE IF NOT EXISTS research_events (
            job_id  TEXT,
            seq     INTEGER,
            t       REAL,
            event   TEXT,
            PRIMARY KEY (job_id, seq)
        );
        """
    )
    await db.commit()
    _tables_ready = True


async def create_job(job_id: str, conversation_id: Optional[str],
                     user_email: Optional[str], question: str) -> None:
    await _ensure_tables()
    db = await get_db()
    now = time.time()
    await db.execute(
        "INSERT OR REPLACE INTO research_jobs "
        "(job_id, conversation_id, user_email, question, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, 'queued', ?, ?)",
        (job_id, conversation_id, user_email, question, now, now))
    await db.commit()


async def append_event(job_id: str, event: dict) -> None:
    """Append one render-ready event to the job's ordered log."""
    await _ensure_tables()
    db = await get_db()
    cur = await db.execute(
        "SELECT COALESCE(MAX(seq), -1) + 1 AS n FROM research_events WHERE job_id = ?",
        (job_id,))
    seq = int((await cur.fetchone())["n"])
    await db.execute(
        "INSERT INTO research_events (job_id, seq, t, event) VALUES (?, ?, ?, ?)",
        (job_id, seq, float(event.get("t") or 0.0), json.dumps(event)))
    await db.execute("UPDATE research_jobs SET updated_at = ? WHERE job_id = ?",
                     (time.time(), job_id))
    await db.commit()


async def set_status(job_id: str, status: str, *, artifact_id: Optional[str] = None,
                     error: Optional[str] = None) -> None:
    await _ensure_tables()
    db = await get_db()
    await db.execute(
        "UPDATE research_jobs SET status = ?, "
        "artifact_id = COALESCE(?, artifact_id), error = COALESCE(?, error), "
        "updated_at = ? WHERE job_id = ?",
        (status, artifact_id, error, time.time(), job_id))
    await db.commit()


def _job_row_to_dict(row: Any) -> dict:
    return {k: row[k] for k in row.keys()}


async def get_job(job_id: str, user_email: Optional[str] = None) -> Optional[dict]:
    """Full job record + ordered event log. Ownership-checked if user_email given."""
    await _ensure_tables()
    db = await get_db()
    cur = await db.execute("SELECT * FROM research_jobs WHERE job_id = ?", (job_id,))
    row = await cur.fetchone()
    if row is None:
        return None
    job = _job_row_to_dict(row)
    if user_email is not None and job.get("user_email") != user_email:
        return None
    cur = await db.execute(
        "SELECT event FROM research_events WHERE job_id = ? ORDER BY seq", (job_id,))
    job["events"] = [json.loads(r["event"]) for r in await cur.fetchall()]
    return job


async def get_job_for_conversation(conversation_id: str,
                                   user_email: str) -> Optional[dict]:
    """Most recent DR job for a conversation (for re-loading the inline view)."""
    await _ensure_tables()
    db = await get_db()
    cur = await db.execute(
        "SELECT job_id FROM research_jobs WHERE conversation_id = ? AND user_email = ? "
        "ORDER BY created_at DESC LIMIT 1", (conversation_id, user_email))
    row = await cur.fetchone()
    return await get_job(row["job_id"], user_email) if row else None


async def list_jobs(user_email: str) -> list[dict]:
    await _ensure_tables()
    db = await get_db()
    cur = await db.execute(
        "SELECT job_id, conversation_id, question, status, created_at, updated_at "
        "FROM research_jobs WHERE user_email = ? ORDER BY created_at DESC LIMIT 50",
        (user_email,))
    return [_job_row_to_dict(r) for r in await cur.fetchall()]
