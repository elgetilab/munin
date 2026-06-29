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
