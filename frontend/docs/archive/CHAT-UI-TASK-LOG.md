# Chat UI — Loading Messages & Task Execution Log

## 1. Playful Loading Messages

While the model is thinking or executing tools, show a rotating message alongside the feather vortex. Messages change every 2–3 seconds. The tone mixes Norse/raven mythology, academic humor, and general playfulness.

### Message Bank

Organized by phase — pick randomly within the relevant phase.

**General thinking (before any tool calls):**
- Consulting the runes...
- Raven dispatched...
- Pondering in the mead hall...
- Unraveling the threads of thought...
- Sharpening the quill...
- Summoning ancient wisdom...
- Perched and pondering...
- Whispering to Odin's ear...
- Fluffing through the archives...
- Gathering scattered feathers...
- Descending into the knowledge well...
- Sifting through the sagas...

**Paper/academic search:**
- Rifling through the scrolls...
- Interrogating the literature...
- Swooping through the stacks...
- Pecking at the knowledge graph...
- Cross-referencing citations...
- Following the paper trail...
- Diving into the archives...
- Chasing footnotes...
- Hunting through abstracts...
- Rummaging through the library...

**Web search:**
- Scouring the nine realms...
- Sending ravens across the web...
- Foraging far and wide...
- Mapping the world tree...
- Flying reconnaissance...
- Circling the internet...
- Dispatching scouts...

**Processing/summarizing:**
- Digesting the findings...
- Distilling the essence...
- Weaving the threads together...
- Arranging the feathers...
- Composing the report...
- Assembling the mosaic...
- Translating from raven to human...

**Code tasks:**
- Compiling incantations...
- Debugging the runes...
- Refactoring the spellbook...
- Optimizing the enchantment...
- Tracing the logic threads...

**Deep research (long-running):**
- This may take a few wingbeats...
- Embarking on a longer journey...
- The raven flies far for this one...
- Deep in the knowledge well...
- Thorough research takes time...

### Implementation

```javascript
const LOADING_MESSAGES = {
  thinking: [ /* general thinking messages */ ],
  paper_search: [ /* paper messages */ ],
  web_search: [ /* web messages */ ],
  processing: [ /* summarizing messages */ ],
  code: [ /* code messages */ ],
  deep_research: [ /* long-running messages */ ]
};

function getLoadingMessage(phase) {
  const messages = LOADING_MESSAGES[phase] || LOADING_MESSAGES.thinking;
  return messages[Math.floor(Math.random() * messages.length)];
}
```

**Phase detection from SSE events:**
- Before any events → `thinking`
- `thinking` event received → `thinking`
- `tool_call` with name `paper_search`, `get_citations`, `get_references`, `semantic_scholar_search` → `paper_search`
- `tool_call` with name `web_search`, `web_fetch` → `web_search`
- `tool_call` with name `llm_summarize` → `processing`
- Deep research submission → `deep_research`

**Rotation:** Change message every 2.5 seconds with a gentle fade transition (200ms opacity out, swap text, 200ms opacity in). Don't repeat the same message twice in a row.

**Layout:** Feather vortex (40px inline) + message text, centered in the chat area where the response will appear.

---

## 2. Task Execution Log

A structured, visible log of everything the model did during a response. This appears as part of the response — between the loading state and the final text output. It builds up in real time as events stream in, and stays visible after the response is complete.

**This is a transparency feature.** The user can see exactly what happened: what the model was thinking, which tools it called, what arguments it used, and what came back. Nothing is hidden by default — every step is a visible row. Detail is one click away.

### Visual Structure

Each action is a row. Rows appear in chronological order as they happen during streaming.

```
┌─────────────────────────────────────────────────────────────┐
│ 🧠 Reasoned for 2.3s                                    ▸  │
│ 📚 Paper search (2 queries)                              ▾  │
│    "lipid raft dynamics"                     → 5 results ▸  │
│    "membrane cholesterol interaction"        → 3 results ▸  │
│ 🧠 Reasoned for 1.1s                                    ▸  │
│ 🔗 Citation lookup: 10.1038/s41586-021...     → 12 refs  ▸  │
│ 🌐 Web search: "cryo-EM lipid 2026"          → 8 results ▸  │
│ 🌐 Fetched nature.com/articles/s41586...      → 2.4k words ▸│
│ 🧠 Reasoned for 0.8s                                    ▸  │
│ 📝 Summarized article                        → 340 words ▸  │
│                                                             │
│ Completed in 8.4s · 6 tool calls · 3 sources queried       │
└─────────────────────────────────────────────────────────────┘
```

