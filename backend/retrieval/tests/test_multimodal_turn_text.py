"""
Tests for the text a multimodal user turn hands to the router and the
auto-titler.

Guards the fix for the 2026-09-07 silent-routing failure. The composer
(`webui ChatInput.tsx`) sends an OpenAI-style content LIST whenever an image
or a document is attached, `main.py` validates and accepts that shape, but
`stream_chat_completion` read `user_message["content"]` raw at three points
that all assume a string:

    router.parse_slash(list)     -> TypeError: expected string or bytes-like
                                    object, got 'list'
    generate_title(list, "...")  -> AttributeError: 'list' object has no
                                    attribute 'strip'

The router call is wrapped in a `try/except` so the TypeError never surfaced.
It degraded the turn to the pinned/default profile and, because the fallback
assigned `pin_id` (which is None by design under auto-route), persisted
`messages.persona = NULL`. Measured over the live DB: since the router went
live (2026-07-08) every one of the 51 user turns carrying an attachment has
persona NULL, against 537 chat / 465 research / 207 code on turns without one.
A 100% failure rate for two months, invisible because the exception was caught.

The subtle half is WHICH text these two consumers should see.
`_resolve_user_content_images` inlines an attached document's full extracted
body as a text block, so the resolved/persisted text of a .docx turn runs to
tens of thousands of characters (chat b0909633: 32,029). `generate_title`
interpolates its first argument with no truncation, so feeding it the resolved
text would push the title call at the context window. Both consumers therefore
get the text the user actually TYPED, taken from the raw content before
resolution; the DB row keeps the resolved text so the document stays
searchable.

Run standalone or under pytest:
    python backend/retrieval/tests/test_multimodal_turn_text.py
    pytest backend/retrieval/tests/test_multimodal_turn_text.py
    docker exec munin-retrieval python /app/tests/test_multimodal_turn_text.py
"""
from __future__ import annotations

import asyncio
import sys
import traceback
from pathlib import Path
from typing import Any, AsyncIterator, Optional
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chat_service  # noqa: E402
import router as router_module  # noqa: E402
from chat_service import stream_chat_completion  # noqa: E402


IMG = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}


# ---------------------------------------------------------------------------
# Part 1: the pure helper
# ---------------------------------------------------------------------------

def test_string_content_passes_through() -> bool:
    assert chat_service.content_text("what is this") == "what is this"
    return True


def test_single_text_block() -> bool:
    assert chat_service.content_text([{"type": "text", "text": "hello"}]) == "hello"
    return True


def test_text_plus_image_yields_only_the_text() -> bool:
    """The image contributes no text. This is the common paste-a-figure turn."""
    content = [{"type": "text", "text": "what does this show"}, IMG]
    assert chat_service.content_text(content) == "what does this show"
    return True


def test_image_only_turn_yields_empty() -> bool:
    """User pastes a figure and sends with an empty composer."""
    assert chat_service.content_text([IMG]) == ""
    return True


def test_unresolved_document_block_yields_empty() -> bool:
    """A document block carries no text until _resolve_user_content_images
    inlines it, so the PRE-resolution text of a doc-only turn is empty."""
    content = [{"type": "document", "document_id": "doc_1", "filename": "cv.docx"}]
    assert chat_service.content_text(content) == ""
    return True


def test_multiple_text_blocks_join_on_newline() -> bool:
    content = [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]
    assert chat_service.content_text(content) == "a\nb"
    return True


def test_unexpected_types_yield_empty() -> bool:
    assert chat_service.content_text(None) == ""
    assert chat_service.content_text(42) == ""
    assert chat_service.content_text([]) == ""
    assert chat_service.content_text(["not a dict"]) == ""
    return True


