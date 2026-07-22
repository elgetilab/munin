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


import asyncio


def _fake_source(answers):
    async def fake(refs, mode, question):  # noqa: ARG001
        doi = refs[0]["doi"]
        a = answers.get(doi, {"outcome": "not_found"})
        return {"outcome": a["outcome"], "read_depth": a.get("read_depth", "full_text"),
                "answer": a.get("answer"), "quote": a.get("quote"),
                "ref_resolved": {"doi": doi, "title": a.get("title", doi)},
                "abstained": a["outcome"] != "resolved"}
    return fake


def test_read_candidates_dedup_cap_and_notes(monkeypatch):
    import sys, importlib
    importlib.import_module("mcp.tools.source")
    src = sys.modules["mcp.tools.source"]  # the submodule is shadowed by the fn in the package
    answers = {
        "10.1/a": {"outcome": "resolved", "answer": "Answer: A holds", "quote": "q", "title": "Paper A"},
        "10.1/b": {"outcome": "resolved", "answer": "Answer: B holds", "quote": "q", "title": "Paper B"},
    }
    monkeypatch.setattr(src, "source", _fake_source(answers))
    node = {"sub_question": "Q", "id": "sq0", "notes": [], "evidence_refs": []}
    cands = [{"doi": "10.1/a"}, {"doi": "10.1/a"}, {"doi": "10.1/b"}, {"doi": "10.1/c"}]
    reads = asyncio.run(D._read_candidates(node, cands, 5, D.AgentTrace("t"), None))
    dois = [(r["ref"] or {}).get("doi") for r in node["evidence_refs"]]
    assert dois == ["10.1/a", "10.1/b", "10.1/c"]  # duplicate 'a' skipped
    assert len(node["notes"]) == 2                  # a, b resolved; c not_found
    assert reads == 3


def test_read_candidates_respects_cap(monkeypatch):
    import sys, importlib
    importlib.import_module("mcp.tools.source")
    src = sys.modules["mcp.tools.source"]  # the submodule is shadowed by the fn in the package
    monkeypatch.setattr(src, "source", _fake_source(
        {"10.1/a": {"outcome": "resolved", "answer": "Answer: x", "title": "A"}}))
    node = {"sub_question": "Q", "id": "sq0", "notes": [], "evidence_refs": []}
    cands = [{"doi": f"10.1/{i}"} for i in range(10)]
    reads = asyncio.run(D._read_candidates(node, cands, 2, D.AgentTrace("t"), None))
    assert reads == 2 and len(node["evidence_refs"]) == 2


def test_group_by_subquestion():
    notes = [
        {"claim": "a", "sub_question_id": "sq0"},
        {"claim": "b", "sub_question_id": "sq1"},
        {"claim": "c", "sub_question_id": "sq0"},
    ]
    groups = D._group_by_subquestion(notes)
    assert set(groups) == {"sq0", "sq1"}
    assert [n["claim"] for n in groups["sq0"]] == ["a", "c"]  # order preserved
    assert [n["claim"] for n in groups["sq1"]] == ["b"]


def test_extract_value_number_plus_unit():
    assert D._extract_value("K ≈ 84,000 M⁻¹") == ("84000", "M⁻¹")
    assert D._extract_value("IC50 of 1.4 µmol/L") == ("1.4", "µmol/L")
    assert D._extract_value("a 37% improvement") == ("37", "%")
    assert D._extract_value("~10-fold higher") == ("10", "fold")
    # bare counts (no unit) are not quantities
    assert D._extract_value("5 sub-questions were posed") == (None, None)
    assert D._extract_value("no numbers here") == (None, None)


def test_numeric_conflicts_same_unit_different_value():
    notes = [
        {"value": "84000", "unit": "M⁻¹", "claim": "a"},
        {"value": "100", "unit": "M⁻¹", "claim": "b"},      # conflicts with the first
        {"value": "1.4", "unit": "µmol/L", "claim": "c"},   # different unit, no conflict
        {"value": "84000", "unit": "M⁻¹", "claim": "d"},    # same value, no conflict
    ]
    conflicts = D._numeric_conflicts(notes)
    pairs = {(a["claim"], b["claim"]) for a, b in conflicts}
    assert ("a", "b") in pairs
    assert ("a", "d") not in pairs and ("a", "c") not in pairs


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
