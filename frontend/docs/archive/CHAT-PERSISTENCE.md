# Chat Persistence & Context Memory

## Overview

Chat history is stored on the cluster backend (retrieval service). Users can browse, continue, and search their past conversations. When continuing a chat or starting a new one, the backend injects a hybrid context — a summary of the conversation so far plus the last few messages verbatim — so the model has memory without overflowing the context window.

---

## 1. Storage

### Database

SQLite on the cluster, stored at `/opt/munin/data/chats.db`. Mounted as a Docker volume for the retrieval service container. SQLite is sufficient for the user base (department-scale, not thousands of concurrent writers).

### Schema

```sql
CREATE TABLE conversations (
    id TEXT PRIMARY KEY,                -- UUID
    user_email TEXT NOT NULL,           -- from X-Munin-Email header
    title TEXT,                         -- auto-generated or user-set
    persona TEXT NOT NULL,              -- persona ID used (chat, code, research)
    created_at TEXT NOT NULL,           -- ISO 8601
    updated_at TEXT NOT NULL,           -- ISO 8601, updated on every new message
    summary TEXT,                       -- LLM-generated rolling summary
    summary_through_index INTEGER       -- message index up to which summary covers
);

CREATE TABLE messages (
    id TEXT PRIMARY KEY,                -- UUID
    conversation_id TEXT NOT NULL,      -- FK to conversations
    index_in_conversation INTEGER NOT NULL, -- 0-based sequential index
    role TEXT NOT NULL,                 -- "user", "assistant", "system"
    content TEXT NOT NULL,              -- message text
    thinking TEXT,                      -- model reasoning (from <think> tags), nullable
    tool_calls TEXT,                    -- JSON array of tool calls made, nullable
    rag_context TEXT,                   -- JSON of RAG sources used, nullable
    created_at TEXT NOT NULL,           -- ISO 8601
    token_count INTEGER,               -- approximate token count of this message
    FOREIGN KEY (conversation_id) REFERENCES conversations(id)
);

CREATE INDEX idx_messages_conversation ON messages(conversation_id, index_in_conversation);
CREATE INDEX idx_conversations_user ON conversations(user_email, updated_at DESC);
```

### Conversation Title

Auto-generated from the first user message. The backend takes the first user message and either:
- Truncates to the first 60 characters if it's short enough, or
- Uses the LLM to generate a short title (5–8 words) from the first exchange, done asynchronously after the first response completes

The user can rename a conversation via the API.

---

## 2. API Endpoints

All endpoints filter by `X-Munin-Email` header — users only see their own chats.

### `GET /api/chats`

List the user's conversations, most recent first.

**Query params:**
- `limit` (int, default 20, max 100)
- `offset` (int, default 0)
- `persona` (string, optional — filter by persona)
- `search` (string, optional — full-text search across message content)

**Response:**
```json
{
  "conversations": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440000",
      "title": "Lipid raft dynamics in cryo-EM",
      "persona": "chat",
      "created_at": "2026-04-11T10:30:00Z",
      "updated_at": "2026-04-11T11:15:00Z",
      "message_count": 12,
      "preview": "What are the latest findings on lipid raft..."
    }
  ],
  "total": 47,
  "limit": 20,
  "offset": 0
}
```

### `GET /api/chats/{id}`

Load a full conversation with all messages.

**Response:**
```json
{
  "id": "550e8400-...",
  "title": "Lipid raft dynamics in cryo-EM",
  "persona": "chat",
  "created_at": "2026-04-11T10:30:00Z",
  "updated_at": "2026-04-11T11:15:00Z",
  "summary": "Discussion about lipid raft research...",
  "messages": [
    {
      "id": "msg-001",
      "role": "user",
      "content": "What are the latest findings on lipid rafts?",
      "created_at": "2026-04-11T10:30:00Z"
    },
    {
      "id": "msg-002",
      "role": "assistant",
      "content": "Recent research on lipid rafts has...",
      "thinking": "The user is asking about lipid rafts...",
      "tool_calls": [
        {
          "name": "paper_search",
          "arguments": {"query": "lipid raft dynamics", "top_k": 5},
          "result": {"papers": [...]},
          "duration_ms": 340
        }
      ],
      "rag_context": {
        "sources_used": ["papers"],
        "documents": [...]
      },
      "created_at": "2026-04-11T10:30:12Z"
    }
  ]
}
```

