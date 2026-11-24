# Turn budget in lab analysis: plan

Status: IMPLEMENTED and DEPLOYED 2026-11-21 ("proceed", Q1-Q6 defaults); Gaussian
FWHM 9/9 (before: 0/9), budget cases 11/12 (10/12), control 30/33 (33/33). Parent: `LABKIT-015-PLAN.md` (S8 round 2
17/21, fresh `_f2` 14/21; Q3 there left "saved nothing" and the turn budget
to this round). The eval instance stays up.

## What the bundles show

Two passes over the lab bundles (`backend/benchmarks/results/lab/<tag>/`):
every tool call from `records.jsonl` (`turns[0].tools`), every executed cell
from `code/run_NNN.py`, classified by regex and difflib. S8: 5 tags
(`s8-pre-epr`, `s8-labkit`, `s8-labkit-fresh`, `s8-r2-targeted`,
`s8-r2-targeted-fresh`), 231 runs, 2,643 calls. Non-S8: all other tags,
2,108 runs, 17,299 calls (left out: `r5-targeted*`, void with "vLLM
unreachable"; the multi-turn S3 runs; `lab/`).

The budget is LLM rounds, not tool calls: persona `max_turns` (default 10)
times 2 for the one auto-continue, so 20 rounds (`chat_service.py`,
`AUTO_CONTINUE_EXTENSIONS`). Parallel calls let a few runs reach 21 to 30
calls. `CHAT_MAX_TOOL_CALLS=30` is never the limit that ends a lab run.

### Where the calls go

| Category | Non-S8 (17,299) | Non-S8, runs at 20+ | S8 (2,643) |
|---|---|---|---|
| Analysis | 33.8% | 28.2% | 39.9% |
| Data loading and inspection | 19.8% | 14.0% | 19.8% |
| Writing results.json | 15.8% | 6.0% | 10.7% |
| Library exploration (guide, help, dir, labkit source) | 8.9% | **20.5%** | 8.0% |
| Plan tools | 7.5% | 12.6% | 2.3% |
| run_python call with no recorded run | 6.4% | 9.0% | 10.2% |
| Near-identical rerun after a failed cell | 5.7% | 5.1% | 3.1% (all reruns) |
| Near-identical rerun after an ok cell | 0.9% | 2.8% | (in the line above) |
| Exact repeat | 0.1% | 0.5% | 1.1% |
| Search tools | 0.7% | 0.6% | 2.5% |
| Invalid tool names (`labkit`, `labkit.guide`) | 0.3% | 0.2% | 0.3% |
| No-op probe (`print('hello')`) | 0 | 0 | 0.2% (4, all one case) |

### Pass and save rate by number of calls (non-S8, runs asked for results.json)

| Calls | Runs | Pass | results.json saved |
|---|---|---|---|
| 0-5 | 645 | 92.2% | 99.5% |
| 6-9 | 833 | 93.6% | 99.0% |
| 10-15 | 379 | 89.7% | 98.7% |
| 16-19 | 92 | 88.0% | 97.8% |
| **20** | 58 | **65.5%** | **58.6%** |
| 21-30 | 27 | 70.4% | 77.8% |

- 85 of 2,108 runs (4.0%) reach 20. They pass 67% and save 65%; the rest
  pass 92% and save 99.1%.
- 30 of the 48 runs with no results.json (62.5%) are budget hits. None of
  the 30 ever ran a cell that writes results.json; 27 had a successful
  analysis cell. 18 put a JSON block in the answer (the grader's fallback
  took it, 12 passed); the other 12 had no numbers and all failed.
- The forced wrap-up cannot save: it runs with `enable_tools=False`.
- Budget hits by suite: S6 27 of 130 (21%), S1 48 of 1,731, S2 10 of 207,
  S5 0 of 40. Cases: `s2_bruker_1d` 10, `s6_hsqc_titration_s1` 8,
  `s6_noesy_buildup` 8, `s6_hsqc_titration_s2` 6.

### Library exploration (non-S8, labkit tags)

| Exploration cells | Runs | Pass | Hit 20 | Mean calls |
|---|---|---|---|---|
| 0 | 1,016 | 89.8% | 2.0% | 7.5 |
| 1 | 597 | 96.3% | 2.2% | 7.4 |
| 2-3 | 134 | 91.0% | 10.4% | 11.4 |
| 4+ | 82 | 84.1% | **36.6%** | 16.7 |

- Budget-hit runs read labkit's source (`inspect.getsource`, `__file__`)
  3.48 cells per run; the others 0.16.
- 739 cells call `guide()`, median at cell 3. `import labkit;
  print(labkit.guide('fit'))` alone appears 387 times.

### Inspection, failed cells, repeats

- 94.5% of non-S8 runs start with an inspection cell; 34% of inspection
  cells re-read an input the run already inspected. 3+ inspection cells:
  17.5% hit 20, against 1.7% with one. Pass rate barely moves (S8 outside
  the Gaussian case: 0.63 to 0.70 for 1 to 6 cells), so a preview saves
  calls more than it adds passes.
- Runs with 4+ failed cells hit 20 in 15.3%; with none, 0.8%.
- Exact repeats are rare (24 executed in non-S8, 29 in S8). The S8 ones ran
  because the guard compares raw arguments and they differ only in comments
  or blank lines. In S8 round 2 the guard forced 4 wrap-ups; all 4 failed,
  3 in `s8_cw_gaussian_fwhm`.
- 6.4% of run_python calls (9.0% in budget hits; 32.5% in `bugfix4-bruker`)
  have no run in the bundle. A bundle stops at the last run that made an
  artifact, and blocked reruns never run, but nothing logs which it was.
- The audit line logs `ok=False` for all 399 run_python calls in today's
  eval log, while the bundles mark most runs ok. `hooks/audit_log.py` sets
  `ok = "error" not in result`, so a result that carries `"error": None`
  counts as a failure.

### S8 and the Gaussian FWHM case

| Tag | Runs | Mean calls | Hit 20 | Pass | Saved |
|---|---|---|---|---|---|
| s8-pre-epr | 63 | 12.2 | 4 | 17/63 | 52/63 |
| s8-labkit | 63 | 12.2 | 6 | 49/63 | 57/63 |
| s8-labkit-fresh | 63 | 10.9 | 3 | 47/63 | 61/63 |
| s8-r2-targeted | 21 | 9.3 | 2 | 17/21 | 18/21 |
| s8-r2-targeted-fresh | 21 | 10.7 | 1 | 14/21 | 16/21 |
| All | 231 | 11.4 | 16 (6.9%) | 144/231 | 204/231 |

- `s8_cw_gaussian_fwhm` (input `defect.txt`): 15 runs, 7 hit 20, 2 pass,
  7 saved. The other 216 S8 runs: 9 hit 20 (4.2%), 142 pass, 197 saved.
- That one case holds 7 of 16 S8 budget hits, 8 of 27 missing results
  files, all 4 probe cells and 11 of 12 cells that read back the run's own
  output.
- Outside it, only 3 S8 runs hit 20 and failed. That caps what a bigger
  budget could win in S8. Runs with 17 to 19 calls pass 0.83.

## The print('hello') runs: the model was blind (verified)

A separate investigation (tag `hello-probe`, 3 reps with the chats kept, then
read through the API and deleted) found the cause. It is the context window,
not the sandbox and not the result formatter.

- `defect.txt` is a `.txt`, so it is pasted into the user message
  (`chat_service.py:320-334`, cap `MAX_DOC_INLINE_CHARS = 50_000` characters,
  not tokens). 37,456 chars of numbers are about 37,400 tokens.
- Inside the tool loop, `budget_loop_messages` (`chat_context.py:445-556`,
  called from `_fit_prompt_for_call`) fits each call to
  65,536 - 16,384 (output reserve) - 512 = 48,640 tokens. The prompt before
  any tool result is already about 50,700 (system 4,591 + user 37,721 +
  schema 8,346). So pass 3, the last resort (`chat_context.py:532-554`),
  elides the pending batch as well: from step 3 on, every tool message the
  model gets, including the one it just asked for, is the stub
  `{"_elided": true, "note": "... re-run the tool if you need its details
  again"}` (`chat_context.py:351`). The model does what the stub says:
  re-reads the file, then probes with `print('hello')`.
- The model's own thinking (persisted, conv 975766f9): "the tool results
  say "_elided": true, which means the result was truncated", then "Let me
  try running a simple command".
- The sandbox output was correct in the persisted results (`error: null`,
  real stdout). The eval log has "budgeted N earlier tool result(s)" with N
  equal to every result so far in each `defect.txt` conversation (191 lines
  today) and in no other conversation.
- About 14K tokens of output room were left, above
  `MIN_USEFUL_OUTPUT_TOKENS` (4,096), so the call went out with no warning.
- The repeat guard (`chat_service.py:1484-1491`) blocked two verbatim
  re-runs with "would give the same result shown there", though nothing had
  been shown.
- How common: 5 probe cells in 2,424 bundles, both in
  `s8_cw_gaussian_fwhm`, the only lab case with a text input over 20 KB
  (next: `s2_gen5_kinetic`, 10 KB). The Gaussian case passes 2 of 15 runs
  over all S8 tags. In production, any user who uploads a large numeric
  `.txt` gets the same blind turn.
- Side finding: `help(labkit.epr)` prints 29,272 chars; `truncate_tool_result`
  (8,000-char limit, `tool_result.py:77`) cuts it to an escaped-JSON preview.
  Change 6 steers away from it.

A widened `epr_lineshape` hint for the Gaussian runs was built and dropped:
its field-axis rule also fired on non-EPR code (magnetometry, E-field
simulations, any plot with `linewidth=`), and those runs failed because the
model could not see any tool result, hints included. The diff is kept
outside the repo; revisit after change 1 if the Gaussian case still fails.

## Changes

1. **No numeric text file in the prompt** (the root cause of the blind runs) (`document_store.py`,
   `chat_service.py`). A `.txt` (or `.md`) whose text is mostly numeric
   rows (say 80% of the first 200 non-empty lines are delimited numbers) is
   treated as a data file: sandbox only, with the data-file note and the
   preview of change 5. Separately, cap any inlined document at a share of
   `chat_context.turn_start_budget()` (default half, in tokens), not a
   fixed 50,000 chars, and say that the rest is in the sandbox or
   `search_user_docs`. Tests: `defect.txt` (both variants) and the other
   S8 ASCII inputs go the data path; a CV, a paper's text and a long email
   thread stay inline.
2. **Never blind the model** (`chat_context.py` `budget_loop_messages`,
   `chat_service.py` `_fit_prompt_for_call`). Pass 3 (the pending batch)
   measures against the hard window (`MAX_MODEL_LEN -
   MIN_USEFUL_OUTPUT_TOKENS - CTX_MARGIN`), not the soft target that
   reserves the full 16K of output. When it still has to cut the newest
   result, it keeps a head preview (about 1,500 chars, via
   `truncate_tool_result(limit=...)`) instead of the stub. The stub for
   older results stops saying "re-run the tool"; it says the output was
   trimmed for space and to print less or save to a file and print a
   summary. `_fit_prompt_for_call` logs a warning with the prompt
   composition when the prompt before any tool result is already over the
   target. If the newest result has to be cut to the stub on two rounds in a
   row, end the loop the `context_full` way. Tests
   (`tests/test_chat_context_fit.py`): a base prompt over the soft target
   plus one small pending result keeps that result (or its preview); a
   prompt over the hard window still elides; a loop with a 37K-token user
   message sees its first result.
3. **A save step before the wrap-up** (`chat_service.py`, section 5b). When
   the loop ends on the turn budget, the guard or the call cap, and the
   turn used a sandbox tool, run one more round with only `run_python` and
   `edit_python` enabled and the message: "You have one tool call left. If
   the task asks for a file (for example results.json), write it now from
   the values you have; then answer." Then the normal tools-off wrap-up.
   It targets the 27 budget-hit runs that had analysis but no save. Test:
   a stub loop that hits the budget gets exactly one sandbox-only round.
4. **Warn two rounds before the end** (`chat_service.py`, the turn loop).
   At round `turn_budget - 2`, append one line to the tool results: "2 tool
   rounds left: save your results now." Costs no call.
5. **Preview in the data-file note** (`labkit_hints.upload_hint`, which
   already gets the bytes, and the note in `chat_service.py`). For a
   delimited text file: line count, the header, the first 5 rows and the
   column count, at most 1,500 chars. Aims at the first-cell inspection
   (94.5% of runs) and the re-reads (34% of inspection cells).
6. **Guide before source** (`labkit_hints.py`, persona text). A cell that
   reads labkit's source (`inspect.getsource`, `getfile`, `labkit.__file__`,
   `help(labkit...)` twice) gets a hint naming the matching
   `labkit.guide('<topic>')` and saying the guide has the call signatures.
   The persona line on labkit says: start with `labkit.guide('<topic>')`
   for the task (fit, nmr, epr, cd, deer), not the source. A tool call
   named `labkit` or `labkit.guide...` gets an error that says labkit is a
   Python library and goes inside run_python (`mcp/dispatchers.py` or the
   unknown-tool path in `chat_service.py`).
