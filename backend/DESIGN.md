# Munin Backend — Design Document

## Repository: `munin-backend`

**Purpose:** Everything AI/backend that lives on the university cluster. LLM serving (vLLM), RAG retrieval, knowledge bases (Qdrant, Neo4j), MCP tooling, deep research, paper pipeline, chat persistence, document embedding, and agentic orchestration. Assumes HuginSLURM Phase 1 is complete.

**Current state:** Phase 2 services are already running on the cluster — Qdrant, Neo4j, GROBID, SearXNG, retrieval service, vLLM, deep research daemon. This repo extracts and upgrades those services, removing the Open WebUI dependency and adding new endpoints the custom frontend needs.

---

## Architecture

```
VPS (munin-vps)
    │ SSH tunnel (port 18080, autossh)
    ▼
Retrieval Service (FastAPI, :8080) ← CENTRAL API
    ├── Qdrant (:6333)     — vector search (papers + user_docs collections)
    ├── Neo4j (:7474/7687) — citation graph
    ├── SearXNG (:8888)    — web search
    ├── GROBID (:8070)     — PDF parsing
    ├── BGE-base           — user document embeddings
    ├── SPECTER2           — paper embeddings
    ├── SQLite             — chat persistence (conversations + messages)
    └── vLLM (:8000)       — LLM inference (GPU 1, SLURM job)
            ├── Chat completions (persona injection, RAG, streaming)
            ├── Summary generation (async, for chat compaction)
            └── Agent orchestration (tool chaining, sub-agents)

Deep Research Daemon (systemd) → SLURM jobs on GPU 0
```

## What Exists (Running Now)

These are already deployed from HuginSLURM Phase 2:

- Docker Compose: Qdrant, Neo4j, GROBID, SearXNG, Retrieval Service, Cloudflared (unused), Status page
- Retrieval service: `/retrieve`, `/search/hybrid`, `/citations/{doi}`, `/references/{doi}`, MCP endpoints (`/mcp/sse`, `/mcp/call`)
- MCP tools: `web_search`, `semantic_scholar_search`, `paper_search`, `paper_lookup`, `get_citations`, `get_references`, `get_author_papers`, `get_paper_pdf`, `web_fetch`, `llm_summarize`
- vLLM serving via SLURM job (6 AM – 2 AM schedule)
- Deep research daemon + SLURM jobs
- Persona definitions (Meitner, Turing, Curie) as JSON files
- autossh tunnel to VPS (port 18080)

## What Needs Adding/Changing

Based on the frontend reference (FRONTEND-REFERENCE.md), these endpoints and features are needed:

### New Endpoints (retrieval service)

| Endpoint | Method | Purpose | Status |
|----------|--------|---------|--------|
| `/api/personas` | GET | List personas with config, icons, suggestions | New |
| `/api/status` | GET | System health with `vllm.status`, `next_start`, `services.embedding` | New |
| `/api/chats` | GET | List user's conversations (paginated, searchable) | New |
| `/api/chats/{id}` | GET | Load full conversation with messages | New |
| `/api/chats/{id}` | PATCH | Rename conversation | New |
| `/api/chats/{id}` | DELETE | Delete conversation | New |
| `/api/chat/completions` | POST | Chat with persona injection, RAG, SSE streaming, tool execution | **Major upgrade** |
| `/api/documents/upload` | POST | Upload + embed user document (BGE-base) | New |
| `/api/documents` | GET | List user's documents | New |
| `/api/documents/{id}` | DELETE | Delete document + embeddings | New |

### Existing Endpoints to Keep

| Endpoint | Purpose |
|----------|---------|
| `/retrieve` | Direct RAG retrieval |
| `/search/hybrid` | Vector + citation hybrid search |
| `/citations/{doi}` | Citation lookup |
| `/references/{doi}` | Reference lookup |
| `/author/{name}/papers` | Author's papers |
| `/paper/{doi}/enriched` | Full paper metadata |
| `/paper/{doi}/pdf` | PDF download |
| `/mcp/sse`, `/mcp/call`, `/mcp/tools` | MCP protocol endpoints |
| `/deepresearch/submit`, `/status/{id}`, `/result/{id}` | Deep research |
| `/health` | Health check |

### Changes to Existing Code

