# Citation grounding: cite records, not recollections

Status: PLAN, awaiting approval (2026-11-24). Follows CITATION-VERIFY-PLAN.md.

## Why

citecheck (post-answer DOI verification, CITATION-VERIFY-PLAN.md) was measured
on the 30-case citation slice of the user-failure suite, 3 replicates, applied
to stored answers:

- rubric points: 11 up, 2 down pooled (5/0, 3/1, 3/1 per replicate); p = 0.11
  when each point is averaged over replicates first
- cases with unregistered DOIs: 7 to 4 across the three arms
- the plan's bar (p < 0.05, zero unregistered DOIs) is not met

Three read-only reviews (design, forensic, code) then looked for the cause.

### Where wrong references come from (forensic, 25 traced strings)

| Origin | Count |
|---|---|
| model memory: the string is in no tool result | 15 |
| miscopied from a tool result (mostly real artifact ids in invented URL shapes) | 6 |
| faithfully copied from a wrong corpus record (GROBID mis-keyed DOI, truncated DOI, wrong author list) | 4 |
| present in a tool result but dropped from context before the answer | 0 confirmed (mechanism exists, unprovable: no pre-truncation context is stored) |

In the reported chats, 74 of 80 DOIs appear verbatim in a tool result: when a
tool supplies a DOI the model copies it. What it invents is what the tools do
not supply (journal, pages, year, full author lists) and DOIs in turns where no
literature tool ran. 61 of 235 DOIs in the 9 worst cases are unregistered, almost
all in web_search-heavy turns (web results carry no DOI field).

### Structural causes (design review, verified in code)

1. **Citations are free text.** The research persona asks the model to "always
   include DOIs" and write the links itself. There are no source ids; the model
   retypes or recalls every bibliographic string.
2. **Records are incomplete.** Corpus search (`mcp/tools/search_agent.py`
   `_norm_corpus`, `_norm_oa`) drops the journal; the source agent's
   `ref_resolved` has no year or journal; web_search has no DOI or authors.
3. **Records do not survive.** Every tool result is cut to 8,000 chars
   (`tool_result.py`), dropping list items from the end: on a realistic search
   envelope all 8 evidence passages go. Older results in a turn are elided;
   later turns replay text only. The model cites from its own earlier prose.
4. **Artifact links cannot be rebuilt.** The ACTIVE ARTIFACTS block shows
   `art_...` ids; the URL needs the conversation id and the sandbox artifact id,
   which the model never sees.
5. **citecheck enforce makes one class worse.** A right reference with a wrong
   DOI is "corrected" to the DOI's paper (uf-f0030). The code review also found
   confirmed false flags ("However," read as an author, italic species names as
   journals, prose numbers as pages/years, a neighbouring citation's details
   attached to a DOI), URL tails kept on DOIs (`...v2.full`, `?via=ihub`), and a
   Stop that cannot interrupt the correction call.

Claude's own products avoid causes 1 and 3 by construction: the model cites a
retrieved result or a document span, and the system renders the link from it.

## Design

### Phase A: harden what exists (about 2 days)

1. **citecheck fixes** (warn stays the default):
   - wrong-DOI case: when a cited title (quoted or italic) clearly differs from
     the DOI's record title, the problem is "the DOI belongs to a different
     paper". Look the cited title up on Crossref (`query.bibliographic`, accept
     only a strong title match); the correction replaces the DOI with the found
     one, or removes the DOI and keeps the reference. Never rewrite the
     reference to the DOI's paper.
   - DOI extraction: strip URL tails (`#...`, `?...`, `.full`, `/-/DC...`,
     version suffixes after a known DOI where the base resolves).
   - lead author only in real reference-list items (a list marker or a
     reference-section line), and never a sentence adverb.
   - journal candidates exclude binomial species names and single emphasised
     words; the window stops at a sentence that holds another attribution.
   - pages and years only in citation position (already partly true).
   - the correction pass runs only for problems of kind exists/DOI/journal/
     year/author, never for low-confidence ones; it is cancelled by Stop.
   - flag (warn) a DOI that appears in no tool result of the conversation.
2. **Complete the records**: corpus and OA hits carry journal and year;
   `ref_resolved` carries year and journal (from `bibref`).
3. **Evidence survives the cut**: `truncate_tool_result` trims lower-ranked
   hits before evidence passages, and caps each passage instead of dropping it.
4. **Artifact URLs**: the ACTIVE ARTIFACTS block lists each artifact's real
   `/api/artifacts/...` URL; create_artifact returns it too.
5. **Corpus data**: the faithfully copied errors come from known corpus bugs
   (GROBID mis-keyed DOIs, the 921 papers on mangled DOIs awaiting your
   sign-off). Not part of this plan; listed so the residue is attributed.

### Phase B: source ledger, cite by id (1 to 2 weeks)

- Every search hit, evidence passage, read paper, web result and artifact in a
  conversation gets a short id (`S7`, `A3`) in a persisted per-conversation
  ledger holding the full record (resolved through `bibref`).
- Tool results show the ids. The persona says: cite only as `[S7]`; never type
  a DOI, URL or reference list.
- The post-turn pass (where citecheck runs) renders `[S7]` into a link and a
  reference list from the ledger, delivered with `answer_revised`. An unknown id
  is removed and flagged.
- Later turns get a compact ledger block (`S7: first author, year, title`), so
  earlier sources stay citable without replaying raw results.
- citecheck remains for DOIs the model still types (from memory), as a backstop.
- Risk: whether sage-a1.5 emits ids reliably. Measured, not assumed.

### Benchmark harness

- The runner deletes each replay chat afterwards, so tool results are lost and
  errors cannot be traced. Records will keep each turn's tool calls and results
  (private store only, as now).

## Measurement

- Phase A: the 30-case slice, 3 fresh replicates on the changed harness (a
  real replay, since phases A2 to A4 change what the model sees), compared with
  the 3 baseline replicates; plus the postfix arm for the citecheck-only fixes.
  Then one full 111-case run for regressions.
- Phase B: same, plus the share of references rendered from the ledger versus
  typed, and the unregistered-DOI count.
- Done when: rubric points on the slice up with p < 0.05 (replicate-averaged
  sign test), unregistered DOIs on the slice down by at least half, no
  significant regression on the full run.

## Open questions (defaults in bold)

1. Scope now:
   - **Phase A, measure, then decide on B with the numbers**
   - A and B together
2. citecheck mode in production meanwhile:
   - **deploy warn after the A1 fixes (marks only, never rewrites)**
   - keep it off until Phase A is measured
3. Replicates on the changed harness need the eval instance or production
   replays at concurrency 2:
   - **production replays at concurrency 2, as for the baselines**
   - wait for a free GPU and an eval instance
