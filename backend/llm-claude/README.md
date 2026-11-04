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

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `CLAUDE_MODEL` | `claude-haiku-5-5` | `claude-haiku-5-5` or `claude-sonnet-5-5`. Requests cannot override it, so API-key users cannot pick a costlier model. |
| `CLAUDE_EFFORT` | `medium` | `low`, `medium` or `high`. Lower means fewer tokens and more early stops. |
| `CLAUDE_MAX_OUTPUT_TOKENS` | `16384` | Ceiling on `max_tokens` for any request. |

## Behaviour worth knowing

- **Thinking is off.** Anthropic's guidance prefers lowering effort, but a
  thinking block must be sent back with its tool result, the OpenAI message
  shape cannot carry one, and retrieval's elision of old tool results would make
  replayed blocks a 400. It also keeps the 8 and 40 token `max_tokens` calls
  from being used up by thinking. The UI shows no reasoning trace as a result.
  Opus 5.5 cannot turn thinking off, so it is refused at startup.
- **Caching applies to tool loops only.** One-shot calls such as titles would
  pay the write premium and never read the entry back.
- **`prompt_tokens` counts the whole prompt**, cached or not, so gateway quotas
  measure conversation size rather than cost. The Console has the real spend.
- **Refusals** are shown to the user as text, with `finish_reason:
  content_filter`. Haiku 5.5 has no server-side fallback.
- **Hitting the account's spend cap returns a 402**, not a 429, because no retry
  can fix it before next month.
- **Usage** is logged per request: `usage prompt= cached= output= stream=`.