def test_matches_the_expression_it_replaces() -> bool:
    """Equivalence guard: content_text() must reproduce, byte for byte, the
    inline expression that used to compute `persisted_text`, including on a
    RESOLVED list where the document body has become a text block."""
    def _old(resolved_content: Any, raw_content: Any) -> str:
        if isinstance(resolved_content, list):
            return "\n".join(
                block.get("text", "") for block in resolved_content
                if isinstance(block, dict) and block.get("type") == "text"
            ).strip()
        return raw_content if isinstance(raw_content, str) else ""

    cases: list[Any] = [
        "plain string",
        "  padded  ",
        [{"type": "text", "text": "typed"}, {"type": "text", "text": "DOC BODY"}],
        [{"type": "text", "text": "typed"}, IMG],
        [IMG],
        [],
        [{"type": "text", "text": "  leading and trailing  "}],
    ]
    for c in cases:
        assert chat_service.content_text(c) == _old(c, c), f"diverged on {c!r}"
    return True


# ---------------------------------------------------------------------------
# Part 2: what the call sites actually pass
#
# Drives the real stream_chat_completion with the DB, vLLM, vision and
# system-prompt assembly stubbed (same approach as
# test_stream_error_persistence.py), and SPIES on router.route and
# generate_title so the assertions are about the call sites, not about the
# helper in isolation.
# ---------------------------------------------------------------------------

TYPED = "compare the two spectra in this figure"
DOC_BODY = "PROJECT PROPOSAL BODY " * 250          # ~5,250 chars
DOC_BLOCK = {"type": "document", "document_id": "doc_1", "filename": "p.docx"}


def _stub_persona(pid: str = "chat") -> dict:
    return {
        "id": pid,
        "name": f"Test {pid}",
        "params": {"system": "You are a test assistant."},
        "tools": [],
    }


async def _fake_stream_plain_answer(*args: Any, **kwargs: Any) -> AsyncIterator[tuple]:
    """One clean turn: a little content, finish_reason stop, no tool calls."""
    acc = chat_service._StreamAccumulator()
    acc.content_parts.append("Here is the answer.")
    acc.finish_reason = "stop"
    yield ("token", {"content": "Here is the answer."}, acc)


async def _fake_run_tool_calls(*args: Any, **kwargs: Any) -> list[dict]:
    raise AssertionError("no tool calls expected in these scenarios")


