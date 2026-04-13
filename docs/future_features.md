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
  Lean sidecar.
- **Kernel pool vs per-conversation spawn?** Per-conversation is
  simpler but wastes resources on idle conversations. A pool of N
  warm kernels with LRU assignment would be more efficient at scale.
  Start with per-conversation, optimise later.
- **Idle kernel timeout?** Kill after 15 min of no use? Reclaim memory
  without killing conversation state (forgivable — user can just
  re-upload data). Probably yes.

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
- **Preprocessing?** For scientific figures, we might want to pre-crop
  whitespace or enhance contrast before sending. Nice-to-have, not
  required.

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

## Updated priority order (agreed 2026-04-13)

1. **§2 Sandbox** — unlocks §3 (plotting), §18 (LaTeX), and parts of §12
2. **§5 Vision** — probed and confirmed, unlocks §11 and plot critique
3. **§6 Citation export** — trivial quick win
4. **§13 Paper download prominence** — tiny backend change, big UX lift
5. **§16 Pin conversations** — small, cross-device persistence
6. **§17 Search past conversations** — small, high utility
7. **§9 User memory** — builds on persistence infra
8. **§7 read_paper** — chains existing pieces
9. **§4 Self-description** — passive + FAQ tool
10. **§8 compare_papers** — builds on §7
11. **§20 S2-wide citation tools** — builds on §13
12. **§14 `ask_clarification` v2** — supersedes §1
13. **§18 LaTeX via sandbox** — requires §2
14. **§19 Autonomous agent selection** — prompt tuning + investigation
15. **§10 Background research jobs** — reuses SLURM infra
16. **§15 Embedding 2D map** — offline-heavy, good researcher-facing feature
17. **§11 Equation OCR** — tiny on top of §5
18. **§12 Reproducibility helper** — speculative, do last
19. **§1 `ask_clarification` v1** — DELETED, replaced by §14

§19 has an **Investigation column** to compare `research_orchestrator`
vs `deep_research` with real usage data before deciding whether to
retire the former.

