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

Save-always extension (2026-05-08): a user
hit "Error in input stream" twice. Each conversation has the user
message persisted but ZERO assistant rows in the DB and the
retrieval service log shows HTTP 200 with no traceback. The only
path consistent with that combination is GeneratorExit from a
client disconnect: when the frontend closes the EventSource (e.g.
the user reloads after seeing the red error banner), main.py
breaks its async-for over the chat_service generator, which calls
aclose() and throws GeneratorExit at the current yield. Because
GeneratorExit derives from BaseException (not Exception) it is
not caught by any `except Exception:` block, and uvicorn does not
log it. Persistence is downstream of the last yield, so it never
runs.

Three layers of coverage:

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

3. Save-always tests that simulate client disconnect (consumer
   calls aclose() after a target SSE event) AND unhandled
   exceptions at points downstream of the user-message persist.
   In all of these the assistant turn must end up persisted — not
   silently lost — because the save-always finally is the
   contract.

A NOTE ON THE assemble_context STUB (fixed 2026-09-07). It must return a
TUPLE. `assemble_context` is typed `-> tuple[list[dict], Optional[dict]]`
and the caller does `messages, compact_info = await ...`, so a stub
returning a bare 2-element list unpacked one message dict into each
variable. `messages` became a dict, and the single test that reaches the
tool-call continuation died on `messages.append(...)` with
`AttributeError: 'dict' object has no attribute 'append'`. A broken test,
not a broken product, and it had been failing in the deployed container for
as long as `assemble_context` has had a second return value. The other 18
never got that far, but they were quietly emitting a `compact_boundary`
SSE built out of the user message, because `compact_info` was a truthy
dict instead of None.

The general shape is worth remembering: a stub whose RETURN SHAPE drifts
from the function it replaces fails somewhere far from the stub, and only
on the paths that consume the second half of it.

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


async def _fake_stream_empty_then_content(
    *args: Any, **kwargs: Any
) -> AsyncIterator[tuple]:
    """Fix #3 shape: the first streaming pass is empty (model emitted an
    immediate stop, so the iterator yields nothing and `acc` stays None);
    the in-place retry then produces real content. The turn should recover
    silently with NO stream-interrupted marker."""
    turn = _fake_stream_empty_then_content.turn  # type: ignore[attr-defined]
    _fake_stream_empty_then_content.turn = turn + 1  # type: ignore[attr-defined]
    if turn == 0:
        return
        yield  # unreachable; makes this an (empty) async generator
    if turn == 1:
        acc = _StreamAccumulator()
        acc.content_parts.append("Here is the answer.")
        acc.finish_reason = "stop"
        yield ("token", {"content": "Here is the answer."}, acc)
        return
    raise AssertionError(
        f"_stream_vllm_once called too many times; got turn {turn}"
    )