def _drive(
    content: Any,
    capture: dict,
    *,
    doc_text: str = DOC_BODY,
    title: Optional[str] = None,
    routed_profile: str = "research",
    route_impl=None,
) -> list[dict]:
    """
    Run one turn through stream_chat_completion with `content` as the user
    message's content. Records into `capture`:

        capture["route_calls"]  - positional args each router.route() saw
        capture["title_calls"]  - positional args each generate_title() saw
        capture["add_message"]  - kwargs of every chat_store.add_message call
        capture["assembled"]    - the new_message dict assemble_context got

    `route_impl` replaces the routing decision (default: a KNN-style hit on
    `routed_profile`); pass a callable that raises to exercise the error path.
    """
    capture.setdefault("route_calls", [])
    capture.setdefault("title_calls", [])
    capture.setdefault("add_message", [])
    capture.setdefault("assembled", [])

    def _spy_route(query, pin, index, embed_fn):
        capture["route_calls"].append(query)
        if route_impl is not None:
            return route_impl(query, pin, index, embed_fn)
        slash = router_module.parse_slash(query)
        if slash is not None:
            return router_module.RoutingDecision(
                slash[0], "rule", 1.0, pin=pin, stripped_query=slash[1]
            )
        return router_module.RoutingDecision(routed_profile, "knn", 0.42, pin=pin)

    async def _spy_generate_title(first_user_message, first_assistant_response):
        capture["title_calls"].append(first_user_message)
        return "Generated Title"

    async def _capture_add_message(**kwargs: Any) -> None:
        capture["add_message"].append(kwargs)

    async def _capture_assemble(**kwargs: Any):
        capture["assembled"].append(kwargs.get("new_message"))
        return (
            [
                {"role": "system", "content": "test system prompt"},
                {"role": "user", "content": "stub"},
            ],
            None,
        )

    async def _fake_get_document_text(user_email: str, document_id: str):
        return doc_text

    async def _fake_upload_document(**kwargs: Any) -> dict:
        """Stand in for the inline-image funnel. Without this the host run
        writes to /data/user_docs and logs a permission warning; the failure
        is swallowed by design, so it would be noise, not signal."""
        return {"document_id": "doc_inline_1", "filename": kwargs.get("filename")}

    conversation = {
        "id": "test-conv-1",
        "user_email": "test@example.com",
        "title": title,
        "persona": "munin",
        "default_tags": None,
        "messages": [],
    }

    events: list[dict] = []

    # A flat list entered through an ExitStack, not a nested `with (...)`:
    # CPython caps statically nested blocks at 20 and this patch set is larger.
    patches = [
        patch.object(chat_service, "ROUTER_ENABLED", True),
        patch.object(chat_service, "_stream_vllm_once", _fake_stream_plain_answer),
        patch.object(chat_service, "_run_tool_calls", _fake_run_tool_calls),
        patch.object(chat_service, "_get_router_index", lambda: object()),
        patch.object(chat_service, "router_module", router_module),
        patch.object(router_module, "route", _spy_route),
        patch.object(
            chat_service,
            "_build_full_system_prompt",
            AsyncMock(return_value="test system prompt"),
        ),
        patch.object(
            chat_service.persona_module,
            "get_persona",
            lambda pid: _stub_persona(pid) if pid else None,
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
            chat_service.chat_store, "update_conversation", AsyncMock(return_value=None)
        ),
        patch.object(chat_service.chat_store, "add_message", _capture_add_message),
        patch.object(chat_service.chat_context, "assemble_context", _capture_assemble),
        patch.object(chat_service.chat_context, "generate_title", _spy_generate_title),
        patch.object(
            chat_service.vision,
            "build_tool_result_followup",
            AsyncMock(return_value=None),
        ),
        patch.object(
            chat_service.vision, "build_view_attachment_followup", lambda **kw: None
        ),
        patch.object(
            chat_service,
            "audit_artifact_urls_in_content",
            lambda content, tcs: (content, []),
        ),
        patch("document_store.get_document_text", _fake_get_document_text),
        patch("document_store.upload_document", _fake_upload_document),
        # Post-turn fire-and-forget work. Both reach the real chats.db, which
        # does not exist on a host run, and _maybe_precompact outlives
        # asyncio.run() so its failure surfaces as an unrelated
        # "Event loop is closed" traceback. Neither is under test here.
        patch.object(
            chat_service.chat_context, "_maybe_precompact", AsyncMock(return_value=None)
        ),
        patch.object(
            chat_service.hooks_module, "dispatch_stop", AsyncMock(return_value=None)
        ),
    ]

    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        gen = stream_chat_completion(
            user_email="test@example.com",
            persona_id=None,                      # auto-route, the live default
            conversation_id=conversation["id"],
            user_message={"role": "user", "content": content},
            rag_config=None,
            ephemeral=False,
        )

        async def _run() -> None:
            async for ev in gen:
                events.append(ev)

        asyncio.run(_run())
    return events


def _routing_event(events: list[dict]) -> dict:
    import json
    for ev in events:
        if ev["event"] == "routing":
            return json.loads(ev["data"])
    raise AssertionError("no routing event emitted")


def _user_row(capture: dict) -> dict:
    for kw in capture["add_message"]:
        if kw.get("role") == "user":
            return kw
    raise AssertionError("no user row persisted")


def test_router_receives_a_string_not_a_list() -> bool:
    """The reported TypeError, at its call site."""
    cap: dict = {}
    _drive([{"type": "text", "text": TYPED}, IMG], cap)
    assert len(cap["route_calls"]) == 1, cap["route_calls"]
    q = cap["route_calls"][0]
    assert isinstance(q, str), f"router got {type(q).__name__}, not str"
    assert q == TYPED, repr(q)
    return True


