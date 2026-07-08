"""
Regression test for the duplicate-assistant-message bug observed in chat
9dd753e5 on 2026-05-29: a clarification turn produced two assistant rows
in the database, 12 ms apart, both carrying the same `ask_clarification`
tool_call id. The second row was the save-always finally writing over
the explicit persist that the clarification path had already done.

Root cause: the clarification intercept in chat_service.py (around line
2287) called chat_store.add_message but never set
`assistant_persisted = True`. The finally block at line 2756 then saw
the flag still False and re-persisted via the save-always shield,
appending a spurious "stream interrupted: stream interrupted" marker
because `had_stream_error` was False so the `or "stream interrupted"`
default kicked in inside `apply_stream_error_marker`.

Fix: set `assistant_persisted = True` immediately after the
clarification-path add_message call (now at line 2299).

Runs in-process inside the retrieval container:

    docker exec munin-retrieval python /app/tests/test_clarification_persistence.py

Exit 0 = pass, non-zero = fail.
"""

from __future__ import annotations

import asyncio
import json
import sys
import traceback
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, patch

sys.path.insert(0, "/app")

import chat_service  # noqa: E402
from chat_service import stream_chat_completion, _StreamAccumulator  # noqa: E402


def _check(name: str, ok: bool, detail: str = "") -> bool:
    label = "PASS" if ok else "FAIL"
    print(f"[{label}] {name}{(' --- ' + detail) if detail and not ok else ''}")
    return ok


def _make_stub_persona() -> dict:
    return {
        "id": "chat",
        "name": "Test Chat",
        "params": {"system": "You are a test assistant."},
        "tools": ["ask_clarification"],
    }


def _stub_conversation(conv_id: str = "test-conv-clarification") -> dict:
    return {
        "id": conv_id,
        "user_email": "test@example.com",
        "title": "Existing chat",
        "persona": "chat",
        "default_tags": None,
        "messages": [],
    }


CLARIFICATION_ARGS: dict = {
    "what_i_understood": (
        "You want a checklist or plan for a festival, but the type "
        "and your role are unclear."
    ),
    "questions": [
        {
            "id": "q1",
            "text": "What type of festival?",
            "options": ["Music festival", "Food festival", "Art festival"],
            "allow_custom": True,
        },
        {
            "id": "q2",
            "text": "Attending or organising?",
            "options": ["Attending", "Organising"],
            "allow_custom": False,
        },
    ],
}


async def _fake_stream_ask_clarification(
    *args: Any, **kwargs: Any
) -> AsyncIterator[tuple]:
    """
    Model emits a valid ask_clarification tool_call on the first (only)
    streaming turn. Mirrors the shape that triggered chat 9dd753e5.
    """
    acc = _StreamAccumulator()
    acc.thinking_parts.append("Festival is too vague; need to clarify.")
    acc.tool_calls[0] = {
        "id": "tc-clar-1",
        "name": "ask_clarification",
        "arguments_raw": json.dumps(CLARIFICATION_ARGS),
    }
    acc.finish_reason = "tool_calls"
    yield (
        "thinking",
        {"content": "Festival is too vague; need to clarify."},
        acc,
    )
    yield (
        "tool_call",
        {
            "id": "tc-clar-1",
            "name": "ask_clarification",
            "arguments": CLARIFICATION_ARGS,
        },
        acc,
    )


async def _fake_run_tool_calls_unused(*args: Any, **kwargs: Any) -> list[dict]:
    raise AssertionError(
        "_run_tool_calls should not run on the clarification short-circuit"
    )


