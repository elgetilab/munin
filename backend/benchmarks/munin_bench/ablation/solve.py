"""Track F future-proofing: each ablation arm as an async solve(prompt) -> answer.

The master plan (sec 7) decided NOT to adopt InspectAI now, but to shape the
Track D arms so a future InspectAI/AstaBench bridge is a WRAPPER, not a rebuild.
That shape is a plain `async def solve(prompt) -> str` per arm; `SOLVERS` maps
the arm name to its solver. The batch `run_arm` runners stay the primary path;
these are the single-item adapters a bridge would call.
"""

from __future__ import annotations

import asyncio
import json
import time
import urllib.request

from . import vllm_answer as V


async def solve_bare(prompt: str, **kw) -> str:
    """Bare model, no tools/RAG."""
    r = await asyncio.to_thread(V.bare_answer, prompt, **kw)
    return r["content"]


async def solve_rag(prompt: str, *, question: str | None = None,
                    top_k: int = 5, **kw) -> str:
    """Vanilla RAG: BGE top-k over papers_bge -> context -> single completion.
    ``question`` (the raw query for retrieval) defaults to ``prompt``."""
    from .run_arm import _retrieve
    ctx = await asyncio.to_thread(_retrieve, question or prompt, top_k)
    r = await asyncio.to_thread(V.rag_answer, prompt, ctx, **kw)
    return r["content"]


async def solve_agentic(prompt: str, *, base_url: str = "http://127.0.0.1:8080",
                        email: str = "litqa2-eval@localhost",
                        deadline: int = 300) -> str:
    """Full harness: stream the live research chat, return the answer text."""
    def _run() -> str:
        body = {"persona": "research", "ephemeral": True,
                "messages": [{"role": "user", "content": prompt}]}
        req = urllib.request.Request(
            base_url.rstrip("/") + "/api/chat/completions",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "X-Munin-Email": email,
                     "X-Munin-Ephemeral": "true", "Accept": "text/event-stream"},
            method="POST")
        content, ev = "", None
        t0 = time.time()
        resp = urllib.request.urlopen(req, timeout=60)
        try:
            for raw in resp:
                if time.time() - t0 > deadline:
                    break
                line = raw.decode("utf-8", "replace").rstrip("\n")
                if line.startswith("event:"):
                    ev = line[6:].strip(); continue
                if line.startswith("data:"):
                    d = line[5:].strip()
                    if d and d != "[DONE]":
                        try:
                            o = json.loads(d)
                        except Exception:
                            continue
                        if ev == "token" and o.get("content"):
                            content += o["content"]
                        if ev == "done":
                            break
        finally:
            resp.close()
        return content
    return await asyncio.to_thread(_run)


SOLVERS = {"bare": solve_bare, "rag": solve_rag, "agentic": solve_agentic}
