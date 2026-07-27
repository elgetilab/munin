# Munin Evaluation Suite — Master Plan

**Status:** planning document. Extends `RETRIEVAL-EVAL-SPEC.md`, which
remains authoritative for the retrieval layer (Tracks below reference
its phases directly).

**Purpose.** One unified, repo-shipped evaluation suite that:

1. produces the quantitative results for the current MuninAI paper
   (retrieval quality, answer quality, harness value, abstention,
   cost), and
2. is re-runnable as a single command whenever the model, the harness,
   or the knowledge base changes — producing a versioned scorecard
   that seeds the follow-up proposal's certification story (RQ-M2)
   without claiming to *be* the certification suite yet.

**Scope split (per varghele, 2026-06):**

- **In-paper:** benchmarks that strengthen the current tool paper,
  especially around the agentic harness. Runnable with data we can get
  now; no external expert dependency.
- **Follow-up (RQ-M1/M2 proposal):** expert-vetted hyperpolarization
  benchmark, formal certification thresholds + predictive validity,
  full AstaBench positioning. The suite's *architecture* anticipates
  these; the *content* is deferred.

**Relationship to the briefing.** This plan operationalizes
`munin-benchmark-landscape-briefing.md`. Several anchors in the
briefing post-date verified knowledge (AstaBench, AbstentionBench,
MedAbstain, AutoResearchBench, PaperArena, HalluLens, FaithBench);
they are carried as the briefing states them and marked `[VERIFY]`
in the TODO document. Verify before citing in the paper.

---

## 1. Suite structure: six tracks

```
Track A  Retrieval quality          = RETRIEVAL-EVAL-SPEC.md Phases 1-5
                                      (+ §4a RAGAS add-on)   [exists, build as specced]
Track B  Answer quality &           local-first faithfulness scoring of
         faithfulness               end-to-end answers                  [new]
Track C  Abstention & calibration   corpus-grounded "not answerable"
                                      benchmark + calibration metrics   [new]
Track D  Harness value & cost       bare-model vs RAG vs agentic
                                      ablation; tool-use success;
                                      cost-accuracy Pareto              [new]
Track E  Regression & scorecard     single-command re-run, versioned
                                      scorecards, diff reports; absorbs
                                      existing smoke/stress/flakiness   [new + existing]
Track F  Follow-up (proposal)       hyperpolarization benchmark,
                                      certification protocol, AstaBench
                                      positioning                       [spec only]
```

Tracks B–D are where the paper gains the most. Track E is the
"ships with the repo, re-run on upgrade" requirement. Track F is
documented so the architecture doesn't paint us into a corner, but
nothing in F blocks the paper.

---

## 2. Track A — Retrieval quality (existing spec)

No changes. `RETRIEVAL-EVAL-SPEC.md` Phases 1–5 (+ §4a) stand as
written: metrics infra, retrievers, BEIR scientific subsets, local
pooled benchmark, LitQA2 anchor. Build order and gates unchanged.

One addition that belongs to Track A and is cheap:

- **LitSearch** (Ajith et al., arXiv 2407.18940): retrieval benchmark
  built from real literature-search queries about ML/NLP papers.
  Closer to Munin's actual usage pattern (natural-language paper
  finding) than SciFact's claim-verification queries. Add as a sixth
  BEIR-style subset in Phase 3; the corpus and qrels are public.
  Caveat: domain is ML/NLP, not chemistry/biophysics — report it as
  an external-validity point, not a deployment claim.

---

## 3. Track B — Answer quality & faithfulness

**Claim it supports:** "Munin's answers are grounded in the retrieved
corpus" — the anti-hallucination half of RQ-M1, scoped to what's
measurable now.

**Design principle: local-first judging.** RAGAS-style evaluation with
a frontier-API judge contradicts the paper's privacy story (queries
and answers leave the premises). The primary faithfulness metric must
run locally:

- **MiniCheck** (Tang et al. 2024, arXiv 2404.10774): small
  fact-checking models (flan-T5-large up to 7B) that score
  claim-vs-evidence entailment at GPT-4-comparable accuracy on
  grounding benchmarks. Runs on GPU 0. This becomes the primary
  faithfulness judge: answer is split into sentences/claims, each
  scored against the retrieved contexts.
- **RAGAS with external judge** (existing §4a spec) is demoted to a
  *cross-check*: run once on the 50-query subset to show MiniCheck and
  a frontier judge agree (report correlation), then rely on MiniCheck
  for all sweeps and re-runs. This also caps LLM-judge spend.
- **Judge validation:** before trusting MiniCheck on our domain, score
  it against a ~100-example sample of **RAGTruth** (Niu et al. 2024,
  arXiv 2401.00396), the span-annotated RAG-hallucination corpus. If
  MiniCheck's AUROC on the sample is poor, fall back to the larger
  MiniCheck variant or flag the issue before building anything on it.

**What gets scored:** the end-to-end answers from Track D's three arms
(bare / RAG / agentic) over the local-pool QA set, so faithfulness and
harness-delta come from the same runs. No separate generation pass.

**Metrics:** mean faithfulness score, % answers fully supported,
% answers with ≥1 unsupported claim; per-arm, with paired bootstrap
CIs (Track A Phase 1 infra reused).

---

## 4. Track C — Abstention & calibration

**Claim it supports:** "Munin knows when the corpus doesn't contain
the answer" — the briefing's thinnest-area / signature claim, and the
single most differentiating benchmark we can build now. General
abstention benchmarks (AbstentionBench, arXiv 2506.09038) target
intrinsic unanswerability, not the private-corpus regime. NB the
novelty pass (2026-07-27, `CITATIONS-VERIFIED.md`) found the closest
prior art IS public — KnowOrNot (Foo et al., arXiv 2505.13545)
operationalizes out-of-knowledge-base abstention in RAG — so the novel
contribution is the *combination* (scientific-literature + DOI
construct-time ground truth + joint abstention & confabulated-citation
axes vs a live RAG stack), NOT the bare concept. Soften any "nothing
public covers it" wording accordingly.

**The corpus-grounded abstention set (build; no experts needed):**

Construct ~150–200 items in three strata:

1. **Absent-paper questions.** Take LitQA2 questions whose source
   paper is deliberately NOT ingested (we control the corpus, so we
   know ground truth). Correct behaviour: abstain / "not in corpus".
   The same questions with the paper ingested (Phase 5) are the
   positive control — same surface form, different ground truth.
2. **Shadow-corpus questions.** Clone the local-pool queries (Phase 4)
   against a shadow Qdrant collection from which the relevant DOIs
   have been removed. Correct behaviour: abstain or answer with
   explicit "outside corpus" sourcing (web/S2), never fabricate a
   local citation.
3. **Unanswerable-by-construction.** Questions referencing
   nonexistent papers, fabricated DOIs, or plausible-but-fake authors
   from the group's field. Correct behaviour: state the reference
   cannot be found; never confabulate metadata.

**Metrics:**

- Abstention precision / recall (treating "should abstain" as the
  positive class).
- Over-abstention rate on the answerable controls (refusing when the
  answer IS in the corpus) — the failure mode that makes assistants
  useless.
- Risk–coverage curve: answer accuracy vs. fraction of questions
  attempted, sweeping the abstention threshold if the harness exposes
  one.
- Confabulated-citation rate: % of abstention-warranted items where
  the model emitted a local-looking citation anyway (scored by
  checking emitted DOIs against the corpus — fully automatic).

**References:** AbstentionBench method for variant construction
(Kirichenko et al., NeurIPS 2025 `[VERIFY]`); Kalai et al. 2025 "Why
Language Models Hallucinate" for the framing that reward structures
favour guessing; "Know Your Limits" survey (TACL 2025 `[VERIFY]`).

**Why this is the paper's best new section:** it's cheap (built from
assets Phases 4–5 already require), it's novel in combination (KnowOrNot
covers OOKB abstention generally, but not scientific-lit + DOI ground
truth + confabulation together — see `CITATIONS-VERIFIED.md`), it's
automatic (DOI-checking needs no judge), and it
directly previews RQ-M1 for the follow-up proposal.

