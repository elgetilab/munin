# Retrieval relevance plan: rank each tier on a relevance axis

Status: APPROVED 2026-07-24, implementing. Scope:
`backend/retrieval/mcp/tools/search_agent.py` (+ a prompt tweak in
`query_expansion.py`). Motivated by the 2026-07-23 breadth investigation (see
`TODO.md` item 1): the DR loop wastes reads on off-topic OA papers, which then
honestly abstain.

## Decisions (resolved)

- **A. Keep tier trust; multiple axes, not one global axis.** Rank *within* each
  tier by relevance and gate the reserved slots. The corpus-first trust ordering
  and the quota structure stay. This preserves the D18/D19 design contract.
- **B. Calibrate the floor.** Measure the BGE-large cosine distribution on the
  capstone sub-questions (known on-topic vs known off-topic candidates) before
  setting any constant. Ship it env-overridable.
- **C. Max over variants** for the relevance score.
- **D. Land item 0 (shared expansion) as its own commit** ahead of the re-rank.

## Calibration results (step 2, measured 2026-07-24)

BGE-large cosine over **30 real retrieved candidates** (live `search`, the three
capstone sub-questions), query embedded with the BGE prefix, candidates as
`title\n\nsnippet`, exactly as production encodes them.

Overall: `min 0.569, median 0.679, mean 0.670, max 0.773`. The band is narrow;
BGE has a high compressed baseline, which is why the floor had to be measured
rather than guessed.

| tier | n | min | median | max |
|---|---|---|---|---|
| corpus | 13 | 0.572 | 0.685 | 0.709 |
| **OA** | 8 | 0.569 | **0.604** | 0.746 |
| **web** | 9 | 0.655 | **0.727** | 0.773 |

Two things fall straight out of this and justify the whole change:

- **The OA tier has the LOWEST median relevance of the three, yet it gets 4
  reserved slots ranked by citation count.** That is the defect, quantified.
- **The web tier has the HIGHEST median, yet it is capped at 3.** Brave is
  pulling its weight; the quota shape is currently upside down relative to
  actual relevance.

