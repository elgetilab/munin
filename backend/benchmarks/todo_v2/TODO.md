# todo_v2 — open items

The specialised-agents architecture and the Deep Research breadth work (the whole
`done/` arc) shipped and deployed. What's genuinely left, in rough priority:

## 1. Re-measure the breadth gap now that the tiers are live  (highest value)
The capstone's honest verdict was Munin reads ~5 sources vs Claude's ~50, blamed
on the dead web tier + Semantic Scholar rate-limiting — **not** a design flaw.
Both are now fixed: the Brave Search API shipped as the primary web source
(`06687d7`, deployed + verified), and production has the S2 key.

**First-pass re-measure (2026-07-23) surfaced — and fixed — a blocker.** A full
production run confirmed Brave feeds the DR loop (web URLs now surface and get
read) but resolved 0/3 sub-questions with 0 notes: post cluster-restart, qwen3.6
returned `[]` from `source(mode=findings)` whenever thinking was disabled, so
every read abstained and reports came out empty. Fixed in `fc7b57b` (findings
mode now runs with thinking on, 8k tokens; tolerant array parse). Two web pages
also failed to fetch — same commit adds retry-with-backoff (recovers NCBI/PMC
burst rate-limiting) and reports `blocked` vs `empty` (MDPI is a hard
datacenter-IP wall, unfixable at the fetch layer).

**Re-measured post-deploy of `fc7b57b` (2026-07-23), same capstone question via
`POST /api/research/start`:**

| metric | pre-Brave | Brave live, thinking bug | post-fix (now) |
|---|---|---|---|
| grounded notes | 6 | 0 | **10** |
| sub-questions resolved | 2/3 | 0/3 | 1/3 |
| distinct papers cited | 5 | 0 | 3 |
| reads → not_found (abstained) | — | 21/21 | 14/18 |
| web (url) reads | 0 | 1 | 0 (this run) |
| report | populated | EMPTY | populated (9.3k, all 5 sections) |

**Grounding is fixed** (0 → 10 notes, empty → full report). **The breadth gap is
NOT closed** — but the per-read abstention that looked like the cause turned out
to be **correct, honest behaviour, not a defect** (investigated 2026-07-23):

- The read step discriminates correctly. The 3 papers that resolved are exactly
  the ones about kinase inhibitors × membranes ("Membrane lipid therapy",
  "VPS34-IN1 selective inhibitor", "Impact of Small-Molecule Kinase Inhibitors
  on Lipid Membranes"). The 14 abstentions are topically-adjacent papers (PKC
  enzymology, EGFR signalling, MD bilayer simulations, force fields) that don't
  address *small-molecule kinase inhibitors*.
- Reproduced: `source(findings)` on the PKC paper abstains 3/3 on the exact run
  question (about inhibitors) but resolves 5/5 on its real topic (PKC activity);
  same for the EGFR paper. Abstention is honest, not stochastic or over-strict.
- The real cause is UPSTREAM — **retrieval relevance + corpus depth**. For the
  hardest sub-question the corpus top hits were daunomycin (not a kinase
  inhibitor) and generic MD simulations; the OA/Semantic Scholar tier returned
  K-RAS, a GPCR Nobel lecture, virus entry, exosomes (semantically off). The
  single most on-topic paper came from the **web/Brave tier** — so Brave is
  already pulling its weight; the read pool just skews corpus-heavy (ranking
  reserves only 3 web slots).

## 1b. BLOCKER FOUND 2026-07-24: reserved tier slots are truncated at the READ stage

The retrieval work (OA keyword queries + relevance re-rank, see
`OA-RELEVANCE-PLAN.md`) demonstrably improved the candidate pool: OA median
relevance 0.604 -> 0.746, web sitting at 0.75-0.83 and holding the single most
on-topic paper. **None of it reached the Deep Research loop.** A DR run
immediately after deploying it produced 18/18 `not_found`, 0 notes, 0/3 resolved,
and read **16 corpus / 2 OA / 0 web**.

