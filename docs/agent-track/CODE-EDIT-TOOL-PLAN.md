# Plan: `edit_python`, a patch-style edit tool for code

Status: **BUILT + DEPLOYED 2026-08-23** (`660aaea`). Measured; see Results. Root-cause follow-on to the context-budget
work (`../paper-track/done/CONTEXT-BUDGET-FIX-SCOPE.md`, reopened 2026-08-12).
Supersedes the argument-elision scope (`../paper-track/TOOL-ARG-ELISION-SCOPE.md`),
which was rejected as symptom-treatment.

## The problem, measured

`run_python` arguments are the single largest component of an overflowing
prompt (18,731 to 38,989 tokens across the 12 Aug 11-12 overflow turns). The
weight is not a few giant scripts; it is repetition:

| measurement (since 2026-07-01) | value |
|---|---|
| successive `run_python` pairs in a turn that are >80% similar | **170 / 306 (56%)** |
| `run_python` argument tokens that literally duplicate the previous call | **285,072 / 805,993 (35%)** |
| median script re-sent | 91 lines |
| median lines actually changed | **3 (3.2%)** |
| median edit sites per re-paste | 2 |
| re-pastes with <= 2 edit sites | 53% |

**The model resends 91 lines to change 3.** A third of all Python source in the
system is a verbatim copy of the call immediately before it.

This is not a capability gap. The sandbox is already a stateful Jupyter kernel
scoped to the conversation, and its description says so. The model has no
natural affordance for "change these three lines", so it uses the only lever it
has: rewrite everything.

## Why NOT unified diff

`update_artifact` already ships a unified-diff mode (`is_diff=True`,
`artifact_store.apply_unified_diff`, strict matching, no fuzz, `base_version`
concurrency check). It is correct, tested, and documented.

**Adoption since 2026-07-01: 1 of 66 calls (2%).**

The handful of `hunk` / `context` errors in artifact results suggests the model
tried, hit strict line-number matching, and gave up. Unified diff demands exact
line numbers and hunk lengths, which is precisely the arithmetic LLMs are worst
at. Building a second unified-diff tool would reproduce that outcome.

**So the format is the design decision, not an implementation detail.** Use
search/replace semantics: an exact `old` string and its `new` replacement, with
no line numbers and no counts. The model already reproduces source verbatim,
which is exactly the skill this format needs.

## Design

### Tool: `edit_python`

    edit_python(edits: [{old: str, new: str}], timeout_s: int = 30)

Semantics:

- Resolve the **base source**: the most recent `run_python` (or `edit_python`)
  source in this conversation. No id for the model to juggle; the common case
  is "the thing I just ran".
- Apply each `{old, new}` in order. Each `old` must appear **exactly once** in
  the current source. Zero matches or more than one match is a hard error that
  names which edit failed and why.
- Execute the resulting source in the same kernel via the existing
  `/exec/{conversation_id}` path, so behaviour after the edit is identical to
  `run_python` today.
- Return the normal `run_python` result plus `edits_applied`, `lines_added`,
  `lines_removed`, mirroring `update_artifact`'s return shape.

Multiple edits per call matter: the median re-paste has 2 edit sites, so a
one-edit-per-call tool would only halve the round trips.

### Base-source tracking

Keep an in-process, per-conversation cache of the last executed source in
`mcp/tools/sandbox.py`, with a fallback that reads the most recent `run_python`
arguments from `chat_store` when the cache misses (service restart, or a
different worker). Edits happen seconds apart inside one turn, so the cache
will almost always hit; the fallback exists so a restart degrades to a slower
path rather than an error.

Deliberately NOT stored in the sandbox sidecar: that is a separate container
with its own deploy, and nothing about this needs kernel-side state.

### Failure modes, and what the model is told

- `old` not found: return the error plus a short list of the closest
  non-matching lines, so the model can correct rather than fall back to a full
  re-send.
- `old` matches more than once: say so and ask for more surrounding context.
- no base source yet: tell it to call `run_python` first.

Every error message must end with a usable next action. A vague failure here is
the fastest route back to re-pasting.

## Adoption is the risk, not correctness

The 2% artifact-diff result is the warning. A correct tool that the model
ignores is wasted work. Three mitigations, in order of expected value:

1. **Surface the affordance at the decision point.** Add a one-line hint to the
   `run_python` RESULT (for example `edit_hint: "to change a few lines, call
   edit_python instead of re-sending"`). The model reads that result
   immediately before deciding how to iterate. This is where adoption is won or
   lost, and it costs a handful of tokens per call.
2. **Lead the tool description with when to use it**, not with mechanics. State
   the trigger ("you have already run code and want to change part of it")
   first.
3. **Trim the `run_python` description while we are there.** Its single
   `IMPORTANT` callout is currently spent on artifact download links, and the
   statefulness that makes editing viable sits 22% of the way into a 1,118-char
   blob.

### Escalation if adoption stays low

