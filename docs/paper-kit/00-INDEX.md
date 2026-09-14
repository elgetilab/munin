# Munin paper kit

A self-contained bundle for drafting the Munin paper. Every file here is
written to be read **without access to the repository**: paths are quoted for
reference but nothing depends on following them, and every number is traced to
a committed scorecard file.

Generated 2026-08-04 from the `munin` monorepo at commit `5441a13`.
Refreshed 2026-09-14 to the Qwen3.8-27B provenance (headline re-measurement of
2026-08-26, git `3e0bcfb`); see "Provenance" below for which numbers moved.

## Reading order

| File | What it gives you |
|---|---|
| `01-SYSTEM.md` | Hardware, models, serving configuration, every service and port, the two-target deployment topology, operational envelope. The Implementation section. |
| `02-ARCHITECTURE.md` | The per-turn router, the four named agents and their contracts, the 42-tool inventory, the design principles (handles-not-payloads, four-way outcomes, config-not-inference). The Methods/System-design section. |
| `03-CORPUS.md` | How 68k papers got into the corpus: ingest pipeline, GROBID, crawler, quarantine, metadata repair, quality audits with numbers. |
| `04-METHODS.md` | Evaluation harness design, metric implementations, bootstrap protocol, judge validation, egress as a controlled variable, scorecard provenance, certification gate. |
| `05-RESULTS.md` | Every result table with CIs and p-values, stripped of narrative, ready to become LaTeX. |
| `06-ABLATIONS.md` | The five ablation families in one place, including the levers that backfired. |
| `07-FINDINGS.md` | The diagnostic arc and the negative/null results, each with its mechanism. This is the paper's intellectual spine. |
| `08-LIMITATIONS.md` | What is explicitly not claimed, threats to validity, deferred work, the caveats that must travel with each headline number. |
| `09-RELATED-WORK.md` | Bib-ready anchors with arXiv IDs and venues, all verified 2026-07-26, plus the novelty analysis for the abstention contribution. |
| `10-REPRODUCE.md` | Exact commands, versions, environment, and the two operational gotchas that each cost a day. |
| `scorecards/` | 19 raw scorecard JSONs behind the headline claims, with per-query arrays so figures and paired tests can be regenerated. |

## One-paragraph summary of the work

Munin is a self-hosted AI research platform for a scientific group: a SLURM
cluster runs LLM inference (Qwen3.8-27B-AWQ-INT4 on vLLM, tensor-parallel over
two RTX 5090s), a hybrid retrieval stack (BGE-large over Qdrant, ~68k papers,
plus a Neo4j citation graph), and an agentic harness of four named agents over
45 MCP tools; a small VPS runs authentication, an API gateway, uploads, and a
React chat UI. It has been in production use by one scientific group since
2026-04. The paper's empirical core is a three-arm ablation on 199 paired
LitQA2 questions: the agentic harness scores 0.874 accuracy against 0.387 for
the bare model and 0.211 for naive RAG, a harness value of +0.487 [0.407,
0.568] at p < 0.001. The same ablation on the previous backbone, a 35B/3B-active
MoE, gave the same ordering and a similar magnitude (+0.538), which is
suggestive rather than controlled evidence that the effect is not
backbone-specific. Two findings cut against the obvious narrative and are
reported as first-class results, and both replicate across the two backbones:
naive top-5 RAG is **worse than no retrieval at all** (−0.176 [−0.251,
−0.096]), and answer faithfulness does **not** improve with the harness (paired
delta +0.010, p = 0.776) despite the 4.1x accuracy gap. A third, new with the
swap: the backbone that guesses more freely on its own is held to the same
abstention rate (0.075) inside the harness, with higher precision (0.946).

## Glossary

Terms used throughout, in the sense the repository uses them.

| Term | Meaning |
|---|---|
| **Track A–F** | The six evaluation tracks. A = retrieval quality, B = answer faithfulness, C = abstention and calibration, D = harness value and cost, E = regression harness and scorecards, F = follow-up scope (specified, not built). |
| **Arm** | One system configuration in the Track D ablation: `bare` (direct vLLM, no tools), `rag` (BGE top-5 into the prompt, one completion), `agentic` (the production harness). |
| **Backbone** | The generation model under the harness. Qwen3.8-27B (dense) since 2026-08-25; Qwen3.6-35B-A3B (MoE) before it, retired. Results tables state which. |
| **Profile** | The per-turn routing target: `chat`, `research`, or `code`. Chosen before the first model call by `router.py`. Replaced the older persona-delegation mechanism. |
| **Persona** | The user-facing identity pin. After the 2026-06 consolidation there is one Munin identity with three routing profiles, not three separate assistants. |
| **Egress** | A first-class experimental control on outbound network access: `off` (local corpus only), `oa_only` (+ scholarly APIs), `full` (+ web). Set via the `X-Munin-Egress` header. Benchmarks default to `off`. |
| **corpus_scope** | A separate control from egress, governing provenance: `curated_only`, `curated+oa_cache`, `all`. Egress-off is necessary but not sufficient for a private-corpus claim, because a cached OA paper answers from local disk. |
| **Scorecard** | One committed JSON per benchmark run, carrying a provenance header (model, encoder, git SHA, corpus snapshot, package versions, seed) plus per-metric aggregates and per-query arrays. |
| **Over-abstention** | The system refuses a question whose answer is in the corpus. The usefulness failure mode, as opposed to confabulation. |
| **Confabulation** | Emitting a local-looking citation for a paper that does not exist in the corpus. Measured judge-free by checking emitted DOIs against the corpus. |
| **Degraded (tool call)** | The call returned but with an unusable or empty payload. Distinct from `error`, which is a failed call. |
| **Recovery rate** | Fraction of queries that hit at least one tool failure and still reached a final answer. |
| **read_depth** | Per-citation annotation in a Deep Research report: `full_text`, `abstract`, or `snippet`. Prevents "synthesised from 100 sources" from overstating what was actually read. |
| **verify_level** | Per-artifact annotation from the compute agent: `executed`, `compiled`, `parsed`, or `returned`. Per-language guarantees genuinely differ and the envelope says which one you got. |

## Provenance shared by every number in this kit

- **Generation model**: split by date. **`qwen3.8-27b`**
  (`cyankiwi/Qwen3.8-27B-AWQ-INT4`, dense, vLLM TP=2, 64k,
  `reasoning_effort=medium`) for the headline Track D, per-arm faithfulness
  and T11 numbers (2026-08-26), and it is what production serves.
  `qwen3.6-35b-a3b` (Qwen3.6-35B-A3B-AWQ-4bit, MoE, retired) for everything
  dated earlier, which includes all of Track C (not re-run) and the Qwen3.6
  column kept beside every re-measured table as the second backbone. Retrieval
  numbers have no LLM in the loop and are backbone-independent.
- **Retrieval encoder**: BGE-large-en-v1.5 (1024d, Qdrant collection `papers_bge`)
  since the 2026-07-06 production cutover; SPECTER-v1 (768d, collection `papers`)
  before it. **The two are never pooled.** Every results table states its encoder.
- **Corpus**: 68,462 papers at the 2026-07 runs (68,436 at the 2026-08-03
  author audit); 68,863 entries at 2026-08-28, the nearest recorded count to
  the 2026-08-26 re-measurement.
- **Confidence intervals**: 95% percentile bootstrap, 1000 resamples, seed 42,
  resampling over query indices.
- **Significance**: paired bootstrap over per-query differences, two-sided
  p-value `2 * min(P(diff <= 0), P(diff >= 0))`.
