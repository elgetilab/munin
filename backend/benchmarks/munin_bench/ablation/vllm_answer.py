"""Direct vLLM completion for the Track D bare + RAG arms (no tools, no loop).

Single non-streaming `/v1/chat/completions` at concurrency=1 so the wall-clock
IS the inference-time (the GPU-seconds cost proxy; sacct is disabled). Returns
content + token usage + elapsed for the cost accounting.
"""

from __future__ import annotations

import json
import time
import urllib.request

from .. import config

# Resolved from config (env-overridable) rather than hardcoded: a model swap has
# to move ONE literal, and these arms are the easiest place in the suite to leave
# a stale one, because they bypass the retrieval service entirely and so never
# fail loudly when the served model changes underneath them.
VLLM_URL = config.VLLM_URL
MODEL = config.VLLM_MODEL_NAME

_BARE_SYSTEM = (
    "You are a research assistant. Answer the question using your own knowledge. "
    "You have no external tools."
)
_RAG_SYSTEM = (
    "You are a research assistant. Use the retrieved context below to help answer "
    "the question; you may also draw on your own knowledge. Pick the "
    "insufficient-information option only if you genuinely cannot determine the "
    "answer from the context or your knowledge. (Vanilla RAG: retrieved evidence "
    "+ the model, no tools or agentic loop.)"
)


def complete(prompt: str, *, system: str, max_tokens: int = None,
             temperature: float = 0.7, timeout: int = 900) -> dict:
    """One vLLM chat completion. Returns {content, prompt_tokens,
    completion_tokens, elapsed_s}.

    `max_tokens` defaults to the SAME budget the agentic arm gets
    (config.MAX_OUTPUT_TOKENS, 16384) rather than the old 4096, and the request
    carries the SAME reasoning effort. Both are arm-matching requirements, not
    tuning: see config.LLM_REASONING_EFFORT for what happens when they drift.
    The timeout is 900s to match the agentic arm's deadline, so an arm is never
    truncated by the client while the model is still producing.
    """
    body = {
        "model": MODEL,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": prompt}],
        "max_tokens": config.MAX_OUTPUT_TOKENS if max_tokens is None else max_tokens,
        "temperature": temperature,
        "stream": False,
    }
    if config.LLM_REASONING_EFFORT:
        body["chat_template_kwargs"] = {
            "reasoning_effort": config.LLM_REASONING_EFFORT}
    req = urllib.request.Request(
        VLLM_URL + "/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    t0 = time.time()
    resp = json.load(urllib.request.urlopen(req, timeout=timeout))
    elapsed = time.time() - t0
    choice = resp["choices"][0]
    msg = choice.get("message") or {}
    content = (msg.get("content") or "")
    usage = resp.get("usage") or {}
    # Kept so an EMPTY content can be told apart afterwards: a budget
    # truncation (finish_reason=length) from a model that reasoned and then
    # ended its turn without a final message. gpt-oss-20b does the latter on
    # 22% of bare and 81% of RAG prompts ("Use search." then stop, ~130
    # tokens, finish=stop), which is a behaviour to report, not a harness limit.
    reasoning = msg.get("reasoning") or msg.get("reasoning_content") or ""
    return {
        "content": content,
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
        "elapsed_s": round(elapsed, 2),
        "finish_reason": choice.get("finish_reason"),
        "reasoning_tail": reasoning[-240:],
        "tool_calls_attempted": len(msg.get("tool_calls") or []),
    }


def bare_answer(mcq_prompt: str, **kw) -> dict:
    return complete(mcq_prompt, system=_BARE_SYSTEM, **kw)


def rag_answer(mcq_prompt: str, contexts: list[str], **kw) -> dict:
    ctx = "\n\n".join(f"[{i+1}] {c}" for i, c in enumerate(contexts))
    prompt = f"Retrieved context:\n{ctx}\n\n---\n\n{mcq_prompt}"
    return complete(prompt, system=_RAG_SYSTEM, **kw)
