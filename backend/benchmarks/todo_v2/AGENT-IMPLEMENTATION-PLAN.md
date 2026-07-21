# Munin Specialised Agents — Implementation Plan + Decision Log

## Context

The research assistant is a single model in one flat MCP tool loop. Track D
(LitQA2) showed it abstains where the answer is present: 16/20 abstentions had
read the source paper, and a full-text oracle flipped 9/11 to correct (0.82).
The bottleneck is plumbing (`read_paper` summarises-and-discards raw text), not
reasoning. `munin-agent-design-v2.md` proposes four named agents (Paper, Search,
Compute, Deep Research) that pass handles not payloads and always declare which
guarantee they gave.

This plan validates design-v2 against the actual code, adjusts where reality
differs, and is structured as a **decision log to iterate through together**.
Nothing is built yet.

**Key correction from the user (supersedes design-v2 §2/§4.4):** Deep Research is
a **specialised, very long-running agent querying the running vLLM**, NOT a SLURM
job with kickoff/poll/handle. That removes the job-store/sbatch path entirely and
makes contention against chat's vLLM the central operational question.

---

## Part 1 — Design-v2 vs repo reality (validated by code investigation)

Headline: **more already exists than design-v2 assumes**, so this is mostly
consolidate + instrument. Evidence cited as `file:line`.

### Already built (build ON it)
1. **Nested-agent framework exists.** `invoke_agent` -> `agents/executor.py`
   runs a real nested vLLM tool loop with per-agent hard tool allowlists.
   `config/agents.yml`: `research_orchestrator` (12 tools, 10 iter / 30 calls /
   300s), `code_checker`, `writing_agent`. This is the natural substrate for a
   long-running Deep Research agent.
2. **Compute is ~70% built.** `run_python` (sandboxed), `calculate` (sympy),
   `compile_latex` exist. Sandbox is a separate container on `sandbox-net` with
   **no internet** (`docker-compose.yml:243-248`). Binary artifacts: on-disk in
   sandbox + `register_sandbox_artifact` + `/api/artifacts/{cid}/{aid}` proxy
   (`main.py:2525`) + `artifact_created` SSE (`chat_service.py:2410`).
3. **Corpus scoping exists** and matches design-v2's model: `#tag` chips
   (topic/group/contributor) -> `current_query_tags` ContextVar -> Qdrant filter
   on the shared `PAPERS_COLLECTION` (`papers.py:34-82,268-316`). "Label set, not
   partition." Surface is `#tag` chips, not an `/elgeti` slash command.
4. **compare_papers** is the fat-tool precedent: parallel `read_paper` fan-out +
   one synthesis call + `failed[]` (`compare_papers.py:186-271`).
5. **Store is not greenfield.** SQLite `/data/chats.db` (WAL+FTS5) backs chats,
   projects, memory, **plans (`conversation_plans` table)**, and **artifacts**.
6. **SSE resume substrate exists** (`stream_registry.py`) for buffered replay on
   reconnect. Relevant to delivering a long-running agent's progress.

### Confirmed accurate
- One model, one Slurm-launched vLLM (`qwen3.6-35b-a3b`). No model-per-mode.
- `max-model-len = 65536` (single-GPU) -> a ~37k-token paper fits, chunking is a
  rare overflow fallback.
- `--enable-chunked-prefill` default ON -> chat-freeze-on-long-prefill already
  mitigated (disabling it OOMs, per a closed experiment).
- **vLLM walltime is a liveness dependency**: Slurm sbatch, `--time=20:00:00`,
  cron start 6am / stop 2am (`schedule-vllm.sh`). A long agent straddling the 2am
  stop dies. This matters MORE now that Deep Research is in-process, not a job.
- VPS never mounts the box store (reverse SSH tunnel, HTTP proxy).

### Aspirational / net-new
- **No egress control.** `egress`/`corpus_scope` do not exist; network is
  unconditional per-tool httpx. Substrate: sandbox already net-off; OA downloads
  already cache to a separate namespace (`/data/papers_cached`).
- **No request priority** (`vllm_client.py` sets none; serve is fcfs).
- **prefix-caching OFF** (`--enable-prefix-caching` unset) - a free cost lever.
- **No structured agent trace** (Principle 6 unimplemented; `usage_tracker.py` is
  in-process only). The genuinely-new load-bearing store piece.
- No DuckDB anywhere.

