#!/usr/bin/env python3
"""
==============================================================================
MUNIN RETRIEVAL SERVICE
==============================================================================
FastAPI service for RAG (Retrieval-Augmented Generation).
Queries multiple knowledge bases and returns ranked results.

Knowledge Bases:
    - papers: Scientific papers (Qdrant + SPECTER)
    - notion: Notion workspace (Qdrant + BGE-base)
    - web: Live web search (SearXNG)
    - graph: Citation graph (Neo4j)

Endpoints:
    # Core Retrieval
    GET  /health                    - Health check
    POST /retrieve                  - Retrieve from selected sources
    GET  /sources                   - List available sources

    # Citation Graph
    GET  /citations/{doi}           - Get papers that cite a paper
    GET  /references/{doi}          - Get papers cited by a paper
    GET  /author/{name}/papers      - Get papers by an author
    GET  /co-authors/{author_id}    - Get co-author network
    GET  /graph/stats               - Get citation graph statistics

    # Paper PDF Downloads
    GET  /paper/{doi}/pdf           - Download paper PDF
    GET  /paper/{doi}/pdf/exists    - Check if PDF exists

    # Hybrid Search (Vector + Graph)
    POST /search/hybrid             - Hybrid search with citation re-ranking
    POST /search/similar-by-authors - Find papers by same authors
    GET  /paper/{doi}/enriched      - Get paper with citation metadata

Usage:
    uvicorn main:app --host 0.0.0.0 --port 8080

Environment Variables:
    QDRANT_HOST, QDRANT_PORT      - Qdrant connection
    NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD - Neo4j connection
    SEARXNG_URL                   - SearXNG URL
    SPECTER_MODEL_PATH            - Path to SPECTER model
    BGE_MODEL_PATH                - Path to BGE model
==============================================================================
"""

import asyncio
import contextlib
import hashlib
import json
import os
import secrets
import shutil
import uuid
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import quote

import httpx
import yaml
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from sse_starlette.sse import EventSourceResponse

# Import from local modules
from database import (
    QDRANT_HOST, QDRANT_PORT, NEO4J_URI, SEARXNG_URL,
    PAPERS_PDF_DIR,
    DEEPRESEARCH_ENABLED,
    DEEPRESEARCH_QUEUE_DIR, DEEPRESEARCH_JOBS_DIR, SLURM_QUEUE_FILE,
    VLLM_URL,
    get_qdrant, get_neo4j, get_specter, get_bge,
    is_specter_loaded, is_bge_loaded,
)
import database  # for the runtime-configurable PAPERS_COLLECTION / paper encoder
from models import (
    RetrieveRequest, RetrievedDocument, RetrieveResponse,
    PaperNode, AuthorNode, CitationsResponse, ReferencesResponse,
    AuthorPapersResponse, CoAuthorNode, CoAuthorsResponse, GraphStatsResponse,
    HybridSearchRequest, EnrichedPaper, HybridSearchResponse,
    SimilarByAuthorsRequest, SimilarByAuthorsResponse,
    DeepResearchRequest, DeepResearchSubmitResponse, DeepResearchStatus,
    SlurmQueueEntry, GpuProcess, GpuInfo, SlurmQueueResponse,
)
from fastapi import UploadFile, File, Form

from mcp.endpoints import router as mcp_router
import chat_store
import personas as persona_module
import chat_service
import document_store
import agents as agents_pkg
import user_profile_store
import project_store
import artifact_store
from maintenance import read_maintenance
import stream_registry as stream_registry_module
import metrics as metrics_module
import metrics_proxy
from logging_config import configure_logging
import logging

# Wire JSON-formatted logging with request-context ContextVar enrichment.
# Must run at import time so anything below this line (and any module that
# imported `logging` before us) writes through the configured handler.
configure_logging()
logger = logging.getLogger(__name__)

# ==============================================================================
# FastAPI App
# ==============================================================================
app = FastAPI(
    title="Munin Retrieval Service",
    description="RAG retrieval API for multiple knowledge bases",
    version="1.0.0"
)

# Include MCP router
app.include_router(mcp_router)

# Expose Prometheus /metrics on the same FastAPI app (P1 #12). Caddy
# only proxies /api/* and /paper/* externally, so /metrics is naturally
# cluster-internal — no auth, scraped by a Prometheus running inside
# the cluster network.
metrics_module.mount_metrics(app)


# ==============================================================================
# Search UI (served at root)
# ==============================================================================
@app.get("/")
async def serve_root():
    """Minimal service identifier — handy for `curl` smoke tests.

    The actual user-facing UIs (search, research, chat, etc.) are
    served by Caddy on the VPS from `frontend/static/...`; this
    route exists only so the cluster service has something at `/`
    other than a 404."""
    return {"service": "munin-retrieval", "docs": "/docs", "health": "/health"}


# NOTE: Pydantic models are imported from models.py
# NOTE: Database connections are imported from database.py
# NOTE: MCP endpoints are imported from mcp/endpoints.py


# ==============================================================================
# Enrichment Helper Functions (used by API endpoints)
# ==============================================================================
# These helper functions are used by the hybrid search and enrichment endpoints
# get_citation_counts for DOIs is also in mcp/tools/papers.py for MCP tool use


# ==============================================================================
# Enrichment Helper Functions
# ==============================================================================
def get_citation_counts(dois: list[str]) -> dict[str, dict]:
    """
    Get citation and reference counts for a list of DOIs from Neo4j.

    Returns dict mapping DOI -> {citation_count, reference_count}
    """
    neo4j = get_neo4j()
    if not neo4j or not dois:
        return {}

    try:
        with neo4j.session() as session:
            result = session.run("""
                UNWIND $dois AS doi
                OPTIONAL MATCH (p:Paper {doi: doi})
                OPTIONAL MATCH (citing:Paper)-[:CITES]->(p)
                OPTIONAL MATCH (p)-[:CITES]->(referenced:Paper)
                RETURN doi,
                       count(DISTINCT citing) as citation_count,
                       count(DISTINCT referenced) as reference_count
            """, dois=dois)

            return {
                r["doi"]: {
                    "citation_count": r["citation_count"],
                    "reference_count": r["reference_count"]
                }
                for r in result
                if r["doi"]
            }
    except Exception as e:
        logger.exception("Citation count lookup failed")
        return {}


def get_paper_ids_citation_counts(paper_ids: list[str]) -> dict[str, dict]:
    """
    Get citation and reference counts for a list of paper IDs from Neo4j.

    Returns dict mapping paper_id -> {citation_count, reference_count, doi}
    """
    neo4j = get_neo4j()
    if not neo4j or not paper_ids:
        return {}

    try:
        with neo4j.session() as session:
            result = session.run("""
                UNWIND $paper_ids AS pid
                OPTIONAL MATCH (p:Paper {paper_id: pid})
                OPTIONAL MATCH (citing:Paper)-[:CITES]->(p)
                OPTIONAL MATCH (p)-[:CITES]->(referenced:Paper)
                RETURN pid as paper_id,
                       p.doi as doi,
                       count(DISTINCT citing) as citation_count,
                       count(DISTINCT referenced) as reference_count
            """, paper_ids=paper_ids)

            return {
                r["paper_id"]: {
                    "doi": r["doi"],
                    "citation_count": r["citation_count"],
                    "reference_count": r["reference_count"]
                }
                for r in result
                if r["paper_id"]
            }
    except Exception as e:
        logger.exception("Paper ID citation count lookup failed")
        return {}


def compute_citation_score(citation_count: int, max_citations: int) -> float:
    """
    Compute normalized citation score (0-1).

    Uses log scaling to handle papers with very high citation counts.
    """
    import math
    if max_citations <= 0 or citation_count <= 0:
        return 0.0

    # Log-scaled normalization
    log_count = math.log1p(citation_count)
    log_max = math.log1p(max_citations)

    return min(1.0, log_count / log_max) if log_max > 0 else 0.0


def get_authors_other_papers(doi: str, limit: int = 20) -> list[dict]:
    """
    Get other papers by the same authors as a given paper.

    Returns list of paper dicts with citation info.
    """
    neo4j = get_neo4j()
    if not neo4j:
        return []

    try:
        with neo4j.session() as session:
            result = session.run("""
                // Find the source paper and its authors
                MATCH (source:Paper {doi: $doi})<-[:AUTHORED]-(author:Author)
                // Find other papers by these authors
                MATCH (author)-[:AUTHORED]->(other:Paper)
                WHERE other.doi <> $doi
                // Get citation counts
                OPTIONAL MATCH (citing:Paper)-[:CITES]->(other)
                WITH other, count(DISTINCT citing) as citation_count,
                     collect(DISTINCT author.name) as shared_authors
                RETURN other.paper_id as paper_id,
                       other.doi as doi,
                       other.title as title,
                       other.year as year,
                       other.journal as journal,
                       other.abstract as abstract,
                       citation_count,
                       shared_authors
                ORDER BY citation_count DESC, other.year DESC
                LIMIT $limit
            """, doi=doi, limit=limit)

            return [dict(r) for r in result]
    except Exception as e:
        logger.exception("Authors other papers lookup failed")
        return []


# ==============================================================================
# PDF File Helpers
# ==============================================================================
def doi_to_filename(doi: str) -> str:
    """
    Convert a DOI to the PDF filename format.

    Example: "10.1111/j.1745-7254.2008.00726.x" -> "doi_10.1111_j.1745-7254.2008.00726.x.pdf"
    """
    # Replace forward slashes with underscores
    safe_doi = doi.replace("/", "_")
    return f"doi_{safe_doi}.pdf"


def get_pdf_path(doi: str) -> Optional[str]:
    """
    Get the full path to a paper's PDF file.

    Returns None if the file doesn't exist.
    """
    filename = doi_to_filename(doi)
    filepath = os.path.join(PAPERS_PDF_DIR, filename)

    if os.path.exists(filepath):
        return filepath
    return None


# ==============================================================================
# Retrieval Functions
# ==============================================================================
async def search_papers(query: str, top_k: int = 5) -> list[RetrievedDocument]:
    """Search papers collection using SPECTER embeddings."""
    qdrant = get_qdrant()
    embedder = database.get_paper_encoder()

    if not qdrant or not embedder:
        return []

    try:
        # Check if collection exists
        collections = qdrant.get_collections().collections
        if not any(c.name == database.PAPERS_COLLECTION for c in collections):
            return []

        # Generate query embedding (BGE prepends PAPER_QUERY_PREFIX; "" for SPECTER)
        vector = embedder.encode(database.PAPER_QUERY_PREFIX + query).tolist()

        # Search (qdrant-client 1.7+ uses query_points instead of search)
        results = qdrant.query_points(
            collection_name=database.PAPERS_COLLECTION,
            query=vector,
            limit=top_k
        )

        documents = []
        for hit in results.points:
            content = f"**{hit.payload.get('title', 'Untitled')}**\n"
            if hit.payload.get('authors'):
                authors = hit.payload['authors']
                if isinstance(authors, list):
                    content += f"Authors: {', '.join(authors[:3])}"
                    if len(authors) > 3:
                        content += " et al."
                    content += "\n"
            if hit.payload.get('year'):
                content += f"Year: {hit.payload['year']}\n"
            if hit.payload.get('abstract'):
                content += f"\n{hit.payload['abstract'][:500]}..."

            documents.append(RetrievedDocument(
                source="papers",
                content=content,
                score=float(hit.score),
                metadata={
                    "title": hit.payload.get("title"),
                    "doi": hit.payload.get("doi"),
                    "year": hit.payload.get("year"),
                    "authors": hit.payload.get("authors", [])[:5]
                }
            ))

        return documents

    except Exception as e:
        logger.exception("Papers search failed")
        return []


async def search_notion(query: str, top_k: int = 5) -> list[RetrievedDocument]:
    """Search Notion collection using BGE embeddings."""
    qdrant = get_qdrant()
    embedder = get_bge()

    if not qdrant or not embedder:
        return []

    try:
        # Check if collection exists
        collections = qdrant.get_collections().collections
        if not any(c.name == "notion" for c in collections):
            return []

        # Generate query embedding
        vector = embedder.encode(query).tolist()

        # Search (qdrant-client 1.7+ uses query_points instead of search)
        results = qdrant.query_points(
            collection_name="notion",
            query=vector,
            limit=top_k
        )

        documents = []
        for hit in results.points:
            content = f"**{hit.payload.get('title', 'Untitled Page')}**\n"
            if hit.payload.get('content'):
                content += f"\n{hit.payload['content'][:500]}..."

            documents.append(RetrievedDocument(
                source="notion",
                content=content,
                score=float(hit.score),
                metadata={
                    "title": hit.payload.get("title"),
                    "page_id": hit.payload.get("page_id"),
                    "url": hit.payload.get("url"),
                    "last_edited": hit.payload.get("last_edited")
                }
            ))

        return documents

    except Exception as e:
        logger.exception("Notion search failed")
        return []


