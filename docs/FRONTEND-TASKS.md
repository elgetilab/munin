# Frontend Tasks (handoff from munin-backend)

This file collects work items the **backend** has identified as belonging
to the frontend repo. Each entry explains the problem, why the backend
can't fix it, and what the concrete change looks like.

When a task lands in the frontend repo, leave the entry here as
historical context (or move to a separate "done" section).

---

## 1. Auto-linkify bare URLs in markdown rendering

**Status:** open
**Driven by:** munin-backend §13 "Paper download prominence"
**Date:** 2026-04-14
**Effort:** 1 line of frontend config

### What the backend does

`paper_search`, `semantic_scholar_search`, and `deep_research` now
return a `download_url` field on every paper that exists in the local
corpus, and an `open_access_pdf` field for papers with an OA URL from
Semantic Scholar. The persona prompts (Meitner / Curie / Turing) all
have a CORE rule:

> URLs MUST be wrapped in markdown link syntax `[label](url)`.
> NEVER write a bare URL on its own line, NEVER write
> `**Label:** url`. Bare URLs do not render as clickable in our
> frontend.

### What actually happens

Qwen3-35B is trained to present metadata as `**Label:** value`. After
three rounds of prompt strengthening (escalating from "should" to
"MUST" to explicit WRONG/RIGHT examples in the OUTPUT STYLE block),
the model still emits something like:

```markdown
**Title:** Membrane structure and dynamics
**Author:** A. Watts
**Year:** 1989
**DOI:** 10.1016/0955-0674(89)90035-5
**Download URL:** https://search.muninai.org/paper/10.1016%2F0955-0674%2889%2990035-5/pdf
```

The URL is correct and present in the answer. It just isn't wrapped
in `[text](url)` syntax.

### Why backend prompting can't reliably fix this

We tried three iterations of progressively stronger persona prompt
language — moving the rule from "Guidelines" to the high-salience
OUTPUT STYLE block, adding explicit WRONG / RIGHT examples, escalating
from "should" to "MUST" / "NEVER". 3/3 stress-test runs still failed.

The pattern `**Label:** value` is too deeply embedded in the model's
trained behaviour for scientific writing to overcome with prompting
alone. Backend post-processing of the streaming token output is
possible but invasive (~50 lines, breaks the live token streaming
into sentence-batched chunks). The right architectural fix is in the
renderer.

### What the frontend needs to do

Enable a markdown auto-linkify plugin in your markdown renderer so
**bare URLs render as `<a href>` automatically**. Both `[text](url)`
AND bare URLs (`https://...`) become clickable.

For the most common stack (`react-markdown`):

```jsx
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

<ReactMarkdown remarkPlugins={[remarkGfm]}>
  {messageContent}
</ReactMarkdown>
```

`remark-gfm` enables GitHub-Flavoured Markdown which auto-links bare
URLs (and gives you tables, strikethrough, task lists, footnotes for
free).

If you use a different markdown library, the equivalent flag exists:

| Library | Plugin / option |
|---|---|
| `react-markdown` | `remarkPlugins={[remarkGfm]}` |
| `marked` | `marked.use({ extensions: [linkify] })` or `mangle: false, gfm: true` |
| `markdown-it` | `markdown-it` has linkify built in: `new MarkdownIt({ linkify: true })` |
| `unified` / direct remark | add `remark-gfm` to the processor pipeline |

### How to verify

Open any chat that asks for papers. Look at an assistant message that
contains lines like `**Download URL:** https://...`. The URL portion
should render as a clickable blue link, not as plain text.

You can also test the failure case: temporarily remove the linkify
plugin, send the same prompt, and verify the URL renders as plain
text. Re-enable the plugin to confirm the fix.

### What else this fixes for free

The same auto-linkify covers:

- Bare DOI URLs (`https://doi.org/10.1234/...`)
- arXiv links (`https://arxiv.org/abs/2401.12345`)
- Any other URL the model emits without thinking to wrap it

Currently the model emits these inconsistently, so the auto-link
covers all of them in one shot.

### Why not fight the model harder

We considered:

