# Scorecards behind the headline claims

94 committed scorecard JSONs, copied verbatim from
`backend/benchmarks/scorecards/`. These are the raw data behind every number in
`05-RESULTS.md`, so figures and paired tests can be regenerated without the
repository.

The full set is 138 JSON plus 90 Markdown twins; the remainder are mostly the
routing-tuning runs from the A0 through A5 migration, which no paper section
cites.

**Commit hashes are those of the published history.** The repository's
history was rewritten before release to remove personal data and one internal
document, which changes every commit hash. Each `git_sha` and
`rescored_at_git_sha` here, and every git `<hash>` cited in the paper docs,
was mapped in 2026-10 to the same commit in the published history, so it
resolves in a clone; `backend/scripts/ci/check_commit_refs.py` keeps it that
way.

**Answer text is withheld.** The four `*_answer-*-900s.json` files used to
carry `text_tail`, the last 600 characters of each model answer. Those tails
quoted the source papers and gave away LitQA2 answers, so the field was
removed for the public release (2026-10). No metric reads it; every verdict,
letter and per-query array is unchanged. (The C1 files keep a short `answer_tail` per
item: the model's own text about invented papers, which quotes no source and
no benchmark item.)

**Backbone is now stamped in every ablation-family file** (`backbone`,
`backbone_checkpoint`, and for Track D `serving`, `egress` per arm,
`arm_matching` and `sampling`), backfilled 2026-09-15 from RESULTS.md's dated
sections and marked as such by `provenance_note`; the runners never wrote
them. Every file dated 2026-08-26 to 2026-09-15 is on Qwen3.8-27B
(production); every earlier file is on the retired Qwen3.6-35B-A3B; every
file dated 2026-09-16 with `gpt-oss-20b` in its name is the third backbone,
run as an eval-only instance beside production (`backbone` and `serving` are
stamped by the driver); every file dated 2026-09-17/18 with `qwen3.6-35b-a3b`
in its name is the retired Qwen3.6 checkpoint brought back the same way and
run through the full suite on the current harness, and the two
`2026-09-17_*egressoff-gpt-oss-20b*` files are gpt-oss-20b's corpus-only arm
from the same chain. Every file dated 2026-11-13/14 with `sage-a1.5` in its
name is the fourth backbone, Sage-A1.5 (an agentic fine-tune of Qwen3.6-35B-A3B,
production since 2026-11-12), run as an eval-only instance with a thinking-token
budget, the card sampling on the bare/RAG arms and concurrency 2. See cautions
8, 9, 10 and 12.

## Schema

```
{
  "meta":  {tag, generated_at, git_sha, encoder, seed, packages,
            model, model_max_len, corpus snapshot, ...},
  "tasks": {"<task>": {"<system>": {
              "metrics":   {"<metric>": {mean, ci_low, ci_high}},
              "per_query": {"<qid>": {"<metric>": value}}}}}
}
```

`per_query` is what makes a **paired** bootstrap possible between two runs over
the same questions. It is the reason these files are large.

That header is the `run_all` scorecard shape (`2026-07-03_*`, `2026-07-06_*`,
`2026-07-24_*`, `2026-07-28_*`). The **ablation family** (`*_harness-ablation*`,
`*_risk-coverage*`, `*_toolreliability*`, the C1/C2 files) uses a flatter
shape, `{track, n_paired, git_sha, date, per_arm, deltas}` or the equivalent,
with no `meta` block; the model, serving profile, egress and arm-matching
facts live in top-level fields backfilled on 2026-09-15 (see
`provenance_note` in each file).

## Index

