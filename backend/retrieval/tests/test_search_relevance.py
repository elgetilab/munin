"""Unit tests for the relevance axis in search_agent (2026-07-24).

Background: the three tiers used to be ranked on incomparable scales - corpus by
Qdrant cosine, OA by CITATION COUNT, web by query-overlap count. So a famous
off-topic paper (a GPCR Nobel lecture, K-RAS) took one of the 4 reserved OA read
slots ahead of an on-topic one, and every such read then honestly abstained.

Now every tier is scored on one cosine axis (same encoder, same query prefix,
same document format) and the reserved external slots are gated by a calibrated
floor. Tier structure is deliberately preserved: corpus still leads for trust.

Pure functions only - no encoder, no network.
"""

import importlib
import sys

from pathlib import Path

# Without these, running this file directly puts /app/tests on sys.path
# but NOT /app, so the import below raises ModuleNotFoundError before any
# test runs. The file only ever worked under pytest, which adds the rootdir.
sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

importlib.import_module("mcp.tools.search_agent")
SA = sys.modules["mcp.tools.search_agent"]


def _hit(tier, i, relevance=None, score=None):
    h = {"source_type": tier, "title": f"{tier}-{i}",
         "doi": f"10.{tier[:2]}/{i}" if tier != SA.TIER_WEB else None,
         "url": f"https://ex.org/{i}" if tier == SA.TIER_WEB else None,
         "snippet": f"snippet {i}",
         "score": 100 - i if score is None else score}
    if relevance is not None:
        h["relevance"] = relevance
    return h


# ---------------------------------------------------------------------------
# _max_cosine (pure math)
# ---------------------------------------------------------------------------

def test_max_cosine_identical_and_orthogonal():
    q = [[1.0, 0.0], [0.0, 1.0]]
    d = [[1.0, 0.0], [0.0, 1.0], [-1.0, 0.0]]
    out = SA._max_cosine(q, d)
    assert out[0] == 1.0          # identical to query 0
    assert out[1] == 1.0          # identical to query 1
    # Opposite of query 0 (-1.0) but orthogonal to query 1 (0.0); MAX wins.
    assert out[2] == 0.0

    # With only the opposing query present, the negative score does surface.
    assert SA._max_cosine([[1.0, 0.0]], [[-1.0, 0.0]])[0] == -1.0


def test_max_cosine_takes_best_variant():
    """Decision C: a candidate scores against its BEST matching variant."""
    q = [[1.0, 0.0], [0.7071, 0.7071]]
    d = [[0.0, 1.0]]              # orthogonal to v0, 45deg from v1
    assert abs(SA._max_cosine(q, d)[0] - 0.7071) < 1e-3


def test_max_cosine_normalises_unnormalised_input():
    """Correct whether or not the encoder already L2-normalises."""
    assert abs(SA._max_cosine([[5.0, 0.0]], [[3.0, 0.0]])[0] - 1.0) < 1e-6


def test_max_cosine_handles_zero_vector():
    assert SA._max_cosine([[0.0, 0.0]], [[1.0, 0.0]])[0] == 0.0


# ---------------------------------------------------------------------------
# Ranking by relevance within a tier
# ---------------------------------------------------------------------------

def test_relevance_outranks_legacy_score_within_tier():
    """The regression case: a low-relevance, high-citation OA paper must NOT
    outrank a high-relevance one just because it is famous."""
    famous_offtopic = _hit(SA.TIER_OA, 0, relevance=0.58, score=99999)
    on_topic = _hit(SA.TIER_OA, 1, relevance=0.75, score=3)
    ranked = SA._dedup_and_rank([famous_offtopic, on_topic], top_k=10)
    oa = [h for h in ranked if h["source_type"] == SA.TIER_OA]
    assert oa[0]["title"] == "oa_paper-1", f"got {[h['title'] for h in oa]}"


def test_legacy_score_is_tie_break_only():
    a = _hit(SA.TIER_OA, 0, relevance=0.70, score=10)
    b = _hit(SA.TIER_OA, 1, relevance=0.70, score=500)
    ranked = SA._dedup_and_rank([a, b], top_k=10)
    oa = [h for h in ranked if h["source_type"] == SA.TIER_OA]
    assert oa[0]["title"] == "oa_paper-1"   # equal relevance -> citations decide


