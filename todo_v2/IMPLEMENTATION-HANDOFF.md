# MuninAI Implementation Handoff — Router Migration + Evaluation Suite

**Audience:** Claude Code, working in the Munin monorepo (`backend/`).
**Owner:** varghele. **Status:** approved plan. No submission deadline — quality over speed; gates, not dates, control progression.
**Last updated:** 2026-06-11 (v2, written from the full source documents).

---

## 0. Required reading before any code is written

Read in order; all four must be present in the repo. If any is missing, STOP and ask varghele — do not reconstruct from memory.

1. `RETRIEVAL-EVAL-SPEC.md` — authoritative for Track A (retrieval). Its build order, gates, anti-goals, and appendices are binding. Rule inherited from it: **if the spec contradicts the codebase, the code wins — update the spec in a separate commit, then proceed. Never deviate silently.**
2. `EVAL-SUITE-MASTER-PLAN.md` — the six-track architecture (A retrieval / B faithfulness / C abstention / D harness ablation + cost / E scorecard / F follow-up), the `backend/benchmarks/` vs `backend/eval/` boundary contract (§8), and the cross-track build order (§10).
3. `BENCHMARK-TODO.md` — items T1–T17 with priorities (P0/P1/F), references, data needs. Citation anchors are all VERIFIED (2026-07-26, `CITATIONS-VERIFIED.md`) and bib-ready; the `[VERIFY]` convention is retired.
4. `routing_eval.py` — the bespoke routing eval: schema, predicate DSL, scorer, 15 seed items across 14 declared categories. Stubs to wire: `run_item`, `judge_abstention`, `as_inspect_task` (the last stays a stub — see §F below).
5. Background only: `munin-benchmark-landscape-briefing.md`.

Also consult in-repo: `DECISIONS.md` (keeps `backend/scripts/` QA tools where they are), `FRONTEND-REPORT-CHAT.md` (report-chat → regression scenario pipeline), `shared/docs/BACKEND-API.md`.

---

## 1. The ordering rule that governs everything

**The three-arm harness ablation (T2) is the paper's headline experiment, and it measures the harness. The harness must therefore be frozen — router migration complete and soaked — before any answer-level number destined for the paper is produced.**

What this does and does not serialize:

- **Harness-independent (start immediately, parallel to the router migration):** all of Track A. The retrieval harness drives retrievers directly against Qdrant/Neo4j (`munin_bench/retrievers/`) and never goes through `/api/chat/completions`. Phases 1–4 including human annotation, Phase 5's retrieval-only track, MiniCheck validation (T4), T2's arm-1/arm-2 wrappers, T3's tooling, and all data acquisition are routing-independent. Build and run them freely.
- **Harness-dependent (wait for freeze):** anything that calls the chat pipeline — Phase 5's answer track, T7 answer-level runs, T2 arm 3 and the full paired ablation, Track B scoring of arm outputs, T3's abstention runs.
- Pre-migration harness runs exist only as regression baselines (tagged scorecards). Never report one as a paper result.

---

## Part A — Persona → Router migration

### Context

Personas currently do three entangled jobs:
1. **Tool-allowlist scoping** — load-bearing: keeps the MCP schema at ~10–14K tokens, below the vLLM prefill cliff at ~22.5K. Some form of per-request tool subsetting must survive.
2. **Prompt + sampling shaping** (research cold, chat warmer, code gets its checker) — genuine but cheap; a router can do it.
3. **Delegation/handoff machinery** (`delegate_to_persona`, message rewind to pre-loop snapshot, persona persistence to `chats.db`, `delegated` + `persona_changed` SSE events, plan-approval interaction) — expensive, fragile, and the thing being removed.

