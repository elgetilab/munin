"""
Paper-related MCP tools.

Provides:
- paper_search: Semantic search over papers using SPECTER (multi-query fan-out)
- semantic_scholar_search: Search Semantic Scholar API (200M+ papers, multi-query fan-out)
- paper_lookup: Look up paper by DOI
- get_citations: Get papers citing a paper
- get_references: Get papers cited by a paper
- get_author_papers: Get papers by an author
- get_paper_pdf: Check PDF availability and get download link
- check_papers_availability: Batch check PDF availability for multiple DOIs
"""

import asyncio
import logging
import os
from typing import Optional
from urllib.parse import quote

import httpx

from database import get_qdrant, get_neo4j, get_specter, PAPERS_PDF_DIR
from .query_expansion import expand_queries
from ..context import current_query_tags

logger = logging.getLogger(__name__)


def _build_tag_filter(tags: Optional[list[dict]]):
    """Translate `tags` (list of {kind, value}) into a Qdrant filter object,
    or None when no filter applies.

    - `topic`        → filter on payload.topic_slug (§15 writes this).
    - `group`        → filter on array-member payload.contributors[].group_slug.
    - `contributor`  → filter on array-member payload.contributors[].username
                       (for the #@username shortcut; papers without a
                       contributors[] array won't match, which is correct).

    Multiple tags AND-combine (Qdrant `must`). Unknown kinds are silently
    dropped so the model passing a malformed tag doesn't break the call.
    """
    if not tags:
        return None
    try:
        from qdrant_client.http import models as qm
    except Exception:
        return None
    conds: list = []
    for tag in tags:
        if not isinstance(tag, dict):
            continue
        kind = (tag.get("kind") or "").strip().lower()
        value = tag.get("value")
        if not isinstance(value, str) or not value.strip():
            continue
        value = value.strip().lower()
        if kind == "topic":
            conds.append(
                qm.FieldCondition(key="topic_slug", match=qm.MatchValue(value=value))
            )
        elif kind == "group":
            conds.append(
                qm.FieldCondition(
                    key="contributors[].group_slug",
                    match=qm.MatchValue(value=value),
                )
            )
        elif kind == "contributor":
            conds.append(
                qm.FieldCondition(
                    key="contributors[].username",
                    match=qm.MatchValue(value=value),
                )
            )
    if not conds:
        return None
    return qm.Filter(must=conds)

# Semantic Scholar API
SEMANTIC_SCHOLAR_API_KEY = os.getenv("SEMANTIC_SCHOLAR_API_KEY", "")
SEMANTIC_SCHOLAR_BASE_URL = "https://api.semanticscholar.org/graph/v1"

# Public URL for download links (accessible from outside the cluster)
PUBLIC_URL = os.getenv("MUNIN_PUBLIC_URL", "https://search.muninai.org")


def get_citation_counts(dois: list[str]) -> dict:
    """
    Get citation and reference counts for a list of DOIs from Neo4j.

    Returns:
        Dict mapping DOI -> {citation_count, reference_count}
    """
    neo4j = get_neo4j()
    if not neo4j or not dois:
        return {}

    try:
        with neo4j.session() as session:
            result = session.run("""
                UNWIND $dois as doi
                OPTIONAL MATCH (p:Paper {doi: doi})
                OPTIONAL MATCH (citing:Paper)-[:CITES]->(p)
                OPTIONAL MATCH (p)-[:CITES]->(ref:Paper)
                RETURN doi,
                       count(DISTINCT citing) as citation_count,
                       count(DISTINCT ref) as reference_count
            """, dois=dois)

            return {
                r["doi"]: {
                    "citation_count": r["citation_count"],
                    "reference_count": r["reference_count"]
                }
                for r in result if r["doi"]
            }
    except Exception as e:
        logger.warning("Failed to get citation counts: %s", e)
        return {}


