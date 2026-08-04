# 10 Reproduce

Exact commands, environment, and the operational traps. Everything here is
copied from the working reproduce block in the results log, not reconstructed.

---

## 1. Read this first: two traps that each cost a day

### Trap 1: concurrency must not exceed vLLM `--max-num-seqs`

The production vLLM serves with `--max-num-seqs 2`. Running an evaluation above
that **silently degrades quality rather than erroring**. This is what produced a
misleading 0.688 agentic figure that was very nearly published as the headline.

All cost-bearing arms run at **concurrency 1**, which is also what makes
wall-clock a usable cost proxy (SLURM accounting is unavailable, so there is no
`sacct` alternative).

### Trap 2: egress defaults off, and it changes results

Evaluations default to `egress=off` (`X-Munin-Egress`) so a benchmark cannot
spend the commercial search or Semantic Scholar quota. Set
`MUNIN_EVAL_EGRESS=full` deliberately for a live-web run, and **record which you
used**: the Track C2 result changed sign on exactly this variable (accuracy
0.080 at `off` versus 0.740 at `full` on the absent arm).

### Trap 3, added 2026-08: check the encoder defaults

Before 2026-08, `PAPER_ENCODER` / `PAPERS_COLLECTION` defaulted to the retired
SPECTER pair, so any run without the cluster secrets file silently benchmarked
the wrong corpus and looked like it worked. The defaults now match production
(`bge-large` / `papers_bge`), and a half-flip is a boot failure. Verify with
`/api/status` before trusting a run.

---

## 2. Environment

```bash
cd backend/benchmarks
export PYTHONPATH=$HOME/.cache/munin_bench_deps:.   # SPECTER stack + rank_bm25 / ir_datasets / pyarrow
export NEO4J_PASSWORD=...                          # from the cluster secrets file
PY=/opt/munin/services/pipeline/venv/bin/python
```

The paper-pipeline virtualenv already carries `sentence-transformers`,
`qdrant-client`, and `neo4j`; only `rank_bm25` is missing, so the lightweight
setup on the cluster is:

```bash
$PY -m pip install --target=$HOME/.cache/munin_bench_deps rank_bm25
```

On a clean machine: `python -m venv .venv && pip install -r requirements.txt`.
A slim `requirements-ci.txt` exists so pure-function tests in CI do not pull
torch plus the CUDA wheels.

Package versions are recorded in every scorecard's provenance header
(`numpy`, `scipy`, `sentence_transformers`, `qdrant_client`), along with the
git SHA, the served model id read live from vLLM's `/v1/models`, the encoder,
the corpus snapshot, and the seed (42).

---

## 3. Unit gates (no infrastructure needed)

```bash
$PY -m pytest tests/     # pure-function tests: metrics, bootstrap, Wilcoxon,
                         # citation-score math, RRF math. ~0.3 s
```

These are the Phase 1-2 gates and include the regression test that pins the
citation-re-rank fixture ranking which inverted under the score-scale bug.
**Note that this suite previously asserted the unnormalised formula**, so it
encoded the defect and failed on the fix; it was rewritten against hand-derived
expectations.

---

## 4. One-command full run (Track E)

```bash
$PY -m munin_bench.pipelines.run_all \
   --tag <label> \
   --tracks beir-scifact,litqa2-retrieval,litqa2-answer,faithfulness,abstention,ablation \
   --with-reliability --certify --date <YYYY-MM-DD>
```

Writes one committed scorecard (`scorecards/<date>_<tag>.{json,md}`).
`--certify` checks the run against `certification_thresholds.json` and emits
PASS/FAIL. `--with-reliability` folds the behavioural-layer PASS/FLAKY/FAIL
summary into a separate `reliability` key that is never cited in the paper.

Regression diff between two runs (paired bootstrap, not eyeballed CIs):

```bash
$PY -m munin_bench.pipelines.compare <old>.json <new>.json
```

Target runtime for a full run is under 8 hours. In practice the answer track at
the 900 s deadline is 4-5 hours per arm, so a three-arm ablation is an
overnight-plus job.

---

## 5. Per-track commands

### Track A: retrieval

```bash
MUNIN_BENCH_SPECTER_DEVICE=cpu $PY -m munin_bench.pipelines.run_beir --subset scifact
$PY -m munin_bench.pipelines.run_litqa2 --track retrieval
MUNIN_BENCH_SPECTER_DEVICE=cpu $PY -m munin_bench.pipelines.run_bakeoff --subset scifact
$PY -m munin_bench.pipelines.run_litsearch
```

### Track A/D: end-to-end answering

```bash
$PY -m munin_bench.pipelines.run_litqa2 --track answer --concurrency 1
```

Concurrency must be <= vLLM `--max-num-seqs`. The deadline is a flag; 900 s is
the current setting and 300 s truncates long answers (11 of 199 on the run that
was measured).

### Track B: faithfulness

```bash
$PY -m munin_bench.faithfulness.smoke_minicheck                        # B1 sanity
$PY -m munin_bench.faithfulness.validate_ragtruth --n-per-cell 20 --date <D>   # B2 judge validation

MUNIN_BENCH_ENTAILMENT_DEVICE=cuda:0 $PY -m munin_bench.faithfulness.faithfulness_runner \
  --base-url http://127.0.0.1:8080 --email <eval-account> \
  --arm agentic-live --limit 40 --concurrency 1 --date <D>             # B3+B4
```

Capture can run on CPU; scoring wants the GPU.

### Track C1: fabricated-paper abstention

