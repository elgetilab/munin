# Lab analysis: deferred work

Status: DEFERRED 2026-11-15. Not being worked on now: bugs found by the Phase 1
smoke and overflow check runs come first (plot edits, stray `<answer>` tag,
seaborn error bars in the figure hook, flaky sandbox timeout check), then other
work. Parent plan: [`LAB-ANALYSIS-PLAN.md`](LAB-ANALYSIS-PLAN.md); Phase 1:
[`LAB-PHASE1-PLAN.md`](LAB-PHASE1-PLAN.md); open bugs in
[`TODO.md`](TODO.md) section 6.

## 1. Phase 1b: 31P lineshapes and MicroCal `.itc`

Add the two input families Phase 1 left out: 31P solid-state lineshape fits and
MicroCal ITC exports (`.itc`), with oracle references and traps like the
existing S2 instrument-format cases.

## 2. Held-out set

`/opt/munin/data/evals/lab` (outside git) is still empty. It needs real
instrument exports from the group (NanoDrop, plate reader, Bruker, ITC), with
the expected results written down by whoever ran the experiment. Blocked on the
user providing data.

## 3. Multi-backbone re-run (HEAVY, required before any model-capability claim)

The lab suites are first measured on the production backbone only. Re-run
S1-S7, both arms (ad hoc and `labkit`), on every backbone we can serve,
starting with gpt-oss, paired against the backbone results. Haiku is excluded
(fallback only).

## Also waiting

- Phase 2 baseline (full suite, 3 reps, ad hoc arm): runs after the bug fixes,
  not deferred indefinitely.
