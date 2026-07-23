"""Unit tests for search_agent._dedup_and_rank - the tier-reservation ranking.
Pure function (no network). The load-bearing property: a plentiful corpus must
NOT crowd the OA/web tiers out of the read pool (breadth)."""

import sys
import importlib

importlib.import_module("mcp.tools.search_agent")
SA = sys.modules["mcp.tools.search_agent"]


def _hit(tier, i):
    return {"source_type": tier, "title": f"{tier}-{i}",
            "doi": f"10.{tier[:2]}/{i}" if tier != SA.TIER_WEB else None,
            "url": f"https://ex.org/{i}" if tier == SA.TIER_WEB else None,
            "score": 100 - i}


def test_rich_corpus_does_not_crowd_out_external_tiers():
    hits = ([_hit(SA.TIER_CORPUS, i) for i in range(40)]
            + [_hit(SA.TIER_OA, i) for i in range(20)]
            + [_hit(SA.TIER_WEB, i) for i in range(20)])
    ranked = SA._dedup_and_rank(hits, top_k=26)
    tiers = [h["source_type"] for h in ranked]
    assert len(ranked) == 26
    # Reserved external slots survive even though corpus alone could fill top_k.
    assert tiers.count(SA.TIER_OA) == SA.OA_QUOTA
    assert tiers.count(SA.TIER_WEB) == SA.WEB_QUOTA
    assert tiers.count(SA.TIER_CORPUS) == 26 - SA.OA_QUOTA - SA.WEB_QUOTA
    # Corpus still leads (trust order preserved).
    assert tiers[0] == SA.TIER_CORPUS


def test_no_reservation_waste_when_external_tiers_thin():
    # Only 1 web, no OA -> corpus fills the rest, nothing is wasted.
    hits = [_hit(SA.TIER_CORPUS, i) for i in range(40)] + [_hit(SA.TIER_WEB, 0)]
    ranked = SA._dedup_and_rank(hits, top_k=26)
    tiers = [h["source_type"] for h in ranked]
    assert len(ranked) == 26
    assert tiers.count(SA.TIER_WEB) == 1
    assert tiers.count(SA.TIER_CORPUS) == 25


def test_web_capped_at_quota():
    hits = [_hit(SA.TIER_WEB, i) for i in range(50)]
    ranked = SA._dedup_and_rank(hits, top_k=26)
    assert len(ranked) == SA.WEB_QUOTA  # web never exceeds its cap, even alone
