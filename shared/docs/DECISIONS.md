# munin: design decisions

Short rationales for non-obvious choices made over the lifetime of
the monorepo, captured so future readers don't have to re-derive
them. Companion to git history (which has the *what*).

Add a new section when a decision is non-obvious from code +
commit messages alone. Don't add a section for things that
self-document (renames, refactors, bug fixes).

---

## 2026-10: behaviour defaults are production; identity has no default

For the public release (docs/RELEASE-PLAN.md, Phase 2), the 2026-08 rule
below is narrowed rather than reversed. It is about *behaviour*: the paper
encoder, the collection, the context window. Those still default to what
production runs, because a fresh install should behave like the measured
system.

*Identity* is the opposite case. The domain, the public URLs, the cookie
scope, the CORS origins, the contact and sender addresses, the cluster's name
in the system prompt, and the database secrets used to default to the
reference deployment's values (`muninai.org` in about forty places). A second
group that forgot one variable got a stack that quietly linked to, logged in
against, or identified itself as someone else's instance, and nothing failed.

So identity has no default:

- `MUNIN_DOMAIN` is required. Compose (both files) refuses to start without
  it and `deploy.sh` refuses to deploy; every URL derives from it
  (`backend/retrieval/site_config.py`, the auth service, `{$MUNIN_DOMAIN}` in
  the Caddyfile, `{{env "MUNIN_URL_*"}}` in the static pages). The chat UI
  derives its sibling URLs at runtime from the host it is served on.
- `NEO4J_PASSWORD` and `SEARXNG_SECRET` lost their fallback values.
- A contact address is never invented: the polite-pool `mailto` is omitted
  when `MUNIN_CONTACT_EMAIL` is unset.
- The personas carry placeholders. With the reference env they render the
  measured prompts byte for byte (`tests/test_persona_render.py`), so the
  paper's configuration is unchanged; `deploy.sh` also requires
  `MUNIN_CLUSTER_NAME`, because an unset one would change that prompt.

The reference deployment states its values explicitly in cluster.env and the
VPS `.env`. `check_compose.py` pins both directions: the reference values
resolve exactly as production did, and with any other domain no `muninai.org`
survives in the resolved config.

Two bugs the inventory turned up, both silent: the citation audit's
phantom-link pattern was the literal `search.muninai.org` (a no-op on any
other domain), and the post-login redirect accepted any URL starting with
`http` (an open redirect). Both now follow the configured domain.

## 2026-09: the backbone is one file; sampling belongs to the model, not the persona

**Decision.** Everything model-specific lives in
`backend/config/models/<slug>.env` (checkpoint, served name, quantization,
both vLLM parsers, `LLM_THINKING_MODE`, `LLM_REASONING_EFFORT`, and the
sampling profile `SAMPLING_DEFAULT` / `SAMPLING_CODE`). `deploy.sh model
activate <slug>` copies it to `/opt/munin/config/active-model.env`, which the
SLURM scripts, `deploy.sh retrieval` and the embedding-map unit all read.
`shared/personas/*.json` name only a `sampling_class` (`default` or `code`)
and no longer carry the five sampling numbers.

**Why.** The README's "Switch LLM model" checklist named twelve places and the
2026-09-15 audit found eighteen; two of them (the tokenizer staging dir in
`deploy.sh`, and a `VLLM_MODEL_NAME` hardcoded rather than substituted in
compose) failed silently. The gpt-oss-20b cross-lab run needed two more
per-model facts that had no home at all: how a sub-task turns reasoning off
(Qwen `enable_thinking: false`; gpt-oss cannot disable reasoning and takes
`reasoning_effort: low`) and the vendor's recommended sampling (OpenAI:
`temperature 1.0, top_p 1.0`, none of Qwen's `top_k 20 / presence 1.5`).
Sampling had lived in the personas, which are shared by every backbone the
stack serves at once; leaving it there would have meant two instances on two
models sampling one of them wrong. The built-in fallbacks reproduce the
Qwen3 set exactly (`retrieval/tests/test_sampling_profile.py` pins each
shipped persona against its pre-migration numbers), so the reference
deployment's request bodies did not change.

**Consequences.** A model change is `deploy.sh model activate` plus a vLLM
restart; a new model is a new profile file. The bench reads the same env
names. Sampling is part of the system under test: every committed number for
a backbone was produced under that backbone's profile, and editing a profile
invalidates its numbers. The VPS gateway proxies `/v1/models` to the
backend's `/api/models` instead of carrying its own literal.

