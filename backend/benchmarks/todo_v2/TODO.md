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

**Still to do once `fc7b57b` is deployed:** re-run and record the actual breadth
numbers.
- Harness: `backend/retrieval/tests/smoke_dr_breadth.py` (in-process; needs
  `SEMANTIC_SCHOLAR_API_KEY` + `BRAVE_API_KEY` exported), or drive the live
  container via `POST /api/research/start` (has both keys already).
- Expected signal: notes > 0 again, `web (url) reads > 0`, more total reads,
  web-tagged citations.

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
