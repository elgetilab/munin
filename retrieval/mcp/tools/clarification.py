"""
MCP tool: ask_clarification (§14).

The model calls this when a user request is ambiguous and it wants to
pause and ask 1-5 structured multiple-choice questions before doing any
real work. The actual clarification flow is handled entirely by
``chat_service`` — it intercepts ``ask_clarification`` calls **before**
running the generic tool dispatcher so it can:

1. Emit a single ``clarification`` SSE event with the card payload.
2. Persist the assistant turn (markdown fallback content + the tool
   call record) and close the stream cleanly — no wrap-up synthesis,
   no other tool calls from the same turn are executed.

That means this module is mostly a stub: if it ever gets invoked via
the generic path (e.g. ``POST /mcp/call`` for testing) it just echoes
the arguments back with ``status: "direct_call_echo"``. In the real
chat flow, control never reaches this function.
"""

from __future__ import annotations

from typing import Any, Optional


_MAX_UNDERSTOOD = 500
_MAX_QUESTIONS = 5
_MIN_QUESTIONS = 1
_MAX_QUESTION_TEXT = 300
_MAX_OPTIONS = 6
_MIN_OPTIONS = 2
_MAX_OPTION_TEXT = 100


def validate_clarification_payload(
    what_i_understood: Any,
    questions: Any,
) -> tuple[Optional[dict], Optional[str]]:
    """
    Validate a clarification payload and normalise it.

    Returns ``(normalised_dict, None)`` on success or ``(None, error_str)``
    on validation failure. This is used both by the direct-call echo and
    by ``chat_service`` to guard against malformed model output before
    emitting the SSE event.
    """
    if not isinstance(what_i_understood, str):
        return None, "what_i_understood must be a string"
    understood = what_i_understood.strip()
    if not understood:
        return None, "what_i_understood must not be empty"
    if len(understood) > _MAX_UNDERSTOOD:
        return (
            None,
            f"what_i_understood too long ({len(understood)} > {_MAX_UNDERSTOOD})",
        )

    if not isinstance(questions, list):
        return None, "questions must be a list"
    if not (_MIN_QUESTIONS <= len(questions) <= _MAX_QUESTIONS):
        return (
            None,
            f"questions must contain between {_MIN_QUESTIONS} and {_MAX_QUESTIONS} entries",
        )

    normalised_questions: list[dict] = []
    for idx, q in enumerate(questions):
        if not isinstance(q, dict):
            return None, f"questions[{idx}] must be an object"

        text = q.get("text")
        if not isinstance(text, str) or not text.strip():
            return None, f"questions[{idx}].text must be a non-empty string"
        text = text.strip()
        if len(text) > _MAX_QUESTION_TEXT:
            return (
                None,
                f"questions[{idx}].text too long ({len(text)} > {_MAX_QUESTION_TEXT})",
            )

        options = q.get("options")
        if not isinstance(options, list):
            return None, f"questions[{idx}].options must be a list"
        if not (_MIN_OPTIONS <= len(options) <= _MAX_OPTIONS):
            return (
                None,
                f"questions[{idx}].options must have {_MIN_OPTIONS}-{_MAX_OPTIONS} entries",
            )
        norm_options: list[str] = []
        for oidx, opt in enumerate(options):
            if not isinstance(opt, str) or not opt.strip():
                return (
                    None,
                    f"questions[{idx}].options[{oidx}] must be a non-empty string",
                )
            opt_text = opt.strip()
            if len(opt_text) > _MAX_OPTION_TEXT:
                return (
                    None,
                    f"questions[{idx}].options[{oidx}] too long "
                    f"({len(opt_text)} > {_MAX_OPTION_TEXT})",
                )
            norm_options.append(opt_text)

        qid_raw = q.get("id")
        if qid_raw is None or (isinstance(qid_raw, str) and not qid_raw.strip()):
            qid = f"q{idx + 1}"
        elif isinstance(qid_raw, str):
            qid = qid_raw.strip()
        else:
            return None, f"questions[{idx}].id must be a string"

        allow_custom_raw = q.get("allow_custom", True)
        if not isinstance(allow_custom_raw, bool):
            return None, f"questions[{idx}].allow_custom must be a boolean"

        normalised_questions.append({
            "id": qid,
            "text": text,
            "options": norm_options,
            "allow_custom": allow_custom_raw,
        })

    return (
        {"what_i_understood": understood, "questions": normalised_questions},
        None,
    )


def render_markdown_fallback(payload: dict) -> str:
    """
    Render a clarification payload as Markdown for conversation history
    display and for clients that haven't implemented the ``clarification``
    SSE event yet. The frontend renders the structured card from the SSE
    payload directly; this text only shows up in replay / history.
    """
    understood = payload["what_i_understood"]
    lines: list[str] = [
        f"**Just to make sure I understand:** \u201c{understood}\u201d",
        "",
    ]
    for idx, q in enumerate(payload["questions"], start=1):
        lines.append(f"**Q{idx}: {q['text']}**")
        for opt in q["options"]:
            lines.append(f"- {opt}")
        if q.get("allow_custom", True):
            lines.append("- *(or type your own answer)*")
        lines.append("")
    lines.append("*(Waiting for your answers to proceed.)*")
    return "\n".join(lines)


async def ask_clarification(
    what_i_understood: str,
    questions: list[dict],
) -> dict:
    """
    Stub — real behaviour lives in ``chat_service``'s intercept path.

    If this function is ever called directly (e.g. via ``POST /mcp/call``
    during stress tests), it validates the payload and echoes it back
    with ``status: "direct_call_echo"`` so tests can confirm the schema
    is well-formed without actually running a chat turn.
    """
    normalised, err = validate_clarification_payload(what_i_understood, questions)
    if err is not None:
        return {"error": err}
    return {
        "status": "direct_call_echo",
        "what_i_understood": normalised["what_i_understood"],
        "questions": normalised["questions"],
        "note": (
            "Direct MCP call: the chat_service intercept path is the real "
            "execution surface; this echo exists only for schema testing."
        ),
    }
