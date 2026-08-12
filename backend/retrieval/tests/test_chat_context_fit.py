"""Tests for the context-budget fit (chat_context.fit_max_tokens + helpers).

Guards the deep_research vLLM 400 context-overflow fix
(docs/paper-track/done/CONTEXT-BUDGET-FIX-SCOPE.md, Tier 1 + Tier 3): the output budget is
clamped so prompt + output never exceeds the model window, and the history-trim
reservation stays in lockstep with the real output cap.

Run standalone or under pytest:
    python backend/retrieval/tests/test_chat_context_fit.py
    pytest backend/retrieval/tests/test_chat_context_fit.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chat_context as cc  # noqa: E402

try:
    import pytest

    @pytest.fixture(autouse=True)
    def _isolate_prompt_tokens():
        """Several fit tests reassign cc.prompt_tokens to a constant. Restore it
        after each test so the stub doesn't leak into the budget_tool_results
        tests (which need real token estimates). Under the standalone __main__
        runner this fixture is inert, but tests run alphabetically there so the
        budget tests execute before any fit test stubs prompt_tokens anyway."""
        orig = cc.prompt_tokens
        yield
        cc.prompt_tokens = orig
except ImportError:  # pytest not present in a bare standalone run
    pass


def _stub_prompt_tokens(n: int):
    """Force prompt_tokens to a fixed value so the fit arithmetic is tested
    independently of whether the Qwen tokenizer is mounted."""
    cc.prompt_tokens = lambda messages: n


def test_small_prompt_keeps_requested_cap():
    _stub_prompt_tokens(2000)
    assert cc.fit_max_tokens([], 16384) == 16384


def test_overflow_case_is_clamped_to_fit():
    # The exact production failure: 49153-token prompt + 16384 requested = 65537,
    # one over the 65536 window. The fit must clamp so the total fits.
    _stub_prompt_tokens(49153)
    out = cc.fit_max_tokens([], 16384)
    assert out == cc.MAX_MODEL_LEN - 49153 - cc.CTX_MARGIN  # 15871
    assert 49153 + out <= cc.MAX_MODEL_LEN


def test_prompt_filling_window_returns_floor():
    _stub_prompt_tokens(cc.MAX_MODEL_LEN)  # no room for output
    assert cc.fit_max_tokens([], 16384) == cc.MIN_OUTPUT_TOKENS


def test_unset_request_fills_available_room():
    _stub_prompt_tokens(40000)
    assert cc.fit_max_tokens([], None) == cc.MAX_MODEL_LEN - 40000 - cc.CTX_MARGIN


def test_halve_converges_and_stops_at_floor():
    body = {"max_tokens": 16000}
    assert cc.halve_max_tokens(body) and body["max_tokens"] == 8000
    body["max_tokens"] = cc.MIN_OUTPUT_TOKENS
    assert cc.halve_max_tokens(body) is False  # nothing left to give


def test_is_ctx_overflow_matches_vllm_message():
    assert cc.is_ctx_overflow("vLLM returned 400: maximum context length is 65536 tokens")
    assert not cc.is_ctx_overflow("some unrelated 500 error")
    assert not cc.is_ctx_overflow("")


def test_tier3_reserve_locked_to_output_cap():
    # The mismatch (reserve 8000 < cap 16384) was the root cause; they must move
    # together via the shared default.
    assert cc.GENERATION_RESERVE == cc.DEFAULT_MAX_OUTPUT_TOKENS


# --- Tier 2: budget_tool_results ------------------------------------------

def _big(n_chars: int) -> str:
    return "x" * n_chars


def _fanout_messages(n_old_results: int, result_chars: int):
    """system + user + [assistant(tool_calls) + k tool results] * iters, ending
    with a final assistant(tool_calls) + a pending tool result batch."""
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "do a big research thing"},
    ]
    for i in range(n_old_results):
        msgs.append({"role": "assistant", "content": "", "tool_calls": [{"id": f"a{i}"}]})
        msgs.append({"role": "tool", "tool_call_id": f"a{i}", "content": _big(result_chars)})
    # the pending batch: latest assistant + its (not-yet-synthesised) result
    msgs.append({"role": "assistant", "content": "", "tool_calls": [{"id": "pending"}]})
    msgs.append({"role": "tool", "tool_call_id": "pending", "content": _big(result_chars)})
    return msgs


def test_budget_noop_when_under_target():
    msgs = _fanout_messages(2, 100)
    out, n = cc.budget_tool_results(msgs, target_tokens=10_000)
    assert n == 0 and out == msgs


def test_budget_elides_oldest_until_fit_and_protects_pending():
    # 20 old results of ~2k tokens each + a pending one; tiny target forces
    # eliding the old ones. The pending tool result (after the last assistant)
    # must stay full.
    msgs = _fanout_messages(20, 8000)
    pending_idx = len(msgs) - 1
    pending_before = msgs[pending_idx]["content"]
    out, n = cc.budget_tool_results(msgs, target_tokens=12_000)
    assert n > 0
    assert cc.prompt_tokens(out) <= 12_000 or n == 20  # fit, or elided all elidable
    assert out[pending_idx]["content"] == pending_before  # pending untouched
    # an elided old result keeps its role + tool_call_id, only content shrank
    elided = next(m for m in out if m.get("role") == "tool"
                  and m["content"] == cc._ELIDED_TOOL_RESULT)
    assert "tool_call_id" in elided


def test_budget_does_not_mutate_input():
    msgs = _fanout_messages(10, 8000)
    snapshot = [dict(m) for m in msgs]
    cc.budget_tool_results(msgs, target_tokens=5_000)
    assert msgs == snapshot  # original conversation untouched


def test_budget_is_idempotent():
    msgs = _fanout_messages(10, 8000)
    out1, _ = cc.budget_tool_results(msgs, target_tokens=5_000)
    out2, n2 = cc.budget_tool_results(out1, target_tokens=5_000)
    # already-elided results are skipped and the pending batch is protected, so
    # a second pass changes nothing.
    assert n2 == 0 and out2 == out1


def test_budget_elides_pending_as_last_resort_to_avoid_overflow():
    # A single fan-out iteration whose OWN pending result exceeds the window,
    # with no old results to elide. Rather than overflow (and let vLLM reject
    # the turn), the pending result is elided as a last resort. This is julia's
    # deep_research org-chart case (chat 5938cd64, 2026-07-08).
    msgs = _fanout_messages(0, 200_000)  # ~50k-token pending result alone
    pending_idx = len(msgs) - 1
    out, n = cc.budget_tool_results(msgs, target_tokens=12_000)
    assert n == 1
    assert out[pending_idx]["content"] == cc._ELIDED_TOOL_RESULT
    assert cc.prompt_tokens(out) <= 12_000
    # persisted conversation untouched
    assert msgs[pending_idx]["content"] != cc._ELIDED_TOOL_RESULT


def test_budget_elides_largest_pending_first():
    # Three pending results in one batch; elide largest-first only until it
    # fits, keeping the smallest intact.
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "research"},
        {"role": "assistant", "content": "",
         "tool_calls": [{"id": "a"}, {"id": "b"}, {"id": "c"}]},
        {"role": "tool", "tool_call_id": "a", "content": _big(120_000)},  # largest
        {"role": "tool", "tool_call_id": "b", "content": _big(80_000)},
        {"role": "tool", "tool_call_id": "c", "content": _big(8_000)},    # smallest
    ]
    out, n = cc.budget_tool_results(msgs, target_tokens=10_000)
    assert n == 2  # the two largest elided
    assert out[3]["content"] == cc._ELIDED_TOOL_RESULT   # a (largest)
    assert out[4]["content"] == cc._ELIDED_TOOL_RESULT   # b
    assert out[5]["content"] == _big(8_000)              # c (smallest) kept
    assert cc.prompt_tokens(out) <= 10_000


# --- 2026-08: the two prompt costs no budget guard could see -----------------
#
# Live symptom: 23 turns died on vLLM context-length rejections (14 of them in
# the first 12 days of August, hitting 4 of 19 active users) while Tier 1's
# "prompt fills the context window" warning had fired ZERO times and Tier 2 had
# elided NOTHING in 30 days of logs. Both guards read a token count that omitted
# (a) the tools schema, which rides in body["tools"] rather than in `messages`,
# and (b) tool_call arguments, which live in `tool_calls` rather than `content`.
# So both guards agreed with each other and both were wrong.


def _run_python_call(source_chars: int, *, nested: bool = True) -> dict:
    """An assistant message whose only real weight is its tool-call arguments,
    which is the shape chat_service appends inside the tool loop."""
    args = {"code": "x" * source_chars}
    if nested:
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "call_1",
                "type": "function",
                "function": {"name": "run_python", "arguments": __import__("json").dumps(args)},
            }],
        }
    # The persisted (chat_store) shape keeps name/arguments at the top level.
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"id": "call_1", "name": "run_python", "arguments": args}],
    }


def test_tool_call_arguments_are_counted():
    # Pre-fix this scored 4 tokens (the framing allowance) no matter how large
    # the arguments were. Production run_python calls measured 3857-6457 tokens.
    msg = _run_python_call(20_000)
    assert cc._message_tokens(msg) > 1_000


def test_tool_call_arguments_counted_in_both_shapes():
    nested = cc._message_tokens(_run_python_call(20_000, nested=True))
    flat = cc._message_tokens(_run_python_call(20_000, nested=False))
    # Same payload either way, so the two must land in the same ballpark.
    assert nested > 1_000 and flat > 1_000
    assert abs(nested - flat) < max(nested, flat) * 0.2


def test_tool_call_results_are_not_double_counted():
    """Results reach vLLM as separate role:"tool" messages, so counting them
    here as well would double-bill them and make Tier 2 over-elide."""
    with_result = {
        "role": "assistant",
        "content": "",
        "tool_calls": [{
            "id": "c1", "name": "search", "arguments": {"q": "x"},
            "result": {"payload": "y" * 40_000},
        }],
    }
    without_result = {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"id": "c1", "name": "search", "arguments": {"q": "x"}}],
    }
    assert cc._message_tokens(with_result) == cc._message_tokens(without_result)


def test_malformed_tool_calls_do_not_raise():
    for bad in (None, "not-a-list", [None], [{"function": "not-a-dict"}], [{}], 7):
        assert cc._message_tokens({"role": "assistant", "content": "", "tool_calls": bad}) >= 0


def test_tools_schema_tokens_counts_and_caches():
    assert cc.tools_schema_tokens(None) == 0
    assert cc.tools_schema_tokens([]) == 0
    schema = [
        {"type": "function", "function": {
            "name": f"tool_{i}",
            "description": "d" * 400,
            "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
        }}
        for i in range(15)
    ]
    n = cc.tools_schema_tokens(schema)
    assert n > 500
    assert cc.tools_schema_tokens(schema) == n  # cached, stable across calls


def test_fit_subtracts_the_schema_cost():
    # Prompt large enough that the remaining room binds, not the requested cap,
    # so the schema's cost is visible in the result.
    _stub_prompt_tokens(50_000)
    without = cc.fit_max_tokens([], 16384, extra_tokens=0)
    with_schema = cc.fit_max_tokens([], 16384, extra_tokens=7_000)
    assert without == cc.MAX_MODEL_LEN - 50_000 - cc.CTX_MARGIN
    assert without - with_schema == 7_000


def test_fit_hits_the_floor_once_the_schema_is_counted():
    # A prompt that looks fine alone but not once the schema is charged. This is
    # the case Tier 1 silently passed for two months.
    _stub_prompt_tokens(cc.MAX_MODEL_LEN - 6_000)
    assert cc.fit_max_tokens([], 16384, extra_tokens=7_000) == cc.MIN_OUTPUT_TOKENS


def test_budget_trips_only_once_the_schema_is_counted():
    """The regression in one assertion: identical messages, and eliding happens
    only when the out-of-band schema cost is included."""
    msgs = _fanout_messages(6, 8_000)
    body_tokens = cc.prompt_tokens(msgs)
    target = body_tokens + 1_000  # messages alone fit, with 1k to spare
    out_blind, n_blind = cc.budget_tool_results(msgs, target_tokens=target)
    assert n_blind == 0 and out_blind == msgs  # pre-fix behaviour
    _, n_aware = cc.budget_tool_results(
        msgs, target_tokens=target, extra_tokens=7_000
    )
    assert n_aware > 0  # schema pushes it over, so Tier 2 acts


# --- Item 3: the refit must not trust a derived figure -----------------------

_DERIVED_MSG = (
    "vLLM returned 400: {\"error\":{\"message\":\"This model's maximum context "
    "length is 65536 tokens. However, you requested 2048 output tokens and your "
    "prompt contains at least 63489 input tokens, for a total of at least 65537 "
    "tokens.\"}}"
)
_MEASURED_MSG = (
    "vLLM returned 400: {\"error\":{\"message\":\"Input length (70787) exceeds "
    "model's maximum context length (65536).\"}}"
)


def test_derived_prompt_figure_is_rejected():
    """63489 == 65536 + 1 - 2048, i.e. it is computed FROM max_tokens, not
    measured. Refitting from it cannot converge, so it must not be parsed as
    ground truth (main.py::_raw_prompt_tokens documents the same trap)."""
    assert 65536 + 1 - 2048 == 63489  # the identity that makes it unusable
    assert cc.parse_exact_prompt_tokens(_DERIVED_MSG) is None


def test_measured_prompt_figure_is_parsed():
    assert cc.parse_exact_prompt_tokens(_MEASURED_MSG) == 70787
    assert cc.parse_exact_prompt_tokens("") is None
    assert cc.parse_exact_prompt_tokens("unrelated error") is None


def test_refit_uses_the_measured_prompt_to_fit_exactly():
    body = {"max_tokens": 16384}
    measured = cc.MAX_MODEL_LEN - 4_000  # leaves 4000 before the margin
    msg = f"Input length ({measured}) exceeds model's maximum context length (65536)."
    assert cc.refit_max_tokens(body, msg) == "exact"
    assert body["max_tokens"] == 4_000 - cc.CTX_MARGIN
    assert measured + body["max_tokens"] <= cc.MAX_MODEL_LEN


def test_refit_returns_none_when_the_prompt_alone_overflows():
    """Shape B: no max_tokens value can help, so the caller must shrink the
    prompt instead of burning retries on the output budget."""
    body = {"max_tokens": 16384}
    assert cc.refit_max_tokens(body, _MEASURED_MSG) is None


def test_refit_falls_back_to_halving_for_the_derived_shape():
    body = {"max_tokens": 16384}
    assert cc.refit_max_tokens(body, _DERIVED_MSG) == "halved"
    assert body["max_tokens"] == 8192


def test_refit_always_makes_progress():
    """A measurement that would imply a LARGER budget must still shrink, or the
    retry loop could spin until it exhausts its attempts."""
    body = {"max_tokens": 512}
    msg = "Input length (1000) exceeds model's maximum context length (65536)."
    assert cc.refit_max_tokens(body, msg) == "exact"
    assert body["max_tokens"] < 512


def test_retry_ladder_can_reach_a_viable_budget():
    """Production wall: nine identical rejections at max_tokens=2048, which is
    exactly DEFAULT_MAX_OUTPUT_TOKENS halved three times. With the old ceiling
    of 3 the ladder could not go below 2048 and the turn died; the ceiling must
    allow reaching a small budget."""
    body = {"max_tokens": cc.DEFAULT_MAX_OUTPUT_TOKENS}
    for _ in range(cc.MAX_REFIT_RETRIES):
        cc.halve_max_tokens(body)
    assert body["max_tokens"] <= 1024
    assert cc.MAX_REFIT_RETRIES >= 4


if __name__ == "__main__":
    import types
    _orig = cc.prompt_tokens
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and isinstance(fn, types.FunctionType):
            fn()
            print(f"  ok  {name}")
            passed += 1
    cc.prompt_tokens = _orig
    print(f"{passed} tests passed")
