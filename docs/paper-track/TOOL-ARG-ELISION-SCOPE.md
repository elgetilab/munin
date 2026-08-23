# Scope: elide tool-call ARGUMENTS to fit the context window

Status: **PLAN, not implemented.** Follow-on to
[`done/CONTEXT-BUDGET-FIX-SCOPE.md`](done/CONTEXT-BUDGET-FIX-SCOPE.md) and its
2026-08-12 reopening (commit `0c178d7`, deployed 2026-08-13).

## Why

The 2026-08 fix made the budget honest: the tools schema and tool-call
arguments are now counted, so Tier 1 clamps correctly and Tier 2 can fire.
Replaying the 12 Aug 11-12 overflow turns through the deployed code:

| | turns exceeding the 65536 window |
|---|---|
| pre-fix code | **12 / 12** (reproduces the outage) |
| deployed code | **1 / 12** at face value, **2 / 12** once reconstruction error is allowed for |

The replay reconstructs each turn's in-loop message list (the live one is never
persisted), calibrated against the 5 turns where vLLM reported a MEASURED
prompt size; mean reconstruction error 15.5%. Direction is unambiguous,
absolute counts carry roughly 15-25% error.

**The residual cases are structural, not a budgeting bug.** Tier 2 elides tool
RESULTS, and results are only a small share of these prompts:

| component | across the 12 failing turns |
|---|---|
| tool-call **arguments** | 18,731 - 38,989 tokens |
| history | 882 - 19,728 |
| tool **results** (all Tier 2 can touch) | **19.2% of the prompt, mean** |

Elidable share predicts the failures exactly: the two that still overflow are
the two lowest, at **1.4%** and **8.0%**. The 1.4% case is a 19,532-token user
paste plus 33,979 tokens of `run_python` arguments against 953 tokens of
results. There is nothing to reclaim.

## What is worth eliding

Argument weight since 2026-07-01 is concentrated in four content-carrying
tools, which are **94.4%** of all argument tokens:

| tool | calls | median | p90 | max | total |
|---|---|---|---|---|---|
| `run_python` | 453 | 1,608 | 4,450 | 18,018 | 923,517 |
| `create_artifact` | 181 | 2,189 | 6,468 | 11,660 | 463,042 |
| `compile_latex` | 97 | 2,285 | 5,944 | 17,019 | 241,046 |
| `update_artifact` | 66 | 1,974 | 5,122 | 7,499 | 155,193 |

Everything else is query-shaped and negligible (`source` median 82, `web_fetch`
70, `web_search` 37, `search` 31). Those must be left alone: they are tiny, and
their arguments are the record of what was searched.

Upper-bound estimate, eliding prior-iteration heavy arguments only:

| | turns that fit |
|---|---|
| results-only elision (today) | 10-11 / 12 |
| plus argument elision | **12 / 12** |

Reclaim ranges 14,663 to 36,936 tokens per turn.

## THE ASSUMPTION THIS ALL RESTS ON

The upper bound assumes the heavy calls span multiple loop iterations, so that
all but the pending batch count as "prior" and are safe to elide.

**This is not verifiable from the database.** `chat_store` aggregates a turn's
tool_calls into one assistant row, so iteration boundaries are lost. The live
loop appends one assistant message per iteration, but that structure exists
only in memory.

Circumstantial support: turn budgets are 10 (chat/code) and 24 (research), and
the failing turns carry 5 to 24 calls, so several iterations are near-certain
for the high-count ones. The 5-call case shows 4 results of 22 tokens each,
consistent with failed runs retried across separate iterations. Neither is
proof.

**Resolve this before building.** Cheapest path: log the loop iteration index
alongside each tool call for a week and read it back. If the heavy calls turn
out to arrive in ONE batch, prior-iteration elision reclaims nothing and this
plan needs rethinking (the fallback would be eliding within the pending batch,
which is a much riskier change).

## Design

Extend `chat_context.budget_tool_results`, or add a sibling that shares its
two-pass structure and its contract:

- **Never mutate the persisted conversation.** Only the copy sent to vLLM.
  Full arguments stay in `chat_store`, so the user and any replay still see them.
- **Never touch the pending batch** (calls after the last assistant message)
  except as a documented last resort, mirroring the existing result behaviour.
- **Idempotent**, so a second pass over an already-elided list is a no-op.
- **Preserve pairing**: keep `id`, `type` and `function.name` exactly. Only the
  `arguments` payload shrinks.

**Preserve the argument SHAPE, do not stub the whole thing.** `arguments` is a
JSON-encoded string that the qwen3 tool parser and chat template both read.
Replacing it wholesale risks a parse or validation failure. Truncate the large
string VALUES instead and keep the keys:

    {"code": "<elided: 18018 tokens; re-issue the call if you need the source>"}

This is the single highest-risk detail in the plan and it must be verified
against live vLLM, not just unit tests.

## Decisions needed

1. **Ordering: arguments before results, or after?** They are not equally
   valuable. For `run_python` the RESULT is the information and the source is
   the model's own draft, so eliding arguments first preserves more useful
   context. For `create_artifact` the argument IS the deliverable, though it is
   independently persisted in `artifact_store` and recoverable. My
   recommendation: arguments first for `run_python` / `compile_latex`, results
   first for `create_artifact` / `update_artifact`. This needs a call.
2. **Do we accept the model losing sight of code it wrote mid-turn?** It can
   re-issue the call, but it may instead repeat work or contradict itself. The
   existing result elision already accepts the analogous trade.
3. **Threshold**: elide only arguments above some size (say 1,000 tokens), so
   small calls keep their exact payload and the diff stays legible.

## Test plan

- Unit, mirroring `test_chat_context_fit.py`: pairing preserved, idempotent,
  input not mutated, pending batch protected, only the four heavy tools
  affected, ordering policy honoured, threshold respected.
- **Live vLLM probe**: send one request carrying an elided-argument assistant
  message and confirm it is accepted and the tool parser stays happy. This is
  the risk in item "preserve the argument SHAPE" above and cannot be unit-tested.
- Replay the same 12 Aug 11-12 turns and confirm 12/12 fit, including under the
  10-25% inflation used in the sensitivity check.
- Regression: the existing 28 cases in `test_chat_context_fit.py` stay green.

## Out of scope

- The 19,532-token user paste in the 1.4% case. Trimming or summarising a large
  user message is a separate concern with its own UX consequences.
- Capping `run_python` source size at the tool boundary. That is a harness or
  prompt fix, and it treats the cause rather than the symptom; worth its own
  discussion, since arguments averaging 27k tokens per failing turn is itself a
  behaviour worth questioning.
- Unifying `MAX_CONTEXT` (60000) with `MAX_MODEL_LEN` (65536), still outstanding
  from the previous scope.
