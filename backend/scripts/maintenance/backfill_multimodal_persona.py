#!/usr/bin/env python3
"""
backfill_multimodal_persona.py - record the profile that answered on user
turns where the router failure wrote NULL instead.

WHY. From the router going live (2026-07-08) until 2026-09-07, every user turn
carrying an image or a document was demoted to the default profile: the
composer sends an OpenAI-style content LIST for those turns, and
`router.parse_slash` regex-matched it and raised TypeError. The router call is
wrapped in `try/except` by design, so the turn answered normally, just always
as the default profile.

The fallback then did `persona, persona_id = pin_persona, pin_id`. `pin_id` is
None by design under auto-route (it means "the user pinned nothing"), so
`messages.persona` was written NULL. Measured before the fix: 51 such rows
across 37 conversations and 11 users, against 537 chat / 465 research / 207
code on turns without an attachment. See `backend/docs/KNOWN-BUGS.md` entry 8.

WHAT THIS WRITES, AND WHY IT IS NOT A GUESS. Those turns really did run on the
default profile: `pin_persona` was `persona_module.get_persona(
DEFAULT_PERSONA_ID)` for the whole turn, so its sampling params, tool
allowlist and task fragment are what answered. NULL here is a dropped fact,
not an unknown, and this records it. Nothing infers what the router WOULD have
chosen; that is unrecoverable and inventing it would corrupt the only clean
signal for how often the bug fired.

SCOPE, deliberately narrow:

  * `created_at >= 2026-07-08` (router go-live). The 4 older attachment rows
    with NULL persona predate the router entirely and are left alone.
  * attachments present. The ~24 NULL rows on turns WITHOUT an attachment have
    a different, unidentified cause (clustered 2026-07-22 to 2026-08-23) and
    are not this bug, so they are left alone too.

Idempotent: re-running matches nothing once applied.

`backend/scripts/` is not baked into the image, and the host user cannot write
`/opt/munin/data/chats.db`, but the retrieval container mounts it read-write.
Run it there:

    docker cp backend/scripts/maintenance/backfill_multimodal_persona.py \\
      munin-retrieval:/tmp/backfill_multimodal_persona.py
    docker exec munin-retrieval python /tmp/backfill_multimodal_persona.py
    docker exec munin-retrieval python /tmp/backfill_multimodal_persona.py --apply
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timezone

DB_PATH = os.getenv("CHATS_DB", "/data/chats.db")
BACKUP_DIR = os.getenv("CHATS_BACKUP_DIR", "/data/backups")

# The day the router went live. Rows before this were never routed by anything,
# so their NULL is legacy rather than a symptom.
ROUTER_LIVE = "2026-07-08"

SELECT_SCOPE = """
    SELECT m.id, m.conversation_id, m.created_at, c.user_email
      FROM messages m
      JOIN conversations c ON c.id = m.conversation_id
     WHERE m.role = 'user'
       AND m.attachments IS NOT NULL
       AND m.attachments != ''
       AND m.persona IS NULL
       AND m.created_at >= ?
     ORDER BY m.created_at
"""

UPDATE_SCOPE = """
    UPDATE messages
       SET persona = ?
     WHERE role = 'user'
       AND attachments IS NOT NULL
       AND attachments != ''
       AND persona IS NULL
       AND created_at >= ?
"""


def default_persona_id() -> str:
    """Take the id from the app rather than hardcoding it, so this can never
    disagree with what the code actually ran."""
    try:
        sys.path.insert(0, "/app")
        import personas as persona_module  # type: ignore

        return persona_module.DEFAULT_PERSONA_ID
    except Exception as exc:  # pragma: no cover - container-only import
        fallback = os.getenv("DEFAULT_PERSONA", "chat")
        print(f"  ! could not import personas ({exc}); using {fallback!r}")
        return fallback


def backup(conn: sqlite3.Connection) -> str:
    """Snapshot via the SQLite backup API, not `cp`. The database runs in WAL
    mode with a live writer attached, so copying the file alone can capture a
    torn state that silently omits recent commits."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = os.path.join(BACKUP_DIR, f"chats.db.pre-persona-backfill-{stamp}")
    with sqlite3.connect(path) as dest:
        conn.backup(dest)
    size = os.path.getsize(path)
    print(f"  backup written: {path} ({size:,} bytes)")
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--apply",
        action="store_true",
        help="perform the backup and the UPDATE (default is a dry run)",
    )
    args = ap.parse_args()

    persona = default_persona_id()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    rows = list(conn.execute(SELECT_SCOPE, (ROUTER_LIVE,)))
    convs = {r["conversation_id"] for r in rows}
    users = {r["user_email"] for r in rows}

    print(f"database        : {DB_PATH}")
    print(f"scope           : attachment turns, persona NULL, >= {ROUTER_LIVE}")
    print(f"would set       : persona = {persona!r}")
    print(f"matched         : {len(rows)} rows, {len(convs)} conversations, "
          f"{len(users)} users")
    if rows:
        print(f"date range      : {rows[0]['created_at']} .. {rows[-1]['created_at']}")

    # Context: what the same population looks like on turns WITHOUT an
    # attachment, which is the comparison that identified the bug.
    print("\n  profile mix on turns WITHOUT an attachment since go-live:")
    for r in conn.execute(
        """SELECT COALESCE(persona,'NULL') p, COUNT(*) n
             FROM messages
            WHERE role='user' AND (attachments IS NULL OR attachments='')
              AND created_at >= ?
            GROUP BY p ORDER BY n DESC""",
        (ROUTER_LIVE,),
    ):
        print(f"    {r['p']:<10} {r['n']}")

    if not rows:
        print("\nNothing to do.")
        return 0

    if not args.apply:
        print("\nDry run. Re-run with --apply to write.")
        for r in rows[:5]:
            print(f"    {r['id']}  {r['created_at']}  {r['user_email']}")
        if len(rows) > 5:
            print(f"    ... and {len(rows) - 5} more")
        return 0

    print()
    backup(conn)
    with conn:
        cur = conn.execute(UPDATE_SCOPE, (persona, ROUTER_LIVE))
        print(f"  updated {cur.rowcount} rows")

    remaining = list(conn.execute(SELECT_SCOPE, (ROUTER_LIVE,)))
    print(f"  remaining in scope after update: {len(remaining)} (expected 0)")

    print("\n  profile mix on turns WITH an attachment since go-live:")
    for r in conn.execute(
        """SELECT COALESCE(persona,'NULL') p, COUNT(*) n
             FROM messages
            WHERE role='user' AND attachments IS NOT NULL AND attachments!=''
              AND created_at >= ?
            GROUP BY p ORDER BY n DESC""",
        (ROUTER_LIVE,),
    ):
        print(f"    {r['p']:<10} {r['n']}")
    conn.close()
    return 0 if not remaining else 1


if __name__ == "__main__":
    sys.exit(main())
