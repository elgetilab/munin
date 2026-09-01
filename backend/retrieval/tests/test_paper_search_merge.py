"""
Investigation + regression tests for paper_search's multi-query MERGE.

WHY. A user asked (2026-08-20) whether the corpus held "Device Performance of
Emerging Photovoltaic Materials (Version 6)". Munin answered that it could not
find it "in any scholarly database or on the web". The paper is in the corpus:
DOI 10.1002/aenm.202505525, year 2026, and inside the #deibel group scope the
user had attached. Dense retrieval ranks it #1 at cosine 1.0 for its own title.

The loss happened AFTER retrieval, in paper_search's fan-out merge, which until
2026-09-01 read:

    merged = sorted(seen.values(), key=lambda r: (-matched_by, -score))[:top_k]

`matched_by` counts how many of the auto-expanded query VARIANTS surfaced the
paper, and as the PRIMARY key it is a frequency prior that overrides cosine
outright. An exact-title query matches its own paper on the base query alone,
while the generic paraphrases the expander invents pile up on survey papers that
then win on count. Because the expander runs at temperature 0.5, the same
question flipped between "here it is" and "this paper does not exist" between
attempts, which is the worst possible failure shape: the model reports a
confident absence, and nothing downstream can tell that from a true one.

Measured across three full runs of this file before the fix, and one after:

    paper_search recall of the named paper   12/18 runs -> 6/6, at rank 1
    same retrieval re-sorted by score alone  rank 1 (the counterfactual)
    paper_search at top_k=50                 absent / 36 / 11 -> rank 1

The report predates the chunk index (papers_chunks, backfilled 2026-08-31), so
the live section below also asks whether the depth axis added since then already
rescued this case. Measured, the answer was "on one path, yes": `search()` on the
user's real phrasing returned the paper 6/6 even before the fix, because
`_dedup_and_rank` re-scores every tier on one shared relevance axis and demotes
the legacy per-tier score to a tie-break (`search_agent.py:302-304`). But
`paper_search` called DIRECTLY, which is what the transcript shows the model
doing, was still at 3-4 in 6. It has no chunk path of its own, and a BARE title
lookup is not answer-shaped, so nothing downstream compensated. The chunk work
narrowed the blast radius; it did not remove the defect.

What this file pins now: similarity orders the window, agreement survives as a
bonus too small to overturn a real gap, and the top hit of the caller's own
wording keeps a reserved slot whatever the expander thinks.

Two layers:
  * merge tests (no network) - canned per-query batches through the real
    paper_search, so the ordering rule itself is under test.
  * live tests (need Qdrant + vLLM) - the actual user query against the real
    index, repeated, reporting the hit RATE rather than one sample, because the
    defect is intermittent by construction.

    docker exec munin-retrieval python /app/tests/test_paper_search_merge.py

To run UNDEPLOYED code, mount the repo over /app, but join the compose NETWORK
rather than the container's namespace: `--network container:munin-retrieval`
shares the netns without the DNS that resolves `qdrant`, so every test touching
the index silently takes the "collection is absent" branch and passes vacuously.

    docker run --rm --network munin-network \
      --add-host host.docker.internal:host-gateway \
      -e QDRANT_HOST=qdrant -e QDRANT_PORT=6333 \
      -e VLLM_URL=http://host.docker.internal:8000 -e VLLM_MODEL_NAME=qwen3.8-27b \
      -v "$PWD/backend/retrieval:/app:ro" -w /app \
      munin-retrieval:latest python tests/test_paper_search_merge.py
"""

from __future__ import annotations

import asyncio
import importlib
import sys
import traceback

sys.path.insert(0, "/app")
sys.path.insert(0, ".")

P = importlib.import_module("mcp.tools.papers")
SA = importlib.import_module("mcp.tools.search_agent")

# The paper the user asked about, and the wording that lost it.
TITLE = "Device Performance of Emerging Photovoltaic Materials (Version 6)"
DOI = "10.1002/aenm.202505525"
# His actual chat phrasing, which ends in "?" and so is answer-shaped.
USER_QUESTION = f'Do you have the paper titled "{TITLE}"?'