1. **Remove Open WebUI** — delete container management from vLLM startup script, remove status page, remove `update-openwebui-models.sh`
2. **Remove Cloudflared** — from docker-compose (unused, tunnel is autossh)
3. **Auth headers** — read `X-Munin-Email` (the transitional `X-Authentik-Email` fallback was removed in 2026-04/05; see `backend/CLAUDE.md`)
4. **Docker compose** — add chat SQLite volume, add user_docs Qdrant collection setup

---

## Repository Structure

```
munin-backend/
├── CLAUDE.md                        ← Claude Code context
├── DESIGN.md                        ← This document
├── deploy.sh                        ← Deployment script
├── config/
│   ├── munin.env.template           ← Secrets template
│   ├── agents.yml                   ← Agent registry definitions
│   ├── deepresearch-daemon.service
│   └── munin-tunnel.service
├── docker/
│   ├── docker-compose.yml           ← Qdrant, Neo4j, GROBID, SearXNG, Retrieval
│   └── searxng/settings.yml
├── retrieval/                       ← THE MAIN API SERVICE
│   ├── main.py                      ← FastAPI app — all endpoints
│   ├── database.py                  ← DB connections (Qdrant, Neo4j, SQLite)
│   ├── models.py                    ← Pydantic request/response models
│   ├── chat_store.py                ← Chat persistence (SQLite CRUD)
│   ├── chat_context.py              ← Context assembly + compaction
│   ├── document_store.py            ← User document embedding + retrieval
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── mcp/                         ← MCP server implementation
│   │   ├── schemas.py
│   │   ├── executor.py
│   │   ├── endpoints.py
│   │   └── tools/
│   │       ├── web.py
│   │       ├── papers.py
│   │       ├── llm.py
│   │       └── agents.py            ← invoke_agent tool
│   ├── agents/                      ← Agentic orchestration
│   │   ├── registry.py              ← Load agent configs from YAML
│   │   ├── executor.py              ← Agent execution loop
│   │   └── parallel.py              ← asyncio.gather for concurrent tools
│   └── static/                      ← Search/Research UIs (kept temporarily)
├── scripts/
│   ├── vllm/
│   │   ├── start-vllm-service.sh    ← SLURM job (Open WebUI removed)
│   │   └── schedule-vllm.sh
│   ├── deepresearch/
│   │   ├── deepresearch-daemon.py
│   │   └── deepresearch-job.sh
│   ├── pipeline/
│   │   └── paper_pipeline.py
│   └── tunnel/
│       └── setup-tunnel.sh
├── personas/
│   ├── chat.json, code.json, research.json
│   └── logos/
├── models/MODEL_REFERENCE.md
└── docs/
    ├── SERVICES.md
    ├── ARCHITECTURE.md
    └── FRONTEND-REFERENCE.md         ← Copy from VPS repo for reference
```

---

## Chat Persistence

SQLite at `/opt/munin/data/chats.db`, mounted as Docker volume.

### Schema

```sql
CREATE TABLE conversations (
    id TEXT PRIMARY KEY,
    user_email TEXT NOT NULL,
    title TEXT,
    persona TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    summary TEXT,
    summary_through_index INTEGER
);

CREATE TABLE messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    index_in_conversation INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    thinking TEXT,
    tool_calls TEXT,          -- JSON array
    rag_context TEXT,         -- JSON object
    created_at TEXT NOT NULL,
    token_count INTEGER,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id)
);

CREATE INDEX idx_messages_conversation ON messages(conversation_id, index_in_conversation);
CREATE INDEX idx_conversations_user ON conversations(user_email, updated_at DESC);
```

### Context Assembly (Hybrid Summary + Recent Messages)

Before every chat request, check if context fits:

1. Calculate token budget: `MAX_CONTEXT - GENERATION_RESERVE - system_prompt - rag_context - new_message`
2. If `summary + recent_messages` fits → use as-is
3. If not → keep recent messages that fit in half the budget, summarize the rest via vLLM
4. Summary generation is synchronous when compaction is needed, async periodic otherwise

Fallback when vLLM is offline: truncate without summarizing.

### Conversation Title Generation

First user message → either truncate to 60 chars, or async LLM call to generate 5-8 word title after first response completes.

---

## User Document Store

Qdrant collection `user_docs`, 768 dimensions (BGE-base), cosine distance.

