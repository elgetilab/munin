"""
Tests for the plan-approval gate (P2 #24 Phase 2).

Covers the synthetic-harness shape:
  - preToolUse: gated tool with no plan -> nudge error
  - preToolUse: gated tool with unapproved plan -> short-circuit
    with awaiting_user_approval + plan_approval_required SSE emit
  - preToolUse: gated tool with approved plan -> None (let it run)
  - preToolUse: non-gated tool -> None
  - preToolUse: ephemeral / no conversation_id -> None
  - preToolUse: persona-level gating via params.plan_approval
  - preToolUse: model-flag gating via plan.requires_approval (when
    the persona doesn't list the tool but the model self-flagged)
  - postToolUse: 'each' mode clears approved_at after one gated
    tool run
  - postToolUse: 'auto' mode preserves approved_at
  - postToolUse: short-circuited result (status=awaiting_user_
    approval) does NOT consume the approval
  - plan_store: mark_approved, clear_approval, replace_items
  - build_plan_block renders the APPROVAL STATUS line in three
    branches (awaiting / each-approved / auto-approved)
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

_TMPDIR = tempfile.mkdtemp(prefix="munin-test-planhook-")
os.environ["CHATS_DB_PATH"] = os.path.join(_TMPDIR, "chats.db")
# Personas dir for the in-memory persona lookup
_PERSONAS_DIR = str(Path(__file__).resolve().parent.parent.parent.parent / "shared" / "personas")
if Path(_PERSONAS_DIR).is_dir():
    os.environ["PERSONAS_DIR"] = _PERSONAS_DIR

import chat_store  # noqa: E402
import personas as persona_module  # noqa: E402
import plan_store  # noqa: E402

# Importing this triggers @register on the gate + consumer hooks.
from hooks import plan_approval as _phook  # noqa: E402, F401
import hooks  # noqa: E402
from mcp.context import (  # noqa: E402
    current_conversation_id,
    current_persona,
    current_sse_emitter,
    current_user_email,
)


def _check(name: str, ok: bool, detail: str = "") -> bool:
    label = "PASS" if ok else "FAIL"
    print(f"[{label}] {name}{(' - ' + detail) if detail and not ok else ''}")
    return ok


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


async def _setup() -> None:
    persona_module.load_personas()
    db = await chat_store.get_db()
    await db.execute("DELETE FROM conversation_plans")
    await db.execute("DELETE FROM conversations WHERE id = 'c1'")
    now = chat_store._iso_now()
    await db.execute(
        "INSERT INTO conversations (id, user_email, title, persona, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        ("c1", "u@x", "fixture", "research", now, now),
    )
    await db.commit()


def _bind_ctx(persona: str = "research"):
    return (
        current_user_email.set("u@x"),
        current_conversation_id.set("c1"),
        current_persona.set(persona),
    )


def _unbind_ctx(tokens):
    current_user_email.reset(tokens[0])
    current_conversation_id.reset(tokens[1])
    current_persona.reset(tokens[2])


# ---------------------------------------------------------------------------
# preToolUse
# ---------------------------------------------------------------------------

def test_pre_no_plan_returns_nudge_error() -> bool:
    async def go():
        await _setup()
        tokens = _bind_ctx("research")
        try:
            result = await _phook.plan_approval_gate.__wrapped__(  # type: ignore[attr-defined]
                hooks.HookContext(user_email="u@x", conversation_id="c1", persona_id="research"),
                "delegate_to_persona", {},
            ) if hasattr(_phook.plan_approval_gate, "__wrapped__") else await hooks.dispatch_pre_tool_use("delegate_to_persona", {})
        finally:
            _unbind_ctx(tokens)
        return isinstance(result, dict) and result.get("error") == "plan_approval_required"
    return _check(
        "gated tool with NO plan returns plan_approval_required nudge",
        asyncio.run(go()),
    )


def test_pre_unapproved_plan_short_circuits_and_emits() -> bool:
    captured: list[tuple[str, dict]] = []

    def emitter(event: str, data: dict) -> None:
        captured.append((event, data))

    async def go():
        await _setup()
        # Set a plan with requires_approval=True
        await plan_store.set_plan(
            user_email="u@x", conversation_id="c1",
            items=[{"title": "step a"}, {"title": "step b"}],
            requires_approval=True,
        )
        tokens = _bind_ctx("research")
        em_token = current_sse_emitter.set(emitter)
        try:
            result = await hooks.dispatch_pre_tool_use(
                "delegate_to_persona", {"persona_id": "code"},
            )
        finally:
            current_sse_emitter.reset(em_token)
            _unbind_ctx(tokens)
        if not isinstance(result, dict):
            return False
        if result.get("status") != "awaiting_user_approval":
            return False
        if not (len(captured) == 1 and captured[0][0] == "plan_approval_required"):
            return False
        return captured[0][1]["tool"] == "delegate_to_persona"
    return _check(
        "gated tool with unapproved plan -> short-circuit + emit",
        asyncio.run(go()),
    )


def test_pre_approved_plan_returns_none() -> bool:
    async def go():
        await _setup()
        await plan_store.set_plan(
            user_email="u@x", conversation_id="c1",
            items=[{"title": "step a"}], requires_approval=True,
        )
        await plan_store.mark_approved(
            conversation_id="c1", approval_mode="each",
        )
        tokens = _bind_ctx("research")
        try:
            result = await hooks.dispatch_pre_tool_use(
                "delegate_to_persona", {},
            )
        finally:
            _unbind_ctx(tokens)
        return result is None
    return _check(
        "gated tool with approved plan -> None (let it run)",
        asyncio.run(go()),
    )


def test_pre_non_gated_tool_returns_none() -> bool:
    async def go():
        await _setup()
        await plan_store.set_plan(
            user_email="u@x", conversation_id="c1",
            items=[{"title": "step a"}], requires_approval=True,
        )
        tokens = _bind_ctx("research")
        try:
            # web_search is NOT in research's plan_approval list
            result = await hooks.dispatch_pre_tool_use("web_search", {})
        finally:
            _unbind_ctx(tokens)
        return result is None
    return _check(
        "non-gated tool on gating persona still runs (preToolUse returns None)",
        asyncio.run(go()),
    )


def test_pre_no_conversation_id_returns_none() -> bool:
    async def go():
        await _setup()
        # Ephemeral: bind only the persona, not the conv id.
        em_tokens = (
            current_user_email.set("u@x"),
            current_persona.set("research"),
        )
        try:
            result = await hooks.dispatch_pre_tool_use("delegate_to_persona", {})
        finally:
            current_user_email.reset(em_tokens[0])
            current_persona.reset(em_tokens[1])
        return result is None
    return _check(
        "no conversation_id (ephemeral) bypasses the gate entirely",
        asyncio.run(go()),
    )


def test_pre_model_flag_gates_even_without_persona_list() -> bool:
    """If the model voluntarily sets requires_approval=True on a
    plan, gated semantics apply even for personas that don't list
    any plan_approval tools."""
    async def go():
        await _setup()
        await plan_store.set_plan(
            user_email="u@x", conversation_id="c1",
            items=[{"title": "x"}], requires_approval=True,
        )
        # Use the chat persona (no plan_approval list); the model
        # flag should still cause gating on web_search.
        tokens = _bind_ctx("chat")
        try:
            result = await hooks.dispatch_pre_tool_use("web_search", {})
        finally:
            _unbind_ctx(tokens)
        return isinstance(result, dict) and result.get("status") == "awaiting_user_approval"
    return _check(
        "model-flag (requires_approval=True) gates even without persona list",
        asyncio.run(go()),
    )


def test_pre_plan_tools_themselves_are_never_gated() -> bool:
    """set_plan + update_plan_item must NEVER block on the gate or
    the model can't escape its own initial set_plan call."""
    async def go():
        await _setup()
        await plan_store.set_plan(
            user_email="u@x", conversation_id="c1",
            items=[{"title": "x"}], requires_approval=True,
        )
        tokens = _bind_ctx("research")
        try:
            r1 = await hooks.dispatch_pre_tool_use("set_plan", {"items": [{"title": "y"}]})
            r2 = await hooks.dispatch_pre_tool_use("update_plan_item", {"id": "p-1"})
        finally:
            _unbind_ctx(tokens)
        return r1 is None and r2 is None
    return _check(
        "set_plan / update_plan_item never gated (would deadlock the model)",
        asyncio.run(go()),
    )


