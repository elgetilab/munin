# Web UI E2E tests (Playwright, mocked-first)

Browser-level tests that drive the real chat UI like a user, with the network
mocked so they are deterministic and need no backend, GPU, or auth. This is the
layer that would have caught the recent regressions (em-dash in the status text,
the Deep Research toggle disabled in a new chat, the missing progress line) - all
frontend/wiring bugs the jsdom component tests miss because they mock the stores
and render components in isolation.

## Decisions (agreed)
- **Playwright** (`@playwright/test`), TypeScript, lives in `frontend/webui/e2e/`.
- **Mocked-first**: intercept `**/api/**` + `/auth/me` with canned responses and
  scripted SSE. A live full-stack smoke tier comes later (Playwright
  `setExtraHTTPHeaders({'X-Munin-Email': ...})` against a real backend, the eval
  harness pattern).
- **Add `data-testid`**: one sweep adding stable ids to ~15 key elements.
- **First flows**: core chat+streaming, Deep Research, artifacts (LaTeX/PDF,
  plot, code), tools+attach+clarification.

## Layout
```
frontend/webui/
  playwright.config.ts         # chromium, baseURL = vite preview, webServer
  e2e/
    PLAN.md                    # this file
    fixtures/
      mock.ts                  # installMocks(page, scenario): routes /api + /auth
      sse.ts                   # helpers to build an SSE body from an event list
      scenarios.ts             # canned personas/status/chats + chat scripts
    core-chat.spec.ts
    deep-research.spec.ts
    artifacts.spec.ts
    tools-attach.spec.ts
```

## The mock harness (the crux)
`installMocks(page, opts)` registers Playwright routes:
- Static JSON: `/auth/me`, `/api/status`, `/api/personas`, `/api/chats` (list),
  `/api/chats/:id`, `/api/chats/:id/artifacts`, `/api/tags`, `/api/usage/*`,
  `/api/announcement` - canned from `scenarios.ts`.
- `POST /api/chat/completions`: fulfil with `content-type: text/event-stream`
  and a body built by `sse.ts` from a scripted event list, e.g.
  `[token("Hello"), token(" world"), toolCall("web_search", {...}),
   artifactCreated({id,title,content_type}), done()]`. The frontend's
  fetch+ReadableStream reader consumes it; we assert the rendered result.
- `POST /api/research/start`: return `{job_id, conversation_id, created_conversation, status:"queued"}`.
- `GET /api/research/status/:id`: STATEFUL handler - returns a progression across
  successive polls (queued -> running w/ plan -> sub_question_start -> synthesising
  -> done + artifact_id), so the elapsed timer and step line can be asserted, and
  the artifact refetch on done can be verified.

## test-id sweep (add these)
Composer: `composer-textarea`, `composer-send`, `composer-attach-button`,
`attach-upload-file`, `attach-upload-image`, `attach-knowledge`,
`attach-deep-research`, `dr-armed-banner`, `dr-status-line`, `dr-status-step`,
`dr-status-elapsed`. App/panels: `ephemeral-toggle`, `artifacts-button`,
`artifact-panel`, `artifact-item`, `message-assistant`, `message-user`,
`clarification-card`, `memory-pill` (already has aria-labels).

## Test matrix (first suite)

### core-chat.spec.ts
- renders composer; typing enables send; empty disables it.
- send a scripted reply -> user bubble + streamed assistant bubble render in order.
- assistant markdown renders (bold/list/code).
- stop button appears while streaming, disappears after `done`.
- a scripted `error` SSE event renders an error state (not a silent hang).

### deep-research.spec.ts  (the recently-buggy area)
- toggle is in the "+" menu; enabling it shows the armed banner; send button
  switches to the flask icon; sending triggers `/api/research/start` (not a chat turn).
- **new chat**: with no conversation, the toggle is enabled and start succeeds
  (mock returns created_conversation) - the exact bug that shipped.
- status line shows the current step and a ticking elapsed timer as the mocked
  status progresses; on `done` it shows "report is in Artifacts" and the artifact
  list refetch adds the report.
- toggle disabled in incognito (ephemeral on).
- **no em-dash** in any DR status string (guards the regression directly).

### artifacts.spec.ts
- chat script emits `artifact_created` (text/html) -> Artifacts button count
  increments, panel opens, item renders, download link present.
- LaTeX/PDF: `compile_latex`-style tool result with a PDF artifact (external_url)
  -> panel shows the PDF item with a working download href.
- run_python -> PNG plot artifact renders as an image in the panel.

### tools-attach.spec.ts
- web_search tool_call + tool_result -> sources render in the answer.
- "+" menu: upload-file and attach-knowledge entries open their pickers; the
  #tag knowledge picker adds a scope chip.
- ask_clarification event -> clarification card renders with options; selecting
  one sends the follow-up.

## Running
- `npm run e2e` (added to package.json) -> `playwright test`.
- `npm run e2e:ui` -> `playwright test --ui` for debugging.
- Playwright `webServer` builds + serves the app (`vite preview`) so tests run
  against the real production bundle; no manual server needed.
- CI-ready (no GPU/backend); wire into a future `.github/workflows` when CI lands.

## Later: live smoke tier
A separate `e2e/live/` project, run manually/nightly against a real backend with
`X-Munin-Email` injected, covering ~3 happy paths (send->answer, LaTeX->PDF,
deep-research->report) to catch integration breaks the mocks can't.
