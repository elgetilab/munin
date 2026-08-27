# Making `search` the primary research tool

Status: PLAN, not implemented. Drafted 2026-08-27 from the iLOV drop
reproducer. Sibling: [`STREAM-RELIABILITY-TODO.md`](STREAM-RELIABILITY-TODO.md)
(items 5 and 6 are the ones this closes).

Baseline to beat, measured on `evals/run_eval.py --scenario lov`:
**26 tool calls, 637,058 turn prompt tokens, 562s**, for one factual question.

---

## 1. What is actually wrong

Three separate causes, all confirmed against the stored tool results of the
failing production turn (conversation `aed5210c`, 2026-08-27).

**The model has four overlapping discovery tools in front of it.**
`CORE_TOOLS` contains `search`, **`paper_search`** and **`web_search`**, and the
research persona's `resident_tools` add **`semantic_scholar_search`**,
`web_fetch` and `paper_lookup`. `search` exists to consolidate the first three
(its own description says "Supersedes paper_search + semantic_scholar_search +
web_search"), yet all of them stay visible. The observed turn used
`search` x1, `paper_search` x1, `semantic_scholar_search` x1, `web_search` x8,
`web_fetch` x8, `source` x7.

**`search` cannot answer a value question.** `grep -c "source(" search_agent.py`
is **0**. It finds and ranks; it never reads. For "what is the extinction
coefficient of iLOV at 280nm" the number is in the body of a paper, not in an
abstract, so `search` structurally cannot answer it and the model must
orchestrate reads by hand. That hand-orchestration IS the overtooling.

**Tier fan-out is parallel and ignores corpus scoping.** `search` fires
corpus + OA + web concurrently through one `asyncio.gather`, whatever the query
and whatever the attached tags. There is no "look in the corpus first, branch
out only if it comes up short". Worse, `tags` are passed to `paper_search`
ONLY, so the OA and web tiers are unscoped by construction: attaching a
knowledge base does not constrain them at all. What exists is presentation-level
(`_dedup_and_rank` keeps corpus first for trust, with relevance-gated reserved
slots for OA/web), which is easy to mistake for tier priority but is not it.

## 2. Change A: staged tiers, corpus first

Replace the single parallel gather with two stages.

1. **Stage 1, corpus.** Run `paper_search(queries=variants, tags=tags)` alone.
2. **Sufficiency test.** Count corpus hits whose `relevance` clears
   `OA_RELEVANCE_FLOOR`. Call that `n_strong`.
3. **Stage 2, external, only if needed.** Branch to OA and web when
   `n_strong < CORPUS_SUFFICIENT_MIN`.
4. When the user attached tags **explicitly** (a slash-command corpus, not an
   inherited default), require a higher bar before branching, and say so in
   `coverage_note`. The user has told us where to look; going straight to the
   open web contradicts that.

Details that must not regress:

- `coverage_note` (D18) already runs a second, UNSCOPED `paper_search`
  (`search_agent.py:447`) to report "X in scope, Y consortium-wide". That call
  is part of stage 1 and its result feeds the sufficiency test for the
  branch-out decision, so a scoped miss that the consortium can answer branches
  to the wider corpus BEFORE it branches to the web.
- `depth="deep"` still gates the web tier, and egress still gates both external
  tiers. Staging narrows *when* they run; it does not widen *whether* they may.
- Latency: on a corpus miss this adds one round trip that used to be
  concurrent. Acceptable: the miss path was already the slow path, and the hit
  path (the common one when a corpus is attached) gets faster.

New constant, env-overridable like the floors:
`CORPUS_SUFFICIENT_MIN` (default 3), and `CORPUS_SUFFICIENT_MIN_TAGGED`
(default 1, i.e. any strong in-scope hit suppresses the web tier).

## 3. Change B: opt-in read

Add `read: int = 0` to `search`. When `read > 0`, after ranking, take the top
`read` scholarly hits and call `source(refs=[ref], mode="qa", question=query)`
for each, returning:

```
answers: [{ref, answer, abstained, source_type}]
```

alongside `ranked`.

**Opt-in, not automatic, and the reason is measured.**
`deep_research_agent.py:287` calls `search` once per sub-question. Reading by
default would multiply every Deep Research run by N `source` calls. `search` is
also the breadth tool for pure discovery, where reading is waste.

**Bounded.** Cap `read` at 3. `source`'s single-ref modes read ONE ref
(`source.py:654`, "summary/qa/extract operate on the first ref for v1"), so
`read=3` is three `source` calls, each its own vLLM call. Three is already the
cost of the hand-rolled loop we are replacing; more would recreate the problem
inside the tool.

**Corpus-first here too.** Prefer corpus hits for the read slots, since those
are full-text local PDFs; fall back to OA hits with an `open_access_pdf`.

The tool description must state plainly: *for a specific value, number or
property, pass `read=2`*. That is the sentence that stops the model
hand-rolling `source` loops.

## 4. Change C: make `search` the only discovery entry point

Remove from `CORE_TOOLS`: **`paper_search`**, **`web_search`**.
Remove from the research persona's `resident_tools`:
**`semantic_scholar_search`**.

They stay reachable through `tool_search`, so nothing is walled off (the hard
`tool_allowlist` was retired in A4b and this does not reintroduce it). Internal
callers are unaffected: `search`, `source` and `research.py` call these as
Python functions, not as tools.

Two consequences to handle deliberately:

- **`tool_search` becomes the escape hatch, and the model will use it.** In the
  observed turn it already called `tool_search` to find `search_user_docs`.
  Mitigation: `search`'s description must name the intents it now owns, and
  `search_user_docs` should arguably become resident on research, since
  "look in my uploaded documents" is a distinct intent `search` does not cover.
- **`web_fetch` stays resident.** Reading a specific known URL is a real,
  distinct intent, and `source` handles refs rather than arbitrary URLs.

Schema-token cost is a wash: three tools out, `search` grows two parameters.

## 5. Risks

- **Corpus-first hides good external hits.** If the corpus has three
  mediocre-but-above-floor hits, we skip OA/web entirely. Mitigated by the
  relevance floor doing the gating rather than a raw count, and by
  `thin_evidence` (now relevance-aware) still telling the model when the
  evidence is weak so it can re-ask with `depth="deep"`.

  **For an EXPLICIT tag this is not a risk, it is the point** (operator
  decision, 2026-08-27): a user who types `/elgeti` has stated where the answer
  should be. If it is not there, that absence is itself the finding and must be
  REPORTED, not papered over by silently substituting open-web results. So on
  an explicit-tag miss `search` says so in `coverage_note` and returns the thin
  result rather than quietly widening. Widening stays available to the model as
  a deliberate second call.
- **`read` becomes the new overtooling.** A model that sets `read=3` on every
  search triples cost. Watch mean `source` calls per turn in the reproducer;
  if it climbs, gate `read` behind the query looking like a value lookup.
- **Removing core tools changes routing-eval behaviour.** The routing eval
  asserts on tool choice in places; expect churn and re-baseline rather than
  assume a regression.
- **DR regression.** Deep Research is the heaviest `search` caller. Staging
  changes what it gets back per sub-question. Its breadth metric is the one to
  re-check, not just the reproducer.

## 6. Test plan

Unit (no network, run in-container):

- staged fan-out: corpus-sufficient -> OA/web NOT called (patch and assert);
  corpus-thin -> both called; `depth="normal"` -> web never called;
  egress=off -> external tiers never called.
- tagged-corpus bar: with explicit tags, one strong hit suppresses branch-out;
  with inherited tags, it does not.
- `read=0` returns no `answers` key; `read=2` calls `source` twice with
  `mode="qa"`; `read` is clamped to 3; abstained reads are reported, not
  dropped.
- corpus hits are preferred for read slots over OA.

Behavioural (the reproducer, advisory):

- `evals/run_eval.py --scenario lov --runs 3`, comparing against the recorded
  baseline of 26 tools / 637,058 tokens / 562s. Add checks for
  `source` calls <= 3 and `paper_search`/`web_search`/`semantic_scholar_search`
  not appearing at all.

Regression:

- `test_search_expansion`, `test_dispatch_registry`, `test_config_schemas`,
  `test_search_agent_hygiene`, plus the routing eval re-baselined.

## 7. Order of work

1. Change C first (visibility). It is the smallest edit and on its own should
   cut the tool count; measuring it alone tells us how much of the overtooling
   is pure tool-menu confusion.
2. Change A (staged tiers). Measure again.
3. Change B (opt-in read). Measure again.

Doing them in this order attributes the improvement instead of landing three
changes and guessing which worked.
