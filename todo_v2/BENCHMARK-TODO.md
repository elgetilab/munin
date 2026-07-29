# Munin Benchmark TODO — what to implement, why, and what data it needs

Companion to `EVAL-SUITE-MASTER-PLAN.md`. One entry per benchmark or
evaluation. varghele writes per-item implementation plans later; each
entry here gives the what / why / reference / data / build-vs-adopt
call needed to write that plan.

**Citation status: ALL ANCHORS VERIFIED — no `[VERIFY]` markers remain in this
document.** The arXiv IDs and venues inline below are the confirmed ones; cite
them directly. The former `[VERIFY]` convention (anchor taken from
`munin-benchmark-landscape-briefing.md`, not yet re-confirmed) is retired.
**VERIFICATION PASS DONE 2026-07-26 — see `CITATIONS-VERIFIED.md`.** All
anchors confirmed real + bib-ready (IDs/venues/authors); one correction landed
(PaperArena is ~18% hard / ~39% overall, NOT <10% — that was AutoResearchBench).
All anchors incl. MedAbstain now bib-ready (MedAbstain = "Knowing When to
Abstain", Machcha et al., EACL 2026, arXiv 2601.12471 - one paper); T3 novelty
pass done (KnowOrNot 2505.13545 is prior art, narrow the claim). See
`CITATIONS-VERIFIED.md`.

Priorities: **P0** = required for the paper push. **P1** = in-paper if
time allows. **F** = follow-up proposal scope.

---

## Summary table

| ID | Item | Track | Prio | Adopt/Build | Hard dependency |
|----|------|-------|------|-------------|-----------------|
| T1 | Retrieval suite (Phases 1–5) | A | P0 | per existing spec | — |
| T2 | Harness three-arm ablation | D | P0 | build | T1 Phase 4–5 data |
| T3 | Corpus-grounded abstention set | C | P0 | build (novel) | T1 Phase 4–5, LitQA2 PDFs |
| T4 | MiniCheck local faithfulness judge | B | P0 | adopt + validate | MiniCheck weights, RAGTruth sample |
| T5 | Cost-accuracy accounting | D | P0 | build (thin) | T2 |
| T6 | Scorecard runner + diff (`run_all`) | E | P0 | build | T1–T5 |
| T7 | Local-pool answer-level extension | A/B | P0 | build | T1 Phase 4 qrels |
| T8 | LitSearch retrieval benchmark | A | P1 | adopt | LitSearch data |
| T9 | RAGAS external-judge cross-check | B | P1 | per existing §4a | judge budget |
| T10 | QASPER full-text QA anchor | B/D | P1 | adopt | QASPER data |
| T11 | Tool-use reliability metrics | D | P1 | build (thin) | T2 telemetry |
| T12 | AstaBench via InspectAI | D/F | F | adopt (arXiv 2510.21652) | InspectAI bridge |
| T13 | AbstentionBench public variants | C/F | F | adopt (arXiv 2506.09038) | dataset access |
| T14 | FACTS-style long-form grounding | B/F | F | adapt method | judge budget |
| T15 | Hyperpolarization expert benchmark | F | F | build | experts ("firm maybe") |
| T16 | Certification thresholds + validity | F | F | build (research) | scorecard history |
| T17 | PaperArena / AutoResearchBench | F | F | adopt (2510.10909 / 2604.25256) | — |

---

## P0 — required for the paper

**P0 at a glance (2026-07-26):** T2/T4/T5/T6 DONE; T1 mostly done (Phase 4 local
pool deferred); T3 2-of-3 strata done (stratum 2 needs Phase 4); T7 deferred.
The only P0 gap is the **Phase-4 local pool** (T1 Phase 4 + T3 stratum 2 + T7),
all blocked on human query curation + qrels, not compute. Per-item detail below.

### T1. Retrieval suite (existing spec, unchanged)

- **What:** Phases 1–5 of `RETRIEVAL-EVAL-SPEC.md` — metrics infra,
  four retrievers, BEIR scientific subsets, local pooled benchmark,
  LitQA2 anchor.
- **Why:** external + internal validity for the retrieval claims;
  everything else builds on its data and metrics code.
- **References:** BEIR (Thakur et al., NeurIPS D&B 2021); SciFact
  (Wadden et al., EMNLP 2020); LitQA2 (Skarlinski et al. 2024,
  arXiv 2409.13740; Narayanan et al. 2024, arXiv 2412.21154); RRF
  (Cormack et al., SIGIR 2009).
- **Data needed:** BEIR subsets via `ir_datasets` (automatic); LitQA2
  eval split from `aviary-paper-data`; **LitQA2 source-paper PDFs**
  (varghele confirmed he can obtain — this is the long-lead item, start
  the collection now); 100–200 curated log queries + two-annotator
  qrels (varghele + one group member).
- **Status (2026-07-26): MOSTLY BUILT.** Phases 1-3 (metrics infra + BEIR
  SciFact etc.) measured (`scorecards/2026-07-03_{baseline-specter-v1,bge-large}`)
  and Phase 5 (LitQA2 anchor, retrieval + answer) measured
  (`2026-07-04/05/06_answer-*`, `2026-07-24_answer-full-900s`). Encoder migration
  SPECTER-v1 -> BGE-large landed (Recall@10 0.44->0.73). **Phase 4 (local pooled
  benchmark) DEFERRED** - needs 100-200 curated log queries + two-annotator
  qrels (human, not compute). LitQA2 source PDFs now fully local (186/186, see
  T2 plan).

### T2. Harness three-arm ablation — the headline experiment

- **What:** bare model vs. vanilla RAG vs. full Munin harness, run
  over (a) LitQA2-in-corpus, (b) local-pool QA, (c) the abstention
  set (T3). Same questions, three pipelines, paired comparison.
- **Why:** converts the paper's central claim (the agentic harness is
  worth shipping) from architecture description into a measured
  result. Also the in-paper preview of RQ-M1's harness-delta question.
  Briefing finding 2 warns the result can go either way — that risk
  is the reason to run it before review, not after.
