# Munin Retrieval Evaluation Harness — Build Spec

**Purpose.** Build a reproducible retrieval-evaluation harness for the Munin paper. The harness measures three things: (1) how Munin's current production retriever compares to baselines on standard scientific IR benchmarks; (2) how it performs on a locally-constructed pool of real user queries; (3) end-to-end RAG answer quality on a subset of (2). The numbers go directly into the paper's Evaluation section.

**Who you are.** A Claude session running in the Munin monorepo with computer-use tools enabled. The operator (varghele) will hand-curate the local query set and the annotations; everything else should be runnable end-to-end without intervention. Treat this document as the source of truth — if it contradicts assumptions you'd make from the codebase, ask before deviating.

**Important: this is not a deliverable in itself.** It's a harness. Quality means reliable numbers and reproducibility, not features. If you're tempted to add a slick CLI dashboard or auto-uploaders, don't. Spend the budget on getting BEIR and the pool benchmark right.

---

## 0. Context: What Munin's retriever actually does

> **RECONCILED 2026-06-18 (KICKOFF-QUESTIONS Q1, Q3).** This section was
> originally written assuming the chat agent retrieves via `/search/hybrid`
> with live weights 0.8/0.2. Both assumptions were wrong against the code.
> The corrected picture is below; the original is preserved in git history.
> Decision records: `todo_v2/KICKOFF-QUESTIONS.md` Q1 (weights) and Q3
> (which retriever is "production").

Munin has **two distinct retrieval paths**, and the eval must not conflate
them:

**Path 1 — `/search/hybrid` (search UI only).** SPECTER dense + Neo4j
citation re-rank. This is what the §0 pipeline below describes. **No backend
code and no chat-agent code calls it** — its only caller is the search page.

**Path 2 — `paper_search` MCP tool (the chat agent; what the paper is
about).** Multi-query SPECTER + vote-fusion, tag-scoped, **no citation
signal**: one `query` is expanded to ~5 variants (an LLM call), each variant
is SPECTER-embedded and searched against `papers`, results are deduped by
DOI keeping max score and counting `matched_by`, and the final ranking is
`sort by (-matched_by, -score)`. Closest analog is RRF (fusion across
rankings), NOT the citation re-ranker. This is the **production retriever
for Track A**; it is built as `AgentRetriever` in Phase 2.

**`/search/hybrid` pipeline (Path 1, for the search-page configuration and
T2 vanilla-RAG arm):**

1. Query is embedded with SPECTER (`sentence-transformers/allenai-specter`, 768d, cosine).
2. Qdrant `papers` collection is searched with `query_points(limit=top_k*3, capped at 100)`.
3. Optional `year_min` / `year_max` filter applied at the Qdrant level.
4. For each hit, Neo4j is queried (`get_citation_counts`) for in-degree (`citation_count`) and out-degree (`reference_count`).
5. Citation score is computed as `log1p(citation_count) / log1p(max_citations_in_pool)`, clamped to [0,1].
6. Weights are normalised: `norm_v = vector_weight / (vector_weight + citation_weight)`, same for citation.
7. Final score: `combined = norm_v * vector_score + norm_c * citation_score`. Hits re-sorted by `combined`.

**Weights — primary baseline configuration:**

- **`vector_weight = 0.7`, `citation_weight = 0.3`.** This is the
  `HybridSearchRequest` API default (`backend/retrieval/models.py`) AND the
  paper claim; the two coincide. The search UI previously hardcoded 0.8/0.2,
  overriding the default; per Q1 that override is removed so production ==
  paper == 0.7/0.3. Note: weights are normalised, so only the ratio matters
  (0.7/0.3 = 2.33:1; 0.8/0.2 = 4:1).
- `top_k = 10` for the primary metric (nDCG@10).
- No year filter unless the benchmark itself imposes one.

**Sweep:** evaluate 0.7/0.3 as the production baseline plus a grid including
0.8/0.2 (labelled "prior search-UI setting") to characterise sensitivity.
**Never auto-tune** (§6 anti-goals); a wildly better setting is a finding to
report, not a silent change. The 0.7/0.3 production weights govern Path 1
(search page) and the T2 vanilla-RAG arm; the agent path (Path 2) carries no
citation signal, so weights do not apply to it.

---

## 1. Deliverables

