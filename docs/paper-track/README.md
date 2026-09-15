# Paper track — index

The eval suite and everything that feeds the paper: retrieval benchmarks,
the harness ablation, abstention, faithfulness, reliability. **Completed
plans live in [`done/`](done/)**; what stays here is open work. Renamed from
`todo_v2/` on 2026-08-04. Updated 2026-09-15.

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
| `CITATIONS-VERIFIED.md` | `[VERIFY]` anchor pass 2026-07-26; every anchor checked, no markers remain. |
| `MODEL-SWAP-QWEN38-PLAN.md` | Qwen3.6-35B-A3B -> Qwen3.8-27B swap and re-measurement. **Steps 1-2 DONE 2026-08-26** (model live on TP=2/64k; Track D, per-arm faithfulness, T11 re-run, scorecards `2026-08-26_*`). Step 3 done for RESULTS.md and PAPER.md 2026-08-27 and for the paper kit 2026-09-14 (next row). Every track since re-run on Qwen3.8. |
| `PAPER-KIT-REFRESH-PLAN.md` | Paper kit brought to the Qwen3.8 provenance, **DONE 2026-09-14**. Same day: standalone LitQA2 answer track re-run on Qwen3.8 (**0.884 [0.839, 0.925]**, agrees with the ablation arm's 0.874 question-paired, p=0.73) and C1 re-run on Qwen3.8 (**100/100 refusals, 0 confabulated local cites**), scorecards `2026-09-14_*` in both folders. Reproduce commands fixed to pass `--encoder bge-large` and `MUNIN_EVAL_EGRESS=full`. **2026-09-15: C2b re-run on Qwen3.8** (correct abstention 0.889 [0.719, 0.961] Wilson, absent-arm accuracy 0.04, paired +0.222 p=0.015 vs Qwen3.6) with the chunk index shadowed too (`papers_chunks_shadow`, a leak path the 07-27 recipe predates), risk-coverage re-derived (agentic risk 0.054), shadow compose rewritten as an `extends` of production (`cd226aa`). Nothing on the retired backbone remains a headline. |

Eval-suite results writeup: `backend/benchmarks/RESULTS.md` (canonical);
claim-to-scorecard index: [`../../PAPER.md`](../../PAPER.md).

## Remaining (here)

| Plan | Status / what's left |
|---|---|
| `EVAL-SUITE-MASTER-PLAN.md` | Suite spec. Tracks A/D built; B has a validated judge + one interim arm; C built and re-run 2026-07-27; F not built. See "What's next". |
| `RETRIEVAL-EVAL-SPEC.md` | Phases 1-3,5 done; **Phase 4 (local pool) deferred** (needs more usage / synthetic queries). Canonical retrieval spec. |
| `BENCHMARK-TODO.md` | Benchmark landscape TODO. **T8 LitSearch BUILT+RUN 2026-07-28** (BGE-dense 0.485 > BM25 0.378; found a production `/search/hybrid` scale-mismatch bug — citation-rerank −0.368 nDCG@10). **T9 DROPPED** (no frontier key, unset cap, would egress eval data; local judge already human-validated at AUROC 0.95). T10 QASPER not started. P0 status 2026-07-27: T2/T4/T5/T6 done, T1 mostly done, T3 re-run done (stratum 2 still needs Phase 4), T7 deferred. **The only human-blocked P0 is Phase 4** (query curation + qrels). |
| `T2-ABLATION-REFRESH-PLAN.md` | Reporting guidance for the Track D/T2 ablation, incl. why the 07-26 run is load-depressed and 07-27 is the headline. |
| `CITATIONS-VERIFIED.md` | Citation verification pass — **all `[VERIFY]` anchors resolved** with arXiv IDs, plus the T3 novelty check (KnowOrNot is prior art) and the PaperArena correction. Bib-ready. |
| `TRACK-B-PLAN.md` | Answer faithfulness (MiniCheck). B1-B4 built; judge validated (QA AUROC 0.95). **Per-arm paired faithfulness DONE 2026-07-27**: RAG 0.326 vs agentic 0.340, paired delta **+0.023 [-0.043, +0.089] p=0.496 (n=189)** — grounding does NOT improve with the harness, despite a 4.9x accuracy gap. **Null replicates on Qwen3.8 (2026-08-26)**: 0.282 vs 0.288, delta +0.010 [-0.052, +0.069], p=0.776 (n=163). `bare` is structurally unscoreable (retrieves nothing). |
| `HARNESS-ITERATION-SCOPE.md` | Scoping for the harness-iteration phase (grounding, over-tooling, tool defs). Pre-implementation. |
| `T2-T3-EDIT-PLAN.md` | Concrete T2 (over-tooling) + T3 (tool defs) edits; measurement + deploy constraints. Plan, not applied. |
| `T1-GROUNDING-PLAN.md` | T1 grounding edits (paper_search excerpts, per-item truncation, structured sub-agent returns). T1a applied; T1b/c planned. |
| `TRACK-C-PLAN.md` | Corpus-grounded abstention. **RE-RUN DONE 2026-07-27 on the current harness.** C1 held (abstain 0.970, 0 confabulated local cites). C2b's 07-10 confound is GONE: at matched `egress=off`, correct-abstention on the answerable subset went **0.200 [0.050, 0.350] -> 0.667 [0.481, 0.852]** (question-paired delta **+0.467 [0.232, 0.697], p<0.001**, CIs added 2026-08-04) and accuracy drops 0.54 -> 0.08 when the source is removed. The system is genuinely corpus-grounded. **Re-run on Qwen3.8**: C1 2026-09-14 (100/100), C2b 2026-09-15 (0.889 [0.719, 0.961] Wilson; absent-arm accuracy 0.04; chunk index shadowed too). Remaining: stratum 2 (needs Phase 4). |
| `KICKOFF-QUESTIONS.md` | Resolved decision record (kept as a live reference cited by the spec). |
| `IMPLEMENTATION-HANDOFF.md` | Overarching eval-suite handoff (reference). |
| `TOOL-ARG-ELISION-SCOPE.md` | Follow-on to the context-budget fix: elide tool-call ARGUMENTS, not just results. **Plan, not implemented.** Replay of the 12 Aug 11-12 overflow turns: pre-fix 12/12 over the window, deployed code 1-2/12, argument elision would close the rest. Blocked on verifying that heavy calls span loop iterations. |
| `MIGRATION-LOOSE-ENDS.md` | Encoder-migration tail: embedding-map repoint + `papers` retirement. varghele/root. |
| `THIRD-MODEL-REVIEW.md` | Third backbone for the harness ablation. **DECIDED 2026-08-25: Qwen3.5-9B**, nothing implemented. Would turn the two-backbone "suggestive" comparison in PAPER.md claim 1 into a three-point one. |

## What's next (ordered plan, set 2026-07-06)

Key sequencing decision: **Tracks C and D characterize the harness, so they run
LAST, against a finished harness** - not while it is still being iterated on. The
~40% abstention seen in the encoder migration is a harness-behaviour signal to
FIX, not just a number to measure. So:

**1. Cleanups first**
   - ~~**Track B - answer faithfulness**~~ DONE. Judge validated (QA AUROC 0.95);
     per-arm paired comparison run 2026-07-27: RAG 0.326 vs agentic 0.340,
     paired delta **+0.023 [-0.043, +0.089] p=0.496**. The harness buys
     correctness/abstention/calibration, NOT literal grounding — state that
     boundary plainly in the paper. `bare` cannot be scored at all (no contexts).
   - ~~**Router A4/A5**~~ DONE 2026-07-08. A4a (delegation deleted), A4b
     (allowlists retired + soft bias, post-soak dead-code cleanup
     `c4e9ce6..45916e6`), A5 (routing tuning) all complete + live; frontend
     dead-handlers verified clean; soak clean. Migration A0-A5 finished. Plans
     in `done/`. NB: the A4b dead-code deletion is committed but not yet
     deployed - a no-op cutover on the next retrieval push.
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
     calls/query, recovery 1.000. Cross-model comparison is suggestive only
     (16 retrieval commits between the runs), see PAPER.md claim 1.
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

**Deferred:** Phase 4 local pool (blocked on more real usage / a synthetic-query
track).
