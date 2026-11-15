# Lab analysis bug fixes: plan

Status: PLAN 2026-11-15, waiting for "proceed". Bugs first, then the Phase 2
baseline (deferred work: [`LAB-DEFERRED.md`](LAB-DEFERRED.md)). Found by the
Phase 1 smoke run and the overflow check (`CONTEXT-OVERFLOW-PLAN.md`), then
investigated by four read-only agents on 2026-11-15. Key code claims below were
re-checked against the source.

## Findings

### F1. The model cannot see its earlier code (product, the main plot cause)

`assemble_context` replays earlier turns as `{role, content}` only
(`backend/retrieval/chat_context.py:903-911`); the assistant `tool_calls` that
hold the code are dropped (also noted at `chat_service.py:1744`). So each turn
rewrites the figure from memory: guessed column names (`activity` for
`activity_U_per_mg`), lost title and xlabel, figsize 8x5 to 8x6 to 10x6, bar
order changed, output file renamed. Over the six overflow-check S3 sessions
the model called `run_python` 464 times and `edit_python` 46 times;
`edit_python` patches the last source that ran (`mcp/tools/sandbox.py:94-140`),
often a small PIL size probe, and the model edits it blind.

### F2. The figure hook gives false layout warnings (product)

- Clipped text: every tick label is checked, including ticks outside the axis
  limits that matplotlib never draws (`backend/sandbox/app/fighook.py:290-306`).
  Flagged '90' with ylim 87.7, '100', log-axis labels.
- Overlapping text: boxes are measured at `fig.dpi`, not the saved dpi
  (`fighook.py:326-335`); `savefig(dpi=150)` on a 100 dpi figure reproduces the
  turn-1 warnings exactly.
- The model chases these for up to 20 tool calls per turn (s3_bars#1: figure
  grew from 203 to 355 mm, byte-identical reruns).

### F3. Sizes (product plus model)

- 85 mm requested, 94.7 mm PDF: correct `figsize`, but `bbox_inches='tight'`
  grew the page. 156.7 mm: tight bbox after the size was lost (F1).
- `describe()` (`fighook.py:337-347`) reports `fig.get_size_inches()`, not the
  saved file's size, so the model was told 85 mm about a 94.7 mm file.
- Nothing in the code persona or tool descriptions covers keeping unchanged
  properties, sizes in mm, or tight bbox with a fixed size.

### F4. Seaborn error bars are invisible to the figure hook (product)

Only `ax.containers` yield bar/errorbar series. Seaborn draws error bars as
plain 2-point `Line2D`s (no gid, no tag), and lineplot bands as a
`PolyCollection` without bounds. So the manifest has no `yerr` and `s3_bars`
turn 1 cannot pass when the model uses seaborn. A draft patch (stem detection
matched to bar centres and tips; pointplot; bands; empty legend proxies
skipped) was reproduced in `munin-sandbox:latest` (matplotlib 3.9.2, seaborn
0.13.2): SD values match `truth.json`, `plt.bar(yerr=)` unchanged, `axvline`
at a bar centre stays a line. There are no `fighook` unit tests today.

### F5. Stray `<answer>` tag (model habit, our missing guard)

No prompt or chat template asks for it; Sage-A1.5 emits it and nothing strips
it. Chain in s3_bars: turn 5 hit the tool cap, the wrap-up reply was five
copies of an `<answer>` block, it was stored as is, turn 6 copied it, turn 7
wrote three `<answer>` "I'll remove KO2..." blocks in 2.1 s. The prose-action
recovery missed it because `_looks_like_prose_action_promise`
(`chat_service.py:703`) only matches "i'll" at the start or after a space,
not after `<answer>\n`. Repetition is the model looping, not our code. In
production: 1 of 58 Sage-era assistant replies; 4 of 18 smoke turns.

### F6. Next run silently empty after a timeout (product race)

On timeout the kernel gets SIGINT, then `_flush_remaining` returns after 0.2 s
of silence (`kernel.py:380-386`) without waiting for the old request's idle.
If the interrupt takes longer (slow BLAS call, `finally` cleanup, loaded host),
the next `execute_request` is queued, ipykernel aborts it because the previous
one errored (`stop_on_error`), and `_drain_iopub` reads its idle as success:
stdout "", error None. Reproduced in throwaway containers: 4/4 under
`--cpus 0.25`, 5/5 with a slow `except KeyboardInterrupt`, 3/3 with a large
matmul; 0/6 on an idle host. The flaky live check is this bug, not a bad test.
Users can hit it: the model then reasons from "no output".

### F7. Grader false positives (harness)

- KO2 removal: correct figure, but bars drawn one series each, and
  `approx_set` wants both values in one series (`graders.py:309-315`).
- `s3_dose` turn 2 requires the x axis to become log; two reps already used
  log in turn 1.
- Float noise counts as a change (exact equality, `graders.py:400`).
- A renamed output file reads as "no new figure" instead of a clear failure.
- Asymmetric error copies (`yerr_low/high`) are not deduped by `_canon_series`.

The title, legend and size changes are real misses, not grader errors.

## Fix

Ordered by deploy unit. Each step has its own tests and commit.

