# Encoder migration - Phase C measurement command sheet

Measure the end-to-end payoff of the SPECTER -> BGE cutover and produce the
paper's headline before/after. Run AFTER the Phase B cutover is live (per
`ENCODER-PHASE-B-IMPLEMENTATION.md`). Parent: `ENCODER-MIGRATION-PLAN.md`.

> **STATUS: EXECUTED 2026-07-06.** Headline table below is filled with the final
> clean-parser paired numbers. Canonical writeup +
> full context: **`backend/benchmarks/RESULTS.md`**. This file remains the
> re-run command sheet for future model/encoder swaps.

All commands run from `backend/benchmarks/` with:

```bash
export PYTHONPATH=$HOME/.cache/munin_bench_deps:.
export NEO4J_PASSWORD=...            # from cluster.env
PY="/opt/munin/services/pipeline/venv/bin/python"
export MUNIN_BENCH_SPECTER_DEVICE=cpu
```

## What's already done (retrieval headline - no re-run needed)

Retrieval was measured on the FULL corpus in Phase A and is committed:
`scorecards/2026-07-03_{baseline-specter-v1,bge-large}.json`. The deployed
service now reads `papers_bge`, i.e. the exact collection the `bge-large`
scorecard used, so that number stands. Regenerate the headline table anytime:

```bash
$PY -m munin_bench.pipelines.compare \
    scorecards/2026-07-03_baseline-specter-v1.json \
    scorecards/2026-07-03_bge-large.json
# -> AgentRetriever Recall@10 0.437 -> 0.729 (+0.29, p~0), etc.
```

## The Phase C measurement: answer track before/after

The answer track hits the LIVE chat service, so it measures whatever encoder is
DEPLOYED - which is why the SPECTER baseline must be captured while SPECTER is
still live. Each run is ~60-90 min at concurrency 1 (one vLLM slot; polite to
users, per memory reference-prod-eval-harness).

### 1. BEFORE cutover - SPECTER answer baseline (while SPECTER is deployed)

```bash
$PY -m munin_bench.pipelines.run_all --tag answer-specter-v1 \
    --tracks litqa2-answer --encoder specter-v1 --date <YYYY-MM-DD>
git add scorecards/<date>_answer-specter-v1.json* && git commit ...
```

(Shortcut: we already have a SPECTER answer run - `results/litqa2/answer.json`,
acc 0.427 / prec 0.817 - but it predates the scorecard format, so a fresh
run_all gives a per-question array that `compare` can pair-bootstrap. Reusing the
old number is fine for an UNPAIRED reference if you want to skip the 90-min run.)

### 2. AFTER cutover - BGE answer (once the service serves BGE)

```bash
$PY -m munin_bench.pipelines.run_all --tag answer-bge-large \
    --tracks litqa2-answer --encoder bge-large --date <YYYY-MM-DD>
git add scorecards/<date>_answer-bge-large.json* && git commit ...
```

### 3. Compare - the headline before/after

```bash
$PY -m munin_bench.pipelines.compare \
    scorecards/<date>_answer-specter-v1.json \
    scorecards/<date>_answer-bge-large.json
# accuracy delta with a paired bootstrap over the same 199 questions.
```

Expected direction: accuracy tracks Recall@10, so ~0.43 -> toward ~0.73
(projection from Phase A); past PaperQA2's 0.66 would be the result. Precision
(of attempted) and abstention rate also move - report all three.

## Paper headline table (FINAL, 2026-07-06)

Answer rows are the clean-parser, same-corpus paired runs
(`scorecards/2026-07-06_answer-{specter-v1-v2,bge-large-v2}.json`); the earlier
0.427 SPECTER answer number was parser-confounded (see the method note below and
in RESULTS.md).

| metric | SPECTER-v1 | BGE-large | Δ (p) | PaperQA2 |
|---|---|---|---|---|
| Retrieval Recall@10 (AgentRetriever) | 0.437 | 0.729 | +0.29 (~0) | - |
| Retrieval MRR | 0.276 | 0.573 | +0.30 (~0) | - |
| Answer accuracy | 0.422 | 0.497 | +0.075 (0.028) | 0.660 |
| Answer precision (of attempted) | 0.832 | 0.853 | +0.022 (n.s.) | ~0.88 |

> **Canonical writeup: `backend/benchmarks/RESULTS.md`** (sections "Encoder
> migration Phase A / Phase C"). Headline: retrieval is a large, decisive win;
> answer accuracy improves significantly but modestly (+0.075, p=0.028) because
> the model abstains on ~40% of questions even with good retrieval. The naive
> "accuracy ~= recall -> ~0.73" projection did NOT hold. Method note: the answer
> parser had a bug (dropped long-reasoning-then-abstain answers, truncated at
> 180s, as "unparseable"); fixed with a hardened parser + 300s deadline + saved
> text (commit 3630f13) before the final measurement.

## Retire the old collection (after soak)

Once BGE is confirmed healthy in production (watch chat retrieval quality / user
reports for a few days) and Phase C numbers are captured:

```bash
# frees ~210 MB; do NOT run until you are certain rollback won't be needed.
$PY -c "from munin_bench.clients import get_qdrant; get_qdrant().delete_collection('papers')"
```

Keep `papers` until then - it is the rollback path. Do not delete it before
Phase C is committed and the deployment has soaked.

## Notes
- Concurrency 1 for the answer track (vLLM single-GPU `--max-num-seqs 2`; the
  app-level 180s deadline guard is already in the runner).
- Each answer scorecard stamps the encoder + corpus snapshot (Track E), so the
  before/after is provenance-clean.
- If retrieval also wants a live re-confirmation post-cutover, re-run
  `run_all --tracks litqa2-retrieval --encoder bge-large` (should match the Phase
  A `bge-large` scorecard; the collection did not change).
