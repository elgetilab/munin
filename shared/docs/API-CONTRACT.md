# API Contract — munin-vps ↔ munin-backend

## Overview

This document defines the API surface between the VPS frontend (`munin-vps`) and the cluster backend (`munin-backend`). Both sides develop against this contract independently.

**Transport:** All requests go from Caddy on the VPS through the SSH tunnel to the retrieval service on the cluster (`127.0.0.1:18080 → localhost:8080`).

**Authentication:** Caddy performs forward-auth via `munin-auth` (email OTP service) before proxying. The backend receives `X-Munin-Email` and `X-Munin-Name` headers on every authenticated request. The backend trusts these headers (they come from Caddy on localhost, not from the internet).

**Content type:** JSON for request/response bodies. Streaming endpoints use SSE.

---

## 1. Health & Status

### `GET /api/status`

System health. The frontend polls this to show service availability.

**Response:**
```json
{
  "vllm": {
    "status": "running",
    "model": "qwen3.5-35b-a3b",
    "started_at": "2026-04-11T06:00:12Z",
    "uptime_seconds": 43200
  },
  "services": {
    "qdrant": "ok",
    "neo4j": "ok",
    "grobid": "ok",
    "searxng": "ok",
    "retrieval": "ok"
  },
  "gpu": [
    {"index": 0, "name": "RTX 5090", "memory_used_mb": 1200, "memory_total_mb": 32768, "utilization_pct": 5},
    {"index": 1, "name": "RTX 5090", "memory_used_mb": 28000, "memory_total_mb": 32768, "utilization_pct": 45}
  ],
  "slurm_queue": {
    "total_jobs": 2,
    "jobs": [
      {"job_id": "1234", "job_name": "vllm-service", "partition": "vllm-serving", "state": "RUNNING", "time": "12:00:05"}
    ]
  },
  "timestamp": "2026-04-11T18:00:12Z"
}
```

**Status values:** `vllm.status`: `"running"` | `"offline"` | `"starting"`. Services: `"ok"` | `"error"` | `"unavailable"`.

---

## 2. Personas

### `GET /api/personas`

Available chat personas with configuration.

**Response:**
```json
{
  "personas": [
    {
      "id": "chat",
      "name": "Meitner - Chat",
      "description": "General-purpose assistant for day-to-day tasks, writing, web search, and quick lookups.",
      "icon_url": "/api/personas/chat/icon",
      "tags": ["general", "writing"],
      "capabilities": {"web_search": true, "paper_search": true, "code_interpreter": false},
      "prompt_suggestions": [
        {"title": "Search the web", "subtitle": "for current information", "content": "Search the web for..."}
      ]
    }
  ],
  "default_persona": "chat"
}
```

### `GET /api/personas/{id}/icon`

Persona SVG icon as `image/svg+xml`.

---

## 3. Chat Completions

### `POST /api/chat/completions`

Primary chat endpoint. Backend injects persona system prompt, optionally performs RAG retrieval, proxies to vLLM with streaming.

**Request:**
```json
{
  "persona": "chat",
  "messages": [
    {"role": "user", "content": "What are the latest findings on lipid rafts?"}
  ],
  "rag": {
    "enabled": true,
    "sources": ["papers", "web"]
  },
  "stream": true
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `persona` | string | Yes | Persona ID (`chat`, `code`, `research`) |
| `messages` | array | Yes | OpenAI-format message array |
| `rag.enabled` | boolean | No | Enable RAG retrieval (default: `false`) |
| `rag.sources` | string[] | No | `papers`, `notion`, `web`, `graph` |
| `stream` | boolean | No | Enable SSE streaming (default: `true`) |

**Streaming response (SSE):**

```
event: metadata
data: {"persona":"chat","model":"qwen3.5-35b-a3b","rag_sources":["papers","web"]}

event: rag_context
data: {"sources_used":["papers","web"],"documents":[{"title":"Lipid Raft Review","source":"papers","score":0.89,"doi":"10.1038/..."}]}

event: thinking
data: {"content":"Let me analyze the recent literature on lipid rafts..."}

event: token
data: {"content":"Recent"}

event: tool_call
data: {"id":"tc_1","name":"paper_search","arguments":{"query":"lipid raft membrane 2025"}}

event: tool_result
data: {"id":"tc_1","name":"paper_search","result":{"papers":[{"title":"...","doi":"..."}]}}

