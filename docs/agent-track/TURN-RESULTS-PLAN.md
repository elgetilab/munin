# One result per turn: self-corrections and redraws, plan

Status: IMPLEMENTED 2026-11-16 ("proceed", Q1-Q4 defaults), not yet
deployed. Backend `4a5166b`, frontend and tests in the next commit.
Regression tests the user asked for, at three layers, each failing on the
old code: `backend/retrieval/tests/test_turn_results.py` (12: the supersede
rules, the AT1R turn replayed through the real chat loop, the show_protein
lookup), `frontend/webui/src/turnResults.test.tsx` (10; 7 fail on the old
client, the 3 that pass are the unchanged-behaviour checks), and
`frontend/webui/e2e/turn-results.spec.ts` (browser, both themes). Changes
from the plan: the redraw rule needs auto-captured figures with matching
plot types as well as code similarity >= 0.75 (similarity alone scored 0.88
for two different plots sharing their data loading); replaying the rules on
the user's chats collapses the three redraw turns to one figure each and the
11-times-rewritten script to one, and leaves turns with genuinely different
outputs alone. The AT1R turn itself only collapses when the model passes
`replaces` (new tool description and CORE RULE 7). Reported 2026-11-16 by the user as the
main bug: when the model corrects itself in a turn, the chat shows several
results.

## What happens (verified on the user's own chats, read-only)

`13550817` #5, "Can you show me At1R?":

1. The model guesses a PDB id from memory: "The first crystal structure of AT1R
   was solved in 2017 (PDB ID: 6JFQ)" and calls `show_protein("6JFQ")`. The
   entry is a bacterial peptide deformylase.
2. "That PDB ID was wrong ... Let me find the correct one": `search`, `source`.
3. `show_protein("4YAY")`, then the answer: "The interactive 3D viewer for
   4YAY is now displayed above."

What the user sees (`MessageList.tsx`): the task log, then ALL the turn's text
as one block (the wrong claim, the retraction, the answer), then ALL the
turn's artifacts (`InlineArtifacts`, deduplicated only by artifact id): two
protein viewers, the wrong one first, both BELOW a text that says "above".

The same shape across the user's history (712 assistant messages scanned):
about 20 turns with several artifact-producing calls of one kind.
- Figure redraws: `d9a31b8b`, `36b9d4cf`, `cbda361b` (2026-11-04): the plot is
  drawn, the plot-critique loop shows it back, the model redraws; 2 to 4
  versions of the same figure render inline, and the answer opens with the
  critique step's narration ("The plot matches the request, so no changes were
  needed.").
- Repeated `create_artifact` of the same file across steps: `e05af958` (11 x
  `zst_unpacker_full_filtered.py`), `5edc09c0`, `08e8a6f6`. The existing
  same-title dedup (chat d28ef78e) only works inside ONE model response.
- Self-corrections in text ("was wrong", "turned out to be") in `60bd54d2`,
  `1e125ed6`, `99fb3d41`.

