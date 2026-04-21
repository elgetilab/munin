"""
Standalone unit tests for raw-mode detection at /api/chat/completions.

Regression guard for the "Meitner injected into external /v1/ calls"
bug: the VPS gateway proxies api.muninai.org/v1/chat/completions →
cluster /api/chat/completions without a persona field, and before
this guard shipped the cluster defaulted to the chat persona,
prepending Munin's system prompt to every request from Cursor / aider
/ raw API clients.

Runs inside the retrieval container via:

    docker exec munin-retrieval python /app/tests/test_raw_mode.py

Exit 0 = pass, non-zero = fail.
"""

from __future__ import annotations

import sys
import traceback

sys.path.insert(0, "/app")

from main import (  # noqa: E402
    _is_raw_mode_request,
    _MUNIN_ONLY_FIELDS,
    _header_ephemeral_flag,
)


def _check(name: str, ok: bool, detail: str = "") -> bool:
    label = "PASS" if ok else "FAIL"
    print(f"[{label}] {name}{(' — ' + detail) if detail and not ok else ''}")
    return ok


# ---------------------------------------------------------------------------
# External /v1/ requests MUST be detected as raw.
# ---------------------------------------------------------------------------

def test_external_v1_openai_style_is_raw() -> bool:
    """Body the gateway forwards for a typical external request:
    no persona, no conversation_id, no project_id. Own system message
    as the first message."""
    body = {
        "model": "qwen3.5-35b-a3b",
        "messages": [
            {"role": "system", "content": "You are a coding assistant."},
            {"role": "user", "content": "Write a bash one-liner."},
        ],
        "temperature": 0.2,
        "stream": True,
    }
    return _check("external /v1/ OpenAI-style body → raw", _is_raw_mode_request(body))


def test_explicit_persona_raw() -> bool:
    return _check(
        'persona == "raw" → raw',
        _is_raw_mode_request({"persona": "raw", "messages": [{"role": "user", "content": "hi"}]}),
    )


def test_explicit_persona_none() -> bool:
    return _check(
        'persona == "none" → raw',
        _is_raw_mode_request({"persona": "none", "messages": [{"role": "user", "content": "hi"}]}),
    )


def test_persona_raw_uppercase_whitespace() -> bool:
    return _check(
        'persona == "  RAW  " → raw (normalised)',
        _is_raw_mode_request({"persona": "  RAW  ", "messages": [{"role": "user"}]}),
    )


# ---------------------------------------------------------------------------
# Frontend requests MUST NOT be detected as raw.
# ---------------------------------------------------------------------------

def test_frontend_new_chat_with_persona_not_raw() -> bool:
    return _check(
        "frontend new chat with persona=chat → not raw",
        not _is_raw_mode_request({
            "persona": "chat",
            "messages": [{"role": "user", "content": "hello"}],
        }),
    )


def test_frontend_existing_chat_with_conv_id_not_raw() -> bool:
    """Frontend frequently omits persona on existing conversations and
    relies on the cluster to look up the stored value. Must NOT trip
    raw mode."""
    return _check(
        "frontend existing chat (persona omitted, conv_id present) → not raw",
        not _is_raw_mode_request({
            "conversation_id": "conv-abc-123",
            "messages": [{"role": "user", "content": "follow up"}],
        }),
    )


def test_frontend_new_project_chat_without_persona_not_raw() -> bool:
    """Brand-new chat filed into a project inherits the project's
    default_persona server-side. project_id in the body is enough to
    signal frontend intent."""
    return _check(
        "frontend new chat with project_id but no persona → not raw",
        not _is_raw_mode_request({
            "project_id": "proj-xyz",
            "messages": [{"role": "user", "content": "start project chat"}],
        }),
    )


def test_frontend_ephemeral_with_persona_not_raw() -> bool:
    """Ephemeral chats have no conversation_id but always carry a
    persona from the UI. Must NOT trip raw mode."""
    return _check(
        "frontend ephemeral with explicit persona → not raw",
        not _is_raw_mode_request({
            "persona": "research",
            "ephemeral": True,
            "messages": [{"role": "user", "content": "quick question"}],
        }),
    )


