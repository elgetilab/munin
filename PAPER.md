# Munin — publication artifact index

What the paper claims, which measurement backs each claim, and how to
reproduce it. Every number below is copied from
[`backend/benchmarks/RESULTS.md`](backend/benchmarks/RESULTS.md), which is
copied in turn from committed scorecard JSON, not from memory.

**Provenance is SPLIT by model as of 2026-08-26.** Claims 1, 2 and 5 (Track D,
faithfulness, T11) were re-measured on `qwen3.8-27b`
(`cyankiwi/Qwen3.8-27B-AWQ-INT4`, dense 27B, TP=2, 64k,
`reasoning_effort=medium`), which is what production serves. Claim 3
(abstention, C1/C2) is still on the retired `qwen3.6-35b-a3b`
(Qwen3.6-35B-A3B-AWQ-4bit) and says so inline. Claim 4 (retrieval) is
**model-independent by construction** and did not need re-running: no LLM is in
its loop and AgentRetriever scores against a frozen variant set. Retrieval
encoder throughout is BGE-large-en-v1.5
(1024d, collection `papers_bge`) since the 2026-07 cutover, SPECTER-v1 (768d,
`papers`) before it. **Every section of RESULTS.md states its own encoder**,
because the two are not comparable and were never meant to be pooled.
Corpus at time of writing: 68,462 papers.

> **Drafting the paper?** [`docs/paper-kit/`](docs/paper-kit/) is a
> self-contained bundle (system, architecture, corpus, methods, results,
> ablations, findings, limitations, related work, reproduce, plus the 15
> headline scorecards) written to be read without repository access. This file
> stays the short claim-to-scorecard index.

---

## 1. Headline claim: the agentic harness is what produces the accuracy

Track D, three arms over the same 199 LitQA2 questions, paired.
`RESULTS.md` "Model swap ... Track D re-run", git `3e0bcfb`, 2026-08-26.
**Measured on two architecturally different backbones.**

| arm | accuracy | precision of attempted | abstain | cost |
|---|---|---|---|---|
| RAG (naive top-5) | 0.211 | 0.420 | 0.498 | 8.3s, 0 tools |
| bare (parametric) | 0.387 | 0.403 | 0.040 | 8.0s, 0 tools |
| **agentic (harness)** | **0.874** | **0.946** | 0.075 | 157.3s, 6.9 tools |

Paired bootstrap: **agentic − bare = +0.487 [0.407, 0.568], p ≈ 0**;
agentic − RAG = +0.663 [0.598, 0.729]; RAG − bare = −0.176 [−0.251, −0.096].

**The claim replicates across backbones.** On the retired Qwen3.6-35B-A3B MoE
(git `9c476b8`, 2026-07-27) the same 199 questions gave RAG 0.171 / bare 0.302 /
agentic 0.839, with agentic − bare = +0.538 [0.457, 0.618]. A dense 27B and a
35B/3B-active MoE agree on the ordering and on the size of the effect.

**On the delta shrinking from +0.538 to +0.487.** The 07-27 bare arm ran at
`max_tokens=4096` against its agentic arm's 16,384, and 33 of its 199 bare
answers were **unparseable**, i.e. truncated and scored as failures rather than
wrong answers. All arms now run at 16,384 and bare returns 0 unparseable. That
`agentic − RAG` is essentially unchanged (+0.668 → +0.663) shows the shrinkage
comes entirely from measuring bare properly. The old number was inflated by a
defect; this is a correction. A like-for-like old-model figure at 16,384 is not
obtainable, since that checkpoint is retired.

Two secondary findings, both of which survive the re-measurement:

- **Naive RAG is worse than no retrieval at all**, and more strongly than
  before (−0.176, against −0.131 on Qwen3.6). It now holds against a
  correctly-budgeted bare arm.
- **Cost is real**: ~20x bare wall-clock at 6.9 tool calls per query.

A third finding is new. **Qwen3.8 abstains far less outside the harness** (bare
0.201 → 0.040, RAG 0.749 → 0.498) and its precision of attempted falls with it
(bare 0.476 → 0.403, RAG 0.708 → 0.420). Inside the harness abstention is
**identical** at 0.075 and precision *rises* (0.908 → 0.946). The harness, not
the backbone, is what keeps attempted answers trustworthy.