## 2026-09: a multimodal turn has two texts, and they are not interchangeable

When the composer sends an attachment, `messages[-1].content` is an
OpenAI-style block list rather than a string. Two derived strings come out of
it inside `chat_service.stream_chat_completion`, and picking the wrong one is
a silent bug in both directions, so `content_text()` is deliberately called
twice with different inputs:

- **typed text** = `content_text(raw_content)`, taken BEFORE
  `_resolve_user_content_images`. This is what the user actually typed. It
  feeds the router and `generate_title`.
- **persisted text** = `content_text(resolved_content)`, taken AFTER. This is
  typed text PLUS any attached document's full inlined body. It goes to
  `messages.content`, which is FTS5-indexed, so the document stays searchable
  and survives a reload.

The direction that bites is using persisted text for the router or the title.
The resolver inlines a whole document as a text block, so a .docx turn's
persisted text runs to tens of thousands of characters (chat b0909633:
32,029). `chat_context.generate_title` interpolates its first argument with no
truncation, so a large PDF would aim the title call straight at the 65,536-token
context limit, and the router would embed the document instead of the question.

The other direction is what actually shipped and stayed broken for two months:
passing the raw list to either one raises inside a `try/except`, which
demoted every attachment turn to the pinned profile without a trace. See
`backend/docs/archive/KNOWN-BUGS-resolved.md` entry 8.

A related consequence, in the same code: when a fallback needs a persona id,
use the id of the persona actually being run, never `pin_id`. `pin_id` is
`None` by design under auto-route, meaning "the user pinned nothing", and
assigning it to `persona_id` writes NULL onto the message row and into
`current_persona`. That NULL is what made the failure invisible.

---

## 2026-08: citation grounding - supply the metadata, then audit the claim

A user reported two papers attributed to authors who had nothing to do with
them (chat 61443530): PMC2098716 credited to "Bazzi et al." (really Le Guyader
et al.) and PMID 23274277 to "Gonzalez-Rodriguez et al." (really Loura, do
Canto, Martins). Investigation found a third, uncaught: "Mun et al." for
10.1021/jp061300r (really Repáková et al.).

None of the three names appeared in any tool result. The cause was a metadata
hole, not a retrieval error: the web tier returns a title, a URL and a snippet
and no author list (176 of 176 web hits in a month), `web_fetch` returns an
LLM summary of the page body with the metadata already discarded (20 of 26
PubMed/PMC fetches yielded no usable metadata), and PMC serves us a bot-check
page that the summarizer described as though it were the article. Given a
title and an identifier but no authors, the model supplied plausible names
from memory.

Two decisions follow from that.

**The metadata is now fetched structurally, never inferred.** `bibref.py`
resolves any DOI / PMID / PMCID / arXiv id through corpus, Semantic Scholar,
Crossref and NCBI eutils, `web_search` attaches the result to any hit whose
URL carries an identifier, and `web_fetch` parses citation meta tags out of
the HTML before the summarizer ever runs. No LLM is in any of these paths, so
there is nothing to hallucinate. Where metadata genuinely cannot be had, the
result carries an explicit `authors: null` plus `metadata_available: false`:
a MISSING field reads to a model as "not applicable", an explicit null reads
as "unknown, do not guess", and that difference is the point.

**The audit annotates, it does not rewrite.** `audit_citation_claims_in_content`
flags any "X et al." whose surname appears in no tool result from that turn,
prefixing a `[backend warning]` block, exactly like the two phantom-URL audits
it sits beside. We deliberately did NOT add a repair generation: the same
persona already carried "Do NOT fabricate paper titles, authors, abstracts or
DOIs" and that rule is what failed here, so the fix had to be mechanical
rather than another instruction, and the annotation keeps the human in the
loop instead of hiding the error behind a silent retry. `munin_citation_claims_total`
tracks grounded vs ungrounded so the rate is measured rather than waiting for
the next user report.

Matching is word-boundary, not substring, for a specific reason: the third
fabrication ("Mun") escaped an earlier hand audit because "Mun" is a substring
of "Munin", which appears in every tool payload.

## 2026-08: deploy defaults are production, not the pre-migration state

Two settings had drifted from "what the cluster runs" to "what the
cluster ran before the last migration", in both cases because the real
value lived only in `/opt/hugin/config/cluster.env`, which is not in
git.

