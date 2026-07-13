"""Direct vLLM completion for the Track D bare + RAG arms (no tools, no loop).

Single non-streaming `/v1/chat/completions` at concurrency=1 so the wall-clock
IS the inference-time (the GPU-seconds cost proxy; sacct is disabled). Returns
content + token usage + elapsed for the cost accounting.
"""

from __future__ import annotations

import json
import time
import urllib.request

VLLM_URL = "http://127.0.0.1:8000"
MODEL = "qwen3.6-35b-a3b"

_BARE_SYSTEM = (
    "You are a research assistant. Answer the question using your own knowledge. "
    "You have no external tools."
)
_RAG_SYSTEM = (
    "You are a research assistant. Answer the question using ONLY the retrieved "
    "context provided below; if the context does not contain the answer, say so "
    "and pick the insufficient-information option."
)


def complete(prompt: str, *, system: str, max_tokens: int = 4096,
             temperature: float = 0.7, timeout: int = 300) -> dict:
    """One vLLM chat completion. Returns {content, prompt_tokens,
    completion_tokens, elapsed_s}."""
    body = {
        "model": MODEL,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }
    req = urllib.request.Request(
        VLLM_URL + "/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    t0 = time.time()
    resp = json.load(urllib.request.urlopen(req, timeout=timeout))
    elapsed = time.time() - t0
    content = (resp["choices"][0]["message"].get("content") or "")
    usage = resp.get("usage") or {}
    return {
        "content": content,
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
        "elapsed_s": round(elapsed, 2),
    }


def bare_answer(mcq_prompt: str, **kw) -> dict:
    return complete(mcq_prompt, system=_BARE_SYSTEM, **kw)


def rag_answer(mcq_prompt: str, contexts: list[str], **kw) -> dict:
    ctx = "\n\n".join(f"[{i+1}] {c}" for i, c in enumerate(contexts))
    prompt = f"Retrieved context:\n{ctx}\n\n---\n\n{mcq_prompt}"
    return complete(prompt, system=_RAG_SYSTEM, **kw)