---

## 5. Track D — Harness value & cost

**Claim it supports:** "the agentic harness adds measurable value over
the bare model" — the empirical core of the paper's contribution
framing, and RQ-M1's harness-delta question scoped to in-paper size.

**The three-arm ablation (the headline experiment):**

| Arm | What it is | Implementation |
|---|---|---|
| 1. Bare model | Qwen3.6-35B, no tools, no RAG | direct vLLM call, persona prompt minus tool instructions |
| 2. Vanilla RAG | retrieve-then-answer, no agent loop | one `/search/hybrid` call, contexts pasted into prompt, single completion |
| 3. Full harness | Munin production: MCP tools, agentic loop, persona | existing `/api/chat/completions` path |

Run all three arms over: (a) the LitQA2 in-corpus subset (Phase 5),
(b) the local-pool QA set (Phase 4 extended to answer-level), (c) the
Track C abstention set. Score: answer accuracy (LitQA2 MCQ is
automatic), faithfulness (Track B), abstention metrics (Track C),
and cost (below). Report per-arm with paired bootstrap CIs.

**Precedent:** PaperQA2's ablations (Skarlinski et al. 2024) showed
scaffolding beats vanilla RAG; briefing finding 2 (Asta Paper Finder
~2x ReAct `[VERIFY]`) reinforces. The briefing also warns the result
is not guaranteed — a stronger base model can hurt a specialized
harness — which is exactly why the experiment is worth running and
reporting honestly either way.

**Tool-use reliability:** from the harness arm's telemetry, report
tool-call error rate, recovery rate (model retries after a failed
call), and mean tool calls per query. The flakiness-suite scenarios
(Track E) already exercise these paths; here we quantify them on
benchmark traffic.

**Cost-accuracy accounting:** for every arm and every benchmark run,
log prompt/completion tokens, wall-clock, and inference-time. Report an
accuracy-vs-cost plot per benchmark — self-hosted cost is compute time,
not API dollars, which is itself a point worth making. Methodology
reference: AstaBench's cost-aware leaderboard `[VERIFY]` — adopt the
Pareto presentation, not the infrastructure.

> **RECONCILED 2026-06-18 (KICKOFF-QUESTIONS Q5):** the original text said
> GPU-seconds "via SLURM job accounting on hugin, for free". That does NOT
> work: `sacct` accounting is disabled (no `slurmdbd`). Tokens come from the
> vLLM `usage` field; inference-time (the GPU-seconds proxy) is **measured**
> by timing the model call at **concurrency=1**, not derived from a blended
> throughput and not from `sacct`. The ablation arms therefore run at
> concurrency=1. See `BENCHMARK-TODO.md` T5 for the full method.

**External agent benchmarks (deliberately deferred):** AstaBench,
PaperArena, AutoResearchBench `[VERIFY all]` are follow-up scope.
Frontier agents reportedly score <10% on AutoResearchBench's hard
splits; running a 35B open model on them now produces noise, not
signal. The follow-up proposal positions against them properly.

---

## 6. Track E — Regression & scorecard (the shipping requirement)

**Claim it supports:** none in the paper directly — this is the
"benchmarks ship with the repo and re-run on upgrade" requirement,
and the seed of RQ-M2's re-certification routine.

**Design:**

- `python -m munin_bench.run_all --tag <label>` runs every track's
  automated benchmarks end-to-end and writes
  `scorecards/<date>_<tag>.json` + a human-readable `.md` twin.
- Scorecard header: model name + revision, harness git SHA, persona
  versions, corpus snapshot stats (paper count, graph edge count,
  knowledge-map build date), package versions, seed. Reuses the
  `make_run_header()` helper from the retrieval spec (§3f).
- `python -m munin_bench.compare A.json B.json` produces a diff report:
  per-metric deltas with the paired-bootstrap significance machinery
  from Phase 1. This is what varghele runs after a model swap — it IS
  the re-certification-lite loop, and the paper can present it as
  such ("the suite re-runs in N hours and flags regressions") without
  overclaiming a validated certification protocol.
