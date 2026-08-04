# 09 Related work

Bib-ready anchors. Every entry below was verified against the primary source
(arXiv, ACL Anthology, or publisher page) on 2026-07-26 or 2026-07-27. One
factual correction found during that pass is recorded in §5.

---

## 1. Direct baselines and benchmarks used

| Work | Citation | Used for |
|---|---|---|
| **PaperQA2 / LitQA2** | Skarlinski et al. 2024, arXiv **2409.13740v2** | The LitQA2 answer benchmark and the published baseline. Definitions verified identical to ours: accuracy = correct / all asked, precision = correct / answered. Reported figures: PaperQA2 accuracy 0.660 ± 0.012 (n=3), precision 0.852 ± 0.011; human experts 0.677 ± 0.119 (n=9), precision 0.738 ± 0.096. |
| **LitSearch** | Ajith et al. 2024, arXiv **2407.18940** | Retrieval benchmark of 597 real natural-language literature-search queries over a 64,183-paper S2ORC corpus. The closest public benchmark to the system's actual usage. |
| **BEIR / SciFact** | Thakur et al. 2021 (BEIR); SciFact, Wadden et al. 2020 | External validity anchor. BM25 reproduction (0.652 measured vs ~0.665 published) validates the measurement harness. |
| **MiniCheck** | Tang et al. 2024, arXiv **2404.10774** | The local faithfulness judge (Flan-T5-Large variant). Small fact-checking models at GPT-4-comparable accuracy on grounding benchmarks. |
| **RAGTruth** | Niu et al. 2024, arXiv **2401.00396** | Span-annotated RAG-hallucination corpus, used to validate the judge before trusting it (QA AUROC 0.950). |

---

## 2. Abstention and selective prediction

| Work | Citation | Relationship |
|---|---|---|
| **KnowOrNot** | Foo, Prasad, Khoo, arXiv **2505.13545** (May 2025) | **The mandatory prior-art cite.** Library and methodology for evaluating out-of-knowledge-base (OOKB) robustness in RAG; auto-generates evaluation data without gold answers; demo benchmark PolicyBench. This is the same core concept as corpus-grounded abstention. |
| **Sufficient Context** | Joren et al., ICLR 2025, arXiv **2411.06037** | Post-hoc classifier for whether already-retrieved context is sufficient; shows models answer instead of abstaining under insufficient context. Same failure mode, different construction (does not withhold documents from a corpus). |
| **RefusalBench** | Muhamed et al., EACL 2026, arXiv **2510.10390** | Generative evaluation of selective refusal in grounded LMs; 176 perturbation strategies x 6 uncertainty categories. Perturbs context linguistically rather than removing a known source from a real corpus. |
| **AbstentionBench** | Kirichenko, Ibrahim, Chaudhuri, Bell (Meta FAIR), NeurIPS 2025 Datasets & Benchmarks, arXiv **2506.09038** | 20 datasets, ~35k unanswerable queries. Targets *intrinsic* unanswerability (false premise, underspecified, stale, unknowable), not corpus-relative answerability. Key finding worth citing: reasoning fine-tuning **degrades** abstention by roughly 24%. |
| **Know Your Limits** | Wen et al. (UW + Ai2), TACL 2025 (2025.tacl-1.26), arXiv **2407.18418** | Survey of abstention in LLMs, from query, model, and human-values perspectives. Vocabulary source. |
| **MedAbstain / "Knowing When to Abstain"** | Machcha et al., EACL 2026 Main (2026.eacl-long.291), arXiv **2601.12471** | Medical MCQA abstention via conformal prediction plus adversarial perturbations. **One paper, not two**: MedAbstain is the benchmark introduced by that paper. Do not double-cite. Key finding: an explicit abstain option raises safe abstention far more than input perturbations; scale and prompting barely help. |
| **RGB "Negative Rejection"** | Chen et al., AAAI 2024 | Knowledge-boundary / rejection-when-documents-insufficient measure. |
| **Why Language Models Hallucinate** | Kalai, Nachum, Vempala, Zhang (OpenAI), arXiv **2509.04664** | Framing cite: benchmark reward structures favour guessing over abstention; the fix is rewarding calibrated abstention. |

---

## 3. Citation fabrication

An orthogonal literature that this work unifies with the abstention axis.

| Work | arXiv | What it does |
|---|---|---|
| CiteAudit | 2602.23452 | Detects hallucinated references in generated scientific text |
| CiteCheck | 2605.27700 | Citation-fidelity checking |
| "Source or It Didn't Happen" | 2605.08583 | Attribution verification |
| BibTeX hallucination | 2604.03159 | DOI is the worst-performing field for fabrication |

All measure citation fidelity **in generated text**. None ties fabrication to
corpus-withholding abstention.

---

## 4. Agentic scientific-literature benchmarks and harnesses

