# Additional Features

Smaller specs that were in the v2 architecture but not yet documented.

---

## 1. Progressive Web App (PWA)

### What

Add a PWA manifest and service worker to the chat frontend so users can "install" Munin on their phone or desktop. Opens full screen without browser chrome, has its own icon on the home screen.

### Implementation

**`frontend/public/manifest.json`:**
```json
{
  "name": "Munin AI",
  "short_name": "Munin",
  "description": "Research assistant for the department",
  "start_url": "/",
  "display": "standalone",
  "background_color": "#0f1419",
  "theme_color": "#0f1419",
  "icons": [
    {"src": "/icons/munin-192.png", "sizes": "192x192", "type": "image/png"},
    {"src": "/icons/munin-512.png", "sizes": "512x512", "type": "image/png"}
  ]
}
```

**`frontend/public/sw.js`:**
```javascript
const CACHE_NAME = 'munin-v1';
const STATIC_ASSETS = ['/', '/style.css', '/shared/feather-vortex.js'];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(STATIC_ASSETS))
  );
});

self.addEventListener('fetch', (event) => {
  // Network-first for API calls, cache-first for static assets
  if (event.request.url.includes('/api/')) {
    event.respondWith(fetch(event.request));
  } else {
    event.respondWith(
      caches.match(event.request).then((cached) => cached || fetch(event.request))
    );
  }
});
```

**In the HTML `<head>`:**
```html
<link rel="manifest" href="/manifest.json">
<meta name="theme-color" content="#0f1419">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<link rel="apple-touch-icon" href="/icons/munin-192.png">
```

### Effort

~100 lines total. Generate icons from the existing Munin logo at 192px and 512px. The service worker caches static assets for offline shell loading (the chat itself needs network, but the app shell loads instantly).

### File Locations

```
frontend/webui/public/
├── manifest.json
├── sw.js
└── icons/
    ├── munin-192.png
    └── munin-512.png
```

---

## 2. Cross-Conversation Memory (Optional)

### What

A per-user profile summary that persists across conversations: "This researcher works on membrane biophysics, prefers verbose explanations, frequently uses the Curie persona for citation analysis." Updated infrequently (every few conversations), injected into the system prompt so the model adapts to the user over time.

### How

**Storage:** A new table in the chat database (cluster):

```sql
CREATE TABLE user_profiles (
    user_email TEXT PRIMARY KEY,
    profile_summary TEXT,           -- LLM-generated user summary
    updated_at TEXT,
    conversation_count INTEGER      -- how many conversations contributed
);
```

**Generation:** After every 5th conversation that has more than 10 messages, the backend asks the LLM:

```
Based on this user's recent conversations, update their profile summary.
Current profile: {existing_summary or "No profile yet"}

Recent conversation summaries:
{summaries from last 5 conversations}

Write a brief profile (3-5 sentences) capturing:
- Research interests and domains
- Communication preferences (verbose vs. concise, formal vs. casual)
- Frequently used tools and personas
- Any stated preferences or constraints
```

**Injection:** Added to the system prompt as a low-priority context block:

```
[User profile: This researcher works on membrane biophysics using MD simulations.
They prefer detailed explanations with citations. They frequently use the Curie
persona for literature discovery and the Turing persona for Python analysis scripts.]
```

**Privacy:** Users can view and delete their profile via the settings page. The profile is generated from conversation summaries (which are already stored), not from raw message content.

### Priority

Low. Per-conversation memory (CHAT-PERSISTENCE.md) handles 90% of the continuity need. Cross-conversation memory is a nice-to-have that improves the experience for heavy users over weeks/months.

---

## 3. BM25 / Keyword Search

### What

Add keyword-based search (BM25) as a retrieval tool alongside semantic vector search. Vector search is great for conceptual similarity ("papers about cell membrane organization") but misses exact term matches ("DPPC lipid bilayer simulation parameters"). BM25 catches those.

### Implementation

**Option A: Qdrant's built-in keyword index**

Qdrant supports payload keyword indexes. Create a text index on the `chunk_text` field:

```python
from qdrant_client.models import PayloadSchemaType

client.create_payload_index(
    collection_name="papers",
    field_name="chunk_text",
    field_schema=PayloadSchemaType.TEXT
)
```

Then query with a filter:
```python
client.scroll(
    collection_name="papers",
    scroll_filter=models.Filter(
        must=[models.FieldCondition(
            key="chunk_text",
            match=models.MatchText(text="DPPC bilayer")
        )]
    )
)
```

This is not true BM25 ranking but handles keyword matching.

**Option B: Tantivy via Qdrant (recommended)**

Qdrant integrates with Tantivy for full-text search with BM25 ranking. Enable it when creating the collection:

```python
client.create_collection(
    collection_name="papers",
    vectors_config=...,
    sparse_vectors_config={
        "text": models.SparseVectorParams(
            modifier=models.Modifier.IDF  # enables BM25
        )
    }
)
```

This gives proper BM25 scoring alongside vector search.

### MCP Tool: `keyword_search`

