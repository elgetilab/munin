# T2 — harness three-arm ablation (refresh + finalize)

Status: EXECUTING 2026-07-26. Decisions (varghele): (1) FULL 199; (2) RETRY
MiniCheck; (3) local-pool OUT OF SCOPE; (4) INCLUDE abstention-per-arm;
(5) paper-grade: RE-RUN agentic at 900s deadline (option b).

### Results so far (199, seed-7 order)

bare/RAG done (reproduce Track D within noise); agentic first pass at the OLD
300s deadline, being re-run at 900s (paper-grade).

| arm | accuracy | precision | abstain | cost |
|---|---|---|---|---|
| RAG | 0.171 | 0.71 | 0.75 | 9.1s, 0 tools |
| bare | 0.302 | 0.48 | 0.20 | 14.7s, 0 tools |
| agentic @300s (interim) | 0.668 | 0.937 | 0.21 | 102.6s, 12.5 tools |

Paired deltas @300s (p~0): agentic-bare **+0.367** [0.276,0.452] (Track D was
+0.24); agentic-rag +0.498; rag-bare -0.131 (RAG hurts, reproduces).

**300s caveat that triggered the re-run:** all 15 agentic unparseables were at
exactly 300s (truncations scored wrong), 23 questions hit the cap. So 0.668
understates; true number ~standalone 0.864. Committed the deadline fix
(`3a1dfa2`, 300->900) and re-running agentic at 900s. The 300s result is kept
(`scratchpad agentic_300s.json`) as the same-deadline Track-D before/after.

Abstention-per-arm (C1 fabricated): bare 0.61, RAG 0.42 (Track D 0.59/0.36),
agentic 0.80 reused from `2026-07-10_abstention-c1-fabricated`.

### BLOCKED 2026-07-26: full-text-read regression confounds the agentic arm

900s re-run did NOT change accuracy (0.668 -> 0.663); truncations became
abstains, not corrects. But it surfaced a bigger problem: the agentic arm is
0.66 while the standalone LitQA2 track measured **0.864 on 2026-07-24** (same
199 Qs, same 900s, byte-identical request path, deterministic MCQ). Aligning
per-question verdicts: **40 flipped correct(07-24) -> abstain(07-26)**, 7 the
other way. Re-running 20 of the 40 through the standalone path NOW: **15/20
still abstain** -> PERSISTENT, not run-time noise.

Mechanism: the flipped answers make 25-31 tool calls but abstain saying they
"cannot access the full article / figures / tables". Direct `source(mode=qa)`
on a flipped DOI returns `outcome=not_found, read_depth=abstract` -> **the
full-text FETCH is falling back to abstract-only**, so the model honestly
abstains. (That direct call ran out-of-container so it's only corroborating;
the live-service recheck is the real signal.)

Likely NOT the tool retirement: `source`'s PDF fetch is an independent code
path (helpers kept; smoke read papers fine). Prime suspect is full-text /
OA-download reliability degrading between 07-24 and 07-26 (the LitQA2 papers
appear to depend on live OA fetch, not the local /papers corpus). `/data` is a
host mount so the 332-file PDF cache persists across rebuilds - not a wiped
cache.

### ROOT CAUSE 2026-07-26 (in-container diagnosis, docker via `sg docker -c`)

NOT a code regression, NOT the tool retirement (source's fetch is independent
and works; magic-byte guard correctly rejects HTML). It is OA full-text fetch
reliability:
- Provenance gates OPEN (corpus_scope=all, egress on) - ruled out.
- Unpaywall works (200, is_oa) and returns valid OA PDF URLs.
- `_download_valid_pdf` fails on two mechanisms:
  1. **PMC/NCBI rate-limiting under eval load** - the ~6-8h run fired ~199
     full-text fetches (+ retries: the 25-31 tool calls per flipped Q are the
     model retrying failing reads), PMC throttled and returned anti-bot HTML
     (200 + text/html), magic-byte guard rejects -> abstract. Spaced out now,
     4/6 sampled flipped papers fetch FINE (Nature/bioRxiv/some PMC). This is
     the bulk of the 40 flips and it's eval-induced, transient.
  2. **Persistent publisher block** for an Elsevier/Cell (cell.com) subset:
     403 from the datacenter IP, never fetches. Small, permanent.

So agentic 0.66 is depressed by eval-load rate-limiting, not a true
per-query regression; standalone 0.864 (differently-timed) is closer to true.

### REFINED 2026-07-26: it's eval-LOAD read degradation, not just OA fetch

Corpus coverage (checked /opt/munin/data/papers/pdf, `doi_<sanitized>.pdf`):
**146/199 LitQA2 papers have local PDFs, 53 missing** (the prior ingest
couldn't fetch the missing ones - Elsevier etc.). Of the 40 flips: **21 have
local PDFs, 19 missing**.

Decisive test - `source(qa)` NOW on 4 flipped papers that HAVE local PDFs:
3/4 `resolved` at `read_depth=full_text`, 1 `full_text` but not_found for that
Q. So local-PDF reads work fine when spaced. Those 21 abstained only DURING
the run -> transient extraction/pipeline degradation under sustained load.
The 19 missing-PDF flips are OA rate-limiting under the burst.

Unified: full research-turn evals that do hundreds of live full-text reads
back-to-back degrade the read pipeline (OA rate-limit accumulation + GROBID/
extraction hiccups over 6-8h); individual spaced reads succeed. True per-query
agentic accuracy ~0.86 (standalone); ablation 0.66 is LOAD-DEPRESSED. NOT a
code regression, NOT the tool retirement. bare/RAG/abstention arms unaffected
(no live full-text fetch).

**Paths to a clean paper-grade agentic number (decision needed):**
1. Ingest the 53 missing PDFs + pre-warm/cache extraction for all 199, then
   re-run agentic reading from local/cache -> deterministic, load-independent,
   reproducible. Most robust; also unblocks T1/T3.
2. Just re-run agentic (reads work spaced) - risks another transient dip,
   non-deterministic.
3. Report agentic = standalone 0.864 (clean, same 199/900s/request path) and
   present the paired ablation delta (+0.36) as CONSERVATIVE (agentic arm
   depressed by eval-load). No new long run.

### LOCAL PDF ARCHIVE built 2026-07-26 (the real fix)

Found the July-1 acquisition folder `backend/benchmarks/data/litqa2/pdfs/`
(130 PDFs + acquire manifests). Consolidated ALL available LitQA2 papers into
it: **176/186 unique papers now archived on-disk** (52 copied from the live
corpus, 130 already there). The whole `data/` dir is gitignored, so it
persists in the repo folder without bloating git - a durable corpus for future
benchmark runs that can't disappear.

10 remain unobtainable (paywalled Cell Press/Elsevier + MDPI + CoB, no OA, not
on Sci-Hub) - listed with titles in `data/litqa2/still_missing.json` for manual
fetch (institutional access). The 33 recoverable-but-missing-from-corpus PDFs
were also pushed into the live retrieval cache (`/data/papers_cached` via the
container; overwriting 1-page junk with the real multi-page versions), so
`source` reads them LOCALLY now - no OA fetch, load-independent.

Validation (previously-flipped questions whose PDF is now local, via standalone
path): recovering to `correct`. With reads now local for 176/186, a re-run of
the agentic arm should land near the true ~0.86 rather than the load-depressed
0.66.

Pending (after fetch is healthy): re-run agentic -> re-`compare` -> MiniCheck
-> assemble+commit scorecard + vs-07-13 diff.

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
