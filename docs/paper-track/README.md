# Paper track — index

The eval suite and everything that feeds the paper: retrieval benchmarks,
the harness ablation, abstention, faithfulness, reliability. **Completed
plans live in [`done/`](done/)**; what stays here is open work. Renamed from
`todo_v2/` on 2026-08-04. Updated 2026-09-17.

> **Start at [`../../PAPER.md`](../../PAPER.md)** if you want the results
> rather than the plans: it maps every claim to its scorecard and its
> reproduce command.
>
> **Model provenance is split since 2026-08-26.** Production serves
> Qwen3.8-27B and the Track D / faithfulness / T11 headlines were re-measured
> on it (`done/MODEL-SWAP-QWEN38-PLAN.md`), with the standalone answer track
> and C1 following on 2026-09-14 and C2b plus risk-coverage on 2026-09-15
> (`done/PAPER-KIT-REFRESH-PLAN.md`), so every headline is on Qwen3.8 and
> the Qwen3.6-35B-A3B figures are the second backbone. Every 07-27 number quoted in the tables below is the
> Qwen3.6 figure and is kept because the plan was closed on it; the current
> headline is in PAPER.md.
>
> **A third backbone from a different lab, gpt-oss-20b, was measured on
> 2026-09-16** as an eval-only instance beside production
> (`done/BACKBONE-SWITCH-AND-EVAL-PLAN.md`): the harness effect replicates in
> kind at a third of the size (+0.156 vs +0.487). **The faithfulness null was
> reversed on 2026-09-16/17**: the capture had never counted `search` and
> `source` passages as evidence; re-captured, the harness roughly doubles
> supported claims (+0.258, p < 0.001). The TRACK-B-PLAN row below quotes the
> old null because the plan closed on it; PAPER.md claim 2 has the corrected
> number.
>
> **The sibling track is [`../agent-track/`](../agent-track/)** (agent
> architecture and Deep Research). Separate `done/`, separate TODO.
>
> **T-numbers are not global.** `BENCHMARK-TODO.md` numbers benchmark items
> (T1 retrieval suite, T2 ablation, T3 abstention, T4 MiniCheck judge...);
> `HARNESS-ITERATION-SCOPE.md` numbers harness edits (T1 grounding, T2
> over-tooling, T3 tool defs, T4 abstention push-pull). Same labels, different
> namespaces. Always qualify: "BENCHMARK T4" vs "HARNESS T4".

## Done (`done/`)