- **References:** PaperQA2 ablations (Skarlinski et al. 2024) as
  precedent for scaffolding-beats-RAG; Asta Paper Finder scores over
  double ReAct on PaperFindingBench (AstaBench, arXiv 2510.21652 —
  VERIFIED) as secondary motivation.
- **Data needed:** nothing new — consumes T1's question sets. Needs a
  "bare model" code path (direct vLLM call with tool instructions
  stripped from the persona prompt) and a "vanilla RAG" path (single
  `/search/hybrid` call + stuffed prompt); both are thin wrappers.
- **Gate:** harness must beat bare model on LitQA2-in-corpus accuracy,
  else stop and investigate before any submission.
- **Status (2026-07-27): DONE — definitive clean run in hand.** Three arms on
  199 LitQA2 (paired): bare 0.302, RAG 0.171, agentic **0.839**. This is the
  single clean concurrency-1 pass with live search (`egress=full`), TP=2, 4.4h,
  Brave 200-OK throughout (verified no 402/429; ~1,093 Brave calls ≈ $5-6). It
  SUPERSEDES the 07-24 (0.864, partial) and 07-26 (0.688, search-degraded)
  numbers — report **0.839**. **Gate PASSED.** Paired harness-value:
  agentic-bare = **+0.538** [0.457, 0.618] p<0.001; agentic-rag = +0.668;
  rag-bare = -0.131 (value is the agentic loop, not retrieval). Verdicts:
  167 correct / 15 abstain / 17 incorrect / 0 unparseable (the 900s deadline
  fully eliminated truncation). Scorecard `2026-07-27_harness-ablation`;
  how-to-report note in `T2-ABLATION-REFRESH-PLAN.md`. NB agentic token cost is
  None in this SSE-capture run; valid concurrency-1 wall-clock = 79s/q, 8.6
  tool calls/q (T5). Sets (b) local-pool + (c) abstention beyond C1 remain the
  deferred Phase-4 / T3 pieces.

