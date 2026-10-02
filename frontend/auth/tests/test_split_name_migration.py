"""Tests for the split_name_v1 migration.

The migration:
  1. Adds first_name + last_name columns to users.
  2. Backfills first/last from the existing single `name` (split on
     first whitespace).
  3. Overlays the new whitelist.csv (email, first_name, last_name,
     role) on top, OVERWRITING first/last for any user whose primary
     email appears in the CSV. This is the recovery path for the
     ~145 users seeded under the old single-name schema -- the user
     judged the whitelist a more reliable source than the post-merge
     DB.
  4. Drops the old `name` column (SQLite >= 3.35).

The tests build a pre-migration DB by hand (CREATE TABLE with the
old single-column shape + INSERT rows + INSERT primary emails), then
run migrate_split_name_v1 and inspect the resulting schema and rows.
"""

from __future__ import annotations

import csv
import sqlite3
from datetime import datetime, timezone


def _create_legacy_users_table(auth_env) -> None:
    """Drop the new-shape users table created by init_db, replace
    with the old single-column shape so the migration has something
    real to convert."""
    conn = auth_env.get_db()
    conn.execute("DROP TABLE users")
    conn.execute("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('user', 'group_leader', 'admin')),
            research_group TEXT REFERENCES groups(slug) ON DELETE SET NULL,
            username TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def _insert_legacy_user(auth_env, name: str, role: str = "user",
                        email: str | None = None) -> int:
    now = datetime.now(timezone.utc).isoformat()
    conn = auth_env.get_db()
    cur = conn.execute(
        "INSERT INTO users (name, role, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (name, role, now, now),
    )
    uid = cur.lastrowid
    if email:
        conn.execute(
            "INSERT INTO user_emails (email, user_id, is_primary, added_at) "
            "VALUES (?, ?, 1, ?)",
            (email.lower(), uid, now),
        )
    conn.commit()
    conn.close()
    return uid


def _write_whitelist(auth_env, rows: list[dict]) -> None:
    """Write a whitelist.csv with the NEW 4-column shape."""
    with open(auth_env.WHITELIST_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f, fieldnames=["email", "first_name", "last_name", "role"]
        )
        w.writeheader()
        for r in rows:
            w.writerow(r)


def test_migration_adds_columns_and_drops_name(auth_env):
    auth_env.init_db()
    _create_legacy_users_table(auth_env)
    _insert_legacy_user(auth_env, "Max Mustermann", email="max.mustermann@x.org")

    summary = auth_env.migrate_split_name_v1()

    assert summary["already_run"] is False
    assert summary["rows_backfilled"] == 1
    conn = auth_env.get_db()
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
    assert "first_name" in cols
    assert "last_name" in cols
    assert "name" not in cols
    assert summary["name_column_dropped"] is True
    conn.close()


def test_migration_splits_existing_single_name_on_whitespace(auth_env):
    auth_env.init_db()
    _create_legacy_users_table(auth_env)
    _insert_legacy_user(auth_env, "Max Mustermann", email="max.mustermann@x.org")
    _insert_legacy_user(auth_env, "Erika", email="erika@x.org")
    _insert_legacy_user(auth_env, "  Jean-Luc  Exemple", email="jp@x.org")

    auth_env.migrate_split_name_v1()

    conn = auth_env.get_db()
    rows = conn.execute(
        "SELECT u.first_name, u.last_name, ue.email "
        "FROM users u JOIN user_emails ue ON ue.user_id = u.id"
    ).fetchall()
    by_email = {r["email"]: (r["first_name"], r["last_name"]) for r in rows}
    assert by_email["max.mustermann@x.org"] == ("Max", "Mustermann")
    assert by_email["erika@x.org"] == ("Erika", None)
    # Multi-space + leading whitespace are normalised by .strip()/.partition().
    assert by_email["jp@x.org"] == ("Jean-Luc", "Exemple")
    conn.close()


def test_migration_overwrites_from_whitelist(auth_env):
    """The whitelist is the source of truth: for any user whose primary
    email matches a whitelist row, first/last get overwritten with the
    whitelist's values, not derived-from-old-name values."""
    auth_env.init_db()
    _create_legacy_users_table(auth_env)
    _insert_legacy_user(auth_env, "Wrong", email="max.mustermann@x.org")

    _write_whitelist(auth_env, [
        {"email": "max.mustermann@x.org", "first_name": "Max",
         "last_name": "Mustermann", "role": "user"},
    ])

    summary = auth_env.migrate_split_name_v1()

    assert summary["rows_overwritten_from_whitelist"] == 1
    conn = auth_env.get_db()
    row = conn.execute(
        "SELECT first_name, last_name FROM users LIMIT 1"
    ).fetchone()
    assert row["first_name"] == "Max"
    assert row["last_name"] == "Mustermann"
    conn.close()


def test_migration_handles_blank_last_name_in_whitelist(auth_env):
    """Whitelist rows with blank last_name (the 30 unambiguous ones)
    must overwrite to NULL, not leave the migration's whitespace-split
    value in place."""
    auth_env.init_db()
    _create_legacy_users_table(auth_env)
    _insert_legacy_user(auth_env, "Wrong Tail", email="erika@x.org")

    _write_whitelist(auth_env, [
        {"email": "erika@x.org", "first_name": "Erika",
         "last_name": "", "role": "user"},
    ])

    auth_env.migrate_split_name_v1()

    conn = auth_env.get_db()
    row = conn.execute(
        "SELECT first_name, last_name FROM users LIMIT 1"
    ).fetchone()
    assert row["first_name"] == "Erika"
    assert row["last_name"] is None
    conn.close()


def test_migration_is_idempotent(auth_env):
    auth_env.init_db()
    _create_legacy_users_table(auth_env)
    _insert_legacy_user(auth_env, "Max Mustermann", email="max.mustermann@x.org")

    first = auth_env.migrate_split_name_v1()
    second = auth_env.migrate_split_name_v1()
    assert first["already_run"] is False
    assert second["already_run"] is True


def test_migration_no_whitelist_file_still_runs(auth_env):
    """If the CSV is absent, the migration falls back to the
    whitespace split and still completes."""
    auth_env.init_db()
    _create_legacy_users_table(auth_env)
    _insert_legacy_user(auth_env, "Erika", email="erika@x.org")

    # WHITELIST_PATH is set by the fixture but the file isn't written.
    summary = auth_env.migrate_split_name_v1()

    assert summary["rows_backfilled"] == 1
    assert summary["rows_overwritten_from_whitelist"] == 0
    conn = auth_env.get_db()
    row = conn.execute("SELECT first_name, last_name FROM users LIMIT 1").fetchone()
    assert row["first_name"] == "Erika"
    conn.close()


def test_migration_on_fresh_db_is_noop(auth_env):
    """Fresh deploys land on init_db's NEW-shape table directly. The
    migration must still run (so subsequent boots are gated) but find
    nothing to backfill."""
    auth_env.init_db()
    summary = auth_env.migrate_split_name_v1()
    assert summary["already_run"] is False
    assert summary["rows_backfilled"] == 0
