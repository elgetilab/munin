# Cluster Usage Tracking — RESOLVED 2026-05-19

The empty-`usage` bug this doc described is fixed. The cluster now emits
aggregated token counts on every `done` SSE event, summed across every
vLLM call in the request (main turn + wrap-up + forced retries + agent
loops + history summary + auto-title), so the gateway records the real
total instead of just the last call's tokens.

Where to look now:

- Wire format: [`../../shared/docs/BACKEND-API.md`](../../shared/docs/BACKEND-API.md)
  §5 `done` event — documents the new `usage` + `usage_by_purpose` payload.
- Implementation: [`../retrieval/usage_tracker.py`](../retrieval/usage_tracker.py)
  — `record_usage(purpose, usage)` + `aggregate_totals(agg)`, plumbed via
  a ContextVar so every vLLM helper folds without signature changes.
- Historical context: [`archive/CLUSTER-USAGE-TRACKING.md`](archive/CLUSTER-USAGE-TRACKING.md)
  — the original problem statement, kept for "why was this once a gap"
  searches.

Closes P0 #3 from [`../../docs/architecture/HARNESS-AUDIT-2026-05.md`](../../docs/architecture/HARNESS-AUDIT-2026-05.md).