| File | Backs | Section |
|---|---|---|
| `2026-08-26_harness-ablation.json` | **Headline** three-arm ablation, n=199 paired, Qwen3.8-27B | R1 |
| `2026-07-27_harness-ablation.json` | The same three arms on Qwen3.6-35B-A3B (second backbone; bare arm at 4,096 tokens) | R1 |
| `2026-09-15_harness-ablation-agentic-egressoff.json` | Agentic arm at `egress=off`, Qwen3.8; bare/rag copied from 08-26 for the paired deltas; carries the full→off verdict transitions. The only `off` agentic arm. | R1 |
| `2026-09-15_toolreliability-qwen38-egressoff_toolreliability.json` | T11 over the `egress=off` arm; degraded web/S2 tiers are the guard, not outages | R1, R6 |
| `2026-09-16_harness-ablation-faithfulness-qwen38-recapture.json` | **Headline** per-arm paired faithfulness, RAG vs agentic, Qwen3.8, agentic arm re-captured with the complete retrieval-tool set (n=199 both arms): agentic 0.540 vs RAG 0.282, +0.258 p<0.001 | R2 |
| `2026-08-26_harness-ablation-faithfulness.json` | The superseded per-arm faithfulness on Qwen3.8 (agentic n=163, contexts missing `search`/`source`; the "null"). Kept to show what the capture gap did | R2 |
| `2026-09-16_harness-ablation-faithfulness-gpt-oss-20b-recapture.json` | Per-arm faithfulness on gpt-oss-20b with the complete tool set: agentic 0.392 (n=159), paired vs RAG +0.253 p=0.056 on n=21 | R2, R7 |
| `2026-09-16_harness-ablation-qwen38-27b-recapture.json`, `..._gpt-oss-20b-recapture.json` | Accuracy of the recaptured agentic arms against the copied RAG arms (Qwen3.8 0.869, gpt-oss 0.583) | R1 |
| `2026-09-16_toolreliability-qwen38-recapture_toolreliability.json`, `..._gpt-oss-20b-recapture_toolreliability.json` | T11 over the recaptured arms (Qwen3.8 4.82 calls/q on the newer search ladder) | R6 |
| `2026-07-27_harness-ablation-faithfulness.json` | Per-arm paired faithfulness on Qwen3.6 (per-question values included) | R2 |
| `2026-07-08_faithfulness-judge-ragtruth.json` | MiniCheck judge validation on RAGTruth | R2 |
| `2026-09-14_abstention-c1-fabricated.json` | C1, 100 fabricated papers, **Qwen3.8**, current harness; `egress` and `harness_note` backfilled | R3 |
| `2026-07-27_abstention-c1-fabricated.json` | C1, 100 fabricated papers, Qwen3.6 | R3 |
| `2026-09-15_abstention-c2-shadow.json` | **Headline** C2b paired shadow corpus at `egress=off`, Qwen3.8, both `papers_bge` and `papers_chunks` shadowed; CIs and the question-paired delta against the 2026-07-27 run; `harness_note` backfilled | R3 |
| `2026-07-27_abstention-c2-shadow.json` | C2b paired shadow corpus at `egress=off`, Qwen3.6, with CIs and the question-paired delta against the 2026-07-10 run | R3 |
| `2026-07-10_abstention-c2-shadow.json` | The earlier C2b pair (old flat-loop harness), rescored with the same CIs so the two are comparable | R3 |
| `2026-07-27_abstention-c2-shadow-egressfull.json` | The same pair at `egress=full`. **A different experiment.** Do not compare across the two. | R3 |
| `2026-09-15_risk-coverage.json` | **Headline** six risk-coverage operating points on Qwen3.8 (ablation 08-26, C1 09-14, C2b 09-15), with `mixed_generations` / `mixed_egress` provenance markers | R3 |
| `2026-07-27_risk-coverage.json` | The same six points on Qwen3.6 | R3 |
| `2026-07-28_litsearch.json` | LitSearch, post-fix, canonical | R4.2 |
| `2026-07-28_litsearch-prefix.json` | LitSearch, pre-fix. The before-half of the citation-re-rank A/B. | R4.2, F5 |
| `2026-07-03_baseline-specter-v1.json` | LitQA2 retrieval, SPECTER-v1 baseline | R4.4 |
| `2026-07-03_bge-large.json` | LitQA2 retrieval, BGE-large full-corpus | R4.4 |
| `2026-07-06_answer-specter-v1-v2.json` | End-to-end answer, SPECTER arm, fixed parser | R5 |
| `2026-07-06_answer-bge-large-v2.json` | End-to-end answer, BGE arm, fixed parser | R5 |
| `2026-07-24_answer-full-900s.json` | End-to-end answer, agent architecture, 900 s, Qwen3.6 | R5 |
| `2026-09-14_answer-qwen38-900s.json` | End-to-end answer, standalone track, 900 s, **Qwen3.8**; per-query arrays pair with both the 07-24 file and the 08-26 ablation arm | R5 |
| `2026-08-26_toolreliability-qwen38_toolreliability.json` | **Headline** tool-use telemetry, Qwen3.8 | R6 |
| `2026-09-16_gpt-oss-20b.json` | Third backbone, **gpt-oss-20b**: `run_all` header for the Track D re-run (n=199) with the driver's provenance (checkpoint, Marlin kernel, decode/prefill tok/s, KV pool, thinking mode, sampling, egress) and the certification verdict | R1, R7 |
| `2026-09-16_harness-ablation-gpt-oss-20b.json` | Three-arm ablation on gpt-oss-20b, n=199 paired, with `unparseable_reasons` and `empty_kinds` per arm (bare 25 and RAG 162 `no_final_message`) | R1, R7 |
| `2026-09-16_gpt-oss-20b-prerepair.json` | The same Track D run BEFORE the tool-name repair and the bold-letter scorer fix (agentic 0.467). The parser-cost comparison point; not a headline | R7 |
| `2026-09-16_answer-gpt-oss-20b-900s.json` | Standalone answer track on gpt-oss-20b, 900 s, per-query arrays pair with the 07-24 and 09-14 files | R5, R7 |
| `2026-09-16_abstention-c1-fabricated-gpt-oss-20b.json` | C1 on gpt-oss-20b: 72 correct abstentions, 24 ambiguous, 4 possible confabulations, 0 confabulated local cites; `egress` stamped by the runner | R3, R7 |
| `2026-09-16_abstention-c2-shadow-gpt-oss-20b.json` | C2b paired shadow pair on gpt-oss-20b at `egress=off`, with the question-paired delta against the 09-15 Qwen3.8 pair | R3, R7 |
| `2026-09-16_risk-coverage-gpt-oss-20b.json` | Six operating points on gpt-oss-20b (ablation, C1, C2b of 09-16) | R3, R7 |
| `2026-09-16_toolreliability-gpt-oss-20b_toolreliability.json` | T11 on gpt-oss-20b after the tool-name repair: 8.75 calls/q, error 0.049, recovery 1.000 | R6, R7 |
| `2026-09-16_routing-gpt-oss-20b-2026-09-16.json` | Routing anchor tier on gpt-oss-20b: pass rate 0.647 [0.45, 0.84]. The deploy gate; not a paper number | R7 |
| `2026-09-16_harness-ablation-faithfulness-gpt-oss-20b.json` | The 13-row placeholder from before the recapture; superseded by `..._gpt-oss-20b-recapture.json`, kept for the record | R7 |
| `2026-09-17_qwen3.6-35b-a3b.json` | **Retired backbone re-run**, Qwen3.6-35B-A3B on an eval instance, current harness: `run_all` header with the driver's provenance (AWQ-4bit checkpoint, `qwen3_xml` parser, thinking on, Qwen sampling, decode/prefill tok/s, KV pool, egress) and the certification verdict (FAIL on the `pong` probe only: empty final message) | R1, R7 |
| `2026-09-17_harness-ablation-qwen3.6-35b-a3b.json` | Three-arm ablation on Qwen3.6, n=199 paired, current harness, bare at 16,384 tokens: agentic 0.869, agentic − bare +0.533 (07-27: +0.538). Question-paired against Qwen3.8's 08-26 arm: −0.005, p=0.93 | R1, R7 |
| `2026-09-17_harness-ablation-agentic-egressoff-qwen3.6-35b-a3b.json` | Agentic arm at `egress=off` on Qwen3.6 (0.704), same day and commit as the `full` arm, so full − off (+0.166) is egress alone; bare/rag copied for the paired deltas | R1 |
| `2026-09-17_harness-ablation-agentic-egressoff-gpt-oss-20b.json` | Agentic arm at `egress=off` on gpt-oss-20b (0.482): full − off +0.080 p=0.02, and agentic(off) − bare +0.075 [−0.010, +0.161] p=0.10, the corpus-only harness effect not distinguishable from zero on this backbone | R1, R7 |
| `2026-09-17_toolreliability-qwen3.6-35b-a3b-egressoff_toolreliability.json`, `..._gpt-oss-20b-egressoff_toolreliability.json` | T11 over the two `off` arms (Qwen3.6 10.5 calls/q recovery 1.000; gpt-oss 7.4 calls/q recovery 0.965, the first sub-1.000 recovery, with 5 unrepaired tool names) | R1, R6 |
| `2026-09-17_harness-ablation-faithfulness-qwen3.6-35b-a3b.json` | Per-arm paired faithfulness on Qwen3.6 with complete contexts (n=199 agentic, 198 RAG): agentic 0.627 vs RAG 0.290, +0.336 p<0.001. Supersedes the 07-27 file for claim 2 on this backbone; paired against the Qwen3.8 recapture +0.088 p<0.001 | R2, R7 |
| `2026-09-17_answer-qwen3.6-35b-a3b-900s.json` | Standalone answer track on Qwen3.6, current harness, 900 s: 0.874, 174/9/16/0; per-query arrays pair with the 07-24, 09-14 and 09-16 files | R5, R7 |
| `2026-09-17_abstention-c1-fabricated-qwen3.6-35b-a3b.json` | C1 on Qwen3.6, current harness: 98 correct abstentions, 2 possible confabulations, 0 confabulated local cites, 10.3 calls/item | R3, R7 |
| `2026-09-17_abstention-c2-shadow-qwen3.6-35b-a3b.json` | C2b paired shadow pair on Qwen3.6 at `egress=off`, current harness with the chunk shadow: correct abstention 0.875 on 32 answerable (the largest base of any pair), question-paired against the 09-15 Qwen3.8 pair −0.014 p=0.85 | R3, R7 |
| `2026-09-17_risk-coverage-qwen3.6-35b-a3b.json` | Six operating points on Qwen3.6, all from one run on one harness commit (ablation, C1, C2b of 09-17) | R3, R7 |
| `2026-09-17_toolreliability-qwen3.6-35b-a3b_toolreliability.json` | T11 on Qwen3.6 at `egress=full`, current harness: 7.33 calls/q, error 0.065, recovery 1.000, web-heavy mix like Qwen3.8's | R6, R7 |
| `2026-09-18_routing-qwen3.6-35b-a3b-2026-09-17.json` | Routing anchor tier on Qwen3.6: pass rate 0.816 [0.65, 0.98]. The deploy gate; not a paper number | R7 |
| `2026-11-13_sage-a1.5.json` | **Fourth backbone, Sage-A1.5** (agentic fine-tune of Qwen3.6-35B-A3B, AWQ-INT4, vLLM 0.33) on an eval instance: `run_all` header with the driver's provenance, including `thinking_token_budget=8192`, `reasoning_end_str=set`, `direct_arm_sampling=profile` and `concurrency=2`, and the certification verdict (PASS, 0 fail, 4 skipped) | R1, R7 |
| `2026-11-13_harness-ablation-sage-a1.5.json` | Three-arm ablation on Sage-A1.5, n=199 paired: bare 0.362, RAG 0.171, agentic 0.905; agentic − RAG +0.734 p<0.001. Bare `empty_kinds`: 5 truncated at 16K (80 before the thinking budget, see caution 12) | R1, R7 |
| `2026-11-13_harness-ablation-agentic-egressoff-sage-a1.5.json` | Agentic arm at `egress=off` on Sage-A1.5 (0.749), same run as the `full` arm; bare/RAG copied for the paired deltas | R1 |
| `2026-11-13_harness-ablation-faithfulness-sage-a1.5.json` | Per-arm paired faithfulness on Sage-A1.5: agentic 0.628 (n=198) vs RAG 0.278 (n=197), +0.351 [0.284, 0.417] p<0.001 (n=196) | R2, R7 |
| `2026-11-13_answer-sage-a1.5-900s.json` | Standalone answer track on Sage-A1.5, 900 s: 0.925, precision 0.968, 184/9/6/0; per-query arrays pair with the 09-17 Qwen3.6 file | R5, R7 |
| `2026-11-13_abstention-c1-fabricated-sage-a1.5.json` | C1 on Sage-A1.5: 96 correct abstentions, 3 ambiguous, 1 possible confabulation, 0 confabulated local cites | R3, R7 |
| `2026-11-13_abstention-c2-shadow-sage-a1.5.json` | C2b paired shadow pair on Sage-A1.5 at `egress=off`: correct abstention 0.774 [0.61, 0.90] on 31 answerable; question-paired against the 09-15 Qwen3.8 pair −0.115 p=0.137 | R3, R7 |
| `2026-11-13_risk-coverage-sage-a1.5.json` | Six operating points on Sage-A1.5, all from one run on one harness commit (ablation, C1, C2b of 11-13) | R3, R7 |
| `2026-11-13_toolreliability-sage-a1.5_toolreliability.json`, `..._sage-a1.5-egressoff_toolreliability.json` | T11 on Sage-A1.5: `full` 9.29 calls/q, error 0.052, recovery 1.000; `off` 11.18 calls/q, error 0.009, recovery 1.000 | R6, R7 |
| `2026-11-14_routing-sage-a1.5-2026-11-13.json` | Routing anchor tier on Sage-A1.5: pass rate 0.787 [0.64, 0.93]. The deploy gate; not a paper number | R7 |
| `2026-07-27_toolreliability-clean.json` | Tool-use telemetry over the Qwen3.6 clean run. `web_fetch` error rate not comparable to 08-26 (failure definition changed between the runs) | R6 |
| `2026-11-14_smoke.json` | Lab-analysis Phase 1 smoke run: 11 cases, 1 rep, Sage-A1.5, ad hoc arm. Proves the harness end to end; **not a result**, no section cites it | none |
| `2026-11-16_phase2-baseline.json` | Lab-analysis Phase 2 baseline, ad hoc arm (no `labkit`), Sage-A1.5, 95 cases x 3 reps (S1 76, S2 10, S3 2, S5 2, S6 5): 243/285 pass, mean check score 0.935 (S1 203/228 0.962, S2 23/30 0.808, S3 2/6 0.935, S5 4/6 0.833, S6 11/15 0.817). Runs 1-243 on production at concurrency 1, the rest on the `eval` instance (GPU1, same weights and profile) at concurrency 4 then 2 (`resumed: true`); two `s6_relaxation` runs lost to the chats.db lock bug were redone (`CHATDB-LOCK-PLAN.md`). The paired baseline for the Phase 4 `labkit` arm | none yet |
| `2026-11-16_phase4-labkit.json` | Lab-analysis Phase 4, `labkit` arm (labkit 0.1 at `6900e5f` in the sandbox, persona and `run_python` pointers), same 95 cases x 3 reps as the Phase 2 baseline, `eval` instance at concurrency 4: 249/285 pass, mean 0.939. Paired vs baseline: S1 synthetic 123->126/147, S1 NIST 80->78/81 (Lanczos2/3), S2 23->25/30, S3 2->4/6, S5 4->5/6, S6 11->11/15. Only 16/285 runs imported `labkit` (15 passed), so the delta is mostly run-to-run noise (S1 synthetic: 15 lost, 18 gained) and does NOT measure the helper; see `LAB-ANALYSIS-PLAN.md` Phase 4 | none yet |
| `2026-11-16_phase4-fresh.json` | Lab-analysis Phase 4 tuning check: 27 fresh S1 cases (seeds 6-8, `fixtures/lab_fresh`, generated after the labkit freeze) x 3 reps, `labkit` arm: 69/81 pass (0.852), mean 0.944, vs 114/135 (0.844) for the same nine families in the baseline. 6/81 runs imported `labkit` | none yet |
| `2026-11-17_phase4b-labkit.json` | Lab-analysis Phase 4 run 2, `labkit` arm plus adoption nudges (`LAB-ADOPTION-PLAN.md`: LAB DATA rule at the top of the code persona, labkit first in the `run_python` description, a once-per-conversation hint on hand-rolled fits, reader named in upload notes), same 95 x 3 as the baseline, `eval` instance at concurrency 4: 260/285 pass (baseline 243, run 1 249), mean 0.941. labkit imported in 155/285 runs. Paired vs baseline: 31 runs fail->pass, 14 pass->fail, exact McNemar p = 0.016. S1 synthetic 114->127/135 (decay1 the main gain), S1 trap 9->11/12, S1 NIST 80->77/81 (Lanczos, no labkit), S2 23->26/30, S3 2->4/6, S5 4->5/6, S6 11->10/15 (labkit never used in S6). Measures the system change (helper plus nudges), not labkit alone | none yet |
| `2026-11-17_phase4b-fresh.json` | Phase 4 run 2 tuning check on the 27 fresh S1 cases (seeds 6-8) x 3: 71/81 (0.877), run 1 69/81, same-family baseline rate 114/135 (0.844); labkit in 70/81 runs. decay1 9/9 (run 1 4/9); standard curve 6/9 (interpolate results saved as [value, CI] lists) | none yet |
| `2026-11-17_phase4c-labkit.json` | Lab-analysis Phase 4 run 3, `labkit` 0.1.2 (`x_scale` guard, 5PL LogEC50 sign fix, `csp` order guard) plus round-3 pointers (`LABKIT-011-PLAN.md`: NMR, titration and standard-curve hints, `labkit.nmr` functions in the persona), same 95 x 3, `eval` instance at concurrency 4: 267/285 pass (baseline 243, run 2 260), mean 0.952. labkit imported in 180/285 runs. Paired vs baseline: 36 fail->pass, 12 pass->fail, exact McNemar p = 0.0007; vs run 2 20/13, p = 0.30. S1 synthetic 132/135, S1 trap 12/12, S1 NIST 78/81, S2 23/30, S3 6/6, S5 6/6, S6 10/15. Measures the system change (helper plus nudges), not labkit alone | none yet |
| `2026-11-17_phase4c-fresh.json` | Phase 4 run 3 on the 27 fresh S1 cases (seeds 6-8) x 3: 79/81 (0.979), run 2 71/81, same-family baseline rate 114/135 (0.844); labkit in 77/81 runs. Failures: one standard curve saved as [value, CI], one competitive with no results.json | none yet |
| `2026-11-18_phase4d-labkit.json` | Lab-analysis Phase 4 run 4, `labkit` 0.1.4 (round 4, `LABKIT-013-PLAN.md`: `nmr.read_sparky`, `noe_buildup`, `read_integrals`, robust `peaks()`, field-name errors, `ppms` alias; domain hints that fire even with labkit imported; JSON outline after each run; rerun guard quotes the error) and **results request v2** (`--results-suffix v2`: a number per key, an object per item, intervals only for interval keys; earlier lab scorecards used v1, which on the same system cost 10/90 runs, p = 0.031), same 95 x 3, `eval` instance at concurrency 4: 280/285 pass (baseline 243, run 3 267), mean 0.990. labkit imported in 216/285 runs. Paired vs baseline: 40 fail->pass, 3 pass->fail, exact McNemar p = 3e-9; vs run 3 16/3, p = 0.0044 (non-NIST 200/204, vs run 3 14/3, p = 0.013). S1 synthetic 135/135, S1 trap 12/12, S1 NIST 80/81, S2 29/30, S3 6/6, S5 3/6, S6 15/15. Failures: S5 ANOVA 0/3 on `reproduces` (unseeded plot jitter), one ENSO local minimum, one JASCO HT cutoff interpolated between points. Measures the system change including the results-request wording, not labkit alone | none yet |
| `2026-11-18_phase4d-fresh.json` | Phase 4 run 4 on the 27 fresh S1 cases (seeds 6-8) x 3, results request v2: 80/81 (0.988), run 3 79/81; labkit in 78/81 runs. Failure: one competitive run exhausted the 20-turn budget reading labkit's source | none yet |
| `2026-11-19_s8-pre-epr.json` | S8 EPR suite baseline (`EPR-PLAN.md`), before `labkit.epr`: labkit 0.1.5, 21 synthetic cases (11 CW-EPR, 10 DEER; Bruker BES3T, ESP, ASCII) x 3, results request v2, `eval` instance: 17/63 (0.270), mean check score 0.335. CW 17/33 (DPPH calibration, Kivelson, overmodulation 3/3; spin count, broad window, ESP 0/3), DEER 0/30. Spin-count and broad-window runs are on the corrected (lower-noise) files | none yet |
| `2026-11-19_s8-labkit.json` | S8 EPR suite with `labkit` 0.2.0 (`labkit.epr`, EPR hints, persona naming it; `EPR-PLAN.md`), 21 x 3, results request v2, `eval` instance: 49/63 (0.778), baseline 17/63. Paired vs baseline: 36 fail->pass, 4 pass->fail, exact McNemar p = 2e-7. CW 25/33 (baseline 17), DEER 24/30 (baseline 0). `labkit.epr` called in 50/63 runs. Failures: Kivelson 0/3 (baseline 3/3; no run called labkit.epr, hand-smoothed peak heights), broad window 0/3, Gaussian FWHM 1/3 (plain-text files, no EPR hint), DEER concentration, labeling and membrane 1/3 each (hand fits despite the DEER hint; `fit_deer` with the default hom3d background on a membrane sample) | none yet |
| `2026-11-19_s8-labkit-fresh.json` | S8 tuning check on the 21 fresh cases (`s8.VARIANT = 1`, generated after labkit 0.2.0 was frozen) x 3, same system: 47/63 (0.746); 47/57 without the two cases model-free fitting cannot pass (short trace and membrane; DeerLab's model-free fit gives the same values as labkit). CW 25/33, DEER 22/30; `labkit.epr` in 52/63 runs. Kivelson 3/3 here; broad window and Gaussian FWHM 0/3 | none yet |
| `2026-11-20_s8-r2-targeted.json` | S8 round 2 (`LABKIT-015-PLAN.md`), `labkit` 0.2.1 (membrane background warning with `dim=2`, text-spectrum upload note, EPR lineshape and power hints, DEER hint example), targeted: the 7 families round 1 failed most (Kivelson, Gaussian FWHM, broad window, power saturation, DEER labeling, concentration, membrane) x 3 on the original set: 17/21 (round 1 on the same runs: 10/21). Paired vs `s8-labkit`: 12 fail->pass, 2 pass->fail, exact McNemar p = 0.013. Kivelson, broad window, membrane 3/3 (round 1: 0, 0, 1); Gaussian FWHM 0/3 (hand work, tool budget) | none yet |
| `2026-11-20_s8-r2-targeted-fresh.json` | Same 7 families on the fresh `_f2` set (`s8.VARIANT = 2`, generated after `labkit` 0.2.1 was frozen) x 3: 14/21. Round 1's fresh `_f1` on the same families: 9/21 (other parameters, not paired). Membrane 3/3, all with `dim=2` (DeerLab with a free stretched background fails this case); broad window, Kivelson 3/3; Gaussian FWHM 0/3 | none yet |
| `2026-11-21_lk16-targeted.json` | Turn-budget round (`LABKIT-016-PLAN.md`): numeric text uploads as data files, the newest tool result never blanked (the `print('hello')` runs were blind), a save round before the wrap-up; retrieval only. Gaussian FWHM 3/3 (`s8-r2-targeted` 0/3) and the four cases that hit the 20-call limit most (`s2_bruker_1d`, both HSQC titrations, NOESY buildup) 11/12 vs `r4-suffix-v2` 10/12 (2/1); mean calls 16.2 vs 20.0, runs at 20+ 3 vs 8 | none yet |
| `2026-11-21_lk16-targeted-fresh.json` | Same round, Gaussian FWHM `_f1` and `_f2`: 6/6 (`s8-labkit-fresh` `_f1` 0/3, `s8-r2-targeted-fresh` `_f2` 0/3) | none yet |
| `2026-11-21_lk16-control.json` | Same round, control: the round-5 cases (competitive s1-s5, JASCO CD, ANOVA, fit) x 3: 22/24 (`r5b-targeted` 24/24). Both failures are a `reproduces` check: a run that wrote an output file and then raised is left out of `analysis.py`, so the bundle cannot remake that file (an existing `bundle.py` defect) | none yet |
| `2026-11-21_lk16-control-fresh.json` | Same round, control fresh (competitive s6-s8) x 3: 8/9 (`r5b-targeted-fresh` 9/9). The failure used all 20 calls; its save round ran more checks instead of writing results.json | none yet |
| `2026-11-21_lk17-control.json` | Bundle and save-round round (`LABKIT-017-PLAN.md`): `analysis.py` also replays a failed run that wrote files (reports its error and goes on), firmer save-round text, a code turn's wrap-up states its values. Same cases as `lk16-control` x 3: 24/24 (`lk16-control` 22/24, `r5b-targeted` 24/24). Two bundles (`s5_anova#3`, `s5_fit#3`) have an output that only a failed run wrote and reproduce through the replay, the case both `lk16-control` failures hit; mean calls 5.9, no run near the limit, so no save round fired | none yet |
| `2026-11-21_lk17-control-fresh.json` | Same round, competitive s6-s8 fresh x 3: 9/9 (`lk16-control-fresh` 8/9, `r5b-targeted-fresh` 9/9) | none yet |
| `2026-11-22_full-lk17.json` | Full-suite regression after LABKIT-015 to 017 (labkit 0.2.1, `LABKIT-017-PLAN.md`), all 116 original cases x 3: 338/348. S1-S7 280/285, paired vs `phase4d-labkit` 280/285 (5/5, p = 1): the five are `s3_bars` x2 and `s6_noesy_buildup` x1 (both have swung before) and `s1_nist_misra1d`, `s1_nist_thurber`, whose last run retyped the data from printed output and mistyped it (a habit seen in 4-11 bundles per full run since `phase4-labkit`). S8 58/63 vs `s8-labkit` 49/63 (14/5, p = 0.064). 21 save rounds: 17 wrote results.json, 3 put the values in the answer, 1 had no values | none yet |
| `2026-11-22_full-lk17-fresh.json` | Same run on the 69 fresh cases x 3: 194/207. S1-S7 fresh 81/81 (`phase4d-fresh` 80/81). S8 `_f1` 55/63 vs `s8-labkit-fresh` 47/63 (11/3, p = 0.057); `_f2` 58/63, its 7 round-2 families 20/21 vs `s8-r2-targeted-fresh` 14/21 (6/0, p = 0.031). Remaining S8 failures: DEER modulation depth and labeling efficiency (5), `short_trace_f1` distance 3.18 vs 3.05 nm (3/3, same value each time), `zero_time_f2` t0 176 vs 168 ns (2) | none yet |
| `2026-11-22_lk18-targeted.json` | Failures-left round (`LABKIT-018-PLAN.md`, labkit 0.2.2): file-aware retyping hint, preview shows small files whole and counts rows, named-file nudge, DEER and CW hints, `fit_deer` checks with `n_boot`, `main_nm`, `labeling_efficiency`. 27 original cases x 3: 79/81, paired vs `full-lk17` 7/1. NIST 20/21 (19/21; the failure is a right fit with the symmetric exponential terms in another order), NOESY 3/3, 11 S8 families 32/33 (28/33), round-5 control 24/24. No run retyped data | none yet |
| `2026-11-22_lk18-targeted-fresh.json` | Same round, 36 fresh cases x 3: 102/108. S8 families `_f1` 29/33 (25/33), `_f2` 32/33 (28/33), new untuned `_f3` (generated after labkit 0.2.2 froze) 32/33, competitive s6-s8 9/9. Both scorecards paired vs `full-lk17*`: 18 fail->pass, 4 pass->fail, exact McNemar p = 0.0043. DEER labeling 11/12 (4/9). Left: CW dBpp from grid extrema (3), one hand-rolled DEER phase run, one lost labeling answer | none yet |
| `2026-11-23_lk18-lanczos-order.json` | Lanczos 1-3 prompts now state the certified term order ("Order the terms so that b2 < b4 < b6."): the three exponential terms are interchangeable, and 8 earlier runs failed on a pure reordering of correct pairs. Lanczos 1-3 x 3: 9/9. Earlier Lanczos scores used the unordered wording | none yet |
| `2026-11-23_es1-baseline.json` | EasySpin port slice 1 baseline (`EASYSPIN-PORT-PLAN.md`), deployed labkit 0.2.2, before `simulate_powder`/`fit_spectrum`: five new S8 powder and fit cases (Cu(II) frozen, rigid nitroxide, rhombic heme, pyrazine anion fit, mT to MHz at g) x 3, `eval` instance: 3/15 (units 3/3, the four simulation cases 0/3) | none yet |
| `2026-11-23_es1-baseline-regen.json` | Same baseline on the Cu, nitroxide and rhombic files regenerated with a converged generator grid (241 x 61; the 61 x 31 grid left 4e-3 of max on Cu), truth unchanged, x 3: 1/9 (nitroxide 1/3, Cu 0/3, rhombic 0/3; 0/9 on the old files). The scorecard's git field (`ba63afb`) predates the regeneration, which is in the commit that adds this file | none yet |
| `2026-11-23_es1-labkit.json` | EasySpin port slice 1 deployed (labkit 0.3.0: `simulate_powder`, `fit_spectrum`, `convert`), the five new S8 cases x 3, `eval` instance: 5/15 (Cu 2/3, units 3/3, nitroxide, rhombic, radical 0/3). Paired vs the baselines (`es1-baseline-regen` for the regenerated cases, `es1-baseline` otherwise): 2 fail->pass, 1 pass->fail, p = 1.0. Failures: the isotropic line hints fired instead of the powder ones, fits ended at their bounds and the model reported a typical value, API slips; see `LABKIT-019-PLAN.md` | none yet |

## Cautions when regenerating figures

1. **Never pool SPECTER and BGE numbers.** Each file's `meta.encoder` states
   which it is.
2. **Never plot the six risk-coverage points on one set of axes** without
   faceting or annotating by egress. The file's provenance block flags the
   mixture.
3. **Read `c1-fabricated` risk-coverage on coverage only.** On Qwen3.6 its
   selective risk of 1.000 is 3 answered items of which 3 were wrong, with an
   uninformative [0.00, 1.00] CI; on Qwen3.8 no item was answered and the
   scorer reports 0, which is undefined, not good. `c2-absent` on Qwen3.8 is
   4 answered items, same caution.
4. **The `2026-07-26` companion ablation is not in this folder** on purpose. It
   is a load/egress sensitivity point (agentic 0.688) and averaging it with the
   headline would be wrong.
5. **The C2 scorecards carry three intervals per rate.** `bootstrap` is the one
   to quote (it matches every other CI in the suite). `wilson` is the
   closed-form check, which matters because the answerable subset is only 20-32
   items. `correct_abstention_rate_ci_unconditional` also resamples *which*
   items are answerable; it is a robustness check and comes out marginally
   **narrower**, not wider, so do not present it as a conservative bound.
6. **The three cells of `on_answerable_when_source_removed` are a multinomial**
   over the same items. Their marginal CIs are not independent and the three
   rates cannot move separately.
7. **`rescored_by` in a scorecard means the verdicts and the statistics were
   produced in different passes.** `git_sha` is the capture-time commit;
   `rescored_at_git_sha` is when the CIs were added. Nothing about the
   underlying verdicts changed.
8. **Never pool or difference the 07-27 and 08-26 ablation files as if they
   were two samples of one system.** They differ by backbone, by the bare
   arm's token budget (4,096 vs 16,384; 33 unparseable vs 0) and by a month of
   retrieval commits. Within-file deltas are paired and clean; cross-file
   deltas between those two are suggestive. For T11, only calls-per-query
   and recovery rate are comparable across the two files; `web_fetch`'s
   failure definition changed in between. **The cross-backbone comparison to
   make is `2026-09-17_harness-ablation-qwen3.6-35b-a3b.json` against
   `2026-08-26_harness-ablation.json`** (and, for the agentic arm, against
   `2026-09-16_harness-ablation-qwen38-27b-recapture.json`): same protocol,
   same 199 questions; the bare and RAG arms bypass the harness, so those
   two compare cleanly whatever the date, and the agentic arm pairs against
   the recapture one day of harness apart. That comparison gives agentic
   −0.005 (p = 0.93), bare −0.050 (p = 0.25), RAG −0.085 (p = 0.002): the
   two backbones share a ceiling and differ on the floor, so the harness
   value is +0.533 on Qwen3.6 and +0.487 on Qwen3.8. Do not describe the
   +0.538 → +0.487 move as a correction of an inflated July figure; the
   09-17 file shows the bare-arm budget was worth ~0.035 on the bare arm and
   the harness value held. For T11 the same pair
   (`2026-09-17_toolreliability-qwen3.6-35b-a3b_toolreliability.json`,
   `2026-09-16_toolreliability-qwen38-recapture_toolreliability.json`) is
   under one failure definition and one coercion layer and compares on every
   row.

9. **Two generations of faithfulness files.** Every `*faithfulness*` file
   dated before 2026-09-16, and the 13-row gpt-oss placeholder, scored the
   agentic arm WITHOUT its `search`/`source` contexts (the capture's tool list
   predated those tools) while the RAG arm's abstracts were complete; their
   nulls are artifacts of that gap. The `*-recapture` files are the agentic
   arm re-captured with the complete set and judged against the same RAG arm.
   The `2026-09-17_harness-ablation-faithfulness-qwen3.6-35b-a3b.json` file is
   a complete-context capture from the start (the tool list was fixed before
   that run), so it belongs with the recapture generation, not the first.

10. **The 07-27 and 09-17 Qwen3.6 files are the same checkpoint on two
   harnesses two months apart**, the reverse of caution 8's pair (same harness
   date, two backbones). Within-file deltas are paired and clean; the 07-27
   per-question verdicts for the agentic arm no longer exist (overwritten by
   the 08-26 run before the per-tag layout), so any 07-27 vs 09-17 comparison
   is of marginals. Prefer the 09-17 files for every Qwen3.6 number: they are
   under the protocol the other two backbones ran under, with the bare arm at
   16,384 tokens and the chunk-level shadow.

