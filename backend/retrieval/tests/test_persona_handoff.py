"""
Tests for persona-switch awareness (chat 14ded1f1, 2026-06-09).

When a conversation switches persona mid-thread (delegate_to_persona),
the receiving persona used to deny the switch and claim it had no memory
of the conversation, because the history carried no per-persona
attribution and no handoff marker. These tests cover the two pieces of
the fix:

  1. personas.persona_handoff_note — builds the marker (or None).
  2. chat_context.assemble_context — injects that marker into the system
     message when stored history has turns from a different persona than
     the one now answering.

Run inside the retrieval container:
    docker exec munin-retrieval python /app/tests/test_persona_handoff.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import shutil
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Point chat_store at a private DB before importing anything that
# triggers schema creation (chat_context imports chat_store).
_TMPDIR = tempfile.mkdtemp(prefix="munin-test-persona-handoff-")
os.environ["CHATS_DB_PATH"] = os.path.join(_TMPDIR, "chats.db")

import personas  # noqa: E402
from personas import persona_handoff_note  # noqa: E402
import chat_context  # noqa: E402
import chat_store  # noqa: E402


def _check(name: str, ok: bool, detail: str = "") -> bool:
    label = "PASS" if ok else "FAIL"
    print(f"[{label}] {name}{(' - ' + detail) if detail and not ok else ''}")
    return ok


# ---------------------------------------------------------------------------
# persona_handoff_note
# ---------------------------------------------------------------------------

def test_handoff_note_live_delegation() -> bool:
    note = persona_handoff_note("code", ["chat"], reason="long debugging session")
    ok = (
        note is not None
        and "handoff" in note.lower()
        and "long debugging session" in note
        and "taken over" in note.lower()
        # must forbid the two confabulations from the bug report
        and "no memory" in note.lower()
        and "no switch" in note.lower()
    )
    return _check("handoff note (live delegation) names reason + forbids denial",
                  ok, repr(note))


def test_handoff_note_replayed_history() -> bool:
    note = persona_handoff_note("code", ["chat"])  # no reason -> later turn
    ok = (
        note is not None
        and "earlier turns" in note.lower()
        and "taken over" not in note.lower()  # not the live-delegation phrasing
    )
    return _check("handoff note (replayed history) uses 'earlier turns' phrasing",
                  ok, repr(note))


def test_handoff_note_none_when_single_persona() -> bool:
    same = persona_handoff_note("code", ["code", "code"])
    nulls = persona_handoff_note("code", [None, None])
    empty = persona_handoff_note("code", [])
    ok = same is None and nulls is None and empty is None
    return _check("no note when history is same-persona / null / empty",
                  ok, f"same={same!r} nulls={nulls!r} empty={empty!r}")


def test_handoff_note_dedups_and_filters() -> bool:
    note = persona_handoff_note("code", ["chat", None, "chat", "research"])
    # 'chat' should appear once; 'research' present; 'code' (active) absent
    ok = (
        note is not None
        and note.lower().count(personas.persona_display_name("chat").lower()) == 1
        and personas.persona_display_name("research") in note
    )
    return _check("handoff note dedups priors and excludes the active persona",
                  ok, repr(note))


# ---------------------------------------------------------------------------
# assemble_context injection
# ---------------------------------------------------------------------------

def _conv(messages: list[dict], persona: str) -> dict:
    return {"id": "conv-test", "persona": persona, "summary": None,
            "messages": messages}


def test_assemble_context_injects_marker_on_mixed_persona() -> bool:
    async def go() -> bool:
        conv = _conv(
            [
                {"role": "user", "content": "what would you do to code?",
                 "persona": "chat"},
                {"role": "assistant", "content": "here is how I'd approach it",
                 "persona": "chat"},
            ],
            persona="code",
        )
        msgs, _ = await chat_context.assemble_context(
            conv, {"role": "user", "content": "ok do it"},
            system_prompt="SYSTEM", ephemeral=True,
        )
        sys_msg = msgs[0]
        ok = sys_msg["role"] == "system" and "[persona handoff]" in sys_msg["content"]
        return _check("assemble_context injects handoff marker for mixed-persona history",
                      ok, sys_msg["content"][-200:])
    return asyncio.run(go())


def test_assemble_context_no_marker_single_persona() -> bool:
    async def go() -> bool:
        # All history authored by the active persona -> no marker. Also
        # covers the legacy case if these were NULL.
        conv = _conv(
            [
                {"role": "user", "content": "hi", "persona": "code"},
                {"role": "assistant", "content": "hello", "persona": "code"},
            ],
            persona="code",
        )
        msgs, _ = await chat_context.assemble_context(
            conv, {"role": "user", "content": "next"},
            system_prompt="SYSTEM", ephemeral=True,
        )
        ok = "[persona handoff]" not in msgs[0]["content"]
        return _check("assemble_context injects no marker for single-persona history",
                      ok, msgs[0]["content"][-120:])
    return asyncio.run(go())


def test_assemble_context_no_marker_legacy_null_persona() -> bool:
    async def go() -> bool:
        conv = _conv(
            [
                {"role": "user", "content": "hi", "persona": None},
                {"role": "assistant", "content": "hello", "persona": None},
            ],
            persona="code",
        )
        msgs, _ = await chat_context.assemble_context(
            conv, {"role": "user", "content": "next"},
            system_prompt="SYSTEM", ephemeral=True,
        )
        ok = "[persona handoff]" not in msgs[0]["content"]
        return _check("assemble_context: legacy NULL-persona history gets no marker",
                      ok, msgs[0]["content"][-120:])
    return asyncio.run(go())


# ---------------------------------------------------------------------------
# Persistence: per-message persona survives save -> load
# ---------------------------------------------------------------------------

def test_persona_persists_per_message() -> bool:
    async def go() -> bool:
        conv = await chat_store.create_conversation(
            user_email="u@x", persona="chat", title=None,
        )
        cid = conv["id"]
        # A turn that switches mid-thread: user+assistant under chat, then
        # an assistant authored by code after a delegation.
        await chat_store.add_message(cid, "user", "what about code?", persona="chat")
        await chat_store.add_message(cid, "assistant", "here's how", persona="chat")
        await chat_store.add_message(cid, "assistant", "switched: coding now",
                                     persona="code")
        loaded = await chat_store.get_conversation(cid, "u@x")
        seq = [m.get("persona") for m in loaded["messages"]]
        return seq == ["chat", "chat", "code"]
    return _check("per-message persona round-trips through save/load",
                  asyncio.run(go()))


def test_persona_defaults_null() -> bool:
    async def go() -> bool:
        conv = await chat_store.create_conversation(
            user_email="u2@x", persona="chat", title=None,
        )
        cid = conv["id"]
        await chat_store.add_message(cid, "user", "no persona arg")  # omitted
        loaded = await chat_store.get_conversation(cid, "u2@x")
        return loaded["messages"][0].get("persona") is None
    return _check("add_message without persona stores NULL (legacy-compatible)",
                  asyncio.run(go()))


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

TESTS = [
    test_handoff_note_live_delegation,
    test_handoff_note_replayed_history,
    test_handoff_note_none_when_single_persona,
    test_handoff_note_dedups_and_filters,
    test_assemble_context_injects_marker_on_mixed_persona,
    test_assemble_context_no_marker_single_persona,
    test_assemble_context_no_marker_legacy_null_persona,
    test_persona_persists_per_message,
    test_persona_defaults_null,
]


def _cleanup() -> None:
    """Close the chat_store connection and drop the temp DB.

    Without the close this file PASSED every test and then hung forever in
    `threading._shutdown`: `chat_store.get_db()` caches one `aiosqlite`
    connection, aiosqlite services it from a NON-daemon worker thread, and
    the interpreter will not exit while that thread is alive. Nothing
    printed after "9 passed, 0 failed", so it read as a test that never
    finished rather than one that had already succeeded, and under a CI
    timeout it is indistinguishable from a real failure.
    """
    try:
        asyncio.run(chat_store.close_db())
    except Exception:
        traceback.print_exc()
    shutil.rmtree(_TMPDIR, ignore_errors=True)


# pytest calls this after the module's tests; `main()` calls _cleanup itself
# for the standalone path. Both are needed and both are safe to run twice
# (close_db no-ops on a closed connection, rmtree ignores errors). An
# atexit hook would NOT work: CPython joins non-daemon threads before
# running atexit callbacks, so the hook is reached only after the hang it
# was meant to prevent. Named for pytest's xunit style so this file still
# imports without pytest installed.
def teardown_module(module=None) -> None:  # noqa: ARG001
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