LIVE_RUNS = 6


def _check(name, ok, detail=""):
    print(("[PASS] " if ok else "[FAIL] ") + name + (f"  {detail}" if detail else ""))
    return ok


def _titles(res):
    return [r.get("title") for r in (res or {}).get("results", [])]


# ---------------------------------------------------------------------------
# Layer 1: the merge, with retrieval stubbed out
# ---------------------------------------------------------------------------

def _stub_batches(batches: dict[str, list[dict]]):
    """Patch paper_search's retrieval so each query returns a canned batch.

    Returns a restore callable. `get_qdrant` / `get_paper_encoder` are bound
    into papers.py's namespace by `from database import ...`, so they must be
    patched THERE, not on database.
    """
    orig = (P.get_qdrant, P.get_paper_encoder, P._qdrant_search_one)
    P.get_qdrant = lambda: object()
    P.get_paper_encoder = lambda: object()
    P._qdrant_search_one = (
        lambda qdrant, specter, q, top_k, query_filter=None: batches.get(q, [])[:top_k]
    )

    def restore():
        P.get_qdrant, P.get_paper_encoder, P._qdrant_search_one = orig

    return restore


def _paper(title, doi, score):
    return {"title": title, "doi": doi, "score": score, "year": 2026}


# One exact-title hit found by the user's own wording, and a field of generic
# survey papers that the paraphrases agree on. The shape is taken from a real
# live merge: the exact title carried the HIGHEST cosine of the whole set
# (0.821) and still sorted fifth behind four lower-scoring papers.
_BASE_Q = TITLE
_VARIANTS = [
    "Performance metrics of novel photovoltaic materials",
    "Evaluation of device efficiency for emerging solar cell materials",
    "Device characteristics of next-generation photovoltaic substances",
    "Assessment of photovoltaic material performance in device configurations",
]
_SURVEYS = [
    _paper("The state of the art in photovoltaic materials and device research", "10.x/1", 0.804),
    _paper("Efficiency Potential of Photovoltaic Materials and Devices", "10.x/2", 0.794),
    _paper("Predicting Trends in Voc", "10.x/3", 0.784),
    _paper("Device Performance of Emerging Photovoltaic Materials (Version 5)", "10.x/4", 0.780),
    _paper("Efficiency of Emerging Photovoltaic Devices under Indoor Conditions", "10.x/5", 0.792),
    _paper("A Flexible Photovoltaic Fatigue Factor", "10.x/6", 0.785),
    _paper("Beginner's Guide to Visual Analysis of Solar Cell Data", "10.x/7", 0.774),
    _paper("Processability Considerations for Organic Photovoltaics", "10.x/8", 0.750),
]
_EXACT = _paper(TITLE, DOI, 0.821)

# The candidate field must EXCEED top_k or truncation never bites and the test
# passes vacuously (it did, on the first run of this file). Each paraphrase
# returns a different 5 of the 8 surveys, so several land at matched_by >= 2 and
# fill the whole result window ahead of the single-variant exact hit. This is
# the live shape: the real merge had 31 candidates for a top_k of 5.
_BATCHES = {
    # The user's own wording puts the exact paper first.
    _BASE_Q: [_EXACT] + _SURVEYS[:4],
    _VARIANTS[0]: _SURVEYS[0:5],
    _VARIANTS[1]: _SURVEYS[1:6],
    _VARIANTS[2]: _SURVEYS[2:7],
    _VARIANTS[3]: _SURVEYS[3:8],
}
_ALL_QUERIES = [_BASE_Q] + _VARIANTS


