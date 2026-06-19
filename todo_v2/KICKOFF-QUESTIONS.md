# Kickoff questions — to settle before building

Gathered from `IMPLEMENTATION-HANDOFF.md` §"Questions to confirm" (which
inherits `RETRIEVAL-EVAL-SPEC.md` §5), plus discrepancies found during the
2026-06-11 repo verification pass. One section per question: context, what
the codebase says, options, recommendation, and a `DECISION:` line to fill
in. Once all decisions are filled, this document's outcomes get folded into
the spec (separate commit, per the "code wins, update the spec" rule) and
this file is marked resolved.

Status: ⬜ open · ✅ decided

---

## Q1 ✅ DECIDED — Hybrid-search weights: which (α, β) is the "live" baseline?

**The question.** Track A evaluates the production retriever as the primary
baseline. The spec assumed live = (0.8, 0.2) from `search.html` and
paper = (0.7, 0.3), and mandates evaluating both plus a sweep. Which pair
is the canonical "live" configuration?

**What the codebase says.** The spec's framing is inverted from reality:

- `backend/retrieval/models.py:118` — `HybridSearchRequest` API defaults
  are **`vector_weight = 0.7`, `citation_weight = 0.3`**, unchanged since
  the file was first committed.
- `frontend/static/search/index.html:410` — the search UI hardcodes and
  sends **0.8/0.2**, overriding the API default, since the initial
  monorepo merge.
- The search UI is the **only** caller of `/search/hybrid` in the whole
  repo. No backend code calls it; the chat agent's `paper_search` bypasses
  it entirely (see Q3). So every hybrid search that has actually executed
  in production ran at 0.8/0.2; the 0.7/0.3 API default is real but
  dormant.
- Weights are normalised before use (`main.py:3298`), so only their ratio
  matters: 0.8/0.2 = 4:1, 0.7/0.3 = 2.33:1. The two configs differ more
  than the raw numbers suggest.

So three values exist: live-as-exercised (0.8/0.2), live-default (0.7/0.3),
paper claim (0.7/0.3). The spec's own tie-break rule ("the FastAPI default
wins") plus the paper claim both point at 0.7/0.3.

**DECISION (2026-06-17): Option A — converge production on 0.7/0.3.**
Make 0.7/0.3 the single primary baseline for Track A (it is the API
default AND the paper claim, so live == paper after this change). A
one-line frontend change drops the hardcoded `vector_weight`/
`citation_weight` from the search UI request body so it inherits the API
default; this eliminates the live-vs-paper discrepancy at the source
rather than disclosing it in prose. The sweep still evaluates 0.8/0.2 as
a grid point (labelled "prior search-UI setting") to characterise what the
change costs/gains; never auto-tuned. Constraints: (1) the frontend fix is
a separate production change, NOT part of the eval build, and must land
BEFORE Track A's local-pool evaluation runs so the evaluated config and the
deployed config match; (2) `compute_citation_score` and the normalisation
are still copied verbatim from `main.py` per spec §3a. Note for the spec
update: the "0.8/0.2 live vs 0.7/0.3 paper" framing in RETRIEVAL-EVAL-SPEC
§0 is inverted and must be rewritten to "0.7/0.3 production (post-
convergence); 0.8/0.2 retained as a sweep point."

---

## Q2 ✅ DECIDED — Chat SQLite store location (Phase 4a query extraction)

**The question.** Phase 4a mines real user queries from the chat store.
The spec guessed `/var/lib/munin/chat.sqlite`.

**What the codebase says.** `backend/retrieval/chat_store.py:18` —
`CHATS_DB_PATH`, default `/data/chats.db` in-container; the retrieval
container mounts `/opt/munin/data:/data` (`backend/docker/docker-compose.yml:141`),
so on hugin the file is **`/opt/munin/data/chats.db`** (confirmed on disk:
16 MB main + a live ~5.5 MB WAL, root-owned, this session can read it).
The DB is **WAL mode**, so readers don't block the live writer. Schema
(`messages`): `role, content, tool_calls (JSON, embeds full tool result
incl. DOIs + totals), persona, created_at`. Both the retrieval-bearing
filter and `n_papers_retrieved_originally` read straight out of the
`tool_calls` JSON; no replay of the chat pipeline is needed.

**DECISION (2026-06-17): confirmed.** Path is `/opt/munin/data/chats.db`.
Phase 4a reads a **consistent read-only snapshot** taken with
`sqlite3 /opt/munin/data/chats.db "VACUUM INTO '/tmp/chats_snapshot_<date>.db'"`
(folds the WAL in; never extract from a bare file copy, which would be
stale, and never write to the live file). Extraction runs against the
snapshot.

