"""
Tests for opportunistic compaction (P2 #22).

Covers:
  - assemble_context returns (messages, compact_info | None) — None
    when no compaction, dict with is_fresh=True/False otherwise
  - existing summary that already covers the dropped range reuses
    instead of re-calling vLLM (is_fresh=False)
  - vLLM-unreachable fallback returns (truncated_messages, None)
  - _maybe_precompact threshold gate, lock, ephemeral skip
  - concurrent precompact calls for the same conv_id only fire once

Follows the project convention (test_disconnect_cleanup.py): mock
chat_store + summarize_messages and exercise the SHAPE of the
pattern rather than driving stream_chat_completion.

Run inside the retrieval container:
    docker exec munin-retrieval python /app/tests/test_compaction.py
Or locally:
    python backend/retrieval/tests/test_compaction.py
"""

from __future__ import annotations

import asyncio
import sys
import traceback
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chat_context  # noqa: E402


def _check(name: str, ok: bool, detail: str = "") -> bool:
    label = "PASS" if ok else "FAIL"
    print(f"[{label}] {name}{(' - ' + detail) if detail and not ok else ''}")
    return ok


# ---------------------------------------------------------------------------
# Module-level monkeypatch helper
# ---------------------------------------------------------------------------

def _patch(module: Any, attr: str, value: Any):
    """Returns (module, attr, original) so the test can restore."""
    original = getattr(module, attr)
    setattr(module, attr, value)
    return module, attr, original


def _restore(saved: list) -> None:
    for mod, attr, original in saved:
        setattr(mod, attr, original)


def _make_msg(idx: int, role: str, content: str) -> dict:
    return {
        "id": f"m{idx}",
        "role": role,
        "content": content,
        "index_in_conversation": idx,
    }


# ---------------------------------------------------------------------------
# assemble_context return shape
# ---------------------------------------------------------------------------

def test_no_compaction_returns_none_info() -> bool:
    """Short conversation that fits in the budget: compact_info=None."""
    conv = {
        "id": "c1",
        "messages": [
            _make_msg(0, "user", "hi"),
            _make_msg(1, "assistant", "hello"),
        ],
        "summary": None,
        "summary_through_index": None,
    }

    async def go():
        messages, info = await chat_context.assemble_context(
            conversation=conv,
            new_message={"role": "user", "content": "next"},
            system_prompt="You are helpful.",
            ephemeral=False,
        )
        return info is None and len(messages) >= 3

    return _check("short conv: compact_info is None", asyncio.run(go()))


def test_compaction_returns_fresh_info() -> bool:
    """Force compaction by stuffing huge messages; assert is_fresh=True
    because no summary existed yet."""
    huge_content = "x " * 50_000  # ~100k chars
    conv = {
        "id": "c1",
        "messages": [_make_msg(i, "user" if i % 2 == 0 else "assistant", huge_content) for i in range(6)],
        "summary": None,
        "summary_through_index": None,
    }

    captured = {}

    async def fake_summarize(conv_id, existing, dropped):
        captured["called"] = True
        captured["existing"] = existing
        captured["dropped_count"] = len(dropped)
        return "Generated summary text"

    async def fake_update(*args, **kwargs):
        captured["persisted"] = True

    saved = [
        _patch(chat_context, "summarize_messages", fake_summarize),
        _patch(chat_context, "update_summary", fake_update),
    ]
    try:
        async def go():
            _, info = await chat_context.assemble_context(
                conversation=conv,
                new_message={"role": "user", "content": "next"},
                system_prompt="sys",
                ephemeral=False,
            )
            return info

        info = asyncio.run(go())
    finally:
        _restore(saved)

    ok = (
        info is not None
        and info["is_fresh"] is True
        and info["summary"] == "Generated summary text"
        and info["dropped_messages"] >= 1
        and captured.get("called") is True
        and captured.get("persisted") is True
    )
    return _check(
        "fresh compaction: vLLM called, summary persisted, is_fresh=True",
        ok, str(info),
    )


def test_reuse_existing_summary_is_not_fresh() -> bool:
    """If summary_through_index already covers everything we'd drop,
    the helper short-circuits (no vLLM call) and is_fresh=False."""
    huge_content = "x " * 50_000
    conv = {
        "id": "c1",
        # Index the messages so the "already_through" filter empties
        # the dropped list — the existing summary already covers them.
        "messages": [_make_msg(i, "user" if i % 2 == 0 else "assistant", huge_content) for i in range(6)],
        "summary": "Cached summary from a prior turn",
        "summary_through_index": 999,  # > any drop index, so dropped filters to []
    }

    vllm_calls = {"n": 0}

    async def fake_summarize(conv_id, existing, dropped):
        vllm_calls["n"] += 1
        # Real summarize_messages already short-circuits when
        # dropped is empty (returns existing). Mirror that here so
        # the test pins both layers.
        if not dropped:
            return existing
        return "should not happen"

    async def fake_update(*args, **kwargs):
        return None

    saved = [
        _patch(chat_context, "summarize_messages", fake_summarize),
        _patch(chat_context, "update_summary", fake_update),
    ]
    try:
        async def go():
            _, info = await chat_context.assemble_context(
                conversation=conv,
                new_message={"role": "user", "content": "next"},
                system_prompt="sys",
                ephemeral=False,
            )
            return info

        info = asyncio.run(go())
    finally:
        _restore(saved)

    ok = (
        info is not None
        and info["is_fresh"] is False
        and info["summary"] == "Cached summary from a prior turn"
    )
    return _check(
        "cached summary covers dropped range: is_fresh=False, no fresh vLLM cost",
        ok, str(info),
    )