def get_pdf_path(doi: str) -> str | None:
    """
    Get the PDF file path for a paper by DOI.

    Args:
        doi: Paper DOI

    Returns:
        Path to PDF file if exists, None otherwise
    """
    if not doi:
        return None

    # DOI sanitization - match paper_crawler.py logic
    safe_doi = doi.replace("/", "_").replace(":", "_")

    # Primary format: doi_10.1234_example.pdf (from paper_crawler.py)
    pdf_path = os.path.join(PAPERS_PDF_DIR, f"doi_{safe_doi}.pdf")
    if os.path.exists(pdf_path):
        return pdf_path

    # Legacy format: 10.1234_example.pdf (without prefix)
    legacy_path = os.path.join(PAPERS_PDF_DIR, f"{safe_doi}.pdf")
    if os.path.exists(legacy_path):
        return legacy_path

    # Case-insensitive fallback
    if PAPERS_PDF_DIR and os.path.isdir(PAPERS_PDF_DIR):
        target_lower = f"doi_{safe_doi}.pdf".lower()
        try:
            for f in os.listdir(PAPERS_PDF_DIR):
                if f.lower() == target_lower:
                    return os.path.join(PAPERS_PDF_DIR, f)
        except OSError:
            pass

    return None


def _paper_dedupe_key(paper: dict) -> str:
    """Best-effort identifier for a paper to dedupe across queries."""
    doi = (paper.get("doi") or "").strip().lower()
    if doi:
        return f"doi:{doi}"
    pid = (paper.get("paper_id") or paper.get("semantic_scholar_id") or "").strip().lower()
    if pid:
        return f"id:{pid}"
    title = (paper.get("title") or "").strip().lower()
    return f"title:{title}" if title else f"unk:{id(paper)}"


def _local_download_fields(doi: str) -> dict:
    """
    Return {"download_url": ..., "local_pdf_available": True} when a PDF for
    this DOI exists in the local corpus, or an empty dict otherwise. Used by
    every search tool to surface clickable downloads on local hits without
    needing a follow-up `get_paper_pdf` tool call.
    """
    if not doi:
        return {}
    try:
        if get_pdf_path(doi):
            return {
                "download_url": f"{PUBLIC_URL}/paper/{quote(doi, safe='')}/pdf",
                "local_pdf_available": True,
            }
    except Exception:
        pass
    return {}


def _qdrant_search_one(
    qdrant, specter, q: str, top_k: int, query_filter=None
) -> list[dict]:
    """Run one SPECTER-embedded Qdrant query_points call and shape the
    payload. `query_filter` — a Qdrant `Filter` from `_build_tag_filter`
    — scopes the search to a topic/group/contributor tag (§28)."""
    try:
        vec = specter.encode(q).tolist()
        results = qdrant.query_points(
            collection_name="papers",
            query=vec,
            limit=top_k,
            query_filter=query_filter,
        )
        out: list[dict] = []
        for r in results.points:
            payload = r.payload or {}
            authors = payload.get("authors", [])
            if isinstance(authors, list):
                authors = [a if isinstance(a, str) else a.get("name", "") for a in authors][:5]
            doi = payload.get("doi")
            row = {
                "title": payload.get("title"),
                "doi": doi,
                "year": payload.get("year"),
                "authors": authors,
                "score": round(float(r.score), 3),
                "matched_query": q,
            }
            # Surface contributor attribution when present so the model
            # can cite it honestly ("a paper contributed by Zeitler Lab").
            contributors = payload.get("contributors")
            if contributors:
                # Keep the shape small for the tool result; the model
                # doesn't need emails or upload times.
                row["contributors"] = [
                    {
                        "display_name": c.get("display_name"),
                        "group_slug": c.get("group_slug"),
                        "group_display_name": c.get("group_display_name"),
                    }
                    for c in contributors
                    if isinstance(c, dict)
                ]
            topic_label = payload.get("topic_label")
            if topic_label and payload.get("topic_slug") != "unclustered":
                row["topic"] = {
                    "label": topic_label,
                    "slug": payload.get("topic_slug"),
                }
            # Local hits always have a PDF on disk (they came from the local
            # papers Qdrant collection). Surface the download link so the
            # model doesn't need a follow-up get_paper_pdf call.
            row.update(_local_download_fields(doi))
            out.append(row)
        return out
    except Exception as e:
        logger.warning("paper_search %r failed: %s", q, e)
        return []


