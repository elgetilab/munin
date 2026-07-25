# Munin retrieval evaluation harness

Paper-grade, reproducible retrieval benchmarks. Build spec:
`../../todo_v2/RETRIEVAL-EVAL-SPEC.md`; suite-wide plan:
`../../todo_v2/EVAL-SUITE-MASTER-PLAN.md`. This folder is benchmark code only — it
never ships in `deploy.sh`, and it only ever *reads* the production Qdrant /
Neo4j (BEIR subsets get their own `eval_*` collections in Phase 3).

## Status

| Phase | What | State |
|---|---|---|
| 1 | metrics (nDCG/Recall/MRR/Hits), paired bootstrap, Wilcoxon | **done** |
| 2 | retrievers (BM25, SPECTER-dense, Agent, citation-rerank, RRF, 2-hop) | **done** |
| 3 | BEIR runner | **done** (SciFact validated: BM25 0.652 ≈ published; SPECTER 0.479) |
| 4 | local pool benchmark | 4a done (extractor); 4b needs varghele-curated `queries.jsonl`; only 42 candidates so far |
| 5 | LitQA2 anchor | **done** — retrieval (agent recall@10=0.44) + answer (acc 0.43 / prec 0.82 vs PaperQA2 0.66) |
| E | regression harness — `run_all` → committed `scorecards/`, `compare` paired-diff | **done** |
| — | encoder bake-off (BGE/E5 ≫ SPECTER-v1 on SciFact + Munin pool; see `RESULTS.md`) | **done** |

**Regression harness (Track E):** `python -m munin_bench.pipelines.run_all
--tag <label> --tracks beir-scifact,litqa2-retrieval[,litqa2-answer]` writes a
provenance-stamped `scorecards/<date>_<tag>.{json,md}` (committed, with per-query
arrays). `python -m munin_bench.pipelines.compare <old>.json <new>.json` diffs
two runs with a paired bootstrap — the before/after check for a model or encoder
swap.

`AgentRetriever` is THE production retriever (the chat agent's `paper_search`
path). `CitationRerankRetriever` is the search-page config, reported alongside.

## Environment

Phase 1 needs only `numpy`, `scipy`, `pytest` (present in the base anaconda
env). Phase 2's live gate needs the SPECTER stack. On hugin the paper-pipeline
venv already has `sentence-transformers`, `qdrant-client`, `neo4j`; only
`rank_bm25` is missing, so the lightweight setup is:

```bash
PYBIN=/opt/munin/services/pipeline/venv/bin/python
$PYBIN -m pip install --target=$HOME/.cache/munin_bench_deps rank_bm25
```

(For a clean machine: `python -m venv .venv && pip install -r requirements.txt`.)

## Run the tests

```bash
python -m pytest backend/benchmarks/tests/      # 56 tests, ~0.3s
```

Pure-function tests (metrics, bootstrap, Wilcoxon, citation-score math, RRF
math) — no infra needed.

## Run the Phase 2 gate (live sanity check)

Instantiates all seven retrievers and runs `"protein folding"` against the live
`papers` collection, printing top-3 each. `NEO4J_PASSWORD` is in the cluster
`.env` (symlinked at `/opt/munin/docker/.env -> /opt/hugin/config/cluster.env`).

```bash
cd backend/benchmarks
export NEO4J_PASSWORD=...        # from the cluster .env
PYTHONPATH=$HOME/.cache/munin_bench_deps:. \
  /opt/munin/services/pipeline/venv/bin/python -m munin_bench.pipelines.gate_phase2
```

Expected: `GATE PASS` with every retriever returning 10 hits.

## Run a BEIR subset (Phase 3)

```bash
cd backend/benchmarks
PYTHONPATH=$HOME/.cache/munin_bench_deps:. MUNIN_BENCH_SPECTER_DEVICE=cpu \
  /opt/munin/services/pipeline/venv/bin/python -m munin_bench.pipelines.run_beir \
  --subset scifact          # or nfcorpus | scidocs | trec-covid | csfcube
```

Builds an isolated `eval_<subset>` Qdrant collection (SPECTER over
`title\n\nabstract`, matching production), runs BM25 / SPECTER-dense /
citation-rerank / RRF[BM25,SPECTER], writes
`results/beir/<subset>/summary.{json,md}`. The eval never touches `papers`.
SPECTER embeds on CPU by default (GPUs busy with vLLM); set
`MUNIN_BENCH_SPECTER_DEVICE=cuda` for large subsets (trec-covid is 171k docs)
when the cards are free. `--rebuild` drops + re-embeds the collection.

**Validation (SciFact):** BM25 nDCG@10 = 0.652 reproduces the published BEIR
number (~0.665), and our metric is bit-identical to `pytrec_eval`. SPECTER-v1
dense = 0.479 (a citation embedder, expectedly below BM25 on claim queries);
citation-rerank degenerates to dense on BEIR (no graph), confirmed empirically.
See `../../todo_v2/RETRIEVAL-EVAL-SPEC.md` Phase 3 for the gate rationale and the
`\n\n`-vs-`[SEP]` production finding.

## Frozen variant set (AgentRetriever)

The agent retriever fans a query out to a FROZEN, committed variant set rather
than a live LLM expansion (a paper-grade benchmark can't depend on a
non-deterministic expander). The committed file is generated ONCE from Phase 4's
`data/local/queries.jsonl` and is a Phase-4 deliverable; until then the gate
uses an inline demo set. Regenerate with:

```bash
python -m munin_bench.frozen_variants.regen_variants \
  --queries data/local/queries.jsonl --out munin_bench/frozen_variants/local_pool_variants.json
```

Track A measures the *ranker* on the frozen input; expansion drift under a
model swap is a live-harness behaviour measured by Track D/E, not here.

## Layout

```
munin_bench/
  config.py            constants (mirrors production defaults; no secrets)
  clients.py           Qdrant / Neo4j / SPECTER factories
  metrics/             Phase 1: ir_metrics, bootstrap, significance
  retrievers/          Phase 2: base + 6 retrievers
  frozen_variants/     AgentRetriever variant regen (provenance)
  pipelines/           gate_phase2 (+ Phase 3-5 runners to come)
tests/                 gold-value unit tests
data/                  gitignored (BM25 cache, downloaded datasets)
scorecards/            committed (small JSONs)
results/               gitignored
```