Build these files. **In `backend/benchmarks/`** (new directory; keep benchmark code out of `retrieval/` so it doesn't ship in deploy.sh). Note the folder contract: `backend/benchmarks/` is for paper-grade, reproducible, versioned benchmarks (this spec); `backend/eval/` is the separate behavioral-test layer (pong test, scenario registry wrapping the `backend/scripts/` QA tools) — see `EVAL-SUITE-MASTER-PLAN.md` §8 for the boundary. Nothing in this spec goes into `backend/eval/`.

```
backend/benchmarks/
  README.md                       # How to run, written for varghele
  requirements.txt                # ir_datasets, ranx, qdrant-client,
                                  # neo4j, sentence-transformers,
                                  # rank_bm25, numpy, pandas, scipy,
                                  # tqdm, pytrec_eval (optional)
  pyproject.toml                  # OR pyproject for the eval package
  munin_bench/
    __init__.py
    config.py                     # All constants, no logic
    retrievers/
      __init__.py
      base.py                     # ABC: retrieve(query, top_k) -> list[(doc_id, score)]
      bm25.py                     # rank_bm25 over abstracts
      specter_dense.py            # SPECTER + Qdrant (single-query)
      agent_retriever.py          # PRODUCTION agent path (Q3): multi-query
                                  # SPECTER + matched_by vote-fusion, frozen
                                  # variant set, tag-scoped, no citation
      citation_rerank.py          # /search/hybrid: SPECTER + citation re-rank
                                  # (search-page config, NOT the agent path)
      rrf_hybrid.py               # SPECTER + 1-hop graph + RRF (future-work retriever)
      two_hop_check.py            # 2-hop graph degradation check
    benchmarks/
      __init__.py
      beir_runner.py              # BEIR subsets via ir_datasets
      local_pool.py               # Munin's own logged-query pool
      litqa2.py                   # LitQA2 anchor; only runs on
                                  # questions whose source DOI is in
                                  # the Munin corpus
    metrics/
      __init__.py
      ir_metrics.py               # nDCG@k, Recall@k, MRR, Hits@k
      bootstrap.py                # paired bootstrap CIs
      significance.py             # paired Wilcoxon
    pipelines/
      __init__.py
      run_beir.py                 # CLI: --subset scifact|trec-covid|...
      run_local_pool.py           # CLI: needs queries.jsonl + qrels
      run_litqa2.py
      build_pool.py               # produce annotation pool from logs
    cli.py                        # `python -m munin_bench` dispatcher
  tests/
    test_metrics.py               # Tiny gold cases for every metric
    test_rrf.py                   # Known RRF outputs
    test_citation_score.py        # Match production compute_citation_score
    test_bootstrap.py             # CI contains true mean for synthetic data
  data/                           # Created by scripts, gitignored
    beir/                         # ir_datasets cache root
    local/
      queries.jsonl               # varghele curates this
      pool.jsonl                  # Built from logs
      qrels.tsv                   # Two-annotator graded labels
    litqa2/
      questions.jsonl             # Downloaded LitQA2 eval split
      overlap.json                # Which questions are in-corpus
  results/                        # Outputs land here, gitignored
    *.json                        # Raw per-query metrics
    *.csv                         # Tables for the paper
    *.md                          # Human-readable summaries
```

**`results/` is the only thing the paper consumes.** Everything in there must be re-creatable by running one CLI command (specified per pipeline below).

---

## 2. Build order

Strict. Don't skip ahead — each phase has a verification gate that protects the next phase from being built on a broken foundation.

### Phase 1: metrics and bootstrap (no I/O)

Build `metrics/ir_metrics.py`, `metrics/bootstrap.py`, `metrics/significance.py`, and `tests/test_metrics.py`, `tests/test_bootstrap.py`.

**Metrics to implement:**

- `ndcg_at_k(ranked: list[str], qrels: dict[str, int], k: int) -> float` — graded relevance (0/1/2/3), log2 discount, ideal DCG computed from the qrels (not from the ranked list). Standard TREC formula.
- `recall_at_k(ranked: list[str], relevant: set[str], k: int) -> float`
- `mrr(ranked: list[str], relevant: set[str]) -> float`
- `hits_at_k(ranked: list[str], relevant: set[str], k: int) -> float`

**Bootstrap:**

- `paired_bootstrap(per_query_a: list[float], per_query_b: list[float], n_resamples: int = 1000, seed: int = 42) -> dict`. Returns `{mean_a, mean_b, mean_diff, ci_low, ci_high, p_value_two_sided}`. Sample query indices with replacement; compute mean of `a[i] - b[i]` over resampled indices; CI is the percentile interval; p-value is `2 * min(P(diff <= 0), P(diff >= 0))`.
- `single_bootstrap(values: list[float], ...) -> dict` for unpaired CIs on a single system.

**Significance:**

- `paired_wilcoxon(a: list[float], b: list[float]) -> dict` wrapping `scipy.stats.wilcoxon(a, b)`. Return W, p-value, n_nonzero.

**Tests are mandatory before Phase 2.** Hand-write 4–5 cases per metric with known answers from a textbook or `ranx` output. Example: `ndcg_at_k(['a','b','c'], {'a': 2, 'c': 1}, k=3)` has DCG = 2 + 0 + 1/log2(4) = 2.5; ideal DCG = 2 + 1/log2(3) ≈ 2.6309; nDCG = 2.5 / (2 + 1/log2(3)) = 0.950234 (an earlier draft rounded this to 0.9501; the exact value is 0.950234). Verify against `ranx` if installed.

**Gate:** `pytest backend/benchmarks/tests/` passes with 100% coverage of metric functions. Don't proceed otherwise.

### Phase 2: retrievers

> **RECONCILED 2026-06-18 (KICKOFF-QUESTIONS Q3).** A fifth retriever,
> `AgentRetriever`, is added: it is the path the chat agent actually uses
> (`paper_search`), and it, not `CitationRerankRetriever`, is "the
> production retriever" for the Phase 4e gate and the LitQA2 retrieval
> track. `CitationRerankRetriever` (= `/search/hybrid`) is retained and
> reported as the **search-page configuration**; the citation-rerank-vs-
> agent gap is a reported finding, not a defect.

Implement **five** retrievers behind a common interface (the original four
plus `AgentRetriever`).

**`AgentRetriever` (the production agent path; copy ranking from
`backend/retrieval/mcp/tools/papers.py::paper_search` verbatim, per §3a):**

- Input is a single query plus a **frozen, committed variant set** (NOT a
  live LLM expansion). Production expands the query to ~5 variants via an
  LLM call at temperature 0.5 (`mcp/tools/query_expansion.py`), which is
  non-deterministic and cannot enter a paper-grade benchmark. So the variant
  set is generated **once** (live `EXPANSION_SYSTEM_PROMPT`, deployed model,
  `n=5` = base + 4 variants), **committed to git** (treated like the
  abstention ground-truth JSON exception — small, not third-party data, it
  IS a benchmark input), and its file header is tagged with the generating
  model name + revision and the prompt SHA. A regen script is kept for
  provenance, not reproduction (temp=0 vLLM is greedy but not
  bit-reproducible across versions).
- Ranking (deterministic given the variant set): each variant SPECTER →
  Qdrant `query_points(limit=max(top_k,5))`, tag-filtered; dedup by
  DOI/id/title keeping max score and counting `matched_by`; final sort
  `(-matched_by, -score)`; return top-k. No Neo4j, no citation signal.
- **Track A measures the ranker on the frozen variant input; it does NOT
  measure the expander.** Expansion drift under a model swap is a live-
  harness behaviour exercised by Track D/E via the actual chat path, not by
  this frozen-input retriever. State this split in the results.
- **T2 vanilla-RAG arm (arm 2)** uses the **single-variant** form of this
  retriever (base query only, no fan-out), so the arm 2 → arm 3 comparison
  isolates the harness loop, not a retrieval-pipeline swap.

The original four retrievers follow.

**`base.py`:**

```python
class Retriever(ABC):
    name: str

    @abstractmethod
    def retrieve(self, query: str, top_k: int = 10) -> list[tuple[str, float]]:
        """Return ranked (doc_id, score) pairs."""

    def retrieve_batch(self, queries: list[str], top_k: int = 10) -> list[list[tuple[str, float]]]:
        """Default: loop. Override for efficiency."""
```

**Retrievers:**

- `BM25Retriever(corpus_iter: Iterator[tuple[str, str]])` — `corpus_iter` yields `(doc_id, text)`. Tokenise with `text.lower().split()` plus alnum filter; persist tokenised corpus + doc_id list to a pickle so re-loads are fast. Use `rank_bm25.BM25Okapi`.
- `SpecterDenseRetriever(qdrant_url, collection, model_name="sentence-transformers/allenai-specter")` — load with `sentence-transformers`. Embed query, call `qdrant.query_points(limit=top_k)`. Use the same client kwargs as `backend/retrieval/database.py`. **Match the production code's deduplication**: if a DOI appears in multiple chunks (it shouldn't, since SPECTER is one-per-paper, but check), keep the best score.
- `CitationRerankRetriever(dense, neo4j_driver, vector_weight=0.8, citation_weight=0.2)` — this is the production retriever. **Copy `compute_citation_score` verbatim from `backend/retrieval/main.py`** so any production bugfix is reflected here. Fetch `top_k * 3` from dense, query Neo4j `get_citation_counts` over the DOIs, compute combined score with normalised weights, re-sort, return top-k. Output `(doi, combined_score)`.
- `RRFHybridRetriever(dense, graph, k=60, seed_n=10)` — the *future-work* retriever from the paper. Dense top-`top_k * 3`, expand seeds (top-`seed_n` dense hits) via Neo4j 1-hop both directions, RRF over [dense_ranking, expansion_ranking] with `k=60`. Document why RRF and not weighted: it's the published methodology (Cormack et al. 2009, SIGIR).
- `TwoHopCheckRetriever(rrf_retriever)` — same as RRF but 2-hop graph expansion. Expected to degrade; this is the degradation-check configuration the paper promises.