| Work | Citation | Relationship |
|---|---|---|
| **AstaBench** | Allen Institute for AI, arXiv **2510.21652** | Rigorous benchmarking of AI agents with a scientific research suite. **Methodology cite**: the cost-aware Pareto leaderboard (accuracy versus inference cost) is the presentation adopted here, not the infrastructure. Also the source for Asta Paper Finder scoring **over double** ReAct on PaperFindingBench (and +15% on LitQA2-FullText-Search), which reinforces the harness-value framing. |
| **PaperArena** | Wang, Cheng et al. (USTC ai4science), arXiv **2510.10909** | Tool-augmented agentic reasoning on scientific literature. Gemini 2.5 Pro (multi-agent) = 38.78% overall, **18.47% on the hard subset**, against 83.5% for PhD experts. **Directly relevant finding**: agents "invoke far more tools than necessary", which is the same over-tooling phenomenon measured and fixed here. |
| **AutoResearchBench** | Xiong, Luo, arXiv **2604.25256** (Apr 2026) | Deep Research (find a target paper) plus Wide Research (collect a set). Top LLMs score **9.39% Deep / 9.31% IoU Wide**, so the "under 10% on hard splits" claim is correct *for this benchmark*. The Deep/Wide split mirrors the breadth framing used in this system's own deep-research work. |

**Deliberately deferred, with a reason.** Running a 35B open model on
AutoResearchBench now produces noise rather than signal, given that frontier
agents score under 10%. These are positioned as follow-up scope.

---

## 5. Hallucination benchmarks (context, not used)

| Work | Citation |
|---|---|
| **HalluLens** | Meta FAIR + HKUST, ACL 2025 (2025.acl-long.1176), arXiv **2504.17550**. Extrinsic/intrinsic taxonomy plus dynamic test-set generation. |
| **FaithBench** | NAACL 2025 (2025.naacl-short.38). Summarization faithfulness across 10 LLM families; adds "questionable" and "benign" gray-area labels. |
| **CSFCube** | Mysore, O'Gorman, McCallum, Zamani, arXiv **2103.12906** (2021). Faceted query-by-example retrieval, 50 query-aspect pairs. **Not in BEIR proper**; CS domain, so note the mismatch if used. |

### One correction found during verification

An internal note had claimed "frontier agents score under 10% on the hard
splits" for **PaperArena**. That is **wrong for PaperArena** (18.47% hard,
38.78% overall). The under-10% figure belongs to **AutoResearchBench**. The two
were conflated. Corrected in the source documents; do not reintroduce it.

---

## 6. Novelty analysis for the abstention contribution

This was assessed explicitly, and the strong claim did not survive.

**The strong claim does not hold.** An internal plan stated that
corpus-grounded / private-corpus abstention is "a gap nothing public covers."
That is **not defensible** as of mid-2026: KnowOrNot (arXiv 2505.13545) names
and operationalises out-of-knowledge-base abstention in RAG. Any wording along
those lines must be softened.

**What is genuinely novel, and safe to claim:** not corpus-grounded abstention
as a concept, but the **specific combination**. A single scientific-literature
benchmark with **DOI-level ground truth known by construction** that jointly
measures:

1. **Corpus-grounded abstention via real source-paper withholding against a
   live production RAG stack** (a shadow Qdrant collection, with the ingested
   paper as a positive control), and
2. **Confabulated-citation rate on fabricated DOIs and fake authors**, scored
   judge-free by checking emitted DOIs against the corpus.

The abstention literature (KnowOrNot, Sufficient Context, RefusalBench) and the
citation-fabrication literature (CiteAudit, CiteCheck) each cover one of these
axes; none covers both together, and none does so in the paper-search agent
regime.

**Recommended framing:** "extending OOKB-style abstention (KnowOrNot) to
scientific literature with construct-time DOI ground truth and a paired
confabulation axis."

**How the closest prior art differs, in one table:**

| Work | Domain | DOI-level ground truth | Withholds real documents from a live corpus | Confabulation axis |
|---|---|---|---|---|
| KnowOrNot | Government policy | No | No | No |
| Sufficient Context | General QA (HotpotQA, MuSiQue) | No | No | No |
| RefusalBench | General QA | No | No (perturbs context) | No |
| AbstentionBench | General | No | Not corpus-relative at all | No |
| CiteAudit / CiteCheck | Scientific text | Partial | No | Yes |
| **This work** | **Scientific literature** | **Yes, by construction** | **Yes (paired shadow corpus)** | **Yes** |

---

## 7. Positioning the harness contribution

The precedent that the harness-versus-vanilla-RAG comparison builds on:
PaperQA2's own ablations showed scaffolding beats vanilla RAG, and AstaBench's
Paper Finder result reinforces it.

**But the result was not guaranteed**, and this should be said. The briefing
that motivated Track D warned explicitly that a stronger base model can *hurt*
a specialised harness, which is exactly why the experiment was worth running
and reporting honestly either way. Two of this work's results run against the
comfortable narrative: naive RAG scoring **below** the bare model, and
faithfulness **not** improving with the harness.
