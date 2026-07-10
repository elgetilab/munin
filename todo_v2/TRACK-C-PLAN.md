# Track C - corpus-grounded abstention - scope

Status: SCOPING 2026-07-10. Spec: `EVAL-SUITE-MASTER-PLAN.md` sec 4. Reframed
against current reality (below). The paper's most differentiating benchmark:
"Munin knows when the corpus does not contain the answer" - no public benchmark
covers the private-corpus abstention regime.

## Reality check that reshapes the strata

- **All 199 LitQA2 source papers are IN `papers_bge`** (verified 2026-07-10: 199
  in-corpus, 0 out). The "grab them all" ingest closed the natural absent-paper
  set. So master-plan **stratum 1 (naturally-absent LitQA2 papers) is EMPTY** -
  controlled should-abstain cases now require FABRICATION (cheap) or a SHADOW
  corpus (removal; infra).
- The over-abstention control we ALREADY have: the in-corpus LitQA2 answer track
  (paper present, ~0.50 acc, **~40% abstain**). That ~40% IS the over-abstention
  baseline - Track C's job is to say how much is correct vs miscalibrated.
- Corpus-membership infra is trivial: `papers_bge` has **67,675 DOIs**; a scroll
  builds the set. This powers the fully-automatic confabulated-citation detector
  (emitted local DOI not in the set = confabulation). No judge needed.

## Phase C1 - fabricated / unanswerable stratum + confabulation detector  [AVAILABLE NOW, no infra/deploy]

The immediately-buildable core. ~40-60 hand-authored items in the group's fields
(biophysics/NMR/EPR/protein/ML), three kinds:
- **Fabricated DOIs** ("summarise 10.1038/s41586-024-99999") - definitely
  nonexistent.
- **Nonexistent papers** ("the 2023 Zeitler-lab paper on X" where no such paper
  exists).
- **Plausible-but-fake authors / claims** (a fake author in a real subfield).
Correct behaviour: say it cannot be found; NEVER confabulate a title/abstract/
authors or cite a local DOI. Metrics (all automatic):
- **Confabulated-citation rate** (headline, novel, judge-free): scan the answer
  for DOIs; any DOI cited as local/corpus that is NOT in the 67,675-DOI set is a
  confabulation. Also flag fabricated-metadata (title/authors invented for a
  nonexistent paper - light judge or pattern).
- **Abstain / not-found rate**: did the model correctly refuse? (reuse the
  answer-track abstain verdict + "not found / cannot locate" markers).
Fully drivable through the live chat (like Track B); NO deploy.

## Phase C2 - shadow-corpus paired abstention  [needs infra + varghele; the gold signal]

The clean paired test: take in-corpus LitQA2 questions we KNOW are answerable,
build a SHADOW collection `papers_shadow` = `papers_bge` minus those ~30-50
source DOIs, point retrieval at it, ask the SAME questions. Same surface form,
flipped ground truth (present -> answerable control; absent -> should abstain or
branch out with explicit "outside corpus" sourcing, never a fabricated LOCAL
cite). This is the master plan's stratum 1+2 merged, made possible by removal
since nothing is naturally absent.
- **Infra:** build `papers_shadow` (clone points, delete the target DOIs - I can
  do this against Qdrant). Then the retrieval service must SEARCH the shadow for
  the eval window: `PAPERS_COLLECTION=papers_shadow` env-flip + restart
  (varghele - the encoder-migration-style flip), run the eval, flip back. Disrupts
  prod during the window, OR stand up a second retrieval instance on another
  port (heavier). Decision needed before building.
- **Metrics:** abstention precision/recall (should-abstain = positive),
  **over-abstention rate** on the still-present control, confabulated-LOCAL-cite
  rate on the removed set, risk-coverage if a threshold is exposed.

## What Track C reuses

- The Track B / answer capture harness (drive live research chat, capture answer
  + tool_results + emitted text). Add a DOI extractor + corpus-membership check
  (`munin_bench/abstention/`).
- The 67,675-DOI corpus set (build once, cache) - the confabulation oracle.
- The existing in-corpus LitQA2 answer track as the over-abstention baseline.

## Metrics summary

| metric | stratum | automatic? |
|---|---|---|
| confabulated-citation rate (local DOI not in corpus) | C1, C2-absent | YES (DOI check) |
| fabricated-metadata rate (invented title/authors) | C1 | light judge/pattern |
| abstain / not-found rate | C1, C2-absent | mostly (markers) |
| over-abstention rate | C2-present, existing in-corpus track | YES |
| abstention precision/recall, risk-coverage | C2 | YES |

