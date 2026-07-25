# Routing eval

Trajectory-level eval for MuninAI's tool-routing decision: for one user
turn, which tool(s) fire, in what order, with what argument shape, and
whether the harness correctly clarifies / abstains / answers from
parametric knowledge. It scores the **routing decision**, never the answer
text (live answers have no stable ground truth; the routing decision does).

Two jobs:
1. Measures routing accuracy against Munin's real tool list (a paper-citable
   harness-section number — this is why it lives in `benchmarks/`, not
   `eval/`; see `../../../../todo_v2/KICKOFF-QUESTIONS.md` Q7).
2. Is the regression harness for the persona → router migration (Part A):
   the A0 baseline below is what A4 regresses against.

## Files

- `routing_eval.py` — schema, predicate DSL, `score_item`, the seed items,
  `run_item` (drives the live endpoint), `judge_abstention` (binary
  abstention-wording judge).
- `trajectory.py` — SSE capture + `step` reconstruction (the `tool_call`
  SSE carries no iteration index; `step` is rebuilt from `tool_result`
  boundaries so the `solo` clarification check works).
- `run.py` — CLI: drive the seed set over N reps, write a rep-aware
  scorecard (mean ± CI, per-item flip-rate, sample failures).

## Run it

```bash
cd backend/benchmarks/munin_bench
VLLM_MODEL_NAME=qwen3.6-35b-a3b python -m routing.run \
    --reps 8 --tag routing-pre-migration --seed 42

# smoke one item:
python -m routing.run --reps 1 --only weather_with_location --tag smoke \
    --out-dir /tmp/rt
```

Env / flags: `--base` (or `RETRIEVAL_BASE`, default
`http://127.0.0.1:8080`), `--email`, `--out-dir` (default
`backend/benchmarks/scorecards/`). Writes `<date>_<tag>.json` + `.md` twin.

Unit tests (offline, no service needed):

```bash
cd backend/benchmarks && python -m pytest tests/test_trajectory.py -q
```

## A0 conventions (pre-migration baseline)

- **Persona policy:** every item runs under the `chat` persona, NOT
  `expected.profile` (hard rule #3 — sending the expected profile would
  grade the router on an answer you handed it). The router does not exist
  pre-migration; `chat` is the routing entry persona the items are written
  against.
- **`ephemeral=true`** for a minimal, reproducible stack. Confirmed not to
  suppress the memory tools (only memory-context injection).
- **Skipped at A0:** `web_search_degraded` needs `inject_tool_result`
  (a test-only MCP injection hook), deferred to A2. The scorecard lists it
  under `skipped`.
- The baseline is a **regression yardstick, never a paper result.** Seed
  items target the post-migration router, so some fail against the current
  persona harness by design; A4's rule is "no previously-passing item may
  fail", not "all green".

## Scorecard shape

```
header     : tag, timestamp, git_sha, model, persona_policy, reps, seed, versions
aggregate  : n_items, mean_pass_rate, ci95_low/high, mean_flip_rate
per_item   : {category, pass_rate "k/N", flip_rate, checks_pass_rate, sample_failures}
skipped    : [{id, reason}]
```

`flip_rate` (fraction of adjacent rep-pairs that change pass/fail) is the
routing-stability signal: high flip = unreliable routing on that phrasing,
a finding rather than noise.
