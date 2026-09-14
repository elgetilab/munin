# Routing eval facts for paper Section 3.2.1 (per-turn routing)

Compiled 2026-09-14 from the repository at `main` (HEAD `b8ce907`), read-only.
Every claim cites file:line and, where history matters, the commit. Line
numbers are against HEAD. Nothing was modified; the one patch in Section 3 is
a draft that was dry-run with `git apply --check` and exercised in a scratch
copy, not applied.

**Read this first.** The single most consequential finding is a framing
mismatch. The "routing anchor eval" that produced 0.963 is a *tool-trajectory*
eval (which tool fires first, which tools are forbidden, whether the model
clarifies alone). The per-turn *profile* decision that `router.py` makes is
recorded in that eval only as a **non-gating diagnostic** (`profile_match`),
and it stood at **0.70** in the same run that scored 0.963. The paper kit's
sentence "the KNN tier reached a routing anchor score of 0.963"
(`docs/paper-kit/02-ARCHITECTURE.md:72-73`) attributes to the KNN router a
number that measures the whole harness's tool routing under a chat pin, with
the profile check switched off from the gate. Details in Sections 1 and 5.

---

## 0. Context verification: `backend/retrieval/router.py`

**Constants (HEAD, `backend/retrieval/router.py:37-40`):**

```python
KNN_K = 8
MARGIN_THRESHOLD = 0.10     # winner must lead runner-up by this fraction of total vote
OOD_SIM_THRESHOLD = 0.45    # nearest-neighbour cosine sim must exceed this (else OOD -> fallback)
PIN_PRIOR = 0.5             # vote weight the pinned profile gets as a prior
```

The kit's 8 / 0.10 / 0.45 / 0.5 is correct. These values are **unchanged since
the file was created** in commit `1a81c6c` (2026-06-24, `feat(router): A3
per-turn router + labelled example set`): `git diff 1a81c6c HEAD --
backend/retrieval/router.py` touches only a docstring path, the
`stripped_query` field, and the rule-tier return. The comment immediately
above them still reads (`router.py:35-36`):

```python
# Tunable (Q4: tuned against the routing eval; these are sensible starting
# values, NOT final). Kept as module constants so the tuning step is one place.
```

A3-PLAN said the thresholds would be "tuned during the build against the
routing eval" (`docs/paper-track/done/A3-PLAN.md:362-364`). Git history shows
no such tuning commit; A5's tuning surface was prompt fragments and the
labelled set, not the constants (`docs/paper-track/done/A5-PLAN.md:118-131`).

**Cascade (`router.py:108-147`):**

```python
def route(query, pin, index, embed_fn) -> RoutingDecision:
    # Tier 1: slash command is absolute.
    slash = parse_slash(query)
    if slash is not None:
        return RoutingDecision(slash[0], "rule", 1.0, pin=pin, stripped_query=slash[1])

    # Tier 2: distance-weighted KNN with pin prior + OOD guard.
    q = _l2_normalise(np.asarray(embed_fn([query]), dtype=np.float32))[0]
    sims = index.embeddings @ q                      # cosine (both normalised)
    k = min(KNN_K, len(sims))
    top_idx = np.argsort(-sims)[:k]

    votes = {p: 0.0 for p in PROFILES}
    for i in top_idx:
        votes[index.profiles[i]] += max(float(sims[i]), 0.0)   # closer = bigger vote
    if pin in PROFILES:
        votes[pin] += PIN_PRIOR                       # the pin biases the vote (Q2)

    total = sum(votes.values()) or 1.0
    ranked = sorted(votes.items(), key=lambda kv: -kv[1])
    top_profile, top_w = ranked[0]
    runner_w = ranked[1][1] if len(ranked) > 1 else 0.0
    margin = (top_w - runner_w) / total
    nearest_sim = float(sims[top_idx[0]]) if k else 0.0

    in_distribution = nearest_sim >= OOD_SIM_THRESHOLD
    decisive = margin >= MARGIN_THRESHOLD
    if in_distribution and decisive:
        return RoutingDecision(top_profile, "knn", margin, pin=pin)

    # Fallback: pin if explicitly pinned, else chat (Q4).
    fallback = pin if pin in PROFILES else DEFAULT_PROFILE
    return RoutingDecision(fallback, "fallback", margin, pin=pin)
```

Acceptance rule, exactly: vote weight per neighbour is its cosine similarity
clipped at 0 (`router.py:129`); the pin adds a flat 0.5 to its own profile
(`:131`); `margin = (top - runner_up) / total_vote_mass` including the pin
prior in `total` (`:133-137`); accept iff `margin >= 0.10` **and** the single
nearest neighbour's cosine `>= 0.45` (`:140-142`). Both comparisons are `>=`,
not `>` (the kit writes "cosine > 0.45", `02-ARCHITECTURE.md:67`). Slash
commands are `/research`, `/code`, `/chat` only (`router.py:60`).

**Embedder.** Injected (`router.py:18-20`). The production wiring is
`chat_service._bge_embed` (`backend/retrieval/chat_service.py:89-94`):

```python
def _bge_embed(queries: list):
    from database import get_bge
    import numpy as np
    return np.asarray(get_bge().encode(queries, normalize_embeddings=True))
```