| Plan | What it was |
|---|---|
| `ENCODER-MIGRATION-PLAN.md` | SPECTER-v1 -> BGE-large paper encoder. **Deployed.** |
| `ENCODER-PHASE-A-TASKS.md` | Full-corpus BGE validation (Recall@10 0.44->0.73). |
| `ENCODER-PHASE-B-IMPLEMENTATION.md` | Flag-gated cutover code. Applied + live. |
| `ENCODER-PHASE-C-MEASUREMENT.md` | Answer before/after (+0.075 acc, p=0.028). |
| `A0-PLAN.md` .. `A3-PLAN.md` | Persona->router migration A0-A3. Built + deployed. |
| `A4-PLAN.md`, `A5-PLAN.md` | Migration A4 (delegation deleted + allowlists retired) and A5 (routing tuning, anchor 0.95). Complete + live; soak clean 2026-07-08. |
| `CONTEXT-BUDGET-FIX-SCOPE.md` | 3-tier context-budget fix. Deployed. |
| `PERSONA-CONSOLIDATION-PLAN.md` | One Munin identity, 3 routing profiles. Done. |
| `TRACK-D-PLAN.md` | Harness ablation. Clean run 2026-07-27 on Qwen3.6 (n=199 paired, finished agent architecture): agentic **0.839** >> bare 0.302 >> RAG 0.171; harness value **+0.538 [0.457, 0.618] p<0.001**. **Re-measured 2026-08-26 on Qwen3.8-27B: 0.874 / 0.387 / 0.211, +0.487 [0.407, 0.568]**, which is the current headline. The 07-13 n=100 pilot (0.56/0.32/0.15) is superseded — do not cite it. |
| `T2-ABLATION-REFRESH-PLAN.md` | Track D refresh. **DONE 2026-07-27**, definitive clean run (n=199 paired). |
| `CITATIONS-VERIFIED.md` | Citation verification pass 2026-07-26: every `[VERIFY]` anchor resolved with arXiv IDs, plus the T3 novelty check (KnowOrNot is prior art) and the PaperArena correction. Bib-ready. |
| `BACKGROUND-TURNS-PLAN.md` | Background turn completion (closed tab, answer still lands). Deployed + live-verified 2026-07-25. Product work that the eval harness relies on for long agentic turns; not a paper result. |
| `MODEL-SWAP-QWEN38-PLAN.md` | Qwen3.6-35B-A3B -> Qwen3.8-27B swap and re-measurement. **Steps 1-2 DONE 2026-08-26** (model live on TP=2/64k; Track D, per-arm faithfulness, T11 re-run, scorecards `2026-08-26_*`). Step 3 done for RESULTS.md and PAPER.md 2026-08-27 and for the paper kit 2026-09-14 (next row). Every track since re-run on Qwen3.8. |
| `THIRD-MODEL-REVIEW.md` | Third backbone candidate review. Decided 2026-08-25 for Qwen3.5-9B (Experiment B, the 16 GB floor), then **superseded 2026-09-15**: Experiment A (a different lab) was run instead with gpt-oss-20b (next row); the 9B is deferred. Its Granite 4.1 row is wrong (dense attention, 80 KiB/token, not hybrid Mamba; corrected in a header note). |
| `BACKBONE-SWITCH-AND-EVAL-PLAN.md` | Model profiles (`backend/config/models/`), `deploy.sh model activate` / `instance up|down|refresh`, `run_suite.sh`, and the gpt-oss-20b full suite as an eval-only instance beside production. **RUN COMPLETE 2026-09-16**: agentic 0.563 vs bare 0.407 vs RAG 0.101, +0.156 [0.075, 0.241] p=0.004, abstain 0.342; C1 0.72 (floor, 20 empty answers) with 0 confabulated local cites; C2b correct abstention 0.80; T11 recovery 1.000; routing 0.647. **2026-09-16/17: faithfulness recapture** (`run_faith_recapture.sh`) on Qwen3.8 and gpt-oss with the complete evidence set reversed the Track B null (Qwen3.8 RAG 0.282 vs agentic 0.540, +0.258 p<0.001; gpt-oss agentic 0.392, paired n=21). Scorecards `2026-09-16_*`. |
| `PAPER-KIT-REFRESH-PLAN.md` | Paper kit brought to the Qwen3.8 provenance, **DONE 2026-09-14**. Same day: standalone LitQA2 answer track re-run on Qwen3.8 (**0.884 [0.839, 0.925]**, agrees with the ablation arm's 0.874 question-paired, p=0.73) and C1 re-run on Qwen3.8 (**100/100 refusals, 0 confabulated local cites**), scorecards `2026-09-14_*` in both folders. Reproduce commands fixed to pass `--encoder bge-large` and `MUNIN_EVAL_EGRESS=full`. **2026-09-15: C2b re-run on Qwen3.8** (correct abstention 0.889 [0.719, 0.961] Wilson, absent-arm accuracy 0.04, paired +0.222 p=0.015 vs Qwen3.6) with the chunk index shadowed too (`papers_chunks_shadow`, a leak path the 07-27 recipe predates), risk-coverage re-derived (agentic risk 0.054), shadow compose rewritten as an `extends` of production (`cd226aa`). Nothing on the retired backbone remains a headline. |

Eval-suite results writeup: `backend/benchmarks/RESULTS.md` (canonical);
claim-to-scorecard index: [`../../PAPER.md`](../../PAPER.md).

## Remaining (here)

| Plan | Status / what's left |
|---|---|
| `EVAL-SUITE-MASTER-PLAN.md` | Suite spec. Tracks A/D built; B has a validated judge + one interim arm; C built and re-run 2026-07-27; F not built. See "What's next". |
| `RETRIEVAL-EVAL-SPEC.md` | Phases 1-3,5 done; **Phase 4 (local pool) deferred** (needs more usage / synthetic queries). Canonical retrieval spec. |
| `BENCHMARK-TODO.md` | Benchmark landscape TODO. **T8 LitSearch BUILT+RUN 2026-07-28** (BGE-dense 0.485 > BM25 0.378; found a production `/search/hybrid` scale-mismatch bug — citation-rerank −0.368 nDCG@10). **T9 DROPPED** (no frontier key, unset cap, would egress eval data; local judge already human-validated at AUROC 0.95). T10 QASPER not started. P0 status 2026-07-27: T2/T4/T5/T6 done, T1 mostly done, T3 re-run done (stratum 2 still needs Phase 4), T7 deferred. **The only human-blocked P0 is Phase 4** (query curation + qrels). |
| `TRACK-B-PLAN.md` | Answer faithfulness (MiniCheck). B1-B4 built; judge validated (QA AUROC 0.95). **Per-arm paired faithfulness DONE 2026-07-27**: RAG 0.326 vs agentic 0.340, paired delta **+0.023 [-0.043, +0.089] p=0.496 (n=189)** — grounding does NOT improve with the harness, despite a 4.9x accuracy gap. **Null replicates on Qwen3.8 (2026-08-26)**: 0.282 vs 0.288, delta +0.010 [-0.052, +0.069], p=0.776 (n=163). `bare` is structurally unscoreable (retrieves nothing). **REVERSED 2026-09-16/17** (`done/BACKBONE-SWITCH-AND-EVAL-PLAN.md`): both nulls were scored without the `search`/`source` passages; re-captured, Qwen3.8 gives RAG 0.282 vs agentic **0.540**, paired **+0.258 [0.206, 0.311] p<0.001** (n=199). |
| `HARNESS-ITERATION-SCOPE.md` | Scoping for the harness-iteration phase (grounding, over-tooling, tool defs). Implementation moved into `T1-GROUNDING-PLAN.md` and `T2-T3-EDIT-PLAN.md` (T1a, T2 and T3 applied); HARNESS T4 (abstention) deferred to Track C. |
| `T2-T3-EDIT-PLAN.md` | Concrete T2 (over-tooling) + T3 (tool defs) edits; measurement + deploy constraints. Both applied (T2 personas, T3 retrieval code), per the plan's own status notes. |
| `T1-GROUNDING-PLAN.md` | T1 grounding edits (paper_search excerpts, per-item truncation, structured sub-agent returns). T1a applied; T1b/c planned. |
| `TRACK-C-PLAN.md` | Corpus-grounded abstention. **RE-RUN DONE 2026-07-27 on the current harness.** C1 held (abstain 0.970, 0 confabulated local cites). C2b's 07-10 confound is GONE: at matched `egress=off`, correct-abstention on the answerable subset went **0.200 [0.050, 0.350] -> 0.667 [0.481, 0.852]** (question-paired delta **+0.467 [0.232, 0.697], p<0.001**, CIs added 2026-08-04) and accuracy drops 0.54 -> 0.08 when the source is removed. The system is genuinely corpus-grounded. **Re-run on Qwen3.8**: C1 2026-09-14 (100/100), C2b 2026-09-15 (0.889 [0.719, 0.961] Wilson; absent-arm accuracy 0.04; chunk index shadowed too). Remaining: stratum 2 (needs Phase 4). |
| `BENCHMARK-AUTHORING-GUIDE.md` | Guide for group members writing items for the internal (Phase 4) benchmark, LitQA2 format plus two additions. Reference, not a plan; the human-curation step Phase 4 is blocked on. |
| `KICKOFF-QUESTIONS.md` | Resolved decision record (kept as a live reference cited by the spec). |
| `IMPLEMENTATION-HANDOFF.md` | Overarching eval-suite handoff (reference). |
| `TOOL-ARG-ELISION-SCOPE.md` | Follow-on to the context-budget fix: elide tool-call ARGUMENTS, not just results. **Plan, not implemented.** Replay of the 12 Aug 11-12 overflow turns: pre-fix 12/12 over the window, deployed code 1-2/12, argument elision would close the rest. Blocked on verifying that heavy calls span loop iterations. |
| `MIGRATION-LOOSE-ENDS.md` | Encoder-migration tail: embedding-map repoint + `papers` retirement. varghele/root. |
| 05-RESULTS p-value check (no plan file) | **Open, needs the cluster.** `docs/paper-kit/05-RESULTS.md` reports the identical paired delta and CI, **−0.005 [−0.050, +0.040]**, for two different comparisons: Qwen3.6 (09-17) − Qwen3.8 (08-26) at **p = 0.93** (line 83) and the Qwen3.8 09-16 recapture − 08-26 at **p = 0.89** (line 216). Either a coincidence or a copy slip. Neither comparison is stored in a scorecard, so re-run both from the per-question run files (`ablation_runs/`, gitignored, cluster only) and correct whichever is wrong. Found 2026-10-01 while checking Figures 2 and 3. |

## What's next (ordered plan, set 2026-07-06)

Key sequencing decision: **Tracks C and D characterize the harness, so they run
LAST, against a finished harness** - not while it is still being iterated on. The
~40% abstention seen in the encoder migration is a harness-behaviour signal to
FIX, not just a number to measure. So:

**1. Cleanups first**
   - ~~**Track B - answer faithfulness**~~ DONE. Judge validated (QA AUROC 0.95);
     per-arm paired comparison run 2026-07-27: RAG 0.326 vs agentic 0.340,
     paired delta **+0.023 [-0.043, +0.089] p=0.496**. ~~The harness buys
     correctness/abstention/calibration, NOT literal grounding~~ **Reversed
     2026-09-17**: that null was the capture missing `search`/`source`
     passages; with them, agentic 0.540 vs RAG 0.282 on Qwen3.8 (+0.258,
     p<0.001). `bare` cannot be scored at all (no contexts).
   - ~~**Router A4/A5**~~ DONE 2026-07-08. A4a (delegation deleted), A4b
     (allowlists retired + soft bias, post-soak dead-code cleanup
     `c4e9ce6..45916e6`), A5 (routing tuning) all complete + live; frontend
     dead-handlers verified clean; soak clean. Migration A0-A5 finished. Plans
     in `done/`. The A4b dead-code deletion has since shipped with later
     retrieval deploys.
   - ~~**Migration loose ends**~~ Task 1 (embedding-map -> `papers_bge`) DONE
     2026-07-07. Task 2 (retire old `papers`) deferred, one-way, post-soak
     (varghele/root) - see `MIGRATION-LOOSE-ENDS.md`.

**2. Harness iterations** - improve/finish the agentic harness itself
   (abstention behaviour, tool use, retrieval loop). Active development, not
   measurement.

**3. Then C and D, on the finished harness**
   - ~~**Track D - harness ablation**~~ DONE. Pilot 2026-07-13 (n=100), then the
     definitive clean run **2026-07-27** on the finished architecture (n=199
     paired): agentic **0.839** >> bare 0.302 >> RAG 0.171, harness value
     **+0.538 [0.457, 0.618] p<0.001** (scorecard `2026-07-27_harness-ablation`).
     Plan in `done/`. The empirical backbone of the harness-contribution claim.
     T11 tool reliability rides on the same run: 8.6 calls/query, **recovery
     rate 1.000** across the 86 queries that hit a tool failure.
     ~~Re-measure on the production model~~ **DONE 2026-08-26** on Qwen3.8-27B:
     agentic 0.874 / bare 0.387 / RAG 0.211, +0.487 [0.407, 0.568]; T11 6.93
     calls/query, recovery 1.000. ~~Cross-model comparison is suggestive
     only (16 retrieval commits between the runs)~~ **Controlled since
     2026-09-17**: the retired Qwen3.6 checkpoint re-run as an eval instance
     on the current protocol gives 0.869 / 0.337 / 0.126, +0.533 [0.452,
     0.613]; agentic equal to Qwen3.8's question-paired (−0.005, p=0.93),
     the bare-arm budget defect worth ~0.035 on the bare arm with the harness
     value unchanged. See PAPER.md claim 1.
     ~~Third backbone from a different lab~~ **DONE 2026-09-16** on
     gpt-oss-20b as an eval instance: 0.563 / 0.407 / 0.101, +0.156 [0.075,
     0.241] p=0.004; replicates in kind at a third of the size, the agentic
     arm abstaining on 34%. Still open: the Qwen3.5-9B 16 GB floor (deferred).
   - **Track C - corpus-grounded abstention benchmark** (over-abstention vs
     correct "not in corpus"). Master plan sec 4. C1 (fabricated papers +
     confabulation detector) and C2b (paired shadow-corpus) both BUILT and RUN
     2026-07-10; risk-coverage operating points derived 2026-07-27.
     ~~re-run C1 + C2 on the current harness~~ **DONE 2026-07-27.** C1 held
     (0.970 vs 0.980, overlapping CIs). C2 moved decisively: correct-abstention
     **0.20 -> 0.67**, which REVERSES the 07-10 verdict that C2 was fatally
     confounded. Two process findings came out of it: (a) `egress` is a
     first-class variable — at `egress=full` the model re-fetches removed source
     papers from S2/Unpaywall (17 of 49), so the absent arm must run
     `egress=off`; (b) `risk_coverage.py` now records capture date AND egress
     per point and refuses to bless a mixed figure. Remaining: stratum 2
     (blocked on Phase 4). ~~CI on the n=27 answerable subset~~ **DONE
     2026-08-04**: 0.667 [0.481, 0.852] bootstrap / [0.478, 0.814] Wilson, and
     the 0.20 -> 0.67 move is now a question-paired test (+0.467 [0.232,
     0.697], p<0.001) rather than an eyeballed non-overlap. The figures above
     are Qwen3.6; re-run on Qwen3.8 in two steps, below.
     ~~C1 on Qwen3.8~~ **DONE 2026-09-14**: 100/100 correct refusals, 0
     confabulated local cites, 5.6 tool calls per item (Qwen3.6: 10.8).
     ~~C2b on Qwen3.8~~ **DONE 2026-09-15**: correct abstention 0.667 -> 0.889
     (paired +0.222, p=0.015, suggestive), absent-arm accuracy 0.08 -> 0.04;
     both `papers_bge` and `papers_chunks` shadowed. Risk-coverage re-derived.
     ~~Qwen3.6 on the current protocol~~ **DONE 2026-09-17**, full suite as an
     eval instance: C1 0.98, C2b 0.875 (paired vs Qwen3.8 −0.014, p=0.85, so
     the +0.222 was the harness), faithfulness 0.627 (+0.336), egress=off
     0.704; every Qwen3.6 caveat in PAPER.md retired.

**Deferred:** Phase 4 local pool (blocked on more real usage / a synthetic-query
track).
