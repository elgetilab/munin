# Munin Backend: API Reference

Hand-off document for the frontend / gateway repo. Describes what the
`munin-retrieval` FastAPI service actually delivers today (Streams 1–5 are
all live as of the first deploy on `hugin`). This is the counterpart to
`FRONTEND-REFERENCE.md`, the frontend expresses the *intent*, this file
describes the *implementation*. Where they disagree, this file wins.

## 1. Overview

| | |
|---|---|
| Service | `munin-retrieval` (FastAPI, uvicorn) |
| Where it runs | Docker container on cluster head `hugin`, bound to `127.0.0.1:8080` |
| How the VPS reaches it | Reverse SSH tunnel: VPS `127.0.0.1:18080` ↔ cluster `127.0.0.1:8080` (`config/munin-tunnel.service`) |
| Framing | JSON everywhere except `/api/chat/completions`, which uses SSE (`sse-starlette`) |
| Python side | 3.11; async throughout; lazy-loaded Qdrant/Neo4j/SPECTER/BGE singletons |
| Persistence | SQLite at `/data/chats.db` (chat history), Qdrant `user_docs` collection (user files), Qdrant `papers` (papers) |

The service also exposes the legacy `/retrieve`, `/search/hybrid`,
`/citations/{doi}`, `/mcp/*`, `/deepresearch/*`, etc. routes, those are
**not** part of the frontend contract. Ignore them for the UI.
(`POST /deepresearch/submit` is additionally disabled since 2026-07:
returns 503 unless the service runs with `DEEPRESEARCH_ENABLED=1`;
the read-only `/deepresearch/*` routes still serve past reports.
See DECISIONS.md "Deep Research (MiroThinker) disabled".)

Two more routes exist but are out-of-band for the UI:

- `GET /health`, ops/uptime check used by the docker healthcheck.
  Returns `{"status": "ok"}`. Not user-facing; safe to ignore.
- `POST /api/admin/ingest`, contributor-ingest endpoint called by the
  VPS hook service via the SSH tunnel. Token-auth, not session-auth;
  see `shared/docs/CONTRIBUTOR-INGEST.md` for the full contract.

## 2. Authentication

Forward-auth pattern: Caddy on the VPS validates the session (or API
key) and attaches headers to the proxied request.

| Header | Purpose | Required |
|---|---|---|
| `X-Munin-Email` | user identity | Yes |
| `X-Munin-Name` | display name | No, not consumed by backend today |
| `X-Munin-Ephemeral` | when `true`, forces ephemeral chat (no persistence); VPS gateway stamps this on every `/v1/*` API-key request | No |

Every `/api/*` route except `/api/status` and `/api/personas/{id}/icon`
requires `X-Munin-Email`; missing → **401** with
`{"error": {"message": "Missing authentication header"}}`.

The old `X-Authentik-Email` transitional fallback was removed on
2026-04-21, the gateway sets `X-Munin-Email` exclusively now.

All conversations and documents are scoped by the lowercased email. There
is **no cross-user visibility** at any layer (DB query and Qdrant filter
both clamp to `user_email`).

## 3. Error format

Uniform across every endpoint:

```json
{"error": {"message": "Human-readable description"}}
```

FastAPI `HTTPException` is serialised into this exact shape. HTTP status
codes follow the usual convention (400 / 401 / 404 / 413 / 500). Streaming
errors come through the SSE stream as `event: error` instead of a HTTP
status flip.

## 4. Endpoint catalogue

### 4.1 `GET /api/status`

Polled by the frontend's `useStatus` hook every 60 s.

**Request**: no body, no auth needed.

**Response (200)**:

```json
{
  "maintenance": {
    "active": false
  },
  "vllm": {
    "status": "running",
    "model": "qwen3.6-35b-a3b",
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
  "timestamp": "2026-04-13T16:30:00Z"
}
```

When maintenance mode is on, the `maintenance` block instead reads
`{"active": true, "message": "...", "since": "<ISO8601>"}` — `message`
is operator-set (may be empty), `since` is when it was switched on.

Notes:

- `maintenance.active` reflects the cluster-side flag set by the
  `munin-maintenance` toggle. When `true` the frontend renders the
  maintenance page **with precedence over** the sleeping page,
  regardless of `vllm.status`.
- `vllm.status` is one of `running` / `offline` / `starting`. When `offline`
  the frontend should render `SleepingPage`.
- `vllm.next_start` is **only present** when `status === "offline"`. It's
  computed as the next 6 AM local time from the cluster's clock.
- `services.embedding` reports whether SPECTER + BGE have already been
  loaded. On a fresh boot it will be `"unavailable"` until the first call
  that needs them (first RAG call or first document upload). This is not a
  bug, it means "not loaded yet", not "broken".
- `services.grobid` may read `unavailable` in the container-DNS form; it's
  probed at `http://grobid:8070/api/isalive`.

### 4.2 `GET /api/personas`

**Response (200)**:

```json
{
  "personas": [
    {
      "id": "chat",
      "name": "Meitner - Chat",
      "description": "General-purpose assistant ...",
      "icon_url": "/api/personas/chat/icon",
      "tags": ["general", "writing"],
      "capabilities": {"web_search": true, "code_interpreter": false, "image_generation": false},
      "prompt_suggestions": [
        {"title": "Search the web", "subtitle": "for current information", "content": "Search the web for ..."}
      ]
    },
    {"id": "code",     "name": "Turing - Code",     "...": "..."},
    {"id": "research", "name": "Curie - Research",  "...": "..."}
  ],
  "default_persona": "chat"
}
```

Three personas are currently loaded: `chat`, `code`, `research` (from
`personas/*.json`). Icon URLs are served by the next endpoint.

### 4.3 `GET /api/personas/{id}/icon`

Returns `image/svg+xml`. Files come from `personas/logos/`, resolved in
this order: `{name}-{id}-inverted.svg` → `{name}-{id}.svg` → `logo-{id}.svg`.
Missing persona or missing file → **404**.

### 4.4 `GET /api/chats`

Lists the authenticated user's conversations.

**Query params** (all optional):

| Name | Type | Default | Notes |
|---|---|---|---|
| `limit` | int | `20` | clamped `[1, 200]` |
| `offset` | int | `0` | ≥0 |
| `persona` | string |, | filter to one persona id |
| `search` | string |, | SQLite FTS5 over all `messages.content` |
| `pinned_only` | bool | `false` | when `true`, return only pinned conversations |

**Response (200)**:

```json
{
  "conversations": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440000",
      "user_email": "alice@example.com",
      "title": "Polymer simulation methods",
      "persona": "chat",
      "created_at": "2026-04-13T10:00:00Z",
      "updated_at": "2026-04-13T10:05:00Z",
      "summary": null,
      "summary_through_index": null,
      "pinned": true,
      "pinned_at": "2026-04-14T09:00:00Z",
      "message_count": 4,
      "preview": "Can you explain the differences between...",
      "generating": false
    }
  ],
  "total": 42
}
```

Notes:

- Ordered by `pinned DESC, updated_at DESC` - pinned conversations float
  to the top of the listing regardless of their last activity.
- `generating` (background turns): `true` when the conversation has a
  live in-flight stream in the server's registry. Derived per request
  from in-process state, no DB column. The webui renders a pulsing
  dot on the sidebar row; the value is only as fresh as the listing
  fetch.
- `pinned_at` is `null` for unpinned rows.
- `preview` is the **first user message** of the conversation, truncated to
  117 chars + ellipsis.
- `search` uses FTS5 with the porter/unicode61 tokenizer and can be any
  FTS5 MATCH expression (a plain word is fine).

### 4.5 `GET /api/chats/{id}`

Loads a full conversation with messages in order.

**Response (200)**:

```json
{
  "id": "...",
  "user_email": "alice@example.com",
  "title": "Polymer crystallization",
  "persona": "chat",
  "created_at": "...",
  "updated_at": "...",
  "summary": null,
  "summary_through_index": null,
  "messages": [
    {
      "id": "msg-uuid",
      "role": "user",
      "content": "What are...",
      "thinking": null,
      "tool_calls": null,
      "rag_context": null,
      "created_at": "...",
      "token_count": null,
      "index_in_conversation": 0
    },
    {
      "id": "msg-uuid",
      "role": "assistant",
      "content": "Molecular dynamics...",
      "thinking": "The user is asking...",
      "tool_calls": [
        {"id": "tc-1", "name": "paper_search", "arguments": {...}, "result": {...}, "duration_ms": 1200}
      ],
      "rag_context": {"sources_used": ["papers"], "documents": [{"title": "...", "source": "papers", "score": 0.9, "doi": "...", "content": "..."}]},
      "created_at": "...",
      "token_count": null,
      "index_in_conversation": 1
    }
  ]
}
```