async def paper_search(
    query: Optional[str] = None,
    queries: Optional[list[str]] = None,
    top_k: int = 5,
    tags: Optional[list[dict]] = None,
) -> dict:
    """
    Search the local papers corpus using SPECTER embeddings with multi-query
    fan-out.

    Behaviour:
        - If `queries` is a list: run exactly those queries (no expansion).
        - Elif `query` is a string: expand it to 3-5 variants and run them.
        - Results deduped by DOI/paper id/title; max Qdrant score across
          queries is kept; `matched_by` counts how many queries surfaced the
          paper.

    Args:
        query: Single query. Will be fanned out automatically.
        queries: Explicit list of queries. Takes precedence.
        top_k: Max number of deduped results to return.
        tags: Optional scope filter — list of {"kind": "topic"|"group"|
              "contributor", "value": str}. Tags AND-combine (paper must
              match all). Falls back to the `current_query_tags` ContextVar
              set by chat_service when omitted, so #zeitler in the chat
              input automatically scopes every paper_search call.

    Returns:
        Dict with queries_executed, total_hits, applied_tags, results.
    """
    qdrant = get_qdrant()
    specter = get_specter()
    if not qdrant or not specter:
        return {"error": "Paper search not available (database or model not loaded)"}

    if queries:
        query_list = [q.strip() for q in queries if q and q.strip()]
    elif query:
        query_list = await expand_queries(query, n=5)
    else:
        return {"error": "paper_search requires either 'query' or 'queries'"}

    if not query_list:
        return {"error": "paper_search got empty query list after normalization"}

    # §28: explicit `tags` argument overrides the ContextVar; None falls
    # back to the conversation's inherited tags.
    effective_tags = tags if tags is not None else current_query_tags.get()
    query_filter = _build_tag_filter(effective_tags)

    # SPECTER encoding + Qdrant queries are CPU-bound / blocking; run them in
    # a thread pool so multiple queries can progress in parallel.
    loop = asyncio.get_running_loop()
    per_query = max(top_k, 5)
    batches: list[list[dict]] = await asyncio.gather(
        *(
            loop.run_in_executor(
                None, _qdrant_search_one, qdrant, specter, q, per_query, query_filter
            )
            for q in query_list
        )
    )

    seen: dict[str, dict] = {}
    total_hits = 0
    for hits in batches:
        for p in hits:
            total_hits += 1
            key = _paper_dedupe_key(p)
            existing = seen.get(key)
            if existing is None:
                seen[key] = {**p, "matched_by": 1}
            else:
                existing["matched_by"] += 1
                if p.get("score", 0) > existing.get("score", 0):
                    existing["score"] = p["score"]

    merged = sorted(
        seen.values(),
        key=lambda r: (-r.get("matched_by", 1), -r.get("score", 0)),
    )

    response: dict = {
        "queries_executed": query_list,
        "total_hits": total_hits,
        "results": merged[:top_k],
    }
    if effective_tags:
        # Echo the applied filter so the model can be honest about scope
        # in its response ("I searched papers contributed by Zeitler Lab").
        response["applied_tags"] = effective_tags
    return response


