# Scorecards behind the headline claims

54 committed scorecard JSONs, copied verbatim from
`backend/benchmarks/scorecards/`. These are the raw data behind every number in
`05-RESULTS.md`, so figures and paired tests can be regenerated without the
repository.

The full set is 97 JSON plus 57 Markdown twins; the remainder are mostly the
routing-tuning runs from the A0 through A5 migration, which no paper section
cites.

**Answer text is withheld.** The four `*_answer-*-900s.json` files used to
carry `text_tail`, the last 600 characters of each model answer. Those tails
quoted the source papers and gave away LitQA2 answers, so the field was
removed for the public release (2026-10). No metric reads it; every verdict,
letter and per-query array is unchanged.

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
from the same chain. See cautions 8, 9 and 10.

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
| `2026-07-27_toolreliability-clean.json` | Tool-use telemetry over the Qwen3.6 clean run. `web_fetch` error rate not comparable to 08-26 (failure definition changed between the runs) | R6 |

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
   closed-form check, which matters because the answerable subset is only 20-27
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
   Quote only the recapture files for claim 2; the Qwen3.6 file cannot be
   recaptured (checkpoint retired).
10. **Bare and RAG arms on gpt-oss-20b are dominated by `no_final_message`.**
   Without tools the model reasons "Use search." and ends its turn with no
   final message (finish `stop`, ~130 tokens, no tool call) on 25 of 199 bare
   and 162 of 199 RAG prompts. These score as unparseable, i.e. wrong for
   accuracy; `precision_of_attempted` is the fairer bare/RAG number for this
   backbone, and the per-arm `empty_kinds` field separates them from
   truncation (of which there were none).