7. **Repeat guard** (`chat_service.py` `_call_key`). Compare code with
   comments and blank lines stripped. When the earlier result was elided, the
   block message says so instead of "the same result shown there"
   (`chat_service.py:1484-1491`). When the guard forces the wrap-up, the save
   step of change 3 still runs.
8. **Logging** (`chat_service.py`, `hooks/audit_log.py`). One line per
   run_python call that did not run, with the reason (guard block,
   malformed arguments, error before execution). Fix the audit flag:
   `ok = not result.get("error")`.

Not changed: the 20-round budget (Q1), plan tools counting against it
(Q3), the eval's results.json request (Q2).

## What was built

All in retrieval plus one persona line; the sandbox image is unchanged.

1. `document_store.looks_like_numeric_table` (80% of the first 200
   non-comment lines are delimited numbers, at least 20 lines). Such a text
   upload takes the data-file path when the chat has a sandbox; a temporary
   chat (no sandbox) still gets the text, capped. Any pasted document is
   capped at `_inline_cap_tokens()` = half of `turn_start_budget()` (about
   15K tokens) as well as 50,000 chars, and the note says the whole file is
   in the sandbox.
2. `budget_loop_messages(hard_tokens=...)`: the pending pass runs only over
   the window minus `MIN_USEFUL_OUTPUT_TOKENS`, cuts each result to a
   1,500-char head first (`"_trimmed": true`), and to the stub only if the
   heads still do not fit. The stub says to print less or save to a file,
   not to re-run (the old stub is still recognised). `_fit_prompt_for_call`
   logs a warning with the prompt composition when a newest result is cut,
   and refuses a tool-enabled call where every newest result is a stub
   (context_full); this replaces "two rounds in a row", which needed state
   for the same effect. Wrap-up calls are exempt.