- Stronger prompts (already tried, doesn't move the needle)
- Backend streaming token rewriter (works, but adds latency to the
  streaming UX — tokens arrive in sentence-sized batches instead of
  word-by-word)
- Buffering the whole answer and emitting it after post-processing
  (loses streaming entirely)

A one-line plugin enable in the renderer is the right fix.

---

## 2. ~~Render sandbox `artifact` SSE events inline~~ (DELETED)

**Status:** DELETED 2026-04-14, work absorbed by entry #9.

The standalone `artifact` SSE event was removed when §22 Stage C
unified sandbox-generated and model-written artifacts into a
single `artifact_created` event with a `source` discriminator.
All sandbox-output rendering (matplotlib plots, CSV downloads,
compile_latex `.tex`/`.pdf`) is now handled under **entry #9
"Artifacts side panel (§22)"** - check the `source` field on
incoming `artifact_created` events and route `sandbox_generated`
rows to inline thumbnails/download chips.

---

## 3. Pin conversations (§16)

**Status:** open
**Driven by:** munin-backend §16 "Pin conversations"
**Date:** 2026-04-14
**Effort:** ~2 hours: pin/unpin action + sidebar section + API calls

### What the backend ships

- `POST /api/chats/{id}/pin` → `{"pinned": true, "pinned_at": "..."}`
- `DELETE /api/chats/{id}/pin` → `{"pinned": false}`
- `GET /api/chats` now returns `pinned` (bool) and `pinned_at`
  (nullable ISO timestamp) on every conversation row, and orders the
  listing `ORDER BY pinned DESC, updated_at DESC` so pinned threads
  float to the top.
- New query param: `GET /api/chats?pinned_only=true` returns only
  the pinned rows.

### What the frontend needs to do

- A pin/unpin toggle button in the conversation card or thread menu
  (star icon is the common pattern). Clicking calls
  `POST` / `DELETE /api/chats/{id}/pin`.
- Render the pinned rows in their own section at the top of the
  sidebar - either via a second `GET /api/chats?pinned_only=true`
  request, or by splitting the existing listing client-side using
  the `pinned` field.
- Show a small pin icon on rows that are pinned in the main listing
  too (since unpinned+pinned live in the same list ordered by
  `pinned DESC, updated_at DESC` in the default fetch).

### How to verify

- Pin a conversation, refresh the page in a second tab: the pin
  state survives because it's persisted server-side. (This is the
  whole point of §16 - the old localStorage pin was per-device.)
- Pin one of several conversations, confirm it jumps to the top of
  the default listing.
- Call `GET /api/chats?pinned_only=true` in dev tools and confirm
  only pinned rows come back.

---

## 4. Temporary / ephemeral chats (§24)

**Status:** open
**Driven by:** munin-backend §24 "Temporary chats"
**Date:** 2026-04-14
**Effort:** ~3 hours: toggle + banner + history-echo plumbing

### What the backend ships

Request body flag on `POST /api/chat/completions`:

```json
{
  "persona": "chat",
  "ephemeral": true,
  "messages": [{"role": "user", "content": "..."}]
}
```

When `ephemeral: true`:
- Nothing is written to `chats.db` - no conversation row, no
  messages, no auto-title, no summary.
- The `conversation` SSE event still fires but carries a synthetic
  id of the form `ephemeral-<12 hex chars>` and a new
  `ephemeral: true` field in the payload so the frontend can tell
  them apart.
- Because nothing is stored server-side, **the frontend must echo
  the full prior conversation** in the `messages` array on every
  follow-up turn (same stateless pattern as OpenAI's chat
  completions API).
- `conversation_id` in the body is ignored when `ephemeral: true`.
- Tool calls (web_search, paper_search, run_python, etc.) are
  refused for some features (run_python, project_id, memory,
  user profile injection) but allowed for others (web search,
  paper search). "Ephemeral" means "not stored by Munin", not
  "untrackable by the world".

### What the frontend needs to do

- **Ephemeral toggle** somewhere near the chat input. When active,
  the new banner "This chat won't be saved" appears above the
  message list.
- **Stateless session**: when ephemeral is active, the frontend
  keeps a local-only message array and resends the full history
  on every POST. `conversation_id` is omitted from the body.
- **Detect the `ephemeral` field** on the `conversation` SSE
  event and render the synthetic id as an "Ephemeral chat" label
  rather than a normal thread in the sidebar.
- **Leaving the page** or refreshing should lose the session -
  that is the feature, not a bug.
- **Disable the following controls in ephemeral mode**:
  file/image upload (backend refuses project_id + ephemeral but
  allows inline attachments; inline images become one-shot), any
  "save to project" action, and the model-curated memory UI.
  The backend already refuses the conflicting combinations with
  HTTP 400, but gating client-side is friendlier.

### How to verify