**Important:** all retrievers return `doc_id` as a string. For Munin's own corpus that's a DOI. For BEIR it's the BEIR `doc_id`. **Never mix the two.** A retriever instance is bound to one corpus.

**Tests:** for `citation_rerank.py`, mock the Neo4j call with three papers (citation counts 0, 10, 1000) and verify the score math matches the production formula to 4 decimals.

**Gate:** all four retrievers instantiate and run a sanity-check query (`"protein folding"` returns >0 hits) against the live `papers` collection. Print top-3 results for visual inspection.

### Phase 3: BEIR benchmark runner

Use `ir_datasets` to load BEIR subsets. **Do not download from the original BEIR drive** — `ir_datasets` handles caching and is well-documented.

**Subsets to evaluate (paper lists five; build all five):**

- `beir/scifact/test` — 300 queries, 5K docs. Highest signal-to-noise; debug here first.
- `beir/trec-covid` — 50 queries, ~171K docs. Heavy; build last.
- `beir/nfcorpus/test` — ~325 test queries, 3.6K docs.
- `beir/scidocs` — paper-to-paper recommendation, several sub-tasks. Use `beir/scidocs` from `ir_datasets`; pick the qrels variant the paper cites (SPECTER's RELISH and CITE — verify against `ir_datasets`' available scorefiles).
- CSFCube — **not in BEIR proper.** Find it via `ir_datasets` (`csfcube` if available) or fall back to the original Mysore et al. release at <https://github.com/iesl/CSFCube>. Document whichever path you use. The paper flags CSFCube as `[VERIFY]`, so if the dataset is hard to access, it's acceptable to drop CSFCube from this round — note the omission and rationale in `results/beir_summary.md`.

**Pipeline (`run_beir.py`):**

```
1. Load subset via ir_datasets.
2. Build a fresh Qdrant collection for this subset's docs:
     - Collection name: f"eval_{subset_name}"
     - Embed all docs with SPECTER (title + abstract concatenated as
       in Cohan et al. 2020 — verify against your existing SPECTER
       usage in backend/retrieval/).
     - Insert with payload {"doc_id": <beir_id>}.
   IMPORTANT: do not collide with the production `papers` collection.
   All eval collections live under `eval_*` prefix and are deletable.
3. Build BM25 index over title+abstract.
4. Citation graph: NOT available for BEIR corpora. The citation-rerank
   and RRF retrievers run against an EMPTY graph for BEIR. Document
   this explicitly in results: it means citation_score is always 0,
   so citation_rerank degenerates to dense-only weighted by
   norm_vector_weight. This is the honest answer — BEIR is the
   "external validity" panel, and its purpose is to compare SPECTER
   against BM25 in the absence of Munin's graph signal.
5. Run each retriever, save per-query rankings to
   results/beir/<subset>/<retriever>.jsonl
6. Score with metrics from Phase 1.
7. Write results/beir/<subset>/summary.json and a Markdown table.
```

