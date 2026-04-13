"""
Paper-related MCP tools.

Provides:
- paper_search: Semantic search over papers using SPECTER
- semantic_scholar_search: Search Semantic Scholar API (200M+ papers)
- paper_lookup: Look up paper by DOI
- get_citations: Get papers citing a paper
- get_references: Get papers cited by a paper
- get_author_papers: Get papers by an author
- get_paper_pdf: Check PDF availability and get download link
- check_papers_availability: Batch check PDF availability for multiple DOIs
"""

import os
from urllib.parse import quote

import httpx

from database import get_qdrant, get_neo4j, get_specter, PAPERS_PDF_DIR

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
        print(f"[WARNING] Failed to get citation counts: {e}")
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


async def paper_search(query: str, top_k: int = 5) -> dict:
    """
    Search papers using SPECTER embeddings.

    Args:
        query: Search query
        top_k: Number of results

    Returns:
        Dict with 'results' list
    """
    qdrant = get_qdrant()
    specter = get_specter()

    if not qdrant or not specter:
        return {"error": "Paper search not available (database or model not loaded)"}

    try:
        # Generate query embedding
        query_vector = specter.encode(query).tolist()

        # Search Qdrant (qdrant-client 1.7+ uses query_points instead of search)
        results = qdrant.query_points(
            collection_name="papers",
            query=query_vector,
            limit=top_k
        )

        papers = []
        for r in results.points:
            payload = r.payload or {}
            authors = payload.get("authors", [])
            if isinstance(authors, list):
                authors = [a if isinstance(a, str) else a.get("name", "") for a in authors][:5]

            papers.append({
                "title": payload.get("title"),
                "doi": payload.get("doi"),
                "year": payload.get("year"),
                "authors": authors,
                "score": round(r.score, 3)
            })

        return {"results": papers}

    except Exception as e:
        return {"error": f"Paper search failed: {str(e)}"}


async def semantic_scholar_search(query: str, top_k: int = 10, year: str = "") -> dict:
    """
    Search the Semantic Scholar API for academic papers.

    Provides access to 200M+ papers across all fields. Use this for broad
    academic search when local paper database may not have relevant results.

    Args:
        query: Search query
        top_k: Number of results (max 100)
        year: Optional year filter (e.g., "2020-2024", "2024-", "2024")

    Returns:
        Dict with 'results' list of papers
    """
    top_k = min(top_k, 100)
    fields = "paperId,title,authors,year,citationCount,abstract,externalIds,journal,tldr,publicationTypes"

    params = {
        "query": query,
        "limit": top_k,
        "fields": fields,
    }
    if year:
        params["year"] = year

    headers = {}
    if SEMANTIC_SCHOLAR_API_KEY:
        headers["x-api-key"] = SEMANTIC_SCHOLAR_API_KEY

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                f"{SEMANTIC_SCHOLAR_BASE_URL}/paper/search",
                params=params,
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()

        papers = []
        for paper in data.get("data", []):
            # Extract DOI from externalIds
            external_ids = paper.get("externalIds") or {}
            doi = external_ids.get("DOI", "")

            # Extract author names
            authors = []
            for author in (paper.get("authors") or [])[:10]:
                name = author.get("name", "")
                if name:
                    authors.append(name)

            # Extract TLDR if available
            tldr = ""
            if paper.get("tldr") and paper["tldr"].get("text"):
                tldr = paper["tldr"]["text"]

            papers.append({
                "title": paper.get("title", ""),
                "doi": doi,
                "year": paper.get("year"),
                "authors": authors,
                "citation_count": paper.get("citationCount", 0),
                "abstract": (paper.get("abstract") or "")[:500],
                "tldr": tldr,
                "journal": (paper.get("journal") or {}).get("name", ""),
                "semantic_scholar_id": paper.get("paperId", ""),
            })

        return {
            "total": data.get("total", len(papers)),
            "results": papers,
        }

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 429:
            return {"error": "Semantic Scholar rate limit exceeded. Try again in a moment."}
        return {"error": f"Semantic Scholar API error: {e.response.status_code}"}
    except Exception as e:
        return {"error": f"Semantic Scholar search failed: {str(e)}"}


async def paper_lookup(doi: str) -> dict:
    """
    Look up detailed paper information by DOI.

    Args:
        doi: Paper DOI

    Returns:
        Dict with paper metadata or error
    """
    qdrant = get_qdrant()
    if not qdrant:
        return {"error": "Vector database not available"}

    try:
        from qdrant_client.models import Filter, FieldCondition, MatchValue

        results = qdrant.scroll(
            collection_name="papers",
            scroll_filter=Filter(
                must=[FieldCondition(key="doi", match=MatchValue(value=doi))]
            ),
            limit=1,
            with_payload=True
        )

        if not results[0]:
            return {"error": f"Paper not found: {doi}"}

        paper_data = results[0][0].payload
        citation_info = get_citation_counts([doi]).get(doi, {})

        authors = paper_data.get("authors", [])
        if isinstance(authors, list):
            authors = [a if isinstance(a, str) else a.get("name", "") for a in authors]

        return {
            "title": paper_data.get("title"),
            "doi": doi,
            "authors": authors,
            "year": paper_data.get("year"),
            "journal": paper_data.get("journal"),
            "abstract": paper_data.get("abstract"),
            "citation_count": citation_info.get("citation_count", 0),
            "reference_count": citation_info.get("reference_count", 0)
        }

    except Exception as e:
        return {"error": f"Paper lookup failed: {str(e)}"}


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