async def search_web(query: str, top_k: int = 5) -> list[RetrievedDocument]:
    """Search the web using SearXNG."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                f"{SEARXNG_URL}/search",
                params={
                    "q": query,
                    "format": "json",
                    "categories": "general,science",
                    "language": "en"
                }
            )
            response.raise_for_status()
            data = response.json()

        documents = []
        for result in data.get("results", [])[:top_k]:
            content = f"**{result.get('title', 'Untitled')}**\n"
            content += f"URL: {result.get('url', '')}\n"
            if result.get('content'):
                content += f"\n{result['content'][:500]}..."

            # SearXNG doesn't provide relevance scores, so we use position
            score = 1.0 - (len(documents) * 0.1)  # Decreasing score by position

            documents.append(RetrievedDocument(
                source="web",
                content=content,
                score=max(0.5, score),
                metadata={
                    "title": result.get("title"),
                    "url": result.get("url"),
                    "engine": result.get("engine")
                }
            ))

        return documents

    except Exception as e:
        logger.exception("Web search failed")
        return []


# ==============================================================================
# API Endpoints
# ==============================================================================
@app.get("/health")
async def health_check():
    """Health check endpoint."""
    qdrant_ok = get_qdrant() is not None
    neo4j_ok = get_neo4j() is not None

    # Overall status is healthy if at least one database is available
    status = "healthy" if (qdrant_ok or neo4j_ok) else "degraded"

    return {
        "status": status,
        "services": {
            "qdrant": "connected" if qdrant_ok else "disconnected",
            "neo4j": "connected" if neo4j_ok else "disconnected",
            "searxng": SEARXNG_URL,
            "vllm": VLLM_URL,
            "mcp": "available"
        }
    }


# ==============================================================================
# Frontend Status Endpoint (/api/status)
# ==============================================================================
def _next_vllm_start_iso() -> str:
    """
    Compute the next scheduled vLLM start time (next 6 AM local time).
    Returned as an ISO 8601 string with timezone offset.
    """
    from datetime import timedelta

    now = datetime.now().astimezone()
    target = now.replace(hour=6, minute=0, second=0, microsecond=0)
    if now >= target:
        target = target + timedelta(days=1)
    return target.isoformat()


async def _probe_http(client: httpx.AsyncClient, url: str) -> str:
    """Return 'ok' if URL returns 2xx, 'error' on non-2xx, 'unavailable' on error."""
    try:
        r = await client.get(url, timeout=3.0)
        return "ok" if 200 <= r.status_code < 300 else "error"
    except Exception:
        return "unavailable"


@app.get("/api/status")
async def api_status():
    """
    Aggregated system status for the frontend.

    Shape matches FRONTEND-REFERENCE.md / DESIGN.md:
      {
        "vllm": {"status", "model", "next_start"?},
        "services": {"retrieval", "embedding", "qdrant", "neo4j", "grobid", "searxng"},
        "timestamp": "..."
      }
    """
    vllm_status = "offline"
    vllm_model: Optional[str] = None
    next_start: Optional[str] = None

    qdrant_url = f"http://{QDRANT_HOST}:{QDRANT_PORT}/collections"
    neo4j_http_url = NEO4J_URI.replace("bolt://", "http://").replace(":7687", ":7474")
    grobid_url = "http://grobid:8070/api/isalive"
    searxng_url = SEARXNG_URL.rstrip("/") + "/healthz"

    async with httpx.AsyncClient() as client:
        # vLLM
        try:
            r = await client.get(f"{VLLM_URL}/v1/models", timeout=3.0)
            if r.status_code == 200:
                data = r.json()
                vllm_status = "running"
                models = data.get("data") or []
                if models:
                    vllm_model = models[0].get("id")
        except Exception:
            vllm_status = "offline"

        if vllm_status == "offline":
            next_start = _next_vllm_start_iso()

        # External services
        qdrant_state, neo4j_state, grobid_state, searxng_state = await asyncio.gather(
            _probe_http(client, qdrant_url),
            _probe_http(client, neo4j_http_url),
            _probe_http(client, grobid_url),
            _probe_http(client, searxng_url),
        )

    # Fallback for searxng if /healthz isn't implemented (older versions)
    if searxng_state == "error":
        async with httpx.AsyncClient() as client:
            searxng_state = await _probe_http(client, SEARXNG_URL)

    embedding_ok = is_specter_loaded() and is_bge_loaded()

    services = {
        "retrieval": "ok",
        "embedding": "ok" if embedding_ok else "unavailable",
        "qdrant": qdrant_state,
        "neo4j": neo4j_state,
        "grobid": grobid_state,
        "searxng": searxng_state,
    }

    vllm_block: dict = {"status": vllm_status, "model": vllm_model}
    if next_start is not None:
        vllm_block["next_start"] = next_start

    return {
        "maintenance": read_maintenance(),
        "vllm": vllm_block,
        "services": services,
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }


# ==============================================================================
# Auth Header Helpers (frontend API)
# ==============================================================================
def _require_user_email(request: Request) -> str:
    """
    Extract the authenticated user's email from the forward-auth header
    set by the VPS gateway (both for browser sessions and API-key
    `/v1/*` requests). Raises 401 if absent.
    """
    email = request.headers.get("X-Munin-Email")
    if not email:
        raise HTTPException(
            status_code=401,
            detail={"error": {"message": "Missing authentication header"}},
        )
    return email.strip().lower()


def _error_404(message: str) -> HTTPException:
    return HTTPException(
        status_code=404, detail={"error": {"message": message}}
    )


# ==============================================================================
# Frontend Chat Routes (/api/chats)
# ==============================================================================
@app.get("/api/chats")
async def api_list_chats(
    request: Request,
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
    persona: Optional[str] = None,
    search: Optional[str] = None,
    pinned_only: bool = Query(False),
    project_id: Optional[str] = Query(None),
):
    """
    List the authenticated user's conversations. Pass ``project_id`` as
    a concrete project id to filter to that project's conversations, or
    as ``__unfiled__`` to list only conversations that have no project.
    """
    user_email = _require_user_email(request)
    return await chat_store.get_conversations(
        user_email=user_email,
        limit=limit,
        offset=offset,
        persona=persona,
        search=search,
        pinned_only=pinned_only,
        project_id=project_id,
    )


@app.get("/api/chats/{conversation_id}")
async def api_get_chat(conversation_id: str, request: Request):
    """Load a full conversation with all messages. P2 #24 Phase 1:
    embeds the current plan (if any) under a `plan` key so the
    frontend can re-render the inline PlanCard on transcript reload
    without a second HTTP call."""
    user_email = _require_user_email(request)
    conversation = await chat_store.get_conversation(
        conversation_id, user_email
    )
    if conversation is None:
        raise _error_404("Conversation not found")
    import plan_store

    plan = await plan_store.get_plan(conversation_id)
    conversation["plan"] = plan  # None if no plan; same shape as /plan
    return conversation


@app.patch("/api/chats/{conversation_id}")
async def api_rename_chat(conversation_id: str, request: Request):
    """Rename a conversation."""
    user_email = _require_user_email(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(
            status_code=400,
            detail={"error": {"message": "Invalid JSON body"}},
        )
    title = body.get("title") if isinstance(body, dict) else None
    if not isinstance(title, str) or not title.strip():
        raise HTTPException(
            status_code=400,
            detail={"error": {"message": "title is required"}},
        )
    updated = await chat_store.update_conversation(
        conversation_id=conversation_id,
        user_email=user_email,
        title=title.strip(),
    )
    if updated is None:
        raise _error_404("Conversation not found")
    return updated


@app.delete("/api/chats/{conversation_id}")
async def api_delete_chat(conversation_id: str, request: Request):
    """Delete a conversation and all of its messages."""
    user_email = _require_user_email(request)
    deleted = await chat_store.delete_conversation(conversation_id, user_email)
    if not deleted:
        raise _error_404("Conversation not found")
    # Best-effort: release the sandbox kernel for this conversation, if any.
    # We don't await on a failure - the kernel may not exist (most chats
    # never call run_python), and the idle reaper is a backstop anyway.
    try:
        from mcp.tools.sandbox import sandbox_shutdown
        await sandbox_shutdown(conversation_id)
    except Exception as e:
        logger.warning("sandbox_shutdown for %s failed: %s", conversation_id, e)
    return {"deleted": True}


@app.post("/api/chats/{conversation_id}/pin")
async def api_pin_chat(conversation_id: str, request: Request):
    """Pin a conversation so it floats to the top of the listing."""
    user_email = _require_user_email(request)
    meta = await chat_store.set_conversation_pinned(
        conversation_id=conversation_id,
        user_email=user_email,
        pinned=True,
    )
    if meta is None:
        raise _error_404("Conversation not found")
    return {"pinned": True, "pinned_at": meta.get("pinned_at")}


@app.delete("/api/chats/{conversation_id}/pin")
async def api_unpin_chat(conversation_id: str, request: Request):
    """Unpin a conversation."""
    user_email = _require_user_email(request)
    meta = await chat_store.set_conversation_pinned(
        conversation_id=conversation_id,
        user_email=user_email,
        pinned=False,
    )
    if meta is None:
        raise _error_404("Conversation not found")
    return {"pinned": False}


# ==============================================================================
# Report chat for review — /api/chats/{cid}/report
# ==============================================================================

REPORTED_DIR = os.environ.get(
    "MUNIN_REPORTED_DIR", "/opt/munin/data/reported"
)


@app.post("/api/chats/{conversation_id}/report")
async def api_report_chat(conversation_id: str, request: Request):
    """
    Flag a conversation for developer review. Serialises the full
    conversation (metadata + all messages with content/thinking/
    tool_calls/rag_context + artifact metadata) to a timestamped JSON
    file under REPORTED_DIR. Reported conversations serve as inputs
    for the test harness — each one can be turned into a regression
    test that replays the user's turns.

    Idempotent: re-reporting the same conversation overwrites the
    previous report file.
    """
    user_email = _require_user_email(request)

    try:
        body = await request.json()
    except Exception:
        body = {}
    reason = (body.get("reason") or "").strip() if isinstance(body, dict) else ""
    if len(reason) > 2000:
        raise HTTPException(
            status_code=400,
            detail={"error": {"message": "reason must be at most 2000 characters"}},
        )

    conversation = await chat_store.get_conversation(conversation_id, user_email)
    if conversation is None:
        raise _error_404("Conversation not found")

    artifacts = []
    try:
        artifacts = await artifact_store.list_artifacts(
            user_email=user_email,
            conversation_id=conversation_id,
        )
    except Exception as e:
        logger.warning("report: artifact listing failed: %s", e)

    now = datetime.utcnow()
    short_id = conversation_id.split("-")[0] if "-" in conversation_id else conversation_id[:8]
    report_id = f"rpt_{now.strftime('%Y%m%dT%H%M%S')}_{short_id}"

    report = {
        "report_id": report_id,
        "conversation_id": conversation_id,
        "user_email": user_email,
        "persona": conversation.get("persona"),
        "title": conversation.get("title"),
        "reason": reason or None,
        "reported_at": now.isoformat() + "Z",
        "messages": [
            {
                "index": msg.get("index_in_conversation", i),
                "role": msg.get("role"),
                "content": msg.get("content"),
                "thinking": msg.get("thinking"),
                "tool_calls": msg.get("tool_calls"),
                "rag_context": msg.get("rag_context"),
                "attachments": msg.get("attachments"),
                "created_at": msg.get("created_at"),
            }
            for i, msg in enumerate(conversation.get("messages") or [])
        ],
        "artifacts": [
            {
                "id": a.get("id"),
                "title": a.get("title"),
                "content_type": a.get("content_type"),
                "source": a.get("source"),
                "latest_version": a.get("latest_version"),
                "filename": a.get("filename"),
                "external_url": a.get("external_url"),
            }
            for a in artifacts
        ],
    }

    os.makedirs(REPORTED_DIR, exist_ok=True)
    report_path = os.path.join(REPORTED_DIR, f"{report_id}.json")
    try:
        import json as _json
        tmp = report_path + ".tmp"
        with open(tmp, "w") as f:
            _json.dump(report, f, indent=2, default=str)
        os.replace(tmp, report_path)
        # Reports are operator-readable by design (admin reviews them
        # to triage model failures). Ensure host-side users can read
        # without sudo regardless of the container's umask.
        try:
            os.chmod(report_path, 0o644)
        except OSError:
            pass
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={"error": {"message": f"Failed to write report: {e}"}},
        )

    return {"reported": True, "report_id": report_id}


# ==============================================================================
# Artifacts (§22) - /api/chats/{cid}/artifacts
# ==============================================================================
@app.get("/api/chats/{conversation_id}/artifacts")
async def api_list_artifacts(conversation_id: str, request: Request):
    """List all artifacts in a conversation (metadata only, no content)."""
    user_email = _require_user_email(request)
    conv = await chat_store.get_conversation(conversation_id, user_email)
    if conv is None:
        raise _error_404("Conversation not found")
    artifacts = await artifact_store.list_artifacts(
        user_email=user_email,
        conversation_id=conversation_id,
    )
    return {"artifacts": artifacts, "total": len(artifacts)}


@app.get("/api/chats/{conversation_id}/artifacts/{artifact_id}")
async def api_get_artifact(
    conversation_id: str,
    artifact_id: str,
    request: Request,
    version: Optional[int] = Query(None, ge=1),
):
    """
    Load the latest (or a specific) version of an artifact. Returns
    title, content_type, language, version metadata, and the full
    content as a string.
    """
    user_email = _require_user_email(request)
    row = await artifact_store.get_artifact_version(
        user_email=user_email,
        conversation_id=conversation_id,
        artifact_id=artifact_id,
        version=version,
    )
    if row is None:
        raise _error_404("Artifact not found")
    return row


@app.patch("/api/chats/{conversation_id}/artifacts/{artifact_id}")
async def api_patch_artifact(
    conversation_id: str,
    artifact_id: str,
    request: Request,
):
    """
    User-driven edit from the side panel. Creates a new version with
    ``created_by="user"``, visible to the model on the next chat turn
    via the updated artifact summary block.
    """
    user_email = _require_user_email(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(
            status_code=400,
            detail={"error": {"message": "Invalid JSON body"}},
        )
    if not isinstance(body, dict):
        raise HTTPException(
            status_code=400,
            detail={"error": {"message": "Request body must be an object"}},
        )
    content = body.get("content")
    if not isinstance(content, str):
        raise HTTPException(
            status_code=400,
            detail={"error": {"message": "content (string) is required"}},
        )
    change_summary = body.get("change_summary")
    try:
        row = await artifact_store.update_artifact(
            user_email=user_email,
            conversation_id=conversation_id,
            artifact_id=artifact_id,
            content=content,
            change_summary=change_summary,
            created_by="user",
        )
    except artifact_store.ArtifactError as exc:
        raise HTTPException(
            status_code=400, detail={"error": {"message": str(exc)}}
        )
    if row is None:
        raise _error_404("Artifact not found")
    return row


# ==============================================================================
# Frontend Persona Routes (/api/personas)
# ==============================================================================
@app.get("/api/personas")
async def api_personas():
    """Return all available personas with their prompt suggestions."""
    return persona_module.public_personas()


@app.get("/api/personas/{persona_id}/icon")
async def api_persona_icon(persona_id: str):
    """Serve the persona's SVG icon from personas/logos/."""
    path = persona_module.get_icon_path(persona_id)
    if path is None:
        raise _error_404("Persona icon not found")
    return FileResponse(path, media_type="image/svg+xml")


# ==============================================================================
# Frontend Chat Completions (/api/chat/completions) — SSE streaming
# ==============================================================================


# ------------------------------------------------------------------------------
# Raw-mode helpers for OpenAI-compatible /v1/ passthrough.
# ------------------------------------------------------------------------------
_RAW_PERSONA_ALIASES = {"raw", "none"}

# Munin-specific body fields that must NOT be forwarded to vLLM — they
# would either confuse its validator or cause it to emit a 4xx. Every
# other field is passed through unchanged so future OpenAI params keep
# working without code changes here.
_MUNIN_ONLY_FIELDS = frozenset({
    "persona",
    "conversation_id",
    "project_id",
    "ephemeral",
    "rag",
    "tags",
})


_TRUTHY_HEADER_VALUES = frozenset({"true", "1", "yes"})


def _header_ephemeral_flag(value: str) -> bool:
    """Parse an X-Munin-Ephemeral header value. The VPS gateway stamps
    `true` on every /v1/* API-key request so external callers don't
    create persistent chat records. Case-insensitive; tolerates
    whitespace; accepts ``true`` / ``1`` / ``yes``. Anything else
    (including ``false`` / ``0`` / ``no`` / empty / absent) → False."""
    if not isinstance(value, str):
        return False
    return value.strip().lower() in _TRUTHY_HEADER_VALUES


def _is_raw_mode_request(body: dict) -> bool:
    persona = body.get("persona")
    if isinstance(persona, str) and persona.strip().lower() in _RAW_PERSONA_ALIASES:
        return True
    if persona is None or persona == "":
        return (
            not body.get("conversation_id")
            and not body.get("project_id")
        )
    return False


# The served model's context window (prompt + max_tokens combined). Mirror
# backend start-vllm-service.sh (--max-model-len). We fit max_tokens to the
# prompt BEFORE calling vLLM rather than reacting to its rejection: vLLM's
# error reports the prompt size as a max_tokens-derived LOWER BOUND ("at least
# N input tokens", where N == window+1-max_tokens), so refitting from that
# number never converges. We count the prompt ourselves instead.
_RAW_MAX_MODEL_LEN = int(os.getenv("VLLM_MAX_MODEL_LEN", "65536"))
# A request whose prompt leaves less than this for output isn't worth running;
# the prompt itself effectively fills the window.
_RAW_MIN_OUTPUT_TOKENS = 256
# Cushion between our prompt-token count and vLLM's. We tokenize with the same
# Qwen tokenizer vLLM uses, but its chat-template framing adds a few dozen
# tokens we don't model exactly. 512 absorbs that comfortably.
_RAW_CTX_MARGIN = 512
# Safety net only: if vLLM rejects for context length despite the preemptive
# clamp (local count under-estimated, e.g. tokenizer not mounted), halve
# max_tokens and retry up to this many times. Halving converges fast.
_RAW_MAX_REFIT_RETRIES = 3


