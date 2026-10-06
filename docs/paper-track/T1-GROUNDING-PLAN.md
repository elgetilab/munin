# T1 grounding edit plan

> **Historical (2026-07).** T1a was deployed and measured: null for grounding, so the T1b/T1c track was stopped (outcome section below).

Status: PLAN 2026-07-09. Lift answer grounding (Track B % claims supported ~0.35)
by making retrieved evidence richer + more citable. Refocused after `329c150`
already fixed context OVERFLOW - T1 is now about grounding QUALITY, not 400s.

## Deploy/measure constraint

T1 is retrieval CODE (papers.py, tool_result.py). Needs `deploy.sh retrieval`
(varghele), which now bundles `329c150` + T3 + T1 as one retrieval deploy. Measure
via the Track B agentic arm (re-capture + GPU score) vs the 0.356 baseline
(pre-T2 deep-default; the current live baseline once research 1.5 redeploys).
Benchmark-only otherwise; no self-deploy.

## Verified facts

- `papers_bge` payload CARRIES `abstract` (~500 chars) - `paper_search` just does
  not surface it (`mcp/tools/papers.py` ~221 builds row = title/doi/year/authors/
  score/matched_query, NO text).
- `truncate_tool_result` (`tool_result.py`, limit 8000) Tier 2 drops WHOLE
  trailing list items (keeps earlier ones full); Tier 3 wraps a too-big single
  item as a preview. So bigger per-item rows (with excerpts) => fewer papers fit.
- `329c150` added the pending-result overflow degrade in chat_context - leave it.

## T1a - paper_search excerpt  [LOW risk, high leverage] - IMPLEMENT NOW

Add a short abstract excerpt to each `paper_search` result row so the model can
(1) ground a claim on real text and (2) judge relevance BEFORE a `read_paper`
(cuts speculative reads -> also helps over-tooling). The abstract is already in
the payload; just surface a truncated snippet. Update the tool description to say
results include a short excerpt. Default excerpt 280 chars.
- Tradeoff: excerpts enlarge results, so Tier 2 truncation drops more papers at
  the 8000-char budget. Mitigate by keeping the excerpt short (280) and, if
  needed, trimming default `top_k` a little. Measure net grounding effect.

## T1b - per-item budgeting in truncate_tool_result  [MED risk] - PLAN, needs go

Instead of DROPPING trailing papers, SHRINK each item's long text fields
(abstract/excerpt/snippet) so more papers survive with a shorter excerpt each.
Touches the shared truncater used by ALL tools and interacts with `329c150`'s
chat_context degrade - coordinate. Gate: no regression in other tools' results.

## T1c - structured sub-agent / deep_research returns  [MED-HIGH risk] - PLAN, needs go

`deep_research` / `invoke_agent` return a large prose/string blob truncated as
one unit (`agents/executor.py` ~268), so the main model can't cite per-paper.
Return structured `{papers:[{id, title, doi, excerpt}], ...}` so the main loop
unpacks + per-item-truncates and can attribute each claim. Biggest change; do
after T1a/T1b show the excerpt approach lifts grounding.

## Sequence

1. **T1a now** (safe; abstract is already there) -> commit (staged, deploy bundled
   with T3/329c150).
2. On the next retrieval deploy: re-run the Track B arm; compare grounding vs
   0.356 and over-tooling vs the deep-default baseline.
3. If T1a helps but truncation is dropping papers -> T1b. Then T1c for the
   sub-agent path. Each gated on a Track B re-measure.

## T1a OUTCOME (2026-07-09): NULL for grounding -> STOP the T1b/T1c evidence track

Deployed T1a + measured (paired 40-q, both deep-default + extract). Grounding
**0.356 -> 0.303** (CIs overlap; flat-to-down), over-tooling flat (median 10.5 ->
13.5), contexts 85.6 -> 100.0. Adding evidence did NOT move grounding, so the
~35% gap is NOT an evidence-availability problem - it is model synthesis beyond
retrieved text + MiniCheck literalness (faithful cross-source synthesis scores as
"unsupported"). Therefore **T1b/T1c (also evidence-availability fixes) are
unlikely to help and are DE-PRIORITISED.** Keep T1a (harmless relevance-judging
feature). The grounding number needs a correctness-aware judge (Track C/D), not
more retrieval plumbing. Scorecard `2026-07-09_faithfulness-agentic-live-t1a`.

## Open questions

1. Excerpt length: 280 chars default OK, or longer (more grounding text, fewer
   papers fit)?
2. Proceed to T1b/T1c after T1a measures, or batch all three before a single
   deploy? (Recommend: ship T1a first, measure, then decide - cheaper attribution.)