def test_corpus_still_leads_for_trust():
    """Tier structure is preserved (decision A): a very relevant web hit does
    NOT displace corpus from the head of the list."""
    hits = ([_hit(SA.TIER_CORPUS, i, relevance=0.65) for i in range(5)]
            + [_hit(SA.TIER_WEB, 0, relevance=0.99)])
    ranked = SA._dedup_and_rank(hits, top_k=6)
    assert ranked[0]["source_type"] == SA.TIER_CORPUS


# ---------------------------------------------------------------------------
# Relevance floor on the reserved slots
# ---------------------------------------------------------------------------

def test_floor_drops_offtopic_oa_and_corpus_reclaims_the_slots():
    """An off-topic OA tier must not consume reserved read slots."""
    corpus = [_hit(SA.TIER_CORPUS, i, relevance=0.70) for i in range(20)]
    weak_oa = [_hit(SA.TIER_OA, i, relevance=0.50) for i in range(10)]
    ranked = SA._dedup_and_rank(corpus + weak_oa, top_k=10)
    tiers = [h["source_type"] for h in ranked]
    assert tiers.count(SA.TIER_OA) == 0, "sub-floor OA should not be reserved"
    assert tiers.count(SA.TIER_CORPUS) == 10, "corpus should reclaim the slots"


def test_floor_keeps_relevant_oa():
    corpus = [_hit(SA.TIER_CORPUS, i, relevance=0.70) for i in range(20)]
    good_oa = [_hit(SA.TIER_OA, i, relevance=0.75) for i in range(10)]
    ranked = SA._dedup_and_rank(corpus + good_oa, top_k=10)
    tiers = [h["source_type"] for h in ranked]
    assert tiers.count(SA.TIER_OA) == SA.OA_QUOTA


def test_gate_stats_report_drops():
    stats = {}
    corpus = [_hit(SA.TIER_CORPUS, i, relevance=0.70) for i in range(10)]
    weak_oa = [_hit(SA.TIER_OA, i, relevance=0.40) for i in range(3)]
    SA._dedup_and_rank(corpus + weak_oa, top_k=10, gate_stats=stats)
    assert stats.get(SA.TIER_OA) == 3, f"expected 3 drops, got {stats!r}"


def test_missing_relevance_passes_the_gate():
    """Encoder unavailable -> hits carry no relevance -> the gate must degrade to
    the old behaviour rather than emptying the external tiers."""
    corpus = [_hit(SA.TIER_CORPUS, i) for i in range(20)]
    oa = [_hit(SA.TIER_OA, i) for i in range(10)]
    ranked = SA._dedup_and_rank(corpus + oa, top_k=10)
    tiers = [h["source_type"] for h in ranked]
    assert tiers.count(SA.TIER_OA) == SA.OA_QUOTA


# ---------------------------------------------------------------------------
# Keyword shaping for the OA tier
# ---------------------------------------------------------------------------

LONG_Q = ("What are the molecular mechanisms by which specific membrane lipid "
          "compositions influence the partitioning and binding affinity of "
          "small-molecule kinase inhibitors?")


def test_keywordize_strips_question_scaffolding():
    kw = SA._keywordize(LONG_Q)
    low = kw.lower()
    for stop in ("what", "are", "the", "by which", "of the"):
        assert stop not in low.split() if " " not in stop else True
    assert "molecular" in low and "membrane" in low


def test_keywordize_preserves_the_salient_entity():
    """The whole point: truncating the tail drops 'kinase inhibitors', and an
    entity-less keyword query is what returns broad off-topic papers."""
    kw = SA._keywordize(LONG_Q).lower()
    assert "kinase" in kw and "inhibitor" in kw, kw


def test_keywordize_dedupes_and_preserves_order():
    kw = SA._keywordize("kinase kinase inhibitor membrane inhibitor")
    assert kw.split() == ["kinase", "inhibitor", "membrane"]


