# Frontend Integration Task List

Based on comparing `BACKEND-API.md` (what the backend actually implements) against
`FRONTEND-REFERENCE.md` (what the frontend was built to expect). Where they disagree,
BACKEND-API.md wins — it describes the live implementation.

---

## Critical Fixes (Backend behaves differently than frontend expects)

### F1 — Document upload has no "processing" status

**Backend reality:** Upload returns `"embedded"` or `"stored"` synchronously. There is no
`"processing"` status. Ever.

**Frontend impact:** If there's any polling or "processing" handling in the upload flow,
remove it. The response is final when it arrives.

**Action:** Check `api.ts` and `ChatInput.tsx` upload handler. Remove any `status === "processing"`
branches. The two states to handle are:
- `"embedded"` → "my_draft.pdf (24 chunks embedded)" confirmation
- `"stored"` → "my_draft.pdf uploaded (not indexed — images are stored but not searchable)"

### F2 — Only the last user message is read from the request

**Backend reality:** `POST /api/chat/completions` ignores all messages in the request except
the last one with `role: "user"`. The backend loads persisted history from the database using
`conversation_id`.

**Frontend impact:** The `messages` array in the request only needs the new user message. Don't
send the full conversation history — it's wasted bandwidth and the backend ignores it.

**Action:** In `useChat.ts`, when calling chat completions with a `conversation_id`, send:
```json
{
  "persona": "chat",
  "conversation_id": "...",
  "messages": [{"role": "user", "content": "the new message"}],
  "rag": {"enabled": true, "sources": ["papers"]}
}
```
Not the full message array.

### F3 — Two `conversation` events per new chat

**Backend reality:** New conversations emit a first `conversation` event with `is_new: true`
and `title: null`, then a second `conversation` event after auto-title generation with the
real title.

**Frontend impact:** The SSE handler must handle receiving `conversation` events more than once
per stream. Match on `id`, update the title in place, don't create duplicate sidebar entries.

**Action:** In `useChat.ts` SSE handler for the `conversation` event:
```typescript
case 'conversation':
  // May fire twice — first with null title, second with generated title
  if (!conversationId) {
    setConversationId(data.id);
    // push URL
  }
  if (data.title) {
    setConversationTitle(data.title);  // update sidebar entry
  }
  break;
```

### F4 — `services.embedding: "unavailable"` is normal on cold start

**Backend reality:** Embedding models (SPECTER2, BGE-base) are lazy-loaded. On a fresh
container start, `services.embedding` will be `"unavailable"` until the first RAG query or
document upload triggers loading. This is not an error.

**Frontend impact:** Don't show a red error indicator for `embedding: "unavailable"`. Either
hide the field, show "warming up", or show a neutral gray indicator.

**Action:** In `useStatus.ts`, treat `"unavailable"` for `embedding` as a non-error state.
Only `qdrant`, `neo4j`, and `retrieval` being down is a real problem.

### F5 — FTS search is not fuzzy

**Backend reality:** Chat search uses SQLite FTS5. `polymer` matches; `polym` doesn't.
FTS5 prefix syntax `polym*` works.

**Frontend impact:** If users search and get no results for partial words, it's expected.

**Action:** Consider appending `*` to the search query in the Sidebar search handler to
enable prefix matching:
```typescript
const searchQuery = query.endsWith('*') ? query : `${query}*`;
```

---

## New Features (Backend supports but frontend doesn't render yet)

### F6 — Agent SSE events (agent_start, agent_thinking, agent_tool_call, agent_tool_result, agent_done)

**Backend sends:** When the model invokes an agent (e.g., research_orchestrator), the stream
contains `agent_*` events nested between a `tool_call(invoke_agent)` and its `tool_result`.

**Stream ordering:**
```
tool_call {"name": "invoke_agent", "arguments": {"agent": "research_orchestrator", ...}}
agent_start {"agent": "research_orchestrator", "query": "..."}
agent_thinking {"content": "Planning approach..."}
agent_tool_call {"id": "atc-1", "name": "paper_search", ...}
agent_tool_result {"id": "atc-1", ...}
agent_thinking {"content": "Following citations..."}
agent_tool_call {"id": "atc-2", ...}
agent_tool_result {"id": "atc-2", ...}
agent_done {"agent": "...", "tool_calls": 4, "duration_seconds": 22, "stopped_reason": "done"}
tool_result {"id": "tc-1", "name": "invoke_agent", "result": {...}}
```

**Frontend work:**

1. In `useChat.ts`, add SSE handlers for all `agent_*` event types
2. In `TaskLog.tsx`, render agent blocks as a collapsible nested section:
   ```
   🔬 Research Orchestrator                                    ▾
   │ 🧠 Planning research approach...                         ▸
   │ 📚 Paper search: "cryo-EM membrane"         → 8 results  ▸
   │ 🔗 Citation lookup: 10.1038/...              → 15 refs   ▸
   │ Completed in 22s · 4 tool calls
   ```