Scorecards: `scorecards/2026-08-26_harness-ablation.json` (current),
`scorecards/2026-07-27_harness-ablation.json` (Qwen3.6).
Do **not** cite the 2026-07-13 pilot (n=100, 0.56/0.32/0.15); it is superseded.

## 2. Grounding does not improve with the harness

Track B faithfulness, per-arm paired. On `qwen3.8-27b` (n=163): RAG 0.282
[0.248, 0.316] vs agentic 0.288 [0.246, 0.333], delta **+0.010 [−0.052, +0.069],
p = 0.776**. The 4.1x accuracy gap in claim 1 does **not** come with a
faithfulness gap.

**The null replicates across backbones.** On the retired Qwen3.6 (n=189) it was
RAG 0.326 vs agentic 0.340, delta +0.023 [−0.043, +0.089], p = 0.496. Two
different models, same conclusion. Absolute grounding is slightly lower on
Qwen3.8 for both arms.

The bare arm is structurally unscoreable (no retrieved context to entail
against). The agentic n is 163 rather than 199 because abstentions and
context-free answers cannot be scored.

Scorecards: `scorecards/2026-08-26_harness-ablation-faithfulness.json` (current),
`scorecards/2026-07-27_harness-ablation-faithfulness.json` (Qwen3.6).

## 3. Abstention behaviour (the novel benchmark)

Track C. Prior art exists (KnowOrNot, arXiv 2505.13545), so the claim is
narrowed to corpus-grounded abstention with a paired shadow corpus.

> **These numbers are on the RETIRED `qwen3.6-35b-a3b`**, not on what production
> serves. C1 and C2b were not re-run in the 2026-08-26 model swap. C2b in
> particular needs the `papers_shadow` Qdrant collection rebuilt from the frozen
> 50 questions and a second retrieval instance on :8081. Note also that claim 1
> found Qwen3.8 abstains far less than Qwen3.6 outside the harness, so these
> figures should **not** be assumed to carry over.

- **C1, fabricated papers** (n=100): abstain 0.970 [0.930, 1.000], **0
  confabulated local citations**.
- **C2b, paired shadow corpus** at matched `egress=off` (n=50): accuracy drops
  0.540 → 0.080 when the source paper is removed, abstain 0.400 → 0.740. On the
  answerable subset, correct abstention is **0.667 [0.481, 0.852]** (n=27), up
  from 0.200 [0.050, 0.350] on the 2026-07-10 harness: question-paired delta
  **+0.467 [0.232, 0.697], p < 0.001**. The 2026-07-10 reading of this pair was
  confounded by egress; the 2026-07-27 re-run at matched egress reverses it.
- **Risk-coverage**, litqa2-answerable, n=199: agentic reaches coverage
  **0.925 [0.88, 0.96]** at selective risk **0.092 [0.05, 0.14]**; bare answers
  nearly as often (0.633) at ~5.7x the risk (0.524); RAG buys low risk only by
  refusing most questions (coverage 0.241).

Scorecards: `2026-07-27_abstention-c1-fabricated.json`,
`2026-07-27_abstention-c2-shadow.json`, `2026-07-27_risk-coverage.json`.

## 4. Retrieval

- **Encoder migration** (SPECTER-v1 → BGE-large): Recall@10 0.44 → 0.73 on the
  LitQA2 pool; end-to-end answer accuracy +0.075, p = 0.028. Live in
  production since 2026-07-06.
- **T8 LitSearch**: BGE-dense 0.485 nDCG@10 beats BM25 0.378 (p ≈ 0). The same
  benchmark surfaced a **production bug**: citation re-rank scored −0.368
  nDCG@10 because the citation signal spans [0,1] while BGE cosine spans 0.087,
  giving citations ~5x the intended influence. Fixed 2026-07-28 by min-max
  normalising within the pool: 0.117 → 0.469, parity with dense.
- **BEIR / SciFact** anchors external validity; see RESULTS.md Phase 3.

Scorecards: `2026-07-28_litsearch.json`, `2026-07-03_bge-large.json`,
`2026-07-06_answer-bge-large-v2.json`.

