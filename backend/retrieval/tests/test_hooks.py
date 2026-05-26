"""
Tests for the hooks framework (P2 #23).

Pins: register decorator, dispatcher semantics (pre short-circuit,
post chain, stop gather), match filters (tool name, user email),
exception isolation, HookContext flow from ContextVars, auto-loader.

Run inside the retrieval container:
    docker exec munin-retrieval python /app/tests/test_hooks.py
Or locally:
    python backend/retrieval/tests/test_hooks.py
"""

from __future__ import annotations

import asyncio
import sys
import traceback
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import hooks  # noqa: E402
from hooks._dispatcher import (  # noqa: E402
    _clear_registry_for_tests,
    _registry_snapshot_for_tests,
)
from mcp.context import (  # noqa: E402
    current_user_email,
    current_conversation_id,
    current_persona,
)


def _check(name: str, ok: bool, detail: str = "") -> bool:
    label = "PASS" if ok else "FAIL"
    print(f"[{label}] {name}{(' - ' + detail) if detail and not ok else ''}")
    return ok


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def test_register_requires_async() -> bool:
    _clear_registry_for_tests()
    try:
        @hooks.register("preToolUse")
        def sync_hook(ctx, name, args):  # not async
            return None
    except TypeError:
        return _check("register: refuses non-async function", True)
    return _check("register: refuses non-async function", False)


def test_register_unknown_kind_rejected() -> bool:
    _clear_registry_for_tests()
    try:
        @hooks.register("badKind")
        async def h(ctx, name, args):
            return None
    except ValueError:
        return _check("register: unknown kind raises ValueError", True)
    return _check("register: unknown kind raises ValueError", False)


def test_register_match_filter_recorded() -> bool:
    _clear_registry_for_tests()

    @hooks.register("preToolUse", match_tools={"run_python"}, match_user=r"^foo@")
    async def h(ctx, name, args):
        return None

    snap = _registry_snapshot_for_tests()
    return _check(
        "register: match filters stored on registration",
        len(snap) == 1
        and snap[0]["match_tools"] == ["run_python"]
        and snap[0]["match_user"] == "^foo@",
    )


# ---------------------------------------------------------------------------
# preToolUse dispatch
# ---------------------------------------------------------------------------

def test_pre_returns_none_passes_through() -> bool:
    _clear_registry_for_tests()
    calls = []

    @hooks.register("preToolUse")
    async def h(ctx, name, args):
        calls.append(name)
        return None

    async def go():
        out = await hooks.dispatch_pre_tool_use("any_tool", {"x": 1})
        return out is None and calls == ["any_tool"]

    return _check("pre: None return passes through (executor runs)", asyncio.run(go()))


def test_pre_first_match_short_circuits() -> bool:
    _clear_registry_for_tests()
    calls = []

    @hooks.register("preToolUse")
    async def first(ctx, name, args):
        calls.append("first")
        return {"synthetic": True}

    @hooks.register("preToolUse")
    async def second(ctx, name, args):
        calls.append("second")  # must NOT run
        return {"synthetic": "from_second"}

    async def go():
        out = await hooks.dispatch_pre_tool_use("run_python", {})
        return out == {"synthetic": True} and calls == ["first"]

    return _check(
        "pre: first non-None short-circuits, second hook skipped",
        asyncio.run(go()),
    )


def test_pre_tool_filter_skips_mismatched() -> bool:
    _clear_registry_for_tests()
    calls = []

    @hooks.register("preToolUse", match_tools={"run_python"})
    async def only_for_python(ctx, name, args):
        calls.append(name)
        return {"blocked": True}

    async def go():
        out1 = await hooks.dispatch_pre_tool_use("web_search", {})
        out2 = await hooks.dispatch_pre_tool_use("run_python", {})
        return out1 is None and out2 == {"blocked": True} and calls == ["run_python"]

    return _check(
        "pre: match_tools skips unrelated tool dispatches",
        asyncio.run(go()),
    )


def test_pre_hook_exception_is_isolated() -> bool:
    _clear_registry_for_tests()

    @hooks.register("preToolUse")
    async def boom(ctx, name, args):
        raise RuntimeError("hook bug")

    @hooks.register("preToolUse")
    async def survivor(ctx, name, args):
        return {"survived": True}

    async def go():
        return await hooks.dispatch_pre_tool_use("any", {}) == {"survived": True}

    return _check(
        "pre: a raising hook is logged + skipped, next hook still runs",
        asyncio.run(go()),
    )


# ---------------------------------------------------------------------------
# postToolUse dispatch
# ---------------------------------------------------------------------------

def test_post_none_keeps_original_result() -> bool:
    _clear_registry_for_tests()

    @hooks.register("postToolUse")
    async def observer(ctx, name, args, result, duration_ms):
        return None  # observe only

    async def go():
        original = {"data": "raw"}
        out = await hooks.dispatch_post_tool_use("t", {}, original, 10)
        return out == original

    return _check("post: None return preserves original result", asyncio.run(go()))


