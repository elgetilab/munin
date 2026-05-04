# Munin Backend — Design Document

> **Status (2026-05-04):** This is the original pre-merge design
> document for the cluster side of Munin. Most of the planned work
> here has shipped. Sections describing future endpoints, future
> code changes, or "what to discard" describe **already-completed**
> work — kept here as a record of design intent. For the live API
> contract, see `../shared/docs/BACKEND-API.md`. For the live
> repo layout and deploy commands, see `CLAUDE.md`.

## Scope

Cluster-side of the Munin monorepo (`backend/`). Everything AI on
the university cluster: LLM serving (vLLM), RAG retrieval,
knowledge bases (Qdrant, Neo4j), MCP tooling, deep research,
paper pipeline, chat persistence, document embedding, and agentic
orchestration. Assumes HuginSLURM Phase 1 is complete.

---

## Architecture

```
VPS (frontend/, on Hetzner)
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

- Docker Compose: Qdrant, Neo4j, GROBID, SearXNG, retrieval service, sandbox sidecar (Cloudflared and the legacy status page were since removed)
- Retrieval service: `/retrieve`, `/search/hybrid`, `/citations/{doi}`, `/references/{doi}`, MCP endpoints (`/mcp/sse`, `/mcp/call`)
- MCP tools: `web_search`, `semantic_scholar_search`, `paper_search`, `paper_lookup`, `get_citations`, `get_references`, `get_author_papers`, `get_paper_pdf`, `web_fetch`, `llm_summarize`
- vLLM serving via SLURM job (6 AM – 2 AM schedule)
- Deep research daemon + SLURM jobs
- Persona definitions (Meitner, Turing, Curie) as JSON files
- autossh tunnel to VPS (port 18080)

## API surface (historical)

This section originally enumerated the new `/api/*` endpoints
that needed to be added to support the custom frontend. They are
all live now.

For the canonical, up-to-date endpoint catalogue (request/response
shapes, query params, error codes, SSE event types), see
`../shared/docs/BACKEND-API.md`. The legacy un-prefixed routes
(`/retrieve`, `/search/hybrid`, `/citations/{doi}`,
`/references/{doi}`, `/paper/{doi}/...`, `/mcp/*`,
`/deepresearch/*`) remain in `retrieval/main.py` but are explicitly
out-of-contract for the frontend.

### Changes to existing code (completed)

1. **Removed Open WebUI** — container management deleted from the
   vLLM startup script; the legacy status page and
   `update-openwebui-models.sh` were excised by `deploy.sh
   cleanup` (which itself has since been retired post-cluster-
   verification — see `CLEANUP.md` Tier 3).
2. **Removed Cloudflared** — out of docker-compose; tunnel is
   autossh (`config/munin-tunnel.service`).
3. **Auth headers** — read `X-Munin-Email`. The transitional
   `X-Authentik-Email` fallback was removed in 2026-04
   (gateway/retrieval) and 2026-05 (upload hook). See
   `CLAUDE.md` Auth Headers.
4. **Docker compose** — chat SQLite volume + user_docs Qdrant
   collection setup live in `docker/docker-compose.yml` and the
   `startup` hook of `retrieval/main.py`.

---

## Repository Structure

For the live, accurate layout see `CLAUDE.md`. The original
intended structure (preserved here as design history) was:

```
backend/                              ← cluster half of the monorepo
├── CLAUDE.md, DESIGN.md, deploy.sh
├── config/                           ← munin.env.template, agents.yml,
│                                     ←   *.service / *.timer files
├── docker/                           ← docker-compose.yml + service configs
├── retrieval/                        ← THE MAIN API SERVICE
│   ├── main.py                       ← FastAPI app — all routes
│   ├── database.py, models.py, chat_store.py, chat_context.py,
│   │   document_store.py, project_store.py, artifact_store.py
│   ├── Dockerfile, requirements.txt
│   ├── mcp/                          ← MCP server (schemas, executor,
│   │                                 ←   endpoints, tools/*.py)
│   ├── agents/                       ← Agentic orchestration (registry,
│   │                                 ←   executor, parallel)
│   └── tests/
├── sandbox/                          ← Jupyter-kernel sandbox sidecar
├── scripts/
│   ├── vllm/                         ← SLURM job + scheduler
│   ├── deepresearch/                 ← daemon + SLURM job
│   ├── knowledge/                    ← build_embedding_map.py
│   └── pipeline/                     ← paper_pipeline.py + friends
└── docs/                             ← internal design notes;
                                      ←   docs/archive/ for shipped specs
```

Persona definitions are now in `../shared/personas/` (cross-cut;
deployed via `deploy.sh personas`). The static UI directory once
listed here was deleted in `CLEANUP.md` Tier 6 — Caddy on the VPS
serves the user-facing UIs from `../frontend/static/`.

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

## What was discarded (completed)

All four of the legacy artifacts the design originally called out
for removal have been excised:

1. Open WebUI container + persona sync script — gone.
2. Status page (nginx) — gone; the VPS serves status pages.
3. Cloudflared container — gone; the autossh tunnel handles it.
4. Legacy modelfiles (Chatgeti/Codegeti/Writegeti format) — gone.

---

## Related

```
HuginSLURM (separate repo, complete)
  └── SLURM, CUDA, users, storage, partitions

munin (this monorepo)
  ├── backend/  ← this directory
  │     vLLM, retrieval API, knowledge bases, chat persistence,
  │     document embedding, agents, deep research, paper pipeline
  │     → exposes port 18080 via autossh tunnel
  ├── frontend/ ← VPS half (Caddy, munin-auth, API gateway,
  │              chat frontend, static pages — calls backend API
  │              via the tunnel)
  └── shared/   ← personas, contributors.yml, contract docs
```
