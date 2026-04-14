# Munin Frontend Reference

Complete reference for the Munin chat frontend. Use this document when working on the backend (cluster retrieval service, API gateway) to ensure API contracts are met and responses match what the frontend expects.

## Stack

- React 19 + TypeScript (strict mode) + Tailwind CSS v4
- Vite builds to `static/chat/`
- No router library (manual `pushState`/`popstate`)
- PWA with service worker and installable manifest

## Architecture Overview

```
Browser (chat.muninai.org)
    |
    |-- Static files served by Caddy from static/chat/
    |-- /api/* proxied by Caddy to api-gateway (:8070)
    |       |
    |       |-- Gateway handles: API keys, rate limiting, usage tracking,
    |       |   announcements, and proxies remaining requests to cluster
    |       |
    |       +-- SSH tunnel to cluster retrieval API (:18080)
    |
    +-- auth.muninai.org/auth/me (user profile, CORS with credentials)
```

All `/api/*` requests go through Caddy's forward-auth first, which sets `X-Munin-Email` and `X-Munin-Name` headers. The gateway reads these headers to identify the user.

## File Structure

```
frontend/src/
    App.tsx                     -- Top-level orchestrator, state management
    main.tsx                    -- React root + service worker registration
    index.css                   -- Tailwind imports
    global.d.ts                 -- BeforeInstallPromptEvent type declaration
    components/
        Sidebar.tsx             -- Chat list, search, profile menu
        MessageList.tsx         -- Message rendering + streaming state
        ChatInput.tsx           -- Text input, persona selector, file upload
        TaskLog.tsx             -- Tool call + thinking display (expandable)
        PersonaSelector.tsx     -- Persona dropdown in input bar
        FeatherVortex.tsx       -- Animated loading indicator (wraps canvas)
        SleepingPage.tsx        -- Shown when vLLM is offline
        Settings.tsx            -- Profile, API keys, usage, admin announcements
    hooks/
        useChat.ts              -- SSE streaming, message state, conversation loading
        useStatus.ts            -- Cluster status polling (60s interval)
    lib/
        api.ts                  -- All fetch functions and TypeScript interfaces
        types.ts                -- Shared type definitions (Persona, Message, SSE, etc.)
        greetings.ts            -- Time-of-day greeting generator
```

---

## API Contract: What the Frontend Calls

Every endpoint below is called as `/api/...` which Caddy proxies to the gateway at `:8070`. The gateway either handles the request itself (keys, usage, announcements) or proxies it through the SSH tunnel to the cluster.

### Personas

**`GET /api/personas`**

Called on mount. Returns available chat personas.

```json
{
  "personas": [
    {
      "id": "chat",
      "name": "Meitner - Chat",
      "description": "General-purpose assistant",
      "icon_url": "/shared/meitner-chat-inverted.svg",
      "tags": ["general"],
      "capabilities": {"web_search": true, "paper_search": true},
      "prompt_suggestions": [
        {
          "title": "Summarize a paper",
          "subtitle": "Give me the key findings",
          "content": "Summarize the paper titled..."
        }
      ]
    }
  ],
  "default_persona": "chat"
}
```

The frontend uses `prompt_suggestions` to show clickable suggestion chips on the empty chat screen. Each suggestion's `content` is inserted into the textarea when clicked.

---

### Conversations

**`GET /api/chats?limit=50&offset=0&persona=chat&search=query`**

Called by the Sidebar to list conversations. All query params are optional.

```json
{
  "conversations": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440000",
      "title": "Polymer simulation methods",
      "persona": "chat",
      "created_at": "2026-04-13T10:00:00Z",
      "updated_at": "2026-04-13T10:05:00Z",
      "message_count": 4,
      "preview": "Can you explain the differences between..."
    }
  ],
  "total": 42
}
```

The Sidebar groups conversations by time (Today, Yesterday, This week, This month, Older) using `updated_at`. It also supports full-text search via the `search` param.