def test_vllm_unreachable_returns_none_info() -> bool:
    """If summarize_messages returns None (vLLM down), assemble_context
    falls back to hard truncation and returns compact_info=None — no
    boundary to render."""
    huge_content = "x " * 50_000
    conv = {
        "id": "c1",
        "messages": [_make_msg(i, "user", huge_content) for i in range(6)],
        "summary": None,
        "summary_through_index": None,
    }

    async def fake_summarize(*args, **kwargs):
        return None  # vLLM unreachable

    saved = [_patch(chat_context, "summarize_messages", fake_summarize)]
    try:
        async def go():
            _, info = await chat_context.assemble_context(
                conversation=conv,
                new_message={"role": "user", "content": "next"},
                system_prompt="sys",
                ephemeral=False,
            )
            return info

        info = asyncio.run(go())
    finally:
        _restore(saved)

    return _check(
        "vLLM unreachable: hard-truncation fallback, compact_info=None",
        info is None,
    )


# ---------------------------------------------------------------------------
# _maybe_precompact
# ---------------------------------------------------------------------------

def test_precompact_skips_below_threshold() -> bool:
    """Short conversation never triggers the background compaction."""
    conv = {
        "id": "c1",
        "messages": [_make_msg(0, "user", "hi"), _make_msg(1, "assistant", "hello")],
        "summary": None,
        "summary_through_index": None,
    }
    vllm_calls = {"n": 0}

    async def fake_get_conv(conv_id, email):
        return conv

    async def fake_summarize(*args, **kwargs):
        vllm_calls["n"] += 1
        return "summary"

    async def fake_update(*args, **kwargs):
        return None

    saved = [
        _patch(chat_context, "get_conversation", fake_get_conv),
        _patch(chat_context, "summarize_messages", fake_summarize),
        _patch(chat_context, "update_summary", fake_update),
    ]
    try:
        async def go():
            await chat_context._maybe_precompact("c1", "u@x")

        asyncio.run(go())
    finally:
        _restore(saved)
    chat_context._precompact_inflight.discard("c1")
    return _check(
        "precompact below threshold: zero vLLM calls",
        vllm_calls["n"] == 0,
    )


def test_precompact_fires_above_threshold() -> bool:
    """Heavy history (over 70% of budget) triggers the background
    compaction; summarize_messages is called and update_summary is
    invoked."""
    huge_content = "x " * 50_000
    conv = {
        "id": "c2",
        "messages": [_make_msg(i, "user", huge_content) for i in range(6)],
        "summary": None,
        "summary_through_index": None,
    }
    captured = {"summarize_calls": 0, "update_called": False, "last_through": None}

    async def fake_get_conv(conv_id, email):
        return conv

    async def fake_summarize(conv_id, existing, dropped):
        captured["summarize_calls"] += 1
        return "background summary"

    async def fake_update(conv_id, summary, through):
        captured["update_called"] = True
        captured["last_through"] = through

    saved = [
        _patch(chat_context, "get_conversation", fake_get_conv),
        _patch(chat_context, "summarize_messages", fake_summarize),
        _patch(chat_context, "update_summary", fake_update),
    ]
    try:
        async def go():
            await chat_context._maybe_precompact("c2", "u@x")

        asyncio.run(go())
    finally:
        _restore(saved)
    chat_context._precompact_inflight.discard("c2")
    return _check(
        "precompact above threshold: vLLM called + update_summary persists",
        captured["summarize_calls"] == 1
        and captured["update_called"] is True
        and captured["last_through"] is not None,
        str(captured),
    )


def test_precompact_lock_prevents_double_fire() -> bool:
    """Two concurrent precompact tasks for the same conv_id: only one
    runs. The second exits early on the in-flight guard."""
    huge_content = "x " * 50_000
    conv = {
        "id": "c3",
        "messages": [_make_msg(i, "user", huge_content) for i in range(6)],
        "summary": None,
        "summary_through_index": None,
    }
    captured = {"summarize_calls": 0}
    start_barrier = asyncio.Event()
    proceed = asyncio.Event()

    async def fake_get_conv(conv_id, email):
        # Slow down enough that the second create_task observes the
        # in-flight set.
        await asyncio.sleep(0)
        return conv

    async def fake_summarize(*args, **kwargs):
        captured["summarize_calls"] += 1
        start_barrier.set()
        await proceed.wait()
        return "summary"

    async def fake_update(*args, **kwargs):
        return None

    saved = [
        _patch(chat_context, "get_conversation", fake_get_conv),
        _patch(chat_context, "summarize_messages", fake_summarize),
        _patch(chat_context, "update_summary", fake_update),
    ]
    try:
        async def go():
            t1 = asyncio.create_task(chat_context._maybe_precompact("c3", "u@x"))
            # Wait for t1 to enter the slow summarize step
            await start_barrier.wait()
            # t2 should observe the in-flight set and exit early
            t2 = asyncio.create_task(chat_context._maybe_precompact("c3", "u@x"))
            await t2  # exits immediately
            proceed.set()
            await t1

        asyncio.run(go())
    finally:
        _restore(saved)
    chat_context._precompact_inflight.discard("c3")
    return _check(
        "precompact lock: two concurrent calls for same conv → only one summarize",
        captured["summarize_calls"] == 1,
        f"saw {captured['summarize_calls']} calls",
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

TESTS = [
    test_no_compaction_returns_none_info,
    test_compaction_returns_fresh_info,
    test_reuse_existing_summary_is_not_fresh,
    test_vllm_unreachable_returns_none_info,
    test_precompact_skips_below_threshold,
    test_precompact_fires_above_threshold,
    test_precompact_lock_prevents_double_fire,
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
