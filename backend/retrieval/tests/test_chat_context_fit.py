"""Tests for the context-budget fit (chat_context.fit_max_tokens + helpers).

Guards the deep_research vLLM 400 context-overflow fix
(todo_v2/CONTEXT-BUDGET-FIX-SCOPE.md, Tier 1 + Tier 3): the output budget is
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
