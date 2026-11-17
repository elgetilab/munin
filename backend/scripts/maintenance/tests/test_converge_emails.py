"""
converge_emails.py against a throwaway chats.db built by the service's own
schema code, plus a gateway.db and user_docs tree shaped like production.

Run:
    pytest backend/scripts/maintenance/tests/test_converge_emails.py -q
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parents[2] / "retrieval"))

import converge_emails as C  # noqa: E402
from test_merge_state import make_db  # noqa: E402

ALIAS, IDENT = "ann@med.de", "ann@gmx.de"


def h(e):
    return hashlib.sha256(e.encode()).hexdigest()[:16]


@pytest.fixture
def mapfile(tmp_path):
    p = tmp_path / "map.json"
    p.write_text(json.dumps({ALIAS: IDENT}))
    return str(p)


@pytest.fixture
def chats(tmp_path):
    db = make_db(tmp_path / "chats.db")
    c = sqlite3.connect(db)
    for i, who in enumerate([ALIAS, ALIAS, IDENT]):
        c.execute("INSERT INTO conversations (id, user_email, persona, created_at, updated_at) "
                  "VALUES (?, ?, 'p', 't', 't')", (f"c{i}", who))
    c.execute("INSERT INTO messages (id, conversation_id, index_in_conversation, role, content, created_at) "
              "VALUES ('m0', 'c0', 0, 'user', 'zebrafish neurons', 't')")
    # user_memory: one clash (alias newer wins), one alias-only key.
    c.executemany("INSERT INTO user_memory VALUES (?, ?, ?, ?, ?)", [
        (ALIAS, "lab", "alias-val", "t", "2026-11-02"),
        (IDENT, "lab", "ident-val", "t", "2026-11-01"),
        (ALIAS, "tz", "CET", "t", "2026-11-01"),
    ])
    # user_profiles: both exist, identity newer, identity kept.
    c.executemany("INSERT INTO user_profiles (user_email, about_me, created_at, updated_at) "
                  "VALUES (?, ?, 't', ?)", [(ALIAS, "old", "2026-01"), (IDENT, "new", "2026-02")])
    c.commit()
    c.close()
    return str(db)


def rows(db, sql, *a):
    c = sqlite3.connect(db)
    try:
        return c.execute(sql, a).fetchall()
    finally:
        c.close()


def test_dry_run_writes_nothing(chats, mapfile, capsys):
    assert C.main(["chats", chats, "--map", mapfile]) == 0
    rep = json.loads(capsys.readouterr().out)
    assert rep["tables"]["conversations"]["moved"] == 2
    assert rows(chats, "SELECT COUNT(*) FROM conversations WHERE user_email=?", ALIAS) == [(2,)]


def test_apply_moves_and_resolves_key_clashes(chats, mapfile, capsys):
    assert C.main(["chats", chats, "--map", mapfile, "--apply"]) == 0
    rep = json.loads(capsys.readouterr().out)
    assert rows(chats, "SELECT COUNT(*) FROM conversations WHERE user_email=?", IDENT) == [(3,)]
    assert dict(rows(chats, "SELECT key, value FROM user_memory WHERE user_email=?", IDENT)) == {
        "lab": "alias-val", "tz": "CET"}
    assert rows(chats, "SELECT about_me FROM user_profiles") == [("new",)]
    assert rep["tables"]["user_memory"] == {"moved": 1, "alias_row_won": 1,
                                            "identity_row_kept": 0, "users": [h(IDENT)]}
    assert (Path(chats).parent / rep["backup"]).exists()
    # FTS untouched and still finds the message.
    assert rows(chats, "SELECT conversation_id FROM messages_fts WHERE messages_fts MATCH 'zebrafish'") == [("c0",)]
    # Idempotent.
    assert C.main(["chats", chats, "--map", mapfile, "--apply"]) == 0
    assert json.loads(capsys.readouterr().out)["tables"] == {}


def test_gateway_tables(tmp_path, mapfile, capsys):
    db = str(tmp_path / "gateway.db")
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE api_keys (id TEXT PRIMARY KEY, key_hash TEXT UNIQUE, user_email TEXT)")
    c.execute("CREATE TABLE usage_log (id INTEGER PRIMARY KEY, user_email TEXT, timestamp TEXT)")
    c.execute("CREATE TABLE user_activity (email TEXT PRIMARY KEY, last_seen_at TEXT, source TEXT)")
    c.execute("INSERT INTO api_keys VALUES ('k1', 'h1', ?)", (ALIAS,))
    c.executemany("INSERT INTO usage_log (user_email, timestamp) VALUES (?, 't')", [(ALIAS,), (IDENT,)])
    c.executemany("INSERT INTO user_activity VALUES (?, ?, 'web')", [(ALIAS, "2026-11-10"), (IDENT, "2026-11-01")])
    c.commit()
    c.close()
    assert C.main(["gateway", db, "--map", mapfile, "--apply"]) == 0
    assert rows(db, "SELECT user_email FROM api_keys") == [(IDENT,)]
    assert rows(db, "SELECT COUNT(*) FROM usage_log WHERE user_email=?", IDENT) == [(2,)]
    assert rows(db, "SELECT email, last_seen_at FROM user_activity") == [(IDENT, "2026-11-10")]


def test_files_moves_docs_and_refuses_collisions(tmp_path, mapfile, capsys):
    root = tmp_path / "user_docs"
    (root / h(ALIAS) / "d1").mkdir(parents=True)
    (root / h(ALIAS) / "d1" / "a.pdf").write_text("x")
    (root / h(IDENT) / "d2").mkdir(parents=True)
    assert C.main(["files", str(root), "--map", mapfile, "--apply"]) == 0
    assert (root / h(IDENT) / "d1" / "a.pdf").exists()
    assert not (root / h(ALIAS)).exists()
    (root / h(ALIAS) / "d2").mkdir(parents=True)
    capsys.readouterr()
    assert C.main(["files", str(root), "--map", mapfile, "--apply"]) == C.EXIT_REFUSED
    assert (root / h(ALIAS) / "d2").exists()


def test_plan_reads_auth_db(tmp_path, capsys):
    db = str(tmp_path / "sessions.db")
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, identity_email TEXT)")
    c.execute("CREATE TABLE user_emails (email TEXT PRIMARY KEY, user_id INTEGER, is_primary INTEGER)")
    c.execute("INSERT INTO users VALUES (1, ?)", (IDENT,))
    c.executemany("INSERT INTO user_emails VALUES (?, 1, ?)", [(IDENT, 1), (ALIAS, 0)])
    c.commit()
    c.close()
    assert C.main(["plan", db]) == 0
    assert json.loads(capsys.readouterr().out) == {ALIAS: IDENT}


def test_plan_to_user_key_maps_every_address_and_the_identity(tmp_path, capsys):
    db = str(tmp_path / "sessions.db")
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, identity_email TEXT)")
    c.execute("CREATE TABLE user_emails (email TEXT PRIMARY KEY, user_id INTEGER, is_primary INTEGER)")
    # User 1 removed their identity address since; user 2 has one address.
    c.executemany("INSERT INTO users VALUES (?, ?)", [(1, "old@x.org"), (2, "b@x.org")])
    c.executemany("INSERT INTO user_emails VALUES (?, ?, ?)",
                  [("new@x.org", 1, 1), ("alt@x.org", 1, 0), ("b@x.org", 2, 1)])
    c.commit()
    c.close()
    assert C.main(["plan", db, "--to-user-key"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "old@x.org": "u1", "new@x.org": "u1", "alt@x.org": "u1", "b@x.org": "u2"}


def test_user_key_move_keeps_the_gateway_address(tmp_path, capsys):
    p = tmp_path / "map.json"
    p.write_text(json.dumps({IDENT: "u7", ALIAS: "u7"}))
    db = str(tmp_path / "gateway.db")
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE user_activity (email TEXT PRIMARY KEY, last_seen_at TEXT, "
              "source TEXT, address TEXT)")
    c.executemany("INSERT INTO user_activity (email, last_seen_at, source) VALUES (?, ?, 'web')",
                  [(IDENT, "2026-11-10"), (ALIAS, "2026-11-01")])
    c.commit()
    c.close()
    assert C.main(["gateway", db, "--map", str(p), "--apply"]) == 0
    assert rows(db, "SELECT email, address, last_seen_at FROM user_activity") == [
        ("u7", IDENT, "2026-11-10")]