Cause, traced through the chain:

1. `_dedup_and_rank` returns `(corpus + reserved)[:top_k]`, i.e. **corpus first**,
   with the reserved OA/web slots appended at the TAIL (search_agent.py:311).
2. `_screen` preserves that order (`kept = [...][:keep]`, deep_research_agent.py:141).
3. `_read_candidates` iterates from the head and stops at `read_cap`
   (deep_research_agent.py:221-223).

So with `read_cap=6` over a corpus-first list, the read loop never reaches the
OA/web tail. The tier reservation added in `cd708cd` guarantees the external
tiers survive into `ranked`, and then the read stage truncates them away again.
It is the same crowding-out defect as the original one, reappearing one stage
later: fixed at the ranking layer, still present at the consumption layer.

This also explains why the run got WORSE than the previous one (0 notes vs 10):
whether a genuinely on-topic paper gets read is currently luck of the ordering.
The previous run happened to pull the bullseye paper in via a DOI in the OA tier;
this one did not.

**Nothing upstream of the read stage can fix this.** Better retrieval cannot help
while the read pool is chosen as top-N of a trust-ordered list.

Options for the fix (needs a decision):
- (a) Reserve READ slots per tier, mirroring the ranking reservation - e.g. of
  `read_cap`, guarantee 1-2 to non-corpus when available. Conservative, matches
  the existing reservation philosophy.
- (b) Order the read pool by `relevance` regardless of tier, now that one shared
  relevance axis exists. Reads are quote-grounded either way, so trust still
  governs citation weighting rather than what gets read. Simpler and uses the
  new signal, but drops "corpus first" at the read stage.

### Read-order fix verified (2026-07-24, `ea87e24` deployed)

The read pool is now ordered by relevance instead of tier. All three
pre-registered predictions held:

| | before fix | after fix |
|---|---|---|
| web reads | **0** (every run to date) | **3** |
| `not_found` rate | 100% | **74%** |
| notes | 0 | **8** |
| read tier mix | 16 corpus / 2 OA / 0 web | **4 corpus / 10 OA / 3 web** |
| report | empty | 7.7k, all 5 sections |

The mechanism is fixed: the external tiers are read for the first time, and the
read pool flipped from corpus-dominated to OA/web-dominated, which is exactly
what relevance ordering does when the corpus is thin on a topic.

**But the breadth number itself did not move.** Distinct papers cited is still
**3** (8 notes, concentrated by multi-note: 4 + 3 + 1), and the abstention rate
only went 78% -> 74%. So the read-selection bug was real and is fixed, yet it was
not what was capping breadth.

What this isolates: reads still abstain ~3 times in 4 even when reading the
highest-cosine candidates. Bi-encoder similarity gets papers that are
*topically near* but do not answer the specific question - the corpus scores
raft-biology papers at 0.78-0.80 on a kinase-inhibitor question purely on
vocabulary overlap. Cosine ranking cannot separate those from genuine hits.

**Next lever (unchanged by this fix): relevance PRECISION, not ordering.**
- A cross-encoder re-rank over the top-N candidates, which judges the (query,
  document) pair jointly instead of comparing two independent embeddings.
- Or a sharper screener prompt: it currently errs permissive by design (D8), so
  it passes vocabulary-overlap papers straight into the read budget.
- Corpus depth on the specific intersection remains a real, separate constraint.

### Full-text triage shipped and measured (2026-07-24, `bfe7e14`)

A cheap yes/no relevance call over the full text now gates the expensive
8000-token findings extraction. Validated before building against 16 papers with
known read outcomes (2/2 useful passed, 14/14 useless skipped), after metadata
re-ranking was ruled out (bi-encoder AUC 0.655, cross-encoder 0.616 - see
`CROSS-ENCODER-PLAN.md`). The signal that predicts a useful read lives in the
full text, not the title or abstract.

Live run, same capstone question and parameters as before:

| | without triage | with triage |
|---|---|---|
| reads | 19 | **23** |
| notes | 8 | **11** |
| distinct sources | 3 | **4** |
| sub-questions resolved | 1/3 | **2/3** |
| wall clock | ~220s | 285s |

Per-read timings confirm the mechanism: **8 of 20 reads were triaged out** at
~3.6s each (29s total) where a full extraction costs ~18s, so roughly **115s was
saved and reinvested** into more reads and more snowball (which fired harder
because more sub-questions resolved). The run is longer because it did more work,
not because triage is slow: a skipped read measured 2.5s against 22.9s for a full
extraction.

**False-negative check.** Two skips looked suspicious, notably "The interaction
of sorafenib and regorafenib with membranes is modulated by their lipid
composition" - on its title, a direct hit. Checked via `qa` (which bypasses the
gate): the paper was available at `read_depth: abstract` only, the abstract
carries no mechanistic content, and `qa` "resolved" it purely from model
knowledge with an EMPTY quote. `findings` requires a verbatim quote, so the skip
was correct. No false negative found, but this is the failure mode to keep
watching, and every skip is traced for exactly that reason.

**Remaining waste:** 8 reads passed triage and still yielded nothing, costing
79s. That is the recall bias working as designed (D8: when uncertain, read it).
Tightening the prompt would recover that time at the cost of real false
negatives, which is the wrong trade until breadth is no longer the priority.

### read_cap experiment: raising it made things WORSE (2026-07-24)

The obvious way to spend the triage saving was more reads. It backfires. Two
runs per condition, everything else identical:

| read_cap | reads | notes | distinct sources |
|---|---|---|---|
| 6 | 20, 15 | **11, 9** | 4, 3 |
| 12 | 29, 32 | **5, 5** | 3, 2 |

Notes ranges do not overlap (9-11 vs 5-5): doubling the read budget roughly
halved the findings, and distinct sources did not improve either. **Do not raise
`read_cap`.** 6 stays.

Mechanism not established. Ruled out: `_seen_keys` is per-node, so a paper
consumed by one sub-question is NOT blocked from a later one. Remaining
candidates are snowball crowd-out (at cap=12 the initial phase consumes nearly
the whole budget, and snowball reads are references of papers that already
answered, so they are the high-yield ones) and plain stochasticity in the
extraction, which runs at `temperature=0.7`.

**Methodological caveat that applies to this whole thread.** The pipeline is
stochastic end to end: decomposition wording differs run to run, retrieval is
non-deterministic, and the findings call samples at temperature 0.7. The first
read_cap comparison (n=1 each) showed notes 11 -> 5 and was confounded: the two
runs had different sub-questions. n=2 makes the effect look real, but n=2 is
still thin. Anything we want to actually rely on should go through the DR eval
harness with repeats, not ad-hoc single runs.

### Corpus coverage IS the ceiling: the Elgeti-corpus test (2026-07-24)

The Elgeti Lab corpus (group `elgeti`, 608 papers) is deep in one niche:
GPCR/rhodopsin 273 papers, conformational dynamics 165, EPR/DEER spin labeling
135. A question aimed at that niche, same code and same parameters as the
kinase-inhibitor question:

| | kinase-inhibitor Q (thin corpus) | Elgeti Q (deep corpus) |
|---|---|---|
| notes | 9-11 | **38** |
| distinct sources cited | 3-4 | **8** |
| sub-questions resolved | 2/3 | **3/3** |
| reads | 15-20 | 30 |
| report | ~7-9k chars | **17.5k chars** |

Roughly 3.5x the findings and 2x the sources from identical machinery. Corpus
relevance for that topic sits at 0.75-0.84 versus 0.65-0.70 for the kinase
question, and the citations are all on point (DEER Analysis of GPCR
Conformational Heterogeneity; Angiotensin Analogs with Divergent Bias Stabilize
Distinct Receptor Conformations; Probing the Y2 Receptor ... for EPR
Measurements).