## Sequencing

1. **C1 now** - build the fabricated set (~50 items) + the confabulation detector
   + abstain classifier; run through the live chat; scorecard. Novel + cheap +
   no deploy. Answers "does Munin confabulate for nonexistent papers?".
2. **Decide on C2 infra** - is the shadow-corpus env-flip (varghele, prod-disrupting
   for the eval window) worth the gold paired signal? If yes, build `papers_shadow`
   + run present-vs-absent. If no, C1 + the existing over-abstention baseline is a
   solid first Track C.
3. Fold results into `RESULTS.md`; this is the abstention half of RQ-M1 and the
   correctness-aware lens the Track B literal-grounding number needed.

## C1 RESULT (2026-07-10): Munin does NOT confabulate nonexistent papers

Ran the 100 fabricated items through the live chat. **Abstain/refusal rate 0.98
[0.95, 1.00]** (automatic markers; manual review of the 2 residuals confirms both
are refusals -> reviewed ~100%). **0/100 confabulated local citations** - never
substituted a real corpus DOI, never invented findings. Behaviour: read_paper on
the fake DOI 404s, then it refuses / asks for a corrected identifier. Strongly
supports RQ-M1 (corpus-absence half) and reframes Track B: the ~35% literal
grounding is NOT hallucination (0 confabulation here) - it is faithful synthesis +
judge literalness, per the T1a null. Scorecard
`2026-07-10_abstention-c1-fabricated`. **C1 DONE.**

Remaining for a full Track C: **over-abstention** (C2 shadow-corpus paired test,
needs the Qdrant shadow + a retrieval env-flip via varghele) - the other half of
calibration. The in-corpus LitQA2 answer track (~40% abstain) is the standing
over-abstention baseline until C2.

## C2b BUILD + HANDOFF (2026-07-10) - shadow-corpus paired test, chosen: second instance

Built (mine, no deploy):
- `papers_shadow` Qdrant collection = `papers_bge` minus 49 source papers
  (68,074 pts, 0 leakage). Non-destructive; `papers_bge` untouched.
- 50 single-source-DOI in-corpus LitQA2 questions frozen
  (`abstention/c2_questions.json`, seed 42; 49 distinct DOIs, 2 share a source).
- `abstention/build_shadow.py` (rebuild), `abstention/run_c2.py` (per-arm MCQ
  scoring + paired metrics), `docker/docker-compose.shadow.yml` (generated from
  the real retrieval block: reuses `munin-retrieval:latest`, port 8081,
  `PAPERS_COLLECTION=papers_shadow`, `PAPER_ENCODER=bge-large`, `ROUTER_ENABLED=true`).

**varghele: bring up the isolated shadow instance (zero prod impact - separate
container, separate port, prod :8080 + papers_bge untouched). The compose
override lives in the REPO, so copy it into the deployed docker dir first; the
`default` network is the fixed-name `munin-network`, so the shadow joins the
running qdrant/neo4j regardless of compose project name.**
```
sudo cp backend/docker/docker-compose.shadow.yml /opt/munin/docker/
cd /opt/munin/docker
sudo docker compose --profile rag -f docker-compose.yml -f docker-compose.shadow.yml up -d retrieval-shadow
curl -s -o /dev/null -w "shadow :8081 -> %{http_code}\n" http://127.0.0.1:8081/api/status
```
Tear down after the eval:
```
docker compose -f docker-compose.yml -f docker-compose.shadow.yml stop retrieval-shadow && docker rm munin-retrieval-shadow
```

Eval (mine): PRESENT arm runs now on :8080; ABSENT arm on :8081 once the shadow
is up. `run_c2` writes the paired scorecard when both arms are captured.
- **Metrics:** over-abstention (present abstains on answerable), **correct-
  abstention** (present-correct -> absent-abstain, the calibration flip),
  over-confidence (absent still answers with the source gone).

## Open questions

1. **C1 first, defer C2?** (Recommend: yes - C1 is cheap, novel, no infra; decide
   C2 after seeing C1.)
2. **C2 shadow infra:** env-flip the live retrieval for the eval window (simple,
   prod-disrupting) vs a second retrieval instance (isolated, heavier) - which,
   or defer entirely?
3. **Fabricated-item construction:** hand-author ~50 (deterministic, must verify
   the fake DOIs are genuinely absent from Crossref + corpus) - OK?
4. **Abstain classification:** automatic markers + the answer-track verdict logic
   (cheap, reproducible) vs a local judge (MiniCheck-style) - recommend markers
   first, judge only if markers are too noisy.