def test_routing_is_not_an_error_on_a_multimodal_turn() -> bool:
    """The user-visible symptom: the turn is routed, not silently degraded."""
    cap: dict = {}
    events = _drive([{"type": "text", "text": TYPED}, IMG], cap)
    ev = _routing_event(events)
    assert ev["method"] != "error", ev
    assert ev["profile"] == "research", ev
    return True


def test_generate_title_receives_a_string() -> bool:
    """The reported AttributeError, at its call site."""
    cap: dict = {}
    _drive([{"type": "text", "text": TYPED}, IMG], cap, title=None)
    assert cap["title_calls"], "generate_title was never reached"
    arg = cap["title_calls"][0]
    assert isinstance(arg, str), f"generate_title got {type(arg).__name__}, not str"
    assert arg == TYPED, repr(arg)
    return True


def test_generate_title_gets_typed_text_not_the_document_body() -> bool:
    """The trap in the obvious fix. Passing the RESOLVED text would hand
    generate_title the whole inlined document, which it interpolates without
    truncation. It must see only what the user typed."""
    cap: dict = {}
    _drive([{"type": "text", "text": TYPED}, DOC_BLOCK], cap, title=None)
    assert cap["title_calls"], "generate_title was never reached"
    arg = cap["title_calls"][0]
    assert arg == TYPED, repr(arg[:120])
    assert "PROJECT PROPOSAL BODY" not in arg, "document body leaked into the title call"
    return True


def test_router_gets_typed_text_not_the_document_body() -> bool:
    """Same argument for the router: embedding 5K of document instead of the
    question would route on the wrong thing."""
    cap: dict = {}
    _drive([{"type": "text", "text": TYPED}, DOC_BLOCK], cap)
    q = cap["route_calls"][0]
    assert q == TYPED, repr(q[:120])
    assert "PROJECT PROPOSAL BODY" not in q
    return True


def test_persisted_row_still_carries_the_document_text() -> bool:
    """The refactor must not change what lands in the FTS-indexed column.

    Checked as an equivalence against the expression content_text() replaced,
    applied to the REAL resolved content the resolver produced for this turn
    (the same object assemble_context received), rather than against a
    hand-written string. The resolver prefixes each inlined document with an
    `[Attached document: ...]` header, and pinning that format here would make
    this test fail the next time the header is reworded, which is not what it
    is guarding.
    """
    cap: dict = {}
    _drive([{"type": "text", "text": TYPED}, DOC_BLOCK], cap)
    row = _user_row(cap)
    resolved = cap["assembled"][0]["content"]
    old_expression = "\n".join(
        block.get("text", "") for block in resolved
        if isinstance(block, dict) and block.get("type") == "text"
    ).strip()
    assert row["content"] == old_expression, repr(row["content"][:200])
    # And the properties that actually matter: the typed text leads, the
    # document body is searchable.
    assert row["content"].startswith(TYPED), repr(row["content"][:120])
    assert "PROJECT PROPOSAL BODY" in row["content"]
    return True


def test_persona_is_persisted_on_a_multimodal_turn() -> bool:
    """The 51 NULL rows. The routed profile must reach the DB."""
    cap: dict = {}
    _drive([{"type": "text", "text": TYPED}, IMG], cap)
    row = _user_row(cap)
    assert row["persona"] is not None, "persona persisted as NULL"
    assert row["persona"] == "research", row["persona"]
    return True


def test_blank_typed_text_skips_the_router_and_stays_on_pin() -> bool:
    """Attachment with an empty composer: nothing to route on, so route() is
    not called at all and the turn honestly reports method 'pin'."""
    cap: dict = {}
    events = _drive([IMG], cap)
    assert cap["route_calls"] == [], f"route() called with {cap['route_calls']!r}"
    ev = _routing_event(events)
    assert ev["method"] == "pin", ev
    assert ev["profile"] is not None, ev
    return True


# ---------------------------------------------------------------------------
# Part 3: slash command alongside an attachment
# ---------------------------------------------------------------------------

