"""
Tests for the plan-mode MCP dispatchers (P2 #24 Phase 1).

Verifies that set_plan + update_plan_item:
  - read user_email / conversation_id from ContextVars
  - emit plan_updated SSE via current_sse_emitter
  - return a compact tool_result the model can reason from
  - degrade safely on validation error (PlanError → {"error": ...})
  - degrade safely on missing context (no email / no conv id)

Synthetic-harness style — no chat_service, no FastAPI, no streaming
generator. The dispatcher just needs the ContextVars and a captured
emitter callable.
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

_TMPDIR = tempfile.mkdtemp(prefix="munin-test-plandisp-")
os.environ["CHATS_DB_PATH"] = os.path.join(_TMPDIR, "chats.db")

import chat_store  # noqa: E402
from mcp import executor  # noqa: F401, E402   triggers dispatcher registration
from mcp._dispatch import get_dispatcher  # noqa: E402
from mcp.context import (  # noqa: E402
    current_conversation_id,
    current_sse_emitter,
    current_user_email,
)


def _check(name: str, ok: bool, detail: str = "") -> bool:
    label = "PASS" if ok else "FAIL"
    print(f"[{label}] {name}{(' - ' + detail) if detail and not ok else ''}")
    return ok


async def _setup_fixture():
    """Reset the DB and ensure conv 'c1' exists (FK target)."""
    db = await chat_store.get_db()
    await db.execute("DELETE FROM conversation_plans")
    await db.execute("DELETE FROM conversations WHERE id = 'c1'")
    now = chat_store._iso_now()
    await db.execute(
        "INSERT INTO conversations (id, user_email, title, persona, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        ("c1", "u@x", "fixture", "chat", now, now),
    )
    await db.commit()


def _bind_context(user: str = "u@x", conv: str = "c1"):
    """Set ContextVars; returns the reset tokens so the caller can
    restore. Without restoration the ContextVars leak across tests."""
    return (
        current_user_email.set(user),
        current_conversation_id.set(conv),
    )


def _reset_context(tokens):
    current_user_email.reset(tokens[0])
    current_conversation_id.reset(tokens[1])


# ---------------------------------------------------------------------------
# set_plan dispatcher
# ---------------------------------------------------------------------------

def test_set_plan_dispatcher_persists_and_emits() -> bool:
    captured: list[tuple[str, dict]] = []

    def emitter(event: str, data: dict) -> None:
        captured.append((event, data))

    async def go():
        await _setup_fixture()
        tokens = _bind_context()
        em_token = current_sse_emitter.set(emitter)
        try:
            fn = get_dispatcher("set_plan")
            result = await fn({
                "items": [
                    {"title": "Search arxiv"},
                    {"title": "Read top 3 hits"},
                ],
            })
        finally:
            current_sse_emitter.reset(em_token)
            _reset_context(tokens)
        if not (result.get("ok") and result.get("item_count") == 2):
            return False, f"result shape: {result!r}"
        if result.get("ids") != ["p-1", "p-2"]:
            return False, f"ids: {result.get('ids')!r}"
        if len(captured) != 1 or captured[0][0] != "plan_updated":
            return False, f"emitter saw {captured!r}"
        return True, ""

    ok, detail = asyncio.run(go())
    return _check(
        "set_plan dispatcher persists + emits plan_updated", ok, detail,
    )


def test_set_plan_dispatcher_no_context_returns_error() -> bool:
    async def go():
        # Don't bind ContextVars — simulate an ephemeral or
        # unauthenticated turn. Dispatcher must return an error
        # rather than crashing.
        fn = get_dispatcher("set_plan")
        result = await fn({"items": [{"title": "x"}]})
        return "error" in result

    return _check(
        "set_plan returns {'error': ...} when no user_email/conv_id bound",
        asyncio.run(go()),
    )


def test_set_plan_dispatcher_validation_error_returned_not_raised() -> bool:
    captured: list[tuple[str, dict]] = []

    def emitter(event: str, data: dict) -> None:
        captured.append((event, data))

    async def go():
        await _setup_fixture()
        tokens = _bind_context()
        em_token = current_sse_emitter.set(emitter)
        try:
            fn = get_dispatcher("set_plan")
            result = await fn({"items": [{"title": ""}]})  # empty title -> PlanError
        finally:
            current_sse_emitter.reset(em_token)
            _reset_context(tokens)
        return "error" in result and not captured  # no emit on failure

    return _check(
        "set_plan PlanError returns {'error': ...} and skips SSE",
        asyncio.run(go()),
    )


# ---------------------------------------------------------------------------
# update_plan_item dispatcher
# ---------------------------------------------------------------------------

def test_update_plan_item_dispatcher_round_trip() -> bool:
    captured: list[tuple[str, dict]] = []

    def emitter(event: str, data: dict) -> None:
        captured.append((event, data))

    async def go():
        await _setup_fixture()
        tokens = _bind_context()
        em_token = current_sse_emitter.set(emitter)
        try:
            # Set the plan first via the dispatcher path so the
            # ContextVars are correctly carried.
            await get_dispatcher("set_plan")({
                "items": [{"title": "a"}, {"title": "b"}],
            })
            captured.clear()  # only inspect the update_plan_item emit
            result = await get_dispatcher("update_plan_item")({
                "id": "p-1", "status": "in_progress",
            })
        finally:
            current_sse_emitter.reset(em_token)
            _reset_context(tokens)
        if not result.get("ok"):
            return False, f"result: {result!r}"
        item = result.get("item")
        if not item or item["id"] != "p-1" or item["status"] != "in_progress":
            return False, f"item: {item!r}"
        if len(captured) != 1 or captured[0][0] != "plan_updated":
            return False, f"emit: {captured!r}"
        return True, ""

    ok, detail = asyncio.run(go())
    return _check(
        "update_plan_item dispatcher flips status + emits", ok, detail,
    )


def test_update_plan_item_unknown_id_returns_error() -> bool:
    async def go():
        await _setup_fixture()
        tokens = _bind_context()
        try:
            await get_dispatcher("set_plan")({"items": [{"title": "x"}]})
            result = await get_dispatcher("update_plan_item")({
                "id": "bogus", "status": "done",
            })
        finally:
            _reset_context(tokens)
        return "error" in result and "bogus" in result["error"]

    return _check(
        "update_plan_item: unknown id returns {'error': ...} naming the id",
        asyncio.run(go()),
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

TESTS = [
    test_set_plan_dispatcher_persists_and_emits,
    test_set_plan_dispatcher_no_context_returns_error,
    test_set_plan_dispatcher_validation_error_returned_not_raised,
    test_update_plan_item_dispatcher_round_trip,
    test_update_plan_item_unknown_id_returns_error,
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
    try:
        asyncio.run(chat_store.close_db())
    except Exception:
        pass
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
