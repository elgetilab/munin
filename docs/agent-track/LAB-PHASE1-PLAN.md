# Lab analysis Phase 1: fixtures and eval harness

Status: DONE 2026-11-14. Approved the same day ("execute the plan afterwards"),
defaults below taken as decided. Parent plan: `LAB-ANALYSIS-PLAN.md`.

## Outcome

- 95 cases (S1 76, S2 10, S3 2, S5 2, S6 5), regenerable byte for byte;
  115 offline tests; live sandbox suite 51/51 with the S0 additions (the
  pre-existing "kernel usable after timeout" check failed once in three runs
  and now prints its detail).
- Smoke run, 11 cases, 1 rep, Sage-A1.5, persona `munin`
  (`scorecards/2026-11-14_smoke.json`, NOT a result): 9/11 pass, mean check
  score 0.874. Every grader ran at least once.
- The smoke run exposed one grader bug, fixed in 670dbe4: `manifest_diff`
  compared plot series by draw order, which matplotlib does not keep stable.
- Real misses the smoke run showed (inputs for Phase 2 and `labkit`):
  - `s2_bruker_1d`: the model read the int32 FID (`DTYPA= 0`) as int16, never
    tried nmrglue, then reported nulls with honest warnings. No made-up numbers.
  - `s3_bars`: unrequested edits on most turns (dpi, size, title, xlabel,
    genotype order); the PDF export came out 94.7 mm wide, not 85
    (tight bbox); turn 7 ("remove KO2") answered in 2 s with a repeated
    `<answer>` fragment and no tool call.
  - `s3_bars` turn 4 hit vLLM's 65,536-token context limit after 57
    sandbox calls over four turns, and the stored reply is the raw 400 error.
    A production issue, not an eval artifact: long plot sessions need
    history trimming. Logged in `TODO.md` section 6.
- Phase 1b (deferred): the 31P lineshape case (needs a lineshape simulator,
  `labkit` work) and MicroCal `.itc` (needs a real sample or licence-checked
  demo files). Also: the figure manifest does not recognise seaborn's error
  bars (drawn as lines); a `fighook` fix.

Phase 1 builds the measuring instrument, not the measurement. It ends with a
smoke run (one case per suite on the production backbone) proving every path
works end to end. The baseline numbers are Phase 2.

## Decisions (defaults, taken because the user said to proceed)

| # | Question | Decision |
|---|---|---|
| D1 | How does a grader read the model's numbers? | Every prompt ends with a fixed answer-format suffix asking the model to also save `results.json` with named keys. Graders read that artifact. A fenced JSON block in the final answer is the fallback. No LLM extraction. The suffix is identical in both arms, so it does not bias the paired comparison. |
| D2 | Commit generated fixtures or only the generators? | Both. Generators are seeded; a test regenerates every fixture and checks its sha256 against `fixtures/lab/SHA256SUMS`, so reviewers see the files and drift is caught. |
| D3 | Where do generators that need nmrglue or lmfit run? | Revised while building: no generator needs them. The Bruker experiment is written directly (acqus, int32 FID, procs) and only *verified* with nmrglue in the `munin-sandbox` image. Generators need numpy, scipy and openpyxl. |
| D4 | Vision judge | Deferred. The production backbone (Sage-A1.5) is text-only, so a local-backbone judge cannot see a figure today. Mechanical manifest checks are the grader; the judge slot stays in the scorecard as `null`. |
| D5 | Held-out set | `/opt/munin/data/evals/lab/`, outside git. Until real exports arrive it holds perturbed synthetic variants (other seeds, other noise, renamed columns) produced by the same generators with a private seed list. Real exports are a request to the user, not a blocker. |
| D6 | Reps | Smoke: 1 rep. Phase 2 default: 3 reps per case, temperature as in production. |
| D7 | Eval identity | `eval-lab@munin.local`, non-ephemeral chats (the sandbox refuses ephemeral ones), `X-Munin-Egress: off`, each chat deleted after grading, which also purges its sandbox. |
| D8 | Where the runner runs | On hugin, against retrieval on localhost with the gateway token (as `munin_bench` does), concurrency 1 by default (well under vLLM max-num-seqs and the background cap of 2). |
| D9 | User data from the varghele repos | The user owns them; data copied in under Apache-2.0 with a provenance line. Reference values are recomputed with fresh code here, never taken from the old scripts' outputs. |

## Layout

```
backend/benchmarks/fixtures/lab/
  README.md              provenance line per fixture, licence, how to regenerate
  SHA256SUMS
  generate/              seeded generators (one module per family)
  s1_fits/<case>/        data files + truth.json
  s2_formats/<case>/
  s3_plots/<case>/
  s5_repro/<case>/
  s6_nmr/<case>/
  nist/                  NIST StRD files, downloaded once, certified values in truth.json
backend/benchmarks/munin_bench/labanalysis/
  cases.py               case schema + loader
  client.py              upload, SSE turn driver, artifacts, bundle, cleanup
  graders.py             one function per check type
  run.py                 CLI: --suite, --case, --reps, --tag, --heldout
  score.py               scorecard writer (existing scorecard.py format)
backend/benchmarks/tests/test_labanalysis_*.py   offline tests, no model
```

## Case schema

A case is a `case.json` next to its fixture files:

```json
{
  "id": "s1_4pl_seed3",
  "suite": "S1",
  "inputs": ["dose_response.csv"],
  "turns": ["Fit a four-parameter logistic ... "],
  "results_keys": {"logEC50": "float", "hill": "float", "top": "float", "bottom": "float"},
  "checks": [
    {"type": "numeric", "key": "logEC50", "truth": "truth.json:logEC50", "rtol": 0.02},
    {"type": "ci_contains", "key": "logEC50_ci", "truth": "truth.json:logEC50"},
    {"type": "flagged", "any_of": ["ambiguous", "not identifiable"]}
  ]
}
```

Multi-turn cases (S3) list several turns; each turn can carry its own checks.

## Fixtures, first cut

**S1 fit correctness**
- NIST StRD nonlinear: all 27 datasets, both starting values, certified
  parameters and residual SS (public domain).
- Synthetic, 5 seeds each: 3PL, 4PL, 5PL dose response; Michaelis-Menten;
  competitive inhibition (global, shared Km/Vmax); Morrison tight binding;
  one- and two-phase decay; standard curve with unknowns (some outside range).
- Traps: replicates given as columns (fit replicates, not means); a heteroscedastic
  set needing 1/Y² weighting; an unidentifiable 4PL (plateau never reached);
  an outlier-contaminated set; an unknown outside the standard range that
  must not be extrapolated.

**S2 instrument parsing** (each from a known truth)
- NanoDrop TSV in UTF-8, UTF-16 LE with BOM, and de-DE decimal commas.
- Tecan xlsx plate layout with "OVER" wells and a metadata header block.
- Gen5-style kinetic export (time as `h:mm:ss`, temperature column).
- BMG table view (wells as rows).
- Jasco CD text with `LOCALE 1031` (comma decimals, `XUNITS NANOMETERS`).
- MicroCal `.itc` text format (injections, heats).
- Bruker 1D FID directory (acqus, procs, fid) zipped, made with nmrglue.
- Proprietary binaries (`.twbk`, `.pcrd`, `.sda`, `.nt`) as random bytes with a
  plausible header: the pass condition is asking for a text or Excel export and
  reporting no numbers.

**S3 iterative plot editing**: two sessions of 8 turns over one dataset
(grouped bars with SD, and a dose-response plot): log x axis, SD to SEM,
significance brackets, palette, size in mm, dpi, PDF export, dropping a
series, relabelling. Checks read the figure manifest per turn, diff it against
the previous turn, and verify earlier artifacts are byte-identical.

**S5 reproducibility**: two cases (a fit and a plot); download the bundle, run
`analysis.py` in a fresh `munin-sandbox` container with no network, compare
outputs numerically.

**S6 domain workflows (first)**
- HSQC titration: peak lists at 8 ligand concentrations for 40 residues
  (some missing, some vanishing), known Kd and per-residue Δδmax; CSP with
  α = 0.14 (stated in the prompt), global Kd with ligand depletion.
- T1, T2, hetNOE: Bruker pseudo-2D series (nmrglue) or peak-intensity tables
  with known rates per residue.
- From the user repos (D9): NOESY buildup (1H1H-NOESY example: Ceritinib in
  lipid, five mixing times), 31P lamellar/isotropic spectrum (31P-siml
  `standard/31P_INP` + parameters), 2H order profile (GNN2H `data/2H_data`).
  References recomputed here.

**S0** stays in the existing `test_artifacts_live.py`; Phase 1 adds three
cases there from these fixtures (UTF-16 file round trip, a zipped Bruker
folder unpacked under `inputs/`, a de-DE file read back byte-identical).

## Graders

`numeric` (rtol/atol vs truth), `ci_contains`, `flagged` (keyword in answer or
`results.json` warnings), `no_numbers` + `asks_export` (binaries),
`no_extrapolation`, `manifest` (assert on figure manifest fields: axis scale,
labels, series count, size in mm, dpi, format), `manifest_diff` (only the
asked field changed), `immutable` (earlier artifacts' sha256 unchanged),
`file_meta` (PNG dpi and pixel size, PDF is vector with embedded fonts),
`reproduces` (S5). Each returns pass/fail plus a detail string; a case scores
the fraction of its checks passed, and the scorecard keeps per-check results.

## Scorecards

`scorecards/<date>_lab-<tag>.json` in the existing format: task = suite,
system = `<backbone>/<arm>`, per_query = `<case>#<rep>` with per-check values,
so Phase 4 can pair arms with `pipelines/compare.py`. Copied to
`docs/paper-kit/scorecards/` in the same commit (scorecard/kit sync rule).
The smoke run is tagged `smoke` and is not a result.

## Order of work

1. Case schema, loader, graders, with offline unit tests on hand-made results.
2. S1 generators and NIST download; determinism test.
3. S2 generators (host and sandbox image), S3 and S5 datasets, S6 generators
   and the user-repo fixtures with recomputed references.
4. Client and runner; a dry mode that grades a recorded run so the runner is
   testable without the model.
5. S0 additions in the live sandbox suite.
6. Smoke run on production (1 case per suite), fix what breaks, commit
   scorecard as `smoke`.

## Done when

- Offline tests pass (graders, schema, generator determinism).
- `test_artifacts_live.py` passes with the S0 additions.
- The smoke run completes for S1, S2, S3, S5 and S6 with every grader
  executed at least once, and its scorecard is committed.

## Not in Phase 1

Pass thresholds, the baseline run, `labkit`, the vision judge, S4 (needs
sleipnir), S7 (needs the viewer).