### T3. Corpus-grounded abstention set — the novel benchmark

- **What:** ~150–200 items in three strata: (1) LitQA2 questions whose
  source paper is deliberately absent from the corpus (with the
  ingested versions as positive controls); (2) local-pool queries run
  against a shadow Qdrant collection with the relevant DOIs removed;
  (3) questions about nonexistent papers / fabricated DOIs / fake
  authors. Ground truth is known by construction. Scoring is
  automatic (emitted DOIs checked against the corpus; abstention
  detected from the answer envelope).
- **Why:** the briefing identifies corpus-grounded abstention as a
  gap nothing public covers, and abstention generally as the thinnest,
  fastest-moving area. This is the cheapest genuinely novel benchmark
  Munin can ship, it makes the paper's evaluation section distinctive
  rather than dutiful, and it is the direct bridge to the follow-up
  proposal. Metrics: abstention precision/recall, over-abstention
  rate on answerable controls, risk-coverage curve, confabulated-
  citation rate.
- **References:** AbstentionBench variant-construction method
  (Kirichenko, Ibrahim, Chaudhuri, Bell, NeurIPS 2025 Datasets &
  Benchmarks, arXiv 2506.09038 — venue VERIFIED); Kalai, Nachum,
  Vempala, Zhang, "Why Language Models Hallucinate" (arXiv 2509.04664)
  for framing; Wen et al., "Know Your Limits: A Survey of Abstention in
  Large Language Models" (TACL 2025, 2025.tacl-1.26, arXiv 2407.18418)
  as survey anchor.