async def _fake_stream_always_empty(
    *args: Any, **kwargs: Any
) -> AsyncIterator[tuple]:
    """Both the initial pass and the single retry come back empty. The turn
    must give up with the 'vLLM produced no output' marker: worst case is
    unchanged from before Fix #3, and the retry can't loop forever."""
    return
    yield  # unreachable; makes this an (empty) async generator


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
    conversation_id: Optional[str] = "test-conv-1",
    stream_id: Optional[str] = None,
) -> list[dict]:
    """
    Run `stream_chat_completion` with a comprehensive set of patches that
    bypass DB, vision, system-prompt assembly, and persona lookup. Captures
    all `chat_store.add_message` calls into `capture["calls"]`. Returns the
    full list of yielded SSE events for inspection.

    Pass ``conversation_id=None`` to exercise the new-conversation path
    (create_conversation is patched to return the stub id) and
    ``stream_id`` to exercise the background-turns registry backfill.
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
            # A TUPLE. assemble_context is typed
            # `-> tuple[list[dict], Optional[dict]]`, and the caller does
            # `messages, compact_info = await ...`. Returning a bare
            # 2-element list unpacked one message dict into each variable,
            # so `messages` became a dict: every test reaching the tool-call
            # continuation died on `messages.append(...)`, and the other 18
            # quietly emitted a compact_boundary SSE built from the user
            # message. See the module docstring.
            AsyncMock(
                return_value=(
                    [
                        {"role": "system", "content": "test system prompt"},
                        {"role": "user", "content": "Try the orchestrator"},
                    ],
                    None,
                )
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
            conversation_id=(
                conversation["id"] if conversation_id is not None else None
            ),
            user_message={"content": "Try the orchestrator"},
            rag_config=None,
            ephemeral=False,
            stream_id=stream_id,
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


def test_empty_completion_retries_and_recovers() -> bool:
    """Fix #3: an empty first pass triggers a single in-place retry. When
    the retry yields content, the turn completes normally: persisted
    content carries the recovered text, NO stream-interrupted marker, and a
    `retrying` SSE surfaced so the frontend showed a reconnecting blip."""
    capture: dict = {}
    try:
        events = _drive_stream_chat(
            _fake_stream_empty_then_content,
            _fake_run_tool_calls_unused,
            capture,
        )
    except Exception as exc:
        return _check(
            "empty completion -> retried and recovered",
            False,
            f"driver raised: {exc!r}",
        )

    assistant_calls = [
        c for c in capture["calls"] if c.get("role") == "assistant"
    ]
    if len(assistant_calls) != 1:
        return _check(
            "empty-completion retry -> exactly one assistant persisted",
            False,
            f"got {len(assistant_calls)} assistant calls",
        )
    saved = assistant_calls[0]
    content = saved.get("content") or ""

    if "Here is the answer." not in content:
        return _check(
            "recovered content persisted", False, f"content={content!r}"
        )
    if "stream interrupted" in content:
        return _check(
            "no interruption marker on a recovered turn",
            False,
            f"content={content!r}",
        )
    retrying = [e for e in events if e.get("event") == "retrying"]
    if not retrying:
        return _check(
            "retrying SSE emitted before the retry",
            False,
            "no retrying event; frontend would show no reconnecting blip",
        )
    if any(e.get("event") == "error" for e in events):
        return _check(
            "no error SSE on a recovered turn",
            False,
            "an error event leaked to the frontend",
        )
    return _check("empty completion -> retried and recovered", True)


def test_empty_completion_twice_gives_up_with_marker() -> bool:
    """Fix #3 worst case: if the retry is ALSO empty, the turn gives up with
    the unchanged 'vLLM produced no output' marker, proving the retry is
    bounded (no infinite loop) and the fallback path is preserved."""
    capture: dict = {}
    try:
        events = _drive_stream_chat(
            _fake_stream_always_empty,
            _fake_run_tool_calls_unused,
            capture,
        )
    except Exception as exc:
        return _check(
            "empty completion x2 -> gives up with marker",
            False,
            f"driver raised: {exc!r}",
        )

    assistant_calls = [
        c for c in capture["calls"] if c.get("role") == "assistant"
    ]
    if len(assistant_calls) != 1:
        return _check(
            "empty x2 -> exactly one assistant persisted",
            False,
            f"got {len(assistant_calls)} assistant calls",
        )
    content = assistant_calls[0].get("content") or ""
    if "stream interrupted" not in content or "vLLM produced no output" not in content:
        return _check(
            "no-output marker present after exhausted retry",
            False,
            f"content={content!r}",
        )
    if not any(e.get("event") == "error" for e in events):
        return _check(
            "error SSE forwarded after exhausted retry",
            False,
            "no error event in stream",
        )
    return _check("empty completion x2 -> gives up with marker", True)


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
# Save-always tests (chat 3951063c, 2026-05-08): client disconnect mid-stream
# and unhandled exceptions downstream of user-message persist must still
# leave an assistant row in the DB.
# ---------------------------------------------------------------------------


def _drive_stream_chat_disconnect_after(
    fake_stream_fn,
    fake_run_tools_fn,
    capture: dict,
    target_event: str,
    cancel_event: Optional[asyncio.Event] = None,
    stream_id: Optional[str] = None,
) -> list[dict]:
    """
    Drive stream_chat_completion until the consumer receives an SSE event
    whose event-name equals `target_event`, then explicitly aclose() the
    generator. This mirrors what main.py does when request.is_disconnected()
    fires: it `break`s its async-for over the chat_service generator, which
    causes aclose() to be invoked, which throws GeneratorExit at the
    generator's current yield. Persistence is downstream of the last yield,
    so without a save-always finally, the assistant row never lands.

    Reuses `_drive_stream_chat`'s patch stack via a thin async-for
    replacement.
    """
    capture["calls"] = []

    async def _capture_add_message(**kwargs: Any) -> None:
        capture["calls"].append(kwargs)

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
            # Tuple, for the same reason as the stub in _drive_stream_chat.
            AsyncMock(
                return_value=(
                    [
                        {"role": "system", "content": "test system prompt"},
                        {"role": "user", "content": "trigger"},
                    ],
                    None,
                )
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
            user_message={"content": "trigger"},
            rag_config=None,
            ephemeral=False,
            cancel_event=cancel_event,
            stream_id=stream_id,
        )

        async def _run() -> None:
            try:
                async for ev in gen:
                    events.append(ev)
                    if ev.get("event") == target_event:
                        # Simulate the consumer (main.py) breaking out of
                        # its async-for after detecting client disconnect.
                        # Explicit aclose() makes the GeneratorExit moment
                        # deterministic for the test; the same effect
                        # happens via GC + aclose() when main.py's
                        # event_stream() returns after its `break`.
                        # When a cancel_event was supplied, set it first —
                        # that is the explicit-Stop shape: the cancel
                        # endpoint fires the event, then the client
                        # aborts its fetch.
                        if cancel_event is not None:
                            cancel_event.set()
                        await gen.aclose()
                        return
            except StopAsyncIteration:
                return

        asyncio.run(_run())

    return events


def test_client_disconnect_after_stream_error_persists_marker() -> bool:
    """
    Reproduces chat 3951063c (2026-05-08): the model's first vLLM call
    yielded an `error` event before any content. The frontend banner
    shows up, the user closes the tab, the EventSource closes, main.py
    breaks its async-for, GeneratorExit propagates into chat_service.
    The save-always finally must persist a marker-only assistant row
    so the failed turn is not silently lost.
    """
    capture: dict = {}
    try:
        events = _drive_stream_chat_disconnect_after(
            _fake_stream_error_on_turn_zero,
            _fake_run_tool_calls_unused,
            capture,
            target_event="error",
        )
    except Exception as exc:
        return _check(
            "client disconnect after error -> persisted with marker",
            False,
            f"driver raised: {exc!r}",
        )

    assistant_calls = [
        c for c in capture["calls"] if c.get("role") == "assistant"
    ]
    if len(assistant_calls) != 1:
        return _check(
            "client disconnect after error -> exactly one assistant persisted",
            False,
            f"got {len(assistant_calls)} assistant calls; events={events!r}",
        )
    saved = assistant_calls[0]
    content = saved.get("content") or ""
    if "stream interrupted" not in content:
        return _check(
            "client disconnect after error -> marker present",
            False,
            f"persisted content={content!r}",
        )
    if not any(e.get("event") == "error" for e in events):
        return _check(
            "client disconnect after error -> error SSE was forwarded",
            False,
            "consumer did not see the error event before disconnect",
        )
    return _check(
        "client disconnect after error -> persisted with marker",
        True,
    )


def test_client_disconnect_after_partial_content_keeps_partial_text() -> bool:
    """
    Variant: model emitted some prose tokens, the consumer disconnects
    after the first token (e.g. user clicks "stop generating" or the
    network drops). Partial content must survive in the DB so the user
    can see what they got on reload.
    """
    capture: dict = {}
    try:
        events = _drive_stream_chat_disconnect_after(
            _fake_stream_error_after_partial_content,
            _fake_run_tool_calls_unused,
            capture,
            target_event="token",
        )
    except Exception as exc:
        return _check(
            "client disconnect after token -> partial preserved",
            False,
            f"driver raised: {exc!r}",
        )

    assistant_calls = [
        c for c in capture["calls"] if c.get("role") == "assistant"
    ]
    if len(assistant_calls) != 1:
        return _check(
            "client disconnect after token -> exactly one assistant persisted",
            False,
            f"got {len(assistant_calls)} assistant calls; events={events!r}",
        )
    content = assistant_calls[0].get("content") or ""
    if "Here is what I found so far:" not in content:
        return _check(
            "partial streamed prose preserved on disconnect",
            False,
            f"persisted content={content!r}",
        )
    return _check(
        "client disconnect after token -> partial preserved",
        True,
    )


def test_client_disconnect_marker_says_disconnected_not_stream_interrupted() -> bool:
    """
    Chat 56b39f33 (2026-06-03): user reported the literal
    `_(stream interrupted: stream interrupted)_` marker and had no
    way to tell whether the server crashed or whether they themselves
    closed the tab. The save-always finally previously fell back to
    the literal string "stream interrupted" whenever `had_stream_error`
    was falsy. The fix differentiates: cancellation (client disconnect)
    gets "client disconnected before completion", any other path gets
    "server error during stream".

    This test exercises the disconnect path and asserts the new text.
    """
    capture: dict = {}
    try:
        _drive_stream_chat_disconnect_after(
            _fake_stream_error_after_partial_content,
            _fake_run_tool_calls_unused,
            capture,
            target_event="token",
        )
    except Exception as exc:
        return _check(
            "disconnect marker says 'client disconnected'",
            False,
            f"driver raised: {exc!r}",
        )

    assistant_calls = [
        c for c in capture["calls"] if c.get("role") == "assistant"
    ]
    if not assistant_calls:
        return _check(
            "disconnect marker says 'client disconnected'",
            False,
            "no assistant row persisted",
        )
    content = assistant_calls[0].get("content") or ""
    # The previous bug was the literal double-phrase below appearing
    # because the fallback string `"stream interrupted"` was passed as
    # the error_message argument to apply_stream_error_marker.
    if "stream interrupted: stream interrupted" in content:
        return _check(
            "disconnect marker says 'client disconnected'",
            False,
            f"literal pre-fix placeholder still present: {content!r}",
        )
    # Even though the fixture would have emitted an error SSE
    # eventually, the consumer disconnected after the first token
    # event, BEFORE the error event made it through. So
    # `had_stream_error` stays None and the disconnect branch wins.
    if "client disconnected before completion" not in content:
        return _check(
            "disconnect marker says 'client disconnected'",
            False,
            f"expected 'client disconnected before completion' in saved "
            f"content; got: {content!r}",
        )
    return _check("disconnect marker says 'client disconnected'", True)


def test_pure_client_disconnect_uses_disconnected_default() -> bool:
    """Direct test of the new `_cancelled()` branch in save-always:
    no upstream error, no stream completion, the consumer just
    disconnects mid-stream. The marker must say "client disconnected
    before completion" -- not the old "stream interrupted: stream
    interrupted" placeholder."""

    async def _slow_stream(*args: Any, **kwargs: Any) -> AsyncIterator[tuple]:
        # Stream three tokens and stop -- no error event, no done.
        # The consumer disconnects after the second token before the
        # turn finishes, so save-always runs with had_stream_error=None.
        acc = _StreamAccumulator()
        for chunk in ("hello ", "there ", "friend"):
            acc.content_parts.append(chunk)
            yield ("token", {"content": chunk}, acc)

    capture: dict = {}
    try:
        _drive_stream_chat_disconnect_after(
            _slow_stream,
            _fake_run_tool_calls_unused,
            capture,
            target_event="token",
        )
    except Exception as exc:
        return _check(
            "pure-disconnect marker uses new default",
            False,
            f"driver raised: {exc!r}",
        )

    assistant_calls = [
        c for c in capture["calls"] if c.get("role") == "assistant"
    ]
    if not assistant_calls:
        return _check(
            "pure-disconnect marker uses new default",
            False,
            "no assistant row persisted",
        )
    content = assistant_calls[0].get("content") or ""
    if "stream interrupted: stream interrupted" in content:
        return _check(
            "pure-disconnect marker uses new default",
            False,
            f"literal pre-fix placeholder still present: {content!r}",
        )
    if "client disconnected before completion" not in content:
        return _check(
            "pure-disconnect marker uses new default",
            False,
            f"expected 'client disconnected before completion' in saved "
            f"content; got: {content!r}",
        )
    return _check("pure-disconnect marker uses new default", True)


