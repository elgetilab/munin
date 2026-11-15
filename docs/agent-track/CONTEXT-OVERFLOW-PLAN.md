# Context overflow in long tool sessions: plan

Status: Steps 0-4 IMPLEMENTED 2026-11-14 ("proceed") and deployed (retrieval
restarted 17:42Z). Step 5 live check done the same evening, plotting sections
only (scope cut by the user; routing anchor and T11 not re-run): `s3_bars` and
`s3_dose`, 3 reps each, all 39 turns completed, zero stored overflow replies,
and none of the budget log lines fired (no "budgeted", "near the context",
"context full" or vLLM "maximum context"). Turn 4 of `s3_bars`, which
overflowed before, ran 20 tool calls cleanly. The scores (0/6, mean 0.676) are
model misses (unrequested title/size/colour edits, bar order), not context
failures. So the new guards were not exercised; keep watching the logs. What
landed: `budget_loop_messages` (old results, then old bulk arguments, then
pending), `turn_start_budget` in `assemble_context` and the pre-compaction,
the no-send floor with `drop_history_to_fit` and the minimal wrap-up in
`_stream_vllm_once` and the tool loop, a readable context-full marker, a
composition log line ("prompt near the context window"), and the three new
knobs in docker-compose. Hermetic suite 110/110. Found by the lab-analysis
Phase 1 smoke run (`LAB-PHASE1-PLAN.md`). Predecessor:
`docs/paper-track/done/CONTEXT-BUDGET-FIX-SCOPE.md` (Tiers 1-3 and the
2026-08-12 reopening, which fixed the blind token count).

## What happened

`s3_bars`, an 8-turn plot-editing session (persona `munin`, routed to
`code`). Turn 4 died with vLLM 400 "maximum context length is 65536 tokens
... at least 65537 input tokens". Retrieval log for that call, in order:

1. `budgeted 11 earlier tool result(s) out of the prompt` (Tier 2 fired and
   elided every old tool result it is allowed to touch),
2. `prompt fills the context window (room=-522 < 256)` (our own count already
   knew the prompt alone was over the window),
3. the request was sent anyway with `max_tokens=256`, rejected, and the error
   shape is the derived one, so the refit ladder had nothing to work with,
4. the stored assistant reply is `_(stream interrupted: vLLM returned 400:
   {...raw JSON...})_`, which is what a user would see.

