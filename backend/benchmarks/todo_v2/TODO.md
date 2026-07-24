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

**Next:** reads are now materially cheaper, so raising `read_cap` (currently 6
per sub-question in these measurements) is the natural way to convert the saving
into breadth. That is the experiment to run next.

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

## 2. Confirm the Source-agent eval gate ran  (validation debt)
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
