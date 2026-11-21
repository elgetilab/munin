"""
Per-request token usage aggregator.

A single chat request fires many vLLM calls — the main streaming turn (up
to MAX_TURNS), forced-clarification / forced-required retries, the wrap-up
synthesis, the specialised agents' internal vLLM calls, and the
non-streaming title + summary calls in chat_context. Before this module
existed, ``done.usage`` carried only the last call's usage and the rest
were invisible to quota accounting.

The aggregator is plumbed via ContextVar so call sites don't have to pass
a handle through every function signature. ``stream_chat_completion``
binds an empty dict at the top of the request; every vLLM helper folds
its own ``usage`` into a per-purpose slot. At ``done`` emission the
``aggregate_totals`` sum is what the gateway records, and the per-purpose
map ships alongside so the frontend / admin can see where the tokens went.

Purpose tags currently in use:
    main_turn             streaming chat turn inside the main loop
    wrap_up               final synthesis after the main loop ends
    forced_clarification  recovery retry that forces ask_clarification
    forced_required       recovery retry that forces any tool call
    summary               history compaction (chat_context.summarize_messages)
    title                 conversation auto-title (chat_context.generate_title)

Adding a new vLLM call site? Pick a stable short tag, call
``record_usage(tag, response["usage"])``, and it shows up in the
``done.usage_by_purpose`` map for free.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Optional

# Bound by stream_chat_completion for the duration of one request. ``None``
# means "no aggregator wired" — record_usage becomes a no-op so unit tests
# and standalone callers (e.g. scripts) don't crash.
current_usage_aggregator: ContextVar[Optional[dict]] = ContextVar(
    "current_usage_aggregator", default=None
)

# Bound beside the aggregator: model name -> number of LLM calls it answered
# in this request, from each response's own ``model`` field. A turn served
# by the hosted fallback (llm-failover routing to llm-claude) reports the
# Claude model there, so the done event, the gateway's usage_log and the
# publication-usage summary say which model really answered.
current_model_tally: ContextVar[Optional[dict]] = ContextVar(
    "current_model_tally", default=None
)

_KEYS = ("prompt_tokens", "completion_tokens", "total_tokens")


def record_model(model: Optional[str]) -> None:
    """Count one LLM call answered by ``model`` (no-op without a tally)."""
    tally = current_model_tally.get()
    if tally is None or not model or not isinstance(model, str):
        return
    tally[model] = tally.get(model, 0) + 1


def primary_model(tally: Optional[dict]) -> Optional[str]:
    """The model that answered most calls of the request (None if none)."""
    if not tally:
        return None
    return max(tally.items(), key=lambda kv: kv[1])[0]


def record_usage(purpose: str, usage: Optional[dict], model: Optional[str] = None) -> None:
    """Fold ``usage`` into the current request's aggregator under ``purpose``.

    No-op if no aggregator is bound, or if ``usage`` is empty / None.
    Tolerates missing keys (some vLLM builds omit ``total_tokens``); the
    fallback recomputes it from ``prompt_tokens + completion_tokens`` so
    downstream consumers always see a populated ``total_tokens``.

    Also mirrors the per-call totals into the Prometheus
    ``munin_vllm_tokens_total`` counter (P1 #12). The Prometheus side
    fires even without an aggregator bound — it's process-wide, not
    request-scoped.
    """
    record_model(model)
    if not usage:
        return
    prompt = int(usage.get("prompt_tokens", 0) or 0)
    completion = int(usage.get("completion_tokens", 0) or 0)
    # Prometheus first — fires regardless of aggregator state.
    if prompt or completion:
        from metrics import observe_vllm_tokens
        observe_vllm_tokens(purpose, prompt, completion)
    agg = current_usage_aggregator.get()
    if agg is None:
        return
    slot = agg.setdefault(purpose, {k: 0 for k in _KEYS})
    slot["prompt_tokens"] += prompt
    slot["completion_tokens"] += completion
    # Per call: a call without total_tokens adds prompt + completion. Summing
    # first and filling in the total only while it was still 0 dropped every
    # later call's tokens from the total.
    slot["total_tokens"] += int(usage.get("total_tokens", 0) or 0) or (prompt + completion)


def aggregate_totals(agg: dict) -> dict:
    """Sum each key across every purpose. Produces an OpenAI-shaped usage
    dict ready to drop into the ``done`` SSE event so the gateway scanner
    parses the cumulative total instead of just the last call."""
    totals = {k: 0 for k in _KEYS}
    for slot in agg.values():
        for k in _KEYS:
            totals[k] += int(slot.get(k, 0) or 0)
    if totals["total_tokens"] == 0 and (totals["prompt_tokens"] or totals["completion_tokens"]):
        totals["total_tokens"] = totals["prompt_tokens"] + totals["completion_tokens"]
    return totals