**Configurations to run on each subset:**

1. BM25 only
2. SPECTER dense only
3. SPECTER + citation re-rank (α=0.8, β=0.2) — DEGENERATE on BEIR (no graph), document
4. SPECTER + citation re-rank (α=0.7, β=0.3) — same
5. RRF [BM25, SPECTER] — proper hybrid where BM25 fills the role of the graph

On BEIR, the *real* story is (1) vs (2) vs (5): does dense beat BM25, and does the RRF hybrid improve over either alone? This is what reviewers will look at to validate that SPECTER (not just our graph) earns its place.

**Statistical testing:** for each pair (e.g., dense vs RRF-hybrid), run paired bootstrap on per-query nDCG@10 with 1000 resamples; report mean diff, 95% CI, two-sided p-value.

**Output format (`results/beir/<subset>/summary.md`):** one Markdown table per subset, columns = retrievers, rows = metrics with 95% CI in parens. Plus a small significance table for the headline pairs.

**Gate:** `python -m munin_bench.pipelines.run_beir --subset scifact` completes
end-to-end and the pipeline reproduces a published reference number. The
operative sanity check is **BM25**, not SPECTER: BM25 must land near the
BEIR-published SciFact BM25 (nDCG@10 ≈ 0.665) — that validates data loading,
qrels, and the metric in one shot. (Reconciled 2026-06-29: the original gate
asked for SPECTER dense > 0.5 "around 0.50–0.55". Measured, SPECTER-v1 scores
**0.4788** with Munin's production document construction (`title\n\nabstract`)
and **0.4943** with the canonical SPECTER `title[SEP]abstract` form — neither
clears 0.5, so the old threshold was optimistic. Our 0.4788 was confirmed
correct two independent ways: it is bit-identical to `pytrec_eval`'s
`ndcg_cut_10`, and our BM25 = 0.6523 reproduces the published BEIR BM25 within
the CI. SPECTER is a citation-document embedder, not retrieval-tuned, so sitting
well below BM25 on claim-verification queries is expected.)

> **FINDING (2026-06-29) — production embedding leaves ~1.5 nDCG points on the
> table.** Production embeds papers as `f"{title}\n\n{abstract}"`
> (`paper_pipeline.py:1392`), but SPECTER's canonical input is
> `title[SEP]abstract`. On SciFact that gap is +0.0155 nDCG@10 (0.4788 →
> 0.4943). Switching the production separator to the tokenizer `[SEP]` token is
> a low-risk retrieval improvement (cost: re-embedding the ~68k-paper corpus).
> Track A deliberately measures the DEPLOYED construction (`\n\n`), so 0.4788
> is the honest paper number; `[SEP]` is a future-work lead, not an eval change.

### Phase 4: local pool benchmark

This is the section reviewers will actually read carefully, because the corpus matches the deployment. Build it carefully.

**Step 4a: Query extraction (`pipelines/build_pool.py --extract-queries`).**

Extract candidate queries from `backend/retrieval/chat_store.py` (SQLite at `/var/lib/munin/chat.sqlite` or wherever it lives — grep the codebase). Heuristics:

- Skip turns where the user message starts with `/`, `<`, or is shorter than 20 chars.
- Skip turns where the *next* assistant turn made no `paper_search` tool call (no retrieval = no retrieval to evaluate).
- Sample 300 candidates uniformly at random across the last 12 months.
- Write to `data/local/candidates.jsonl`: `{qid, text, conversation_id, timestamp, n_papers_retrieved_originally}`.

> **STATUS 2026-06-30 — 4a built, Phase 4 DEFERRED (insufficient usage).** The
> extractor (`build_pool.py --extract-queries`) is built and run. The chat store
> (375 conversations / 1719 messages, ~2.5 months Apr-Jun 2026) yields only
> **42 candidate queries** — just 76 turns ever called `paper_search`, and after
> the heuristics + dedupe that's 42. (We do NOT count `semantic_scholar_search`;
> that's web retrieval, not the local corpus.) The spec targets 100-200 final
> queries and Step 4b blocks below 100, so curating now would give an
> underpowered pool (~25-40 after curation). **Decision: gather more real usage
> first, re-extract later.**
>
> **TODO — synthetic-query augmentation (likely needed).** Real usage may not
> reach 100-200 for a while, so plan a synthetic-query track to supplement (NOT
> replace) the curated real queries: generate natural-language queries from
> in-corpus papers (LLM reads a paper's title+abstract, emits a query it would
> answer), giving an automatic query -> known-relevant-DOI pair (Promptagator /
> InPars / LitQA2-style construction). Caveats to bake in: (a) clearly label
> synthetic vs real and report them separately — the distributions differ; (b)
> the "only the source paper is relevant" assumption undercounts other relevant
> papers (pool bias), so pair synthetic items with the same pooled-judging step,
> not bare source-DOI labels; (c) a synthetic set validates retrieval recall,
> not real user intent. Treat real curated queries as primary and synthetic as a
> power-boosting supplement. Build this when revisiting Phase 4.

**Step 4b: Manual curation.**

varghele will go through `candidates.jsonl` and produce `data/local/queries.jsonl`:

```json
{"qid": "q001", "query": "GROMACS umbrella sampling tutorial recommendations", "relevant_dois": ["10.1021/...", "10.1063/..."]}
```

Target: 100–200 final queries. Don't proceed to 4c until this file exists with ≥100 lines.

**Step 4c: Pool construction (`pipelines/build_pool.py --build-pool`).**

