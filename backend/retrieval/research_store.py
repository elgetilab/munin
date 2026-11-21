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

from chat_store import get_db, write_txn

_tables_ready = False


async def _ensure_tables() -> None:
    global _tables_ready
    if _tables_ready:
        return
    # Plain statements inside write_txn, not executescript: executescript
    # COMMITs first, which would commit another coroutine's half-done unit.
    async with write_txn() as db:
        await db.execute(
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
            )
            """
        )
        await db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_research_jobs_conv
                ON research_jobs(conversation_id, created_at DESC)
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS research_events (
                job_id  TEXT,
                seq     INTEGER,
                t       REAL,
                event   TEXT,
                PRIMARY KEY (job_id, seq)
            )
            """
        )
        # Token usage and the answering model of the job, written once at the
        # end (set_usage). Deep Research runs outside any chat turn, so the
        # gateway's usage_log never sees these tokens; the publication-usage
        # summary reads them from here (usage_for_user).
        cur = await db.execute("PRAGMA table_info(research_jobs)")
        cols = {r["name"] for r in await cur.fetchall()}
        for col, typ in (("tokens_prompt", "INTEGER"), ("tokens_completion", "INTEGER"),
                         ("tokens_total", "INTEGER"), ("model", "TEXT"), ("models", "TEXT")):
            if col not in cols:
                await db.execute(f"ALTER TABLE research_jobs ADD COLUMN {col} {typ}")
    _tables_ready = True


async def create_job(job_id: str, conversation_id: Optional[str],
                     user_email: Optional[str], question: str) -> None:
    await _ensure_tables()
    async with write_txn() as db:
        now = time.time()
        await db.execute(
            "INSERT OR REPLACE INTO research_jobs "
            "(job_id, conversation_id, user_email, question, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'queued', ?, ?)",
            (job_id, conversation_id, user_email, question, now, now))


async def append_event(job_id: str, event: dict) -> None:
    """Append one render-ready event to the job's ordered log."""
    await _ensure_tables()
    async with write_txn() as db:
        cur = await db.execute(
            "SELECT COALESCE(MAX(seq), -1) + 1 AS n FROM research_events WHERE job_id = ?",
            (job_id,))
        seq = int((await cur.fetchone())["n"])
        await db.execute(
            "INSERT INTO research_events (job_id, seq, t, event) VALUES (?, ?, ?, ?)",
            (job_id, seq, float(event.get("t") or 0.0), json.dumps(event)))
        await db.execute("UPDATE research_jobs SET updated_at = ? WHERE job_id = ?",
                         (time.time(), job_id))


async def set_status(job_id: str, status: str, *, artifact_id: Optional[str] = None,
                     error: Optional[str] = None) -> None:
    await _ensure_tables()
    async with write_txn() as db:
        await db.execute(
            "UPDATE research_jobs SET status = ?, "
            "artifact_id = COALESCE(?, artifact_id), error = COALESCE(?, error), "
            "updated_at = ? WHERE job_id = ?",
            (status, artifact_id, error, time.time(), job_id))


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


async def set_usage(job_id: str, totals: dict, models: dict[str, int],
                    model: Optional[str]) -> None:
    """Record the job's summed token usage and which model(s) answered."""
    await _ensure_tables()
    async with write_txn() as db:
        await db.execute(
            "UPDATE research_jobs SET tokens_prompt = ?, tokens_completion = ?, "
            "tokens_total = ?, model = ?, models = ? WHERE job_id = ?",
            (int(totals.get("prompt_tokens") or 0), int(totals.get("completion_tokens") or 0),
             int(totals.get("total_tokens") or 0), model,
             json.dumps(models) if models else None, job_id))


async def usage_for_user(user_email: str, since: float, until: float) -> list[dict]:
    """The caller's Deep Research jobs created in [since, until) (epoch
    seconds) with their usage, for the publication-usage summary."""
    await _ensure_tables()
    db = await get_db()
    cur = await db.execute(
        "SELECT job_id, status, created_at, tokens_prompt, tokens_completion, "
        "tokens_total, model FROM research_jobs "
        "WHERE user_email = ? AND created_at >= ? AND created_at < ? ORDER BY created_at",
        (user_email, since, until))
    return [_job_row_to_dict(r) for r in await cur.fetchall()]