**Paper encoder.** `PAPER_ENCODER` / `PAPERS_COLLECTION` defaulted to
`specter` / `papers` in `database.py` and in compose, while production
had run `bge-large` / `papers_bge` since the 2026-07 cutover. Anything
started without cluster.env (a fresh host, a local run, the eval
harness) silently searched the retired 768-d corpus and looked like it
worked. The defaults now match production; rollback is the env flip in
the other direction. Because the two vars are a pair, a half-flip is now
a boot failure (`database.verify_paper_space()` compares encoder width
against the collection's real vector size) and a deploy failure
(`deploy.sh verify` asserts the pairing over `/api/status`). Related:
`deploy.sh models` stages the bge-large weights, which nothing did
before, so the container no longer falls back to pulling 1.3 GB from
HuggingFace on each start.

**Deep Research.** `deploy.sh all` still provisioned the MiroThinker
daemon and offered its 17 GB download for a feature disabled in 2026-07,
using a `huggingface-cli` binary that no longer exists in the vLLM venv.
The legacy target is now excluded from `all`, the download is opt-in via
`--with-model`, and `DEEPRESEARCH_ENABLED` is finally passed through
compose so the documented re-enable path actually works. The Deep
Research users see is the in-process agent behind `/api/research/*` and
was never part of any of this; `deploy.sh verify` now probes it.

The general rule this encodes: when a migration completes, move the
defaults, do not leave them pointing at the rollback. Secrets stay in
cluster.env; topology should not.

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
and `delegated`/`persona_changed` SSE). The migration (`docs/paper-track/`, steps A1-A4)
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
  *Superseded (2026-10): the buffer is now capped at 100000 events and
  8 MiB (`MAX_LOG_EVENTS` / `MAX_LOG_BYTES` in
  `backend/retrieval/stream_registry.py`).*
- **`done` retention 60 s.** A late reconnect (slow refresh, slow
  network) can still pick up the final tail; janitor evicts after.
- **Cross-tab sessionStorage on the frontend.** Per-tab semantics;
  closing the tab loses the resume, which matches user intent.
  Ephemeral chats deliberately don't persist (nothing to restore).
  *Superseded (2026-10): since background turns (2026-07 entry above)
  the resume pointer lives in localStorage, so it survives closing the
  tab.*

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

*Superseded (2026-10): the function is now `deploy.sh::stage_tokenizer`,
and the `VLLM_MODEL_DIR` literal is gone. The model directory comes from
the active model profile (see "2026-09: the backbone is one file"), so
there is no longer a second place to keep in sync.*
- The heuristic fallback undercounts code / LaTeX / JSON by 1.5-2x.
  That is the *old* behaviour, so a missing tokenizer is a graceful
  degradation, not a regression — but it does mean oversized prompts
  can still slip past the budget until the tokenizer is in place.

## 2026-05-19: P0 reliability batch (audit closeout)

Five fixes from an internal harness audit (not published) landed in one batch. The mechanics
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
  cluster; the breadcrumb in `backend/README.md` makes the constants
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
  `test_compile_latex_diff_flow.py`), actively useful as developer
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

> **Note (2026-08-04):** the `CLAUDE.md` files are no longer part of the
> public repository. They remain in the working tree for local agent
> sessions and are gitignored. The decision below is kept because it
> explains why the READMEs carry the runbook content they do.

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

*Superseded (2026-10): `shared/config/contributors.yml` is no longer
tracked. It is gitignored and only `shared/config/contributors.yml.example`
is in the repo. Contributors, group leaders and admins are managed in the
chat UI's admin panel; the cluster pulls the list from the auth service
every 5 minutes (`backend/retrieval/contributors_sync.py`,
`CONTRIBUTORS_SYNC_URL`), and `deploy.sh` only seeds
`/opt/munin/data/contributors.yml` when it is absent.*

The two deploys stayed separate. There is no top-level deploy script and no merged `docker-compose.yml`: the cluster needs sudo + systemd, the VPS is docker compose, different lifecycles, intentionally not unified.

Other artefacts of the merge worth knowing:

- The React UI was at `frontend/frontend/` pre-merge, renamed to `frontend/webui/` to remove the confusing nesting.
- `backend/personas/` was canonical; `frontend/cluster/personas/` was stale and dropped.
- The pre-merge `BACKEND-FRONTEND-SYNC.md` is archived at `shared/docs/archive/SYNC-2026-04.md` (see the 2026-05-04 entry above).

Post-merge cleanup landed in six tiers (commit `e419fe6` plus the 2026-05-04 entries above). The original step-by-step merge plan (`REFACTOR-PLAN.md`) was deleted in 2026-05 once this section captured the outcome; `git log --diff-filter=D -- REFACTOR-PLAN.md` will resurrect it if needed.