```json
{
  "name": "keyword_search",
  "description": "Search papers by exact keywords and phrases. Use when looking for specific terms, chemical names, method names, or exact phrases that semantic search might miss.",
  "input_schema": {
    "query": "DPPC bilayer simulation parameters",
    "collection": "papers",
    "top_k": 5
  }
}
```

### Hybrid Search Update

The existing `/api/search/hybrid` endpoint (vector + citation re-ranking) can be extended to a three-way hybrid:

```
score = α * vector_score + β * citation_score + γ * bm25_score
```

Default weights: α=0.6, β=0.2, γ=0.2. Configurable per query.

### Priority

Medium. Most queries work well with vector search alone. BM25 adds value for exact term lookups and method-specific queries common in scientific research.

---

## 4. Context-Aware Chat Compaction

### What

The v2 architecture specifies that before every request, the backend should check whether the assembled context (summary + recent messages + system prompt + RAG) fits the model's context window. If not, it should actively re-summarize to make room. Our current spec (CHAT-PERSISTENCE.md) only does periodic summarization.

### Updated Flow

Replace the simple periodic summarization with an active check:

```python
async def assemble_context(conversation, new_message, persona, rag_context=None):
    # 1. Start with fixed components
    system_tokens = count_tokens(persona.system_prompt)  # ~500-800
    rag_tokens = count_tokens(rag_context) if rag_context else 0  # ~1000-3000
    new_msg_tokens = count_tokens(new_message)

    # 2. Calculate available budget for conversation context
    MAX_CONTEXT = 60000  # leave headroom below 64k model limit
    GENERATION_RESERVE = 8000  # reserve for model output
    available = MAX_CONTEXT - GENERATION_RESERVE - system_tokens - rag_tokens - new_msg_tokens

    # 3. Get summary + recent messages
    summary = conversation.summary or ""
    summary_tokens = count_tokens(summary)
    recent_messages = get_messages_after_summary(conversation)

    # 4. Check if everything fits
    recent_tokens = sum(count_tokens(m.content) for m in recent_messages)
    total_conversation = summary_tokens + recent_tokens

    if total_conversation <= available:
        # Everything fits — use as-is
        return build_prompt(persona, summary, recent_messages, rag_context, new_message)

    # 5. Doesn't fit — compact
    # Keep the most recent messages that fit in half the budget
    recent_budget = available // 2
    kept_messages = []
    kept_tokens = 0
    for msg in reversed(recent_messages):
        msg_tokens = count_tokens(msg.content)
        if kept_tokens + msg_tokens > recent_budget:
            break
        kept_messages.insert(0, msg)
        kept_tokens += msg_tokens

    # 6. Summarize the messages we're dropping
    dropped = [m for m in recent_messages if m not in kept_messages]
    if dropped:
        new_summary = await generate_summary(summary, dropped)
        # Store updated summary
        await update_conversation_summary(conversation, new_summary, dropped[-1].index)
        summary = new_summary

    return build_prompt(persona, summary, kept_messages, rag_context, new_message)
```

### Key Differences from Simple Periodic Summarization

| Aspect | Periodic (old) | Context-aware (new) |
|--------|----------------|---------------------|
| When to summarize | Every N messages | When context would overflow |
| Blocking | Async background task | Synchronous (must complete before sending to vLLM) |
| Guarantee | Context might still overflow | Context always fits |
| Latency | No added latency | Adds 1-3s when compaction triggers |

### Fallback

If vLLM is unavailable for summary generation (off-hours, but the user somehow has a cached session): truncate older messages without summarizing. Lose some context rather than failing entirely.

### Token Counting

Use a fast approximate counter (tiktoken or a character-based heuristic: ~4 chars per token for English). Exact token counting with the model's tokenizer would be ideal but adds latency. The approximation with a safety margin is sufficient.

### Priority

High for long conversations. Short chats (under 20 messages) will never trigger compaction. But a user who has a 100-message research session will hit the context window without this.

---

## 5. Web Search Routing

### What

The model decides whether each query needs web search, rather than always searching or never searching. This was explicitly called out in the v2 architecture to reduce unnecessary latency.

### Implementation

This is not a separate system — it's a system prompt instruction that the model follows. The persona prompts already contain guidance on when to use tools. The addition is making the web search decision explicit:

**Added to all persona system prompts:**
```
When deciding whether to search the web:
- DO search: current events, recent publications, statistics, "what's the latest...",
  any question where the answer might have changed in the last year
- DON'T search: reasoning tasks, code writing, questions about well-established science,
  creative writing, follow-up questions where context is already in the conversation
- When in doubt, consider: "Would a knowledgeable researcher need to look this up, or
  would they know it?" If they'd know it, don't search.
```

### Parallel Retrieval

When the model does decide to search, all retrieval sources execute concurrently:

```python
# Model asks for paper_search AND web_search
results = await asyncio.gather(
    search_papers(query),
    search_web(query),
    search_neo4j(query),  # if citations relevant
    timeout=10  # don't wait forever for one slow source
)
```

This is already implied by the agentic orchestration spec (parallel tool execution) but worth calling out explicitly: retrieval is never sequential.

### Priority

Already partially implemented via persona system prompts. The parallel execution is the important engineering part — ensure the retrieval service uses `asyncio.gather` for concurrent tool calls, not sequential awaits.