**`GET /api/chats/{id}`**

Load a full conversation with all messages.

```json
{
  "id": "550e8400-...",
  "title": "Polymer simulation methods",
  "persona": "chat",
  "created_at": "2026-04-13T10:00:00Z",
  "updated_at": "2026-04-13T10:05:00Z",
  "summary": "Discussion about MD vs MC simulation approaches...",
  "messages": [
    {
      "id": "msg-1",
      "role": "user",
      "content": "What are the differences between MD and MC?",
      "created_at": "2026-04-13T10:00:00Z"
    },
    {
      "id": "msg-2",
      "role": "assistant",
      "content": "Molecular dynamics (MD) and Monte Carlo (MC)...",
      "thinking": "The user is asking about simulation methods...",
      "tool_calls": [
        {
          "id": "tc-1",
          "name": "paper_search",
          "arguments": {"query": "molecular dynamics monte carlo comparison"},
          "result": {"papers": [...]},
          "duration_ms": 1200
        }
      ],
      "rag_context": {
        "sources_used": ["papers", "web"],
        "documents": [
          {
            "title": "A comparison of MD and MC methods",
            "source": "papers",
            "score": 0.92,
            "doi": "10.1234/example",
            "content": "..."
          }
        ]
      },
      "created_at": "2026-04-13T10:00:05Z"
    }
  ]
}
```

Important: The frontend stores `thinking`, `tool_calls`, and `rag_context` as optional fields on assistant messages. These are displayed in an expandable TaskLog component. If the backend doesn't include them, the UI simply doesn't show the expandable section.

**`PATCH /api/chats/{id}`** -- Rename conversation

```json
{"title": "New title"}
```

**`DELETE /api/chats/{id}`** -- Delete conversation

---

### Streaming Chat

**`POST /api/chat/completions`**

The main chat endpoint. Uses Server-Sent Events (SSE) for streaming.

**Request body:**

```json
{
  "persona": "chat",
  "conversation_id": "550e8400-..." | null,
  "messages": [
    {"role": "user", "content": "What is polymer crystallization?"}
  ],
  "rag": {"enabled": true, "sources": ["papers", "web"]},
  "stream": true
}
```

When `conversation_id` is null, the backend creates a new conversation. The backend sends back the conversation ID in the `conversation` SSE event.

**SSE event stream (in order):**

```
event: conversation
data: {"id": "550e8400-...", "title": "Polymer crystallization", "is_new": true, "ephemeral": false}

event: rag_context
data: {"sources_used": ["papers"], "documents": [{...}]}

event: thinking
data: {"content": "The user is asking about..."}

event: thinking
data: {"content": " crystallization mechanisms..."}

event: tool_call
data: {"id": "tc-1", "name": "paper_search", "arguments": {"query": "polymer crystallization"}}

event: tool_result
data: {"id": "tc-1", "name": "paper_search", "result": {"papers": [...]}, "duration_ms": 800}

event: token
data: {"content": "Polymer"}

event: token
data: {"content": " crystallization"}

event: token
data: {"content": " is..."}

event: done
data: {"usage": {"prompt_tokens": 1500, "completion_tokens": 300}, "finish_reason": "stop"}
```

**Other SSE events the frontend needs to handle** (triggered by specific features):

