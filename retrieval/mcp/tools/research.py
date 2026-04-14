"""
Composite research tool.

`deep_research` chains everything we built in Phases 1-3 into one deterministic
pipeline: decompose a question into sub-questions, run query expansion per
sub-question, fan the flattened query pool across paper_search /
semantic_scholar_search / web_search in parallel, then fetch + map-reduce
summarise the top web results. The tool call returns a single structured
dict the main model can synthesise into a final answer without calling any
further tools.

Compared to the `research_orchestrator` agent: the agent is flexible and
can iterate based on early findings, but burns a full nested vLLM loop per
call. `deep_research` is deterministic and cheaper — ideal for "give me
papers and context about X" shaped questions.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Optional

import httpx

from database import VLLM_URL, VLLM_MODEL_NAME

from .query_expansion import expand_queries
from .papers import paper_search, semantic_scholar_search
from .web import web_search, web_fetch_content


# ---- tunables ---------------------------------------------------------------

_DEPTH_CONFIG = {
    "medium": {
        "n_sub_questions": 3,
        "variants_per_sub": 3,
        "max_fetches": 3,
        "paper_top_k": 8,
        "s2_top_k": 10,
        "web_top_k": 10,
        "wall_clock_budget_s": 90.0,
    },
    "deep": {
        "n_sub_questions": 5,
        "variants_per_sub": 4,
        "max_fetches": 5,
        "paper_top_k": 12,
        "s2_top_k": 15,
        "web_top_k": 15,
        "wall_clock_budget_s": 180.0,
    },
}

# Cap the concurrent fetches — each fetch fires its own chunk summariser
# semaphore internally, so two concurrent fetches is a polite ceiling for
# the GPU (vLLM is started with --max-num-seqs 2).
_FETCH_CONCURRENCY = 2


# ---- question decomposition -------------------------------------------------

_DECOMPOSE_SYSTEM_PROMPT = (
    "You are a research-question decomposer. Given one research question, "
    "break it into N conceptually distinct sub-questions that together "
    "would give a comprehensive answer. Cover different aspects: "
    "definitions, mechanisms, methods, evidence, implications, open "
    "questions. Do not restate the same sub-question twice. Return ONLY a "
    "JSON array of strings. No commentary."
)


def _parse_sub_questions(raw: str) -> list[str]:
    """
    Lifted pattern from query_expansion._parse_variants: find a JSON array
    in the model output, fall back to line-splitting.
    """
    if not raw:
        return []

    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()

    match = re.search(r"\[[^\[\]]*\]", raw, flags=re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, list):
                return [str(x).strip() for x in parsed if str(x).strip()]
        except json.JSONDecodeError:
            pass

    out: list[str] = []
    for line in raw.splitlines():
        s = line.strip().lstrip("-*0123456789. ").strip()
        s = s.strip("\"'`,")
        if s:
            out.append(s)
    return out


async def decompose_question(question: str, n: int = 4) -> list[str]:
    """
    Ask vLLM to break a research question into `n` conceptually distinct
    sub-questions. Always returns at least `[question]` so callers can
    proceed even if vLLM is unavailable.
    """
    question = (question or "").strip()
    if not question:
        return []

    n = max(2, min(8, int(n)))

    messages = [
        {"role": "system", "content": _DECOMPOSE_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Research question: {question}\n\n"
                f"Return exactly {n} sub-questions as a JSON array of strings. "
                "Do not repeat the main question verbatim."
            ),
        },
    ]

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{VLLM_URL}/v1/chat/completions",
                json={
                    "model": VLLM_MODEL_NAME,
                    "messages": messages,
                    "max_tokens": 400,
                    "temperature": 0.5,
                    "stream": False,
                    # Qwen3 would otherwise eat the budget inside <think>.
                    "chat_template_kwargs": {"enable_thinking": False},
                },
            )
            if resp.status_code != 200:
                return [question]
            data = resp.json()
            choices = data.get("choices") or []
            if not choices:
                return [question]
            content = (choices[0].get("message") or {}).get("content") or ""
    except Exception:
        return [question]

    parsed = _parse_sub_questions(content)

    seen = {question.lower()}
    ordered = [question]
    for sq in parsed:
        key = sq.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(sq)
        if len(ordered) >= n + 1:
            break

    return ordered


# ---- deep_research pipeline ------------------------------------------------


async def _run_paper_tools(
    queries: list[str],
    paper_top_k: int,
    s2_top_k: int,
) -> tuple[dict, dict]:
    """Fire paper_search and semantic_scholar_search in parallel."""
    paper_task = paper_search(queries=queries, top_k=paper_top_k)
    s2_task = semantic_scholar_search(queries=queries, top_k=s2_top_k)
    paper_res, s2_res = await asyncio.gather(
        paper_task, s2_task, return_exceptions=True
    )
    paper_res = paper_res if isinstance(paper_res, dict) else {"results": []}
    s2_res = s2_res if isinstance(s2_res, dict) else {"results": []}
    return paper_res, s2_res


async def _run_web_search(queries: list[str], web_top_k: int) -> dict:
    try:
        return await web_search(queries=queries, top_k=web_top_k)
    except Exception as e:
        return {"error": str(e), "results": []}


def _merge_papers(
    local_res: dict, s2_res: dict, limit: int
) -> list[dict]:
    """
    Merge the two paper sources by DOI. Local papers win on their own fields
    (journal, year), S2 entries contribute TLDR and citation count when the
    DOI overlaps. Papers without a DOI are kept but only deduped within a
    single source.
    """
    merged: dict[str, dict] = {}

    def _add(source_label: str, paper: dict) -> None:
        key = (paper.get("doi") or "").strip().lower()
        if not key:
            key = f"notitle:{id(paper)}"
        existing = merged.get(key)
        enriched = {
            "title": paper.get("title"),
            "doi": paper.get("doi"),
            "authors": paper.get("authors") or [],
            "year": paper.get("year"),
            "tldr": paper.get("tldr"),
            "abstract": paper.get("abstract"),
            "citation_count": paper.get("citation_count"),
            "matched_by": paper.get("matched_by", 1),
            "source": source_label,
            # Carry through download attribution from §13. Either field may
            # be None on any given paper; merge logic below promotes the
            # better one when both sources have data on the same DOI.
            "download_url": paper.get("download_url"),
            "local_pdf_available": paper.get("local_pdf_available", False),
            "open_access_pdf": paper.get("open_access_pdf"),
        }
        if existing is None:
            merged[key] = enriched
        else:
            # Prefer the version with richer metadata; merge missing fields.
            for field in (
                "tldr", "abstract", "citation_count", "year", "doi", "authors",
                "download_url", "open_access_pdf",
            ):
                if not existing.get(field) and enriched.get(field):
                    existing[field] = enriched[field]
            # local_pdf_available is OR-merged: True wins.
            if enriched.get("local_pdf_available"):
                existing["local_pdf_available"] = True
            existing["matched_by"] = max(
                existing.get("matched_by", 1), enriched.get("matched_by", 1)
            )

    for p in local_res.get("results", []) or []:
        _add("local", p)
    for p in s2_res.get("results", []) or []:
        # Only overwrite source to "semantic_scholar" if it wasn't already
        # in the local corpus — local wins when both have the paper.
        _add("semantic_scholar", p)

    ranked = sorted(
        merged.values(),
        key=lambda r: (
            -(r.get("matched_by") or 0),
            -(r.get("citation_count") or 0),
        ),
    )
    return ranked[:limit]


async def _fetch_top_urls(
    urls: list[str], instruction: str, max_fetches: int
) -> list[dict]:
    """Run web_fetch_content on the top URLs in parallel, capped by a semaphore."""
    urls = urls[:max_fetches]
    if not urls:
        return []

    sem = asyncio.Semaphore(_FETCH_CONCURRENCY)

    async def _one(url: str) -> dict:
        async with sem:
            try:
                return await web_fetch_content(url, summary_instruction=instruction)
            except Exception as e:
                return {"url": url, "error": f"fetch failed: {e}"}

    results = await asyncio.gather(*(_one(u) for u in urls), return_exceptions=True)
    out: list[dict] = []
    for r in results:
        if isinstance(r, Exception):
            continue
        if isinstance(r, dict):
            out.append(r)
    return out


async def deep_research(question: str, depth: str = "medium") -> dict:
    """
    One-call research pipeline.

    Flow:
        1. Decompose the research question into N distinct sub-questions.
        2. Expand each sub-question into ~4 search query variants in parallel.
        3. Flatten the variant pool and dedupe.
        4. Fan out across paper_search, semantic_scholar_search, and
           web_search in parallel with the shared query pool.
        5. Merge paper results across sources, rank by match count + citation.
        6. Fetch the top web URLs in parallel and map-reduce summarise each.
        7. Return a single structured dict with everything the main model
           needs to compose an answer.

    Args:
        question: The research question to investigate.
        depth: "medium" (default) or "deep". Controls sub-question count,
            fetch count, and wall-clock budget.

    Returns:
        Dict with sub_questions, queries_executed, papers, web_sources,
        web_summaries, sources_used, and timing breakdown. Or an error dict.
    """
    question = (question or "").strip()
    if not question:
        return {"error": "deep_research requires a non-empty question"}

    cfg = _DEPTH_CONFIG.get(depth) or _DEPTH_CONFIG["medium"]
    start = time.monotonic()

    # --- Stage 1: decompose --------------------------------------------------
    decompose_start = time.monotonic()
    sub_questions = await decompose_question(question, n=cfg["n_sub_questions"])
    decompose_ms = int((time.monotonic() - decompose_start) * 1000)

    # --- Stage 2: expand each sub-question in parallel -----------------------
    expand_start = time.monotonic()
    expansion_tasks = [
        expand_queries(sq, n=cfg["variants_per_sub"]) for sq in sub_questions
    ]
    expansion_results = await asyncio.gather(*expansion_tasks, return_exceptions=True)

    query_pool: list[str] = []
    seen: set[str] = set()
    for result in expansion_results:
        if isinstance(result, Exception) or not result:
            continue
        for q in result:
            key = q.lower().strip()
            if key and key not in seen:
                seen.add(key)
                query_pool.append(q)

    # Hard cap on pool size so we don't explode downstream fan-out.
    query_pool = query_pool[: cfg["n_sub_questions"] * cfg["variants_per_sub"]]
    expand_ms = int((time.monotonic() - expand_start) * 1000)

    if not query_pool:
        return {
            "error": "deep_research failed to generate any queries",
            "question": question,
        }

    # --- Stage 3: fan out the search tools on the shared pool ---------------
    search_start = time.monotonic()
    try:
        (papers_local, papers_s2), web_res = await asyncio.gather(
            _run_paper_tools(query_pool, cfg["paper_top_k"], cfg["s2_top_k"]),
            _run_web_search(query_pool, cfg["web_top_k"]),
        )
    except Exception as e:
        return {"error": f"deep_research search stage failed: {e}", "question": question}
    search_ms = int((time.monotonic() - search_start) * 1000)

    # --- Stage 4: merge + rank papers ---------------------------------------
    merged_papers = _merge_papers(papers_local, papers_s2, limit=cfg["paper_top_k"])

    # --- Stage 5: web fetches on the top URLs -------------------------------
    top_urls = [r.get("url") for r in (web_res.get("results") or []) if r.get("url")]
    fetch_instruction = (
        f"Extract the facts, findings, and claims that help answer: {question}"
    )
    fetch_start = time.monotonic()
    fetched = await _fetch_top_urls(top_urls, fetch_instruction, cfg["max_fetches"])
    fetch_ms = int((time.monotonic() - fetch_start) * 1000)

    web_summaries: list[dict] = []
    for f in fetched:
        if not isinstance(f, dict):
            continue
        if f.get("error"):
            continue
        summary = (f.get("summary") or "").strip()
        if not summary:
            continue
        web_summaries.append({
            "url": f.get("url"),
            "summary": summary,
            "summary_chars": f.get("summary_chars", len(summary)),
            "total_chars_original": f.get("total_chars_original"),
        })

    total_ms = int((time.monotonic() - start) * 1000)

    return {
        "question": question,
        "depth": depth,
        "sub_questions": sub_questions,
        "queries_executed": query_pool,
        "papers": merged_papers,
        "web_sources": [
            {
                "title": r.get("title"),
                "url": r.get("url"),
                "snippet": r.get("snippet"),
                "matched_by": r.get("matched_by", 1),
            }
            for r in (web_res.get("results") or [])[: cfg["web_top_k"]]
        ],
        "web_summaries": web_summaries,
        "sources_used": ["local_papers", "semantic_scholar", "web"],
        "latency_ms": {
            "decompose": decompose_ms,
            "expand": expand_ms,
            "search": search_ms,
            "fetch": fetch_ms,
            "total": total_ms,
        },
    }
