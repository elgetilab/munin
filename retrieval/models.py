"""
Pydantic models for the Munin Retrieval Service.
"""

from typing import Optional
from pydantic import BaseModel


# ==============================================================================
# Core Retrieval Models
# ==============================================================================
class RetrieveRequest(BaseModel):
    """Request to retrieve documents."""
    query: str
    sources: list[str] = ["papers", "notion", "web"]
    top_k: int = 5

    class Config:
        json_schema_extra = {
            "example": {
                "query": "transformer attention mechanisms",
                "sources": ["papers", "notion", "web"],
                "top_k": 5
            }
        }


class RetrievedDocument(BaseModel):
    """A retrieved document."""
    source: str
    content: str
    score: float
    metadata: dict


class RetrieveResponse(BaseModel):
    """Response with retrieved documents."""
    query: str
    documents: list[RetrievedDocument]
    sources_queried: list[str]


# ==============================================================================
# Neo4j Response Models
# ==============================================================================
class PaperNode(BaseModel):
    """A paper from the citation graph."""
    paper_id: Optional[str] = None
    doi: Optional[str] = None
    title: Optional[str] = None
    year: Optional[int] = None
    journal: Optional[str] = None
    abstract: Optional[str] = None
    author_name: Optional[str] = None  # Matched author name (for author search)


class AuthorNode(BaseModel):
    """An author from the citation graph."""
    author_id: str
    name: str
    paper_count: Optional[int] = None


class CitationsResponse(BaseModel):
    """Response for citation queries."""
    doi: str
    paper_title: Optional[str] = None
    citation_count: int
    citing_papers: list[PaperNode]


class ReferencesResponse(BaseModel):
    """Response for reference queries."""
    doi: str
    paper_title: Optional[str] = None
    reference_count: int
    references: list[PaperNode]


class AuthorPapersResponse(BaseModel):
    """Response for author paper queries."""
    author_query: str
    authors_found: list[AuthorNode]
    paper_count: int
    papers: list[PaperNode]


class CoAuthorNode(BaseModel):
    """A co-author with collaboration count."""
    author_id: str
    name: str
    shared_papers: int


class CoAuthorsResponse(BaseModel):
    """Response for co-author network queries."""
    author_id: str
    author_name: Optional[str] = None
    co_author_count: int
    co_authors: list[CoAuthorNode]


class GraphStatsResponse(BaseModel):
    """Response for graph statistics."""
    total_papers: int
    total_authors: int
    total_citations: int
    papers_with_doi: int


# ==============================================================================
# Hybrid Search Models
# ==============================================================================
class HybridSearchRequest(BaseModel):
    """Request for hybrid search combining vector and graph signals."""
    query: str
    top_k: int = 10
    vector_weight: float = 0.7  # Weight for vector similarity (0-1)
    citation_weight: float = 0.3  # Weight for citation score (0-1)
    include_authors: bool = True  # Include papers by same authors
    year_min: Optional[int] = None
    year_max: Optional[int] = None

    class Config:
        json_schema_extra = {
            "example": {
                "query": "transformer attention mechanisms",
                "top_k": 10,
                "vector_weight": 0.7,
                "citation_weight": 0.3,
                "include_authors": True
            }
        }


class EnrichedPaper(BaseModel):
    """A paper with enriched metadata from both vector and graph databases."""
    paper_id: Optional[str] = None
    doi: Optional[str] = None
    title: str
    authors: list[str] = []
    year: Optional[int] = None
    journal: Optional[str] = None
    abstract: Optional[str] = None
    pdf_path: Optional[str] = None
    # Enriched fields from Neo4j
    citation_count: int = 0  # How many papers cite this one
    reference_count: int = 0  # How many papers this cites
    # Scores
    vector_score: float = 0.0
    citation_score: float = 0.0  # Normalized citation impact
    combined_score: float = 0.0


class HybridSearchResponse(BaseModel):
    """Response for hybrid search."""
    query: str
    total_results: int
    vector_weight: float
    citation_weight: float
    papers: list[EnrichedPaper]


class SimilarByAuthorsRequest(BaseModel):
    """Request to find similar papers by the same authors."""
    doi: str
    top_k: int = 10

    class Config:
        json_schema_extra = {
            "example": {
                "doi": "10.1038/s41586-021-03819-2",
                "top_k": 10
            }
        }


class SimilarByAuthorsResponse(BaseModel):
    """Response for similar papers by same authors."""
    source_doi: str
    source_title: Optional[str] = None
    source_authors: list[str] = []
    similar_papers: list[EnrichedPaper]


# ==============================================================================
# Deep Research Models
# ==============================================================================
class DeepResearchRequest(BaseModel):
    """Request to submit a deep research job."""
    question: str
    context: Optional[str] = None

    class Config:
        json_schema_extra = {
            "example": {
                "question": "What are the current therapeutic approaches for treating glioblastoma?",
                "context": "Focus on immunotherapy and targeted molecular therapies"
            }
        }


class DeepResearchSubmitResponse(BaseModel):
    """Response after submitting a deep research job."""
    request_id: str
    message: str
    estimated_wait: str


class DeepResearchStatus(BaseModel):
    """Status of a deep research job."""
    request_id: str
    job_id: Optional[str] = None
    status: str  # queued, pending, running, completed, failed
    question: str
    submitted_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    slurm_state: Optional[str] = None
    queue_position: Optional[int] = None
    output_available: bool = False
    pdf_available: bool = False
    error: Optional[str] = None


class SlurmQueueEntry(BaseModel):
    """A SLURM queue entry."""
    job_id: str
    job_name: str
    user: str
    partition: str
    state: str
    time: str
    nodes: str
    gres: Optional[str] = None  # GPU resource allocation


class GpuProcess(BaseModel):
    """A process running on a GPU."""
    pid: str
    name: str
    memory_mb: int


class GpuInfo(BaseModel):
    """GPU status information."""
    index: int
    name: str
    memory_used_mb: int
    memory_total_mb: int
    utilization_percent: int = 0
    processes: list[GpuProcess] = []
    error: Optional[str] = None


class SlurmQueueResponse(BaseModel):
    """SLURM queue status."""
    total_jobs: int
    deepresearch_jobs: int
    queue: list[SlurmQueueEntry]
    gpus: list[GpuInfo] = []
    updated_at: Optional[str] = None


# ==============================================================================
# MCP Models
# ==============================================================================
class MCPCallRequest(BaseModel):
    """Request to call an MCP tool via REST."""
    name: str
    arguments: dict = {}

    class Config:
        json_schema_extra = {
            "example": {
                "name": "paper_search",
                "arguments": {"query": "protein folding", "top_k": 5}
            }
        }