def test_unhandled_exception_in_tool_call_persists_marker() -> bool:
    """
    Tool dispatch raises (e.g. _run_tool_calls hits an asyncio.gather
    fan-out exception that is not caught). Today this propagates out
    of the streaming loop unhandled and skips persistence. After the
    fix the finally block persists a marker-only assistant row that
    explains the gap.
    """
    capture: dict = {}

    async def _raises_on_tool_dispatch(*args: Any, **kwargs: Any) -> list[dict]:
        raise RuntimeError("simulated tool execution failure")

    # Use the invoke-agent stream fixture so a tool call IS attempted.
    try:
        events = _drive_stream_chat(
            _fake_stream_after_invoke_agent,
            _raises_on_tool_dispatch,
            capture,
        )
    except RuntimeError:
        # The fix re-raises so the request handler can surface it via
        # SSE and so uvicorn marks the response as errored. That's
        # acceptable - we just need to verify persistence happened
        # before the re-raise.
        pass
    except Exception as exc:
        return _check(
            "tool exception -> persisted with marker",
            False,
            f"driver raised unexpected exception: {exc!r}",
        )

    assistant_calls = [
        c for c in capture["calls"] if c.get("role") == "assistant"
    ]
    if len(assistant_calls) != 1:
        return _check(
            "tool exception -> exactly one assistant persisted",
            False,
            f"got {len(assistant_calls)} assistant calls",
        )
    content = assistant_calls[0].get("content") or ""
    if "stream interrupted" not in content:
        return _check(
            "tool exception -> marker present in saved content",
            False,
            f"persisted content={content!r}",
        )
    return _check(
        "tool exception -> persisted with marker",
        True,
    )