def test_slash_command_with_an_image_keeps_the_image() -> bool:
    """`/research compare these` + a figure. Stripping the slash token must
    not flatten the content list and throw the image away."""
    cap: dict = {}
    _drive([{"type": "text", "text": "/research compare these"}, IMG], cap)
    assert cap["assembled"], "assemble_context never reached"
    sent = cap["assembled"][0]["content"]
    assert isinstance(sent, list), f"content list flattened to {type(sent).__name__}"
    assert any(b.get("type") == "image_url" for b in sent), "image dropped"
    texts = [b["text"] for b in sent if b.get("type") == "text"]
    assert texts == ["compare these"], texts
    return True


def test_bare_slash_with_an_image_keeps_the_image() -> bool:
    """`/research` with nothing else typed. The text block strips to empty and
    is dropped; the image must survive."""
    cap: dict = {}
    _drive([{"type": "text", "text": "/research"}, IMG], cap)
    sent = cap["assembled"][0]["content"]
    assert isinstance(sent, list), f"content list flattened to {type(sent).__name__}"
    assert any(b.get("type") == "image_url" for b in sent), "image dropped"
    texts = [b["text"] for b in sent if b.get("type") == "text"]
    assert texts == [], texts
    return True


def test_slash_command_on_plain_text_is_unchanged() -> bool:
    """Regression guard on the existing string path."""
    cap: dict = {}
    _drive("/research find me papers on nanodiscs", cap)
    sent = cap["assembled"][0]["content"]
    assert sent == "find me papers on nanodiscs", repr(sent)
    return True


# ---------------------------------------------------------------------------
# Part 4: the error path that hid this for two months
# ---------------------------------------------------------------------------

def test_router_exception_still_persists_a_real_profile() -> bool:
    """D2. Whatever makes route() raise, the turn still runs on a real
    profile and must record which one. Persisting NULL is what made the
    original failure invisible."""
    def _boom(*args: Any, **kwargs: Any):
        raise RuntimeError("router index unavailable")

    cap: dict = {}
    events = _drive([{"type": "text", "text": TYPED}, IMG], cap, route_impl=_boom)
    ev = _routing_event(events)
    assert ev["method"] == "error", ev          # still diagnosable
    assert ev["pin"] is None, ev                # honest: there was no pin
    assert ev["profile"] is not None, ev
    row = _user_row(cap)
    assert row["persona"] is not None, "router failure persisted persona NULL again"
    assert row["persona"] == "chat", row["persona"]   # DEFAULT_PERSONA_ID
    return True


# ---------------------------------------------------------------------------

TESTS = [
    test_string_content_passes_through,
    test_single_text_block,
    test_text_plus_image_yields_only_the_text,
    test_image_only_turn_yields_empty,
    test_unresolved_document_block_yields_empty,
    test_multiple_text_blocks_join_on_newline,
    test_unexpected_types_yield_empty,
    test_matches_the_expression_it_replaces,
    test_router_receives_a_string_not_a_list,
    test_routing_is_not_an_error_on_a_multimodal_turn,
    test_generate_title_receives_a_string,
    test_generate_title_gets_typed_text_not_the_document_body,
    test_router_gets_typed_text_not_the_document_body,
    test_persisted_row_still_carries_the_document_text,
    test_persona_is_persisted_on_a_multimodal_turn,
    test_blank_typed_text_skips_the_router_and_stays_on_pin,
    test_slash_command_with_an_image_keeps_the_image,
    test_bare_slash_with_an_image_keeps_the_image,
    test_slash_command_on_plain_text_is_unchanged,
    test_router_exception_still_persists_a_real_profile,
]


def _main() -> int:
    failures = 0
    for t in TESTS:
        try:
            t()
            print(f"[PASS] {t.__name__}")
        except Exception:
            failures += 1
            print(f"[FAIL] {t.__name__}")
            traceback.print_exc()
    total = len(TESTS)
    print(f"\n{total - failures}/{total} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_main())
