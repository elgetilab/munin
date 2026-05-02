# Frontend Implementation Plan (v2 Backend Integration)

Based on comparing BACKEND-API_v2.md, FRONTEND-REFERENCE_v2.md, and
FRONTEND-TASKS_v2.md against the current frontend state.

## What's Already Done

- Markdown rendering with `react-markdown` + `remark-gfm` (auto-linkify, tables, code blocks with syntax highlighting)
- Agent SSE events (`agent_start/thinking/tool_call/tool_result/done`)
- Loading messages by SSE phase (feather vortex with phase-based message banks)
- RAG context display in TaskLog
- Slash command autocomplete (`/research`, `/write`, `/analyze`)
- Conversation starring (localStorage — needs migration to backend pinning)
- Tool icons (SVG) in TaskLog

## What's Not Done Yet (from v1 integration fixes)

These are small fixes that should be done first as they prevent bugs.

---

## Phase 0 — Quick Integration Fixes (~1 hour)

### 0a. Document upload: remove "processing" status handling
Backend only returns `"embedded"` or `"stored"`. Check `ChatInput.tsx`
upload flow and remove any `status === "processing"` branches. Add
`"stored"` display: "uploaded (not indexed)".

### 0b. Only send last user message
When `conversation_id` is set, backend ignores all messages except
the last `role: "user"`. Stop sending full history — just send the
new message. Saves bandwidth.

**Files:** `useChat.ts` `sendMessage` function.

### 0c. Handle two `conversation` events per new chat
Backend emits a first `conversation` event with `title: null`, then
a second with the auto-generated title. The SSE handler should match
on `id` and update the title in place, not create duplicates.

**Files:** `useChat.ts` SSE handler.

### 0d. Handle `ephemeral` flag on conversation event
The `conversation` SSE event now carries `ephemeral: true/false`.
Store it; needed for Phase 3.

### 0e. FTS prefix search
Append `*` to sidebar search queries for prefix matching
(`polym` → `polym*`).

**Files:** `Sidebar.tsx` search handler.

### 0f. Embedding "unavailable" is normal
Don't show error for `services.embedding: "unavailable"` — it's
a cold-start state that resolves on first RAG query.

**Files:** `useStatus.ts` if services are rendered anywhere.

---

## Phase 1 — Pin Conversations (~2 hours)

**Replaces:** localStorage starring with server-side pinning.

### What to do

1. **Remove localStorage starring** — delete `loadStarred()`,
   `saveStarred()`, `STARRED_KEY` from `Sidebar.tsx`.

2. **Read `pinned` from API** — `GET /api/chats` now returns
   `pinned: bool` and `pinned_at` on each row. Backend already
   orders `pinned DESC, updated_at DESC`.

3. **Toggle via API** — replace `toggleStar` to call:
   - `POST /api/chats/{id}/pin` (pin)
   - `DELETE /api/chats/{id}/pin` (unpin)

4. **Add to `api.ts`:**
   ```typescript
   export async function pinChat(id: string): Promise<void> {
     await fetch(`${API}/chats/${id}/pin`, { method: 'POST' });
   }
   export async function unpinChat(id: string): Promise<void> {
     await fetch(`${API}/chats/${id}/pin`, { method: 'DELETE' });
   }
   ```

5. **Update types** — add `pinned: boolean` and `pinned_at: string | null`
   to `ConversationSummary`.

6. **Sidebar grouping** — the "Starred" group becomes "Pinned".
   Use the `pinned` field from the API instead of the local Set.

7. **One-time migration** — on first load, check localStorage for
   starred IDs. If found, POST pin for each, then clear localStorage.

**Files:** `Sidebar.tsx`, `api.ts`, `types.ts`

---

## Phase 2 — User Profile Settings (~3 hours)

### What to do

1. **New section in Settings.tsx** — "Profile" tab with:
   - "About you" textarea (`about_me`, max 1500 chars)
   - "How should I respond?" textarea (`response_format`, max 1500 chars)
   - Default persona picker dropdown
   - Timezone picker (IANA names)
   - Character count indicators
   - Save / Reset buttons

2. **API functions in `api.ts`:**
   ```typescript
   export async function getProfile(): Promise<Profile> { ... }
   export async function updateProfile(data: Partial<Profile>): Promise<Profile> { ... }
   export async function deleteProfile(): Promise<void> { ... }
   ```

3. **Types in `types.ts`:**
   ```typescript
   interface Profile {
     user_email: string;
     about_me: string | null;
     response_format: string | null;
     default_persona: string | null;
     default_rag_sources: string[] | null;
     timezone: string | null;
     created_at: string;
     updated_at: string;
   }
   ```