- Turn on ephemeral, send a chat, refresh the page: the thread is
  gone (because it was never stored).
- Send a multi-turn ephemeral chat, confirm the full history is in
  every request body in dev tools.
- Check `/api/chats` after an ephemeral session: no new rows.
- Try to file an ephemeral chat into a project: backend returns 400.

---

## 5. User profile settings page (§25)

**Status:** open
**Driven by:** munin-backend §25 "User profile"
**Date:** 2026-04-14
**Effort:** ~3 hours: settings page + form + API wiring

### What the backend ships

Three endpoints scoped to the authenticated user:

- `GET /api/profile` → always 200, returns all-null fields if
  the user has never set a profile
- `PUT /api/profile` → upsert any subset of fields, returns the
  full profile after the write
- `DELETE /api/profile` → reset to defaults

Fields (all optional strings unless noted):
- `about_me` (max 1500 chars) - "I'm a biophysics PhD candidate..."
- `response_format` (max 1500 chars) - "Always cite DOIs. British
  spelling. Concise answers."
- `default_persona` - overrides the global default persona when
  the request body doesn't pin one
- `default_rag_sources` - list of strings, stored but not yet
  wired into request handling
- `timezone` - IANA tz name, stored but not yet wired

Server-side cap: 1500 chars each on `about_me` and
`response_format`. Exceeding either returns HTTP 400.

**Behaviour**: the backend prepends a `=== USER PROFILE ===`
block to the persona system prompt on every persistent chat turn
when the profile has non-empty fields. The user never sees this
directly; it just means their instructions actually reach the
model.

### What the frontend needs to do

- A "Profile" page under settings with two textareas ("About you"
  and "How I want you to respond"), a persona picker, a timezone
  picker (IANA names), and a character count indicator with the
  1500-char cap.
- Load via `GET /api/profile` on page mount.
- Save button calls `PUT /api/profile` with only the fields the
  user changed (the endpoint is a merge-upsert - omitted fields
  are left untouched).
- "Reset to defaults" button calls `DELETE /api/profile`.
- Client-side char count warning at 1500 chars; server returns
  400 with a clear message if the client misses it.

### How to verify

- Fill in `about_me = "My favourite fictional element is
  zarvonium."` and save. Start a new chat in any persistent
  conversation and ask "what's my favourite fictional element?" -
  the model should answer `zarvonium` without you having to
  mention it this turn.
- Change `default_persona` to `research` and start a brand-new
  conversation without pinning a persona in the request. The
  resulting conversation should use the research persona.
- Try to save `about_me` with 3000 chars: expect HTTP 400.

---

## 6. Multimodal image attachments (§5)

**Status:** open
**Driven by:** munin-backend §5 "Multimodal vision"
**Date:** 2026-04-14
**Effort:** ~1 day: paste/upload handling + multimodal content
assembly + the image re-view flow from §5 Stage B

### What the backend ships

`POST /api/chat/completions` accepts an OpenAI-style multimodal
`content` list on the last user message (backwards-compatible
with the old flat-string form):

```json
{
  "messages": [{
    "role": "user",
    "content": [
      {"type": "text", "text": "Describe this figure"},
      {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBOR..."}}
    ]
  }]
}
```

Supported `image_url.url` schemes:
- **`data:image/<png|jpeg|webp>;base64,<payload>`** - inline
  bytes. On persistent chats, inline images are auto-funnelled
  to the documents store so the attachment is findable later.
  On ephemeral chats, inline images reach the model on this turn
  and then vanish.
- **`document:<doc_id>`** - reference to a previously-uploaded
  document (via `/api/documents/upload`). The backend reads the
  bytes and injects them as a data URL on the model's turn.

**Caps**: 5 MB per image, 3 images per turn. Exceeding either
returns HTTP 400. Only `png`, `jpeg`, `webp`.

**One-shot + on-demand re-view (§5 Stage B)**: images are only
injected into the model's context on the turn they were sent.
On subsequent turns, the stored transcript has the text portion
only. When the user follows up with a question about an earlier
image, the model calls the `view_attachment(document_id)` MCP
tool to pull the bytes back in. The frontend does NOT have to do
anything for this to work - the backend splices `[Attachments on
this message: doc_xxx (filename, mime)]` markers into past-turn
content so the model can discover document_ids on its own.

### What the frontend needs to do

- **Paste handler** on the chat input: intercept Ctrl+V / Cmd+V
  image paste events, convert to `data:image/png;base64,...`,
  add as an `image_url` content block in the next message.