### `DELETE /api/chats/{id}`

Delete a conversation and all its messages.

**Response:** `{"deleted": true}`

### `PATCH /api/chats/{id}`

Update conversation metadata (title only for now).

**Request:**
```json
{"title": "My renamed conversation"}
```

**Response:** Updated conversation object (without messages).

### `POST /api/chat/completions` (updated)

The existing chat completions endpoint (from API-CONTRACT.md) gains a `conversation_id` field:

**Request:**
```json
{
  "persona": "chat",
  "conversation_id": "550e8400-...",
  "messages": [
    {"role": "user", "content": "What about cholesterol's role specifically?"}
  ],
  "rag": {"enabled": true, "sources": ["papers"]},
  "stream": true
}
```

**New behavior:**
- If `conversation_id` is provided: this is a continuation. The backend loads the summary + last messages (see section 3) and prepends them to the context.
- If `conversation_id` is null/omitted: this is a new conversation. The backend creates a new conversation record after the first exchange completes.
- The response SSE stream gains a new event:

```
event: conversation
data: {"id": "550e8400-...", "title": "Lipid raft dynamics", "is_new": true}
```

This fires early in the stream so the frontend can update the URL/sidebar immediately.

**After the response completes**, the backend:
1. Stores both the user message and the assistant response (with thinking, tool_calls, rag_context) in the messages table
2. Updates `conversation.updated_at`
3. Checks if a summary update is needed (see section 3)

---

## 3. Context Memory (Hybrid Summary + Recent Messages)

### The Problem

The vLLM model has a finite context window (e.g., 64k tokens for Qwen3.5-35B). A long conversation can easily exceed this. Stuffing the entire chat history into every request wastes tokens and eventually fails.

### The Solution

Maintain a **rolling summary** of the conversation plus the **last N messages** verbatim. On each new request, the backend assembles the context as:

```
[system prompt (persona)]
[summary of conversation so far]
[last N messages verbatim]
[new user message]
```

### Summary Generation

**When:** After every 10 messages (configurable via `SUMMARY_INTERVAL` env var), or when the total token count of unsummarized messages exceeds 4,000 tokens — whichever comes first.

**How:** The backend calls the local vLLM instance with a summarization prompt:

```
Summarize this conversation between a user and an assistant.
Preserve: key facts discussed, decisions made, questions answered,
specific papers/DOIs/results mentioned, and the current topic thread.
Be concise but don't lose important details.

Previous summary (if any):
{existing_summary}

New messages to incorporate:
{messages since last summary}
```

**The summary is incremental.** Each time, the existing summary is included with the new messages, and the LLM produces an updated summary. This keeps the summary current without re-reading the entire history.

**Storage:** The summary is stored on the `conversations` table. `summary_through_index` tracks which messages the summary covers, so the backend knows which messages are "post-summary" (and must be included verbatim).

### Context Assembly

When handling a `POST /api/chat/completions` with a `conversation_id`:

```python
def assemble_context(conversation, new_user_message, persona):
    messages = []

    # 1. Persona system prompt
    messages.append({"role": "system", "content": persona.system_prompt})

    # 2. Summary (if exists)
    if conversation.summary:
        messages.append({
            "role": "system",
            "content": f"Summary of conversation so far:\n{conversation.summary}"
        })

    # 3. Last N messages after the summary
    recent = get_messages_after_index(
        conversation.id,
        conversation.summary_through_index or -1
    )
    # Keep last 10 messages or last ~3000 tokens, whichever is smaller
    recent = trim_to_fit(recent, max_messages=10, max_tokens=3000)
    for msg in recent:
        messages.append({"role": msg.role, "content": msg.content})

    # 4. New user message
    messages.append({"role": "user", "content": new_user_message})

    return messages
```

