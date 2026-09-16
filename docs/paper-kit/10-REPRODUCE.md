# 10 Reproduce

Exact commands, environment, and the operational traps. Everything here is
copied from the working reproduce block in the results log, not reconstructed.

---

## 1. Read this first: two traps that each cost a day

### Trap 1: concurrency must not exceed vLLM `--max-num-seqs`

Production serves with `--max-num-seqs 8` on the TP=2 profile and 2 on the
single-GPU profile; read the value out of the running job before starting.
Running an evaluation above it **silently degrades quality rather than
erroring**. This is what produced a misleading 0.688 agentic figure that was
very nearly published as the headline.

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

The benchmark CLI has the same trap on its own side and it was **not** fixed:
`run_all --encoder` still defaults to `specter-v1`. Pass `--encoder bge-large`
explicitly on every retrieval-bearing run (section 4).

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

Package versions are recorded in every `run_all` scorecard's provenance
header (`numpy`, `scipy`, `sentence_transformers`, `qdrant_client`), along
with the git SHA, the served model id read live from vLLM's `/v1/models`, the
encoder, the corpus snapshot, and the seed (42). **The ablation, faithfulness,
abstention and risk-coverage scorecards do not carry that header**; their
runners recorded `track / n_paired / git_sha / date` only, and the backbone,
serving profile, egress per arm and arm-matching facts were backfilled into
top-level fields on 2026-09-15 from `RESULTS.md`'s dated sections (each file
says so in `provenance_note`). Before 2026-08-26 is Qwen3.6, from 2026-08-26
is Qwen3.8.

**Backbone and serving, at the headline runs:** `qwen3.8-27b`
(`cyankiwi/Qwen3.8-27B-AWQ-INT4`) on vLLM TP=2, 64k window, `--max-num-seqs
8`, `reasoning_effort=medium`. The bare/RAG arms read the model name and
reasoning effort from `VLLM_MODEL_NAME` and `LLM_REASONING_EFFORT` (defaults
match production); set both explicitly after any model switch, or the arms
silently diverge from the agentic path.

**Cost of a full Track D pass at `egress=full`:** roughly 331 `web_search`
calls and ~1,190 billed Brave requests (~$6) for the agentic arm; the bare and
RAG arms make no external calls. Around 9 GPU-hours at concurrency 1 for the
agentic arm on the dense 27B (157 s/query), minutes for the other two.

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
   --tag <label> --encoder bge-large \
   --tracks litqa2-retrieval,litqa2-answer,faithfulness,abstention,ablation \
   --with-reliability --certify --date <YYYY-MM-DD>
```

Writes one committed scorecard (`scorecards/<date>_<tag>.{json,md}`).
`--certify` checks the run against `certification_thresholds.json` and emits
PASS/FAIL. `--with-reliability` folds the behavioural-layer PASS/FLAKY/FAIL
summary into a separate `reliability` key that is never cited in the paper.

**`--encoder bge-large` is not optional.** `run_all` defaults to `specter-v1`,
the retired 768d `papers` collection, so the command without it reproduces the
pre-migration retrieval numbers (Recall@10 0.44) rather than the ones claimed
here (0.73). `beir-scifact` is deliberately absent from the track list: that
track builds a 768d eval collection and `run_all` refuses it with any preset
other than `specter-v1`, so it cannot share a run with the BGE tracks. The
SciFact anchor is its own command, `run_beir --subset scifact` (SPECTER) and
`run_bakeoff --subset scifact` (per-encoder), see section 5.

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
MUNIN_EVAL_EGRESS=full $PY -m munin_bench.pipelines.run_litqa2 --track answer --concurrency 1
```

