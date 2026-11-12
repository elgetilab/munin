# llm-claude

Lets Munin run on the Claude API when no local vLLM is available. It accepts
OpenAI chat-completions from retrieval and calls Claude through the Anthropic
SDK. The API key exists only in this container, and it has no host port.

## Why not the API's OpenAI-compatible endpoint

Munin's requests fail there, and it has no prompt caching, which matters on a
paid API because every tool round resends the whole prompt. Temperatures are
set in about ten places and roughly eight modules call the LLM directly, so a
single service in front of them is a smaller change than editing each one.

## Enabling it

In the backend's `.env`:

    ANTHROPIC_API_KEY=sk-ant-...
    CLAUDE_MODEL=claude-haiku-5-5
    LLM_BASE_URL=http://llm-claude:8000
    LLM_MODEL_NAME=claude-haiku-5-5
    COMPOSE_PROFILES=rag,llm-claude,...

Create the key in a dedicated Console workspace with a spend limit. The default
workspace cannot have one, and usage from your other work stays separate.

Then check it against the real API, which costs well under a cent:

    docker compose exec llm-claude python smoke.py

To use it only while a local vLLM is down, put `llm-failover` in front of both
instead (`LLM_BASE_URL=http://llm-failover:8000`, profiles `llm-claude` and
`llm-failover`); see docs/ops/HAIKU-FALLBACK.md.

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `CLAUDE_MODEL` | `claude-haiku-5-5` | `claude-haiku-5-5`, `claude-sonnet-5` or `claude-sonnet-5-5`. Requests cannot override it, so API-key users cannot pick a costlier model. |
| `CLAUDE_FALLBACK_MODEL` | `claude-sonnet-5` | Answers requests the main model refuses, and stays on for that conversation. Costs 20 times Haiku 5.5's rate. Empty turns it off. |
| `CLAUDE_THINKING` | `on` | `on` or `off`. |
| `CLAUDE_EFFORT` | `medium` | `low`, `medium` or `high`. Lower means fewer tokens and more early stops. |
| `CLAUDE_MAX_OUTPUT_TOKENS` | `16384` | Ceiling on `max_tokens` for any request. |

## Behaviour worth knowing

- **Thinking is on, except for sub-tasks.** Retrieval marks those with
  `enable_thinking=false`, so keep `LLM_THINKING_TOGGLE=1`. Requests under 1,024
  `max_tokens` also skip it, because thinking counts toward that cap. Summaries
  reach the UI's reasoning trace through `delta.reasoning`.
- **Thinking blocks are kept in memory for 2,000 tool rounds**, because the
  OpenAI message shape cannot carry them. They are sent back only when everything
  before them is byte-identical. Retrieval trimming old tool results, or its
  system prompt changing, just costs that reasoning. If the API still rejects a
  block, the request is retried once without any.
- **Refusals fall back once, if nothing visible was sent yet.** A refusal after
  visible text, or from the fallback too, is shown to the user as text with
  `finish_reason: content_filter`. A retry pays the fallback's full cache write,
  since Haiku 5.5 refusals carry no fallback credit, and `bio` refusals are billed.
- **Caching applies to tool loops only.** One-shot calls such as titles would
  pay the write premium and never read the entry back. Retrieval's system prompt
  includes the time of day, so conversation history is reread uncached on each
  new user turn. The tools still hit the cache.
- **`prompt_tokens` counts the whole prompt**, cached or not, so gateway quotas
  measure conversation size rather than cost. The Console has the real spend.
- **Hitting the account's spend cap returns a 402**, not a 429, because no retry
  can fix it before next month.
- **Logs** have one line per request (`usage model= prompt= cached= output=
  stream=`) and one per refusal (`refusal model= category=`), never message text.
