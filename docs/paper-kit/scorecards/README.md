# Scorecards behind the headline claims

23 committed scorecard JSONs, copied verbatim from
`backend/benchmarks/scorecards/`. These are the raw data behind every number in
`05-RESULTS.md`, so figures and paired tests can be regenerated without the
repository.

The full set is 65 JSON plus 49 Markdown twins; the remainder are mostly the
routing-tuning runs from the A0 through A5 migration, which no paper section
cites.

**Backbone is not stamped in most ablation-family files.** Every file dated
2026-08-26 or later is on Qwen3.8-27B (production; the 09-15 C2b file also
carries an explicit `backbone` field); every earlier file is on the retired
Qwen3.6-35B-A3B. See caution 8.

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
with **no `meta` block, no model and no encoder field**. The two headline
ablation files differ only in `git_sha` and `date`.

## Index

| File | Backs | Section |
|---|---|---|
| `2026-08-26_harness-ablation.json` | **Headline** three-arm ablation, n=199 paired, Qwen3.8-27B | R1 |
| `2026-07-27_harness-ablation.json` | The same three arms on Qwen3.6-35B-A3B (second backbone; bare arm at 4,096 tokens) | R1 |
| `2026-08-26_harness-ablation-faithfulness.json` | **Headline** per-arm paired faithfulness, RAG vs agentic, Qwen3.8 (per-question values included) | R2 |
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
   deltas are suggestive. For T11, only calls-per-query and recovery rate are
   comparable across the two files; `web_fetch`'s failure definition changed
   in between.
