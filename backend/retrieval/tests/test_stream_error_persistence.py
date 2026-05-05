"""
Standalone unit tests for partial-state persistence on a vLLM stream
error mid-turn (`stream_chat_completion` in chat_service.py).

Regression guard for the failure pattern observed in chat 676a3238 on
2026-05-05: the user asked the chat persona to "try again with the
research orchestrator", the model emitted invoke_agent and the agent
ran (visible to the user as live SSE events), but on the model's
next streaming call (where it would have summarised the agent
result) vLLM yielded an "Error in input stream" event. Before this
fix, the early `return` at chat_service.py 1503 abandoned the whole
turn and `chat_store.add_message` was never called, so the agent
invocation that the user just watched scroll by vanished from the
saved transcript on reload.

Two layers of coverage:

1. Pure-function tests for `apply_stream_error_marker`. Cover empty
   content, with content (trim trailing whitespace), empty error
   message (degrades to a generic phrase). Pure string transform; no
   I/O, no model call.

2. Behavioural tests that drive `stream_chat_completion` end-to-end
   with `_stream_vllm_once` monkey-patched to a fixture that mimics
   the real bug shape: turn 0 emits thinking + invoke_agent
   tool_call, the executor is stubbed to return a real-looking agent
   result dict, then turn 1 emits a stream `error` event before any
   content. We capture `chat_store.add_message` calls and assert the
   tool_call survives AND the saved content carries the marker.

   The fixtures stand in for "what the model + vLLM produce" — i.e.
   model behaviour. The assertion is that the backend reaction is
   correct: nothing the user saw scroll by vanishes from disk.

Runs in-process inside the retrieval container:

    docker exec munin-retrieval python /app/tests/test_stream_error_persistence.py

Exit 0 = pass, non-zero = fail.
"""

from __future__ import annotations

import asyncio
import sys
import traceback
from typing import Any, AsyncIterator, Optional
from unittest.mock import AsyncMock, patch

sys.path.insert(0, "/app")

import chat_service  # noqa: E402
from chat_service import (  # noqa: E402
    apply_stream_error_marker,
    stream_chat_completion,
    _StreamAccumulator,
)


def _check(name: str, ok: bool, detail: str = "") -> bool:
    label = "PASS" if ok else "FAIL"
    print(f"[{label}] {name}{(' --- ' + detail) if detail and not ok else ''}")
    return ok


# ---------------------------------------------------------------------------
# Pure-function tests for apply_stream_error_marker
# ---------------------------------------------------------------------------


def test_marker_on_empty_content() -> bool:
    out = apply_stream_error_marker("", "Error in input stream")
    return _check(
        "marker on empty content -> marker only",
        out == "_(stream interrupted: Error in input stream)_",
        f"got {out!r}",
    )


def test_marker_on_whitespace_only_content() -> bool:
    out = apply_stream_error_marker("   \n\n  ", "boom")
    return _check(
        "marker on whitespace-only content -> marker only",
        out == "_(stream interrupted: boom)_",
        f"got {out!r}",
    )


def test_marker_appended_with_blank_line() -> bool:
    out = apply_stream_error_marker(
        "I'll call the orchestrator.", "vLLM returned 500: Error in input stream"
    )
    expected = (
        "I'll call the orchestrator.\n\n"
        "_(stream interrupted: vLLM returned 500: Error in input stream)_"
    )
    return _check(
        "marker appended after content with blank-line separator",
        out == expected,
        f"got {out!r}",
    )


def test_marker_strips_trailing_whitespace() -> bool:
    out = apply_stream_error_marker("Calling the agent now.\n\n\n", "oops")
    expected = "Calling the agent now.\n\n_(stream interrupted: oops)_"
    return _check(
        "trailing whitespace on content is collapsed before marker",
        out == expected,
        f"got {out!r}",
    )


