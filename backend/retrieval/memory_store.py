"""
User memory store (§9) — model-curated facts that persist across chats.

Complementary to §25 user profile, not overlapping with it:

  * §25 profile (``user_profile_store``) is USER-curated: the end user
    edits it through a settings UI. A small, fixed schema
    (``about_me``, ``response_format``, ``default_persona``,
    ``default_rag_sources``, ``timezone``).
  * §9 memory (this module) is MODEL-curated: the Qwen-backed chat
    model calls ``remember(key, value)`` mid-conversation when the
    user tells it something worth remembering. Free-form key-value
    pairs with LRU eviction at 20 entries.

Both get injected into the system prompt on every persistent chat
turn; the memory block sits between the profile block and the
persona prompt so the model sees "what the user told you directly"
before "what you've learned while working with them".

Design notes:

- Caps are deliberately tight: 20 entries per user, 100 chars per
  key, 200 chars per value. That bounds the system-prompt overhead
  at roughly 4 KB (~1000 tokens) per turn, which is what the spec
  budgeted. Enforce at the store layer so the MCP tool stays thin.
- Eviction is LRU by ``updated_at``. When a ``remember`` call would
  push the store past MAX_MEMORIES, the oldest-touched entry is
  silently dropped and its key is reported back in the tool result
  so the model can tell the user "I had to forget X to make room
  for Y" if it wants to.
- There is no vector search here. 20 entries × 200 chars of text is
  tiny; plain ``.lower()`` substring is instant.
"""

from __future__ import annotations

from typing import Any, Optional

from chat_store import _iso_now, get_db


MAX_MEMORIES = 20
MAX_KEY_CHARS = 100
MAX_VALUE_CHARS = 200


class MemoryError(ValueError):
    """Raised for any unusable memory input (cap violations, missing
    fields, etc.). Callers convert this to a tool error response."""


# ----------------------------------------------------------------------------
# Validation
# ----------------------------------------------------------------------------

def _validate_key(key: Any) -> str:
    if not isinstance(key, str):
        raise MemoryError("key must be a string")
    key = key.strip()
    if not key:
        raise MemoryError("key must be non-empty")
    if len(key) > MAX_KEY_CHARS:
        raise MemoryError(
            f"key exceeds {MAX_KEY_CHARS}-character cap (got {len(key)})"
        )
    return key


def _validate_value(value: Any) -> str:
    if not isinstance(value, str):
        raise MemoryError("value must be a string")
    value = value.strip()
    if not value:
        raise MemoryError("value must be non-empty")
    if len(value) > MAX_VALUE_CHARS:
        raise MemoryError(
            f"value exceeds {MAX_VALUE_CHARS}-character cap (got {len(value)})"
        )
    return value


# ----------------------------------------------------------------------------
# CRUD
# ----------------------------------------------------------------------------

def _row_to_memory(row) -> dict:
    return {
        "key": row["key"],
        "value": row["value"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


async def recall_all(user_email: str) -> list[dict]:
    """Return every memory for this user, newest-touched first."""
    db = await get_db()
    cur = await db.execute(
        "SELECT key, value, created_at, updated_at FROM user_memory "
        "WHERE user_email = ? ORDER BY updated_at DESC",
        (user_email,),
    )
    rows = await cur.fetchall()
    return [_row_to_memory(r) for r in rows]


async def recall_matching(user_email: str, search: Optional[str]) -> list[dict]:
    """
    Substring search across key+value, case-insensitive, newest
    first. If ``search`` is empty/None, returns all memories.
    """
    all_mem = await recall_all(user_email)
    if not search or not search.strip():
        return all_mem
    needle = search.strip().lower()
    return [
        m for m in all_mem
        if needle in m["key"].lower() or needle in m["value"].lower()
    ]


async def count_memories(user_email: str) -> int:
    db = await get_db()
    cur = await db.execute(
        "SELECT COUNT(*) AS n FROM user_memory WHERE user_email = ?",
        (user_email,),
    )
    row = await cur.fetchone()
    return int(row["n"]) if row else 0


async def remember(user_email: str, key: str, value: str) -> dict:
    """
    Upsert a (key, value) pair for this user. If inserting a brand-new
    key would push the store past MAX_MEMORIES, the oldest-touched
    entry is LRU-evicted and its key is returned in the ``evicted``
    list so the caller can surface it to the model.
    """
    key = _validate_key(key)
    value = _validate_value(value)

    db = await get_db()
    now = _iso_now()
    evicted: list[str] = []

    # Upsert-then-check: if the key already exists this is just an
    # update, no cap logic needed. If it's new we check the cap and
    # LRU-evict exactly one entry if over the limit.
    cur = await db.execute(
        "SELECT key FROM user_memory WHERE user_email = ? AND key = ?",
        (user_email, key),
    )
    exists = (await cur.fetchone()) is not None

    if exists:
        await db.execute(
            "UPDATE user_memory SET value = ?, updated_at = ? "
            "WHERE user_email = ? AND key = ?",
            (value, now, user_email, key),
        )
    else:
        # Cap check before insert; evict the LRU entry if full.
        total = await count_memories(user_email)
        if total >= MAX_MEMORIES:
            # Find the single oldest-touched entry and remove it.
            evict_cur = await db.execute(
                "SELECT key FROM user_memory WHERE user_email = ? "
                "ORDER BY updated_at ASC LIMIT 1",
                (user_email,),
            )
            evict_row = await evict_cur.fetchone()
            if evict_row is not None:
                evicted_key = evict_row["key"]
                await db.execute(
                    "DELETE FROM user_memory WHERE user_email = ? AND key = ?",
                    (user_email, evicted_key),
                )
                evicted.append(evicted_key)
        await db.execute(
            "INSERT INTO user_memory "
            "(user_email, key, value, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_email, key, value, now, now),
        )

    await db.commit()

    return {
        "remembered": True,
        "key": key,
        "value": value,
        "evicted": evicted,
        "total_memories": await count_memories(user_email),
        "max_memories": MAX_MEMORIES,
    }


async def forget(user_email: str, key: str) -> dict:
    key = _validate_key(key)
    db = await get_db()
    cur = await db.execute(
        "DELETE FROM user_memory WHERE user_email = ? AND key = ?",
        (user_email, key),
    )
    await db.commit()
    return {
        "forgotten": (cur.rowcount or 0) > 0,
        "key": key,
        "total_memories": await count_memories(user_email),
    }


# ----------------------------------------------------------------------------
# System prompt rendering
# ----------------------------------------------------------------------------

def build_memory_block(memories: list[dict]) -> Optional[str]:
    """
    Render a list of memory rows as the ``=== WHAT YOU REMEMBER ABOUT
    THIS USER ===`` block chat_service prepends to the system prompt.
    Returns None if there are no memories so the caller can skip the
    block entirely rather than emit an empty header.
    """
    if not memories:
        return None
    lines = ["=== WHAT YOU REMEMBER ABOUT THIS USER ==="]
    for m in memories:
        key = (m.get("key") or "").strip()
        value = (m.get("value") or "").strip()
        if not key or not value:
            continue
        lines.append(f"- {key}: {value}")
    if len(lines) == 1:
        return None
    lines.append("=== END ===")
    return "\n".join(lines)