3. The `agent_done` event includes `stopped_reason` which can be: `done`, `max_iterations`,
   `max_tool_calls`, `timeout`, `error`. Display non-`done` reasons as warnings.
4. Agent thinking (`agent_thinking`) accumulates like regular `thinking` but is displayed
   inside the agent's nested block, not at the top level.

**New icon for TaskLog:**

| Tool name | Icon |
|-----------|------|
| `invoke_agent` | Beaker/Flask (🔬) or Workflow icon |

### F7 — Slash commands for agent invocation

**Backend supports:** `/research <query>`, `/write <description>`, `/analyze <DOI>`

These should be sent as regular chat messages — the backend's persona prompt already instructs
the model to recognize these patterns and invoke the appropriate agent. No special frontend
parsing is strictly required.

**Optional enhancement:** Parse slash commands in `ChatInput.tsx` to provide:
- Visual feedback when typing `/` (show available commands as autocomplete)
- A badge above the input showing which agent will be invoked
- Send as `{"role": "user", "content": "/research What is the current state of CRISPR delivery?"}` —
  the backend handles it from there

### F8 — Loading messages by SSE phase

**Current state:** FeatherVortex shows during streaming but the loading message may be static.

**Enhancement:** Track the current activity phase from SSE events and rotate loading messages
from the appropriate category:

```typescript
// In useChat.ts, track current phase
const [loadingPhase, setLoadingPhase] = useState<string>('thinking');

// Update phase based on SSE events:
case 'thinking':      setLoadingPhase('thinking'); break;
case 'tool_call':
  if (data.name.includes('paper') || data.name.includes('citation'))
    setLoadingPhase('paper_search');
  else if (data.name.includes('web'))
    setLoadingPhase('web_search');
  else if (data.name === 'llm_summarize')
    setLoadingPhase('processing');
  else if (data.name === 'invoke_agent')
    setLoadingPhase('deep_research');
  break;
case 'agent_start':   setLoadingPhase('deep_research'); break;
```

Pass `loadingPhase` to the FeatherVortex/loading message component. Rotate messages every
2.5s with a fade transition. See CHAT-UI-TASK-LOG.md for the full message bank.

### F9 — RAG context display

**Backend sends:** `rag_context` event with `sources_used` and `documents` array (title, source,
score, DOI, content snippet).

**Current state:** Stored on the message but "currently not rendered" per FRONTEND-REFERENCE.md.

**Enhancement:** Add a collapsible "Sources" section to TaskLog when `rag_context` is present:
```
📖 Retrieved from 2 sources                                  ▾
│ papers: "Lipid Raft Dynamics in Cellular Membranes"
│   DOI: 10.1038/... · Score: 0.94
│ web: "Recent advances in membrane biology"
│   Score: 0.87
```

### F10 — `check_papers_availability` tool in TaskLog

**Backend has a tool** not in the FRONTEND-REFERENCE icon list: `check_papers_availability`
(bulk DOI availability check).

**Action:** Add to the TaskLog icon mapping:
```typescript
case 'check_papers_availability': return <FileSearch />; // or Books icon
```

### F11 — `search_user_docs` tool rendering

**Backend tool:** `search_user_docs` searches the user's uploaded documents.

**Action:** Already in the TaskLog icon list as a fallback (Gear icon). Consider giving it
a dedicated icon:
```typescript
case 'search_user_docs': return <FolderSearch />; // or FileText icon
```

And display the query: `"📁 Searched your documents: 'methods section'" → 3 results`

---

## Polish Items

### F12 — Conversation `user_email` field in list response

**Backend sends:** `user_email` in the conversation list response. The frontend doesn't need
this (it's always the current user). Ignore it, don't render it.

### F13 — `summary` and `summary_through_index` in conversation responses

**Backend sends:** These fields on conversation objects. The frontend should NOT render them
in the message list. They're internal context-management metadata.

**Action:** Confirm these fields are ignored in `MessageList.tsx`.

### F14 — `token_count` and `index_in_conversation` on messages

**Backend sends:** These on every message. `token_count` may be null. `index_in_conversation`
is a sequential integer.

**Action:** Can be safely ignored. `index_in_conversation` might be useful for debugging but
shouldn't be displayed.

### F15 — `persona` filter exact match

**Backend behavior:** `GET /api/chats?persona=chat` is exact match on the id string.

**Action:** Ensure the Sidebar persona filter (if implemented) sends the lowercase persona id
(`chat`, `code`, `research`), not the display name.

---

## Implementation Priority

| Priority | Tasks | Effort |
|----------|-------|--------|
| **Do first** | F1, F2, F3, F4 | Small fixes, prevent bugs |
| **Do second** | F6 (agent events) | Medium — new TaskLog rendering |
| **Do third** | F8 (loading messages), F9 (RAG display) | Medium — UX polish |
| **Optional** | F5, F7, F10, F11 | Small enhancements |
| **Ignore** | F12, F13, F14 | Just don't break on extra fields |
