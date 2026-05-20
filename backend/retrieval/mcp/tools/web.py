"""
Web-related MCP tools.

Provides:
- web_search: Search the web using SearXNG (multi-query fan-out)
- web_fetch_content: Fetch and extract content from URLs
"""

import asyncio
import logging
from typing import Optional

import httpx

from database import SEARXNG_URL
from .llm import llm_summarize
from .query_expansion import expand_queries

logger = logging.getLogger(__name__)


# Engines passed to SearXNG on every web_search. Google is intentionally
# excluded — see docker/searxng/settings.yml for the rationale.
_SEARXNG_ENGINES = "startpage,duckduckgo,brave"


async def _searxng_one(client: httpx.AsyncClient, q: str) -> dict:
    """Single SearXNG query.

    Returns a dict with:
        results: raw list of result dicts (possibly empty)
        unresponsive: list of (engine_name, reason) pairs for engines
                      that failed on this query. SearXNG returns this
                      as `unresponsive_engines` in its JSON envelope
                      and we propagate it so callers can distinguish
                      "no hits because the topic is obscure" from
                      "no hits because every engine was rate-limited."
        transport_error: str|None, set if the HTTP call itself
                      raised (timeout, DNS, etc.). Treated as "every
                      engine on this query is unavailable" by
                      web_search's degradation logic.
    """
    try:
        response = await client.get(
            f"{SEARXNG_URL}/search",
            params={
                "q": q,
                "format": "json",
                "engines": _SEARXNG_ENGINES,
                "language": "en",
            },
        )
        response.raise_for_status()
        body = response.json()
    except Exception as e:
        logger.warning("web_search %r failed: %s", q, e)
        return {"results": [], "unresponsive": [], "transport_error": str(e)}
    return {
        "results": body.get("results", []) or [],
        "unresponsive": body.get("unresponsive_engines", []) or [],
        "transport_error": None,
    }


async def web_search(
    query: Optional[str] = None,
    queries: Optional[list[str]] = None,
    top_k: int = 10,
) -> dict:
    """
    Search the web using SearXNG with multi-query fan-out.

    Behaviour:
        - If `queries` is a non-empty list: run exactly those queries in
          parallel. No expansion.
        - Elif `query` is a non-empty string: expand it into 3-5 variants via
          `query_expansion.expand_queries`, then run each variant in parallel.
        - Results are deduped by URL and returned ranked by how many queries
          surfaced each URL (ties broken by the best per-query position).

    Args:
        query: Single search query. Expanded automatically.
        queries: Explicit list of queries. Takes precedence; no expansion.
        top_k: Max number of deduped results to return globally.

    Returns:
        Dict with:
            results: list of {title, url, snippet, matched_by: int, engine}
            queries_executed: list of the query strings actually run
            total_hits: total raw results before dedup
    """
    # Decide which queries to execute.
    if queries:
        query_list = [q.strip() for q in queries if q and q.strip()]
    elif query:
        query_list = await expand_queries(query, n=5)
    else:
        return {"error": "web_search requires either 'query' or 'queries'"}

    if not query_list:
        return {"error": "web_search got empty query list after normalization"}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            raw_batches = await asyncio.gather(
                *(_searxng_one(client, q) for q in query_list)
            )
    except Exception as e:
        return {"error": f"Web search failed: {str(e)}"}

    # Merge + dedupe by URL, counting how many queries surfaced each url.
    # Also aggregate engine status across all queries so the caller can
    # tell "no hits because obscure" from "no hits because every engine
    # was rate-limited / blocked / behind a CAPTCHA" (chat 689f8df3,
    # 2026-05-06).
    seen: dict[str, dict] = {}
    total_hits = 0
    unresponsive: dict[str, str] = {}  # engine_name -> reason (last wins)
    transport_errors = 0
    for q, batch in zip(query_list, raw_batches):
        for rank, item in enumerate(batch.get("results", [])):
            total_hits += 1
            url = item.get("url", "")
            if not url:
                continue
            if url not in seen:
                seen[url] = {
                    "title": item.get("title", ""),
                    "url": url,
                    "snippet": (item.get("content") or "")[:500],
                    "engine": item.get("engine", ""),
                    "matched_by": 1,
                    "_best_rank": rank,
                }
            else:
                entry = seen[url]
                entry["matched_by"] += 1
                if rank < entry["_best_rank"]:
                    entry["_best_rank"] = rank
        for pair in batch.get("unresponsive", []):
            # SearXNG returns either ["name", "reason"] or {...} depending
            # on version; accept both shapes.
            if isinstance(pair, (list, tuple)) and len(pair) >= 2:
                name, reason = str(pair[0]), str(pair[1])
            elif isinstance(pair, dict):
                name = str(pair.get("name") or pair.get("engine") or "")
                reason = str(pair.get("reason") or pair.get("error") or "")
            else:
                continue
            if name:
                unresponsive[name] = reason
        if batch.get("transport_error"):
            transport_errors += 1

    # Rank: more matches first, then earlier best rank. Drop the internal
    # tracking key before returning.
    merged = sorted(
        seen.values(),
        key=lambda r: (-r["matched_by"], r["_best_rank"]),
    )
    for r in merged:
        r.pop("_best_rank", None)

    out: dict = {
        "queries_executed": query_list,
        "total_hits": total_hits,
        "results": merged[:top_k],
    }

    # Surface engine degradation to the caller.
    requested_engines = [e for e in _SEARXNG_ENGINES.split(",") if e]
    if unresponsive:
        out["engines_unresponsive"] = [
            [name, reason] for name, reason in sorted(unresponsive.items())
        ]
    # Total-degradation warning: if every requested engine was unresponsive
    # AND we got no results, the tool is effectively broken on this call.
    # Emit an explicit warning string so the model surfaces "my tool is
    # degraded" to the user instead of concluding the topic doesn't exist.
    all_engines_down = (
        len(unresponsive) >= len(requested_engines) and len(requested_engines) > 0
    )
    transport_total = transport_errors == len(query_list) and len(query_list) > 0
    if total_hits == 0 and (all_engines_down or transport_total):
        if transport_total:
            reason = (
                f"Could not reach the search backend ({SEARXNG_URL}) on "
                f"any of the {len(query_list)} queries."
            )
        else:
            engine_list = ", ".join(sorted(unresponsive.keys()))
            reason = (
                f"All configured search engines ({engine_list}) were "
                "unresponsive on this call (rate-limited, blocked, or "
                "behind a CAPTCHA)."
            )
        out["warning"] = (
            f"{reason} Treat this zero-result response as TOOL FAILURE, "
            "not 'no information found'. Tell the user the search "
            "backend is degraded and ask them to provide a direct URL "
            "or DOI if they have one. Do not infer that the topic is "
            "obscure or non-existent from this result."
        )

    return out


