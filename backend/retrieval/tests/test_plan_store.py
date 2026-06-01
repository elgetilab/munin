"""
Tests for plan_store (P2 #24 Phase 1).

Covers:
  - set_plan: ID auto-assignment ('p-1', 'p-2', ...), user-supplied id
    preservation, duplicate id rejection
  - validation caps: 20 items, 200/500 char titles/notes, valid status
    enum, non-empty title
  - get_plan: returns None for missing rows, full dict shape for present
  - update_item: 404 on missing plan, 404 on missing item id, status +
    notes update independently
  - clear_plan idempotent
  - build_plan_block render shape (returns None for empty/missing)
  - Phase 2 fields default safely on set_plan (requires_approval=0,
    approved_at=NULL, approval_mode='each')

Run inside the retrieval container:
    docker exec munin-retrieval python /app/tests/test_plan_store.py
Or locally:
    python backend/retrieval/tests/test_plan_store.py
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

# Private DB before chat_store import triggers schema creation
_TMPDIR = tempfile.mkdtemp(prefix="munin-test-plan-")
os.environ["CHATS_DB_PATH"] = os.path.join(_TMPDIR, "chats.db")

import chat_store  # noqa: E402
import plan_store  # noqa: E402


def _check(name: str, ok: bool, detail: str = "") -> bool:
    label = "PASS" if ok else "FAIL"
    print(f"[{label}] {name}{(' - ' + detail) if detail and not ok else ''}")
    return ok


async def _reset_db() -> None:
    """Wipe plan rows and reinsert a fixture conversation each test.
    conversation_plans has a FOREIGN KEY to conversations(id) with
    ON DELETE CASCADE, so we need a real conversation row for
    set_plan to land."""
    db = await chat_store.get_db()
    await db.execute("DELETE FROM conversation_plans")
    # Also wipe the test conversation so the unique-id INSERT below
    # doesn't conflict on re-runs.
    await db.execute("DELETE FROM conversations WHERE id = 'c1'")
    now = chat_store._iso_now()
    await db.execute(
        "INSERT INTO conversations (id, user_email, title, persona, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        ("c1", "u@x", "fixture", "chat", now, now),
    )
    await db.commit()


# ---------------------------------------------------------------------------
# set_plan: id assignment + validation
# ---------------------------------------------------------------------------

def test_set_plan_auto_assigns_ids() -> bool:
    async def go():
        await _reset_db()
        plan = await plan_store.set_plan(
            user_email="u@x", conversation_id="c1",
            items=[{"title": "Step A"}, {"title": "Step B"}, {"title": "Step C"}],
        )
        ids = [it["id"] for it in plan["items"]]
        return ids == ["p-1", "p-2", "p-3"]
    return _check("set_plan auto-assigns p-1/p-2/p-3 when id omitted", asyncio.run(go()))


def test_set_plan_preserves_user_ids() -> bool:
    async def go():
        await _reset_db()
        plan = await plan_store.set_plan(
            user_email="u@x", conversation_id="c1",
            items=[
                {"id": "search", "title": "Search arxiv"},
                {"id": "synthesize", "title": "Write summary"},
            ],
        )
        ids = [it["id"] for it in plan["items"]]
        return ids == ["search", "synthesize"]
    return _check("user-supplied ids preserved verbatim", asyncio.run(go()))


def test_set_plan_rejects_duplicate_ids() -> bool:
    async def go():
        await _reset_db()
        try:
            await plan_store.set_plan(
                user_email="u@x", conversation_id="c1",
                items=[
                    {"id": "x", "title": "a"},
                    {"id": "x", "title": "b"},
                ],
            )
            return False
        except plan_store.PlanError as e:
            return "duplicate" in str(e).lower()
    return _check("duplicate ids rejected with PlanError", asyncio.run(go()))


def test_set_plan_rejects_empty_title() -> bool:
    async def go():
        await _reset_db()
        try:
            await plan_store.set_plan(
                user_email="u@x", conversation_id="c1",
                items=[{"title": "   "}],
            )
            return False
        except plan_store.PlanError:
            return True
    return _check("empty/whitespace title rejected", asyncio.run(go()))


def test_set_plan_rejects_over_cap() -> bool:
    async def go():
        await _reset_db()
        try:
            await plan_store.set_plan(
                user_email="u@x", conversation_id="c1",
                items=[{"title": f"step {i}"} for i in range(plan_store.MAX_ITEMS + 1)],
            )
            return False
        except plan_store.PlanError as e:
            return "exceeds" in str(e).lower() or "cap" in str(e).lower()
    return _check(
        f"items beyond MAX_ITEMS={plan_store.MAX_ITEMS} rejected",
        asyncio.run(go()),
    )


def test_set_plan_rejects_long_title() -> bool:
    async def go():
        await _reset_db()
        long_title = "x" * (plan_store.MAX_TITLE_CHARS + 1)
        try:
            await plan_store.set_plan(
                user_email="u@x", conversation_id="c1",
                items=[{"title": long_title}],
            )
            return False
        except plan_store.PlanError:
            return True
    return _check("title exceeding cap rejected", asyncio.run(go()))


def test_set_plan_rejects_unknown_status() -> bool:
    async def go():
        await _reset_db()
        try:
            await plan_store.set_plan(
                user_email="u@x", conversation_id="c1",
                items=[{"title": "x", "status": "bogus"}],
            )
            return False
        except plan_store.PlanError:
            return True
    return _check("invalid status enum value rejected", asyncio.run(go()))


def test_set_plan_default_status_is_pending() -> bool:
    async def go():
        await _reset_db()
        plan = await plan_store.set_plan(
            user_email="u@x", conversation_id="c1",
            items=[{"title": "x"}],
        )
        return plan["items"][0]["status"] == "pending"
    return _check("status defaults to 'pending' when omitted", asyncio.run(go()))


def test_set_plan_phase2_defaults() -> bool:
    """Phase 2 columns must default safely on Phase 1 writes."""
    async def go():
        await _reset_db()
        plan = await plan_store.set_plan(
            user_email="u@x", conversation_id="c1",
            items=[{"title": "x"}],
        )
        return (
            plan["requires_approval"] is False
            and plan["approved_at"] is None
            and plan["approval_mode"] == "each"
        )
    return _check(
        "Phase 2 fields default to (False, None, 'each')",
        asyncio.run(go()),
    )


# ---------------------------------------------------------------------------
# get_plan + update_item
# ---------------------------------------------------------------------------

def test_get_plan_returns_none_for_missing() -> bool:
    async def go():
        await _reset_db()
        return (await plan_store.get_plan("never-created")) is None
    return _check("get_plan returns None when no row", asyncio.run(go()))


def test_update_item_flips_status() -> bool:
    async def go():
        await _reset_db()
        await plan_store.set_plan(
            user_email="u@x", conversation_id="c1",
            items=[{"title": "step a"}, {"title": "step b"}],
        )
        plan = await plan_store.update_item(
            user_email="u@x", conversation_id="c1",
            item_id="p-1", status="in_progress",
        )
        statuses = {it["id"]: it["status"] for it in plan["items"]}
        return statuses == {"p-1": "in_progress", "p-2": "pending"}
    return _check("update_item flips single status, leaves others intact", asyncio.run(go()))


def test_update_item_404_on_missing_id() -> bool:
    async def go():
        await _reset_db()
        await plan_store.set_plan(
            user_email="u@x", conversation_id="c1",
            items=[{"title": "x"}],
        )
        try:
            await plan_store.update_item(
                user_email="u@x", conversation_id="c1",
                item_id="bogus", status="done",
            )
            return False
        except plan_store.PlanError as e:
            return "no plan item" in str(e).lower()
    return _check("update_item raises PlanError on missing id", asyncio.run(go()))


def test_update_item_404_when_no_plan() -> bool:
    async def go():
        await _reset_db()
        try:
            await plan_store.update_item(
                user_email="u@x", conversation_id="c1",
                item_id="p-1", status="done",
            )
            return False
        except plan_store.PlanError as e:
            return "no plan" in str(e).lower()
    return _check("update_item raises PlanError when plan absent", asyncio.run(go()))


def test_update_item_notes_independent_of_status() -> bool:
    async def go():
        await _reset_db()
        await plan_store.set_plan(
            user_email="u@x", conversation_id="c1",
            items=[{"title": "x"}],
        )
        plan = await plan_store.update_item(
            user_email="u@x", conversation_id="c1",
            item_id="p-1", notes="found the relevant paper",
        )
        item = plan["items"][0]
        return item["status"] == "pending" and item["notes"] == "found the relevant paper"
    return _check("update_item updates notes without touching status", asyncio.run(go()))


# ---------------------------------------------------------------------------
# set_plan resets approval state
# ---------------------------------------------------------------------------

def test_set_plan_resets_approval_state() -> bool:
    """Phase 2 invariant: replacing a plan must clear any prior approval.
    Otherwise a stale approval would carry across plan revisions."""
    async def go():
        await _reset_db()
        # Set initial plan, then directly poke approved_at to simulate
        # a Phase 2 approve.
        await plan_store.set_plan(
            user_email="u@x", conversation_id="c1",
            items=[{"title": "x"}],
        )
        db = await chat_store.get_db()
        await db.execute(
            "UPDATE conversation_plans SET approved_at = ?, approval_mode = ? "
            "WHERE conversation_id = ?",
            ("2026-05-28T...", "auto", "c1"),
        )
        await db.commit()
        # Replace the plan.
        plan = await plan_store.set_plan(
            user_email="u@x", conversation_id="c1",
            items=[{"title": "y"}],
        )
        return plan["approved_at"] is None and plan["approval_mode"] == "each"
    return _check(
        "set_plan resets prior approval state (Phase 2 invariant)",
        asyncio.run(go()),
    )


# ---------------------------------------------------------------------------
# clear_plan
# ---------------------------------------------------------------------------

def test_clear_plan_idempotent() -> bool:
    async def go():
        await _reset_db()
        await plan_store.set_plan(
            user_email="u@x", conversation_id="c1",
            items=[{"title": "x"}],
        )
        first = await plan_store.clear_plan(conversation_id="c1")
        second = await plan_store.clear_plan(conversation_id="c1")
        return first is True and second is False
    return _check("clear_plan: True on hit, False on second call", asyncio.run(go()))


# ---------------------------------------------------------------------------
# build_plan_block
# ---------------------------------------------------------------------------

def test_build_plan_block_none_inputs() -> bool:
    return _check(
        "build_plan_block returns None for missing/empty",
        plan_store.build_plan_block(None) is None
        and plan_store.build_plan_block({"items": []}) is None,
    )


def test_build_plan_block_renders_items() -> bool:
    plan = {
        "items": [
            {"id": "p-1", "title": "Search arxiv", "status": "in_progress", "notes": None},
            {"id": "p-2", "title": "Read top 3", "status": "pending", "notes": "focus on MoS2"},
        ],
    }
    block = plan_store.build_plan_block(plan)
    if block is None:
        return _check("build_plan_block renders items", False, "got None")
    ok = (
        "=== CURRENT PLAN ===" in block
        and "[in_progress] p-1: Search arxiv" in block
        and "[pending] p-2: Read top 3" in block
        and "notes: focus on MoS2" in block
        and "=== END ===" in block
    )
    return _check("build_plan_block renders items + statuses + notes", ok)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

TESTS = [
    test_set_plan_auto_assigns_ids,
    test_set_plan_preserves_user_ids,
    test_set_plan_rejects_duplicate_ids,
    test_set_plan_rejects_empty_title,
    test_set_plan_rejects_over_cap,
    test_set_plan_rejects_long_title,
    test_set_plan_rejects_unknown_status,
    test_set_plan_default_status_is_pending,
    test_set_plan_phase2_defaults,
    test_get_plan_returns_none_for_missing,
    test_update_item_flips_status,
    test_update_item_404_on_missing_id,
    test_update_item_404_when_no_plan,
    test_update_item_notes_independent_of_status,
    test_set_plan_resets_approval_state,
    test_clear_plan_idempotent,
    test_build_plan_block_none_inputs,
    test_build_plan_block_renders_items,
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
    # Close aiosqlite cleanly so the process exits without lingering
    # (lesson from 2026-05-28 zombie cleanup).
    try:
        asyncio.run(chat_store.close_db())
    except Exception:
        pass
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
