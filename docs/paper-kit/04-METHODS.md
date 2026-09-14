# 04 Methods

The evaluation harness: what it measures, how, and which methodological choices
were forced by the deployment rather than chosen freely.

---

## 1. Suite structure

Six tracks. Tracks A through E are built; F is specified only.

| Track | Question it answers | Status |
|---|---|---|
| **A** Retrieval quality | Does the retriever surface the right paper? | Built. Phases 1-3 and 5 done; Phase 4 (local query pool) deferred. |
| **B** Answer faithfulness | Are answer claims entailed by the retrieved evidence? | Built. Judge validated; per-arm paired comparison run on both backbones (2026-07-27 Qwen3.6, 2026-08-26 Qwen3.8). |
| **C** Abstention and calibration | Does the system know when the corpus lacks the answer? | Built. C1 and C2b run, re-run 2026-07-27 on Qwen3.6; risk-coverage derived. **C1 re-run on Qwen3.8 2026-09-14 (100/100); C2b not.** |
| **D** Harness value and cost | Does the agentic harness beat the bare model and vanilla RAG? | Built. Clean run 2026-07-27 (Qwen3.6); headline re-measurement 2026-08-26 on the production backbone Qwen3.8. |
| **E** Regression and scorecard | Can the whole suite re-run as one command and flag regressions? | Built. `run_all`, `compare`, `certify`. |
| **F** Follow-up | Expert benchmark, validated certification thresholds, AstaBench positioning | Specified, not built. Two cheap pieces pulled forward (see §8). |

**A deliberate sequencing decision worth reporting:** Tracks C and D
*characterise* the harness, so they were run **last, against a frozen harness**,
not while it was still being iterated on. The roughly 40% abstention rate seen
during the encoder migration was treated as a harness-behaviour signal to fix,
not a number to publish. This is why the Track D pilot and the Track D headline
run differ so much (see `06-ABLATIONS.md` §1).

**Which backbone each number is on.** The generation model was swapped on
2026-08-25 (Qwen3.6-35B-A3B, MoE, to Qwen3.8-27B, dense). Tracks D, B-per-arm
and T11 were re-run on the same 199 questions and the Qwen3.8 figures are the
headline; Track A is backbone-independent at scoring time, scored against
frozen Qwen3.6-era query variants generated once by the production expander,
and was not re-run for the model swap because regenerating the variants would
change the benchmark, not the system; C1 was re-run on Qwen3.8 on
2026-09-14 and holds; C2b was not re-run and stays on Qwen3.6.
Within a run every comparison is paired; across the two backbones it is
suggestive only, because the harness code also moved between the runs.