- `artifact_created` (§22) — fires when a model-written document (`create_artifact` tool) OR a sandbox file (`run_python`, `compile_latex`) is produced. Has a `source` discriminator: `"model_written"` routes to the side panel with version picker; `"sandbox_generated"` routes to an inline download chip / thumbnail (comes with `filename`, `size_bytes`, `external_url`).
- `artifact_updated` (§22) — fires when `update_artifact` successfully mutates a model-written row. Bumps the side panel's version picker. Never fires for sandbox artifacts (they're read-only).
- `clarification` (§14) — fires when the model calls `ask_clarification` on an ambiguous request. The turn is short-circuited: no `tool_result`, no wrap-up text, `done` follows immediately with `finish_reason: "clarification"`. Payload: `{tool_call_id, conversation_id, what_i_understood, questions: [{id, text, options, allow_custom}, ...]}`. Render as an inline multiple-choice card in the transcript. See FRONTEND-TASKS.md entry #10.
- `agent_start` / `agent_thinking` / `agent_tool_call` / `agent_tool_result` / `agent_done` — emitted mid-stream when the model invokes an agent via `invoke_agent`. Nested under the parent `tool_call(invoke_agent)` in the TaskLog.

**The authoritative SSE event catalogue** (with every field for every event) lives in `docs/BACKEND-API.md` §5. Prefer that table over this section when the two disagree; this section is a quick tour, the table is the contract.

Or on error:

```
event: error
data: {"message": "Rate limit exceeded"}
```

**How the frontend processes this stream:**