### Investigation answers to design-v2's open decisions
- **#9 (relational DB?):** No server DB. But SQLite exists (incl.
  `conversation_plans`). "No new database" holds; DuckDB stays optional.
- **#10 (vLLM config):** captured. `max-num-seqs = 2` (single-GPU default) is the
  sharpest constraint. gpu-util 0.90, fcfs, no client priority, `--time=20h`.
  TP2 variant = 131072 / 8 seqs but blocks all batch jobs.
- **#11 (VPS on-prem?):** No, cloud Hetzner CAX21. RQ-M1 needs scoping.

### The constraint that shapes everything now: `max-num-seqs = 2`
A long-running Deep Research agent hitting the SAME vLLM as chat competes for
only two batch slots. Sequential Paper reads (one request at a time) plus a
client-side concurrency cap plus the user-facing toggle are the levers.
Prefix-caching (free) and request-priority (needs serve flag + client change)
are the tuning knobs. See D4.

---

## Part 2 — Recommended architecture (given reality + the Deep Research correction)

1. **Source / Search / Compute = fat MCP tools** (the `compare_papers` pinned
   fan-out+reduce pattern). Pinned pipelines keep the auditability the diagnosis
   demanded. (The read agent is named **`source`**, D14.)
2. **Deep Research = a long-running, semi-pinned nested agent** built on
   `agents/executor.py` (extend `research_orchestrator`): plan-as-data-structure
   loop, larger budget, running against the live vLLM. Runs as a **detached
   in-process task**, streams progress over SSE while connected, resumes from the
   SQLite-persisted plan on reconnect or crash, and delivers the final composite
   as a **markdown artifact**. No sbatch, no job store, no poll. Concurrency is a
   configurable cap (default 1). Its eval metric is an external research benchmark
   (D13); it ships last.
3. **Consolidate the three research surfaces:** retire the sync `deep_research`
   tool's role into "the outer loop does shallow research with Search+`source`";
   fold `research_orchestrator` into the new Deep Research; leave the disabled
   SLURM deepresearch pipeline retired (not the chosen path).
4. **Store: reuse before adding.** Plan/notes -> SQLite; traces -> append-only
   JSONL (grep-able), DuckDB only if the eval needs SQL aggregates; derived blobs
   -> existing `/data/papers_cached` + pdf dirs.
5. **Contention:** sequential `source` reads + client-side semaphore + the toggle;
   enable prefix-caching; request-priority later if needed.
6. **Provenance controls built in from day one (D29):** `egress` and
   `corpus_scope` are honoured by Source/Search/Compute; a certification run forces
   `egress:off` + `corpus_scope:curated_only`. Up-front scope add vs a
   Source-first-only increment.

---

## Part 3 — Build order

```
0. Trace spine + thin store   NEW: JSONL trace writer + read helper.
                              REUSE: SQLite (conversation_plans) for plan/notes.
                              + egress/corpus_scope control skeleton (D29).
1. Source agent               REUSE: read_paper extraction, document_store.chunk_text,
                              database.get_bge, compare_papers pattern.
                              NEW: source(refs[], mode), tagged-ref + confab-DOI trace,
                              four-way outcome envelope, qa on FULL text, extract
                              preview->schema, honour egress/corpus_scope.
                              FOLDS IN: read_paper + compare_papers.
2. Search agent               REUSE: paper_search (+#tag), semantic_scholar_search, web_search.
                              NEW: dedup-on-alias, source_type tiering, coverage_note
                              (scoped+unscoped counts, D18), thin_evidence, egress-aware.
3. Compute agent              REUSE: run_python, net-off sandbox, register_sandbox_artifact,
                              artifact proxy + SSE, calculate, compile_latex.
                              NEW: verifier ladder + verify_level, budget tiers,
                              code+data+figure bundle, BLAS pinning, self-test guard.
4. Deep Research              REUSE: agents/executor.py, conversation_plans, stream_registry.
                              NEW: plan loop, batched screener, funnel (config), depth-1
                              snowball, read_depth citations, detached-task + SSE-progress +
                              resume, artifact delivery, structured contradiction-aware
                              notes, external-benchmark eval. Ships last.
```

---

## Part 4 — First increment: trace spine + Source agent

- **Trace spine (thin):** append-only JSONL, one record per agent call (resolved
  sources, LLM calls, decisions, abstain/failure reason). No DuckDB yet. Include
  the `egress`/`corpus_scope` control skeleton so provenance is recorded from the
  first call (D29).
