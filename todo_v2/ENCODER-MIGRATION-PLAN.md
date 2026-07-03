# Paper encoder migration: SPECTER-v1 -> BGE-large-en-v1.5

**Status: PLANNED, not started (2026-07-03).** Written for review; no production
change until Phase A validates and the cutover is approved.

## Why

The eval suite showed retrieval is the bottleneck (LitQA2 answer accuracy 0.43
~= retrieval Recall@10 0.44) and SPECTER-v1 is a weak, dated document-embedder
used out-of-distribution on short queries. The encoder bake-off
(`RESULTS.md`) is decisive:

| | SPECTER-v1 | BGE-large-en-v1.5 |
|---|---|---|
| SciFact nDCG@10 | 0.479 | 0.746 (+56%, beats BM25) |
| Munin/LitQA2-pool Recall@10 | 0.663 | 0.837 (+0.17, p~0) |
| Munin/LitQA2-pool Recall@1 | 0.392 | 0.661 (+68%, p~0) |
| Munin/LitQA2-pool MRR | 0.493 | 0.734 (p~0) |

SciNCL (scientific SPECTER successor) was NOT significant on our data (p=0.70),
so the lever is a general SOTA retriever, not scientific pretraining.

**Chosen encoder: `BAAI/bge-large-en-v1.5`** (1024-d). English-only, which fits
the overwhelmingly-English corpus + queries. Prompting (must match the bake-off
for the gains to hold): apply the query instruction
`"Represent this sentence for searching relevant passages: "` to QUERIES only;
embed documents raw (`title\n\nabstract`, no prefix). If non-English queries
become common, revisit `multilingual-e5-large`.

## The structural constraint

BGE-large is **1024-d**; the `papers` Qdrant collection is **768-d** (SPECTER).
Qdrant cannot change a collection's vector size in place, so this is a
build-new-collection (`papers_bge`) then cutover migration. Keeping `papers`
intact gives instant rollback.

## Phase A - validate on the full corpus (non-invasive; `vi` can run it)

Goal: confirm the pool result on the real 68k corpus BEFORE any production
change. Nothing here is read by production.

1. Download `BAAI/bge-large-en-v1.5` to `/opt/munin/data/models/bge-large`.
2. Build `papers_bge` (1024-d, cosine): scroll `papers`, and for each point
   re-embed `title\n\nabstract` (raw, no prefix) with BGE, upsert into
   `papers_bge` with the SAME id / DOI / payload, new vector. ~68k points.
   Metadata is reused from the existing payloads; no GROBID re-parse.
   Cost: a GPU window (BGE-large 335M, ~30-60 min) or a few CPU hours.
3. Add optional `query_prefix` support to the eval retrievers
   (`SpecterDenseRetriever` / `AgentRetriever` encode queries raw today).
4. Run `run_all --tag bge-large --tracks litqa2-retrieval` pointed at
   `papers_bge` + BGE (+ query prefix), then
   `compare scorecards/<...>_baseline-specter-v1.json <...>_bge-large.json`.
   This is the FULL-corpus lift (the pool was optimistic). Gate: Recall@10 /
   MRR improve significantly (paired bootstrap, same 199 questions).
5. If it does not hold on the full corpus, STOP and reassess. If it holds,
   proceed to Phase B.

## Phase B - cutover (production code + deploy; varghele/root)

Switch the QUERY side to BGE + `papers_bge`. Keep `papers`+SPECTER for rollback.
Touch points (from code inspection):
- `retrieval/database.py`: add a paper encoder loader for BGE-large (analogous
  to `get_bge` for user-docs bge-base) + the papers collection name.
- `retrieval/mcp/tools/papers.py` (`_qdrant_search_one`): embed each query
  variant with BGE + the query instruction prefix; query `papers_bge`.
- `retrieval/main.py` (`/search/hybrid`): same query-encoder + collection swap.
- `scripts/pipeline/paper_pipeline.py` (line ~1392): NEW papers must embed with
  BGE (raw doc) into `papers_bge`, else new ingests break the collection.
- Deploy via `deploy.sh` (retrieval + pipeline). Coordinated: the query side and
  the pipeline embed side must flip together.
- Also fix the `\n\n` vs `[SEP]` finding is moot here (BGE has no SEP
  convention; raw title\n\nabstract is correct for BGE).

Rollback = flip the collection name + encoder back to `papers`+SPECTER (both
kept live) and redeploy.

## Phase C - measure end-to-end + retire

1. Re-run `run_all --tracks litqa2-answer` on the new stack (the ultimate
   payoff): confirm answer accuracy rises toward/past PaperQA2's 0.66.
2. Commit the post-cutover scorecard; `compare` vs the SPECTER baseline is the
   headline before/after for the paper.
3. Soak (watch live chat retrieval quality / user reports), then retire the old
   `papers` collection once confident.

## Risks / notes
- Re-embed compute needs a GPU window or hours of CPU. `vi` can run it; a SLURM
  batch job on a free shard/GPU is the clean path.
- 1024-d collection is marginally larger in Qdrant RAM (68k x 1024 x 4B ~= 280MB
  vs 210MB) - negligible.
- Every eval run stamps `encoder` in the scorecard (Track E), so SPECTER and BGE
  numbers are never silently compared - same discipline as the SPECTER2 note in
  `backend/CLAUDE.md`.
- The query instruction prefix is load-bearing: omitting it on queries loses
  part of the gain. Keep docs raw.
- Provenance: record the corpus snapshot (paper count) with each run; the corpus
  grows, so compare like-for-like via the scorecard header.

## Open decisions (resolved / pending)
- Encoder: **BGE-large-en-v1.5** (resolved 2026-07-03).
- Cutover strategy: **new `papers_bge` collection + flip** (rollback-safe).
- Compute for re-embed: GPU window vs CPU - TBD when Phase A is scheduled.
- Who runs the Phase B deploy: `vi` prepares code + eval; the production cutover
  is varghele/root.
