# todo_v2 — index

At-a-glance status. **Completed plans live in `done/`**; active/remaining plans
stay here. Updated 2026-07-27.

> **Two `todo_v2/` directories exist.** This one (repo root) is the **eval suite
> and paper track**. The other, `backend/benchmarks/todo_v2/`, is the **agent
> architecture and Deep Research** track. They have separate `done/` dirs and
> separate TODOs. Check which you are in before filing anything.
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
| `TRACK-D-PLAN.md` | Harness ablation. **Definitive clean run 2026-07-27** (n=199 paired, finished agent architecture): agentic **0.839** >> bare 0.302 >> RAG 0.171; harness value **+0.538 [0.457, 0.618] p<0.001**. The 07-13 n=100 pilot (0.56/0.32/0.15) is superseded — do not cite it. |

Eval-suite results writeup: `backend/benchmarks/RESULTS.md` (canonical).

## Remaining (here)

| Plan | Status / what's left |
|---|---|
| `EVAL-SUITE-MASTER-PLAN.md` | Suite spec. Tracks A/D built; B has a validated judge + one interim arm; C built and re-run 2026-07-27; F not built. See "What's next". |
| `RETRIEVAL-EVAL-SPEC.md` | Phases 1-3,5 done; **Phase 4 (local pool) deferred** (needs more usage / synthetic queries). Canonical retrieval spec. |
| `BENCHMARK-TODO.md` | Benchmark landscape TODO. P0 status 2026-07-27: T2/T4/T5/T6 done, T1 mostly done, T3 re-run done (stratum 2 still needs Phase 4), T7 deferred. **The only human-blocked P0 is Phase 4** (query curation + qrels). |
| `T2-ABLATION-REFRESH-PLAN.md` | Reporting guidance for the Track D/T2 ablation, incl. why the 07-26 run is load-depressed and 07-27 is the headline. |
| `CITATIONS-VERIFIED.md` | Citation verification pass — **all `[VERIFY]` anchors resolved** with arXiv IDs, plus the T3 novelty check (KnowOrNot is prior art) and the PaperArena correction. Bib-ready. |
| `TRACK-B-PLAN.md` | Answer faithfulness (MiniCheck). B1-B4 built; judge validated (QA AUROC 0.95); interim arm 0.38 claims-supported. |
| `HARNESS-ITERATION-SCOPE.md` | Scoping for the harness-iteration phase (grounding, over-tooling, tool defs). Pre-implementation. |
| `T2-T3-EDIT-PLAN.md` | Concrete T2 (over-tooling) + T3 (tool defs) edits; measurement + deploy constraints. Plan, not applied. |
| `T1-GROUNDING-PLAN.md` | T1 grounding edits (paper_search excerpts, per-item truncation, structured sub-agent returns). T1a applied; T1b/c planned. |
| `TRACK-C-PLAN.md` | Corpus-grounded abstention. **RE-RUN DONE 2026-07-27 on the current harness.** C1 held (abstain 0.970, 0 confabulated local cites). C2b's 07-10 confound is GONE: at matched `egress=off`, correct-abstention on the answerable subset went **0.20 -> 0.67** and accuracy drops 0.54 -> 0.08 when the source is removed. The system is genuinely corpus-grounded. Remaining: stratum 2 (needs Phase 4); CI on the n=27 subset. |
| `KICKOFF-QUESTIONS.md` | Resolved decision record (kept as a live reference cited by the spec). |
| `IMPLEMENTATION-HANDOFF.md` | Overarching eval-suite handoff (reference). |
| `MIGRATION-LOOSE-ENDS.md` | Encoder-migration tail: embedding-map repoint + `papers` retirement. varghele/root. |

## What's next (ordered plan, set 2026-07-06)

Key sequencing decision: **Tracks C and D characterize the harness, so they run
LAST, against a finished harness** - not while it is still being iterated on. The
~40% abstention seen in the encoder migration is a harness-behaviour signal to
FIX, not just a number to measure. So:

**1. Cleanups first**
   - **Track B - answer faithfulness** (local MiniCheck scoring). Master plan sec 3.
     *(only remaining cleanup)*
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
     (blocked on Phase 4), and a CI on the n=27 answerable subset.

**Deferred:** Phase 4 local pool (blocked on more real usage / a synthetic-query
track).