def test_marker_with_empty_error_message_degrades_gracefully() -> bool:
    out = apply_stream_error_marker("partial", "")
    expected = "partial\n\n_(stream interrupted: vLLM stream error)_"
    return _check(
        "empty error message -> generic phrase, marker still present",
        out == expected,
        f"got {out!r}",
    )


# ---------------------------------------------------------------------------
# Behavioural tests: drive stream_chat_completion with a faked
# _stream_vllm_once and capture chat_store.add_message
# ---------------------------------------------------------------------------


def _make_stub_persona() -> dict:
    """Minimal persona dict that satisfies stream_chat_completion's accessors."""
    return {
        "id": "chat",
        "name": "Test Chat",
        "params": {"system": "You are a test assistant."},
        # Allowlist contains invoke_agent so the test fixture's tool_call
        # passes the persona's tool gate without triggering a synthetic
        # rejection.
        "tools": ["invoke_agent"],
    }


def _stub_conversation(conv_id: str = "test-conv-1") -> dict:
    return {
        "id": conv_id,
        "user_email": "test@example.com",
        "title": "Existing chat",
        "persona": "chat",
        "default_tags": None,
        "messages": [],
    }


def _drain(events: list[dict], gen) -> Any:
    """Drive an async generator, append each yielded event to `events`,
    return None when exhausted."""

    async def _run() -> None:
        async for ev in gen:
            events.append(ev)

    return asyncio.run(_run())


async def _fake_stream_after_invoke_agent(
    *args: Any, **kwargs: Any
) -> AsyncIterator[tuple]:
    """
    Fixture for the actual bug shape from chat 676a3238.
    Turn 0: model emits a brief thinking trace + the invoke_agent tool call.
    Turn 1: vLLM yields an `error` event before any content (the model was
    about to summarise the agent's result).
    """
    turn = _fake_stream_after_invoke_agent.turn  # type: ignore[attr-defined]
    _fake_stream_after_invoke_agent.turn = turn + 1  # type: ignore[attr-defined]
    if turn == 0:
        acc = _StreamAccumulator()
        acc.thinking_parts.append("I should call the research orchestrator.")
        acc.tool_calls[0] = {
            "id": "tc-invoke-1",
            "name": "invoke_agent",
            "arguments_raw": (
                '{"agent": "research_orchestrator", "query": "luthiers"}'
            ),
        }
        acc.finish_reason = "tool_calls"
        yield (
            "thinking",
            {"content": "I should call the research orchestrator."},
            acc,
        )
        yield (
            "tool_call",
            {
                "id": "tc-invoke-1",
                "name": "invoke_agent",
                "arguments": {
                    "agent": "research_orchestrator",
                    "query": "luthiers",
                },
            },
            acc,
        )
        return
    if turn == 1:
        acc = _StreamAccumulator()
        yield (
            "error",
            {"message": "vLLM returned 500: Error in input stream"},
            acc,
        )
        return
    raise AssertionError(
        f"_stream_vllm_once should not be called more than twice; got turn {turn}"
    )


async def _fake_stream_error_on_turn_zero(
    *args: Any, **kwargs: Any
) -> AsyncIterator[tuple]:
    """Variant: error on the very first stream call, no content yet."""
    acc = _StreamAccumulator()
    yield ("error", {"message": "Error in input stream"}, acc)


async def _fake_stream_error_after_partial_content(
    *args: Any, **kwargs: Any
) -> AsyncIterator[tuple]:
    """Variant: model emitted some prose, then vLLM died mid-stream."""
    acc = _StreamAccumulator()
    acc.content_parts.append("Here is what I found so far: ")
    yield ("token", {"content": "Here is what I found so far: "}, acc)
    yield ("error", {"message": "Error in input stream"}, acc)


