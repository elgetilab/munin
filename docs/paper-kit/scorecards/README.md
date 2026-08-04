# Scorecards behind the headline claims

15 committed scorecard JSONs, copied verbatim from
`backend/benchmarks/scorecards/`. These are the raw data behind every number in
`05-RESULTS.md`, so figures and paired tests can be regenerated without the
repository.

The full set is 58 JSON plus 47 Markdown twins; the remainder are mostly the
routing-tuning runs from the A0 through A5 migration, which no paper section
cites.

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

## Index

| File | Backs | Section |
|---|---|---|
| `2026-07-27_harness-ablation.json` | Headline three-arm ablation, n=199 paired | R1 |
| `2026-07-27_harness-ablation-faithfulness.json` | Per-arm paired faithfulness, RAG vs agentic (per-question values included) | R2 |
| `2026-07-08_faithfulness-judge-ragtruth.json` | MiniCheck judge validation on RAGTruth | R2 |
| `2026-07-27_abstention-c1-fabricated.json` | C1, 100 fabricated papers, current harness | R3 |
| `2026-07-27_abstention-c2-shadow.json` | C2b paired shadow corpus at `egress=off` | R3 |
| `2026-07-27_abstention-c2-shadow-egressfull.json` | The same pair at `egress=full`. **A different experiment.** Do not compare across the two. | R3 |
| `2026-07-27_risk-coverage.json` | Six risk-coverage operating points, with `mixed_generations` / `mixed_egress` provenance markers | R3 |
| `2026-07-28_litsearch.json` | LitSearch, post-fix, canonical | R4.2 |
| `2026-07-28_litsearch-prefix.json` | LitSearch, pre-fix. The before-half of the citation-re-rank A/B. | R4.2, F5 |
| `2026-07-03_baseline-specter-v1.json` | LitQA2 retrieval, SPECTER-v1 baseline | R4.4 |
| `2026-07-03_bge-large.json` | LitQA2 retrieval, BGE-large full-corpus | R4.4 |
| `2026-07-06_answer-specter-v1-v2.json` | End-to-end answer, SPECTER arm, fixed parser | R5 |
| `2026-07-06_answer-bge-large-v2.json` | End-to-end answer, BGE arm, fixed parser | R5 |
| `2026-07-24_answer-full-900s.json` | End-to-end answer, agent architecture, 900 s | R5 |
| `2026-07-27_toolreliability-clean.json` | Tool-use telemetry over the clean run | R6 |

## Cautions when regenerating figures

1. **Never pool SPECTER and BGE numbers.** Each file's `meta.encoder` states
   which it is.
2. **Never plot the six risk-coverage points on one set of axes** without
   faceting or annotating by egress. The file's provenance block flags the
   mixture.
3. **Read `c1-fabricated` risk-coverage on coverage only.** Its selective risk
   of 1.000 is 3 answered items of which 3 were wrong, with an uninformative
   [0.00, 1.00] CI.
4. **The `2026-07-26` companion ablation is not in this folder** on purpose. It
   is a load/egress sensitivity point (agentic 0.688) and averaging it with the
   headline would be wrong.