def _raw_prompt_tokens(messages: list) -> int:
    """Prompt token count for a raw-mode messages list, via the real Qwen
    tokenizer when mounted (chat_context), plus a small allowance for the
    assistant generation prompt / trailing special tokens."""
    from chat_context import _message_tokens
    total = 0
    for m in messages:
        if not isinstance(m, dict):
            continue
        try:
            total += _message_tokens(m)
        except Exception:
            total += max(1, len(str(m.get("content", ""))) // 4)
    return total + 8


def _is_ctx_overflow(text: str) -> bool:
    """True if `text` is vLLM's context-length rejection."""
    return "maximum context length" in (text or "").lower()


def _halve_for_retry(forward: dict) -> bool:
    """Halve forward['max_tokens'] for a retry after vLLM rejected the request
    despite the preemptive clamp. Returns False when there's no output budget
    left to give. Deliberately ignores vLLM's reported token counts — they're
    a max_tokens-derived lower bound, so feeding them back would not converge."""
    current = forward.get("max_tokens")
    if not isinstance(current, int) or current <= _RAW_MIN_OUTPUT_TOKENS:
        return False
    forward["max_tokens"] = max(_RAW_MIN_OUTPUT_TOKENS, current // 2)
    return True


async def _raw_chat_proxy(
    request: Request, body: dict, user_email: str
) -> Response:
    """Forward the request straight to vLLM with no persona / tool /
    RAG / chat_store machinery. vLLM's response is already an
    OpenAI-compatible stream (or JSON for stream=False), so we just
    relay bytes. Preserves the client's streaming preference and any
    OpenAI parameters we don't recognise."""
    # Build the forwarded body. Default model to whatever vLLM is
    # serving if the client didn't specify one.
    forward: dict = {k: v for k, v in body.items() if k not in _MUNIN_ONLY_FIELDS}
    if not forward.get("model"):
        forward["model"] = os.getenv("VLLM_MODEL_NAME", "qwen3.6-35b-a3b")
    if not isinstance(forward.get("messages"), list) or not forward["messages"]:
        raise HTTPException(
            status_code=400,
            detail={"error": {"message": "messages must be a non-empty list"}},
        )

    # Fit max_tokens to the context window before calling vLLM. The window is
    # shared between prompt and output, so a client max_tokens (Positron
    # defaults to 16384) that plus the prompt would overflow makes a growing
    # session fail partway through. Count the prompt with the real tokenizer
    # and clamp an explicit, too-large max_tokens to the room left. This also
    # covers clients that set max_tokens above the window outright. An unset
    # max_tokens is left alone: vLLM caps it to the remaining window itself and
    # never overflows.
    prompt_tokens = _raw_prompt_tokens(forward["messages"])
    room = _RAW_MAX_MODEL_LEN - prompt_tokens - _RAW_CTX_MARGIN
    requested_max = forward.get("max_tokens")
    if isinstance(requested_max, int) and requested_max > room:
        if room < _RAW_MIN_OUTPUT_TOKENS:
            # Prompt alone (near-)fills the window — no usable output budget.
            # Fail fast with a clear message instead of a partial generation.
            return JSONResponse(
                status_code=400,
                content={"error": {
                    "message": (
                        f"Prompt is too long: ~{prompt_tokens} tokens, but the "
                        f"model's context window is {_RAW_MAX_MODEL_LEN} tokens "
                        f"(shared between prompt and response). Shorten the "
                        f"conversation or start a new chat."
                    ),
                    "type": "context_length_exceeded",
                    "param": "messages",
                    "code": "context_length_exceeded",
                }},
            )
        forward["max_tokens"] = room

    vllm_url = os.getenv("VLLM_URL", "http://127.0.0.1:8000").rstrip("/")
    endpoint = f"{vllm_url}/v1/chat/completions"

    wants_stream = bool(forward.get("stream", False))

    # Make vLLM emit a final `usage` chunk so the VPS gateway's per-user
    # token accounting records real numbers for API-key requests.
    # External clients (Cursor, aider, OpenAI SDK) don't set this
    # themselves — they don't care about usage — but the gateway does.
    # The extra chunk is standard OpenAI SSE; clients that aren't
    # usage-aware ignore it. Respect a client-set value if they sent
    # their own preference.
    if wants_stream:
        existing_opts = forward.get("stream_options")
        if not isinstance(existing_opts, dict):
            forward["stream_options"] = {"include_usage": True}
        elif "include_usage" not in existing_opts:
            forward["stream_options"] = {**existing_opts, "include_usage": True}

    client = httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=30.0))

    if not wants_stream:
        # Simple JSON round-trip; external client didn't ask to stream.
        try:
            r = await client.post(endpoint, json=forward)
            # Safety net: if vLLM still rejects for context length despite the
            # preemptive clamp, halve max_tokens and retry. Any other error
            # passes straight through with vLLM's message and status intact.
            retries = 0
            while (r.status_code == 400 and _is_ctx_overflow(r.text)
                   and retries < _RAW_MAX_REFIT_RETRIES
                   and _halve_for_retry(forward)):
                retries += 1
                r = await client.post(endpoint, json=forward)
        finally:
            await client.aclose()
        return Response(
            content=r.content,
            status_code=r.status_code,
            media_type=r.headers.get("content-type", "application/json"),
        )

    # Streaming passthrough. vLLM emits OpenAI SSE format
    # (`data: {...}\n\n`, terminated by `data: [DONE]`) — we relay bytes
    # without interpretation so any future OpenAI streaming field (tool
    # calls, logprobs, etc.) keeps working.
    #
    # We open the upstream stream and check its status BEFORE committing to a
    # 200 SSE response. A pre-stream error is returned as a real HTTP error
    # carrying vLLM's message, never a 200 stream wrapping an error event —
    # the latter is unparseable to OpenAI clients (Positron, Cursor), which
    # report it as an opaque "error making request". Context-length
    # rejections that slip past the preemptive clamp are retried by halving
    # max_tokens (safety net); any other error surfaces immediately.
    async def _open_upstream():
        return await client.send(
            client.build_request("POST", endpoint, json=forward), stream=True
        )

    upstream = await _open_upstream()
    retries = 0
    while upstream.status_code != 200:
        err_text = (await upstream.aread()).decode("utf-8", errors="replace")
        status = upstream.status_code
        await upstream.aclose()
        if (status == 400 and _is_ctx_overflow(err_text)
                and retries < _RAW_MAX_REFIT_RETRIES
                and _halve_for_retry(forward)):
            retries += 1
            upstream = await _open_upstream()
            continue
        await client.aclose()
        return Response(
            content=err_text.encode("utf-8"),
            status_code=status,
            media_type="application/json",
        )

    async def _relay():
        try:
            async for chunk in upstream.aiter_raw():
                if chunk:
                    yield chunk
        except Exception as e:
            yield (
                b"data: "
                + json.dumps({
                    "error": {
                        "message": f"proxy error: {type(e).__name__}: {e}",
                        "type": "proxy_error",
                    }
                }).encode("utf-8")
                + b"\n\n"
            )
        finally:
            await upstream.aclose()
            await client.aclose()

    from fastapi.responses import StreamingResponse
    return StreamingResponse(
        _relay(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Munin-Raw-Mode": "1",
        },
    )


@app.post("/api/chat/completions")
async def api_chat_completions(request: Request):
    """
    Streaming chat completion with persona injection, optional RAG, and
    mid-stream tool execution. Emits SSE events consumed by the Munin frontend.
    """
    user_email = _require_user_email(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(
            status_code=400,
            detail={"error": {"message": "Invalid JSON body"}},
        )
    if not isinstance(body, dict):
        raise HTTPException(
            status_code=400,
            detail={"error": {"message": "Request body must be an object"}},
        )

    # ------------------------------------------------------------------
    # Raw-mode passthrough for OpenAI-compatible /v1/ callers.
    #
    # External API users (Cursor, aider, custom scripts) hit
    # api.muninai.org/v1/chat/completions. The VPS gateway proxies that
    # path to this endpoint without setting a persona. If we let the
    # usual persona resolver fall through it would prepend Meitner's
    # system prompt to their messages — those callers provide their own
    # system prompt and don't want Munin's persona layered on top.
    #
    # Detection (no gateway change required):
    #   - persona is the literal string "raw" or "none", OR
    #   - persona absent AND conversation_id absent AND project_id
    #     absent (the shape of every external /v1/ call).
    #
    # Frontend flows never hit the second condition: new chats always
    # carry an explicit persona from the picker, existing chats always
    # carry conversation_id, ephemeral chats always carry a persona.
    if _is_raw_mode_request(body):
        return await _raw_chat_proxy(request, body, user_email)

    conversation_id = body.get("conversation_id")
    messages = body.get("messages") or []
    rag_config = body.get("rag") or {}
    # `X-Munin-Ephemeral: true` is stamped by the VPS gateway on every
    # /v1/* API-key request so external callers never create persistent
    # chat records even if they bypass raw-mode detection (e.g. a
    # future Munin-aware client that hits /v1/ with an explicit
    # persona). An explicit header wins over body.ephemeral=false —
    # we OR the two so either signal forces the ephemeral path.
    ephemeral = bool(body.get("ephemeral", False)) or _header_ephemeral_flag(
        request.headers.get("X-Munin-Ephemeral", "")
    )
    # §21: the body may carry a project_id so brand-new conversations
    # can be created pre-filed into a project (and get that project's
    # default_persona). Ignored on ephemeral chats (refused below) and
    # on existing conversations (the conversation already has a
    # project, if any).
    body_project_id = body.get("project_id")

    # Resolve the project for this conversation (if any). Ephemeral
    # chats cannot belong to a project by design - the two features
    # have incompatible persistence stories. For existing conversations
    # we look up the project through the conversation row; for new
    # conversations we look up via body_project_id if provided.
    project_for_request: Optional[dict] = None
    if not ephemeral:
        if conversation_id:
            try:
                project_for_request = (
                    await project_store.get_project_for_conversation(
                        conversation_id, user_email
                    )
                )
            except Exception:
                project_for_request = None
        elif body_project_id:
            try:
                project_for_request = await project_store.get_project(
                    body_project_id, user_email
                )
            except Exception:
                project_for_request = None
            if project_for_request is None:
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"message": "Project not found"}},
                )
    if ephemeral and body_project_id:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "message": (
                        "projects and ephemeral chats are mutually "
                        "exclusive; drop either ephemeral or project_id"
                    )
                }
            },
        )

    # Persona resolution:
    #   1. Body `persona` always wins.
    #   2. Existing conversations keep their stored persona (we do NOT
    #      switch personas mid-flight when a conversation is filed into
    #      a project later). The body_project_id path is skipped.
    #   3. Brand-new conversations resolve project > profile > global.
    explicit_persona = body.get("persona")
    if explicit_persona:
        persona_id = explicit_persona
    elif conversation_id:
        # Existing conversation - read the stored persona and use it.
        existing_conv = await chat_store.get_conversation(
            conversation_id, user_email
        )
        if existing_conv is None:
            raise _error_404("Conversation not found")
        # Legacy conversations with no stored persona auto-route (munin).
        persona_id = existing_conv.get("persona") or persona_module.AUTO_PERSONA_ID
    else:
        project_default = (
            (project_for_request or {}).get("default_persona")
        )
        try:
            profile = await user_profile_store.get_profile(user_email)
        except Exception:
            profile = None
        # Unspecified -> the user-facing Munin identity, i.e. auto-route. A
        # project/profile default_persona still pins if explicitly set.
        persona_id = (
            project_default
            or (profile or {}).get("default_persona")
            or persona_module.AUTO_PERSONA_ID
        )

    if not isinstance(messages, list) or not messages:
        raise HTTPException(
            status_code=400,
            detail={"error": {"message": "messages must be a non-empty list"}},
        )
    user_message = messages[-1]
    if not isinstance(user_message, dict) or user_message.get("role") != "user":
        raise HTTPException(
            status_code=400,
            detail={"error": {"message": "last message must be from role 'user'"}},
        )
    # Shallow validation of multimodal content: if the frontend sent an
    # OpenAI-style content list, check the per-turn image cap and the
    # shape of each block. The actual image resolution (data URL parsing,
    # document reference lookup, size caps, funnelling to the documents
    # store) happens inside chat_service which has access to the user
    # context it needs. See retrieval/vision.py.
    user_content = user_message.get("content")
    if isinstance(user_content, list):
        image_count = 0
        for block in user_content:
            if not isinstance(block, dict):
                raise HTTPException(
                    status_code=400,
                    detail={"error": {"message": "content blocks must be objects"}},
                )
            btype = block.get("type")
            if btype == "text":
                if not isinstance(block.get("text"), str):
                    raise HTTPException(
                        status_code=400,
                        detail={"error": {"message": "text block missing text field"}},
                    )
            elif btype == "image_url":
                image_count += 1
                iu = block.get("image_url") or {}
                if not isinstance(iu, dict) or not isinstance(iu.get("url"), str):
                    raise HTTPException(
                        status_code=400,
                        detail={"error": {"message": "image_url block missing url"}},
                    )
            elif btype == "document":
                # A reference to an already-uploaded text document
                # (.pdf/.txt/.md/.docx). chat_service resolves it to the
                # document's extracted text. Deliberately NOT counted
                # against the image cap. See chat_service.py
                # _resolve_user_content_images.
                if not isinstance(block.get("document_id"), str) or not block.get(
                    "document_id"
                ):
                    raise HTTPException(
                        status_code=400,
                        detail={
                            "error": {"message": "document block missing document_id"}
                        },
                    )
            else:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": {
                            "message": f"unknown content block type: {btype!r}"
                        }
                    },
                )
        if image_count > 3:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {
                        "message": "at most 3 image attachments per turn"
                    }
                },
            )
    # In ephemeral mode the frontend echoes the full thread; everything
    # before the trailing user turn is the prior history.
    prior_messages = messages[:-1] if ephemeral else None

    # §28: #tag chips from the composer, normalised by chat_service.
    query_tags = body.get("tags")

    # P1 #10: the listener and the work are decoupled via a Stream in
    # the registry. The chat_service generator runs as its own task,
    # appending events to the stream's log. A grace timer (default 60s)
    # absorbs brief disconnects — only after the listener has been
    # absent for that window does it fire ``cancel_event`` and let
    # P0 #2's cancellation cascade run. This lets a WiFi blip or
    # browser refresh resume the in-flight turn via
    # GET /api/chat/completions/resume.
    stream = stream_registry_module.Stream(
        user_email=user_email,
        conversation_id=conversation_id,
    )
    stream_registry_module.registry.register(stream)

    async def _run_chat_into_log() -> None:
        try:
            async for event_dict in chat_service.stream_chat_completion(
                user_email=user_email,
                persona_id=persona_id,
                conversation_id=conversation_id,
                user_message=user_message,
                rag_config=rag_config,
                ephemeral=ephemeral,
                prior_messages=prior_messages,
                project=project_for_request,
                file_into_project_id=body_project_id if not conversation_id else None,
                query_tags=query_tags,
                cancel_event=stream.cancel_event,
                stream_id=stream.stream_id,
            ):
                stream.record(event_dict["event"], event_dict["data"])
        except Exception:
            logger.exception(
                "stream %s runner crashed", stream.stream_id
            )
        finally:
            stream.mark_done()

    stream.runner_task = asyncio.create_task(_run_chat_into_log())
    stream.grace_task = asyncio.create_task(
        stream_registry_module._grace_timer(stream)
    )

    return EventSourceResponse(
        stream_registry_module.serve_stream(stream),
        ping=int(stream_registry_module.KEEPALIVE_S),
    )


@app.get("/api/chat/completions/resume")
async def api_chat_completions_resume(stream_id: str, request: Request):
    """Resume an in-flight (or just-completed) chat completion stream.

    The client carries ``Last-Event-ID: <stream_id>-<seq>`` to indicate
    the last event it applied; the server replays everything strictly
    newer and continues live. Used by the frontend for mid-stream
    reconnects (WiFi blip) and on-page-load resume after a browser
    refresh (sessionStorage carries stream_id + last_event_id across
    the reload). P1 #10."""
    user_email = _require_user_email(request)
    stream = stream_registry_module.registry.get(stream_id)
    if stream is None or stream.truncated:
        # Unknown id, expired (evicted by the janitor), or buffer
        # overflowed past the client's checkpoint. 410 is the right
        # signal — the resource is gone, don't retry blindly.
        raise HTTPException(
            status_code=410,
            detail={"error": {"message": "stream is gone or has been truncated"}},
        )
    if stream.user_email and stream.user_email != user_email:
        # Belt-and-braces: stream_id is a UUID so guessing is infeasible,
        # but never let user A resume user B's stream.
        raise HTTPException(
            status_code=403,
            detail={"error": {"message": "stream does not belong to this user"}},
        )

    parsed = stream_registry_module.parse_last_event_id(
        request.headers.get("Last-Event-ID")
    )
    after_seq = 0
    if parsed is not None:
        parsed_sid, parsed_seq = parsed
        # If the header's stream_id disagrees with the path, trust the
        # path (the client may have stale Last-Event-ID state) and
        # replay from 0.
        if parsed_sid == stream_id:
            after_seq = parsed_seq

    return EventSourceResponse(
        stream_registry_module.serve_stream(stream, after_seq=after_seq),
        ping=int(stream_registry_module.KEEPALIVE_S),
    )


# ==============================================================================
# Embedding Map (/api/embedding_map) — §15
# ==============================================================================
EMBEDDING_MAP_PATH = os.getenv(
    "EMBEDDING_MAP_PATH", "/knowledge/embedding_map.json"
)


@app.get("/api/embedding_map")
async def api_embedding_map():
    """Serve the flat JSON produced by scripts/knowledge/build_embedding_map.py.

    Returns 404 with the standard error envelope if the map hasn't been
    built yet (first-run state before the nightly timer fires).
    """
    if not os.path.isfile(EMBEDDING_MAP_PATH):
        raise HTTPException(
            status_code=404,
            detail={"error": {"message": "Embedding map not yet built"}},
        )
    return FileResponse(EMBEDDING_MAP_PATH, media_type="application/json")


_TAG_KIND_TO_FILTER_KEY = {
    "topic": "topic_slug",
    "group": "contributors[].group_slug",
    "contributor": "contributors[].username",
}

# Public host used to build `download_url` fields on browse results.
# Same convention as retrieval/mcp/tools/papers.py.
PUBLIC_MUNIN_URL = os.getenv("MUNIN_PUBLIC_URL", "https://search.muninai.org")