- **Source agent (fat MCP tool):**
  `source(refs[], mode: summary|qa|extract|compare, question?, focus?, schema?)`.
  Interior pinned: `resolve -> extract -> one LLM call` (full text; chunk only on
  overflow). Reuse `read_paper.py` extraction, `document_store.chunk_text` (512/50)
  for overflow only, `database.get_bge` if chunk-ranking is ever needed.
  Envelope: `ref_resolved`, `source{origin,url,extraction_method}`,
  `outcome: resolved|ambiguous|extraction_failed|not_found|out_of_scope`, `trace`,
  mode body (`qa -> {answer, supporting_quotes[], confidence}`;
  `extract -> {handle, schema, n_rows, preview[5], provenance}`, handle-only, with
  the model finalizing the schema from the preview, D17).
  Folds in `read_paper` (summary) and `compare_papers` (compare, over `source`-qa).
  Abstain: quote-or-`not_found`.

---

## Part 5 — Verification

- **Source eval seam (already built):** re-run the 20-question over-abstention set
  through `source(mode=qa)`; measure abstain->correct vs the 0.82 oracle. Harnesses:
  `munin_bench/ablation/diag_overabstain.py`, `diag_fulltext_oracle.py`.
- **Regression guard:** Track D agentic arm on 100 for precision/latency.
- **Trace check:** every call emits a readable trace; confab-DOI audit is a grep.
- **Cert wiring:** fold the Source metric into `run_all` + `certify.py` before live.
- No deploy in this increment (varghele runs `deploy.sh retrieval`); bench runs use
  the pipeline venv against live chat `127.0.0.1:8080` and vLLM `127.0.0.1:8000`.

---

## Part 6 — MASTER DECISION LOG (iterate through these)

Status: DECIDED / REC (my recommendation, needs your yes) / OPEN (genuinely
undecided). We walk these top-down; I record each resolution here as we go.

### A. Architecture / gating
- **D1. Deep Research execution model.** `[DECIDED - user]` Long-running in-process
  agent against the live vLLM. No SLURM job, no poll.
- **D2. Agent implementation pattern.** `[DECIDED]` Paper/Search/Compute = fat MCP
  tools (compare_papers pattern); Deep Research = extended `executor.py` agent.
- **D3. Fate of existing research surfaces + framework.** `[DECIDED]` Consolidate:
  keep `invoke_agent`/`executor` as Deep Research's substrate; retire the sync
  `deep_research` tool + `research_orchestrator` into the new roster; leave the
  disabled SLURM pipeline retired. One research path.