`get_bge()` loads `BGE_MODEL_PATH`, default `/models/bge-base`, falling back to
`BAAI/bge-base-en-v1.5` (`backend/retrieval/database.py:20, 244-257`). The
compose file mounts `/opt/munin/data/models/bge-base` at `/models/bge-base`
and sets `BGE_MODEL_PATH=/models/bge-base`
(`backend/docker/docker-compose.yml:196, 238`); that directory's
`config.json` has `hidden_size: 768`. So the router embeds with
**BGE-base-en-v1.5 (768d)**, not the BGE-large retrieval encoder, and with
**no query-instruction prefix** (plain `encode`). The labelled set is
re-embedded at service start (`router.py:86-96`, `chat_service.py:97-103`).

**How the eval pins.** `run_item` posts `"persona": persona` with the default
`A0_PERSONA = "chat"` (`routing_eval.py:817, 918`; `run.py:295`). In
`chat_service`, `"chat"` is a real profile id, so `_auto_route` is False and
`pin_id = "chat"` (`chat_service.py:1917-1925, 1936`). Every anchor run
therefore exercises the router **with a chat pin**: chat gets the 0.5 prior,
and fallback resolves to chat. Real users on the consolidated UI send
`AUTO_PERSONA_ID = "munin"`, which maps to **no pin** (`personas.py:28`,
`chat_service.py:1917-1920`). The eval condition and the production condition
differ in the prior.

---

## 1. The metric behind 0.963

**Script.** `backend/benchmarks/munin_bench/routing/run.py` (CLI) driving
`routing_eval.py` (`SEED_ITEMS`, `score_item`, `run_item`) and
`trajectory.py` (SSE capture). Every `2026-06-2x_routing-*` and
`2026-06-2x_a5-*` scorecard carries this runner's header, including the
literal `"note": "PRE-MIGRATION regression baseline. Not a paper result."`
that `make_header` hard-codes (`run.py:86`). The later anchor-tier files
`2026-07-09_t2-postdeploy`, `2026-07-10_postcap-t3`,
`2026-07-25_toolretire-{before,after}` come from the same runner.

**Which file is 0.963.** `backend/benchmarks/scorecards/2026-07-10_postcap-t3.json`
(`aggregate.mean_pass_rate: 0.963`, `ci95 0.911-1.000`, `n_items 16`,
`reps 5`, `tier anchor`, header `git_sha 304fdc2`, committed in `e78da2c`
2026-07-10). Note: the header SHAs in all these scorecards (`304fdc2`,
`41d20ad`, `5a9df63`, `f84fcb2`, `20a62ef`, ...) **do not resolve** in the
current history because the tree was rewritten before public release; the
commits that *added* the scorecards (`e78da2c`, `e34e344`, ...) do resolve.

**What the number is.** Neither pooled routing accuracy nor macro-F1 over
profiles. It is the **mean over items of the per-item pass fraction across
reps**, where an item passes a rep iff every *gated* trajectory check holds
(`run.py:120-125, 161-166`; `routing_eval.py:357`
`passed = all(checks.values()) if checks else True`). Checks are `first_tool`,
`required`, `forbidden`, `solo`, `no_tool`, `abstain_routing`, filtered by
each item's `reward_basis` (`routing_eval.py:226-227, 270-355`). Since every
item ran the same 5 reps, this equals the pooled pass rate: 77 of 80 turns
(3 failures: `known_doi_read` 3/5, `weather_no_location` 4/5). The CI is a
normal approximation over the 16 per-item means (`run.py:101-111`), which the
code itself calls "rough".

**The profile decision is not in that number.** `score_item` records
`diagnostics["profile_match"] = emitted_profile == exp.profile` and nothing
else about the router (`routing_eval.py:261-262`). Diagnostics are "NEVER
folded into `passed`" (`routing_eval.py:216-223`). This was a deliberate
change in `fd5638c` (2026-06-25, `fix(router): profile = reported metric`),
which turned the earlier gated `checks["profile"]` into a diagnostic with the
rationale "genuinely-ambiguous queries ... meet the tool gate but sit on a
profile boundary, so a profile mismatch must not fail the item"
(`routing_eval.py:250-259`). Regression test:
`backend/benchmarks/tests/test_profile_assertion.py:50-56`
(`test_profile_mismatch_does_not_fail_the_item`).

**All numbers the runner emits, from `2026-07-10_postcap-t3.json`:**

| quantity | value | where |
|---|---|---|
| mean pass rate (headline) | **0.963** (77/80 turns) | `aggregate.mean_pass_rate` |
| 95% CI (normal approx over 16 item means) | 0.911-1.000 | `aggregate.ci95_*` |
| mean flip rate | 0.062 | `aggregate.mean_flip_rate` |
| by_category | 14 categories; `clarify` 0.80, `direct_ref` 0.80, all others 1.00 | `by_category` |
| `profile_match` (non-gating), item level | **7 of 10 profiled items at 1.00, 3 at 0.00** | `per_item.*.diagnostics_rate` |
| `profile_match`, turn level | **35/50 = 0.70** | derived from the same field |

