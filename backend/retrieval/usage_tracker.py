"""
Per-request token usage aggregator.

A single chat request fires many vLLM calls — the main streaming turn (up
to MAX_TURNS), forced-clarification / forced-required retries, the wrap-up
synthesis, an agent's own internal loop (via invoke_agent), and the
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
    agent_turn            non-streaming call inside agents/executor's loop
    agent_wrap_up         non-streaming wrap-up inside agents/executor
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

_KEYS = ("prompt_tokens", "completion_tokens", "total_tokens")


def record_usage(purpose: str, usage: Optional[dict]) -> None:
    """Fold ``usage`` into the current request's aggregator under ``purpose``.

    No-op if no aggregator is bound, or if ``usage`` is empty / None.
    Tolerates missing keys (some vLLM builds omit ``total_tokens``); the
    fallback recomputes it from ``prompt_tokens + completion_tokens`` so
    downstream consumers always see a populated ``total_tokens``.
    """
    if not usage:
        return
    agg = current_usage_aggregator.get()
    if agg is None:
        return
    slot = agg.setdefault(purpose, {k: 0 for k in _KEYS})
    for k in _KEYS:
        slot[k] += int(usage.get(k, 0) or 0)
    if slot["total_tokens"] == 0 and (slot["prompt_tokens"] or slot["completion_tokens"]):
        slot["total_tokens"] = slot["prompt_tokens"] + slot["completion_tokens"]


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
