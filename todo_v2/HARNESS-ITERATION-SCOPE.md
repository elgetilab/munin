# Harness iteration - scope

Status: SCOPING 2026-07-09. What to improve in the agentic harness BEFORE Track
C/D (they characterise the harness, so they need it stable). Grounded in a
3-agent code sweep (loop/sub-agents, tool definitions, persona fragments) +
verified against the code and our own eval evidence. This is a scoping doc:
themes, leverage/risk, sequencing, open questions - not an implementation.

## Method: iterate against a measurement dashboard

Harness changes must be measured, or we churn blind (the A5 clarify lesson:
"further prompt churn is high-risk/low-yield"). The dashboard already exists:

- **Routing anchor eval** (0.950 now) - regression gate; deploy -> measure.
- **Track B faithfulness** (% claims supported 0.378) - grounding signal; re-run
  the 40-q agentic arm (cheap now: ~9 min GPU scoring on a saved capture).
- **Over-tooling count** (`soft_max_calls` diagnostic; live 28-32 seen) - the
  call-density signal.
- **Live soak** (reported chats + chats.db) - real-world backstop.

Cadence: persona/fragment changes ship via `deploy.sh personas` + restart
(cheap); code changes via `deploy.sh retrieval`. Measure each on the above.

## Evidence base (what we know hurts)

- **~40% abstention** even with good retrieval (encoder migration).
- **~38% of answer claims grounded** (Track B) - lots of un-retrieved synthesis.
- **28-32 tool calls** on one query, occasional vLLM 400 (A5 + soak).
- **clarify brittle ~0.56** (A5) - inherent ask-vs-act ambiguity, known-hard.

---

## Themes (verified findings, leverage x risk)

### T1. Retrieved evidence is thin + truncated -> the faithfulness root  [HIGH / MED]

> UPDATE 2026-07-09: `329c150` (varghele) already added the context-OVERFLOW
> mitigation - the "degrade the largest pending tool results instead of
> overflowing" last-resort in `chat_context.py`. So T1 should NO LONGER target
> overflow/400s (done); it targets GROUNDING QUALITY: excerpts on results,
> per-item truncation, structured sub-agent returns. Re-read `329c150`'s
> chat_context/chat_service changes before starting T1.

Mechanism (verified):
- `paper_search` returns only `{title, doi, score, matched_by}` - **no abstract
  or matched excerpt** (`mcp/tools/papers.py` ~221). The model cannot see WHY a
  paper matched, so it either over-calls `read_paper` or synthesises around the
  metadata.
- Every tool result is truncated to **8000 chars** (`tool_result.py:35
  TOOL_RESULT_CHAR_LIMIT`); a big `deep_research` / sub-agent result becomes one
  ~8K blob with `_truncated: true` and the model grounds on the preview,
  fabricating the dropped remainder.
- Sub-agent (`invoke_agent`) / `deep_research` returns are a single prose string
  (`agents/executor.py` ~268), truncated as one unit rather than per-item.
- Context accumulates many 8K results; the pending batch is protected but has a
  last-resort largest-first degrade (`chat_context.py:270-288`).

Evidence: Track B 0.378 grounding; vLLM 400 context-overflows.
Directions (scope): (a) add a short **matched excerpt / abstract snippet** to
`paper_search` results (lets the model ground claims AND judge relevance ->
fewer speculative reads); (b) **per-item** truncation for list results, not one
blob; (c) **structured** sub-agent / deep_research returns (`papers:[{id,
snippet}]`) so the main loop can cite each; (d) compact-then-keep stale results
instead of dropping. Measure: Track B faithfulness delta.

### T2. Over-tooling is PROMPTED, not just emergent - and unbounded  [HIGH / LOW-MED]

Mechanism (verified in `shared/personas/research.json`): the fragment makes
`deep_research depth=deep` (5 sub-qs x 4 variants x 3 search tools ~= 60 API
calls) the **default opening move**, THEN prescribes follow-up chains
(`paper_lookup -> get_citations -> get_references`, `get_author_papers`, ...),
THEN states "**Ten or more tool calls in a single conversation is normal and
expected. You are not optimising for call count.**" No per-turn tool-call cap in
the loop; `deep_research` fan-out has no cross-query dedup / early-exit
(`mcp/tools/research.py:37-54`).

Evidence: 28-32 calls/query, vLLM 400s.
Directions: (a) **resolve the fragment contradiction** - keep the depth default
but replace "10+ normal / not optimising for call count" with a bounded "after
deep_research, at most N targeted follow-ups, then synthesise" (prompt-only,
cheap, measure on `soft_max_calls`); (b) `deep_research`: dedup queries +
early-exit when coverage saturates + cap total fan-out; (c) a soft per-turn
tool-call cap as a loop backstop. Coordinate with A5 (flagged over-tooling as
pending). Measure: over-tooling count + routing eval (no regression).

### T3. Tool DEFINITIONS don't front-load scope -> wrong-tool + weak grounding  [MED-HIGH / LOW]

