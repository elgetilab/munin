# Per-question verdict tables

The Track D (harness ablation), Track C2b (paired shadow corpus) and
per-arm faithfulness scorecards publish aggregates. The run files they were
computed from (`ablation_runs/`, `c2_runs/`) are not released, because they
carry model answers, retrieved passages and paper identifiers. These tables
are the part of those run files that the statistics need: one row per
question per arm, enough to recompute every published rate, count, CI and
paired test, and nothing else.

Each file is named after the scorecard it reproduces
(`<scorecard>.verdicts.csv`, scorecards in `backend/benchmarks/scorecards/`).
The tables in `docs/paper-kit/verdicts/` are byte-identical copies.

## What is in a row, and what is not

| Track | Columns |
|---|---|
| D (ablation) | `qid`, `arm` (`bare` / `rag` / `agentic`), `verdict` (`correct` / `incorrect` / `abstain` / `unparseable`), `letter` (the option letter the answer chose; empty when none was parsed) |
| C2b (shadow corpus) | `qid`, `condition` (`present` / `absent`), `verdict`, `letter` |
| Faithfulness per arm | `qid`, `arm` (`rag` / `agentic`), `frac_supported` (MiniCheck fraction of the answer's claims supported by that arm's own contexts) |
| Faithfulness, single live arm | `qid`, `n_claims`, `mean_support`, `frac_supported`, `fully_supported` (0/1); the three scores are empty when `n_claims` is 0 |

There is no question text, answer text, passage, model output or DOI in any
file, and no correct-answer column: every scorecard statistic is a function
of `verdict` alone. The `qid` is the LitQA2 item id. The question, its
options and the answer are fetched by qid from LAB-Bench
(`futurehouse/lab-bench`, config `LitQA2`) at the pinned revision
`5c77cec648430f30611808808861eb86f81d5eaa` (`LITQA2_REVISION` in
`munin_bench/benchmarks/litqa2_runner.py`); LitQA2 is CC BY-SA 4.0 and is
not redistributed here. Bare has no faithfulness rows by design: it
retrieves nothing, so there is no evidence to check claims against.

## Files

| File | Rows | Scorecard numbers it reproduces | Paper claim |
|---|---|---|---|
| `2026-08-26_harness-ablation` | 3 x 199 | Headline ablation, Qwen3.8: agentic 0.874, bare 0.387, RAG 0.211; agentic minus bare +0.487 | Claim 1, R1 |
| `2026-09-17_harness-ablation-qwen3.6-35b-a3b` | 3 x 199 | Qwen3.6 on the current protocol: agentic 0.869, agentic minus bare +0.533 | Claim 1, R1, R7 |
| `2026-09-16_harness-ablation-gpt-oss-20b` | 3 x 199 | gpt-oss-20b: agentic 0.563, agentic minus bare +0.156 p=0.004 | Claim 1, R1, R7 |
| `2026-09-16_gpt-oss-20b-prerepair` | 3 x 199 | gpt-oss-20b before the tool-name repair (agentic 0.467); the parser-cost point. Scorecard is the `run_all` shape (`tasks.ablation`, `meta.ablation_deltas`) | R7 |
| `2026-09-16_harness-ablation-qwen38-27b-recapture` | 2 x 199 | Qwen3.8 agentic arm re-captured for R2 (0.869) against the 08-26 RAG arm | R1, R2 |
| `2026-09-16_harness-ablation-gpt-oss-20b-recapture` | 2 x 199 | gpt-oss-20b agentic re-capture (0.583) against its RAG arm | R1, R2 |
| `2026-09-15_harness-ablation-agentic-egressoff` | 3 x 199 | Qwen3.8 agentic at `egress=off` (0.663), bare and RAG copied from 08-26; the `paired_vs_egress_full` block (+0.211, transitions) is recomputed from this table and the 08-26 one | R1 |
| `2026-09-17_harness-ablation-agentic-egressoff-qwen3.6-35b-a3b` | 3 x 199 | Qwen3.6 at `egress=off` (0.704); with the 09-17 full table, full minus off +0.166 | R1 |
| `2026-09-17_harness-ablation-agentic-egressoff-gpt-oss-20b` | 3 x 199 | gpt-oss-20b at `egress=off` (0.482), agentic(off) minus bare +0.075 p=0.10; with the 09-16 full table, full minus off +0.080 | R1, R7 |
| `2026-09-15_abstention-c2-shadow` | 2 x 50 | Headline C2b, Qwen3.8, `egress=off`: correct abstention 24/27 = 0.889, and the question-paired delta against 07-27 | Claim 3, R3 |
| `2026-07-27_abstention-c2-shadow` | 2 x 50 | C2b on Qwen3.6, July harness: 18/27 = 0.667, and the delta against 07-10 | R3 |
| `2026-07-10_abstention-c2-shadow` | 2 x 50 | The earlier flat-loop pair: 4/20 = 0.200 | R3 |
| `2026-07-27_abstention-c2-shadow-egressfull` | 2 x 50 | The same pair at `egress=full`; a different experiment, do not compare across | R3 |
| `2026-09-16_abstention-c2-shadow-gpt-oss-20b` | 2 x 50 | gpt-oss-20b: 16/20 = 0.800, and the delta against the 09-15 Qwen3.8 pair | R3, R7 |
| `2026-09-17_abstention-c2-shadow-qwen3.6-35b-a3b` | 2 x 50 | Qwen3.6, current harness: 28/32 = 0.875, delta against 09-15 minus 0.014 p=0.85 | R3, R7 |
| `2026-09-16_harness-ablation-faithfulness-qwen38-recapture` | 199 + 199 | Headline per-arm faithfulness: agentic 0.540 vs RAG 0.282, +0.258 p<0.001 | Claim 2, R2 |
| `2026-09-17_harness-ablation-faithfulness-qwen3.6-35b-a3b` | 199 + 198 | Qwen3.6, complete contexts: 0.627 vs 0.290, +0.336 | Claim 2, R2, R7 |
| `2026-09-16_harness-ablation-faithfulness-gpt-oss-20b-recapture` | 159 + 28 | gpt-oss-20b: agentic 0.392, paired +0.253 p=0.056 on n=21 | R2, R7 |
| `2026-08-26_harness-ablation-faithfulness` | 163 + 199 | Superseded Qwen3.8 capture (agentic contexts incomplete): +0.010 p=0.776 | R2 |
| `2026-07-27_harness-ablation-faithfulness` | 193 + 195 | Superseded Qwen3.6 capture (same gap): +0.023 p=0.496 | R2 |
| `2026-07-09_faithfulness-agentic-live` | 40 | Single-arm live faithfulness, baseline: 0.356 claims supported | R2 (B3/B4) |
| `2026-07-09_faithfulness-agentic-live-t1a` | 40 | The same, T1a variant (0.303) | R2 (B3/B4) |
| `2026-07-09_faithfulness-agentic-live-cap` | 40 | The same, cap variant (0.331) | R2 (B3/B4) |