For each query, run BM25, dense, citation-rerank, and RRF-hybrid against the live `papers` collection with `top_k=20`. Take the union. Write `data/local/pool.jsonl`:

```json
{"qid": "q001", "query": "...", "pool": ["10.1021/...", "10.1063/...", ...], "pool_size": 23}
```

Pool sizes should be 30–60 per query (overlap reduces the union below 4×20=80).

**Step 4d: Annotation.**

Two annotators (varghele + one group member). Build a minimal web UI: a single-page HTML form served by a 50-line Flask script that loads `pool.jsonl` and writes `qrels_<annotator>.tsv`:

```
qid\tdoc_id\trelevance\tannotator
q001\t10.1021/...\t2\tvarghele
```

Relevance scale (paper-aligned): `0` = not relevant, `1` = partially relevant, `2` = relevant.

After both annotators are done, merge:

```python
# pipelines/merge_qrels.py
# 1. Load both qrels_*.tsv.
# 2. For each (qid, doc_id) pair:
#    - If both annotators agree: use that score.
#    - If they disagree by 1 (e.g., 1 vs 2): use the lower score conservatively.
#    - If they disagree by 2 (0 vs 2): mark for adjudication; write to disagreements.tsv.
# 3. Report Cohen's kappa across all pairs (use sklearn.metrics.cohen_kappa_score with weights="linear").
# 4. After varghele adjudicates disagreements.tsv, re-run to produce final qrels.tsv.
```

**Cohen's kappa must be reported in `results/local/agreement.md`**, including the n of items adjudicated. If kappa < 0.5, stop and tell varghele — the labels aren't reliable enough to defend in a paper.

**Step 4e: Evaluation (`pipelines/run_local_pool.py`).**

Same retriever sweep as BEIR, but now the citation graph is meaningful (production corpus). Configurations:

> **RECONCILED 2026-06-18 (Q3).** Config 0 added: `AgentRetriever` is **the
> production retriever** (the chat agent's path). Configs 3/4 (citation
> re-rank = `/search/hybrid`) are retained as the **search-page
> configuration**, no longer labelled "production"; the citation weights are
> 0.7/0.3 per Q1 (config 3 was the old 0.8/0.2 search-UI setting, kept as a
> sweep point).

0. **`AgentRetriever`** (frozen-variant multi-query SPECTER vote-fusion, tag-scoped, no citation): **THE PRODUCTION RETRIEVER** (chat agent path)
1. BM25 only
2. SPECTER dense only (single-query)
3. SPECTER + citation re-rank (α=0.8, β=0.2) — prior search-UI setting (sweep point)
4. SPECTER + citation re-rank (α=0.7, β=0.3) — **search-page configuration** (= API default = paper weights)
5. SPECTER + citation re-rank, swept α ∈ {0.9, 0.7, 0.5, 0.3} with β = 1−α — sweet-spot search
6. RRF [BM25, SPECTER, 1-hop graph expansion] — future-work retriever
7. RRF + 2-hop expansion — degradation check

The `AgentRetriever`-vs-config-4 comparison (does the agent leave citation
signal on the table?) is a **reported finding**, per Q3.

**Disclose all threats to validity in `results/local/summary.md`:**

- Pool bias: any system that retrieves docs outside the union got 0 credit for them. Compute and report "judged-rate" per system per query = `|retrieved ∩ pool| / top_k`. If a system's judged-rate is consistently below the others', its scores are pessimistic.
- Sample size n = 100–200 limits resolution. Report 95% CIs, don't oversell point estimates.
- Annotators are internal. Disclose kappa, disclose adjudication count.

**Gate:** the production retriever (**config 0, `AgentRetriever`**) must beat BM25 (config 1) on nDCG@10 with paired bootstrap p < 0.05. If it doesn't, *something is wrong with the harness, not the retriever* — investigate before claiming a result in the paper. (Reconciled 2026-06-18, Q3: the gate is against `AgentRetriever`, the deployed path, not the citation re-ranker.)

### Phase 5: LitQA2 anchor

LitQA2 anchors Munin against PaperQA2 — the closest academic comparator. varghele confirmed he can obtain the source papers, so the in-corpus subset should be large enough to support a real comparison rather than a curio. Treat this as a primary test.

1. Download the LitQA2 eval split from FutureHouse's `aviary-paper-data` repo. Check the license; cite Skarlinski et al. 2024 and Narayanan et al. 2024 in any release.
2. For each question, extract the source paper DOI. Check `data/litqa2/overlap.json`: which DOIs are in the Munin corpus?
3. **If overlap < 50 questions, stop and tell varghele before continuing** — the comparison loses statistical power below that threshold. varghele will need to ingest more LitQA2 source papers before this phase can proceed.

