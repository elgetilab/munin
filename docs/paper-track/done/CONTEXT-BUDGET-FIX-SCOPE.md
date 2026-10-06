# Scope: deep_research vLLM 400 context-overflow (backend context budget)

Status: **ALL THREE TIERS IMPLEMENTED + DEPLOYED.** Tier 1 + Tier 3 (commit
1c670ee): deep_research 0.69 -> 0.88, zero ctx errors. Tier 2 (commit 266690b,
deployed 2026-06-29): deep_research holds at 0.938, zero ctx errors, no
regression; logic proven by unit + composed offline tests (a 50K-token fan-out
prompt -> elide old result -> output budget 14.7K -> 16.4K). Direct prod log
confirmation of a Tier-2 elision needs `sudo docker logs munin-retrieval | grep
budgeted` (the eval runner can't read the container logs). Surfaced by A5
paraphrase tier (3/16 deep_research paraphrases 400'd). NOT an A5 persona-tuning
item; a backend fix.

VERIFIED: after deploy, the deep_research paraphrase category re-ran with ZERO
context/vLLM errors (was 3); the three that 400'd (sota_phip__p09/p11/p14) all
pass; category mean 0.69 -> 0.875. The 2 residual misses are `set_plan` before
`deep_research` (benign planning), not overflow. Unit test:
retrieval/tests/test_chat_context_fit.py (7 cases).

## Symptom

```
vLLM returned 400: maximum context length is 65536 tokens. However, you
requested 16384 output tokens and your prompt contains at least 49153 input
tokens, for a total of at least 65537 tokens.
```

49153 + 16384 = 65537 = window + 1. The turn dies mid-conversation; the user
sees an `error` SSE and no answer. Pre-existing (handoff noted "28 calls ->
occasional vLLM 400"); A5 density made it reproducible.

## Root cause (three compounding gaps)

1. **The tool-loop vLLM call uses a fixed `max_tokens: 16384` with NO fit to the
   actual prompt size.** `chat_service.py:766`. A proven fit/clamp/retry already
   exists (`main.py::_raw_chat_proxy`, constants at main.py:1006-1023:
   `_RAW_MAX_MODEL_LEN`, `_RAW_CTX_MARGIN`, `_halve_for_retry`,
   `_is_ctx_overflow`) but it ONLY guards the raw passthrough path, not
   `run_chat_completion`. The full pipeline never clamps output to the room left.

2. **History is trimmed ONCE per turn, before the tool loop runs.**
   `chat_context.assemble_context` (chat_service.py:1847) budgets the prompt at
   turn start. But tool results are appended DURING the loop
   (chat_service.py:2375-2394) and never re-budgeted. Over-tooling (10-28 calls
   in one turn) accumulates many results AFTER the only trim.

3. **The per-result cap is per-result, not per-turn.** `truncate_tool_result`
   caps each result at `TOOL_RESULT_CHAR_LIMIT = 8000` chars (~2.5K tokens,
   tool_result.py:35). 20 capped results still sum to ~50K tokens. No ceiling on
   the SUM of retained results in a turn.

Minor: `chat_context.GENERATION_RESERVE = 8000` (chat_context.py:34) is half the
real `max_tokens=16384`, so even the one-shot history trim under-reserves for
output.

## Fix tiers

### Tier 1 - per-call max_tokens fit + 400 retry (minimal, proven)
Wire the `_raw_chat_proxy` mechanism into the tool-loop call:
- Before each vLLM call in the loop, count prompt tokens (`_raw_prompt_tokens` /
  `chat_context._message_tokens`) and clamp
  `max_tokens = min(persona_or_default, MAX_MODEL_LEN - prompt - CTX_MARGIN)`,
  floored at `_RAW_MIN_OUTPUT_TOKENS` (256).
- Keep the streaming 400-overflow retry (`_is_ctx_overflow` -> `_halve_for_retry`)
  as a backstop.
Extract the raw-proxy logic into a shared helper so both paths use one
implementation. Directly kills the 1-token (and larger) overflow.
Effort: ~half day. Risk: low. Files: chat_service.py, main.py (extract helper).

### Tier 2 - mid-loop tool-result budgeting (IMPLEMENTED)

`chat_context.budget_tool_results(messages)`, called at the top of
`_stream_vllm_once` before the Tier 1 fit. When the prompt exceeds
`MAX_MODEL_LEN - GENERATION_RESERVE - CTX_MARGIN` (~48.6K), it elides the OLDEST
tool-role results - replacing only their `content` with a compact
`{"_elided":true,...}` stub (role + tool_call_id preserved, so the
assistant<->tool pairing stays valid) - oldest first, until the prompt fits.

Safety: it elides ONLY results before the most recent assistant message (prior
loop iterations the model has already reasoned past); the PENDING batch (results
after the last assistant message) is never touched, so a result the current step
still needs is never dropped. It returns a COPY - the full results stay in the
persisted conversation; only the vLLM-bound prompt is trimmed. Idempotent.

Effect (composed with Tier 1): a heavy fan-out turn that would have squeezed the
output budget toward the floor instead gets its old results elided, restoring the
full output budget. Verified: a 50K-token fan-out prompt -> elide old result(s)
-> output budget 14.7K -> 16.4K, pending result intact, input untouched.
Unit tests in retrieval/tests/test_chat_context_fit.py (4 Tier 2 cases).

### Tier 3 - reserve alignment (one-liner)
Set `GENERATION_RESERVE = 16384` (== max_tokens) or derive both from one constant
so the history trim reserves the real output budget. Effort: minutes. Risk: low
(slightly more aggressive history trim).

## Recommendation

Ship **Tier 1 + Tier 3 first** (cheap, proven, removes the hard 400 immediately),
then **Tier 2** as the durable fix for over-tooling accumulation. Tier 1 turns a
fatal 400 into a graceful (smaller-output) answer; Tier 2 keeps output budget
healthy under heavy fan-out.

Do NOT rely on prompt nudges to bound tool calls - the research fragment already
says "synthesise from deep_research, do not pile on more searches" and the model
still over-tools. The fix must be context-side.

## Test plan

- Repro: the 3 deep_research paraphrases that 400'd
  (`sota_phip__p09/p11/p14`) + a synthetic unit test that builds a >50K-token
  message list and asserts the clamped `max_tokens` keeps prompt+output <=
  MAX_MODEL_LEN.
- Regression: re-run the routing paraphrase deep_research category (target: no
  `error` SSE; deep_research mean recovers from 0.69).
- Guard: assert the retry path converges (no infinite halving) and never sends
  `max_tokens < MIN_OUTPUT_TOKENS`.

## Out of scope

- Reducing the over-tooling itself (a routing/prompt concern; A5 added the
  `soft_max_calls` diagnostic to track it).
- Token-budgeting `truncate_tool_result` (char-based today; noted as a possible
  follow-up in tool_result.py:34).

---

## REOPENED 2026-08-12: all three tiers were reading a blind token count

The status line above ("ALL THREE TIERS IMPLEMENTED + DEPLOYED... zero ctx
errors") was true only for the deep_research paraphrase eval category it was
verified against. General chat kept overflowing, and the rate accelerated:
4 rejections in June, 5 in July, **14 in the first 12 days of August**, hitting
4 of 19 active users and 9 of 55 conversations. Nobody reported it; the turn
just dies with an `error` SSE.

Cause: every tier fed off `_message_tokens`, which omitted the two largest
contributors to a tool-heavy prompt.

1. **The tools schema was never counted at all.** It rides in `body["tools"]`,
   not in `messages`, and `chat_service` attached it two lines AFTER
   `fit_max_tokens` ran. Live cost: 6220 (chat) / 6898 (research) / 7289 (code)
   tokens on every single call.
2. **`tool_calls` were never counted.** Arguments live in `tool_calls`, not
   `content`, so an assistant message that shipped 6457 tokens of `run_python`
   source scored 4 tokens. Measured undercount on one representative 3-call
   code turn: **14,974 tokens**.

Both guards read the same blind number, so they agreed with each other and were
both wrong. The consequences were visible in the logs all along:

| signal | over 30 days of prod logs |
|---|---|
| Tier 1's "prompt fills the context window" warning | **0 occurrences** |
| Tier 2's "budgeted N earlier tool result(s)" | **0 occurrences, ever** |
| the halving retry ladder | **60 occurrences** |

This doc predicted its own blind spot: Tier 2's prod verification was listed as
still needed via `docker logs munin-retrieval | grep budgeted`. That grep
returns nothing, because Tier 2 has never once fired in production.

### Also corrected: the retry ladder could not reach a viable budget

Nine rejections were numerically identical at `max_tokens=2048`, which is
`DEFAULT_MAX_OUTPUT_TOKENS` halved three times against a ceiling of exactly 3.
The ladder exhausted before it could go lower and the turn died.
`MAX_REFIT_RETRIES` is now 5 in both `chat_context.py` and
`backend/docker/docker-compose.yml` (the compose value overrides the code
default, so changing only the code would have been a no-op in production).

### The trap in reading vLLM's error

Two rejection shapes exist and only one carries a measurement:

- `Input length (70787) exceeds model's maximum context length (65536)` is a
  real measurement, and is now used to refit exactly and to correct the
  undercount before re-eliding.
- `...you requested 2048 output tokens and your prompt contains at least 63489
  input tokens` is **derived**: 65536 + 1 - 2048 == 63489 exactly, in every
  observed instance. Refitting from it cannot converge, which is the trap
  `main.py::_raw_prompt_tokens` already documented. `parse_exact_prompt_tokens`
  returns None for this shape on purpose.

Guarded by 15 new cases in `retrieval/tests/test_chat_context_fit.py`.
