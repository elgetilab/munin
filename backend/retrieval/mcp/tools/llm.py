"""
LLM-based MCP tools.

Provides:
- llm_summarize: Use vLLM to summarize or process text
"""

import httpx

from database import VLLM_URL, VLLM_MODEL_NAME, thinking_off_fields, LLM_HEADERS


async def llm_summarize(text: str, instruction: str, max_tokens: int = 1200) -> dict:
    """
    Use the vLLM serving model to summarize or extract from text.

    Runs against vLLM's /v1/chat/completions with reasoning disabled via
    `chat_template_kwargs.enable_thinking=False`. This matters because Qwen3
    by default emits long <think> traces that would otherwise consume the
    entire token budget before any real content is produced — summarisation
    is a mechanical rephrasing task that doesn't benefit from reasoning.

    Args:
        text: The text to process. Truncated to ~30k chars to stay within
            vLLM's context window for this model.
        instruction: What the summariser should do with the text (e.g.
            "Summarize the main points", "Extract the key findings about X").
        max_tokens: Max tokens in the summariser's output. Default 1200 is
            enough for ~500 words of condensed text.

    Returns:
        Dict with either {"summary": "..."} or {"error": "..."}.
    """
    max_chars = 30000
    if len(text) > max_chars:
        text = text[:max_chars] + "\n\n[... truncated ...]"

    messages = [
        {
            "role": "system",
            "content": "You are a concise summariser. Follow the user's instruction exactly. Do not add commentary, disclaimers, or meta-remarks about being an AI.",
        },
        {
            "role": "user",
            "content": f"{instruction}\n\nText:\n{text}",
        },
    ]

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{VLLM_URL}/v1/chat/completions",
                headers=LLM_HEADERS,
                json={
                    "model": VLLM_MODEL_NAME,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": 0.3,
                    "stream": False,
                    # Disable Qwen3 reasoning — a simple summary task would
                    # otherwise burn the whole token budget inside <think>.
                    **thinking_off_fields(),
                },
            )

            if response.status_code != 200:
                return {
                    "error": f"vLLM request failed: {response.status_code} - {response.text[:200]}"
                }

            data = response.json()
            choices = data.get("choices") or []
            if not choices:
                return {"error": "vLLM returned no choices"}

            message = choices[0].get("message") or {}
            summary = (message.get("content") or "").strip()
            if not summary:
                return {"error": "vLLM returned empty content"}

            return {"summary": summary}

    except httpx.TimeoutException:
        return {"error": "vLLM request timed out (model may be loading or offline)"}
    except Exception as e:
        return {"error": f"LLM summarization failed: {str(e)}"}