### Upload Flow

1. Frontend sends multipart POST to `/api/documents/upload`
2. Backend stores original file at `/opt/munin/data/user_docs/{email_hash}/{doc_id}/`
3. Extract text (GROBID for PDF, pdftotext, python-docx, or direct read)
4. Chunk: 512 tokens, 50 token overlap, paragraph-boundary splitting
5. Embed with BGE-base
6. Store in Qdrant with payload: `{user_email, conversation_id, filename, chunk_index, chunk_text, total_chunks, upload_time}`

### Supported File Types

PDF, TXT, MD, DOCX, PNG, JPG, JPEG, WEBP (images stored but not chunked/embedded — available for future multimodal support).

### MCP Tool

`search_user_docs` — auto-filters by `X-Munin-Email`.

---

## SSE Streaming Protocol

The `/api/chat/completions` endpoint streams these events:

```
event: conversation     — {"id": "...", "title": "...", "is_new": true}
event: rag_context      — {"sources_used": [...], "documents": [...]}
event: thinking         — {"content": "partial thinking text..."}  (multiple, accumulated)
event: tool_call        — {"id": "tc-1", "name": "paper_search", "arguments": {...}}
event: tool_result      — {"id": "tc-1", "name": "paper_search", "result": {...}, "duration_ms": 800}
event: agent_start      — {"agent": "research_orchestrator", "query": "..."}
event: agent_thinking   — {"content": "Planning approach..."}
event: agent_tool_call  — {"id": "tc-2", "name": "...", "arguments": {...}}
event: agent_tool_result — {"id": "tc-2", "result": {...}, "duration_ms": ...}
event: agent_done       — {"agent": "...", "tool_calls": 8, "duration_seconds": 34}
event: token            — {"content": "partial response text"}  (many)
event: done             — {"usage": {"prompt_tokens": N, "completion_tokens": N}, "finish_reason": "stop"}
event: error            — {"message": "Human-readable error"}
```

The frontend accumulates `thinking` events into a string, matches `tool_result` to `tool_call` by `id`, and accumulates `token` events into the response.

---

## Status Endpoint

`GET /api/status` returns:

```json
{
  "vllm": {
    "status": "running|offline|starting",
    "model": "qwen3.5-35b-a3b",
    "next_start": "2026-04-14T06:00:00+02:00"
  },
  "services": {
    "retrieval": "ok",
    "embedding": "ok",
    "qdrant": "ok",
    "neo4j": "ok",
    "grobid": "ok",
    "searxng": "ok"
  },
  "gpu": [...],
  "slurm_queue": {...},
  "timestamp": "..."
}
```

`next_start` is only included when `status === "offline"`. Calculated from the cron schedule (next 6 AM).

`services.embedding` reports whether the BGE-base and SPECTER2 models are loaded and functional.

---

## Agentic Orchestration

Agent registry in `config/agents.yml`. Three initial agents: research_orchestrator, code_checker, writing_agent.

Invoked via `invoke_agent` MCP tool (model-triggered or user-triggered via `/research`, `/write`, `/analyze` slash commands).

Execution: loop of vLLM calls + tool execution with constrained tool allowlists, iteration limits, and timeouts. Parallel tool execution via `asyncio.gather`.

See AGENTIC-ORCHESTRATION.md for full spec.

---

## Auth Headers

```python
email = request.headers.get("X-Munin-Email")
```

The transitional `X-Authentik-Email` fallback was removed across the
service in 2026-04 (gateway/retrieval) and 2026-05 (upload hook).

---

## What to Discard from Current Setup

1. Open WebUI container + persona sync script
2. Status page (nginx) — frontend handles this
3. Cloudflared container — autossh tunnel stays
4. Legacy modelfiles (Chatgeti/Codegeti/Writegeti format)

---

## Connection to Other Repositories

```
HuginSLURM (Phase 1) — already complete
  └── SLURM, CUDA, users, storage, partitions

munin-backend (this repo)
  └── vLLM, retrieval API, knowledge bases, chat persistence,
      document embedding, agents, deep research
  └── Exposes port 18080 via autossh tunnel

munin-vps (frontend + gateway)
  └── Caddy, munin-auth, API gateway, chat frontend, static pages
  └── Calls this repo's API via tunnel
```