Replacement for job 1: the existing `CORE_TOOLS` + `tool_search` deferred-tool mechanism (P1 #7). Replacement for job 2: a per-turn up-front router. **Dependency that must not be inverted: harden `tool_search` first (A2), retire allowlists second (A4).** Single model (Qwen3.6-35B-A3B) — no weight swapping is in play.

### A0. Baseline (gate for everything in Part A)

- Wire `routing_eval.py::run_item`: POST `/api/chat/completions`, headers `X-Munin-Email`, body `{messages, ephemeral: true}`; collect ordered `tool_call` SSE events (same stream `scripts/test_delegate_persona.py` parses; `step` increments per assistant-turn boundary so `solo` evaluates). Honour `context.inject_tool_result` via a stub MCP layer for the robustness items (`web_search_degraded`).
- Wire `judge_abstention`: one-line binary rubric against the local vLLM; used only for `expected.abstain` items.
- **`profile` is a label, never an input.** Assert `expected.profile` against the routing event the backend emits; do not send it as the request persona — that grades the router on an answer you handed it.
- Run the full seed set against the CURRENT persona harness; commit as a tagged scorecard (e.g. `scorecards/<date>_routing-pre-migration.json`).

### A1. Pin personas / kill auto-delegation

- Pinning becomes the default: the model never calls `delegate_to_persona`; conversations don't switch persona mid-flight. The user-facing Research/Chat/Code selector keeps working — it sets the pin.
- Tiny, fully reversible. Deploy; soak ~1 week of real group usage watching whether anything genuinely wanted a handoff.

### A2. Harden CORE_TOOLS + tool_search

- Verify with `repro_vllm_hang.py` that the resident schema stays comfortably under the prefill cliff in EVERY mode, including mid-turn unlocks via `tool_search`.
- **Gate:** routing-eval items `citing_papers` and `export_bibtex` green via the `tool_search` path (`via_tool_search_ok=True`). These are the allowlist-retirement acceptance tests. Do not start A4 until green.
- Known scorer nuance: the `via_tool_search_ok` acceptance branch in `score_item` is permissive (discovery counts as routed-correctly even if the unlocked tool fires on a later turn the single-turn capture misses). Acceptable for now; tighten if it masks real failures.

### A3. Up-front per-turn router

- Routing decides tool subset + prompt fragment + sampling preset BEFORE the first model call, per turn, from the query. Cheapest-first:
  1. Rules: slash commands (`/research`, `/write`) — explicit cases for free.
  2. KNN over labeled example queries with existing SPECTER/BGE embeddings — no extra LLM call.
  3. One-shot classification pass ONLY if 1+2 underperform on the routing eval (adds per-turn latency; needs varghele's sign-off).
- The pinned persona biases or pins routing; it is not a delegating state machine.
- **Sampling presets must survive** — the router sets them; don't let temperature defaults fall on the floor.
- Activate `expected.profile` assertions against the backend's routing event.
- **Gate:** `reroute_research_to_compute` green (turn 1 research, turn 2 plot → THIS turn routes to `run_python`, no handoff, no re-search), plus `clarify` (`weather_no_location`: solo `ask_clarification`) and `no_tool` (`define_nmr`) green.

### A4. Delete the delegation machinery — and update everything that asserts it

- Remove from the hot path: `delegate_to_persona` (survives at most as a rare explicit fallback), message rewind, delegation-driven persona persistence, `delegated`/`persona_changed` SSE choreography, the synthetic "nudge toward delegation" tool-rejection errors, the plan-approval interaction. The `persona` field survives as a routing-profile alias; frontend selector keeps working.
- **Consequential updates the master plan predates (it assumes the persona/delegation era):**
  - `EVAL-SUITE-MASTER-PLAN.md` §8's pong test asserts (a) `delegate_to_persona` → Turing fires and (b) every tool_call is in the active persona's server-side allowlist. Both mechanisms are being deleted. Build the **router-era pong test** instead: (a') the router picks the code profile for "code me pong" (or the turn routes to code tools), (b') every emitted tool_call is a real registered tool (hallucinated-tool detector — keep the spirit, drop the allowlist reference), (c/d/e) unchanged (artifact lands, no `error` SSE, non-empty final content). Keep flakiness-suite rep/variant semantics. The "start directly in Turing" variant becomes "pin = code profile".
  - Retire or rewrite the delegate-persona assertions in the `backend/scripts/` QA tools that `backend/eval/registry.py` wraps (`test_delegate_persona.py` and friends). Their replacement IS the routing eval. Update `DECISIONS.md` with a dated entry.
  - Scorecard header field "persona versions" becomes "routing-profile versions".
- **Gate:** full routing-eval regression vs. the A0 scorecard — no previously-passing item may fail. Then 1 week of real-user soak.
- Deliberate trade-off to document in code: the allowlist was a HARD boundary (research literally could not run code); per-turn routing relaxes it. If any tool needs a hard wall, implement an explicit per-tool guard — do not resurrect allowlists.

### A5. Expand the routing eval (parallel with A2–A4)

- Fan each seed item to ~15–20 paraphrases per category (mine real variants from logs with SPECTER/BGE if useful) so one brittle phrasing can't swing the score. Categories declared but not yet seeded (`abstain`, `artifact`) get items too.
- Keep `reward_basis` discipline; argument predicates stay shape-assertions, never frozen blobs.

---

## Part B — Evaluation suite build

Track letters / T-numbers per the master plan and TODO, which stay authoritative on detail. This section fixes ordering, placement, and gates.

### Placement (binding, from master plan §8 + retrieval spec §1)

```
backend/benchmarks/            # paper-grade, reproducible, versioned
  munin_bench/
    (retrievers/, benchmarks/, metrics/, pipelines/, cli.py  — per retrieval spec)
    answer_eval/               # Track B: minicheck_judge.py, validate_judge.py
    abstention/                # Track C: build_set.py, score.py, shadow_collection.py
    harness_ablation/          # Track D: arms.py, run_ablation.py, cost_accounting.py
    routing/                   # routing_eval.py + items + runner  [RECOMMENDED — see note]
    run_all.py                 # Track E entry
    compare.py                 # scorecard diff
  data/                        # gitignored; every dataset gets download_<name>.py + checksum.
                               # EXCEPTION: the abstention ground-truth JSON IS committed.
  scorecards/                  # committed small JSONs (+ .md twins)
  results/                     # gitignored; the only thing the paper consumes
backend/eval/                  # deployment-behavioral; never cited in the paper
  registry.py                  # wraps (does not move) backend/scripts/ QA tools
  scenarios/pong_test.py       # router-era version per A4
```

**Routing-eval placement note (varghele may override):** it goes in `benchmarks/` because it is versioned, deterministically scored, and produces a paper-citable routing-accuracy number for the harness section; flakiness in it is a bug, not information. The boundary contract then stays clean: pong test and friends remain in `eval/`.

### B1. Immediately, parallel to Part A (data acquisition, in lead-time order)

1. **LitQA2 source PDFs** (T1/T3) — longest lead. From day one maintain `ingest-list` vs `withhold-list`; the withhold-list IS the T3 abstention ground truth. Losing the split loses the paper's best novel benchmark.
2. **MiniCheck weights + RAGTruth corpus** (T4) — largest MiniCheck variant that fits GPU 0 alongside batch jobs (7B Bespoke, flan-T5-large fallback).
3. **`[VERIFY]` pass** on briefing anchors (AstaBench, AbstentionBench, MedAbstain, AutoResearchBench, PaperArena, HalluLens, FaithBench, CSFCube, "Know Your Limits", Asta-2×-ReAct claim) — half a day, before any enters `munin.bib`. Include the T3 novelty check: confirm nothing comparable to corpus-grounded abstention shipped 2025–26. **DONE 2026-07-27 (`CITATIONS-VERIFIED.md`): all anchors bib-ready; T3 novelty pass found KnowOrNot (2505.13545) as public prior art, so the claim is narrowed to the combination, not the concept.**
4. **LitSearch, QASPER** (T8/T10) — public, quick, P1.
5. **Stratum-3 fake references** (T3) — ~30 min of varghele's domain imagination; do not fully automate.
6. **LLM-judge budget** (T9) — varghele sets a number once.

### B2. Track A build (start week 1; harness-independent)

Per `RETRIEVAL-EVAL-SPEC.md`, phases strict, gates binding:

- **Phase 1** metrics + bootstrap + significance, with mandatory gold-case tests. Gate: pytest green, 100% metric-function coverage.
- **Phase 2** four retrievers behind the ABC. Copy `compute_citation_score` and fetch-k rules VERBATIM from `backend/retrieval/main.py`. Gate: sanity query returns hits on live `papers`.
- **Phase 3** BEIR via `ir_datasets` (SciFact first; TREC-COVID last; CSFCube optional-with-note). All eval collections under `eval_*` prefix — **never write to the production `papers` collection or the production graph**. Gate: SciFact nDCG@10 > 0.5 for SPECTER dense.
- **Phase 4** local pool: 4a extract candidates → 4b varghele curates ≥100 queries → 4c pool build → 4d two-annotator labelling via the 50-line Flask UI, conservative merge, adjudication. Gates: Cohen's kappa ≥ 0.5 (else stop and tell varghele); production retriever beats BM25 on nDCG@10, paired bootstrap p < 0.05 (else suspect the harness first).
- **Phase 5** LitQA2: retrieval track now; answer track post-freeze. Gate: ≥50 in-corpus questions or stop and tell varghele. Disclose the PaperQA2 training-exposure caveat verbatim per spec §3c.
- Evaluate both (0.8, 0.2) "live" and (0.7, 0.3) "paper" weights + the sweep; **reconcile the discrepancy with varghele before submission. Never auto-tune α/β** — the sweep characterises sensitivity; a wildly better setting is a finding to report, not a silent change.
- Reproducibility: `--seed` everywhere (default 42), `make_run_header()` on every result, versions pinned per Appendix B.

### B3. Harness-independent infra (during Part A soaks)

- T2 arms 1–2: bare-model (direct vLLM, tool instructions stripped) and vanilla-RAG (one `/search/hybrid` + stuffed prompt) wrappers. **Each arm is a plain `async def solve(question) -> answer` callable** — the InspectAI-bridge shape, built for free, bridge NOT built (master plan §7 decision).
- T4: MiniCheck judge + validation on ~100 RAGTruth items (AUROC vs span annotations; poor → larger variant or flag before building on it). MiniCheck is the PRIMARY faithfulness judge; RAGAS is a one-time cross-check only (T9). A frontier-API judge must never become the primary path — the eval methodology itself honours the privacy thesis.
- T3 tooling: `shadow_collection.py` (DOI-removed Qdrant clone), `build_set.py` (three strata: absent-paper LitQA2 + ingested positive controls; shadow-corpus clones of Phase 4 queries; unanswerable-by-construction). Scoring fully automatic: emitted-DOI check + abstention envelope; metrics incl. over-abstention rate on answerable controls, risk–coverage, confabulated-citation rate.
- T5: cost accounting (tokens, wall-clock, GPU-seconds via `sacct`) extending the spec's §3d timing.

### B4. Post-freeze (Part A complete + soaked)

- Phase 5 answer track; T7 local-pool answer-level extension (+1 annotator-hour marking checkable-factual vs open-ended).
- T3 abstention set: build from withhold-list, run all arms.
- **T2 three-arm ablation — the headline experiment:** bare / vanilla-RAG / full harness over (a) LitQA2-in-corpus, (b) local-pool QA, (c) abstention set; accuracy + Track B faithfulness + Track C metrics + T5 cost; paired bootstrap CIs.
  - **HARD GATE: harness arm beats bare model on LitQA2-in-corpus accuracy. If not: STOP, report to varghele.** Do not quietly tune past it; the briefing warns this can legitimately go either way, which is why it runs before reviewers ask.
- Carry-over gates: kappa floor, BM25 floor, LitQA2 ≥50.

### B5. Track E

- `python -m munin_bench.run_all --tag <label>` → `scorecards/<date>_<tag>.json` + `.md` twin; header = model+revision, harness git SHA, routing-profile versions, corpus snapshot stats, package versions, seed (reuse `make_run_header()`).
- `compare A.json B.json` → per-metric deltas with paired-bootstrap significance. This is the re-certification-lite loop; the paper presents it as a regression suite, never a validated certification protocol.
- `--with-reliability` folds the `backend/eval/` registry summary (PASS/FLAKY/FAIL) under a `reliability` key, separated from benchmark metrics, never cited in the paper.
- The routing eval runs as a `run_all` track from here on.
- **Runtime budget: full `run_all` < 8 h on hugin GPU 0** (overnight model-swap evaluation). A track that blows it gets `--quick` (e.g. SciFact-only BEIR).

### B6. Fill-ins and writing support (P1, quality permitting)

- T8 LitSearch (sixth BEIR-style subset; ML/NLP-domain caveat in the paper). T9 RAGAS cross-check (50-query subset, judge correlation, then retired from the loop). T10 QASPER (drop without regret if tight). T11 tool-use reliability from T2 telemetry.
- Regenerate the `.tex` Evaluation tables/figures from `results/` per master plan §9 (varghele writes prose; produce paste-ready CSV/Markdown, don't restructure the paper).
- T15 hyperpolarization pilot (20–30 self-authored items, labelled "pilot, not expert-vetted") only on explicit instruction.

---

## Week-by-week (nominal; gates control progression)

| Week | Router stream (Part A) | Eval stream (Part B) |
|---|---|---|
| 1 | A0 baseline wired + committed; A1 pin personas, soak starts | B1 data acquisition kicked off; Track A Phase 1 (metrics + tests) |
| 2 | A2 tool_search hardening; A5 paraphrase expansion starts | Phase 2 retrievers; Phase 3 SciFact first |
| 3 | A3 per-turn router; profile assertions live | Phase 3 remaining subsets; Phase 4a–c (varghele curates queries) |
| 4 | A4 delete machinery; regression vs A0; router-era pong test; soak starts | Phase 4d annotation begins (~2 wks, human-gated); T2 arms 1–2 + T5 built |
| 5 | Soak completes → **harness frozen** | Annotation + adjudication (kappa gate); T4 MiniCheck validation; T3 tooling |
| 6 | — | Phase 4e (BM25 gate); Phase 5 retrieval track + overlap check (≥50 gate) |
| 7 | — | T7 answer-level runs; T3 set built + run; Phase 5 answer track |
| 8 | — | **T2 full ablation + hard gate**; T5 plots |
| 9 | — | T6 `run_all` + `compare` + `--with-reliability`; first committed scorecard |
| 10+ | — | P1 fill-ins; `.tex` Evaluation rewrite from real numbers; optional T15 pilot |

---

## Questions to confirm with varghele at kickoff (inherited from the retrieval spec §5 + new)

1. `HybridSearchRequest` defaults in `backend/retrieval/models.py` — 0.8/0.2 or different? (API default wins over `search.html`.)
2. Chat SQLite store location for Phase 4a extraction.
3. Reusable pre-computed SPECTER embeddings for BEIR, or embed from scratch?
4. Pipelines as SLURM jobs or interactive? (Interactive default.)
5. CSFCube: skip-with-note acceptable if `ir_datasets` lacks it? (Default yes.)
6. Routing-eval placement in `benchmarks/` (recommended above) — confirm or move to `eval/`.
7. LLM-judge budget number for T9.

Defaults if no answer in a working day: per retrieval spec §5 (0.8/0.2, interactive, skip CSFCube with note, embed from scratch); note defaults in the first commit message.

---

## Hard rules — things Claude Code must NOT do

1. No answer-level paper number before the harness freeze (A4 + soak complete).
2. No allowlist retirement before the A2 gate is green.
3. Never send `profile` as a request input in the routing eval.
4. Never merge ingest-list and withhold-list; never ingest a withheld paper to improve numbers.
5. Never write to the production `papers` Qdrant collection or production Neo4j graph; eval collections live under `eval_*`.
6. Never auto-tune α/β; the sweep characterises, the paper claim stands until varghele changes it.
7. MiniCheck stays the primary judge; no frontier-API judge in the re-run loop.
8. No unverified citation enters `munin.bib`. As of 2026-07-26 every anchor is confirmed (`CITATIONS-VERIFIED.md`); re-check only anchors added after that date.
9. No InspectAI runner adoption now — async-callable arm shape only (master plan §7 decision; `as_inspect_task` stays a stub).
10. When a hard gate fails (kappa, BM25, LitQA2 overlap, harness-beats-bare): stop and report; do not tune until it passes.
11. No scope creep, no dashboards, no slick CLI; deviations from spec go in a commit + note to varghele, never silently (retrieval spec §6, §7 anti-goals apply suite-wide).

---

## Definition of done

- [ ] A0 pre-migration routing scorecard committed
- [ ] A1 pinning live, 1-week soak clean
- [ ] A2 schema under prefill cliff all modes; tool_search acceptance items green
- [ ] A3 per-turn router live; profile assertions green; sampling presets preserved
- [ ] A4 delegation machinery deleted; regression vs A0 green; router-era pong test + registry updated; DECISIONS.md entry; 1-week soak clean
- [ ] A5 paraphrase-density routing set, all 14 categories seeded
- [ ] B1 LitQA2 PDFs with ingest/withhold split; [VERIFY] pass done (incl. T3 novelty check)
- [ ] Track A Phases 1–5 gates passed (pytest, SciFact >0.5, kappa ≥0.5, BM25 p<0.05, LitQA2 ≥50)
- [ ] T4 MiniCheck validated on RAGTruth
- [ ] T3 abstention set built, committed (ground-truth JSON), run
- [ ] T2 ablation run on frozen harness; hard gate passed
- [ ] T6 `run_all` < 8 h; first scorecard committed incl. routing track + reliability key
- [ ] α/β live-vs-paper discrepancy reconciled with varghele
- [ ] Evaluation tables/figures regenerated from `results/`
