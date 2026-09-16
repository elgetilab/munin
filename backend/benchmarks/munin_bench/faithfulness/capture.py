"""B3 - capture (free-text answer, retrieved contexts) from the live harness.

Drives the live research chat over HTTP for a free-text question and records
BOTH the final answer AND the evidence the harness retrieved this turn, so the
MiniCheck judge (B1) can score the answer's claims against that evidence.

The research persona is agentic: retrieval arrives as ``tool_result`` SSE events
from paper_search / search_user_docs / web_* etc. (not only an upfront
``rag_context`` event). We capture both. Contexts are the union of the text
extracted from those results - this is the "what the model could ground on" set.

Capture is deliberately split from scoring: it is the slow, live, I/O-bound part
and is written to JSONL once, then scored offline (and re-scored freely). Each
captured file is ONE arm; Track D's bare/RAG/agentic arms are just more files.
"""

from __future__ import annotations

import json
import time
import urllib.request

# Tools whose results are retrieved EVIDENCE the answer may ground on.
RETRIEVAL_TOOLS = {
    "paper_search", "search_user_docs", "semantic_scholar_search",
    "web_search", "web_fetch", "get_citations", "get_references",
    "read_paper", "paper_lookup", "compare_papers", "deep_research",
    "s2_get_citations", "s2_get_references", "get_paper_pdf",
    # The search ladder and the grounded read stage (2026-08) were missing
    # here until 2026-09-16, so their results never became grounding contexts:
    # the 08-26 Qwen3.8 per-arm faithfulness (agentic n=163) was scored on
    # web/S2/paper_search evidence only, and the gpt-oss-20b run, which made
    # 1,695 of its 1,741 calls through `search` and `source`, left 13 scoreable
    # agentic rows. Contexts are extracted at capture time, so neither run can
    # be re-scored; the next capture will count them.
    "search", "source",
}

# Result keys that carry groundable text (title kept as a short label).
# "excerpt" is paper_search's T1a abstract snippet - it IS retrieved evidence, so
# it must count toward the grounding contexts or faithfulness is understated.
# "quote" is the verbatim chunk text source(mode=evidence) and source(mode=qa)
# return (never model-generated; findings[].claim is and stays excluded). Added
# 2026-09-16 with `search`/`source` in RETRIEVAL_TOOLS: without it the recapture
# would have grounded against the tool's digests but not the paper text itself.
_TEXT_KEYS = {"content", "text", "abstract", "snippet", "passage", "summary",
              "body", "tldr", "answer", "excerpt", "quote"}
_MIN_LEN = 20


def _extract_texts(obj, out: list[str]) -> None:
    """Recursively pull groundable strings from a tool result."""
    if isinstance(obj, dict):
        title = obj.get("title")
        for k, v in obj.items():
            if isinstance(v, str):
                if k in _TEXT_KEYS and len(v.strip()) >= _MIN_LEN:
                    prefix = f"{title}: " if title and k != "title" else ""
                    out.append((prefix + v).strip())
            else:
                _extract_texts(v, out)
    elif isinstance(obj, list):
        for x in obj:
            _extract_texts(x, out)


def _free_text_prompt(question: str) -> str:
    return (
        "Answer the following research question using the scientific "
        "literature. Search for and rely on the retrieved sources; be specific "
        "and concise.\n\n"
        f"Question: {question}"
    )


def capture_answer(base_url: str, email: str, question: str,
                   sock_timeout: int = 60, deadline: int = 300) -> dict:
    """Stream one live research answer; return
    {answer, contexts[], truncated, n_tool_results}. Same keepalive/deadline
    guards as the answer track's _ask_chat."""
    body = {"persona": "research", "ephemeral": True,
            "messages": [{"role": "user", "content": _free_text_prompt(question)}]}
    req = urllib.request.Request(
        base_url.rstrip("/") + "/api/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "X-Munin-Email": email,
                 "X-Munin-Ephemeral": "true", "Accept": "text/event-stream"},
        method="POST")

    content = ""
    contexts: list[str] = []
    seen: set[str] = set()
    n_tool_results = 0
    n_tool_calls = 0  # ALL tool calls this turn (over-tooling signal), not just retrieval
    truncated = True
    terminal_reason = None
    ev = None
    start = time.time()
    resp = urllib.request.urlopen(req, timeout=sock_timeout)
    try:
        for raw in resp:
            if time.time() - start > deadline:
                break
            line = raw.decode("utf-8", "replace").rstrip("\n")
            if line.startswith("event:"):
                ev = line[6:].strip()
                continue
            if not line.startswith("data:"):
                continue
            d = line[5:].strip()
            if not d or d == "[DONE]":
                continue
            try:
                obj = json.loads(d)
            except Exception:
                continue
            if ev == "token" and isinstance(obj, dict) and obj.get("content"):
                content += obj["content"]
            elif ev == "tool_call" and isinstance(obj, dict):
                n_tool_calls += 1
            elif ev == "tool_result" and isinstance(obj, dict):
                if obj.get("name") in RETRIEVAL_TOOLS:
                    n_tool_results += 1
                    found: list[str] = []
                    _extract_texts(obj.get("result"), found)
                    for t in found:
                        if t not in seen:
                            seen.add(t)
                            contexts.append(t)
            elif ev == "rag_context" and isinstance(obj, dict):
                for doc in (obj.get("documents") or []):
                    c = (doc.get("content") or "").strip()
                    if c and c not in seen:
                        seen.add(c)
                        contexts.append(c)
            if ev == "done":
                truncated = False
                # terminal_reason == "max_turns" is the budget/tool-cap wrap-up
                # path (over-tooling cap trips here); "done" = natural finish.
                terminal_reason = obj.get("terminal_reason") if isinstance(obj, dict) else None
                break
    finally:
        resp.close()
    return {"answer": content, "contexts": contexts, "truncated": truncated,
            "n_tool_results": n_tool_results, "n_tool_calls": n_tool_calls,
            "terminal_reason": terminal_reason}