event: token
data: {"content":"According to a recent study..."}

event: done
data: {"usage":{"prompt_tokens":1250,"completion_tokens":480},"finish_reason":"stop"}
```

**SSE event types:**

| Event | Description |
|-------|-------------|
| `metadata` | Model and persona info (first event) |
| `rag_context` | RAG retrieval results if enabled |
| `thinking` | Model chain-of-thought |
| `token` | Generated text token |
| `tool_call` | Model invoking an MCP tool |
| `tool_result` | Tool execution result |
| `error` | Error message |
| `done` | Generation complete with usage stats (last event) |

**Backend flow:**
1. Look up persona → get system prompt and parameters
2. If RAG enabled: retrieve with last user message, prepend context
3. Call vLLM `/v1/chat/completions` with assembled messages
4. Parse stream: separate thinking from content, detect tool calls
5. Execute tool calls via MCP, inject results, continue generation
6. Forward structured SSE events to frontend

---

## 4. RAG Retrieval (Direct)

### `POST /api/retrieve`

```json
{
  "query": "attention mechanism transformers",
  "sources": ["papers", "web"],
  "top_k": 5
}
```

**Response:**
```json
{
  "results": [
    {
      "source": "papers",
      "title": "Attention Is All You Need",
      "content": "We propose a new simple network architecture...",
      "score": 0.95,
      "metadata": {"doi": "10.48550/arXiv.1706.03762", "authors": ["Vaswani, A."], "year": 2017, "pdf_available": true}
    }
  ],
  "total_results": 5
}
```

### `POST /api/search/hybrid`

Hybrid vector + citation search. Same response format with additional `citation_score` in metadata.

---

## 5. Citation Graph

### `GET /api/citations/{doi}` — Papers citing a given paper
### `GET /api/references/{doi}` — Papers cited by a given paper
### `GET /api/author/{name}/papers` — Papers by author
### `GET /api/paper/{doi}/enriched` — Full metadata with citation counts

---

## 6. Deep Research

### `POST /api/deepresearch/submit`

```json
{"question": "Current challenges in cryo-EM for membrane proteins?", "email": "user@example.com"}
```

→ `{"job_id": "dr_abc123", "status": "queued", "estimated_minutes": 15}`

### `GET /api/deepresearch/status/{job_id}`

→ `{"job_id": "...", "status": "running", "progress": "Searching literature..."}`

Statuses: `queued` | `pending` | `running` | `completed` | `failed` | `timeout`

### `GET /api/deepresearch/result/{job_id}` — Markdown report
### `GET /api/deepresearch/result/{job_id}/pdf` — PDF download
### `GET /api/deepresearch/jobs` — User's jobs (filtered by `X-Munin-Email`)

---

## 7. Paper Management

### `GET /api/paper/{doi}/pdf` — Download PDF
### `GET /api/paper/{doi}/pdf/exists` — Check availability
### `GET /api/sources` — Available knowledge sources with stats

---

## 8. MCP Tools (Direct)

### `POST /api/mcp/call`

```json
{"tool": "web_search", "arguments": {"query": "latest cryo-EM advances"}}
```

→ `{"tool": "web_search", "success": true, "result": {...}, "duration_ms": 1200}`

### `GET /api/mcp/tools` — List tools with schemas

---

## 9. Models

### `GET /api/models` — Proxy to vLLM `/v1/models`

---

## Error Format

```json
{"error": {"code": "vllm_offline", "message": "LLM service is offline. Runs daily 6 AM – 2 AM."}}
```

| Code | HTTP | Description |
|------|------|-------------|
| `vllm_offline` | 503 | vLLM not running |
| `persona_not_found` | 404 | Unknown persona ID |
| `rag_source_unavailable` | 503 | RAG source down |
| `job_not_found` | 404 | Unknown research job |
| `rate_limited` | 429 | Too many requests |
| `internal_error` | 500 | Unexpected error |

---

## Auth Headers

The backend reads user identity from headers set by Caddy after forward-auth with munin-auth:

| Header | Description | Example |
|--------|-------------|---------|
| `X-Munin-Email` | Authenticated user's email | `user@example.org` |
| `X-Munin-Name` | Display name (may be derived from email) | `user` |

During migration, the backend should also accept `X-Authentik-Email` / `X-Authentik-Name` as fallbacks.

These headers are used for:
- Filtering deep research jobs by user
- Logging API usage
- Per-user rate limiting (if implemented)