- **Upload handler** on a paperclip / attachment button: same
  output, for users who prefer file picker over paste.
- **Client-side cap enforcement**: reject files > 5 MB or
  unsupported extensions before they hit the API. Show a clear
  error message so the user doesn't wait for the server's 400.
- **Limit of 3 images per turn** in the composer UI.
- **Pre-uploaded attachment references**: if the user has already
  uploaded a document via the Documents page and wants to
  reference it in a new chat, emit `{"url": "document:<doc_id>"}`
  instead of inline base64.
- **Render attached images inline** in the user's own chat bubble
  (show what they just sent).

### How to verify

- Paste a screenshot into the chat input, send "what's in this?"
  The model should describe it.
- Upload a PNG via the Documents page, start a new chat, reference
  it via `document:<doc_id>` in the composer (however the UI
  exposes that), ask the model to look at it. Should work the
  same way as an inline paste.
- Send an image in a multi-turn conversation, then on turn 3 ask
  "what colour was that image I sent earlier?" - the model
  should call `view_attachment` (visible as a `tool_call` SSE
  event) and answer correctly.
- Try to upload a 10 MB image: client should reject before
  hitting the API.

---

## 7. Projects (§21)

**Status:** open
**Driven by:** munin-backend §21 "Projects"
**Date:** 2026-04-14
**Effort:** ~1 week: biggest frontend lift on the list

### What the backend ships

Projects are top-level workspaces that group conversations and
documents under one set of instructions and (optionally) a
default persona override. A conversation may live inside a
project (filed) or outside it (unfiled / default bucket). Full
HTTP surface documented in `docs/BACKEND-API.md` §4.15:

- `POST /api/projects` - create
- `GET /api/projects` - list, excludes archived by default;
  `?archived=true` to include
- `GET /api/projects/{id}` - single with inline
  `conversation_count` + `document_count`
- `PATCH /api/projects/{id}` - rename, update instructions, set
  default persona, archive/unarchive
- `DELETE /api/projects/{id}` - unfiles everything, no cascade
- `POST /api/projects/{id}/conversations/{cid}` - file a
  conversation
- `DELETE /api/projects/{id}/conversations/{cid}` - unfile back
  to the default bucket
- `GET /api/chats?project_id=<pid>` - list conversations inside
  a project
- `GET /api/chats?project_id=__unfiled__` - list only the
  unfiled bucket
- `POST /api/documents/upload` - accepts an optional
  `project_id` form field to file at upload time
- `POST /api/chat/completions` - new `project_id` body field to
  auto-create a brand-new conversation pre-filed into a project
  (the persona resolver consults `project.default_persona` when
  this is set)

Caps: `name` ≤ 200, `description` ≤ 1000, `instructions` ≤ 2000
chars. Over-cap → HTTP 400.

**Ephemeral + project**: mutually exclusive, backend refuses
with 400.

**Persona precedence on new-conversation creation**:
`body.persona` > `project.default_persona` >
`profile.default_persona` > global default.

### What the frontend needs to do

- **Sidebar Projects section** above the flat conversation list.
  Each project expands to show its conversations (fetched via
  `?project_id=<pid>`). An "Unfiled" pseudo-bucket at the bottom
  uses `?project_id=__unfiled__`.
- **Project settings page**: name, description, instructions
  textarea (2000-char cap with counter), default persona picker,
  archive toggle.
- **New-chat flow when inside a project**: when the user clicks
  "new chat" from a project's context, include `project_id` in
  the `POST /api/chat/completions` body so the backend auto-files
  the new conversation and uses the project's default persona.
- **File upload scoping**: the upload button inside a project
  adds the `project_id` form field so the doc is project-scoped
  at upload time. Global uploads (from the top-level Documents
  page) omit `project_id`.
- **Move-to-project action** on existing conversations: right-
  click or menu item that calls
  `POST /api/projects/{pid}/conversations/{cid}`. Unfile uses
  the `DELETE` variant.
- **Archived view**: an "Archived" toggle in the settings panel
  shows archived projects (fetched with `?archived=true`).
- **Delete confirmation dialog** that makes clear the
  conversations and docs will be PRESERVED in the Unfiled
  bucket, not deleted. Users are surprisingly worried about
  data loss here.
