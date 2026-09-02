"""
Tests for memory proposals (P2 #25).

Covers:
  - memory_proposals_store CRUD + FIFO caps (proposals 10/user,
    rejections 50/user)
  - all_known_keys union (accepted + pending + rejected)
  - memory_extract._parse_output: JSON parsing tolerance + per-entry
    validation + MAX_PROPOSALS_PER_TURN cap
  - hook gating: skips non-done terminal reasons, skips ephemeral,
    skips empty exchange

Run inside the retrieval container:
    docker exec munin-retrieval python /app/tests/test_memory_proposals.py
Or locally:
    python backend/retrieval/tests/test_memory_proposals.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Point chat_store at a private DB before importing anything that
# triggers schema creation.
_TMPDIR = tempfile.mkdtemp(prefix="munin-test-memprop-")
os.environ["CHATS_DB_PATH"] = os.path.join(_TMPDIR, "chats.db")

import chat_store  # noqa: E402
import memory_extract  # noqa: E402
import memory_proposals_store  # noqa: E402
import memory_store  # noqa: E402


def _check(name: str, ok: bool, detail: str = "") -> bool:
    label = "PASS" if ok else "FAIL"
    print(f"[{label}] {name}{(' - ' + detail) if detail and not ok else ''}")
    return ok


async def _reset_db() -> None:
    """Drop every row from the three memory tables. The schema is
    initialised on the first get_db() call."""
    db = await chat_store.get_db()
    await db.execute("DELETE FROM user_memory")
    await db.execute("DELETE FROM proposed_memories")
    await db.execute("DELETE FROM rejected_memory_keys")
    await db.commit()


# ---------------------------------------------------------------------------
# Store: create, list, get, delete
# ---------------------------------------------------------------------------

def test_create_and_list() -> bool:
    async def go():
        await _reset_db()
        await memory_proposals_store.create_proposal(
            user_email="u@x", conversation_id="c1",
            key="role", value="postdoc", reason="identity",
        )
        rows = await memory_proposals_store.list_pending("u@x")
        return (
            len(rows) == 1
            and rows[0]["key"] == "role"
            and rows[0]["value"] == "postdoc"
            and rows[0]["conversation_id"] == "c1"
            and bool(rows[0]["id"])  # uuid populated
        )
    return _check("create_proposal + list_pending", asyncio.run(go()))


def test_deleting_a_conversation_removes_its_proposals() -> bool:
    """Deleting a conversation must take its extracted proposals with it.

    Before this fix `proposed_memories` had a plain conversation_id and no
    foreign key, so nothing cleaned it up. The rows were not inert: list_pending
    is user-scoped, so they kept rendering as pills and kept consuming the
    MAX_PENDING=10 budget. Measured in production on 2026-09-02, one user sat at
    10/10 pending with every slot owned by a deleted conversation.
    """
    async def go():
        await _reset_db()
        db = await chat_store.get_db()
        await db.execute("DELETE FROM conversations")
        await db.commit()
        # Real API rather than a hand-built INSERT: conversations has NOT NULL
        # columns a literal row would have to keep in step with the schema.
        doomed = await chat_store.create_conversation("u@x", "chat", title="doomed")
        keep = await chat_store.create_conversation("u@x", "chat", title="keep")

        await memory_proposals_store.create_proposal(
            user_email="u@x", conversation_id=doomed["id"],
            key="doomed", value="v", reason=None)
        await memory_proposals_store.create_proposal(
            user_email="u@x", conversation_id=keep["id"],
            key="survivor", value="v", reason=None)
        # A proposal not tied to any conversation must be untouched: NULL is
        # not an orphan, and purging it would discard a live pending memory.
        await memory_proposals_store.create_proposal(
            user_email="u@x", conversation_id=None,
            key="unattached", value="v", reason=None)

        assert await chat_store.delete_conversation(doomed["id"], "u@x")
        keys = {r["key"] for r in await memory_proposals_store.list_pending("u@x")}
        return keys == {"survivor", "unattached"}
    return _check("deleting a conversation removes its proposals", asyncio.run(go()))


def test_delete_conversation_does_not_touch_another_users_proposals() -> bool:
    """The delete is scoped by user_email as well as conversation_id, matching
    the conversations delete beside it."""
    async def go():
        await _reset_db()
        db = await chat_store.get_db()
        await db.execute("DELETE FROM conversations")
        await db.commit()
        conv = await chat_store.create_conversation("owner@x", "chat", title="t")
        # Same conversation id, different owner: the delete must not reach it.
        await memory_proposals_store.create_proposal(
            user_email="other@x", conversation_id=conv["id"],
            key="theirs", value="v", reason=None)
        await chat_store.delete_conversation(conv["id"], "owner@x")
        keys = {r["key"] for r in await memory_proposals_store.list_pending("other@x")}
        return keys == {"theirs"}
    return _check("delete is scoped to the owning user", asyncio.run(go()))


def test_get_proposal_enforces_owner() -> bool:
    async def go():
        await _reset_db()
        row = await memory_proposals_store.create_proposal(
            user_email="u@x", conversation_id=None,
            key="k", value="v", reason=None,
        )
        same_user = await memory_proposals_store.get_proposal(row["id"], "u@x")
        other_user = await memory_proposals_store.get_proposal(row["id"], "other@x")
        return same_user is not None and other_user is None
    return _check("get_proposal scopes by user_email", asyncio.run(go()))


def test_delete_proposal_returns_true_on_hit_false_on_miss() -> bool:
    async def go():
        await _reset_db()
        row = await memory_proposals_store.create_proposal(
            user_email="u@x", conversation_id=None,
            key="k", value="v", reason=None,
        )
        first = await memory_proposals_store.delete_proposal(row["id"], "u@x")
        second = await memory_proposals_store.delete_proposal(row["id"], "u@x")
        return first is True and second is False
    return _check("delete_proposal is idempotent on miss", asyncio.run(go()))


# ---------------------------------------------------------------------------
# FIFO caps
# ---------------------------------------------------------------------------

def test_proposal_fifo_evicts_oldest() -> bool:
    """Once MAX_PENDING entries exist, the next create evicts the
    oldest (lowest proposed_at)."""
    async def go():
        await _reset_db()
        # Fill to the cap with distinct keys
        for i in range(memory_proposals_store.MAX_PENDING):
            await memory_proposals_store.create_proposal(
                user_email="u@x", conversation_id=None,
                key=f"k{i}", value=f"v{i}", reason=None,
            )
            # Tiny sleep ensures unique proposed_at timestamps so
            # FIFO ordering is deterministic across SQLite ISO-second
            # resolution.
            await asyncio.sleep(0.01)
        # One more pushes out k0
        await memory_proposals_store.create_proposal(
            user_email="u@x", conversation_id=None,
            key="k_new", value="v_new", reason=None,
        )
        rows = await memory_proposals_store.list_pending("u@x")
        keys = {r["key"] for r in rows}
        return (
            len(rows) == memory_proposals_store.MAX_PENDING
            and "k_new" in keys
            and "k0" not in keys
        )
    return _check(
        "proposed_memories cap evicts oldest on overflow",
        asyncio.run(go()),
    )


def test_rejection_idempotent_and_fifo_capped() -> bool:
    async def go():
        await _reset_db()
        # Re-rejecting the same key shouldn't duplicate the row.
        await memory_proposals_store.add_rejection("u@x", "dup_key")
        await memory_proposals_store.add_rejection("u@x", "dup_key")
        rejected = await memory_proposals_store.list_rejected_keys("u@x")
        if len([k for k in rejected if k == "dup_key"]) != 1:
            return False
        # Fill past the cap.
        for i in range(memory_proposals_store.MAX_REJECTIONS):
            await memory_proposals_store.add_rejection("u@x", f"k{i}")
            await asyncio.sleep(0.001)
        # We've added MAX_REJECTIONS + 1 unique keys ("dup_key" + N
        # k_i). The store should have FIFO-evicted to land on
        # MAX_REJECTIONS rows.
        rejected = await memory_proposals_store.list_rejected_keys("u@x")
        return len(rejected) == memory_proposals_store.MAX_REJECTIONS
    return _check(
        "rejected_memory_keys is idempotent and FIFO-capped",
        asyncio.run(go()),
    )


# ---------------------------------------------------------------------------
# Dedupe helper
# ---------------------------------------------------------------------------

def test_all_known_keys_unions_three_sources() -> bool:
    async def go():
        await _reset_db()
        # Seed one accepted, one pending, one rejected.
        await memory_store.remember("u@x", "accepted_key", "v")
        await memory_proposals_store.create_proposal(
            user_email="u@x", conversation_id=None,
            key="pending_key", value="v", reason=None,
        )
        await memory_proposals_store.add_rejection("u@x", "rejected_key")
        keys = await memory_proposals_store.all_known_keys("u@x")
        return {"accepted_key", "pending_key", "rejected_key"} == keys
    return _check(
        "all_known_keys unions accepted + pending + rejected",
        asyncio.run(go()),
    )


# ---------------------------------------------------------------------------
# Classifier output parser (no vLLM needed)
# ---------------------------------------------------------------------------

def test_parse_output_strips_code_fences() -> bool:
    raw = (
        "```json\n"
        "[{\"key\": \"role\", \"value\": \"postdoc\", \"reason\": \"identity\"}]\n"
        "```"
    )
    out = memory_extract._parse_output(raw)
    return _check(
        "_parse_output strips ```json fences",
        len(out) == 1 and out[0]["key"] == "role",
    )


def test_parse_output_skips_malformed_entries() -> bool:
    raw = """[
        {"key": "good", "value": "yes", "reason": "ok"},
        {"key": "", "value": "blank key"},
        {"value": "no key"},
        "not even an object",
        {"key": "also good", "value": "yes", "reason": "ok"}
    ]"""
    out = memory_extract._parse_output(raw)
    keys = [p["key"] for p in out]
    return _check(
        "_parse_output drops malformed entries, keeps valid ones",
        keys == ["good", "also good"],
    )


def test_parse_output_caps_at_max_proposals() -> bool:
    items = ", ".join(
        f'{{"key":"k{i}","value":"v{i}","reason":"r"}}' for i in range(10)
    )
    raw = f"[{items}]"
    out = memory_extract._parse_output(raw)
    return _check(
        "_parse_output caps at MAX_PROPOSALS_PER_TURN",
        len(out) == memory_extract.MAX_PROPOSALS_PER_TURN,
    )


def test_parse_output_returns_empty_on_garbage() -> bool:
    raw = "I cannot decide what to save."
    out = memory_extract._parse_output(raw)
    return _check("_parse_output returns [] for non-JSON garbage", out == [])


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

TESTS = [
    test_create_and_list,
    test_deleting_a_conversation_removes_its_proposals,
    test_delete_conversation_does_not_touch_another_users_proposals,
    test_get_proposal_enforces_owner,
    test_delete_proposal_returns_true_on_hit_false_on_miss,
    test_proposal_fifo_evicts_oldest,
    test_rejection_idempotent_and_fifo_capped,
    test_all_known_keys_unions_three_sources,
    test_parse_output_strips_code_fences,
    test_parse_output_skips_malformed_entries,
    test_parse_output_caps_at_max_proposals,
    test_parse_output_returns_empty_on_garbage,
]


def main() -> int:
    passed = 0
    failed = 0
    for t in TESTS:
        try:
            ok = t()
        except Exception:
            ok = False
            print(f"[FAIL] {t.__name__} - exception:")
            traceback.print_exc()
        passed += int(ok)
        failed += int(not ok)
    print(f"\n{passed} passed, {failed} failed")
    # Close the aiosqlite connection so the process exits cleanly.
    # Without this the module-level _db connection's writer thread
    # keeps the asyncio loop alive after main() returns; the
    # interpreter hangs at shutdown and zombies pile up on dev
    # machines (observed 2026-05-28: 33 stale procs after a
    # day of testing).
    try:
        asyncio.run(chat_store.close_db())
    except Exception:
        pass
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