- **404** if the conversation doesn't exist or belongs to another user.
- `tool_calls` and `rag_context` are parsed from the stored JSON columns
  back into Python objects, frontend receives them as structured JSON,
  not strings.
- `thinking` is the accumulated `reasoning_content` from the vLLM stream.
- Ownership is enforced by `WHERE user_email = ?`; we don't leak 403 vs 404.
- The response also carries `active_stream` (background turns): the
  registry's newest stream for this conversation, or `null`:

  ```json
  "active_stream": {"stream_id": "abc123...", "done": false, "last_seq": 41}
  ```

  `done: false` means a turn is still generating — the client should
  re-attach via §4.8a with **no** `Last-Event-ID` so the full log
  replays (the transcript above only contains persisted turns).
  `done: true` means the turn completed within the ~60 s retention
  window and its answer is already in `messages`. A `null` says
  nothing about the past: completed streams are evicted ~60 s after
  finishing. Like `generating`, this is in-process registry state, so
  it does not survive a retrieval-service restart.

### 4.6 `PATCH /api/chats/{id}`

**Body**: `{"title": "New title"}` (non-empty string, trimmed).

**Response (200)**: the updated conversation row (same shape as the row in
`/api/chats`, minus `message_count`/`preview`).

**400** if body is not JSON or title is missing/empty. **404** if the
conversation doesn't exist or is owned by another user.

### 4.7 `DELETE /api/chats/{id}`

**Response (200)**: `{"deleted": true}`.

Deletes both the conversation row and all of its messages (explicit
cascade, not FK-based, so FTS triggers fire reliably). **404** if missing.

### 4.7a `POST /api/chats/{id}/pin`

Pins a conversation so it floats to the top of the user's listing
across devices. Idempotent.

**Response (200)**:

```json
{"pinned": true, "pinned_at": "2026-04-14T09:00:00Z"}
```

**404** if the conversation does not exist or belongs to another user.

### 4.7b `DELETE /api/chats/{id}/pin`

Unpins a conversation. Idempotent.

**Response (200)**: `{"pinned": false}`.

**404** under the same conditions as `POST .../pin`.

### 4.8 `POST /api/chat/completions`: SSE streaming

**The only SSE endpoint.** Everything else is plain JSON.

**Request body**:

```json
{
  "persona": "chat",
  "conversation_id": null,
  "messages": [{"role": "user", "content": "What is polymer crystallization?"}],
  "rag": {"enabled": true, "sources": ["papers", "web"]},
  "ephemeral": false,
  "stream": true
}
```

- `persona` defaults to `chat` if omitted.
- `conversation_id: null` → backend creates a new conversation.
- `messages` must be a non-empty list; the **last** element must be
  `role: "user"`. Prior messages are ignored in the **persistent** mode -
  the backend loads persisted history for `conversation_id` and uses that
  as context. Only the trailing new user turn is read from the request.
- `rag.enabled: true` triggers parallel `paper_search` + `web_search` via
  the MCP executor. Supported `rag.sources`: `papers`, `web`. If unset,
  defaults to `["papers"]`. Omit `rag` entirely to disable retrieval.
- `project_id` (optional, §21) - when creating a **new** conversation
  (i.e. `conversation_id` is null), pass a project id to auto-file
  the new conversation into that project at creation time and have
  its persona resolved against `project.default_persona` before
  `profile.default_persona`. Ignored for existing conversations
  (their project membership is already set via the
  `/api/projects/{id}/conversations/{cid}` routes). Refused with 400
  when combined with `ephemeral: true`.
- `ephemeral` (default `false`) - when `true`, **nothing** is written to
  `chats.db`: no conversation row, no message rows, no auto-title, no
  summary persistence. The `conversation` SSE event still fires but with
  a synthetic id of the form `ephemeral-<12 hex chars>` and an extra
  `ephemeral: true` flag in the payload. Because the server stores
  nothing, the **frontend must echo the full prior conversation** in the
  `messages` array on every follow-up turn (same as a stateless
  OpenAI-compatible chat completions call). `conversation_id` is ignored
  when `ephemeral: true`. Tool calls (web search, paper search, etc.)
  still execute normally - "ephemeral" means not stored by Munin, not
  untrackable by the world.
