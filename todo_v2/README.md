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

## What's next (suggested priority)

1. **Track D — harness ablation** (bare model vs vanilla-RAG vs full agentic).
   Newly compelling: the encoder migration showed the model abstains on ~40% of
   questions even with good retrieval, so quantifying what the agentic harness
   actually adds is the sharpest open question. Master plan section 5.
2. **Track C — corpus-grounded abstention benchmark.** Directly probes that
   abstention behaviour (over-abstention vs correct "not in corpus"). Master
   plan section 4; the paper's most novel section.
3. **Track B — answer faithfulness** (local MiniCheck scoring). Master plan §3.
4. **Router A4/A5** — finish the migration cleanup (delete delegation, allowlists).
5. **Migration loose ends** (small): point the nightly embedding-map at
   `papers_bge`; retire the old `papers` collection after a soak.
6. **Phase 4 local pool** — blocked on more real usage; revisit + likely a
   synthetic-query track (see the spec's deferred note).