def test_post_chain_composes() -> bool:
    _clear_registry_for_tests()

    @hooks.register("postToolUse")
    async def add_redacted(ctx, name, args, result, duration_ms):
        return {**result, "redacted": True}

    @hooks.register("postToolUse")
    async def add_audited(ctx, name, args, result, duration_ms):
        # Sees the previous hook's replacement.
        return {**result, "audited": True}

    async def go():
        out = await hooks.dispatch_post_tool_use("t", {}, {"data": "x"}, 5)
        return out == {"data": "x", "redacted": True, "audited": True}

    return _check(
        "post: chained hooks compose (each sees prior replacement)",
        asyncio.run(go()),
    )


def test_post_hook_exception_does_not_break_chain() -> bool:
    _clear_registry_for_tests()

    @hooks.register("postToolUse")
    async def boom(ctx, name, args, result, duration_ms):
        raise RuntimeError("post bug")

    @hooks.register("postToolUse")
    async def survivor(ctx, name, args, result, duration_ms):
        return {**result, "survived": True}

    async def go():
        out = await hooks.dispatch_post_tool_use("t", {}, {"x": 1}, 0)
        return out == {"x": 1, "survived": True}

    return _check(
        "post: a raising hook is logged + skipped, chain continues",
        asyncio.run(go()),
    )


# ---------------------------------------------------------------------------
# stop dispatch
# ---------------------------------------------------------------------------

def test_stop_fires_all_in_parallel() -> bool:
    _clear_registry_for_tests()
    seen: list[str] = []

    @hooks.register("stop")
    async def a(ctx, reason):
        seen.append(f"a:{reason}")

    @hooks.register("stop")
    async def b(ctx, reason):
        seen.append(f"b:{reason}")

    async def go():
        await hooks.dispatch_stop("done")
        return sorted(seen) == ["a:done", "b:done"]

    return _check("stop: all matching hooks fire", asyncio.run(go()))


def test_stop_one_raising_does_not_block_others() -> bool:
    _clear_registry_for_tests()
    fired: list[str] = []

    @hooks.register("stop")
    async def boom(ctx, reason):
        raise RuntimeError("oh no")

    @hooks.register("stop")
    async def survivor(ctx, reason):
        fired.append("ok")

    async def go():
        await hooks.dispatch_stop("error")
        return fired == ["ok"]

    return _check(
        "stop: a raising hook is logged after the fact; others still run",
        asyncio.run(go()),
    )


# ---------------------------------------------------------------------------
# HookContext from ContextVars
# ---------------------------------------------------------------------------

def test_context_reads_contextvars() -> bool:
    _clear_registry_for_tests()
    captured: list = []

    @hooks.register("preToolUse")
    async def grab(ctx, name, args):
        captured.append(ctx)
        return None

    async def go():
        current_user_email.set("alice@example.com")
        current_conversation_id.set("conv-42")
        current_persona.set("research")
        await hooks.dispatch_pre_tool_use("any", {})
        return (
            len(captured) == 1
            and captured[0].user_email == "alice@example.com"
            and captured[0].conversation_id == "conv-42"
            and captured[0].persona_id == "research"
        )

    return _check(
        "HookContext: user/conv/persona pulled from ContextVars",
        asyncio.run(go()),
    )


def test_user_filter_skips_non_match() -> bool:
    _clear_registry_for_tests()
    seen: list = []

    @hooks.register("preToolUse", match_user=r"^student@")
    async def student_only(ctx, name, args):
        seen.append(ctx.user_email)
        return {"gated": True}

    async def go():
        current_user_email.set("teacher@example.com")
        out = await hooks.dispatch_pre_tool_use("run_python", {})
        # No match -> no short-circuit, no hook invocation.
        if out is not None or seen:
            return False
        current_user_email.set("student@example.com")
        out2 = await hooks.dispatch_pre_tool_use("run_python", {})
        return out2 == {"gated": True} and seen == ["student@example.com"]

    return _check(
        "match_user: only matching email triggers the hook",
        asyncio.run(go()),
    )


# ---------------------------------------------------------------------------
# Auto-loader
# ---------------------------------------------------------------------------

def test_auto_loader_picks_up_audit_log_demo() -> bool:
    """Imports every hooks/*.py, including the shipped audit_log demo.
    Reset the registry first so this is a clean count."""
    _clear_registry_for_tests()
    n = hooks.load_all()
    snap = _registry_snapshot_for_tests()
    audit_names = [r["name"] for r in snap if "audit" in r["name"].lower()]
    return _check(
        "auto-loader: load_all registers the shipped audit_log hook",
        n >= 1 and len(audit_names) == 1,
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

TESTS = [
    test_register_requires_async,
    test_register_unknown_kind_rejected,
    test_register_match_filter_recorded,
    test_pre_returns_none_passes_through,
    test_pre_first_match_short_circuits,
    test_pre_tool_filter_skips_mismatched,
    test_pre_hook_exception_is_isolated,
    test_post_none_keeps_original_result,
    test_post_chain_composes,
    test_post_hook_exception_does_not_break_chain,
    test_stop_fires_all_in_parallel,
    test_stop_one_raising_does_not_block_others,
    test_context_reads_contextvars,
    test_user_filter_skips_non_match,
    test_auto_loader_picks_up_audit_log_demo,
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
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
