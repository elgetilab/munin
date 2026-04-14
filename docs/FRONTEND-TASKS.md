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

## 2. Render sandbox `artifact` SSE events inline

**Status:** DEPRECATED - superseded by entry #9 after §22 Stage C
**Driven by:** munin-backend §2 "Python sandbox"
**Date:** 2026-04-14 (deprecated same-day by §22 Stage C)
**Effort:** n/a

**DO NOT IMPLEMENT THIS ENTRY AS WRITTEN.** §22 Stage C unified the
sandbox artifact path with the model-written artifacts path. The
old standalone `artifact` SSE event has been **removed** from the
backend. Sandbox-generated files now fire `artifact_created` events
with `source: "sandbox_generated"` alongside the usual fields
(`filename`, `size_bytes`, `external_url`), and the frontend work
is described under **entry #9 "Artifacts side panel (§22)"** below.
The routing logic lives there: check the `source` field on
incoming `artifact_created` events and render sandbox-generated
ones as inline thumbnails/download chips in the chat transcript,
and model-written ones as side-panel entries.

The rest of this entry is kept for historical context. Skip to
entry #9 for what the frontend actually needs to do.

**Effort:** ~30-50 lines: a new SSE event handler + an inline renderer

### What the backend does

The new `run_python` MCP tool runs Python code in a per-conversation
Jupyter kernel inside the sandbox sidecar container. When the kernel
produces matplotlib figures (or any `display_data` PNG), the backend:

1. Persists the bytes to the sandbox's per-conversation scratch
   directory.
2. Emits a new SSE event on the `/api/chat/completions` stream after
   the matching `tool_result`:

```
event: artifact
data: {"id": "<uuid hex>",
       "filename": "<id>.png",
       "content_type": "image/png",
       "size_bytes": 12345,
       "display_url": "/api/artifacts/<conversation_id>/<artifact_id>",
       "conversation_id": "<conv id>",
       "tool_call_id": "tc-2"}
```

3. Exposes `GET /api/artifacts/{conversation_id}/{artifact_id}` which
   returns the raw file bytes with the correct `Content-Type`. Auth
   piggybacks on `X-Munin-Email` plus a server-side ownership check
   against the conversation row.

### What the frontend needs to do

Two things:

**(a) Add an `artifact` event handler to the SSE consumer.** Wherever
you currently switch on `event: token`, `event: tool_call`, etc.,
add an `event: artifact` branch. Push the parsed payload onto the
current message's `artifacts` array (alongside `tool_calls`), keyed
by `tool_call_id` so you can render each artifact under the call
that produced it.

**(b) Render artifacts inline in the assistant transcript.** For each
artifact attached to a message, render based on `content_type`:

- `image/*` -> `<img src="${BASE}${display_url}" alt="${filename}" />`
  Click-to-expand with the same URL is nice-to-have. The image
  inherits the user's auth automatically because it's on the same
  origin.
- Anything else -> a download chip / link with the filename, file
  size (use `size_bytes`), and an icon based on extension. Clicking
  triggers a normal browser download from `display_url`.

Place the artifact block right under the corresponding tool call in
the TaskLog (you already have a similar nesting for `agent_*`
events).

### Important: auth on the image URL

`display_url` is a same-origin path on the retrieval service. The
browser will send cookies / forward-auth headers automatically as
long as the image request hits the same domain that served the chat
page. No code change needed beyond pointing `<img src>` at the URL.
If the frontend ever fetches images via `fetch()` for some reason,
make sure to include the same auth headers as other API calls.

### Test plan for the frontend

1. Open a regular (non-ephemeral) chat with the chat persona.
2. Ask: "Plot y = x^2 from 0 to 5 with matplotlib."
3. The model should call `run_python` with a small matplotlib
   snippet. After the `tool_result` arrives, expect an `artifact`
   event with `content_type: image/png`.
4. The image should render inline below the tool call, with a
   visible plot of the parabola.
5. Ask: "Now save the values to a CSV and let me download it."
6. The model produces a CSV artifact. The frontend should render it
   as a download chip rather than an inline image.
7. Open the same chat in a second tab as the same user. The
   artifacts should still be fetchable (they live in the sandbox's
   scratch dir, not in the SSE event itself).
8. Open the chat URL while logged in as a different user. Artifact
   `<img>` should 404 (the backend enforces ownership).

### Why the backend can't fix this

The artifact bytes have to land in front of the user's eyes
somewhere, and the model can't paste them inline as base64 without
blowing the SSE budget and the assistant's own context window. The
streaming `display_url` + frontend renderer is the only architecture
that scales.

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
