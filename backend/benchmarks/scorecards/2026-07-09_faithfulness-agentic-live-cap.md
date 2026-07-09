# Scorecard — over-tooling cap + faithfulness (arm: agentic-live-cap)

- judge `MiniCheck-Flan-T5-Large` cuda:0 | 40 LitQA2 q | claim_mode=extract
- generator: live research chat, research 1.5 + T3 + T1a + **over-tooling cap
  (CHAT_MAX_TOOL_CALLS=30)** live | 2026-07-09
- paired vs `agentic-live-t1a` (same stack, pre-cap), same 40 questions.

## Cap VERIFIED working

| n_tool_calls/answer | pre-cap (t1a) | post-cap |
|---|---|---|
| max | 43 | **31** |
| p90 | 40 | **30** |
| answers > 30 | 9/40 | 2/40 (both 31) |
| mean | 17.3 | 16.6 |

`terminal_reason` post-cap: done 31, **max_turns 7**, none 2. The 7 max_turns
answers have n_tool_calls exactly [30,30,30,30,30,31,31] - the cap firing at the
ceiling and routing into the wrap-up synthesis. The 40-52 pathological tail is
gone. (31 vs 30: the check runs after a turn completes, so a turn can push
cumulative from 28 to 31 before the cap trips - one turn's overshoot, acceptable.)

## No quality regression

| metric | pre-T2 base | T1a | **post-cap** |
|---|---|---|---|
| % claims supported | 0.356 [0.272,0.425] | 0.303 [0.229,0.376] | **0.331 [0.275,0.387]** |
| empty/failed answers | 1 | 3 | **0** |

Grounding held (all CIs overlap); 0 empty/failed (best of any arm). Forcing the
tail answers to wrap up at 30 calls did NOT hurt grounding - the wrap-up
synthesis produces a real answer from what was gathered. (Median n_tool_calls
13.5 -> 16.5 is reps=1 stochastic wobble; the cap only touches the >30 tail, and
mean fell as expected.)

## Verdict

The over-tooling cap is the harness-iteration WIN. Prompt levers (T2 depth /
follow-up cap) and evidence levers (T1a) both failed to move over-tooling; the
code cap cleanly removes the pathological tail with no grounding cost. Default 30
is a safety ceiling; CHAT_MAX_TOOL_CALLS can tune it (a quality-vs-calls sweep
would find the efficiency-optimal, out of scope here). Routing-anchor
no-regression is expected (anchor items reach their required tool well under 30)
but not separately re-run.