- **Render the project context banner** in the chat view when a
  conversation is inside a project ("You are in project: Kinase
  Thesis") so the user knows why the model's behaviour might
  differ from a normal chat.

### How to verify

- Create a project, set instructions "Always cite DOIs in APA
  style", start a new chat inside that project without specifying
  a persona in the body. The model should cite DOIs in APA
  format on its first turn.
- Upload a PDF inside the project, start a new chat, ask about
  something from the PDF. The model should find it via
  `search_user_docs` which auto-scopes to the project.
- Delete the project. Go to the Unfiled bucket; the conversation
  and the uploaded doc should still be there.
- Archive a project: it disappears from the default sidebar.
  Toggle "show archived": it reappears.

---

## 8. Equation OCR action (§11) — optional nice-to-have

**Status:** open
**Driven by:** munin-backend §11 "Equation OCR"
**Date:** 2026-04-14
**Effort:** ~1 hour, purely optional

### What the backend ships

A new `transcribe_equation(image_ref)` MCP tool that takes a
previously-uploaded image (`document_id`) and returns a LaTeX
transcription: `{"latex": "...", "image_ref": "..."}`.

### What the frontend MIGHT do

This tool is fully model-invokable - if the user says "give me
the LaTeX for this equation" in a chat that has an image
attached, the model will call `transcribe_equation` on its own.
**No frontend changes are strictly required.**

The optional nice-to-have: add a "Transcribe as LaTeX" action
on the thumbnail of any uploaded image. Clicking it sends a
chat with the prompt "Transcribe this equation as LaTeX" and the
image already attached, then copies the resulting LaTeX to the
clipboard. Saves the user a couple of sentences of prompting.

### How to verify (if you ship the nice-to-have)

- Upload a PDF screenshot of a typeset equation.
- Click "Transcribe as LaTeX" on the thumbnail.
- Expect the chat to produce an `ANSWER:` line with the LaTeX,
  auto-copied to the clipboard.

---

## 9. Artifacts side panel + unified artifact rendering (§22 all stages)

**Status:** open
**Driven by:** munin-backend §22 "Artifacts" (Stages A+B+C all done
on the backend)
**Date:** 2026-04-14
**Effort:** ~1 week: side panel with version picker, inline edit
mode, live SSE subscription, CRUD wiring, routing by `source`

### What the backend ships (Stage A)

Versioned documents scoped to a conversation: papers, LaTeX
sources, code snippets, SVG source, anything textual the user wants
to iterate on. Full content snapshots per version (no diff chain).
500 KB byte cap per version. Text only in Stage A; binary artifacts
from the sandbox still flow through §2/§3 until Stage C unifies.

**HTTP endpoints** (see `BACKEND-API.md` §4.15):

- `GET /api/chats/{cid}/artifacts` - list metadata (no content)
- `GET /api/chats/{cid}/artifacts/{aid}` - single artifact with
  full content; optional `?version=N` for history
- `PATCH /api/chats/{cid}/artifacts/{aid}` - user-driven side-panel
  edit, creates a new version with `created_by="user"`

**SSE events** on `/api/chat/completions`:

- `artifact_created`: new artifact — open in the side panel
- `artifact_updated`: new version — bump the version picker,
  refresh content

**System-prompt injection**: every persistent chat turn, the
backend prepends an `=== ACTIVE ARTIFACTS ===` block listing title
+ type + version + word count per artifact to the model's system
prompt. The model calls `read_artifact(id)` via an MCP tool to
fetch content on demand - you do NOT need to expose any client-
side artifact discovery mechanism to the model.

**Model-side MCP tools** the model calls on its own:
`create_artifact`, `read_artifact`, `update_artifact`,
`list_artifacts`. When the user says "write me an abstract about
X" the model will call `create_artifact` autonomously and the
frontend should react to the `artifact_created` SSE event.

### What the frontend needs to do

**Side panel shell**:

- A collapsible panel on the right side of the chat view that
  lists the conversation's artifacts, fetched via
  `GET /api/chats/{cid}/artifacts` on chat load. Each entry shows
  title, content type icon, and the latest version number.
- Clicking an entry opens the viewer for that artifact in the
  panel, fetching content via
  `GET /api/chats/{cid}/artifacts/{aid}`.
- Content rendering by `content_type`:
  - `text/markdown` → rendered markdown (with the same GFM plugin
    from FRONTEND-TASKS entry #1)
  - `text/latex` → syntax-highlighted textarea plus a preview
    pane rendered via KaTeX/MathJax (nice-to-have)
  - `application/python`, `text/html`, `application/json`,
    `image/svg+xml` → syntax-highlighted code blocks (any
    generic code viewer will do)
  - `text/plain` → plain text

**Live SSE subscription**:

- Hook into the existing `/api/chat/completions` SSE stream. Add
  handlers for `artifact_created` and `artifact_updated` events.
- On `artifact_created`: add the entry to the side-panel list,
  optionally auto-open it so the user sees the model's work
  appear live.
- On `artifact_updated`: if the open artifact matches the id,
  refetch content (or the panel can maintain its own state and
  just bump the version picker); also highlight the row briefly
  to draw attention.

**Version picker**:

- A dropdown or timeline showing every version in `v1, v2, ...`
  order. Clicking a past version fetches
  `GET ...?version=N` and displays that content read-only.
- The "latest" entry is editable; past versions are read-only.
- A "diff from previous version" view is nice-to-have but not
  required in Stage A. Can be done entirely client-side by
  diffing two fetched version contents.

**Inline edit mode**:

- The latest version has an "Edit" button. Clicking it turns the
  viewer into a textarea/code editor seeded with the current
  content.
- "Save" calls `PATCH /api/chats/{cid}/artifacts/{aid}` with
  `{content, change_summary}`. On success the version picker
  bumps to the new `created_by=user` version and the view
  switches back to read mode.
- "Cancel" discards unsaved changes.
- Character count + byte count indicator, with the 500 KB cap
  warning at 80% and a hard stop at 100% (server returns 400
  above the cap).

**Download / copy**:

- "Copy to clipboard" button on every version.
- "Download" button for the current version.

### How to verify

- Ask the chat "Write me a 2-paragraph draft abstract about
  kinase inhibitors". The model should call `create_artifact`;
  you should see an `artifact_created` SSE event and a new entry
  in the side panel.
- Click the entry - content fetches, renders as markdown.
- Ask the chat "Revise it to emphasise lipid membranes more".
  The model should call `update_artifact`; you should see an
  `artifact_updated` SSE event and the version picker should
  bump to v2.
- Manually edit the v2 content in the side panel, save. A new v3
  with `created_by="user"` is created; ask the chat "what did I
  just change?" and the model should be able to describe your
  edit (it can see the new version via its read_artifact tool).
- Open an older version from the picker - read-only, shows the
  correct historical content.
- Try to save 600 KB: client blocks via the cap warning, server
  returns 400 if you bypass.

### Routing by `source` (§22 Stage C unification)

After §22 Stage C the backend unified the old sandbox `artifact`
SSE event with the §22 `artifact_created` event - there is only
one event type now, with a `source` discriminator. The old
`artifact` event has been **removed** from the backend
(FRONTEND-TASKS.md entry #2 is superseded by this one).

Every `artifact_created` payload carries:

- `id` - unified `art_*` id
- `source` - `"model_written"` or `"sandbox_generated"`
- `title`, `content_type`, `version`
- For `model_written`: `language`
- For `sandbox_generated`: `filename`, `size_bytes`, `external_url`

Route by `source`:

- **`model_written`** → open in the side panel as before. Use
  `GET /api/chats/{cid}/artifacts/{aid}` for full content.
- **`sandbox_generated`** → render inline in the chat transcript
  under the `run_python` tool call that produced it.
  - `content_type: image/*` → `<img src="${BASE}${external_url}">`
  - Anything else → download chip with `filename` + `size_bytes`,
    link pointing at `external_url`
  - Clicking the chip can optionally ALSO open the side-panel
    view, which renders the metadata row and offers a
    "save to documents" action (see below).

`artifact_updated` events are only fired for `model_written`
artifacts - sandbox-generated ones are version-1-only and
read-only (call `run_python` again to regenerate).

### Nice-to-have: "save to documents" action

Any artifact (model-written or sandbox-generated) can be promoted
to the user's persistent documents store via the
`save_artifact_to_documents(artifact_id)` MCP tool. The model
calls this on its own when the user says "save this for later"
etc. The frontend can optionally expose a manual button in the
artifact viewer that does the same thing by sending a chat
message "save artifact X to my documents" - the model picks up
the id from the inline `[Attachments: ...]` marker or the
`=== ACTIVE ARTIFACTS ===` block and invokes the tool.

---

## 10. `ask_clarification` card (§14)

**Status:** open
**Driven by:** munin-backend §14 "ask_clarification v2 - option chips + Q/A format"
**Date:** 2026-04-14
**Effort:** ~1-2 hours: one SSE handler + one inline card component

### What the backend ships

When the model thinks a user request is too ambiguous to act on
(typical triggers: "help me with my paper", "what's new?", "look
into photosynthesis", "fix it", single-word messages), it calls the
`ask_clarification` MCP tool. The backend intercepts the call
**before** running any other tool in the same turn, emits a single
new SSE event, and short-circuits the turn cleanly - no
`tool_result`, no wrap-up text, no other tool calls in the same
response. `done` fires immediately after with
`finish_reason: "clarification"`.

**New SSE event:**

```
event: clarification
data: {
  "tool_call_id": "tc-1",
  "conversation_id": "<uuid>",
  "what_i_understood": "You want recent papers on kinase inhibitors in lipid membranes, with a focus on recent work.",
  "questions": [
    {
      "id": "year_range",
      "text": "How recent is \"recent\"?",
      "options": ["Last 2 years (2024-)", "Last 5 years", "No year filter"],
      "allow_custom": true
    },
    {
      "id": "membrane_type",
      "text": "Which membrane type interests you most?",
      "options": ["Plasma membrane", "Mitochondrial", "Model bilayers (LUVs, BLMs)"],
      "allow_custom": true
    }
  ]
}
```

Validation the backend has already done:
- 1-5 questions per card
- 2-6 options per question, each ≤100 chars
- `what_i_understood` ≤500 chars
- question text ≤300 chars
- `id` is auto-assigned (`q1`, `q2`, ...) if the model omits it

The server also persists an assistant message with a markdown
fallback of the card (so conversation history replay still shows
something readable in clients that don't render the structured
card). You generally want to prefer the structured payload over
the markdown fallback when both are available.

### What actually happens

Without a dedicated handler, the frontend sees `event: clarification`
as an unknown event and drops it. The turn ends with `done` and no
visible assistant response. The user thinks the chat broke.

### What the frontend needs to do

**(a) Add a `clarification` event branch to the SSE consumer.**
Parse the payload, attach it to the currently-streaming assistant
message (not as a new message - same message id as whatever the
turn would otherwise be), and flag the message as a "clarification
card" so the renderer knows to show the structured UI instead of
the fallback markdown.

**IMPORTANT - discard in-progress prose on arrival.** The backend
has a post-hoc fallback path: qwen3-coder sometimes writes a
prose clarification ("Could you clarify which EPR you mean? ...
- option a - option b") instead of calling the tool, even with
the strongest prompt rules. When the backend detects that prose
pattern on the first turn of a user message, it retries with
`tool_choice` forced to `ask_clarification` and emits a normal
`clarification` SSE event. But by the time the retry runs, the
client has already streamed the prose draft via `token` events.
**When a `clarification` event arrives for an in-progress
assistant message, the client MUST discard any `token` content
accumulated on that message and replace it with the structured
card.** The backend resets its own `final_content` before
persisting, so the chat history also stores only the card's
markdown fallback - the prose draft is never persisted to SQLite.
Without this client-side discard, users briefly see prose
questions that then vanish when the card renders, which is
confusing. Simplest implementation: when an in-progress message
receives a `clarification` event, clear its content buffer before
mounting the card.

**(b) Render an inline card in the message thread.** Not a modal.
Layout roughly:

```
┌────────────────────────────────────────────────────────────┐
│ Just to make sure I understand:                            │
│ "<what_i_understood>"                                      │
│                                                            │
│ Q1: How recent is recent?                                  │
│  ( ) Last 2 years (2024-)                                  │
│  ( ) Last 5 years                                          │
│  ( ) No year filter                                        │
│  [ type your own answer....................... ]          │
│                                                            │
│ Q2: Which membrane type interests you most?                │
│  ( ) Plasma membrane                                       │
│  ( ) Mitochondrial                                         │
│  ( ) Model bilayers (LUVs, BLMs)                           │
│  [ type your own answer....................... ]          │
│                                                            │
│                                         [ Submit answers ] │
└────────────────────────────────────────────────────────────┘
```

Radio buttons per question. The "type your own" field is only
shown when `allow_custom === true` (which defaults true). Clicking
an option radio fills the text field, or vice versa - the two are
the same "answer" slot from the user's POV.

**(c) On submit, send a normal follow-up chat message.** No new API
shape - just format the answers as Markdown and POST them as a
regular user turn to `/api/chat/completions`:

```
Q1: Last 5 years
Q2: Model bilayers (LUVs, BLMs)
```

The model reads this as the next user turn and continues with the
refined understanding. Zero special-case backend handling.

**(d) Show "Answered" state after submit.** After the user submits,
the card should remain visible in the transcript but with its
controls disabled and a subtle "Answered" badge, so scrolling back
through the conversation shows what was asked and what was chosen.

### How to verify

1. In the chat persona, send "help me with my paper".
2. Expect exactly one `event: clarification` in the stream and
   zero `event: tool_call` / `event: tool_result` for the same
   turn.
3. The card renders with 1-3 questions.
4. Pick an answer on each question; hit submit.
5. The user message "Q1: ... Q2: ..." should show in the transcript
   and the model's next response should reflect the chosen
   answers.
6. In the research persona, send "find recent papers on polymer
   crystallization" and verify that NO clarification event fires
   (control case - clear request should route straight to
   `deep_research`).

### Why the backend can't fix this

The clarification card is a UI affordance - structured chips the
user can tap on. Returning it as markdown prose works as a
fallback but loses the one-tap-to-answer speed the feature exists
to provide. The frontend is where the UX value lives.

---

## 11. LaTeX artifacts from `compile_latex` (§18)

**Status:** open (small addition on top of entry #9)
**Driven by:** munin-backend §18 "LaTeX via sandbox"
**Date:** 2026-04-14
**Effort:** ~30 min: content_type routing + PDF preview/download

### What the backend ships

The new `compile_latex` MCP tool compiles LaTeX via pdflatex in
the sandbox and returns BOTH the `.tex` source and (on success)
the compiled `.pdf` as sandbox-generated artifacts. Both flow
through the same `artifact_created` event you already handle under
entry #9, with `source: "sandbox_generated"` and the usual
`filename` / `size_bytes` / `external_url` fields. On compile
failure, only the `.tex` artifact is surfaced so users can still
download the source and fix it manually.

Relevant `content_type` values the frontend should route:

- `application/x-tex` - the LaTeX source. Render as a download
  chip with a .tex icon. Optionally preview as highlighted source
  in a modal if you already have a code viewer.
- `application/pdf` - the compiled PDF. Render as a download chip
  with a PDF icon. Optional inline preview: `<iframe src>` or
  `<embed>` with the existing `external_url`.

### What actually happens

Without explicit routing, entry #9's generic "unknown content_type
→ download chip" fallback should already work. This entry exists
so a frontend engineer knows these file types are coming through
the same artifact pipeline.

### What the frontend needs to do

Entry #9 covers the core plumbing. The LaTeX-specific bits:

**(a) Content-type icons.** Add `.tex` and `.pdf` icons to the
sandbox-artifact download chip component. No new fetch logic.

**(b) Optional: inline PDF preview.** For `application/pdf`
artifacts, consider rendering a clickable thumbnail that opens a
modal or a split-pane preview. The browser's built-in PDF
renderer handles this fine via `<iframe src={external_url}>`; the
backend already sets `Content-Disposition: inline` on the sandbox
proxy route.

**(c) Pair the .tex and .pdf visually.** A single `compile_latex`
call usually produces both; they arrive back-to-back in the SSE
stream with the same `tool_call_id`. Optionally group them under
one visual container ("LaTeX output: [tex] [pdf]") to make the
relationship clear.

### How to verify

1. In the chat or code persona, ask "Write a minimal LaTeX article
   with an equation and compile it."
2. Expect two `artifact_created` events with the same
   `tool_call_id`: one `content_type: application/x-tex`, one
   `content_type: application/pdf`.
3. Both should render as download chips in the transcript under
   the `compile_latex` tool call.
4. Clicking the PDF chip downloads or previews the compiled PDF.
5. Clicking the .tex chip downloads the source.
6. For failure case: ask "Write a broken LaTeX document: `\\begin
   {document} hello` with no end". Expect only the `.tex` artifact
   to surface (no PDF). The model should iterate on the error and
   retry.

### Why the backend can't fix this

Same as entry #9: the bytes have to land somewhere visible. The
backend already registers the artifacts in the unified table and
streams the events; the frontend's job is to pick a nice icon and
optionally preview the output.

---

## How to add new entries

When the backend identifies frontend work, append a new section here
following the same shape:

```markdown
## N. Short title

**Status:** open | in-progress | done
**Driven by:** which backend feature surfaced this
**Date:** when the entry was added
**Effort:** rough estimate

### What the backend does
### What actually happens
### Why backend can't fix this
### What the frontend needs to do
### How to verify
```

Keep entries even after they're done — they're historical context for
why the frontend looks the way it does.