Per-profile breakdown of `profile_match` in that run (gold profile is
`expected.profile`; 6 of the 16 items carry no profile label at all):

| gold profile | items | profile_match |
|---|---|---|
| chat | `weather_with_location`, `weather_no_location`, `define_nmr` | 1.00, 1.00, **0.00** |
| research | `sota_phip`, `group_corpus_qa`, `citing_papers`, `known_doi_read`, `corpus_absent_abstain` | 1.00 x5 |
| code | `reroute_research_to_compute`, `html_poster_artifact` | **0.00, 0.00** |
| (none) | `percent_calc`, `unit_convert_physical`, `known_url_fetch`, `remember_research_area`, `export_bibtex`, `weather_paraphrase` | not measured |

Per-profile accuracy from that run: chat 2/3 items, research 5/5, code 0/2. A
macro average over the three profiles is 0.556; pooled over profiled items
0.70. The runner does not compute either; these are read off `per_item`.

The same 0.70 recurs in every anchor run that recorded the diagnostic
(`2026-06-29_a5-munin-frame`, `2026-07-09_t2-postdeploy`: 7/10;
`2026-07-25_toolretire-{before,after}`: 8/11 with `compare_known_dois` added).
The only routing-accuracy sentence the project ever wrote is in
`docs/paper-track/done/A3-PLAN.md:293-296`: "Profile routing accuracy: 6/8
profiled items correct ... (75% routing accuracy)" at the A3 gate v3.

**Most recent anchor-tier scorecard** is not 0.963. It is
`2026-07-25_toolretire-after.json`: mean pass **0.835**, n=17, reps 5
(`before`: 0.788), with `sota_phip` 0/5 (`first tool 'search', expected
'deep_research'`) and `group_corpus_qa` 2/5 after the tool consolidation.
Commit `e34e344` calls the overall move "within noise". The paper kit
(`05-RESULTS.md:488-491`, `06-ABLATIONS.md:202`) quotes 0.963 as
"post-deploy", which is true of the 2026-07-10 deploy of the tool-call cap,
not of the current tree.

