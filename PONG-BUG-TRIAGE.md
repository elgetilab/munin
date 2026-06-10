# Pong-chat bug triage (TEMPORARY working doc)

Source chat: `d28ef78e-4344-48b9-9a2e-2c1e54426fca` ("Please code me a pong came", 2026-06-10, persona `chat`).
This is a transient scratchpad for fixing the pong-chat failures one by
one. Not meant to be committed long-term — as each item lands it moves
to a commit + (where relevant) `backend/docs/KNOWN-BUGS.md`. Delete when
the list is clear.

## Raw evidence from the chat

4 messages, 5 artifacts, persona never left `chat`.

- **msg 0** (user): "Please code me a pong came" (26 chars).
- **msg 1** (assistant, persona=chat): tool_calls in one response =
  `[create_artifact, update_plan_item, set_plan, create_artifact, create_artifact, ask_clarification]`.
  Visible content = only the ask_clarification card (Q1 play-as, Q2
  language). So it created **3 artifacts + set a plan** in the same turn
  it asked the user a clarifying question. The 3 artifacts all had
  `content_type='text/html'`, `language=None`.
- **msg 2** (user): answered the clarification (150 chars).
- **msg 3** (assistant, persona=chat): tool_calls =
  `[create_artifact, create_artifact, run_python]` (2 more artifacts,
  this time `language='html'`). Content opens with a `[backend warning]`
  about 2 referenced artifact URLs not created this turn (hallucinated/
  stale), then **gives the full answer TWICE** (two "Here is your Pong
  game" blocks, two different download links).

Artifacts (5): 2× "Pong - Single Player vs Computer" (text/html,
language=html), 3× "Pong Game" (text/html, **language empty**). All
`source=model_written` (stored as text in SQLite).

## Classification

| # | Failure | Kind | Status |
|---|---------|------|--------|
| 1 | HTML artifact downloads as `.txt` | deterministic (frontend) | ✅ FIXED + tested + deployed |
| 2 | No "working" spinner during artifact creation | deterministic (frontend) | ✅ FIXED + tested + deployed |
| 3 | Artifacts created alongside ask_clarification / set_plan (not dropped) | deterministic (backend) | ⬜ TODO |
| 4 | Eval harness for model-behaviour failures | new tooling | ⬜ TODO |
| 5 | No persona switch to `code` | model behaviour | → eval (#4) |
| 6 | Answer generated twice in one message | model behaviour | → eval (#4) |
| 7 | 5 redundant artifacts for one game | model behaviour | → eval (#4) |
| 8 | Hallucinated download URLs in prose | model behaviour (backend already warns) | → eval (#4) |

---

## ✅ Item 1 — `.txt` download (DONE)

Root cause: `getExtension` had no `text/html` / `language='html'` case →
fell through to `.txt`. Fixed by extracting to
`frontend/webui/src/lib/artifactDownload.ts` (language-first, content-
type fallback) + regression test `artifactDownload.test.ts` (the "pong
test", 7 cases). Deployed (bundle `index-BUZD8gQ-.js`). Commit pending.

---

## ✅ Item 2 — Spinner: user can't tell the model is working (DONE)

**Confirmed root cause:** the `artifact_created`/`artifact_updated`
chatStore handlers don't touch `streaming.phase` or `.content`, and the
backend emits the `tool_call` SSE event only AFTER the (long) artifact
argument finishes generating. So while the model writes the artifact
body, the frontend gets no events and `phase` stays `'thinking'` with
prose already on screen. `MessageList`'s vortexes had a gap: the big one
shows only when content is empty OR `phase==='tool_call'`; the small one
showed only when `phase==='generating'`. So `thinking` + non-empty
content → NEITHER rendered → looked frozen.

**Fix (deployed, bundle `index-D46w2Ycx.js`):**
- `MessageList.tsx`: broadened the small working spinner from
  `phase === 'generating'` to `phase !== 'tool_call'`, so any active
  non-tool_call phase with content shows it. Combined with the big
  vortex (empty content OR tool_call), every active state now shows
  exactly one spinner.
- Added `create_artifact`/`update_artifact` to `detectPhase` (→ `code`)
  for a sensible label once the tool call registers.
- Tests in `MessageList.test.tsx`: spinner shows in the
  thinking+content gap (fails pre-fix), still shows while generating,
  big vortex on empty content, none once idle; plus a `detectPhase`
  case. Full suite 206 green.

Note: the underlying backend behaviour (no progress events while a big
tool argument generates) is unchanged — the frontend now degrades
gracefully. A future backend improvement could emit a "writing artifact"
progress event, but it's not needed for the spinner.

---

## ⬜ Item 3 — ask_clarification / set_plan should drop sibling tool calls

**Symptom:** model emitted `create_artifact ×3` + `set_plan` +
`ask_clarification` in ONE response; the artifacts were executed even
though the turn was really "ask the user a question first". Result:
premature/garbage artifacts before the user has answered.

**Precedent:** the `delegate_to_persona` intercept in `chat_service.py`
already drops any non-delegation tool calls in the same response (a
control-flow tool can't also do regular work). `ask_clarification`
(and arguably `set_plan` when it gates the turn) should follow the same
rule.

**Code to inspect:**
- `chat_service.py` — the §14 `ask_clarification` intercept (does it
  currently run before/after `_run_tool_calls`? does it drop siblings?).
- Compare with the `delegate_to_persona` intercept (~line 1970) which
  does drop siblings.

**Fix sketch:** when `ask_clarification` is present in a response's
tool_calls, intercept BEFORE executing the others and drop them (only
the clarification runs), matching the delegate precedent.

**Test plan:** backend test (style of `test_clarification_persistence.py`)
— a response with `[create_artifact, ask_clarification]` executes ONLY
the clarification; no artifact is created/persisted.

---

## ⬜ Item 4 — Pong eval harness (model-behaviour guard)

Unit tests can't assert non-deterministic model behaviour. An eval runs
the pong prompt against the live model and checks properties, tolerant
of run-to-run variation (e.g. pass if K of N runs satisfy each).

**Properties to assert (from failures 5-8):**
- Delegates to the `code` persona (or at least produces a working game)
  for a "code me a game" request.
- Creates **at most 1** artifact for one game request (no 5×).
- Does NOT emit the final answer text twice.
- Any download URL in prose points at a real, fetchable artifact
  endpoint (no phantom-URL backend warning fires).
- The produced artifact is `content_type=text/html` and downloads as
  `.html` (ties back to item 1).

**Open design questions:**
- Where it runs (manual script vs CI; needs the live vLLM on the
  cluster, so not in frontend CI).
- N runs + pass threshold per property.
- Corpus: this chat + other pong conversations as seed prompts.

---

## Notes / decisions
- Items 2 and 3 are independent; either order is fine. Item 4 is the
  biggest and is tooling, not a fix.
- Model-behaviour items (5-8) are intentionally NOT unit-tested; the
  eval (item 4) is their home.
