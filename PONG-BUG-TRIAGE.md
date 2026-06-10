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
| 3 | Artifacts created "before" ask_clarification | ~~deterministic~~ → model behaviour | ❌ NOT A BUG — intercept already correct; folded into eval (#4) |
| 3b | Multiple `create_artifact` with same title in ONE response → redundant artifacts | deterministic (backend) | ⬜ TODO (Option C) |
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

## ❌ Item 3 — ask_clarification drops siblings — NOT A BUG (investigated)

**Finding:** the `ask_clarification` intercept ALREADY drops same-response
siblings correctly. `chat_service.py:2293` finds the clarification call
regardless of siblings and, on the valid path, emits `done` and
**`return`s at line 2437 — before the single `_run_tool_calls` site
(2466)**. Nothing executes tools between the delegate and clarification
intercepts. So `[create_artifact, ask_clarification]` in one response
short-circuits and the artifact never runs.

**Proof from the chat:** the 3 `create_artifact`s in msg 1 carry
execution RESULTS (real artifact IDs), which is only possible if they
ran in an EARLIER loop iteration where no clarification was present. The
6 tool calls in the persisted message are accumulated across iterations.
Real sequence: iter 1 = `[create_artifact, update_plan_item, set_plan,
create_artifact, create_artifact]` (all executed; note update_plan_item
ran before set_plan and errored); iter 2 = `[ask_clarification]`
(short-circuited correctly).

**Conclusion:** the pong "artifacts before clarification" is model
SEQUENCING across iterations — non-deterministic behaviour, folded into
the eval (#4) as: "if the model asks for clarification, no
`create_artifact` ran earlier in the same turn."

## ⬜ Item 3b — dedupe same-title `create_artifact` within one response (Option C)

**Symptom (deterministic, the real same-response bug):** in iter 1 the
model emitted THREE `create_artifact` calls with the same title ("Pong
Game") in ONE response, producing 3 redundant artifacts (and iter 3 did
the same with 2× "Pong - Single Player vs Computer"). One model response
should not mint multiple artifacts with the same title.

**Fix sketch:** pre-pass over a response's tool_calls before/inside
`_run_tool_calls` — for `create_artifact` calls sharing a normalised
title, actually create only ONE (keep the last = most refined); the
others get a synthetic tool_result `{skipped: duplicate, title}` so every
tool_call_id still has a result (history stays consistent) and the model
gets feedback. Does NOT touch legitimately-different artifacts (distinct
titles) in the same response.

**Code to inspect:** `chat_service.py` `_run_tool_calls` (def ~863, call
~2466) — dispatch + parallel execution + result assembly; decide whether
to dedupe as a pre-pass on the list or inside the executor.

**Test plan:** backend test — a tool_calls batch with 3 same-title
`create_artifact`s yields ONE created artifact + 2 synthetic
"duplicate" results; a batch with 2 DIFFERENT titles creates both.

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