def _drive_stream_chat(capture: dict) -> list[dict]:
    capture["calls"] = []

    async def _capture_add_message(**kwargs: Any) -> None:
        capture["calls"].append(kwargs)

    events: list[dict] = []
    persona = _make_stub_persona()
    conversation = _stub_conversation()

    with (
        patch.object(
            chat_service, "_stream_vllm_once", _fake_stream_ask_clarification
        ),
        patch.object(
            chat_service, "_run_tool_calls", _fake_run_tool_calls_unused
        ),
        patch.object(
            chat_service,
            "_build_full_system_prompt",
            AsyncMock(return_value="test system prompt"),
        ),
        patch.object(
            chat_service.persona_module, "get_persona", lambda pid: persona
        ),
        patch.object(
            chat_service.persona_module,
            "sampling_params",
            lambda p: {"temperature": 0.7},
        ),
        patch.object(
            chat_service.chat_store,
            "get_conversation",
            AsyncMock(return_value=conversation),
        ),
        patch.object(
            chat_service.chat_store,
            "create_conversation",
            AsyncMock(return_value={"id": conversation["id"]}),
        ),
        patch.object(
            chat_service.chat_store,
            "update_conversation",
            AsyncMock(return_value=None),
        ),
        patch.object(
            chat_service.chat_store, "add_message", _capture_add_message
        ),
        patch.object(
            chat_service.chat_context,
            "assemble_context",
            AsyncMock(
                return_value=[
                    {"role": "system", "content": "test system prompt"},
                    {"role": "user", "content": "plan for a festival"},
                ]
            ),
        ),
        patch.object(
            chat_service.chat_context,
            "generate_title",
            AsyncMock(return_value=None),
        ),
        patch.object(
            chat_service.vision,
            "build_tool_result_followup",
            AsyncMock(return_value=None),
        ),
        patch.object(
            chat_service.vision,
            "build_view_attachment_followup",
            lambda **kw: None,
        ),
        patch.object(
            chat_service,
            "audit_artifact_urls_in_content",
            lambda content, tcs: (content, []),
        ),
    ):
        gen = stream_chat_completion(
            user_email="test@example.com",
            persona_id="chat",
            conversation_id=conversation["id"],
            user_message={"content": "plan for a festival"},
            rag_config=None,
            ephemeral=False,
        )

        async def _run() -> None:
            async for ev in gen:
                events.append(ev)

        asyncio.run(_run())

    return events


def test_clarification_persists_assistant_exactly_once() -> bool:
    capture: dict = {}
    try:
        events = _drive_stream_chat(capture)
    except Exception as exc:
        traceback.print_exc()
        return _check(
            "clarification turn persists assistant exactly once",
            False,
            f"driver raised: {exc!r}",
        )

    assistant_calls = [
        c for c in capture["calls"] if c.get("role") == "assistant"
    ]
    if len(assistant_calls) != 1:
        return _check(
            "clarification turn persists assistant exactly once",
            False,
            f"got {len(assistant_calls)} assistant add_message calls "
            f"(expected 1); roles: {[c.get('role') for c in capture['calls']]}",
        )
    return _check("clarification turn persists assistant exactly once", True)


def test_clarification_persisted_message_has_no_stream_marker() -> bool:
    """
    The pre-fix second persist appended `_(stream interrupted: stream
    interrupted)_` to the content because `apply_stream_error_marker`
    fell back to the literal `"stream interrupted"` default. With the
    fix in place, save-always is skipped and the marker must not appear.
    """
    capture: dict = {}
    try:
        _drive_stream_chat(capture)
    except Exception as exc:
        traceback.print_exc()
        return _check(
            "clarification persist has no stream-interrupted marker",
            False,
            f"driver raised: {exc!r}",
        )

    assistant_calls = [
        c for c in capture["calls"] if c.get("role") == "assistant"
    ]
    if not assistant_calls:
        return _check(
            "clarification persist has no stream-interrupted marker",
            False,
            "no assistant add_message call captured",
        )
    # Scan ALL assistant rows; the save-always-second-persist bug
    # writes the marker to a row at index >= 1, not [0].
    for i, call in enumerate(assistant_calls):
        content = call.get("content") or ""
        if "stream interrupted" in content:
            return _check(
                "clarification persist has no stream-interrupted marker",
                False,
                f"marker in persisted content at row {i}: {content!r}",
            )
    return _check(
        "clarification persist has no stream-interrupted marker", True
    )


def test_clarification_persisted_tool_call_is_ask_clarification() -> bool:
    """
    Sanity guard: the one persisted assistant row must carry the
    ask_clarification tool_call so reload reproduces the card.
    """
    capture: dict = {}
    try:
        _drive_stream_chat(capture)
    except Exception as exc:
        traceback.print_exc()
        return _check(
            "clarification persist carries the ask_clarification tool_call",
            False,
            f"driver raised: {exc!r}",
        )

    assistant_calls = [
        c for c in capture["calls"] if c.get("role") == "assistant"
    ]
    if not assistant_calls:
        return _check(
            "clarification persist carries the ask_clarification tool_call",
            False,
            "no assistant add_message call captured",
        )
    tcs = assistant_calls[0].get("tool_calls") or []
    has_clar = any(tc.get("name") == "ask_clarification" for tc in tcs)
    if not has_clar:
        return _check(
            "clarification persist carries the ask_clarification tool_call",
            False,
            f"persisted tool_calls={tcs!r}",
        )
    return _check(
        "clarification persist carries the ask_clarification tool_call", True
    )


def main() -> int:
    tests = [
        test_clarification_persists_assistant_exactly_once,
        test_clarification_persisted_message_has_no_stream_marker,
        test_clarification_persisted_tool_call_is_ask_clarification,
    ]
    results = [t() for t in tests]
    failed = sum(1 for r in results if not r)
    print(
        f"\n{len(results) - failed}/{len(results)} passed; {failed} failed."
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