@app.get("/api/tags/{kind}/{slug}/papers")
async def api_tag_papers(
    kind: str,
    slug: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    sort: str = Query("year_desc", pattern="^(year_desc|year_asc|upload_desc)$"),
):
    """Paginated list of papers matching a tag.

    Unlike `paper_search`, this is a **browse** view — no ranking, no
    SPECTER query, ordered by metadata (year descending by default).
    Used by the knowledge browser page in the frontend.

    `kind` is one of `topic` / `group` / `contributor`; `slug` is the
    value (topic_slug, research_group slug, or username). Returns
    small paper stubs (title/doi/year/authors/contributors/topic/
    download_url). Clients paginate with `offset` + `limit`.
    """
    filter_key = _TAG_KIND_TO_FILTER_KEY.get(kind)
    if filter_key is None:
        raise HTTPException(
            status_code=400,
            detail={"error": {"message": f"unknown tag kind: {kind!r}"}},
        )
    slug = (slug or "").strip().lower()
    if not slug:
        raise HTTPException(
            status_code=400, detail={"error": {"message": "slug is required"}}
        )

    qdrant = get_qdrant()
    if qdrant is None:
        raise HTTPException(
            status_code=503, detail={"error": {"message": "Qdrant unavailable"}}
        )

    from qdrant_client.http import models as qm
    flt = qm.Filter(
        must=[qm.FieldCondition(key=filter_key, match=qm.MatchValue(value=slug))]
    )

    # Total count is cheap with payload indexes — the frontend uses it
    # to render "1 of 418".
    try:
        count_res = qdrant.count(
            collection_name=database.PAPERS_COLLECTION, count_filter=flt, exact=True
        )
        total = getattr(count_res, "count", 0)
    except Exception:
        total = 0

    # Qdrant scroll doesn't do offset directly — we paginate by walking
    # pages until we've skipped `offset`. Cheap at current scale; for
    # >100k corpora we'd want a DB-side ordering column.
    papers: list[dict] = []
    seen = 0
    scroll_offset: Optional[Any] = None
    page_size = min(256, offset + limit)
    try:
        while seen < offset + limit:
            points, scroll_offset = qdrant.scroll(
                collection_name=database.PAPERS_COLLECTION,
                scroll_filter=flt,
                limit=page_size,
                offset=scroll_offset,
                with_payload=True,
                with_vectors=False,
            )
            if not points:
                break
            for p in points:
                payload = p.payload or {}
                if seen >= offset:
                    papers.append(_paper_stub(payload))
                seen += 1
                if len(papers) >= limit:
                    break
            if scroll_offset is None:
                break
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={"error": {"message": f"Qdrant scroll failed: {e}"}},
        )

    # Sort within the page (Qdrant doesn't sort-by-payload natively).
    # For a true corpus-wide sort we'd need to materialise everything,
    # which isn't scalable — but for a single page of ≤200 this is fine.
    if sort == "year_desc":
        papers.sort(key=lambda p: (p.get("year") or 0), reverse=True)
    elif sort == "year_asc":
        papers.sort(key=lambda p: (p.get("year") or 9999))
    elif sort == "upload_desc":
        papers.sort(
            key=lambda p: ((p.get("contributors") or [{}])[-1].get("upload_time") or ""),
            reverse=True,
        )

    return {
        "kind": kind,
        "slug": slug,
        "total": total,
        "offset": offset,
        "limit": limit,
        "sort": sort,
        "papers": papers,
    }


def _paper_stub(payload: dict) -> dict:
    """Shape a Qdrant payload into the public paper-card shape used by
    browse + search results. Mirrors what paper_search returns, minus
    the score/matched_query fields."""
    authors = payload.get("authors") or []
    if isinstance(authors, list):
        authors = [a if isinstance(a, str) else (a or {}).get("name", "") for a in authors][:5]
    doi = payload.get("doi")
    stub = {
        "title": payload.get("title"),
        "doi": doi,
        "year": payload.get("year"),
        "authors": authors,
        "journal": payload.get("journal"),
    }
    contributors = payload.get("contributors")
    if contributors:
        stub["contributors"] = [
            {
                "display_name": (c or {}).get("display_name"),
                "group_slug": (c or {}).get("group_slug"),
                "group_display_name": (c or {}).get("group_display_name"),
                "upload_time": (c or {}).get("upload_time"),
            }
            for c in contributors
            if isinstance(c, dict)
        ]
    topic_label = payload.get("topic_label")
    if topic_label and payload.get("topic_slug") != "unclustered":
        stub["topic"] = {
            "label": topic_label,
            "slug": payload.get("topic_slug"),
        }
    if doi:
        # Convention: only return a download_url when the PDF is on disk.
        # Cheap to check — os.path.exists against PAPERS_PDF_DIR.
        pdf_path = os.path.join(
            PAPERS_PDF_DIR, f"doi_{doi.replace('/', '_')}.pdf"
        )
        if os.path.isfile(pdf_path):
            stub["download_url"] = f"{PUBLIC_MUNIN_URL}/paper/{quote(doi, safe='')}/pdf"
    return stub


def _dedup_contributor_people(known: dict[str, dict]) -> list[dict]:
    """Collapse the email-keyed contributors map into distinct people.

    `_load_contributors` keys by email, so a multi-email person appears
    once per address. Returns one record per person:
    ``{"emails": set[str], "entry": dict}``.
    """
    people: dict[int, dict] = {}
    for addr, entry in known.items():
        p = people.setdefault(id(entry), {"emails": set(), "entry": entry})
        if addr:
            p["emails"].add(addr)
    return list(people.values())


def _build_group_contributor_lists(
    people: list[dict],
    group_counts: dict[str, int],
    contributor_counts: dict[str, int],
) -> tuple[list[dict], list[dict]]:
    """Assemble the /api/tags ``groups`` and ``contributors`` lists.

    Groups dedup by slug; the contributor mention list dedups by username.
    The two are INDEPENDENT — contributor collection used to be nested
    under the group dedup, which silently dropped any contributor sharing
    a research group with another. Both lists are sorted by paper_count
    descending.
    """
    groups: list[dict] = []
    contributors: list[dict] = []
    seen_groups: set[str] = set()
    seen_contributors: set[str] = set()
    for p in people:
        entry = p["entry"]
        slug = (entry.get("research_group") or "").strip()
        if slug and slug not in seen_groups:
            seen_groups.add(slug)
            groups.append({
                "slug": slug,
                "display_name": entry.get("research_group_display_name") or slug,
                "paper_count": group_counts.get(slug, 0),
            })
        username = entry.get("username")
        if username and username not in seen_contributors:
            seen_contributors.add(username)
            contributors.append({
                "username": username,
                "display_name": entry.get("display_name") or username,
                "group_slug": slug,
                "paper_count": contributor_counts.get(username, 0),
            })
    groups.sort(key=lambda g: -g["paper_count"])
    contributors.sort(key=lambda c: -c["paper_count"])
    return groups, contributors


@app.get("/api/tags")
async def api_tags():
    """Tag autocomplete catalog for the chat composer (§28 Sprint B).

    Returns three families:

    - `topics`: distinct `{slug, label, paper_count}` from the §15
      embedding map. Noise/unclustered bucket is omitted.
    - `groups`: one entry per research group in `contributors.yml`,
      `paper_count` counted from Qdrant via distinct paper IDs whose
      payload `contributors[].group_slug` matches.
    - `contributors`: individual uploaders that have a `username`, for
      the `#@username` mention shortcut. This is intentionally a subset
      (only mentionable people), so it is NOT a contributor headcount.
    - `contributor_count`: distinct contributors (by email) with at
      least one paper in the KB. This is the "Contributors" stat the
      knowledge overview shows.

    No auth — tag names are public (the model already sees them in
    search results).
    """
    topics: list[dict] = []
    if os.path.isfile(EMBEDDING_MAP_PATH):
        try:
            with open(EMBEDDING_MAP_PATH, encoding="utf-8") as f:
                emb_map = json.load(f)
            for cluster in emb_map.get("clusters", []) or []:
                slug = cluster.get("slug")
                if not slug or slug == "unclustered":
                    continue
                topics.append({
                    "slug": slug,
                    "label": cluster.get("label"),
                    "paper_count": cluster.get("size", 0),
                })
            topics.sort(key=lambda t: -t["paper_count"])
        except (OSError, json.JSONDecodeError):
            pass

    known = _load_contributors()
    people = _dedup_contributor_people(known)

    # Count papers per group/username via Qdrant. Uses payload filters; a
    # missing payload index just makes the scan slower, not wrong (we
    # lazy-create the indexes at startup; see `_ensure_contributor_indexes`).
    qdrant = get_qdrant()
    group_counts: dict[str, int] = {}
    contributor_counts: dict[str, int] = {}
    # Distinct contributors with >= 1 paper in the KB, matched by their
    # indexed `contributors[].email`. This is the "Contributors" stat the
    # knowledge overview shows (people who actually contributed), as opposed
    # to the username-keyed mention list below.
    contributor_count = 0
    if qdrant is not None:
        try:
            from qdrant_client.http import models as qm

            for slug in {
                (p["entry"].get("research_group") or "").strip()
                for p in people
            }:
                if not slug:
                    continue
                try:
                    res = qdrant.count(
                        collection_name=database.PAPERS_COLLECTION,
                        count_filter=qm.Filter(must=[
                            qm.FieldCondition(
                                key="contributors[].group_slug",
                                match=qm.MatchValue(value=slug),
                            )
                        ]),
                        exact=True,
                    )
                    group_counts[slug] = getattr(res, "count", 0)
                except Exception:
                    group_counts[slug] = 0

            for p in people:
                entry = p["entry"]
                username = entry.get("username")
                if username:
                    try:
                        res = qdrant.count(
                            collection_name=database.PAPERS_COLLECTION,
                            count_filter=qm.Filter(must=[
                                qm.FieldCondition(
                                    key="contributors[].username",
                                    match=qm.MatchValue(value=username),
                                )
                            ]),
                            exact=True,
                        )
                        contributor_counts[username] = getattr(res, "count", 0)
                    except Exception:
                        contributor_counts[username] = 0

                emails = [e for e in p["emails"] if e]
                if emails:
                    try:
                        res = qdrant.count(
                            collection_name=database.PAPERS_COLLECTION,
                            count_filter=qm.Filter(should=[
                                qm.FieldCondition(
                                    key="contributors[].email",
                                    match=qm.MatchValue(value=e),
                                )
                                for e in emails
                            ]),
                            exact=True,
                        )
                        if getattr(res, "count", 0) > 0:
                            contributor_count += 1
                    except Exception:
                        pass
        except Exception as e:
            logger.warning("tag catalog Qdrant counts failed: %s", e)

    groups, contributors = _build_group_contributor_lists(
        people, group_counts, contributor_counts
    )

    return {
        "topics": topics,
        "groups": groups,
        "contributors": contributors,
        "contributor_count": contributor_count,
    }


# ==============================================================================
# Admin Ingest (/api/admin/ingest) — §28
#
# Accepts a single PDF from the VPS-side hook_service (upload.muninai.org →
# tusd → hook_service → POST /api/admin/ingest over the autossh tunnel),
# looks up the uploader against contributors.yml, drops the file into
# pdf/inbox/ with a sidecar, and invokes paper_pipeline.py --single
# synchronously. Returns 200 only after the paper is fully indexed.
# ==============================================================================
ADMIN_INGEST_TOKEN = os.getenv("ADMIN_INGEST_TOKEN", "")
CONTRIBUTORS_CONFIG_PATH = os.getenv(
    "CONTRIBUTORS_CONFIG", "/app/config/contributors.yml"
)
PAPER_PIPELINE_SCRIPT = os.getenv(
    "PAPER_PIPELINE_SCRIPT", "/app/pipeline/paper_pipeline.py"
)
PAPERS_INBOX_DIR = os.path.join(PAPERS_PDF_DIR, "inbox")
# Quarantine sidesteps — PDFs that finished the pipeline but weren't
# usable (quality filter, duplicate DOI, non-research content) land in
# SKIPPED_DIR; PDFs where the pipeline crashed/timed out land in
# FAILED_DIR. Both directories keep the file + sidecar + a small
# skip_info.json for auditing. Keeps /inbox/ clean and makes it
# trivial to `ls pdf/skipped/` to see what didn't make it.
PAPERS_SKIPPED_DIR = os.path.join(PAPERS_PDF_DIR, "skipped")
PAPERS_FAILED_DIR = os.path.join(PAPERS_PDF_DIR, "failed")
# paper_pipeline.py --watch looks at THIS directory for marker files
# matching a PDF's basename; if one exists, it skips that PDF. We
# write one here on successful /api/admin/ingest so the new
# pipeline-daemon doesn't re-ingest papers the admin-ingest path
# just handled.
PAPERS_PROCESSED_MARKER_DIR = os.getenv(
    "PAPERS_PROCESSED_DIR", "/papers-processed"
)
PIPELINE_TIMEOUT_SECS = int(os.getenv("PIPELINE_TIMEOUT_SECS", "600"))

# Max concurrent /api/admin/ingest pipelines. GROBID has 10 engine
# slots (grobid.yaml: concurrency: 10) so we cap at 4 by default,
# leaving 6 engines headroom for the watcher (--workers 2) plus
# ad-hoc test calls. Without this cap a VPS-side hook + cron
# combination forked 37+ paper_pipeline subprocesses simultaneously,
# saturating GROBID's pool and producing sustained 503s
# (2026-04-23 incident).
INGEST_CONCURRENCY = int(os.getenv("INGEST_CONCURRENCY", "4"))
# How long to wait for a free slot before rejecting with 503.
# Fast-fail keeps the VPS-side cron retry from accumulating
# in-flight HTTP connections; the cron picks the file up next pass.
INGEST_ACQUIRE_TIMEOUT_SECS = float(os.getenv("INGEST_ACQUIRE_TIMEOUT_SECS", "5"))
_ingest_semaphore: Optional[asyncio.Semaphore] = None


def _get_ingest_semaphore() -> asyncio.Semaphore:
    """Lazy-init so the semaphore binds to the running event loop
    instead of the import-time absence of one."""
    global _ingest_semaphore
    if _ingest_semaphore is None:
        _ingest_semaphore = asyncio.Semaphore(INGEST_CONCURRENCY)
    return _ingest_semaphore


def _doi_from_upload_filename(name: Optional[str]) -> Optional[str]:
    """Extract a DOI from the uploader's original filename when it
    follows the `doi_<doi>.pdf` convention used by the crawler.

    Mirrors paper_pipeline._extract_doi_from_filename so uploaded
    PDFs whose user named them that way can feed the same
    filename-DOI-authoritative path that protects crawler downloads
    against GROBID picking up a citation DOI. Returns None when the
    filename doesn't match the convention.
    """
    if not name:
        return None
    base = os.path.basename(name)
    if not base.startswith("doi_"):
        return None
    doi_part = base[4:]
    if doi_part.endswith(".pdf"):
        doi_part = doi_part[:-4]
    if not doi_part.startswith("10."):
        return None
    idx = 3
    while idx < len(doi_part) and doi_part[idx].isdigit():
        idx += 1
    if idx < len(doi_part) and doi_part[idx] == "_":
        return doi_part[:idx] + "/" + doi_part[idx + 1:]
    return None


def _quarantine_inbox_paper(
    inbox_pdf: str,
    sidecar_path: str,
    dest_dir: str,
    skip_info: dict,
) -> None:
    """Move a stuck inbox paper + its sidecar to a quarantine dir and
    write a companion skip_info.json. Best-effort: logs and swallows
    failures so the caller's error path isn't clobbered by a
    secondary move-error. Idempotent across retries — same UUID
    overwrites."""
    try:
        os.makedirs(dest_dir, exist_ok=True)
        uuid_stem = os.path.splitext(os.path.basename(inbox_pdf))[0]
        dest_pdf = os.path.join(dest_dir, f"{uuid_stem}.pdf")
        dest_sidecar = os.path.join(dest_dir, f"{uuid_stem}.contributor.json")
        dest_info = os.path.join(dest_dir, f"{uuid_stem}.skip_info.json")
        if os.path.isfile(inbox_pdf):
            shutil.move(inbox_pdf, dest_pdf)
        if os.path.isfile(sidecar_path):
            shutil.move(sidecar_path, dest_sidecar)
        with open(dest_info, "w", encoding="utf-8") as f:
            json.dump(skip_info, f, indent=2)
    except OSError as e:
        logger.warning("quarantine move failed (%s): %s", dest_dir, e)

_contributors_cache: Optional[dict[str, dict]] = None
_contributors_cache_mtime: float = 0.0


