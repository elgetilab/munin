# Track D - harness value & cost (ablation) - plan

Status: DONE 2026-07-13 (agentic 0.56 >> bare 0.32 >> RAG 0.15, all p~0; scorecard 2026-07-13_harness-ablation). Spec: `EVAL-SUITE-MASTER-PLAN.md` sec 5. The empirical
backbone of "the agentic harness adds measurable value over the bare model."
Sharpened by C2 (the model answers 12/20 answerable questions correctly WITHOUT
the local source -> strong parametric knowledge -> "what does the harness add?"
is a real open question). Benchmark-only: NO deploy, no varghele, no new infra.

## The three arms (same questions, paired)

| arm | what | implementation |
|---|---|---|
| **bare** | Qwen3.6-35B, no tools, no RAG | direct vLLM `/v1/chat/completions` (:8000), MCQ prompt, single completion |
| **RAG** | retrieve-then-answer, no loop | BGE top-k over `papers_bge` (bench retriever) -> contexts pasted into the MCQ prompt -> single vLLM completion |
| **agentic** | full Munin harness | live `/api/chat/completions` (research), the path we already measure |

All at **concurrency=1** (the master-plan cost-timing requirement: inference-time
is the GPU-seconds proxy, measured, not from sacct which is disabled).

## Metrics

- **Accuracy** (LitQA2 MCQ, automatic): correct / incorrect / abstain per arm.
  Headline deltas: bare->RAG = value of retrieval; RAG->agentic = value of the
  agentic loop. Paired bootstrap CIs on the deltas (Phase-1 infra).
- **Cost** per arm: mean prompt+completion tokens (vLLM `usage`), mean
  inference-time (s) at concurrency=1, mean tool calls (agentic only).
  Accuracy-vs-cost Pareto point per arm.
- **Tool-use reliability** (agentic arm telemetry): tool-call count, error rate.

## What we reuse

- `litqa2_runner`: `load_litqa2`, `build_mcq`, `parse_letter`, the abstain option.
- bench retrievers (`SpecterDenseRetriever`/BGE via `load_encoder`) against
  `papers_bge` for the RAG arm.
- The answer-track verdict scheme (correct/incorrect/abstain/unparseable).
- vLLM reachable at 127.0.0.1:8000 (`qwen3.6-35b-a3b`), confirmed.

## Design decisions (need a call)

1. **Question set + agentic arm.** The agentic per-query verdicts are NOT stored
   (only the aggregate 0.497 on 199), so a clean PAIRED 3-arm comparison needs a
   fresh agentic run. Options:
   - (a) **Subset (~100 in-corpus, fresh all 3 arms)** - clean paired CIs, agentic
     re-run ~2h + bare/RAG fast. RECOMMENDED.
   - (b) Full 199 bare+RAG fresh, agentic = the existing 0.497 as an UNPAIRED
     reference point (no delta CI vs it). Cheaper but weaker statistics.
2. **Faithfulness across arms (Track B scorer).** MCQ has no free-text claims, so
   faithfulness needs a free-text variant. Scope D to ACCURACY + COST first
   (the clean harness-value signal); free-text faithfulness-per-arm is an
   extension (bare has no contexts anyway). Defer unless wanted.
3. **Abstention set across arms (Track C).** Running bare/RAG on the C1 fabricated
   set would show whether the HARNESS is what enables refusal (does the bare model
   also refuse fabricated DOIs?) - a cheap, interesting add-on. Include or defer?

## Build order

1. `munin_bench/ablation/vllm_answer.py` - direct vLLM MCQ call + usage/timing.
2. `munin_bench/ablation/run_bare.py` - bare arm over the question set.
3. `munin_bench/ablation/run_rag.py` - BGE retrieve -> context prompt -> vLLM.
4. agentic arm: reuse `litqa2_runner._score_one` (live chat) restricted to the set.
5. `munin_bench/ablation/compare_arms.py` - paired accuracy + cost table + Pareto.

## Open questions (answer before building)

1. Q1 above: **subset-fresh-all-3 (recommended)** vs full-199-reuse-agentic?
2. Q3 above: also run the bare/RAG arms on the **C1 fabricated set** (does the
   harness cause the good abstention, or does the bare model refuse too)?
3. Accuracy-only first, or wire the free-text faithfulness extension too?