def test_merge_is_similarity_first():
    """The rule the fix installs: similarity orders the window, agreement is a
    bounded bonus on top.

    Kept as a named fact separate from the recall tests below, because the
    ordering and the reserved slot are two different guarantees and a
    regression in either one should say which broke. `matched_by` must survive
    in the payload: it is real signal and the model reads it.
    """
    restore = _stub_batches(_BATCHES)
    try:
        res = asyncio.run(P.paper_search(queries=_ALL_QUERIES, top_k=5))
    finally:
        restore()
    rows = (res or {}).get("results", [])
    if len(rows) < 2:
        return _check("merge is similarity-first", False, f"only {len(rows)} rows")
    adjusted = [P._agreement_adjusted_score(r) for r in rows]
    ok = (adjusted == sorted(adjusted, reverse=True)
          and rows[0].get("doi") == DOI
          and all("matched_by" in r for r in rows))
    return _check(
        "merge is similarity-first (best-scoring paper leads, agreement kept as signal)",
        ok,
        f"lead={rows[0].get('title')!r} adjusted={[round(a, 4) for a in adjusted]}",
    )


def test_agreement_bonus_breaks_a_tie_but_cannot_overturn_a_gap():
    """Pins the SIZE of the bonus, which is the whole design of the constant.

    Corpus cosines sit in a narrow band, so a bonus big enough to be useful is
    also big enough to recreate the original defect. The cap must leave a real
    similarity gap untouchable while still ordering two papers that are level.
    """
    tie_a = {"title": "tie-a", "doi": "10.t/a", "score": 0.800, "matched_by": 1}
    tie_b = {"title": "tie-b", "doi": "10.t/b", "score": 0.800, "matched_by": 5}
    gap_lo = {"title": "gap-lo", "doi": "10.t/c", "score": 0.804, "matched_by": 5}
    gap_hi = {"title": "gap-hi", "doi": "10.t/d", "score": 0.821, "matched_by": 1}
    breaks_tie = P._agreement_adjusted_score(tie_b) > P._agreement_adjusted_score(tie_a)
    respects_gap = P._agreement_adjusted_score(gap_hi) > P._agreement_adjusted_score(gap_lo)
    # The live pair that lost the user his paper: 0.821 at 1 variant against
    # 0.804 at 5. The bonus must not close that.
    return _check("agreement breaks ties, never overturns a real gap",
                  breaks_tie and respects_gap,
                  f"tie={breaks_tie} gap={respects_gap}")


def test_exact_title_survives_frequency_prior():
    """The user asked for a paper by its exact title. It is in the corpus, it is
    the highest-scoring hit in the merged set, and it must come back.
    """
    restore = _stub_batches(_BATCHES)
    try:
        res = asyncio.run(P.paper_search(queries=_ALL_QUERIES, top_k=5))
    finally:
        restore()
    return _check(
        "exact-title paper is returned despite matching only one variant",
        TITLE in _titles(res),
        f"got {_titles(res)}",
    )


def test_base_query_top_hit_is_never_dropped():
    """A sharper, cheaper invariant than "score-ordered": whatever the user's OWN
    wording ranked first must appear in the result set. The expander's opinion
    may reorder the tail, never evict the literal query's best hit. This is the
    property that would have kept the answer honest here.
    """
    restore = _stub_batches(_BATCHES)
    try:
        res = asyncio.run(P.paper_search(queries=_ALL_QUERIES, top_k=5))
    finally:
        restore()
    return _check(
        "top hit of the base query survives the merge",
        _BATCHES[_BASE_Q][0]["title"] in _titles(res),
        f"got {_titles(res)}",
    )


def test_frequency_prior_needs_a_crowd_to_bite():
    """Negative control: with a single query there is no count to sort on, so
    the exact paper comes back. Pins that the defect is the MERGE and not the
    encoder, the filter, or the excerpt shaping."""
    restore = _stub_batches(_BATCHES)
    try:
        res = asyncio.run(P.paper_search(queries=[_BASE_Q], top_k=5))
    finally:
        restore()
    return _check("single-query search returns the exact paper", TITLE in _titles(res))


