# munin: design decisions

Short rationales for non-obvious choices made over the lifetime of
the monorepo, captured so future readers don't have to re-derive
them. Companion to git history (which has the *what*).

Add a new section when a decision is non-obvious from code +
commit messages alone. Don't add a section for things that
self-document (renames, refactors, bug fixes).

---

## 2026-07: background turns — registry is the source of truth, no DB status column

Chat turns now survive a closed tab: after the 60 s reconnect grace,
the stream registry promotes a listenerless turn to *background*
(bounded: 2 per user, 30 min wall-clock) instead of cancelling it, so
the answer generates to completion and persists. Non-obvious choices:

- **"Turn in progress" state lives in the in-memory StreamRegistry,
  not in chats.db.** `GET /api/chats/{id}` derives `active_stream`
  (and the listing derives `generating`) from the registry at request
  time. A durable status column was rejected: the registry and the
  chats API share a process, so the registry is always reachable when
  the question is asked, and a DB status would go stale exactly when
  it matters — on process death the turn is dead anyway (SIGTERM runs
  the save-always persist) and a durable "generating" flag would lie
  forever. Deep Research keeps its `research_jobs.status` column
  because its jobs deliver into chats asynchronously; chat turns
  deliver into the same process's registry.
- **Stop became an explicit endpoint**
  (`POST /api/chat/completions/{id}/cancel`). Before background
  turns, the Stop button only aborted the client fetch and silently
  relied on the grace timer to cancel 60 s later. Once grace expiry
  promotes instead of cancels, closing the connection stops nothing —
  and as a side effect Stop is now immediate instead of delayed a
  minute.
- **410 on resume is a reload signal, not an error.** The turn's
  outcome is persisted no matter how the stream ends, so the webui
  maps a resume 410 to "refetch the transcript" (synthetic
  `stream_gone` event). Mid-stream reconnect failures keep the error
  banner: there the user watched a live bubble die.
- **Reopen re-attach replays from seq 0**, ignoring any stored
  `Last-Event-ID`: the client's accumulators are empty after a
  reload, so resuming mid-log would build a final bubble missing
  everything before the checkpoint (latent P1 #10 wart, fixed by the
  same change).

## 2026-07: web_search primary source = Brave Search API (direct), SearXNG demoted to supplement

`web_search` (retrieval MCP tool) now calls the Brave Search API
(`api.search.brave.com`, keyed JSON API) directly from `web.py` when
`BRAVE_API_KEY` is set, with the SearXNG fan-out kept as a keyless
best-effort supplement. Why this shape:

- **The scraper path is dead from a datacenter IP.** All four keyless
  SearXNG engines (startpage, duckduckgo, qwant, mojeek) are CAPTCHA'd
  or access-denied from the cluster's IP (probe 2026-07-22). This is
  inherent to scraping consumer engines from a datacenter address, not
  tunable via UA/proxy settings. The "science" engines keep working
  because they are APIs — which is the lesson.
- **Direct call, not SearXNG's `brave` engine.** SearXNG's built-in
  `brave` engine is an HTML scraper (dropped 2026-06-01 after months of
  "Suspended: too many requests"); it has no first-class engine for the
  JSON API. Industry practice for production web search is a keyed
  index API or a paid SERP proxy, never scraping.
- **Why Brave was rejected before and isn't now:** the free tier's
  1k req/month quota was too thin for multi-user load. A paid key
  removed that objection.
