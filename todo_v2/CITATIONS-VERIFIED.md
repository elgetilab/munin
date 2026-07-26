# [VERIFY] citation anchors — verification pass (2026-07-26)

All `[VERIFY]`-flagged anchors from `BENCHMARK-TODO.md` /
`EVAL-SUITE-MASTER-PLAN.md` / `IMPLEMENTATION-HANDOFF.md` checked against the
live web (arXiv / ACL Anthology / publisher pages). **Every anchor is real and
citable.** One factual error found in the TODO (PaperArena score, below).
Bib-ready details:

| anchor | verdict | citation (title / authors / venue / arXiv) |
|---|---|---|
| **AstaBench** | ✅ | "AstaBench: Rigorous Benchmarking of AI Agents with a Scientific Research Suite", Allen Institute for AI, **arXiv 2510.21652**. Cost-aware Pareto leaderboard (accuracy vs inference cost) confirmed — the methodology cite is valid. *(TODO had no arXiv ID; add 2510.21652.)* |
| **Asta Paper Finder ~2× ReAct** | ✅ | Asta Paper Finder scores **over double** ReAct on PaperFindingBench (and +15% on LitQA2-FullText-Search) — the "~2× ReAct" claim holds. Source: AstaBench paper (2510.21652) / Ai2 Asta blog. |
| **AbstentionBench** | ✅ | Kirichenko, Ibrahim, Chaudhuri, Bell (Meta FAIR), "AbstentionBench: Reasoning LLMs Fail on Unanswerable Questions", **NeurIPS 2025 Datasets & Benchmarks Track**, **arXiv 2506.09038** (OpenReview OkHC30LLpO). 20 datasets / ~35k unanswerable queries; key finding: reasoning fine-tuning *degrades* abstention (~24%). NeurIPS 2025 venue CONFIRMED. |
| **AbstentionBench public variants (T13)** | ✅ | Same paper; the abstain-variant construction (underspecified / false-premise / stale, incl. new underspecified-reasoning sets) is in 2506.09038. |
| **MedAbstain** | ⚠️ exists | Medical-MCQA abstention benchmark (conformal prediction + adversarial perturbations + insufficient-evidence/missing-info injection). Confirmed to exist; **get the exact arXiv ID + authors before it enters munin.bib** (a distinct related paper, "Knowing When to Abstain: Medical LLMs Under Clinical Uncertainty" arXiv 2601.12471, also exists — don't conflate). |
| **AutoResearchBench** | ✅ | Xiong, Luo, "AutoResearchBench: Benchmarking AI Agents on Complex Scientific Literature Discovery", **arXiv 2604.25256** (Apr 2026). Deep Research (find a target paper) + Wide Research (collect a set). Top LLMs **9.39% Deep / 9.31% IoU Wide** — the "<10% on hard splits" claim is CORRECT *for this benchmark*. (Note the Deep/Wide split mirrors Munin's own DR-breadth framing.) |
| **PaperArena** | ✅ w/ CORRECTION | Wang, Cheng et al. (USTC ai4science), "PaperArena: An Evaluation Benchmark for Tool-Augmented Agentic Reasoning on Scientific Literature", **arXiv 2510.10909**. **CORRECTION: the TODO's "frontier agents score <10% on the hard splits" is WRONG for PaperArena** — Gemini 2.5 Pro (multi-agent) = 38.78% overall, **18.47% on the hard subset** (vs 83.5% PhD-expert). The <10% figure belongs to AutoResearchBench; the TODO (T17) conflated the two. Finding worth citing: agents "invoke far more tools than necessary" (over-tooling) — directly relevant to Munin's own over-tooling work. |
| **HalluLens** | ✅ | Meta FAIR + HKUST, "HalluLens: LLM Hallucination Benchmark", **ACL 2025** (2025.acl-long.1176), **arXiv 2504.17550**. Extrinsic/intrinsic taxonomy + dynamic test-set generation. |
| **FaithBench** | ✅ | "FaithBench: A Diverse Hallucination Benchmark for Summarization by Modern LLMs", **NAACL 2025** (2025.naacl-short.38). Summarization faithfulness over 10 LLM families; adds "questionable"/"benign" gray-area labels. |
| **CSFCube** | ✅ | Mysore, O'Gorman, McCallum, Zamani, "CSFCube — A Test Collection of Computer Science Research Articles for Faceted Query by Example", **arXiv 2103.12906** (2021). Faceted (problem/method/result) QBE retrieval, 50 query-aspect pairs. **Not in BEIR proper** (matches RETRIEVAL-EVAL-SPEC caveat); CS domain, not chemistry — note the domain mismatch if used. |
| **"Know Your Limits"** | ✅ | Wen et al. (UW + Ai2), "Know Your Limits: A Survey of Abstention in Large Language Models", **TACL 2025** (2025.tacl-1.26), **arXiv 2407.18418**. Survey; abstention from query/model/human-values perspectives. |
| **Kalai et al. "Why Language Models Hallucinate"** | ✅ | Kalai, Nachum, Vempala, Zhang (OpenAI), **arXiv 2509.04664** (Sep 2025). Framing: benchmarks reward guessing over abstention; fix = reward calibrated abstention. *(Not [VERIFY]-flagged but confirmed for the framing cite.)* |

## Actions taken
- Fixed the PaperArena `<10%` error in `BENCHMARK-TODO.md` T17.
- All other anchors are safe to enter `munin.bib` with the IDs above.

## Remaining
- **MedAbstain**: confirm exact arXiv/authors before bib (one targeted lookup).
- **T3 novelty check** (is corpus-grounded abstention genuinely novel vs
  2025-26 work?) is a SEPARATE, deeper question than anchor verification — the
  benchmarks above (AbstentionBench, MedAbstain, Know-Your-Limits) are the
  closest prior art to compare against when making the novelty claim.