# Map-reduce summarization parameters (tunable)
_FETCH_CHUNK_CHARS = 2000       # ~500 tokens per chunk
_FETCH_MAX_CHUNKS = 20          # hard cap: 40k chars of original content
_FETCH_CHUNK_SUMMARY_TOKENS = 400   # per-chunk summary budget
_FETCH_META_SUMMARY_TOKENS = 900    # final meta-summary budget
_FETCH_SHORT_SUMMARY_TOKENS = 700   # single-chunk direct summary budget
_FETCH_MAX_TOTAL_CHARS = _FETCH_CHUNK_CHARS * _FETCH_MAX_CHUNKS
# vLLM serves at --max-num-seqs 2; cap our fan-out to a small over-subscription
# so the loop doesn't queue 20 requests behind 2 GPU slots.
_FETCH_CHUNK_CONCURRENCY = 4


def _chunk_for_summarization(text: str, target_chars: int = _FETCH_CHUNK_CHARS) -> list[str]:
    """
    Split text into ~target_chars chunks along paragraph boundaries.
    Oversized paragraphs are hard-split; the last chunk absorbs whatever's
    left. Returns `[text]` unchanged if the whole thing already fits.
    """
    import re

    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= target_chars:
        return [text]

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paragraphs:
        # No paragraph breaks found — slice by char count.
        return [text[i : i + target_chars] for i in range(0, len(text), target_chars)]

    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        if len(para) > target_chars:
            if current:
                chunks.append(current)
                current = ""
            while len(para) > target_chars:
                chunks.append(para[:target_chars])
                para = para[target_chars:]
            current = para
            continue
        if not current:
            current = para
        elif len(current) + len(para) + 2 <= target_chars:
            current = f"{current}\n\n{para}"
        else:
            chunks.append(current)
            current = para
    if current:
        chunks.append(current)
    return chunks


