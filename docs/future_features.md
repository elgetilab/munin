# Future Features — munin-backend

Design notes for features that are planned but not yet implemented. When
we come back to build one of these, the notes below should give enough
context to pick up without a rediscovery pass.

---

## 1. `ask_clarification` MCP tool

### Problem

The model never asks follow-up questions on ambiguous user input. It
dives straight into `deep_research` or individual tool calls even when
the request is underspecified (e.g. *"help me with my paper"*, *"what's
new?"*, *"fix this"*, single-word messages). This wastes tool budget and
often answers a question the user didn't ask.

Our recent prompt tuning pushed the model *toward* decisive action
("trust the tools, answer confidently, stop iterating"), which actively
fights any soft "ask clarification when unclear" guidance. We need a
first-class affordance the model can invoke, not just a prompt nudge.

### Approach — dedicated MCP tool

Add a tool the model calls when it wants to pause and ask the user:

```python
ask_clarification(
    what_i_understood: str,    # one-line restatement of the request
    questions: list[str],      # 1-3 specific clarifying questions
) -> dict
```

When `chat_service` sees this tool name in a tool_call, it takes a
special fast-exit path:

1. Emit the `what_i_understood` + `questions` as normal `token` events
   (so the frontend renders them like any other assistant message).
2. Short-circuit the main loop — do NOT execute any other tool calls
   from the same turn, do NOT re-enter vLLM for a synthesis pass.
3. Persist the assistant message with the clarification text as its
   `content`, and stash `tool_calls=[{name:"ask_clarification",
   arguments:..., result:{"status":"awaiting_user_response"}}]` so the
   next turn can see what the model asked.
4. Emit `done` and close the stream cleanly.

The next user message flows through the normal loop with the
clarification exchange now in `conversation.messages`. The model sees
both its own question and the user's answer in context.

### Why a dedicated tool instead of just prompting

We tried soft prompt guidance with multi-query fan-out in Phase 1 and
the model picked it up immediately. But that was adding *more* work on
top of existing instructions. Clarification is the *opposite* — we want
the model to stop and wait. Every other instruction in our prompts
pushes toward action. A dedicated tool gives the model a clear
mechanical affordance: "I don't know what you want → call this tool".
The schema description becomes the decision rule.

### Implementation sketch

Files to touch:

- `retrieval/mcp/tools/clarification.py` — new module, 20-30 lines:
  - `async def ask_clarification(what_i_understood, questions) -> dict`
    returns `{"status": "awaiting_user_response", "questions_asked":
    questions}`. The backend never actually invokes this tool normally;
    the dispatch path in `chat_service` intercepts it before the generic
    executor runs. But the function is still registered as a fallback
    so `/mcp/call` tests can exercise it.
- `retrieval/mcp/schemas.py` — add the tool definition. Description is
  the decision rule: when to use vs when not to use, with concrete
  examples on each side.
- `retrieval/mcp/executor.py` — add a dispatch case (for completeness;
  normal execution is intercepted upstream).
- `retrieval/mcp/tools/__init__.py` — export.
- `retrieval/chat_service.py` — the interesting part:
  1. After `_stream_vllm_once` finalises tool calls, scan for
     `name=="ask_clarification"` before kicking off `_run_tool_calls`.
  2. If found, extract `what_i_understood` + `questions`, format as
     Markdown (e.g. `"{what_i_understood}\n\n- {q1}\n- {q2}\n- {q3}"`),
     emit as `token` events so the frontend renders it, set
     `final_content` to the rendered text, skip the rest of the loop,
     jump straight to message persistence + `done`.
  3. Do NOT fire the wrap-up synthesis path. Do NOT reset the stream
     with `enable_tools=False`.

### Prompt changes

All three personas get a short block added to their tool usage
strategy section, e.g.:

> **When the request is ambiguous**, call `ask_clarification` instead
> of guessing. Examples that should prompt clarification: "help me with
> my paper", "what's new?", "look into this", single-word messages,
> requests with undefined scope. Only skip clarification when the
> request is concrete and unambiguous.

Curie gets a slightly different wording — research questions are more
often ambiguous about scope, so she should err harder toward
clarification on depth/time-range/sub-topic uncertainty.

### Test plan (add to `scripts/stress-test.py`)

Two-sided — we need to verify it triggers on ambiguous input AND does
NOT trigger on clear input.

**Expect clarification:**
- `test_ambiguous_help`: `"help me with my paper"`
- `test_ambiguous_whats_new`: `"what's new?"`
- `test_single_word`: `"fix it"`
- `test_no_scope`: `"look into photosynthesis"` (how deep? what angle?)

**Expect direct answer / tool use (controls):**
- `test_clear_factual`: `"what day is it today?"`
- `test_clear_research`: `"find recent papers on polymer crystallization"`
- `test_clear_code`: `"review this Python function: ..."`

For clarification-expected tests, assert that:
- Exactly one tool_call event with `name == "ask_clarification"` fires
- No other tool_calls fire in the same response
- The answer content contains a question mark (at least one `?`)
- The assistant message is short (< 1000 chars) compared to a normal
  research answer

For control tests, assert that `ask_clarification` is NOT in the
tool_calls list and the answer is substantive.

### Estimated effort

~1 hour of code + a deploy + a stress-test run. Distribution:

- Tool module + schema + executor dispatch: 15 min
- chat_service intercept path: 25 min (the fiddly part — need to
  make sure we don't accidentally fire wrap-up or double-persist)
- Persona prompt additions: 10 min
- Stress tests: 15 min
- Deploy + verify: 10 min

### Open questions

- **Frontend rendering**: should the clarification show up in the
  TaskLog as a tool call, or inline in the main message bubble? Latter
  is friendlier. This is a frontend decision — the backend just needs
  to emit the text so either works.
- **Forced clarification mode**: should there be a request flag like
  `{"require_clarification": true}` that the frontend can set on the
  first message of a new conversation? Probably no — the tool + prompt
  should be enough. Adding request-body flags is surface-area creep.
- **Multiple rounds**: if the model asks for clarification, gets an
  answer, and then still doesn't understand, does it ask again or
  guess? Current design: asks again freely. The model will converge
  after 1-2 rounds in practice.

---

## 2. Python sandbox (Jupyter kernel per conversation)

### Problem

The model has zero ability to compute. It can talk about matplotlib plots,
write Python scripts the user has to copy-paste into their own environment,
and describe what an Excel sheet should contain — but it cannot run any of
it. For a research assistant, this is a huge gap. Sandboxed execution
unlocks plotting, spreadsheet generation, Excel/docx/pdf output, code
checking, and interactive data analysis. It is the single biggest feature
on the roadmap.

### Design: Jupyter kernel per conversation

**One `ipykernel` process per active conversation, lifecycle-bound to the
conversation row.** Managed by a new service (either inside the retrieval
container as a thread/subprocess, or — preferred — a sidecar container).

Why Jupyter kernels instead of per-call subprocess:

- **Stateful**: variables, imports, and data live between calls. The model
  can run `import pandas; df = pd.read_csv(...)` once, then `df.head()` on
  the next turn, just like a human would. Maps perfectly to the iterate-
  on-errors loop: "run → see stderr → fix → re-run".
- **jupyter-client is mature**: ZMQ channels for stdin/stdout/display,
  well-documented message protocol, easy to drive from async Python.
- **Clean output separation**: `execute_result`, `stream` (stdout/stderr),
  `display_data` (images/files), `error` — all come back as distinct
  message types. Much nicer than parsing a single byte stream.
- **Kernel restart is cheap** (~200 ms) — on user command or on fatal error.

### Isolation

No GPU access (decided). No network access (decided). What we need:

- Separate Linux user (`munin-sandbox`) with no shell, no sudo, read-only
  home. Kernels run as this user.
- `firejail` or `bwrap` profile applied on kernel startup — blocks
  `/etc/`, `/opt/munin/`, the host network namespace, and everything else
  the researcher doesn't need.
- Writable directories:
  - `/scratch/{conversation_id}/` — ephemeral, dies with the conversation
  - `/data/user_docs/{email_hash}/` — read-only, so the kernel can read
    user-uploaded documents but cannot overwrite them
- Resource limits via cgroups or `resource.setrlimit`:
  - Wall clock: 30 s per execution (configurable per call via a tool param)
  - Memory: 2 GB per kernel
  - CPU: 2 cores
  - Open files: 256
  - File size: 100 MB per file

### Package set (pre-installed in the sandbox image)

Scientific Python stack, frozen at known-good versions, no pip install at
runtime. Initial list:

    numpy scipy pandas matplotlib seaborn scikit-learn
    openpyxl xlsxwriter python-docx reportlab Pillow
    biopython mdanalysis sympy networkx pyyaml
    requests httpx  # available but with network disabled, so they error
    jupyter-client ipykernel ipython

No `torch` / `jax` / `tensorflow` — too large, would require GPU to be
useful. Heavy ML work goes through SLURM (see feature §7).

### Pip install story

**User's question: can the sandbox install packages?** Yes, but via a
backend-mediated flow, not free `!pip install`:

- A new MCP tool `sandbox_install_package(name, version=None)`.
- The tool is executed **outside** the sandbox. The retrieval container
  runs `pip install --target /wheelhouse/{name} {name}`.
- After install, the sandbox image's `PYTHONPATH` is updated for the
  calling kernel so the package becomes importable.
- A whitelist in `config/sandbox_packages.yml` controls what's allowed.
  Start permissive (`allow_any: true`) and tighten if abuse appears.
- Wheels are cached per host so repeated installs are instant after the
  first.

This keeps the sandbox itself network-free while still letting users
install what they need. An alternative (free network inside sandbox, just
for pypi.org) is simpler but harder to audit.

### New MCP tools

    run_python(code: str, timeout_s: int = 30) -> dict
    #   → {"stdout", "stderr", "result", "error", "artifacts": [files]}

    sandbox_reset() -> dict
    #   → restart the kernel for this conversation

    sandbox_install_package(name: str, version: str = None) -> dict
    #   → {"installed": bool, "version": "...", "error": "..."}

    save_artifact_to_documents(artifact_id: str, filename: str) -> dict
    #   → copies a file from /scratch into the user's document store

### chat_service integration

New SSE event type: `artifact`. Fires whenever the sandbox produces a
file (generated plot, written .xlsx, etc.). Payload:

    event: artifact
    data: {"id": "...", "filename": "plot.png", "content_type": "image/png",
           "size_bytes": 12345, "display_url": "/api/artifacts/{id}",
           "caption": "histogram of chain ordering"}

The frontend renders the artifact inline using `content_type`. For
images, it shows a thumbnail with a click-to-expand. For docx/xlsx/pdf,
a download link with an icon.

Two more SSE events optional but useful:

    event: sandbox_stdout
    event: sandbox_stderr

Streamed in real time as the kernel produces them. Lets the user watch
long-running scripts. If not streaming, still captured and returned in
the `tool_result`.

### Test plan

- `test_sandbox_hello_world`: run `print("hi")`, expect stdout="hi\n".
- `test_sandbox_numpy`: run `import numpy; print(numpy.zeros(3))`.
- `test_sandbox_state`: run `x=1`, then on next call run `print(x)`,
  expect "1".
- `test_sandbox_timeout`: run `while True: pass`, expect timeout error.
- `test_sandbox_memory_cap`: run `a = [0]*10**9`, expect OOM kill.
- `test_sandbox_no_network`: run `import urllib.request;
  urllib.request.urlopen('http://example.com')`, expect network error.
- `test_sandbox_no_host_fs`: run `open('/opt/munin/config/munin.env')`,
  expect permission denied.
- `test_sandbox_plot`: run matplotlib code, expect an `artifact` event
  with an image.

### Estimated effort

2-3 days. Breakdown:

- Sandbox base image (Dockerfile + package list): half a day
- Jupyter client wrapper service + kernel lifecycle: 1 day
- MCP tools + schemas + executor wiring: half a day
- chat_service integration (artifact events, tool dispatch): half a day
- Test plan + stress tests: half a day
- Deploy + iterate on security hardening: half a day

### Open questions

- **Sidecar container or in-process?** Sidecar is cleaner (separate
  cgroups, easier to restart) but adds a network hop. In-process is
  simpler but couples sandbox lifetime to retrieval container lifetime.
  Lean sidecar. **Decided 2026-04-14: sidecar.**
- **Kernel pool vs per-conversation spawn?** Per-conversation is
  simpler but wastes resources on idle conversations. A pool of N
  warm kernels with LRU assignment would be more efficient at scale.
  Start with per-conversation, optimise later. **Decided 2026-04-14:
  per-conversation.**
- **Idle kernel timeout?** Kill after 15 min of no use? Reclaim memory
  without killing conversation state (forgivable — user can just
  re-upload data). Probably yes. **Decided + shipped 2026-04-14: 15 min,
  configurable via SANDBOX_IDLE_TTL_S.**

### Status

**Stage A — DONE 2026-04-14**

Shipped: sandbox sidecar container on a private internal docker network,
per-conversation Jupyter kernels under firejail --net=none, idle reaper
(15 min default), 64 KB stdout/stderr cap, in-kernel setrlimit
belt-and-suspenders for the 2 GB RLIMIT_AS, matplotlib inline backend
auto-enabled, plot-to-PNG-artifact pipeline, /api/artifacts/{cid}/{aid}
ownership-checked endpoint, `artifact` SSE event, run_python +
sandbox_reset MCP tools, ephemeral chat refusal, explicit kernel
cleanup on conversation delete. 10/10 stress tests pass.

Pre-installed package set baked into the sandbox image: numpy, scipy,
pandas, matplotlib, seaborn, scikit-learn, sympy, networkx, openpyxl,
Pillow, pyyaml, requests, jupyter-client, ipykernel, ipython,
matplotlib-inline.

**Stage B — DEFERRED**

Three follow-ups are explicitly deferred and need their own small PRs
when there is real user demand:

- **Live `sandbox_stdout` / `sandbox_stderr` SSE streaming** during long
  executions. Stage A captures the full output server-side and emits it
  with the final `tool_result`; live streaming would let the user watch
  long-running scripts character-by-character. Needs sandbox-svc to
  stream its `/exec/{cid}` response and chat_service to forward the
  bytes as they arrive. ~2 hours.
- **`sandbox_install_package` MCP tool** for backend-mediated pip
  installs. Requires a whitelist file at `config/sandbox_packages.yml`,
  a wheelhouse cache mounted into the sandbox container, dynamic
  `PYTHONPATH` manipulation per kernel, and a security model around
  what the model is allowed to install. Without this, the sandbox is
  hard-frozen to the package set above. ~4 hours.
- **`save_artifact_to_documents`** tool that copies an artifact from
  the sandbox's per-conversation scratch dir into the user's persistent
  document store, so users can reuse generated plots / spreadsheets in
  later conversations without re-running the code. ~1 hour but depends
  on the artifact-id surface settling.

**Frontend work tracked in `docs/FRONTEND-TASKS.md` entry #2**:
rendering the new `artifact` SSE event inline in the assistant
transcript (image tag for image artifacts, download chip for
everything else).

---

## 3. Scientific plotting (on top of §2)

### Problem

Researchers need figures. Matplotlib is in the language Curie and Meitner
already speak fluently, but without sandbox execution there's no way to
actually produce one.

### Design

This is a thin layer on top of §2 — no new tool. When the model wants a
plot, it calls `run_python` with matplotlib code that saves to
`/scratch/{conversation_id}/plot.png`. The sandbox returns the artifact
id, chat_service emits the `artifact` SSE event, frontend renders inline.

### Vision feedback loop (if vision is enabled)

**Vision is confirmed** (probed against `qwen3.5-35b-a3b-awq-4bit` on
2026-04-13: model correctly identified colors and OCR'd rendered text
from a 320×120 PNG, using 102 prompt tokens for the image). So the
plot-check feedback loop is live:

1. Model calls `run_python` → plot saved, artifact emitted.
2. On the next turn, chat_service includes the generated image as an
   `image_url` content block in the user context (auto-attached tool
   result).
3. Model looks at its own plot, judges whether axes, legends, colors
   match intent.
4. If not, calls `run_python` again with adjustments. Loop.

Implementation note: `chat_service` already builds messages from stored
conversation history. Need to extend the "tool_result → role: tool"
conversion to also include an `image_url` content block when a tool
result has `artifacts` of type image.

### MCP helper tools

Consider two conveniences on top of raw `run_python`:

    quick_plot(kind: str, x: list, y: list, title: str, ...) -> dict
    #   → one-shot "give me a bar chart / line plot / scatter" helper

    compare_plots(artifact_ids: list) -> dict
    #   → render a grid of previously-generated plots side by side

Both optional; `run_python` is enough to start.

### Test plan

- `test_plot_simple`: "plot sin(x) from 0 to 2π" — expect an artifact.
- `test_plot_critique`: "plot sin(x), then check if the axes are
  labelled, and if not, redo it" — expect 2 `run_python` calls,
  second one produces a labelled plot.
- `test_plot_xlsx`: "create an Excel sheet with the iris dataset and
  a chart" — expect an xlsx artifact.

### Effort

~1 day on top of §2. Mostly SSE artifact pipeline + the image
round-trip in chat_service.

### Status

**Stage A - DONE 2026-04-14**

Shipped:

- Persona prompt updates for chat (Meitner), research (Curie), and code
  (Turing). Each persona now has a paragraph telling the model when to
  reach for `run_python` and how to caption the resulting artifacts.
  Curie gets stronger guidance around publication-quality figures
  (axis labels with units, perceptually-uniform colormaps, tight_layout).
  Turing gets framing as "self-test the code you write" rather than
  "compute the answer".
- Sandbox now extracts on-disk file artifacts in addition to the inline
  `display_data` images shipped in §2 Stage A. Each per-conversation
  scratch directory carries an `_artifacts.json` manifest mapping
  artifact ids to filenames + content types, so an `openpyxl.save("a.xlsx")`
  produces a downloadable artifact with the correct filename and MIME
  type. The `/api/artifacts/{cid}/{aid}` endpoint reads the manifest
  rather than glob-by-prefix.
- Stress tests: `plot_simple_via_chat` (model called run_python on
  "plot sin(x)..." and the artifact rendered as image/png) and
  `plot_xlsx_via_chat` (model produced a real .xlsx with PK magic
  bytes via openpyxl).

**Deferred (separate PRs)**

- ~~**Vision feedback loop**~~: **CLOSED 2026-04-14 as part of §5
  Stage A**. `chat_service` now injects a synthetic multimodal user
  message carrying the plot image after each `run_python` tool_result,
  via `vision.build_tool_result_followup`. See §5 Status for the full
  scope.
- **`quick_plot` and `compare_plots` MCP helper tools**: the spec calls
  them optional. Skip until a user asks; raw `run_python` is enough
  for now.

---

## 4. Self-description / capabilities introspection

### Problem

Users don't read docs. They ask the model "what can you do?" and expect
a real answer. Currently Meitner has no idea what tools she has, what
agents exist, or how to tell users to upload documents.

### Design — both passive and active

**(a) Passive: bake capability summary into the system prompt.** Extend
`chat_service.stream_chat_completion`'s prompt-building step (where
`agents.agent_summaries_for_prompt()` already gets injected) to also
inject a compact MCP tool summary and a persona list. Result in the
system prompt:

    === CAPABILITIES ===
    Available tools:
      - deep_research: bundled research pipeline
      - web_search / paper_search / semantic_scholar_search: multi-query search
      - web_fetch: fetch + summarise a URL
      - paper_lookup: DOI lookup with local → S2 → Crossref cascade
      - run_python: execute code in a sandboxed kernel
      - ...

    Available agents: research_orchestrator, code_checker, writing_agent

    User-facing features:
      - Upload documents (PDF, TXT, MD, DOCX, images)
      - Switch personas: chat (Meitner), code (Turing), research (Curie)
      - Check system status at /api/status
    === END CAPABILITIES ===

Rough cost: ~600 tokens per request. Bounded and worth it.

**(b) Active: an `faq` MCP tool backed by a YAML file.** Admin-maintained:

    # config/faq.yml
    topics:
      upload_documents:
        question: "How do I upload a document?"
        answer: "Click the + in the chat input and select the file..."
      agents:
        question: "What are agents?"
        answer: "Agents are bounded workflows..."
      personas:
        question: "What's the difference between Meitner, Turing, and Curie?"
        answer: "..."

MCP tool:

    faq(topic: str = None, search: str = None) -> dict
    #   → returns the matching answer, or a list of available topics

The model calls `faq` when the user asks how-to questions with a
specific topic tag. The schema description tells the model this is
for user-teaching, not for answering research questions.

### Why both

Passive covers "what can you do" (quick, always in context). Active
covers "how do I do X" (longer, admin-curated, can include examples
and exact UI clicks). Passive bloats every request; active only fires
when needed.

### Test plan

- `test_capability_query_passive`: ask "what tools do you have?",
  assert response mentions `deep_research`, `web_search`, `run_python`.
- `test_faq_lookup`: ask "how do I upload a PDF?", assert the model
  calls `faq(topic="upload_documents")` and uses the canned answer.
- `test_persona_explanation`: ask "what's the difference between the
  three personas?", assert response names all three.

### Effort

Half a day for (a), half a day for (b). The FAQ YAML can start with
6-8 entries and grow over time.

### Open questions

- Should `faq` entries support templating so e.g. the current upload
  limit (50 MB) is pulled from config instead of hard-coded? Probably
  overkill for v1.

---

## 5. Multimodal image inputs

### Problem

Users want to paste screenshots of papers, photos of lab notebooks,
or raw scientific figures and have the model discuss them. Currently
uploaded images are stored but never reach the model.

### Vision capability confirmed

Probed on 2026-04-13 against the deployed Qwen3.5-35B-A3B-AWQ-4bit
via vLLM's `/v1/chat/completions`:

    POST /v1/chat/completions
    {
      "messages": [{"role": "user", "content": [
        {"type": "text", "text": "What colors?"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}
      ]}]
    }

- 64×64 red-on-blue test image → reply "Red and blue" (correct)
- 320×120 rendered text "KINASE-7743" → reply "kinase-7743" (OCR works,
  case-folded but content correct)
- Image token cost: ~100 tokens for a 320×120 PNG (trivial)

Works out of the box. No model swap needed.

### Design

**Frontend → backend path:**

1. User uploads an image via `POST /api/documents/upload` (already
   implemented — images are stored with `status: "stored"`).
2. Frontend includes an `attachments: [document_id, ...]` array in
   the `/api/chat/completions` request body on the turn that
   references the image.
3. Backend's `chat_service` resolves each document_id to the on-disk
   file, reads the bytes, base64-encodes, attaches as an `image_url`
   content block on the user message.
4. The user message becomes multipart:

    {"role": "user", "content": [
      {"type": "text", "text": "What's in this figure?"},
      {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}
    ]}

**Or skip the document store hop:** frontend sends base64 inline in
the chat request. Simpler but doesn't reuse the document store.

My vote: inline first (minimum friction), document store reference
later as an optimisation.

### New use cases it unlocks

- Paste a figure from a paper → model describes methods, extracts
  data points, compares to literature.
- Photograph a lab notebook → model transcribes.
- Screenshot of a code error → model debugs.
- Screenshot of a molecular structure → model identifies motif.
- Plot critique loop (see §3) — model looks at its own output.

### Test plan

- `test_vision_color`: 64×64 solid color PNG, ask "what color?".
- `test_vision_ocr`: rendered-text image, ask "what does this say?".
- `test_vision_figure`: screenshot of a simple bar chart, ask
  "what does this figure show?".
- `test_vision_docstore`: upload via documents API, reference by
  document_id in a chat request.
- `test_vision_multiple`: two images in one message, ask the model
  to compare them.

### Effort

Half a day for inline base64. Another half-day for document store
reference + round-trip caching.

### Open questions

- **Image size limits?** vLLM's tokeniser handles images by resizing.
  Probably cap at 2048×2048 server-side to avoid pathological inputs.
  **Decided 2026-04-14**: cap at 5 MB per image and 3 images per turn.
  Pixel dimensions left to vLLM.
- **Preprocessing?** For scientific figures, we might want to pre-crop
  whitespace or enhance contrast before sending. Nice-to-have, not
  required.

### Status

**Stage A - DONE 2026-04-14**

Shipped:

- OpenAI-style multimodal content lists on the last user message of
  `POST /api/chat/completions`. Backwards-compatible: string content
  still works. Supports inline `data:image/...;base64,...` URLs and
  `document:<doc_id>` references; the resolver lives in
  `retrieval/vision.py`.
- 5 MB per image and 3 images per turn caps enforced server-side.
- On persistent chats, inline data URLs are auto-funnelled into the
  documents store so the attachment is findable later. The
  `messages.attachments` column (new, idempotent migration) holds
  `{document_id, filename, content_type, source}` metadata for each
  image on the row. Text-only content stays in `messages.content` so
  FTS5 search keeps working.
- On ephemeral chats, inline images reach the model on the turn they
  were sent and then vanish (matches the "nothing stored" contract).
  `document:<id>` references still work in ephemeral mode because the
  documents table already existed.
- §3 plot-critique feedback loop closed: after a `run_python`
  tool_result with image artifacts, `chat_service` calls
  `vision.build_tool_result_followup` which fetches the artifact bytes
  from the sandbox sidecar and appends a synthetic multimodal user
  message to the vLLM message list so the model can look at its own
  plot on the next tool-loop iteration. Errors on fetch are logged
  but do not break the loop.
- Tests: `test_vision_color`, `test_vision_ocr`,
  `test_vision_document_upload`, `test_vision_unit_synthesis`
  (in-container unit test via `docker exec`),
  `test_feedback_loop_ocr_forced` (strong, 1-in-9000 false-pass),
  `test_feedback_loop_color_forced` (softer, 1-in-3 false-pass;
  both use a ground-truth read from the sandbox kernel via a
  second `/mcp/call` after the chat completes).

**Image re-view capability - CLOSED 2026-04-14**

Shipped the deferred `view_attachment` MCP tool as a follow-up small
PR (Stage B).

- `view_attachment(document_id)` MCP tool in
  `retrieval/mcp/tools/documents.py`. Validates ownership via
  `current_user_email`, resolves the doc to disk, refuses non-image
  types with an explicit error.
- `vision.build_view_attachment_followup` mirrors the existing
  sandbox-artifact follow-up: on the next tool-loop iteration,
  `chat_service` injects a synthetic multimodal user message carrying
  the requested attachment bytes. The tool's own return value stays a
  small metadata marker so the serialized tool message doesn't
  become a base64 wall of garbage.
- `chat_context.assemble_context` now inlines an
  `[Attachments on this message: <doc_id> (<filename>, <mime>). Call
  view_attachment(document_id="...") to see one again.]` marker into
  past-turn content when the stored row has attachments. The model
  discovers document_ids from these markers without needing an
  extra discovery tool call. The stored `messages.content` column
  is unchanged, so FTS search keeps working against the clean text.
- Tests: `view_attachment_mcp_call` (direct tool invocation),
  `view_attachment_mcp_rejects_non_image`, and
  `view_attachment_end_to_end` (two-turn chat where turn 1 uploads a
  red square and turn 2 asks the model to recall its colour via
  `view_attachment`).

Other deferred items:

- **Preprocessing / whitespace cropping** for scientific figures.
  Nice-to-have, not required for the core flow. Would live in
  `vision.py` as a resize/crop step before base64.
- **Image carry-over across conversation history**: deliberate "one-shot"
  design (see §5 Stage A status above). Changing this would require
  a context-budget policy on how many past images to re-include and
  is entangled with the re-view capability above.

---

## 6. Citation export (quick win)

### Problem

Researchers build reference lists one paper at a time. The model has
the DOIs in hand but no way to emit BibTeX / RIS / CSL-JSON for drop-in
import to Zotero, Mendeley, EndNote, LaTeX.

### Design

One MCP tool:

    export_citations(dois: list[str], format: str = "bibtex") -> dict
    #   → {"citations": [{doi, format, text}, ...]}

Implementation uses Crossref content negotiation:

    curl -H "Accept: application/x-bibtex" https://doi.org/10.1038/s41586-021-03819-2

Supported formats: `bibtex`, `ris`, `csl-json` (JSON bibliography),
`apa` (plain-text APA style), `chicago`, `nature`. Crossref supports
all of these via the Accept header.

### Estimated effort

30 lines of code. Half an hour.

### Open questions

None. This is the simplest feature on the list.

---

## 7. `read_paper` — PDF fetch + map-reduce summarise

### Problem

Phase 3 populated `open_access_pdf` URLs in `paper_lookup` responses.
The model can see the URL but has no tool to actually read it. Currently
the best it can do is call `web_fetch` on the URL (which works but is
awkward) or cite from abstract-only.

### Design

New MCP tool that chains three existing pipelines:

    read_paper(doi: str, focus: str = None) -> dict
    #   → {"doi", "title", "authors", "summary", "key_findings",
    #      "sources_used": ["open_access_pdf" | "crossref" | "cached"]}

Flow:

1. `paper_lookup(doi)` to resolve title/authors/open_access_pdf URL
   (via the local → S2 → Crossref cascade we already built)
2. If an OA PDF URL is available: download, pipe through GROBID
   (already deployed) → clean text, apply the `web_fetch` map-reduce
   summariser with `focus` as the summary_instruction
3. If no OA PDF: fall back to the abstract/TLDR from S2
4. Cache the PDF in `/opt/munin/data/papers/cached/{doi_hash}.pdf`
   so repeated calls on the same DOI are instant
5. Return the structured summary

### Estimated effort

~60 lines. 1-2 hours. Entirely chains existing infrastructure.

---

## 8. `compare_papers` composite tool

### Problem

"Compare methods across these three papers" is a common research task.
Currently the model fires three parallel `read_paper` calls and tries
to assemble a table by hand. Consolidating into one tool with a shared
comparison prompt is cleaner.

### Design

    compare_papers(dois: list[str], focus: str, max_papers: int = 5) -> dict
    #   → {"question": focus, "papers": [...], "comparison_table": {...},
    #      "disagreements": [...], "common_findings": [...]}

Internal flow:

1. `asyncio.gather` → `read_paper(doi, focus=focus)` for each DOI
2. Feed the concatenated summaries into one vLLM call with a
   "produce a side-by-side comparison" prompt
3. Return structured comparison with columns the LLM picks
   (methods, results, sample size, conclusions, limitations)

### Effort

~80 lines, mostly a slightly-larger prompt template. 2 hours on top
of §7.

---

## 9. User memory / notes store

### Problem

Users re-explain themselves every new conversation. The model has no
way to remember "I'm a biophysics PhD finishing on kinase inhibitors"
or "I prefer Chicago-style citations" between sessions.

### Design

Per-user KV store, persistent across conversations, scoped by email.

**Schema (new SQLite table in `chats.db`):**

    CREATE TABLE user_memory (
      user_email TEXT NOT NULL,
      key TEXT NOT NULL,
      value TEXT NOT NULL,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      PRIMARY KEY (user_email, key)
    );

**MCP tools:**

    remember(key: str, value: str) -> dict
    forget(key: str) -> dict
    recall(search: str = None) -> dict
    #   → list[{key, value, updated_at}] — all memories if no search,
    #     matches by substring if search is provided

**System prompt injection:** on every chat request, `chat_service`
loads the user's memory store and prepends a block:

    === WHAT YOU REMEMBER ABOUT THIS USER ===
    - research_area: kinase inhibitors in lipid membranes
    - affiliation: PhD candidate, biophysics
    - citation_style: APA
    - last_seen_topic: membrane compaction simulations
    === END ===

Bounded to ~20 memories per user, ~200 chars each, so system prompt
bloat is capped at ~4 KB.

### Test plan

- `test_memory_roundtrip`: remember + recall in same conversation
- `test_memory_persistence`: remember in conversation A, recall in
  conversation B, same user
- `test_memory_user_isolation`: remember as user A, verify user B
  cannot recall
- `test_memory_size_cap`: attempt to store 100 memories, verify older
  ones get evicted or a size limit kicks in

### Effort

~150 lines (schema + tools + chat_service injection). ~2 hours.

---

## 10. Long-running / background research jobs

### Problem

Some research questions take 10+ minutes of real work: traversing
citation graphs, reading 20 papers, running numerical experiments in
the sandbox. Blocking the chat endpoint for that long is hostile.

### Design

Reuse the existing `deepresearch` SLURM daemon infra from Phase 2.
Currently it's wired to the research service but not exposed as a
chat-level tool.

**New MCP tools:**

    schedule_research(question: str, depth: "deep"|"exhaustive" = "deep",
                      notify_on_done: bool = True) -> dict
    #   → {"job_id", "estimated_duration_s", "submitted_at"}

    check_research(job_id: str) -> dict
    #   → {"status": "queued"|"running"|"done"|"failed",
    #      "result": {...} or None, "log_tail": "..."}

    list_research_jobs() -> dict
    #   → list of this user's recent jobs

### Backend mechanics

1. `schedule_research` submits a SLURM job that runs the
   `research_orchestrator` agent with `depth=exhaustive` (we'd add
   a new tier) against the question, writes the result to
   `/opt/munin/deepresearch/results/{job_id}.json` when done.
2. `check_research` reads the result file and returns status.
3. Notification (optional): when a job finishes, an entry appears in
   the user's memory store, so the next time they open a conversation
   the model can say "by the way, your background research on X
   finished this morning, want me to summarise?"

### Effort

Half to full day. The daemon infra exists; the wiring is new.

### Open questions

- **How does the user find out when the job is done?** Options:
  (a) push via WebSocket / SSE — requires frontend changes
  (b) polling in the frontend status widget
  (c) surface at next conversation open via memory injection
  I'd do (b) + (c) first, (a) later if people ask.

---

## 11. Equation OCR (conditional on §5)

### Problem

Copying equations from a PDF is painful. Every researcher has done it;
everyone hates it. If vision is live, we have the tool.

### Design

    transcribe_equation(image_ref: str) -> dict
    #   → {"latex": "\\int_0^\\infty ...", "confidence": 0.9}

`image_ref` is either an attachment id from the current conversation
or a region selector if the frontend supports cropping. The tool
passes the image to vLLM with the prompt "Transcribe this equation
as LaTeX. Output only the LaTeX, no commentary."

### Effort

30 minutes. Needs §5 first.

### Status

**DONE 2026-04-14**

Shipped:

- New `transcribe_equation(image_ref)` MCP tool in
  `retrieval/mcp/tools/equation.py`. Resolves a document_id to disk,
  reads bytes, builds a data URL, posts a multimodal message to
  vLLM with a tight system prompt demanding LaTeX-only output.
  Qwen3 reasoning disabled via `chat_template_kwargs.enable_thinking=False`.
- Output sanitisation strips stray code fences / `$$` delimiters the
  model slips in despite the system prompt, and handles the
  `NO_EQUATION` sentinel for non-mathematical images.
- Ownership check via `current_user_email`; rejects non-image
  document types with an explicit error.
- Dropped the spec's optional `confidence` field: self-reported
  confidence on OCR output is noise and returning one would mislead
  callers. Output shape is `{"latex": "...", "image_ref": "..."}`.
- Dropped the spec's optional region-selector variant of `image_ref`:
  requires frontend cropping support we don't have yet. Only
  document_id references for v1.
- Tests: `equation_ocr_basic` (renders `x^2 + y^2 = z^2` via PIL,
  uploads, transcribes, asserts the returned LaTeX contains all
  three variables and a squared-form marker), and
  `equation_ocr_rejects_non_image` (uploads .txt, expects error).

---

## 12. Reproducibility helper (speculative)

### Problem

"I read this paper's methods section, now I want to actually run it
on my own data." Gap between reading methods and writing code is real.

### Design

    reproduce_methods(doi: str = None, methods_text: str = None,
                      language: "python"|"r" = "python") -> dict
    #   → {"script": "...", "dependencies": [...], "caveats": [...],
    #      "input_format_assumed": "..."}

Flow:

1. If `doi` is given, fetch methods text via `read_paper`
2. Feed methods text to vLLM with a "write a skeleton Python script
   that attempts to reproduce this analysis" prompt
3. Optionally run the script in the sandbox (§2) to catch obvious
   syntax errors
4. Return script + caveats

**Speculative** because the output quality depends entirely on the
model's understanding of the methods, and methods sections are often
vague or incomplete. Value depends on users accepting the script as
a starting point, not a turnkey solution.

### Effort

1-2 days. Most of the work is prompt engineering and validating
output against real papers.

---

## Priority order (agreed 2026-04-13)

1. **§2 Sandbox** — unlocks §3 (plotting) and indirectly §11 (OCR once vision lands) and §12 (reproducibility)
2. **§5 Vision** — probed and confirmed, unlocks §11 and plot critique in §3
3. **§6 Citation export** — trivial quick win
4. **§9 User memory** — big usability lift, small code
5. **§7 read_paper** — chains existing pieces
6. **§4 Self-description** — split across prompt injection and FAQ tool
7. **§8 compare_papers** — builds on §7
8. **§10 Background research jobs** — reuses SLURM infra
9. **§1 ask_clarification** — small, orthogonal to everything else
10. **§11 Equation OCR** — tiny feature on top of §5
11. **§12 Reproducibility helper** — speculative, do last

---

## Global design decisions (agreed 2026-04-13)

- **Admin model**: single admin (repo owner). No per-user role concept.
  Features like FAQ editing, sandbox package allowlists, memory
  eviction policies are admin-only and managed via config files in
  the repo.
- **Sandbox GPU access**: no. Heavy GPU work escalates to SLURM.
- **Sandbox network**: no. Network calls go through existing
  `web_search`, `web_fetch`, `paper_lookup`. `sandbox_install_package`
  is the one exception, and it runs outside the sandbox.
- **File persistence**: scratch-per-conversation dies with the
  conversation. Explicit "save to my documents" tool promotes
  artifacts to the persistent user document store.
- **Self-description**: both passive (prompt injection) and active
  (FAQ tool).

---

## 13. Paper download prominence + clickable links

### Problem

Paper downloads from the local corpus exist (`/paper/{doi}/pdf` endpoint,
`get_paper_pdf` MCP tool, `PUBLIC_URL=https://search.muninai.org`) but
the model rarely surfaces them and the frontend doesn't render
ordinary markdown links as clickable anchors. Net effect: even when a
user is citing a paper that's literally sitting on the cluster as a
PDF, they don't get a one-click download.

### Backend changes

**1. Inject `download_url` into paper search results when the paper is
in the local corpus.**

`paper_search` and `semantic_scholar_search` currently return neither
`download_url` nor a `local_pdf_available` flag. Change both:

- In `paper_search`: since every hit comes from the local Qdrant
  corpus, every hit gets a `download_url` field computed as
  `f"{PUBLIC_URL}/paper/{quote(doi)}/pdf"` (the same URL shape
  `get_paper_pdf` already uses). Only emit the field if `doi` is
  non-empty and `get_pdf_path(doi)` actually finds the file.
- In `semantic_scholar_search`: results are from S2, so most won't be
  local. After the S2 call, batch-check which DOIs exist locally via
  `get_pdf_path(doi)` and add `download_url` + `local_pdf_available:
  True` only to those. Also surface `open_access_pdf` from the S2
  response for papers that have one but aren't local — the model can
  present it as a "read online" link.
- Same treatment for `deep_research` paper results and `paper_lookup`.

Saves a round-trip — the model doesn't need to follow up with
`get_paper_pdf` to find out if the paper is downloadable.

**2. Persona prompt nudge.** One sentence added to all three personas:

> When citing a paper from a tool result, if the result includes a
> `download_url` field, ALWAYS include it in your answer as a clickable
> markdown link, e.g. `[Download PDF](download_url)`. Users want the
> one-click download whenever it's available.

This is one of the few cases where a "always do X" rule is justified:
surfacing a link is cheap, useful, and never wrong.

### Frontend changes (not implementable from this repo)

- **Enable clickable markdown links.** This is the big one. Whatever
  markdown renderer the frontend uses (`react-markdown`, `marked`,
  `markdown-it`, ...) should render `[text](url)` as `<a href>`. Open
  in a new tab. This also fixes clickable DOIs, PMIDs, arxiv IDs,
  and any other URL the model emits.
- **VPS gateway**: confirm `/paper/{doi}/pdf` is proxied through the
  gateway to the cluster retrieval service. If the `PUBLIC_URL` is
  `search.muninai.org` but the user is on `chat.muninai.org`, the
  browser will open a new tab to `search.muninai.org/paper/...` and
  download. Should just work, but verify.

### Test plan

- `test_paper_search_has_download_url`: call `paper_search` with a
  known-local query, assert top result has a populated `download_url`.
- `test_s2_result_has_download_url_when_local`: call
  `semantic_scholar_search` with a query known to return a
  local-corpus paper, assert the local hit has `download_url` and
  remote hits don't.
- `test_model_surfaces_download`: ask Curie for *"papers about X"*
  where X is local, assert the response contains the string
  `download` and a URL pointing to `PUBLIC_URL`.

### Effort

~1 hour for the backend changes. ~30 lines. Test plan adds another
hour.

---

## 14. `ask_clarification` v2 — option chips + Q/A format

Supersedes the schema in §1. Same core idea, richer UI affordance.

### Schema

```python
ask_clarification(
    what_i_understood: str,   # prominent at top of card
    questions: list[{
        id: str,              # stable identifier for this sub-question
        text: str,            # the actual question
        options: list[str],   # 2-4 pre-made answers the model thinks are plausible
        allow_custom: bool = True,  # show a "type your own" text field
    }]
) -> dict
```

**Batched multi-question**: the `questions` list can have multiple
entries for a single card. User fills them all in and submits. The
model sees all answers in the next turn.

**Sequential multi-turn**: after the user submits, the model can
decide to ask AGAIN (a new `ask_clarification` call on the next
turn) if new ambiguities surfaced from the first round of answers.
This is the Q/A back-and-forth pattern Claude uses — no special
state required, the conversation history carries the flow.

### Frontend rendering (not implementable from this repo)

Inline chat card in the message thread — NOT a modal. Looks like:

```
┌────────────────────────────────────────────────────────────┐
│ Just to make sure I understand:                            │
│ "You want papers about kinase inhibitors in lipid          │
│ membranes, with a focus on recent work."                   │
│                                                            │
│ Q1: How recent is "recent"?                                │
│  ○ Last 2 years (2024–)                                    │
│  ○ Last 5 years                                            │
│  ○ No year filter                                          │
│  ○ [ type your own answer............... ]                 │
│                                                            │
│ Q2: Which membrane type interests you most?                │
│  ○ Plasma membrane                                         │
│  ○ Mitochondrial                                           │
│  ○ Model bilayers (LUVs, BLMs)                             │
│  ○ [ type your own answer............... ]                 │
│                                                            │
│                                        [ Submit answers ]  │
└────────────────────────────────────────────────────────────┘
```

When the user submits, the frontend constructs a follow-up user
message of the form:

> Q1: Last 5 years
> Q2: Model bilayers (LUVs, BLMs)

and posts it as a normal `/api/chat/completions` request. Zero
special-case backend handling — the answers just arrive as a regular
user turn, the model reads them from conversation history.

### Backend changes

- New file `retrieval/mcp/tools/clarification.py` with the
  `ask_clarification` function (returns `{"status":
  "awaiting_user_response", "questions_asked": [...]}`, mostly a
  no-op because `chat_service` intercepts it).
- `chat_service`: when `_stream_vllm_once` finalizes tool calls,
  scan for `name == "ask_clarification"` BEFORE firing
  `_run_tool_calls`. If found:
  - Emit the full card payload as a single SSE event
    (`event: clarification`, new event type) so the frontend can
    render the structured UI directly instead of parsing text.
  - Skip the rest of the loop — no tool execution, no wrap-up
    synthesis, no additional streaming.
  - Persist an assistant message with `content` = a markdown
    fallback (for conversation history display), `tool_calls` =
    `[{name, arguments, result: {status: "awaiting"}}]`.
  - Emit `done` and close.
- New SSE event: `clarification` with payload
  `{what_i_understood, questions}`.
- Schema in `mcp/schemas.py` with the decision rule in the
  description (when to use vs when not to).
- Persona prompt additions (Meitner + Curie) encouraging
  clarification on ambiguous input, with examples of
  ambiguous-vs-clear.

### Test plan

Two-sided coverage — trigger on ambiguous, NOT on clear. Add to
`stress-test.py`:

- **Expect clarification**:
  - `test_ambiguous_help`: "help me with my paper"
  - `test_ambiguous_whats_new`: "what's new?"
  - `test_vague_look_into`: "look into photosynthesis"
  - `test_single_word_fix`: "fix it"
- **Expect direct answer / tool use** (controls):
  - `test_clear_date`: "what day is it?"
  - `test_clear_research`: "find recent papers on polymer crystallization"
  - `test_clear_code`: "review this Python function: ..."

Assertion shape for the ambiguous cases:

- Exactly one `clarification` SSE event fires
- Zero regular `tool_call` events
- `done` arrives cleanly

For the control cases: zero `clarification` events, normal response.

### Effort

Supersedes the estimate in §1. ~1.5 hours now that the schema is
more structured:

- Tool module + schema + executor dispatch: 20 min
- `chat_service` intercept + new SSE event: 30 min
- Persona prompt additions: 10 min
- Stress tests: 30 min
- Deploy + verify: 10 min

### Open questions

- **"Type your own" vs "None of these"**: always visible text field,
  or collapsed behind an option? Vote: always visible.
- **Frontend can POST a structured answer back**: instead of
  synthesizing a user message string, the frontend could send
  `{"clarification_answers": {q_id: answer, ...}}` as a side channel
  in the next request body. Cleaner data model but requires a new
  request shape. Defer — string synthesis is fine for v1.

---

## 15. Paper-embedding 2D map with clustering

### Problem

Users have no way to explore the shape of the paper corpus visually.
Searching is fine for targeted queries but gives zero signal about
what topics are over/under-represented, what clusters exist, or
where a given paper sits in the research landscape.

### Design

**Offline script**: `scripts/knowledge/build_embedding_map.py`

1. Scroll the `papers` Qdrant collection with `with_vectors=True`,
   collecting `(paper_id, doi, title, year, authors, embedding)` for
   every point.
2. Stack embeddings into a numpy array, run UMAP with `n_components=2,
   n_neighbors=15, min_dist=0.1` (standard visualization defaults).
3. Run HDBSCAN (`min_cluster_size=30`, let it pick the cluster count
   automatically) over either the raw embeddings or the UMAP output —
   raw is more accurate, UMAP is faster. Start with UMAP output.
4. For each cluster, sample ~10 representative titles (closest to the
   cluster centroid in embedding space) and ask vLLM for a 2-4 word
   topic label with `chat_template_kwargs={enable_thinking: false}`.
5. Write a flat JSON file to
   `/opt/munin/knowledge/embedding_map.json`:

```json
{
  "generated_at": "2026-04-13T03:00:00Z",
  "paper_count": 12457,
  "cluster_count": 34,
  "points": [
    {"id": "...", "doi": "10.1038/...", "title": "...", "year": 2023,
     "x": -4.23, "y": 1.87, "cluster": 7}
  ],
  "clusters": [
    {"id": 7, "label": "Kinase inhibitors in membranes",
     "size": 142, "centroid": [-3.9, 2.1]}
  ]
}
```

**Serving**: new endpoint `GET /api/embedding_map` returns the file as-is.

**Update cadence**: nightly cron job at 3 AM. Idempotent — if no new
papers since last run, skips rebuild. Runs outside the retrieval
container (either on host or in a separate SLURM job — either works).

### Scope

- **Shared paper corpus only**, not user documents. Rationale: the
  visualization is meant to show the research landscape of the
  cluster's curated corpus. User docs are private and small.
- **All papers** in every build — no incremental updates for v1. The
  corpus is ~30-50k papers; UMAP on that size is ~30 seconds. Fast
  enough.
- **2D only** — 3D version possible later but not needed.
- **No interactive filters** v1. Static scatter. Users can click
  points to jump to paper details in the existing search UI.

### Test plan

- Unit-test the script with a synthetic small embedding set (100
  points, known structure) — verify clusters are identified and labels
  are non-empty.
- Integration test: run against a recent snapshot of the real Qdrant
  corpus, sanity-check the cluster labels look like real topics
  ("molecular dynamics", "NMR spectroscopy", etc.).
- HTTP test: `GET /api/embedding_map` returns 200 with a valid JSON
  payload within 1 second.

### Effort

- Script itself: ~150 lines, 4-5 hours including tuning HDBSCAN
  params and the label-generation prompt.
- Endpoint: 20 lines, 30 minutes.
- Cron integration: 15 minutes.
- Frontend: not our problem, but they need a D3/deck.gl scatter with
  pan/zoom and click-to-details.

### Open questions

- **HDBSCAN vs k-means**: HDBSCAN handles noise points (outliers stay
  uncategorized) which is more honest. k-means forces every paper into
  a cluster. Vote: HDBSCAN.
- **Label quality**: vLLM-generated labels are hit-or-miss. May need
  iteration on the prompt. If quality is poor, add a fallback using
  top-k TF-IDF keywords from cluster titles.
- **Incremental updates**: on a 50k-paper corpus with nightly adds of
  ~50 papers, a full rebuild every night is overkill. Could add an
  "incremental mode" that only re-UMAPs new points, but this is
  premature optimization.

---

## 16. Pin conversations (cross-device persistence)

### Problem

Users currently have temporary (localStorage-only) pinning in the
frontend. Cross-device sync requires backend storage.

### Design

**Schema change:** add a boolean column to `conversations`:

```sql
ALTER TABLE conversations ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0;
ALTER TABLE conversations ADD COLUMN pinned_at TEXT;
CREATE INDEX idx_conversations_pinned ON conversations(user_email, pinned DESC, updated_at DESC);
```

**New endpoints:**

```
POST   /api/chats/{id}/pin    → {"pinned": true, "pinned_at": "..."}
DELETE /api/chats/{id}/pin    → {"pinned": false}
```

Both respect `X-Munin-Email` ownership (standard pattern).

**Change to existing `GET /api/chats`**:

- Add optional query param `pinned_only=true` for the sidebar's pinned
  section.
- Default ordering: `ORDER BY pinned DESC, updated_at DESC` — pinned
  conversations float to the top of the normal listing. Nice-to-have
  but low-risk.
- Include `pinned` and `pinned_at` fields in the returned conversation
  objects so the frontend can render a pin icon.

**Migration:** since `chats.db` uses SQLite, the `ALTER TABLE ADD
COLUMN` runs on startup idempotently. `chat_store.init_db` can check
if the column exists and add it if missing. No manual migration step.

### Test plan

- `test_pin_persists`: pin a conversation, fetch via GET, assert
  `pinned: true`
- `test_pin_cross_session`: pin, close the HTTP client, open a new
  one, fetch, assert still pinned
- `test_pin_user_isolation`: user A pins, user B fetches, assert
  user B sees nothing pinned
- `test_pinned_first_in_listing`: pin one out of several
  conversations, GET /api/chats without `pinned_only`, assert pinned
  is first in the list

### Effort

~1 hour. 80 lines of code. Minimal risk.

---

## 17. Search past conversations (model-invoked)

### Problem

Users ask *"didn't we talk about X before?"* but the model has no way
to look at prior conversations from the same user. All it sees is the
current chat's history.

### Design

New MCP tool, user-scoped via the `current_user_email` contextvar:

```python
search_past_conversations(query: str, limit: int = 5,
                          persona: str = None) -> dict
#   → {
#     "results": [{
#         "conversation_id": "...",
#         "conversation_title": "...",
#         "persona": "chat",
#         "created_at": "...",
#         "pinned": true,
#         "snippet": "...highlighted match context...",
#         "matching_message_role": "user" | "assistant",
#     }, ...],
#     "total_matches": 47,
#   }
```

Implementation reuses `chat_store`'s existing FTS5 index on
`messages.content` (built in Phase 1). Already scoped by
`user_email`. Already supports substring match via FTS5 MATCH. Just
wrap it as an MCP tool.

**Scope decisions**:

- **Exclude the current conversation** by default. Optional arg
  `include_current: bool = False` if the model ever wants to search
  within the current chat (unlikely since the current chat is already
  in context).
- **Snippet generation**: pull ~150 chars around the matched term using
  SQLite's `snippet()` FTS5 helper or by grepping the message content
  in Python.
- **Ranking**: FTS5 BM25 is the default. Boost pinned conversations
  (from §16) via `ORDER BY c.pinned DESC, bm25(fts)`.
- **Persona filter**: optional `persona` parameter so the user can
  narrow to e.g. "research" conversations only.

### Schema description for the model

> Use this when the user mentions something from a prior conversation
> ("didn't we discuss X?", "what was that paper I found last week?",
> "I asked about Y earlier"). Searches the user's own past
> conversations via full-text search. Returns matching snippets with
> the conversation id so you can reference the specific chat. Does
> NOT search the current conversation — that's already in your
> context.

### Test plan

- `test_search_finds_prior_chat`: seed two conversations (one about
  kinases, one about photosynthesis), then in a third conversation
  ask *"didn't we talk about kinases before?"*, assert the model
  calls `search_past_conversations` and cites the first conversation.
- `test_search_excludes_current`: assert messages from the current
  conversation are NOT in the result set.
- `test_search_user_isolation`: user A seeds a conversation, user B
  searches, assert no hits.
- `test_search_pinned_boost`: pin one of two matching conversations,
  assert it's ranked first.

### Effort

~40 lines + 3 tests. 1-2 hours.

---

## 18. LaTeX via sandbox (specialization of §2)

### Problem

Researchers write papers. They want the model to generate LaTeX, and
crucially they want the model to VERIFY the LaTeX compiles before
handing it over — with iterative fix-on-error when it doesn't.
Currently the model just writes LaTeX blindly and hopes.

### Design

Builds directly on the §2 sandbox. Additional pieces:

**Sandbox image additions** (medium texlive install, ~1 GB):

```
texlive-latex-base
texlive-latex-extra
texlive-latex-recommended
texlive-science
texlive-bibtex-extra
texlive-fonts-recommended
```

**New MCP tool**:

```python
compile_latex(
    source: str,               # main .tex content
    bibliography: str = None,  # optional .bib content
    extra_files: dict = None,  # {filename: content} for \includegraphics etc.
) -> dict
#   → {
#     "success": bool,
#     "pdf_artifact_id": "..." | None,
#     "stdout": "...",
#     "errors": [{line, message}],
#     "warnings": [...],
#     "log_tail": "...",   # last 50 lines of pdflatex log
#   }
```

**Implementation**:

1. Create a scratch dir under `/scratch/{conversation_id}/latex-{uuid}/`.
2. Write `main.tex`, optionally `refs.bib` and any `extra_files`.
3. Run `pdflatex -interaction=nonstopmode main.tex` (possibly with
   `bibtex main && pdflatex main && pdflatex main` if a `.bib` is
   provided — standard BibTeX cycle).
4. Parse `main.log` for errors and warnings.
5. On success, promote `main.pdf` to an artifact, emit the artifact
   event, return the artifact_id.
6. On failure, return structured errors so the model can iterate.

**Iteration loop**: the model writes LaTeX → compiles → reads
structured error → fixes → compiles again. Natural fit with the
existing MAX_TURNS=10 loop. Wraps up cleanly via §6 if it can't fix
within the budget.

### Persona guidance

Meitner + Curie get a snippet:

> When you write LaTeX for the user — whether a full document, a
> figure caption, a table, or just an equation — ALWAYS run
> `compile_latex` to verify it compiles before returning it to the
> user. If compilation fails, read the structured errors, fix the
> source, and retry. Only ship LaTeX you've verified compiles.

Applied regardless of whether the user explicitly asks for
verification.

### Test plan

- `test_latex_simple`: compile a trivial article, assert PDF artifact
  is produced.
- `test_latex_math`: compile a document with complex equations.
- `test_latex_bibtex`: compile with a `.bib` file, assert citations
  resolve.
- `test_latex_error_iteration`: feed broken LaTeX, assert the model
  fixes it within 2-3 iterations and succeeds.
- `test_latex_image_include`: compile with an `extra_files` image.

### Effort

~150 lines of Python + sandbox image rebuild. Half a day on top of
§2 being in place.

### Open questions

- **Do we need XeLaTeX / LuaLaTeX as fallback for fancy fonts?**
  Probably not v1 — most research LaTeX uses pdflatex.
- **Streaming compilation output?** pdflatex is fast (<5s for most
  docs), probably not worth streaming. Return the result at the end.

---

## 19. Autonomous agent selection (prompt tuning + investigation)

### Problem

The model almost never picks `invoke_agent` on its own, even when
the task matches an agent's description. It defaults to
`deep_research` or bare tool calls. We want more autonomous agent
invocation without users having to trigger it.

### Current state (confirmed 2026-04-13)

- Slash commands (`/research`, `/write`, `/analyze`) are **not
  implemented** on the backend. They're mentioned in DESIGN.md but
  have no wiring. Users typing them see the literal text.
- Only the model can trigger agents, via the `invoke_agent` MCP tool.
- `chat_service.stream_chat_completion` already injects
  `agent_summaries_for_prompt()` into the system prompt so the model
  knows which agents exist.
- The user reports **never** seeing the model pick an agent in
  practice, despite low interaction so far.

### Proposed changes

**1. Persona prompt additions** (Meitner + Curie + Turing):

> === WHEN TO INVOKE AGENTS ===
>
> You have access to agents via `invoke_agent(agent, query)`. Each
> agent is a bounded workflow with its own system prompt and tool
> allowlist. Prefer invoking an agent when the user's task matches
> an agent description strongly:
>
> - `code_checker`: user pastes code and asks for review
> - `writing_agent`: user asks you to draft, edit, or polish prose
>   at length (abstract, grant intro, reviewer response)
> - `research_orchestrator`: (under review — see §19 open questions)
>
> Still use `deep_research` or individual tool calls for normal
> questions. Agents are for tasks where a dedicated workflow
> genuinely helps — not for every research question.

**2. Optional: slash-command-as-hint pattern.** Add to personas:

> If the user's message starts with `/research`, `/write`, or
> `/analyze`, treat it as a strong hint that this is a task for the
> corresponding agent (`research_orchestrator`, `writing_agent`,
> `code_checker`). The slash is not a command the backend parses;
> you are parsing it.

Zero backend code, purely prompt-level.

### Effort

Prompt tuning is ~30 minutes. Behavior verification via stress test
is another hour. But see investigation below — the scope may change.

### Investigation column (open question, not decided)

#### `research_orchestrator` vs `deep_research` — keep both, retire one, or rescope?

When I built Phase 4, `deep_research` was designed as a deterministic
alternative to `research_orchestrator`. They now heavily overlap:

| Property | research_orchestrator (agent) | deep_research (tool) |
|---|---|---|
| Decomposition | Model plans sub-questions mid-loop | Fixed at entry (3-5 sub-questions) |
| Tool iteration | Can iterate based on early findings | Single deterministic pass |
| Parallelism | Inside the agent via asyncio.gather | Same |
| Wall clock | 30-300 s (agent guardrails) | 15-45 s |
| SSE visibility | `agent_start` / `agent_thinking` / `agent_done` | None — opaque bulk tool |
| Failure mode | Turns exhausted, guardrail caps | Atomic success/failure |
| Self-correction | Yes — agent can re-plan | No |

**Arguments for keeping both:**

- Agent's mid-course correction is genuinely different: "the first
  search came up empty, let me try a different angle" is something
  `deep_research` can't do. Useful on hard questions.
- Agent's streaming events make the work visible to the user — a
  40-second `deep_research` call looks the same as a 40-second hang.
- Different personas might prefer different defaults (Curie →
  agent, Meitner → `deep_research`).

**Arguments for retiring `research_orchestrator`:**

- Two tools that solve the "big research question" problem means
  models (and users) have to pick between them. Cognitive load
  without much payoff if `deep_research` is sufficient in practice.
- Agent is more expensive (full nested vLLM loop with its own
  reasoning phase on each iteration).
- `deep_research` is deterministic, easier to reason about, easier
  to test.

**Arguments for rescoping:**

- Keep `research_orchestrator` but drop its tool allowlist to just
  the things `deep_research` can't do: follow-up questions,
  targeted paper_lookup on DOIs the first pass flagged, author
  traversal. Make it the "second-pass deep dive" tool rather than
  a competitor to `deep_research`.

**Decision pending:** need real-world usage data. Action items:

- Instrument `invoke_agent` and `deep_research` so we can count
  usage over a week.
- Run side-by-side comparison tests: same question, one through the
  agent, one through `deep_research`, score answer quality.
- Revisit after a week of use with actual data.

### Test plan (for the prompt tuning part, not the investigation)

- `test_code_review_picks_agent`: paste a Python function with a
  bug, ask for review, assert model calls
  `invoke_agent(agent="code_checker", ...)`.
- `test_draft_abstract_picks_agent`: ask for a scientific abstract
  on a topic, assert model calls
  `invoke_agent(agent="writing_agent", ...)`.
- `test_normal_research_doesnt_pick_agent`: ask a normal research
  question, assert model calls `deep_research`, NOT
  `invoke_agent(agent="research_orchestrator", ...)`.
- `test_slash_research_hint`: message starts with `/research`,
  assert model treats it as agent hint.

---

## 20. S2-wide citation tools

### Problem

`get_citations(doi)` and `get_references(doi)` today query Neo4j,
which only knows about papers in the local corpus. A paper with 200
citations in the S2 graph might show 5 in our local corpus. Users
asking *"list the papers citing X"* or *"what does Y cite"* get
dramatically undercounted answers.

### Design

**Two new tools** that hit Semantic Scholar's citation endpoints
directly, complementing (not replacing) the existing Neo4j-backed
tools. Model picks based on whether it wants fast-local or wide-S2.

```python
s2_get_citations(doi: str, limit: int = 50,
                 year_from: int = None) -> dict
#   → {
#     "doi": "...",
#     "paper_title": "...",
#     "total_citations": 1234,  # S2's count
#     "citations": [{
#         "doi", "title", "authors", "year", "citation_count",
#         "tldr", "open_access_pdf",
#         "download_url" if locally available else None,
#     }, ...],
#   }
#
#   Hits https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}/citations
#   Honors the fields= parameter to minimise payload.

s2_get_references(doi: str, limit: int = 50) -> dict
#   → same shape but pulls the papers THIS one cites.
#   Hits https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}/references
```

**Key integration with §13**: for every returned citation, batch-check
`get_pdf_path(doi)` against the local corpus. Add `download_url` +
`local_pdf_available: True` to the hits we have locally. This means
citation listings automatically surface downloadable papers.

**Dedupe within the response**: Semantic Scholar occasionally returns
the same paper twice (different paperId, same DOI). Dedupe by DOI.

**Model guidance (schema description):**

> Use `s2_get_citations` when the user asks *"what papers cite this?"*
> or *"who's building on X?"*. Use `s2_get_references` when they ask
> *"what does this paper cite?"* or *"what did the authors build on?"*.
> These tools hit the full Semantic Scholar citation graph (~200M
> papers), much broader than the local corpus. Combine with
> `paper_lookup` or `deep_research` for full metadata on specific
> results.

### Batching over multiple DOIs

The user asked about *"list the citations of this and that paper"*.
Two approaches:

**A. Model uses parallel tool calls** (existing capability):

The model calls `s2_get_citations` multiple times in a single turn
(one per DOI) and the backend's existing multi-tool-call-per-turn
support runs them in parallel via `asyncio.gather`. No new code. The
model is already capable of this (verified in Phase 1 tests).

**B. Dedicated batch tool**:

```python
s2_get_citations_batch(dois: list[str], limit_per: int = 20,
                       direction: "cited_by"|"cites"|"both" = "both")
```

Runs all DOIs in parallel server-side, merges results, dedupes across
the batch, returns a structured comparison.

**Vote: A for v1** (zero new code, uses existing parallelism), add B
later if real usage shows the model struggles with batching.

### Test plan

- `test_s2_citations_known_paper`: call with AlphaFold DOI, assert
  `total_citations > 1000` and at least one result has a non-empty
  `tldr`.
- `test_s2_references_round_trip`: call `s2_get_references` on paper
  A, pick one returned reference B, call `s2_get_citations` on B,
  assert A is in B's citation list.
- `test_local_download_url_injected`: call `s2_get_citations` with a
  DOI whose citations include a locally-available paper, assert the
  local one has `download_url` set.
- `test_dedup_handles_duplicate_paperids`: mock a response with
  duplicate DOIs, assert single entry in output.

### Effort

~100 lines each for `s2_get_citations` and `s2_get_references`,
significant overlap (factor out into `_s2_citation_call` helper).
Schema entries. Executor dispatch. Test plan. **Half a day total.**

### Open questions

- **Rate limits**: Semantic Scholar's unauthenticated tier is 100
  requests / 5 minutes. With a Semantic Scholar API key (already
  configured via `SEMANTIC_SCHOLAR_API_KEY`), it's 1 req/sec. For
  citation lookups on heavy papers this can get eaten quickly. Add
  polite backoff and surface rate-limit errors clearly.
- **Multi-hop**: "papers that cite the papers that cite X" — possible
  but explodes fast. Not building v1 unless asked.
- **Citation context**: S2 can return the *actual sentence* where
  paper A cites paper B (the `contexts` field). Very valuable for
  understanding *how* papers are cited. Add as an optional flag:
  `include_contexts: bool = False`.

---

## 21. Projects — persistent scoped workspaces

### Problem

Research is organized into projects, but conversations are not. Every
new chat starts from zero — the model has no idea that you're
finishing a PhD on kinase inhibitors, that your reference corpus is
the 40 PDFs you uploaded last month, that you only care about papers
from 2023+. Users end up re-establishing context constantly.

### Design

A **Project** is a top-level organizational unit owned by one user,
consisting of:

- A name, description, and optional long-form instructions
- A set of conversations (many-to-one)
- A set of scoped documents (many-to-one, separate from the user's
  global document store)
- Optional default persona override
- Created/updated timestamps

Conversations can live outside a project (legacy conversations stay
where they are; "Unfiled" is the default). When a conversation is
moved into a project, it inherits the project's context for every
subsequent turn.

### Schema

```sql
CREATE TABLE projects (
    id TEXT PRIMARY KEY,
    user_email TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    instructions TEXT,                -- long-form "about this project"
    default_persona TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

ALTER TABLE conversations ADD COLUMN project_id TEXT REFERENCES projects(id);
CREATE INDEX idx_conversations_project ON conversations(project_id);

-- Project-scoped documents live alongside user-scoped ones:
-- document_store already has payload.conversation_id; add payload.project_id.
-- Existing user docs (no project) continue to work as before.
```

Migration is idempotent (the `ALTER TABLE ADD COLUMN` runs on startup
if missing, same pattern as §16).

### Context injection in `chat_service`

When a conversation has a `project_id`, extend `assemble_context` to
prepend a project context block to the system prompt:

```
=== PROJECT CONTEXT ===
You are working inside project "Kinase Inhibitor Thesis".

Description: Finishing a PhD on small-molecule kinase inhibitors
interacting with lipid membranes.

Instructions: Prefer papers from 2023 onwards. Always cite with DOIs.
I know biophysics well, skip basic explanations.

The user has uploaded 40 project documents, searchable via
search_user_docs (automatically scoped to this project).
=== END PROJECT CONTEXT ===
```

`search_user_docs` gets a new optional `project_id` parameter that
defaults to the current conversation's project (via contextvar).
Passing `project_id=None` searches the user's global docs only;
passing a specific project scopes there; no arg searches project docs
first, falling back to global.

### New endpoints

```
POST   /api/projects                → create
GET    /api/projects                → list user's projects
GET    /api/projects/{id}           → load, with conversation + doc counts
PATCH  /api/projects/{id}           → rename, update instructions, set default persona
DELETE /api/projects/{id}           → delete (plus cascading conversation unfile — conversations are orphaned into the "Unfiled" bucket, not deleted)

POST   /api/projects/{id}/conversations/{cid}   → file a conversation into a project
DELETE /api/projects/{id}/conversations/{cid}   → unfile back to unorganized
```

Documents are filed into a project at upload time via an optional
`project_id` form field on `POST /api/documents/upload`.

### MCP tools

```python
list_projects() -> dict                   # so the model can reference others
get_current_project() -> dict             # for "what project am I in?" questions
search_project_docs(query, top_k=5)       # alias for search_user_docs scoped to project
```

### Frontend changes (not in this repo)

- Sidebar: top-level "Projects" section above the conversation list,
  each project expands to show its conversations
- Project picker / settings page with instructions textarea
- File upload zone scoped to the active project
- "Move to project" right-click on a conversation

### Test plan

- `test_create_and_list_project`: CRUD roundtrip
- `test_conversation_filed_in_project`: create project, create
  conversation with `project_id`, verify GET conversation includes
  project metadata
- `test_project_instructions_injected`: set project instructions,
  send a chat, assert the system prompt (via tool_call inspection)
  contained the instructions
- `test_project_docs_scoped_search`: upload doc A to project P,
  upload doc B unscoped, search inside project P, assert only A
  returns
- `test_cross_project_isolation`: user's project A docs don't leak
  into project B

### Effort

~1 week. Breakdown:

- Schema + migration: 30 min
- CRUD endpoints: 3 h
- `chat_service` context injection: 2 h
- `search_user_docs` project scoping: 1 h
- Project-aware document upload: 1 h
- MCP tools: 1 h
- Tests: 3 h
- Frontend (separate repo): 2-3 days

### Open questions

- **Project instructions token cost**: injecting on every turn adds
  to context overhead. Cap at 2000 chars per project. UI should
  warn if the user writes more. **Decided 2026-04-14: 2000 chars,
  400 on exceed.**
- **Shared projects** (multi-user collab): NO for v1. Single-user
  projects only. Revisit if users ask. **Confirmed: deferred to a
  future sprint, no Stage A work.**
- **Archive vs delete**: should projects have an "archived" state
  that hides them without deleting? Probably yes; add a `archived`
  boolean column. **Decided + shipped 2026-04-14: `archived` column
  on projects + `?archived=true` query param on list.**
- **Project export**: should users be able to export a project (all
  conversations + docs + instructions) as a tarball for backup? Nice
  to have, not required. **Deferred.**

### Status

**Stage A - DONE 2026-04-14**

Shipped:

- `projects` table with `id, user_email, name, description,
  instructions, default_persona, archived, created_at, updated_at`;
  idempotent `conversations.project_id` column migration (same
  `PRAGMA table_info` + `ALTER TABLE` pattern as `pinned`).
- `retrieval/project_store.py` with CRUD, per-project conversation
  count, filing/unfiling, and the `=== PROJECT CONTEXT ===` prompt
  block renderer.
- Full HTTP surface under `/api/projects` (POST/GET/PATCH/DELETE,
  `POST/DELETE /api/projects/{id}/conversations/{cid}` for filing),
  plus `GET /api/chats?project_id=<pid>` with a `__unfiled__`
  sentinel for the default bucket, plus a new `project_id` form
  field on `POST /api/documents/upload` (ownership-checked).
- Two-phase `search_user_docs`: project-scoped first, user-global
  fallback if the scoped search returns zero. Response carries
  `sources_used: ["project"|"project","global"|"global"]` so the
  model can be honest about provenance. Qdrant payload now has
  `project_id`; existing points without it remain user-global.
- `chat_service` prepends the project context block to the system
  prompt above the persona prompt, binds a new
  `current_project_id` ContextVar for tool dispatch, and passes
  the pre-resolved project down from `main.py` to avoid a duplicate
  DB lookup.
- Persona precedence in `api_chat_completions`:
  `project.default_persona` > `profile.default_persona` >
  `DEFAULT_PERSONA_ID`. Explicit body `persona` still wins.
- Ephemeral + project is refused with HTTP 400 (the two features
  have incompatible persistence stories).
- Two new MCP tools: `list_projects` and `get_current_project`.
- Delete semantics: **no cascade, no tombstones, no reaper**. When
  a project is deleted, conversations are unfiled
  (`project_id → NULL`) and docs have their Qdrant `project_id`
  payload cleared. Users keep their data in the Unfiled bucket and
  can delete individual items manually.
- Tests: `project_crud_roundtrip`, `project_instructions_cap`,
  `project_conversation_filing`, `project_instructions_injected`,
  `project_scoped_doc_search`, `project_doc_search_global_fallback`,
  `project_cross_user_isolation`, `project_archived_hidden_by_default`,
  `project_persona_precedence`.

**Deferred (separate PRs)**

- **Multi-user shared projects**: biggest known-unknown. Requires
  an ACL layer and probably a new endpoint for invitation/acceptance.
  Ship when users ask.
- **Project export tarball**: nice-to-have, not blocking any other
  feature.
- **Project templates**: spin up a new project pre-filled with an
  instructions template for common research patterns (e.g. a
  "literature review" template). Future nice-to-have.

---

## 22. Artifacts — iterative document side panel

### Problem

When the model produces a document, code snippet, LaTeX manuscript,
or SVG diagram, it currently dumps the whole thing inline. Users
iterate by scrolling through 15 near-identical copies across the
chat history. There's no concept of "the current version of the
abstract I'm working on". Research writing workflows are the biggest
victim — papers, grant proposals, reviewer responses all benefit
enormously from an edit-in-place surface.

### Design

An **artifact** is a versioned document associated with a
conversation. The model can create new artifacts, read existing
ones, and produce new versions. The frontend renders them in a side
panel with version history.

### Schema

```sql
CREATE TABLE artifacts (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    user_email TEXT NOT NULL,
    title TEXT NOT NULL,
    content_type TEXT NOT NULL,  -- "text/markdown", "text/latex",
                                 -- "application/python", "image/svg+xml", ...
    language TEXT,                -- "python", "latex", etc. for syntax highlighting
    latest_version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);

CREATE TABLE artifact_versions (
    artifact_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    content TEXT NOT NULL,        -- full snapshot per version (storage is cheap)
    change_summary TEXT,          -- one-line model-generated describe of the change
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,     -- "user" or "assistant"
    PRIMARY KEY (artifact_id, version),
    FOREIGN KEY (artifact_id) REFERENCES artifacts(id) ON DELETE CASCADE
);

CREATE INDEX idx_artifacts_conversation ON artifacts(conversation_id);
```

Full snapshots per version (no diff chain). Storage is cheap; rollback
and diff views are trivial; the complexity of a proper diff-based
store isn't worth it at our scale.

### MCP tools

```python
create_artifact(
    title: str,
    content: str,
    content_type: str,   # "text/markdown" etc.
    language: str = None,
) -> dict
#   → {"artifact_id", "version": 1, "title", "content_type"}

update_artifact(
    artifact_id: str,
    content: str,                  # new full content OR unified diff
    is_diff: bool = False,         # if True, content is a unified diff to apply
    change_summary: str = None,
) -> dict
#   → {"artifact_id", "version": N+1, "applied", "errors"?}
#   If is_diff=True, backend applies the diff to the previous version
#   server-side and returns errors if the diff doesn't apply cleanly.
#   This saves tokens vs. rewriting the full doc every time.

read_artifact(artifact_id: str, version: int = None) -> dict
#   → latest or specified version

list_artifacts() -> dict
#   → all artifacts in the current conversation
```

The diff-based update flow is important: for a 5000-word abstract
that the user wants one paragraph tweaked in, the model shouldn't
have to rewrite the whole thing. It emits a unified diff
(`@@ -45,7 +45,9 @@`-style), the backend applies it with the `diff_match_patch`
or `difflib.unified_diff` library, versions bump, done.

### SSE events

Two new event types:

```
event: artifact_created
data: {"id", "title", "content_type", "version", "content_preview"}

event: artifact_updated
data: {"id", "version", "change_summary", "diff_lines_added",
       "diff_lines_removed"}
```

The frontend subscribes and updates the side panel live as the model
writes. For long artifacts (a full paper draft), the panel should
scroll to the active section the model is currently editing (nice
polish, not required).

### HTTP endpoints (for the side panel to drive manual user edits)

```
GET   /api/chats/{cid}/artifacts                → list
GET   /api/chats/{cid}/artifacts/{aid}          → latest version
GET   /api/chats/{cid}/artifacts/{aid}?version=N → specific version
PATCH /api/chats/{cid}/artifacts/{aid}          → user edits in side panel,
                                                   creates version with
                                                   created_by="user"
```

User-driven edits show up in the next chat context as part of the
artifact's latest version — so the model sees what the user changed
and can pick up from there.

### Context injection

When an artifact exists in the current conversation, inject a summary
into the system prompt (NOT the full content, to preserve tokens):

```
=== ACTIVE ARTIFACTS ===
1. "Kinase inhibitor abstract" (text/markdown, 5 versions, 487 words)
2. "Figure 3 plot script" (application/python, 2 versions, 34 lines)
=== END ===

Use read_artifact(id) to see the current content of any artifact.
Use update_artifact(id, content_or_diff) to produce new versions.
```

This keeps context overhead bounded regardless of how many artifacts
exist. The model fetches content on-demand via `read_artifact`.

### Test plan

- `test_create_artifact`: create a markdown artifact, assert v1
  stored, artifact_created event fired
- `test_update_artifact_full`: update with new full content, assert
  v2 stored, artifact_updated event fired
- `test_update_artifact_diff`: update with a unified diff, assert
  diff applied correctly, v2 content matches expected
- `test_diff_rejection`: update with malformed diff, assert error
  returned and no new version created
- `test_user_edit_persists`: PATCH via the HTTP endpoint, next chat
  turn asks "what did I change?" and assert the model correctly
  describes the edit
- `test_artifact_conversation_isolation`: artifact in convo A
  invisible in convo B

### Effort

1.5-2 weeks. The biggest feature in the doc. Backend alone is maybe
5 days; frontend (side panel with version picker, inline edit mode,
diff view, live updates) is another 5-7 days in the separate repo.

Backend breakdown:
- Schema + migration: 1 h
- CRUD endpoints: 1 day
- MCP tools (create/read/update/list): 1 day
- Diff application logic with error handling: 0.5 day
- SSE event wiring in chat_service: 0.5 day
- Context injection + artifact summary rendering: 0.5 day
- Tests: 1 day

### Open questions

- **Artifact size cap**: 100 KB per version? Users writing full
  dissertations (maybe 500 KB+) might hit this. Start at 500 KB.
- **Version cap**: keep all versions forever, or prune old ones?
  Start with all forever, add pruning if storage becomes an issue.
- **Cross-conversation artifacts**: can an artifact be "shared" into
  another conversation? Probably not v1 — scope to current chat.
- **Binary artifacts** (PNG plots from the sandbox): store as
  base64 in the content column, or as files on disk referenced by
  id? Probably files, since they'll come from the sandbox artifacts
  feature (§2/§3) anyway. Unify with those.
- **Integration with §18 LaTeX and §2 sandbox**: when the sandbox
  produces a PDF or an image, should it become an artifact
  automatically? Probably yes — artifacts are the natural surface
  for any generated file.
- **Merging sandbox artifacts with document artifacts**: both are
  files the user wants to see, just different origins. Probably the
  same underlying table (§22) with a `source` field distinguishing
  `"model_written"` vs `"sandbox_generated"`.

---

## 23. Morning research digests (lightweight scheduler)

### Problem

Researchers want a daily update on what's new in their field:
*"what preprints landed on arxiv/biorxiv/medrxiv overnight that touch
my topics?"*. The existing deep research daemon (§10) is overkill for
this — we don't need 300-second SLURM jobs, we need a cheap query
that runs once a day per user and displays the results next time
they open the chat.

### Design — lightweight, cheap, cron-driven

This is **not** §10. No SLURM, no agents, no deep_research. Just:

1. User configures 1-5 "topics" (free-text queries like *"kinase
   inhibitors lipid membranes"*, *"gated deltanet attention"*).
2. Every morning, a sweeper hits Semantic Scholar with a `year=2026,
   publicationDateFrom=yesterday` filter for each topic, plus
   optionally arxiv/biorxiv RSS feeds if the user opted in.
3. Results are stored in a `digests` table.
4. Next time the user opens the chat (or on a new conversation
   start), the frontend fetches pending digests via a new endpoint
   and shows them as a dismissible top-of-sidebar card OR as an
   injected first assistant message.

Cost: maybe 3 S2 API calls per user per day. At 100 users, 300
calls/day. S2's authenticated tier is 1 req/sec — trivial.

### Schema

```sql
CREATE TABLE user_digest_topics (
    id TEXT PRIMARY KEY,
    user_email TEXT NOT NULL,
    topic TEXT NOT NULL,                -- e.g. "kinase inhibitors lipid membranes"
    sources TEXT NOT NULL,              -- JSON array, e.g. ["arxiv","biorxiv","semantic_scholar"]
    max_results_per_day INTEGER NOT NULL DEFAULT 10,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE user_digest_runs (
    id TEXT PRIMARY KEY,
    user_email TEXT NOT NULL,
    topic_id TEXT NOT NULL,
    run_at TEXT NOT NULL,
    results TEXT NOT NULL,              -- JSON array of paper records
    read_at TEXT,                        -- null until user marks as read
    FOREIGN KEY (topic_id) REFERENCES user_digest_topics(id) ON DELETE CASCADE
);

CREATE INDEX idx_digest_runs_user_unread
    ON user_digest_runs(user_email, read_at)
    WHERE read_at IS NULL;
```

### Scheduler

Daily cron entry (or a systemd timer) runs
`scripts/digests/run_daily_digests.py` at 7 AM in the **server's
local time** (keeping it simple). For every topic with `enabled=1`:

1. Query each configured source for papers dated `>= yesterday 00:00
   UTC`:
   - Semantic Scholar: `paper/search?query=...&fields=...&year=2026`
     with post-filtering on `publicationDate`
   - arXiv: OAI-PMH or the query API `http://export.arxiv.org/api/query`
     with `start_date` filter, filtered by topic via the `search_query`
     parameter
   - bioRxiv/medRxiv: their public details feed
     `https://api.biorxiv.org/details/{server}/{interval}/0`
2. Rank by relevance (simple BM25 over title+abstract against the topic
   string, via SQLite FTS or a tiny in-memory rank)
3. Keep top N (user-configurable, default 5 per topic)
4. Write a row to `user_digest_runs`

If no results: skip writing a row (don't clutter with empty digests).

### Endpoints

```
GET    /api/digests                    → user's current topics + any pending unread runs
POST   /api/digests/topics             → add a topic: {topic, sources, max_results}
PATCH  /api/digests/topics/{id}        → enable/disable/edit
DELETE /api/digests/topics/{id}        → remove
POST   /api/digests/runs/{id}/read     → mark a digest run as read
GET    /api/digests/unread_count       → badge count for the frontend sidebar
```

### Frontend presentation

Two options (pick based on UI preference):

**A. Sidebar card**: a dismissible "🔔 3 new preprints since
yesterday" card at the top of the conversation list. Click expands
to show the paper list with title, authors, abstract, and a
"discuss in a new chat" button that opens a pre-filled chat with the
paper already in context.

**B. First-message injection**: when the user opens any chat, the
first message is an assistant card summarizing what's new. Dismissible.

My vote: **A** (sidebar card). More explicit, less intrusive, easier
to ignore.

### MCP tools (so the model can interact with digests)

```python
list_digest_topics() -> dict
add_digest_topic(topic: str, sources: list[str] = None) -> dict
remove_digest_topic(topic_id: str) -> dict
get_unread_digests() -> dict
```

So users can say *"Add a digest topic for lipid-protein interactions"*
in chat and the model configures it for them. Much lower friction
than navigating to a settings page.

### Test plan

- `test_add_topic`: POST topic, GET digests, assert listed
- `test_digest_run_fake`: inject a synthetic run, GET unread, assert
  returned
- `test_mark_read`: POST read endpoint, assert subsequent unread
  count is zero
- `test_user_isolation`: user A's digests invisible to user B
- `test_add_topic_via_chat`: ask Meitner "add a digest for X", assert
  an `add_digest_topic` tool call and a matching row in the DB
- **Manual test**: actually run the sweeper against real sources for
  a day, inspect the results

### Effort

2-3 days including the sweeper script, endpoints, tools, and tests.
Breakdown:

- Schema + migration: 30 min
- Sweeper script (arxiv + biorxiv + S2 sources): 1 day
- Endpoints + MCP tools: half a day
- `chat_service` integration (unread badge, new-chat injection hook): 2 h
- Tests: 4 h
- Cron/systemd wiring: 30 min
- Manual verification pass: 2 h

### Open questions

- **Time zone**: digest runs at 7 AM server local. For users in other
  time zones, this is fine for v1 — they see yesterday's new papers
  when they wake up. Revisit if users complain. Full per-user
  timezone requires §25 (user profile) to exist first.
- **Arxiv API terms**: the API is public and rate-limited to 1
  request per 3 seconds. Our sweeper batches one call per topic
  per day, well within limits.
- **De-duplication across topics**: if two topics both return the
  same paper, should it show up once or twice? Show twice (under
  each topic) for clarity.
- **Historical catch-up**: if the sweeper missed a day (server down),
  should it catch up on the next run? Probably yes — widen the
  `publicationDateFrom` filter to cover the gap.

---

## 24. Temporary / ephemeral chats

### Problem

Some queries shouldn't stick around — privacy-sensitive questions,
quick fact checks, experiments ("how would you describe X differently
in persona Y?"). Currently everything is persisted.

### Design

Request body flag in `/api/chat/completions`:

```json
{
  "persona": "chat",
  "ephemeral": true,
  "messages": [{"role": "user", "content": "..."}]
}
```

When `ephemeral: true`:

- `chat_service` skips ALL persistence:
  - No `conversations` row created
  - No `messages` rows written
  - No auto-title generation
  - No summary generation
- The `conversation` SSE event still fires but with a synthetic in-memory
  id (prefix `ephemeral-`) so the frontend can display the chat
  session-scoped, and nothing else references it
- If the user sends a follow-up in the same ephemeral session, the
  frontend must include the previous messages in the `messages`
  array (as the model would otherwise have no history — nothing is
  stored server-side). Same way stateless OpenAI-compatible chat
  completions work.

### No DB changes

This is the cleanest part — nothing new to store because nothing is
stored.

### Backend changes

- `api_chat_completions` reads the `ephemeral` flag from the body
- `stream_chat_completion` gets a new `ephemeral: bool = False`
  parameter; when True, it skips `chat_store.create_conversation`,
  `chat_store.add_message`, title generation, and summary
  compaction. It still reads and uses the `messages` array from
  the request as-is (no server-side history).

### Frontend changes

- Toggle somewhere near the chat input ("🕶 Ephemeral mode")
- When active, submitted messages go into a local-only conversation
  array; each subsequent POST includes the full history
- A banner: "This chat won't be saved."
- Leaving the page or refreshing loses the session; that's the point

### Test plan

- `test_ephemeral_no_db_rows`: POST with `ephemeral: true`, assert
  no new conversations/messages in `chats.db`
- `test_ephemeral_stream_works`: assert the stream still produces a
  valid answer
- `test_ephemeral_follow_up`: POST ephemeral, then POST ephemeral
  again with the prior messages in the body, assert coherent
  multi-turn with no DB writes
- `test_ephemeral_doesnt_leak_to_listings`: after several ephemeral
  chats, GET /api/chats returns nothing new

### Effort

~2 hours. Tiny feature, high clarity.

### Open questions

- **Ephemeral + deep_research**: if the user fires deep_research in
  an ephemeral chat, the model still makes real tool calls (web
  searches etc.) that may be logged by external services. Document
  this — "ephemeral means not stored by Munin, not untrackable by
  the world".
- **Ephemeral + document upload**: disallow. Uploading a file and
  then not storing its context is contradictory. Reject with 400 if
  the user attempts it.
- **Ephemeral + artifacts**: artifacts also don't persist. Artifact
  side panel shows them session-scoped only.

---

## 25. Custom user instructions / profile

### Problem

Users have response preferences they want applied to every
conversation without repeating themselves: *"I know molecular
biology, skip the basics"*, *"always cite DOIs"*, *"I prefer British
spelling"*, *"I'm finishing my PhD"*. Currently there's no place to
put this.

### Relationship to §9 (user memory)

Deliberately distinct:

- **§9 user memory** = facts I've told you, stored via tool calls
  during conversation (*"remember that I work with Sunitinib"*).
  Model-curated.
- **§25 user profile** = how I want you to respond to me, set once
  in a profile page. User-curated, static across conversations.

They complement each other. Profile goes at the top of the system
prompt; memory goes below. Both get injected by `chat_service` on
every turn.

### Schema

```sql
CREATE TABLE user_profiles (
    user_email TEXT PRIMARY KEY,
    about_me TEXT,                 -- "I'm a biophysics PhD..."
    response_format TEXT,          -- "always cite DOIs, British spelling..."
    default_persona TEXT,          -- overrides global default
    default_rag_sources TEXT,      -- JSON array, e.g. ["papers","web"]
    timezone TEXT,                 -- IANA tz name, e.g. "Europe/Berlin"
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

Fields are all optional. Missing fields fall back to system
defaults.

Cap `about_me` and `response_format` at 1500 chars each (combined
~750 tokens, well-bounded system prompt overhead).

### Endpoints

```
GET  /api/profile              → load current user's profile (may be empty)
PUT  /api/profile              → upsert; returns the updated profile
DELETE /api/profile            → reset to defaults
```

All three scoped to `current_user_email`.

### Context injection in `chat_service`

Right before the existing persona system prompt, add:

```
=== USER PROFILE ===
About you: I'm a biophysics PhD candidate finishing up on kinase
inhibitors in lipid membranes. I know the field well — skip basic
biochemistry explanations.

Response preferences: Always cite DOIs when discussing papers.
Use British spelling. Prefer concise, technical responses with
inline code examples where relevant.
=== END USER PROFILE ===
```

### Frontend changes (not in this repo)

- New "Profile" page under settings
- Two textareas ("About you", "How I want you to respond")
- Persona / RAG source defaults
- Timezone picker (pulls IANA names from the frontend's locale
  library)
- Character count indicator with the 1500-char cap

### MCP tools (so the model can help set it up)

```python
get_my_profile() -> dict
update_my_profile(about_me=None, response_format=None,
                  default_persona=None, timezone=None) -> dict
```

Useful for "remember that I prefer..." flow where the user tells
the model once and it writes to the profile. Distinct from §9
memory because the target is structured fields, not free-form
memories.

### Test plan

- `test_profile_roundtrip`: PUT profile, GET, assert fields match
- `test_profile_injected_into_system_prompt`: set `about_me`,
  trigger a chat, inspect the constructed system prompt via a tool
  that echoes it back (or a private debug endpoint)
- `test_profile_default_persona`: set `default_persona=research`
  in profile, POST chat completion without persona, assert Curie
  was used
- `test_profile_cap`: PUT profile with 3000-char `about_me`, assert
  400 or silent truncation
- `test_profile_user_isolation`: same as every other feature

### Effort

~2 hours. ~100 lines of code.

### Open questions

- **Profile vs per-project instructions (§21)**: overlap. Profile is
  user-global; project instructions are scoped to one project.
  Precedence: project > profile > defaults. Both get injected when
  both exist — project instructions come AFTER profile in the
  system prompt so they override on conflict.
- **`timezone` field's immediate use**: §23 digests can honor it in
  v2 (per-user morning digest timing). v1 runs at server local time.
- **Import from §9 memory**: if the user has accumulated facts in
  memory and then creates a profile, should we offer to "promote"
  some memories to profile fields? Nice-to-have, not required.

---

## 26. Calculator tool (numeric / symbolic / physical)

### Problem

LLMs mis-compute arithmetic, especially when chained across multiple
operations or involving units. Claude and ChatGPT route math through
their code execution sandboxes — there's no dedicated "calculator"
feature in either. Munin's sandbox (§2) will eventually cover this,
but:

1. Sandbox kernel dispatch is ~500 ms per call; a dedicated tool is
   ~10 ms. Matters for in-flow arithmetic.
2. Sandbox is 1-2 weeks away; the calculator is ~1-2 hours.
3. Researchers need more than basic arithmetic: arbitrary precision,
   symbolic operations, physical constants, unit conversions.

### Design

One MCP tool, three modes:

```python
calculate(expression: str, mode: str = "numeric") -> dict
# mode ∈ {"numeric", "symbolic", "physical"}
#
#  → {"expression", "result", "mode", "error"?}
```

**Numeric mode** — safe arithmetic evaluation via `asteval` or
`simpleeval`. Whitelist of math functions (`math.*`, basic operators,
min/max/abs/round). No subprocess, no `eval`. Handles percentages,
parenthesized expressions, chained operations:

```
calculate("17% of 450")                  → 76.5
calculate("2**1024 mod 1000007")         → (exact big integer)
calculate("sqrt(2) * pi / log(10)")      → 1.931...
```

**Symbolic mode** — `sympy.parse_expr` + `sympy.sympify`, then
evaluate whatever callable the expression maps to. Supports
derivatives, integrals, equation solving, simplification, series
expansion, limits:

```
calculate("diff(sin(x)**2, x)", "symbolic")
  → "2*sin(x)*cos(x)"

calculate("integrate(exp(-x**2), (x, -oo, oo))", "symbolic")
  → "sqrt(pi)"

calculate("solve(x**2 - 3*x + 2, x)", "symbolic")
  → "[1, 2]"

calculate("limit(sin(x)/x, x, 0)", "symbolic")
  → "1"

calculate("series(cos(x), x, 0, 6)", "symbolic")
  → "1 - x**2/2 + x**4/24 + O(x**6)"
```

**Physical mode** — `pint.UnitRegistry` with its built-in physical
constants catalog. Handles unit-aware arithmetic and unit
conversions:

```
calculate("8.6 MJ to kcal", "physical")
  → "2054.59... kcal"

calculate("1 eV to J", "physical")
  → "1.60218e-19 J"

calculate("avogadro_constant * 1.66e-24 g", "physical")
  → "0.9997 g"  (≈ 1 gram, because 1 Da = 1/Na g)

calculate("boltzmann_constant * 310 K to eV", "physical")
  → "0.0267 eV"  (thermal energy at body temperature)

calculate("planck_constant * c / (500 nm) to eV", "physical")
  → "2.480 eV"  (energy of a 500 nm photon)
```

### Implementation notes

**File**: `retrieval/mcp/tools/calculator.py`, ~150 lines total.

**Safety** — the biggest concern is `eval`-style code execution.
Mitigations per mode:

- **Numeric**: use `asteval` with the default whitelist (no imports,
  no dunder access, no attribute access). Hard-block `__`,
  `globals`, `locals`, `exec`, `compile`, `open`.
- **Symbolic**: `sympy.parse_expr` with an explicit namespace that
  includes only whitelisted sympy functions. Block `lambdify`
  (which can compile arbitrary Python). Block `Symbol.__init__`
  with unsafe names.
- **Physical**: `pint.Quantity` and `pint.UnitRegistry`, operating
  on pre-parsed strings. pint's parser is strict and doesn't
  execute arbitrary code.

All three modes wrap execution in `try/except` and return
`{"error": "..."}` on failure rather than crashing. A short wall-clock
timeout (1 second, via `signal.alarm` or `concurrent.futures`) catches
pathological symbolic operations that would otherwise hang sympy.

### New dependencies

Add to `retrieval/requirements.txt`:

```
sympy>=1.12
pint>=0.23
asteval>=1.0   # safer than raw eval, whitelist-based
```

All three are pure Python, MIT-licensed, tens of MB each. No native
extensions, no GPU deps. Drop into the existing retrieval container
without any Dockerfile changes beyond `pip install -r requirements.txt`.

### Schema description

> Evaluate mathematical expressions precisely. Three modes:
>
> - `numeric`: arithmetic, trigonometry, logs, powers. Use for
>   percentages, chained arithmetic, concrete numbers.
> - `symbolic`: derivatives, integrals, equation solving, limits,
>   series expansions, simplification. Use when the user asks you
>   to compute symbolically or wants an exact answer.
> - `physical`: unit-aware arithmetic with physical constants (speed
>   of light, Planck's constant, Avogadro's number, Boltzmann,
>   gas constant, etc.) and unit conversions. Use whenever units
>   are involved.
>
> Always prefer this tool over doing arithmetic yourself — LLMs
> make silent math errors, and researchers notice.

### Persona prompt nudge

One sentence added to Meitner and Curie (Turing is debatable — code
questions rarely need a calculator):

> For any calculation beyond trivial single-digit arithmetic, use
> the `calculate` tool. Do not compute in your head.

### Test plan

- `test_numeric_basic`: `"17% of 450"` → 76.5
- `test_numeric_arbitrary_precision`: `"2**1024"` → exact integer
- `test_symbolic_derivative`: `"diff(x**3, x)"` → `"3*x**2"`
- `test_symbolic_integral`: `"integrate(1/x, x)"` → `"log(x)"`
- `test_symbolic_solve`: `"solve(x**2 - 4, x)"` → `"[-2, 2]"`
- `test_physical_conversion`: `"1 eV to J"` → `"1.60218e-19 J"`
- `test_physical_constants`: expression using
  `speed_of_light * 1 s to km` → `"299792.458 km"`
- `test_safety_no_import`: `"__import__('os').system('id')"` →
  error, not execution
- `test_safety_no_dunder`: `"(1).__class__.__bases__[0]"` → error
- `test_safety_timeout`: `"integrate(exp(exp(exp(x))), x)",
  symbolic` → error within 1 s, not hang
- `test_model_uses_calculator`: ask *"what's 17.3% of 6820?"*,
  assert the model calls `calculate` rather than answering from
  general knowledge

### Effort

~1-2 hours. Distribution:

- Tool module (three modes, safety wrappers, timeout): 45 min
- Schema + executor dispatch: 10 min
- Persona prompt additions: 5 min
- Requirements update: 2 min
- Test plan: 30 min
- Deploy + verify: 15 min

### Open questions

- **Unit registry scope**: `pint` ships with a large default
  registry (~500 units). Researchers in biology might want extras
  like daltons, angstroms, molar mass units — pint has these built
  in, so no extra work.
- **Precision for symbolic mode**: sympy can return exact answers
  (`sqrt(2)`), decimal approximations (`1.41421356...`), or both.
  Default to showing both when they differ: `"sqrt(2) ≈ 1.4142"`.
- **Unit parsing ambiguity**: pint has a few quirks — e.g. `m`
  could be meters or milli-. Set a disambiguation policy (prefer
  SI base units) in the registry config.
- **Upgrade path to sandbox**: once §2 is live, we could route
  complex symbolic operations through the sandbox (for faster
  sympy with compiled backends), but the dedicated tool stays for
  fast-path arithmetic and unit conversions. Not a retirement.

---

## 27. Parallelize `paper_cleanup repair-and-clean`

### Problem

`scripts/pipeline/paper_cleanup.py` (and the deployed copy at
`/opt/cluster/scripts/knowledge/paper_cleanup.py`) walks papers
strictly serially. Each paper triggers three sequential HTTP calls
to OpenAlex → Semantic Scholar → Crossref via
`MultiSourceMetadataFetcher.fetch_all()`. At ~3-5 seconds per paper
and a ~30k-paper corpus, a full `--max-check 30000` sweep takes
**25-40 hours of wall clock**. Nightly `--max-check 5000` runs are
workable but mean full-corpus coverage takes about a week.

### Design

Replace the `for i, doi in enumerate(dois_to_check)` loop in
`repair_and_clean` (around line 1036) with an async batch:

```python
import asyncio
import httpx

CLEANUP_CONCURRENCY = 8  # 8 concurrent metadata fan-outs

async def _fetch_one(client: httpx.AsyncClient, fetcher, doi: str,
                     check_pdf: bool, sem: asyncio.Semaphore) -> tuple[str, AggregatedMetadata]:
    async with sem:
        # MultiSourceMetadataFetcher needs an async variant;
        # either rewrite it with httpx or wrap the sync version
        # in asyncio.to_thread() for a minimal change.
        metadata = await asyncio.to_thread(fetcher.fetch_all, doi, check_pdf)
        return doi, metadata

async def _run_batch(fetcher, dois: list[str], check_pdf: bool):
    sem = asyncio.Semaphore(CLEANUP_CONCURRENCY)
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(
            *(_fetch_one(client, fetcher, d, check_pdf, sem) for d in dois)
        )
    return dict(results)
```

The Neo4j updates (enrichment) still happen serially after the fetch
stage, because Neo4j connections are typically shared and writes to
the same nodes from multiple coroutines would need locking anyway.
That's fine — Neo4j writes are ~50 ms each, not the bottleneck.

### Expected speedup

- Current: ~3-5s/paper × 30k = 25-40 hours
- With 8-way concurrency: ~30k / 8 × 4s avg = ~4 hours
- With 16-way concurrency: ~2 hours, but risks hitting S2 / OpenAlex
  rate limits. 8 is the polite ceiling.

### API rate limits to respect

- **OpenAlex**: 100,000 requests/day (polite pool with
  `ADMIN_EMAIL`). 10 req/s sustained. 8 concurrent is fine.
- **Semantic Scholar**: with API key, 1 req/s sustained. **This is
  the bottleneck.** Need a per-source semaphore, not just a global
  one. Implementation:

  ```python
  class SourceSemaphores:
      def __init__(self):
          self.openalex = asyncio.Semaphore(8)
          self.s2 = asyncio.Semaphore(1)   # strict 1 req/s
          self.crossref = asyncio.Semaphore(5)
  ```

  and the fetcher uses the appropriate semaphore per source.
- **Crossref**: polite pool with email, 50 req/s theoretical but
  they throttle hard at sustained traffic. 5 concurrent safe.

With per-source semaphores the real-world speedup is limited by S2
(~1 req/s). For 30k papers: 30000 seconds / 1 = ~8 hours of S2 time.
OpenAlex and Crossref fetches overlap that window for free. So the
realistic full-sweep time becomes **~8 hours**, down from ~30-40.

### Implementation approach

Two paths:

**A. Minimal change** — wrap `MultiSourceMetadataFetcher.fetch_all`
in `asyncio.to_thread()`, add a global semaphore, run the main loop
inside `asyncio.run()`. ~30 lines of code. Does not respect per-
source rate limits, so might hit S2 429s.

**B. Proper async rewrite** — port `MultiSourceMetadataFetcher` to
`httpx.AsyncClient` with per-source semaphores. ~100 lines. Safer
and faster. Recommended.

### Test plan

- `test_parallel_correctness`: process the same 20-DOI set serially
  and in parallel, assert identical `results` dict
- `test_rate_limit_respected`: mock S2 to return 429 on second
  concurrent request, assert the semaphore prevents it
- `test_speedup_smoke`: process 100 papers, assert wall clock is
  under 3 minutes (would be ~7 minutes serial)

### Effort

Half a day for path B. 1-2 hours for path A if you want it
shipped immediately and can tolerate occasional rate-limit retries.

### Open questions

- **Should this replace the existing serial path or coexist?** Keep
  both via a `--parallel N` flag. Default to `N=1` (serial) for
  safety; explicit opt-in for the async path.
- **Retry logic on 429**: needs exponential backoff. Currently the
  serial code just prints a warning and moves on. Parallel version
  should be stricter about retries.
- **Apply same treatment to `repair-auto`?** Probably not — that
  mode shells out to `paper_crawler.py` and `paper_pipeline.py`
  per paper, which are themselves heavy jobs. Parallelizing
  subprocess spawns would hammer disk + GPU.

---

## 28. Tag-scoped knowledge with contributor attribution

### Problem

Three related gaps:

1. **Personal in-chat notes**: users want to drop short facts into a
   private knowledge bucket they can query later (*"my standard buffer
   is 50 mM Tris pH 7.4, 37 °C"*) without uploading PDFs.
2. **Contributor-attributed uploads**: select users want to upload
   their own paper collection so it becomes searchable by everyone,
   with credit visible (*"this paper was contributed by Mustermann"*).
   Today the upload pipeline (§3) treats every doc as private to the
   uploader.
3. **Topic-scoped queries**: once §15 produces an embedding map with
   labelled clusters, users want to scope a query to a specific topic
   (*"#nmr what's the typical chemical shift for ..."*) rather than
   competing with the whole corpus.

The unifying surface is a **`#tag` syntax** in the chat input that
boosts or filters retrieval. Three flavours of tag, one mechanism.

### Design — tag taxonomy

Three tag namespaces, parsed by the frontend, passed to the backend
as a structured field in the chat completion request body:

| Tag form | Meaning | Backed by |
|---|---|---|
| `#me` | The asker's personal notes | new `user_notes` Qdrant collection (BGE-base embeddings, separate from papers) |
| `#@username` | Papers a contributor uploaded via the upload page | **the main `papers` Qdrant collection**, filtered by `payload.contributor == username`. Embedded with SPECTER via the existing `paper_pipeline.py`, indistinguishable from admin-curated papers in every other respect. |
| `#topic` (e.g. `#nmr`, `#lipids`) | Papers in that cluster | `papers` collection, filtered by `payload.cluster_id` matching the §15-derived slug |

Two of the three tags (`#@user` and `#topic`) query the **same**
underlying `papers` collection — they're just different payload
filters. Contributed papers participate in clustering automatically
because they live in the same collection §15 walks. A paper
contributed by Mustermann that happens to fall in the NMR cluster
is retrievable by both `#@mustermann` AND `#nmr`. That's the
behaviour you want.

`#me` is the only tag that touches a different collection.

The model isn't asked to parse tags — the frontend does that on
input and sends the chat request body with a structured field:

```json
{
  "persona": "research",
  "messages": [{"role": "user", "content": "explain CSA in lipid bilayers"}],
  "tags": [
    {"kind": "topic", "value": "nmr"},
    {"kind": "contributor", "value": "mustermann"}
  ]
}
```

Backend `chat_service` reads `tags`, sets a contextvar
`current_query_tags`, which the search tools (`paper_search`,
`semantic_scholar_search`, `search_user_docs`, `deep_research`)
consume to scope or boost results.

### Schema changes

#### Personal notes — new piece

```sql
CREATE TABLE user_notes (
    id TEXT PRIMARY KEY,
    user_email TEXT NOT NULL,
    content TEXT NOT NULL,         -- free text, ~max 2000 chars
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX idx_user_notes_user ON user_notes(user_email, updated_at DESC);
```

Per-user quota: 100 notes or 200 KB total content (whichever first).
Each note also gets a BGE-base embedding stored in a new Qdrant
collection `user_notes` (768d, cosine), with payload
`{user_email, note_id, content, created_at}`. Filtered by
`user_email` on every search — the same isolation pattern as
`user_docs`.

MCP tools:

```python
add_note(content: str) -> dict
list_notes(limit: int = 20) -> dict
search_my_notes(query: str, top_k: int = 5) -> dict
delete_note(note_id: str) -> dict
```

The model invokes these when the user says *"remember that..."* or
*"check my notes for..."*. The model never has direct access to
another user's notes — `current_user_email` contextvar enforces it.

#### Contributor uploads — into the main `papers` collection

Two completely separate upload paths today:

| Path | Endpoint | Storage | Embedder | Purpose |
|---|---|---|---|---|
| **Personal docs** | `POST /api/documents/upload` (private flow today) | `user_docs` collection, scoped per uploader | BGE-base | Chat-attached docs that only the uploader sees |
| **Curated corpus** | `paper_pipeline.py` (offline admin tool) | `papers` collection + Neo4j citation graph | SPECTER | Searchable corpus shared by everyone |

The contributor flow needs to land an uploaded PDF in **path 2**, not
path 1. That means the upload endpoint has to detect "this is a
contributor upload" and route the file through the heavier pipeline
(GROBID extract → CrossRef enrich → SPECTER embed → Qdrant `papers`
+ Neo4j Paper node), with a `contributor` field stamped on the
resulting payload.

**Allowlist gating.** A new file `config/contributors.yml` (admin-
only, edited in the repo and synced via `deploy.sh`) lists which
emails are allowed to contribute:

```yaml
contributors:
  - email: mustermann@example.com
    username: mustermann
    display_name: Hans Mustermann
  - email: alice@example.com
    username: alice
    display_name: Alice Schmidt
```

When a request comes in, `chat_service` resolves the uploader's
email against this list. If they're on it AND the upload form had
`contribute=true`, route through the contributor pipeline. Otherwise,
fall back to the standard private path. Non-contributors who set
`contribute=true` get coerced to `private` silently — no error.

**Upload endpoint extension.** `POST /api/documents/upload` learns
one new optional form field:

```
file: <PDF binary>
contribute: "true"   (optional — only honored for allowlist members)
```

Backend behaviour when `contribute=true` and uploader is allowlisted:

1. Save the PDF to `/opt/munin/data/papers/pdf/inbox/{uuid}.pdf`
2. Write a sidecar marker `/opt/munin/data/papers/pdf/inbox/{uuid}.contributor.json`
   containing the contributor metadata pulled from `contributors.yml`:
   ```json
   {
     "contributor_email": "mustermann@example.com",
     "contributor_username": "mustermann",
     "contributor_display_name": "Hans Mustermann",
     "uploaded_at": "2026-04-14T01:30:00Z"
   }
   ```
3. Call `paper_pipeline.py --single /opt/munin/data/papers/pdf/inbox/{uuid}.pdf`
   in a subprocess (or queue for async processing — see open question).
4. `paper_pipeline.py` is taught to look for the `.contributor.json`
   sidecar next to the PDF it's about to process and, if present,
   stamp `contributor`, `contributor_username`, `contributor_display_name`
   into the Qdrant `papers` payload AND into the Neo4j `Paper` node
   as properties.
5. After successful processing, move the PDF into the regular
   `pdf/` directory and delete the sidecar.
6. Return a job-status response to the user (`processing` or
   `embedded`, with the eventual `paper_id` once known).

**Extended `papers` Qdrant payload schema:**

```python
{
    # existing fields
    "title": "...",
    "doi": "...",
    "year": 2024,
    "authors": [...],
    "abstract": "...",

    # new from §15 (cluster_id is set on EVERY paper, not just contributed)
    "cluster_id": 7,
    "topic_label": "Kinase inhibitors in membranes",
    "topic_slug": "kinase-inhibitors-membranes",

    # new for contributors (only set on contributed papers; absent otherwise)
    "contributor_username": "mustermann",
    "contributor_display_name": "Hans Mustermann",
    "contributed_at": "2026-04-14T01:30:00Z",
}
```

Non-contributed papers simply don't have the `contributor_*` fields.
Qdrant's payload filter handles "field exists" naturally so
`#@mustermann` filters work without any null-handling.

**Neo4j `Paper` node extended properties:**

```cypher
(:Paper {
    doi: "10.1234/example",
    title: "...",
    year: 2024,
    contributor_username: "mustermann",        // optional
    contributor_display_name: "Hans Mustermann",
    contributed_at: "2026-04-14T01:30:00Z"
})
```

So citation graph traversal can also surface attribution.

**Result rendering.** When `paper_search`/`semantic_scholar_search`/
`deep_research` returns a paper that has a `contributor_display_name`
field, the result row includes it:

```json
{
  "title": "...",
  "doi": "...",
  "score": 0.92,
  "contributor_display_name": "Hans Mustermann"
}
```

The model is told (via persona prompts) to surface contributor
attribution in citations:

> When citing a paper that has a `contributor_display_name` field,
> include the contributor in the citation: *"according to a paper
> contributed by Hans Mustermann (DOI: 10.1234/example), ..."*

This makes the recognition / "reward" aspect explicit in the user-
facing output.

**The `user_docs` collection stays strictly personal.** No
`visibility` flag, no shared mode, no contributor field. Uploads
that don't go through the contributor path remain private to the
uploader, indistinguishable from today's behaviour.

#### Topic tags — derived from §15

When §15 ships, the embedding map script writes
`{cluster_id, topic_label}` back to each Qdrant point in the
`papers` collection (extending the existing payload schema):

```python
{
    # existing
    "title": "...",
    "doi": "...",
    "year": 2024,
    "authors": [...],

    # new (from §15 build_embedding_map.py)
    "cluster_id": 7,
    "topic_label": "Kinase inhibitors in membranes",
    "topic_slug": "kinase-inhibitors-membranes",  # url/tag-safe
}
```

`topic_slug` is the lowercase-dashed version that becomes the `#tag`.
A user types `#nmr` and the frontend autocompletes against a tag
catalog endpoint (see below) to find the closest matching slug.

### New endpoint: tag catalog

`GET /api/tags` returns everything the frontend autocomplete needs:

```json
{
  "topics": [
    {"slug": "nmr", "label": "Solid-state NMR", "paper_count": 1234},
    {"slug": "lipid-rafts", "label": "Lipid rafts", "paper_count": 567}
  ],
  "contributors": [
    {"username": "mustermann", "display_name": "Hans Mustermann",
     "doc_count": 42}
  ],
  "system": [
    {"slug": "me", "label": "My personal notes"}
  ]
}
```

Topics are read from the §15 cluster output. Contributors are
distinct from `user_docs` payloads where `visibility = "shared"`.

### Search tool integration

All three existing search tools gain an optional `tags` parameter.

```python
paper_search(
    query: str = None,
    queries: list[str] = None,
    top_k: int = 5,
    tags: list[dict] = None,  # [{"kind": "topic", "value": "nmr"}, ...]
)
```

How tags affect retrieval:

| Tag kind | Effect on `paper_search` / `semantic_scholar_search` / `deep_research` | Effect on `search_user_docs` | Effect on `search_my_notes` |
|---|---|---|---|
| `topic` (`#nmr`) | Filter `papers` collection by `payload.cluster_id` matching the slug. AND-filter when multiple topic tags. | No effect | No effect |
| `contributor` (`#@mustermann`) | Filter `papers` collection by `payload.contributor_username` matching the value. AND-filter when multiple. | No effect | No effect |
| `me` | No effect | No effect | Routes to the personal notes collection |

When `#nmr` and `#@mustermann` are passed together, both filters
apply to the **same** `papers` collection query — the result is
"papers in the NMR cluster contributed by Mustermann". This is
clean because both tags target the same Qdrant collection; no
fan-out across collections is needed.

When `#me` is also present, the backend additionally calls
`search_my_notes` and merges its results into the response with a
clear "from your personal notes" label. That's the only case where
results from two collections get merged in one response.

`deep_research` reads the same `current_query_tags` contextvar and
plumbs the filters through to its internal `paper_search` and
`semantic_scholar_search` calls. Sub-question expansion is
unaffected (the model still generates varied query phrasings); the
filter is applied at the search layer below.

### Frontend changes (not in this repo)

- Tag autocomplete on `#` input: fetch `/api/tags`, fuzzy-match,
  show inline chips
- Render contributor attribution in result snippets:
  *"...from a paper contributed by Hans Mustermann (uploaded 2026-03-15)"*
- A "My Notes" page in settings showing the personal notes list
  (CRUD via the new endpoints), with a "use in chat" toggle
- The upload page (currently for private docs) gains a "Share with
  the cluster" checkbox, only enabled for users on the contributors
  allowlist

### Backend changes summary

| File | Change |
|---|---|
| `chat_store.py` | New `user_notes` table + CRUD (notes are stored both here for ground-truth and embedded into the new Qdrant `user_notes` collection for retrieval) |
| `retrieval/notes_store.py` (new) | Per-user notes embedding + retrieval; mirrors `document_store` shape but for short free text. New Qdrant collection `user_notes`, BGE-base embeddings. |
| `retrieval/mcp/tools/notes.py` (new) | `add_note`, `list_notes`, `search_my_notes`, `delete_note` — `#me` tag dispatch target |
| `retrieval/document_store.py` | **Extended `upload_document`**: accepts `contribute=true` form flag. When the uploader is on the contributors allowlist, the file is dropped into `pdf/inbox/` with a sidecar JSON, and `paper_pipeline.py --single` is invoked (subprocess or queue) to route it through the heavyweight pipeline. Otherwise unchanged — file goes to `user_docs` as today. |
| `scripts/pipeline/paper_pipeline.py` | Reads `{uuid}.contributor.json` sidecar next to the input PDF (when present) and stamps `contributor_username`, `contributor_display_name`, `contributed_at` into both Qdrant `papers` payload and Neo4j `Paper` properties |
| `retrieval/mcp/tools/papers.py` | `paper_search` and `semantic_scholar_search` accept `tags`. Topic filter on `cluster_id`, contributor filter on `contributor_username`. Both apply to the `papers` Qdrant collection. |
| `retrieval/mcp/tools/research.py` | `deep_research` reads `current_query_tags` contextvar and plumbs filters through to internal sub-searches |
| `retrieval/mcp/tools/documents.py` | **No `tags` extension** — `user_docs` stays strictly personal. `search_user_docs` is unchanged. |
| `retrieval/mcp/schemas.py` | Schema additions for the four notes tools, the `tags` parameter on paper-search tools, and clarifying descriptions |
| `retrieval/main.py` | New endpoints: `GET /api/tags`, `POST /api/notes`, `GET /api/notes`, `DELETE /api/notes/{id}`. Existing `POST /api/documents/upload` accepts the new `contribute` form field. |
| `retrieval/chat_service.py` | Reads `tags` from request body, sets `current_query_tags` contextvar before dispatching tool calls. Optionally injects active tag list into the system prompt as context. |
| `retrieval/mcp/context.py` | New `current_query_tags` ContextVar |
| `config/contributors.yml` (new) | Admin-edited allowlist with email → username + display name mapping |
| `deploy.sh` | New `contributors` mode that copies `config/contributors.yml` to `/opt/munin/config/` |
| `personas/*.json` | Persona prompt update: explain tag semantics, instruct the model to surface contributor attribution in citations when `contributor_display_name` is present |

### Test plan

**Personal notes:**
- `test_add_and_search_note`: add a note, search for it, assert hit
- `test_notes_user_isolation`: user A's notes invisible to user B
- `test_notes_quota`: hit the 100-note limit, assert 429 or oldest-evict

**Contributor uploads:**
- `test_contribute_upload_allowlisted`: POST `/api/documents/upload`
  with `contribute=true` as a user listed in `contributors.yml`,
  assert the file lands in `pdf/inbox/` with a `.contributor.json`
  sidecar, then assert the resulting Qdrant `papers` point has
  `contributor_username` and `contributor_display_name` set.
- `test_contribute_upload_non_allowlisted`: POST same flag as a
  user NOT on the allowlist, assert the file is silently routed to
  `user_docs` (private) instead and no Qdrant `papers` point is
  created. No 4xx error.
- `test_contribute_upload_creates_neo4j_node`: after the pipeline
  processes a contributed PDF, assert the corresponding Neo4j
  `Paper` node has `contributor_username` and `contributed_at`
  properties.
- `test_search_finds_contributor_with_tag`: contribute a paper as
  Mustermann, then query `paper_search(tags=[{kind:"contributor",
  value:"mustermann"}])`, assert that paper appears in the results.
- `test_search_omits_contributor_when_no_tag`: same paper, query
  `paper_search` with no tags, assert it still appears (it's a
  first-class corpus member, just unfiltered) and that the result
  row carries `contributor_display_name` so the model can render
  attribution.
- `test_topic_and_contributor_combined`: contribute a paper that
  ends up in cluster `nmr`, query with both
  `tags=[{kind:"topic",value:"nmr"},{kind:"contributor",value:"mustermann"}]`,
  assert it's returned by the AND-combined filter.
- `test_user_docs_unchanged`: existing per-user document upload
  flow still works; non-contributor uploads still land in
  `user_docs` collection only.

**Topic tags:**
- `test_topic_tag_filters_papers`: paper_search with
  `tags=[{kind:"topic", value:"nmr"}]` returns only papers with
  matching cluster
- `test_unknown_tag_silently_ignored`: `#xyz123` (no matching
  cluster/contributor) doesn't break, just falls back to unfiltered
  search

**Tag catalog:**
- `test_tag_catalog_returns_topics_and_contributors`: GET `/api/tags`
  returns both lists with counts

**End-to-end:**
- `test_chat_with_topic_tag`: send a chat with a topic tag, assert
  the backend search results were scoped (e.g. by inspecting the
  tool_call arguments in the SSE stream)
- `test_chat_with_personal_tag`: ask `#me what's my standard buffer`,
  assert `search_my_notes` was called instead of paper_search

### Effort

~1.5 weeks total. Substantial because it touches a lot of surface
area, but no individual piece is hard.

Breakdown:

- `user_notes` table + CRUD + Qdrant collection: 1 day
- `notes_store.py` + MCP tools: 1 day
- `document_store.py` extensions for visibility + contributor: 0.5 day
- Allowlist gating + `config/contributors.yml`: 0.5 day
- §15 cluster_id payload write-back (depends on §15 shipping): 0.5 day
- Search tool tag plumbing across all 3 search tools + deep_research: 1 day
- `chat_service.py` tag contextvar + system prompt hint: 0.5 day
- `/api/tags` catalog endpoint: 0.5 day
- HTTP endpoints for notes CRUD: 0.5 day
- Personas update: 0.25 day
- Test plan: 1 day
- Frontend (separate repo): 3-4 days

### Open questions

- **Tag visibility in the system prompt**: should the model see a
  list of all available tags every turn, or only when the user
  actively passes one? Always-visible would cost ~500 tokens per
  request but lets the model proactively suggest tags. I'd lean
  toward "only when present" for v1 and revisit.
- **Tag inheritance to follow-ups**: if the user opens a chat with
  `#nmr` then asks five follow-up questions, do those follow-ups
  inherit the `#nmr` tag? Probably yes, set as a conversation-level
  attribute (new column on `conversations`). User can clear it
  with `#none` or by removing from the input chip UI.
- **Allowlist mechanism**: `config/contributors.yml` is admin-only
  (single admin per global decisions). When a new user wants
  contributor rights, they email the admin who edits the YAML and
  re-deploys (no UI). Acceptable for the current scale.
- **Personal notes vs §9 user memory**: overlap. §9 is for
  conversation-history-derived facts (the model curates), §28 notes
  are user-curated free text. Could unify them: `user_notes` becomes
  the storage backend for both, with a `kind: "user_curated" |
  "model_curated"` flag. Worth considering during implementation.
- **Tag namespaces collision**: if a topic slug happens to match a
  username (`#alice` exists as both a topic and a user), the
  frontend should show both in autocomplete and let the user pick.
  Backend treats them as distinct kinds (different parameters).
- **Contributor attribution beyond uploads**: should papers in the
  shared `papers` corpus also have contributor attribution if the
  ingestion source was a particular user? Probably yes — extend the
  `papers` collection payload too. Less common case, save for later.
- **#me tag privacy**: when `#me` is in play, the model must not
  echo the personal notes back to other users in any context. This
  is enforced naturally by the per-user Qdrant filter, but worth a
  test (`test_me_tag_user_isolation`).
- **Note size cap**: 2000 chars per note, 100 notes per user. Could
  also support longer "long-form notes" (10k chars) but at lower
  quota. Open question — start with one tier.
- **Citation in the chat response**: when search results come from
  a shared contribution, the assistant should naturally include
  attribution like *"according to the paper contributed by
  Mustermann, ..."*. This is prompt-level guidance, no code change
  needed beyond ensuring the tool result includes
  `contributor_display_name`.

### Connection to other features

- **§9 user memory** — overlap with personal notes. Consider
  unification (see open question above).
- **§15 embedding map** — provides the `cluster_id` and `topic_label`
  fields that power `#topic` tags. §28 cannot ship without §15
  having shipped first.
- **§3 document store** (already shipped) — extended in-place rather
  than replaced.
- **§4 self-description** — the FAQ tool should explain how tags
  work; one of the canned topics should be *"how do I use #tags?"*.
- **§21 projects** — projects could have default tags. *"Project
  Kinase Thesis defaults to `#kinase-inhibitors`"*. Out of scope
  for v1 but a natural extension once both ship.

---

## Updated priority order (agreed 2026-04-14)

1. **§2 Sandbox** — unlocks §3 (plotting), §18 (LaTeX), and parts of §12
2. **§5 Vision** — probed and confirmed, unlocks §11 and plot critique
3. **§6 Citation export** — trivial quick win
4. **§13 Paper download prominence** — tiny backend change, big UX lift
5. **§26 Calculator** — 1-2 hour quick win, fixes LLM arithmetic silently
6. **§24 Temporary chats** — 2-hour quick win, nice privacy feature
7. **§25 User profile** — 2-hour quick win, enables per-user timezone for §23
8. **§16 Pin conversations** — small, cross-device persistence
9. **§17 Search past conversations** — small, high utility
10. **§21 Projects** — biggest organizational improvement, ~1 week
11. **§9 User memory** — builds on profile/project infra
12. **§23 Morning digests** — researcher-specific, reuses S2 API
13. **§7 read_paper** — chains existing pieces
14. **§4 Self-description** — passive + FAQ tool
15. **§8 compare_papers** — builds on §7
16. **§20 S2-wide citation tools** — builds on §13
17. **§14 `ask_clarification` v2** — supersedes §1
18. **§22 Artifacts** — biggest UX transformation, ~2 weeks
19. **§18 LaTeX via sandbox** — requires §2
20. **§19 Autonomous agent selection** — prompt tuning + investigation
21. **§10 Background research jobs** — reuses SLURM infra
22. **§15 Embedding 2D map** — offline-heavy, good researcher-facing feature
23. **§28 Tag-scoped knowledge** — depends on §15; personal notes + contributor uploads + #topic queries
24. **§11 Equation OCR** — tiny on top of §5
25. **§27 `paper_cleanup` parallelization** — pipeline admin tool, ~8h → ~2-4h full sweeps
26. **§12 Reproducibility helper** — speculative, do last
27. **§1 `ask_clarification` v1** — DELETED, replaced by §14

§19 has an **Investigation column** to compare `research_orchestrator`
vs `deep_research` with real usage data before deciding whether to
retire the former.

### Rough grouping by "what to tackle in what order"

**Sprint 1 — quick wins (1-2 days total)**: §6, §13, §26, §24, §25, §16, §17 — **DONE 2026-04-14**

**Sprint 2 — foundation (1 week total)**: §2 sandbox, §3 scientific
plotting, §5 multimodal vision — **DONE 2026-04-14**. §2 shipped the
sidecar + run_python + artifact pipeline; §3 closed it with persona
prompts + on-disk file artifacts + xlsx support; §5 added multimodal
user attachments and closed §3's deferred vision feedback loop.
Deferred: sandbox Stage B (live stdout streaming,
`sandbox_install_package`, `save_artifact_to_documents`), §5 image
re-view capability (tracked prominently in §5 Status). (§2 was
defined as "unblocks most other things" — §3 and §5 were both
unblocked by §2 and shipped alongside it in the same sprint.)

**Sprint 3 — organizational (1 week)**: §21 projects

**Sprint 4 — researcher-specific (1 week)**: §23 digests, §7
read_paper, §20 S2 citations

**Sprint 5 — transformation (2 weeks)**: §22 artifacts

**Sprint 6 — everything else**: remaining items by priority

