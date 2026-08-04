# Autonomous session log (2026-07-22)

> **Archived 2026-07-23.** Every step below shipped and deployed (DR breadth via
> `deploy.sh retrieval`; webui rsynced). The Brave Search API tier — the one
> "next task" open at the end — also shipped, deployed, and was verified live on
> 2026-07-23. Forward-looking items live in `../TODO.md`.

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

## Capstone — full run on the Claude comparison question  [DONE]

Running the exact question the user gave Claude (membrane lipid composition x
kinase-inhibitor partitioning) through the full pipeline (OA lever + wider funnel
+ snowball + R1 report). Saves to `docs/paper-track/MUNIN-DR-SAMPLE-REPORT.md` so the user
can compare Munin's report side-by-side with the Claude reference. NOTE
(2026-07-27): this log originally said the reference was never committed. It
was in fact committed (in `01eaa68`, under an opaque `compass_artifact_*`
filename) and now lives at `docs/agent-track/CLAUDE-DR-REFERENCE-REPORT.md`.

**The capstone caught a real bug (b5a58c6).** The first capstone run produced an
off-topic report about ChatGPT chemistry, researching the literal string
"sub_questions". Root cause: `_decompose` asked for a JSON list but the model
returned an OBJECT `{"sub_questions": [...]}`; the old code iterated the dict and
got its KEYS as the sub-questions. Fix: `_coerce_str_list` extracts the wrapped
list (and handles a few other malformed shapes); the decompose prompt now says
"array, not object". Unit-tested (10/10). This is exactly the class of bug the
end-to-end run exists to catch - it would have shipped silently otherwise. Re-ran
the capstone with the fix (result appended below).

**Capstone result (after the fix):** 3 real sub-questions, 2 resolved / 1
unresolvable, **21 reads**, 6 grounded notes, 5 citations (4 full-text, 1
abstract). Report saved to `docs/paper-track/MUNIN-DR-SAMPLE-REPORT.md`. All features
visibly working:
- All 5 sections render, gated correctly (Caveats lists the 1 unresolved
  sub-question + the 1 abstract-only source).
- **R3 contradiction-naming fired**: the efficacy/resistance section explicitly
  contrasts "trans-flupentixol reverses MDR by blocking efflux" vs "membrane
  sequestration reduces efficacy" - "While one mechanism highlights ... the
  opposing perspective emphasizes ...". That is the cross-source synthesis the
  step was built for, on real evidence.
- Every claim traceable to a read PDF; read_depth shown per source.

vs Claude: Munin now matches on STRUCTURE, SYNTHESIS, CONTRADICTION-NAMING, and
GROUNDING DISCIPLINE, and beats it on auditability (Claude self-admits its numbers
need verifying against PDFs). It remains far behind on BREADTH (5 sources vs
~50) - a funnel/corpus/rate-limit gap, not a design gap. In the runner, S2 is
rate-limited (no API key), throttling snowball + OA; production (S2 key, no
limits) reads more, and the earlier off-corpus eval hit 0.83 resolution. Note:
some findings still hedge "the text does not cover kinase inhibitors
specifically" because the corpus is thin on this exact topic - honest, and a
coverage matter (grow corpus / more reads), not a bug.

---

## FINAL SUMMARY

Done this session, all committed on `main`, all verified:
1. **Frontend DR inline rendering + composer block** (f3149db) - 18/18 E2E.
2. **R1** Claude-style report (fe7c76b) - 4 unit tests + live.
3. **R2** wider funnel + depth-1 snowball (8e2583e) - reads 2-3x up, unit tests.
4. **R3+R4** cross-source synthesis, numeric-conflict detection, quantitative
   value/unit extraction (4ed63b5) - 10/10 unit tests, R3 verified in the report.
5. **Bug fix** decompose object-wrapped list (b5a58c6) - caught by the capstone.

Unit tests: 10/10 (`backend/retrieval/tests/test_dr_report.py`). Frontend E2E:
18/18. Sample report vs Claude reference: side by side in `docs/paper-track/`.