def test_keywordize_empty_for_pure_stopwords():
    assert SA._keywordize("what are the of and to") == ""


def test_oa_queries_falls_back_when_stripped_too_far():
    """A query that keywordizes to nothing must still be searchable."""
    out = SA._oa_queries(["what are the of and to", LONG_Q])
    assert out[0] == "what are the of and to"      # fell back to the original
    assert "kinase" in out[1].lower()


def test_oa_queries_dedupes():
    out = SA._oa_queries(["kinase inhibitor membrane", "the kinase inhibitor membrane"])
    assert len(out) == 1


# ---------------------------------------------------------------------------
# DR read ordering (the read pool must follow relevance, not tier order)
# ---------------------------------------------------------------------------

def test_read_order_is_by_relevance_not_tier():
    """Regression guard for the 2026-07-24 blocker: `search` returns corpus
    first with the reserved OA/web slots at the TAIL, and read_cap then took the
    head, so the external tiers were never read despite scoring highest."""
    import importlib as _il
    _il.import_module("deep_research_agent")
    DR = sys.modules["deep_research_agent"]

    ranked = ([{"source_type": "corpus_paper", "doi": f"10.c/{i}", "relevance": 0.65}
               for i in range(11)]
              + [{"source_type": "oa_paper", "doi": "10.o/1", "relevance": 0.75}]
              + [{"source_type": "web", "url": "https://e.org/1", "relevance": 0.83}])
    ordered = sorted(ranked, key=DR._read_order_key)
    assert ordered[0]["source_type"] == "web", "highest relevance must be read first"
    assert ordered[1]["source_type"] == "oa_paper"
    # With read_cap=6 the external tiers now actually get read.
    assert {c["source_type"] for c in ordered[:6]} == {"web", "oa_paper", "corpus_paper"}


def test_read_order_falls_back_to_tier_order_without_relevance():
    """Encoder unavailable -> no relevance -> stable sort preserves the original
    corpus-first order, i.e. exactly the old behaviour."""
    import importlib as _il
    _il.import_module("deep_research_agent")
    DR = sys.modules["deep_research_agent"]

    ranked = [{"source_type": "corpus_paper", "doi": "10.c/1"},
              {"source_type": "oa_paper", "doi": "10.o/1"},
              {"source_type": "web", "url": "https://e.org/1"}]
    ordered = sorted(ranked, key=DR._read_order_key)
    assert [c["source_type"] for c in ordered] == ["corpus_paper", "oa_paper", "web"]


def test_read_order_mixed_scored_and_unscored():
    import importlib as _il
    _il.import_module("deep_research_agent")
    DR = sys.modules["deep_research_agent"]

    ranked = [{"source_type": "corpus_paper", "doi": "10.c/1"},
              {"source_type": "web", "url": "https://e.org/1", "relevance": 0.80}]
    ordered = sorted(ranked, key=DR._read_order_key)
    assert ordered[0]["source_type"] == "web", "scored candidates lead unscored"


if __name__ == "__main__":
    # Standalone runner, per the convention in tests/README.md: these files
    # must print PASS/FAIL and exit non-zero under plain `python`, not only
    # under pytest (which is not installed in the retrieval container).
    # Functions taking parameters want a pytest fixture and are reported as
    # skipped rather than called with nothing, which would fail misleadingly.
    import inspect as _inspect
    import sys as _sys
    import traceback as _traceback
    import types as _types

    _passed = _failed = _skipped = 0
    for _name, _fn in sorted(globals().items()):
        if not (_name.startswith("test_") and isinstance(_fn, _types.FunctionType)):
            continue
        if _inspect.signature(_fn).parameters:
            print(f"[SKIP] {_name} (needs a pytest fixture)")
            _skipped += 1
            continue
        try:
            _fn()
            print(f"[PASS] {_name}")
            _passed += 1
        except Exception:
            print(f"[FAIL] {_name}")
            _traceback.print_exc()
            _failed += 1
    print(f"\n{_passed} passed, {_failed} failed, {_skipped} skipped")
    _sys.exit(1 if _failed else 0)