### Token Budget

| Component | Approximate tokens | Notes |
|-----------|-------------------|-------|
| System prompt (persona) | 500–800 | Fixed per persona |
| Summary | 300–600 | Grows slowly, capped by summarization prompt |
| RAG context (if enabled) | 1,000–3,000 | Depends on retrieval results |
| Recent messages (last 10) | 1,000–3,000 | Trimmed to fit |
| New user message | variable | |
| **Total context budget** | **~8,000** | Leaves 56k+ tokens for generation |

The budget is conservative — plenty of room for the model to generate long responses with tool use.

### Edge Cases

- **First message in conversation:** No summary, no recent messages. Just persona + RAG + user message.
- **Short conversation (<10 messages):** No summary generated yet. All messages included verbatim.
- **vLLM offline when summary is due:** Skip summarization, mark as pending. Retry on next message when vLLM is available.
- **User switches persona mid-conversation:** The conversation records which persona was used. The summary carries forward. The system prompt changes to the new persona. This works fine — the summary provides continuity.

---

## 4. Frontend Integration

### Chat Sidebar

The chat frontend shows a sidebar (or panel) listing past conversations:

```
┌─ Conversations ──────────────┐
│ 🔍 Search chats...           │
│                              │
│ Today                        │
│  Lipid raft dynamics         │
│  SLURM job script help       │
│                              │
│ Yesterday                    │
│  Cryo-EM paper analysis      │
│  Python plotting question    │
│                              │
│ This week                    │
│  Deep research: membrane...  │
│  Grant proposal draft        │
│                              │
│ + New chat                   │
└──────────────────────────────┘
```

- Grouped by time (today, yesterday, this week, this month, older)
- Click to load and continue
- Search across all past chats (full-text via the API)
- Current chat highlighted
- "New chat" button starts a fresh conversation

### URL Routing

- `chat.muninai.org/` — new chat (or most recent)
- `chat.muninai.org/c/{conversation_id}` — specific conversation

### Continuity UX

When the user opens an old chat, they see the full message history (loaded from the API) including the task execution log for each assistant message (thinking, tool calls — all stored and re-rendered). They can type a new message to continue, and the backend handles context assembly transparently.

---

## 5. Data Lifecycle

### Retention

No automatic deletion. Chats persist indefinitely. Users can delete individual conversations via the API (and the frontend should expose a delete button).

### Backup

The SQLite database at `/opt/munin/data/chats.db` is included in the regular cluster backup. Since it's a single file, it's trivial to back up:

```bash
sqlite3 /opt/munin/data/chats.db ".backup /opt/munin/backups/chats-$(date +%Y%m%d).db"
```

### Migration from Open WebUI

Open WebUI stored chats in its own SQLite database. If users want their old chat history migrated, a one-time script can read from the Open WebUI database and insert into the new schema. This is optional and low priority — most users won't need it.

---

## 6. Backend Implementation Notes

### New Dependencies

- No new Python packages needed. SQLite is in the standard library. `aiosqlite` is recommended for async access in FastAPI — add to `retrieval/requirements.txt`.

### New Files

- `retrieval/chat_store.py` — database layer (create tables, CRUD operations, summary management)
- `retrieval/chat_routes.py` — FastAPI route handlers for `/api/chats/*`

### Docker Volume

Add to `docker/docker-compose.yml` for the retrieval service:

```yaml
volumes:
  - /opt/munin/data:/data
```

The chats database lives at `/data/chats.db` inside the container (mapped to `/opt/munin/data/chats.db` on the host).

### Summary Generation Timing

Summaries are generated **asynchronously after the response is sent**, not during the request. The flow:

1. User sends message → backend assembles context → streams response → stores messages
2. After storing, check: should we update the summary?
3. If yes: spawn a background task that calls vLLM with the summarization prompt
4. When done: update `conversations.summary` and `summary_through_index`

This way the user never waits for summary generation. If it fails (vLLM offline), the summary just doesn't update — next time it will catch up.
