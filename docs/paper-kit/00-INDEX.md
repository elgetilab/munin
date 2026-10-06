# Munin paper kit

A self-contained bundle for drafting the Munin paper. Every file here is
written to be read **without access to the repository**: paths are quoted for
reference but nothing depends on following them, and every number is traced to
a committed scorecard file.

Generated 2026-08-04 from the `munin` monorepo at commit `edaa0fb`.
Refreshed 2026-09-14 to the Qwen3.8-27B provenance (headline re-measurement of
2026-08-26, git `df14dff`); see "Provenance" below for which numbers moved.
Extended 2026-09-16 with a third backbone from a different lab, gpt-oss-20b
(git `ad46d05`), reported beside the Qwen numbers in every affected table, and
revised 2026-09-17 for the faithfulness recapture (git `c95d849`), which
reversed the per-arm faithfulness null: the accuracy headline stays on the
08-26 capture, the faithfulness headline moves to the 09-16 recapture of the
same arm. Extended 2026-09-17/18 with the retired Qwen3.6 backbone re-run on
an eval-only instance under the current protocol (git `6983e86`), which turned
the Qwen3.6 comparison from a different-protocol one into a question-paired
one.

## Reading order

| File | What it gives you |
|---|---|
| `01-SYSTEM.md` | Hardware, models, serving configuration, every service and port, the two-target deployment topology, operational envelope. The Implementation section. |
| `02-ARCHITECTURE.md` | The per-turn router, the four named agents and their contracts, the 45-tool inventory, the design principles (handles-not-payloads, four-way outcomes, config-not-inference). The Methods/System-design section. |
| `03-CORPUS.md` | How 68k papers got into the corpus: ingest pipeline, GROBID, crawler, quarantine, metadata repair, quality audits with numbers. |
| `04-METHODS.md` | Evaluation harness design, metric implementations, bootstrap protocol, judge validation, egress as a controlled variable, scorecard provenance, certification gate. |
| `05-RESULTS.md` | Every result table with CIs and p-values, stripped of narrative, ready to become LaTeX. |
| `06-ABLATIONS.md` | The five ablation families in one place, including the levers that backfired. |
| `07-FINDINGS.md` | The diagnostic arc and the negative/null results, each with its mechanism. This is the paper's intellectual spine. |
| `08-LIMITATIONS.md` | What is explicitly not claimed, threats to validity, deferred work, the caveats that must travel with each headline number. |
| `09-RELATED-WORK.md` | Bib-ready anchors with arXiv IDs and venues, all verified 2026-07-26, plus the novelty analysis for the abstention contribution. |
| `10-REPRODUCE.md` | Exact commands, versions, environment, and the two operational gotchas that each cost a day. |
| `figures/` | Figure scripts and their rendered PDF/PNG/SVG. `fig_architecture.py` draws the trust boundaries and every path a request takes through them, reading its profile and egress labels from the code (the tool counts go in the caption); see `figures/README.md` for provenance and a draft caption; earlier versions are in `figures/archive/`. |
| `scorecards/` | 54 raw scorecard JSONs behind the headline claims. |
| `verdicts/` | Per-question verdict tables (qids, verdicts, letters; no LitQA2 text) for the Track D, C2b and faithfulness scorecards, from which every paired test in the paper recomputes; `verdicts/README.md` maps each table to its scorecard and claim. |

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
MoE, re-run under the same protocol on 2026-09-17, gives the same ordering and
a similar magnitude (+0.533 [0.452, 0.613]); question-paired, the two backbones
reach the same accuracy inside the harness (-0.005, p = 0.93) and differ only
outside it. One finding cuts against the obvious narrative and is
reported as a first-class result, replicating on three backbones: naive top-5
RAG is **worse than no retrieval at all** (−0.176 [−0.251, −0.096] on
Qwen3.8). A second, that answer faithfulness did **not** improve with the
harness, held on two backbones and three runs until 2026-09-16 and turned out
to be a capture defect: the judge never saw the full-text passages the
harness read. Re-captured with the complete evidence set, the harness roughly
**doubles** supported claims (RAG 0.282 vs agentic 0.540, paired +0.258
[0.206, 0.311], p < 0.001); both generations of the number are reported. A third, new with the
swap: the backbone that guesses more freely on its own is held to the same
abstention rate (0.075) inside the harness, with higher precision (0.946).
A third backbone from a different lab, gpt-oss-20b, replicates the ordering
and the sign of every delta at a third of the harness value (+0.156 [0.075,
0.241], p = 0.004): its bare arm matches Qwen3.8's, but inside the harness it
abstains on a third of answerable questions, so the size of the effect is a
backbone property and one non-Qwen point is not a curve.

## Glossary

Terms used throughout, in the sense the repository uses them.

| Term | Meaning |
|---|---|
| **Track A to F** | The six evaluation tracks. A = retrieval quality, B = answer faithfulness, C = abstention and calibration, D = harness value and cost, E = regression harness and scorecards, F = follow-up scope (specified, not built). |
| **Arm** | One system configuration in the Track D ablation: `bare` (direct vLLM, no tools), `rag` (BGE top-5 into the prompt, one completion), `agentic` (the production harness). |
| **Backbone** | The generation model under the harness. Qwen3.8-27B (dense) since 2026-08-25; Qwen3.6-35B-A3B (MoE) before it, retired from production but re-run on the current protocol on 2026-09-17 as an eval instance; gpt-oss-20b, never production, an eval instance. Results tables state which. |
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
  `qwen3.6-35b-a3b` (Qwen3.6-35B-A3B-AWQ-4bit, MoE, retired from production
  2026-08-25) for everything dated earlier, and for the 2026-09-17 rows, where
  the retired checkpoint was brought back as an eval-only instance and run
  through every track on the current protocol; those are the Qwen3.6 numbers
  to quote, kept beside every table as the second backbone.
  Track C followed on 2026-09-14 (C1) and 2026-09-15 (C2b, risk-coverage), so
  every headline is on Qwen3.8. `gpt-oss-20b` (`openai/gpt-oss-20b`, 21B MoE,
  3.6B active, native MXFP4) for the 2026-09-16 rows: the third backbone, from
  a different lab, run as an eval-only instance beside production and never
  what production served. Retrieval
  numbers are backbone-independent at scoring time, against frozen
  Qwen3.6-era query variants generated once by the production expander.
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