### Row Types

#### Thinking / Reasoning

Every `thinking` SSE event becomes a row. This is the model's chain-of-thought from `<think>` tags. Multiple thinking blocks appear as separate rows (the model thinks, calls a tool, thinks again, calls another tool — each thinking segment is its own row).

**Collapsed (default):**
```
🧠 Reasoned for 2.3s                                        ▸
```

**Expanded:**
```
🧠 Reasoned for 2.3s                                        ▾
┌─ Model reasoning ───────────────────────────────────────────┐
│ The user is asking about lipid rafts. I should search the   │
│ paper database first for recent work, then check if there   │
│ are any 2026 publications via web search. Let me also look  │
│ up citations for the key Simons & Ikonen 1997 paper to see  │
│ what the latest follow-up work looks like.                  │
│                                                             │
│ I'll start with a broad paper search, then narrow based on  │
│ what I find.                                                │
└─────────────────────────────────────────────────────────────┘
```

This lets users see the model's full reasoning process — why it chose certain tools, how it planned its approach, what it decided to look for.

#### Tool Calls

Every `tool_call` + `tool_result` pair becomes a row (or a grouped row if consecutive same-type calls).

**Collapsed:**
```
📚 Searched papers for "lipid raft dynamics"    → 5 results  ▸
```

**Expanded:**
```
📚 Searched papers for "lipid raft dynamics"    → 5 results  ▾
┌─ Arguments ──────────────────────────────────────────────────┐
│ {                                                            │
│   "query": "lipid raft dynamics",                            │
│   "sources": ["papers"],                                     │
│   "top_k": 5                                                 │
│ }                                                            │
├─ Results ────────────────────────────────────────────────────┤
│ 1. Lipid Raft Dynamics in Cellular Membranes                 │
│    DOI: 10.1038/s41580-024-00123-4 · Score: 0.94 · 2024     │
│                                                              │
│ 2. Cholesterol Organization in Raft Domains                  │
│    DOI: 10.1016/j.cell.2023.08.012 · Score: 0.89 · 2023     │
│                                                              │
│ 3. Revisiting the Lipid Raft Hypothesis                      │
│    DOI: 10.1146/annurev-biochem-2024 · Score: 0.87 · 2024   │
│                                                              │
│ 4. Membrane Microdomains: From Theory to Imaging             │
│    DOI: 10.1073/pnas.2024.1234 · Score: 0.82 · 2025         │
│                                                              │
│ 5. Phase Separation in Biological Membranes                  │
│    DOI: 10.1038/s41557-023-01234 · Score: 0.79 · 2023       │
├─ Timing ─────────────────────────────────────────────────────┤
│ Duration: 0.34s                                              │
└──────────────────────────────────────────────────────────────┘
```

The expanded view shows the **raw arguments** (what the model asked for) and the **raw results** (what came back) so the user can dig into exactly what data the model based its response on.

#### Agent Calls / Multi-Step Plans

If the model chains multiple actions as part of a deliberate plan (e.g., "search, then look up citations for the top result, then fetch the PDF"), each step is its own row in chronological order. The thinking rows between them reveal the model's planning:

```
🧠 Reasoned for 1.5s                                        ▸
   "Let me search for the paper first..."
📚 Searched papers for "CRISPR off-target"      → 8 results ▸
🧠 Reasoned for 0.6s                                        ▸
   "The top result looks relevant, let me check its citations"
🔗 Citation lookup: 10.1126/science.aad5227      → 23 refs  ▸
🧠 Reasoned for 0.4s                                        ▸
   "Several recent citations, let me fetch the 2025 one"
🌐 Fetched nature.com/articles/s41587-025...     → 3.1k words ▸
📝 Summarized article                           → 280 words ▸
```