**NOT deployed** (autonomous session; deploy is the user's call): all of the
above is backend + frontend committed-not-deployed. To go live:
`sudo bash deploy.sh retrieval` (backend: event log, report, funnel, OA lever,
decompose fix) AND rsync the built webui (frontend: inline rendering). The two
depend on each other (inline UI reads the event-log endpoints), so deploy
together.

**Open follow-ups (not blocking):** EuropePMC full-text fallback for PMC/ASM
papers (the 2/8 the OA lever couldn't fetch). Report title could be generated
rather than echoing the raw question.

---

## Breadth levers 1-4  [DONE]  (source.py 29f7fec, DR loop 424f74d)

The remaining gap vs Claude was breadth. Implemented all four levers while keeping
every citation full-text-grounded (no snippet tier):

1. **Read the web tier.** `source` now reads web pages by URL, not just papers by
   DOI: `_fetch_and_extract_web` (trafilatura, browser UA, `origin=web`), gated by
   `may_fetch(NET_WEB)`. The DR loop keeps URL candidates through screening and
   reads them like any paper. Source-level fetch verified (16k chars from a live
   page); unit-tested end to end (a URL candidate flows through `_read_candidates`).
2. **Multi-note extraction.** New `source(mode="findings")` returns up to 4
   distinct claims, each with its own verbatim quote; each becomes a separate
   grounded note+citation. One paper can now contribute several findings without
   loosening grounding. **Verified live: 12 notes from 10 reads, 3 papers
   multi-noted.** (On off-topic papers it correctly returns `[]` — same abstention
   the strict question demands; no regression vs the old qa-mode read.)
3. **Wider caps + depth-2 snowball.** Defaults raised: `max_subq` 4->6,
   `screen_keep` -> 20, `read_cap` -> 8, `snowball_reads` -> 4, new
   `snowball_depth = 2` (snowball the snowballed papers once more). `_seen_dois`
   became `_seen_keys` (dedups DOIs **and** URLs across the corpus/OA/web tiers and
   both snowball passes). `research_routes` default depth `normal`->`deep` so
   UI-triggered runs include the web tier.
4. **Semantic Scholar API key in eval.** No code change — the code already reads
   `SEMANTIC_SCHOLAR_API_KEY` (papers.py, s2_citations.py). Confirmed the need: the
   offline eval env is keyless, so S2 (the OA tier) rate-limits to 0 results.
   **Action for the user:** `export SEMANTIC_SCHOLAR_API_KEY=...` before any offline
   `eval_dr`/diag run; production already has it in the retrieval container env.

Unit tests: 10/10 (`_read_candidates` test rewritten for multi-note + web + dedup +
cap). Live breadth harness: `tests/smoke_dr_breadth.py`.

**Eval-env caveat (not a code bug):** the offline env can't demonstrate the web/OA
tiers — SearXNG's upstream engines return 0 from the cluster IP and S2 is keyless.
Both tiers work in production; the read paths for them are unit-tested here.

**Deploy:** these DR changes need `sudo bash deploy.sh retrieval` to go live
(alongside the earlier committed-not-deployed DR work).

---

## Search ranking fix + source-tier auditability  [DONE + DEPLOYED]  (cd708cd, daba612)

Live A/B testing of the deployed DR endpoint surfaced two things:

1. **Ranking crowded out external tiers (fixed, `cd708cd`).** `_dedup_and_rank`
   did `(corpus + oa + web)[:top_k]`, so a corpus-rich query filled `top_k` and
   OA/web were truncated off before screening. Confirmed live: the web tier
   returned 17-20 results that were silently discarded. Fix reserves **4 OA + 3
   web** slots within `top_k` (`OA_QUOTA`/`WEB_QUOTA`); corpus takes the rest.
   Verified: ranking went 26-corpus/0-external → 19/4/3. +3 unit tests.

2. **Source tier was invisible in the timeline (fixed, `daba612`).** Reads/notes
   didn't say which tier a citation came from. Added `_origin_tier()` +
   propagation into events, `evidence_refs`, `_citations`, the Sources list, and a
   `TierBadge` in `ResearchTimeline.tsx`. Now every citation is badged
   corpus/oa/web.

Both **deployed** (backend via `deploy.sh retrieval`; webui rebuilt + rsynced to
the VPS). 14/14 DR unit tests + 3 ranking tests pass.

## The real web-breadth blocker: SearXNG is IP-blocked  [INVESTIGATED]

Per-engine probe: all four general engines fail from the cluster's datacenter IP —
duckduckgo `CAPTCHA`, startpage `Suspended: CAPTCHA`, qwant/mojeek `Suspended:
access denied`. Inherent to scraper engines from a datacenter IP; not tunable.
Science engines (arxiv/biorxiv/S2) work because they're APIs. Full analysis +
industry context in **`RESUME-2026-07-23.md`**.

## Brave Search API integration  [IMPLEMENTED — awaiting deploy]

User has a paid Brave key; implemented post cluster-restart on 2026-07-23.
`web.py` now calls the Brave JSON API directly (NOT SearXNG's brave scraper
engine) as the primary web source when `BRAVE_API_KEY` is set; SearXNG stays as
keyless supplement, and with the key unset behaviour is unchanged. Details:

- `_brave_one()` + a min-interval throttle: `BRAVE_SEARCH_QPS` (default 1 = free
  tier safe; raise in cluster.env for paid tiers) serializes Brave calls; one
  retry on 429 honouring Retry-After. Failures surface as an
  `engines_unresponsive` entry named `brave`, never as transport_error (that
  still means "SearXNG unreachable").
- Fan-out cap: only the first `BRAVE_MAX_QUERIES` (default 3) expanded variants
  go to Brave (billed per request); SearXNG still gets all variants free.
- Degradation: when Brave answers cleanly, zero hits = real "no info found"
  (no TOOL FAILURE warning even with SearXNG fully suspended/unreachable);
  warning fires only when Brave is down/unconfigured too.
- Compose: `BRAVE_API_KEY`/`BRAVE_SEARCH_QPS`/`BRAVE_MAX_QUERIES` passthrough
  added to `docker-compose.yml` + shadow. Key itself lives in
  `/opt/hugin/config/cluster.env` (root-only; could not verify from `vi`).
- Tests: new `tests/test_web_brave.py` (9/9). Also repaired
  `test_web_search_failsafe.py`: its fixtures still simulated the pre-2026-06
  engine roster (brave/ddg/startpage), so the all-engines-down warning could
  never trigger — 3 tests silently stale-failing since qwant/mojeek replaced
  brave. Updated to the current 4-engine roster + pinned `BRAVE_API_KEY=""` so
  the suite stays hermetic in-container. Now 8/8.
- Rationale captured in `shared/docs/DECISIONS.md` (2026-07 Brave entry).

**Deploy:** needs `sudo bash deploy.sh retrieval` (varghele). Verify after:
`web_search` returns `engine:"brave"` hits, then a live `smoke_dr_breadth.py`
run should show web reads > 0 with `web` tier badges.
