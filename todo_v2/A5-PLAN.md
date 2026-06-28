# A5 — Paraphrase-density routing set + tuning

Status: DRAFT (auto-mode build). Gates, not dates.
Owner: varghele. Built by Claude in auto mode (draft + run autonomously; ask on
genuine forks).

## Goal (from IMPLEMENTATION-HANDOFF §A5 + accumulated migration tuning items)

1. Fan each anchor seed item to N paraphrases per category so one brittle
   phrasing can't swing the score. Phrasing-robustness, measured.
2. Seed the two declared-but-unseeded categories: `abstain` and `artifact`.
   (CATEGORIES lists 14; 12 are seeded, these two are not.)
3. Fold in the migration's open tuning items: reroute (code+research
   ambiguity), `remember` recognition gap, research over-tooling (bounded
   call count). known_url_fetch was fixed at A4b-v3.
4. Keep `reward_basis` discipline; arg predicates stay shape-assertions, never
   frozen blobs.

## Design

### Anchors vs paraphrases (two-tier)

- **Anchors** = the current hand-authored `SEED_ITEMS` (one or two per
  category, carefully predicate-tuned) + 2 new-category anchors. These stay the
  canonical, fast **tuning tier** (high reps, every deploy-measure iteration).
- **Paraphrases** = a frozen `routing_paraphrases.json` produced ONCE by a
  build script (`routing_paraphrases_build.py`), mirroring the
  `router_examples_build.py` pattern. Each paraphrase is a `RoutingEvalItem`
  that INHERITS its anchor's `Expected` (same predicates, same gate, same
  category), id = `{anchor_id}__p{n}`. This is the **regression/robustness
  tier**, run occasionally (acceptance + pre-freeze), not every iteration.

Rationale: the router + labelled set are baked into the image, so each tuning
iteration costs a redeploy + remeasure. A fast anchor tier keeps that loop
tight; the frozen paraphrase tier gives the phrasing-robustness number without
making every iteration multi-hour.

### Generation (one-time, frozen)

Matches the established preference (freeze variants via a one-time generation,
per the retrieval-eval decision). Method per anchor:
1. Local-vLLM-assisted candidate paraphrases (the same model under test, so the
   distribution matches real queries), temperature spread for variety.
2. Normalise + dedup; **bidirectional leakage guard** (see below); I curate out
   any paraphrase whose correct route drifts from the anchor's `Expected`
   (a bad paraphrase that changes the right answer is dropped, not kept).
3. Freeze to committed JSON, provenance-tagged.

### Leakage guard (bidirectional — the new invariant)

`router_examples.json` (TRAIN, ~229) and the routing-eval TEST set must stay
DISJOINT, normalised. Today's guard only checks TRAIN vs the 16 anchors.
Expanding TEST to ~hundreds of paraphrases means a paraphrase could collide
with a train example. So:
- `routing_paraphrases_build.py` asserts no paraphrase normalises to any train
  example.
- `router_examples_build.py`'s existing guard already covers train vs anchors;
  extend it to also check train vs the frozen paraphrases.
Both are frozen JSONs, so both guards run at build time.

### New categories (routing-level only)

- **artifact**: "make an HTML poster of these results", "build a one-page
  handout", "turn this into a slide". `create_artifact` is CORE -> routes
  directly (no `via_tool_search_ok`). Gate: `create_artifact` required;
  forbid `deep_research`/`paper_search` (it's a build, not a search).
- **abstain**: routing-level proxy ONLY. An unanswerable-but-plausible ask
  must route to retrieval (paper_search/web_search), NOT fabricate via
  run_python/create_artifact, and signal not-found (existing abstain proxy +
  judge_abstention wording). **Boundary:** deep corpus-grounded abstention
  (withhold-list, shadow corpus, confabulated-citation rate) is Track C / T3 —
  do NOT duplicate it here. A5's abstain is a thin routing check.

### Tuning targets (measured on the expanded set, tuned without touching alpha/beta)

- **reroute** (code+research ambiguity): paraphrases of
  `reroute_research_to_compute` that stress "plot/graph these <science> values"
  — the implicit-plot pattern that should land on `run_python`, not re-search.
  Tune via labelled-set density (code "implicit plotting" sub-pattern) if weak.
- **remember** recognition: paraphrases of `remember_research_area`
  ("note that...", "for future reference...", "keep in mind I..."). Tune via a
  research/chat fragment cue + labelled-set density if recognition is < target.
- **research over-tooling**: add a bounded-call-count assertion on a deep
  research item (e.g. `max_calls` on paper_search/web_search) so a regression
  into the 20-plus-call vLLM-400 failure mode is caught by the eval.

## Run modes

`run.py` gains `--tier {anchor,paraphrase,all}` (default `anchor`).
- `--tier anchor`: the ~18 hand-authored items. Fast. Default tuning loop.
- `--tier paraphrase`: the frozen paraphrase set. Robustness/regression.
- Scorecard reports per-category mean pass + spread across paraphrases (the
  phrasing-robustness signal), separate from the anchor gate.

## Gate

- No ANCHOR item regresses vs the A0 scorecard (same gate as A4).
- Paraphrase tier establishes a phrasing-robustness baseline: report
  per-category mean + variance; flag any category whose paraphrase mean sits
  far below its anchor (brittle phrasing surface to tune).
- All 14 categories seeded (definition-of-done checkbox).

## Resolved fork

- **N = 15-20 paraphrases per category** (handoff's literal figure; chosen
  2026-06-28). Full `--tier paraphrase` runs go multi-hour, so they run at
  acceptance / pre-freeze, not every iteration. Tuning stays on the ~18 anchors.
- **Generation method = hand-authored, frozen** (not model-generated). Reason:
  a paraphrase must PRESERVE the anchor's correct route; hand-authoring
  guarantees that and uses the group's domain vocabulary directly. The frozen
  JSON satisfies the determinism preference (no regeneration per run) the same
  way model-gen-then-freeze would. Recorded as a deliberate choice.

## Deploy note (auto-mode economics)

The routing eval is in `benchmarks/` and talks to the live API over HTTP; it
does NOT ship via `deploy.sh`. So the entire test-set expansion (anchors +
paraphrases + scorer + run modes) is built AND measured against the current
live harness with NO deploy. Deploys are only needed when tuning the SERVED
router (labelled set, fragments, router constants).