**DEFERRED to Phase 4 — local-pool data-starvation flag (raised 2026-06-17,
decision: note now, resolve at Phase 4).** The live data is much thinner
than the spec's pipeline assumes, because the deployment is young
(~2 months of traffic: 2026-04-13 → present, 348 conversations, mostly
`chat` persona — 313 chat / 21 research / 14 code):

| Quantity | Count |
|---|---|
| Total user messages | 774 |
| Survive length + non-slash filter | 652 |
| Assistant turns calling `paper_search` (local corpus) | **56** |
| Assistant turns calling any search (incl. `semantic_scholar`) | 111 |

The spec's Phase 4a filter ("skip turns whose next assistant made no
`paper_search` call") yields ~56 retrieval-bearing queries — below the
Phase 4 **≥100-query gate**, before varghele's curation (which only shrinks
it). The kappa and BM25 gates downstream need that statistical power.
**This is the spec's "stop and tell varghele below 100" moment, surfaced
early.** Options to weigh at Phase 4 (not decided now):

1. Broaden the filter to count `semantic_scholar_search` turns whose hits
   overlap the local corpus (recoverable signal; pure-S2 hits dilute the
   local-pool premise since their DOIs are often not in `papers`).
2. Accept an underpowered pool, report wider CIs, be honest about n.
3. Let chat traffic accumulate before running Phase 4 (couples a paper
   gate to deployment growth).
4. Supplement with varghele-authored corpus queries, clearly labelled as
   not log-mined.

Likely a blend of (1) + (4). Revisit when Phase 4a actually runs.

---

## Q3 ✅ DECIDED — NEW: what is "the production retriever" for Track A?

**The question (the biggest one).** The spec describes the production
retriever as SPECTER dense + citation re-rank (`/search/hybrid`). But the
chat pipeline's `paper_search` MCP tool (`backend/retrieval/mcp/tools/papers.py`)
does **not** call `/search/hybrid`. The two paths diverge on four axes:

| Axis | `/search/hybrid` (search UI only) | `paper_search` (the chat agent) |
|---|---|---|
| Citation signal | yes (the α/β re-rank) | **none** (no Neo4j) |
| Query handling | single query | **LLM fan-out to 3-5 variants + vote-fusion** |
| Determinism | deterministic | **LLM expander at temp 0.5** (`query_expansion.py`) |
| Scoping | none | tag-scoped via ContextVar |

`paper_search` ranking (`papers.py:255`): one `query` → `expand_queries(query,
n=5)` (a vLLM call, temp 0.5) → 3-5 variants; each variant SPECTER→Qdrant
`limit=max(top_k,5)`, tag-filtered; dedup by DOI/id/title keeping max score
and counting `matched_by`; **final sort `(-matched_by, -score)`** — a
cross-variant vote, pure vector signal, no citation. Its closest spec
analog is RRF (fusion across rankings), NOT the citation re-ranker.

Three consequences: (a) the Q1 α/β decision governs a path the agent never
uses — citation-rerank is "production" only for the search page; every
paper number about the deployed assistant runs on the no-citation agent
path; (b) `paper_search` is NOT single-query dense, so the doc's earlier
`agent_dense` framing was wrong — the `matched_by` vote is a real ranking
function; (c) the temp-0.5 expander makes the agent retriever
non-deterministic, so it cannot enter a paper-grade benchmark unmodified.

**DECISION (2026-06-17): build the agent path as a fifth retriever, frozen
for determinism.**

1. **(A) Phase 2 retrievers** = spec's four (BM25, SPECTER-dense, citation-
   rerank, RRF) **plus `AgentRetriever`** = the multi-query vote-fusion the
   chat loop actually runs (SPECTER + `matched_by` vote over a variant
   set, tag-scoped). Ranking logic copied verbatim from `papers.py` per
   spec §3a.
