# Tool retirement plan (benchmarks TODO #4)

Status: DONE 2026-07-25. read_paper + compare_papers retired; calculate +
run_python kept per decision A. Committed AND deployed to prod (retrieval).
Routing before/after run live (scorecards 2026-07-25_toolretire-{before,
after}): retirement behaviorally safe (no control regression); source now
handles read/compare intents (known_doi_read source-required 0.8->1.0,
compare_known_dois 0.2->0.4); overall 0.788->0.835 is temperature-0.7 noise
on untouched tools (CIs overlap). Two pre-existing routing quirks surfaced
(S2 over-tooling on known DOIs; compare-mode under-selection) — logged in
TODO #4 as follow-ups, not caused by this change. Commits: 978f9ce, 09980fd,
be6bf7c, 20a62ef (before), a144561 (after). LitQA2 skipped (retired tools
not on the answer path).

## What the code actually shows (vs the TODO premise)

The TODO frames this as "`source` folded in `read_paper` + `compare_papers`,
`compute` folded in `run_python` + `calculate`; retire all four originals."
Only the `source` half is real in the code:

- **`read_paper` + `compare_papers` — genuinely folded.** `source`
  (`mcp/tools/source.py`) imports their *internal helpers*
  (`_summarise_narrative`, `_summarise_key_findings`, PDF fetch/cache from
  `read_paper.py:234,395`; `_build_comparison_prompt`, `HARD_MAX_PAPERS`
  from `compare_papers.py:721`). Both tools are **already dropped from
  `CORE_TOOLS`** (`schemas.py:19-43`) to tool_search-only, with a comment
  saying so "during transition". Neither is named in any persona prompt.
  Retiring them finishes a transition the codebase already started.
- **`calculate` — NOT folded.** `compute_agent.py` never imports or calls
  it (grep: zero refs). It is in `CORE_TOOLS` and named in all three
  persona prompts (`chat/code/research.json`). Retiring it is a real
  capability decision, not a cleanup — see the scoping decision below.
- **`run_python` — NOT folded; load-bearing.** `compute` *uses* it as its
  sandbox executor, and it is depended on by `latex.py`, `vision.py`,
  `chat_service.py` Stage-C, `executor.py`, `hooks/audit_log.py`,
  `artifact_store.py`. The core comment explicitly keeps it. **Out of
  scope — do not touch.**

Consistency gate to respect: `mcp/_dispatch.py:70 verify_dispatch_registry()`
cross-checks the dispatcher registry against `MCP_TOOLS` at boot and raises
on drift. Schema entry + dispatcher must be removed together or the service
won't start (`tests/test_dispatch_registry.py` enforces this).

## Recommended scope

**Retire `read_paper` and `compare_papers` as tools. Keep `calculate` and
`run_python`.** This is the part that matches what was actually folded, is
low-risk (both are already non-core / tool_search-only, unmentioned in
prompts), and closes the documented transition. Because they are already
demoted, the behavioral delta is small: the model can no longer *discover*
them via tool_search, so any residual direct use shifts to `source`. The
before/after eval is therefore a **regression guard** (confirm no accuracy
loss), not an improvement demo.

### Scoping decision (needs your call): `calculate`

The TODO says compute folded it in; the code says it never did. Options:

- **(A) Keep `calculate` [recommended].** It is a cheap single-shot
  sympy-backed primitive for exact/symbolic/unit arithmetic. The only
  "replacements" are heavier: `run_python` (spins a sandbox kernel) or
  `compute` (spins a vLLM loop). Retiring it downgrades a common, cheap
  intent to a slow path and needs three persona-prompt rewrites for no
  architectural gain. The premise was simply wrong here.
- **(B) Retire `calculate` too.** Only if you want the roster to match the
  design-v2 doc literally. Then arithmetic must be routed to `run_python`/
  `compute` in the persona prompts, and we accept the cost/latency
  downgrade. More work, real capability regression, weaker eval story.

Default applied if you don't override: **(A) keep calculate.**

## Implementation (scope = retire read_paper + compare_papers)

All paths under `backend/retrieval/`.

1. **Baseline eval (before any code change).** Capture the before side while
   the tools still exist (see Eval section). Commit the scorecards first so
   the diff is honest.
2. **Remove the tool surface, keeping the module files** (source needs their
   helpers):
   - `mcp/schemas.py`: delete the `read_paper` entry (176-193) and
     `compare_papers` entry (194-217) from `MCP_TOOLS`. (Neither is in
     `CORE_TOOLS`, so no core edit.)
   - `mcp/dispatchers.py`: delete the `@register_tool("read_paper")` shim
     (103-108) and its impl import (line 43); the
     `@register_tool("compare_papers")` shim (111-117) and import (line 23).
   - `mcp/tools/__init__.py`: drop the `read_paper` exports (26, 61) and
     `compare_papers` exports (27, 62).
   - **Keep** `mcp/tools/read_paper.py` and `compare_papers.py` — source
     imports helpers from both.