def _load_contributors() -> dict[str, dict]:
    """Parse contributors.yml, keyed by lowercased email. Auto-reloads
    when the YAML file's mtime changes so editing the allowlist doesn't
    require a container restart.

    Each entry may declare its email(s) in EITHER form:

        - email: alice@x        # single-email entry (one person)

        - emails:               # multi-email entry (several aliases
            - bob@x             # / a shared lab account; all map to
            - bob@y.de          # the same contributor identity)

    Multiple PEOPLE in one research group should be separate entries
    that share the same `research_group` slug (preserves per-person
    attribution while keeping `#group` filtering correct).
    """
    global _contributors_cache, _contributors_cache_mtime
    try:
        mtime = os.path.getmtime(CONTRIBUTORS_CONFIG_PATH)
    except OSError:
        return {}
    if _contributors_cache is not None and mtime == _contributors_cache_mtime:
        return _contributors_cache
    try:
        with open(CONTRIBUTORS_CONFIG_PATH, encoding="utf-8") as f:
            doc = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as e:
        logger.warning("contributors.yml unreadable: %s", e)
        return {}
    out: dict[str, dict] = {}
    for entry in doc.get("contributors", []) or []:
        # Collect every email this entry claims (both forms allowed).
        addrs: list[str] = []
        single = entry.get("email")
        if isinstance(single, str) and single.strip():
            addrs.append(single.strip().lower())
        listed = entry.get("emails")
        if isinstance(listed, list):
            for a in listed:
                if isinstance(a, str) and a.strip():
                    addrs.append(a.strip().lower())
        for addr in addrs:
            if addr in out:
                logger.warning(
                    "contributors.yml: duplicate email %r, later entry wins (%r)",
                    addr,
                    entry.get("display_name"),
                )
            out[addr] = entry
    _contributors_cache = out
    _contributors_cache_mtime = mtime
    return out


def _require_admin_token(request: Request) -> None:
    if not ADMIN_INGEST_TOKEN:
        raise HTTPException(
            status_code=503,
            detail={"error": {"message": "Admin ingest not configured (ADMIN_INGEST_TOKEN unset)"}},
        )
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail={"error": {"message": "Missing bearer token"}},
        )
    if not secrets.compare_digest(header[7:], ADMIN_INGEST_TOKEN):
        raise HTTPException(
            status_code=401,
            detail={"error": {"message": "Invalid admin token"}},
        )


@app.post("/api/admin/ingest")
async def api_admin_ingest(
    request: Request,
    file: UploadFile = File(...),
    email: str = Form(...),
    filename: Optional[str] = Form(None),
    upload_time: Optional[str] = Form(None),
):
    """Shared-corpus ingest for files arriving from upload.muninai.org.

    Synchronous: returns 200 only after the pipeline has fully processed
    the PDF (GROBID → CrossRef → SPECTER → Qdrant `papers` + Neo4j).

    Concurrency-capped: at most ``INGEST_CONCURRENCY`` (default 4)
    pipeline subprocesses run in parallel. Over-cap requests fail
    fast with 503 + Retry-After so the VPS-side cron can retry next
    pass instead of stacking up subprocess fork bombs that saturate
    GROBID's engine pool (2026-04-23 incident).
    """
    _require_admin_token(request)

    email = (email or "").strip().lower()
    if not email:
        raise HTTPException(
            status_code=400, detail={"error": {"message": "email is required"}}
        )

    sem = _get_ingest_semaphore()
    try:
        await asyncio.wait_for(
            sem.acquire(), timeout=INGEST_ACQUIRE_TIMEOUT_SECS
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "message": (
                        f"Ingest pipeline saturated "
                        f"(>= {INGEST_CONCURRENCY} concurrent jobs). "
                        f"Retry after the cooldown."
                    )
                }
            },
            headers={"Retry-After": "60"},
        )

    try:
        return await _api_admin_ingest_inner(
            request=request,
            file=file,
            email=email,
            filename=filename,
            upload_time=upload_time,
        )
    finally:
        sem.release()


async def _api_admin_ingest_inner(
    request: Request,
    file: UploadFile,
    email: str,
    filename: Optional[str],
    upload_time: Optional[str],
):
    contents = await file.read()
    if not contents or not contents.startswith(b"%PDF"):
        raise HTTPException(
            status_code=400,
            detail={"error": {"message": "File is empty or not a PDF"}},
        )

    os.makedirs(PAPERS_INBOX_DIR, exist_ok=True)
    inbox_id = uuid.uuid4().hex[:16]
    inbox_pdf = os.path.join(PAPERS_INBOX_DIR, f"{inbox_id}.pdf")
    sidecar_path = os.path.join(PAPERS_INBOX_DIR, f"{inbox_id}.contributor.json")
    with open(inbox_pdf, "wb") as f:
        f.write(contents)

    # Resolve uploader against the allowlist. Unknown emails still
    # ingest (per USER-DOCUMENTS-ANSWERS §4) — they land with
    # research_group="unknown" and no display name.
    known = _load_contributors().get(email)
    sidecar_payload = {
        "contributor_email": email,
        "contributor_username": (known or {}).get("username"),
        "contributor_display_name": (known or {}).get("display_name"),
        "research_group": (known or {}).get("research_group") or "unknown",
        "research_group_display_name": (known or {}).get("research_group_display_name"),
        "uploaded_at": upload_time or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "original_filename": filename,
    }
    # Stage 1.5: if the user uploaded a `doi_*.pdf`-named file, hand
    # the parsed DOI to the pipeline so it can be used as the
    # authoritative source instead of relying on GROBID extraction
    # (which is what produces the LIGPLOT/JSTOR-style splices).
    filename_doi_hint = _doi_from_upload_filename(filename)
    if filename_doi_hint:
        sidecar_payload["filename_doi_hint"] = filename_doi_hint
    with open(sidecar_path, "w", encoding="utf-8") as f:
        json.dump(sidecar_payload, f)

    # Run the pipeline synchronously. Pipeline auto-detects the sidecar.
    # Phase B (2026-05-13): pass --ingest-path upload so paper_pipeline.py
    # stamps the state sidecar with the correct provenance. The pipeline
    # subprocess itself handles the PDF move + Qdrant pdf_path update +
    # processed-marker write via _process_and_dispose; this endpoint
    # parses the resulting "[DISPOSE] {...}" line from stdout to build
    # the HTTP response.
    pipeline_env = {**os.environ}
    try:
        proc = await asyncio.create_subprocess_exec(
            "python3",
            PAPER_PIPELINE_SCRIPT,
            "--single",
            inbox_pdf,
            "--ingest-path", "upload",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=pipeline_env,
        )
        try:
            stdout_bytes, _ = await asyncio.wait_for(
                proc.communicate(), timeout=PIPELINE_TIMEOUT_SECS
            )
        except asyncio.TimeoutError:
            proc.kill()
            _quarantine_inbox_paper(
                inbox_pdf,
                sidecar_path,
                PAPERS_FAILED_DIR,
                {
                    "outcome": "timeout",
                    "reason": f"Pipeline timed out after {PIPELINE_TIMEOUT_SECS}s",
                    "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "email": email,
                    "group_slug": sidecar_payload["research_group"],
                    "original_filename": filename,
                },
            )
            raise HTTPException(
                status_code=504,
                detail={"error": {"message": f"Pipeline timed out after {PIPELINE_TIMEOUT_SECS}s"}},
            )
    except FileNotFoundError as e:
        raise HTTPException(
            status_code=500,
            detail={"error": {"message": f"Pipeline script not found: {e}"}},
        )

    full_stdout = stdout_bytes.decode("utf-8", errors="replace")
    tail = full_stdout[-2000:]
    if proc.returncode != 0:
        _quarantine_inbox_paper(
            inbox_pdf,
            sidecar_path,
            PAPERS_FAILED_DIR,
            {
                "outcome": "pipeline_error",
                "reason": f"Pipeline exited {proc.returncode}",
                "returncode": proc.returncode,
                "log_tail": tail,
                "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "email": email,
                "group_slug": sidecar_payload["research_group"],
                "original_filename": filename,
            },
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"message": f"Pipeline exited {proc.returncode}", "log_tail": tail}},
        )

    # Phase B (2026-05-13): the subprocess prints "[DISPOSE] {...}"
    # as its last meaningful line with the result of
    # _process_and_dispose. That's the authoritative outcome — PDF
    # is already at its final location, the state sidecar is
    # written, Qdrant has the pdf_path + state mirror. We just parse
    # the line and build the response.
    dispose: Optional[Dict] = None
    for line in reversed(full_stdout.splitlines()):
        line = line.strip()
        if line.startswith("[DISPOSE]"):
            try:
                dispose = json.loads(line.split(" ", 1)[1])
            except (IndexError, json.JSONDecodeError):
                dispose = None
            break

    if dispose is None:
        # Subprocess exited 0 but didn't emit a [DISPOSE] line. Could
        # be an old paper_pipeline.py that predates Phase B, or the
        # disposal helper threw before printing. Fall back to the
        # legacy quarantine-to-failed for safety; the inbox PDF still
        # exists in this branch (dispose would have moved it).
        if os.path.isfile(inbox_pdf):
            _quarantine_inbox_paper(
                inbox_pdf,
                sidecar_path,
                PAPERS_FAILED_DIR,
                {
                    "outcome": "pipeline_no_dispose_line",
                    "reason": "Pipeline did not emit a [DISPOSE] result line",
                    "log_tail": tail,
                    "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "email": email,
                    "group_slug": sidecar_payload["research_group"],
                    "original_filename": filename,
                },
            )
        raise HTTPException(
            status_code=500,
            detail={"error": {"message": "Pipeline produced no [DISPOSE] result", "log_tail": tail}},
        )

    # Inbox sidecar is moved alongside the PDF by dispose. If it's
    # still here (e.g. dispose failed to move it), clean up.
    try:
        if os.path.isfile(sidecar_path):
            os.remove(sidecar_path)
    except OSError:
        pass

    state = dispose.get("state", "quarantine")
    if state != "live":
        return {
            "status": "skipped",
            "reason": "; ".join(dispose.get("quarantine_reasons") or ["unknown"]),
            "log_tail": tail,
            "quarantined_to": os.path.dirname(dispose.get("final_pdf_path", "")) or "quarantine",
        }

    return {
        "status": "ingested",
        "doi": dispose.get("doi"),
        "final_pdf_path": dispose.get("final_pdf_path"),
        "contributor": {
            "email": email,
            "group_slug": sidecar_payload["research_group"],
            "known": known is not None,
        },
    }


# ==============================================================================
# Frontend Profile Routes (/api/profile)
# ==============================================================================
@app.get("/api/profile")
async def api_get_profile(request: Request):
    """Load the authenticated user's profile (all-None if never set)."""
    user_email = _require_user_email(request)
    return await user_profile_store.get_profile(user_email)