async def _semantic_scholar_one(
    client: httpx.AsyncClient,
    q: str,
    top_k: int,
    year: str,
) -> list[dict]:
    """Run one Semantic Scholar /paper/search query and shape the results.

    Each returned row carries:
      * `download_url` + `local_pdf_available=True` if the DOI exists in
        our local corpus (model can render an immediate download link).
      * `open_access_pdf` if S2 reports an OA URL, even when the paper is
        not in our local corpus (model can render a "read online" link).
    """
    fields = (
        "paperId,title,authors,year,citationCount,abstract,externalIds,"
        "journal,tldr,publicationTypes,openAccessPdf"
    )
    params = {
        "query": q,
        "limit": top_k,
        "fields": fields,
    }
    if year:
        params["year"] = year

    headers = {}
    if SEMANTIC_SCHOLAR_API_KEY:
        headers["x-api-key"] = SEMANTIC_SCHOLAR_API_KEY

    try:
        response = await client.get(
            f"{SEMANTIC_SCHOLAR_BASE_URL}/paper/search",
            params=params,
            headers=headers,
        )
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 429:
            logger.warning("semantic_scholar_search %r rate limited", q)
        else:
            logger.warning(
                "semantic_scholar_search %r HTTP %d",
                q, e.response.status_code,
            )
        return []
    except Exception as e:
        logger.warning("semantic_scholar_search %r failed: %s", q, e)
        return []

    out: list[dict] = []
    for paper in data.get("data", []):
        external_ids = paper.get("externalIds") or {}
        doi = external_ids.get("DOI", "")

        authors: list[str] = []
        for author in (paper.get("authors") or [])[:10]:
            name = author.get("name", "")
            if name:
                authors.append(name)

        tldr = ""
        if paper.get("tldr") and paper["tldr"].get("text"):
            tldr = paper["tldr"]["text"]

        oa_pdf = (paper.get("openAccessPdf") or {}).get("url") or None

        row = {
            "title": paper.get("title", ""),
            "doi": doi,
            "year": paper.get("year"),
            "authors": authors,
            "citation_count": paper.get("citationCount", 0),
            "abstract": (paper.get("abstract") or "")[:500],
            "tldr": tldr,
            "journal": (paper.get("journal") or {}).get("name", ""),
            "semantic_scholar_id": paper.get("paperId", ""),
            "open_access_pdf": oa_pdf,
            "matched_query": q,
        }
        # If the DOI is also in our local corpus, surface a direct download
        # link so the model prefers it over the OA mirror. Local copies are
        # GROBID-parsed and SPECTER-embedded, often with cleaner extraction.
        row.update(_local_download_fields(doi))
        out.append(row)
    return out


async def semantic_scholar_search(
    query: Optional[str] = None,
    queries: Optional[list[str]] = None,
    top_k: int = 10,
    year: str = "",
) -> dict:
    """
    Search the Semantic Scholar API for academic papers with multi-query
    fan-out. 200M+ papers across all fields.

    Behaviour:
        - `queries` list → run exactly those queries in parallel, no expansion.
        - `query` string → expand via expand_queries, then fan out.
        - Results deduped by DOI/paperId; `matched_by` counts hits across
          queries, used as the primary sort key ahead of citation count.

    Args:
        query: Single query. Automatically expanded.
        queries: Explicit list of queries. Takes precedence.
        top_k: Max deduped results (max 100).
        year: Optional year filter applied to every query (e.g. "2020-2024").

    Returns:
        Dict with queries_executed, total, results.
    """
    top_k = min(top_k, 100)

    if queries:
        query_list = [q.strip() for q in queries if q and q.strip()]
    elif query:
        query_list = await expand_queries(query, n=5)
    else:
        return {"error": "semantic_scholar_search requires either 'query' or 'queries'"}

    if not query_list:
        return {"error": "semantic_scholar_search got empty query list after normalization"}

    per_query = max(top_k, 10)
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            batches = await asyncio.gather(
                *(_semantic_scholar_one(client, q, per_query, year) for q in query_list)
            )
    except Exception as e:
        return {"error": f"Semantic Scholar search failed: {str(e)}"}

    seen: dict[str, dict] = {}
    total_hits = 0
    for hits in batches:
        for paper in hits:
            total_hits += 1
            key = _paper_dedupe_key(paper)
            existing = seen.get(key)
            if existing is None:
                seen[key] = {**paper, "matched_by": 1}
            else:
                existing["matched_by"] += 1
                # Keep the higher citation count if they disagree.
                if (paper.get("citation_count") or 0) > (existing.get("citation_count") or 0):
                    existing["citation_count"] = paper.get("citation_count", 0)

    merged = sorted(
        seen.values(),
        key=lambda r: (-r.get("matched_by", 1), -(r.get("citation_count") or 0)),
    )

    return {
        "queries_executed": query_list,
        "total": total_hits,
        "results": merged[:top_k],
    }