- **Scorecards are committed to git** (small JSONs). The history of
  scorecards across model swaps becomes data for the follow-up
  proposal's predictive-validity argument.
- **The behavioral layer is a separate folder with a separate
  contract:** `backend/eval/` (see §8) holds deployment-behavioral
  scenarios — starting with the pong test — and a registry that wraps
  the existing `backend/scripts/` QA tools (flakiness-suite,
  stress-test, smoke tests; left in place per DECISIONS.md). `run_all
  --with-reliability` invokes the registry and folds its
  PASS/FLAKY/FAIL summary into the scorecard under a `reliability`
  key, clearly separated from the benchmark metrics: reliability
  results regression-track the deployment and are never cited in the
  paper. The report-chat → regression-test pipeline
  (FRONTEND-REPORT-CHAT.md) continues to feed scenarios into this
  layer.

**Runtime budget:** target < 8 h for a full `run_all` on hugin
(GPU 0), so a model swap can be evaluated overnight. If a track blows
the budget, it gets a `--quick` subset (e.g. SciFact only for BEIR).

---

## 7. Track F — Follow-up scope (documented, not built)

> STATUS 2026-07-14: two cheap pieces PULLED FORWARD now that A-E are
> done: (1) the light **certification gate** (`certify.py` +
> `certification_thresholds.json`; `run_all --certify`) - provisional
> thresholds = current baselines, PASS/FAIL, NOT the validated/predictive
> version (that stays follow-up); (2) the **solve(prompt)->answer** adapter
> (`ablation/solve.py`) so an InspectAI bridge is a wrapper. The
> **hyperpolarization expert benchmark stays follow-up** (needs expert vetting).

Kept here so the suite's architecture anticipates them; each is a
one-paragraph placeholder that the follow-up proposal expands.

- **Hyperpolarization expert benchmark.** Expert availability is a
  "firm maybe" — specced to the follow-up. Optional pilot if time
  allows: varghele self-authors 20–30 items (metadata extraction +
  domain QA + provenance over in-corpus hyperpolarization papers),
  clearly labelled "pilot, not expert-vetted" — enough to demonstrate
  the item format in the proposal, not enough to claim a benchmark.
- **Certification protocol.** Thresholds (faithfulness ≥ X,
  abstention calibration ≥ Y, tool success ≥ Z, cost ≤ budget) and
  predictive-validity evidence. The scorecard history (Track E) is
  the raw material; defining and validating thresholds is the
  follow-up's research content.
- **AstaBench / InspectAI positioning.** Decision: do NOT adopt
  InspectAI as the runner for Tracks A–E now — the existing
  lightweight harness is built and the rewrite buys nothing for the
  paper. DO structure Track D's arms so each is callable as a plain
  `async def solve(question) -> answer` function; that is the shape
  an InspectAI bridge needs, making the follow-up integration a
  wrapper, not a rebuild. Revisit when targeting the AstaBench
  leaderboard.

---

## 8. Repo layout delta

Extends the `backend/benchmarks/` tree from `RETRIEVAL-EVAL-SPEC.md` §1:

```
backend/benchmarks/
  munin_bench/
    ...                      # everything from the retrieval spec
    answer_eval/             # Track B
      minicheck_judge.py     # local faithfulness scoring
      validate_judge.py      # RAGTruth sample validation
    abstention/              # Track C
      build_set.py           # constructs the three strata
      score.py               # abstention metrics, risk-coverage
      shadow_collection.py   # builds the DOI-removed Qdrant clone
    harness_ablation/        # Track D
      arms.py                # bare / vanilla-rag / full-harness callables
      run_ablation.py
      cost_accounting.py     # token + wallclock + GPU-seconds per run
    run_all.py               # Track E entry point
    compare.py               # scorecard diff
  data/
    ...                      # per retrieval spec
    abstention/              # generated set + ground truth
    litsearch/               # LitSearch corpus + qrels
  scorecards/                # COMMITTED to git (small JSONs)
  results/                   # gitignored, as before
```