@app.put("/api/profile")
async def api_put_profile(request: Request):
    """Upsert profile fields. Body keys are merged onto the existing row."""
    user_email = _require_user_email(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(
            status_code=400,
            detail={"error": {"message": "Invalid JSON body"}},
        )
    try:
        cleaned = user_profile_store.validate_profile_input(body)
    except ValueError as e:
        raise HTTPException(
            status_code=400, detail={"error": {"message": str(e)}}
        )
    return await user_profile_store.upsert_profile(user_email, cleaned)


@app.delete("/api/profile")
async def api_delete_profile(request: Request):
    """Reset the profile to defaults (deletes the row)."""
    user_email = _require_user_email(request)
    removed = await user_profile_store.delete_profile(user_email)
    return {"removed": removed}


# ==============================================================================
# Frontend Project Routes (/api/projects) — §21
# ==============================================================================
@app.post("/api/projects")
async def api_create_project(request: Request):
    user_email = _require_user_email(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(
            status_code=400,
            detail={"error": {"message": "Invalid JSON body"}},
        )
    try:
        cleaned = project_store.validate_project_input(body)
        if not cleaned.get("name"):
            raise ValueError("name is required")
        return await project_store.create_project(user_email, cleaned)
    except ValueError as e:
        raise HTTPException(
            status_code=400, detail={"error": {"message": str(e)}}
        )


@app.get("/api/projects")
async def api_list_projects(
    request: Request,
    archived: bool = Query(False),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    user_email = _require_user_email(request)
    return await project_store.list_projects(
        user_email=user_email,
        include_archived=archived,
        limit=limit,
        offset=offset,
    )


@app.get("/api/projects/{project_id}")
async def api_get_project(project_id: str, request: Request):
    user_email = _require_user_email(request)
    proj = await project_store.get_project(
        project_id, user_email, include_counts=True
    )
    if proj is None:
        raise _error_404("Project not found")
    # Document count comes from Qdrant rather than project_store so
    # the store module stays Qdrant-free.
    try:
        proj["document_count"] = document_store.count_project_documents(
            user_email, project_id
        )
    except Exception:
        proj["document_count"] = 0
    return proj


@app.patch("/api/projects/{project_id}")
async def api_update_project(project_id: str, request: Request):
    user_email = _require_user_email(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(
            status_code=400,
            detail={"error": {"message": "Invalid JSON body"}},
        )
    try:
        cleaned = project_store.validate_project_input(body)
    except ValueError as e:
        raise HTTPException(
            status_code=400, detail={"error": {"message": str(e)}}
        )
    updated = await project_store.update_project(
        project_id, user_email, cleaned
    )
    if updated is None:
        raise _error_404("Project not found")
    return updated


@app.delete("/api/projects/{project_id}")
async def api_delete_project(project_id: str, request: Request):
    """
    Hard-delete the project row, unfile its conversations (project_id
    -> NULL), and unfile its documents in Qdrant (project_id payload
    cleared). No tombstones - the user's data stays in the Unfiled
    bucket and is not reaped. See §21 Status block in future_features.
    """
    user_email = _require_user_email(request)
    ok = await project_store.delete_project(project_id, user_email)
    if not ok:
        raise _error_404("Project not found")
    # Sweep user_docs for any points that were filed into this project
    # and clear their project_id payload so search treats them as
    # user-global again. project_store already unfiled the
    # conversations SQL rows before we got here.
    try:
        document_store.unfile_project_documents(user_email, project_id)
    except Exception as e:
        logger.warning("unfile_project_documents %s: %s", project_id, e)
    return {"deleted": True}


@app.post("/api/projects/{project_id}/conversations/{conversation_id}")
async def api_file_conversation(
    project_id: str,
    conversation_id: str,
    request: Request,
):
    """File a conversation into a project."""
    user_email = _require_user_email(request)
    result = await project_store.file_conversation(
        project_id=project_id,
        conversation_id=conversation_id,
        user_email=user_email,
    )
    if result is None:
        raise _error_404("Project or conversation not found")
    return result


@app.delete("/api/projects/{project_id}/conversations/{conversation_id}")
async def api_unfile_conversation(
    project_id: str,
    conversation_id: str,
    request: Request,
):
    """Remove a conversation from a project (moves to Unfiled)."""
    user_email = _require_user_email(request)
    # project_id is part of the URL for symmetry with the file route
    # but we don't actually need to verify it matches - the caller is
    # just saying "get this conversation out of whatever project it's in".
    _ = project_id
    ok = await project_store.unfile_conversation(conversation_id, user_email)
    if not ok:
        raise _error_404("Conversation not found")
    return {"unfiled": True}


# ==============================================================================
# Frontend Sandbox Artifacts (/api/artifacts/{cid}/{aid})
# ==============================================================================
@app.get("/api/artifacts/{conversation_id}/{artifact_id}")
async def api_get_artifact(
    conversation_id: str,
    artifact_id: str,
    request: Request,
):
    """
    Serve an artifact (plot, file) produced by the run_python sandbox tool
    inside the given conversation. Auth via X-Munin-Email + ownership of
    the conversation. The actual file lives in the sandbox sidecar; we
    proxy the bytes through so the host never needs to expose the sandbox
    container's port directly.
    """
    user_email = _require_user_email(request)

    # Ownership: the user must own the conversation row this artifact came
    # from. Without this check, anyone with the artifact id could pull
    # arbitrary files from another user's chat.
    conv = await chat_store.get_conversation(conversation_id, user_email)
    if conv is None:
        raise _error_404("Artifact not found")

    sandbox_url = os.environ.get("SANDBOX_URL", "http://sandbox:8090")
    proxy_url = f"{sandbox_url}/artifacts/{conversation_id}/{artifact_id}"

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(proxy_url)
    except httpx.RequestError as e:
        raise HTTPException(
            status_code=502,
            detail={"error": {"message": f"sandbox unreachable: {e}"}},
        )
    if r.status_code == 404:
        raise _error_404("Artifact not found")
    if r.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail={"error": {"message": f"sandbox returned {r.status_code}"}},
        )

    # Pass through the content-type and a sensible filename. The sandbox
    # sets these in its FileResponse; httpx surfaces them as headers.
    return Response(
        content=r.content,
        media_type=r.headers.get("content-type", "application/octet-stream"),
        headers={
            "Content-Disposition": r.headers.get(
                "content-disposition",
                f'inline; filename="{artifact_id}"',
            ),
            "Cache-Control": "private, max-age=300",
        },
    )


# ==============================================================================
# Frontend Document Routes (/api/documents)
# ==============================================================================
MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # Frontend enforces 50 MB; mirror on server.


@app.post("/api/documents/upload")
async def api_upload_document(
    request: Request,
    file: UploadFile = File(...),
    conversation_id: Optional[str] = Form(None),
    project_id: Optional[str] = Form(None),
):
    """Upload + embed a user document. Returns a document record.

    Optional ``project_id`` files the document into a project (§21) so
    subsequent project-scoped search finds it. The project must be owned
    by the requesting user or we return 404.
    """
    user_email = _require_user_email(request)
    if not file.filename:
        raise HTTPException(
            status_code=400,
            detail={"error": {"message": "filename is required"}},
        )

    if project_id:
        owner_project = await project_store.get_project(
            project_id, user_email
        )
        if owner_project is None:
            raise _error_404("Project not found")

    contents = await file.read()
    if len(contents) == 0:
        raise HTTPException(
            status_code=400,
            detail={"error": {"message": "Uploaded file is empty"}},
        )
    if len(contents) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail={"error": {"message": "File exceeds 50 MB limit"}},
        )

    try:
        return await document_store.upload_document(
            filename=file.filename,
            file_bytes=contents,
            user_email=user_email,
            conversation_id=conversation_id,
            project_id=project_id,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=400, detail={"error": {"message": str(e)}}
        )
    except Exception as e:
        logger.exception("Upload failed")
        raise HTTPException(
            status_code=500,
            detail={"error": {"message": "Upload processing failed"}},
        )


@app.get("/api/documents")
async def api_list_documents(
    request: Request, conversation_id: Optional[str] = None
):
    """List the authenticated user's uploaded documents."""
    user_email = _require_user_email(request)
    docs = await document_store.list_documents(
        user_email=user_email, conversation_id=conversation_id
    )
    return {"documents": docs}


@app.delete("/api/documents/{document_id}")
async def api_delete_document(document_id: str, request: Request):
    """Delete a document and all of its embeddings."""
    user_email = _require_user_email(request)
    deleted = await document_store.delete_document(document_id, user_email)
    if not deleted:
        raise _error_404("Document not found")
    return {"deleted": True}


# ==============================================================================
# Memory (P2 #25) — accepted + auto-proposed
# ==============================================================================
@app.get("/api/memories")
async def api_list_memories(request: Request):
    """Return both user-accepted memories and pending auto-proposed
    candidates for this user. The frontend uses pending entries for
    the post-turn accept/reject pills; accepted entries are surfaced
    in a future Memory settings tab (P2 #25 follow-up)."""
    user_email = _require_user_email(request)
    import memory_store
    import memory_proposals_store

    accepted = await memory_store.recall_all(user_email)
    pending = await memory_proposals_store.list_pending(user_email)
    return {"accepted": accepted, "pending": pending}


@app.post("/api/memories/proposed/{proposal_id}/accept")
async def api_accept_proposal(proposal_id: str, request: Request):
    """Upsert the proposed (key, value) into user_memory and delete the
    proposal. Returns the upserted entry (including any LRU eviction
    from user_memory's 20-entry cap)."""
    user_email = _require_user_email(request)
    import memory_store
    import memory_proposals_store

    proposal = await memory_proposals_store.get_proposal(proposal_id, user_email)
    if proposal is None:
        raise _error_404("Proposal not found")
    try:
        accepted = await memory_store.remember(
            user_email, proposal["key"], proposal["value"],
        )
    except memory_store.MemoryError as e:
        raise HTTPException(
            status_code=400,
            detail={"error": {"message": str(e)}},
        )
    await memory_proposals_store.delete_proposal(proposal_id, user_email)
    return {"accepted": accepted}


@app.post("/api/memories/proposed/{proposal_id}/reject")
async def api_reject_proposal(proposal_id: str, request: Request):
    """Delete the proposal and remember the key so the classifier
    doesn't re-propose it next turn. The key is recorded even if the
    proposal row no longer exists (concurrent reject + page refresh
    race) so a double-tap doesn't surface the same proposal."""
    user_email = _require_user_email(request)
    import memory_proposals_store

    proposal = await memory_proposals_store.get_proposal(proposal_id, user_email)
    if proposal is not None:
        await memory_proposals_store.delete_proposal(proposal_id, user_email)
        await memory_proposals_store.add_rejection(user_email, proposal["key"])
        return {"rejected": True, "key": proposal["key"]}
    # Idempotent on re-reject: the row's already gone but we still
    # record the key (if the caller supplied one in a query param,
    # they would — but to keep this stateless, just 404).
    raise _error_404("Proposal not found")


# ==============================================================================
# Plan mode (P2 #24 Phase 1) — structural plan per conversation
# ==============================================================================
@app.get("/api/chats/{conversation_id}/plan")
async def api_get_plan(conversation_id: str, request: Request):
    """Return the current plan for this conversation, or 404 if no
    plan has been set. Frontend uses this on transcript reload to
    re-attach the plan snapshot to the assistant message that last
    touched it. Ownership is enforced via the conversation row."""
    user_email = _require_user_email(request)
    import plan_store

    conversation = await chat_store.get_conversation(conversation_id, user_email)
    if conversation is None:
        raise _error_404("Conversation not found")
    plan = await plan_store.get_plan(conversation_id)
    if plan is None:
        raise _error_404("No plan for this conversation")
    return plan


@app.delete("/api/chats/{conversation_id}/plan")
async def api_delete_plan(conversation_id: str, request: Request):
    """Drop the conversation's plan. Used by the frontend's 'clear
    plan' affordance (Phase 1 has no UI for this yet) and as the
    cleanup path after a Phase 2 rejection. Returns 204 even if no
    plan existed (idempotent)."""
    user_email = _require_user_email(request)
    import plan_store

    conversation = await chat_store.get_conversation(conversation_id, user_email)
    if conversation is None:
        raise _error_404("Conversation not found")
    deleted = await plan_store.clear_plan(conversation_id=conversation_id)
    return {"deleted": deleted}


# ==============================================================================
# Plan-mode approval gate (P2 #24 Phase 2)
# ==============================================================================
@app.post("/api/chats/{conversation_id}/plan/approve")
async def api_approve_plan(conversation_id: str, request: Request):
    """Mark the plan approved with the supplied ``mode`` ('each' or
    'auto'). Body: ``{"mode": "each" | "auto"}`` (default 'each').

    Frontend's Approve button posts ``mode='each'``; Approve-all
    posts ``mode='auto'``; the "revoke auto" affordance posts
    ``mode='each'`` again to flip back to per-call gating without
    losing the existing approval state for the next call."""
    user_email = _require_user_email(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    mode = (body.get("mode") if isinstance(body, dict) else None) or "each"
    import plan_store

    conversation = await chat_store.get_conversation(conversation_id, user_email)
    if conversation is None:
        raise _error_404("Conversation not found")
    try:
        plan = await plan_store.mark_approved(
            conversation_id=conversation_id, approval_mode=mode,
        )
    except plan_store.PlanError as e:
        raise HTTPException(
            status_code=400,
            detail={"error": {"message": str(e)}},
        )
    return {"approved": True, "plan": plan}


@app.post("/api/chats/{conversation_id}/plan/reject")
async def api_reject_plan(conversation_id: str, request: Request):
    """Drop the plan and surface the rejection. Frontend's Reject
    button posts here; on success the user is expected to type a
    follow-up message describing the new direction (the model sees
    no plan block in its next system prompt + the user's prose, and
    recovers naturally — no synthetic system message needed)."""
    user_email = _require_user_email(request)
    import plan_store

    conversation = await chat_store.get_conversation(conversation_id, user_email)
    if conversation is None:
        raise _error_404("Conversation not found")
    deleted = await plan_store.clear_plan(conversation_id=conversation_id)
    return {"rejected": True, "deleted": deleted}


@app.patch("/api/chats/{conversation_id}/plan")
async def api_edit_plan(conversation_id: str, request: Request):
    """Replace the plan items with the user's edited list AND mark
    the plan approved with the supplied mode (implicit approve-on-
    save). Body: ``{"items": [...], "mode"?: "each" | "auto"}``.

    Distinct from `set_plan` (the MCP tool) in that this preserves
    `requires_approval=True` — the model still wanted approval; the
    user has edited + reviewed, so this revision is implicitly OK
    to proceed."""
    user_email = _require_user_email(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        raise HTTPException(
            status_code=400,
            detail={"error": {"message": "Body must be a JSON object"}},
        )
    items = body.get("items")
    if not isinstance(items, list):
        raise HTTPException(
            status_code=400,
            detail={"error": {"message": "items must be a list"}},
        )
    mode = body.get("mode") or "each"
    import plan_store

    conversation = await chat_store.get_conversation(conversation_id, user_email)
    if conversation is None:
        raise _error_404("Conversation not found")
    try:
        plan = await plan_store.replace_items(
            user_email=user_email,
            conversation_id=conversation_id,
            items=items,
            approval_mode=mode,
        )
    except plan_store.PlanError as e:
        raise HTTPException(
            status_code=400,
            detail={"error": {"message": str(e)}},
        )
    return {"plan": plan}


@app.get("/sources")
async def list_sources():
    """List available knowledge base sources."""
    qdrant = get_qdrant()

    # Count PDF files available for download
    pdf_count = 0
    try:
        if os.path.exists(PAPERS_PDF_DIR):
            pdf_count = len([f for f in os.listdir(PAPERS_PDF_DIR) if f.endswith('.pdf')])
    except Exception as e:
        logger.warning("Failed to count PDFs: %s", e)

    sources = {
        "papers": {
            "name": "Scientific Papers",
            "description": "Academic papers indexed with SPECTER embeddings",
            "available": False,
            "count": 0,
            "pdf_count": pdf_count
        },
        "notion": {
            "name": "Notion Workspace",
            "description": "Team Notion pages indexed with BGE embeddings",
            "available": False,
            "count": 0
        },
        "web": {
            "name": "Web Search",
            "description": "Live web search via SearXNG",
            "available": True,
            "count": None  # Real-time, no fixed count
        }
    }

    if qdrant:
        try:
            collections = qdrant.get_collections().collections
            for coll in collections:
                if coll.name == "papers":
                    info = qdrant.get_collection(coll.name)
                    sources["papers"]["available"] = True
                    sources["papers"]["count"] = info.points_count
                elif coll.name == "notion":
                    info = qdrant.get_collection(coll.name)
                    sources["notion"]["available"] = True
                    sources["notion"]["count"] = info.points_count
        except Exception as e:
            logger.exception("Failed to get collection info")

    return sources


@app.post("/retrieve", response_model=RetrieveResponse)
async def retrieve(request: RetrieveRequest):
    """
    Retrieve documents from selected knowledge bases.

    Sources:
    - papers: Scientific papers (Qdrant + SPECTER)
    - notion: Notion workspace (Qdrant + BGE)
    - web: Live web search (SearXNG)
    """
    if not request.query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty")

    if not request.sources:
        raise HTTPException(status_code=400, detail="At least one source must be specified")

    valid_sources = {"papers", "notion", "web"}
    invalid = set(request.sources) - valid_sources
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid sources: {invalid}. Valid: {valid_sources}"
        )

    # Query sources in parallel
    tasks = []
    if "papers" in request.sources:
        tasks.append(("papers", search_papers(request.query, request.top_k)))
    if "notion" in request.sources:
        tasks.append(("notion", search_notion(request.query, request.top_k)))
    if "web" in request.sources:
        tasks.append(("web", search_web(request.query, request.top_k)))

    # Execute all searches in parallel
    results = await asyncio.gather(*[t[1] for t in tasks], return_exceptions=True)

    # Combine results
    all_documents = []
    for i, (source_name, _) in enumerate(tasks):
        if isinstance(results[i], Exception):
            logger.error("%s search failed: %s", source_name, results[i])
            continue
        all_documents.extend(results[i])

    # Sort by score (highest first)
    all_documents.sort(key=lambda d: d.score, reverse=True)

    # Limit total results
    all_documents = all_documents[:request.top_k * len(request.sources)]

    return RetrieveResponse(
        query=request.query,
        documents=all_documents,
        sources_queried=request.sources
    )


# ==============================================================================
# Neo4j Citation Graph Endpoints
# ==============================================================================
@app.get("/citations/{doi:path}", response_model=CitationsResponse)
async def get_citations(doi: str, limit: int = 20):
    """
    Get papers that cite a given paper.

    Args:
        doi: The DOI of the paper to find citations for
        limit: Maximum number of citing papers to return (default: 20)

    Returns:
        List of papers that cite the given paper
    """
    neo4j = get_neo4j()
    if not neo4j:
        raise HTTPException(
            status_code=503,
            detail="Neo4j graph database not available"
        )

    try:
        with neo4j.session() as session:
            # First get the paper's title
            paper_result = session.run("""
                MATCH (p:Paper {doi: $doi})
                RETURN p.title as title
            """, doi=doi).single()

            paper_title = paper_result["title"] if paper_result else None

            # Get citing papers
            result = session.run("""
                MATCH (citing:Paper)-[:CITES]->(p:Paper {doi: $doi})
                RETURN citing.paper_id as paper_id,
                       citing.doi as doi,
                       citing.title as title,
                       citing.year as year,
                       citing.journal as journal
                ORDER BY citing.year DESC
                LIMIT $limit
            """, doi=doi, limit=limit)

            citing_papers = [
                PaperNode(
                    paper_id=r["paper_id"],
                    doi=r["doi"],
                    title=r["title"],
                    year=r["year"],
                    journal=r["journal"]
                )
                for r in result
            ]

        return CitationsResponse(
            doi=doi,
            paper_title=paper_title,
            citation_count=len(citing_papers),
            citing_papers=citing_papers
        )

    except Exception as e:
        logger.exception("Citations query failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/references/{doi:path}", response_model=ReferencesResponse)
async def get_references(doi: str, limit: int = 50):
    """
    Get papers that a given paper cites (its references).

    Args:
        doi: The DOI of the paper
        limit: Maximum number of references to return (default: 50)

    Returns:
        List of papers cited by the given paper
    """
    neo4j = get_neo4j()
    if not neo4j:
        raise HTTPException(
            status_code=503,
            detail="Neo4j graph database not available"
        )

    try:
        with neo4j.session() as session:
            # First get the paper's title
            paper_result = session.run("""
                MATCH (p:Paper {doi: $doi})
                RETURN p.title as title
            """, doi=doi).single()

            paper_title = paper_result["title"] if paper_result else None

            # Get referenced papers
            result = session.run("""
                MATCH (p:Paper {doi: $doi})-[:CITES]->(ref:Paper)
                RETURN ref.paper_id as paper_id,
                       ref.doi as doi,
                       ref.title as title,
                       ref.year as year,
                       ref.journal as journal
                ORDER BY ref.year DESC
                LIMIT $limit
            """, doi=doi, limit=limit)

            references = [
                PaperNode(
                    paper_id=r["paper_id"],
                    doi=r["doi"],
                    title=r["title"],
                    year=r["year"],
                    journal=r["journal"]
                )
                for r in result
            ]

        return ReferencesResponse(
            doi=doi,
            paper_title=paper_title,
            reference_count=len(references),
            references=references
        )

    except Exception as e:
        logger.exception("References query failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/author/{author_name}/papers", response_model=AuthorPapersResponse)
async def get_author_papers(author_name: str, limit: int = 50):
    """
    Get all papers by a given author.

    Args:
        author_name: The author's name (partial match supported)
        limit: Maximum number of papers to return (default: 50)

    Returns:
        List of papers authored by matching authors
    """
    neo4j = get_neo4j()
    if not neo4j:
        raise HTTPException(
            status_code=503,
            detail="Neo4j graph database not available"
        )

    try:
        with neo4j.session() as session:
            # Find matching authors and their papers, including all authors for each paper
            result = session.run("""
                MATCH (a:Author)-[:AUTHORED]->(p:Paper)
                WHERE toLower(a.name) CONTAINS toLower($name)
                WITH a, p
                ORDER BY p.year DESC
                WITH a, collect(DISTINCT p)[..$limit] as papers
                UNWIND papers as paper
                // Get all authors for each paper
                OPTIONAL MATCH (allAuthor:Author)-[:AUTHORED]->(paper)
                WITH a, paper, collect(DISTINCT allAuthor.name) as all_authors
                RETURN a.author_id as author_id,
                       a.name as matched_author,
                       paper.paper_id as paper_id,
                       paper.doi as doi,
                       paper.title as title,
                       paper.year as year,
                       paper.journal as journal,
                       all_authors
                ORDER BY paper.year DESC
            """, name=author_name, limit=limit)

            authors_found = {}
            all_papers = []
            seen_dois = set()

            for r in result:
                # Track unique matched authors
                author_id = r["author_id"]
                if author_id and author_id not in authors_found:
                    authors_found[author_id] = AuthorNode(
                        author_id=author_id,
                        name=r["matched_author"],
                        paper_count=0
                    )
                if author_id:
                    authors_found[author_id].paper_count += 1

                # Avoid duplicate papers
                paper_key = r["doi"] or r["paper_id"]
                if paper_key and paper_key in seen_dois:
                    continue
                if paper_key:
                    seen_dois.add(paper_key)

                # Format authors list
                authors_list = r["all_authors"] or []
                authors_str = ", ".join(authors_list[:5])
                if len(authors_list) > 5:
                    authors_str += " et al."

                paper = PaperNode(
                    paper_id=r["paper_id"],
                    doi=r["doi"],
                    title=r["title"],
                    year=r["year"],
                    journal=r["journal"],
                    author_name=authors_str  # All authors, not just matched
                )
                all_papers.append(paper)

            authors_found = list(authors_found.values())

        return AuthorPapersResponse(
            author_query=author_name,
            authors_found=authors_found,
            paper_count=len(all_papers),
            papers=all_papers[:limit]
        )

    except Exception as e:
        logger.exception("Author papers query failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/co-authors/{author_id}", response_model=CoAuthorsResponse)
async def get_co_authors(author_id: str, limit: int = 50):
    """
    Get the co-author network for an author.

    Args:
        author_id: The author's internal ID
        limit: Maximum number of co-authors to return (default: 50)

    Returns:
        List of co-authors with shared paper counts
    """
    neo4j = get_neo4j()
    if not neo4j:
        raise HTTPException(
            status_code=503,
            detail="Neo4j graph database not available"
        )

    try:
        with neo4j.session() as session:
            # Get author name
            author_result = session.run("""
                MATCH (a:Author {author_id: $author_id})
                RETURN a.name as name
            """, author_id=author_id).single()

            author_name = author_result["name"] if author_result else None

            if not author_name:
                raise HTTPException(
                    status_code=404,
                    detail=f"Author not found: {author_id}"
                )

            # Find co-authors (authors who share papers)
            result = session.run("""
                MATCH (a:Author {author_id: $author_id})-[:AUTHORED]->(p:Paper)<-[:AUTHORED]-(coauthor:Author)
                WHERE coauthor.author_id <> $author_id
                WITH coauthor, count(p) as shared_papers
                RETURN coauthor.author_id as author_id,
                       coauthor.name as name,
                       shared_papers
                ORDER BY shared_papers DESC
                LIMIT $limit
            """, author_id=author_id, limit=limit)

            co_authors = [
                CoAuthorNode(
                    author_id=r["author_id"],
                    name=r["name"],
                    shared_papers=r["shared_papers"]
                )
                for r in result
            ]

        return CoAuthorsResponse(
            author_id=author_id,
            author_name=author_name,
            co_author_count=len(co_authors),
            co_authors=co_authors
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Co-authors query failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/graph/stats", response_model=GraphStatsResponse)
async def get_graph_stats():
    """
    Get statistics about the citation graph.

    Returns:
        Counts of papers, authors, and citations in the graph
    """
    neo4j = get_neo4j()
    if not neo4j:
        raise HTTPException(
            status_code=503,
            detail="Neo4j graph database not available"
        )

    try:
        with neo4j.session() as session:
            result = session.run("""
                MATCH (p:Paper)
                WITH count(p) as total_papers,
                     count(p.doi) as papers_with_doi
                MATCH (a:Author)
                WITH total_papers, papers_with_doi, count(a) as total_authors
                MATCH ()-[c:CITES]->()
                RETURN total_papers, papers_with_doi, total_authors, count(c) as total_citations
            """).single()

            if result:
                return GraphStatsResponse(
                    total_papers=result["total_papers"],
                    total_authors=result["total_authors"],
                    total_citations=result["total_citations"],
                    papers_with_doi=result["papers_with_doi"]
                )
            else:
                return GraphStatsResponse(
                    total_papers=0,
                    total_authors=0,
                    total_citations=0,
                    papers_with_doi=0
                )

    except Exception as e:
        logger.exception("Graph stats query failed")
        raise HTTPException(status_code=500, detail=str(e))


# ==============================================================================
# Paper PDF Download
# ==============================================================================
@app.get("/paper/{doi:path}/pdf")
async def download_paper_pdf(doi: str):
    """
    Download the PDF file for a paper.

    Args:
        doi: The DOI of the paper (e.g., "10.1111/j.1745-7254.2008.00726.x")

    Returns:
        The PDF file as a download

    Raises:
        404: If the PDF file is not found
    """
    pdf_path = get_pdf_path(doi)

    if not pdf_path:
        raise HTTPException(
            status_code=404,
            detail=f"PDF not found for DOI: {doi}. The paper may not have been downloaded yet."
        )

    # Generate a friendly filename for download
    # Use last part of DOI for the filename
    doi_suffix = doi.split("/")[-1] if "/" in doi else doi
    download_filename = f"{doi_suffix}.pdf"

    return FileResponse(
        path=pdf_path,
        media_type="application/pdf",
        filename=download_filename
    )


@app.get("/paper/{doi:path}/pdf/exists")
async def check_paper_pdf_exists(doi: str):
    """
    Check if a PDF file exists for a paper.

    Args:
        doi: The DOI of the paper

    Returns:
        JSON with exists status and download URL if available
    """
    pdf_path = get_pdf_path(doi)
    exists = pdf_path is not None

    response = {
        "doi": doi,
        "pdf_available": exists,
    }

    if exists:
        response["download_url"] = f"/paper/{doi}/pdf"

    return response


# ==============================================================================
# Hybrid Search Endpoints
# ==============================================================================
@app.post("/search/hybrid", response_model=HybridSearchResponse)
async def hybrid_search(request: HybridSearchRequest):
    """
    Hybrid search combining vector similarity with citation graph signals.

    The combined score is computed as:
        score = vector_weight * vector_score + citation_weight * citation_score

    Citation score is log-normalized to handle papers with very high citation counts.

    Args:
        request: HybridSearchRequest with query and scoring weights

    Returns:
        List of papers with enriched metadata and combined scores
    """
    qdrant = get_qdrant()
    embedder = database.get_paper_encoder()

    if not qdrant or not embedder:
        raise HTTPException(
            status_code=503,
            detail="Vector database or embedder not available"
        )

    # Validate weights
    if request.vector_weight + request.citation_weight == 0:
        raise HTTPException(
            status_code=400,
            detail="At least one weight must be non-zero"
        )

    try:
        # Check if collection exists
        collections = qdrant.get_collections().collections
        if not any(c.name == database.PAPERS_COLLECTION for c in collections):
            return HybridSearchResponse(
                query=request.query,
                total_results=0,
                vector_weight=request.vector_weight,
                citation_weight=request.citation_weight,
                papers=[]
            )

        # Build Qdrant filter for year range
        query_filter = None
        if request.year_min or request.year_max:
            from qdrant_client.models import Filter, FieldCondition, Range
            conditions = []
            if request.year_min:
                conditions.append(
                    FieldCondition(key="year", range=Range(gte=request.year_min))
                )
            if request.year_max:
                conditions.append(
                    FieldCondition(key="year", range=Range(lte=request.year_max))
                )
            query_filter = Filter(must=conditions)

        # Generate query embedding and search (BGE prefix on query; "" for SPECTER)
        vector = embedder.encode(database.PAPER_QUERY_PREFIX + request.query).tolist()

        # Get more results than needed for re-ranking
        fetch_k = min(request.top_k * 3, 100)

        results = qdrant.query_points(
            collection_name=database.PAPERS_COLLECTION,
            query=vector,
            limit=fetch_k,
            query_filter=query_filter
        )

        if not results.points:
            return HybridSearchResponse(
                query=request.query,
                total_results=0,
                vector_weight=request.vector_weight,
                citation_weight=request.citation_weight,
                papers=[]
            )

        # Collect DOIs for enrichment
        dois = [hit.payload.get("doi") for hit in results.points if hit.payload.get("doi")]

        # Get citation counts from Neo4j
        citation_data = get_citation_counts(dois) if dois else {}

        # Find max citations for normalization
        max_citations = max(
            (d.get("citation_count", 0) for d in citation_data.values()),
            default=1
        )

        # Build enriched papers with combined scores
        enriched_papers = []
        for hit in results.points:
            doi = hit.payload.get("doi")
            citation_info = citation_data.get(doi, {})
            citation_count = citation_info.get("citation_count", 0)
            reference_count = citation_info.get("reference_count", 0)

            # Compute scores
            vector_score = float(hit.score)
            citation_score = compute_citation_score(citation_count, max_citations)

            # Normalize weights
            total_weight = request.vector_weight + request.citation_weight
            norm_vector_weight = request.vector_weight / total_weight
            norm_citation_weight = request.citation_weight / total_weight

            combined_score = (
                norm_vector_weight * vector_score +
                norm_citation_weight * citation_score
            )

            # Get authors
            authors = hit.payload.get("authors", [])
            if isinstance(authors, list):
                authors = [a if isinstance(a, str) else a.get("name", "") for a in authors]

            enriched_papers.append(EnrichedPaper(
                paper_id=hit.id if isinstance(hit.id, str) else str(hit.id),
                doi=doi,
                title=hit.payload.get("title", "Untitled"),
                authors=authors[:10],
                year=hit.payload.get("year"),
                journal=hit.payload.get("journal"),
                abstract=hit.payload.get("abstract"),
                pdf_path=hit.payload.get("pdf_path"),
                citation_count=citation_count,
                reference_count=reference_count,
                vector_score=round(vector_score, 4),
                citation_score=round(citation_score, 4),
                combined_score=round(combined_score, 4)
            ))

        # Sort by combined score
        enriched_papers.sort(key=lambda p: p.combined_score, reverse=True)

        # Limit to top_k
        enriched_papers = enriched_papers[:request.top_k]

        return HybridSearchResponse(
            query=request.query,
            total_results=len(enriched_papers),
            vector_weight=request.vector_weight,
            citation_weight=request.citation_weight,
            papers=enriched_papers
        )

    except Exception as e:
        logger.exception("Hybrid search failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/search/similar-by-authors", response_model=SimilarByAuthorsResponse)
async def similar_by_authors(request: SimilarByAuthorsRequest):
    """
    Find similar papers by the same authors as a given paper.

    This is useful for exploring an author's related work when you find
    an interesting paper.

    Args:
        request: SimilarByAuthorsRequest with source DOI

    Returns:
        List of papers by the same authors, ranked by citation count
    """
    neo4j = get_neo4j()
    if not neo4j:
        raise HTTPException(
            status_code=503,
            detail="Neo4j graph database not available"
        )

    try:
        # Get source paper info
        with neo4j.session() as session:
            source_result = session.run("""
                MATCH (p:Paper {doi: $doi})
                OPTIONAL MATCH (a:Author)-[:AUTHORED]->(p)
                RETURN p.title as title,
                       collect(DISTINCT a.name) as authors
            """, doi=request.doi).single()

            if not source_result or not source_result["title"]:
                raise HTTPException(
                    status_code=404,
                    detail=f"Paper not found: {request.doi}"
                )

            source_title = source_result["title"]
            source_authors = source_result["authors"] or []

        # Get other papers by same authors
        other_papers = get_authors_other_papers(request.doi, limit=request.top_k)

        # Find max citations for normalization
        max_citations = max(
            (p.get("citation_count", 0) for p in other_papers),
            default=1
        )

        # Build enriched papers
        similar_papers = []
        for paper in other_papers:
            citation_count = paper.get("citation_count", 0)
            citation_score = compute_citation_score(citation_count, max_citations)

            similar_papers.append(EnrichedPaper(
                paper_id=paper.get("paper_id"),
                doi=paper.get("doi"),
                title=paper.get("title") or "Untitled",
                authors=paper.get("shared_authors", []),
                year=paper.get("year"),
                journal=paper.get("journal"),
                abstract=paper.get("abstract"),
                citation_count=citation_count,
                reference_count=0,  # Not fetched for performance
                vector_score=0.0,  # Not applicable
                citation_score=round(citation_score, 4),
                combined_score=round(citation_score, 4)  # Just citation score
            ))

        return SimilarByAuthorsResponse(
            source_doi=request.doi,
            source_title=source_title,
            source_authors=source_authors,
            similar_papers=similar_papers
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Similar by authors search failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/paper/{doi:path}/enriched", response_model=EnrichedPaper)
async def get_enriched_paper(doi: str):
    """
    Get a single paper with enriched metadata from both Qdrant and Neo4j.

    Args:
        doi: The DOI of the paper

    Returns:
        Paper with full metadata including citation counts
    """
    qdrant = get_qdrant()

    if not qdrant:
        raise HTTPException(
            status_code=503,
            detail="Vector database not available"
        )

    try:
        from qdrant_client.models import Filter, FieldCondition, MatchValue

        # Get paper from Qdrant
        results = qdrant.scroll(
            collection_name=database.PAPERS_COLLECTION,
            scroll_filter=Filter(
                must=[FieldCondition(key="doi", match=MatchValue(value=doi))]
            ),
            limit=1,
            with_payload=True
        )

        if not results[0]:
            raise HTTPException(
                status_code=404,
                detail=f"Paper not found: {doi}"
            )

        paper_data = results[0][0].payload
        paper_id = results[0][0].id

        # Get citation counts
        citation_info = get_citation_counts([doi]).get(doi, {})

        # Get authors
        authors = paper_data.get("authors", [])
        if isinstance(authors, list):
            authors = [a if isinstance(a, str) else a.get("name", "") for a in authors]

        return EnrichedPaper(
            paper_id=str(paper_id),
            doi=doi,
            title=paper_data.get("title", "Untitled"),
            authors=authors,
            year=paper_data.get("year"),
            journal=paper_data.get("journal"),
            abstract=paper_data.get("abstract"),
            pdf_path=paper_data.get("pdf_path"),
            citation_count=citation_info.get("citation_count", 0),
            reference_count=citation_info.get("reference_count", 0),
            vector_score=0.0,
            citation_score=0.0,
            combined_score=0.0
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Get enriched paper failed")
        raise HTTPException(status_code=500, detail=str(e))


# ==============================================================================
# Deep Research Endpoints
# ==============================================================================
@app.post("/deepresearch/submit", response_model=DeepResearchSubmitResponse)
async def submit_deepresearch(request: DeepResearchRequest):
    """
    Submit a deep research question for processing.

    The question will be queued for processing by a 30B thinking model
    running on the cluster GPU. Results will be available as Markdown and PDF.
    """
    if not DEEPRESEARCH_ENABLED:
        raise HTTPException(
            status_code=503,
            detail={"error": {"message": "Deep Research is disabled. Past reports remain available."}},
        )

    if not request.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty")

    if len(request.question) > 10000:
        raise HTTPException(status_code=400, detail="Question too long (max 10,000 characters)")

    # Check if queue directory exists
    if not os.path.exists(DEEPRESEARCH_QUEUE_DIR):
        raise HTTPException(
            status_code=503,
            detail="Deep Research service not configured. Queue directory not available."
        )

    # Generate request ID
    request_id = str(uuid.uuid4())

    # Create job request
    job_request = {
        "request_id": request_id,
        "question": request.question.strip(),
        "context": request.context.strip() if request.context else None,
        "submitted_at": datetime.utcnow().isoformat() + "Z",
        "status": "queued"
    }

    # Write to queue directory
    queue_file = os.path.join(DEEPRESEARCH_QUEUE_DIR, f"{request_id}.json")
    try:
        with open(queue_file, "w") as f:
            json.dump(job_request, f, indent=2)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to queue job: {str(e)}")

    return DeepResearchSubmitResponse(
        request_id=request_id,
        message="Research question submitted successfully. Check status for updates.",
        estimated_wait="5-30 minutes depending on cluster queue"
    )


@app.get("/deepresearch/status/{request_id}", response_model=DeepResearchStatus)
async def get_deepresearch_status(request_id: str):
    """
    Get the status of a deep research job.
    """
    # First check if still in queue (not yet picked up by daemon)
    queue_file = os.path.join(DEEPRESEARCH_QUEUE_DIR, f"{request_id}.json")
    if os.path.exists(queue_file):
        try:
            with open(queue_file) as f:
                data = json.load(f)
            return DeepResearchStatus(
                request_id=request_id,
                status="queued",
                question=data.get("question", ""),
                submitted_at=data.get("submitted_at", ""),
                output_available=False,
                pdf_available=False
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to read queue file: {str(e)}")

    # Check job directory
    job_dir = os.path.join(DEEPRESEARCH_JOBS_DIR, request_id)
    status_file = os.path.join(job_dir, "status.json")

    if not os.path.exists(status_file):
        raise HTTPException(status_code=404, detail="Job not found")

    try:
        with open(status_file) as f:
            status_data = json.load(f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read status file: {str(e)}")

    # Check for output files
    output_md = os.path.join(job_dir, "output.md")
    output_pdf = os.path.join(job_dir, "output.pdf")

    return DeepResearchStatus(
        request_id=request_id,
        job_id=status_data.get("slurm_job_id"),
        status=status_data.get("status", "unknown"),
        question=status_data.get("question", ""),
        submitted_at=status_data.get("submitted_at", ""),
        started_at=status_data.get("started_at"),
        completed_at=status_data.get("completed_at"),
        slurm_state=status_data.get("slurm_state"),
        queue_position=status_data.get("queue_position"),
        output_available=os.path.exists(output_md),
        pdf_available=os.path.exists(output_pdf),
        error=status_data.get("error")
    )


@app.get("/deepresearch/output/{request_id}")
async def get_deepresearch_output(request_id: str, format: str = Query("md", regex="^(md|pdf)$")):
    """
    Get the output of a completed deep research job.

    Args:
        request_id: The job request ID
        format: Output format - 'md' for Markdown or 'pdf' for PDF
    """
    job_dir = os.path.join(DEEPRESEARCH_JOBS_DIR, request_id)

    if not os.path.exists(job_dir):
        raise HTTPException(status_code=404, detail="Job not found")

    if format == "md":
        output_file = os.path.join(job_dir, "output.md")
        media_type = "text/markdown"
        filename = f"research_{request_id[:8]}.md"
    else:
        output_file = os.path.join(job_dir, "output.pdf")
        media_type = "application/pdf"
        filename = f"research_{request_id[:8]}.pdf"

    if not os.path.exists(output_file):
        raise HTTPException(
            status_code=404,
            detail=f"Output not available yet. The job may still be processing."
        )

    return FileResponse(output_file, media_type=media_type, filename=filename)


@app.get("/deepresearch/queue", response_model=SlurmQueueResponse)
async def get_slurm_queue():
    """
    Get the current SLURM queue status and GPU information.

    This shows all jobs in the cluster queue and GPU usage,
    helping users understand wait times for their deep research jobs.
    """
    if not os.path.exists(SLURM_QUEUE_FILE):
        return SlurmQueueResponse(
            total_jobs=0,
            deepresearch_jobs=0,
            queue=[],
            gpus=[],
            updated_at=None
        )

    try:
        with open(SLURM_QUEUE_FILE) as f:
            data = json.load(f)

        # Parse queue entries
        queue_entries = []
        for entry in data.get("queue", []):
            try:
                queue_entries.append(SlurmQueueEntry(**entry))
            except Exception as e:
                logger.warning("Failed to parse queue entry: %s", e)

        # Parse GPU info
        gpu_entries = []
        for gpu_data in data.get("gpus", []):
            try:
                # Handle error case
                if "error" in gpu_data and len(gpu_data) == 1:
                    gpu_entries.append(GpuInfo(
                        index=0, name="Unknown", memory_used_mb=0,
                        memory_total_mb=0, error=gpu_data["error"]
                    ))
                else:
                    # Parse processes
                    processes = [
                        GpuProcess(**proc)
                        for proc in gpu_data.get("processes", [])
                    ]
                    gpu_entries.append(GpuInfo(
                        index=gpu_data.get("index", 0),
                        name=gpu_data.get("name", "Unknown"),
                        memory_used_mb=gpu_data.get("memory_used_mb", 0),
                        memory_total_mb=gpu_data.get("memory_total_mb", 0),
                        utilization_percent=gpu_data.get("utilization_percent", 0),
                        processes=processes
                    ))
            except Exception as e:
                logger.warning("Failed to parse GPU data: %s", e)

        return SlurmQueueResponse(
            total_jobs=data.get("total_jobs", 0),
            deepresearch_jobs=data.get("deepresearch_jobs", 0),
            queue=queue_entries,
            gpus=gpu_entries,
            updated_at=data.get("updated_at")
        )
    except Exception as e:
        logger.exception("Failed to read SLURM queue file")
        return SlurmQueueResponse(
            total_jobs=0,
            deepresearch_jobs=0,
            queue=[],
            gpus=[],
            updated_at=None
        )


@app.get("/deepresearch/jobs")
async def list_deepresearch_jobs(limit: int = Query(20, ge=1, le=100)):
    """
    List recent deep research jobs.

    Returns a summary of recent jobs with their status.
    """
    jobs = []

    # Check queue directory for pending jobs
    if os.path.exists(DEEPRESEARCH_QUEUE_DIR):
        for filename in os.listdir(DEEPRESEARCH_QUEUE_DIR):
            if filename.endswith(".json"):
                try:
                    with open(os.path.join(DEEPRESEARCH_QUEUE_DIR, filename)) as f:
                        data = json.load(f)
                    jobs.append({
                        "request_id": data.get("request_id"),
                        "question": data.get("question", "")[:100] + "..." if len(data.get("question", "")) > 100 else data.get("question", ""),
                        "status": "queued",
                        "submitted_at": data.get("submitted_at"),
                        "completed_at": None
                    })
                except Exception:
                    pass

    # Check jobs directory for processed jobs
    if os.path.exists(DEEPRESEARCH_JOBS_DIR):
        for job_id in os.listdir(DEEPRESEARCH_JOBS_DIR):
            job_dir = os.path.join(DEEPRESEARCH_JOBS_DIR, job_id)
            status_file = os.path.join(job_dir, "status.json")
            if os.path.exists(status_file):
                try:
                    with open(status_file) as f:
                        data = json.load(f)
                    jobs.append({
                        "request_id": data.get("request_id"),
                        "question": data.get("question", "")[:100] + "..." if len(data.get("question", "")) > 100 else data.get("question", ""),
                        "status": data.get("status"),
                        "submitted_at": data.get("submitted_at"),
                        "completed_at": data.get("completed_at")
                    })
                except Exception:
                    pass

    # Sort by submitted_at descending
    jobs.sort(key=lambda x: x.get("submitted_at", ""), reverse=True)

    return jobs[:limit]


# ==============================================================================
# Startup
# ==============================================================================
@app.on_event("startup")
async def startup():
    """Initialize connections on startup."""
    print("=" * 60)
    print("MUNIN RETRIEVAL SERVICE")
    print("=" * 60)

    # Initialize database connections
    logger.info("Initializing database connections...")
    qdrant = get_qdrant()
    neo4j = get_neo4j()

    # Spawn the SSE stream-registry janitor (P1 #10). Drops streams
    # that have been done > 60s so the in-memory log doesn't grow
    # unbounded.
    stream_registry_module.registry.start_janitor()

    # Initialize chat persistence (SQLite)
    try:
        await chat_store.init_db()
        logger.info("Chat store initialized at %s", chat_store.CHATS_DB_PATH)
    except Exception:
        logger.exception("Failed to initialize chat store")

    # Load persona definitions from disk
    try:
        loaded = persona_module.load_personas()
        logger.info(
            "Loaded %d personas from %s", len(loaded), persona_module.PERSONAS_DIR
        )
    except Exception:
        logger.exception("Failed to load personas")

    # Ensure the Qdrant user_docs collection exists (best-effort)
    try:
        document_store.ensure_collection()
    except Exception as e:
        logger.warning("Failed to ensure user_docs collection: %s", e)

    # §28 Sprint B: payload indexes on the `papers` collection so
    # contributor/topic filters on paper_search don't do full scans.
    # Qdrant create_payload_index raises if the index already exists;
    # we swallow and move on so repeated starts are idempotent.
    try:
        qd = get_qdrant()
        if qd is not None:
            from qdrant_client.http import models as qm
            for key, schema in (
                ("contributors[].group_slug", qm.PayloadSchemaType.KEYWORD),
                ("contributors[].username", qm.PayloadSchemaType.KEYWORD),
                ("contributors[].email", qm.PayloadSchemaType.KEYWORD),
                ("topic_slug", qm.PayloadSchemaType.KEYWORD),
                ("cluster_id", qm.PayloadSchemaType.INTEGER),
            ):
                try:
                    qd.create_payload_index(
                        collection_name=database.PAPERS_COLLECTION,
                        field_name=key,
                        field_schema=schema,
                    )
                except Exception:
                    pass  # already exists or collection not present yet
    except Exception as e:
        logger.warning("Failed to ensure papers payload indexes: %s", e)

    # Load agent registry
    try:
        loaded_agents = agents_pkg.load_agents()
        logger.info("Loaded %d agents", len(loaded_agents))
    except Exception:
        logger.exception("Failed to load agents")

    # Load hooks (P2 #23). Auto-imports every hooks/*.py so module-
    # level @register decorators populate the registry. Hook
    # exceptions during import are logged but don't block startup.
    try:
        import hooks as hooks_pkg

        n_hooks = hooks_pkg.load_all()
        logger.info("Loaded %d hooks", n_hooks)
    except Exception:
        logger.exception("Failed to load hooks")

    # P1 #11: pull contributors.yml from the VPS auth service. The
    # auth DB is the source of truth; we mirror its YAML projection
    # to CONTRIBUTORS_CONFIG every CONTRIBUTORS_SYNC_INTERVAL_SECS
    # so the existing mtime-watch in _load_contributors() picks up
    # changes without a restart.
    try:
        import contributors_sync

        contributors_sync.start_sync_task()
    except Exception:
        logger.exception("Failed to start contributors_sync")

    # MCP dispatch registry consistency check (P2 #19). Catches the
    # three drift modes the old if/elif chain allowed: schema entry
    # with no executor branch, executor branch with no schema entry,
    # duplicate registration. Failure raises — internal developer
    # invariant, container restarts until fixed.
    from mcp._dispatch import verify_dispatch_registry

    verify_dispatch_registry()
    logger.info("MCP dispatch registry verified")

    # Eager-load embedding models so /api/status reflects real
    # readiness instead of "unavailable" (which used to mean
    # either failed-to-load or lazy-not-yet-called — confusing
    # both operators and the dashboard). Each model is wrapped
    # individually so a single failure doesn't block the other.
    logger.info("Loading embedding models (this may take a moment)...")
    try:
        get_specter()
    except Exception:
        logger.exception("SPECTER preload failed")
    try:
        # Preload the ACTIVE paper encoder (BGE-large when PAPER_ENCODER=bge-large;
        # a no-op alias of SPECTER otherwise).
        database.get_paper_encoder()
    except Exception:
        logger.exception("paper encoder preload failed")
    try:
        get_bge()
    except Exception:
        logger.exception("BGE preload failed")

    logger.info(
        "Service ready (qdrant=%s neo4j=%s searxng=%s vllm=%s)",
        f"{QDRANT_HOST}:{QDRANT_PORT}" + (" [OK]" if qdrant else " [UNAVAILABLE]"),
        NEO4J_URI + (" [OK]" if neo4j else " [UNAVAILABLE]"),
        SEARXNG_URL,
        VLLM_URL,
    )
    print("\n" + "=" * 60)
    print("Service ready!")
    print("=" * 60)
    print(f"  Qdrant:  {QDRANT_HOST}:{QDRANT_PORT} {'[OK]' if qdrant else '[UNAVAILABLE]'}")
    print(f"  Neo4j:   {NEO4J_URI} {'[OK]' if neo4j else '[UNAVAILABLE]'}")
    print(f"  SearXNG: {SEARXNG_URL}")
    print(f"  vLLM:    {VLLM_URL}")
    print("=" * 60)
    print("\nEndpoints:")
    print("  Core:")
    print("    GET  /health                    - Health check")
    print("    GET  /sources                   - List knowledge sources")
    print("    POST /retrieve                  - RAG retrieval")
    print("  MCP (Model Context Protocol):")
    print("    GET  /mcp/sse                   - SSE endpoint (Open WebUI)")
    print("    POST /mcp/messages              - JSON-RPC messages")
    print("    POST /mcp/call                  - REST tool execution")
    print("    GET  /mcp/tools                 - List available tools")
    print("  Citation Graph:")
    print("    GET  /citations/{doi}           - Papers citing a paper")
    print("    GET  /references/{doi}          - Papers cited by a paper")
    print("    GET  /author/{name}/papers      - Papers by author")
    print("    GET  /co-authors/{id}           - Co-author network")
    print("    GET  /graph/stats               - Graph statistics")
    print("  PDF Downloads:")
    print("    GET  /paper/{doi}/pdf           - Download paper PDF")
    print("    GET  /paper/{doi}/pdf/exists    - Check PDF availability")
    print("  Hybrid Search:")
    print("    POST /search/hybrid             - Vector + citation search")
    print("    POST /search/similar-by-authors - Papers by same authors")
    print("    GET  /paper/{doi}/enriched      - Paper with citation metadata")
    print("=" * 60)


# ============================================================================
# ADMIN METRICS PROXY (2026-06-02)
# ============================================================================
# Forwards PromQL queries from the webui Admin panel -> Metrics tab to
# the Prometheus container next door. Gated by checking that the
# caller's X-Munin-Email (set by Caddy forward-auth) has role=admin
# via auth.muninai.org/admin/check-role. See metrics_proxy.py for the
# wire details.


async def _require_admin_email(request: Request) -> str:
    """Resolve X-Munin-Email and verify admin role. Returns the email
    on success; raises HTTPException on missing header / unknown user /
    non-admin role / proxy not configured."""
    if not metrics_proxy.is_configured():
        raise HTTPException(
            status_code=503,
            detail={"error": {"message": "Metrics proxy not configured (KB_GATE_TOKEN unset)"}},
        )
    email = (request.headers.get("X-Munin-Email") or "").strip().lower()
    if not email:
        raise HTTPException(
            status_code=401,
            detail={"error": {"message": "Missing X-Munin-Email header"}},
        )
    try:
        async with httpx.AsyncClient() as client:
            role = await metrics_proxy.lookup_role(client, email)
    except httpx.HTTPError as e:
        raise HTTPException(
            status_code=502,
            detail={"error": {"message": f"auth role lookup failed: {e}"}},
        )
    if role != "admin":
        raise HTTPException(
            status_code=403,
            detail={"error": {"message": "Admin role required"}},
        )
    return email


@app.post("/api/admin/metrics/query")
async def api_admin_metrics_query(request: Request):
    """Proxy a Prometheus instant query.

    Body (JSON):
        {"query": "<promql>", "time": "<rfc3339>"?}

    Returns Prometheus's raw response body. Errors are forwarded as
    JSONResponse with the upstream status code so the frontend can
    distinguish auth failures from query syntax errors."""
    await _require_admin_email(request)
    body = await request.json()
    query = (body.get("query") or "").strip()
    if not query:
        raise HTTPException(
            status_code=400,
            detail={"error": {"message": "`query` is required"}},
        )
    time_param = body.get("time") or None
    try:
        async with httpx.AsyncClient() as client:
            return JSONResponse(
                await metrics_proxy.query_instant(client, query, time_param)
            )
    except httpx.HTTPStatusError as e:
        return JSONResponse(
            {"error": {"message": e.response.text[:500]}},
            status_code=e.response.status_code,
        )
    except httpx.HTTPError as e:
        raise HTTPException(
            status_code=502,
            detail={"error": {"message": f"prometheus unreachable: {e}"}},
        )


@app.post("/api/admin/metrics/query_range")
async def api_admin_metrics_query_range(request: Request):
    """Proxy a Prometheus range query.

    Body (JSON):
        {"query": "<promql>", "start": "<rfc3339>", "end": "<rfc3339>", "step": "15s"}
    """
    await _require_admin_email(request)
    body = await request.json()
    query = (body.get("query") or "").strip()
    start = (body.get("start") or "").strip()
    end = (body.get("end") or "").strip()
    step = (body.get("step") or "").strip()
    missing = [k for k, v in (("query", query), ("start", start),
                              ("end", end), ("step", step)) if not v]
    if missing:
        raise HTTPException(
            status_code=400,
            detail={"error": {"message": f"missing fields: {', '.join(missing)}"}},
        )
    try:
        async with httpx.AsyncClient() as client:
            return JSONResponse(
                await metrics_proxy.query_range(client, query, start, end, step)
            )
    except httpx.HTTPStatusError as e:
        return JSONResponse(
            {"error": {"message": e.response.text[:500]}},
            status_code=e.response.status_code,
        )
    except httpx.HTTPError as e:
        raise HTTPException(
            status_code=502,
            detail={"error": {"message": f"prometheus unreachable: {e}"}},
        )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