> **STATUS 2026-06-30 — overlap = 0/199, Phase 5 BLOCKED.** Checked the public
> LitQA2 split (`futurehouse/lab-bench`, 199 Qs, each with a source DOI) against
> the live `papers` corpus (67,482 DOIs). **Zero** source papers are in-corpus.
> Verified not a DOI-normalisation artefact: formats match and publisher
> prefixes overlap heavily (Nature/Elsevier/bioRxiv/Science/PNAS in both). It's
> genuine — LitQA2 is general molecular-bio/microbiology/neuroscience (microglia,
> connectomes, glycoRNA), a different slice than what's been ingested. Outputs:
> `data/litqa2/overlap.json` (the check) and `data/litqa2/missing_papers.csv`
> (all 190 unique source papers, title+venue+year via Crossref, 178 open-access,
> sorted open-access-first then by #questions). To unlock Phase 5 varghele must
> ingest ≥50 of these (≈1 paper ≈ 1 question). OPEN QUESTION: ingesting ~190
> mostly-out-of-domain bio papers purely to run a benchmark is a real cost —
> reconsider whether LitQA2 is the right anchor for Munin's corpus, or ingest a
> prioritised subset and report the smaller N honestly.
>
> **RESOLVED 2026-07-01 (varghele: "grab all of them").** All 190 source DOIs
> acquired (97 OA; +34 per-publisher via curl_cffi Cloudflare bypass from the
> Leipzig IP; 59 manual browser grab of Elsevier/Oxford/Wiley) and ingested.
> Coverage now **199/199 questions, 189/190 papers** (was 0). 48 papers were
> "missing" only because GROBID mis-keyed them under a reference DOI (production
> bug; see memory project_grobid_doi_extraction_bug), corrected via
> re-key/fresh-embed. Phase 5 can run on the full 199-question set.

4. Run two tracks against the in-corpus subset:
   - **Retrieval-only:** does the production retriever (**`AgentRetriever`**, the chat agent path, per Q3) surface the source paper in the top-10? Report Recall@1/5/10 and MRR. (Optionally also report the `/search/hybrid` search-page config alongside for the same finding as Phase 4e.)
   - **End-to-end answer:** run the question through Munin's full chat pipeline with the research persona. Score: did the model pick the right multiple-choice option (or correctly say "Insufficient Information")? Report precision, accuracy, recall — matching PaperQA2's reported metrics so the numbers go side-by-side in the paper's comparison table.
5. Output `results/litqa2/summary.md` with:
   - Subset size N (out of 200) and the list of in-corpus DOIs.
   - Retrieval and answer metrics with 95% CIs from paired bootstrap over the N questions.
   - A small table putting Munin's accuracy next to PaperQA2's published 66.0% on LitQA2 (verify the exact number against Skarlinski et al. before citing).

**Important caveat to disclose openly in `results/litqa2/README.md`:** PaperQA2 was trained using LitQA2 train and eval splits (Narayanan et al. 2024, Aviary). The test split is held out, but PaperQA2 has direct exposure to the question distribution. Munin's model (Qwen3.6-35B-A3B) has not. The comparison is therefore *not* like-for-like — it is "how does an off-the-shelf model with Munin's retrieval stack up against a system explicitly trained on this benchmark." Frame the result accordingly: a loss is expected and informative; parity is a strong result; a win would be remarkable.

**Gate:** the harness must run end-to-end on at least 50 in-corpus questions and produce both retrieval and answer numbers. If you can't get answer numbers (e.g., the model refuses to follow the multiple-choice format), report retrieval-only and note the gap in `results/litqa2/README.md`.

---

## 3. Things to get right

These are the points where I expect mistakes if I don't flag them.

### 3a. Use the production code, don't reimplement it

`compute_citation_score`, the weight normalisation, the `fetch_k = min(top_k * 3, 100)` rule — copy these verbatim from `backend/retrieval/main.py`. Do **not** rewrite from the spec; the spec describes the code, the code is the source of truth.

If you find the production code diverges from this spec, the production code wins. Update this spec (commit a separate change) and tell varghele.

### 3b. Eval collections are isolated

Every BEIR subset goes into its own Qdrant collection (`eval_scifact`, `eval_trec_covid`, etc). The production `papers` collection is **never** touched by the eval harness. If you accidentally write to `papers`, you've broken varghele's deployment.

Same for Neo4j: the local-pool benchmark queries the production graph (that's intentional — we want the real graph signal), but doesn't write to it.

### 3c. No leaks between train and test

LitQA2's eval split is in the public release but its source papers may have been ingested into Munin's corpus. That's fine for the retrieval-recall metric. It is *not* fine to claim accuracy numbers comparable to PaperQA2's published scores, because PaperQA2 trained on this data and you're evaluating against papers it has memorised. State this in `results/litqa2/README.md`.

### 3d. Time the runs honestly

The harness should log per-query wall-clock for every retriever. The paper's system-performance section needs honest latency numbers, and the eval harness is the cheapest way to collect them. Median, p95, p99 per retriever, per benchmark.

### 3e. Don't be slick

No fancy progress bars beyond `tqdm`. No emoji in CLI output. No HTML dashboards. The harness has one job: produce trustworthy numbers varghele can paste into a `.tex` table. Anything else is a distraction.

### 3f. Reproducibility hygiene

Every pipeline takes a `--seed` flag (default 42). Every bootstrap, every random sample, every "shuffle 50 queries" operation reads it. If a run isn't bit-for-bit reproducible from `--seed`, it's broken.

Every `results/*.json` includes a header: `{run_id, timestamp, git_sha, seed, python_version, package_versions}`. `pipelines/_common.py` should have a `make_run_header()` helper.

### 3g. Network reality

Neo4j and Qdrant queries are slow over a network. Batch them: `get_citation_counts` already takes a list of DOIs in one call — use it. Don't issue 100 sequential Neo4j queries when one will do.

For the BEIR runs, embedding 5K SciFact docs with SPECTER on CPU is fine (a few minutes). 171K TREC-COVID docs is hours on CPU — schedule that on `hugin` GPU 0 via SLURM if needed. The harness should expose a `--device cuda` flag; default `cpu`.

---

## 4. Acceptance criteria

You're done when:

1. `pytest backend/benchmarks/tests/` passes.
2. `python -m munin_bench.pipelines.run_beir --subset scifact` produces `results/beir/scifact/summary.md` with non-trivial numbers.
3. Same for the other three core BEIR subsets (TREC-COVID, NFCorpus, SciDocs). CSFCube optional — skip with a note if `ir_datasets` doesn't expose it cleanly.
4. `python -m munin_bench.pipelines.build_pool --extract-queries` produces a candidates file.
5. (Manual step by varghele: curate `queries.jsonl`.)
6. `python -m munin_bench.pipelines.build_pool --build-pool` produces a pool.
7. (Manual step: annotate via the 50-line Flask UI.)
8. `python -m munin_bench.pipelines.run_local_pool` produces `results/local/summary.md` with all 7 configurations.
9. `results/local/agreement.md` reports Cohen's kappa.
10. The harness's `README.md` is detailed enough that varghele can re-run any single step from a fresh `git pull` without re-reading this spec.
11. `python -m munin_bench.pipelines.run_litqa2 --in-corpus-only --seed 42` produces `results/litqa2/summary.md` covering ≥50 in-corpus questions with both retrieval and answer metrics, and explicit caveats about PaperQA2's training exposure.

**Add-on (Phase 6 below; not required for first paper submission):**

12. End-to-end RAG answer quality on a 50-query subset of the local pool, scored via RAGAS with an external LLM judge. See §4a for the full spec.

---

## 4a. Add-on: end-to-end answer quality (RAGAS)

**Status: optional.** Build this only after Phases 1–5 are committed and varghele explicitly asks for it. It needs an LLM-judge budget and is fragile to prompt drift, so it's not worth running until the retrieval picture is settled.

**Goal.** Show that Munin's *retrieval* improvements translate into *better answers* — not just better rankings. The paper's "End-to-end answer quality" subsection consumes this.

**Inputs:**

- The 50-query subset of the local pool (Phase 4). Pick the 50 with the highest annotator agreement to minimise label noise contaminating the answer evaluation. Persist the choice in `data/local/ragas_subset.txt` so runs are reproducible.
- The qrels from Phase 4d (ground-truth relevance, used to compute *context precision* and *context recall*).
- An external LLM judge. Default: Claude (Sonnet- or Opus-class via the Anthropic API). Document the model version and date — judge model affects scores.

**Pipeline:**

1. For each query, run the full Munin chat pipeline against the live retrieval service: persona = research, hybrid retriever = production (config 3 from Phase 4e). Capture: query, retrieved contexts (with DOIs), final assistant answer text, the model's emitted citations.
2. For each (query, contexts, answer), call RAGAS metrics:
   - **Faithfulness:** fraction of factual claims in the answer that are supported by the retrieved contexts. LLM judge breaks the answer into claims, then checks each against the contexts.
   - **Answer relevancy:** does the answer address the question? LLM judge generates synthetic questions from the answer, then measures their similarity to the original query.
   - **Context precision:** of the retrieved contexts, how many were actually relevant (per qrels)? Uses ground truth, not LLM judge.
   - **Context recall:** of the relevant documents in the qrels, how many appeared in the retrieved contexts? Uses ground truth.
3. Compute paired bootstrap CIs over the 50 queries for each metric.
4. Output `results/ragas/summary.md` with the four metric scores, CIs, and a per-query CSV for inspection.

**Implementation notes:**

- Use the official `ragas` Python package (>=0.1.x). Pin the version — RAGAS metric definitions have shifted between releases. Document the version in the run header.
- Run RAGAS twice with different judge models (e.g., Claude Sonnet and GPT-4-class) on a 10-query subset to check that scores correlate. If they don't, the judge is the bottleneck and the headline number is unreliable. Disclose openly.
- Cost: 50 queries × ~10 LLM calls per metric × 4 metrics ≈ 2000 LLM calls per run. Budget for two full runs (different judges) and a few debugging runs. Cap spend at a number varghele sets in advance.

**Optional extension — ARES calibration (Saad-Falcon et al., NAACL 2024):**

If RAGAS scores look noisy or implausible, add an ARES-style calibration step:

1. Hand-annotate 50 (query, answer, score) triples for *faithfulness* and *answer relevancy* using a 0/1 binary scale.
2. Treat these as the calibration set in the ARES framework.
3. Use ARES's prediction-powered inference to produce confidence intervals on the LLM judge's scores.
4. Report ARES-calibrated CIs alongside the raw RAGAS scores.

This is heavy; only do it if a reviewer specifically asks for calibrated answer-quality numbers.

**Caveats to disclose in `results/ragas/summary.md`:**

- LLM-as-judge bias (position, verbosity, self-preference). Cite Zheng et al. 2023, Panickssery et al. 2024, Stureborg et al. 2024.
- Judge-model dependence (your numbers are tied to whichever model you ran).
- Context-precision and context-recall depend on the qrels, which are pool-biased (see Phase 4 caveats — they cascade here).
- 50 queries is small. Don't overclaim from absolute scores; relative comparisons across configurations are more defensible than absolute numbers.

**Acceptance criteria for §4a:**

- `python -m munin_bench.pipelines.run_ragas --subset data/local/ragas_subset.txt --judge claude-sonnet-4-X` completes and writes `results/ragas/summary.md`.
- The summary includes all four RAGAS metrics with 95% bootstrap CIs.
- A second run with a different judge model is committed under `results/ragas/judge_correlation.md` showing per-query score correlation (Spearman, with CI).
- Caveats section is present and matches the bullets above.

**Don't build this until:**

- Phase 4e (local pool) is committed and the production retriever has beaten BM25 with p < 0.05.
- varghele has explicitly approved the LLM-judge budget.
- The paper's structure is otherwise stable (RAGAS scores depend on the persona prompt, which has been changing).

---

## 5. What to ask varghele before starting

Before writing any code, send varghele a short message confirming:

1. **Live retriever weights:** what does `backend/retrieval/models.py` have as the `HybridSearchRequest` default for `vector_weight` and `citation_weight`? The spec says 0.8/0.2 from `search.html`, but if the API default differs, that's the one to use.
2. **Where is the chat SQLite store** (so Phase 4a knows where to read from)?
3. **Embedding caching:** can you re-use any pre-computed SPECTER embeddings for the BEIR corpora, or should the harness embed from scratch every time?
4. **SLURM access:** do you want the harness pipelines to submit themselves as SLURM jobs (`sbatch run_beir.sbatch ...`) or stay interactive? Interactive is simpler; SLURM is more honest about the production environment.
5. **CSFCube:** is it acceptable to skip if `ir_datasets` doesn't have it cleanly, and document the omission?

If varghele doesn't respond within a working day, default: 0.8/0.2 weights, interactive runs, skip CSFCube with a note, embed BEIR corpora from scratch. Note the defaults in the first commit message.

---

## 6. Anti-goals

Things you might be tempted to do that hurt the paper:

- **Don't add a "smart re-ranker" or cross-encoder.** The harness measures the existing system. Innovations belong in the paper's Future Work, not in the evaluation harness.
- **Don't auto-tune α and β to maximise nDCG.** The paper claim is α=0.7 / β=0.3. The sweep is to *characterise sensitivity*, not to pick a winner. If the sweep shows a wildly better setting, that's a finding to *report honestly*, not a reason to silently change the paper claim.
- **Don't reformat the paper's results from scratch.** Produce CSV and Markdown that varghele can paste in. The paper structure is settled.
- **Don't write a paper-grade write-up of the harness.** That's the job of the paper. The harness's README is operator documentation, not narrative.
- **Don't suggest scope creep mid-build.** If you find something that *should* be in scope (e.g., a third hybrid retriever), open an issue or note it in `results/notes.md`. Don't silently expand the harness — varghele budgets for what's in this spec.

---

## 7. What to do if you get stuck

- Qdrant collection creation fails: check disk space first (BEIR corpora can be tens of GB embedded); fall back to a smaller `vector_size` setting only if you've verified disk is full.
- `ir_datasets` can't find a subset: try the newer `beir/<subset>` naming vs older paths; check the `ir_datasets` registry at <https://ir-datasets.com/>.
- SPECTER takes hours on a corpus: see §3g; move to GPU.
- Cohen's kappa is < 0.5: stop. The labels are unreliable. Tell varghele; expand the annotator pool or rewrite the labelling guidelines before continuing.
- Production retriever loses to BM25 on the local pool: the harness has a bug, OR the production retriever has a regression. Investigate both. Don't paper over a failure by changing weights.
- You realise the spec is wrong about something: update the spec in a separate commit, then proceed. Don't proceed silently with a deviation.

---

## Appendix A: One reference command per pipeline

```bash
# Phase 1: tests
cd backend/benchmarks && pytest -v

# Phase 3: BEIR (run per subset)
python -m munin_bench.pipelines.run_beir --subset scifact      --device cuda --seed 42
python -m munin_bench.pipelines.run_beir --subset trec-covid   --device cuda --seed 42
python -m munin_bench.pipelines.run_beir --subset nfcorpus     --device cuda --seed 42
python -m munin_bench.pipelines.run_beir --subset scidocs      --device cuda --seed 42

# Phase 4a: extract queries
python -m munin_bench.pipelines.build_pool --extract-queries --n 300 --seed 42

# (varghele curates queries.jsonl)

# Phase 4c: build pool
python -m munin_bench.pipelines.build_pool --build-pool --top-k 20

# Phase 4d: serve annotation UI
python -m munin_bench.pipelines.annotate_ui --port 5050

# (Both annotators label)

# Phase 4d: merge
python -m munin_bench.pipelines.merge_qrels --in qrels_*.tsv --out data/local/qrels.tsv

# Phase 4e: evaluate
python -m munin_bench.pipelines.run_local_pool --bootstrap 1000 --seed 42

# Phase 5: LitQA2 anchor (core test, ≥50 in-corpus questions required)
python -m munin_bench.pipelines.run_litqa2 --in-corpus-only --seed 42

# Phase 6: RAGAS (add-on; build only after varghele approves budget)
python -m munin_bench.pipelines.run_ragas \
    --subset data/local/ragas_subset.txt \
    --judge claude-sonnet-4-X \
    --seed 42
```

---

## Appendix B: Versions to pin (in `requirements.txt`)

Pin everything; eval reproducibility depends on it. Suggested floors (bump only if there's a reason):

```
qdrant-client>=1.7
neo4j>=5.14
sentence-transformers>=2.7
rank-bm25>=0.2.2
ir-datasets>=0.5.6
ranx>=0.3.20         # optional, for cross-checking metrics
scipy>=1.11
numpy>=1.26
pandas>=2.1
tqdm>=4.66
flask>=3.0           # annotation UI
pytest>=7.4

# Only needed for Phase 6 (RAGAS add-on); install separately to avoid
# pulling in a heavy LLM-judge dependency stack for the core build.
# ragas>=0.1.10
# anthropic>=0.30.0  # for Claude-class judge
# openai>=1.30.0     # for GPT-4-class judge (cross-correlation only)
```

---

## Appendix C: Where things live in the existing Munin codebase

- Production retriever: `backend/retrieval/main.py` → search `/search/hybrid`
- Citation score: `backend/retrieval/main.py` → `compute_citation_score`
- Neo4j wrapper: `backend/retrieval/main.py` → `get_citation_counts`
- Qdrant client init: `backend/retrieval/database.py`
- SPECTER model path: `backend/retrieval/database.py` → `SPECTER_MODEL_PATH`
- Chat store (for Phase 4a): grep for `chat.sqlite` or `chat_store.py` under `backend/retrieval/`
- API contract: `shared/docs/BACKEND-API.md`
- The paper's evaluation section: `munin-paper.tex` § Evaluation (currently lines ~857–956)

---

End of spec. If anything here contradicts what you see in the codebase, the code wins — update the spec, then proceed.