11. **Bare and RAG arms on gpt-oss-20b are dominated by `no_final_message`.**
   Without tools the model reasons "Use search." and ends its turn with no
   final message (finish `stop`, ~130 tokens, no tool call) on 25 of 199 bare
   and 162 of 199 RAG prompts. These score as unparseable, i.e. wrong for
   accuracy; `precision_of_attempted` is the fairer bare/RAG number for this
   backbone, and the per-arm `empty_kinds` field separates them from
   truncation (of which there were none).
12. **The Sage-A1.5 files ran with three settings no earlier backbone had.**
   (a) A thinking-token budget of 8192 (vLLM `thinking_token_budget`, with a
   forced "answer now" end string), on every arm and in production: Sage has
   no reasoning-effort dial, and without it 80 of 199 bare answers ran out of
   the 16K output cap inside the reasoning trace (that pre-fix run is not
   committed). (b) The profile's card sampling on the bare and RAG arms
   (`direct_arm_sampling=profile`); earlier backbones' bare/RAG arms sent only
   temperature 0.7, so those two arms are not strictly like-for-like across
   backbones. (c) Concurrency 2: accuracy is unaffected, but `elapsed_s` is wall
   time under load and must not be compared with the concurrency-1 files. The
   16 unparseable bare answers are mostly Sage asking to search in an arm that
   has no tools, not truncation.