So this is not the blind-count bug of August (counting is now exact: the
mounted tokenizer is byte-identical to Sage's). It is what is left once the
count is right: **the prompt can exceed the window with nothing that the
existing guards are allowed to remove.**

## Why the guards run out of moves

What a loop iteration's prompt holds, and who may shrink it:

| part | shrunk by | today |
|---|---|---|
| system prompt + summary | nobody mid-turn | fixed |
| earlier turns (history) | `assemble_context`, once at turn start | budget `MAX_CONTEXT - GENERATION_RESERVE` = 60000 - 16384 = **43,616** tokens |
| tools schema | nobody | 5.6-7.3K per call, **not in `assemble_context`'s budget** |
| assistant `tool_calls` arguments (the code it writes) | **nobody** | grows every iteration |
| old tool results | Tier 2 | elided oldest first |
| pending tool results | Tier 2 last resort | elided largest first |

Two structural gaps:

- **G1. The history budget leaves no room for the turn.** History may take
  43.6K, the schema adds about 6K, and the output reserve is 16K: that is
  already 65.6K of a 65.5K window before the first tool call. The guard only
  works today because history is usually far below its cap.
- **G2. Tool-call arguments are never elided.** Tier 2 replaces old `role:
  tool` content but leaves every earlier `run_python` / `edit_python` source
  in place. Measured on stored turns since 2026-10-01 (sizes only): per-turn
  argument tokens p90 11,190 and max **37,666** (persona `chat`), p90 8,430
  (`code`), p90 1,930 (`research`). The smoke run's plot session made 87
  sandbox runs of 1.3-4.1K characters each, often re-running near-identical
  scripts.

Plus one product gap:

- **G3. When it does overflow, the user gets raw JSON.** The turn ends with
  the vLLM body pasted into the transcript and no way forward except retyping.

Frequency: 17 conversations have a stored overflow reply, all users, most in
June-August before the count fix; one on 2026-10-01; then the smoke run. Rare
in chat, but lab analysis is exactly the workload that makes it common: long
multi-turn sessions where every turn writes code.

## Fix

Ordered so each step is useful alone. Code in `chat_context.py` and
`chat_service.py`; tests in `retrieval/tests/test_chat_context_fit.py`.

**Step 0: measure (no behaviour change).** When `fit_max_tokens` hits the
floor or Tier 2 elides, log one line with the composition: system, history,
schema, assistant text, tool-call args, old results, pending results, image
blocks. Without it every later number is a guess. Then reproduce: `s3_bars`
with `--keep` on production at concurrency 1, read the line.

**Step 1: elide old tool-call arguments (G2).** Extend `budget_tool_results`
(renamed `budget_loop_messages`, old name kept as an alias) with a second
pass, after old results and before the pending batch: replace the
`arguments` of assistant `tool_calls` from earlier iterations with a stub
that keeps the call valid JSON for the chat template, for example
`{"_elided": true, "note": "source of this run trimmed to fit the context;
the variables and functions it defined are still live in the kernel"}`. Only
for tools whose argument is bulk payload (`run_python`, `edit_python`,
`create_artifact`, `update_artifact`, `compile_latex`); small arguments (a
search query) cost nothing and help the model, so they stay. Oldest first,
never the latest iteration, never the persisted copy. The kernel keeps its
state across runs, so dropping old source loses less than dropping old
results. The source itself is only kept in the sandbox run log (it becomes
`code/run_NNN.py` in the bundle), not as a file the kernel can read; see Q1.

**Step 2: give the turn its own budget (G1).** `assemble_context` budgets
history against `MAX_MODEL_LEN - GENERATION_RESERVE - schema_tokens -
TURN_HEADROOM` instead of `MAX_CONTEXT - GENERATION_RESERVE`. Default
`TURN_HEADROOM` 12,000 (about 20 iterations of median code after Step 1).
The schema is passed in by the caller, which already computes it.
`VLLM_MAX_CONTEXT` stays as an upper cap so nobody's override breaks.
Effect: long sessions compact a turn earlier (the existing summary path,
already visible to the user as the "earlier messages summarised" divider).

**Step 3: never send a request we know will be rejected (G1, G3).** If after
Steps 1-2 the prompt still leaves less than `MIN_OUTPUT_TOKENS`, do not call
vLLM. Instead, in order:
  a. mid-turn compaction: drop the oldest history messages from the copy sent
     to vLLM (the summary already covers them or is produced as at turn
     start), then re-fit;
  b. if still over, end the loop and run the existing wrap-up synthesis on a
     minimal prompt (system, summary, the user's message, the last
     assistant text and the latest results), so the user gets an answer from
     what was done, and the Continue affordance as for the tool-call cap.

**Step 4: a readable message for the rare case that still fails (G3).**
`apply_stream_error_marker` maps a context-length error to a plain sentence
("This conversation got too long for the model to continue in one step.
Press Continue, or start a new chat with the files.") and keeps the raw text
in the log only. Other stream errors keep today's marker.

**Step 5: verify.** Unit tests for each pass (args stub keeps valid JSON and
the tool_call_id pairing; latest iteration untouched; history budget with
schema; the no-send path). Then live: `s3_bars` and `s3_dose` 3 reps each on
production, expecting zero `prompt fills` warnings and zero stored overflow
replies, plus the routing anchor tier and T11 (tool reliability) to check the
model still finds its code. Grep `budgeted` and the new composition line in
the logs for a week.

## Open questions (defaults taken unless you say otherwise)

| # | Question | Default |
|---|---|---|
| Q1 | Elide old code arguments, or summarise them ("fit 4PL to activity.csv, saved fig")? Should the sandbox also write each run's source to a readable `runs/run_NNN.py` so the stub can point at it? | Elide, no summary (a model call per iteration, and it can be wrong). Writing `runs/` is a small sandbox change but needs a sandbox deploy; default no for now, add it if Step 5 shows the model re-deriving code it lost. |
| Q2 | `TURN_HEADROOM` size | 12,000, an env var. Revisit with the Step 0 numbers. |
| Q3 | Should the code persona get a tighter tool-call cap than 30 (the plot session hit 20 per turn, often re-running the same script)? | No, out of scope here. That is the over-tooling question; log it for the lab suite instead. |
| Q4 | Deploy | Retrieval only (`deploy.sh` retrieval, you run it); no model or vLLM change. |

## Not in this plan

- The stray repeated `<answer>` turn (`TODO.md` section 6). Possibly linked
  (it came three turns after the overflow), but not shown; Step 0's log will
  tell whether that turn was near the window.
- Raising `--max-model-len`. Sage's KV pool on one 5090 is the limit, and the
  failure above happens at any window size; it only moves.
- Re-running the earlier scorecards: Steps 1-2 change prompts only on turns
  near the window, which the suite's single-turn questions do not reach. T11
  and the routing anchor tier in Step 5 are the check.
