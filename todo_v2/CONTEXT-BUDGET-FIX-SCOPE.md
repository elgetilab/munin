# Scope: deep_research vLLM 400 context-overflow (backend context budget)

Status: SCOPE (for sign-off). Surfaced by A5 paraphrase tier (3/16 deep_research
paraphrases 400'd). NOT an A5 persona-tuning item; a backend fix.

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

### Tier 2 - mid-loop prompt budgeting (the structural fix)
When accumulated tool results push the prompt toward the window, trim WITHIN the
turn before the next call. Options (pick one, lowest-risk first):
- (a) Cap the SUM of retained full tool results per turn; replace the oldest
  over-budget ones with their `{"_truncated":true,"preview":...}` stub (they have
  already been folded into later assistant reasoning).
- (b) Drop/summarise the oldest tool-role messages when prompt > threshold
  (reuse `chat_context` summarisation).
Addresses the real cause (over-tooling accumulation) so output budget isn't
starved to the floor. Effort: ~1-1.5 days. Risk: medium (must not drop a result
the model still needs this turn; keep the most recent K full).

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