def test_unhandled_exception_in_assemble_context_persists_marker() -> bool:
    """
    Context assembly raises after the user message is persisted but
    before the streaming loop starts. Today this propagates and skips
    assistant persistence; after the fix the finally block must still
    leave a marker-only assistant row.
    """
    capture: dict = {}
    capture["calls"] = []

    async def _capture_add_message(**kwargs: Any) -> None:
        capture["calls"].append(kwargs)

    async def _raises_on_assemble(*args: Any, **kwargs: Any) -> list:
        raise RuntimeError("simulated assemble_context failure")

    persona = _make_stub_persona()
    conversation = _stub_conversation()

    with (
        patch.object(
            chat_service,
            "_stream_vllm_once",
            AsyncMock(side_effect=AssertionError("must not be called")),
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
            _raises_on_assemble,
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
            user_message={"content": "trigger"},
            rag_config=None,
            ephemeral=False,
        )

        async def _run() -> None:
            try:
                async for _ev in gen:
                    pass
            except RuntimeError:
                # Expected: the fix re-raises after persisting in the
                # finally block.
                return

        asyncio.run(_run())

    user_calls = [c for c in capture["calls"] if c.get("role") == "user"]
    assistant_calls = [
        c for c in capture["calls"] if c.get("role") == "assistant"
    ]
    if not user_calls:
        return _check(
            "assemble_context exception -> user message was persisted first",
            False,
            "no user-role add_message call seen; the gap doesn't apply",
        )
    if len(assistant_calls) != 1:
        return _check(
            "assemble_context exception -> exactly one assistant persisted",
            False,
            f"got {len(assistant_calls)} assistant calls",
        )
    content = assistant_calls[0].get("content") or ""
    if "stream interrupted" not in content:
        return _check(
            "assemble_context exception -> marker present in saved content",
            False,
            f"persisted content={content!r}",
        )
    return _check(
        "assemble_context exception -> persisted with marker",
        True,
    )


def test_audit_exception_after_loop_still_persists() -> bool:
    """
    The streaming loop completed cleanly, but a post-loop audit
    function raises (e.g. a regex bug in audit_paper_urls_in_content).
    Today this propagates and skips the chat_store.add_message at line
    2330. After the fix the finally block must still persist what we
    have — both the streamed content and any tool calls that fired.
    """
    capture: dict = {}

    def _raises_on_audit(content, tcs):
        raise RuntimeError("simulated audit failure")

    async def _fake_one_token_then_done(
        *args: Any, **kwargs: Any
    ) -> AsyncIterator[tuple]:
        acc = _StreamAccumulator()
        acc.content_parts.append("Here is the answer.")
        acc.finish_reason = "stop"
        yield ("token", {"content": "Here is the answer."}, acc)

    capture["calls"] = []

    async def _capture_add_message(**kwargs: Any) -> None:
        capture["calls"].append(kwargs)

    persona = _make_stub_persona()
    conversation = _stub_conversation()

    with (
        patch.object(
            chat_service, "_stream_vllm_once", _fake_one_token_then_done
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
            # Tuple, for the same reason as the stub in _drive_stream_chat.
            AsyncMock(
                return_value=(
                    [
                        {"role": "system", "content": "test system prompt"},
                        {"role": "user", "content": "trigger"},
                    ],
                    None,
                )
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
            chat_service, "audit_artifact_urls_in_content", _raises_on_audit
        ),
    ):
        gen = stream_chat_completion(
            user_email="test@example.com",
            persona_id="chat",
            conversation_id=conversation["id"],
            user_message={"content": "trigger"},
            rag_config=None,
            ephemeral=False,
        )

        async def _run() -> None:
            try:
                async for _ev in gen:
                    pass
            except RuntimeError:
                return

        asyncio.run(_run())

    assistant_calls = [
        c for c in capture["calls"] if c.get("role") == "assistant"
    ]
    if len(assistant_calls) != 1:
        return _check(
            "audit exception -> exactly one assistant persisted",
            False,
            f"got {len(assistant_calls)} assistant calls",
        )
    content = assistant_calls[0].get("content") or ""
    # Either the original content (if the audit error did not cause a
    # marker) or content + marker (if the fix labels it as
    # interrupted) is acceptable. Critical: the content the user saw
    # ("Here is the answer.") must survive.
    if "Here is the answer." not in content:
        return _check(
            "audit exception -> streamed content survives",
            False,
            f"persisted content={content!r}",
        )
    return _check(
        "audit exception -> persisted with content preserved",
        True,
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def test_explicit_stop_marker_says_stopped_by_user() -> bool:
    """Background turns follow-up: an explicit Stop (cancel endpoint)
    records cancel_reason on the registry stream before firing
    cancel_event. The save-always marker must surface that reason —
    "stopped by user" — instead of blaming a client disconnect, which
    is what the transcript used to claim for every cancellation."""
    import stream_registry as sr

    s = sr.Stream(
        user_email="test@example.com", conversation_id="test-conv-1"
    )
    s.cancel_reason = "stopped by user"  # what the endpoint sets
    sr.registry.register(s)
    capture: dict = {}
    try:
        _drive_stream_chat_disconnect_after(
            _fake_stream_error_after_partial_content,
            _fake_run_tool_calls_unused,
            capture,
            target_event="token",
            cancel_event=s.cancel_event,
            stream_id=s.stream_id,
        )
        assistant_calls = [
            c for c in capture["calls"] if c.get("role") == "assistant"
        ]
        content = (
            assistant_calls[0].get("content") or ""
        ) if assistant_calls else ""
        ok = (
            len(assistant_calls) == 1
            and "stream interrupted: stopped by user" in content
            and "client disconnected" not in content
        )
        return _check(
            "explicit Stop -> marker says stopped by user",
            ok,
            f"content={content!r}",
        )
    finally:
        sr.registry._streams.pop(s.stream_id, None)


def test_new_conversation_backfills_registry_stream() -> bool:
    """Background turns: for a NEW conversation the registry Stream is
    constructed before the conversation row exists (the POST body has
    no conversation_id), so stream_chat_completion must backfill
    stream.conversation_id once the row is created — otherwise the
    active_stream / generating lookups never match first-message
    turns, which is exactly the close-tab-on-first-question case
    (found in live verification 2026-07-25)."""
    import stream_registry as sr

    async def _clean_stream(*args: Any, **kwargs: Any) -> AsyncIterator[tuple]:
        acc = _StreamAccumulator()
        acc.content_parts.append("Answer.")
        acc.finish_reason = "stop"
        yield ("token", {"content": "Answer."}, acc)

    s = sr.Stream(user_email="test@example.com", conversation_id=None)
    sr.registry.register(s)
    capture: dict = {}
    try:
        # This is the only test here that completes a turn CLEANLY, so
        # it is the only one that reaches the post-turn stop hooks —
        # patch them out or the memory-extract hook tries to open the
        # real chats.db, which only exists inside the container.
        with patch.object(
            chat_service.hooks_module,
            "dispatch_stop",
            AsyncMock(return_value=None),
        ):
            _drive_stream_chat(
                _clean_stream,
                _fake_run_tool_calls_unused,
                capture,
                conversation_id=None,
                stream_id=s.stream_id,
            )
        return _check(
            "new-conversation turn backfills registry conversation_id",
            s.conversation_id == "test-conv-1",
            f"conversation_id={s.conversation_id!r}",
        )
    finally:
        sr.registry._streams.pop(s.stream_id, None)


TESTS = [
    test_explicit_stop_marker_says_stopped_by_user,
    test_new_conversation_backfills_registry_stream,
    # Pure helper
    test_marker_on_empty_content,
    test_marker_on_whitespace_only_content,
    test_marker_appended_with_blank_line,
    test_marker_strips_trailing_whitespace,
    test_marker_with_empty_error_message_degrades_gracefully,
    # Behavioural (existing)
    test_invoke_agent_then_stream_error_persists_tool_call_and_marker,
    test_stream_error_on_turn_zero_persists_marker,
    test_empty_completion_retries_and_recovers,
    test_empty_completion_twice_gives_up_with_marker,
    test_partial_content_then_stream_error_keeps_partial_text,
    # Save-always (chat 3951063c, 2026-05-08)
    test_client_disconnect_after_stream_error_persists_marker,
    test_client_disconnect_after_partial_content_keeps_partial_text,
    test_client_disconnect_marker_says_disconnected_not_stream_interrupted,
    test_pure_client_disconnect_uses_disconnected_default,
    test_unhandled_exception_in_tool_call_persists_marker,
    test_unhandled_exception_in_assemble_context_persists_marker,
    test_audit_exception_after_loop_still_persists,
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
