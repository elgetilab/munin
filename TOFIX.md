# TOFIX

Deferred work, written down rather than done, so a consolidation pass can
start from a known state. Each item says what was observed, what the evidence
is, and what the proposed change is. Nothing here is implemented.

Opened 2026-11-13 after the primer-design feature landed in `main`
(`cdb265f`). Background and full context:
[`docs/agent-track/PRIMER-DESIGN-PLAN.md`](docs/agent-track/PRIMER-DESIGN-PLAN.md).

---

## 1. The model almost never calls `design_primers`  (highest value)

**Status: the feature is merged, correct, tested, and effectively unused.**

Measured live on 2026-11-13 against `sage-a1.5`, via an isolated sidecar
running the production image with the primer code mounted read-only
(`backend/retrieval/evals/run_eval.py`, scenarios `primer_pcr`,
`primer_cloning`, `primer_nosequence`, 3 runs each):

| scenario | called `design_primers` | notes |
|---|---|---|
| `primer_pcr` | **0/3** | reinvented it in the sandbox: 5, 13 and 7 `run_python` calls |
| `primer_cloning` | **0/3** | see item 2, it produced nothing at all |
| `primer_nosequence` | 0/3 | correctly refused to invent, but see item 3 |

Across every live observation including an earlier instrumented probe, the
tool was called in roughly **1 run out of 13**.

Cost of reinventing it: cumulative turn prompt tokens of **114,472 / 408,136 /
169,939** on the three PCR runs, for a task the tool answers in ONE call whose
envelope is ~750 tokens.

**Discovery is not the problem, and this was checked.** `tool_search` returns
`design_primers` for every phrasing tried ("design PCR primers", "primer
design", "PCR", "amplify a DNA sequence", "cloning primers Gibson"), and the
capabilities block in the system prompt already advertises it:

> `design_primers`: Design PCR, cloning or sequencing primers against a DNA
> sequence, and return them already verified.

The model simply never looks: **no `tool_search` call in any PCR or cloning
run**. Its default for a computational task is the sandbox, and a specialist
tool one lookup away does not change that.

### Proposed change

1. **Add `design_primers` to `CORE_TOOLS`** in `backend/retrieval/mcp/schemas.py`.
   This was deliberately decided against when the tool was built, on the
   grounds that it is a specialist tool and `CORE_TOOLS` is kept small (~9 of
   39) to keep prefill clear of the hang cliff. That reasoning was sound on
   the information available; the live numbers change it. The cost is ~600
   prefill tokens per turn for every user. The thing it prevents is
   400,000-token reinvention turns.
2. **Steer the general-purpose tools away.** Add to the descriptions of
   `run_python` and `compute` that primer and oligo design is not their job
   and that `design_primers` does it with verified output. This mirrors the
   established pattern in this repo: `source`'s description states it
   supersedes `read_paper` and `compare_papers`, and `search` states it
   consolidates the three search tools.
3. **Re-measure with the same three scenarios** before concluding anything.
   The whole point of layer 4 is that offline reasoning about this was wrong
   twice already.

**Do not treat 1 and 2 as obviously correct.** They are the hypothesis the
evidence supports, not a verified fix.

---

## 2. `primer_cloning` produces nothing, and it is not understood

All three runs made **zero tool calls**, and 2 of 3 returned **empty assistant
content** after ~170 s of thinking (prompt tokens 21,820 / 14,917 / 21,820).
That is a different failure from item 1, where the model at least did
something.

Unknowns worth checking before changing anything:

- Is the prompt simply too hard, so the model gives up?
- Does it interact with the **per-profile thinking-token budget** merged in
  `8b607cc`? The sidecar used for these runs was branched BEFORE that merge,
  so it did not have it. Re-run on current `main` first; the behaviour may
  differ or may be explained by it.
- Is content being produced and lost somewhere between the stream and the
  persisted conversation?

---

## 3. The one passing scenario passes while behaving pathologically

`primer_nosequence` scores 3/3 on every check, including the one that matters
most (it does NOT invent a primer sequence from memory, which is the failure
mode documented in the Cureus and CRISPR-GPT studies).

But it gets there with **30 to 31 tool calls** and **1,024,472 / 1,045,692 /
523,842** cumulative prompt tokens per turn, mostly `web_fetch` x14-17 and
`web_search` x10-13.

**This is a gap in the eval, not a success.** `_primer_nosequence_checks()` in
`backend/retrieval/evals/run_eval.py` has no tool-count or prompt-token
budget, unlike `_primer_checks()`, which has both. Add them, at which point
this scenario will correctly start failing.

---

## 4. Research-profile turns run hot, independently of primers

Observed cumulative prompt tokens per single turn on `sage-a1.5`: 174k, 291k,
327k, 408k, and over 1M on the search-heavy scenario. Flagged to the Sage
eval-suite session (`munin-36`), which records prompt tokens per question and
was going to check its agentic arm.

Not a primer finding. Recorded here because it was measured here.

---

## 5. Known weaknesses in the primer engine itself

Both are documented in the plan and neither blocks use, but both matter before
anyone relies on this for real bench work or a paper claim.

- **`primer/structure.py` underestimates duplex stability**, mean offset +1.55
  to +2.14 kcal/mol against `ntthal`, from an unverified hairpin loop constant
  (`HAIRPIN_LOOP_DG37_REF`) and the `MAX_INTERNAL_LOOP = 4` cap. Because
  `rules.py` treats dimer and hairpin checks as HARD, this errs toward
  **passing primers that should fail**, which is the unsafe direction.
  Structure-based rejections are trustworthy; structure-based acceptances are
  weaker than they look. The real fix is a verified internal-loop energy
  table.
- **`primer/tails.py` ignores reading frame.** A restriction tail for an N- or
  C-terminal fusion can land out of frame with the vector, and the vector is
  not currently an input to the design.

---

## 6. Housekeeping

- **Stale branches.** Many `*-scrubbed` duplicates, `backup/*` refs and merged
  feature branches remain (`git branch --list`). Several are checked out in
  other worktrees, so this needs care and should not be done blind.
- **`varghele/primer-design` is redundant**, fully absorbed into `main` by
  `cdb265f`. `backup/primer-pre-rebase` likewise. Both can go.
- **Frontend has two test surfaces.** `npm test` (vitest) does NOT run
  `e2e/widgets.spec.ts` (Playwright). A behaviour change needs both; a naming
  change in this feature passed vitest and broke e2e, fixed after the fact in
  `3c3685d`.