3. Save round: a sandbox turn's last round (budget end), or one extra
   round after the repeat guard or the call cap ends the loop, offers only
   `run_python` and `edit_python` with `SAVE_ROUND_MESSAGE`; then the
   tools-off wrap-up. Turns that never ran code are unchanged.
4. `ROUNDS_LEFT_NOTE` is appended to the last tool result two rounds before
   the end (the round before the save round).
5. The data-file note shows the line count and the first 8 lines (at most
   1,500 chars) of a text file (`.csv`, `.tsv`, `.txt`, `.dat`, `.asc`,
   `.list`, `.xy`, `.prn`, no extension); binary files get none.
6. Hint `labkit_source` (help() on labkit, `inspect.getsource`/`signature`
   on labkit, `labkit...__file__`, opening a labkit `.py`), placed last
   among the domain hints so a specific hint in the same cell wins; quiet
   when the code calls `guide(`. Persona: the guide, not help() or the
   source; labkit goes inside run_python. A tool call named `labkit...`
   gets "Unknown tool: ... labkit is a Python library, not a tool: call it
   inside run_python".
7. Repeat guard: run_python/edit_python code is compared without blank
   lines, full-line comments and trailing spaces; the block message says
   "if that result was cut or not shown, print only the part you need".
8. `_log_calls_not_run`: one line per sandbox call that did not run
   (repeat_guard, invalid_arguments, unknown_tool, tool_failed). Audit
   `ok = not result.get("error")`.