**Arm matching in Track D.** The bare and RAG arms call vLLM directly, so
model name, `reasoning_effort` (medium) and `max_tokens` (16,384) are set
explicitly to match what the backend applies to the agentic arm. The 07-27
bare arm ran at 4,096 tokens and lost 33 answers to truncation; that is fixed,
and it is the reason the harness delta moved between the runs. Sampling is
not matched (bare/RAG 0.7 vs the persona's 1.0 / 0.95 / 20 / 1.5) and is
reported as a threat rather than corrected. **Run-to-run variance** on a
199-question arm at temperature 0.7 is ~0.035 (two identical Qwen3.8 bare
arms a day apart: 0.422 vs 0.387); no single-run difference of that size is
signal.

---

## 2. Metric implementations

All metrics are implemented in `munin_bench.metrics` and **verified
bit-identical to `pytrec_eval`**. Reimplementation was chosen so the harness
has no heavy IR dependency and every convention is explicit and testable.

Conventions, stated because they are the kind of thing that silently differs
between papers:

- `ranked` is document ids in rank order, best first. Ids may repeat in
  pathological inputs; only the first occurrence counts toward rank-sensitive
  metrics (MRR), matching TREC behaviour.
- Graded relevance from `qrels` as `{doc_id: grade}` with integer grades. A
  document absent from qrels is grade 0.
- **nDCG uses linear gain** (the raw grade, not `2**grade - 1`) with a
  `log2(rank + 1)` discount at 1-indexed ranks, so the top position has
  discount 1. The ideal DCG is computed from the qrels grades themselves,
  never from the ranked list.

Reported metrics: nDCG@k, Recall@k, MRR, Hits@k for retrieval; accuracy,
precision-of-attempted, abstention rate, unparseable count for answering;
coverage and selective risk for calibration.

---

## 3. Statistics

**Confidence intervals**: 95% percentile bootstrap, 1000 resamples, seed 42,
resampling **over query indices** (the unit of variance in IR). Seeded so
committed scorecards are bit-stable on a fixed numpy.

**Significance**: paired bootstrap over the per-query difference `a - b`,
reporting the observed mean difference, a percentile CI on the resampled mean
difference, and a two-sided p-value `2 * min(P(diff <= 0), P(diff >= 0))`,
clamped to 1.0.

**Companion test**: paired Wilcoxon signed-rank, reported alongside the
bootstrap so a claimed improvement carries both an effect size with a CI and a
distribution-free p-value. When every pair is tied the test is undefined; the
harness reports `p = 1.0` with `n_nonzero = 0` rather than raising, so a
no-change comparison degrades gracefully inside a scorecard.

**Paired everywhere it is possible.** Every arm-versus-arm comparison in
Tracks A, C, and D is over the same question set in the same order, and the
scorecard stores per-query arrays specifically so a paired test can be run
after the fact without re-scoring.

---

## 4. Track A: retrieval

### Datasets

| Dataset | Size | What it tests | Domain |
|---|---|---|---|
| **BEIR / SciFact** | 300 queries, 5,183 docs | External validity anchor; claim-verification framing | Biomedical |
| **LitSearch** (Ajith et al., arXiv 2407.18940) | 597 real natural-language literature-search queries, 64,183-paper S2ORC corpus, mean 1.07 relevant papers/query, binary relevance | The closest public benchmark to Munin's actual usage: finding papers from a question | ML/NLP |
| **LitQA2 retrieval** | 199 in-corpus questions, retrieve depth 20 | Does the production retriever surface the source paper? | Biology, in Munin's own corpus |
| **Encoder bake-off pool** | 190 real source papers + 5,000 random corpus papers, shared pool | Apples-to-apples encoder comparison on Munin's own data without a production re-embed | Munin's domain |

Benchmark corpora get their **own isolated `eval_*` Qdrant collections**. The
harness only ever reads production Qdrant and Neo4j; it is never part of a
deploy.

### The Phase 3 gate, and how it was met

The specified gate was "SPECTER > 0.5 nDCG@10 on SciFact". It was **not met and
should not have been**: even canonical `[SEP]`-formatted SPECTER reaches only
0.494. The gate was instead satisfied by the harness reproducing published BEIR
BM25 (0.652 measured against ~0.665 published), which validates the measurement
apparatus rather than the retriever. This is the honest form of a gate and is
worth reporting as such.

### Retrievers compared

`BM25`, `SPECTER-dense`, `BGE-dense`, `AgentRetriever` (the production path
used by the chat agent's `paper_search`), `CitationRerankRetriever` (the search
page's `/search/hybrid` configuration), `RRF[BM25, dense]`, and a two-hop
citation check.

---

## 5. Track B: faithfulness

**Design principle: local-first judging.** RAGAS-style evaluation with a
frontier-API judge contradicts the system's privacy story, because queries and
answers would leave the premises. The primary faithfulness metric must run
locally, so it does.

**Judge**: MiniCheck-Flan-T5-Large (Tang et al., arXiv 2404.10774), under 1B
parameters, replicating the authors' exact inference. Each answer claim is
scored for entailment against the retrieved contexts.

**Judge validation (a required step, not a formality)**: scored against
RAGTruth (Niu et al., arXiv 2401.00396), 120 responses, ungated from GitHub.

| Task | AUROC | Balanced accuracy @0.5 |
|---|---|---|
| **QA** (Munin's task) | **0.950** | 0.725 |
| Summary | 0.708 | 0.650 |
| Data-to-text | 0.723 | 0.525 |

Gate was QA-AUROC >= 0.70. Passed, so the Flan-T5-Large variant was kept and no
7B escalation was needed. **The judge is validated on the task it is used
for and is visibly weaker on tasks it is not used for**, which is the right
shape for this evidence.

**Claim extraction was refined and the result held.** The original scorer split
answers into raw sentences; the refined version drops process narration,
questions, and headers. The metric moved from 0.378 to 0.356 with heavily
overlapping CIs, so the grounding gap is **robust to claim extraction** and is
not a narration artifact. Reporting both numbers is part of the finding.

**The primary metric is macro `% claims supported`**, which is length-robust.
`% answers fully supported` is reported but is close to zero for arithmetic
reasons on long answers, not as a finding, and should not be quoted alone.

**The context union is deliberately generous** (all retrieval results), which
if anything *inflates* support. Stating the direction of the bias matters more
than eliminating it.

**RAGAS with an external judge was specified as a cross-check and then
dropped** (see `08-LIMITATIONS.md`): no frontier key, no spending cap, and it
would egress evaluation data. The local judge is human-validated at AUROC 0.95,
which was judged sufficient.

---

## 6. Track C: abstention

Three strata were specified; two are built and one is blocked.

| Stratum | Construction | Correct behaviour | Status |
|---|---|---|---|
| **C1 fabricated** | 100 frozen items: 80 Crossref-verified-nonexistent DOIs + 20 nonexistent-paper-by-description, in the group's fields. Zero collide with the 67,675-DOI corpus. | Refuse; never cite a local paper | **Built and run twice** |
| **C2b paired shadow corpus** | 50 single-source-DOI LitQA2 questions asked twice: against the live corpus, and against an isolated second retrieval instance on `papers_shadow` (= `papers_bge` minus the 49 source papers). | Answer when present; abstain when absent | **Built and run twice** |
| Stratum 2 (local query pool) | Clone of the Phase 4 local queries against a DOI-removed collection | Abstain or answer with explicit outside-corpus sourcing | **Blocked on Phase 4** |

**Why C1 is the cleanest signal**: because the papers do not exist, *any*
in-corpus DOI cited for them is a confabulation by construction. The
confabulated-local-citation rate is therefore **fully automatic and
judge-free**, needing only a regex for DOIs and a corpus membership test. There
is no judge to validate, no threshold to tune, and no annotator to agree with.

**Shadow-corpus verification is explicit**: the removed papers were confirmed
present in the live top-20 (8 of 12 sampled) and **0 of 12 in the shadow**; on
the re-run, 49 of 49 confirmed present in `papers_bge` and 0 of 49 in
`papers_shadow`.

**Abstention detection is marker-based and deterministic**, over a curated list
of refusal phrasings. Three properties keep this defensible:

- The **raw answer text is kept in the scorecard**, so the ambiguous middle can
  be judged later if markers prove noisy.
- Refusal-by-asking-for-a-corrected-identifier phrasings were added only after
  manual review confirmed they are refusals on the fabricated set: the model
  never asserts the fake paper's findings alongside them.
- The residual ambiguous cases were manually reviewed and reported (2 residuals
  on the first C1 run, both also refusals).

### Risk-coverage

Definitions, uniform across configurations:

```
answered = correct + incorrect        (committed to a specific option)
coverage = answered / N
risk     = incorrect / answered       ( = 1 - precision_of_attempted )
```

`abstain` and `unparseable` are both treated as **not answered**, and the
per-configuration `unparseable` count is reported so a high-unparseable arm is
not silently flattered by an attempted-only denominator.

**Proportions on small subsets carry two intervals.** The C2 correct-abstention
rate sits on an answerable subset of 20-27 items, where the percentile
bootstrap can only land on multiples of 1/n and degenerates entirely at p near
0 or 1. So the scorer reports the bootstrap (for consistency with every other
CI in the suite) **and** the closed-form Wilson score interval alongside it.
Where they disagree, prefer Wilson and say so; where they agree, quote the
bootstrap.

A third, **unconditional** interval is also stored, which resamples the full
paired question set and re-derives the answerable subset inside each resample,
so it carries the uncertainty in *which* questions are answerable. It comes out
marginally narrower than the conditional interval rather than wider, because
the rate is a ratio estimator whose numerator and denominator co-vary. It is a
robustness check on conditioning, not a more conservative bound, and the
docstring and unit tests say so explicitly because the opposite is the natural
assumption.

**Two-run comparisons on these subsets are paired at the question level**, not
compared as two marginal intervals. The C2 runs share the same 50 frozen
questions, so one resample of question ids drives both arms and each derives
its own answerable subset within it. Disjoint marginal CIs are weaker evidence
than this test.

**A stated limitation rather than a hidden one:** these are operating points,
not a within-run swept curve. The chat emits a hard abstain decision per item
(it selects the "Insufficient information" option) with no per-item confidence,
so one run yields one point. A true swept curve needs the answer-letter logprob
captured per item, which is a one-line addition to the next capture rather than
a re-run.

---

## 7. Egress as a first-class experimental variable

This is a methodological contribution that generalises beyond Munin.

`X-Munin-Egress` controls outbound access per request: `off` (local corpus
only), `oa_only` (+ scholarly APIs), `full` (+ web). **Benchmarks default to
`off`**, so an evaluation run cannot silently spend the Brave or Semantic
Scholar quota, and `MUNIN_EVAL_EGRESS=full` must be set deliberately.

**Why it must be recorded per run, with a concrete case:** the Track C2 result
*changed sign* on exactly this variable. At `egress=full`, removing a paper
from the corpus costs almost nothing, because the model re-fetches the removed
paper from the open web: measured, **17 of 49 removed sources were pulled back
in** through Semantic Scholar and Unpaywall. That pair is a legitimate
"with web access" robustness result and is **not** a corpus-grounded abstention
measurement. Reading across the two conditions produces a confident, wrong
conclusion.

**The harness enforces this rather than trusting discipline.** The
risk-coverage scorer records capture date and egress per operating point and
emits `mixed_generations` / `mixed_egress` markers into the scorecard's
provenance block when points do not share a condition, so a figure that pools
them is flagged at generation time. `bare` and `rag` are recorded as `n/a`
rather than a setting, because they make zero tool calls and egress provably
cannot reach them.

`corpus_scope` is a **separate control** from egress: egress-off is necessary
but not sufficient for a private-corpus claim, because a cached open-access
paper sits on local disk and answers the question without touching the network.
A certification run forces both `egress: off` and `corpus_scope: curated_only`.

---

## 8. Track E: scorecards, comparison, certification

**Scorecard schema**: one committed JSON per run.

```
{
  "meta":  {model, model_max_len, encoder, git_sha, corpus snapshot,
            package versions, seed, tag, generated_at},
  "tasks": {"<task>": {"<system>": {
              "metrics":   {"<metric>": {mean, ci_low, ci_high}},
              "per_query": {"<qid>": {"<metric>": value}}}}}
}
```

The per-query arrays are the price of paired significance and are the reason a
comparison between two runs months apart is a paired bootstrap rather than
eyeballing overlapping CIs. Provenance is best-effort: missing pieces degrade
to `n/a` rather than failing a run. The served model id and max length are read
live from vLLM's `/v1/models` at run time rather than being asserted.

**Scorecards are committed to git**: 58 JSON plus 47 Markdown twins. The
history across model, encoder, and harness swaps is itself data.

**`compare <old>.json <new>.json`** produces a paired-bootstrap regression diff
between two runs.

**`certify`** checks a run against `certification_thresholds.json` and emits
PASS/FAIL. The thresholds are explicitly **provisional**: current committed
baselines with a roughly 15% regression margin, and **not validated** (no
predictive-validity evidence, which is Track F follow-up work). The file says
so in its own `note` field. Current gates:

| Task | System | Metric | Op | Threshold | Baseline |
|---|---|---|---|---|---|
| litqa2_retrieval | agent | recall@10 | >= | 0.62 | 0.729 |
| faithfulness | agentic-live | frac_claims_supported | >= | 0.28 | 0.33 |
| abstention_c1 | agentic | abstain_rate | >= | 0.90 | 0.98 |
| abstention_c1 | agentic | confabulation_rate | <= | 0.05 | 0.01 |
| ablation | agentic | accuracy | >= | 0.48 | 0.56 |
| ablation | agentic | precision | >= | 0.72 | 0.86 |

Plus one relational gate: **the agentic arm must beat the bare arm on
accuracy**. If it does not, the paper's core framing has a problem, and the
point of encoding it as a gate is to find out before submission rather than
after.

**Runtime budget**: the target for a full `run_all` is under 8 hours on the
cluster, so a model swap can be evaluated overnight. In practice the answer
track at the 900s deadline runs 4-5 hours per arm, so a full three-arm ablation
is an overnight-plus job.

### The benchmark / behavioural boundary

Two folders with two different contracts, deliberately not merged:

| | `backend/benchmarks/` | behavioural layer |
|---|---|---|
| Purpose | paper-grade measurement | deployment correctness |
| Reproducible by outsiders | required | not a goal |
| Items change | versioned, comparability-breaking | freely, as features ship |
| Flakiness | a bug | an informative outcome (PASS / FLAKY / FAIL) |
| Cited in the paper | yes | **never** |
| Scorecard role | the numbers | a labelled `reliability` key, clearly separated |

`run_all --with-reliability` folds the behavioural summary into the scorecard
under a separate key. This separation exists so that a flaky deployment test
can never leak into a published metric.

### Two pieces pulled forward from Track F

1. The light certification gate above.
2. A `solve(prompt) -> answer` adapter for each ablation arm, so that a future
   InspectAI / AstaBench bridge is a wrapper rather than a rebuild. The
   decision **not** to adopt InspectAI as the runner now was explicit: the
   existing harness is built and the rewrite buys nothing for this paper.

---

## 9. Constraints imposed by the deployment

These are not choices and should be presented as constraints:

| Constraint | Consequence for the method |
|---|---|
| `slurmdbd` not deployed, so no `sacct` | GPU-seconds cannot be read from job accounting. Cost = vLLM `usage` tokens + wall-clock timed at concurrency 1. |
| vLLM `--max-num-seqs` (8 on the TP=2 production profile, 2 single-GPU) | Every cost-bearing arm runs at concurrency 1. Exceeding the running profile's value silently degrades quality (see `07-FINDINGS.md` §6). |
| One vLLM instance, one model | No model-per-mode. Cost levers are caching, batching, and context length only. The TP=2 profile takes both GPUs, so a benchmark and a batch job cannot share the node. |
| Nightly vLLM stop at 02:00 | A 199-question agentic arm at ~157 s/query is ~9 h; a run that crosses the stop loses its tail (8 questions of the 08-26 run had to be re-run next morning). Start early. |
| Sub-1B local judge | Faithfulness is scored by a small model, validated on RAGTruth, rather than by a frontier judge. |
| Single scientific group as the user base | Phase 4's local query pool has only 42 real candidate queries so far, which is why it is deferred rather than merely unfinished. |