If adoption is under 20% after two weeks, gate rather than persuade: reject a
`run_python` whose source is >80% similar to the previous source in the same
conversation, returning an error that points at `edit_python`. This is a
behaviour-forcing change with real failure modes (a legitimate near-identical
re-run becomes an error), so it is a deliberate second step, not part of v1.

## Measurement

Baseline is already captured above. Re-run the same analysis two weeks after
deploy:

- re-paste rate (currently **56%** of successive pairs >80% similar)
- duplicated share of `run_python` argument tokens (currently **35%**)
- `edit_python` adoption: calls, and the ratio against `run_python` re-sends
- error rate per failure mode, especially `old` not found

Success: re-paste rate below 25% and duplicated tokens below 15%. Those two
moving is the whole point; context overflows should follow, but they are a lagging
and traffic-dependent indicator, so do not gate on them.

## Implementation steps

1. `mcp/tools/sandbox.py`: `edit_python`, base-source cache, `chat_store`
   fallback, apply-and-execute path reusing the existing exec call.
2. `mcp/schemas.py`: tool spec. **Decision needed**: `CORE_TOOLS` (visible on
   every turn, costs schema tokens on all traffic) versus the `code` profile's
   `resident_tools` (currently `compile_latex`, `sandbox_reset`,
   `save_artifact_to_documents`). Recommend resident-on-`code` first, since
   `run_python` is itself core and `tool_search` can still reach it elsewhere.
3. `mcp/dispatchers.py`: `@register_tool("edit_python")`, following `_run_python`.
4. `run_python` result: add the `edit_hint` field.
5. Tests, mirroring `mcp/tools` conventions: single-match applied, multi-edit
   ordering, zero-match error, ambiguous-match error, no-base error, cache miss
   falls back to `chat_store`, executed source matches the applied result, and
   the persisted transcript records the resulting source (so replay and the
   context budget see what actually ran).
6. Description edits from "Adoption" above.

## Risks

- **Adoption**, covered above. This is the one that decides whether the work pays.
- **A wrong edit silently produces working-but-different code.** Exact-match plus
  unique-match makes a wrong application unlikely, but not impossible. The
  executed source is persisted, so it stays auditable.
- **Context accounting**: `edit_python` arguments are counted by the deployed
  budget code exactly like any other tool call, so a large `old` string is
  charged honestly. No interaction to fix, but worth asserting in a test.
- **Schema cost**: another tool description on every `code` turn. Keep it short;
  measure the delta with `chat_context.tools_schema_tokens`.

## Out of scope

- The 45-calls-in-one-turn over-tooling problem (HARNESS T2, `soft_max_calls`).
- Artifact-side diff adoption. The same search/replace argument probably applies
  to `update_artifact`, and if `edit_python` adoption is good that is the
  evidence to act on, but it is a separate change.
- Trimming large user pastes, still open from the previous scope.


---

## Results (2026-08-23, `evals/eval_edit_python.py`)

Deployed to prod (`deploy.sh personas` then `retrieval`; personas first because
they are read once at startup). Resident on `code` only, 383 schema tokens on
coding turns, nothing elsewhere.

Paired A/B over real decision points, arms differing in the request rather than
the deployment (`tools` with/without `edit_python`, plus the `edit_hint`):

| run | n/arm | re-paste before | after | p | adoption | p |
|---|---|---|---|---|---|---|
| 1 (no hint, harness bug) | 60 | 25.0% | 21.7% | 0.83 | 1.7% | 1.00 |
| 2 (hint) | 60 | 21.7% | 11.7% | 0.22 | 6.7% | 0.12 |
| 3 (hint, larger) | 129 | 32.6% | 20.9% | **0.049** | **7.0%** | **0.0034** |
| **pooled 2+3** | **189** | **29.1%** | **18.0%** | **0.015** | **6.9%** | **0.0002** |

**The tool is adopted and re-pasting falls by ~38% relative.** Against the true
production baseline of 40.6% that projects to roughly 25%, which is the
pre-registered target. That is a projection from a reconstructed setting, not a
production measurement.

### The hint, not the tool, appears to be load-bearing

Run 1 shipped the identical tool schema and measured 1.7% adoption. The only
change in run 2 was one line added to the `run_python` RESULT, and adoption went
to 6.7%. This is the same shape as `update_artifact`'s unified-diff mode sitting
at 2%: the capability existed, nothing pointed at it where the model decides.
**Applying the same treatment to `update_artifact` is the obvious next
experiment**, and it is cheaper than any format change.

### What it does not fix

Adoption is 7%, not 70%, and **20.9% of after-arm cases still re-paste** with the
tool and the hint both available. This is a dent, not a fix. By the escalation
rule above, 7% is below the 20% threshold that would trigger the similarity
gate. Recommendation is to hold the gate until two weeks of production data show
whether the projected 40.6% -> ~25% holds, because the gate turns a legitimate
near-identical re-run into a hard error and that is a real cost to pay on a
projection.

### Baseline correction

The 56% re-paste figure used while scoping was inflated: it counted only
`run_python` -> `run_python` pairs and dropped every case where the model did
something else next. Over the full population of 399 decision points the real
rate is **40.6%**. Success criteria should be read against that.
