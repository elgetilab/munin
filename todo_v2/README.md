# todo_v2 — index

At-a-glance status. **Completed plans live in `done/`**; active/remaining plans
stay here. Updated 2026-07-06.

## Done (`done/`)

| Plan | What it was |
|---|---|
| `ENCODER-MIGRATION-PLAN.md` | SPECTER-v1 -> BGE-large paper encoder. **Deployed.** |
| `ENCODER-PHASE-A-TASKS.md` | Full-corpus BGE validation (Recall@10 0.44->0.73). |
| `ENCODER-PHASE-B-IMPLEMENTATION.md` | Flag-gated cutover code. Applied + live. |
| `ENCODER-PHASE-C-MEASUREMENT.md` | Answer before/after (+0.075 acc, p=0.028). |
| `A0-PLAN.md` .. `A3-PLAN.md` | Persona->router migration A0-A3. Built + deployed. |
| `CONTEXT-BUDGET-FIX-SCOPE.md` | 3-tier context-budget fix. Deployed. |
| `PERSONA-CONSOLIDATION-PLAN.md` | One Munin identity, 3 routing profiles. Done. |

Eval-suite results writeup: `backend/benchmarks/RESULTS.md` (canonical).

## Remaining (here)

| Plan | Status / what's left |
|---|---|
| `EVAL-SUITE-MASTER-PLAN.md` | Tracks B/C/D/F not built (see "What's next"). |
| `RETRIEVAL-EVAL-SPEC.md` | Phases 1-3,5 done; **Phase 4 (local pool) deferred** (needs more usage / synthetic queries). Canonical retrieval spec. |
| `BENCHMARK-TODO.md` | Benchmark landscape TODO; specced, not built. |
| `A4-PLAN.md`, `A5-PLAN.md` | Router **A4** (delete delegation machinery + allowlists) and **A5** (paraphrase density / revisit reroute) — pending. |
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
   - **Router A4/A5** - delete delegation machinery + allowlists; paraphrase
     density / revisit reroute.
   - **Migration loose ends** (`MIGRATION-LOOSE-ENDS.md`) - varghele/root:
     repoint the nightly embedding-map at `papers_bge` (do soon), then retire
     the old `papers` collection after a soak (deferred, one-way).

**2. Harness iterations** - improve/finish the agentic harness itself
   (abstention behaviour, tool use, retrieval loop). Active development, not
   measurement.

**3. Then C and D, on the finished harness**
   - **Track C - corpus-grounded abstention benchmark** (over-abstention vs
     correct "not in corpus"). Master plan sec 4.
   - **Track D - harness ablation** (bare vs vanilla-RAG vs full agentic).
     Master plan sec 5. The empirical backbone of the harness contribution claim.

**Deferred:** Phase 4 local pool (blocked on more real usage / a synthetic-query
track).