async def _fake_run_tool_calls_invoke_agent(
    tool_calls: list[dict], **kwargs: Any
) -> list[dict]:
    """Stub for chat_service._run_tool_calls. Returns a realistic-looking
    agent result for invoke_agent so the loop continues to a second
    streaming iteration."""
    out = []
    for tc in tool_calls:
        if tc["name"] == "invoke_agent":
            result = {
                "agent": "research_orchestrator",
                "result": "Luthiers are stringed-instrument makers ...",
                "tool_calls": 4,
                "duration_seconds": 18,
                "stopped_reason": "done",
            }
        else:
            result = {"ok": True}
        out.append(
            {
                "id": tc["id"],
                "name": tc["name"],
                "arguments": tc["arguments"],
                "result": result,
                "duration_ms": 18000,
            }
        )
    return out


async def _fake_run_tool_calls_unused(*args: Any, **kwargs: Any) -> list[dict]:
    """For variants where no tool calls fire, this should never be called."""
    raise AssertionError("_run_tool_calls should not be invoked in this scenario")


def _drive_stream_chat(
    fake_stream_fn,
    fake_run_tools_fn,
    capture: dict,
) -> list[dict]:
    """
    Run `stream_chat_completion` with a comprehensive set of patches that
    bypass DB, vision, system-prompt assembly, and persona lookup. Captures
    all `chat_store.add_message` calls into `capture["calls"]`. Returns the
    full list of yielded SSE events for inspection.
    """
    capture["calls"] = []

    async def _capture_add_message(**kwargs: Any) -> None:
        capture["calls"].append(kwargs)

    # Reset turn counter on the fake stream fixture (function attribute).
    if hasattr(fake_stream_fn, "turn"):
        fake_stream_fn.turn = 0
    else:
        try:
            fake_stream_fn.turn = 0  # type: ignore[attr-defined]
        except (AttributeError, TypeError):
            pass

    events: list[dict] = []
    persona = _make_stub_persona()
    conversation = _stub_conversation()

    with (
        patch.object(chat_service, "_stream_vllm_once", fake_stream_fn),
        patch.object(chat_service, "_run_tool_calls", fake_run_tools_fn),
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
            chat_service.persona_module, "tool_allowlist", lambda p: None
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
                    {"role": "user", "content": "Try the orchestrator"},
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
            user_message={"content": "Try the orchestrator"},
            rag_config=None,
            ephemeral=False,
        )
        _drain(events, gen)
    return events


def test_invoke_agent_then_stream_error_persists_tool_call_and_marker() -> bool:
    """
    The exact shape of chat 676a3238: invoke_agent fires + completes, then
    the next streaming call errors out. After the fix, the assistant
    message that gets persisted MUST carry the invoke_agent tool_call (so
    the agent doesn't 'vanish') AND a stream-interrupted marker (so the
    user sees why the turn is incomplete).
    """
    capture: dict = {}
    try:
        events = _drive_stream_chat(
            _fake_stream_after_invoke_agent,
            _fake_run_tool_calls_invoke_agent,
            capture,
        )
    except Exception as exc:
        return _check(
            "invoke_agent + mid-stream error -> persisted with tool_call + marker",
            False,
            f"driver raised: {exc!r}",
        )

    # Must have exactly one assistant add_message call.
    assistant_calls = [
        c for c in capture["calls"] if c.get("role") == "assistant"
    ]
    if len(assistant_calls) != 1:
        return _check(
            "invoke_agent + mid-stream error -> exactly one assistant persisted",
            False,
            f"got {len(assistant_calls)} assistant add_message calls; "
            f"all roles: {[c.get('role') for c in capture['calls']]}",
        )
    saved = assistant_calls[0]

    # tool_calls field must contain invoke_agent.
    tcs = saved.get("tool_calls") or []
    has_invoke = any(tc.get("name") == "invoke_agent" for tc in tcs)
    if not has_invoke:
        return _check(
            "invoke_agent tool_call survives stream error",
            False,
            f"persisted tool_calls={tcs!r}",
        )

    # Content must contain the marker, including the underlying error text.
    content = saved.get("content") or ""
    if "stream interrupted" not in content or "Error in input stream" not in content:
        return _check(
            "stream-interrupted marker present in saved content",
            False,
            f"persisted content={content!r}",
        )

    # An error SSE must have surfaced to the frontend.
    error_events = [e for e in events if e.get("event") == "error"]
    if not error_events:
        return _check(
            "error SSE event was forwarded",
            False,
            "no error event in stream; frontend would not know to mark phase=error",
        )

    return _check(
        "invoke_agent + mid-stream error -> persisted with tool_call + marker",
        True,
    )


