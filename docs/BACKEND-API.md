# Munin Backend — API Reference

Hand-off document for the frontend / gateway repo. Describes what the
`munin-retrieval` FastAPI service actually delivers today (Streams 1–5 are
all live as of the first deploy on `hugin`). This is the counterpart to
`FRONTEND-REFERENCE.md` — the frontend expresses the *intent*, this file
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
`/citations/{doi}`, `/mcp/*`, `/deepresearch/*`, etc. routes — those are
**not** part of the frontend contract. Ignore them for the UI.

## 2. Authentication

Forward-auth pattern: Caddy on the VPS validates the session and attaches
headers to the proxied request.

| Header | Purpose | Required |
|---|---|---|
| `X-Munin-Email` | user identity (primary) | Yes |
| `X-Authentik-Email` | transitional fallback | No (accepted if `X-Munin-Email` missing) |
| `X-Munin-Name` | display name | No, not consumed by backend today |

Every `/api/*` route except `/api/status` and `/api/personas/{id}/icon`
requires one of the two email headers; missing → **401** with
`{"error": {"message": "Missing authentication header"}}`.

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
  "vllm": {
    "status": "running",
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
  "timestamp": "2026-04-13T16:30:00Z"
}
```

Notes:

- `vllm.status` is one of `running` / `offline` / `starting`. When `offline`
  the frontend should render `SleepingPage`.
- `vllm.next_start` is **only present** when `status === "offline"`. It's
  computed as the next 6 AM local time from the cluster's clock.
- `services.embedding` reports whether SPECTER + BGE have already been
  loaded. On a fresh boot it will be `"unavailable"` until the first call
  that needs them (first RAG call or first document upload). This is not a
  bug — it means "not loaded yet", not "broken".
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
| `persona` | string | — | filter to one persona id |
| `search` | string | — | SQLite FTS5 over all `messages.content` |
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
      "preview": "Can you explain the differences between..."
    }
  ],
  "total": 42
}
```

Notes:

- Ordered by `pinned DESC, updated_at DESC` - pinned conversations float
  to the top of the listing regardless of their last activity.
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
  back into Python objects — frontend receives them as structured JSON,
  not strings.
- `thinking` is the accumulated `reasoning_content` from the vLLM stream.
- Ownership is enforced by `WHERE user_email = ?`; we don't leak 403 vs 404.

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

### 4.8 `POST /api/chat/completions` — SSE streaming

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

### 4.9 `POST /api/documents/upload`

Multipart form upload.

**Form fields**:

- `file` (required) — PDF / TXT / MD / DOCX / PNG / JPG / JPEG / WEBP
- `conversation_id` (optional) — associates the upload with a conversation
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
  - `"embedded"` — text was extracted, chunked, embedded, and upserted into
    Qdrant. Ready for RAG.
  - `"stored"` — file is on disk but not embedded. This is the state for
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

- `conversation_id` (optional) — filter to docs uploaded in that chat

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

1. Qdrant scroll — authoritative for embedded documents (one row per
   `document_id`, with `total_chunks` and `upload_time` from the payload).
2. Disk scan — adds any file the user has under their user dir that isn't
   in Qdrant (images, zero-chunk docs). Only included when
   `conversation_id` is not specified.

### 4.11 `DELETE /api/documents/{id}`

Removes both the on-disk directory and every Qdrant point whose payload
matches `{user_email, document_id}`.

**Response (200)**: `{"deleted": true}`. **404** only if neither a
directory nor any Qdrant points existed.

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
(created by `run_python`, stored on disk in the sandbox container,
referenced via `external_url`). Rows carry a `source` field
(`'model_written'` or `'sandbox_generated'`) to distinguish them.

Model-written artifacts are versioned and editable; sandbox-generated
artifacts are always `latest_version: 1` and read-only (`update_artifact`
returns an error on a sandbox row — regenerate via `run_python` instead).
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

**SSE events** on `/api/chat/completions` when the model calls
create/update tools:

- `artifact_created`: `{id, title, content_type, language, version, conversation_id, tool_call_id}`
- `artifact_updated`: `{id, title, version, change_summary, created_by, conversation_id, tool_call_id}`

Both are separate from the existing `artifact` event used by §2/§3
sandbox outputs so the frontend can route them to the side panel
rather than inline transcript rendering.

### 4.16 Project routes (§21)

Projects are top-level workspaces that scope conversations and
documents together under a single set of instructions. A conversation
may live inside a project (filed) or outside it (unfiled / in the
default bucket); deleting a project leaves its conversations and
documents in the Unfiled bucket - **no cascading delete**.

**`POST /api/projects`** — create a project.

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

**`GET /api/projects`** — list the user's projects.

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

**`GET /api/projects/{id}`** — single project with inline counts.
Same shape as above plus `document_count` (sourced from Qdrant).
**404** if the id is unknown or owned by another user.

**`PATCH /api/projects/{id}`** — update any subset of `name`,
`description`, `instructions`, `default_persona`, `archived`. Returns
the updated project. **400** on cap violation, **404** on unknown id.

**`DELETE /api/projects/{id}`** — hard-delete the project row. Unfiles
its conversations (`project_id` → NULL) and its docs (Qdrant
`project_id` payload cleared). Responds **200** with
`{"deleted": true}`. **Conversations and docs are preserved** in the
Unfiled bucket - the user can re-file them later or delete them
individually.

**`POST /api/projects/{id}/conversations/{conversation_id}`** — file a
conversation into a project. **404** if either id is unknown or owned
by another user.

**`DELETE /api/projects/{id}/conversations/{conversation_id}`** —
unfile a conversation back to the default bucket. The URL includes the
project id for symmetry with the file route, but the backend does not
verify it matches the current filing.

