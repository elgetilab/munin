# Encoder migration - Phase A implementation tasks

Non-invasive full-corpus validation of BGE-large-en-v1.5 vs SPECTER-v1. Build
`papers_bge` (1024-d) alongside the live `papers` (768-d), run the eval against
it, and `compare` scorecards. NOTHING here is read by production. Parent plan:
`ENCODER-MIGRATION-PLAN.md`. Status: NOT started (2026-07-03).

## Outcome / gate

A committed `scorecards/<date>_bge-large.json` and a `compare` vs
`baseline-specter-v1` showing the LitQA2 retrieval lift (Recall@10, MRR) holds on
the FULL 68k corpus with paired-bootstrap significance. If it does not hold,
STOP and reassess (the 5k pool was optimistic).

## Facts established (repo read 2026-07-03)

- Corpus: `papers` = 68,121 points; ~78% have an abstract (rest title-only -
  embed `title\n\nabstract` as-is, matching production).
- Retriever query-encode sites to touch (eval code, backward-compatible):
  `retrievers/specter_dense.py:28` and `retrievers/agent_retriever.py:61`
  (`self.specter.encode(query)`), both need an optional `query_prefix`.
- `litqa2_runner.run()` hardcodes collection `"papers"` at lines 135-139;
  parameterize (encoder model + collection + query_prefix).
- `clients.load_specter(device)` -> generalize to `load_encoder(path, device)`.
- BGE-large-en-v1.5: 1024-d. Prompting (must match the bake-off): query
  instruction `"Represent this sentence for searching relevant passages: "` on
  QUERIES only; documents embedded RAW.
- No free GPU now (GPU0 busy with a 16 GB batch job + the pipeline watcher, GPU1
  = vLLM). `vi` can `sbatch` shard jobs; CPU embedding is the reliable fallback.

## Tasks

- [x] **A1. Query-prefix support in retrievers.** `query_prefix` added to
  `SpecterDenseRetriever` + `AgentRetriever` (query-only; default "" = SPECTER).
- [x] **A2. Generic encoder loader.** `clients.load_encoder(model, device,
  hf_fallback)`; `load_specter` now a wrapper. `config.BGE_LARGE_*` +
  `ENCODER_PRESETS` added. (Model loads from the HF id into cache; no /opt write
  needed for Phase A.)
- [~] **A3. Build `papers_bge`.** `munin_bench/pipelines/build_papers_bge.py`
  written + RUNNING (CPU background, ~1-2 h). Idempotent/resumable, reads
  existing payloads, upserts under the SAME id + payload. Reusable for Phase B.
- [x] **A4. Parameterize the runner.** `litqa2_runner.run(..., collection,
  query_prefix, id_field)`; `run_all --encoder {specter-v1,bge-large}` threads
  model/collection/prefix. Encoder label flows into the scorecard header.
- [ ] **A5. Validate.** (BLOCKED on A3 finishing.) `run_all --tag bge-large
  --tracks litqa2-retrieval --encoder bge-large`; then `compare
  scorecards/*_baseline-specter-v1.json scorecards/*_bge-large.json`. Commit the
  BGE scorecard + a short note in `RESULTS.md`. Apply the gate above.
- [ ] **A6. (optional) BEIR/bake-off for the record** already covered by the
  committed bake-off; no action unless we want a BEIR scorecard too.

## Non-goals (Phase A)

- No change to `backend/retrieval/*` or `paper_pipeline.py` (that is Phase B).
- No cutover; production keeps reading `papers`+SPECTER throughout.
- `papers_bge` is kept after Phase A (Phase B reuses / rebuilds it).

## Open questions

1. **Re-embed compute:** CPU background (~1-2 h, reliable, I run it now) vs a
   `sbatch` shard:batch GPU job (~30 min, but GPU0 is busy so scheduling is
   uncertain). Default: CPU background unless you want me to try the GPU job.
2. **`papers_bge` retention:** keep it after Phase A for Phase B reuse (default
   yes), or drop it once validated to save Qdrant RAM (~280 MB)?
