"""
LLM-based MCP tools.

Provides:
- llm_summarize: Use vLLM to summarize or process text
"""

import re
import httpx

from database import VLLM_URL, VLLM_MODEL_NAME


async def llm_summarize(text: str, instruction: str, max_tokens: int = 16384) -> dict:
    """
    Use the vLLM serving model to summarize or extract from text.

    Args:
        text: The text to process
        instruction: Instructions for the LLM
        max_tokens: Maximum tokens in response

    Returns:
        Dict with 'summary' or 'error' key
    """
    # Truncate text if too long (roughly 30k tokens = 120k chars)
    max_chars = 120000
    if len(text) > max_chars:
        text = text[:max_chars] + "\n\n[... truncated ...]"

    prompt = f"""<|im_start|>system
You are a helpful assistant that follows instructions precisely.
<|im_end|>
<|im_start|>user
{instruction}

Text to process:
{text}
<|im_end|>
<|im_start|>assistant
"""

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{VLLM_URL}/v1/completions",
                json={
                    "model": VLLM_MODEL_NAME,
                    "prompt": prompt,
                    "max_tokens": max_tokens,
                    "temperature": 0.3,
                    "stop": ["<|im_end|>", "<|endoftext|>"]
                }
            )

            if response.status_code != 200:
                return {"error": f"vLLM request failed: {response.status_code} - {response.text[:200]}"}

            data = response.json()
            result_text = data.get("choices", [{}])[0].get("text", "")

            # Strip any thinking tags from Qwen3
            # Handle complete <think>...</think> pairs
            result_text = re.sub(r'<think>.*?</think>', '', result_text, flags=re.DOTALL)
            # Handle incomplete <think>... without closing tag (truncated by max_tokens)
            result_text = re.sub(r'<think>.*$', '', result_text, flags=re.DOTALL)
            result_text = result_text.strip()

            return {"summary": result_text}

    except httpx.TimeoutException:
        return {"error": "vLLM request timed out (model may be loading or offline)"}
    except Exception as e:
        return {"error": f"LLM summarization failed: {str(e)}"}
