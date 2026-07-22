# Autonomous session log (2026-07-22)

Working through, in order, while the user is away: (0) frontend DR inline
rendering + composer block, then (R1) report structure, (R2) wider funnel +
snowball, (R3) contradiction synthesis, (R4) quantitative notes. Test + commit +
log after each step. Nothing is deployed autonomously (backend needs
`deploy.sh retrieval`; the inline UI depends on the un-deployed event-log
backend, so the frontend is committed-not-deployed too — deploy both together).

Status key: DONE / IN PROGRESS / BLOCKED / SKIPPED.

---

## Step 0 — Frontend DR inline rendering + block composer  [DONE]  (f3149db)

Built: `ResearchTimeline.tsx` renders the durable event log inline (plan
checklist with per-item status, a tool card per search/read, findings, and a
"View the full report" link that opens the artifact panel). `deepResearchStore`
now holds the full job+events; App loads it via `/for-conversation` on chat open
and polls `/status` while active; the composer is disabled + shows `dr-blocked`
while a job runs. Removed the old composer status pill.

Verified: full E2E suite **18/18 green**, including new tests "renders the
research inline and blocks the composer", "inline research view contains no
em-dash", and the fresh-chat regression now asserting the timeline. Fixed 2
em-dashes I'd introduced in the timeline copy.

NOT deployed: this UI depends on the un-deployed event-log backend (commit
01eaa68). Deploy backend (`deploy.sh retrieval`) + frontend (rsync) together.

Problems: none blocking. Minor: a stale-WAL hang during an earlier isolated
store test (killed process left a `.db-wal` lock) - environmental, not a code
bug; the code round-trips fine.

## Step R1 — Claude-style report structure  [DONE]  (fe7c76b)

Reshaped the report builder: `# Title / ## TL;DR / ## Key Findings / ## Details
(per sub-question) / ## Sources / ## Caveats`. TL;DR is one grounded LLM pass over
the notes; Key Findings + Sources + Caveats are deterministic from notes +
citations. Sections are gated: TL;DR/Key Findings/Sources appear only when there
are grounded notes; Caveats appears only when a sub-question is unresolved or a
citation was abstract-only.

Verified: 4/4 new unit tests (`tests/test_dr_report.py`, pure functions) pass in
the runner venv. Two live smoke runs: (a) off-corpus, 0 notes -> correctly shows
Details + Caveats (lists both unanswered sub-questions), omits TL;DR/Key
Findings/Sources; (b) in-corpus CRISPR, 6 notes/5 citations -> ALL five sections
render, TL;DR = 5 cited headline bullets, Key Findings numbered + cited.

Observed (not an R1 bug; for R2/R3): retrieval/screening let a couple of
off-target papers through (a Cas9-nuclease paper cited for a base-editor
sub-question; a "Phred" methods paper), so some notes hedge "the text does not
cover X; from broader knowledge...". Structure is correct; relevance is the
funnel/screen job (R2) and synthesis (R3).

## Step R2 — widen funnel + depth-1 snowball  [DONE]  (8e2583e)

Raised defaults: `read_cap` 4->6, `screen_keep` 12->15. Extracted the read loop
into `_read_candidates` (dedup by DOI, cap, notes+tool-cards). Added `_snowball`:
after a sub-question resolves, pull the references of its two strongest cited
papers via `s2_get_references`, screen them for relevance, and read up to
`snowball_reads=3` more. Graceful if S2 fails.

Verified: 6/6 unit tests pass (`_read_candidates` dedup + cap + notes;
`_seen_dois`; plus R1 tests). Live smoke (CRISPR, 1 sub-question): completed
`resolved`, **n_reads=9** (vs ~2-3 before), 4 notes/4 citations, and the
`snowball` decision fired in the trace. Confirms wider reads + snowball work
end-to-end and don't break the run.

## Step R3 + R4 — cross-source synthesis, contradiction, quantitative notes  [DONE]  (4ed63b5)

R3: `_group_by_subquestion` (deterministic grouping) + a stronger section-synthesis
prompt that numbers the notes and requires SYNTHESIS (connect/contrast, not list)
and explicit naming of disagreements. R4: `_extract_value` pulls a number+unit
(only real quantities, not bare counts) out of each finding into the note's
`value`/`unit`; `_numeric_conflicts` flags notes with the same unit but different
values - a concrete contradiction signal fed into the synthesiser. Key Findings /
synthesis now carry hard numbers.

Verified: **9/9 unit tests pass** (added `_group_by_subquestion`, `_extract_value`
across 6 cases incl. the µmol/L-vs-µM alternation-order fix, and
`_numeric_conflicts`). Live capstone run in progress (see below).

## Capstone — full run on the Claude comparison question  [IN PROGRESS]

Running the exact question the user gave Claude (membrane lipid composition x
kinase-inhibitor partitioning) through the full pipeline (OA lever + wider funnel
+ snowball + R1 report). Saves to `todo_v2/MUNIN-DR-SAMPLE-REPORT.md` so the user
can compare Munin's report side-by-side with the Claude reference
(`compass_artifact_...md`).
