# Citation verification: from warning to enforcement

Status: PLAN, awaiting approval (2026-11-23).

## Why

The first user-failure baseline (`backend/benchmarks/scorecards/userfail/2026-11-23_sage-lk030.json`,
21/111 pass) shows citations as the largest failure family the harness can fix
without a better model:

- invented citations: 1/13 pass
- 4 cases cite DOIs that doi.org does not know (all checked by hand, all 404)
- 5 cases trip the existing "X et al." audit
- several "wrong fact" failures are misattributions: a real DOI with the wrong
  journal, volume, year or first author

30 cases touch citations (ids in "Measurement" below).

## What exists today

`chat_service.py` runs three post-turn audits:

- `audit_artifact_urls_in_content`
- `audit_paper_urls_in_content`
- `audit_citation_claims_in_content`

All three only annotate. They prepend a `[backend warning]` block to the
content that is saved. The live bubble is built from the streamed text
(`chatStore.ts`, `processDoneInline`), so a user sees the warning only after a
reload. Nothing checks whether a cited DOI exists or whether the authors,
journal and year next to it are right, even though `bibref.resolve()` can
already resolve DOIs (corpus, then Semantic Scholar, then Crossref).

## Design

### 1. `citecheck.py`: find and verify references (new, mostly pure)

**Extract** reference units from the final answer:

- a DOI with the text of its line or list item
- "Surname et al. (Year)" attributions with no DOI

DOI extraction reuses the parenthesis-safe rule from the benchmark checker
(`munin_bench/userfail/checks.py::extract_dois`).

**Resolve** each DOI with `bibref.resolve("doi", ...)`. Run the lookups in
parallel, with one overall timeout of 8 s. A timeout or network error counts
as "unverified" and is never flagged.

**Check** each unit:

| Check | Rule | Flag when |
|---|---|---|
| exists | the DOI resolves | doi.org/Crossref say 404 (not on a timeout) |
| first author | the surname named in the unit | it is not among the record's first 3 authors |
| year | the year named in the unit | it is more than 1 year off (online vs print) |
| journal | the journal named in the unit, by fuzzy token match against the full and short container titles | it clearly names a different journal |
| no-DOI attribution | grounded in this turn's tool results or earlier ones (the existing rule) | ungrounded |

Every check is conservative. A flag on a correct citation is worse than a
miss, as the existing audit already notes.

**Output:** a list of issues, each with the resolved record (authors, title,
journal, year, DOI).

### 2. Correction pass, only when there are issues

One extra model call, without streaming or tools, on the same backbone. Its
input:

- the answer
- the issues
- the resolved records

The instruction: correct each flagged citation from its record, remove any
citation or claim that no record supports, and change nothing else. The model
returns the full answer.

Guards:

- The revision is discarded if it drops below 70% of the original length or
  loses an artifact link.
- `citecheck` runs again on the revision.
- Anything still failing is marked inline as `[unverified: <reason>]` next to
  the citation. No footer: the judge read one as backend noise in the answer
  (uf-f0039), and corrected references need no trace.

Inline marking replaces deleting text deterministically, because removing a
citation from the middle of a sentence by regex breaks prose.

### 3. Delivery

A new SSE event `answer_revised` `{content, notes}` is sent before `done`.

- The client swaps the bubble's content for the revision. The counts travel
  in `notes` for later UI use.
- The revision is what gets saved.
- The same event carries the existing three audit warnings, so they are
  visible live too. This fixes the reload-only gap even when correction is
  off.

### 4. Config and cost

- `MUNIN_CITECHECK=enforce|warn|off`, with `warn` as the default until the
  measurement below passes.
- Turns with no references skip everything.
- Turns with references that all check out cost only the parallel lookups
  (memoised in `bibref`).
- A Prometheus counter records checked, corrected and unverified citations.

## Measurement

**Target slice: 30 cases.**

uf-f0030, uf-f0039, uf-f0044, uf-f0049, uf-f0077, uf-f0084, uf-f0092, uf-f0110,
uf-f0157, uf-f0158, uf-f0169, uf-f0188, uf-f0209, uf-f0239, uf-f0261, uf-f0274,
uf-f0275, uf-f0316, uf-f0319, uf-f0334, uf-f0345, uf-f0346, uf-f0363, uf-f0389,
uf-f0390, uf-f0483, uf-f0496, uf-f0539, uf-f0559, uf-rep_676a3238

**Runs:**

- The slice gets 3 replicates on an eval instance with the change, compared
  with `compare` against the baseline replicates `2026-11-23_sage-lk030{,-r2,-r3}`.
- Then one full 111-case run to check for regressions elsewhere.
- Unit tests use synthetic answers modelled on the reported chats (wrong
  journal, wrong first author, an invented DOI, a correct citation that must
  pass untouched).

**Done when:**

- the slice's rubric points met go up with sign-test p < 0.05
- `dois_resolve` failures on the slice drop to 0
- the full run shows no significant regression
- turns without issues add under 2 s at the median

## Out of scope

- Wrong facts unrelated to citations.
- Plan items 2 to 4 from the baseline review come next, each measured the
  same way:
  - phantom artifact links
  - an expected file that was never created
  - context overflow

## Open questions (defaults in bold)

1. Residual bad citations after one correction:
   - **inline `[unverified]` mark**
   - delete
2. Verification lookups under `egress=off`:
   - **allow the keyless verify-only sources (doi.org, Crossref), since they
     add no content**
   - keep them gated, in which case most eval cases can only verify against
     the corpus
3. Model for the correction pass:
   - **same backbone**
   - the hosted Haiku fallback
4. After a passing measurement:
   - **default `enforce`**
   - stay on `warn`