Benchmark *data* is never committed (size + licenses); every dataset
gets a `download_<name>.py` script with a checksum, so a fresh clone
can rebuild `data/` deterministically. The abstention set is the
exception — it's generated from the corpus and its ground-truth JSON
is committed (it's small and it IS the benchmark).

**The behavioral layer lives separately, in `backend/eval/`:**

```
backend/eval/
  README.md                  # what belongs here vs. benchmarks/
  registry.py                # scenario registry; wraps (does not move)
                             # the existing backend/scripts/ QA tools
  scenarios/
    pong_test.py             # agentic correctness: code-me-pong
    ...                      # future behavioral scenarios
```

**Boundary contract (the reason for two folders):**

| | `backend/benchmarks/` | `backend/eval/` |
|---|---|---|
| Purpose | paper-grade measurement | deployment-behavioral correctness |
| Reproducible by outsiders | required | not a goal |
| Items change | versioned, comparability-breaking | freely, as features ship |
| Flakiness | a bug | an informative outcome (PASS/FLAKY/FAIL) |
| Cited in paper | yes | never |
| Scorecard role | the numbers | a labelled reliability summary |

The existing `backend/scripts/` QA tools (flakiness-suite,
stress-test, smoke-parser-swap, the compile_latex and
delegate_persona tests) stay where they are, per the DECISIONS.md
entry that deliberately keeps them as developer tooling.
`backend/eval/registry.py` wraps them so `run_all
--with-reliability` has one entry point without relocating files.

**The pong test, formalized** (first scenario in `backend/eval/`):
prompt the chat persona to "code me pong"; assert (a)
`delegate_to_persona` -> Turing fires (agentic handoff), (b) every
`tool_call` name emitted is in the active persona's server-side
allowlist (hallucinated-tool detector), (c) `create_artifact` or
`run_python` lands the pong code as an artifact, (d) no `error` SSE
events, (e) non-empty final assistant content. Run N reps with
flakiness-suite semantics; a variant starting directly in the Turing
persona covers the no-handoff path. This is a flakiness-suite-shaped
scenario and can reuse its rep/variant/assertion machinery directly.

---

## 9. What lands where in the paper

| Track | Paper section | What changes in munin-paper.tex |
|---|---|---|
| A | Evaluation: retrieval (exists) | LitSearch added to the BEIR list |
| B | Evaluation: answer quality (rewrite of the RAGAS subsection) | MiniCheck-primary, RAGAS as cross-check; privacy argument for local judging |
| C | NEW subsection: "Corpus-grounded abstention" | new; also feeds Limitations honestly |
| D | NEW subsection: "Harness ablation and cost" + strengthens Contributions | the harness contribution becomes a measured claim |
| E | Deployment/Reproducibility section | "the suite re-runs in <N h and ships with the repo" |
| F | Future Work | one paragraph pointing at the follow-up RQs |

The .tex update is a separate task once this plan is approved.

---

## 10. Build order across tracks

1. Track A Phases 1–5 as specced (unchanged; everything depends on
   its metrics infra and pooled data).
2. Track D arms 1–2 (bare, vanilla RAG) — small, and needed before
   any answer-level scoring makes sense.
3. Track B MiniCheck judge + RAGTruth validation.
4. Phase 4 answer-level extension + Track D full ablation on
   LitQA2-in-corpus and local pool.
5. Track C abstention set + scoring (depends on Phase 4/5 assets and
   the shadow-collection tooling).
6. Track E `run_all` + scorecard + compare (wraps everything).
7. Track A LitSearch, Track B RAGAS cross-check, Track D telemetry
   polish — fill-in items, any order.
8. Track F pilot items only if schedule allows.

Gates carry over from the retrieval spec (kappa floor, BM25-beating
floor, LitQA2 ≥50 overlap). New gate: the harness arm must beat the
bare-model arm on LitQA2-in-corpus accuracy — if it doesn't, the
paper's core framing has a problem and we need to know before
submission, not after.
