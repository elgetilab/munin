"""
User profile persistence (SQLite).

Stores per-user response preferences ("about me", "response format",
default persona, etc.) so the model can adapt without the user repeating
themselves on every turn. Lives in the same chats.db as conversations and
shares the connection from chat_store.

Relationship to user memory (§9): profile is user-curated, static across
conversations, and injected at the top of the system prompt. Memory is
model-curated facts written via tool calls during chat. They complement
each other; this module owns only the profile half.
"""

from __future__ import annotations

import json
from typing import Any, Optional

import aiosqlite

from chat_store import _iso_now, get_db


PROFILE_TEXT_FIELD_LIMIT = 1500
PROFILE_FIELDS = (
    "about_me",
    "response_format",
    "default_persona",
    "default_rag_sources",
    "timezone",
)
TEXT_CAPPED_FIELDS = ("about_me", "response_format")


def _row_to_profile(row: Optional[aiosqlite.Row]) -> Optional[dict]:
    if row is None:
        return None
    raw_sources = row["default_rag_sources"]
    if raw_sources:
        try:
            sources = json.loads(raw_sources)
        except (TypeError, ValueError):
            sources = None
    else:
        sources = None
    return {
        "user_email": row["user_email"],
        "about_me": row["about_me"],
        "response_format": row["response_format"],
        "default_persona": row["default_persona"],
        "default_rag_sources": sources,
        "timezone": row["timezone"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _empty_profile(user_email: str) -> dict:
    return {
        "user_email": user_email,
        "about_me": None,
        "response_format": None,
        "default_persona": None,
        "default_rag_sources": None,
        "timezone": None,
        "created_at": None,
        "updated_at": None,
    }


def validate_profile_input(payload: dict) -> dict:
    """
    Sanity-check + normalise a PUT /api/profile body. Raises ValueError on
    invalid input. Unknown keys are silently dropped (forward-compat).
    """
    if not isinstance(payload, dict):
        raise ValueError("profile body must be an object")

    cleaned: dict[str, Any] = {}
    for field in PROFILE_FIELDS:
        if field not in payload:
            continue
        value = payload[field]
        if value is None or value == "":
            cleaned[field] = None
            continue
        if field == "default_rag_sources":
            if not isinstance(value, list) or not all(isinstance(s, str) for s in value):
                raise ValueError("default_rag_sources must be a list of strings")
            cleaned[field] = value
            continue
        if not isinstance(value, str):
            raise ValueError(f"{field} must be a string")
        if field in TEXT_CAPPED_FIELDS and len(value) > PROFILE_TEXT_FIELD_LIMIT:
            raise ValueError(
                f"{field} exceeds {PROFILE_TEXT_FIELD_LIMIT}-character cap "
                f"(got {len(value)})"
            )
        cleaned[field] = value
    return cleaned


async def get_profile(user_email: str) -> dict:
    """Load a profile. Returns an all-None profile if the user has none."""
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM user_profiles WHERE user_email = ?",
        (user_email,),
    )
    row = await cur.fetchone()
    await cur.close()
    return _row_to_profile(row) or _empty_profile(user_email)


async def upsert_profile(user_email: str, fields: dict) -> dict:
    """
    Upsert: write only the fields that appear in ``fields`` (validated by
    ``validate_profile_input``); leave everything else untouched. Returns
    the full profile after the write.
    """
    db = await get_db()
    now = _iso_now()

    cur = await db.execute(
        "SELECT user_email FROM user_profiles WHERE user_email = ?",
        (user_email,),
    )
    exists = (await cur.fetchone()) is not None
    await cur.close()

    if not exists:
        # Insert with defaults; subsequent UPDATE applies the new fields.
        await db.execute(
            """
            INSERT INTO user_profiles (
                user_email, about_me, response_format, default_persona,
                default_rag_sources, timezone, created_at, updated_at
            ) VALUES (?, NULL, NULL, NULL, NULL, NULL, ?, ?)
            """,
            (user_email, now, now),
        )

    if fields:
        set_clauses: list[str] = []
        params: list[Any] = []
        for field in PROFILE_FIELDS:
            if field not in fields:
                continue
            value = fields[field]
            if field == "default_rag_sources" and value is not None:
                value = json.dumps(value)
            set_clauses.append(f"{field} = ?")
            params.append(value)
        if set_clauses:
            set_clauses.append("updated_at = ?")
            params.append(now)
            params.append(user_email)
            await db.execute(
                f"UPDATE user_profiles SET {', '.join(set_clauses)} "
                f"WHERE user_email = ?",
                params,
            )

    await db.commit()
    return await get_profile(user_email)


async def delete_profile(user_email: str) -> bool:
    """Wipe the profile row. Returns True if a row was removed."""
    db = await get_db()
    cur = await db.execute(
        "DELETE FROM user_profiles WHERE user_email = ?",
        (user_email,),
    )
    await db.commit()
    removed = (cur.rowcount or 0) > 0
    return removed


def build_profile_block(profile: Optional[dict]) -> Optional[str]:
    """
    Render a profile dict as the system-prompt block defined in §25. Returns
    None if the profile has nothing user-visible to inject (so callers can
    skip the block entirely instead of emitting an empty header).
    """
    if not profile:
        return None
    about = (profile.get("about_me") or "").strip()
    fmt = (profile.get("response_format") or "").strip()
    if not about and not fmt:
        return None
    parts: list[str] = ["=== USER PROFILE ==="]
    if about:
        parts.append(f"About you: {about}")
    if fmt:
        if about:
            parts.append("")
        parts.append(f"Response preferences: {fmt}")
    parts.append("=== END USER PROFILE ===")
    return "\n".join(parts)