async def web_fetch_content(
    url: str,
    summary_instruction: str = "Summarize the main points and key findings, focusing on factual content.",
) -> dict:
    """
    Fetch a URL, extract its main content, and return a CONDENSED SUMMARY.

    Raw content never reaches the caller — this tool always returns a
    summary. For short pages, a single `llm_summarize` call is enough. For
    long pages, the extract is chunked along paragraph boundaries, each
    chunk is summarised in parallel via separate vLLM calls, and a final
    meta-summary composes the chunk summaries into one coherent output.

    Args:
        url: The URL to fetch.
        summary_instruction: Instructions that bias the summariser (e.g.
            "focus on methodology", "only extract conclusions about X").
            Applied to both per-chunk and meta-summary prompts.

    Returns:
        Dict with:
            url: the fetched URL
            summary: condensed text (typically <2500 chars regardless of page length)
            chunks_summarized: int
            successful_chunks: int (may be < chunks_summarized if some vLLM calls failed)
            total_chars_original: int (length of extracted text before truncation)
            summary_chars: int
            truncated: bool (true when page exceeded ~40k chars)
        Or {"error": "...", "url": ...} on failure.
    """
    try:
        import trafilatura

        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            response = await client.get(url, headers={
                "User-Agent": "Mozilla/5.0 (compatible; MuninBot/1.0; +https://muninai.org)"
            })
            response.raise_for_status()
            html = response.text
    except httpx.TimeoutException:
        return {"error": f"Request timed out for URL: {url}", "url": url}
    except httpx.HTTPStatusError as e:
        return {"error": f"HTTP {e.response.status_code} for URL: {url}", "url": url}
    except Exception as e:
        return {"error": f"Failed to fetch URL: {str(e)}", "url": url}

    try:
        content = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=True,
            favor_precision=True,
        )
    except Exception as e:
        return {"error": f"Content extraction failed: {str(e)}", "url": url}

    if not content:
        return {"error": "Could not extract content from URL", "url": url}

    total_chars = len(content)
    truncated = False
    if total_chars > _FETCH_MAX_TOTAL_CHARS:
        content = content[:_FETCH_MAX_TOTAL_CHARS]
        truncated = True

    chunks = _chunk_for_summarization(content)
    if not chunks:
        return {"error": "Extracted content was empty after chunking", "url": url}

    # --- Short path: one chunk, one summarizer call -------------------------
    if len(chunks) == 1:
        result = await llm_summarize(
            chunks[0],
            f"{summary_instruction}\n\nKeep the summary under 500 words.",
            max_tokens=_FETCH_SHORT_SUMMARY_TOKENS,
        )
        if isinstance(result, dict) and "error" in result:
            return {
                "error": f"Summarization failed: {result['error']}",
                "url": url,
            }
        summary = (result or {}).get("summary", "").strip()
        if not summary:
            return {"error": "Summarizer returned empty output", "url": url}
        return {
            "url": url,
            "summary": summary,
            "chunks_summarized": 1,
            "successful_chunks": 1,
            "total_chars_original": total_chars,
            "summary_chars": len(summary),
            "truncated": truncated,
        }

    # --- Map: summarize each chunk in parallel, capped by a semaphore ------
    chunk_instruction = (
        f"{summary_instruction}\n\n"
        "This is one section of a longer web page. Summarize only this "
        "section in under 150 words. Preserve named entities, numbers, and "
        "any claims that would be needed to understand the page as a whole."
    )
    sem = asyncio.Semaphore(_FETCH_CHUNK_CONCURRENCY)

    async def _summarize_chunk(chunk_text: str) -> dict:
        async with sem:
            return await llm_summarize(
                chunk_text,
                chunk_instruction,
                max_tokens=_FETCH_CHUNK_SUMMARY_TOKENS,
            )

    chunk_results = await asyncio.gather(
        *(_summarize_chunk(c) for c in chunks),
        return_exceptions=True,
    )

    chunk_summaries: list[str] = []
    for i, r in enumerate(chunk_results):
        if isinstance(r, Exception):
            logger.warning("web_fetch chunk %d raised: %s", i, r)
            continue
        if not isinstance(r, dict):
            continue
        if "error" in r:
            logger.warning("web_fetch chunk %d error: %s", i, r["error"])
            continue
        text = (r.get("summary") or "").strip()
        if text:
            chunk_summaries.append(f"[Section {i + 1}]\n{text}")

    if not chunk_summaries:
        return {
            "error": "All chunk summarizations failed",
            "url": url,
            "total_chars_original": total_chars,
        }

    # --- Reduce: compose chunk summaries into one coherent summary --------
    combined_chunk_text = "\n\n".join(chunk_summaries)
    meta_instruction = (
        f"Below are summaries of sequential sections of a single web page. "
        f"Compose them into one coherent summary focused on: "
        f"{summary_instruction}\n\n"
        "Keep the final summary under 600 words. Remove duplicate points. "
        "Do not refer to the sections by number in the output."
    )
    meta_result = await llm_summarize(
        combined_chunk_text,
        meta_instruction,
        max_tokens=_FETCH_META_SUMMARY_TOKENS,
    )

    if isinstance(meta_result, dict) and "summary" in meta_result and meta_result["summary"]:
        summary = meta_result["summary"].strip()
    else:
        # Fallback: strip the `[Section N]` tags and join the chunk summaries
        # directly. Less polished but still condensed.
        import re
        summary = "\n\n".join(
            re.sub(r"^\[Section \d+\]\n?", "", s).strip()
            for s in chunk_summaries
        )
        logger.warning("web_fetch meta-summary failed, returning concatenated chunks")

    return {
        "url": url,
        "summary": summary,
        "chunks_summarized": len(chunks),
        "successful_chunks": len(chunk_summaries),
        "total_chars_original": total_chars,
        "summary_chars": len(summary),
        "truncated": truncated,
    }