def test_title_lookup_is_not_answer_shaped():
    """The chunk index cannot rescue this case.

    search()'s chunk-evidence stage (the depth axis added with papers_chunks)
    only runs when `_wants_answer(query)` is true. A bare title lookup is not
    answer-shaped, so the stage never fires, and paper_search has no chunk path
    of its own. The user's chat phrasing DOES end in "?" and would reach the
    stage today, which is the one thing the chunk work changes here.
    """
    ok = (not SA._wants_answer(TITLE)) and SA._wants_answer(USER_QUESTION)
    return _check("bare title lookup skips the chunk-evidence stage", ok,
                  f"title={SA._wants_answer(TITLE)} question={SA._wants_answer(USER_QUESTION)}")


# ---------------------------------------------------------------------------
# Layer 2: the live index (skipped when Qdrant / vLLM are not reachable)
# ---------------------------------------------------------------------------

def _live_ready() -> bool:
    try:
        return P.get_qdrant() is not None and P.get_paper_encoder() is not None
    except Exception:
        return False


def _skip(name, why):
    print(f"[SKIP] {name}  {why}")
    return True


def test_live_paper_is_in_the_index():
    if not _live_ready():
        return _skip("live: paper is in the index", "qdrant or encoder unavailable")
    import database
    from qdrant_client.http import models as qm
    pts, _ = P.get_qdrant().scroll(
        collection_name=database.PAPERS_COLLECTION,
        scroll_filter=qm.Filter(must=[qm.FieldCondition(
            key="doi", match=qm.MatchValue(value=DOI))]),
        limit=2, with_payload=True, with_vectors=False,
    )
    return _check("live: the paper the user asked for IS in the corpus",
                  len(pts) > 0, f"doi={DOI}")


def test_live_exact_title_recall_rate():
    """The headline number. Same query, repeated, because the expander is
    stochastic and the defect only bites on some expansions."""
    if not _live_ready():
        return _skip("live: exact-title recall rate", "qdrant or encoder unavailable")
    hits, ranks = 0, []
    for _ in range(LIVE_RUNS):
        res = asyncio.run(P.paper_search(query=TITLE, top_k=5))
        titles = _titles(res)
        if TITLE in titles:
            hits += 1
            ranks.append(titles.index(TITLE) + 1)
        else:
            ranks.append(None)
    print(f"       recall {hits}/{LIVE_RUNS}, ranks {ranks}")
    return _check("live: exact title returned on every run", hits == LIVE_RUNS,
                  f"{hits}/{LIVE_RUNS}")


def test_live_score_order_would_have_found_it():
    """Counterfactual on the SAME retrieval: is the paper in the merged candidate
    pool and merely mis-sorted? If yes, the fix is the ordering and nothing
    upstream needs to change.

    The pool is rebuilt here rather than read back from paper_search, because
    the tool truncates BEFORE returning: raising top_k does not widen the window
    onto the pool, it widens the per-query fan-out too (`per_query = max(top_k,
    5)`), which recruits even more agreement candidates ahead of the exact hit.
    Reading `results` at a large top_k therefore measures the defect again
    instead of the counterfactual.
    """
    if not _live_ready():
        return _skip("live: score order recovers it", "qdrant or encoder unavailable")
    from mcp.tools.query_expansion import expand_queries
    variants = asyncio.run(expand_queries(TITLE, n=5)) or [TITLE]
    qdrant, enc = P.get_qdrant(), P.get_paper_encoder()
    pool: dict[str, dict] = {}
    for q in variants:
        for row in P._qdrant_search_one(qdrant, enc, q, 5, None):
            key = P._paper_dedupe_key(row)
            prev = pool.get(key)
            if prev is None or (row.get("score") or 0) > (prev.get("score") or 0):
                pool[key] = row
    by_score = sorted(pool.values(), key=lambda r: -(r.get("score") or 0))
    pos = next((i + 1 for i, r in enumerate(by_score) if r.get("doi") == DOI), None)
    print(f"       pool={len(pool)} rank-by-score={pos}")
    return _check("live: the paper is in the pool and score order puts it top-5",
                  pos is not None and pos <= 5, f"rank {pos}")


