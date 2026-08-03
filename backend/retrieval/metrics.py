"""
Prometheus metrics for the Munin retrieval service (P1 #12).

All metrics live in the default ``prometheus_client`` REGISTRY and are
exposed via ``/metrics`` on the same FastAPI app as the rest of
retrieval (port 8080). Caddy only proxies ``/api/*`` and ``/paper/*``
externally, so ``/metrics`` is naturally cluster-internal.

Metric naming follows the ``munin_<subsystem>_<noun>`` convention so a
multi-service Prometheus can group them. Counters end in ``_total`` per
Prometheus norms; histograms end in ``_seconds`` because that's the
unit.

The helpers (``observe_vllm_request`` etc.) keep the call sites short
one-liners and centralise the bucket / label choices here.
"""

from __future__ import annotations

from typing import Optional

from prometheus_client import Counter, Histogram

# ---------------------------------------------------------------------------
# Histogram bucket choices — tuned for the actual latency ranges we see.
# ---------------------------------------------------------------------------

# vLLM: title gen ~0.5s, single chat turn 1-5s, deep_research wrap-up
# up to ~120s, runaway up to the engine timeout.
_VLLM_BUCKETS = (0.5, 1, 2, 5, 10, 30, 60, 120, 300)

# MCP tools: hot reads (paper_search, calculate, faq) <0.5s; sandbox /
# compile_latex / deep_research 1-60s.
_TOOL_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1, 2, 5, 15, 60)


# ---------------------------------------------------------------------------
# Metric definitions
# ---------------------------------------------------------------------------

vllm_request_total = Counter(
    "munin_vllm_request_total",
    "vLLM HTTP calls broken down by purpose and final outcome.",
    labelnames=("purpose", "outcome"),
)

vllm_request_duration_seconds = Histogram(
    "munin_vllm_request_duration_seconds",
    "Wall time of a single successful vLLM call (retries not included).",
    labelnames=("purpose",),
    buckets=_VLLM_BUCKETS,
)

vllm_tokens_total = Counter(
    "munin_vllm_tokens_total",
    "Tokens billed per vLLM call site. Mirrors record_usage in usage_tracker.",
    labelnames=("purpose", "kind"),  # kind = prompt | completion
)

mcp_tool_total = Counter(
    "munin_mcp_tool_total",
    "MCP tool dispatches broken down by tool name and outcome.",
    labelnames=("name", "outcome"),
)

mcp_tool_duration_seconds = Histogram(
    "munin_mcp_tool_duration_seconds",
    "Wall time of a single MCP tool dispatch.",
    labelnames=("name",),
    buckets=_TOOL_BUCKETS,
)

chat_turns_total = Counter(
    "munin_chat_turns_total",
    "Chat turns completed, by persona and terminal reason.",
    labelnames=("persona", "terminal_reason"),
)

phantom_url_total = Counter(
    "munin_phantom_url_total",
    "Phantom-URL audit hits (model hallucinated a URL no tool produced).",
    labelnames=("kind",),  # kind = artifact | paper
)

citation_claims_total = Counter(
    "munin_citation_claims_total",
    "Author attributions in assistant answers, by whether a tool result "
    "from the same turn supports them.",
    labelnames=("outcome",),  # outcome = grounded | ungrounded
)


# ---------------------------------------------------------------------------
# Helpers — single import surface for call sites.
# ---------------------------------------------------------------------------

def observe_vllm_request(
    purpose: Optional[str],
    outcome: str,
    duration: Optional[float] = None,
) -> None:
    """Record a vLLM call. ``purpose`` of None silently skips — keeps
    callers that don't pass a purpose tag from emitting unlabelled
    metrics. ``duration`` is observed only on a successful outcome."""
    if not purpose:
        return
    vllm_request_total.labels(purpose=purpose, outcome=outcome).inc()
    if duration is not None and outcome == "success":
        vllm_request_duration_seconds.labels(purpose=purpose).observe(duration)


def observe_vllm_tokens(purpose: str, prompt: int, completion: int) -> None:
    """Mirror of record_usage's accounting into the Prometheus counters."""
    if prompt:
        vllm_tokens_total.labels(purpose=purpose, kind="prompt").inc(prompt)
    if completion:
        vllm_tokens_total.labels(purpose=purpose, kind="completion").inc(completion)


def observe_tool(name: str, outcome: str, duration: float) -> None:
    mcp_tool_total.labels(name=name, outcome=outcome).inc()
    mcp_tool_duration_seconds.labels(name=name).observe(duration)


def observe_turn(persona: Optional[str], terminal_reason: str) -> None:
    """Persona may be None on early-exit paths (e.g. unknown persona);
    coerce to 'unknown' so the label cardinality stays predictable."""
    chat_turns_total.labels(
        persona=persona or "unknown",
        terminal_reason=terminal_reason,
    ).inc()


def observe_phantom_urls(kind: str, count: int) -> None:
    if count > 0:
        phantom_url_total.labels(kind=kind).inc(count)


def observe_citation_claims(grounded: int, ungrounded: int) -> None:
    """Record the per-turn author-attribution audit. Both counts matter: the
    ungrounded rate is only interpretable against how many attributions were
    made at all."""
    if grounded > 0:
        citation_claims_total.labels(outcome="grounded").inc(grounded)
    if ungrounded > 0:
        citation_claims_total.labels(outcome="ungrounded").inc(ungrounded)


# ---------------------------------------------------------------------------
# /metrics endpoint mount.
# ---------------------------------------------------------------------------

def mount_metrics(app) -> None:
    """Attach Prometheus ``/metrics`` to a FastAPI app. Cluster-internal
    only — Caddy does not proxy this path externally."""
    from prometheus_client import make_asgi_app

    app.mount("/metrics", make_asgi_app())
