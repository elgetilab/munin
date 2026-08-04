# A2 implementation plan — harden CORE_TOOLS + tool_search

**STATUS: CLOSED 2026-06-23.** Prefill verified (no hang cliff; 64K context
limit). Gate met on the permissive bar under the post-allowlist preview
(`_eval_full`): citing_papers 15/16, export_bibtex 12/16 via tool_search.
Matcher fix + prompt cue shipped; moderate completion (~0.3-0.6) documented
as an A3/A5 residual. Deploy-applied. See "A2 CLOSED" findings section. A4 is
unblocked (after A3 in build order).

**Goal (IMPLEMENTATION-HANDOFF §A2).** Make the deferred-tool mechanism
(`CORE_TOOLS` + `tool_search`) robust enough to REPLACE the persona
allowlists. Two parts: (1) verify with `repro_vllm_hang.py` that the
resident schema stays comfortably under the prefill cliff in every mode,
including mid-turn `tool_search` unlocks; (2) get the routing-eval gate
items green via the tool_search path.

**Gate (blocks A4):** routing-eval `citing_papers` and `export_bibtex` green
via `via_tool_search_ok`. Do NOT start A4 (retire allowlists) until green.

**Dependency rule (must not invert):** harden tool_search FIRST (A2), retire
allowlists SECOND (A4). A2 proves tool_search carries the load before the
allowlists that currently back it are removed.

**Status:** plan for review. No code until varghele signs off (plan-first).

---

## 1. How the mechanism works (verified 2026-06-22)

- **Schema build** (`chat_service.py:_openai_tools_schema`): visible tools =
  `(CORE_TOOLS | current_unlocked_tools) & persona_universe`. Only these
  ship in the vLLM `tools` array. The allowlist remains the *authorization*
  boundary (enforced in `_run_tool_calls`); the schema only controls
  *visibility*.
- **tool_search** (`mcp/tools/tool_search.py`): keyword-scores the query
  against the persona's non-core tools (name match = 3, description match =
  1), returns up to 8 schemas, and adds them to `current_unlocked_tools` so
  they appear in the schema on the **next iteration**. Discovery is one hop:
  iteration N calls tool_search, iteration N+1 calls the unlocked tool. Both
  iterations are inside the SAME user turn (same SSE stream), so the A0
  harness captures the whole sequence.
- **CORE_TOOLS** (`mcp/schemas.py`, 11 tools): paper_search, web_search,
  read_paper, run_python, create_artifact, calculate, ask_clarification,
  delegate_to_persona, tool_search, set_plan, update_plan_item.

## 2. Current state (A0 baseline + static measurement)

**Schema token sizes** (4-chars/token rule, measured 2026-06-22):

| Schema | ~tokens |
|---|---|
| CORE (11 tools) | 4,956 |
| CORE + 8 unlocked (worst-case biggest) | 9,237 |
| FULL registry (42 tools) | 13,824 |

So the schema is NEVER the cliff driver by itself. It matters only as one
component of total prefill (schema + persona system prompt [chat ~3.75K,
research ~4.2K tokens] + accumulated history). Deferring tools buys ~9K
headroom (FULL 14K → CORE 5K) for long conversations, which is the point.

**Cliff-number discrepancy to resolve:** the handoff says ~22.5K, the
`chat_service.py` comment says ~40K. `repro_vllm_hang.py` probes sizes
`5,15,30,45` K and is the empirical authority — A2 resolves this by
measuring, not by trusting either constant.

**Gate items at A0 (N=8, chat persona):**
- `citing_papers` (get_citations via tool_search): **8/8, flip 0.00 —
  already green.**
- `export_bibtex` (export_citations via tool_search): **6/8, flip 0.29 —
  needs hardening.**

**tool_search matchability (verified, not the bottleneck):** export_citations
scores 1 on "bibtex", 9 on "export citations bibtex"; get_citations scores 4
on "citations citing". The tools ARE discoverable; the 2/8 export_bibtex
failures are turns where the model neither called tool_search NOR the tool —
it tried to answer without reaching for discovery. **A2's reliability problem
is REACH, not matching.**

---

## 3. Sub-goal 1 — prefill-cliff safety verification (measurement)