- **D4. vLLM contention (shares chat's vLLM, `max-num-seqs=2`).** `[DECIDED]`
  Sequential Paper reads + client-side semaphore + user toggle + enable
  prefix-caching. Request-priority deferred until measured (see D27).
- **D5. Store backing.** `[DECIDED]` Plan/notes -> SQLite (`conversation_plans` or
  a sibling table); traces -> append-only JSONL (DuckDB read-layer only if the
  eval later needs SQL aggregates).
- **D6. Long-agent delivery + resume.** `[DECIDED - hybrid; details in D6a-c]`
  Detached in-process asyncio task (survives client disconnect); streams progress
  over SSE while connected; `stream_registry` replays on reconnect; plan+notes
  persist to SQLite continuously so a reconnect shows live state AND a crash can
  resume. Not a SLURM job.
  - **D6a.** `[DECIDED]` Final composite delivered as a **markdown artifact**
    (artifact_store + `artifact_created` SSE; citations + read_depth travel with it).
  - **D6b.** `[DECIDED]` On interruption, **resume from the persisted plan** (reload
    plan+notes, re-run only open sub-questions).
  - **D6c.** `[DECIDED]` **Configurable N concurrent DR runs, default 1**, enforced
    by a client-side semaphore; others queue.

### B. Deep Research specifics
- **D7. Plan is a data structure** `{id, sub_question, status, notes[], evidence_refs[]}`,
  persisted to SQLite. `[DECIDED]` Yes.
- **D8. Screener** batched over abstracts, tuned for recall, logs every drop. `[DECIDED]`
- **D9. Funnel widths are config**, not agent-chosen (~100 -> ~25 reads). `[DECIDED]`
- **D10. Snowball depth-1** (seed refs only, back through the screener); depth-2
  after measurement. `[DECIDED]`
- **D11. `read_depth: full_text|abstract|snippet` per citation**; notes carry
  quote+ref+sub_question_ids. `[DECIDED]`
- **D12. Note granularity.** `[DECIDED]` Structured notes
  `{claim, value?, unit?, quote, ref, sub_question_id}` -> contradiction detection
  is mechanical (conflicting claims on the same sub_question_id).
- **D13. Research certification gate.** `[DECIDED]` Adopt an external benchmark
  (AutoResearchBench / ScholarQA-CS2 / ArxivDIGESTables) as DR's metric. Note: this
  is real up-front eval-harness work; DR still ships last so the benchmark can be
  built while agents 1-3 land.

### C. Paper / Search  (the read agent is named `source`, see D14)
- **D14. Agent name.** `[DECIDED]` **`source(refs[], mode)`** - neutral across
  corpus papers, preprints, and web sources; avoids `paper(blog_url)` being a lie.
  (Renames the "Paper agent" throughout to the **Source agent**.)
- **D15. Chunking.** `[DECIDED]` Full-text default; chunk only on overflow,
  justified against the no-chunk baseline on cost.
- **D16.** `[DECIDED]` Four-way(+`out_of_scope`) outcome + tagged-ref resolution +
  confabulated-DOI trace audit.
- **D17. `extract` schema authoring.** `[DECIDED]` **Model proposes from a preview**:
  `source` first returns a 5-row preview of what it found, then the model/user
  finalizes the schema from real data. Autonomous (usable inside Deep Research),
  avoids inventing structure blind; costs a round-trip. Dovetails with extract's
  `preview[5]` return.
- **D18. Scoped abstention.** `[DECIDED]` **`coverage_note` from Search**: run the
  query scoped and unscoped (same query, cheap), report "X in scope, Y
  consortium-wide". Source's outcome stays four-way; the scope signal lives in
  Search.
- **D19. Sub-corpus surface.** `[DECIDED]` Keep `#tag` chips (already built); add
  `/elgeti` slash-command only if wanted (plumbing supports it).

### D. Compute
- **D20. Verifier ladder** (parse/lint/import/smoke) + `verify_level` in envelope;
  sandbox boundary between lint and import. `[DECIDED]`
- **D21. Budget tiers** `quick` (~15s, default) / `compute` (~5min). `[DECIDED]`
- **D22. Bundle return** (code + data.csv + figure.png; script reads data from its
  own dir; byte-identical to what ran; seeded RNG). `[DECIDED]`
- **D23. Sandbox image.** `[DECIDED]` One broad pinned scientific image, no pip
  install ever; split only on a real hard dependency conflict later.
- **D24. GPU protection = BLAS thread pinning** (`OMP/OPENBLAS/MKL_NUM_THREADS`) +
  cgroup quota, throttle not on/off. `[DECIDED]`
- **D25. Self-authored-test guard** (tests from spec or a separate generation step,
  never the repair loop). `[DECIDED]`

### E. Infra / ops
- **D26. Enable prefix-caching.** `[DECIDED]` Yes (free cost lever; part of D4).
- **D27. Request-priority.** `[DECIDED-deferred]` Build only if measured contention
  needs it (serve `--scheduling-policy priority` + `vllm_client.py` `priority`).
- **D28. Interactive queue latency.** `[DECIDED]` Mostly moot (no SLURM research
  job); keep Compute `quick` fast, watch sandbox contention.
- **D29. Egress + corpus_scope controls.** `[DECIDED]` **Build now, with the
  agents** - `egress: off|oa_only|full` and `corpus_scope: curated_only|
  curated+oa_cache|all` enforced from day one (Source/Search/Compute honour them;
  a cert run forces `egress:off` + `corpus_scope:curated_only`). Reuses the
  net-off sandbox + separate OA-cache namespace substrate. Up-front scope add.

### F. Proposal / claims
- **D30. RQ-M1 claim scoping.** `[DECIDED]` State plainly: corpus + all inference
  stay on-prem (cluster); chat, uploads, artifacts transit a cloud frontend VPS
  (TLS terminates there). Accurate + defensible; one sentence in the proposal.

---

## One-line summary

Design-v2's four-agent shape holds, but the repo already has the agent framework,
the sandbox, corpus scoping, and a SQLite store, so this is consolidate +
instrument. Deep Research becomes a long-running detached in-process agent against
the live vLLM (not a job), which makes vLLM contention (D4) the central
operational question. All 30 decisions are resolved; next we build the trace spine
+ the **Source agent** first, measured against the 0.82 oracle.