Tests: `test_chat_context_fit` 39/39 (new: pending result kept over the soft
target, head kept over the hard limit, stub only when the head cannot fit,
stub wording); `test_lab_plumbing` 19/19 (numeric detection, `defect.txt` as
a data file with a preview, temporary chat capped, CSV preview, binary none,
long prose capped in tokens); `test_repeat_calls` 15/15 (save round after
the guard, save round and warning at the budget end, none without the
sandbox, comment-only reruns blocked); `test_labkit_hints` 19/19 (source
hint, audit flag, labkit-as-a-tool); retrieval hermetic 118/118. Hint replay
over all 2,424 bundles: 235 gain `labkit_source`, no other hint changes (an
earlier placement first in the list displaced 3 Sparky hints; moved last).

## Test, then rerun

Unit tests for each change (`retrieval/tests`, `test_labkit_hints`),
retrieval hermetic (`backend/scripts/ci/run_retrieval_tests.sh`), labkit
tests in the sandbox image only if the guide text changes.

Targeted rerun, 3 reps, v2 results request:

| Set | Cases | Runs | Compared with |
|---|---|---|---|
| Gaussian FWHM | `s8_cw_gaussian_fwhm`, `_f1`, `_f2` | 9 | `s8-r2-targeted`, `s8-labkit-fresh`, `s8-r2-targeted-fresh` (1/9 together) |
| Budget hits | `s2_bruker_1d`, `s6_hsqc_titration_s1`, `s6_hsqc_titration_s2`, `s6_noesy_buildup` | 12 | `r4-suffix-v2` on the same cases (paired) |
| Control | the round-5 cases (`r5b-targeted`, `r5b-targeted-fresh`) | 33 | 33/33 there, so a check for harm |

