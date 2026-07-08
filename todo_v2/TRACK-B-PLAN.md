# Track B - Answer faithfulness (local MiniCheck) - implementation plan

Status: DRAFT 2026-07-08. Plan-first; no code until an explicit go.
Spec: `EVAL-SUITE-MASTER-PLAN.md` sec 3. Reuses Track A Phase-1 infra
(`munin_bench/metrics/` bootstrap + significance) and the Track E scorecard.

## What Track B is

The anti-hallucination half of RQ-M1: "Munin's answers are grounded in the
retrieved corpus." A **local** faithfulness judge (privacy story: queries +
answers must not leave the premises) that scores each answer's claims against
the retrieved contexts, producing per-arm faithfulness metrics with paired
bootstrap CIs.

Primary judge: **MiniCheck** (Tang et al. 2024, arXiv 2404.10774) - small
entailment models (Flan-T5-Large .. 7B) at GPT-4-comparable grounding accuracy,
run on GPU0/CPU. Answer -> claims (sentence split); each claim scored vs the
contexts; aggregate to answer level.

## The two structural facts that shape this plan

1. **Track B is fundamentally a SCORER, not a generation pass.** The spec says
   it scores "Track D's three arms (bare / RAG / agentic) over the local pool."
   Track D and the local pool are BOTH deferred to after the harness. So Track B
   splits into: (a) build + VALIDATE the scorer now (independent of Track D,
   validated on RAGTruth which ships its own answer/context/label triples), and
   (b) APPLY it to Munin answers later, when Track D's arms exist. Building the
   validated judge now is exactly the right "cleanup" - it de-risks Track D and
   is on the critical path for it.

2. **The current answer track is multiple-choice.** `litqa2_runner.build_mcq` /
   `parse_letter` score a chosen LETTER, not free text - there are no claims to
   fact-check, and the scorecard does not save retrieved contexts. Faithfulness
   needs (free-text answer, retrieved contexts) pairs. So a thin FREE-TEXT +
   context-capture path is a Track B prerequisite (and is what Track D will feed).

## Phases

### B1. MiniCheck scorer (local, standalone) - the core deliverable

- `munin_bench/faithfulness/minicheck.py`: load the MiniCheck model (new HF
  loader in `clients.py` - `load_entailment(model, device)`, generalising the
  existing `load_encoder`; MiniCheck is a seq2seq/roberta checker, not a
  SentenceTransformer). Default `lytang/MiniCheck-Flan-T5-Large`.
- API: `score(claims: list[str], contexts: list[str]) -> list[float]` (per-claim
  support prob) + `score_answer(answer: str, contexts: list[str]) -> dict`
  (claim split -> per-claim -> answer aggregate).
- Claim decomposition: start with sentence-splitting (spaCy/regex); the MiniCheck
  paper scores at sentence granularity, so no LLM decomposition needed (keeps it
  local + cheap).
- Compute: CPU works for Flan-T5-Large on hundreds of examples (slow but fine);
  GPU0 opportunistically for large sweeps (vLLM holds GPU1). Device via an env
  knob mirroring `MUNIN_BENCH_SPECTER_DEVICE`.

### B2. RAGTruth judge-validation (the GATE - do before trusting B1)

- Fetch a ~100-example stratified sample of **RAGTruth** (Niu et al. 2024,
  arXiv 2401.00396) - span-annotated RAG-hallucination corpus (answer + context
  + hallucination labels). Likely via HuggingFace; note it as a fetch task.
- Score MiniCheck's per-response support against RAGTruth's hallucination labels;
  report **AUROC + agreement**. Commit the validation scorecard.
- **Gate:** if AUROC is poor on our-adjacent (scientific-QA) slices, escalate to
  the larger MiniCheck variant (7B) BEFORE building anything downstream; if still
  poor, flag it loudly - do not silently ship a bad judge.

### B3. Free-text answer + context capture (the bridge to Track D)

- Add a free-text QA mode to the answer runner (or a sibling
  `faithfulness_runner.py`): for each question, drive the live research chat,
  capture the FINAL answer text AND the retrieved contexts from the `rag_context`
  SSE event (already emitted; frontend consumes it today). Persist
  `{qid, question, answer, contexts[]}` per question.
- Run it over the existing LitQA2 pool (free-text, not MCQ) as the interim
  application + end-to-end smoke test of B1. This is NOT the full Track D
  per-arm study - it is one arm (agentic/live) to exercise the pipeline.
- Design the record shape so Track D's three arms drop in later with no scorer
  change (arm is just a field).

### B4. Metrics + scorecard (reuse Phase 1 infra)

- Metrics per the spec: **mean faithfulness, % answers fully supported, %
  answers with >=1 unsupported claim**, per-arm, with paired bootstrap CIs
  (`munin_bench/metrics/bootstrap.py`, seed 42). Emit a Track E scorecard with
  the standard provenance header (judge model, git SHA, corpus snapshot, seed).

### B5. (OPTIONAL, opt-in) frontier cross-check - demoted per spec

- One-time RAGAS/frontier-judge run on a ~50-query subset to report
  MiniCheck-vs-frontier correlation, then rely on MiniCheck for all sweeps.
- **This sends queries + answers OFF-PREM (external judge), which contradicts
  the privacy story.** So it is OFF by default and requires an explicit user
  opt-in; if declined, we ship MiniCheck validated on RAGTruth alone and note
  the frontier cross-check as future work. Caps LLM-judge spend to one subset.

## Deliverables / acceptance

- [ ] B1 MiniCheck scorer, local, with a unit smoke (a supported claim scores
      high, a fabricated claim scores low on a toy context).
- [ ] B2 RAGTruth validation scorecard committed; AUROC reported; gate decision
      recorded (Flan-T5-Large sufficient, or escalate).
- [ ] B3 free-text + `rag_context` capture; LitQA2 pool scored end-to-end (one
      arm) as the interim application; record shape is Track-D-ready.
- [ ] B4 faithfulness metrics + committed scorecard with paired-bootstrap CIs.
- [ ] B5 only if opted in.
- No deploy: Track B lives entirely in `benchmarks/`, talks to the live API over
  HTTP for capture, and scores locally. Nothing ships via `deploy.sh`.

## Open questions (need a decision before building)

1. **Scope now vs at Track D.** Recommend: build B1+B2 (validated scorer) NOW +
   B3 interim single-arm application on the LitQA2 pool; leave the full
   three-arm study to Track D (it reuses this scorer unchanged). Alternative:
   B1+B2 only (pure scorer), defer ALL Munin application to Track D. Default:
   the former - a smoke-tested scorer is worth more than an unexercised one.
2. **MiniCheck size.** Default `MiniCheck-Flan-T5-Large` (fits CPU, GPU0 when
   free), escalate to 7B only if RAGTruth AUROC is poor (spec's fallback). OK?
3. **Frontier cross-check (B5).** Off by default (privacy). Want the
   MiniCheck-vs-frontier correlation number for the paper (opt-in, one-time,
   off-prem on a 50-q subset), or skip and rely on RAGTruth validation alone?
4. **Compute.** RAGTruth validation (~100) + LitQA2 pool (~42-199) on CPU is
   tractable; use GPU0 only if a window opens. Assume CPU-default unless you want
   me to schedule a GPU slot. OK?
