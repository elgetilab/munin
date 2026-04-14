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

**Status:** open
**Driven by:** munin-backend §2 "Python sandbox"
**Date:** 2026-04-14
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