Mechanism (verified in `mcp/schemas.py`): descriptions don't lead with scope, so
`paper_search` (local) vs `semantic_scholar_search` (external 200M) vs
`search_user_docs` (user uploads) vs `web_search` are ambiguous; `get_citations`
vs `s2_get_citations` near-collide (scope buried mid-description). `tool_search`
(`mcp/tools/tool_search.py`) has **no stemming** ("cite" != "citations"), so
inflected natural queries match weakly.

Directions: front-load scope in descriptions ("LOCAL curated corpus (fast)...",
"EXTERNAL Semantic Scholar (200M, slower)...", "ONLY your uploaded docs...",
"FROM SEMANTIC SCHOLAR:" on `s2_*`); add lemmatisation to the `tool_search`
matcher. Cheap, safe (schemas.py, no behaviour gate). CAVEAT: the A5
local-first-then-branch logic lives in the FRAGMENT today; moving cues into
descriptions could trim fragment bloat but risks double-steering - test, do not
duplicate. Measure: routing eval + over-tooling.

### T4. Abstention/grounding is push-pull - COORDINATE with Track C, don't tune blind  [HIGH / MEASUREMENT-GATED]

Mechanism (verified): research fragment "NEVER answer from general knowledge
alone... Uncited claims are failures" + "(b) training (c) reasoning should be
rare" pushes abstention/grounding; chat fragment "distinguish tools/training/
reasoning" permits synthesis. No "thin-evidence -> say so" threshold. The ~40%
abstention AND ~38% synthesis coexist: the model both over-abstains and
ungroundedly synthesises depending on framing.

Direction: this IS Track C's measurement target (over-abstention vs correct
"not in corpus", confabulated-citation rate). Recommendation: do the T1-T3
grounding/tooling fixes first, then let **Track C quantify abstention
calibration and tune against it** - do not hard-tune abstention prompts blind
now (avoids the A5 blind-churn failure mode).

### T5. Loop / sub-agent robustness  [LOW / LOW] - batch as a cleanup pass

Empty-turn retries can consume turns; main-loop and sub-agent budgets are not
shared (nested calls can exceed a conversation-wide ceiling); wrap-up-synthesis
failure handling is thin. Real but lower-leverage; fold into a robustness pass.

---

## What NOT to do (reject / cross-ref our own evidence)

- **Do NOT shrink `resident_tools`.** A4b PROVED surfacing high-value tools fixed
  the deferred-tool follow-through (get_citations completed_in_turn 0.62 ->
  1.00). The sweep's "cut research resident_tools to 4" contradicts this.
- **Do NOT re-churn the clarification MECHANICAL RULE** for marginal gain (A5:
  high-risk/low-yield; 0.56 is inherent ask-vs-act ambiguity). Touch only if
  Track C / soak shows real harm. Ignore "probabilistic P>0.6" prompt
  suggestions - not mechanically meaningful to the model.
- **Verify file:line before editing.** The sweep was fast; e.g. it missed the
  pending-batch last-resort degrade. Trust the theme, re-check the anchor.

---

## Proposed sequencing (prompt-first: cheap -> measurable -> then code)

1. **T2 prompt fix** (research-fragment over-tooling contradiction) + **T3
   description front-loading + tool_search stemming**. Cheapest, mostly
   deploy-only; measure over-tooling count + routing anchor (no regression).
2. **T1 paper_search excerpt + per-item truncation**. Targets grounding; re-run
   the Track B agentic arm to measure the faithfulness delta.
3. **T1 structured sub-agent/deep_research returns + T2 deep_research fan-out
   throttle** (code). Measure grounding + over-tooling together.
4. **T4 abstention** - defer to Track C; tune against its numbers.
5. **T5 robustness** - cleanup pass.

## Decisions (2026-07-09)

1. **Start prompt-first (T2 + T3)** - the research-fragment over-tooling
   contradiction + tool-description scope front-loading. Lowest risk, mostly
   deploy-only, measured on over-tooling count + routing anchor eval.
2. **Sharpen Track B first - DONE.** Added deterministic claim extraction
   (`extract_claims`: drop narration/questions/headers). Dashboard baseline is
   now **% claims supported 0.356 [0.272, 0.425]** on 14.6 real claims/answer
   (`2026-07-09` scorecard). Key: the number barely moved from the raw-sentence
   0.378, so the grounding gap is ROBUST, not a narration artifact - a sound
   target to optimise against for the T1 grounding work.
3. **Defer T4 (abstention) to Track C** - tune against its numbers, not blind.

## Next step

Implement **T2 + T3** (prompt-first). T2: rewrite the research-fragment
over-tooling licence ("10+ normal / not optimising for call count" -> bounded
"after deep_research, <=N targeted follow-ups, then synthesise"). T3: front-load
scope in the retrieval-tool descriptions + `tool_search` stemming. Gate: routing
anchor 0.950 no-regress + over-tooling count drop; then re-run the Track B arm to
watch grounding. Plan the exact edits before touching prompts (plan-first).