# ---------------------------------------------------------------------------
# postToolUse: 'each' vs 'auto'
# ---------------------------------------------------------------------------

def test_post_each_mode_clears_approved_at() -> bool:
    async def go():
        await _setup()
        await plan_store.set_plan(
            user_email="u@x", conversation_id="c1",
            items=[{"title": "x"}], requires_approval=True,
        )
        await plan_store.mark_approved(conversation_id="c1", approval_mode="each")
        tokens = _bind_ctx("research")
        try:
            await hooks.dispatch_post_tool_use(
                "delegate_to_persona", {}, {"ok": True}, 50,
            )
        finally:
            _unbind_ctx(tokens)
        plan = await plan_store.get_plan("c1")
        return plan is not None and plan["approved_at"] is None
    return _check(
        "'each' mode clears approved_at after one gated tool run",
        asyncio.run(go()),
    )


def test_post_auto_mode_preserves_approved_at() -> bool:
    async def go():
        await _setup()
        await plan_store.set_plan(
            user_email="u@x", conversation_id="c1",
            items=[{"title": "x"}], requires_approval=True,
        )
        await plan_store.mark_approved(conversation_id="c1", approval_mode="auto")
        before = (await plan_store.get_plan("c1"))["approved_at"]
        tokens = _bind_ctx("research")
        try:
            await hooks.dispatch_post_tool_use(
                "delegate_to_persona", {}, {"ok": True}, 50,
            )
        finally:
            _unbind_ctx(tokens)
        after = (await plan_store.get_plan("c1"))["approved_at"]
        return before == after and before is not None
    return _check(
        "'auto' mode preserves approved_at across gated tool calls",
        asyncio.run(go()),
    )