CROSSREF_BASE_URL = "https://api.crossref.org"


def _paper_lookup_local(doi: str) -> Optional[dict]:
    """
    Try the local Qdrant papers corpus. Returns None on miss. This is the
    fastest source and is the only one that carries citation-graph counts
    from our Neo4j mirror, so we prefer it when available.
    """
    qdrant = get_qdrant()
    if not qdrant:
        return None

    try:
        from qdrant_client.models import Filter, FieldCondition, MatchValue

        results = qdrant.scroll(
            collection_name="papers",
            scroll_filter=Filter(
                must=[FieldCondition(key="doi", match=MatchValue(value=doi))]
            ),
            limit=1,
            with_payload=True,
        )
        if not results[0]:
            return None

        payload = results[0][0].payload or {}
        citation_info = get_citation_counts([doi]).get(doi, {})

        authors = payload.get("authors", [])
        if isinstance(authors, list):
            authors = [a if isinstance(a, str) else a.get("name", "") for a in authors]

        return {
            "title": payload.get("title"),
            "doi": doi,
            "authors": authors,
            "year": payload.get("year"),
            "journal": payload.get("journal"),
            "abstract": payload.get("abstract"),
            "citation_count": citation_info.get("citation_count", 0),
            "reference_count": citation_info.get("reference_count", 0),
            "source": "local",
        }
    except Exception as e:
        logger.warning("local paper_lookup failed for %s: %s", doi, e)
        return None


async def _paper_lookup_semantic_scholar(doi: str) -> Optional[dict]:
    """
    Try Semantic Scholar's `/graph/v1/paper/DOI:{doi}` endpoint. Returns None
    on 404 or any error. S2 has the richest metadata (TLDR, abstract,
    citation counts, open-access PDF links) when it has the paper at all.
    """
    fields = (
        "paperId,title,authors,year,citationCount,abstract,externalIds,"
        "journal,tldr,venue,openAccessPdf,publicationTypes"
    )
    headers = {}
    if SEMANTIC_SCHOLAR_API_KEY:
        headers["x-api-key"] = SEMANTIC_SCHOLAR_API_KEY

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                f"{SEMANTIC_SCHOLAR_BASE_URL}/paper/DOI:{quote(doi, safe='')}",
                params={"fields": fields},
                headers=headers,
            )
            if response.status_code == 404:
                return None
            response.raise_for_status()
            paper = response.json()
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 429:
            logger.warning("semantic_scholar paper_lookup rate limited for %s", doi)
        else:
            logger.warning(
                "semantic_scholar paper_lookup HTTP %d for %s",
                e.response.status_code, doi,
            )
        return None
    except Exception as e:
        logger.warning("semantic_scholar paper_lookup failed for %s: %s", doi, e)
        return None

    if not paper or not paper.get("title"):
        return None

    authors: list[str] = []
    for author in (paper.get("authors") or [])[:15]:
        name = author.get("name", "")
        if name:
            authors.append(name)

    tldr = ""
    if paper.get("tldr") and paper["tldr"].get("text"):
        tldr = paper["tldr"]["text"]

    oa_pdf = (paper.get("openAccessPdf") or {}).get("url", "")
    journal_name = (paper.get("journal") or {}).get("name") or paper.get("venue") or ""

    return {
        "title": paper.get("title"),
        "doi": doi,
        "authors": authors,
        "year": paper.get("year"),
        "journal": journal_name,
        "abstract": (paper.get("abstract") or "").strip() or None,
        "tldr": tldr or None,
        "citation_count": paper.get("citationCount", 0),
        "open_access_pdf": oa_pdf or None,
        "semantic_scholar_id": paper.get("paperId"),
        "external_ids": paper.get("externalIds") or {},
        "source": "semantic_scholar",
    }