Per the handoff, this must be DONE, not assumed from the static math (the
cliff is an empirical vLLM property; the static sizes only predict headroom).

**Procedure** (read-only against live vLLM; runnable on hugin):

```bash
python backend/scripts/repro_vllm_hang.py --sizes 5,15,30,45 --reps 3
```

**No extension needed (see §6 Q3):** `fetch_tools_schema` already pulls the
full 42-tool registry (~13.5K tokens) from the live MCP `tools/list`, so the
default run already carries a strict UPPER BOUND on every unlock state
(CORE 5.0K ⊆ CORE+8 9.2K ⊆ full 13.5K). The **mid-turn-unlock mode is
covered by a subset argument**: if the full schema is safe at conversation
size X, every `(CORE ∪ unlocked)` state is safe at X. repro is in fact
conservative — production sends CORE most turns, a smaller schema than repro
probes.

**Expected result:** comfortable headroom in every mode (static math
predicts the schema never exceeds ~14K even fully unlocked). Deliverables:
(a) the measured cliff location, resolving the 22.5K-vs-40K discrepancy in
the A2 record; (b) a one-line note that mid-turn unlock is subset-bounded by
the full-schema probe, plus the prefix-cache-invalidation perf nuance (the
tools array grows on iteration 2, busting the cache; perf, not cliff). If the
cliff is surprisingly low and even the full schema gets close, that is a
finding that constrains how many tools tool_search may unlock at once
(tighten `_MAX_RESULTS`).

---

## 4. Sub-goal 2 — tool_search REACH reliability (the gate)

Get `export_bibtex` to green (citing_papers already is), reliably, via the
iterative measured loop (the human+Claude loop from
`todo/eval-prompt-pipeline.md`: measure → bounded edit → re-measure → keep
only if it improves without regressing). Levers, cheapest/most-reversible
first:

1. **Prompt / guidance hardening (try first).** Make the persona prompt and
   the tool-absence signal clearer that, when a needed capability is not in
   the visible schema, the move is `tool_search`, not "answer without it".
   Two bounded sub-levers:
   a. The persona system prompt's tool-usage section (a bounded edit, not a
      wholesale rewrite — Risk 2 from the eval-prompt-pipeline: narrow
      surface).
   b. The CORE tool descriptions could carry a one-line "if you need a
      capability you don't see, call tool_search" cue. (Note: after A4 the
      allowlist-reject nudge is gone, so the prompt is the durable place for
      this guidance.)
2. **CORE_TOOLS promotion — OFF the table for the gate tools (decided, §6
   Q1).** Real usage shows export_citations / get_citations at ~1 invocation
   each in 2 months; promoting them would tax every turn to fix near-unused
   tools. Promotion stays a reserved lever (per-tool sign-off) but no gate
   tool qualifies. SEPARATE candidate, not part of the gate: `deep_research`
   (42 uses) has a genuine frequency case for CORE — weigh on its own merits.
   If prompt guidance can't make the gate green, that limitation is itself the
   finding to surface before A4 (not a reason to promote a rare tool).
3. **tool_search matching quality (lower priority).** `_score` is crude
   substring matching; it already surfaces the gate tools, so only revisit
   if a needed tool proves undiscoverable.
4. **Same-turn unlock (deferred / flag only).** Currently unlock is
   next-iteration. Surfacing an unlocked tool in the SAME iteration would
   remove the hop where the model goes off-track, but it is a larger harness
   change. Flag as an option; do not build unless 1-2 fail.

**The loop:** re-run the routing eval (the two gate items at N=8, plus the
other deferred-tool items `remember_research_area`, `known_doi_read` as
canaries) after each bounded edit; keep the edit only if the gate items
improve and nothing else regresses vs the A0 baseline. Stop when both gate
items are green and stable (low flip).

---

## 5. The gate + a scorer-permissiveness note

The handoff flags it: `score_item`'s `via_tool_search_ok` branch passes if
`tool_search` appears in the trajectory at all, even if the unlocked tool
never fires (it may surface on a later user turn the single-turn capture
misses). So **"green" under the current scorer = "the model reached for
tool_search"**, not "the model completed the export in-turn".

