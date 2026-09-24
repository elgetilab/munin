"""Corpus provenance stamped on a run: which collections an arm searched and
how many points they held, recorded at capture time.

Written 2026-09-24. The 2026-08-26 / 2026-09-15 Qwen3.8 egress pair recorded no
corpus counts at all, so "did the two arms search the same corpus?" could not be
answered after the fact even though `papers_chunks` had been built between them
(docs/EGRESS-PAIR-COMMIT-AUDIT.md). These pin the two properties that made that
unanswerable: the count is taken per arm, and the collection NAMES are
overridable so the Track C2b absent arm stamps its shadow pair rather than the
production corpus it deliberately does not search.

Pure-function tests; no Qdrant, no live chat.
"""

from __future__ import annotations

import os
import sys

import pytest

HERE = os.path.dirname(__file__)
BENCH = os.path.abspath(os.path.join(HERE, ".."))
if BENCH not in sys.path:
    sys.path.insert(0, BENCH)

from munin_bench import config, scorecard  # noqa: E402


class _FakeQdrant:
    """Counts by collection name; raises for anything it does not hold, which
    is how a missing collection behaves (papers_chunks before 2026-08-30)."""

    def __init__(self, counts):
        self._counts = counts

    def count(self, name):
        if name not in self._counts:
            raise RuntimeError(f"collection {name} not found")
        return type("R", (), {"count": self._counts[name]})()


def test_defaults_to_the_production_pair(monkeypatch):
    monkeypatch.delenv("MUNIN_EVAL_PAPERS_COLLECTION", raising=False)
    monkeypatch.delenv("MUNIN_EVAL_CHUNKS_COLLECTION", raising=False)
    assert scorecard.corpus_collections() == (config.PAPERS_COLLECTION,
                                              config.CHUNKS_COLLECTION)
    assert config.CHUNKS_COLLECTION == "papers_chunks"


def test_absent_arm_stamps_the_shadow_pair_not_production(monkeypatch):
    """The whole point of the override: the C2b absent arm is DEFINED by
    searching a corpus with the source papers removed."""
    monkeypatch.setenv("MUNIN_EVAL_PAPERS_COLLECTION", "papers_shadow")
    monkeypatch.setenv("MUNIN_EVAL_CHUNKS_COLLECTION", "papers_chunks_shadow")
    qc = _FakeQdrant({"papers_bge": 68913, "papers_shadow": 68864,
                      "papers_chunks": 1_430_000, "papers_chunks_shadow": 1_427_753})
    snap = scorecard.corpus_snapshot(qc)
    assert snap["papers_collection"] == "papers_shadow"
    assert snap["papers_points"] == 68864        # not 68913
    assert snap["chunks_collection"] == "papers_chunks_shadow"
    assert snap["chunks_points"] == 1_427_753


def test_counts_both_collections_and_timestamps_the_count(monkeypatch):
    monkeypatch.delenv("MUNIN_EVAL_PAPERS_COLLECTION", raising=False)
    monkeypatch.delenv("MUNIN_EVAL_CHUNKS_COLLECTION", raising=False)
    snap = scorecard.corpus_snapshot(
        _FakeQdrant({"papers_bge": 68913, "papers_chunks": 1_430_000}))
    assert (snap["papers_points"], snap["chunks_points"]) == (68913, 1_430_000)
    assert snap["counted_at"].endswith("+00:00")


def test_a_missing_chunk_collection_is_none_not_a_crash(monkeypatch):
    """Pre-2026-08-30 there was no papers_chunks. A ten-hour arm must not die
    on provenance, and None must not be confused with a real count of 0."""
    monkeypatch.delenv("MUNIN_EVAL_PAPERS_COLLECTION", raising=False)
    monkeypatch.delenv("MUNIN_EVAL_CHUNKS_COLLECTION", raising=False)
    snap = scorecard.corpus_snapshot(_FakeQdrant({"papers_bge": 68913}))
    assert snap["papers_points"] == 68913
    assert snap["chunks_points"] is None


def test_unreachable_qdrant_degrades_rather_than_raising(monkeypatch):
    class _Dead:
        def count(self, name):
            raise ConnectionError("qdrant down")

    snap = scorecard.corpus_snapshot(_Dead())
    assert snap["papers_points"] is None and snap["chunks_points"] is None
    assert snap["papers_collection"]        # names are still recorded


def test_run_header_carries_both_counts(monkeypatch):
    monkeypatch.delenv("MUNIN_EVAL_PAPERS_COLLECTION", raising=False)
    monkeypatch.delenv("MUNIN_EVAL_CHUNKS_COLLECTION", raising=False)
    hdr = scorecard.make_run_header(
        _FakeQdrant({"papers_bge": 68913, "papers_chunks": 1_430_000}),
        encoder="bge-large-en-v1.5")
    assert hdr["corpus_papers"] == 68913
    assert hdr["corpus_chunks"] == 1_430_000
    assert hdr["corpus_collections"] == {"papers": "papers_bge",
                                         "chunks": "papers_chunks"}


# --- scorecard-level hoist ---------------------------------------------------

from munin_bench.ablation.compare import corpus_of  # noqa: E402

_PROD = {"papers_collection": "papers_bge", "chunks_collection": "papers_chunks",
         "papers_points": 68913, "chunks_points": 1_426_195}


def test_agreeing_arms_hoist_to_one_field():
    """Counted minutes apart, so counted_at differs and must not split them."""
    meta = {"bare": {"corpus": dict(_PROD, counted_at="2026-09-24T01:00:00+00:00")},
            "agentic": {"corpus": dict(_PROD, counted_at="2026-09-24T11:00:00+00:00")}}
    got = corpus_of(meta)
    assert got["papers_points"] == 68913 and got["chunks_points"] == 1_426_195
    assert "note" not in got


def test_disagreeing_arms_keep_both_rather_than_picking_one():
    shadow = {"papers_collection": "papers_shadow",
              "chunks_collection": "papers_chunks_shadow",
              "papers_points": 68864, "chunks_points": 1_427_753}
    got = corpus_of({"present": {"corpus": dict(_PROD, counted_at="t1")},
                     "absent": {"corpus": dict(shadow, counted_at="t2")}})
    assert "different corpora" in got["note"]
    assert got["per_arm"]["absent"]["papers_points"] == 68864
    assert got["per_arm"]["present"]["papers_points"] == 68913


def test_unstamped_arms_hoist_nothing():
    """Captures older than this change carry no corpus key; the scorecard then
    omits the field rather than asserting a corpus nobody recorded."""
    assert corpus_of({"bare": {"arm": "bare"}, "agentic": {"arm": "agentic"}}) is None
    assert corpus_of({}) is None