async def _paper_lookup_crossref(doi: str) -> Optional[dict]:
    """
    Try Crossref's `/works/{doi}` endpoint. Returns None on 404 or any error.
    Crossref is the most reliable source for recent DOIs (it's the DOI
    registry for most publishers) but has the sparsest metadata — no TLDR,
    abstracts are often missing or HTML-wrapped.
    """
    import re

    headers = {
        "User-Agent": (
            "MuninBot/1.0 (https://muninai.org; mailto:research@muninai.org)"
        )
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                f"{CROSSREF_BASE_URL}/works/{quote(doi, safe='')}",
                headers=headers,
            )
            if response.status_code == 404:
                return None
            response.raise_for_status()
            data = response.json()
    except Exception as e:
        logger.warning("crossref paper_lookup failed for %s: %s", doi, e)
        return None

    msg = data.get("message") or {}
    title_list = msg.get("title") or []
    title = title_list[0] if title_list else None
    if not title:
        return None

    authors: list[str] = []
    for author in (msg.get("author") or [])[:15]:
        given = (author.get("given") or "").strip()
        family = (author.get("family") or "").strip()
        name = f"{given} {family}".strip()
        if name:
            authors.append(name)

    year = None
    for field in ("issued", "published-print", "published", "created"):
        date_parts = ((msg.get(field) or {}).get("date-parts") or [[None]])
        first = date_parts[0] if date_parts else None
        if first and first[0]:
            year = first[0]
            break

    container = msg.get("container-title") or []
    journal = container[0] if container else ""

    abstract = (msg.get("abstract") or "").strip()
    if abstract:
        # Crossref abstracts are wrapped in <jats:p> et al.
        abstract = re.sub(r"<[^>]+>", " ", abstract)
        abstract = re.sub(r"\s+", " ", abstract).strip()

    return {
        "title": title,
        "doi": doi,
        "authors": authors,
        "year": year,
        "journal": journal,
        "abstract": abstract or None,
        "citation_count": msg.get("is-referenced-by-count", 0),
        "type": msg.get("type"),
        "publisher": msg.get("publisher"),
        "source": "crossref",
    }


async def paper_lookup(doi: str) -> dict:
    """
    Look up detailed paper information by DOI, cascading through sources:

        1. Local Qdrant papers corpus (fast, has citation graph from Neo4j)
        2. Semantic Scholar /graph/v1/paper/DOI:{doi} (richest metadata)
        3. Crossref /works/{doi} (most reliable for recent DOIs)

    Returns the first successful lookup with the originating source marked
    in the `source` field. Fails only if all three sources miss.

    Args:
        doi: Paper DOI (case-insensitive, slashes preserved).

    Returns:
        Dict with paper metadata + source field, or {"error": "..."} if
        the DOI could not be resolved anywhere.
    """
    doi = (doi or "").strip()
    if not doi:
        return {"error": "paper_lookup requires a non-empty DOI"}

    local = _paper_lookup_local(doi)
    if local and local.get("title"):
        return local

    s2 = await _paper_lookup_semantic_scholar(doi)
    if s2 and s2.get("title"):
        return s2

    crossref = await _paper_lookup_crossref(doi)
    if crossref and crossref.get("title"):
        return crossref

    return {
        "error": (
            f"Paper not found in local corpus, Semantic Scholar, or Crossref: {doi}"
        ),
        "doi": doi,
    }


async def get_citations(doi: str, limit: int = 20) -> dict:
    """
    Get papers that cite the given paper.

    Args:
        doi: Paper DOI
        limit: Max results

    Returns:
        Dict with citing papers
    """
    neo4j = get_neo4j()
    if not neo4j:
        return {"error": "Neo4j graph database not available"}

    try:
        with neo4j.session() as session:
            result = session.run("""
                MATCH (citing:Paper)-[:CITES]->(p:Paper {doi: $doi})
                RETURN citing.doi as doi,
                       citing.title as title,
                       citing.year as year
                ORDER BY citing.year DESC
                LIMIT $limit
            """, doi=doi, limit=limit)

            citing_papers = [dict(r) for r in result]

        return {"doi": doi, "citation_count": len(citing_papers), "citing_papers": citing_papers}

    except Exception as e:
        return {"error": f"Failed to get citations: {str(e)}"}


