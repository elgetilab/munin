# T2 + T3 edit plan (harness iteration, prompt-first)

Status: PLAN 2026-07-09. Concrete edits for T2 (over-tooling) + T3 (tool defs)
from `HARNESS-ITERATION-SCOPE.md`. Grounded in the actual current text.

## Measurement + deploy constraint (READ FIRST)

These change SERVED behaviour, so they must be DEPLOYED to be measured (the evals
drive the live `/api/chat/completions`, which loads `/opt/munin` personas + code
- not the working tree). Two consequences:

- **`vi` cannot deploy** (deploy.sh is root/varghele). So each measured step needs
  a varghele deploy.
- The document-attachment WIP is now **committed** (`329c150`, varghele, 2026-07-09)
  and landed cleanly under the T2/T3 commits (no conflict; disjoint files). So
  the "park behind uncommitted WIP" concern is RESOLVED:
  - **T2 = personas-only** (`deploy.sh personas` + restart) - independent of the
    attachment code. Safe to deploy + measure now. APPLIED (`af2d04d`, research
    1.3->1.4).
  - **T3 = retrieval code** (schemas.py, tool_search.py) - APPLIED (`fdf8ded`).
    A `deploy.sh retrieval` would now ship `329c150` + T3 together as a coherent
    unit. No longer blocked by uncommitted code; the only question is whether
    varghele considers `329c150` ready to go live - coordinate the timing, do not
    self-deploy.

Dashboard per step: routing anchor eval (**gate: >= 0.950, no regression**),
`soft_max_calls` over-tooling diagnostic (**target: drop**), Track B agentic arm
(**target: grounding holds/improves** vs 0.356; capture resumable, GPU score
~7-9 min).

## Critical scoping note: T3 is NARROWER than the code sweep implied

Verified in `mcp/schemas.py`: `get_citations`/`s2_get_citations`,
`get_references`/`s2_get_references`, and `deep_research` ALREADY disambiguate
well (local-fast-vs-S2-broad; "USE THIS INSTEAD OF ... for short lookups prefer
individual tools"). **Do NOT re-edit those - it's churn / double-steering risk.**
The genuine gaps are only: (a) `paper_search` vs `semantic_scholar_search` don't
front-load the local-curated-incomplete vs external-slower-quota tradeoff; (b)
`tool_search` has no stemming.

---

## T2 - research fragment over-tooling (`shared/personas/research.json`, personas deploy)

Three edits; keep everything A5 tuned (local-first-then-branch, DOI-vs-URL,
memory cue, clarification rule) INTACT.

1. **Default depth deep -> medium.** Current: "Your default opening move for any
   substantive research question is `deep_research` in `depth="deep"` mode." This
   fights the tool's OWN description ("default 'medium' ... is the right choice
   for most questions"). Change the fragment default to `depth="medium"`, and
   reserve `deep` for explicitly exhaustive asks ("comprehensive", "thorough
   review", "exhaustive"). Cuts the default fan-out from ~60 API calls
   (5 sub x 4 variants x 3 sources) to ~27 (3 x 3 x 3).

2. **Replace the over-tooling licence.** Current: "Ten or more tool calls in a
   single conversation is normal and expected. You are not optimising for call
   count; you are optimising for answer completeness and source density."
   Replace with a bounded rule, e.g.: "After `deep_research`, make AT MOST 3-4
   targeted follow-up tool calls, each closing a NAMED gap (a specific citation
   network, a key author, one paper to read in full). Then synthesise. Do NOT
   re-search the same question with new phrasings or speculatively broaden -
   breadth is `deep_research`'s job; after it, your job is to close specific gaps
   and write the answer."

3. **Trim the follow-up-pattern list** so it reads as "close named gaps", not "run
   all of these chains" (it currently enumerates paper_lookup -> get_citations ->
   get_references, get_author_papers, etc., which reads as a checklist). Keep the
   patterns as EXAMPLES, capped by edit #2.

Risk: routing-eval items with tool sequences (`sota_phip`, `reroute`) could
shift. Gate on anchor >= 0.950. Expect `soft_max_calls` to drop and Track B
grounding to hold or rise (fewer, more-targeted retrievals = less context bloat).

---

## T3a - paper_search / semantic_scholar_search scope (`mcp/schemas.py`, PARK deploy)

- `paper_search` (schemas.py:62): prepend scope - "Search the **LOCAL curated**
  paper corpus (fast; the corpus is deliberately curated and INCOMPLETE, so
  branch to `semantic_scholar_search` when hits are thin or off-corpus). ..."
  (keep the existing multi-query / returns text).
- `semantic_scholar_search` (schemas.py:85): prepend - "Search the **EXTERNAL**
  Semantic Scholar index (200M+ papers; broader coverage but slower and uses the
  S2 quota). ..." (keep the rest).
- Note: this moves part of A5's local-first-then-branch CUE onto the tool
  surface, reinforcing (not replacing) the fragment line. KEEP the fragment line
  too (A5 tuned it); watch for double-steering in the anchor eval.

## T3b - tool_search stemming (`mcp/tools/tool_search.py`, PARK deploy)

- Add a deterministic, dependency-free suffix normaliser to `_tokenize`
  (line 59) applied to BOTH query and doc tokens (so "cite"/"citing"/"citations"
  collapse to one stem). Minimal ruleset: strip trailing `ations|ation|ing|ers|
  er|es|s|ed` to a stem (guard min length). NOT nltk/Porter (not a dep; keep it
  simple - the tool vocab is small + technical).
- Add a test to `tests/test_tool_search.py`: "papers that cite X" surfaces
  `get_citations`; a stem case ("citing"/"citations") matches. Guard against
  over-stemming collapsing distinct tools.

---

## Sequencing

1. **T2** edits -> commit -> (varghele) `deploy.sh personas` + restart -> measure
   anchor (>=0.950) + `soft_max_calls` (drop) + Track B arm (grounding). Iterate
   the wording if the anchor regresses (A5 cadence: edit -> deploy -> measure).
2. **T3a + T3b** edits -> commit -> **PARK** the retrieval deploy until the
   attachment WIP lands -> coordinate ONE `deploy.sh retrieval` -> re-measure.

Rationale: T2 is the highest-leverage over-tooling fix AND independently
deployable (personas-only, WIP-safe); T3 is retrofit-ready but deploy-blocked by
the attachment WIP, so its edits wait in the tree.

## Open questions

1. **Default depth:** change the fragment default `deep -> medium` (matches the
   tool's own guidance), or keep deep-default and only bound follow-ups? (Recommend
   medium-default - it's the single biggest fan-out cut.)
2. **Follow-up cap:** 3-4 targeted follow-ups after deep_research the right bound?
   (Start at 4; `soft_max_calls` will tell us.)
3. **Stemming:** dependency-free suffix normaliser (recommend) vs adding nltk?
4. **Deploy:** confirm varghele runs `deploy.sh personas` for T2 measurement, and
   that T3's retrieval deploy is parked behind the attachment WIP.
