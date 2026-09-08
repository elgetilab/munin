"""
Tests for _build_full_system_prompt parallelisation (P2 #21).

Pins three properties of the refactor:

  1. PARALLELISM     — the three SQLite fetches run concurrently
                       (test deadlocks if they don't).
  2. EXCEPTION ISO   — one failing fetch only drops its own block;
                       the other two still appear in the prompt.
  3. BLOCK ORDER     — final prompt stacks top-to-bottom as
                       project → artifact → memory → profile → persona
                       → ambient → agent_hint → capabilities.

Run inside the retrieval container:
    docker exec munin-retrieval python /app/tests/test_build_full_system_prompt.py
Or locally:
    python backend/retrieval/tests/test_build_full_system_prompt.py
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Must be set BEFORE chat_service is imported: it pulls in chat_store, which
# reads this env var once at module import time. Without it chat_store falls
# back to its default of /data/chats.db, so importing this test file opened
# the PRODUCTION chat database and ran init_db() (CREATE TABLE IF NOT EXISTS,
# the migration block, PRAGMA journal_mode=WAL) against it. Harmless in
# practice but it has no business touching live data, and it is the reason
# this file held an aiosqlite connection open at all.
_DB_DIR = tempfile.mkdtemp(prefix="munin-test-sysprompt-")
os.environ["CHATS_DB_PATH"] = os.path.join(_DB_DIR, "chats.db")

import chat_service as cs  # noqa: E402
import chat_store  # noqa: E402


def _check(name: str, ok: bool, detail: str = "") -> bool:
    label = "PASS" if ok else "FAIL"
    print(f"[{label}] {name}{(' - ' + detail) if detail and not ok else ''}")
    return ok


def _install_stubs(
    *,
    profile_coro,
    memory_coro,
    artifact_coro,
    profile_builder=lambda v: None,
    memory_builder=lambda v: None,
    artifact_builder=lambda v: None,
    persona_text: str = "PERSONA",
    capabilities_text=None,
    agent_summary: str = "",
):
    """Patch every module that _build_full_system_prompt touches.

    Returns a list of (module, attr, original) triples so the caller
    can restore state after the test (avoids polluting subsequent
    tests in the same process)."""
    saved: list[tuple[object, str, object]] = []

    def patch(mod, attr, new):
        saved.append((mod, attr, getattr(mod, attr)))
        setattr(mod, attr, new)

    patch(cs.user_profile_store, "get_profile", profile_coro)
    patch(cs.user_profile_store, "build_profile_block", profile_builder)
    patch(cs.memory_store, "recall_all", memory_coro)
    patch(cs.memory_store, "build_memory_block", memory_builder)
    patch(cs.artifact_store, "list_artifacts", artifact_coro)
    patch(cs.artifact_store, "build_artifact_summary_block", artifact_builder)
    patch(cs.persona_module, "build_system_prompt", lambda persona: persona_text)
    patch(cs.capabilities_module, "build_capabilities_block", lambda: capabilities_text)
    patch(cs.agents_pkg, "agent_summaries_for_prompt", lambda: agent_summary)
    return saved


def _restore(saved):
    for mod, attr, original in saved:
        setattr(mod, attr, original)


# ---------------------------------------------------------------------------
# 1. Parallelism — three fetches are in flight before any completes.
# ---------------------------------------------------------------------------

def test_fetches_run_in_parallel() -> bool:
    """If the refactor regresses to sequential awaits, the second
    fake-fetch never starts because the first is blocked on `proceed`,
    and the asyncio.wait_for on `started[1]` times out."""
    started = [asyncio.Event() for _ in range(3)]
    proceed = asyncio.Event()

    async def fake_profile(email):
        started[0].set()
        await proceed.wait()
        return {}

    async def fake_memory(email):
        started[1].set()
        await proceed.wait()
        return []

    async def fake_artifacts(**kwargs):
        started[2].set()
        await proceed.wait()
        return []

    async def go() -> bool:
        saved = _install_stubs(
            profile_coro=fake_profile,
            memory_coro=fake_memory,
            artifact_coro=fake_artifacts,
        )
        try:
            task = asyncio.create_task(cs._build_full_system_prompt(
                persona={"id": "chat"},
                user_email="u@x",
                conversation_id="c1",
                ephemeral=False,
                project=None,
            ))
            try:
                # All three must reach their start barrier before any
                # has finished. With sequential awaits, started[1] will
                # never fire and wait_for raises TimeoutError.
                for i, ev in enumerate(started):
                    await asyncio.wait_for(ev.wait(), timeout=2.0)
                proceed.set()
                result = await task
                return "PERSONA" in result
            except asyncio.TimeoutError:
                task.cancel()
                try:
                    await task
                except Exception:
                    pass
                return False
        finally:
            _restore(saved)

    return _check(
        "all three SQLite fetches start before any completes",
        asyncio.run(go()),
    )


# ---------------------------------------------------------------------------
# 2. Exception isolation — one failing fetch does not skip the others.
# ---------------------------------------------------------------------------

def test_failing_fetch_does_not_drop_other_blocks() -> bool:
    async def boom_profile(email):
        raise RuntimeError("db went away")

    async def ok_memory(email):
        return [{"key": "m"}]

    async def ok_artifacts(**kwargs):
        return [{"id": "a"}]

    async def go() -> bool:
        saved = _install_stubs(
            profile_coro=boom_profile,
            memory_coro=ok_memory,
            artifact_coro=ok_artifacts,
            memory_builder=lambda _: "===MEM===",
            artifact_builder=lambda _: "===ART===",
            profile_builder=lambda _: "===PROF===",
        )
        try:
            result = await cs._build_full_system_prompt(
                persona={"id": "chat"},
                user_email="u@x",
                conversation_id="c1",
                ephemeral=False,
                project=None,
            )
            return (
                "===MEM===" in result
                and "===ART===" in result
                and "===PROF===" not in result
                and "PERSONA" in result
            )
        finally:
            _restore(saved)

    return _check(
        "profile-fetch failure leaves memory + artifact blocks intact",
        asyncio.run(go()),
    )


# ---------------------------------------------------------------------------
# 3. Block ordering — top-to-bottom stack matches docstring.
# ---------------------------------------------------------------------------

def test_block_order_matches_contract() -> bool:
    async def fake_profile(email):
        return {}

    async def fake_memory(email):
        return []

    async def fake_artifacts(**kwargs):
        return []

    async def go() -> bool:
        saved = _install_stubs(
            profile_coro=fake_profile,
            memory_coro=fake_memory,
            artifact_coro=fake_artifacts,
            profile_builder=lambda _: "PROFILE_BLOCK",
            memory_builder=lambda _: "MEMORY_BLOCK",
            artifact_builder=lambda _: "ARTIFACT_BLOCK",
            persona_text="PERSONA_BLOCK",
            capabilities_text="CAPABILITIES_BLOCK",
            agent_summary="AGENT_HINT",
        )
        # project_store.build_project_prompt_block is sync — patch directly.
        saved.append((
            cs.project_store, "build_project_prompt_block",
            cs.project_store.build_project_prompt_block,
        ))
        cs.project_store.build_project_prompt_block = lambda project: "PROJECT_BLOCK"
        try:
            result = await cs._build_full_system_prompt(
                persona={"id": "chat"},
                user_email="u@x",
                conversation_id="c1",
                ephemeral=False,
                project={"id": "p1"},
            )
            # Find the position of each marker; their relative order
            # must match the docstring contract.
            order = [
                "PROJECT_BLOCK",
                "ARTIFACT_BLOCK",
                "MEMORY_BLOCK",
                "PROFILE_BLOCK",
                "PERSONA_BLOCK",
                # ambient ("Current date:") sits here — checked by phrase
                "AGENT_HINT",
                "CAPABILITIES_BLOCK",
            ]
            positions = [result.find(marker) for marker in order]
            if -1 in positions:
                return False
            ascending = all(a < b for a, b in zip(positions, positions[1:]))
            # Spot-check the ambient block falls between PERSONA and AGENT_HINT.
            ambient_pos = result.find("Current date:")
            if not (
                positions[order.index("PERSONA_BLOCK")] < ambient_pos
                < positions[order.index("AGENT_HINT")]
            ):
                return False
            return ascending
        finally:
            _restore(saved)

    return _check(
        "block order: project > artifact > memory > profile > persona > ambient > agent_hint > capabilities",
        asyncio.run(go()),
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

TESTS = [
    test_fetches_run_in_parallel,
    test_failing_fetch_does_not_drop_other_blocks,
    test_block_order_matches_contract,
]


def _cleanup() -> None:
    """Close the chat_store connection and drop the throwaway DB.

    Without the close this file PASSED all 3 tests and then hung forever in
    `threading._shutdown`: aiosqlite services its connection from a
    NON-daemon worker thread and CPython will not finalize while one is
    alive. See tests/README.md, "Two ways a test lies about itself".
    """
    try:
        asyncio.run(chat_store.close_db())
    except Exception:
        traceback.print_exc()
    shutil.rmtree(_DB_DIR, ignore_errors=True)


def teardown_module(module=None) -> None:  # noqa: ARG001
    """pytest's hook; `main()` handles the standalone path. Safe twice."""
    _cleanup()


def main() -> int:
    passed = 0
    failed = 0
    try:
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
    finally:
        _cleanup()


if __name__ == "__main__":
    sys.exit(main())
