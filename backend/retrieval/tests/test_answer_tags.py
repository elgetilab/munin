"""
Stray ``<answer>`` tags and looped replies (LAB-BUGFIX-PLAN.md F5, Step 3).

Sage-A1.5 wraps replies in literal ``<answer>`` tags and sometimes repeats a
paragraph or block. The strings below are copied from the lab smoke run
(backend/benchmarks/results/lab/smoke/records.jsonl, s3_bars turns 5 and 7).

Covers ``answer_tags.clean_reply`` (persistence and history), the live-stream
filter (also end to end through ``chat_service._stream_vllm_once`` with a fake
vLLM stream), and the prose-action detector on the turn-7 string.

    docker exec munin-retrieval python /app/tests/test_answer_tags.py
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from answer_tags import AnswerTagStreamFilter, clean_reply, has_repeats, strip_answer_tags  # noqa: E402

TURN7 = (
    "\n\n<answer>\nI'll remove KO2 from the chart and regenerate it.\n\n"
    "<answer>\nI'll remove KO2 from the chart and regenerate it.\n\n"
    "<answer>\nI'll remove KO2 from the chart and regenerate it."
)

_T5_LINE = (
    "I'll regenerate the chart with the exact dimensions you specified "
    "(85 mm × 60 mm at 300 dpi)."
)
TURN5_TAIL = (
    "The `bbox_inches='tight'` parameter crops the figure to the content, "
    "which is why the pixel size doesn't match. Let me save with a fixed "
    "bounding box.\n\n"
    + "".join(f"<answer>\n{_T5_LINE}\n\n\n\n" for _ in range(5))
)


def _check(name: str, ok: bool, detail: str = "") -> bool:
    label = "PASS" if ok else "FAIL"
    print(f"[{label}] {name}{(' : ' + detail) if detail and not ok else ''}")
    return ok


def test_turn7_string() -> bool:
    out, n_tags, n_rep = clean_reply(TURN7)
    want = "I'll remove KO2 from the chart and regenerate it."
    return _check(
        "turn-7 string: tags gone, three copies collapse to one",
        out == want and n_tags == 3 and n_rep == 2,
        f"{out!r} tags={n_tags} rep={n_rep}",
    )


def test_turn5_tail() -> bool:
    out, n_tags, n_rep = clean_reply(TURN5_TAIL)
    ok = (
        "<answer>" not in out
        and out.count(_T5_LINE) == 1
        and out.startswith("The `bbox_inches='tight'` parameter")
        and n_tags == 5 and n_rep == 4
    )
    return _check("turn-5 style 5x tail collapses to one copy", ok,
                  f"{out!r} tags={n_tags} rep={n_rep}")


def test_repeated_block_collapses() -> bool:
    block = "Para one: the bars show mean activity.\n\nPara two: error bars are SD."
    out, _, n_rep = clean_reply("\n\n".join([block] * 3))
    return _check("repeated two-paragraph block collapses", out == block and n_rep == 2,
                  repr(out))


def test_single_closed_tag() -> bool:
    out = strip_answer_tags("<answer>\nThe mean is 4.2 (n = 6).\n</answer>")
    return _check("single closed tag pair stripped", out == "The mean is 4.2 (n = 6).",
                  repr(out))


def test_case_insensitive_inline() -> bool:
    out = strip_answer_tags("Result: <ANSWER>42</Answer> units")
    return _check("inline, mixed-case tags stripped", out == "Result: 42 units", repr(out))


def test_tag_inside_code_fence_untouched() -> bool:
    text = (
        "Here is the parser:\n\n"
        "```python\n"
        "pattern = '<answer>(.*?)</answer>'\n"
        "```\n"
    )
    out = strip_answer_tags(text)
    return _check("tag inside a code fence untouched (text unchanged)", out == text,
                  repr(out))


def test_fence_kept_while_prose_cleaned() -> bool:
    text = (
        "<answer>\nUse this:\n\n```\nx = '<answer>'\n```\n</answer>"
    )
    out = strip_answer_tags(text)
    ok = out == "Use this:\n\n```\nx = '<answer>'\n```"
    return _check("prose tags stripped, fenced tag kept", ok, repr(out))


def test_clean_text_unchanged() -> bool:
    text = "## Result\n\n\n- a\n- b\n\nThe plot is saved.\n"
    out, n_tags, n_rep = clean_reply(text)
    return _check("text without tags or repeats is byte-identical",
                  out == text and n_tags == 0 and n_rep == 0, repr(out))


def test_has_repeats() -> bool:
    return _check(
        "has_repeats: fires on turn 7, not on distinct paragraphs",
        has_repeats(TURN7) and not has_repeats("A.\n\nB.\n\nA."),
    )


def _feed_all(chunks: list[str]) -> tuple[str, AnswerTagStreamFilter]:
    f = AnswerTagStreamFilter()
    out = "".join(f.feed(c) for c in chunks) + f.flush()
    return out, f


def test_stream_holdback_across_chunks() -> bool:
    f = AnswerTagStreamFilter()
    first = f.feed("Hi <ans")
    second = f.feed("wer>there</ans")
    third = f.feed("wer> done")
    tail = f.flush()
    ok = (first == "Hi " and second == "there" and third == " done"
          and tail == "" and f.stripped == 2)
    return _check("stream: '<ans' + 'wer>' held back and dropped", ok,
                  f"{first!r} {second!r} {third!r} {tail!r}")


def test_stream_non_tag_released() -> bool:
    out, f = _feed_all(["a <an", "d b < c <", "/div> <answ"])
    return _check("stream: '<' that is not a tag is released; dangling '<answ' flushed",
                  out == "a <and b < c </div> <answ" and f.stripped == 0, repr(out))


def test_stream_one_char_chunks() -> bool:
    out, f = _feed_all(list(TURN7))
    ok = "<answer>" not in out and f.stripped == 3 and "I'll remove KO2" in out
    return _check("stream: turn-7 fed one char at a time drops all tags", ok, repr(out))


def test_stream_fence_passthrough() -> bool:
    text = "Code:\n```\ns = '<answer>'\n```\n<answer>ok"
    out, f = _feed_all([text[i:i + 3] for i in range(0, len(text), 3)])
    ok = out == "Code:\n```\ns = '<answer>'\n```\nok" and f.stripped == 1
    return _check("stream: tag inside a fence passes, prose tag dropped", ok, repr(out))


def test_stream_vllm_once_end_to_end() -> bool:
    """The token frames _stream_vllm_once yields carry no tags; acc keeps raw."""
    import chat_service

    chunks = ["<ans", "wer>\nI'll remove KO2", " from the chart.</", "answer>"]
    lines = [
        "data: " + json.dumps({"choices": [{"delta": {"content": c}}]}) for c in chunks
    ] + [
        "data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
        "data: [DONE]",
    ]

    @contextlib.asynccontextmanager
    async def fake_stream(body, purpose=None):
        async def it():
            for ln in lines:
                yield ln
        yield it()

    real = chat_service.vllm_post_stream
    chat_service.vllm_post_stream = fake_stream
    try:
        async def go():
            shown = []
            acc = None
            async for ev, payload, a in chat_service._stream_vllm_once(
                messages=[{"role": "user", "content": "hi"}],
                sampling={}, enable_tools=False,
            ):
                acc = a
                if ev == "token":
                    shown.append(payload["content"])
            return "".join(shown), acc
        shown, acc = asyncio.run(go())
    finally:
        chat_service.vllm_post_stream = real
    ok = (shown == "\nI'll remove KO2 from the chart."
          and acc is not None and acc.content == "".join(chunks))
    return _check("_stream_vllm_once yields tag-free tokens, acc keeps raw", ok,
                  f"shown={shown!r}")


def test_detector_fires_on_turn7() -> bool:
    from chat_service import _looks_like_prose_action_promise
    return _check("prose-action detector fires on the turn-7 string",
                  _looks_like_prose_action_promise(TURN7))


def test_detector_opener_after_markup() -> bool:
    from chat_service import _looks_like_prose_action_promise
    ok = (_looks_like_prose_action_promise("**Plan.**\nI'll regenerate the figure now.")
          and _looks_like_prose_action_promise("> I’ll rerun the analysis."))
    return _check("detector: opener after newline/markup/curly apostrophe fires", ok)


def test_detector_no_substring_opener() -> bool:
    from chat_service import _looks_like_prose_action_promise
    # "outlet me" must not read as "let me".
    return _check("detector: 'outlet me' is not the 'let me' opener",
                  not _looks_like_prose_action_promise("The outlet me compile data."))


def test_short_repeats_are_formatting() -> bool:
    from answer_tags import clean_reply, has_repeats
    text = "Intro.\n\n---\n\n---\n\nOutput:\n\nOutput:\n\nEnd."
    ok = (not has_repeats(text)) and clean_reply(text)[0] == text
    return _check("short repeated paragraphs ('---', 'Output:') are not a loop", ok)


TESTS = [
    test_turn7_string,
    test_turn5_tail,
    test_repeated_block_collapses,
    test_single_closed_tag,
    test_case_insensitive_inline,
    test_tag_inside_code_fence_untouched,
    test_fence_kept_while_prose_cleaned,
    test_clean_text_unchanged,
    test_has_repeats,
    test_stream_holdback_across_chunks,
    test_stream_non_tag_released,
    test_stream_one_char_chunks,
    test_stream_fence_passthrough,
    test_stream_vllm_once_end_to_end,
    test_detector_fires_on_turn7,
    test_detector_opener_after_markup,
    test_detector_no_substring_opener,
    test_short_repeats_are_formatting,
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