4. **Load on mount** of Settings page, save only changed fields.

**Files:** `Settings.tsx`, `api.ts`, `types.ts`

---

## Phase 3 — Ephemeral Chats (~3 hours)

### What to do

1. **Ephemeral toggle** — a switch/button near the chat input or in
   the header. When active, show banner: "This chat won't be saved."

2. **State management** — when ephemeral is active:
   - Keep a local-only message array
   - Send `ephemeral: true` + full message history on every POST
   - Omit `conversation_id` from the body
   - Don't add to sidebar

3. **Detect `ephemeral: true`** on `conversation` SSE event:
   - Don't push URL to `/c/{id}`
   - Don't refresh sidebar
   - Show "Ephemeral Chat" label instead of title

4. **On page refresh / leave** — session is lost (that's the feature).

5. **Disable in ephemeral mode:**
   - File/image upload (allow inline paste images though)
   - "Save to project" actions
   - Pin button

6. **Handle `finish_reason: "clarification"`** on `done` event
   (needed for Phase 6).

**Files:** `App.tsx`, `useChat.ts`, `ChatInput.tsx`, `MessageList.tsx`

---

## Phase 4 — Clarification Cards (~2 hours)

### What to do

1. **SSE handler** — add `clarification` event branch in `useChat.ts`.
   Attach the clarification payload to the current streaming message.

2. **ClarificationCard component** — inline card (not modal) with:
   - "Just to make sure I understand:" header
   - `what_i_understood` quote
   - 1-5 questions, each with radio buttons for options
   - Optional free-text field when `allow_custom: true`
   - Submit button

3. **On submit** — format answers as markdown and send as normal
   follow-up message:
   ```
   Q1: Last 5 years
   Q2: Model bilayers (LUVs, BLMs)
   ```

4. **After submit** — disable controls, show "Answered" badge.

5. **Handle `finish_reason: "clarification"`** on `done` event —
   the turn ends without a text response, only the card.

**Files:** `useChat.ts`, `MessageList.tsx`, new `ClarificationCard.tsx`

---

## Phase 5 — Multimodal Image Attachments (~4 hours)

### What to do

1. **Paste handler** — intercept Ctrl+V / Cmd+V image paste in
   `ChatInput.tsx`. Convert to `data:image/...;base64,...`.

2. **Upload handler** — extend the existing attach button to accept
   images (PNG, JPG, WEBP). Convert to base64 on selection.

3. **Composer state** — track pending images (max 3, max 5 MB each).
   Show thumbnails above the textarea with remove buttons.

4. **Send as multimodal content** — when images are attached, format
   the last message's `content` as an OpenAI-style list:
   ```json
   [
     {"type": "text", "text": "Describe this figure"},
     {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}
   ]
   ```

5. **Render in chat bubbles** — show attached images inline in user
   message bubbles.

6. **Client-side validation** — reject > 5 MB, unsupported types,
   > 3 images. Show clear error messages.

7. **Document reference** — for previously uploaded docs, emit
   `{"url": "document:<doc_id>"}` instead of inline base64.

**Files:** `ChatInput.tsx`, `useChat.ts`, `MessageList.tsx`, `api.ts`, `types.ts`

---

## Phase 6 — Artifacts Side Panel (~1 week)

The biggest lift. Can be broken into sub-phases.

### 6a. Side panel shell + SSE subscription (~3 hours)

1. **Panel component** — collapsible right panel in the chat view.
   Toggle button in the header.

2. **Fetch artifacts** — `GET /api/chats/{cid}/artifacts` on chat load.
   Display list: title, content_type icon, version number.

3. **SSE handlers** — add `artifact_created` and `artifact_updated`
   event branches in `useChat.ts`:
   - `artifact_created` → add to list, optionally auto-open
   - `artifact_updated` → bump version, highlight row

4. **Types:**
   ```typescript
   interface Artifact {
     id: string;
     title: string;
     content_type: string;
     language?: string;
     latest_version: number;
     word_count: number;
     byte_size: number;
     source: 'model_written' | 'sandbox_generated';
     filename?: string;
     size_bytes?: number;
     external_url?: string;
     created_at: string;
     updated_at: string;
   }
   ```

### 6b. Artifact viewer + version picker (~3 hours)

1. **Content fetch** — `GET /api/chats/{cid}/artifacts/{aid}` for
   full content. Route rendering by `content_type`:
   - `text/markdown` → rendered markdown
   - `text/latex`, `application/python`, `application/json`,
     `image/svg+xml` → syntax-highlighted code
   - `text/plain` → plain text

2. **Version picker** — dropdown showing v1, v2, ... Latest is
   editable; past versions are read-only (fetch with `?version=N`).

3. **Copy + Download** buttons.

### 6c. Inline edit mode (~2 hours)

1. **Edit button** on latest version → switches to textarea/editor.
2. **Save** → `PATCH /api/chats/{cid}/artifacts/{aid}` with
   `{content, change_summary}`.
3. **Character/byte count** with 500 KB cap warning.
4. **Cancel** discards unsaved changes.

### 6d. Sandbox artifact routing (~2 hours)

Route `artifact_created` by `source` field:
- `model_written` → open in side panel
- `sandbox_generated` → render inline in chat:
  - `image/*` → `<img src="${external_url}">`
  - Other → download chip with filename + size

### 6e. LaTeX artifacts (task #11) (~30 min)

- Add `.tex` and `.pdf` icons to download chips
- Group `.tex` + `.pdf` from same `tool_call_id`
- Optional: inline PDF preview via `<iframe>`

**Files:** new `ArtifactPanel.tsx`, new `ArtifactViewer.tsx`,
`useChat.ts`, `App.tsx`, `MessageList.tsx`, `api.ts`, `types.ts`

---

## Phase 7 — Projects (~1 week)

The second biggest lift. Can also be sub-phased.

### 7a. Sidebar projects section (~4 hours)

1. **Fetch projects** — `GET /api/projects` on mount.
2. **Render project list** above flat conversation list.
3. **Expand project** → fetch `GET /api/chats?project_id={pid}`.
4. **"Unfiled" bucket** → `GET /api/chats?project_id=__unfiled__`.
5. **New chat inside project** → include `project_id` in
   completions request body.

### 7b. Project settings page (~3 hours)

1. **CRUD** — create, rename, update instructions/description/
   default persona, archive toggle, delete (with confirmation).
2. **Character cap indicators** (name 200, description 1000,
   instructions 2000).

### 7c. Move-to-project action (~2 hours)

1. **Context menu item** on conversations — "Move to project..."
2. **API calls:**
   - `POST /api/projects/{pid}/conversations/{cid}` (file)
   - `DELETE /api/projects/{pid}/conversations/{cid}` (unfile)

### 7d. Project context banner (~1 hour)

Show "You are in project: Kinase Thesis" banner in chat view
when the conversation belongs to a project.

### 7e. Project-scoped uploads (~1 hour)

Upload button inside project adds `project_id` form field.

**Files:** `Sidebar.tsx`, new `ProjectSettings.tsx`, `App.tsx`,
`ChatInput.tsx`, `api.ts`, `types.ts`

---

## Phase 8 — Polish & Tool Icons (~1 hour)

### 8a. Update tool icon map

Add icons for new tools from FRONTEND-REFERENCE_v2:
- `read_paper`, `compare_papers` → Books
- `s2_get_citations`, `s2_get_references` → Link
- `deep_research` → Magnifier
- `run_python` → Terminal
- `compile_latex` → Page
- `ask_clarification` → Question mark
- `calculate` → Calculator
- `search_user_docs` → Folder
- `check_papers_availability` → Books
- `invoke_agent` → Robot/Flask

### 8b. Update gateway exempt endpoints

Add new endpoints to the gateway's rate-limit exempt list as
needed (e.g., `profile`, `projects`, `artifacts`).

---

## Implementation Order Summary

| Phase | What | Effort | Dependencies |
|-------|------|--------|-------------|
| **0** | Quick integration fixes | 1h | None |
| **1** | Pin conversations (replace starring) | 2h | Phase 0 |
| **2** | User profile settings | 3h | None |
| **3** | Ephemeral chats | 3h | Phase 0c, 0d |
| **4** | Clarification cards | 2h | Phase 0 |
| **5** | Multimodal images | 4h | None |
| **6** | Artifacts side panel | ~1 week | Phase 0 |
| **7** | Projects | ~1 week | Phase 1 |
| **8** | Polish & tool icons | 1h | After Phase 4, 6 |

Phases 0-5 can be done in roughly 2-3 days of focused work.
Phases 6-7 are the big lifts (~1 week each) and can run in
parallel with different focus areas (6 is more component-heavy,
7 is more navigation/routing-heavy).

**Recommended order:** 0 → 1 → 2 → 4 → 3 → 5 → 8 → 6 → 7

This front-loads the quick wins and the features that improve
daily usability (pinning, profile, clarification), then tackles
the larger structural changes (artifacts, projects).