3. **Prune the now-orphaned top-level functions (optional, separable).**
   With the dispatchers gone, `read_paper()` (`read_paper.py:224`) and
   `compare_papers()` (`compare_papers.py:146`) have no callers except
   compare_papers→read_paper internally. Either leave them as dead-but-
   harmless, or delete both top-level fns and the chain at
   `compare_papers.py:187`, keeping every helper source imports. Recommend
   deleting for cleanliness, in a clearly separate commit so a revert is
   trivial.
4. **Scrub name references in schema descriptions** so the model isn't told
   to call a tool that no longer exists:
   - `paper_search` desc (71): "...before calling read_paper" → "...before
     calling source".
   - `semantic_scholar_search` desc (94): "use read_paper" → "use source
     (mode=summary)".
   - `faq` desc (995): "deep_research / paper_search / web_search /
     read_paper" → drop read_paper.
   - `tool_search` desc (1016): the example list "read_paper, run_python,
     create_artifact, calculate" → replace read_paper with a live tool.
   - **Keep** the `source` desc mentions (220, 232) — they correctly tell
     the model source supersedes read_paper/compare_papers, which stays
     useful for a release or two, then can be softened later.
5. **Tests.** Update the two that name these tools:
   - `tests/test_tool_search.py`, `tests/test_tool_validation.py` — drop
     read_paper/compare_papers from expected-tool assertions.
   - Re-run `tests/test_dispatch_registry.py` (the tri-consistency gate)
     and `tests/test_config_schemas.py` — should pass once schema +
     dispatcher are removed together.
6. **Docs.** Update `shared/docs/BACKEND-API.md` §9 (MCP tool registry) if it
   enumerates these; note the retirement in the benchmarks TODO.

No persona-prompt edits are needed for the recommended scope (read_paper/
compare_papers appear in none). If decision (B) is taken, add: rewrite
`chat.json`, `code.json`, `research.json` (via
`shared/personas/_build_munin_personas.py`) to reroute arithmetic, and drop
`calculate` from `CORE_TOOLS` + schema + dispatcher + `__init__`.

## Eval before/after

Two harnesses, per the eval-harness map. Both hit the live chat/vLLM stack,
so run on the cluster.

1. **Primary — routing eval** (`munin_bench/routing/run.py`). Directly
   measures tool selection. Run before and after, same seed set, reps for
   flip-rate:
   ```
   python -m munin_bench.routing.run --tag before-toolretire --reps 8 --seed 42
   # (apply change, redeploy) then:
   python -m munin_bench.routing.run --tag after-toolretire  --reps 8 --seed 42
   ```
   Expect: zero read_paper/compare_papers fires after (they already fire
   rarely, being non-core); `source` pass rates and `soft_max_calls`
   unchanged; `mean_flip_rate` flat. NB the seed set has no positive
   read_paper/compare_papers items (`routing_eval.py:380-660`) — consider
   adding one or two "read this DOI" / "compare these papers" items so the
   eval actively proves the intent now routes to `source`. Small addition,
   worth it for the before/after to mean something.
2. **Regression guard — LitQA2 answer track** at the 900s deadline (matches
   baseline `scorecards/2026-07-24_answer-full-900s.json`):
   ```
   python -m munin_bench.pipelines.run_all --tag before-toolretire \
     --tracks litqa2-answer
   # after the change:
   python -m munin_bench.pipelines.run_all --tag after-toolretire \
     --tracks litqa2-answer
   python -m munin_bench.pipelines.compare \
     scorecards/<before>.json scorecards/<after>.json
   ```
   Expect accuracy flat within noise (temperature-0.7 churn makes sub-3-pt
   moves meaningless — treat as pass/fail on "no significant regression",
   not on the point estimate).

DR eval (`eval_dr.py`) is optional and directional only; the answer track
doesn't exercise the DR loop, but `source` is on the DR read path, so a
single-shot DR run before/after is a cheap sanity check if time allows.

## Rollout

1. Commit baseline scorecards (before).
2. Land the code change (steps 2-6) in one commit, top-level-fn pruning in a
   second.
3. `sudo ./deploy.sh retrieval` on hugin.
4. Run the after evals, `compare`, commit the after scorecards.
5. If routing shows the intent rerouted to `source` and LitQA2 is flat,
   done. Update benchmarks TODO #4 to resolved and archive this plan.

## Risks

- **Low, by construction.** The two retired tools are already non-core and
  prompt-unmentioned; source already carries their logic in production.
- Main failure mode: a stray internal caller of the top-level `read_paper()`
  we missed. Mitigation: keep the module + helpers; grep for
  `read_paper(`/`compare_papers(` call-sites before deleting the top-level
  fns (step 3), and the dispatch-registry gate fails loudly at boot if a
  schema/dispatcher pair drifts.
- If decision (B): real capability regression on cheap arithmetic; do the
  persona rewrites carefully and lean on the routing eval to catch
  mis-routing.

## Open questions

1. **calculate: keep (A, default/recommended) or retire (B)?**
2. Prune the orphaned top-level `read_paper()`/`compare_papers()` functions
   (recommended, separate commit) or leave them as dead code?
3. Add positive read/compare items to the routing seed set so the
   before/after actively proves rerouting (recommended, small)?