## 5. Tool-use reliability

T11, telemetry over the same 199-question run. On `qwen3.8-27b`: 1,380 tool
calls, mean **6.93 calls/query**, error rate 0.139, degraded rate 0.379, 102
queries hit at least one failure, and **recovery rate 1.000**, i.e. every
failure was recovered from within the turn.

**Recovery 1.000 replicates** (Qwen3.6: 1,714 calls, 8.61/query, recovery
1.000). But the error and degraded rates roughly doubled (0.061 → 0.139 and
0.240 → 0.379). The driver is a **tool-layer regression, not a model one**:
`web_fetch` errors on 0.678 of its 261 calls (was 0.453), `search` newly fails
at 0.122 (was 0.000), and `update_plan_item` failed both of its 2 calls. Worth
fixing before this is cited as a reliability result. Reported per tool in
RESULTS.md.

Scorecards: `2026-08-26_toolreliability-qwen38_toolreliability.json` (current),
`2026-07-27_toolreliability-clean.json` (Qwen3.6).

---

## What is NOT claimed

Stating these plainly is cheaper than being asked.

| Item | Status | Why |
|---|---|---|
| Phase 4 local query pool | **deferred** | blocked on human query curation and two-annotator qrels, not compute |
| T3 stratum 2, T7 answer-level local pool | deferred | both depend on Phase 4 |
| MiniCheck as a headline judge (T4) | dropped | validated but not used for the headline; faithfulness is reported from the local judge |
| T9 RAGAS external cross-check | dropped | documented in the benchmark TODO |
| Track F | not run | follow-up proposal scope |
| BEIR nfcorpus / scidocs / trec-covid | not run | SciFact only |
| Human-expert comparison | context, not a claim | PaperQA2 0.660 and expert mean 0.677 are quoted from their sources, not re-measured here |

## Reproducing

Full command set, per track, lives in
[`RESULTS.md` → "Reproduce"](backend/benchmarks/RESULTS.md#reproduce). The
short version:

```bash
cd backend/benchmarks
export PYTHONPATH=$HOME/.cache/munin_bench_deps:.
export NEO4J_PASSWORD=...                      # from /opt/hugin/config/cluster.env
PY=/opt/munin/services/pipeline/venv/bin/python

$PY -m pytest tests/                           # unit gates
$PY -m munin_bench.pipelines.run_all \
   --tag <label> \
   --tracks beir-scifact,litqa2-retrieval,litqa2-answer,faithfulness,abstention,ablation \
   --with-reliability --certify --date <YYYY-MM-DD>
```

`--certify` checks the run against `certification_thresholds.json` and emits
PASS/FAIL; `munin_bench.pipelines.compare <old>.json <new>.json` gives a
paired-bootstrap regression diff between two runs.

Two operational notes that will otherwise cost you a day:

- **Concurrency must not exceed vLLM `max-num-seqs`**, or the run silently
  degrades (this is what produced the misleading 0.688 agentic figure).
- **Evals default to `egress=off`** (`X-Munin-Egress`), so a benchmark cannot
  spend the Brave or Semantic Scholar quota. Set `MUNIN_EVAL_EGRESS=full`
  deliberately for a live-web run, and record which you used: the C2 result
  above changed sign on exactly this.

## Where everything lives

| Artifact | Path |
|---|---|
| **Paper kit** (self-contained drafting bundle) | `docs/paper-kit/` |
| Results log (canonical numbers) | `backend/benchmarks/RESULTS.md` |
| Scorecards (58 JSON, 47 Markdown) | `backend/benchmarks/scorecards/` |
| Benchmark harness | `backend/benchmarks/munin_bench/` |
| Certification thresholds | `backend/benchmarks/certification_thresholds.json` |
| Paper track: plans, specs, open items | `docs/paper-track/` |
| Agent track: architecture, Deep Research | `docs/agent-track/` |
| Design decisions (the *why*) | `shared/docs/DECISIONS.md` |
| Harness audit vs Claude Code | `docs/architecture/HARNESS-AUDIT-2026-05.md` |
| Corpus quality evidence | `backend/docs/corpus-quality/` |
