# Backend ↔ Frontend Sync Log

This is the live coordination log between the cluster (`backend/`)
and VPS (`frontend/`) sides of the monorepo. Add an entry here
when a change needs both sides to coordinate, when one side has a
question for the other, or when a decision is reached that should
be findable later.

The previous sync log (which ran roughly 2026-03 through
2026-04) was archived to
[`archive/SYNC-2026-04.md`](archive/SYNC-2026-04.md) on
2026-05-04 once its self-archival criterion was met
(`default_tags` shipped + both sides done with the
`munin-vps` → monorepo housekeeping). Read that doc for the
historical record of why various design decisions look the way
they do.

For the canonical API contract, see
[`BACKEND-API.md`](BACKEND-API.md). For contributor-ingest, see
[`CONTRIBUTOR-INGEST.md`](CONTRIBUTOR-INGEST.md).

---

## Format

```
### Q<N>. <one-line topic>  (<status>)

**From:** backend | frontend
**Date:** YYYY-MM-DD
**Question / decision / context:**

  ...

**Answer / outcome:**

  ...
```

`status` is one of `open`, `answered`, `decided`, `obsolete`. Mark
status changes inline; don't delete entries, they're load-bearing
context for whoever's catching up.

---

## Open

(none yet)

## Resolved

### Q1. Empty `usage` object in SSE `done` event  (decided)

**From:** frontend
**Date:** 2026-05-19 (retroactive; the gap was documented earlier
in `backend/docs/archive/CLUSTER-USAGE-TRACKING.md`)
**Question / decision / context:**

  The gateway logs `tokens_total` per request by scanning the SSE
  stream for `"usage"`. The cluster previously sent an empty
  `usage: {}` on `done`, so every request was recorded as 0
  tokens — breaking per-user quota enforcement and the admin
  usage dashboard.

**Answer / outcome:**

  Closed by the P0 audit batch on 2026-05-19. `chat_service`
  binds a per-request `usage_aggregator` ContextVar; every vLLM
  call site (main turn, wrap-up, forced retries, agent loops,
  history summary, auto-title) folds its `usage` into a
  per-purpose slot via `record_usage(purpose, usage)`. On `done`
  the aggregate ships as `usage` (gateway-shaped, drop-in
  compatible) **plus** `usage_by_purpose` for per-call-site
  breakdown. No gateway changes were required; the existing
  scanner picks up the cumulative total automatically. Module:
  `retrieval/usage_tracker.py`. Contract: `BACKEND-API.md` §5
  `done` event row.


---

## vLLM scheduling priority: chat ahead of API traffic

**From:** backend
**Date:** 2026-08-25
**Question / decision / context:**

  With the removal of the per-user requests-per-minute and
  concurrent-request limits (gateway, 2026-08-25), nothing stops
  API-key traffic from holding all 8 vLLM batch slots and making
  interactive browser chat queue behind it. Measured on the TP=2
  profile: 8 concurrent is the throughput knee (604 tok/s); at 12
  aggregate throughput *falls* to 487 and p95 latency roughly
  doubles. A per-user cap does not fix this, because three API
  users at 4 each is 12.

**Answer / outcome:**

  PLANNED, not built. Design in
  [`docs/SCHEDULING-PRIORITY-PLAN.md`](../../docs/SCHEDULING-PRIORITY-PLAN.md).

  **No frontend change is expected.** The distinction already
  exists structurally in the backend: API-key requests arrive with
  no persona / conversation_id / project_id and route to
  `main.py:_raw_chat_proxy`, while browser chat goes through the
  persona path. Priority is set there, not in the gateway, because
  the gateway relays request bodies byte-for-byte and injecting a
  field would mean parsing and re-serialising every request
  including streaming ones.

  Flagged here anyway because it changes API-request latency under
  contention, which is user-visible on `api.muninai.org`, and
  because the docs on `docs.muninai.org` may need a line about it.

  Two things the frontend side should know if it ever does get
  involved: vLLM's `priority` is **lower = earlier** (chat keeps
  the default 0; API is demoted with a positive number), and any
  non-zero priority **400s** unless the server was started with
  `--scheduling-policy priority`, so the serve flag must land
  before anything sends the field.