def test_empty_persona_string_treated_as_missing() -> bool:
    """A literal empty string for persona behaves the same as an
    absent field — the body has nothing to disambiguate from a raw
    /v1/ call."""
    return _check(
        'persona == "" with no conv/project → raw',
        _is_raw_mode_request({
            "persona": "",
            "messages": [{"role": "user", "content": "hi"}],
        }),
    )


# ---------------------------------------------------------------------------
# Munin-only field list — make sure the proxy strips exactly the right keys.
# ---------------------------------------------------------------------------

def test_munin_only_fields_covered() -> bool:
    """Every body field that's meaningful only to chat_service must be
    in _MUNIN_ONLY_FIELDS so the raw proxy doesn't forward it to vLLM.
    Failing this test means the proxy might leak a munin-specific key
    that makes vLLM 400 on request validation."""
    required = {"persona", "conversation_id", "project_id", "ephemeral", "rag", "tags"}
    missing = required - _MUNIN_ONLY_FIELDS
    return _check(
        "_MUNIN_ONLY_FIELDS covers every munin-specific body key",
        not missing,
        f"missing: {missing!r}",
    )


def test_header_ephemeral_truthy_values() -> bool:
    """VPS gateway stamps `true`; external scripts might send `1` or
    `yes`. All should flip the flag on."""
    cases = ["true", "True", "TRUE", " true ", "1", "yes", "YES", "Yes"]
    for raw in cases:
        if not _header_ephemeral_flag(raw):
            return _check(
                "header ephemeral truthy values trip the flag",
                False,
                f"{raw!r} should be truthy",
            )
    return _check("header ephemeral truthy values trip the flag", True)


def test_header_ephemeral_falsy_values() -> bool:
    """Anything that isn't in the truthy set must NOT trip the flag.
    Explicit enumerations of common falsy spellings so a regression
    that silently widens the set gets caught."""
    cases = [
        "",
        "   ",
        "false",
        "False",
        "0",
        "no",
        "off",
        "random",
        "truthy",   # partial match — must NOT trip
        "yes please",
    ]
    for raw in cases:
        if _header_ephemeral_flag(raw):
            return _check(
                "header ephemeral falsy values keep the flag off",
                False,
                f"{raw!r} should be falsy",
            )
    return _check("header ephemeral falsy values keep the flag off", True)


def test_header_ephemeral_non_string_input() -> bool:
    """Defensive: the FastAPI header accessor always returns a string,
    but if something upstream passes None / bytes / dict we must not
    crash — just return False."""
    for raw in (None, b"true", 1, {"x": "true"}, ["true"]):
        try:
            if _header_ephemeral_flag(raw):  # type: ignore[arg-type]
                return _check(
                    "non-string header values default to False (no crash)",
                    False,
                    f"{raw!r} unexpectedly returned True",
                )
        except Exception as e:
            return _check(
                "non-string header values default to False (no crash)",
                False,
                f"{raw!r} raised {type(e).__name__}: {e}",
            )
    return _check("non-string header values default to False (no crash)", True)


def test_standard_openai_fields_not_stripped() -> bool:
    """OpenAI params the client depends on must NOT be in the strip
    list. Catches accidental over-filtering."""
    standard = {
        "model", "messages", "temperature", "top_p", "max_tokens",
        "stream", "tools", "tool_choice", "response_format", "stop",
        "logprobs", "top_logprobs", "seed", "user",
    }
    leaked = standard & _MUNIN_ONLY_FIELDS
    return _check(
        "_MUNIN_ONLY_FIELDS does not strip OpenAI-standard fields",
        not leaked,
        f"over-stripped: {leaked!r}",
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

TESTS = [
    test_external_v1_openai_style_is_raw,
    test_explicit_persona_raw,
    test_explicit_persona_none,
    test_persona_raw_uppercase_whitespace,
    test_frontend_new_chat_with_persona_not_raw,
    test_frontend_existing_chat_with_conv_id_not_raw,
    test_frontend_new_project_chat_without_persona_not_raw,
    test_frontend_ephemeral_with_persona_not_raw,
    test_empty_persona_string_treated_as_missing,
    test_header_ephemeral_truthy_values,
    test_header_ephemeral_falsy_values,
    test_header_ephemeral_non_string_input,
    test_munin_only_fields_covered,
    test_standard_openai_fields_not_stripped,
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