```bash
$PY -m munin_bench.abstention.fabricate --n 100        # freeze the set (Crossref-verified)

PYTHONPATH=$HOME/.cache/munin_bench_deps:. $PY -m munin_bench.abstention.run_c1 \
  --base-url http://127.0.0.1:8080 --email <eval-account> --date <D>
```

### Track C2b: paired shadow-corpus abstention

```bash
# Build papers_shadow (= papers_bge minus the source papers) and freeze the questions
PYTHONPATH=$HOME/.cache/munin_bench_deps:. $PY -m munin_bench.abstention.build_shadow --n 50

# Bring up the shadow retrieval instance on :8081 (docker/docker-compose.shadow.yml)

PYTHONPATH=$HOME/.cache/munin_bench_deps:. $PY -m munin_bench.abstention.run_c2 \
  --arm present --base-url http://127.0.0.1:8080 --email <eval-account> --date <D>
PYTHONPATH=$HOME/.cache/munin_bench_deps:. $PY -m munin_bench.abstention.run_c2 \
  --arm absent  --base-url http://127.0.0.1:8081 --email <eval-account> --date <D>   # writes the paired scorecard
```

**Both arms must run at the same egress setting.** The shadow build verifies
that the removed DOIs are present in the live collection and absent from the
shadow, and reports the counts.

Re-score existing captures without regenerating anything (this is how the CIs
were added to the 2026-07-27 scorecards, and the cheap path whenever scoring
changes but the captures do not):

```bash
# egress=off pair, preserving the capture-time git sha, plus the
# question-paired delta against the earlier 2026-07-10 captures
PYTHONPATH=$HOME/.cache/munin_bench_deps:. $PY -m munin_bench.abstention.run_c2 \
  --rescore --date 2026-07-27 --keep-git-sha 8d0366c --vs-suffix 2026-07-10

# egress=full pair
PYTHONPATH=$HOME/.cache/munin_bench_deps:. $PY -m munin_bench.abstention.run_c2 \
  --rescore --suffix egressfull --date 2026-07-27 \
  --out-tag abstention-c2-shadow-egressfull --keep-git-sha 8d0366c
```

A re-scored scorecard records `rescored_by` and `rescored_at_git_sha` alongside
the original `git_sha`, so a later reader can see that the verdicts and the
statistics came from different passes.

### Track C: risk-coverage

```bash
PYTHONPATH=$HOME/.cache/munin_bench_deps:. $PY -m munin_bench.abstention.risk_coverage
```

Derives operating points from already-captured verdicts. No new inference. Emits
`mixed_generations` / `mixed_egress` provenance markers when the points do not
share a condition.

### Track D: harness ablation

```bash
for arm in bare rag agentic; do
  PYTHONPATH=$HOME/.cache/munin_bench_deps:. $PY -m munin_bench.ablation.run_arm --arm $arm --n 199
done

$PY -m munin_bench.ablation.compare --date <D>
$PY -m munin_bench.ablation.abstain_arms --arm bare       # and --arm rag: abstention per arm on the fabricated set
MUNIN_BENCH_ENTAILMENT_DEVICE=cuda:0 $PY -m munin_bench.ablation.faithfulness --date <D>
```

---

## 6. What has not been run

As of 2026-08-04:

- BEIR `nfcorpus` / `scidocs` / `trec-covid`.
- **Phase 4 local query pool** (deferred: blocked on human query curation and
  two-annotator qrels, not on compute), and the two items that depend on it
  (Track C stratum 2, Track B answer-level local pool).
- Track F throughout.
- QASPER (Benchmark T10).
- RAGAS external cross-check (Benchmark T9, dropped with a stated reason).

---

## 7. Artifact locations in the repository

| Artifact | Path |
|---|---|
| Canonical results log | `backend/benchmarks/RESULTS.md` |
| Claim-to-scorecard index | `PAPER.md` |
| Scorecards (58 JSON, 47 Markdown) | `backend/benchmarks/scorecards/` |
| Benchmark harness | `backend/benchmarks/munin_bench/` |
| Certification thresholds | `backend/benchmarks/certification_thresholds.json` |
| Paper track: plans, specs, open items | `docs/paper-track/` |
| Agent track: architecture, deep research | `docs/agent-track/` |
| Design decisions (the *why*) | `shared/docs/DECISIONS.md` |
| Corpus quality evidence | `backend/docs/corpus-quality/` |
| Harness audit | `docs/architecture/HARNESS-AUDIT-2026-05.md` |
| This kit | `docs/paper-kit/` |

Per-query raw artifacts live under `backend/benchmarks/results/` and are
gitignored: they are regenerable, and the scorecards carry the per-query arrays
that any paired test needs.

---

## 8. Reproducibility posture, for the paper

What a third party can and cannot reproduce, stated honestly:

| Reproducible | How |
|---|---|
| All metric and statistics code | Pure-function unit tests, no infrastructure |
| BEIR / SciFact and LitSearch results | Public corpora and qrels; download scripts with checksums |
| The abstention benchmark items | The fabricated set is generated from the corpus and its ground-truth JSON is committed, because it is small and it *is* the benchmark |
| Every reported aggregate and paired test | Scorecards carry per-query arrays |

| Not reproducible externally | Why |
|---|---|
| LitQA2 answer runs | Require the ~68k-paper private corpus and the deployed harness |
| The shadow-corpus experiment | Requires the private corpus and a second retrieval instance |
| Exact wall-clock costs | Hardware-specific (single RTX 5090, `--max-num-seqs 2`) |
| Benchmark data files | Never committed, for size and licence reasons; each dataset has a download script with a checksum so `data/` rebuilds deterministically |