def test_live_wider_window_does_not_help():
    """A wide window must not bury the exact hit.

    Under the old rule, asking for MORE results made recall WORSE: `per_query =
    max(top_k, 5)`, so raising top_k widens the per-query fan-out as well as the
    window, and every extra hit per query is another chance for a paper to be
    seen twice and jump the whole single-variant tail. The agreement block grew
    at least as fast as the window it had to fill. Observed at top_k=50 across
    three pre-fix runs: absent from the returned 50 entirely, then rank 36, then
    rank 11, each time behind 50 agreement hits. That is why deep_research,
    which asks for a wide top_k, could not be assumed safe just because chat
    sometimes was. Ranking on similarity removes the dependency on window size
    altogether, so this pins the head of a WIDE window, not just a narrow one.
    """
    if not _live_ready():
        return _skip("live: wider window does not help", "qdrant or encoder unavailable")
    res = asyncio.run(P.paper_search(query=TITLE, top_k=50))
    rows = (res or {}).get("results", [])
    pos = next((i + 1 for i, r in enumerate(rows) if r.get("doi") == DOI), None)
    n_multi = sum(1 for r in rows if r.get("matched_by", 1) > 1)
    print(f"       returned={len(rows)} multi-variant={n_multi} rank={pos}")
    return _check("live: top_k=50 puts the exact title at the head",
                  pos is not None and pos <= 5,
                  f"rank {pos} behind {n_multi} agreement hits")


def test_live_search_ladder_recall():
    """The path a model actually takes today, on the user's real phrasing.

    Egress is forced off so the ladder stays on the corpus: escalating to
    S2/Brave would answer from the open web and hide whether the LOCAL corpus
    lookup works. `search` returns its hits under `ranked` (not `results`), and
    the chunk-evidence stage reports separately under `evidence`, so both are
    counted: the question is whether ANY part of today's pipeline surfaces the
    paper, not just the tier that lost it.
    """
    if not _live_ready():
        return _skip("live: search() ladder recall", "qdrant or encoder unavailable")
    from mcp.context import current_egress
    tok = current_egress.set("off")
    try:
        ranked_hits = evidence_hits = 0
        for _ in range(LIVE_RUNS):
            res = asyncio.run(SA.search(query=USER_QUESTION, top_k=10)) or {}
            titles = [h.get("title") for h in res.get("ranked", [])]
            ranked_hits += TITLE in titles
            ev_dois = [(e.get("ref") or {}).get("doi") for e in res.get("evidence", [])]
            evidence_hits += DOI in ev_dois
    finally:
        current_egress.reset(tok)
    print(f"       ranked {ranked_hits}/{LIVE_RUNS}, chunk evidence {evidence_hits}/{LIVE_RUNS}")
    return _check("live: search() surfaces the paper on every run",
                  ranked_hits == LIVE_RUNS,
                  f"ranked {ranked_hits}/{LIVE_RUNS} evidence {evidence_hits}/{LIVE_RUNS}")


TESTS = [
    test_merge_is_similarity_first,
    test_agreement_bonus_breaks_a_tie_but_cannot_overturn_a_gap,
    test_exact_title_survives_frequency_prior,
    test_base_query_top_hit_is_never_dropped,
    test_frequency_prior_needs_a_crowd_to_bite,
    test_title_lookup_is_not_answer_shaped,
    test_live_paper_is_in_the_index,
    test_live_exact_title_recall_rate,
    test_live_score_order_would_have_found_it,
    test_live_wider_window_does_not_help,
    test_live_search_ladder_recall,
]


def main() -> int:
    failed = 0
    for fn in TESTS:
        try:
            if not fn():
                failed += 1
        except Exception:
            failed += 1
            print(f"[FAIL] {fn.__name__} raised:")
            traceback.print_exc()
    print(f"\n{len(TESTS) - failed}/{len(TESTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