async def get_references(doi: str, limit: int = 50) -> dict:
    """
    Get papers cited by the given paper.

    Args:
        doi: Paper DOI
        limit: Max results

    Returns:
        Dict with referenced papers
    """
    neo4j = get_neo4j()
    if not neo4j:
        return {"error": "Neo4j graph database not available"}

    try:
        with neo4j.session() as session:
            result = session.run("""
                MATCH (p:Paper {doi: $doi})-[:CITES]->(ref:Paper)
                RETURN ref.doi as doi,
                       ref.title as title,
                       ref.year as year
                ORDER BY ref.year DESC
                LIMIT $limit
            """, doi=doi, limit=limit)

            references = [dict(r) for r in result]

        return {"doi": doi, "reference_count": len(references), "references": references}

    except Exception as e:
        return {"error": f"Failed to get references: {str(e)}"}


async def get_author_papers(author_name: str, limit: int = 50) -> dict:
    """
    Get papers by an author (partial name match).

    Args:
        author_name: Author name (partial match supported)
        limit: Max results

    Returns:
        Dict with author's papers
    """
    neo4j = get_neo4j()
    if not neo4j:
        return {"error": "Neo4j graph database not available"}

    try:
        with neo4j.session() as session:
            result = session.run("""
                MATCH (a:Author)-[:AUTHORED]->(p:Paper)
                WHERE toLower(a.name) CONTAINS toLower($name)
                RETURN DISTINCT p.doi as doi,
                       p.title as title,
                       p.year as year,
                       a.name as author_name
                ORDER BY p.year DESC
                LIMIT $limit
            """, name=author_name, limit=limit)

            papers = [dict(r) for r in result]

        return {"author_query": author_name, "paper_count": len(papers), "papers": papers}

    except Exception as e:
        return {"error": f"Failed to get author papers: {str(e)}"}


async def get_paper_pdf(doi: str) -> dict:
    """
    Check if a PDF is available for the paper and get download link.

    Args:
        doi: Paper DOI (e.g., "10.1038/nature12373")

    Returns:
        Dict with pdf_available, download_url (public), and suggestions if not available
    """
    pdf_path = get_pdf_path(doi)
    encoded_doi = quote(doi, safe='')

    if pdf_path:
        return {
            "doi": doi,
            "pdf_available": True,
            "download_url": f"{PUBLIC_URL}/paper/{encoded_doi}/pdf",
            "message": f"PDF available for download at {PUBLIC_URL}/paper/{encoded_doi}/pdf"
        }

    return {
        "doi": doi,
        "pdf_available": False,
        "message": "PDF not available in local knowledge base",
        "suggestions": {
            "sci_hub": f"https://sci-hub.st/{doi}",
            "google_scholar": f"https://scholar.google.com/scholar?q={encoded_doi}"
        }
    }


async def check_papers_availability(dois: list[str]) -> dict:
    """
    Check PDF availability for multiple papers at once.

    Args:
        dois: List of DOIs to check (max 20)

    Returns:
        Dict with available and not_available lists, each with download info
    """
    if not dois:
        return {"error": "No DOIs provided"}

    if len(dois) > 20:
        return {"error": "Too many DOIs. Please check no more than 20 at a time."}

    available = []
    not_available = []

    for doi in dois:
        doi = doi.strip()
        if not doi:
            continue

        pdf_path = get_pdf_path(doi)
        encoded_doi = quote(doi, safe='')

        if pdf_path:
            available.append({
                "doi": doi,
                "download_url": f"{PUBLIC_URL}/paper/{encoded_doi}/pdf"
            })
        else:
            not_available.append({
                "doi": doi,
                "sci_hub": f"https://sci-hub.st/{doi}"
            })

    return {
        "total_checked": len(available) + len(not_available),
        "available_count": len(available),
        "not_available_count": len(not_available),
        "available": available,
        "not_available": not_available
    }