- **For the A2 gate: accept the permissive definition** (per the handoff —
  "Acceptable for now").
- **But also report the stricter number** alongside: did the actual tool
  (export_citations / get_citations) fire in the captured turn? The A0
  trajectory already contains this (both iterations are in one SSE stream),
  so it is a free second metric — `reached_tool_search` vs
  `completed_in_turn`. This tells us the REAL reliability without changing
  the gate, and flags early if the permissive branch is masking failures
  (the handoff's "tighten if it masks real failures").

---

## 6. Open questions (needs varghele)

1. **Lever order / scope. RESOLVED (2026-06-22): prompt-first for the gate;
   no CORE promotion of the gate tools.** Approved the cheapest-first order
   (prompt guidance → CORE promotion → matching → same-turn). Decision rests
   on real usage data (2mo of chat logs):

   | Tool | Real invocations | CORE cost/turn |
   |---|---|---|
   | `deep_research` | 42 | 408 tok |
   | `remember` | 3 | 333 tok |
   | `get_citations` (gate) | 1 | 158 tok |
   | `export_citations` (gate) | 1 | 240 tok |
   | `s2_get_citations` | 1 | 437 tok |

   The two gate tools are invoked ~once each in production, so promoting them
   would tax 100% of turns to fix tools used in <0.1% of turns — a bad trade
   and exactly the case the deferred-tool model exists for. **A2 fixes the
   gate via bounded prompt guidance only.** CORE promotion stays a reserved
   lever requiring per-tool sign-off; on this data no gate tool qualifies. If
   prompt guidance CANNOT get `export_bibtex` reliably green, that is the
   interesting finding (the deferred mechanism can't surface rare-but-real
   tools) to bring to varghele before A4 removes the allowlist backstop.

   **Separate follow-up (NOT an A2 gate item): `deep_research` → CORE
   promotion candidate.** It is by far the most-used deferred tool (42 vs
   ~1-3 for the rest); the per-turn cost (~408 tok) has a real frequency
   justification the gate tools lack. Weigh on its own merits, outside A2.
   Logged here so it is not lost.
2. **Persona-prompt edits. RESOLVED (2026-06-22): Option A — apply bounded
   diffs directly to `shared/personas/*.json`; varghele reviews via `git diff`
   + before/after eval numbers and gates the deploy.** Rationale: the
   tool_search guidance ALREADY EXISTS in all three personas, but buried at
   the very end of a ~15K-char prompt (chat char 14967/15186; main "TOOL
   USAGE STRATEGY" section is much earlier at char 4548). So the first,
   most-bounded edit is to RELOCATE / duplicate that cue up into the main
   tool-usage section (optionally with one concrete example, e.g.
   "formatting or exporting citations → tool_search"), not to rewrite
   anything. Bounded-edit discipline (narrow surface, measured, reversible)
   per eval-prompt-pipeline Risk 2. Edits target the single shared prompt
   (the portable default); the per-model `default` + `by_model` mechanism is
   Part B infra and stays out of A2. The dedicated "tunable guidance block"
   (Option C) is the long-term home if prompt-tuning grows beyond A2 —
   logged, not built now.
3. **repro_vllm_hang.py `--unlocked N` extension. RESOLVED (2026-06-22): no
   extension — run as-is.** The check that settled it: `repro_vllm_hang.py`'s
   `fetch_tools_schema` pulls from the live MCP `tools/list`, which returns
   ALL 42 tools (~13.5K tokens), so the repro tool already sends the FULL
   schema by default. That is a strict upper bound on every unlock state
   (CORE 5.0K ⊆ CORE+8 9.2K ⊆ full 13.5K), so the mid-turn-unlock case is
   covered by a subset argument, not a synthetic flag: if the full schema is
   safe at conversation size X, every `(CORE ∪ unlocked)` state is safe at X.
   It is also conservative — production sends CORE most turns, so repro tests
   a HARDER schema than production ever hits. Nuance: within one user turn
   each vLLM iteration is a separate request with its own prefill, so there
   is no "schema mutates mid-stream" failure mode beyond "iteration 2's
   prompt is bigger" (already subset-bounded); the only real effect is
   prefix-cache invalidation when the tools array grows (a PERFORMANCE
   observation, not a cliff risk — note it, don't gate on it).
4. **Scorer stricter metric. RESOLVED (2026-06-22): add `completed_in_turn`
   as a non-gating diagnostic.** The permissive branch
   (`routing_eval.py:267-271`) is `via_ok = n > 0 or True` — a tautology, so
   a `via_tool_search_ok` requirement passes the moment `tool_search` appears,
   even if the actual tool never fired. `completed_in_turn` is the value of
   `via_ok` BEFORE that override (line 266: `count_ok and pred_ok`) — free to
   compute, the data is already in the trajectory.

   **Mechanism (preserves the gate AND A0 comparability):** the current
   scorer does `passed = all(checks.values())`, so the metric must NOT go in
   `checks` (that would gate it). Add a separate non-gating `diagnostics`
   field to `ItemResult`; put `completed_in_turn` there (computed for
   `via_tool_search_ok` requirements); the scorecard reports it beside, but
   separate from, the gating checks. `passed` is untouched, so the A0
   baseline pass rates stay directly comparable.

   **Value:** if e.g. `export_bibtex` reads 8/8 on the gate but 3/8
   completed_in_turn, the model reaches for tool_search but doesn't follow
   through in-turn — a real gap the permissive gate hides (the handoff's
   "tighten if it masks real failures" signal). Implemented WHEN A2 is built,
   not now.

## 7. Acceptance for A2

- [ ] `repro_vllm_hang.py` run; cliff location measured; 22.5K-vs-40K
      resolved; mid-turn-unlock mode confirmed under the cliff.
- [ ] `export_bibtex` green and stable (low flip) via tool_search; reach
      hardening edits bounded and measured, no regression vs A0 on other
      items.
- [ ] `citing_papers` still green (regression check).
- [ ] `completed_in_turn` non-gating diagnostic added (separate
      `diagnostics` field on `ItemResult`) and reported alongside the gate;
      `passed` unchanged so A0 stays comparable.
- [ ] A2 record notes which levers were used (prompt edits / CORE changes)
      and the before/after gate numbers.
- [ ] Gate confirmed green → A4 is unblocked (but A3 comes first in build
      order).

## Findings (build log)

### Prefill verification (2026-06-22) — the hang cliff does not reproduce

Ran `repro_vllm_hang.py` with the full 42-tool schema (the worst-case upper
bound on any unlock state, §6 Q3) across actual prompt sizes ~2.4K → ~50K
tokens, 2 reps each.

| ~actual prompt tokens | result |
|---|---|
| 2.4K, 7.6K, 12.5K, 17.3K, 22.5K | healthy: TTFT 0.84→2.8s (linear), max_gap 0.0-0.1s, decode 500-720 c/s, all completed |
| ~30K, ~40K, ~50K | clean **HTTP 400 "maximum context"** (no hang, no stall, immediate) |

**Conclusions:**
1. **No hang reproduced.** The mid-decode stall that motivated
   `repro_vllm_hang.py` (2026-04) and the deferred-tool model does not
   manifest in the current vLLM. `max_gap` never exceeded 0.1s (the hang
   signature is >30s).
2. **The operative hard limit is `max_model_len = 65536` (64K)**, enforced
   as a graceful 400 (prompt + `max_tokens` reserve must fit). BOTH prior
   "cliff" estimates are stale: handoff's ~22.5K and the `chat_service.py`
   comment's ~40K described a hang that no longer occurs. The 22.5K-vs-40K
   discrepancy is resolved by invalidating both.
3. **The deferred-tool model's value is now a CONTEXT-BUDGET argument, not
   hang-avoidance:** keeping the schema at CORE (~5K) vs full (~13.5K) buys
   ~8.5K tokens of conversation headroom under the 64K cap. Still real, just
   differently motivated.
4. **A2 prefill-safety sub-goal is satisfied:** the resident schema (CORE 5K,
   full 13.5K, any unlock state a subset) is trivially clear of the 64K cap;
   there is no cliff to stay under, only the context budget, of which the
   schema is a small bounded part.

**Conservative caveat:** the historical hang was intermittent; 16 shots with
no reproduction is strong but not absolute evidence it is permanently fixed.
Framed as "no hang reproduced; operative limit is the 64K context cap",
not "the hang is gone forever".

**Follow-up (separate, non-blocking):** the `chat_service.py:222-224`
"~40K-token hang cliff" comment and the deferred-tool rationale should be
updated to the context-budget framing. Logged, not changed mid-A2.

### Reach reliability (2026-06-23) — the diagnostic exposed a tool_search MATCHER bug

The `completed_in_turn` diagnostic (Q4) immediately paid off. A2-before
scorecard (N=8, current deployed prompts):

| deferred item | gate | completed_in_turn |
|---|---|---|
| citing_papers (get_citations) | 4/8 | **0.00** |
| export_bibtex (export_citations) | 4/8 | **0.00** |
| known_doi_read (read_paper) | 8/8 | **0.00** |
| remember_research_area (remember) | 0/8 | **0.00** |
| reroute (run_python, a CORE tool) | 1/8 | 0.62 |

**The actual deferred tool NEVER completes in-turn (0.00); CORE tools do
(run_python 0.62).** The permissive gate's "green" was hollow.

**Root cause (verified by live probes + deterministic analysis), NOT what the
plan assumed:** it is a `tool_search` MATCHER-QUALITY bug, not (only) a reach
problem.

- Live probe of `citing_papers`: the model sent GOOD queries ("find papers
  that cite a DOI", "list papers citing a specific DOI, citation graph"), but
  `get_citations` was NOT in the unlocked matches — so the model could not
  call it and improvised with paper_lookup / semantic_scholar_search /
  web_search.
- Why: `_score` (tool_search.py) rewards generic-term and STOPWORD matches.
  For "find papers that cite a DOI", get_citations ranks only **#5** (score 7),
  behind compare_papers (10), check_papers_availability (8),
  get_author_papers (8), s2_get_citations (8) — all winning on
  "papers"/"find"/"doi"/"a"/"that". Sitting at rank 5 against the
  `_MAX_RESULTS=8` cap, it tips OUT under small phrasing variance → unstable,
  mostly-failing unlock → completed_in_turn ≈ 0.
- A second failure mode also exists (export_bibtex probe): sometimes the model
  does not call tool_search AT ALL and improvises with web_search/run_python.
  That is the reach problem the prompt edit targets — but it is SECONDARY to
  the matcher bug, because even when the model DOES call tool_search, the
  matcher fails to surface the right tool.

**A2 lever re-prioritization (data overruled the plan, per "code wins"):**
1. **PRIMARY (new): fix the `tool_search` matcher** — stopword filtering,
   weight name-matches far above description-matches, and/or guarantee the
   top-scored tool isn't crowded out by the cap. Backend code change
   (`mcp/tools/tool_search.py`), testable OFFLINE (pure logic;
   `test_tool_search.py` exists), deployable independently of prompts.
2. SECONDARY: the prompt reach cue (Q1/Q2) — still useful for the
   "doesn't call tool_search at all" mode, but it cannot help until the
   matcher reliably returns the right tool.

This is exactly the handoff's warning ("tighten if it masks real failures")
and the reason A2 must precede A4: tool_search demonstrably does NOT yet carry
the load.

### Matcher fix implemented (2026-06-23) — offline-validated, deploy-gated

Decisions (AskUserQuestion): **targeted + guaranteed top-k**; **offline +
live re-measure**.

Implemented in `backend/retrieval/mcp/tools/tool_search.py`:
- **IDF-weighted token scoring.** Replaced the +3/+1 substring scorer with
  token matching weighted by inverse document frequency over the tool
  corpus, so generic terms ("papers", "search", "doi") and stopwords ("a",
  "that", "find") no longer inflate tangential tools; rare specific terms
  (cite, bibtex, remember) dominate. Name-match weighted 3x over
  description-match.
- **Stopword filtering** (English + generic domain terms).
- **`_MAX_RESULTS` 8 -> 10** (guaranteed top-k headroom; prefill is far under
  the 64K limit so the cost is negligible).

**Offline validation (the gate this had to clear before deploy):**
- `test_tool_search.py`: 12/12 pass (incl. a new
  `surfaces_gate_tools_natural_phrasing` regression and a fixed stale
  `caps_at_max`/`materially_smaller` bound that referenced an outdated CORE
  size).
- In the real chat-persona universe, the previously-crowded-out tools now
  rank inside the top-10: **get_citations #6, export_citations #1,
  remember #1** (all unlocked; before, get_citations was dropped by the cap).

**Deploy-gated next step (live re-measure):** deploy the tool_search change
(`deploy.sh`), then re-run the routing eval (`routing-A2-after-matcher`) and
confirm `completed_in_turn` for the deferred items rises from ~0.00. ONE
lever at a time (disciplined loop): the secondary prompt reach-cue (Q1/Q2) is
HELD until the matcher's live effect is measured — if the model still fails
to CALL tool_search at all (the export_bibtex improvise-instead mode), the
prompt edit is the next lever; if the matcher fix alone gets the gate green,
the prompt edit may be unnecessary.

**Adjacent fix made (noted):** `test_schema_materially_smaller_than_universe`
asserted a stale `schema_size <= 10`; CORE grew to 11 (plan-mode tools), so
it now tracks `len(CORE_TOOLS)`. Pre-existing staleness, not caused by A2.

### Post-allowlist gate preview (2026-06-23) — root cause was allowlist scoping, not just the matcher

Deploying the matcher fix and re-probing live exposed the REAL blocker, which
the flawed offline validation had hidden:

- **The chat persona's deployed allowlist (27 tools) contains NO citation
  tools** (get_citations, export_citations, s2_* all absent). `tool_search`
  only surfaces tools within the persona allowlist, so under chat these tools
  are unreachable BY CONSTRUCTION — `completed_in_turn = 0.00` was the
  allowlist blocking them, not matcher ranking. (research allowlist = 35
  tools, HAS them; code = 17, none.)
- **The offline matcher validation was flawed:** with `PERSONAS_DIR` unset
  locally, `get_persona('chat')` returned None → tool_allowlist None → it
  tested the FULL 42-tool registry, not chat's real 27. Corrected: the
  matcher fix re-validated against the real research universe (get_citations
  #6, export_citations #1) AND confirmed live under research
  (`tool_search → get_citations` actually fires).
- **This reframes the A2 gate:** the handoff calls these items "allowlist-
  retirement acceptance tests" — their point is the POST-A4 world (no
  allowlist). Under the current chat persona they cannot pass until A4
  retires the allowlist.

**Decision (varghele, AskUserQuestion): preview the post-allowlist end-state.**
Measure the gate over the FULL tool universe (no allowlist), the hardest
matcher case, using the chat prompt (the entry persona).

**Built (offline-validated, deploy-gated):**
- `shared/personas/_eval_full.json` — INTERNAL fixture: chat system prompt +
  sampling, NO `tool_allowlist` (→ full 42-tool universe everywhere). Loads +
  validates; `_`-prefix marks it internal.
- `personas.py::public_personas()` hides `_`-prefixed personas from the
  user-facing `/api/personas` selector (clean lasting convention). Verified
  the selector stays [chat, code, research].
- `routing/run.py` + `run_item`: `--persona` override (default chat;
  `_eval_full` for the preview) and `--items` subset flag. NOT sending
  expected.profile (hard rule #3 intact); a deliberate test-config persona.
  Header records the override. Benchmark tests green (15/15).

**Deploy-gated next step:** deploy the fixture + filter, then run the gate
items under `_eval_full` and confirm `completed_in_turn` rises from 0.00 over
the full universe. See deploy steps in the session notes.

### Gate preview RESULT (2026-06-23) — matcher fix works; follow-through is the remaining gap

`routing-A2-after-preview` (N=8, `_eval_full` full universe) vs A2-before
(chat). completed_in_turn:

| item (tool) | before (chat) | after (_eval_full) | permissive gate |
|---|---|---|---|
| citing_papers (get_citations) | 0.00 | 0.25 | 8/8 |
| export_bibtex (export_citations) | 0.00 | 0.38 | 5/8 |
| known_doi_read (read_paper) | 0.00 | 1.00 | 7/8 (forbidden s2 fired) |
| remember (remember) | 0.00 | 0.12 | 1/8 |

**Proven:** the matcher fix works end-to-end — every deferred tool went from
unreachable (0.00, allowlist-blocked) to reachable+sometimes-completed over
the full universe. Post-allowlist discovery is viable (de-risks A4).

**Remaining gap:** follow-through. The model reaches tool_search and the tool
unlocks, but often does NOT call it in-turn (improvises/stops). Permissive
gate green only for citing_papers (8/8); export_bibtex 5/8, completion rates
low (0.12-0.38). Caveats: N=8, wide CIs; known_doi_read's miss is a separate
forbidden-tool issue (read_paper itself completes 1.00).

**=> The held SECONDARY lever (prompt edit, Q1/Q2) is now data-justified.**
Apply a bounded persona-prompt edit: relocate the buried tool_search cue into
the main TOOL USAGE STRATEGY section AND add a follow-through cue ("after
tool_search unlocks a tool, call it directly rather than improvising"). Then
re-measure under _eval_full; keep only if completion improves without
regressing the green items.

### A2 CLOSED (2026-06-23) — permissive gate met; prompt edit kept

Prompt-cue re-measure under `_eval_full` (full universe). N=8 was optimistic;
N=16 (clean, after a vLLM job cutover invalidated the first N=16) is the
firm number:

| item | permissive gate (N=16) | completed_in_turn (N=16) |
|---|---|---|
| citing_papers (get_citations) | 15/16 (0.94) | 0.31 |
| export_bibtex (export_citations) | 12/16 (0.75) | 0.62 |
| known_doi_read (read_paper) | 16/16 | 1.00 |
| remember (remember) | 3/16 (0.19) | 0.19 |

**Decision (varghele): close A2 on the permissive gate.** The handoff's bar
("citing_papers and export_bibtex green via tool_search, via_tool_search_ok")
is MET: the model reliably REACHES tool_search for the deferred tools over
the full universe — the allowlist-retirement acceptance test passes, so
tool_search can replace allowlists for discovery (de-risks A4).

**Honest residual (documented, not hidden):** actual in-turn COMPLETION of
the deferred tool is moderate (get_citations ~0.31, export_citations ~0.62) —
the model reaches tool_search reliably but fires the unlocked tool only
~1/3-2/3 of the time. The `completed_in_turn` diagnostic (built for exactly
this) keeps the claim honest. This follow-through reliability is a residual
for A3 (router) / A5 (paraphrase density), not a blocker for the A2 gate.

**Levers, final:**
- Matcher fix (IDF + stopwords + name-weight + cap 10): made the tools
  REACHABLE (0.00 -> reachable). Essential, validated, committed.
- Prompt cue (follow-through instruction in all 3 personas): modest real
  gain (export_bibtex completion 0.38 -> 0.62; gate 5/8 -> 12/16), NO
  regression of green items (citing_papers held 0.94). KEEP.
- `remember` is a routing-RECOGNITION gap (model doesn't see "going forward
  I work on X" as memory-worthy, so never tool_searches) — different from
  follow-through; flag for A3/A5.

**Harness robustness follow-up (non-blocking):** vLLM runs as a SLURM job
that can cut over (job 666 -> 677) mid-eval, which wiped the first N=16 run
(all "peer closed connection"). Consider a pre-flight job-stability check or
per-item retry on dropped SSE before long eval runs. Logged for the eval
harness, not fixed in A2.

## 8. Risks / notes

- **Goodhart (eval-prompt-pipeline Risk 1):** hardening to the two gate items
  could over-fit. Mitigation: keep the canary items (remember, known_doi_read)
  and the full A0 set in the regression check; do not optimize prompts to the
  gate in isolation.
- **A2 vs A1 ordering:** A1's soak runs in parallel; A2 is harness-
  independent of the delegation flag (tool_search has nothing to do with
  delegation). Safe to build A2 during the A1 soak.
- **The reach problem may be largely behavioral (model + prompt), not
  structural.** If prompt + a CORE promotion can't get export_bibtex
  reliably green, that is itself a finding about the deferred-tool model's
  limits to bring to varghele BEFORE A4 removes the allowlist backstop.