**`MUNIN_EVAL_EGRESS=full` is required to reproduce the reported numbers.**
The runner defaults to `egress=off` (Trap 2), which measures a corpus-only
system; the 2026-07-24 run predates the guard and therefore ran with full
egress, and the 2026-09-14 run set it explicitly. Concurrency must be <= vLLM
`--max-num-seqs`. The deadline is not a CLI flag: 900 s is the default of
`_ask_chat(deadline=...)` in `litqa2_runner.py`, and 300 s truncates long
answers (11 of 199 on the run that
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

# run_c1 RESUMES from c1_runs/c1.capture.jsonl. Move the previous capture aside
# first, or the "new" scorecard re-scores the old answers under a new date.
mv c1_runs/c1.capture.jsonl c1_runs/c1.capture.<previous-date>.jsonl
MUNIN_EVAL_EGRESS=full PYTHONPATH=$HOME/.cache/munin_bench_deps:. $PY -m munin_bench.abstention.run_c1 \
  --base-url http://127.0.0.1:8080 --email <eval-account> --concurrency 1 --date <D>
```

`egress=full` is the reported (harder) condition for C1; the runner defaults
to `off`. `run_c1` does not stamp egress into the scorecard, so record it
(the 07-27 and 09-14 files carry a backfilled `egress` field).

### Track C2b: paired shadow-corpus abstention

The question set is **frozen** in `munin_bench/abstention/c2_questions.json`
(50 questions, 49 source DOIs, seed 42). Do not re-run `build_shadow`'s
`main()` against a grown corpus: it re-selects the questions. Build the
shadows from the frozen `removed_dois`.

```bash
# 1. Shadow BOTH collections the harness searches. Snapshot-recover clones
#    1.43M chunk points in seconds; a scroll copy takes hours.
#    POST /collections/papers_chunks/snapshots
#    PUT  /collections/papers_chunks_shadow/snapshots/recover {"location":"file:///qdrant/snapshots/papers_chunks/<name>"}
#    (same for papers_bge -> papers_shadow)
#    Resolve removed_dois to papers_bge point ids and paper_ids; delete those ids from
#    papers_shadow; delete-by-filter (paper_id OR doi) from papers_chunks_shadow.
#    Verify: 0 removed DOIs in papers_shadow, 0 chunks of them in papers_chunks_shadow.

# 2. run_arm overwrites c2_runs/{present,absent}.verdicts.json in place: move the previous pair aside.
mv c2_runs/present.verdicts.json c2_runs/present.verdicts.<olddate>.json   # and absent, and both .meta.json

# 3. Shadow instance on :8081. docker-compose.shadow.yml extends the production
#    retrieval service and overrides only PAPERS_COLLECTION and CHUNKS_COLLECTION.
#    Run from the production project name with --no-deps so nothing else is touched;
#    /opt/munin/docker/.env is root-only, so export the production container's env
#    (docker inspect munin-retrieval) and pass an empty --env-file.
docker compose -p munin --project-directory backend/docker --env-file /dev/null --profile rag \
  -f backend/docker/docker-compose.yml -f backend/docker/docker-compose.shadow.yml \
  up -d --no-deps --no-build retrieval-shadow
#    Probe: a removed paper's title on /search/hybrid must be FOUND on :8080 and absent on :8081.

# 4. Both arms at egress=off (the runner defaults to off; say it anyway).
MUNIN_EVAL_EGRESS=off PYTHONPATH=$HOME/.cache/munin_bench_deps:. $PY -m munin_bench.abstention.run_c2 \
  --arm present --base-url http://127.0.0.1:8080 --email <eval-account> --concurrency 1 --date <D>
MUNIN_EVAL_EGRESS=off PYTHONPATH=$HOME/.cache/munin_bench_deps:. $PY -m munin_bench.abstention.run_c2 \
  --arm absent  --base-url http://127.0.0.1:8081 --email <eval-account> --concurrency 1 --date <D> \
  --vs-suffix <olddate>   # writes the paired scorecard with the question-paired delta vs the previous pair

# 5. Teardown: docker stop munin-retrieval-shadow && docker rm munin-retrieval-shadow;
#    snapshot papers_shadow (kept, ~0.5 GB); delete papers_shadow and papers_chunks_shadow.
```

**Both arms must run at the same egress setting.** ~70 s per question at
`egress=off` on the present arm, ~120 s on the absent arm; about 3 h for the
pair on the dense 27B.

**Every retrieval path must be shadowed.** The 07-27 recipe shadowed
`papers_bge` only; the chunk-level evidence layer added on 2026-08-30 reads
`papers_chunks`, and a shadow that missed it would have let the absent arm
read the removed papers' full text. When a new retrieval path is added to
the harness, the shadow recipe is out of date until it covers that path too.

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
# Every arm writes to ablation_runs/<tag>/ and resumes from <arm>.capture.jsonl
# there, so a crash mid-arm costs nothing and a new run can never overwrite a
# committed run's per-query arrays (the 08-26 Qwen3.8 set is ablation_runs/qwen38-27b/).
# Pick a fresh tag per run; compare / faithfulness / risk_coverage / toolreliability
# take the same --tag (or MUNIN_ABLATION_TAG). For a corpus-only agentic arm run the
# agentic arm alone under a second tag with MUNIN_EVAL_EGRESS=off (2026-09-15 recipe).
export VLLM_MODEL_NAME=qwen3.8-27b LLM_REASONING_EFFORT=medium   # arm matching
for arm in bare rag agentic; do
  MUNIN_EVAL_EGRESS=full PYTHONPATH=$HOME/.cache/munin_bench_deps:. \
    $PY -m munin_bench.ablation.run_arm --arm $arm --n 199 --tag <run-tag>
done

$PY -m munin_bench.ablation.compare --date <D> --tag <run-tag>
$PY -m munin_bench.ablation.abstain_arms --arm bare       # and --arm rag: abstention per arm on the fabricated set
MUNIN_BENCH_ENTAILMENT_DEVICE=cuda:0 $PY -m munin_bench.ablation.faithfulness --date <D> --tag <run-tag>   # cpu works too, ~1-2 h
$PY -m munin_bench.toolreliability.score ablation_runs/<run-tag>/agentic.json --tag <D>_toolreliability-<slug>
```

---

## 5a. A second backbone beside production (2026-09-15)

The whole generation suite can be run on another model without swapping
production. The model is one profile file, `backend/config/models/<slug>.env`
(checkpoint, served name, vLLM parsers, quantization, how sub-tasks turn
reasoning off, and the vendor's recommended sampling); three ship:
`qwen3.8-27b`, `gpt-oss-20b`, `qwen3.6-35b-a3b`.

```bash
# one-time: passwordless deploy.sh / vllm-service for the operator (root-equivalent, read the template)
sudo MUNIN_OPERATOR=$USER backend/deploy.sh sudoers
# production must be on the single-GPU profile so GPU 0 is free:
sudo vllm-service stop && sudo vllm-service start single

# everything else, unattended (phases resume; re-run the same command after any failure):
backend/benchmarks/scripts/run_suite.sh gpt-oss-20b --date <D>
```

The driver brings the backbone up as `deploy.sh instance up <slug> --name eval`
(its own SLURM job on GPU 0, vLLM :8001, a retrieval container on :8082 that
`extends` production and differs in exactly the model variables and the
tokenizer mount) plus a shadow-corpus instance on :8083 for the C2b absent
arm, gates both (`scripts/vllm/backbone_gates.py`: KV pool, served id,
tokenizer sha, thinking-off ratio, `reasoning_effort` accepted, one
tool-calling turn, decode/prefill tok/s), smokes 20 questions against the
Qwen3.8 arm, then runs Track D (n=199), C1 (100), the standalone answer
track (199), the C2b pair (2x50), faithfulness per arm (CPU judge), T11,
risk-coverage and the routing anchor tier, all at concurrency 1, and finally
tears the instances down, drops the shadow collections (snapshot kept) and
returns production to TP=2. Outputs: `scorecards/<D>_*-<slug>.json`,
`ablation_runs/<tag>/`, `c1_runs/<tag>/`, `c2_runs/<tag>/`,
`results/litqa2/answer.<tag>.*`, and `runs/<tag>/driver.log` with every gate.

Cost columns from such a run are clean because the instance owns its GPU;
user traffic stays on production. Sampling is the profile's, so gpt-oss runs
at OpenAI's `temperature 1.0, top_p 1.0` while the Qwen numbers were produced
under Qwen's set; the scorecards record both.

## 6. What has not been run

As of 2026-09-16:

- **Faithfulness per arm on gpt-oss-20b** (13 scoreable agentic rows: the
  capture's retrieval-tool list lacked `search` and `source` until
  2026-09-16; the next capture on any backbone will count them).
- **The Qwen3.5-9B hardware-floor run** (16 GB claim), deferred; its profile
  file is not written.

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
| Scorecards (67 JSON, 49 Markdown; the `2026-08-26_*`, `2026-09-14_*` and `2026-09-15_*` files are the current headline) | `backend/benchmarks/scorecards/` |
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
that any paired test needs. **The exception is `ablation_runs/`, `c1_runs/`,
`c2_runs/` and `faithfulness_runs/`**, which hold the per-query verdicts
behind the Track C/D numbers and are not regenerable for a retired backbone.
Since 2026-09-15 the ablation arms write to `ablation_runs/<tag>/` and resume
from a capture, so a re-run no longer overwrites anything; the Qwen3.6 (2026-08-25)
and Qwen3.8 (2026-09-15) sets are archived off-machine with checksums.

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
| Exact wall-clock costs | Hardware-specific (2x RTX 5090 at TP=2, `--max-num-seqs 8`; the Qwen3.6 figures are on one card at `--max-num-seqs 2`) |
| The Qwen3.6 column of any table | That checkpoint is retired. Its numbers are reproducible **from artifacts** (archived per-query verdicts, committed scorecards) but not re-runnable, so no like-for-like Qwen3.6 figure can be produced for a protocol change made after 2026-08-25. |
| Benchmark data files | Never committed, for size and licence reasons; each dataset has a download script with a checksum so `data/` rebuilds deterministically |