**Deploy gate.** The `>= 0.950` figure is a **written procedure, not code**.
It appears in `docs/paper-track/T2-T3-EDIT-PLAN.md:26` ("routing anchor eval
(gate: >= 0.950, no regression)") and
`docs/paper-track/HARNESS-ITERATION-SCOPE.md:14, 167`, and as "~0.95 with no
item regressed" in `docs/paper-track/done/PERSONA-CONSOLIDATION-PLAN.md:41-42`.
`run.py` has no threshold, no non-zero exit on regression, and `backend/deploy.sh`
and `.github/workflows/ci.yml` contain no reference to the routing eval.
The 0.950 is the A5 v4 result (`2026-06-28_a5-v4-anchor.json`) adopted as the
bar by the two later plans. Enforcement was a human reading the scorecard.

---

## 2. Anchor set size

**Anchor file.** The anchors are Python literals: `SEED_ITEMS` in
`backend/benchmarks/munin_bench/routing/routing_eval.py:367-749`. There is no
separate data file. The paraphrase tier is
`backend/benchmarks/munin_bench/routing/routing_paraphrases.json` (224
entries, 8 or 16 per anchor, built by `routing_paraphrases_build.py`).

**Counts at HEAD.**

| set | total | chat | research | code | unlabelled |
|---|---|---|---|---|---|
| anchors in `SEED_ITEMS` | **18** | 3 | 6 | 2 | **7** (no `expected.profile`) |
| anchors actually run (`web_search_degraded` is skipped, `run.py:313-314`) | **17** | 3 | 6 | 2 | 6 |
| runtime KNN examples, `backend/retrieval/router_examples.json` | **244** | **70** | **91** | **83** | 0 |

Example-set counts are in the file's own `meta.counts` and were re-counted
from `examples[]`; sources are 228 `curated` + 16 `prompt_suggestion:*`.
The "anchors per profile" column is `expected.profile`, which is the only
profile label an anchor has; the 7 unlabelled anchors (`percent_calc`,
`unit_convert_physical`, `known_url_fetch`, `remember_research_area`,
`export_bibtex`, `web_search_degraded`, `weather_paraphrase`) contribute
nothing to any profile-routing measurement.

**Anchor count over time** (from `git show <sha>:...routing_eval.py`):
15 items / 14 run at `1b6be07` (2026-06-22, A0); 17 / 16 from `8cd63df`
(2026-06-28, added `html_poster_artifact`, `corpus_absent_abstain`); 18 / 17
from `fe0a456` (2026-07-25, added `compare_known_dois`, and re-targeted
`known_doi_read` from `read_paper` to `source`). The 0.963 run used the
17 / 16 set with `known_doi_read` still expecting `read_paper` (its
`completed_in_turn:read_paper` diagnostic is in the scorecard).

**Example-set count over time:** 24 at `1a81c6c` (2026-06-24; 16 prompt
suggestions + **8 seed items**), 229 from `9340364`/`fd5638c` (2026-06-25),
244 from `4355fa4` (2026-08-27, +15 research "property/value lookup"
examples). The 0.963 run was measured against the 229-example set.

---

## 3. Disjointness of anchors from the KNN example set

### (a) Exact match on normalised text

Normalisation as in `router_examples_build._normalise`
(`backend/retrieval/router_examples_build.py:349-353`: lowercase, strip
punctuation to spaces, collapse whitespace). Result over the 18 anchors and
244 examples: **0 collisions**. The 224 paraphrases also have 0 exact
collisions with the example set.

### (b) Near-duplicate check

Run 2026-09-14 with the production router embedder (`SentenceTransformer`
on `/opt/munin/data/models/bge-base`, `normalize_embeddings=True`, i.e. the
exact `_bge_embed` call), cosine of each anchor against all 244 examples,
plus token Jaccard on the normalised text. The model weights are deployed
state, not a repo artifact; the queries and the code are from the repo.

**Two anchor/example pairs exceed cosine 0.95:**

| anchor | anchor query | nearest example (label) | cosine | Jaccard | example source line |
|---|---|---|---|---|---|
| `citing_papers` | "Which papers cite the Zeitler 2021 review, doi 10.1038/s41586-021-03456-2?" | "which papers cite the Zeitler 2021 review" (research) | **0.964** | 0.50 | `router_examples_build.py:186` |
| `sota_phip` | "What's the current state of the art in parahydrogen-induced polarization for in-vivo imaging?" | "find papers on parahydrogen-induced polarization for in-vivo imaging" (research) | **0.951** | 0.50 | `router_examples_build.py:164` |

Next highest, below 0.95 but notable:

| anchor | nearest example (label) | cosine | Jaccard | line |
|---|---|---|---|---|
| `group_corpus_qa` "What did our group report about SABRE catalyst lifetime?" | "what did our group report about catalyst lifetime" (research) | 0.894 | **0.89** | `:223` |
| `weather_no_location` | "what's the weather like right now" (chat) | 0.886 | 0.50 | `:250` |
| `reroute_research_to_compute` | "graph the polarization versus temperature" (code) | 0.830 | 0.31 | `:49` |
| `known_doi_read` | "read this DOI and tell me what polarization method they used" (research) | 0.795 | 0.56 | `:216` |

The `citing_papers` example is the anchor with the DOI removed; the
`group_corpus_qa` example is the anchor minus the word "SABRE". These entered
the example set in `9340364` (2026-06-25), the same commit that removed the
verbatim anchors, under the heading "Generalised phrasings of each profile's
sub-patterns; deliberately NOT the verbatim routing-eval test queries"
(`router_examples_build.py:42-44`). They pass the exact-match guard and are
labelled with the anchor's gold profile. All 18 anchors clear the 0.45 OOD
floor by a wide margin (minimum nearest-neighbour cosine 0.513,
`percent_calc`), so the OOD guard never fires on the anchor set.

### (c) Git history

| event | commit | date |
|---|---|---|
| Anchors authored (15 items) | `1b6be07` feat(benchmarks): A0 routing-eval harness | 2026-06-22 |
| Router + example set created; **8 anchors with `expected.profile` copied INTO the example set** as `source: seed_item` (`git show 1a81c6c:backend/retrieval/router_examples.json`, meta.sources lists "routing-eval seed items with expected.profile") | `1a81c6c` | 2026-06-24 |
| Leak found and removed; example set 24 -> 229; build-time exact-match guard added; near-verbatim "generalised" examples added | `9340364` fix(router): ship labelled set in image, expand it, drop test-set leakage | 2026-06-25 |
| Guard finalised; profile made non-gating | `fd5638c` fix(router): profile = reported metric; leak-free labelled set + guard | 2026-06-25 |
| Anchors +2 (`8cd63df`), reroute stub fixed (`7caacbd`), abstain gate relaxed (`350ce7d`) | | 2026-06-28/29 |
| Anchor +1 and `known_doi_read` re-targeted | `fe0a456` | 2026-07-25 |
| **Example set last changed**: +15 research examples | `4355fa4` fix(search): ... plus the router gap ... | 2026-08-27 |

So: anchors were copied into the example set for one day (2026-06-24 to
2026-06-25). The `2026-06-24_routing-A3-gate.json` scorecard (mean 0.643,
`git_sha 915a1e3`) is the only committed run taken while the leak was live;
A3-PLAN records it as "Test/train LEAKAGE found + removed"
(`docs/paper-track/done/A3-PLAN.md:285-287`). No anchor was ever copied
*from* the example set. The 0.963 run (2026-07-10) post-dates the removal and
pre-dates the 2026-08-27 expansion.

### Does the eval script assert disjointness?

**No.** `routing_eval.py` never opens `router_examples.json`; the only
disjointness text in it is the docstring claim "it stays DISJOINT from the
router TRAIN set by construction (routing_paraphrases_build.py enforces it)"
(`routing_eval.py:760-762`). `run.py` has no check either. The guards that
exist are on the two *build* scripts:

- `router_examples_build._assert_no_test_leakage`
  (`backend/retrieval/router_examples_build.py:355-372`): exact normalised
  match of examples against `SEED_ITEMS` only, run when the example set is
  regenerated. Its docstring says "including near-dups"; the code has no
  near-duplicate logic. It also silently skips if `routing.routing_eval`
  cannot be imported (`:361-364`), which requires `httpx` and `pydantic` on
  the path. It does **not** check the 224 paraphrases, although A5-PLAN said
  it would be extended to (`docs/paper-track/done/A5-PLAN.md:57-58`).
- `routing_paraphrases_build.main`
  (`backend/benchmarks/munin_bench/routing/routing_paraphrases_build.py:374-405`):
  exact normalised match of paraphrases against the example set and against
  other anchors, run when the paraphrase file is regenerated.

Both are one-shot at build time; a later hand edit to either JSON is not
re-checked at eval time. Draft of the smallest patch that makes the eval
refuse to run on a leaked set (dry-run `git apply --check`: applies cleanly;
in a scratch copy it passes on current data and raises on an injected
collision):

```diff
--- a/backend/benchmarks/munin_bench/routing/routing_eval.py
+++ b/backend/benchmarks/munin_bench/routing/routing_eval.py
@@ -784,9 +784,37 @@ def load_paraphrase_items(path: Optional[str] = None) -> list[RoutingEvalItem]:
     return items
 
 
+_TRAIN_PATH = (
+    __import__("pathlib").Path(__file__).resolve().parents[3]
+    / "retrieval" / "router_examples.json"
+)
+
+
+def _normalise(q: str) -> str:
+    """Same normalisation as router_examples_build._normalise."""
+    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", q.lower())).strip()
+
+
+def assert_disjoint_from_train(items: list[RoutingEvalItem], path=None) -> None:
+    """Fail the run if any TEST query (normalised) is also a router TRAIN
+    example. Mirrors the build-time guard so the eval itself refuses to
+    score a leaked set, whichever side was edited last."""
+    import json
+    p = path or _TRAIN_PATH
+    if not p.exists():
+        return
+    train = {_normalise(e["query"]) for e in json.loads(p.read_text())["examples"]}
+    leaks = sorted(it.id for it in items if _normalise(it.query) in train)
+    if leaks:
+        raise RuntimeError(
+            f"TEST/TRAIN LEAKAGE: eval items also in router_examples.json: {leaks}"
+        )
+
+
 def items_for_tier(tier: str) -> list[RoutingEvalItem]:
     """Select the eval item set by tier: 'anchor' (the hand-authored seeds,
     fast tuning loop), 'paraphrase' (the frozen robustness set), or 'all'."""
+    assert_disjoint_from_train(list(SEED_ITEMS) + load_paraphrase_items())
     if tier == "anchor":
         return list(SEED_ITEMS)
     if tier == "paraphrase":
```

This is exact-match only, matching the existing guards' definition of
"disjoint". A near-duplicate assertion (cosine or Jaccard threshold) would
need the embedder inside the benchmark package, which by design does not
import `backend.retrieval` (`routing_eval.py:800-803`); it would fail today on
the two pairs above.

---

## 4. Who labelled the anchors, and how

**Authorship.** `git blame` over `SEED_ITEMS` (`routing_eval.py:367-749`):
every line is authored by `varghele` (the repository's sole committer for
this file, 12 commits from `1b6be07` to `dcec85c`). `router_examples_build.py`
(407 lines) and `routing_paraphrases.json` (1150 lines): 100% `varghele`.
The authored commits were produced in Claude Code sessions (the commit
messages and plan documents are written in that register, e.g. "Decision
(varghele, 2026-06-25): ACCEPT", `A3-PLAN.md:303`), so "authored by" means
committed under one identity; the repo does not separate human-written from
assistant-written label text.

**Second annotator / agreement.** **None.** There is no second label set, no
adjudication file, no kappa for the routing anchors anywhere in the tree.
Every mention of two annotators, Cohen's kappa, or agreement in
`docs/paper-track/` refers to the retrieval qrels of the local pool
(`RETRIEVAL-EVAL-SPEC.md:367-389, 418`, `IMPLEMENTATION-HANDOFF.md:135`), not
to routing. The A5 paraphrases were "hand-authored, literals preserved"
(`routing_paraphrases.json` meta.method) by the same author.

**Written definition of "the correct profile".** There is no standalone
labelling guideline. What exists:

- A one-line ontology: "A persona IS a profile: `{system prompt, sampling,
  tool_allowlist}`. The three are chat / code / research."
  (`docs/paper-track/done/A3-PLAN.md:39-40`), and the pin-strength ladder
  (`:105-112`).
- Each anchor carries a free-text `rationale` field (schema
  `routing_eval.py:167`), e.g. `sota_phip`: "Substantive 'state of the art'
  research -> deep_research first (chat.json TOOL USAGE STRATEGY)"
  (`:450-452`). The rationales argue the *tool* expectation and cite the
  persona prompts (`chat.json`) as the authority; they are per-item, not a
  definition.
- The code comment that turned profile into a diagnostic concedes the
  boundary is fuzzy: "genuinely-ambiguous queries (define_nmr 'what is NMR'
  -> research; reroute 'plot those polarization values...' -> chat) meet the
  tool gate but sit on a profile boundary" (`routing_eval.py:253-256`).
  `define_nmr` is labelled `chat` and has scored `profile_match` 0.00 in
  every run; the same comment says routing it to research is defensible.
- The example-set labels follow a stated convention for one ambiguity
  class: "paper-finding / scientific-literature queries lean RESEARCH"
  (`router_examples_build.py:31-37` at `1a81c6c`, kept at HEAD `:31-35`).

---

## 5. Fallthrough scoring

**How a fallback turn is scored.** `score_item` receives only
`emitted_profile` (`routing_eval.py:230-234, 942`); the routing method is
captured from the `routing` SSE event into `CapturedTrajectory.routing_method`
(`trajectory.py:50-55, 138-141`) and **never used**: no reference outside
`trajectory.py` and one unit test (`tests/test_profile_assertion.py:37`). The
scorer therefore cannot distinguish rule / knn / fallback. The only
profile-related code is:

```python
# routing_eval.py:261-262
if exp.profile and emitted_profile is not None:
    diagnostics["profile_match"] = emitted_profile == exp.profile
```

Consequences, exactly:

1. A turn that falls through tier 2 is emitted with `profile = pin` if
   pinned, else `chat` (`router.py:145-147`; SSE payload
   `chat_service.py:2002-2007` sends `persona_id`, which is the routed
   profile). Under the eval's chat pin, fallback always emits `chat`, so
   `profile_match` is **True iff the anchor's gold label is `chat`**, False
   for a research or code anchor. It is not "any fallthrough counts as
   correct".
2. Because `profile_match` is a diagnostic, **neither outcome changes
   `passed`**. Whether a code anchor fell through to chat or was KNN-routed
   to code has no effect on the 0.963 unless it changed which tools fired.
3. For the 7 anchors without `expected.profile`, nothing is recorded at all.
4. If the eval were run unpinned (`--persona munin`), fallback would still be
   `chat` via `DEFAULT_PROFILE`, so point 1 holds; only the 0.5 prior
   disappears.

**Per-tier counts in the latest run: not determinable from the repo.** No
scorecard records `method`; `run.py`'s `aggregate` never sees it. The KNN
acceptance rate is likewise unrecorded. The only tier evidence in a committed
document is A3-PLAN's note that `reroute` had "margin 0.034 (near-tie) ->
falls back to chat" at the v3 gate (`A3-PLAN.md:297-302`).

**Offline re-derivation (not a committed artifact; reported so the gap is
sized).** Running `router.route()` at HEAD over the 18 anchors with the
current 244-example set, the deployed bge-base weights, and `pin="chat"` as
the eval sends it: 0 rule, **14 knn, 4 fallback** (`percent_calc` margin
0.002, `export_bibtex` 0.008, `reroute_research_to_compute` 0.034,
`web_search_degraded` 0.050); over the 17 run anchors, 14 knn / 3 fallback,
KNN acceptance 0.82. Profile agreement with gold on the 11 labelled anchors:
8/11 (misses: `define_nmr` -> research by KNN, `reroute` -> chat by fallback,
`html_poster_artifact` -> chat by KNN), i.e. the same three misses the
scorecards show. This is the current tree, not the 2026-07-10 state (which
had 229 examples), and it bypasses the live service, so it is indicative only.

---

## 6. Three claims to confirm

### 6.1 "Delegation was removed after a soak run in which it fired roughly 15 times in 6 weeks"

**Source of the count.** `docs/paper-track/done/A4-PLAN.md:182-185`, decision
dated 2026-06-25:

> **Rare usage:** 15 delegation attempts in chats.db over ~6 weeks
> (declining: 7 on 05-28, then 2-3/date, latest 06-19), small user base.

Restated in `shared/docs/DECISIONS.md:235-239` ("~15 attempts in 6 weeks").
The paper kit's phrasing (`02-ARCHITECTURE.md:78-80`) is a paraphrase of
DECISIONS.md.

**What the count actually is.** The dates (05-28 to 06-19) all precede the
A1 commit that disabled delegation (`41fb267`, 2026-06-22), and A4a deleted the
machinery three days later (`158e70c`, 2026-06-25). So the 15 are
**historical `delegate_to_persona` records in `chats.db` while delegation was
enabled**, not attempts logged during a delegation-off soak. The planned
soak with `DELEGATION_ENABLED=false` and `delegation-disabled attempt` log
lines (`docs/paper-track/done/A1-PLAN.md:140-148`) has its deploy and
"Soak started; date logged" checkboxes unticked (`A1-PLAN.md:170-178`); no
soak log, tally, or query script is committed. "~6 weeks" is the plan's own
estimate of the window; the exact start date is not determinable from the
repo (the earliest date quoted is 05-28, which would be about 3 weeks before
06-19).

Accurate form: a query of the chat database on 2026-06-25 found 15
delegation events over roughly the preceding six weeks (latest 2026-06-19),
all cross-profile; delegation was flag-disabled on 2026-06-22 and deleted on
2026-06-25.

### 6.2 "Every tool is reachable from every profile via a core set plus tool_search; resident_tools only surface"

**Confirmed.** Tool list per profile is built in
`backend/retrieval/chat_service.py:359-385`:

```python
def _openai_tools_schema(persona: Optional[dict] = None) -> list[dict]:
    ...
    resident = set(persona_module.resident_tools(persona)) if persona else set()
    unlocked = current_unlocked_tools.get() or set()
    visible = CORE_TOOLS | resident | unlocked

    tools: list[dict] = []
    for name, spec in MCP_TOOLS.items():  # registry order for stable output
        if name not in visible:
            continue
        ...
```

`CORE_TOOLS` (`backend/retrieval/mcp/schemas.py:19-55`): `source`, `search`,
`compute`, `web_search`, `run_python`, `create_artifact`, `calculate`,
`ask_clarification`, `tool_search`, `set_plan`, `update_plan_item` (11).
`resident_tools` is additive only (`personas.py:425-442`: "Additive only ...
every other tool is still reachable via tool_search and nothing is
rejected"); per profile from `shared/personas/*.json` `params.resident_tools`:
chat `web_fetch, remember, recall`; research `get_citations, get_references,
export_citations, paper_lookup, web_fetch, search_user_docs`; code
`edit_python, compile_latex, sandbox_reset, save_artifact_to_documents`.
`tool_search` searches `universe = set(MCP_TOOLS.keys())` with no profile
filter and unlocks matches into `current_unlocked_tools`
(`backend/retrieval/mcp/tools/tool_search.py:143-150, 158-162`). At dispatch
there is no allowlist check left: `_run_tool_calls` goes dedup -> preToolUse
hooks -> `execute_mcp_tool` (`chat_service.py:1114-1164`), and the executor
rejects only names with no registered dispatcher
(`backend/retrieval/mcp/executor.py:330-332`, `"Unknown tool"`). Two stale
comments still mention the allowlist (`tool_search.py:12-15` docstring;
`chat_service.py:1143-1144` "Runs after the persona-allowlist check above")
but no such check exists in the code path.

One nuance for the sentence "every tool": the model can also call a
non-visible tool by name without `tool_search` and it will execute, since
visibility is schema-only. Reachability is therefore even broader than
"core + tool_search".

### 6.3 Per-tool guards checked before invocation

Concrete guards that exist at HEAD, with enforcement point:

| guard | scope | enforced at |
|---|---|---|
| Sandbox network namespace: kernels run under `firejail --net=none --seccomp --private-tmp`; sidecar reachable only on the internal `sandbox-net`, no host port | `run_python`, `edit_python`, `compile_latex` | `backend/sandbox/app/kernel.py:100-103`; `backend/sandbox/app/latex.py:94`; `backend/docker/docker-compose.yml:418-435` |
| Sandbox execution timeout: `timeout_s` clamped to [1, 120] client-side; sidecar `SANDBOX_MAX_TIMEOUT_S=120` | `run_python`, `edit_python` | `backend/retrieval/mcp/tools/sandbox.py:214-222`; `backend/sandbox/app/main.py:46, 157, 186`; compose `:432` |
| Sandbox refused in ephemeral chats (hard wall: returns an error dict, no execution) | `run_python`, `edit_python`, `sandbox_reset` | `sandbox.py:40-46` |
| Sandbox resource quota: 4G memory, CPU quota | sandbox container | `docker-compose.yml:441-450` |
| Per-request egress level (`X-Munin-Egress` header -> `current_egress`; `full` / `oa_only` / `off`, fail-closed on unknown) | `web_search`, `web_fetch`, `semantic_scholar_search`, `source` (OA download and web read), `search` agent tiers, `bibref` | header parsed `backend/retrieval/main.py:1391-1402`; predicate `backend/retrieval/provenance.py:60-69`; call sites `mcp/tools/web.py:260, 705`, `mcp/tools/papers.py:503`, `mcp/tools/source.py:263, 628`, `mcp/tools/search_agent.py:410-411, 488`, `bibref.py:263` |
| URL allowlist for fetches: a URL must have appeared in a prior search result or user message, or be a canonical reference URL | `web_fetch` | `mcp/tools/web.py:715-724`; set seeded `chat_service.py:2012-2016` |
| Cumulative tool-call cap per user message, `CHAT_MAX_TOOL_CALLS` default **30**; breaks into the wrap-up synthesis | all tools, main chat loop | `chat_service.py:197` (constant), `:2890-2896` (check) |
| Turn budget: `params.max_turns` (default 10, clamped [1, 30]; research sets 24) times `(AUTO_CONTINUE_EXTENSIONS + 1)` = 2 | main chat loop | `personas.py:448-466`; `chat_service.py:2305, 2315-2316, 2334` |
| Sub-agent bounds: `max_iterations` (default 8, [1, 30]), `max_tool_calls` (default 20, [1, 100]), `timeout_seconds` (default 300, [10, 1800]) from `backend/config/agents.yml` | agents run via `invoke_agent` (`source`, `search`, `compute`, `research`), **not** the main loop | schema `backend/retrieval/agents/registry.py:44-46`; loop `agents/executor.py:151-152, 178-180, 229-230` |
| `deep_research` internal budgets: `max_fetches` 3/5, `wall_clock_budget_s` 90/180 by depth | `deep_research` | `mcp/tools/research.py:36-56` |
| Plan-approval preToolUse hook: tools listed in `params.plan_approval` (or all non-plan tools when the model set `requires_approval`) are blocked until the user approves the plan; skipped in ephemeral chats | any tool, configurable | `backend/retrieval/hooks/plan_approval.py:60-100`; dispatched `chat_service.py:1147`. **Currently no persona sets `plan_approval`** (all three `null`), so only the model-flag path can engage it |
| Duplicate `create_artifact` collapse within one response | `create_artifact` | `chat_service.py:1114-1140` |
| Argument schema validation and alias normalisation before dispatch | all tools | `mcp/executor.py:320-329` |

Note on the kit's list: `max_iterations` and `timeout_seconds` are
**sub-agent** parameters (`agents.yml`), not guards on the tools the router's
profile sees; the main loop's analogues are `max_turns` and
`CHAT_MAX_TOOL_CALLS`. Tools with a hard per-tool wall beyond the shared
guards: the three sandbox tools (namespace, timeout clamp, ephemeral refusal)
and `web_fetch` (URL allowlist). No tool is walled off by profile.

---

## 7. Paper sentences (true as written)

1. **Metric.** "The routing anchor eval scores tool trajectories, not
   profile labels: an anchor passes a rep when its gated tool checks hold,
   and the reported 0.963 is the mean pass rate over 16 anchors x 5 reps
   (77/80) on 2026-07-10; the router's profile choice is logged as a
   non-gating diagnostic, which agreed with the gold profile on 7 of the 10
   labelled anchors (0.70) in that run."
2. **Anchor set.** "The anchor set comprises 18 hand-written items (17
   runnable), of which 11 carry a profile label (3 chat, 6 research, 2
   code); the router's KNN index holds 244 labelled examples (70 chat, 91
   research, 83 code)."
3. **Disjointness.** "No anchor query appears verbatim (after
   case/punctuation normalisation) in the KNN example set, and the
   example-set build script rejects such collisions; two anchors do have a
   near-verbatim example in the index (cosine 0.964 and 0.951 under the
   router's own embedder), and the eval script itself performs no
   disjointness check."
4. **Labelling.** "Anchors and their gold profiles were written by a single
   author with a per-item rationale; there was no second annotator and no
   agreement measurement."
5. **Fallthrough.** "A turn that falls through the KNN tier is emitted as the
   pinned profile (chat in the eval); the scorer compares only the emitted
   profile with the gold label, so a fallthrough counts as a profile match
   only when the gold label is chat, and in no case affects the headline pass
   rate; the tier at which each anchor resolved is not recorded."

Supporting claims:

- "Delegation was deleted on 2026-06-25 after a chat-database query found 15
  delegation events over roughly the preceding six weeks, all cross-profile."
- "Every profile's tool schema is the 11-tool core plus that profile's
  resident tools plus whatever `tool_search` has unlocked; `tool_search`
  searches the full registry, and no tool is rejected at dispatch by
  profile."
- "Guards checked before invocation are per-tool, not per-profile: the
  firejail `--net=none` sandbox with a 120 s execution ceiling for code
  tools, a per-request egress level on every network-reaching tool, a
  search-result URL allowlist on `web_fetch`, a 30-call cumulative cap and a
  turn budget on the chat loop, and iteration / call / wall-clock bounds on
  sub-agents."

## 8. Cannot claim

- That **0.963 is a routing (profile) accuracy**, or that "the KNN tier
  reached" it. The number is a tool-trajectory pass rate under a chat pin;
  profile agreement in that run was 0.70.
- That the anchor eval measures the router **as deployed for real users**:
  the eval pins `chat` (0.5 prior on chat, fallback to chat); the UI sends
  the unpinned `munin` identity.
- That 0.963 is **current**: the latest anchor-tier scorecard
  (`2026-07-25_toolretire-after`) is 0.835 on 17 items after the tool
  consolidation, and the KNN example set changed again on 2026-08-27 with no
  anchor run since.
- That the gate `>= 0.950` is **enforced**; it is a written procedure in two
  plan documents, with no code, CI, or deploy-script check.
- That the router constants were **tuned** against the eval; they are the
  2026-06-24 initial values and the code still labels them "NOT final".
- That anchors and the KNN index are disjoint **beyond exact match**; two
  near-verbatim pairs exist, and neither build guard checks near-duplicates.
- Any **per-profile precision/recall, macro-F1, or per-tier (rule / KNN /
  fallback) counts** for a committed run; the scorecards do not contain the
  data.
- A **KNN acceptance rate** for any committed run.
- **Inter-annotator agreement** or a written profile-labelling guideline.
- That a **soak with delegation off** produced the 15-in-6-weeks count; the
  events pre-date the disable flag, and the soak's deploy checklist is
  unticked.
- That `max_iterations` / `timeout_seconds` guard the tools the router's
  profile exposes; they bound sub-agents launched via `invoke_agent`.
- That the tier-3 LLM classifier was dropped **because of 0.963**: the
  decision not to build it routinely was made in A3-PLAN on 2026-06-24/25
  (`A3-PLAN.md:359-361`), two weeks before that run, on latency grounds
  with a "build only if 1+2 underperform" clause.