**Step 1: sandbox kernel race (F6).** In `kernel.py`: send
`stop_on_error=False` in `execute`; read the shell `execute_reply` and turn a
status `aborted` into an error (or one resend) instead of an empty success;
make `_flush_remaining` wait for the interrupted msg_id's idle (deadline about
10 s), else restart the kernel and say in the error that state was lost.
Test: a deterministic case in `test_artifacts_live.py` (slow
`except KeyboardInterrupt`, `timeout_s=2`, then `print(1+1)` gives "2" with
`error is None`), run 10 times under `--cpus 0.25`.

**Step 2: figure hook (F2, F3, F4).** In `fighook.py`: skip tick labels
outside the view limits; measure text at the saved dpi and against the saved
bbox when `bbox_inches='tight'`; report the saved file's real size and dpi
(`size_mm_saved`, and flag when it differs from the figure's size); seaborn
error bars, pointplot and bands as in F4. New `backend/sandbox/tests/test_fighook.py`
(the agent's list: sd/se/ci bars, hue, horizontal, capsize, `errorbar=None`,
pointplot, band, unrelated fill, `axvline`, `plt.bar(yerr=)` regression, the
false clipped/overlap cases, tight-bbox size). Steps 1-2 ship as one sandbox
deploy.

**Step 3: `<answer>` and looping replies (F5).** In `chat_service.py`:
`strip_answer_tags` (outside code fences; also collapses consecutive identical
paragraphs) on `final_content` before saving, on the wrap-up path, and on the
live stream (hold back a `<`-prefixed partial token); apply it to stored
assistant history in `assemble_context`. Prose-action detector: strip tags and
leading markup, word-boundary opener match. A reply with repeated paragraphs
and no tool call counts as a failed step: retry once with tools required
(loop) or with a frequency penalty (wrap-up). Wrap-up prompt: "Reply in plain
Markdown, no XML tags". Count stripped tags in the log. Tests with the exact
turn-5 and turn-7 strings and a code-fence negative.

**Step 4: keep the current script across turns (F1).** The sandbox already
records which run produced which artifact. Expose per conversation the latest
successful source for each output file (the run that last wrote it). At turn
start, when the conversation has a sandbox session, `assemble_context` adds a
short block after the system prompt: "Current script for activity.png (run 37):
<source>", newest output files first, capped (Q3), counted in the turn budget.
`edit_python` gets an optional `file=` that targets that script instead of the
last run. Tests: block present and capped, budget respected, `edit_python
file=` patches the right source.

**Step 5: prompt rules (F1, F3).** Code persona (`shared/personas/code.json`)
and the `run_python` / `edit_python` descriptions: change only what was asked;
keep file name, title, colours, order and size unless asked; start from the
current script; sizes in mm via `figsize` with `constrained_layout`, never
`bbox_inches='tight'` when a size is fixed; check the saved size reported by
the hook; at most two layout fix attempts.

**Step 6: grader fixes (F7).** Bar values matched across series by xtick
label; floats compared with rtol in `manifest_diff`; `must_change` satisfied
when the value already matches the request; explicit "output file renamed"
failure; `_canon_series` dedupes asymmetric copies. Tests in
`test_labanalysis.py` with the observed manifests.

**Step 7: verify.** Hermetic retrieval suite, sandbox live suite (10 runs of
the timeout check under CPU limit), lab unit tests. Then you deploy sandbox and
retrieval, and I run `s3_bars` and `s3_dose` x3 plus the smoke set and compare
with the overflow-check numbers (mean 0.676). Expect: no `<answer>` in any
reply, no false layout warnings, sizes within 1 mm, far fewer unrequested
edits. Then the Phase 2 baseline.

## Open questions (defaults taken unless you say otherwise)

| # | Question | Default |
|---|---|---|
| Q1 | Seaborn detection: a user's own vertical line exactly through a bar centre and across its top reads as that bar's error bar. Require every bar in a group to match instead? | Accept the rare false positive (the strict rule drops errors for whole plots with an n=1 group). |
| Q2 | A `save_figure(path, width_mm, height_mm, dpi)` helper in the sandbox now? | No. It would change the ad hoc arm of the Phase 2 baseline; it belongs in `labkit`. Prompt rules plus the hook's real saved size cover it for now. |
| Q3 | Step 4 scope: which turns get the current-script block, how big? | Any persona whose conversation has a sandbox session; up to 3 output files, 6,000 tokens total, oldest dropped first, and the block is the first thing trimmed near the window. |
| Q4 | Retry a repeated, tool-less reply? | Yes, once; otherwise keep the stripped text. |
| Q5 | Re-run the earlier lab scorecards after the fix? | Only the S3 cases and the smoke set (Step 7); the smoke scorecard stays as is ("not a result"). |
| Q6 | Deploy | Sandbox (Steps 1-2) and retrieval (Steps 3-5) via `deploy.sh`, you run them; Step 6 is harness only. |

## Not in this plan

- The tool-call cap (20 per turn) and over-tooling in general: Step 2 removes
  the main source of pointless reruns; revisit with Step 7 numbers.
- Sandbox logging of kernel aborts and timeouts (no production count for F6
  exists today); worth a follow-up.
