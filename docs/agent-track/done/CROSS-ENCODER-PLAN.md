# Cross-encoder re-rank: plan, with a validation gate first

Status: **NOT BUILT. Superseded 2026-07-24 by the full-text triage** (commit
`65833a0`), which is the alternative this document recommended. Kept for the
measurement that ruled the cross-encoder out.

The triage was validated the same way, against 16 papers whose real read outcome
was already known, and the contrast is stark:

| approach | separation |
|---|---|
| bi-encoder cosine on metadata | AUC 0.655 |
| cross-encoder on metadata | AUC 0.616 |
| **cheap LLM call on the FULL TEXT** | **2/2 useful passed, 14/14 useless skipped, 0 wrong drops** |

That is the finding in one line: **the signal that predicts a useful read lives
in the full text, not in the title or abstract.** No metadata re-ranker was ever
going to fix this, which is why the gate in this plan mattered.

Original draft follows.

---

Status of the original draft: awaiting decision. Motivated by `TODO.md` item 1b: after the
read-order fix, reads still abstain ~74% of the time, and the hypothesis was
that bi-encoder cosine cannot separate "topically near" from "answers the
question", so a cross-encoder should.

**The headline: I tested that hypothesis before planning the build, and the
preliminary evidence does NOT support it.** The plan below therefore leads with
a validation gate rather than an implementation.

---

## What I measured (2026-07-24)

Ground truth came from the system itself: 49 (sub-question, paper) pairs whose
read outcome was already recorded across three DR runs. `resolved` (the read
produced a grounded note) = positive, `not_found` = negative. 6 positives, 43
negatives.

| scorer | AUC | mean rank of the resolved papers |
|---|---|---|
| BGE-large bi-encoder cosine (current production) | **0.655** | 18.3 / 49 |
| `BAAI/bge-reranker-base` cross-encoder | **0.616** | 20.0 / 49 |

The cross-encoder was **worse**, not better.

Two readings, and the second matters more:

1. **The cross-encoder did not earn its place** on this evidence.
2. **Both scorers are weak** (AUC 0.62-0.66 against a 0.5 coin flip). Whether a
   read yields a grounded note is only weakly predictable from title-level
   metadata *by any scorer*. If that holds up, the headroom in re-ranking is
   small no matter which model does it, and the lever is elsewhere.

### Why this is not decisive

- **Title-only input.** Both scorers saw titles, not title+abstract. Cross-encoders
  are built for passage-level text, so this understates them specifically. I
  tried to backfill abstracts from live search but only 5/49 pairs re-matched
  (retrieval is non-deterministic, so today's candidates differ from the
  historical runs).
- **Noisy label.** "Did the read produce a note" conflates topical relevance with
  "contains an extractable, quotable answer to this exact sub-question". A paper
  can be genuinely relevant and still yield no note.
- **Small and imbalanced.** 6 positives.

### Latency is not the constraint

`bge-reranker-base` on the 24-thread host scored 36 realistic pairs in well under
a second. Even allowing for my measurement being optimistic (repeated inputs,
~130 tokens rather than the 512 cap), this is not what decides the question.
Note the retrieval container has **no GPU**, so this would be CPU inference.

---

## Step 0 (GATE): validate properly before building anything

Do not build until this clears. Concretely:

1. Build a larger labeled set (target 150+ pairs) carrying **title + abstract**,
   not titles. Source it by capturing candidates *at read time* inside the DR
   loop (add the snippet to the read trace) so the label and the text are
   recorded together and stay joinable, instead of trying to reconstruct them
   after the fact.
2. Score with: bi-encoder cosine (baseline), `bge-reranker-base`, and
   `bge-reranker-v2-m3` (larger, multilingual, generally stronger).
3. **Gate:** proceed only if a cross-encoder beats the bi-encoder baseline by a
   margin that survives a paired bootstrap. The benchmarks harness already has
   paired bootstrap and Wilcoxon (`munin_bench/metrics`), so reuse it rather
   than eyeballing AUC.
4. Also report the ceiling: what does AUC look like for an oracle that has read
   the full text? If even that is low, the label is the problem and the whole
   framing needs revisiting.

If the gate fails, stop and take the alternatives below instead.

## Conditional build design (only if the gate clears)

- **Placement:** a second pass in `search_agent`, after `_attach_relevance` and
  before `_dedup_and_rank`. Bi-encoder narrows to the top ~20, cross-encoder
  re-scores those into `relevance`. Everything downstream (tier gating, the DR
  read ordering) keeps working unchanged because it already reads `relevance`.
- **Scope:** gate it behind a flag and apply it to `depth="deep"` only at first,
  so user-facing chat latency is unaffected while DR gets the benefit.
- **Model hosting:** `/opt/munin/data/models/` is **not writable by `vi`**, so
  varghele must place the model, plus a compose volume mount and env var
  (`RERANK_MODEL_PATH`), mirroring how `bge-large` is wired.
- **Failure behaviour:** identical to `_attach_relevance` today. If the model is
  missing or errors, log once and fall back to the bi-encoder score. Retrieval
  must never harden into a dependency on it.
- **Tests:** pure-function scoring tests plus a fallback test, matching the
  existing `test_search_relevance.py` pattern.

## Alternatives the evidence actually points to

Given both scorers scored ~0.65, the more promising levers do not involve
ranking metadata better:

- **Cheap full-text triage before the expensive extraction.** The predictive
  signal appears to live in the full text, not the abstract. A short "does this
  text address the question at all?" call before the 8k-token findings
  extraction would let us afford many more candidate reads at similar cost.
  This directly attacks the 74% abstention rate rather than trying to predict it.
- **Sharpen the screener.** It errs permissive by design (D8) and currently
  passes vocabulary-overlap papers into a read budget that has become expensive.
  Zero new infrastructure.
- **Corpus depth** on under-covered intersections, which no ranking change fixes.

**Recommendation:** run the Step 0 gate, but expect the cheap full-text triage to
be the better investment. It is the one option supported by the finding that
metadata-level relevance is only weakly predictive of read success.

## Open questions

- **A.** Accept the recommendation (gate first, expect to pivot to full-text
  triage), or build the cross-encoder regardless because the title-only test was
  too weak to trust?
- **B.** If we gate: is instrumenting the DR loop to record (question, title,
  abstract, outcome) at read time acceptable? It is the only clean way to build
  a joinable labeled set, and it doubles as permanent eval data.
- **C.** Should any of this wait for the missing OA/multi-tier benchmark track
  (`TODO.md`), so the change can be scored automatically rather than by one-off
  probes?
