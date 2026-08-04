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
| **MedAbstain** | ✅ | Machcha, Yerra, Gupta, Sahoo, Sultana, Yu, Yao, "Knowing When to Abstain: Medical LLMs Under Clinical Uncertainty", **EACL 2026 Main** (2026.eacl-long.291), **arXiv 2601.12471**. **CORRECTION to the earlier note:** MedAbstain is NOT a separate paper — it is the benchmark *introduced by* "Knowing When to Abstain" (abstract: "We introduce MedAbstain, a unified benchmark and evaluation protocol for abstention in medical multiple-choice..."). They are one and the same; do not double-cite. Medical MCQA abstention via conformal prediction (LAC/APS) + adversarial perturbations + explicit abstain option + insufficient-evidence injection. Key finding: an explicit abstain option raises safe abstention far more than input perturbations; scale/prompting barely help. |
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

## T3 novelty check (2026-07-27) — is corpus-grounded abstention novel?

**Verdict: the STRONG claim does not survive; a NARROWED claim does.** The plan
(`EVAL-SUITE-MASTER-PLAN.md` L123-124, `TRACK-C-PLAN`) says corpus-grounded /
private-corpus abstention is "a gap nothing public covers." That literal
statement is **not defensible** as of mid-2026 — the concept is named and
operationalized in prior work. The paper must soften it. What remains genuinely
novel is the *specific combination*, not the concept.

Closest prior art, in decreasing similarity:

| work | what it does | overlap with T3 | what it does NOT do |
|---|---|---|---|
| **KnowOrNot** — Foo, Prasad, Khoo, arXiv **2505.13545** (May 2025) | Library + methodology for evaluating **out-of-knowledge-base (OOKB) robustness in RAG** — "LLMs may still hallucinate when presented with questions outside of the knowledge base ... expected to abstain"; auto-generates eval data without gold answers; demo benchmark **PolicyBench** (govt-policy QA chatbots). | **This is the same core concept** as "corpus-grounded abstention." Directly refutes "nothing public covers it." | Govt-policy domain, not scientific literature; **no DOI-level ground truth; no fabricated-citation / confabulation stratum**; not built against a live production RAG stack with source-paper withholding + positive controls. |
| **Sufficient Context** — Joren et al., **ICLR 2025**, arXiv **2411.06037** | Post-hoc classifier for whether *already-retrieved* context is sufficient; shows models answer-instead-of-abstain when context is insufficient; selective-generation method. | Same failure mode (answer vs abstain under missing evidence). | Classifies sufficiency of retrieved context on existing QA sets (HotpotQA/MuSiQue); **does not withhold documents from a corpus by construction**; no citations/DOIs. |
| **RefusalBench** — Muhamed et al., **EACL 2026**, arXiv **2510.10390** | Generative eval of **selective refusal in grounded LMs**; 176 perturbation strategies × 6 uncertainty categories; RefusalBench-NQ + RefusalBench-GaRAGe. | Selective refusal under flawed/insufficient context; programmatic construction. | Perturbs context linguistically rather than removing a known source from a real corpus; general QA, no DOI ground truth, no confabulated-citation rate. |
| **RGB "Negative Rejection"** (Chen et al., AAAI 2024) & **Know2Guess** (2606.26101), **"Purging the Gray Zone"** (2604.14324), **KG-guided abstention eval** (2412.07430) | Various knowledge-boundary / rejection-when-docs-insufficient measures. | Abstain-when-insufficient signal. | None target scientific literature + DOI construct-time ground truth + confabulation jointly. |
| **AbstentionBench** (2506.09038), **Know Your Limits** (2407.18418, survey) | Intrinsic-unanswerability abstention (false premise, underspecified, stale, unknowable). | Abstention framing / metrics vocabulary. | **Not corpus-grounded at all** — answerability is intrinsic to the question, not relative to a deployed corpus. |
| Citation-fabrication line: **CiteAudit** (2602.23452), **CiteCheck** (2605.27700), **"Source or It Didn't Happen"** (2605.08583), **BibTeX hallucination** (2604.03159) | Detect hallucinated/fabricated references & DOIs in generated scientific text (DOI is the worst-performing field; fabrication rates high). | Munin's stratum-3 confabulated-citation rate. | Measure citation *fidelity in generated text*; **do not tie fabrication to corpus-withholding abstention** — orthogonal axis Munin unifies. |

**What is genuinely novel in T3 (safe to claim):** not "corpus-grounded
abstention" per se, but the *combination* — a single scientific-literature
benchmark with **DOI-level ground truth known by construction** that jointly
measures (a) corpus-grounded abstention via **real source-paper withholding
against a live production RAG stack** (shadow-Qdrant, with the ingested paper as
a positive control), and (b) **confabulated-citation rate on fabricated DOIs /
fake authors** — two axes that the abstention literature (KnowOrNot, Sufficient
Context, RefusalBench) and the citation-fabrication literature (CiteAudit,
CiteCheck) each cover *separately* but none together, and none in the
paper-search agent regime. KnowOrNot is the mandatory prior-art cite; frame T3
as "extending OOKB-style abstention (KnowOrNot) to scientific literature with
construct-time DOI ground truth and a paired confabulation axis."

**Plan-doc edits needed before the paper draft:**
- `EVAL-SUITE-MASTER-PLAN.md` L123-124 & L166 ("nothing public does" / "it's
  novel (no public benchmark)") → soften to the narrowed claim above; add
  KnowOrNot as the closest prior art.
- `BENCHMARK-TODO.md` T3 "References" → add KnowOrNot (2505.13545), Sufficient
  Context (2411.06037), RefusalBench (2510.10390), and the citation-fabrication
  cluster.

## Remaining
- **MedAbstain**: DONE (2026-07-27) — = "Knowing When to Abstain" (Machcha et
  al., EACL 2026, arXiv 2601.12471); one paper, not two. Bib-ready.
- **T3 novelty check**: DONE (above). Fold the narrowed claim + KnowOrNot cite
  into the plan docs when the paper draft starts.

All `[VERIFY]` anchors + MedAbstain are now resolved; nothing citation-side is
left open before drafting.