**`GET /api/chats?project_id=<pid>`** — list conversations inside a
specific project. Pass the literal `__unfiled__` sentinel to list
only the conversations that have no project.

**`POST /api/documents/upload`** — accepts an optional `project_id`
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

Serves an artifact (plot, file, generated document) produced by the
`run_python` sandbox tool inside the given conversation. The actual
file lives in the sandbox sidecar; this endpoint proxies the bytes so
the host never needs to expose the sandbox container's port.

**Auth + ownership**: requires `X-Munin-Email` and the caller must own
the conversation. A user fetching another user's artifact gets **404**
(we deliberately do not distinguish 403 from 404 here so artifact ids
cannot be probed).

**Path params**:

- `conversation_id` - the chat the artifact was produced in.
- `artifact_id` - the id from the `artifact` SSE event (also present
  inside the `run_python` tool result's `artifacts` array).

**Response (200)**: the raw file bytes with `Content-Type` set to the
artifact's media type (`image/png` for plots, etc.) and a
`Content-Disposition: inline` header so browsers can render images
directly. **404** for unknown ids or cross-user access. **502** if the
sandbox sidecar is unreachable.

`display_url` on the `artifact` SSE event always points at this route.
The frontend can drop it straight into an `<img>` `src` for images or
into a download anchor for non-image artifacts.

## 5. SSE event catalogue for `/api/chat/completions`

All events follow the SSE framing:

```
event: <name>
data: <minified json>

```

| Event | Payload | Emitted when |
|---|---|---|
| `conversation` | `{"id": "...", "title": "..." \| null, "is_new": true \| false, "ephemeral": true \| false}` | At stream start; again after auto-title for new conversations. `ephemeral: true` means the id has the `ephemeral-` prefix and was never persisted; the auto-title follow-up event is skipped |
| `rag_context` | `{"sources_used": ["papers","web"], "documents": [{"title":"...", "source":"...", "score":0.0, "doi":"...", "content":"..."}, ...]}` | After RAG retrieval, before any generation, only if `rag.enabled: true` and at least one source returned hits |
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
| `done` | `{"usage": {"prompt_tokens": N, "completion_tokens": N}, "finish_reason": "stop"}` | Always the last event on success |
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
or as a single tool entry — both views are supported by the stream.

## 6. Persona context injection

Every chat completion assembles its system prompt as:

```
{persona.params.system}

{agent summaries — one bullet per registered agent, with invocation hint}
```

The agent summaries are injected automatically so the main model knows it
can delegate to `research_orchestrator`, `code_checker`, or `writing_agent`.
The summaries come from `config/agents.yml` and re-load on every service
restart. Frontend does not need to touch this.

## 7. Context budgeting & summarization

- Budget: `MAX_CONTEXT = 60000`, `GENERATION_RESERVE = 8000` (overridable
  via env). Tokens are estimated heuristically at ~4 chars/token.
- If `system_prompt + summary + history + new_message` fits, sent as-is.
- If not: keep the newest messages that fit in half the history budget,
  summarize everything older via a non-streaming vLLM call, persist the
  summary + `summary_through_index` to the `conversations` row, rebuild.
- Fallback: if vLLM is unreachable mid-summarization, hard-truncate the
  history and proceed. No 500 to the user.

Frontend implication: **the `summary` field on a conversation row will
start populated after long chats.** Don't render it as part of the message
list — treat it as background metadata.

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
| `search_user_docs` | Semantic search over the current user's uploaded docs (auto-filters by `X-Munin-Email` via contextvar — never pass a user id) |
| `invoke_agent` | Delegate to an agent workflow. Input: `{"agent": "...", "query": "..."}` |

Agents themselves (`research_orchestrator`, `code_checker`, `writing_agent`)
are defined in `config/agents.yml` with their own tool allowlists, iteration
limits, and wall-clock timeouts. Agents cannot invoke each other.

## 10. Known gotchas for frontend devs

1. **`services.embedding: "unavailable"` is normal on a cold start.** It
   flips to `"ok"` after the first retrieval or upload that loads the model.
   Don't show a red dot — show "warming up" or hide the field.
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
   status from the backend today — either `"embedded"` or `"stored"`.
   If you see `"processing"` in the frontend code, that's a leftover from
   the original FRONTEND-REFERENCE draft; the backend never emits it.
7. **`persona` filter on `/api/chats` is exact-match** on the id. Passing
   `persona=Chat` won't find conversations created with `persona: "chat"`.
8. **FTS search is not fuzzy.** `polymer` matches; `polym` doesn't. Use
   FTS5 prefix syntax (`polym*`) if you want prefix matching.
9. **Rate limits / quotas are on the VPS gateway**, not this service.
   `/api/chat/completions` will happily stream until vLLM runs out of
   capacity or the tunnel drops.
10. **The model emits bare URLs sometimes — your renderer must auto-link
    them.** Tool results from `paper_search` and `semantic_scholar_search`
    contain a `download_url` field for papers in the local corpus, and
    persona prompts instruct the model to render them as `[Download PDF](url)`.
    Qwen3 frequently ignores that instruction and emits `**Download URL:**
    https://...` instead. Bare URLs are valid markdown content; making
    them clickable is the renderer's job. Enable a markdown auto-linkify
    plugin (e.g. `remark-gfm` for `react-markdown`) so both `[text](url)`
    AND bare URLs become clickable. See `docs/FRONTEND-TASKS.md` for
    the full handoff. Don't try to fix this on the backend — three rounds
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
root — 16 checks covering every endpoint in this doc.