Separation is clean enough to gate on: the on-topic bullseye ("Impact of
Selected Small-Molecule Kinase Inhibitors on Lipid Membranes") scores
0.727-0.773 across all three sub-questions, and a genuinely relevant OA hit
(sorafenib/regorafenib, both kinase inhibitors) scores 0.746. The clearly
off-topic tail (MUC4 oncomucin 0.569, phototropin1 0.586, transglutaminase
0.584, doxorubicin resistance 0.612, neoadjuvant therapy 0.613) sits below 0.62.

**Floor chosen: 0.62** (`OA_RELEVANCE_FLOOR` / `WEB_RELEVANCE_FLOOR`,
env-overridable). It cuts the off-topic tail while keeping every genuinely
relevant external hit in the sample.

*Known limitation:* an absolute floor may be brittle across domains, since a
different field could shift the whole similarity band. Mitigated by the env
override and by logging the drop count. If it proves brittle, the follow-up is a
relative floor (e.g. `max(abs_floor, best_in_query - margin)`).

## Implementation sequence

| Step | What | Commit |
|---|---|---|
| 1 | Item 0: expand once in `search()`, pass `queries=` to all three tiers | own commit |
| 2 | Calibration probe: cosine distribution on-topic vs off-topic (informs step 4) | throwaway script, numbers recorded here |
| 3 | Item 1 + 2: `_attach_relevance`, rank within tier by relevance | own commit |
| 4 | Item 3: relevance floor gating the reserved slots, using step 2's number | same commit as 3 or its own |
| 5 | Item 4: tighten `EXPANSION_SYSTEM_PROMPT` to preserve salient entities | own commit |
| 6 | Tests + before/after measurement on the capstone queries | with each commit |

---

## What the code actually does today (verified, not assumed)

| Tier | Retrieval | `score` used for ranking | Scale |
|---|---|---|---|
| corpus | `paper_search` -> Qdrant `papers_bge` | Qdrant score | cosine, 0..1 |
| OA | `semantic_scholar_search` -> S2 `/paper/search` | **`citation_count`** | unbounded int |
| web | `web_search` -> Brave + SearXNG | **`matched_by`** | 1..n_variants |

`_dedup_and_rank` (search_agent.py:98) sorts each tier by `-score` and then
reserves `OA_QUOTA=4` + `WEB_QUOTA=3` slots inside `top_k`. So within its
reserved slots the OA tier is ordered **purely by fame**, which is exactly why a
GPCR Nobel lecture and K-RAS outranked the on-topic kinase-inhibitor paper.

Other verified facts that shape the design:

1. `papers_bge` is **1024d, distance=Cosine, 68,128 points**. Qdrant scores are
   already cosine similarities, so a locally computed cosine from the same
   encoder is directly comparable.
2. `database.py` already exposes exactly the helpers needed:
   `encode_paper_query()` (applies the BGE query instruction prefix) and
   `encode_paper_doc()` (raw, documents are `title\n\nabstract`).
3. The paper encoder is `get_paper_encoder()` (BGE-large, 1024d). It is NOT
   `get_bge()`, which is BGE-base 768d for user docs. Using the wrong one gives
   a dimension mismatch and non-comparable scores.
4. `expand_queries` already returns `[base, ...variants]` (query_expansion.py:140),
   so **the base query is already sent verbatim to S2**. Half of item 4 is done.
5. `search()` passes `query=` to all three tools, so **each tool calls
   `expand_queries` independently**: three vLLM expansion calls per `search()`,
   and three different variant lists.
6. The retrieval container has **no GPU**, so BGE-large encoding runs on CPU.
7. `sentence-transformers.encode()` does not normalise by default. Cosine must
   be computed explicitly.
8. DR calls `search(top_k=screen_keep + 6)`, i.e. `top_k=18` at default width,
   so a re-rank touches up to ~18 OA + ~18 web candidates per search.

---

## Work items

### Item 0 (enabler): expand the query ONCE in `search()`

Call `expand_queries(query, n=5)` in `search()` and pass `queries=variants` to
`paper_search`, `semantic_scholar_search` and `web_search` (all three already
accept a `queries` list).

Why this comes first:
- It gives one shared variant list, so the tiers retrieve the same intents and
  their scores become comparable in the first place.
- It removes 2 redundant vLLM expansion calls per `search()`.
- It provides the variant list needed for max-over-variants relevance scoring.
- It is the substantive half of item 4.

### Item 1: relevance score for OA + web

New helper in `search_agent.py`:

```
_attach_relevance(variants: list[str], hits: list[dict]) -> None
```

- Batch-encode the variants with `encode_paper_query`.
- Build one doc string per hit as `f"{title}\n\n{snippet}"`, matching the corpus
  document format, and batch-encode them with `encode_paper_doc` in a SINGLE
  `encode([...])` call.
- Run the encode inside `loop.run_in_executor` (the pattern `paper_search`
  already uses for blocking encode work).
- Normalise both sides, take cosine, keep the MAX over variants, write
  `hit["relevance"]`.
- Corpus hits: `relevance = score` (already a max-over-variants Qdrant cosine).
- On any failure (encoder unavailable, exception), leave `relevance = None` and
  fall back to today's legacy sort. Search must never break on this.

### Item 2: rank on the relevance axis

`_dedup_and_rank` sorts each tier by `relevance` descending, with the old score
demoted to a tie-break (OA: `citation_count`; web: `matched_by`; corpus: score).

The cross-tier question (one global axis vs preserving tier trust) is **open
question A** below. This plan defaults to preserving the tier structure.

### Item 3: relevance floor on the reserved slots

Add `OA_RELEVANCE_FLOOR` / `WEB_RELEVANCE_FLOOR` constants (env-overridable,
like the quotas). A hit only occupies a reserved slot if
`relevance >= floor`; otherwise corpus fills the slot. Log (and trace) how many
candidates the gate dropped, so a mis-set floor is visible rather than silently
starving the external tiers.

The floor value must be **calibrated, not guessed**: BGE cosine has a high
baseline (unrelated pairs commonly sit near 0.6). See open question B.

### Item 4: keep salient entities in the expansion

`EXPANSION_SYSTEM_PROMPT` currently asks for "one broader query", which is the
drift that turns "small-molecule kinase inhibitor partitioning" into "membrane
biology". Tighten it so every variant preserves the base query's salient
entities and constraints, and only varies phrasing/synonyms/angle.

No change needed for "send the base verbatim" (already the case, fact 4).

---

## Sequencing

0 -> 1 -> 2 -> 3, then 4 (independent, can land anytime). Item 3 depends on
item 1's scores existing and on the calibration in open question B.

## Tests

Factor the scoring so the ranking math is a pure function over precomputed
vectors; then it is unit-testable with no model and no network.

- Update `tests/test_search_ranking.py` (3 existing tests build hits with a bare
  `score`; they need a `relevance` field and will otherwise assert the old order).
- New cases: relevance re-rank ordering; floor gating (a weak OA tier yields its
  slots to corpus instead of consuming reads); fallback path when the encoder is
  unavailable; exactly one batched `encode` call per search; tie-break behaviour.

## Measurement

1. Before/after on the capstone sub-questions: the specific regression case is
   that GPCR Nobel lecture / K-RAS / exosomes should fall out of the top OA
   slots and the on-topic kinase-inhibitor paper should rise.
2. A full DR run: expect the `not_found` read rate to fall, because off-topic OA
   stops consuming read slots. That is the real payoff, fewer wasted reads.
3. Note the gap: no benchmark currently exercises the OA tier at all (the
   suite's `AgentRetriever` is corpus-only). Closing that is tracked separately
   in `TODO.md`; without it there is no automated score for this change.

## Measurement outcome (step 6, 2026-07-24)

Ran the committed ranking against real live candidates for the three capstone
sub-questions (the live container still runs the old ranking, so its output is
the "before").

- **web: 0/9 reserved slots below the floor.** The web tier is already earning
  its slots; the 0.62 floor does not touch it. Consistent with the calibration
  (web median 0.727).
- **OA: not measurable.** The OA tier returned **zero candidates** on this run.

### Blocker found: the OA tier is silently rate-limited

`semantic_scholar_search` returned `{"results": [], "error": None}`. Probing the
S2 API directly from this host returns **HTTP 429 Too Many Requests** with
"apply for a key for higher rate limits". The calibration run 30 minutes earlier
did get 8 OA candidates, so availability is bursty, which is exactly what an
unauthenticated / shared-pool rate limit looks like.

Two things follow:

1. **Verify `SEMANTIC_SCHOLAR_API_KEY` is actually set in the retrieval
   container.** The container's OA tier came back empty at the same moment a
   keyless call from the host was 429'd, which is what you would expect if the
   container is also calling S2 unauthenticated. If the key is missing or
   expired, the OA tier is effectively dead in production, and no amount of
   re-ranking helps a tier that returns nothing.
2. **`semantic_scholar_search` has no degradation signal.**
   `_semantic_scholar_one` catches the 429, logs a warning and returns `[]`, so
   the tool reports zero results with no error. That is the same silent-zero
   failure mode `web_search` was given a `warning` field for after chat
   689f8df3. The OA tier deserves the same treatment: surface "the scholarly
   backend was rate-limited" so an empty OA tier is never misread as "no OA
   literature exists". Tracked as a follow-up.

Until the key question is resolved, the OA half of this work cannot be scored
end to end. The ranking, gating and expansion changes are all in and unit-tested
regardless, and they take effect the moment the tier returns candidates again.

## Risks

- **Blast radius is limited to the `search` agent.** The benchmark
  `AgentRetriever` copies the `paper_search` ranking directly, so BEIR/LitQA2
  retrieval numbers are not affected by a `search()`-level change.
- **CPU latency.** ~36 short texts per search on CPU BGE-large. Batched this
  should be a few hundred ms, but it must be measured; add a per-tier cap if it
  exceeds ~1s.
- **Over-filtering.** A floor set too high starves the external tiers, which is
  the opposite of the breadth goal. Mitigated by calibration, the env override
  and the drop logging.
- **Shared expansion changes corpus retrieval inside `search()`.** Intended, but
  it is a behaviour change on a path the benchmarks do not cover.

---

## Open questions (need a decision before implementing)

**A. Single global relevance axis, or keep tier trust?**
The module's own design contract (search_agent.py:9-13, D18/D19) states that
"trust isn't uniform, so a single similarity score can't rank across tiers".
Item 2 as originally framed contradicts that contract: on one global cosine
axis, a high-similarity blog post can outrank a corpus paper.
*Default recommendation:* keep the tier structure (corpus first for trust), rank
**within** each tier by relevance, and gate the reserved slots by the floor.
That fixes the actual defect (fame-ranked OA, junk consuming read slots) without
discarding the trust guarantee. Full single-axis merge becomes a follow-up once
we can measure it. Say the word if you would rather go straight to one axis.

**B. What floor value?**
Cannot be guessed responsibly. *Default:* first measure the cosine distribution
over the capstone sub-questions (on-topic vs the known off-topic set), then set
the constant from that, shipped env-overridable.

**C. Relevance against the base query only, or max over variants?**
*Default:* max over the shared variants. It is nearly free once candidate
vectors exist, and it matches how corpus scores are already max-over-variants.

**D. Should item 0 (shared expansion) ship separately first?**
It is a clean standalone improvement (removes 2 LLM calls per search, aligns the
tiers) and is independently verifiable. *Default:* land it as its own commit
ahead of the re-rank, so any retrieval shift it causes is attributable.