**This settles the thread's central question.** Four real defects were found and
fixed (findings-mode thinking, S2 query shape, OA fame-ranking, read-stage
truncation) and together they moved distinct sources 3 -> 4. Pointing the same
system at a topic the corpus actually covers moved it to 8 immediately. Breadth
was bounded by corpus coverage, not by the retrieval or reading machinery.

Question text: `todo_v2/` scratch, reproduced in the session log. Kept for a
side-by-side against Claude, where the interesting axis is whether Munin surfaces
lab-specific work a general model has never seen.

**Real breadth levers (corrected):**
- Retrieval relevance, esp. the OA/Semantic Scholar tier returning off-topic
  papers for specific queries; the ranker/embedding doesn't distinguish "kinase
  inhibitor partitioning" from "anything about kinases/membranes".
- Corpus depth on under-covered intersections (kinase inhibitor × membrane).
- Give the web tier more read slots when the corpus is thin (it surfaced the
  best paper here but only gets 3 reserved slots).
- Do NOT "fix" abstention — honest quote-or-abstain is the design's core value;
  loosening it would trade trust for hollow breadth.
- Minor: grounding calls run at `temperature=0.7`; not the cause here (behaviour
  was consistent), but lowering it for extraction would cut incidental variance.

## 2. Source-agent eval gate: RUN AND PASSED (2026-07-24)

LitQA2 answer track, 199 questions, driven against the live chat research
persona. Apples-to-apples with the 2026-07-06 baseline: same question set, same
model (`qwen3.6-35b-a3b`), same encoder (bge-large), and the runner/scoring code
is **unchanged** since that scorecard (verified by git log on
`litqa2_runner.py`).

| | 2026-07-06 baseline | 2026-07-24 |
|---|---|---|
| accuracy | 0.497 (CI 0.432-0.563) | **0.814 (CI 0.759-0.869)** |
| precision | 0.853 (n=116 answered) | **0.926 (n=175 answered)** |
| questions actually answered | 116/199 (42% withheld) | **175/199 (12% withheld)** |
| verdicts | - | 162 correct, 13 incorrect, 13 abstain, 11 unparseable |

The confidence intervals do not overlap (baseline tops out at 0.563, the new
result starts at 0.759).

**This is the architecture's own predicted ceiling, hit.** The original Track D
diagnosis found that `read_paper`'s summarise-and-discard was the bottleneck and
that a full-text oracle flipped over-abstentions to **0.82**. The `source` agent
was built to deliver that oracle in production. Measured: **0.814**. The
over-abstention it was designed to fix has collapsed from 42% withheld to 12%.

For reference, PaperQA2 on this benchmark is 0.66.

**The 11 "unparseable" verdicts were a harness artefact, not a parser bug**
(`f4558c4`). Every one was a wall-clock deadline truncation, and truncation was
perfectly predictive: no truncated response ever parsed. Six had produced only
whitespace, i.e. the agent was still inside its tool loop at the 300s cut-off. A
`source` read now costs ~20s (full text, thinking on), so a research turn doing
several reads legitimately needs longer. Deadline raised 300s -> 900s, and
truncations are now reported separately from `unparseable` (lumping them hid a
harness limit as a model failure). `parse_letter` was deliberately NOT touched -
there was no evidence it was broken, and loosening a scorer to catch non-answers
would inflate the metric rather than fix it.

Re-running only those 11 with the raised deadline: **0/11 still truncated**, so
900s is sufficient. Verdicts 6 correct / 3 abstain / 2 incorrect, giving a
projected **0.844** (168/199) versus 0.814 measured.

*Use 0.814 for the baseline comparison.* It was produced under the same harness
as the 0.497 baseline; the 0.844 projection had a budget the baseline never got.
A clean full re-run under the 900s deadline is what should replace it.

### Clean full re-run under the 900s deadline (2026-07-24)

Committed scorecard `2026-07-24_answer-full-900s`. All 199 questions, same
budget each, **0 truncated, 0 unparseable**.