def test_stream_error_on_turn_zero_persists_marker() -> bool:
    """No tool calls fired, error came on the very first stream. Persisted
    message should still contain the marker so the empty-looking turn is
    explained on reload, not silently absent."""
    capture: dict = {}
    try:
        events = _drive_stream_chat(
            _fake_stream_error_on_turn_zero,
            _fake_run_tool_calls_unused,
            capture,
        )
    except Exception as exc:
        return _check(
            "turn-0 stream error -> persisted with marker",
            False,
            f"driver raised: {exc!r}",
        )

    assistant_calls = [
        c for c in capture["calls"] if c.get("role") == "assistant"
    ]
    if len(assistant_calls) != 1:
        return _check(
            "turn-0 stream error -> exactly one assistant persisted",
            False,
            f"got {len(assistant_calls)} assistant calls",
        )
    saved = assistant_calls[0]

    content = saved.get("content") or ""
    tcs = saved.get("tool_calls") or []
    if "stream interrupted" not in content:
        return _check(
            "turn-0 marker present in saved content",
            False,
            f"persisted content={content!r}",
        )
    if tcs:
        return _check(
            "turn-0 has no tool_calls (none fired)",
            False,
            f"unexpected tool_calls: {tcs!r}",
        )

    # And the error SSE was forwarded.
    if not any(e.get("event") == "error" for e in events):
        return _check(
            "turn-0 error SSE forwarded",
            False,
            "no error event in stream",
        )

    return _check("turn-0 stream error -> persisted with marker", True)


def test_partial_content_then_stream_error_keeps_partial_text() -> bool:
    """The model streamed some prose, then vLLM died. The user already saw
    that prose render, so it must show up in the persisted message
    alongside the marker (not be wiped)."""
    capture: dict = {}
    try:
        _drive_stream_chat(
            _fake_stream_error_after_partial_content,
            _fake_run_tool_calls_unused,
            capture,
        )
    except Exception as exc:
        return _check(
            "partial content + stream error -> partial preserved",
            False,
            f"driver raised: {exc!r}",
        )

    assistant_calls = [
        c for c in capture["calls"] if c.get("role") == "assistant"
    ]
    if len(assistant_calls) != 1:
        return _check(
            "partial-content variant -> exactly one assistant persisted",
            False,
            f"got {len(assistant_calls)} assistant calls",
        )
    content = assistant_calls[0].get("content") or ""

    if "Here is what I found so far:" not in content:
        return _check(
            "partial streamed prose preserved before marker",
            False,
            f"persisted content={content!r}",
        )
    if "stream interrupted" not in content:
        return _check(
            "marker appended after partial prose",
            False,
            f"persisted content={content!r}",
        )

    return _check("partial content + stream error -> partial preserved", True)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

TESTS = [
    # Pure helper
    test_marker_on_empty_content,
    test_marker_on_whitespace_only_content,
    test_marker_appended_with_blank_line,
    test_marker_strips_trailing_whitespace,
    test_marker_with_empty_error_message_degrades_gracefully,
    # Behavioural
    test_invoke_agent_then_stream_error_persists_tool_call_and_marker,
    test_stream_error_on_turn_zero_persists_marker,
    test_partial_content_then_stream_error_keeps_partial_text,
]


def main() -> int:
    failed = 0
    for fn in TESTS:
        try:
            if not fn():
                failed += 1
        except Exception:
            failed += 1
            print(f"[FAIL] {fn.__name__} raised:")
            traceback.print_exc()
    print(f"\n{len(TESTS) - failed}/{len(TESTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