- **Rate limiting:** Brave free tier is 1 req/s, so Brave calls are
  serialized through a min-interval throttle (`BRAVE_SEARCH_QPS`,
  default 1; raise via cluster.env when the key's tier allows) and the
  fan-out to Brave is capped at `BRAVE_MAX_QUERIES` (default 3) variants
  per call since each is a billed request. SearXNG still gets the full
  fan-out for free.
- **Degradation semantics preserved:** when Brave answers cleanly, a
  zero-hit response is a real "no information found" even if SearXNG is
  fully suspended; the TOOL FAILURE warning only fires when nothing
  keyed worked either. With `BRAVE_API_KEY` unset, behaviour is
  byte-identical to the old SearXNG-only path.
- **Sovereignty:** web queries leave the cluster to Brave — the same
  trust boundary the scraper path already had. Brave is an independent
  index and EU-friendly, the most defensible of the keyed options.

## 2026-07: Deep Research (MiroThinker) disabled, kept in code

The standalone Deep Research feature (research.muninai.org, `/deepresearch/*`
endpoints, `deepresearch-daemon` + SLURM job running MiroThinker-30B on GPU 0)
is disabled but not deleted, pending a final retirement decision. The chat-side
`deep_research` MCP tool (composite decompose/expand/search pipeline on the main
model) covers the same ground far cheaper and stays; it is unrelated to Miro.

What "disabled" means:
- `POST /deepresearch/submit` returns 503 unless `DEEPRESEARCH_ENABLED=1`
  (flag in `retrieval/database.py`, default off). The read endpoints
  (`status`, `output`, `jobs`, `queue`) still work so past reports remain
  downloadable.
- `deepresearch-daemon` is `systemctl disable --now`ed on hugin; unit file and
  scripts stay installed, `deploy.sh deepresearch` still works but prints a
  disabled notice.
- research.muninai.org serves an "unavailable" notice page pointing to Chat;
  all nav/footer/docs links to it were removed. The old submit UI is in git
  history (`frontend/static/research/index.html`).
- MiroThinker weights stay on disk at
  `/opt/munin/data/models/mirothinker-v1.5-30b` (~17 GB).

Why disable rather than delete: the model may still be retired for good or
revived with a stronger harness; keeping the plumbing makes either cheap.
Re-enable = env flag + daemon enable + restore the page from git.

## 2026-06: persona delegation retired in favour of the per-turn router (A1-A4)

The persona system used to do three entangled jobs: tool-allowlist scoping,
prompt/sampling shaping, and mid-conversation **delegation** (`delegate_to_persona`
handed a turn to another persona, with message rewind, persona persistence,
and `delegated`/`persona_changed` SSE). The migration (`todo_v2/`, steps A1-A4)
replaced delegation with an up-front **per-turn router** (`router.py`): each
turn's profile (chat/research/code) is chosen from the query, biased by the
pinned persona, and the system prompt is composed `base[pin] + fragment[routed]`.

Why delegation went, not just got disabled:
- The A1 soak (delegation off, attempts logged) plus chats.db showed it was
  rarely used (~15 attempts in 6 weeks) and the cases were all cross-profile
  needs the router now handles up-front (a chat user asking a research
  question routes to research directly).
- Once the allowlists are retired (A4b), every tool is reachable via CORE +
  tool_search regardless of profile, so delegation's real job (tool access)
  vanishes. It became vestigial.

A4a deleted the machinery (intercept, rewind, budget, `delegated`/
`persona_changed` SSE, the `DELEGATION_ENABLED` flag, `delegate_to_persona`
from CORE/registry). `backend/scripts/test_delegate_persona.py` is retired
(stub) — its replacement is the routing eval
(`backend/benchmarks/munin_bench/routing/`).

**Deliberate trade-off (A4b):** the per-persona `tool_allowlist` was a HARD
boundary (research literally could not run_python). The router relaxes it:
profiles bias tool *usage* via the prompt fragment + a soft `resident_tools`
surfacing set, but any tool is reachable. If a specific tool ever needs a hard
wall, add an explicit per-tool guard — do NOT resurrect allowlists.

## 2026-05-25: SSE reconnect decouples listener from work, reshapes P0 #2

P1 #10 makes a mid-stream WiFi blip or full browser refresh resume the
in-flight chat turn instead of losing it. The realisation that made
this feasible was decoupling the HTTP listener from the work:

- A per-request `Stream` (in `retrieval/stream_registry.py`) holds the
  monotonic event log, a `cancel_event`, and listener attach/detach
  state. The `stream_chat_completion` coroutine runs as a task that
  pushes events into the log, not into any specific HTTP response.
- The `POST /api/chat/completions` SSE response is just a *listener*
  on that log. A `GET /api/chat/completions/resume?stream_id=...` is
  another listener that, given `Last-Event-ID`, replays unseen entries
  then continues live. Either response can detach and reattach without
  restarting the work.

**Reshape of P0 #2 (disconnect = immediate cancel).** P0 #2 wired a
500 ms watchdog that fired `cancel_event` the moment the client
dropped. P1 #10 replaces that with a **grace timer**: on detach,
start a 60 s window; reattach during the window keeps the work
running; only if grace expires does the cascade fire. So a brief
disconnect no longer kills the turn — but a truly abandoned stream
still frees its vLLM slot, which was P0 #2's whole point.

Choices worth recording:

- **Per-event id format `<stream_id>-<seq>`.** `stream_id` is a hex
  UUID with no `-`, so `rfind('-')` is unambiguous. Encoding both
  into one `Last-Event-ID` value means the client only needs one
  header on resume.
- **In-memory buffer, bounded at 1000 events.** Overflow flips a
  `truncated` flag; subsequent resumes return 410 rather than
  silently skipping events. No disk persistence — a retrieval
  restart legitimately loses in-flight streams.
- **`done` retention 60 s.** A late reconnect (slow refresh, slow
  network) can still pick up the final tail; janitor evicts after.
- **Cross-tab sessionStorage on the frontend.** Per-tab semantics;
  closing the tab loses the resume, which matches user intent.
  Ephemeral chats deliberately don't persist (nothing to restore).

## 2026-05-22: maintenance mode is a single cluster-side flag

Operator-triggered maintenance (distinct from the nightly 2-6 AM GPU
sleep) is one flag file on the cluster: `/opt/munin/data/maintenance.json`,
written by `munin-maintenance on`. `/opt/munin/data` is already
bind-mounted into the retrieval container, so `/api/status` reads the
flag with no new mount and reports a `maintenance` block.

Both UIs are pure consumers of `/api/status`: the React chat app shows
`MaintenancePage` (precedence over the `SleepingPage`), and the static
page at `chat.muninai.org/maintenance` fetches the same endpoint. One
flag, one signal, no second source of truth.

**Boundary caveat.** The flag is cluster-side; Caddy is VPS-side and
cannot read it. So there is no *automatic* proxy-level cutover (Caddy
serving a maintenance page for every route based on the flag). The
static maintenance page is the API-driven fallback for the normal case
(retrieval up, vLLM/Miro down). A full Caddy-level cutover would need a
separate VPS-side flag — deliberately left out of scope.

**Why a file, not an env var or DB row:** a file is trivially
toggled by a root shell script, needs no service restart (an env var
would), and needs no schema. `munin-maintenance` is the single owner
of the vLLM cron toggle while maintenance is on; `off` returns vLLM to
the normal 6am/2am schedule (24/7 mode, if it was on, must be
re-enabled by hand). Operator usage is documented in
backend/README.md under "Maintenance mode".

## 2026-05-21: Qwen tokenizer staged, not mounted from the model dir

The retrieval container budgets context tokens with the real Qwen3
tokenizer (P1 #8). The tokenizer ships inside the vLLM model dir
(`/opt/munin/data/models/qwen3.6-35b-a3b-awq-4bit/`), but that dir is
~19 GB of quantized weights the retrieval service has no business
reading.

Decision: `deploy.sh::stage_qwen_tokenizer` copies just
`tokenizer.json` (+ `tokenizer_config.json`, ~7 MB total) into a
dedicated `/opt/munin/data/models/qwen-tokenizer/` dir, and
docker-compose bind-mounts *that* read-only into the container as
`/models/qwen`. The container sees exactly the files it needs and none
of the weights ("correct blast radius" — rejected the simpler
whole-model-dir mount for this reason).

**Invariants:**

- `VLLM_MODEL_DIR` in `deploy.sh` must track `MODEL_PATH` in
  `scripts/vllm/start-vllm-service.sh`. A model upgrade changes the
  versioned dir name in both places; `stage_qwen_tokenizer` then
  re-copies on the next `deploy.sh retrieval`.
- The copy is deliberately best-effort. On a first-ever deploy the
  vLLM model has not been downloaded yet, so there's nothing to copy;
  `deploy.sh` warns and continues. `chat_context.approx_tokens` falls
  back to a ~4-chars-per-token heuristic when the tokenizer file is
  absent, and `_get_tokenizer` logs one warning. A later deploy (after
  vLLM's first run) stages the file and a container restart picks it up.
- The heuristic fallback undercounts code / LaTeX / JSON by 1.5-2x.
  That is the *old* behaviour, so a missing tokenizer is a graceful
  degradation, not a regression — but it does mean oversized prompts
  can still slip past the budget until the tokenizer is in place.

## 2026-05-19: P0 reliability batch (audit closeout)

Five fixes from `munin-audit.md` landed in one batch. The mechanics
are in the commits; this section captures the choices that aren't
obvious from the diff.

- **ContextVar for usage aggregation, not threaded return tuples**
  (`retrieval/usage_tracker.py`). Five vLLM call sites across three
  files (`chat_service`, `chat_context`, `agents/executor`) plus the
  forced-retry helpers all need to fold their `usage` into a single
  per-request aggregator. Threading a tuple return through
  `assemble_context` → `summarize_messages` → `_call_vllm` (and
  similarly through the agent dispatcher) is three layers of
  signature churn. The `current_sse_emitter` ContextVar already
  proved this pattern works across the same call tree; using
  `current_usage_aggregator` matches that and keeps signatures clean.
  Cost: implicit dataflow, mitigated by the fact that every fold
  goes through the same `record_usage(purpose, usage)` helper.

- **Retry tunables hardcoded, not env-driven**
  (`retrieval/vllm_client.py`). `MAX_ATTEMPTS_FOREGROUND=5`,
  `MAX_ATTEMPTS_BACKGROUND=2`, `BASE_DELAY_S=0.5`,
  `MAX_RETRY_AFTER_S=30` are constants at the top of the module,
  with a comment block documenting the worst-case wall-time math and
  pointing operators at the edit site. Env knobs are tempting but
  every additional env var is one more thing to forget on a fresh
  cluster; the breadcrumb in `backend/CLAUDE.md` makes the constants
  findable. Change them by editing + redeploying retrieval.

- **`is_concurrency_safe` defaults True**, with eight explicit
  `False` opt-outs in `mcp/schemas.py` (the artifact / memory /
  sandbox mutators). Default-False would force every new tool to
  declare the flag and would under-parallelise anything anyone
  forgot to mark. Default-True means a new mutating tool that
  forgets to set the flag *over*-parallelises until someone notices
  the race. The 31 read-only tools today (paper_search, web_search,
  calculate, ...) genuinely don't need the flag; explicit-only for
  the mutators is the smaller surface area.

- **`asyncio.shield` on save-always persistence**
  (`chat_service.py` save-always finally). The 2026-05-08 invariant
  above already runs the assistant-row persist in a `try/finally`.
  With the new disconnect watchdog, a second cancellation can arrive
  *during* the finally (process shutdown right after a client
  disconnect). Without `shield`, that cancellation can truncate the
  `chat_store.add_message` mid-row. Shielding lets the inner
  coroutine complete in the background even when our await is
  cancelled — the request is already lost, but the row lands.

- **500ms disconnect watchdog, not per-event polling**
  (`main.py::_watch_disconnect`). The audit's "minutes" symptom was a
  single long-running tool (`run_python` 30s, `deep_research`
  multi-min) blocking every event boundary. Per-event polling never
  saw the disconnect because no event flowed. A background watchdog
  sets `cancel_event`; a `cancel_listener` inside the tool runner
  cancels the in-flight task the moment the flag is set. 500ms is
  the right cadence: small enough to feel responsive within one
  user-visible turn, big enough to be cheap.

## 2026-05-08: `stream_chat_completion` save-always invariant

`backend/retrieval/chat_service.py::stream_chat_completion` wraps
its body in a `try:` / `finally:` so the assistant turn is
**always** persisted to `chats.db` once the user message has been
persisted, regardless of how the function exits. Three exit
paths matter, only one of which the code prior to 2026-05-08
handled correctly:

1. **Normal completion.** The explicit `chat_store.add_message`
   call near the end of the function runs, sets
   `assistant_persisted = True`, and the finally is a no-op.
2. **Unhandled exception** (any `Exception`). The finally fires,
   persists a marker-only assistant row, then the exception
   propagates so uvicorn / sse_starlette mark the response as
   errored.
3. **`GeneratorExit`** (client disconnect). When the FastAPI
   handler in `main.py:1297` `break`s its `async for` over the
   chat_service generator (because `request.is_disconnected()`
   returned True), `aclose()` is invoked on the generator, which
   throws `GeneratorExit` at its current `yield`. The finally
   fires; `GeneratorExit` propagates after.

`GeneratorExit` derives from `BaseException`, not `Exception`,
so `except Exception:` never catches it, and uvicorn does not
log it. That is why the regression in chat 3951063c
(contributor-d@example.org, 2026-05-08) presented as 200 OK
+ no traceback + missing assistant rows: the user closed the tab
after seeing the "Error in input stream" banner, the EventSource
closed, and persistence (which was downstream of the last yield)
never ran.

**Invariants any future refactor must preserve:**

- The `try:` / `finally:` MUST stay in place, with the explicit
  `add_message` for the assistant turn inside the `try:` block
  followed by `assistant_persisted = True`. Adding a new `yield`
  *between* the existing persist call and the
  `assistant_persisted = True` assignment would re-introduce the
  disconnect-loses-data bug.
- Any new helper that holds `acc.content` partial state must
  either fold into `final_content` synchronously or be visible
  to the finally via a known variable. The current mechanism is
  the `acc_transferred` flag, set False at the top of each turn
  and True after the existing transfer points (lines ~1628 and
  ~1638). The finally folds `acc.content` into `final_content`
  iff `acc_transferred` is False.
- Do NOT add a top-level `except GeneratorExit:` handler.
  Catching `GeneratorExit` and not re-raising is illegal in an
  async generator (Python raises `RuntimeError("async generator
  ignored GeneratorExit")`).
- The save-always `add_message` call in the finally is itself
  wrapped in `try: ... except Exception:` and logs without
  re-raising. A crash in the finally would mask the original
  exception (or `GeneratorExit`) and lose the diagnostic signal.
  Keep the swallow-and-log behavior.

Behavioural coverage lives in
`backend/retrieval/tests/test_stream_error_persistence.py`. The
13-test suite covers stream errors, partial content + error,
client disconnect (after error / after token), and unhandled
exceptions in tool dispatch / context assembly / post-loop
audits. New regressions in this area should be reproduced as
a test there before fixing.

## 2026-05-04: `shared/docs/API-CONTRACT.md` retired

The file is now a redirect stub pointing at
[`BACKEND-API.md`](BACKEND-API.md) (canonical). The original had
four broken descriptions of code reality:

- A phantom `event: metadata` SSE frame that doesn't exist in any
  code path. The real first event is `conversation`.
- A wrong `/api/status` response shape (listed `gpu`,
  `slurm_queue`, `started_at`, `uptime_seconds`, none of which
  are emitted). Real shape is
  `{vllm: {...}, services: {...}, timestamp}`.
- Wrong RAG sources (listed `notion`, `graph`; backend supports
  only `papers` + `web`).
- A fictitious `code` field in the error envelope. Real envelope
  is `{"error": {"message": "..."}}` only.

The redirect stub was kept (rather than the file being deleted
outright) so prior links from external docs and archived specs
don't 404.

## 2026-05-04: `shared/docs/BACKEND-FRONTEND-SYNC.md` archived

The original sync log declared its own self-archival criterion
("this doc gets archived once `default_tags` lands and both sides
have completed housekeeping"). Both conditions had been met. The
original file was moved to
`shared/docs/archive/SYNC-2026-04.md` as a frozen historical
record; a fresh minimal sync log replaced it at the original
path. Future cross-cut Q&A entries go in the new file; the
archived one is read-only context for understanding why various
2026-Q1/Q2 design decisions look the way they do.

## 2026-05-04: files deliberately kept (post-merge cleanup Tier 4)

A six-tier post-merge cleanup audit identified things that *look*
like dead code but were intentionally retained. Listed here so a
future "should we delete this?" question can find the answer:

- **`frontend/docs/archive/`, `backend/docs/archive/`**:
  already-archived design specs that drove shipped features (e.g.
  `FRONTEND-KNOWLEDGE-TAB.md`, `FRONTEND-REPORT-CHAT.md`,
  `MIGRATION.md`, `CHAT-PERSISTENCE.md`). Not referenced from
  live code; kept as frozen historical context. Moving them to a
  git tag was considered and rejected as more ceremony than
  benefit.
- **Manual QA scripts** under `backend/scripts/` (`smoke-test.sh`,
  `flakiness-suite.py`, `repro_vllm_hang.py`, `stress-test.py`,
  `test_compile_latex_diff_flow.py`,
  `test_delegate_persona.py`), actively useful as developer
  tooling for cluster-side debugging. Not invoked by deploys, so
  they don't appear in the runtime path; safe to ignore unless
  you're debugging.
- **Frontend build helpers** under `frontend/scripts/`
  (`gen_pwa_icons.py`, `invert_logos.py`, `create_background.py`),
  one-shot maintenance utilities used during initial setup.
  Harmless to keep; expensive to re-derive if needed again.
- **`backend/docs/archive/FRONTEND-TASKS.md`**: looks duplicate
  with `frontend/docs/archive/FRONTEND-TASKS.md` but isn't: the
  backend copy is "Frontend Tasks (handoff from munin-backend)"
  (backend's perspective), the frontend copy is "Frontend
  Integration Task List" (frontend's API-contract reconciliation).
  Both kept; the backend one is referenced from
  `chat_service.py` and `future_features.md`.

## 2026-05-04: CLAUDE.md kept lean; runbook content lives in README.md

CLAUDE.md is auto-loaded into the agent's context every session.
Keep it focused on "what you need to know to make safe edits in
this directory", coding conventions, gotchas, cross-references
to canonical docs. Anything that reads more like
"here's the project" or "here's how to deploy" goes into
README.md (or DESIGN.md), where humans + agents alike find it.

This is why the directory-level CLAUDE.md files are short
(~70 lines each) and the README.md files carry the architecture
diagrams, repo-structure trees, key paths, and deploy commands.
Don't move runbook content back into CLAUDE.md.

## 2026-04: monorepo origin (merged from `munin-backend` + `munin-vps`)

This repo started as two separate repos: `munin-backend` (cluster-side: vLLM, retrieval API, paper pipeline) and `munin-vps` (VPS-side: Caddy, auth, gateway, web UI). They were merged in 2026-04 because three concrete artifacts had drifted between them:

- Persona JSON files and logos: two copies, content drift.
- Contributor backfill script: three copies with diverging 503-handling.
- `BACKEND-FRONTEND-SYNC.md`: two half-filled copies, one per side.

The merge introduced `shared/` as the single source of truth for cross-cut artifacts: `shared/personas/` (backend deploy rsyncs into `/opt/munin/personas`; frontend fetches at runtime via `/api/personas`), `shared/config/contributors.yml` (read by the cluster ingest endpoint and the VPS backfill cron), and `shared/docs/` (canonical API contract and sync docs both sides edit).

The two deploys stayed separate. There is no top-level deploy script and no merged `docker-compose.yml`: the cluster needs sudo + systemd, the VPS is docker compose, different lifecycles, intentionally not unified.

Other artefacts of the merge worth knowing:

- The React UI was at `frontend/frontend/` pre-merge, renamed to `frontend/webui/` to remove the confusing nesting.
- `backend/personas/` was canonical; `frontend/cluster/personas/` was stale and dropped.
- The pre-merge `BACKEND-FRONTEND-SYNC.md` is archived at `shared/docs/archive/SYNC-2026-04.md` (see the 2026-05-04 entry above).

Post-merge cleanup landed in six tiers (commit `e419fe6` plus the 2026-05-04 entries above). The original step-by-step merge plan (`REFACTOR-PLAN.md`) was deleted in 2026-05 once this section captured the outcome; `git log --diff-filter=D -- REFACTOR-PLAN.md` will resurrect it if needed.
