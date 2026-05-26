"""
Memory proposal store (P2 #25).

Two tables (defined in chat_store.py CREATE block):

  proposed_memories       Auto-extracted (key, value) candidates the
                          user hasn't accepted yet. Cap 10/user with
                          FIFO eviction. Renders as accept/reject
                          pills in the chat UI.

  rejected_memory_keys    Keys the user explicitly dismissed so the
                          classifier doesn't re-propose them. Cap
                          50/user with FIFO eviction.

Both are user-scoped. The classifier dedupe step (memory_extract.py)
unions accepted (user_memory) + pending (proposed_memories) +
rejected (rejected_memory_keys) and excludes any key that already
appears so the same fact never lands as a duplicate pill.
"""

from __future__ import annotations

import uuid
from typing import Optional

from chat_store import _iso_now, get_db


# ---------------------------------------------------------------------------
# Caps. Tune-once constants; document the rationale rather than gate
# behind env vars (every operator dial that nobody touches is a footgun).
# ---------------------------------------------------------------------------

# At most N pending proposals per user. A chatty conversation that the
# user ignores can otherwise stack up dozens of pills. FIFO eviction:
# the oldest pending falls off when the (N+1)th lands.
MAX_PENDING = 10

# At most N rejection memories per user. Larger than MAX_PENDING because
# rejections are essentially a "don't suggest this again" tombstone and
# we'd rather not GC them aggressively. Still FIFO so the table can't
# grow unbounded if a user develops a habit of rejecting everything.
MAX_REJECTIONS = 50


# ---------------------------------------------------------------------------
# Proposed memories
# ---------------------------------------------------------------------------


def _row_to_proposal(row) -> dict:
    return {
        "id": row["id"],
        "key": row["key"],
        "value": row["value"],
        "reason": row["reason"],
        "conversation_id": row["conversation_id"],
        "proposed_at": row["proposed_at"],
    }


async def list_pending(user_email: str) -> list[dict]:
    """Newest proposal first."""
    db = await get_db()
    cur = await db.execute(
        "SELECT id, key, value, reason, conversation_id, proposed_at "
        "FROM proposed_memories WHERE user_email = ? "
        "ORDER BY proposed_at DESC",
        (user_email,),
    )
    rows = await cur.fetchall()
    return [_row_to_proposal(r) for r in rows]


async def get_proposal(proposal_id: str, user_email: str) -> Optional[dict]:
    """Fetch one proposal; returns None if not found or owned by someone else."""
    db = await get_db()
    cur = await db.execute(
        "SELECT id, key, value, reason, conversation_id, proposed_at "
        "FROM proposed_memories WHERE id = ? AND user_email = ?",
        (proposal_id, user_email),
    )
    row = await cur.fetchone()
    return _row_to_proposal(row) if row else None


async def create_proposal(
    *,
    user_email: str,
    conversation_id: Optional[str],
    key: str,
    value: str,
    reason: Optional[str],
) -> dict:
    """Insert a new proposal. FIFO-evicts the oldest pending row if the
    user is at MAX_PENDING. Returns the persisted proposal dict
    (including the assigned id)."""
    proposal_id = str(uuid.uuid4())
    now = _iso_now()
    db = await get_db()

    # FIFO cap. Same shape as the LRU eviction in memory_store.remember.
    cur = await db.execute(
        "SELECT COUNT(*) AS n FROM proposed_memories WHERE user_email = ?",
        (user_email,),
    )
    row = await cur.fetchone()
    if row and int(row["n"]) >= MAX_PENDING:
        # Drop oldest by proposed_at ASC.
        await db.execute(
            "DELETE FROM proposed_memories WHERE id = ("
            "  SELECT id FROM proposed_memories "
            "  WHERE user_email = ? ORDER BY proposed_at ASC LIMIT 1"
            ")",
            (user_email,),
        )

    await db.execute(
        "INSERT INTO proposed_memories "
        "(id, user_email, conversation_id, key, value, reason, proposed_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (proposal_id, user_email, conversation_id, key, value, reason, now),
    )
    await db.commit()
    return {
        "id": proposal_id,
        "key": key,
        "value": value,
        "reason": reason,
        "conversation_id": conversation_id,
        "proposed_at": now,
    }


async def delete_proposal(proposal_id: str, user_email: str) -> bool:
    """Returns True if a row was removed."""
    db = await get_db()
    cur = await db.execute(
        "DELETE FROM proposed_memories WHERE id = ? AND user_email = ?",
        (proposal_id, user_email),
    )
    await db.commit()
    return (cur.rowcount or 0) > 0


# ---------------------------------------------------------------------------
# Rejected keys
# ---------------------------------------------------------------------------


async def list_rejected_keys(user_email: str) -> list[str]:
    db = await get_db()
    cur = await db.execute(
        "SELECT key FROM rejected_memory_keys WHERE user_email = ?",
        (user_email,),
    )
    rows = await cur.fetchall()
    return [r["key"] for r in rows]


async def add_rejection(user_email: str, key: str) -> None:
    """Idempotent: re-rejecting the same key just refreshes rejected_at.
    FIFO-evicts the oldest rejection when the cap is exceeded."""
    if not key:
        return
    now = _iso_now()
    db = await get_db()

    # UPSERT via INSERT OR REPLACE keeps the row primary-key-unique.
    await db.execute(
        "INSERT OR REPLACE INTO rejected_memory_keys "
        "(user_email, key, rejected_at) VALUES (?, ?, ?)",
        (user_email, key, now),
    )

    cur = await db.execute(
        "SELECT COUNT(*) AS n FROM rejected_memory_keys WHERE user_email = ?",
        (user_email,),
    )
    row = await cur.fetchone()
    overflow = int(row["n"]) - MAX_REJECTIONS if row else 0
    if overflow > 0:
        await db.execute(
            "DELETE FROM rejected_memory_keys WHERE user_email = ? AND key IN ("
            "  SELECT key FROM rejected_memory_keys "
            "  WHERE user_email = ? ORDER BY rejected_at ASC LIMIT ?"
            ")",
            (user_email, user_email, overflow),
        )
    await db.commit()


# ---------------------------------------------------------------------------
# Helpers consumed by the classifier
# ---------------------------------------------------------------------------


async def all_known_keys(user_email: str) -> set[str]:
    """Union of accepted + pending + rejected keys. The classifier
    excludes any candidate whose key already appears here so the same
    fact never re-surfaces as a duplicate pill."""
    # Lazy import: memory_store imports from chat_store which is fine,
    # but having this module import memory_store at top-level created an
    # awkward cycle during tests.
    import memory_store

    accepted = await memory_store.recall_all(user_email)
    pending = await list_pending(user_email)
    rejected = await list_rejected_keys(user_email)
    keys: set[str] = set()
    for m in accepted:
        keys.add(m["key"])
    for p in pending:
        keys.add(p["key"])
    for k in rejected:
        keys.add(k)
    return keys