The faithfulness scorecards already embed their per-question values; these
tables are the same numbers in the common flat format.

**Not published, because the run files no longer reproduce them:**

- `2026-07-27_harness-ablation` (Qwen3.6, July). Its agentic array was
  overwritten by the 08-26 Qwen3.8 run and its bare and RAG arrays by the
  08-25 Qwen3.8 arms, before runs were kept per tag. The September Qwen3.6
  file above is the like-for-like replacement.
- `2026-07-26_harness-ablation` (the load and web-degradation companion).
  Its agentic arm survives and reproduces 137 / 46 / 14 / 2 exactly, but it
  shares the overwritten July bare and RAG arms, so its paired deltas cannot
  be recomputed.

## Row order

Track D statistics pair over sorted qids, so row order does not matter.
Two tracks are order-sensitive at the last digit of a CI, and the tables
keep the capture order for them: C2b (`run_c2` walks the verdict file in
the order the questions completed) and faithfulness (the per-arm bootstrap
runs over the scored rows in capture order). The `paired_vs_egress_full`
block of the 09-15 egress-off scorecard was computed in the arm files' row
order rather than sorted, which shifts its CI by 0.005; walking the 08-26
table's agentic rows in file order reproduces it. Point estimates never
depend on order.

## Recomputing from a table

From `backend/benchmarks/` with `PYTHONPATH=.` (needs `numpy` and `scipy`):

```python
import csv
from munin_bench.metrics import paired_bootstrap, single_bootstrap

def arms(name):
    out = {}
    for r in csv.DictReader(open(f"verdicts/{name}.verdicts.csv")):
        out.setdefault(r.get("arm") or r["condition"], {})[r["qid"]] = r
    return out

d = arms("2026-08-26_harness-ablation")
qids = sorted(d["agentic"])
corr = lambda a: [1.0 if d[a][q]["verdict"] == "correct" else 0.0 for q in qids]

print(sum(corr("agentic")) / len(qids))               # accuracy 0.8744
print(paired_bootstrap(corr("agentic"), corr("bare")))  # +0.4874 [0.407, 0.568], p 0.0

# a cross-backbone, question-paired test: Qwen3.6 minus Qwen3.8, agentic arm
q36 = arms("2026-09-17_harness-ablation-qwen3.6-35b-a3b")["agentic"]
q38 = arms("2026-08-26_harness-ablation")["agentic"]
qs = sorted(set(q36) & set(q38))
c = lambda v: [1.0 if v[q]["verdict"] == "correct" else 0.0 for q in qs]
print(paired_bootstrap(c(q36), c(q38)))               # -0.005 [-0.050, +0.040], p 0.93

# faithfulness: per-arm mean and CI in file order
f = arms("2026-09-16_harness-ablation-faithfulness-qwen38-recapture")
print(single_bootstrap([float(r["frac_supported"]) for r in f["agentic"].values()]))  # 0.540
```

For C2b, `munin_bench.abstention.run_c2._paired(present, absent, date)` and
`_correct_abstention_delta(old_pair, new_pair)` take
`{qid: {"verdict": ...}}` dicts, which is what `arms(...)["present"]` and
`["absent"]` are; they return every rate, CI and delta in the scorecard.

`python -m munin_bench.verdict_tables verify` does all of this for every
table: it reads only the CSVs and the scorecards and checks each published
rate, count, CI and paired delta for exact equality (or to the scorecard's
rounding), plus the cross-backbone comparisons the paper cites.
`python -m munin_bench.verdict_tables build` regenerates the tables from the
run directories, where they exist.