Two separate causes:
- **C1. Narration is part of the answer.** Each model step's text is appended
  to one `content` string, so step text written before a tool call ("I'll
  show 6JFQ") reads as the result, and a later correction reads as a second
  result.
- **C2. Every artifact is a result.** Nothing records that a later artifact
  replaces an earlier one, so attempts and redraws all render inline.

## Fix

### 1. Steps: narration goes to the work log, the answer stays the message (C1)

- Backend (`chat_service.py`): track the text of each model step. Text from a
  step that ended in tool calls is narration; it is stored on the first tool
  call of that step as `step_text` (inside the existing `tool_calls` JSON, no
  schema change). `content` keeps the text of the final step and the wrap-up:
  the answer. If the final step wrote almost nothing (under 40 characters, e.g.
  the model said everything before its last tool call), nothing is moved and
  `content` stays as today.
- Audits (phantom links, citations, answer tags) keep running on the full text
  before the split, so nothing they check is lost.
- Frontend: `TaskLog` shows each `step_text` before its tool calls, in the
  log's muted style (the log is open while streaming and collapsible after).
  While streaming, the text written so far moves into the log when a
  `tool_call` event arrives, so the live view and the reloaded view match.
  Old messages (no `step_text`) render exactly as today.
- History replay (`chat_context`) already sends `content` only, so the next
  turn sees the answer without the abandoned claims.

### 2. Superseded results collapse (C2)

A result is superseded when a later call in the same turn replaces it. The
backend marks it (`superseded_by: <artifact id>` on the tool result, and a
`artifact_superseded` SSE event); the frontend drops it from the inline
results and lists it in the work log as "earlier attempt (replaced)", still
openable. Nothing is deleted. Rules, all within one turn:

- **Explicit**: `show_protein`, `create_artifact`, `run_python`, `edit_python`
  and `compile_latex` get an optional `replaces` argument (artifact ids). The
  `code`, `chat` and `research` personas get one rule: "When a result was
  wrong or you redo it, pass `replaces` with the ids of the results it
  replaces." This is the only way two `show_protein` calls collapse (showing
  two structures can be what the user asked for).
- **Same file**: `create_artifact` with the title of an artifact created
  earlier in the turn supersedes it (extends the d28ef78e dedup across steps).
  Saving the same filename again already keeps one artifact id.
- **Redrawn figure**: an `edit_python` call's figures supersede the figures of
  the call whose source it edited (the lineage `script_index` already tracks).
  A `run_python` whose code is a revision of an earlier figure-producing call
  in the same turn (difflib similarity of the two sources at least 0.6)
  supersedes that call's auto-captured figures. Different scripts (lower
  similarity) never supersede each other; the threshold is checked on the
  redraw turns listed above and on turns that legitimately make several
  different figures.

### 3. Fewer wrong guesses in the first place (`show_protein`)

`show_protein` gets an optional `query` (a protein name): retrieval asks the
RCSB search API and returns the top entries (id, title, organism, method,
resolution) for the model to choose from, instead of the model recalling an id.
With a `pdb_id`, the result already carries the entry title; the tool
description gets: "check that the title is the protein you meant; if not, call
again with `replaces`". (Needs egress; with egress off, `query` returns a
clear "not available" and the model falls back to search.)

## Tests

- Backend hermetic: a scripted turn (fake model) reproducing `13550817` #5:
  `content` holds only the final answer, the narration sits in `step_text`,
  the first viewer carries `superseded_by`; a turn whose last step is short
  keeps everything in `content`; two `show_protein` calls without `replaces`
  both stay; same-title `create_artifact` across steps; an `edit_python`
  redraw supersedes the earlier figure, a different script does not.
- Frontend (vitest): `TaskLog` renders `step_text`; `InlineArtifacts` hides
  superseded results and the log lists them; streaming moves text into the
  log on `tool_call`; old messages unchanged. e2e: the existing artifacts spec
  plus a self-correction fixture.
- `show_protein(query=...)` against a recorded RCSB response (no network in
  tests).

## Rollout

Retrieval and personas deploy, plus the web UI build and the VPS frontend
rsync (the user runs both). Old chats keep their look; new turns get the step
view.

## Open questions (defaults taken unless you say otherwise)

| # | Question | Default |
|---|---|---|
| Q1 | Narration in the log, or hidden entirely? | In the log (muted, collapsible): it shows how the answer was reached, and hiding it would hide tool reasoning the user may want. |
| Q2 | Superseded results: collapse, or delete the artifact? | Collapse, keep it openable from the log. Deleting loses the record, and a wrong `replaces` would destroy a real result. |
| Q3 | Do 3 (`show_protein` search) now? | Yes, it is small and removes the guess that caused the reported case. |
| Q4 | Apply the step split to the research and chat personas too? | Yes: the mechanism is persona-independent. |