| | 300s run | 900s run |
|---|---|---|
| accuracy | 0.814 (0.759-0.869) | **0.864 (0.819-0.910)** |
| precision | 0.926 (n=175) | 0.920 (n=187) |
| correct / incorrect / abstain | 162 / 13 / 13 | 172 / 15 / 12 |
| unparseable (truncations) | 11 | **0** |

0.864 is the canonical post-fix number, and it matched the +0.03 projection
(0.844) within noise rather than the +0.05 I first guessed. Precision essentially
unchanged, so the gain came from converting truncations into answers, not from
answering more loosely.

**Honest churn note.** 34/199 verdicts changed between the two runs, and it is
NOT just the 11 recovered truncations: 6 correct->incorrect and 6
incorrect->correct flipped purely from temperature-0.7 resampling. Net movement
is real (the CI floor rose from 0.759 to 0.819), but any single-question or
sub-3-point comparison in this benchmark is inside the noise floor. This is the
same stochasticity caveat that applied to the DR hand-runs; only aggregate,
repeated measurements should be trusted.

**Headline for the session:** LitQA2 answer accuracy 0.497 -> 0.864, precision
0.85 -> 0.92, over-abstention 42% -> ~6% withheld, versus PaperQA2's 0.66. The
`source` agent's full-text reading is doing the work the architecture predicted.

**Attribution, honestly:** this spans 2026-07-06 to 2026-07-24, which includes
the entire four-agent architecture landing, not just the recent retrieval work.
The dominant cause is almost certainly `source(mode=qa)` reading FULL text
instead of `read_paper` summarising and discarding it. The retrieval fixes from
2026-07-24 are in the number but cannot be separated out by this measurement.
The answer track drives the CHAT research persona, so it does not exercise the
Deep Research read-ordering or triage work at all.

## 2b. (superseded) Confirm the Source-agent eval gate ran
`done/AGENT-IMPLEMENTATION-PLAN.md` Part 5 defines the whole justification for the
architecture: re-run the 20-question over-abstention set through `source(mode=qa)`
and compare abstain→correct against the 0.82 full-text oracle. Confirm this ran
against the landed `source.py` (not just the design); if not, run it.

## 3. R5 — Recommendations section in DR reports  (optional, small)
The one open item from `DR-VS-CLAUDE-COMPARISON.md`. A final synthesis pass over
confirmed findings producing actionable recommendations, each tied to a cited
finding. Prompt/template change in `deep_research_agent.py`; explicitly
lower-priority and gated on R1-R3 (all done).

## 4. Retire the folded-in tools  (cleanup with behavioral impact)
Design-v2 had `source` fold in `read_paper` + `compare_papers`, and `compute`
fold in `run_python` + `calculate`. The agents landed, but all the originals are
still registered side-by-side in `mcp/dispatchers.py`. Decide whether to
deprecate the originals and update the model's tool roster + system prompts.
Behavioral change — do it deliberately, with an eval before/after.

---

## Still-current reference (kept at top level)
- `DR-VS-CLAUDE-COMPARISON.md` — report-format target; R1-R4 done, R5 open.
- `MUNIN-DR-SAMPLE-REPORT.md` — sample DR output for side-by-side comparison.

## History
`done/` holds the archived design + planning + session docs (all executed):
design-v2, the implementation plan + 30-decision log, the v1 roster, the
autonomous session log, and the 2026-07-23 resume handoff.

## Deploy reminders
- Backend (retrieval): `sudo bash deploy.sh retrieval` on hugin (root; varghele).
- Frontend (webui): `npm run build` in `frontend/webui`, then rsync
  `frontend/static/chat/` to `varghele@<vps-host>:~/munin/frontend/static/chat/`
  (key `~/.ssh/munin_admin`). **Served path is `~/munin/frontend/static/`, NOT
  `~/munin/static/`** (the stale pre-merge tree — deploying there is a silent
  no-op).
