"""
Standalone unit tests for the prose-only-action-promise heuristic
(`_looks_like_prose_action_promise` in chat_service.py) that gates
the prose-action recovery hook.

Regression guard for the failure pattern observed in chat
cbb006ff on 2026-04-27: on the third turn ("can you have the beamer
presentation in 16:9 format?") the model wrote a short future-tense
announcement of intent ("I'll update the Beamer presentation to use
16:9 format and compile it for you.") and stopped — never emitted
the compile_latex tool call. The thinking trace explicitly said
"I need to call compile_latex". When followed by user pushback
("Use your tool"), the same shape repeated for eight more turns.

Same pattern reproduced in scripts/flakiness-suite.py
modify_aspect_ratio_after_colour_change reps 1 and 3 on 2026-04-27.

Runs in-process inside the retrieval container:

    docker exec munin-retrieval python /app/tests/test_prose_action_recovery.py

Exit 0 = pass, non-zero = fail.

Pure-function tests — no I/O, no model calls. Behavioural coverage
of the same failure shape lives in scripts/flakiness-suite.py
latex_compile/modify_aspect_ratio_after_colour_change.
"""

from __future__ import annotations

import sys
import traceback

sys.path.insert(0, "/app")

from chat_service import _looks_like_prose_action_promise  # noqa: E402


def _check(name: str, ok: bool, detail: str = "") -> bool:
    label = "PASS" if ok else "FAIL"
    print(f"[{label}] {name}{(' — ' + detail) if detail and not ok else ''}")
    return ok


# ---------------------------------------------------------------------------
# Positive cases — these are the actual prose drafts from observed failures.
# Heuristic MUST fire so the recovery hook kicks in.
# ---------------------------------------------------------------------------

def test_observed_aspect_ratio_promise_v1() -> bool:
    """Direct copy from flakiness-suite rep 1 turn 2."""
    content = (
        "I'll update the Beamer presentation to use 16:9 widescreen "
        "format by adding `aspectratio=16` to the document class "
        "options, then recompile it."
    )
    return _check(
        "observed aspect-ratio promise (rep 1) → fires",
        _looks_like_prose_action_promise(content),
    )


def test_observed_aspect_ratio_promise_v2() -> bool:
    """Direct copy from flakiness-suite rep 3 turn 2."""
    content = "I'll update the Beamer presentation to use 16:9 format and compile it for you."
    return _check(
        "observed aspect-ratio promise (rep 3) → fires",
        _looks_like_prose_action_promise(content),
    )


def test_let_me_compile_promise() -> bool:
    """The 'Let me X' opener variant. Common after error-acknowledgment turns."""
    content = (
        "You're absolutely right - I keep promising to compile without "
        "actually using the compile_latex tool. Let me do that properly now."
    )
    # Note: this is borderline — the opener is "Let me do that" but the
    # action verb "compile_latex tool" appears earlier as a noun phrase.
    # The heuristic should fire because the opener appears in the first
    # 200 chars and "compile" is in the content.
    return _check(
        "'Let me ... compile ...' → fires",
        _looks_like_prose_action_promise(content),
    )


def test_i_will_recompile_promise() -> bool:
    content = "I will recompile the LaTeX with the corrected aspect ratio."
    return _check(
        "'I will recompile ...' → fires",
        _looks_like_prose_action_promise(content),
    )


def test_im_going_to_update_promise() -> bool:
    content = "I'm going to update the document class and regenerate the PDF for you."
    return _check(
        "'I'm going to update ... regenerate ...' → fires",
        _looks_like_prose_action_promise(content),
    )


def test_now_let_me_run_promise() -> bool:
    content = "Now let me run the script to generate the requested plot."
    return _check(
        "'Now let me run ...' → fires",
        _looks_like_prose_action_promise(content),
    )


# ---------------------------------------------------------------------------
# Negative cases — heuristic MUST NOT fire on legitimate responses.
# ---------------------------------------------------------------------------

def test_completion_tense_does_not_fire() -> bool:
    """Past-tense work descriptions describe finished work — recovery
    must not be triggered, the tool calls already ran."""
    content = (
        "I've compiled the Beamer presentation with the requested 16:9 "
        "aspect ratio. The PDF is ready."
    )
    return _check(
        "completion tense ('I've compiled ...') → does not fire",
        not _looks_like_prose_action_promise(content),
    )


def test_completion_tense_i_have_does_not_fire() -> bool:
    content = "I have updated the LaTeX source and recompiled successfully."
    return _check(
        "completion tense ('I have updated ...') → does not fire",
        not _looks_like_prose_action_promise(content),
    )


def test_meta_explanation_does_not_fire() -> bool:
    """Pure prose explanation, no action verb — must not fire."""
    content = (
        "I'll explain how the Schrödinger equation describes the time "
        "evolution of a quantum system."
    )
    return _check(
        "'I'll explain ...' → does not fire (no action verb)",
        not _looks_like_prose_action_promise(content),
    )


def test_long_response_does_not_fire() -> bool:
    """Length cap protects long answers that begin with future-tense
    framing but actually contain the answer in full."""
    content = (
        "I'll walk you through the derivation step by step. "
        "First, we start from the time-dependent Schrödinger equation. "
        + ("This is a detailed explanation that goes on. " * 30)
    )
    return _check(
        "long response → does not fire (over length cap)",
        not _looks_like_prose_action_promise(content),
    )


def test_short_non_action_does_not_fire() -> bool:
    content = "Yes."
    return _check(
        "very short non-action response → does not fire",
        not _looks_like_prose_action_promise(content),
    )


def test_empty_does_not_fire() -> bool:
    return _check(
        "empty content → does not fire",
        not _looks_like_prose_action_promise(""),
    )


def test_none_does_not_fire() -> bool:
    return _check(
        "None content → does not fire",
        not _looks_like_prose_action_promise(None),  # type: ignore[arg-type]
    )


def test_no_first_person_opener_does_not_fire() -> bool:
    """An action-verb sentence without the future-tense opener is
    almost certainly post-hoc commentary, not a promise."""
    content = "The compile_latex tool will produce a PDF artifact."
    return _check(
        "third-person action statement → does not fire",
        not _looks_like_prose_action_promise(content),
    )


def test_question_back_to_user_does_not_fire() -> bool:
    """Clarification-style follow-up — handled by the §14
    clarification fallback, not this recovery."""
    content = "Could you tell me which colour scheme you'd prefer?"
    return _check(
        "clarification question → does not fire",
        not _looks_like_prose_action_promise(content),
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

TESTS = [
    test_observed_aspect_ratio_promise_v1,
    test_observed_aspect_ratio_promise_v2,
    test_let_me_compile_promise,
    test_i_will_recompile_promise,
    test_im_going_to_update_promise,
    test_now_let_me_run_promise,
    test_completion_tense_does_not_fire,
    test_completion_tense_i_have_does_not_fire,
    test_meta_explanation_does_not_fire,
    test_long_response_does_not_fire,
    test_short_non_action_does_not_fire,
    test_empty_does_not_fire,
    test_none_does_not_fire,
    test_no_first_person_opener_does_not_fire,
    test_question_back_to_user_does_not_fire,
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