def test_post_short_circuit_does_not_consume_approval() -> bool:
    """If the dispatch result is the awaiting_user_approval
    synthetic shape (the preToolUse gate fired), the postToolUse
    consumer must NOT clear approved_at — there's no approval to
    consume, the executor never ran."""
    async def go():
        await _setup()
        await plan_store.set_plan(
            user_email="u@x", conversation_id="c1",
            items=[{"title": "x"}], requires_approval=True,
        )
        await plan_store.mark_approved(conversation_id="c1", approval_mode="each")
        before = (await plan_store.get_plan("c1"))["approved_at"]
        tokens = _bind_ctx("research")
        try:
            await hooks.dispatch_post_tool_use(
                "delegate_to_persona",
                {},
                {"status": "awaiting_user_approval", "tool": "delegate_to_persona"},
                0,
            )
        finally:
            _unbind_ctx(tokens)
        after = (await plan_store.get_plan("c1"))["approved_at"]
        return before == after  # NOT cleared
    return _check(
        "short-circuited dispatch does not consume approval",
        asyncio.run(go()),
    )


# ---------------------------------------------------------------------------
# plan_store Phase 2 helpers
# ---------------------------------------------------------------------------

def test_mark_approved_invalid_mode() -> bool:
    async def go():
        await _setup()
        await plan_store.set_plan(
            user_email="u@x", conversation_id="c1",
            items=[{"title": "x"}], requires_approval=True,
        )
        try:
            await plan_store.mark_approved(
                conversation_id="c1", approval_mode="bogus",
            )
            return False
        except plan_store.PlanError as e:
            return "approval_mode" in str(e)
    return _check("mark_approved rejects unknown mode", asyncio.run(go()))


def test_replace_items_implicit_approve() -> bool:
    async def go():
        await _setup()
        await plan_store.set_plan(
            user_email="u@x", conversation_id="c1",
            items=[{"title": "original"}], requires_approval=True,
        )
        plan = await plan_store.replace_items(
            user_email="u@x", conversation_id="c1",
            items=[{"title": "edited a"}, {"title": "edited b"}],
            approval_mode="each",
        )
        return (
            plan["approved_at"] is not None
            and len(plan["items"]) == 2
            and plan["items"][0]["title"] == "edited a"
            and plan["requires_approval"] is True  # preserved
        )
    return _check(
        "replace_items edits + implicitly approves + preserves requires_approval",
        asyncio.run(go()),
    )


# ---------------------------------------------------------------------------
# build_plan_block STATUS line
# ---------------------------------------------------------------------------

def test_status_line_awaiting() -> bool:
    plan = {
        "items": [{"id": "p-1", "title": "x", "status": "pending"}],
        "requires_approval": True,
        "approved_at": None,
        "approval_mode": "each",
    }
    block = plan_store.build_plan_block(plan)
    return _check(
        "STATUS line: AWAITING APPROVAL when requires_approval && !approved_at",
        block is not None and "AWAITING APPROVAL" in block and "do NOT retry" in block,
    )


def test_status_line_approved_each() -> bool:
    plan = {
        "items": [{"id": "p-1", "title": "x", "status": "pending"}],
        "requires_approval": True,
        "approved_at": "2026-05-28T...",
        "approval_mode": "each",
    }
    block = plan_store.build_plan_block(plan)
    return _check(
        "STATUS line: APPROVED (single use) on each-mode + approved_at",
        block is not None and "APPROVED (single use)" in block,
    )


def test_status_line_approved_auto() -> bool:
    plan = {
        "items": [{"id": "p-1", "title": "x", "status": "pending"}],
        "requires_approval": True,
        "approved_at": "2026-05-28T...",
        "approval_mode": "auto",
    }
    block = plan_store.build_plan_block(plan)
    return _check(
        "STATUS line: APPROVED (auto-mode) on auto-mode + approved_at",
        block is not None and "APPROVED (auto-mode)" in block,
    )


def test_no_status_line_when_approval_not_required() -> bool:
    plan = {
        "items": [{"id": "p-1", "title": "x", "status": "pending"}],
        "requires_approval": False,
        "approved_at": None,
        "approval_mode": "each",
    }
    block = plan_store.build_plan_block(plan)
    return _check(
        "no APPROVAL STATUS line when requires_approval is False (Phase 1 path)",
        block is not None and "APPROVAL STATUS" not in block,
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

TESTS = [
    test_pre_no_plan_returns_nudge_error,
    test_pre_unapproved_plan_short_circuits_and_emits,
    test_pre_approved_plan_returns_none,
    test_pre_non_gated_tool_returns_none,
    test_pre_no_conversation_id_returns_none,
    test_pre_model_flag_gates_even_without_persona_list,
    test_pre_plan_tools_themselves_are_never_gated,
    test_post_each_mode_clears_approved_at,
    test_post_auto_mode_preserves_approved_at,
    test_post_short_circuit_does_not_consume_approval,
    test_mark_approved_invalid_mode,
    test_replace_items_implicit_approve,
    test_status_line_awaiting,
    test_status_line_approved_each,
    test_status_line_approved_auto,
    test_no_status_line_when_approval_not_required,
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