1. `conversation` event: sets `conversationId` state, updates URL to `/c/{id}`. A second `conversation` event may arrive later carrying the auto-generated `title` once the first assistant response is synthesised. `ephemeral: true` on this event means the id has an `ephemeral-` prefix and was never persisted; the auto-title follow-up is skipped.
2. `rag_context` event: stored and passed to streaming state (currently not rendered but preserved)
3. `thinking` events: accumulated into a string, shown in TaskLog as "Reasoning..."
4. `tool_call` events: each pushed to an array, shown in TaskLog with icon and arguments
5. `tool_result` events: matched by `id` to update the corresponding tool_call entry
6. `artifact_created` / `artifact_updated` events: routed by `source` field (see FRONTEND-TASKS.md entry #9). `model_written` → side panel; `sandbox_generated` → inline chip.
7. `clarification` event: render an inline multiple-choice card on the in-flight assistant message; the turn ends on the next `done`. See FRONTEND-TASKS.md entry #10.
8. `agent_*` events: nested rendering under the parent `tool_call(invoke_agent)` in the TaskLog.
9. `token` events: accumulated into the response content, shown with a blinking cursor
10. `done` event: finalizes the assistant message, adds it to messages array, resets streaming state. `finish_reason: "clarification"` indicates the turn was short-circuited by an `ask_clarification` card.
11. `error` event: displayed as a red banner at the top of the chat

**SSE format details:** The frontend parser expects:
- Lines starting with `event: ` set the event type
- Lines starting with `data: ` contain the JSON payload
- Empty lines reset the event type
- The `data:` line must be valid JSON

**Tool calls the frontend recognizes (for icons):**

The canonical list of MCP tool names the backend advertises to the
model is fetched from `GET /mcp/tools` and also hard-coded in
`retrieval/mcp/schemas.py::MCP_TOOLS` - **treat that as the source of
truth** rather than this table, which is maintained by hand and will
drift. New tools added since the original table include
`deep_research`, `read_paper`, `compare_papers`, `s2_get_citations` /
`s2_get_references`, `search_user_docs`, `view_attachment`,
`transcribe_equation`, `faq`, `ask_clarification`, `remember` /
`forget` / `recall`, `create_artifact` / `read_artifact` /
`update_artifact` / `list_artifacts` / `save_artifact_to_documents`,
`list_projects` / `get_current_project`, `run_python` /
`sandbox_reset`, `compile_latex`, `calculate`, `export_citations`,
`invoke_agent`, `search_past_conversations`, `check_papers_availability`,
`get_paper_pdf`. The legacy icon assignments below still apply to the
tools they cover; everything else falls through to the gear icon.

| Tool name | Icon |
|-----------|------|
| `paper_search` | Books |
| `semantic_scholar_search` | Books |
| `paper_lookup` | Books |
| `read_paper` | Books |
| `compare_papers` | Books |
| `get_citations` / `s2_get_citations` | Link |
| `get_references` / `s2_get_references` | Link |
| `get_author_papers` | Link |
| `web_search` | Globe |
| `web_fetch` | Globe |
| `llm_summarize` | Memo |
| `get_paper_pdf` | Page |
| `run_python` | Terminal |
| `compile_latex` | Page |
| `ask_clarification` | Question mark (or handled specially - see FRONTEND-TASKS #10) |
| `calculate` | Calculator |
| `deep_research` | Magnifier |
| `invoke_agent` | Robot |
| (any other) | Gear |

Tool calls also display `arguments.query` if present (e.g., `paper search: "polymer crystallization"`).

---

### Documents (User File Upload)

**`POST /api/documents/upload`**

Multipart form data upload. The frontend sends this via XHR (not fetch) for upload progress tracking.

**Form fields:**
- `file`: The uploaded file (PDF, TXT, MD, DOCX, or images: PNG, JPG, JPEG, WEBP)
- `conversation_id`: (optional) Associates the upload with a conversation

**Frontend enforces:** 50 MB max file size (client-side check before upload).

**Expected response:**

```json
{
  "document_id": "doc_abc123",
  "filename": "my_draft.pdf",
  "chunks": 24,
  "status": "embedded",
  "upload_time": "2026-04-13T14:30:00Z"
}
```

The `status` field can be `"embedded"` (ready) or `"processing"` (async embedding in progress). The frontend currently does not poll for completion on `"processing"` status; it just shows what it receives.

After a successful upload, the frontend shows a confirmation banner: "my_draft.pdf (24 chunks embedded)" which auto-dismisses after 5 seconds.

**Error response format:**

```json
{"error": {"message": "Unsupported file type"}}
```

**`GET /api/documents?conversation_id=...`**

List user's uploaded documents. The `conversation_id` query param is optional.

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

**`DELETE /api/documents/{document_id}`**

Delete a document and its embeddings.

---

### System Status

**`GET /api/status`**

Polled every 60 seconds by `useStatus` hook.

```json
{
  "vllm": {
    "status": "running",
    "model": "qwen3.5-35b-a3b",
    "next_start": "2026-04-14T06:00:00Z"
  },
  "services": {
    "retrieval": "ok",
    "embedding": "ok"
  },
  "timestamp": "2026-04-13T12:00:00Z"
}
```

**Important:** When `vllm.status === "offline"`, the frontend shows the SleepingPage component instead of the chat interface. This page displays a countdown to `next_start` if provided. When the status API itself is unreachable (network error), the frontend treats it as `"offline"`.

The `services` field is currently unused by the frontend but reserved for future health indicators.

---

### API Keys (handled by gateway, not cluster)

**`GET /api/keys`**

```json
{
  "keys": [
    {
      "id": "key-uuid",
      "key_prefix": "sk-munin-a1b2c3...",
      "name": "My notebook",
      "created_at": "2026-04-10T10:00:00Z",
      "last_used_at": "2026-04-13T08:00:00Z",
      "revoked": false
    }
  ]
}
```

**`POST /api/keys`**

```json
{"name": "My notebook"}
```

Response:

```json
{
  "id": "key-uuid",
  "key": "sk-munin-a1b2c3d4e5f6...",
  "key_prefix": "sk-munin-a1b2c3...",
  "name": "My notebook",
  "created_at": "2026-04-10T10:00:00Z",
  "warning": "Save this key now. You won't be able to see it again."
}
```

The full `key` is only returned once at creation. The frontend shows it in a highlighted banner with a copy button.

**`DELETE /api/keys/{id}`** -- Revoke a key

---

### Usage Stats (handled by gateway)

**`GET /api/usage/me`**

```json
{
  "current_month": {
    "tokens_used": 150000,
    "tokens_limit": 2000000,
    "tokens_remaining": 1850000,
    "requests": 42,
    "tools_used": {"paper_search": 15, "web_search": 8}
  },
  "api_keys": [
    {
      "key_prefix": "sk-munin-a1b2...",
      "name": "My notebook",
      "tokens_this_month": 5000,
      "last_used": "2026-04-13T08:00:00Z"
    }
  ],
  "is_admin": false
}
```

The frontend displays:
- A usage progress bar (tokens used / limit) with color coding (green < 50%, yellow 50-80%, red > 80%)
- Rate limit info text: "1 concurrent request | 10 requests/min | 2M tokens/month"
- Per-key usage breakdown

The `is_admin` flag determines whether the admin announcement section is shown in Settings.

---

### Announcements (handled by gateway)

**`GET /api/announcement`**

```json
{
  "announcement": {
    "message": "Cluster maintenance scheduled for tomorrow 6 AM",
    "level": "warning",
    "updated_at": "2026-04-13T10:00:00Z"
  }
}
```

Or `{"announcement": null}` if no active announcement. The frontend shows a dismissible banner at the top of the chat with level-based coloring:
- `info`: blue (accent color)
- `warning`: yellow
- `error`: red

**`PUT /api/announcement`** (admin only)

```json
{"message": "Cluster will be down tomorrow", "level": "warning"}
```

**`DELETE /api/announcement`** (admin only)

---

### User Profile (handled by munin-auth, NOT the gateway)

These go directly to `auth.muninai.org`, not through `/api/`.

**`GET https://auth.muninai.org/auth/me`** (with `credentials: 'include'`)

```json
{
  "email": "user@example.com",
  "name": "Display Name",
  "full_name": "Full Name",
  "nickname": "Nick",
  "avatar": "data:image/png;base64,..."
}
```

**`PATCH https://auth.muninai.org/auth/me`** (with `credentials: 'include'`)

```json
{"full_name": "New Name", "nickname": "NewNick", "avatar": "data:image/..."}
```

All fields are optional. Returns the updated profile in the same format as GET.

---

## URL Routing

The frontend manages its own routing via the History API:

| URL | Behavior |
|-----|----------|
| `/` | Empty chat (new conversation) |
| `/c/{conversation_id}` | Load and display a specific conversation |
| `/?persona=code` | Start with a specific persona pre-selected (cleaned from URL after reading) |

Back/forward browser navigation is supported via `popstate` event listener.

---

## Authentication Flow

1. User visits `chat.muninai.org`
2. Caddy's `forward_auth` calls `munin-auth` at `/auth/check`
3. If no valid session cookie: 401 -> redirect to `auth.muninai.org/login?redirect=https://chat.muninai.org`
4. User enters email -> OTP sent -> user enters code -> session cookie set (`.muninai.org` domain, 30 days, httpOnly, secure, sameSite=lax)
5. Redirect back to original URL
6. On subsequent requests, Caddy's forward-auth passes and sets `X-Munin-Email` + `X-Munin-Name` headers

The frontend itself never handles login. It just calls `/auth/me` to get user info and uses the cookie implicitly for all `/api/` requests (same domain, so cookies are sent automatically).

---

## Component Behavior Details

### Sidebar
- Fetches `GET /api/chats?limit=50` on mount and whenever `refreshKey` changes
- Refresh is triggered when streaming completes (new messages)
- Supports search via `GET /api/chats?search=query`
- Right-click / long-press on a conversation shows rename and delete options
- Profile menu at bottom: Settings, Learn more (docs link), language selector (stub)

### ChatInput
- Textarea auto-resizes up to 200px height
- Enter sends, Shift+Enter adds newline
- `+` button opens attach menu with "Upload file" (.pdf, .txt, .md, .docx) and "Upload image" (.png, .jpg, .jpeg, .webp)
- Upload uses XHR for progress tracking, shows progress banner above input
- Persona selector dropdown at bottom-right of input
- Stop button (red circle) replaces Send button during streaming

### MessageList
- User messages: right-aligned bubbles with border
- Assistant messages: left-aligned with "M" avatar circle
- Streaming content shows blinking cursor
- During streaming without content: shows FeatherVortex loading animation
- Auto-scrolls to bottom on new content

### TaskLog (expandable section above assistant messages)
- Shows "Reasoning..." (thinking) with expandable content
- Shows tool calls with icons, query text, result summaries, and duration
- Each row is individually expandable to see full arguments and results
- Result summaries: "N papers" for paper_search, "N results" for generic, "N words" for content

### Settings
- Three sections: Profile, API Keys, Admin (if `is_admin`)
- Profile: full name, nickname, avatar upload (base64, 150KB limit)
- API Keys: usage bar, create key form, key list with revoke, quick-start Python snippet
- Admin: announcement textarea, level selector (info/warning/error), save/clear buttons

---

## Gateway Routing

The API gateway at `:8070` handles these endpoints directly:

**Handled by gateway (SQLite):**
- `GET/POST/DELETE /api/keys` and `/api/keys/{id}`
- `GET /api/usage/me`
- `GET/PUT/DELETE /api/announcement`

**Proxied to cluster via SSH tunnel:**
- `GET /api/personas`
- `GET /api/chats`, `GET/PATCH/DELETE /api/chats/{id}`
- `POST /api/chat/completions`
- `GET /api/status`
- `POST /api/documents/upload`
- `GET /api/documents`
- `DELETE /api/documents/{id}`

The gateway adds `X-Munin-Email` to proxied requests so the cluster knows which user is making the request.

**Critical:** In the gateway's FastAPI app, all specific route handlers (keys, usage, announcement) MUST be defined before the catch-all `/{path:path}` proxy route. FastAPI matches routes in definition order, so the catch-all would intercept everything if placed first.

---

## Rate Limits

The gateway enforces these limits (configurable in `config/quotas.yml`):

| Limit | Default |
|-------|---------|
| Tokens per month | 2,000,000 |
| Requests per minute | 10 |
| Concurrent requests | 1 |

Rate limit errors are returned as:

```json
{"error": {"message": "Rate limit exceeded: 10 requests per minute"}}
```

For the streaming endpoint, rate limit errors are sent as SSE error events.

---

## Error Handling Patterns

The frontend handles errors at two levels:

1. **Global error banner** (red, top of chat): Set by `useChat` hook on stream errors or conversation load failures. Cleared on next `sendMessage` call.

2. **Inline error responses**: API functions throw on non-200 responses. Components catch and display locally (e.g., Settings shows API key creation errors inline).

**Expected error response format** for all endpoints:

```json
{"error": {"message": "Human-readable error description"}}
```

Or for simple cases:

```json
{"error": "Human-readable error description"}
```

The frontend checks `err.error?.message` first, then falls back to `HTTP {status}`.

---

## PWA

- Service worker (`public/sw.js`): cache-first for static assets, network-first for `/api/`
- Manifest: standalone display, dark theme (#0f1419), 192px and 512px icons
- Install banner shown on mobile (Android: native prompt, iOS: manual Share hint)
- Dismissed state persisted to `localStorage` key `munin_pwa_dismissed`

---

## CSS / Theming

The frontend uses Tailwind CSS v4 with CSS custom properties defined in `static/shared/style.css`. Key color tokens used throughout:

| Token | Usage |
|-------|-------|
| `--bg-primary` | Main background |
| `--bg-secondary` | Cards, sidebar, input bubble |
| `--bg-tertiary` | Hover states, active items |
| `--text-primary` | Main text |
| `--text-secondary` | Muted text, labels |
| `--accent` | Links, active states, highlights |
| `--accent-hover` | Button hover states |
| `--border` | All borders |
| `--error` | Error banners, stop button |
| `--warning` | Warning announcements |

These are referenced in Tailwind classes like `bg-bg-primary`, `text-accent`, `border-border`.