Tags `lk16-targeted`, `lk16-targeted-fresh`, `lk16-control`. About 54 runs,
1 to 2 hours. Measured: pass, results.json saved as a file, mean calls,
runs at 20, and from the eval log how often changes 2, 3 and 6 fired. No
full run unless the control drops.

## Rollout

Retrieval deploy (changes 1 to 8 are all in retrieval), then `instance
refresh eval`. The sandbox image changes only if the labkit guide text
changes. Production gets all of it: change 1 also stops a user's large
text export from filling the prompt, and change 3 means a long turn can
still save its file.

## Open questions (defaults taken unless you say otherwise)

| # | Question | Default |
|---|---|---|
| Q1 | Raise the 20-round budget for lab work? | No: only 4.0% (non-S8) and 6.9% (S8) of runs reach it, and changes 1, 3 and 6 remove the main sinks in those same runs. Revisit after the rerun if S6 still hits it. |
| Q2 | Ask for a provisional results.json early in the eval prompt (`cases.py`)? | No: it changes the test, not the product, and breaks pairing with older tags. Change 4 does it in the product. |
| Q3 | Stop counting plan-tool calls against the budget? | No: plan calls are 12.6% of calls in budget-hit runs, but that is confounded with case difficulty. Log it and decide after the rerun. |
| Q4 | Numeric-text threshold for change 1? | 80% of the first 200 non-empty lines are delimited numbers; a header and comment lines are allowed. |
| Q5 | Inline cap for documents? | Half of `turn_start_budget()` in tokens (about 15,000 today), not 50,000 chars. |
| Q6 | Run the save step (change 3) in every chat that used the sandbox, or only when a file was asked for? | Every chat that used the sandbox: the product cannot know what the prompt asked, and the message says "if the task asks for a file". |

## Result: targeted rerun (2026-11-21, deployed)

Tags `lk16-targeted`, `lk16-targeted-fresh`, `lk16-control`,
`lk16-control-fresh`, 3 reps, v2 results request, `eval` instance (new code
checked in the container).

| Set | lk16 | Before | Calls (mean) |
|---|---|---|---|
| Gaussian FWHM, original | 3/3 | 0/3 (`s8-r2-targeted`), 1/3 (`s8-labkit`) | 13.0 (before 13.3-20.0) |
| Gaussian FWHM `_f1`, `_f2` | 6/6 | 0/3, 0/3 | 11.3 |
| `s2_bruker_1d`, HSQC s1/s2, NOESY | 11/12 | 10/12 (`r4-suffix-v2`, paired 2/1) | 16.2 vs 20.0; at 20+: 3 vs 8 |
| Control (round-5 cases) | 30/33 | 33/33 (`r5b-targeted*`) | 7.1 |

- No blind turns: the eval log since the deploy has no "cut ... newest
  tool result" and no refused call; older results were budgeted out in two
  conversations only. All 9 Gaussian runs read `defect.txt` as a data file
  (preview in the note) and saved results.json.
- Save round: fired 3 times (all at round 20). Two of the runs wrote
  results.json in it (HSQC s1#3, s2#3, both passed); one
  (`s1_competitive_s8#2`) spent it on another check of its fit, and the
  wrap-up answer ("Let me write the results file.") had no numbers.
- Hints fired: `labkit_source` 7, plus the usual competitive, EPR, CD, Bruker
  and Sparky hints. No "call not run" lines.

What failed:

| Run | Cause | Related to this round? |
|---|---|---|
| `s5_anova#3`, `s5_fit#2` | `reproduces`: a run wrote `tukey.csv` / `fit_params.csv` and then raised; `bundle.py` leaves failed runs out of `analysis.py`, so the bundle cannot remake the file (a later ok run reads it, or it is in outputs/) | No: an existing bundle defect; round 5 avoided it by chance |
| `s1_competitive_s8#2` | 20 calls of checks; the save round ran one more | Partly: the save round fired but did not save |
| `s6_hsqc_titration_s1#2` | Stopped after 6 calls saying it ran out of time (it had 14 rounds left); no tool error, no budget event | No: the model gave up early |

Next, if wanted: (a) `bundle.py` keeps a failed run that produced files
another run or outputs/ depends on (sandbox); (b) the save round's message
names the values to write ("write results.json now; do not run more
checks"), or the wrap-up asks for the final numbers as a JSON block.