This makes the model's agentic behavior fully transparent. The user can see the entire decision chain: reason → act → observe → reason → act.

### Grouping Rules

Same-type **consecutive** tool calls are grouped under a collapsible header. The group shows the count, and each sub-item has its own expand toggle.

| Pattern | Grouped display |
|---------|----------------|
| 2+ consecutive `paper_search` | `📚 Paper search (N queries)` with sub-items |
| 2+ consecutive `web_search` | `🌐 Web search (N queries)` with sub-items |
| 2+ consecutive `web_fetch` | `🌐 Fetched N pages` with sub-items |

**Not grouped (always separate rows):**
- `get_citations` / `get_references` — each is a specific DOI lookup worth seeing individually
- `llm_summarize` — each is a distinct summarization
- `thinking` blocks — each represents a separate reasoning phase
- Tool calls of different types — no cross-type grouping

**Grouping breaks** when a thinking block or a different tool type appears between same-type calls. So `paper_search → paper_search → thinking → paper_search` becomes: group of 2, then thinking row, then separate paper_search.

### Icons

| Category | Icon | SSE events / tool names |
|----------|------|------------------------|
| Reasoning | 🧠 | `thinking` SSE event |
| Papers | 📚 | `paper_search`, `semantic_scholar_search`, `paper_lookup` |
| Citations | 🔗 | `get_citations`, `get_references`, `get_author_papers` |
| Web | 🌐 | `web_search`, `web_fetch` |
| Processing | 📝 | `llm_summarize` |
| PDF | 📄 | `get_paper_pdf` |

### Summary Footer

After all rows, a single-line summary:

```
Completed in 8.4s · 6 tool calls · 3 sources queried
```

Fields:
- Total wall-clock time from first event to `done` event
- Number of tool calls (not counting thinking blocks)
- Number of distinct sources used (papers, web, citations — deduplicated)

### Real-Time Behavior During Streaming

The log builds up live as SSE events arrive:

1. **`thinking` event arrives** → New "🧠 Reasoning..." row appears (animated, no duration yet). The thinking text streams into the expandable content.
2. **`tool_call` event arrives** → New row appears with tool icon and "Calling..." state. The feather vortex message switches to the relevant phase (e.g., `paper_search` messages).
3. **`tool_result` event arrives** → The row updates from "Calling..." to the result summary ("→ 5 results"). The expand toggle becomes active.
4. **`token` events arrive** → The log is complete, the response text streams below it.
5. **`done` event arrives** → The summary footer appears. Feather vortex stops.

**While streaming, the current active row has a subtle pulse animation** (the feather vortex inline at 28px, or just a pulsing dot) to show which step is in progress.

### Styling

The execution log uses the Munin dark theme from `shared/style.css`:

- Container: `background: var(--bg-secondary)`, `border: 1px solid var(--border)`, `border-radius: 12px`, `padding: 12px 16px`, `margin-bottom: 16px`
- Rows: `padding: 6px 0`, `border-bottom: 1px solid var(--border)` (except last)
- Row text: `font-size: 13px`, `color: var(--text-secondary)`, `font-family: monospace` for arguments/results
- Expand toggle: `color: var(--text-secondary)`, `cursor: pointer`, on hover `color: var(--accent)`
- Active/in-progress row: `color: var(--text-primary)` (brighter than completed rows)
- Expanded detail panel: `background: var(--bg-tertiary)`, `border-radius: 8px`, `padding: 12px`, `margin: 4px 0 4px 24px`, `font-size: 12px`, `font-family: monospace`
- Summary footer: `font-size: 12px`, `color: var(--text-secondary)`, `padding-top: 8px`, `border-top: 1px solid var(--border)`
- Grouped sub-items: indented `margin-left: 24px`

### File Location

Implement as a React component when building the chat frontend:
- `frontend/src/components/TaskLog.tsx` — the execution log component
- `frontend/src/components/LoadingMessage.tsx` — the feather vortex + rotating message
- `frontend/src/data/loading-messages.ts` — the message bank

For the static research page (`static/research/index.html`), a simplified version of the loading messages can be used with the feather vortex during deep research job polling.