2. **(B) Determinism via frozen variants.** Decouple expander from ranker.
   The variant set is a **seeded, committed input** generated **once** with
   the live `EXPANSION_SYSTEM_PROMPT` and deployed model at **n=5** (base +
   4 variants, matching production). The frozen variants file is
   **committed to git** (treated like the abstention ground-truth JSON
   exception — it's small, it's not third-party data, it IS a benchmark
   input; temp=0 vLLM is greedy but not bit-reproducible across versions,
   so a regen script can't reproduce the exact file). The generation script
   is kept alongside for provenance, not for reproduction. The committed
   file's **header is tagged with the generating model name + revision and
   the prompt SHA**.
3. **(B-Track E interaction)** The frozen set is a **fixed Track-A
   benchmark asset**: retrieval-quality comparisons (BM25 vs dense vs agent)
   stay apples-to-apples on identical inputs across the paper's life, and
   stay comparable across model swaps. Expansion *drift* under a model swap
   is a live-harness behaviour that **Track D/E exercise via the actual
   chat path**, not via Track A's frozen input. Track A measures the ranker
   on a frozen input; it does not measure the expander. This split is
   stated explicitly in the spec and the results so the distinction isn't
   silently lost.
4. **(C) Gate + reporting semantics.** The Phase 4e BM25-beating gate and
   the LitQA2 retrieval track run against **`AgentRetriever`** (the deployed
   reality). `/search/hybrid` citation-rerank (0.7/0.3 per Q1) is reported
   **alongside** as the search-page configuration. The citation-rerank-vs-
   agent gap is a **reported finding** (does the agent leave citation signal
   on the table?), not just a disclosure.
5. **(D) T2 arm alignment.** Arm 2 (vanilla RAG) uses the **single-variant
   agent retriever** (not `/search/hybrid`), so the arm 2→3 comparison
   isolates the **harness loop**, not a retrieval-pipeline swap. Noted in
   the ablation design.

**Spec updates this forces (separate commit):** §0's "production retriever
= `/search/hybrid`" is corrected; the §1/Phase 2 retriever taxonomy gains
`AgentRetriever`; the "0.8/0.2 live vs 0.7/0.3 paper" framing is rewritten
(see Q1).

---

## Q4 ✅ DECIDED — SPECTER embeddings for BEIR: reuse or embed from scratch?

**The question.** Phase 3 builds an `eval_*` Qdrant collection per BEIR
subset, embedding every doc with SPECTER. Is there a pre-computed embedding
cache worth reusing, or embed from scratch?

**What the codebase says (verified 2026-06-17).**

- Production SPECTER = **`allenai-specter` (SPECTER v1)**, on disk at
  `/opt/munin/data/models/specter` → mounted `/models/specter` (768d,
  cosine, max_seq 512). Confirmed from the model's own README + config.
- Production `papers` collection: **67,529 vectors, 768d cosine.**
- `backend/CLAUDE.md` says "SPECTER2 for papers" but the deployed model is
  v1. The retrieval spec is correct (`sentence-transformers/allenai-specter`);
  the CLAUDE.md line is stale. (See follow-up fix below.)
- The question splits by phase: BEIR docs are external (no cache can
  exist); Phase 4/5 docs ARE the production corpus (already embedded).

**DECISION (2026-06-17): embed-from-scratch for BEIR, reuse production for
4/5, pin v1.**

1. **Correctness anchor:** every retriever and eval corpus embeds with the
   exact on-disk `allenai-specter` v1 at `/models/specter` (the same path
   the service loads), never SPECTER2 and never an unpinned HF pull. Zero
   drift from production.
2. **Phase 3 (BEIR):** embed from scratch (no cache can exist for external
   corpora), persisting `eval_*` collections so re-runs skip re-embedding
   (collection name + doc-count is the cache key). `--device cuda` for
   TREC-COVID (~171K docs, the only heavy embed; SciFact/NFCorpus/SciDocs
   are 3-5K).
3. **Phases 4/5 (local pool, LitQA2):** **reuse the production `papers`
   embeddings as-is** (query the live collection directly, as the spec
   already designs); never re-embed the deployed corpus. Only *query*
   embeddings are computed fresh (one cheap encode per query).
4. **BEIR input format:** match production's exact title+abstract
   concatenation/separator when embedding BEIR docs (verify against
   production SPECTER usage at Phase 2/3 build time) so the BEIR-vs-
   production comparison isn't confounded by input formatting.

**FOLLOW-UP FIX (separate, non-blocking): `backend/CLAUDE.md` "SPECTER2"
staleness.** The line should read SPECTER v1 (`allenai-specter`) to match
the deployed model. NOTE (varghele, 2026-06-17): a SPECTER2 upgrade — or
running v1 and v2 concurrently — is a plausible future change. When that
lands, the harness's pinned-model anchor (point 1) and the frozen-variant
header tags (Q3) must record *which* SPECTER produced each embedding/run so
v1 and v2 results never get silently compared. Track A's scorecard header
should carry the embedding-model identity for exactly this reason.

---

## Q5 ✅ DECIDED — Pipelines: interactive or SLURM jobs?

**The question.** Do benchmark pipelines run interactively on hugin, or
submit themselves as SLURM jobs?

**What the codebase/cluster says (verified 2026-06-17).**

- SLURM present (`sbatch`/`sacct`/`squeue`); 2× RTX 5090 (32 GB each).
  Partition GRES splits GPUs into `gpu:batch` (GPU 0, ~free) and `gpu:vllm`
  (GPU 1, holds the served model, ~28 GB). "GPU 0" in the spec = the batch
  GPU.
- **`sacct` returns "Slurm accounting storage is disabled."** There is no
  `slurmdbd`. T5's planned "GPU-seconds via `sacct`" **does not work on this
  cluster.** This removes the main reason SLURM beat interactive for the
  eval.

**DECISION (2026-06-18): interactive default; cost = tokens + wall-clock +
measured inference-time.**

1. **Pipelines run interactive by default** (spec default). The `sacct`
   accounting argument for SLURM is moot. Long-running jobs (TREC-COVID
   embed, full T2 ablation, `run_all`) get a thin `nohup`/`tmux` (or
   optional `sbatch`) wrapper **purely for shell-survival**, not a separate
   code path and not for accounting.
2. **T5 cost accounting redesigned off `sacct`:**
   - **Tokens** — from the vLLM API `usage` field (`prompt_tokens`,
     `completion_tokens`). Exact, hardware-independent, the primary cost
     axis.
   - **End-to-end wall-clock** — measured in-process (spec §3d already
     mandates per-query timing). The latency the user feels.
   - **Inference-time (the GPU-seconds proxy)** — **measure it, don't
     derive it.** Run the eval at **concurrency=1** and time the model
     call: when the GPU serves only one request, the inference-call
     wall-clock IS that query's GPU-occupancy, with prefill and decode
     folded correctly. Do **not** estimate GPU-seconds as
     `tokens ÷ blended_throughput` — the prefill/decode asymmetry (prefill
     ~thousands tok/s parallel; decode ~tens tok/s sequential) makes a
     single blended rate badly biased for RAG's long-prompt/short-answer
     shape. Do **not** use `sacct`.
3. **Why inference-time is kept (not dropped as redundant):** the harness
   arm's total latency = inference + retrieval + tool execution + network.
   Breaking out inference-time decomposes the cost ("12 s/query: 7 s
   inference, 4 s retrieval, 1 s tools"), which is exactly what
   distinguishes the agentic arm's cost profile from the bare model in the
   T2 ablation. End-to-end wall-clock alone can't show that split.

**Implication for T5/T2:** the ablation arms must run at controlled
concurrency=1 for the inference-time number to be valid; throughput-under-
load is a different (production) question, out of scope for the cost-
accuracy Pareto plot.

---

## Q6 ✅ DECIDED — CSFCube: skip-with-note acceptable?

**The question.** CSFCube is not in BEIR proper and may not be cleanly
available via `ir_datasets`. The spec says it is acceptable to drop it this
round with a note in `results/beir_summary.md`.

**What's known (2026-06-18).** `ir_datasets` not yet installed (the package
doesn't exist until Phase 3), so the registry isn't introspectable live.
From the public registry: the four core BEIR subsets (`scifact`,
`trec-covid`, `nfcorpus`, `scidocs`) are exposed under `beir/*` and load
cleanly; **CSFCube is not in BEIR and not (to current knowledge) in the
`ir_datasets` registry** — its home is the `iesl/CSFCube` GitHub release.
It is also a `[VERIFY]` anchor (re-confirmed in the B1 verification pass).
**Deeper mismatch:** CSFCube is a **faceted query-by-example** benchmark
(the "query" is a full paper; relevance is aspect-graded:
background/method/result), whereas the other four are **query-by-text** with
flat relevance. So it (a) doesn't SPECTER-embed a short query string the way
`paper_search` does — it exercises a retrieval mode Munin doesn't deploy —
and (b) needs aspect-graded metric plumbing the Phase 1 flat-relevance
metrics don't have.

**DECISION (2026-06-18): skip with a documented note.** Attempt CSFCube via
`ir_datasets` when Phase 3 is built; if not cleanly exposed (expected),
**skip it** and document the omission in `results/beir/summary.md`, citing
**both** the access gap **and** the task-shape mismatch (query-by-example +
faceted relevance vs Munin's query-by-text deployment) — the latter is the
more defensible reason. The four core BEIR subsets stand. Revisit only if a
reviewer specifically wants a query-by-example anchor, as a scoped add-on,
not part of the core build. Do not hand-roll a loader from the
`iesl/CSFCube` release this round.

---

## Q7 ✅ DECIDED — Routing-eval placement: `backend/benchmarks/` or `backend/eval/`?

**The question.** The boundary contract says `benchmarks/` = paper-grade,
versioned, flakiness-is-a-bug; `eval/` = deployment-behavioral,
flakiness-is-information. Where does the routing eval live?

**The honest picture.** The routing eval is a hybrid, and its two halves
(already separated in `routing_eval.py`) split across the contract:

| Criterion | Verdict | Pulls toward |
|---|---|---|
| Produces a citable number | yes | benchmarks/ |
| Versioned / comparability matters | yes | benchmarks/ |
| Reproducible by outsiders | **no** (needs live deployment) | eval/ |
| Drives `/api/chat/completions` | yes (no other benchmarks/ code does) | eval/ |
| Flakiness | **partly informative** | eval/ |
| Grows with features (A5 paraphrases) | yes | eval/ |

The handoff's stated reason ("flakiness is a bug, not information") is the
weak part: routing runs through the same sampling LLM as everything else,
so same-query route variation is a real *stability* signal (the pong/
flakiness model), not pure noise. The decisive criterion is **purpose**:
routing accuracy is a measurement characterizing the harness, and the
harness is the paper's object of study — that is what `benchmarks/` is for.
`eval/`'s "never cited" rule exists because eval/ is regression tooling,
not measurement.

**DECISION (2026-06-18): `benchmarks/munin_bench/routing/`, with three
corrections to the handoff's reasoning.**

1. **Different reproducibility bar, documented.** The retrieval tracks are
   outsider-reproducible (public BEIR data, committed code). The routing
   track is reproducible *given the deployment + pinned model revision +
   seed + frozen item set*, NOT by an outsider. It is the single
   `benchmarks/` track that requires the live chat endpoint; document that
   bar explicitly so it is never mistaken for a BEIR-grade artifact.
2. **Nondeterminism handled, not denied.** Run **N reps** (reuse the
   flakiness-suite rep machinery the `routing_eval.py` docstring already
   references) and report routing accuracy as **mean ± CI over reps, plus a
   flip-rate / stability metric**. The item set stays versioned so the
   number is comparable across runs; flakiness is reported as a finding,
   not asserted away.
3. **One harness, two uses.** The A0 pre-migration baseline and A4
   regression are the *same* harness producing tagged routing scorecards;
   the paper-citation use is the same run. No duplication.

The pong test and the `backend/scripts/` QA tools stay in `eval/`, keeping
the boundary otherwise clean. `routing_eval.py` moves from `todo_v2/` into
`benchmarks/munin_bench/routing/` as part of the A0 commit (see Q10).

---

## Q8 ✅ DECIDED — LLM-judge budget for T9 (RAGAS cross-check)

**The question.** T9 runs RAGAS once with an external frontier judge on the
50-query subset to validate MiniCheck, then retires it. varghele sets a hard
cap once; runs stop when it is hit.

**Reframing (2026-06-18).** Three corrections to the spec's framing:

1. **One-time, then retired.** T9 validates that MiniCheck correlates with
   a frontier judge, reports the correlation, and is removed from the loop.
   Track E's re-run loop has zero frontier-judge cost (hard rule #7 stays
   intact; T9 is the explicitly-allowed exception). This is a one-time
   validation expense, not recurring.
2. **The spec's ~2000-calls/run over-counts ~2×.** Per §4a, context
   precision/recall use ground-truth qrels, NOT the judge. Only
   faithfulness (~11 calls/query: 1 decompose + ~10 claim-checks) and
   answer relevancy (~1 call/query) are LLM-judged → **~600 calls per judge
   per run**, not 2000.
3. **Lifetime cost is tens of dollars.** Two judges × ~600 calls + carried
   contexts + a 2-3× debugging margin ≈ 5-8M input + <1M output tokens
   over T9's whole life: ~$25-40 (Sonnet-class) to ~$100-150 (Opus-class),
   approximate, not live-quoted. The cap's job is to stop rerun creep, not
   ration a scarce resource.

**DECISION (2026-06-18): dual judges, hard cap $200.** Validate with **two
judges** (one Sonnet-class, one Opus/GPT-4-class) so the MiniCheck
correlation isn't an artifact of a single judge — the spec's intended
cross-check. **Hard cap: $200** total across all T9 runs (the RAGAS runner
aborts when spend reaches it). T9 is one-time; once the correlation is
reported in `results/ragas/judge_correlation.md`, RAGAS is removed from
`run_all`. Exact current Sonnet/Opus pricing to be pulled (claude-api
reference) when the RAGAS runner is built, to confirm the cap leaves
comfortable margin; the $200 figure is the policy ceiling regardless.
Not needed until after Phase 4e + MiniCheck validation (P1).

---

## Q9 ✅ DECIDED — Briefing document is not in the repo

**The question.** `munin-benchmark-landscape-briefing.md` is referenced by
all three planning docs (background only; its `[VERIFY]` anchors are
already carried into `BENCHMARK-TODO.md`). It is not anywhere in the repo.

**Confirmed (2026-06-18).** The briefing has never been in the repo or its
git history. It is the upstream research document the three planning docs
were distilled from ("This plan operationalizes ..."). Its binding content
is already lifted out: (1) every `[VERIFY]` anchor is carried forward in
the planning docs; (2) motivation sentences cite "briefing finding N" as
rationale, not instructions; (3) the T3 corpus-grounded-abstention novelty
claim traces to it. The handoff marks it "Background only."

**DECISION (2026-06-18): proceed without it.** The B1 **`[VERIFY]` pass is
the single source of truth** for anchor validity — it re-checks each anchor
against arXiv/venues before anything enters `munin.bib`, so the briefing is
not needed to validate any claim. The planning docs' paraphrases are the
claims to verify. Minor cost accepted: the `[VERIFY]` pass works from the
plan's paraphrases rather than the briefing's original phrasing/finding-
numbers; if an anchor is ambiguous, resolve it by what the planning docs
state, not by reconstructing the briefing.

---

## Q10 ✅ DECIDED — Where do `todo_v2/` artifacts land in the tree?

**The question.** Where do the planning docs and `routing_eval.py` live as
the build starts, and how do the decisions recorded here propagate?

**DECISION (2026-06-18).**

1. **Planning docs (`todo_v2/*.md`)** stay in `todo_v2/` while in flight
   (repo working-doc convention). A spec graduates to `backend/docs/` or
   `shared/docs/` via `git mv` + a short stub when its content stabilizes
   (e.g. retrieval spec after Phase 5), matching the archive-over-delete
   precedent. The shipped `backend/benchmarks/README.md` is the *operator*
   doc; the graduated spec is the *design* doc.
2. **`routing_eval.py`** → `benchmarks/munin_bench/routing/` at the A0
   commit (per Q7), no duplicate left behind.
3. **`KICKOFF-QUESTIONS.md`** is the durable decision record (the *why*
   behind choices the specs state as bare *what*). It stays as the dated
   audit trail; its decisions propagate into the specs. When `todo_v2/`
   retires, it graduates alongside the spec, not deleted.

**Spec-reconciliation backlog (created by Q1/Q3/Q4/Q5; lands as ONE "spec
reconciliation" commit before A0/Phase 1 so implementers read a correct
spec):**

| Source | Edit | Target | Deploy-affecting? |
|---|---|---|---|
| Q1 | "0.8/0.2 live vs 0.7/0.3 paper" → "0.7/0.3 production; 0.8/0.2 sweep point" | RETRIEVAL-EVAL-SPEC §0 | no |
| Q1 | drop hardcoded weights from search UI (before local-pool run) | `frontend/static/search/index.html` | **yes** |
| Q3 | correct "production retriever = `/search/hybrid`"; add `AgentRetriever` to Phase 2 taxonomy | RETRIEVAL-EVAL-SPEC §0, §1/Phase 2 | no |
| Q4 | "SPECTER2" → SPECTER v1 (`allenai-specter`) | `backend/CLAUDE.md` | no |
| Q5 | T5 cost model: drop `sacct`/GPU-seconds-via-accounting → tokens + wall-clock + concurrency-1 inference-time | BENCHMARK-TODO T5 / MASTER-PLAN §5 | no |

Commit message drafted for varghele (never auto-committed, per workflow). The
two deploy-affecting items (none here except the frontend weights fix) are
flagged in the commit body.

---

## Status

All ten questions decided (2026-06-18). Next: the spec-reconciliation
commit, then A0 (wire `run_item`/`judge_abstention`, pre-migration routing
scorecard) and Track A Phase 1 (metrics + tests) in parallel, per the
handoff week-1 plan.