- **Data needed:** LitQA2 PDFs (shared with T1 — ingest a controlled
  subset, withhold the rest); a shadow Qdrant collection (tooling to
  build it is part of the item); ~30 min of varghele's time inventing
  plausible-but-fake references for stratum 3 (domain plausibility
  matters; don't fully automate this).
- **Verify-first task:** confirm nothing comparable shipped in
  2025–26 before claiming novelty in the paper (briefing §2.5 says
  verify; a half-day literature pass — I can run this as a deep
  research task when you're ready).
- **Status (2026-07-26): 2 of 3 strata DONE.** Stratum 3 (fabricated papers /
  fake DOIs) measured (`scorecards/2026-07-10_abstention-c1-fabricated`, +per-arm
  bare/RAG/agentic in the T2 work: confab ~7% bare -> ~0% agentic). Stratum 1
  (LitQA2 source withheld -> shadow Qdrant) measured
  (`2026-07-10_abstention-c2-shadow`). **Stratum 2 (local-pool queries with DOIs
  removed) BLOCKED on Phase 4** (the local pool). **Risk-coverage DONE
  (2026-07-27, `scorecards/2026-07-27_risk-coverage`,
  `munin_bench.abstention.risk_coverage`):** selective-prediction operating
  points with item-level bootstrap CIs from the existing captures (no new
  inference; refreshed 2026-07-27 with the clean agentic run). Agentic reaches
  the good corner on the LitQA2 answerable set (coverage **0.925** /
  selective-risk 0.092 - up from the degraded run's 0.76 as truncations vanished)
  vs bare (0.63 / 0.52) and rag (0.24 / 0.29); corpus-grounded strata plotted
  alongside (c2-present over-abstention,
  c2-absent, c1-fabricated). A within-run *swept* curve needs a per-item
  confidence score (answer-letter logprob) the live capture doesn't yet emit -
  one-line addition to the next live run, noted in the scorecard.
  **Verify-first novelty pass DONE (2026-07-27, see CITATIONS-VERIFIED.md):**
  the strong "nothing public covers corpus-grounded abstention" claim does NOT
  survive — KnowOrNot (arXiv 2505.13545, May 2025) already operationalizes
  out-of-KB abstention in RAG (govt-policy domain). Narrowed novel claim that
  holds: scientific-literature + DOI-level construct-time ground truth + joint
  corpus-grounded-abstention & confabulated-citation axes against a live RAG
  stack. Soften the plan-doc novelty wording + cite KnowOrNot before drafting.

### T4. MiniCheck local faithfulness judge

- **What:** adopt MiniCheck as the primary, locally-running
  faithfulness scorer for all end-to-end answers; validate it first
  on a ~100-item RAGTruth sample (AUROC vs. the human span
  annotations); wire it into T2's scoring.
- **Why:** faithfulness measurement that doesn't ship user queries to
  a frontier API — the evaluation methodology then *itself* honours
  the paper's privacy thesis, which is a sentence worth writing.
  Also removes the per-rerun LLM-judge cost that would otherwise make
  Track E's "re-run on every model swap" economically silly.
- **References:** MiniCheck (Tang et al. 2024, arXiv 2404.10774);
  RAGTruth (Niu et al. 2024, arXiv 2401.00396); LLM-judge critiques
  already in `munin.bib` (zheng2023judging etc.) for why a validated
  small judge beats an unvalidated big one.
- **Data needed:** MiniCheck model weights from HF (pick the largest
  variant that fits GPU 0 alongside batch jobs — the 7B Bespoke
  variant or flan-T5-large fallback); RAGTruth corpus from its GitHub
  release.
- **Status (2026-07-26): DONE (judge validated + wired).** Judge validated on
  RAGTruth (`scorecards/2026-07-08_faithfulness-judge-ragtruth`, QA AUROC ~0.95);
  agentic-live faithfulness measured (`2026-07-08/09_faithfulness-agentic-live*`).
  NB the T2-agentic faithfulness *retry* was dropped (needs clean agentic answers
  we won't regenerate under the Brave-cost decision); RAG-arm grounding is still
  scoreable from stored contexts if wanted. GPU-OOM caveat: run when GPU 0 has
  headroom (not while TP=2 vLLM saturates both cards).

### T5. Cost-accuracy accounting

- **What:** per-run logging of prompt/completion tokens, wall-clock,
  and inference-time for every arm and benchmark; one accuracy-vs-cost
  plot per benchmark in the paper.
- **RECONCILED 2026-06-18 (KICKOFF-QUESTIONS Q5):** `sacct` accounting
  is **disabled** on hugin (no `slurmdbd`), so "GPU-seconds via SLURM
  accounting" does not work. Cost = (1) **tokens** from the vLLM API
  `usage` field (exact, hardware-independent, primary axis); (2)
  **end-to-end wall-clock** in-process; (3) **inference-time** as the
  GPU-seconds proxy, **measured** by timing the model call at
  **concurrency=1** (when the GPU serves one request, inference-call
  wall-clock IS that query's GPU-occupancy, prefill+decode folded
  correctly). Do NOT estimate GPU-seconds as `tokens ÷ blended
  throughput` (the prefill/decode asymmetry biases it for RAG's
  long-prompt/short-answer shape), and do NOT use `sacct`. Inference-
  time is kept (not dropped as redundant) because it decomposes the
  harness arm's latency into inference vs retrieval vs tool execution.
  Implication: the T2 ablation arms run at concurrency=1 for this
  number to be valid.
- **Why:** briefing finding 4 — cost-aware Pareto is now table
  stakes. For a self-hosted system the cost axis is compute time,
  not dollars, which differentiates the plot from API-model
  leaderboards and reinforces the on-prem story. Cheap: the retrieval
  spec (§3d) already mandates per-query timing; this extends it to
  tokens and GPU-seconds.
- **References:** AstaBench cost-aware (accuracy vs inference cost)
  Pareto leaderboard methodology, arXiv 2510.21652 — adopt the
  presentation, cite as methodology precedent.
- **Data needed:** none external.
- **Status (2026-07-26): DONE.** Per-arm cost (mean prompt/completion tokens,
  inference-time, mean tool calls) recorded in the ablation scorecards
  (`2026-07-13_harness-ablation`, `2026-07-26_harness-ablation`); cost-accuracy
  Pareto computed. Caveat: the concurrency=1 requirement for the inference-time
  proxy was NOT met in the fast 07-26 re-run (concurrency=8 for speed) - token
  cost is still valid; re-measure inference-time at concurrency=1 on a small
  subset if the GPU-seconds axis is used in a figure.

### T6. Scorecard runner + diff (`run_all` / `compare`)

- **What:** single-command full-suite run producing a versioned,
  git-committed scorecard (model, git SHA, corpus snapshot, all
  metrics); a diff command with significance testing between any two
  scorecards; `--with-reliability` flag folding the existing
  flakiness-suite summary in.
- **Why:** this IS the user-stated shipping requirement ("re-run when
  I upgrade a model or part of the harness"), and the scorecard
  history is the raw material for RQ-M2's re-certification argument.
  The paper presents it as a regression suite, not a certification
  protocol — the validated-thresholds claim stays in the follow-up.
- **References:** none needed (engineering); RQ-M2 framing from the
  briefing for the Future Work paragraph.
- **Data needed:** none. Budget target: full run < 8 h on hugin so a
  model swap evaluates overnight.
- **Status (2026-07-26): DONE.** `run_all` (tracks: beir/litqa2-retrieval/
  litqa2-answer/faithfulness/abstention/ablation) + `compare` (paired bootstrap)
  exist and produce committed scorecards; `--with-reliability` folds in the
  behavioural QA suite; `certify` gate against `certification_thresholds.json`.
  NB: full `run_all` now needs the eval cost guard (task #22) - the agentic
  tracks hit live Brave/S2, so don't run the full suite until web/S2 is
  cached/disabled.

### T7. Local-pool answer-level extension

- **What:** extend the Phase 4 pooled benchmark from
  ranking-evaluation to QA: for each of the 100–200 curated queries,
  the three T2 arms produce answers, scored for faithfulness (T4)
  and, where the query has a factual target, correctness.
- **Why:** retrieval metrics are a proxy; reviewers (and finding-2's
  caution) want the answer-level result on the deployment corpus.
  Also the substrate for stratum 2 of the abstention set.
- **References:** methodology per TREC pooling + RAGAS/ARES framing
  already in the retrieval spec.
- **Data needed:** the Phase 4 curation (already required); ~1 extra
  annotator-hour to mark which queries have checkable factual
  targets vs. open-ended ones.
- **Status (2026-07-26): DEFERRED.** Blocked on the Phase-4 local pool (the
  curated queries + qrels), same dependency as T1 Phase 4 and T3 stratum 2.
  Human curation, not compute.

---

## P1 — in-paper if time allows

### T8. LitSearch

- **What:** add LitSearch (Ajith et al. 2024, arXiv 2407.18940) as a
  sixth subset in Phase 3 — retrieval from real natural-language
  literature-search queries.
- **Why:** closest public benchmark to Munin's actual usage pattern
  (paper-finding from questions, not claim verification). Cheap once
  Phase 3 infrastructure exists.
- **Data needed:** LitSearch corpus + qrels (public, GitHub/HF).
  Caveat for the paper: ML/NLP domain, not chemistry.
- **Status (2026-07-28): BUILT + RUN.** 597 queries / 64,183 docs, BGE-large
  (production encoder), isolated `eval_litsearch`. BGE-dense **nDCG@10 0.485**
  > BM25 0.378 (+0.107, p≈0) — the reverse of the SPECTER-era BEIR result;
  RRF adds nothing over dense (n.s.). **It also found a production bug:**
  citation-rerank scores **0.117** (−0.368 vs dense, p≈0) because
  `compute_citation_score` spans [0,1] while BGE cosine spans ~0.087 inside the
  pool, so the nominal 70/30 relevance/citation split behaves as ~83/17
  citation-first (4.95x influence ratio). Affected `/search/hybrid` in
  production — **FIXED 2026-07-28**: min-max normalise the vector score within
  the fetched pool (`main.py::hybrid_search` + the mirrored eval retriever).
  Post-fix re-run on the same collection: citation-rerank **0.117 -> 0.469
  nDCG@10** (+0.352), now at practical parity with dense (-0.016, p=0.014);
  BM25/dense/RRF bit-identical as a control. The fix removes active harm rather
  than creating a gain — on a near-single-target benchmark (1.07 relevant/query)
  a popularity prior has little to add. **NOT YET DEPLOYED to /opt/munin.**
  This ALSO revises the Phase-5 cold-start explanation — the
  collapse reproduces on a dense citation graph. Scorecard
  `2026-07-28_litsearch.json`; runner `munin_bench/benchmarks/litsearch_runner.py`.
  Unlike BEIR (empty graph -> citation-rerank degenerates to dense), LitSearch
  is the first public benchmark where this retriever is genuinely exercised
  (top-10 changed on 597/597 queries).

### T9. RAGAS external-judge cross-check

- **What:** the existing §4a spec, repositioned: run once on the
  50-query subset to report MiniCheck-vs-frontier-judge correlation,
  then retire from the re-run loop.
- **Why:** validates T4's local judge against the field-standard
  approach without inheriting its cost or privacy problems.
- **Data needed:** LLM-judge budget (varghele sets the cap, per §4a).
- **Status (2026-07-28): DROPPED — deliberate scope cut, not an oversight.**
  Three blockers, none of them schedule: (1) no frontier API key exists on the
  cluster (checked `/opt/hugin/config`, `/opt/munin/config`, and the retrieval
  container: none); (2) the judge spend cap was never set — it is still listed
  as an open decision below; (3) it would send Munin answers + retrieved
  contexts to a third-party API, i.e. off-premises egress of eval data, which
  is precisely what the privacy thesis argues against.
  **Why dropping is defensible on the merits:** T9's purpose is to validate the
  local MiniCheck judge. That validation already exists and is *stronger* —
  MiniCheck was scored against **RAGTruth's human span annotations** (QA AUROC
  **0.950**, `2026-07-08_faithfulness-judge-ragtruth`), i.e. against ground
  truth rather than against another model's opinion. Agreement with a frontier
  judge would be weaker evidence than agreement with human annotators. State it
  this way in the paper: the local judge is validated against humans, and no
  eval data leaves the premises.

### T10. QASPER anchor

- **What:** QASPER (Dasigi et al., NAACL 2021) — QA over full NLP
  papers with evidence selection. Run the harness arm (read_paper
  tool path) on a sample.
- **Why:** exercises full-text reading, which LitQA2 covers only via
  its own corpus; cheap external anchor for the `read_paper` tool.
  Drop without regret if schedule is tight — lowest priority of P1.
- **Data needed:** QASPER from AllenAI (public).

### T11. Tool-use reliability metrics

- **What:** from T2's harness-arm telemetry: tool-call error rate,
  recovery rate, mean calls per query, per-tool breakdown.
- **Why:** quantifies harness robustness for the engineering section;
  feeds Track E scorecards; near-zero cost once T2 logs trajectories.
- **Data needed:** none.
- **Status (2026-07-27): BUILT.** Capture: `_agentic_one` now logs per-tool
  `tool_events` (hard `is_error` + soft `degraded` = self-reported
  warning/engines_unresponsive). Scorer: `munin_bench/toolreliability/score.py`.
  Key design finding: a hard-error-only metric MISSES the dominant failure mode
  - `web_search` catches a Brave 402 and returns a warning, so it shows 0%
  hard-error during a total outage; T11 therefore reports BOTH error_rate and
  degraded_rate. **CLEAN baseline DONE (2026-07-27, 199 Q,
  `scorecards/2026-07-27_toolreliability-clean`,** from the same clean agentic
  run as T2): mean **8.61 calls/q** (HALF the degraded-state 16.1 - a healthy
  agent that gets real results thrashes far less), error_rate 6.1%,
  degraded_rate 24.0%, recovery **1.0**. Per-tool: the answer-critical LOCAL
  tools are essentially perfect - paper_search 330 calls 0/0, source 266 calls
  0.4% err, semantic_scholar 451 calls 0/0. Degradation is confined to the WEB
  tier: web_fetch 45% error (datacenter-IP block on arbitrary page fetches),
  web_search "degraded" 1.0 - BUT that flag fires on the dead KEYLESS fallback
  engines (ddg/startpage/etc, CAPTCHA'd from the datacenter IP) while the
  primary Brave tier was 200-OK all run, so it OVERSTATES real degradation.
  Two capture caveats to note in the writeup: (1) S2 rate-limiting is logged as
  a WARNING but not surfaced in the tool result dict, so it shows 0 degraded
  here despite ~8 questions hitting S2 limits (same blind spot we fixed for
  Brave 402, now for S2); (2) web_search degraded conflates "primary failed"
  with "a fallback engine unresponsive." Headline finding stands: the harness
  is robust because local corpus tools are rock-solid and the agent recovers
  from web-tier flakiness (recovery 1.0, 0 unparseable, 0.839 accuracy). Beyond
  routing-eval's per-item forbidden/soft_max_calls, T11 adds aggregate
  error/degraded/recovery + per-tool rates.

---

## F — follow-up proposal scope

### T12. AstaBench via InspectAI (arXiv 2510.21652)

- **What:** wrap Munin's harness arm as an InspectAI agent; run
  selected AstaBench literature-understanding components
  (PaperFindingBench, LitQA2-FullText-Search, ScholarQA-CS2).
- **Why deferred:** open-weight agents reportedly reach ~11% overall
  on AstaBench — running now produces a small number next to frontier
  systems and helps nothing in the tool paper. The follow-up proposal
  is where leaderboard positioning belongs. Architecture prep is done
  in-paper for free: T2's arms are plain async callables, which is
  the shape an InspectAI bridge needs.
- **Data needed:** `allenai/asta-bench` repo access; InspectAI
  package; verify both exist as the briefing describes.

### T13. AbstentionBench public variants (arXiv 2506.09038)

- **What:** run the harness on AbstentionBench's GSM8K/GPQA/MMLU
  abstain variants to position Munin's abstention behaviour against
  the public baseline numbers.
- **Why deferred:** general-knowledge abstention is not Munin's
  claim; corpus-grounded abstention (T3) is. The public variants add
  comparability for the proposal, not the paper.
- **Data needed:** AbstentionBench datasets (arXiv 2506.09038; paper
  VERIFIED, dataset release state still to confirm at use time).

### T14. FACTS-Grounding-style long-form grounding

- **What:** adapt the FACTS Grounding (Google DeepMind, 2501.03200)
  protocol — long-form answers judged for full grounding in provided
  documents — to deep-research reports specifically.
- **Why deferred:** deep-research report evaluation is a long-form
  judging problem with real judge-budget cost; the paper's
  deep-research section survives on a worked example, and the
  rigorous version belongs with the proposal's certification work.
- **Data needed:** judge budget; a sample of completed deep-research
  jobs from the deployment.

### T15. Hyperpolarization expert benchmark

- **What:** expert-authored and expert-vetted items — metadata
  extraction, domain QA, provenance — over hyperpolarization
  literature; the briefing's seed-domain benchmark.
- **Why deferred:** expert availability is a "firm maybe"; an
  unvetted domain benchmark is worse for credibility than none.
  **Optional in-paper pilot:** 20–30 self-authored items labelled
  explicitly as a pilot demonstrating the item format — include only
  if T1–T7 are done early.
- **Data needed:** experts (2+ for vetting), item-writing guidelines,
  the group's hyperpolarization papers (already in corpus).

### T16. Certification thresholds + predictive validity

- **What:** define pass thresholds over the scorecard metrics
  (faithfulness, abstention calibration, tool success, cost budget)
  and gather evidence that passing predicts trustworthy field
  behaviour; the re-certification-on-swap routine formalized.
- **Why deferred:** this is RQ-M2's research content — defining
  thresholds is easy, *validating* them is the contribution, and
  validation needs the scorecard history that T6 only starts
  accumulating now. Every T6 scorecard committed between now and the
  proposal strengthens this item.
- **Data needed:** months of scorecard history across model/harness
  changes; deployment incident/feedback records to correlate against.

### T17. PaperArena / AutoResearchBench (VERIFIED 2026-07-26)

- **What:** tool-augmented agentic literature reasoning
  (PaperArena, arXiv 2510.10909, USTC ai4science) and deep/wide discovery
  (AutoResearchBench, arXiv 2604.25256, Xiong & Luo, Apr 2026).
- **Why deferred:** both are very hard, but note the CORRECTED numbers:
  **AutoResearchBench** ~9% (9.39% Deep / 9.31% IoU Wide) for top LLMs -> a
  35B score is noise there. **PaperArena** is NOT <10%: Gemini 2.5 Pro
  multi-agent 38.78% overall / **18.47% hard** (vs 83.5% expert) - the earlier
  "<10% on the hard splits" note conflated it with AutoResearchBench (fixed).
  PaperArena's "agents over-invoke tools" finding is directly relevant to
  Munin's over-tooling work. Track for the proposal's related-work positioning.
- **Verification:** IDs, venues, authors confirmed - see
  `CITATIONS-VERIFIED.md`.

---

## Data acquisition checklist (start now, in order of lead time)

1. **LitQA2 source-paper PDFs** (T1, T3) — varghele confirmed
   obtainable; longest lead, blocks two P0 items. Keep an explicit
   ingest-list vs. withhold-list from day one — the abstention set
   depends on knowing which is which.
2. **Phase 4 query curation + annotation time** (T1, T7) — varghele +
   one group member; ~2 weeks calendar including adjudication.
3. **MiniCheck weights + RAGTruth corpus** (T4) — public downloads,
   quick; do early so judge validation isn't on the critical path.
4. **LitSearch, QASPER** (T8, T10) — public, quick, P1.
5. **Stratum-3 fake references** (T3) — 30 min of varghele's domain
   imagination.
6. ~~**LLM-judge budget decision** (T9)~~ MOOT — T9 dropped 2026-07-28 (no frontier key, unset cap, and it would egress eval data; the local judge is already human-validated at QA AUROC 0.95). See T9.
7. ~~**Verification pass on `[VERIFY]` anchors**~~ DONE 2026-07-26.
   Every anchor confirmed real and citable with arXiv IDs and venues in
   `CITATIONS-VERIFIED.md`; one factual error found and fixed (T17
   PaperArena, below). Safe to enter munin.bib.

## Suggested paper-push sequencing (calendar view)

- **Weeks 1–2:** T1 Phases 1–3 build; LitQA2 PDF collection runs in
  parallel; MiniCheck validation (T4).
- **Weeks 2–4:** Phase 4 curation + annotation (human-gated); T2 arm
  wrappers + T5 accounting built while waiting.
- **Weeks 4–5:** Phase 4e/5 runs; T7 answer-level scoring; T3
  abstention set built and run.
- **Week 6:** T2 full ablation runs; T6 scorecard wraps everything;
  first committed scorecard.
- **Buffer:** T8–T11 as schedule allows; .tex Evaluation section
  rewrite from actual numbers.