- **Multimodal content on the last user message** (§5): the trailing
  user message's `content` field may be an OpenAI-style content list
  instead of a plain string. Older clients sending a string keep
  working unchanged. The list shape looks like:

  ```json
  {
    "role": "user",
    "content": [
      {"type": "text", "text": "Describe this figure."},
      {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBOR..."}}
    ]
  }
  ```

  Supported `image_url.url` schemes:

  - `data:image/<png|jpeg|webp>;base64,<payload>` - inline bytes.
    On **persistent** chats, inline images are funnelled to the
    documents store so the user can re-reference them later (the
    `document_id` ends up in the message's `attachments` column).
    On **ephemeral** chats, inline images reach the model on this
    turn and then vanish, matching the "nothing stored" contract.
  - `document:<doc_id>` - reference to a previously-uploaded
    document. The backend resolves the file path (ownership-checked
    via `user_docs/<email_hash>/<doc_id>/`), reads the bytes, and
    injects them as a data URL on the model's turn.

  **Caps**: 5 MB per image, 3 images per turn. Exceeding either
  returns HTTP 400 with an error message identifying the offending
  block.

  **One-shot across turns with on-demand re-view**: images are
  included only on the turn they were sent. On subsequent turns the
  stored transcript has the text portion only; the raw image bytes
  are not automatically re-injected. When the user follows up with a
  question about an earlier image, the model can call the
  `view_attachment(document_id)` MCP tool to pull the image bytes
  back into its context for the next turn. The model discovers
  available document_ids from inline `[Attachments on this message:
  <doc_id> (<filename>, <mime>)]` markers that `chat_context` splices
  into past-turn content - the stored `messages.content` column is
  unchanged, so FTS search still sees the clean text.
- `stream` is implicit; the response is always SSE.

**Response**: `Content-Type: text/event-stream`, frames are
`event: <name>\ndata: <json>\n\n`. See §5 for the full event catalogue.

**On success** the stream contains one `conversation` event at the start,
optional `rag_context`, the generation events, and a terminal `done`. On
**brand-new conversations** a second `conversation` event is emitted near
the end once the auto-generated title is ready (re-use the same `id`, just
update the title in the UI).

**Errors**: surfaced as `event: error` frames, not HTTP error codes (the
status is already `200 OK` once the stream is open). The stream is
terminated after any `error` event.

**Raw-mode passthrough (OpenAI-compatible).** When the request carries no
`persona`, no `conversation_id`, and no `project_id` (or `persona` is the
literal `"raw"`/`"none"`), the endpoint skips the persona/RAG/tool/
chat-store machinery and proxies straight to vLLM. This is the shape of
every external `api.muninai.org/v1/*` call (Cursor, aider, Positron, the
OpenAI SDK). The response is vLLM's own OpenAI payload: SSE
`chat.completion.chunk`s terminated by `data: [DONE]` when `stream: true`,
or a single `chat.completion` JSON object otherwise.

Error semantics here differ from persona mode: raw mode returns a **real
HTTP status** (e.g. `400`) with vLLM's error body for failures detected
before the stream opens, instead of a `200` carrying an `event: error`
frame. OpenAI clients expect HTTP-level errors, so the old 200-wrapped
form surfaced to them as an opaque "error making request".

Context-length handling: the served model's window is **65536 tokens**
(prompt + `max_tokens` combined). On an overflow, the proxy refits
`max_tokens` down to the room the prompt leaves and retries once, so a
growing session keeps working rather than hard-failing once it crosses
the line. If the prompt **alone** exceeds the window, the `400` ("This
model's maximum context length is 65536 tokens...") passes through
unchanged so the client can trim and retry.

**`id:` framing and reconnect (P1 #10).** Every SSE event carries an
`id: <stream_id>-<seq>` line where `seq` is a monotonic per-stream
integer starting at 1. The first `conversation` event's payload also
carries `stream_id` so the client can persist it (the webui uses
localStorage — moved from sessionStorage for background turns, so the
pointer survives a closed tab) and resume across a refresh or reopen.
SSE comments (`: keepalive`) fire every ~15 s of silence so reverse
proxies don't drop idle connections.

### 4.8a `GET /api/chat/completions/resume?stream_id=<id>`

Resume an in-flight (or just-completed) stream after a disconnect or
browser refresh. The client carries `Last-Event-ID: <stream_id>-<seq>`
indicating the last event it applied; the server replays everything
with `seq > last_seq` from its in-memory log, then continues live
until `done`. Same authorization as the POST (`X-Munin-Email` must
match the stream's owner).

**Background turns.** A disconnect no longer cancels the turn. After
the last listener has been gone for the **60 s grace window**, the
stream is promoted to *background*: it keeps generating, and the
finished answer persists through the normal completion path, so a
closed tab still gets its answer on the next conversation load.
Listeners are refcounted (two tabs can watch one stream); the grace
timer only engages when the count hits zero. Bounds:

- at most **2 concurrent background streams per user**
  (`MAX_BACKGROUND_PER_USER`) — at the cap, grace expiry cancels the
  newest candidate exactly as it did pre-background-turns;
- a listenerless stream is hard-cancelled **30 min after it started**
  (`BACKGROUND_MAX_S`), a runaway guard on top of the tool-turn
  budget.

Cancellation (Stop button, cap fallback, runaway guard) still runs
the P0 #2 cascade and the save-always finally persists partial state.
Explicit cancellation is §4.8b — closing the connection alone stops
nothing.

**Responses**:

- `200` with an SSE body — same wire format as the POST. Replayed
  events arrive in `seq` order, then live events follow.
- `410 Gone` — the stream is unknown, evicted (kept ~60 s after
  completion), or its log overflowed (>1000 events) past the
  client's checkpoint. Because the turn's outcome is persisted
  regardless, the webui treats this as "reload the transcript"
  (synthetic `stream_gone` event, §5), not as an error.
- `403 Forbidden` — `X-Munin-Email` does not match the stream's
  owner. Stream ids are UUIDs so this shouldn't happen organically;
  it's a belt-and-braces check.

Note for proxies: this GET has no body, so gateways that detect
streaming by a `stream: true` body flag must also honour
`Accept: text/event-stream` (the VPS gateway does since background
turns Phase B) — otherwise the resumed stream gets buffered to
completion.

### 4.8b `POST /api/chat/completions/{stream_id}/cancel`

Explicitly cancel an in-flight chat completion (background turns).
Used by the webui Stop button. Idempotent.

A cancelled turn persists its partial content with an italic marker
naming the cause: `_(stream interrupted: stopped by user)_` for this
endpoint, or a background cap / time-limit phrase when the server
cancelled. The "client disconnected before completion" wording is
reserved for genuine disconnects (aclose without a cancel signal,
e.g. service shutdown).

**Responses**:

- `204 No Content` — cancellation signalled, or the stream had
  already finished (no-op).
- `410 Gone` — unknown or evicted stream id.
- `403 Forbidden` — `X-Munin-Email` does not match the stream's
  owner.

### 4.9 `POST /api/documents/upload`

Multipart form upload.

**Form fields**:

- `file` (required), PDF / TXT / MD / DOCX / PNG / JPG / JPEG / WEBP
- `conversation_id` (optional), associates the upload with a conversation
  for the `payload.conversation_id` filter

**Server-side limit**: 50 MB, same as the frontend. **413** if exceeded.

**Response (200)**:

```json
{
  "document_id": "doc_abc123",
  "filename": "my_draft.pdf",
  "chunks": 24,
  "status": "embedded",
  "upload_time": "2026-04-13T14:30:00Z"
}
```

- `status` is one of:
  - `"embedded"`, text was extracted, chunked, embedded, and upserted into
    Qdrant. Ready for RAG.
  - `"stored"`, file is on disk but not embedded. This is the state for
    images, zero-chunk documents, or uploads that occurred before the BGE
    model / Qdrant was reachable.
- `chunks` reflects the number of Qdrant points written (0 for `stored`).

Extraction strategy:

| Extension | Extractor | Notes |
|---|---|---|
| `.pdf` | GROBID `processFulltextDocument` → pypdf fallback | GROBID is good for papers, pypdf is the safety net |
| `.txt`, `.md` | direct read (`utf-8` → `latin-1` fallback) | |
| `.docx` | `python-docx` paragraph walk | |
| `.png`, `.jpg`, `.jpeg`, `.webp` | none | file saved, `status: "stored"` |

Chunking: ~512 tokens (2048 chars) per chunk, ~50-token overlap, respects
paragraph boundaries, splits oversized paragraphs on sentence then on
character. Embeddings via BGE-base (768-dim, cosine).

File storage: `/data/user_docs/{sha256(email)[:16]}/{document_id}/{filename}`
inside the container (`/opt/munin/data/user_docs/...` on the host).

### 4.10 `GET /api/documents`

**Query params**:

- `conversation_id` (optional), filter to docs uploaded in that chat

**Response (200)**:

```json
{
  "documents": [
    {
      "document_id": "doc_abc123",
      "filename": "my_draft.pdf",
      "chunks": 24,
      "status": "embedded",
      "upload_time": "2026-04-13T14:30:00Z"
    }
  ]
}
```

Sources of truth:

1. Qdrant scroll, authoritative for embedded documents (one row per
   `document_id`, with `total_chunks` and `upload_time` from the payload).
2. Disk scan, adds any file the user has under their user dir that isn't
   in Qdrant (images, zero-chunk docs). Only included when
   `conversation_id` is not specified.

### 4.11 `DELETE /api/documents/{id}`

Removes both the on-disk directory and every Qdrant point whose payload
matches `{user_email, document_id}`.

**Response (200)**: `{"deleted": true}`. **404** only if neither a
directory nor any Qdrant points existed.

### 4.11a `GET /api/memories` (P2 #25)

Returns the authenticated user's accepted memories AND any pending
auto-proposed candidates from the post-turn extraction hook.

**Response (200)**:
```json
{
  "accepted": [
    {"key": "user_role", "value": "postdoc in Smith Lab",
     "created_at": "...", "updated_at": "..."}
  ],
  "pending": [
    {"id": "uuid", "key": "research_focus",
     "value": "cryo-EM of membrane proteins",
     "reason": "ongoing project context",
     "conversation_id": "...", "proposed_at": "..."}
  ]
}
```

Accepted entries come from `user_memory` (20-entry LRU cap, model-
curated via `remember`/`forget` tools or via the accept endpoint
below). Pending entries come from `proposed_memories` (10-entry
FIFO cap per user; populated by the `stop` hook).

### 4.11b `POST /api/memories/proposed/{id}/accept`

Upserts the proposed (key, value) into `user_memory` and deletes
the proposal row. Triggers the same 20-entry LRU eviction as a
`remember` tool call.

**Response (200)**: `{"accepted": {"remembered": true, "key": "...",
"value": "...", "evicted": [...], "total_memories": N, "max_memories": 20}}`

**404** if the proposal id does not exist or is not owned by this user.
**400** with the standard error envelope if `memory_store.MemoryError`
fires (cap violations on the underlying upsert).

### 4.11c `POST /api/memories/proposed/{id}/reject`

Deletes the proposal and remembers the key in `rejected_memory_keys`
so the classifier doesn't re-propose it next turn (50-entry FIFO
cap per user; idempotent on re-reject).

**Response (200)**: `{"rejected": true, "key": "..."}`. **404** if
the proposal id does not exist or is not owned by this user.

### 4.11d `GET /api/chats/{cid}/plan` (P2 #24 Phase 1)

Returns the current plan dict for this conversation, or **404** if no
plan has been set. The plan is one-per-conversation, mutable; replace
via the `set_plan` MCP tool or `PATCH /api/chats/{cid}/plan`
(Phase 2). The same dict is also embedded under `plan` on
`GET /api/chats/{cid}` so transcript reload doesn't need a second
HTTP call.

**Response (200)**:
```json
{
  "conversation_id": "...",
  "items": [{"id": "p-1", "title": "...", "status": "pending|in_progress|done|cancelled", "notes": "..." | null, "updated_at": "..."}],
  "requires_approval": false,
  "approved_at": null,
  "approval_mode": "each",
  "created_at": "...",
  "updated_at": "..."
}
```

### 4.11e `DELETE /api/chats/{cid}/plan` (P2 #24 Phase 1)

Drops the plan row entirely. Returns `{"deleted": true|false}` —
idempotent: `false` simply means there was nothing to delete.

### 4.11f `POST /api/chats/{cid}/plan/approve` (P2 #24 Phase 2)

Marks the plan approved with the supplied mode. Body:
`{"mode": "each" | "auto"}` (default `"each"`).

- `mode="each"` — one approval = one gated tool call. The
  `postToolUse` hook clears `approved_at` after the next gated
  dispatch so subsequent calls re-trigger the gate.
- `mode="auto"` — the approval persists across all subsequent
  gated calls until the plan is replaced (`set_plan` resets the
  approval state to `(False, NULL, 'each')`) or the user revokes
  (POST `/approve` again with `mode="each"`).

**Response (200)**: `{"approved": true, "plan": {...}}` with the
freshly-approved plan. **400** with the standard error envelope on
`PlanError` (no plan exists, invalid mode).

### 4.11g `POST /api/chats/{cid}/plan/reject` (P2 #24 Phase 2)

Deletes the plan row. The user is expected to type a follow-up
message describing the new direction; the model's next turn will
see no plan block and recover naturally. **No** synthetic system
message is injected — the user-driven follow-up is the recovery
signal.

**Response (200)**: `{"rejected": true, "deleted": true|false}`.

### 4.11h `PATCH /api/chats/{cid}/plan` (P2 #24 Phase 2)

User-edits-the-plan with **implicit approve-on-save**. Body:
`{"items": [...], "mode"?: "each" | "auto"}` (mode defaults to
`"each"`). The new items list replaces the old verbatim (same
shape as `set_plan`'s `items`); the plan is marked approved with
the supplied mode in the same transaction. Preserves
`requires_approval=True` so the gate continues to fire on future
gated tool calls after this approval is consumed.

**Response (200)**: `{"plan": {...}}` with the post-edit plan.
**400** on validation failure (empty items, cap violation, etc.).

### 4.12 `GET /api/profile`

Loads the authenticated user's profile. Always returns 200; users who
never set a profile get an all-`null` shape.

**Response (200)**:

```json
{
  "user_email": "alice@example.org",
  "about_me": "I'm a biophysics PhD candidate working on lipid bilayers.",
  "response_format": "Always cite DOIs. British spelling.",
  "default_persona": "research",
  "default_rag_sources": ["papers", "web"],
  "timezone": "Europe/Berlin",
  "created_at": "2026-04-14T09:00:00Z",
  "updated_at": "2026-04-14T09:30:00Z"
}
```

### 4.13 `PUT /api/profile`

Upserts profile fields. Only keys present in the body are written; missing
keys are left untouched. Send `null` (or `""`) to clear a field.

**Body** (all keys optional):

```json
{
  "about_me": "I'm a biophysics PhD...",
  "response_format": "Cite DOIs, British spelling, concise answers.",
  "default_persona": "research",
  "default_rag_sources": ["papers", "web"],
  "timezone": "Europe/Berlin"
}
```

**Caps**: `about_me` and `response_format` are each capped at **1500
characters**. Bodies that exceed the cap return **400** with an error
message identifying the offending field.

**Response (200)**: the full profile after the upsert (same shape as
`GET /api/profile`).

**Effect on chat completions**:

- The non-empty parts of `about_me` + `response_format` are rendered as a
  `=== USER PROFILE === ... === END USER PROFILE ===` block prepended to
  the persona system prompt on every persistent chat turn. **Ephemeral
  chats deliberately skip this injection** so privacy mode does not
  carry user-identifying preferences into the model.
- `default_persona` is consulted only when the request body of
  `POST /api/chat/completions` does not pin a persona itself. Body
  always wins.
- `default_rag_sources` and `timezone` are stored but not yet wired
  into request handling (RAG is currently model-driven, and timezone
  awaits §23 digests).

### 4.14 `DELETE /api/profile`

Resets the profile by removing the row. Returns **200** with
`{"removed": true}` if a row was deleted, `{"removed": false}` if there
was nothing to delete.

### 4.15 Artifact routes (§22 Stage A)

Artifacts are versioned documents scoped to a conversation - papers,
LaTeX sources, code snippets, SVG figures, sandbox-generated files,
anything the user wants to iterate on or revisit rather than
re-scroll through the chat history. After §22 Stage C the table
unifies both **model-written** artifacts (created via
`create_artifact`, edited via `update_artifact`, stored inline in
`artifact_versions.content`) and **sandbox-generated** artifacts
(created by `run_python` **or** `compile_latex`, stored on disk in
the sandbox container, referenced via `external_url`). Rows carry a
`source` field (`'model_written'` or `'sandbox_generated'`) to
distinguish them. §18 `compile_latex` returns two sandbox artifacts
per successful call: the `.tex` source (always) and the compiled
`.pdf` (on success only); on compile failure only the `.tex`
artifact is surfaced so users can still download and edit the
source manually.

Model-written artifacts are versioned and editable; sandbox-generated
artifacts are always `latest_version: 1` and read-only (`update_artifact`
returns an error on a sandbox row, regenerate via `run_python` or
`compile_latex` instead).
The full content snapshot chain lives in `artifact_versions`; there is
no diff chain, just full snapshots per version.

**Text in SQLite, binary on disk.** Model-written artifacts hold text
inline under a 500 KB byte cap. Sandbox-generated artifacts store
a short metadata placeholder inline and point `external_url` at
`GET /api/artifacts/{cid}/{sandbox_aid}` (the existing sandbox proxy
endpoint) for the real file bytes.

**`GET /api/chats/{conversation_id}/artifacts`** - list every
artifact in a conversation. Metadata only (no content). Ordered by
`updated_at DESC`. Response:

```json
{
  "artifacts": [
    {
      "id": "art_abc123",
      "title": "Kinase abstract v1",
      "content_type": "text/markdown",
      "language": "markdown",
      "latest_version": 3,
      "word_count": 487,
      "byte_size": 2893,
      "created_at": "...",
      "updated_at": "..."
    }
  ],
  "total": 1
}
```

**`GET /api/chats/{conversation_id}/artifacts/{artifact_id}`** - load
the latest version of a specific artifact, including the full
content. Optional `?version=N` returns a historical version
instead. 404 if the artifact doesn't exist, belongs to a different
conversation, or is owned by another user.

**`PATCH /api/chats/{conversation_id}/artifacts/{artifact_id}`** -
user-driven side-panel edit. Creates a new version with
`created_by="user"`, visible to the model on the next chat turn via
the updated `=== ACTIVE ARTIFACTS ===` summary block. Request:

```json
{
  "content": "<full new content>",
  "change_summary": "fixed a typo in equation 3"
}
```

Full-content replacement only - there is no diff mode on the user
side. 400 if `content` is missing or exceeds the 500 KB cap.

**MCP tools**: `create_artifact`, `read_artifact`, `update_artifact`,
`list_artifacts`. All four refused in ephemeral chats. `create_artifact`
requires `title`, `content`, `content_type`. `update_artifact` supports
two modes - full-content replacement (default) and unified-diff
application (`is_diff=true`, Stage B). Both modes accept an optional
`base_version` that the backend checks against `latest_version`; a
mismatch is rejected as stale so concurrent edits from the user's
side-panel PATCH don't get silently clobbered. The model discovers
existing artifacts from the `=== ACTIVE ARTIFACTS ===` block injected
into the system prompt on every persistent turn.

**Diff format** (Stage B): standard unified diff with `@@ -old_start,old_len
+new_start,new_len @@` hunk headers and ` `/`-`/`+`-prefixed body lines.
Optional `--- a/ +++ b/` file headers are accepted and ignored. Strict
matching only: context lines and removal lines must match the source
exactly at the indicated line number. On any mismatch the whole update
is rejected with a clear `"hunk N: context mismatch at line M"` error so
the caller can re-read and retry. Multi-hunk diffs work. Line numbers
are 1-based (standard unified-diff convention). The 500 KB byte cap is
enforced against the *result* of applying the diff, not the diff text
itself.

**`update_artifact` response shape** (same for both modes):

```json
{
  "id": "art_abc123",
  "title": "...",
  "content_type": "text/markdown",
  "language": "markdown",
  "version": 4,
  "change_summary": "...",
  "created_by": "assistant",
  "updated_at": "...",
  "base_version": 3,
  "applied_hunks": 2,
  "lines_added": 12,
  "lines_removed": 4
}
```

For full-content updates, `applied_hunks` is `null` and `lines_added`/
`lines_removed` are computed from `difflib.ndiff(prev, new)` so the
frontend can display a uniform `+12 −4` chip regardless of which
update mode was used.

**SSE events** on `/api/chat/completions` when an artifact is
written or updated:

- `artifact_created`: unified event for both model-written
  (`create_artifact` tool) and sandbox-generated (`run_python` and
  `compile_latex` tool outputs) files. The payload always contains
  `{id, source, title, content_type, version, conversation_id,
  tool_call_id}`. A **`source` discriminator** tells the frontend
  which branch to render:
  - `source: "model_written"`, additional fields: `language`
    (only for text/code artifacts). Route to the side panel with
    version picker + editable content.
  - `source: "sandbox_generated"`, additional fields: `filename`,
    `size_bytes`, `external_url` (points at
    `/api/artifacts/{cid}/{sandbox_aid}` for download / inline
    image rendering). Read-only; no version picker.
- `artifact_updated`: `{id, source, title, version, change_summary,
  created_by, conversation_id, tool_call_id, applied_hunks,
  lines_added, lines_removed, base_version}`. Only fires for
  model-written artifacts, sandbox-generated rows are read-only.

**§22 Stage C unified the sandbox output path**: the legacy
standalone `artifact` event is removed. Both `run_python` PNG/CSV
outputs and `compile_latex` `.tex`/`.pdf` outputs now flow through
`artifact_created` with `source: "sandbox_generated"`. See §5 for
the full event catalogue row including the `~~artifact~~` tombstone.

### 4.16 Project routes (§21)

Projects are top-level workspaces that scope conversations and
documents together under a single set of instructions. A conversation
may live inside a project (filed) or outside it (unfiled / in the
default bucket); deleting a project leaves its conversations and
documents in the Unfiled bucket - **no cascading delete**.

**`POST /api/projects`**: create a project.

Body (all strings optional except `name`):

```json
{
  "name": "Kinase Thesis",
  "description": "PhD on small-molecule kinase inhibitors",
  "instructions": "Prefer 2023+ papers. Always cite DOIs.",
  "default_persona": "research"
}
```

Caps: `name` ≤ 200 chars, `description` ≤ 1000, `instructions` ≤ 2000.
**400** for cap violations or missing name.

**`GET /api/projects`**: list the user's projects.

Query params: `archived` (bool, default `false` - archived projects
are hidden unless this is set), `limit`, `offset`.

Response:

```json
{
  "projects": [
    {
      "id": "proj_...",
      "user_email": "...",
      "name": "Kinase Thesis",
      "description": "...",
      "instructions": "...",
      "default_persona": "research",
      "archived": false,
      "created_at": "...",
      "updated_at": "...",
      "conversation_count": 24
    }
  ],
  "total": 3
}
```

Ordered by `updated_at DESC`.

**`GET /api/projects/{id}`**: single project with inline counts.
Same shape as above plus `document_count` (sourced from Qdrant).
**404** if the id is unknown or owned by another user.

**`PATCH /api/projects/{id}`**: update any subset of `name`,
`description`, `instructions`, `default_persona`, `archived`. Returns
the updated project. **400** on cap violation, **404** on unknown id.

**`DELETE /api/projects/{id}`**: hard-delete the project row. Unfiles
its conversations (`project_id` → NULL) and its docs (Qdrant
`project_id` payload cleared). Responds **200** with
`{"deleted": true}`. **Conversations and docs are preserved** in the
Unfiled bucket - the user can re-file them later or delete them
individually.

**`POST /api/projects/{id}/conversations/{conversation_id}`**: file a
conversation into a project. **404** if either id is unknown or owned
by another user.

**`DELETE /api/projects/{id}/conversations/{conversation_id}`**:
unfile a conversation back to the default bucket. The URL includes the
project id for symmetry with the file route, but the backend does not
verify it matches the current filing.

**`GET /api/chats?project_id=<pid>`**: list conversations inside a
specific project. Pass the literal `__unfiled__` sentinel to list
only the conversations that have no project.

**`POST /api/documents/upload`**: accepts an optional `project_id`
form field to file the document into a project at upload time. The
project must be owned by the requesting user (**404** otherwise).

**Ephemeral chats + projects**: mutually exclusive by design. If a
request is `ephemeral: true` AND references a conversation that
belongs to a project, the backend returns **400**.

**Persona precedence**: when the request body has no `persona` field,
the backend resolves the persona in this order:
`project.default_persona` > `profile.default_persona` >
`DEFAULT_PERSONA_ID`. An explicit `persona` in the request body always
wins.

**Project context injection**: when a conversation has a `project_id`,
`chat_service` prepends a `=== PROJECT CONTEXT === ... === END
PROJECT CONTEXT ===` block to the system prompt on every turn,
containing the project name, description, and instructions.

**`search_user_docs` project scoping**: when a conversation is filed
into a project, the MCP `search_user_docs` tool auto-scopes to the
project's docs. The response includes a `sources_used` field
(`["project"]`, `["project", "global"]`, or `["global"]`) so the
model can honestly tell the user whether a hit came from their
project or from the broader corpus. Two-phase fallback: if the
scoped search returns zero, a user-global search is run and its
results are returned alongside `sources_used: ["project", "global"]`.

### 4.17 `GET /api/artifacts/{conversation_id}/{artifact_id}` (sandbox)

Serves an artifact (plot, file, generated document) produced by a
sandbox tool inside the given conversation. Today that means
`run_python` output (PNG, CSV, XLSX, arbitrary file writes) or
`compile_latex` output (the `.tex` source and, on success, the
compiled `.pdf`). The actual file lives in the sandbox sidecar;
this endpoint proxies the bytes so the host never needs to expose
the sandbox container's port.

**Auth + ownership**: requires `X-Munin-Email` and the caller must own
the conversation. A user fetching another user's artifact gets **404**
(we deliberately do not distinguish 403 from 404 here so artifact ids
cannot be probed).

**Path params**:

- `conversation_id` - the chat the artifact was produced in.
- `artifact_id` - the `id` field from the `artifact_created` SSE
  event (also present inside the tool result's `artifacts` array
  for both `run_python` and `compile_latex`). Both kernel-produced
  and compile_latex-produced files use the same URL shape - the
  sandbox service reads from two sibling manifests
  (`_artifacts.json` for kernel outputs, `_latex_artifacts.json`
  for compile_latex outputs) under the conversation's scratch
  directory, transparent to callers.

**Response (200)**: the raw file bytes with `Content-Type` set to the
artifact's media type (`image/png` for plots, etc.) and a
`Content-Disposition: inline` header so browsers can render images
directly. **404** for unknown ids or cross-user access. **502** if the
sandbox sidecar is unreachable.

`display_url` on the `artifact` SSE event always points at this route.
The frontend can drop it straight into an `<img>` `src` for images or
into a download anchor for non-image artifacts.

### 4.18 `POST /api/chats/{id}/report`: report a chat for review

Flag a conversation for developer review. The backend serialises the
full conversation (metadata, all messages with content/thinking/
tool_calls/rag_context, and artifact metadata) to a timestamped JSON
file under `/opt/munin/data/reported/`. These reports serve as input
for the test harness, each reported conversation can be turned into
a regression test that replays the user's turns and asserts on the
assistant's behaviour.

**Auth + ownership**: requires `X-Munin-Email`. The caller must own the
conversation, reporting someone else's chat returns **404**.

**Body** (JSON, all fields optional):

```json
{
  "reason": "Download link didn't work, opened the chat page instead of the PDF"
}
```

`reason` is a free-text description of what went wrong (max 2000
chars). If omitted, the report is filed without a description.

**Response (200)**:

```json
{
  "reported": true,
  "report_id": "rpt_20260418T091500_979c7fda"
}
```

**Idempotent**: reporting the same conversation twice overwrites the
previous report file (same `report_id` derived from conversation id).
No rate limit beyond idempotency.

**Report file structure** (`/opt/munin/data/reported/{report_id}.json`):

```json
{
  "report_id": "rpt_20260418T091500_979c7fda",
  "conversation_id": "979c7fda-...",
  "user_email": "user@example.com",
  "persona": "research",
  "title": "VS protocol design",
  "reason": "Download link didn't work",
  "reported_at": "2026-04-18T09:15:00Z",
  "messages": [
    {
      "index": 0,
      "role": "user",
      "content": "...",
      "thinking": null,
      "tool_calls": null,
      "rag_context": null,
      "created_at": "2026-04-17T08:41:12Z"
    }
  ],
  "artifacts": [
    {
      "id": "art_...",
      "title": "...",
      "content_type": "application/pdf",
      "source": "sandbox_generated",
      "latest_version": 1
    }
  ]
}
```

**Errors**: 404 if conversation not found or not owned by caller. 400
if `reason` exceeds 2000 chars.

---

### 4.19 `GET /api/tags`

Tag-autocomplete catalogue for the chat composer (`#topic`, `#group`,
`#@username` shortcuts) and for tag pills on the knowledge browser.

**Request**: no body, no auth needed (tag names are public; the model
already sees them in search results).

**Response (200)**:

```json
{
  "topics": [
    {"slug": "machine-learning", "label": "Machine Learning", "paper_count": 412}
  ],
  "groups": [
    {"slug": "varghela-lab", "display_name": "Varghela Lab", "paper_count": 87}
  ],
  "contributors": [
    {"username": "alice", "display_name": "Alice Doe", "group_slug": "varghela-lab", "paper_count": 23}
  ],
  "contributor_count": 18
}
```

Topics come from the §15 embedding map (`unclustered` is filtered out).
Groups come from `contributors.yml`. Lists are sorted by `paper_count`
descending. Empty arrays are returned (not 404) when a family has no
entries yet.

`contributors` is the `#@username` mention list, so it contains **only**
contributors that have a `username` set — it is deliberately a subset and
not a contributor headcount. `contributor_count` is the distinct number of
contributors (by `contributors[].email`) with at least one paper in the
KB; this is what the knowledge overview's "Contributors" stat shows.

### 4.20 `GET /api/tags/{kind}/{slug}/papers`

Paginated paper list for a tag. Browse view, no ranking, no SPECTER
query, ordered by metadata. Used by the knowledge browser page to
drill down from a tag pill.

**Path params**:

| Param | Values |
|---|---|
| `kind` | `topic` \| `group` \| `contributor` |
| `slug` | the corresponding `topic_slug`, `group_slug`, or `username` |

**Query params**:

| Param | Default | Notes |
|---|---|---|
| `offset` | `0` | non-negative |
| `limit` | `50` | 1–200 |
| `sort` | `year_desc` | `year_desc` \| `year_asc` \| `upload_desc` |

**Response (200)**:

```json
{
  "kind": "topic",
  "slug": "machine-learning",
  "total": 412,
  "offset": 0,
  "limit": 50,
  "sort": "year_desc",
  "papers": [
    {
      "title": "...",
      "doi": "10.xxxx/...",
      "year": 2025,
      "authors": ["Doe, A.", "Smith, B."],
      "journal": "Nature",
      "contributors": [{"display_name": "Alice", "group_slug": "varghela-lab",
                        "group_display_name": "Varghela Lab", "upload_time": "2026-04-12T..."}],
      "topic": {"label": "Machine Learning", "slug": "machine-learning"},
      "download_url": "https://search.muninai.org/paper/.../pdf"
    }
  ]
}
```

`download_url` is only present when the PDF is on disk. Sort is applied
**within the returned page**: for stable cross-page ordering, rely on
the default `year_desc` and don't re-sort client-side.

**Errors**: 400 if `kind` is unknown or `slug` is empty. 503 if Qdrant
is unavailable.

### 4.21 `GET /api/embedding_map`

Serves the flat JSON produced by `scripts/knowledge/build_embedding_map.py`
, a 2D UMAP projection of the paper corpus with cluster labels. Used by
the knowledge-map visualisation on the frontend.

**Request**: no body, no auth needed.

**Response (200)**: JSON file (raw `application/json`). Shape is defined
by the build script; treat as opaque from the API contract's POV.

**Errors**: 404 with `{"error": {"message": "Embedding map not yet built"}}`
when the nightly timer hasn't run yet (first-boot state).

---

## 5. SSE event catalogue for `/api/chat/completions`

All events follow the SSE framing:

```
event: <name>
data: <minified json>

```

| Event | Payload | Emitted when |
|---|---|---|
| `conversation` | `{"id": "...", "title": "..." \| null, "is_new": true \| false, "ephemeral": true \| false, "stream_id": "..."}` | At stream start; again after auto-title for new conversations. `ephemeral: true` means the id has the `ephemeral-` prefix and was never persisted; the auto-title follow-up event is skipped. `stream_id` (P1 #10) is the server-assigned id for this SSE stream — the frontend persists it (localStorage since background turns, so the pointer survives a closed tab) along with the latest `Last-Event-ID`, and latches it in-memory so Stop can hit §4.8b. Resume via the `/resume` endpoint |
| `routing` | `{"profile": "chat"\|"research"\|"code", "pin": "chat"\|..., "method": "rule"\|"knn"\|"fallback"\|"pin", "confidence": 0.0}` | **A3** (persona→router migration). Fires once at stream start, before the first model call. `profile` is the per-turn routed profile (drives the layered system prompt, sampling, and tools); `pin` is the user's selected persona. `method` is how the profile was decided (`rule`=slash command, `knn`=example-set match, `fallback`=pin/chat default, `pin`=router disabled). When `ROUTER_ENABLED` is false, `profile == pin` and `method == "pin"`. Replaces the retired `persona_changed`/`delegated` events (the delegation machinery is removed at A4). |
| `thinking` | `{"content": "partial reasoning text"}` | Multiple. Accumulate client-side. Sourced from vLLM `delta.reasoning_content` (qwen3 reasoning parser) |
| `tool_call` | `{"id": "tc-1", "name": "paper_search", "arguments": {...}}` | Once per finalized tool call the main model asks for. Emitted after the vLLM delta for that turn finishes, not mid-arguments |
| `tool_result` | `{"id": "tc-1", "name": "paper_search", "result": {...}, "duration_ms": 800}` | After the tool actually finishes. Matches `tool_call.id` |
| `artifact_created` | `{"id": "art_...", "source": "model_written" \| "sandbox_generated", "title": "...", "content_type": "...", "version": 1, "conversation_id": "...", "tool_call_id": "...", "language": "...", "filename": "...", "size_bytes": N, "external_url": "/api/artifacts/{cid}/{sandbox_aid}"}` | §22. Fired after both `create_artifact` (model-written) and `run_python` (sandbox-generated) produce a new artifact. Frontend routes by the `source` discriminator: model-written → side-panel entry with version picker and editable content; sandbox-generated → download chip or inline image using `external_url`. The `language` field is only present for model-written rows; `filename`, `size_bytes`, and `external_url` are only present for sandbox-generated rows |
| `artifact_updated` | `{"id": "art_...", "source": "...", "title": "...", "version": N, "change_summary": "...", "created_by": "assistant" \| "user", "conversation_id": "...", "tool_call_id": "...", "applied_hunks": N \| null, "lines_added": N, "lines_removed": N, "base_version": N}` | §22. After a successful `update_artifact` tool call (or a user PATCH reflected back on the next streaming turn). Bumps the side panel's version picker and refreshes content. Only fires for model-written artifacts - sandbox-generated rows are read-only |
| ~~`artifact`~~ | ~~*deprecated, removed in §22 Stage C*~~ | ~~The old standalone sandbox-artifact event has been removed. Consume `artifact_created` with `source: "sandbox_generated"` instead.~~ |
| `clarification` | `{"tool_call_id": "tc-1", "conversation_id": "...", "what_i_understood": "...", "questions": [{"id": "q1", "text": "...", "options": ["a","b","c"], "allow_custom": true}, ...]}` | §14. Fires exactly once per turn when the model calls `ask_clarification` on an ambiguous request. The turn is short-circuited: no `tool_result`, no wrap-up synthesis, no other tool calls from the same turn run, and `done` follows immediately with `finish_reason: "clarification"`. Frontend renders an inline card with 1-5 multiple-choice questions (2-6 options each, plus optional free-text field when `allow_custom: true`). The user's answers should be posted as a normal follow-up user message on the next `/api/chat/completions` request - no special-case API |
| `agent_start` | `{"agent": "research_orchestrator", "query": "..."}` | When the model invokes an agent via the `invoke_agent` tool |
| `agent_thinking` | `{"content": "..."}` | Nested reasoning stream from the agent's own vLLM loop |
| `agent_tool_call` | `{"id": "atc-1", "name": "paper_search", "arguments": {...}}` | Each tool the agent fires |
| `agent_tool_result` | `{"id": "atc-1", "name": "paper_search", "result": {...}, "duration_ms": 42}` | Paired with `agent_tool_call` by id |
| `agent_done` | `{"agent": "...", "tool_calls": 8, "duration_seconds": 34, "stopped_reason": "done"}` | When the agent returns. `stopped_reason` ∈ `done`/`max_iterations`/`max_tool_calls`/`timeout`/`error` |
| `token` | `{"content": "partial response text"}` | Many. Accumulate into the visible answer. Sourced from vLLM `delta.content` |
| `done` | `{"usage": {"prompt_tokens": N, "completion_tokens": N, "total_tokens": N}, "usage_by_purpose": {"main_turn": {...}, "wrap_up": {...}, ...}, "finish_reason": "stop", "terminal_reason": "done"}` | Always the last event on success. `usage` is the **aggregate across every vLLM call this turn**, not just the last one — the gateway records `total_tokens` from here for quota. `usage_by_purpose` is the same numbers broken down by call site for debugging: `main_turn`, `wrap_up`, `forced_clarification`, `forced_required`, `agent_turn`, `agent_wrap_up`, `summary`, `title`. Purposes with zero calls are omitted. `terminal_reason` ∈ `done` / `max_turns` / `stream_error` / `cancelled` — distinct from `finish_reason` (which is `stop` even after the turn-budget wrap-up). The frontend uses `terminal_reason == "max_turns"` to render a **Continue** affordance so the user can resume a task that hit its (auto-extended) tool-use budget without retyping "keep going" |
| `retrying` | `{"attempt": N, "max_attempts": M, "delay_s": 1.0, "reason": "vllm 503"}` | A vLLM call hit a transient error (5xx / 429 / connection drop / pre-first-byte stream drop) and is about to retry. Fires before the backoff sleep. `attempt` is 1-indexed. `reason` is a short tag (e.g. `vllm 503`, `vllm ConnectError`). Multiple may fire per turn. Frontend should render a transient "reconnecting" indicator and reset it once any other event resumes |
| `reconnecting` | `{"attempt": N, "max_attempts": M, "delay_s": 1.0}` | **Synthetic, client-side only** (P1 #10). Not emitted by the server — the frontend's SSE consumer dispatches it when an SSE connection drops and a `GET /api/chat/completions/resume` is being attempted with `Last-Event-ID`. Renders the same "reconnecting" indicator as `retrying`; cleared on the first real event from the resumed connection |
| `stream_gone` | `{"reason": "..."}` (fields optional) | **Synthetic, client-side only** (background turns). Dispatched by `resumeChat` when a resume GET returns `410 Gone`. Because the turn's outcome — completed answer or save-always partial — is already persisted, the store reacts by **reloading the conversation transcript**, not by showing an error banner. The mid-stream reconnect loop in `streamChat` deliberately does NOT use this: there the user is watching a live bubble die, and an error banner is the honest signal |
| `memory_proposed` | `{"id": "uuid", "key": "user_role", "value": "postdoc in Smith Lab", "reason": "stable identity fact"}` | **P2 #25**. Auto-extracted memory candidate from a post-turn classifier hook. Fires zero or more times per turn, typically AFTER `done` (the `stop` hook runs in the finally block). Only fires when `terminal_reason ∈ {done, max_turns}` — never on cancelled/error paths. Capped at 3 per turn and 10 pending per user (FIFO). Frontend renders an inline accept/reject pill below the assistant bubble; user action posts to `/api/memories/proposed/{id}/{accept,reject}`. Skips the persistent store ContextVar lookup is unavailable (ephemeral chats are silently skipped) |
| `plan_updated` | `{"conversation_id": "...", "items": [...], "requires_approval": bool, "approved_at": str\|null, "approval_mode": "each"\|"auto", "created_at": "...", "updated_at": "..."}` | **P2 #24 Phase 1**. Fires after every successful `set_plan` or `update_plan_item` MCP tool dispatch. Frontend renders an inline `PlanCard` checkbox list above the assistant bubble whose turn last touched the plan. State persists across reloads via `Message.plan_snapshot` (also returned in `GET /api/chats/{id}` under the `plan` key). |
| `plan_approval_required` | `{"tool": "...", "arguments": {...}, "plan": {...}}` | **P2 #24 Phase 2**. Fires from the `preToolUse` gate hook when a gated tool dispatch short-circuits because the in-flight plan is unapproved. The dispatch returns a synthetic `{"status": "awaiting_user_approval", ...}` result; the model sees this, generates a "waiting for approval" message, and the turn ends with `done`. Frontend renders Approve / Approve-all / Edit / Reject buttons on the inline `PlanCard`; the user's choice posts to `/api/chats/{cid}/plan/{approve\|reject}` or `PATCH /api/chats/{cid}/plan`, then the frontend sends a synthetic `"I've approved the plan, please continue."` user message so the model resumes. |
| `compact_boundary` | `{"summary_through_index": N, "dropped_messages": K, "summary": "...", "is_fresh": true \| false}` | **P2 #22**. Fires at most once per turn, immediately after the initial assembly step in `assemble_context` (before any `thinking`/`token` event), when the model's view of the conversation has been compressed to fit the context budget. `is_fresh: true` means the summary was generated this turn (blocking vLLM call ~1-3s); `is_fresh: false` means a prior turn's opportunistic prefetch had already populated it (no cost this turn). Frontend renders a thin "earlier N messages summarised" divider above the assistant bubble with the full summary text revealed on click. The backend schedules a fire-and-forget background task at end-of-turn that pre-summarises whenever history exceeds 70% of the budget, so subsequent turns generally land in the `is_fresh: false` path |
| `error` | `{"message": "Human-readable error"}` | On failure. Stream terminates after this |

Ordering for a normal RAG-enabled chat with one tool call:

```
conversation → rag_context → thinking* → tool_call → tool_result
             → thinking* → token* → done
```

Ordering when the model invokes an agent:

```
conversation → tool_call(invoke_agent)
             → agent_start → agent_thinking* → agent_tool_call → agent_tool_result
             → agent_done → tool_result(invoke_agent)
             → token* → done
```

The parent `tool_result` for `invoke_agent` still lands after `agent_done`,
so the `TaskLog` in the frontend can either render it as a nested workflow
or as a single tool entry, both views are supported by the stream.

## 6. Persona context injection

Every chat completion assembles its system prompt as:

```
{persona.params.system}

{agent summaries, one bullet per registered agent, with invocation hint}
```

The agent summaries are injected automatically so the main model knows it
can delegate to `research_orchestrator`, `code_checker`, or `writing_agent`.
The summaries come from `config/agents.yml` and re-load on every service
restart. Frontend does not need to touch this.

## 7. Context budgeting & summarization

- Budget: `MAX_CONTEXT = 60000`, `GENERATION_RESERVE = 8000` (overridable
  via env). Tokens are counted with the real Qwen3 tokenizer (its
  `tokenizer.json` is mounted into the retrieval container); if that
  file is unreachable the count falls back to a ~4-chars/token
  heuristic, which undercounts code / LaTeX / JSON.
- If `system_prompt + summary + history + new_message` fits, sent as-is.
- If not: keep the newest messages that fit in half the history budget,
  summarize everything older via a non-streaming vLLM call, persist the
  summary + `summary_through_index` to the `conversations` row, rebuild.
- Fallback: if vLLM is unreachable mid-summarization, hard-truncate the
  history and proceed. No 500 to the user.

Frontend implication: **the `summary` field on a conversation row will
start populated after long chats.** Don't render it as part of the message
list, treat it as background metadata.

## 8. Auto-title generation

On a brand-new conversation, after the first assistant response completes:

- If the first user message is ≤ 60 chars → use it verbatim as the title.
- Otherwise → non-streaming vLLM call with a title-generation prompt. Falls
  back to a 60-char truncation if vLLM fails.

Result is persisted via `PATCH`, and a **second `conversation` SSE event**
with the new title is pushed into the same stream. The frontend should
update the title in place without creating a new tab or reloading.

## 9. MCP tool registry

Tools available to the main model and agents (see `retrieval/mcp/schemas.py`):

| Tool | What it does |
|---|---|
| `web_search` | SearXNG web search |
| `web_fetch` | Fetch a URL and optionally summarise with vLLM |
| `paper_search` | SPECTER semantic search over the local papers corpus |
| `semantic_scholar_search` | Semantic Scholar API |
| `paper_lookup` | Paper metadata by DOI |
| `get_citations` / `get_references` | Neo4j citation graph traversal |
| `get_author_papers` | Author-indexed lookup |
| `get_paper_pdf` | Local PDF availability by DOI |
| `check_papers_availability` | Bulk DOI availability check |
| `llm_summarize` | vLLM summarisation helper |
| `search_user_docs` | Semantic search over the current user's uploaded docs (auto-filters by `X-Munin-Email` via contextvar, never pass a user id) |
| `invoke_agent` | Delegate to an agent workflow. Input: `{"agent": "...", "query": "..."}` |
| `tool_search` | Discover deferred tools. Input: `{"query": "..."}`. See "Deferred tool schema" below |

Agents themselves (`research_orchestrator`, `code_checker`, `writing_agent`)
are defined in `config/agents.yml` with their own tool allowlists, iteration
limits, and wall-clock timeouts. Agents cannot invoke each other.

**Deferred tool schema (P1 #7).** The `tools` array sent to vLLM does
**not** carry all 39 tools. It carries only a ~9-tool core set
(`paper_search`, `web_search`, `read_paper`, `run_python`,
`create_artifact`, `calculate`, `ask_clarification`,
`delegate_to_persona`, `tool_search`), intersected with the persona's
allowlist. Every other tool is hidden until the model calls
`tool_search` with a natural-language query; the executor returns the
matching tools' schemas (top 8 by relevance, scoped to the persona's
allowlist) and unlocks them into the schema for the rest of that
request. This keeps prefill ~15K tokens lighter and well clear of the
hang cliff. Authorization is unchanged — the persona allowlist is still
the gate; deferral only governs schema *visibility*. Unlocked tools
reset per request. Frontend implication: none — `tool_search` appears
as an ordinary `tool_call` / `tool_result` pair.

The executor validates every tool call's `arguments` against the tool's
`inputSchema` before dispatch (`mcp/executor.py`). Schema mismatches
(wrong type, missing required field, value out of enum) short-circuit
with a `tool_result` whose `result` is `{"error": "invalid arguments for
<tool> at <pointer>: <message>"}`. The frontend doesn't need to render
these differently — they appear as normal tool_result events and the
model self-corrects on its next turn. Permissive on extra unknown keys.

Concurrency policy: tools that declare `is_concurrency_safe: False` in
`mcp/schemas.py` (the artifact / memory / sandbox mutators —
`create_artifact`, `update_artifact`, `save_artifact_to_documents`,
`remember`, `forget`, `run_python`, `sandbox_reset`, `compile_latex`)
run **serially in declared order** when the model emits multiple of
them in one turn. Every other tool fans out via `asyncio.gather`. The
two groups run concurrently with each other since safe tools by
definition don't share mutable state with anything. Frontend
implication: `tool_result` events for unsafe tools arrive in the same
order they appeared in the matching `tool_call` events; the existing
match-by-id rendering keeps working unchanged.

## 10. Known gotchas for frontend devs

1. **`services.embedding: "unavailable"` is normal on a cold start.** It
   flips to `"ok"` after the first retrieval or upload that loads the model.
   Don't show a red dot, show "warming up" or hide the field.
2. **Two `conversation` events per new chat.** The first carries
   `is_new: true` and a null title; the second lands after auto-title.
   Accumulator model: match on `id`, replace the title field, don't clear.
3. **Tool calls use the OpenAI tool_call ID format** (`"tc-1"`, `"atc-1"`).
   Match `tool_result.id` against `tool_call.id` to pair them.
4. **Agent events are mid-stream.** While a `tool_call` with name
   `invoke_agent` is executing, the stream will emit `agent_*` events
   *before* the matching `tool_result`. The TaskLog needs to render them
   nested (or as a chronological list) without assuming tool_result comes
   straight after tool_call.
5. **Thinking text can be huge.** Qwen3 reasoning traces can exceed the
   visible answer. Collapse by default, show a "Reasoning" toggle.
6. **Document upload returns synchronously.** There's no `"processing"`
   status from the backend today, either `"embedded"` or `"stored"`.
   If you see `"processing"` in the frontend code, that's a leftover from
   the original FRONTEND-REFERENCE draft; the backend never emits it.
7. **`persona` filter on `/api/chats` is exact-match** on the id. Passing
   `persona=Chat` won't find conversations created with `persona: "chat"`.
8. **FTS search is not fuzzy.** `polymer` matches; `polym` doesn't. Use
   FTS5 prefix syntax (`polym*`) if you want prefix matching.
9. **Rate limits / quotas are on the VPS gateway**, not this service.
   `/api/chat/completions` will happily stream until vLLM runs out of
   capacity or the tunnel drops.
10. **The model emits bare URLs sometimes, your renderer must auto-link
    them.** Tool results from `paper_search` and `semantic_scholar_search`
    contain a `download_url` field for papers in the local corpus, and
    persona prompts instruct the model to render them as `[Download PDF](url)`.
    Qwen3 frequently ignores that instruction and emits `**Download URL:**
    https://...` instead. Bare URLs are valid markdown content; making
    them clickable is the renderer's job. Enable a markdown auto-linkify
    plugin (e.g. `remark-gfm` for `react-markdown`) so both `[text](url)`
    AND bare URLs become clickable. See `docs/FRONTEND-TASKS.md` for
    the full handoff. Don't try to fix this on the backend, three rounds
    of prompt strengthening did not move the needle.

## 11. Quick curl recipes

```bash
# Health + status
curl -s http://127.0.0.1:8080/api/status | python3 -m json.tool

# List personas
curl -s -H "X-Munin-Email: you@muninai.org" \
    http://127.0.0.1:8080/api/personas | python3 -m json.tool

# Send a chat message (streaming)
curl -N -H "X-Munin-Email: you@muninai.org" \
    -H "Content-Type: application/json" \
    -d '{"persona":"chat","messages":[{"role":"user","content":"hi"}],"rag":{"enabled":false}}' \
    http://127.0.0.1:8080/api/chat/completions

# Upload a document
curl -H "X-Munin-Email: you@muninai.org" \
    -F "file=@/path/to/paper.pdf" \
    http://127.0.0.1:8080/api/documents/upload

# List + delete
curl -s -H "X-Munin-Email: you@muninai.org" http://127.0.0.1:8080/api/documents
curl -X DELETE -H "X-Munin-Email: you@muninai.org" \
    http://127.0.0.1:8080/api/documents/doc_abc123
```

For a full end-to-end sanity run, see `scripts/smoke-test.sh` at the repo
root, 16 checks covering every endpoint in this doc.

## 12. Prometheus `/metrics`

The retrieval service exposes a Prometheus scrape endpoint at
`/metrics` on the same port as the rest of the API (8080). Caddy only
proxies `/api/*` and `/paper/*` externally, so this endpoint is
naturally cluster-internal. Add a scrape job pointing directly at the
hugin-side service if you want to ingest it.

Metric names follow the `munin_<subsystem>_<noun>` convention; counters
end in `_total`, histograms end in `_seconds`. The default
`prometheus_client` process / GC / FD metrics are also exported.

| Metric | Type | Labels | What it counts |
|---|---|---|---|
| `munin_vllm_request_total` | Counter | `purpose`, `outcome` | Every vLLM HTTP call. `outcome` is one of `success` / `transient_retry` (per retry attempt) / `transport_error` (exhausted) / `permanent` (non-retryable status). `purpose` is the call site: `chat_turn`, `chat_wrap_up`, `forced_clarification`, `forced_required`, `summary`, `title`, `agent_turn`, `agent_wrap_up`. |
| `munin_vllm_request_duration_seconds` | Histogram | `purpose` | Wall time of a successful call only (retries excluded). Streaming purposes record time-to-first-byte. |
| `munin_vllm_tokens_total` | Counter | `purpose`, `kind` | Token counts mirrored from `record_usage`. `kind` is `prompt` or `completion`. |
| `munin_mcp_tool_total` | Counter | `name`, `outcome` | Every `execute_mcp_tool` dispatch. `outcome` is `success` / `error` / `validation_error` / `unknown_tool`. |
| `munin_mcp_tool_duration_seconds` | Histogram | `name` | Wall time of a single tool dispatch (success and failure both observed). |
| `munin_chat_turns_total` | Counter | `persona`, `terminal_reason` | One increment per call to `stream_chat_completion`, regardless of success. `terminal_reason` is one of `done` / `max_turns` / `stream_error` / `cancelled` / `error`. |
| `munin_phantom_url_total` | Counter | `kind` | Phantom-URL audit hits in assistant content. `kind` is `artifact` or `paper`. |

The helpers in `retrieval/metrics.py` are the single import surface for
call sites; if you add a new vLLM call site, pass a fresh `purpose` tag
through `vllm_post_json` / `vllm_post_stream` so the metric labels stay
useful.
