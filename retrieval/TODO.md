# Retrieval Service - TODO

## Current Status

The retrieval service (`main.py`) provides a FastAPI-based RAG retrieval API with functionality for querying multiple knowledge bases including citation graph queries and hybrid search.

### What's Implemented

- [x] FastAPI application structure
- [x] Three knowledge sources: `papers`, `notion`, `web`
- [x] Qdrant integration for vector search
- [x] SearXNG integration for web search
- [x] SPECTER embedder for papers (lazy-loaded)
- [x] BGE embedder for Notion (lazy-loaded)
- [x] Parallel query execution across sources
- [x] Health check endpoint (includes Neo4j status)
- [x] Source listing endpoint with collection stats

**Neo4j Citation Graph:**
- [x] `/citations/{doi}` endpoint - Get papers that cite a given paper
- [x] `/references/{doi}` endpoint - Get papers cited by a given paper
- [x] `/author/{name}/papers` endpoint - Get all papers by an author
- [x] `/co-authors/{author_id}` endpoint - Get co-author network
- [x] `/graph/stats` endpoint - Get graph statistics

**Hybrid Search (Vector + Graph):**
- [x] `POST /search/hybrid` - Hybrid search with configurable weights
- [x] Hybrid scoring: `score = α * vector_score + β * citation_score`
- [x] Log-normalized citation scoring for high-citation papers
- [x] Year range filtering support
- [x] Citation-aware re-ranking

**Paper Metadata Enrichment:**
- [x] `POST /search/similar-by-authors` - Find papers by same authors
- [x] `GET /paper/{doi}/enriched` - Get paper with full metadata
- [x] Citation count from Neo4j
- [x] Reference count from Neo4j
- [x] Full abstract in results
- [x] Journal/venue information
- [x] PDF path if available

### What's Missing

## Medium Priority

### 4. Caching Layer
Add caching to reduce latency for repeated queries.

**TODO:**
- [ ] Add Redis or in-memory cache for embeddings
- [ ] Cache frequent query results
- [ ] Add cache invalidation on collection updates

### 5. Query Expansion
Improve recall by expanding queries.

**TODO:**
- [ ] Add synonym expansion for scientific terms
- [ ] Add query rewriting for better search
- [ ] Support for boolean operators (AND, OR, NOT)

### 6. Filtering and Facets
Enable filtering search results.

**TODO:**
- [ ] Add year range filter for papers
- [ ] Add venue/journal filter
- [ ] Add author filter
- [ ] Return facet counts in response

**Example:**
```python
class RetrieveRequest(BaseModel):
    query: str
    sources: list[str] = ["papers", "notion", "web"]
    top_k: int = 5
    filters: dict = {}  # {"year_min": 2020, "venue": "NeurIPS"}
```

### 7. Batch Retrieval
Support batch queries for efficiency.

**TODO:**
- [ ] Add `/retrieve/batch` endpoint for multiple queries
- [ ] Optimize embedding generation for batches

## Low Priority

### 8. Analytics and Logging
Track usage for optimization.

**TODO:**
- [ ] Log query latencies per source
- [ ] Track most common queries
- [ ] Add Prometheus metrics endpoint

### 9. Admin Endpoints
Endpoints for managing the service.

**TODO:**
- [ ] Add `/admin/reindex` to trigger re-indexing
- [ ] Add `/admin/stats` for detailed statistics
- [ ] Add `/admin/collections` to manage Qdrant collections

### 10. Authentication
Secure the API.

**TODO:**
- [ ] Add API key authentication
- [ ] Rate limiting per user/key
- [ ] Integrate with LiteLLM for unified auth

## Dependencies to Add

```txt
# Add to requirements.txt
redis          # For caching (optional)
prometheus-client  # For metrics (optional)
```

## Testing

### Missing Tests
- [ ] Unit tests for each search function
- [ ] Integration tests with mock Qdrant/Neo4j
- [ ] Load testing for concurrent queries
- [ ] Test error handling for service failures

## Configuration

### Environment Variables to Add
```bash
# Optional future config
REDIS_URL=redis://localhost:6379
ENABLE_CACHING=true
CACHE_TTL_SECONDS=3600
MAX_CONCURRENT_QUERIES=10
```

## Notes

- The retrieval service is designed to be called by Open WebUI's RAG pipeline or directly by LLM tool calls
- SPECTER is optimized for scientific text; BGE is better for general content
- Web search via SearXNG is real-time and doesn't require embedding
- Consider adding a "research agent" that chains multiple retrieval calls
