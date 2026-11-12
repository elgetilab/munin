"""
merge_state.py against throwaway databases built by the service's own schema
code (chat_store.init_db + research_store._ensure_tables), so a schema change
there is exercised here without a second copy of the DDL.

Run:
    pip install pytest aiosqlite
    pytest backend/scripts/maintenance/tests/test_merge_state.py -q

The Qdrant tests need a disposable Qdrant and are skipped unless
MERGE_STATE_QDRANT_URL points at one (they create and drop their own
collections), e.g.
    docker run --rm -d --name qdrant-merge-test -p 127.0.0.1:16333:6333 qdrant/qdrant:v1.16.3
    MERGE_STATE_QDRANT_URL=http://127.0.0.1:16333 pytest ... -q
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import sqlite3
import sys
import uuid
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parents[2] / "retrieval"))

import merge_state as M  # noqa: E402

LEGACY_MESSAGES = """
CREATE TABLE messages (
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
)
"""


def make_db(path: Path, legacy_column_order: bool = False) -> Path:
    """A chats.db exactly as the service creates it. With legacy_column_order the
    messages table predates the attachments/persona columns, so init_db appends
    them and the column ORDER differs from a fresh DB, as between cluster and
    Lite."""
    if legacy_column_order:
        c = sqlite3.connect(path)
        c.execute(LEGACY_MESSAGES)
        c.commit()
        c.close()

    async def go():
        import chat_store
        import research_store
        chat_store.CHATS_DB_PATH = str(path)
        chat_store._db = None
        research_store._tables_ready = False
        await chat_store.init_db()
        await research_store._ensure_tables()
        await chat_store.close_db()

    asyncio.run(go())
    return path


def conn(path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(path)
    c.execute("PRAGMA foreign_keys=ON")
    return c


def add_conv(c, cid=None, email="a@example.org", ts="2026-11-01T10:00:00Z", title="t"):
    cid = cid or str(uuid.uuid4())
    c.execute("INSERT INTO conversations (id, user_email, title, persona, created_at, updated_at)"
              " VALUES (?, ?, ?, 'munin', ?, ?)", (cid, email, title, ts, ts))
    return cid


def add_msg(c, cid, idx, content, ts="2026-11-01T10:00:00Z", mid=None, attachments=None):
    mid = mid or str(uuid.uuid4())
    c.execute("INSERT INTO messages (id, conversation_id, index_in_conversation, role, content,"
              " created_at, attachments) VALUES (?, ?, ?, 'user', ?, ?, ?)",
              (mid, cid, idx, content, ts, attachments))
    return mid


def run(capsys, *argv) -> tuple[int, dict]:
    code = M.main(list(argv))
    out = capsys.readouterr().out
    return code, (json.loads(out) if out.strip().startswith("{") else {})


def db_args(src, tgt, tmp, *extra):
    return ["db", "--source", str(src), "--target", str(tgt), "--work-dir", str(tmp / "work"),
            "--backup-dir", str(tmp / "backups"), *extra]


@pytest.fixture
def pair(tmp_path):
    src = make_db(tmp_path / "lite.db")
    tgt = make_db(tmp_path / "cluster.db", legacy_column_order=True)
    return src, tgt, tmp_path


# ---------------------------------------------------------------------------
# db
# ---------------------------------------------------------------------------

def test_column_order_really_differs(pair):
    src, tgt, _ = pair
    s = [r[1] for r in conn(src).execute("PRAGMA table_info(messages)")]
    t = [r[1] for r in conn(tgt).execute("PRAGMA table_info(messages)")]
    assert s != t and set(s) == set(t)


def test_merge_inserts_by_name_indexes_and_is_idempotent(pair, capsys):
    src, tgt, tmp = pair
    with conn(tgt) as c:
        old = add_conv(c, email="b@example.org")
        add_msg(c, old, 0, "existing cluster text about photosynthesis")
    with conn(src) as c:
        cid = add_conv(c)
        add_msg(c, cid, 0, "question about zeolites", attachments='[{"document_id": "doc_aaaaaaaaaaaa"}]')
        add_msg(c, cid, 1, "answer mentioning perovskites")
        c.execute("INSERT INTO research_jobs (job_id, conversation_id, user_email, status,"
                  " created_at, updated_at) VALUES ('j1', ?, 'a@example.org', 'done', 1.0, 2.0)", (cid,))
        c.execute("INSERT INTO research_events VALUES ('j1', 0, 1.5, '{}')")

    code, dry = run(capsys, *db_args(src, tgt, tmp))
    assert code == 0 and dry["pending_writes"] == 5 and dry["mode"] == "dry-run"
    assert conn(tgt).execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1

    code, rep = run(capsys, *db_args(src, tgt, tmp, "--apply"))
    assert code == 0 and rep["result"] == "merged", rep
    assert rep["fts_after"]["merged_messages_match_found"] == "2/2"
    assert rep["replan_after_write"] == "no-op"
    assert Path(rep["target_backup"]["path"]).is_file()
    b = sqlite3.connect(rep["target_backup"]["path"])
    assert b.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1

    c = conn(tgt)
    row = c.execute("SELECT content, attachments FROM messages WHERE index_in_conversation=0 "
                    "AND conversation_id=?", (cid,)).fetchone()
    assert row == ("question about zeolites", '[{"document_id": "doc_aaaaaaaaaaaa"}]')
    hits = c.execute("SELECT m.conversation_id FROM messages_fts f JOIN messages m ON m.rowid=f.rowid "
                     "WHERE messages_fts MATCH 'perovskites'").fetchall()
    assert hits == [(cid,)]

    code, again = run(capsys, *db_args(src, tgt, tmp, "--apply"))
    assert code == 0 and again["pending_writes"] == 0 and again["result"] == "nothing to do"
    assert len(list((tmp / "backups").iterdir())) == 1  # no-op takes no backup


def test_merged_rows_load_through_chat_store(pair, capsys):
    src, tgt, tmp = pair
    with conn(src) as c:
        cid = add_conv(c)
        add_msg(c, cid, 0, "hello there")
        add_msg(c, cid, 1, "general kenobi")
    assert run(capsys, *db_args(src, tgt, tmp, "--apply"))[0] == 0

    async def go():
        import chat_store
        chat_store.CHATS_DB_PATH = str(tgt)
        chat_store._db = None
        conv = await chat_store.get_conversation(cid, "a@example.org")
        found = await chat_store.search_user_messages("a@example.org", "kenobi")
        await chat_store.close_db()
        return conv, found

    conv, found = asyncio.run(go())
    assert [m["content"] for m in conv["messages"]] == ["hello there", "general kenobi"]
    assert found["total_matches"] == 1


def test_differing_collision_refuses_then_policies(pair, capsys):
    src, tgt, tmp = pair
    cid = str(uuid.uuid4())
    with conn(tgt) as c:
        add_conv(c, cid, title="cluster title", ts="2026-11-01T10:00:00Z")
    with conn(src) as c:
        add_conv(c, cid, title="lite title", ts="2026-11-05T10:00:00Z")

    code, rep = run(capsys, *db_args(src, tgt, tmp, "--apply"))
    assert code == 2 and rep["refused"]
    assert rep["conflict_samples"]["conversations"][0]["columns"] == [
        "created_at", "title", "updated_at"]
    assert conn(tgt).execute("SELECT title FROM conversations").fetchone()[0] == "cluster title"

    code, rep = run(capsys, *db_args(src, tgt, tmp, "--apply", "--on-conflict", "keep-target"))
    assert code == 0 and rep["tables"]["conversations"]["kept_target"] == 1
    assert conn(tgt).execute("SELECT title FROM conversations").fetchone()[0] == "cluster title"

    code, rep = run(capsys, *db_args(src, tgt, tmp, "--apply", "--on-conflict", "newer-wins"))
    assert code == 0 and rep["written"]["conversations"]["updated"] == 1
    assert conn(tgt).execute("SELECT title FROM conversations").fetchone()[0] == "lite title"


def test_source_wins_message_edit_keeps_fts_in_step(pair, capsys):
    src, tgt, tmp = pair
    cid, mid = str(uuid.uuid4()), str(uuid.uuid4())
    for path, text in ((tgt, "draft about graphene"), (src, "final about borophene")):
        with conn(path) as c:
            add_conv(c, cid)
            add_msg(c, cid, 0, text, mid=mid)
    code, rep = run(capsys, *db_args(src, tgt, tmp, "--apply", "--on-conflict", "source-wins"))
    assert code == 0 and rep["written"]["messages"]["updated"] == 1
    c = conn(tgt)
    assert c.execute("SELECT COUNT(*) FROM messages_fts WHERE messages_fts MATCH 'graphene'").fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM messages_fts WHERE messages_fts MATCH 'borophene'").fetchone()[0] == 1


def test_index_slot_taken_is_a_conflict(pair, capsys):
    src, tgt, tmp = pair
    cid = str(uuid.uuid4())
    with conn(tgt) as c:
        add_conv(c, cid)
        add_msg(c, cid, 0, "one")
    with conn(src) as c:
        add_conv(c, cid)
        add_msg(c, cid, 0, "different message, same slot")
    code, rep = run(capsys, *db_args(src, tgt, tmp))
    assert code == 2 and rep["conflict_samples"]["messages"][0]["kind"] == "index_taken"


def test_source_internal_duplicate_slot_is_copied_not_refused(pair, capsys):
    src, tgt, tmp = pair
    with conn(src) as c:
        cid = add_conv(c)
        add_msg(c, cid, 5, "double persisted A")
        add_msg(c, cid, 5, "double persisted B")
    code, rep = run(capsys, *db_args(src, tgt, tmp, "--apply"))
    assert code == 0 and rep["written"]["messages"]["inserted"] == 2
    assert rep["tables"]["messages"]["source_duplicate_index_slots"] == 1


def test_orphans_refuse_unless_skipped(pair, capsys):
    src, tgt, tmp = pair
    c = sqlite3.connect(src)  # FKs off, as an old or hand-edited DB might be
    c.execute("INSERT INTO artifact_versions VALUES ('art_gone', 1, 'x', NULL, "
              "'2026-11-01T00:00:00Z', 'assistant')")
    c.commit()
    code, rep = run(capsys, *db_args(src, tgt, tmp, "--apply"))
    assert code == 2 and rep["orphan_samples"]["artifact_versions"]
    code, rep = run(capsys, *db_args(src, tgt, tmp, "--apply", "--skip-orphans"))
    assert code == 0 and rep["pending_writes"] == 0


def test_schema_mismatch_refuses_unless_drift_allowed(pair, capsys):
    src, tgt, tmp = pair
    with conn(src) as c:
        c.execute("ALTER TABLE conversations ADD COLUMN future_col TEXT")
        add_conv(c)
    code, rep = run(capsys, *db_args(src, tgt, tmp, "--apply"))
    assert code == 2 and any("columns differ" in p for p in rep["refused"])
    code, rep = run(capsys, *db_args(src, tgt, tmp, "--apply", "--allow-schema-drift"))
    assert code == 0 and rep["written"]["conversations"]["inserted"] == 1


def test_since_scopes_and_pulls_in_parents(pair, capsys):
    src, tgt, tmp = pair
    with conn(src) as c:
        old = add_conv(c, ts="2026-10-01T00:00:00Z")
        add_msg(c, old, 0, "before the cutoff", ts="2026-10-01T00:00:00Z")
        add_msg(c, old, 1, "after the cutoff", ts="2026-11-02T00:00:00Z")
        other = add_conv(c, ts="2026-10-01T00:00:00Z")
        add_msg(c, other, 0, "entirely before", ts="2026-10-01T00:00:00Z")
    code, rep = run(capsys, *db_args(src, tgt, tmp, "--apply", "--since", "2026-11-01T00:00:00Z"))
    assert code == 0, rep
    assert rep["tables"]["conversations"]["pulled_in_as_parent"] == 1
    assert rep["written"]["messages"]["inserted"] == 1
    ids = {r[0] for r in conn(tgt).execute("SELECT id FROM conversations")}
    assert ids == {old}


def test_report_hashes_natural_keys(pair, capsys):
    src, tgt, tmp = pair
    for path, val in ((tgt, "x"), (src, "y")):
        with conn(path) as c:
            c.execute("INSERT INTO user_memory VALUES ('secret@example.org', 'pet_name', ?, "
                      "'2026-11-01T00:00:00Z', '2026-11-01T00:00:00Z')", (val,))
    code = M.main(db_args(src, tgt, tmp))
    out = capsys.readouterr().out
    assert code == 2 and "secret@example.org" not in out and "pet_name" not in out


# ---------------------------------------------------------------------------
# files
# ---------------------------------------------------------------------------

def put(root: Path, user: str, doc: str, name: str, data: bytes) -> None:
    d = root / user / doc
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_bytes(data)


def test_files_copy_idempotent_and_conflicts(tmp_path, capsys):
    s, t = tmp_path / "src", tmp_path / "tgt"
    u, v = M.email_hash("a@example.org"), M.email_hash("b@example.org")
    put(s, u, "doc_000000000001", "paper.pdf", b"%PDF one")
    put(s, u, "doc_000000000002", "notes.md", b"# two")
    put(t, v, "doc_000000000009", "other.txt", b"cluster")
    (s / "not-a-hash").mkdir()
    os.symlink("/etc", s / u / "doc_00000000000f")

    args = ["files", "--source-dir", str(s), "--target-dir", str(t)]
    code, rep = run(capsys, *args)
    assert code == 0 and rep["copy_documents"] == 2 and rep["skipped_unexpected_entries"] == 2
    code, rep = run(capsys, *args, "--apply")
    assert code == 0 and rep["copied_files"] == 2
    assert (t / u / "doc_000000000001" / "paper.pdf").read_bytes() == b"%PDF one"
    assert not (t / u / "doc_00000000000f").exists()
    code, rep = run(capsys, *args, "--apply")
    assert code == 0 and rep["result"] == "nothing to do" and rep["identical_skipped"] == 2

    (s / u / "doc_000000000002" / "notes.md").write_bytes(b"# changed")
    code, rep = run(capsys, *args, "--apply")
    assert code == 2 and rep["conflicts"][0]["kind"] == "content_differs"
    code, rep = run(capsys, *args, "--apply", "--on-conflict", "keep-target")
    assert code == 0 and rep["kept_target"] == 1

    put(s, u, "doc_000000000009", "other.txt", b"same id, other user")
    code, rep = run(capsys, *args, "--on-conflict", "keep-target")
    assert code == 2 and rep["conflicts"][0]["kind"] == "doc_id_owned_by_other_user"


# ---------------------------------------------------------------------------
# qdrant (needs a disposable Qdrant)
# ---------------------------------------------------------------------------

QURL = os.getenv("MERGE_STATE_QDRANT_URL")
needs_qdrant = pytest.mark.skipif(not QURL, reason="MERGE_STATE_QDRANT_URL not set")


def _points(n_docs, chunks, email="a@example.org", seed=0):
    rnd = random.Random(seed)
    pts = []
    for d in range(n_docs):
        doc = f"doc_{seed:06x}{d:06x}"
        for i in range(chunks):
            v = [rnd.uniform(-1, 1) for _ in range(8)]
            norm = sum(x * x for x in v) ** 0.5
            pts.append({"id": str(uuid.UUID(int=rnd.getrandbits(128))),
                        "vector": [x / norm for x in v],
                        "payload": {"user_email": email, "document_id": doc, "chunk_index": i,
                                    "chunk_text": f"chunk {i}", "total_chunks": chunks,
                                    "upload_time": "2026-11-05T00:00:00Z",
                                    "conversation_id": None, "project_id": None,
                                    "filename": "f.pdf"}})
    return pts


def _seed(coll, pts):
    M._q(QURL, "PUT", f"/collections/{coll}", {"vectors": {"size": 8, "distance": "Cosine"}})
    if pts:
        M._q(QURL, "PUT", f"/collections/{coll}/points?wait=true", {"points": pts})


@needs_qdrant
def test_qdrant_export_merge_idempotent_and_conflicts(tmp_path, capsys):
    tag = uuid.uuid4().hex[:8]
    src_c, tgt_c = f"t_src_{tag}", f"t_tgt_{tag}"
    try:
        lite = _points(3, 4, seed=1)
        cluster = _points(2, 3, email="b@example.org", seed=2)
        _seed(src_c, lite)
        _seed(tgt_c, cluster)
        export = tmp_path / "pts.jsonl"
        assert M.main(["qdrant-export", "--url", QURL, "--collection", src_c,
                       "--out", str(export)]) == 0
        args = ["qdrant", "--url", QURL, "--collection", tgt_c, "--source", str(export)]
        code, rep = run(capsys, *args)
        assert code == 0 and rep["upsert"] == 12 and rep["source_points"] == 12
        code, rep = run(capsys, *args, "--apply")
        assert code == 0 and rep["target_points_after"] == 18 and rep["replan_after_write"] == "no-op"
        code, rep = run(capsys, *args, "--apply")
        assert code == 0 and rep["result"] == "nothing to do" and rep["identical_skipped"] == 12

        # Same point id, different payload.
        p = dict(lite[0], payload=dict(lite[0]["payload"], chunk_text="edited"))
        M._q(QURL, "PUT", f"/collections/{tgt_c}/points?wait=true", {"points": [p]})
        code, rep = run(capsys, *args)
        assert code == 2 and rep["conflicts"] == 1
        code, rep = run(capsys, *args, "--on-conflict", "keep-target")
        assert code == 0 and rep["kept_target"] == 1

        # A document already indexed under other point ids (re-embedded).
        extra = dict(lite[5], id=str(uuid.uuid4()))
        M._q(QURL, "PUT", f"/collections/{tgt_c}/points?wait=true", {"points": [extra]})
        code, rep = run(capsys, *args, "--on-conflict", "keep-target")
        assert rep["documents_indexed_differently"] == 1

        # Into a target without the collection.
        new_c = f"t_new_{tag}"
        code, rep = run(capsys, "qdrant", "--url", QURL, "--collection", new_c,
                        "--source", str(export), "--apply")
        assert code == 2
        code, rep = run(capsys, "qdrant", "--url", QURL, "--collection", new_c,
                        "--source", str(export), "--apply", "--create-collection")
        assert code == 0 and rep["target_points_after"] == 12
    finally:
        for c in (src_c, tgt_c, f"t_new_{tag}"):
            try:
                M._q(QURL, "DELETE", f"/collections/{c}")
            except RuntimeError:
                pass
