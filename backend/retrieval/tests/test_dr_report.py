"""Unit tests for the Deep Research report builder (R1 - Claude-style structure).
Pure functions only (no vLLM/network); the LLM-backed TL;DR and section prose are
covered by live runs, not here."""

import deep_research_agent as D


def test_cite_prefers_title_then_doi():
    assert D._cite({"title": "AlphaFold", "doi": "10.1/x"}) == "AlphaFold"
    assert D._cite({"doi": "10.1/x"}) == "10.1/x"
    assert D._cite(None) == "source"
    assert D._cite({}) == "source"


def test_key_findings_numbered_with_citations():
    notes = [
        {"claim": "Lipid composition changes binding", "ref": {"title": "Paper One"}},
        {"claim": "Cholesterol sequesters EGFR", "ref": {"doi": "10.1/c"}},
    ]
    md = D._key_findings(notes)
    assert md.startswith("## Key Findings")
    assert "1. Lipid composition changes binding (Paper One)" in md
    assert "2. Cholesterol sequesters EGFR (10.1/c)" in md
    assert D._key_findings([]) == ""


def test_caveats_surface_unresolved_and_abstract_only():
    plan = [
        {"sub_question": "Q1", "status": "resolved", "notes": [{"claim": "x"}]},
        {"sub_question": "Q2 unanswered", "status": "unresolvable", "notes": []},
    ]
    cites = [
        {"ref": {"title": "P1"}, "read_depth": "full_text"},
        {"ref": {"title": "P2"}, "read_depth": "abstract"},
    ]
    md = D._caveats(plan, cites)
    assert "## Caveats" in md
    assert "Q2 unanswered" in md              # unresolved sub-question surfaced
    assert "abstract level only" in md         # abstract-only citation flagged
    # Nothing to caveat when everything resolved + full text.
    assert D._caveats(
        [{"sub_question": "Q1", "status": "resolved", "notes": [1]}],
        [{"ref": {}, "read_depth": "full_text"}]) == ""


def test_citations_dedupe_and_keep_read_depth():
    plan = [
        {"notes": [
            {"ref": {"doi": "10.1/a", "title": "A"}, "read_depth": "full_text"},
            {"ref": {"doi": "10.1/a", "title": "A"}, "read_depth": "full_text"},  # dup
            {"ref": {"doi": "10.1/b", "title": "B"}, "read_depth": "abstract"},
        ]},
    ]
    cites = D._citations(plan)
    assert len(cites) == 2
    assert {c["ref"]["doi"] for c in cites} == {"10.1/a", "10.1/b"}
    assert any(c["read_depth"] == "abstract" for c in cites)
