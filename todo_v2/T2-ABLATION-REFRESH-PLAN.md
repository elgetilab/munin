# T2 — harness three-arm ablation (refresh + finalize)

Status: APPROVED 2026-07-25, executing. Decisions (varghele): (1) FULL 199
in-corpus questions; (2) RETRY faithfulness-per-arm MiniCheck; (3) local-pool
arm OUT OF SCOPE; (4) INCLUDE abstention-per-arm on the C1 set. Expect the
agentic arm ~6-7h at concurrency=1.

## Key finding: T2 ≈ Track D, already built and run

T2 in `BENCHMARK-TODO.md` ("bare vs vanilla RAG vs full Munin harness, paired")
is the same experiment as the archived **Track D**
(`done/TRACK-D-PLAN.md`), which is DONE and shipped:

- Infra: `backend/benchmarks/munin_bench/ablation/` (`vllm_answer.py`,
  `run_arm.py`, `compare.py`, `abstain_arms.py`, `faithfulness.py`,
  `d_questions.json` + stored qids).
- Wired into the scorecard runner: `run_all.py --tracks ablation`
  (`run_all.py:144-152`).
- Scorecard `scorecards/2026-07-13_harness-ablation.{json,md}` already carries:
  accuracy + precision + abstain per arm (paired-bootstrap deltas w/ CIs),
  per-arm cost (mean prompt/completion tokens, mean inference-time at
  concurrency=1, mean tool calls) = **T5 cost-accounting**, the cost-accuracy
  Pareto, abstention-per-arm on the C1 fabricated set = **T3-adjacent**, and
  partial faithfulness-per-arm (MiniCheck) = **T4-adjacent**.

The 2026-07-13 result (100 in-corpus LitQA2 MCQ, seed 7, paired):

| arm | accuracy | precision | abstain | cost |
|---|---|---|---|---|
| RAG (naive top-5) | 0.15 | 0.52 | 0.70 | 9.6s, 0 tools |
| bare (parametric) | 0.32 | 0.48 | 0.25 | 14.1s, 0 tools |
| agentic (harness) | 0.56 | 0.86 | 0.31 | 118s, 16 tools |

Gate PASSED (harness beats bare, +0.24 [0.11,0.37], p~0). Naive RAG *hurts*
(anchors on imperfect top-5, over-abstains). The harness value is the agentic
loop, not retrieval per se.

## Why it needs a refresh (the real T2 work)

The **agentic arm is stale.** Track D ran at git ~`dfe89f8` on 2026-07-13.
Since then the source agent's full-text reading + the retrieval fixes landed,
taking standalone LitQA2 answer accuracy **0.497 → 0.864** (07-06 → 07-24), plus
the tool retirement and R5. The 0.56 agentic number predates all of it and
almost certainly understates the current system; a re-run should widen the
harness-value delta.

The bare and RAG arms do **not** use the harness, and the model
(`qwen3.6-35b-a3b`) and encoder (BGE-large, live since ~07-05) are unchanged, so
those arms should reproduce within temperature-0.7 noise. But a valid *paired*
bootstrap needs all three arms' per-question verdicts from the same run, so we
re-run all three together (bare/RAG are cheap).

## Plan

1. **Re-run the three arms on the current deployed harness**, same 100 in-corpus
   subset (seed 7, reproduced from the stored qids in `d_questions.json` /
   the scorecard) for continuity with Track D. Command already exists:
   ```
   python -m munin_bench.pipelines.run_all --tag t2-ablation-refresh \
     --tracks ablation --base http://127.0.0.1:8080
   ```
   (agentic arm drives live `/api/chat/completions`; bare/RAG hit vLLM :8000
   directly; concurrency=1 for the cost-timing requirement.)
2. **Refresh the derived views** the scorecard already computes: paired deltas +
   CIs (bare→RAG, RAG→agentic, agentic→bare), the cost-accuracy Pareto, and the
   per-arm abstention on the C1 fabricated set. No new code if the run_all
   ablation path still matches the current APIs (verify first — see Risks).
3. **Commit** `scorecards/2026-07-25_t2-ablation-refresh.{json,md}` and
   `compare` it against `2026-07-13_harness-ablation` to show the harness
   improvement over time (agentic-arm delta) alongside the within-run arm
   deltas.
4. **Reconcile paper framing**: T2 and Track D are one experiment. Update
   `BENCHMARK-TODO.md` T2 to point at the refreshed scorecard as the deliverable;
   don't present them as two results.

## Decisions needed (defaults in bold)

1. **Question set for the re-run.**
   - **(a) Same 100 in-corpus subset (seed 7)** — clean continuity + a fresh
     paired 3-arm; agentic arm ~3.3h at concurrency=1 (118s × 100). RECOMMENDED.
   - (b) Broaden to all 199 in-corpus — more power for the paper, but not paired
     with Track D and ~6-7h agentic. Consider as a later "final" run.
   - (c) Smaller (50) — ~1.7h agentic, faster iteration, weaker CIs.
2. **Faithfulness-per-arm (T4).** Track D's agentic MiniCheck re-score was
   GPU-OOM-blocked. Retry now, or **defer** (accuracy + cost is the clean
   harness-value signal; grounding is Track B's job)? Default: **defer**.
3. **Local-pool QA arm (T2 spec's set (b)).** Blocked on Phase 4 curation
   (deferred, needs more usage / synthetic queries). **Out of scope** for this
   refresh; note as pending. Default: **out of scope**.
4. **Abstention-per-arm refresh (C1).** Cheap (bare/RAG are fast) and it's a
   strong paper point (tool-grounding causes honest refusal: confab ~7% bare →
   ~0% agentic). **Include** in the refresh. Default: **include**.

## Effort / cost

- bare arm: ~100 × 14s ≈ 25 min. RAG arm: ~100 × 10s ≈ 17 min.
- agentic arm: ~100 × 118s ≈ 3.3h at concurrency=1 (dominant cost; run in
  background). Total ~4h wall-clock, all on the live cluster.
- Code: expected near-zero if the ablation track still runs clean; budget a
  small fix pass for API drift (see Risks).

## Risks

- **API drift since 07-13.** The ablation agentic arm calls
  `/api/chat/completions` (research persona) and reuses `litqa2_runner`
  helpers; the tool retirement + background-turns changes may have shifted
  something the runner assumes. Mitigation: a 2-question smoke of the ablation
  track before the full run.
- **Stochasticity.** temperature-0.7 means sub-3-point moves are noise; report
  deltas with CIs, treat the agentic-arm jump (expected large) as the signal.
- **concurrency=1 is required** for the inference-time cost metric — do not
  parallelize the agentic arm to save time, or T5's GPU-seconds proxy breaks.

## Not in this plan (separate items)

- T6 scorecard runner/diff already exists (`run_all` + `compare`); no new work.
- T3 full abstention benchmark, T7 local-pool answer extension, T4 MiniCheck
  as primary judge — their own BENCHMARK-TODO items.
