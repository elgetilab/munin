"""
Unit tests for the title-similarity guard, markup stripping, DOI
extraction, and Crossref merge logic added in Stage 1 of the
2026-05-12 paper-ingest audit (see docs/PAPER-INGEST-AUDIT.md).

Pure-function tests. No GROBID, no Qdrant, no network. Importable
via spec_from_file_location so they don't accidentally trigger any
of the pipeline's lazy DB/embedder loads.

Run:
    python scripts/pipeline/tests/test_paper_pipeline_merge.py

Exit 0 = pass, non-zero = number of failed cases.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
_PIPELINE_DIR = os.path.dirname(_HERE)
_PAPER_PIPELINE_PATH = os.path.join(_PIPELINE_DIR, "paper_pipeline.py")

_spec = importlib.util.spec_from_file_location("paper_pipeline_under_test", _PAPER_PIPELINE_PATH)
pp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pp)


def _check(name: str, ok: bool, detail: str = "") -> bool:
    label = "PASS" if ok else "FAIL"
    suffix = f" — {detail}" if detail and not ok else ""
    print(f"[{label}] {name}{suffix}")
    return ok


# ---------------------------------------------------------------------------
# _normalize_title_tokens — strips markup, lowercases, drops stopwords + shorts
# ---------------------------------------------------------------------------

def test_normalize_basic() -> bool:
    return _check(
        "normalize: basic ASCII",
        pp._normalize_title_tokens("Hello World Title") == {"hello", "world", "title"},
    )


def test_normalize_drops_stopwords() -> bool:
    tokens = pp._normalize_title_tokens("The Theory and the Application")
    return _check(
        "normalize: drops 'the'/'and'",
        "the" not in tokens and "and" not in tokens and "theory" in tokens,
        f"got {tokens!r}",
    )


def test_normalize_drops_short_tokens() -> bool:
    tokens = pp._normalize_title_tokens("a be cat in our home")
    # only 'cat', 'our', 'home' are >=3 chars; 'our' is not a stopword
    return _check(
        "normalize: drops <3-char tokens",
        tokens == {"cat", "our", "home"},
        f"got {tokens!r}",
    )


def test_normalize_strips_html_and_entities() -> bool:
    tokens = pp._normalize_title_tokens(
        "<b>Bold</b> and <sup>1</sup>H NMR &amp; spectroscopy"
    )
    return _check(
        "normalize: HTML + entities stripped",
        "bold" in tokens and "nmr" in tokens and "spectroscopy" in tokens
        and "amp" not in tokens,  # &amp; should not produce a token
        f"got {tokens!r}",
    )


def test_normalize_empty_input() -> bool:
    return _check(
        "normalize: empty input",
        pp._normalize_title_tokens("") == set()
        and pp._normalize_title_tokens(None) == set(),
    )


# ---------------------------------------------------------------------------
# _title_similarity — Jaccard, calibrated against 2026-05-12 audit data
# ---------------------------------------------------------------------------

def test_similarity_identical() -> bool:
    s = "How to measure and evaluate binding affinities"
    return _check(
        "similarity: identical titles -> 1.0",
        pp._title_similarity(s, s) == 1.0,
    )


def test_similarity_ligplot_vs_barhillel_zero() -> bool:
    sim = pp._title_similarity(
        "LIGPLOT: a program to generate schematic diagrams of protein-ligand interactions",
        "<b>Language and information</b>: Selected essays on their theory and application. By Yehoshua Bar-Hillel",
    )
    return _check(
        "similarity: LIGPLOT vs Bar-Hillel -> 0.0",
        sim == 0.0,
        f"got {sim}",
    )


def test_similarity_ocr_drift_high() -> bool:
    """OCR substitution (immunoglobulin -> lmmunoglobulin) on an
    otherwise-identical 14-token title scores 0.818 — that's the
    calibration target we relied on when picking 0.3 as the threshold."""
    sim = pp._title_similarity(
        "A single rearrangement event generates most of the chicken immunoglobulin light chain diversity",
        "A Single Rearrangement Event Generates Most of the Chicken lmmunoglobulin Light Chain Diversity",
    )
    return _check(
        "similarity: OCR-drift case is well above threshold",
        0.75 < sim < 0.9,
        f"expected ~0.818, got {sim:.3f}",
    )


def test_similarity_case_insensitive() -> bool:
    s1 = "EUKARYOTIC PROTEIN SYNTHESIS"
    s2 = "Eukaryotic Protein Synthesis"
    return _check(
        "similarity: case insensitive",
        pp._title_similarity(s1, s2) == 1.0,
    )


def test_similarity_html_markup_tolerated() -> bool:
    """Markup on one side, plain text on the other should still match."""
    return _check(
        "similarity: HTML on one side still matches plain on the other",
        pp._title_similarity(
            "<b>Gd<sup>3+</sup></b>-Gd<sup>3+</sup> distances",
            "Gd3+-Gd3+ distances",
        ) == 1.0,
    )


def test_similarity_empty_returns_zero() -> bool:
    return _check(
        "similarity: empty input -> 0.0",
        pp._title_similarity("", "anything") == 0.0
        and pp._title_similarity("anything", "") == 0.0
        and pp._title_similarity("", "") == 0.0,
    )


def test_similarity_threshold_value() -> bool:
    """The threshold constant in the pipeline class must match the
    documented 0.3 (PAPER-INGEST-AUDIT.md). Catches accidental drift."""
    return _check(
        "merge threshold constant matches docs (0.3)",
        pp.PaperPipeline._MERGE_TITLE_SIM_THRESHOLD == 0.3,
    )


# ---------------------------------------------------------------------------
# _strip_markup — JATS / HTML / entities, idempotent, whitespace-collapsed
# ---------------------------------------------------------------------------

def test_strip_plain_unchanged() -> bool:
    return _check(
        "strip_markup: plain text unchanged",
        pp._strip_markup("plain title") == "plain title",
    )


def test_strip_basic_tags() -> bool:
    return _check(
        "strip_markup: <b> stripped",
        pp._strip_markup("<b>Bold</b> tag") == "Bold tag",
    )


def test_strip_chemistry_notation() -> bool:
    """Chemistry uses <sup>1</sup>H, Gd<sup>3+</sup> — these should
    collapse without inserting spaces (the standard rendering is
    '1H NMR', not '1 H NMR')."""
    return _check(
        "strip_markup: chemistry notation",
        pp._strip_markup("<sup>1</sup>H NMR") == "1H NMR"
        and pp._strip_markup("Gd<sup>3+</sup>-Gd<sup>3+</sup> distances") == "Gd3+-Gd3+ distances",
    )


def test_strip_mathml() -> bool:
    return _check(
        "strip_markup: MathML",
        pp._strip_markup(
            'The<mml:math xmlns:mml="http://www.w3.org/1998/Math/MathML"><mml:mn>3</mml:mn></mml:math> state'
        ) == "The3 state",
    )


def test_strip_named_entities() -> bool:
    return _check(
        "strip_markup: named entities (&amp;, &mdash;)",
        pp._strip_markup("Tom &amp; Jerry &mdash; A study") == "Tom & Jerry — A study",
    )


def test_strip_numeric_entities() -> bool:
    return _check(
        "strip_markup: numeric entity (&#945; = α)",
        pp._strip_markup("G&#945;<sub>q</sub>-coupled") == "Gαq-coupled",
    )


def test_strip_whitespace_collapse() -> bool:
    return _check(
        "strip_markup: collapses whitespace runs",
        pp._strip_markup("   leading   and   trailing   ws  ") == "leading and trailing ws",
    )


def test_strip_idempotent() -> bool:
    """Running _strip_markup on already-clean output must not change it."""
    once = pp._strip_markup("<b>Foo</b> &amp; bar")
    twice = pp._strip_markup(once)
    return _check(
        "strip_markup: idempotent on clean output",
        once == twice,
    )


def test_strip_empty() -> bool:
    return _check(
        "strip_markup: empty / None passthrough",
        pp._strip_markup("") == "" and pp._strip_markup(None) is None,
    )


# ---------------------------------------------------------------------------
# _merge_metadata — the title-similarity guard (Stage 1.2 + 1.3)
# ---------------------------------------------------------------------------

def _make_pipeline_stub():
    """PaperPipeline.__new__ skips __init__ so we don't need Qdrant /
    Neo4j / embedder. _merge_metadata is a pure method on the instance."""
    return pp.PaperPipeline.__new__(pp.PaperPipeline)


def test_merge_ligplot_rejects() -> bool:
    pipe = _make_pipeline_stub()
    grobid = {
        "title": "LIGPLOT: a program to generate schematic diagrams of protein-ligand interactions",
        "doi": "10.2307/411791",
        "year": 1995,
        "journal": "Protein Engineering",
    }
    crossref = {
        "title": "<b>Language and information</b>: Selected essays",
        "container-title": ["Language"],
        "published-print": {"date-parts": [[1965]]},
    }
    out = pipe._merge_metadata(dict(grobid), crossref)
    return _check(
        "merge: LIGPLOT-style mismatch keeps GROBID metadata",
        out["title"] == grobid["title"]
        and out.get("year") == 1995
        and out.get("journal") == "Protein Engineering"
        and out.get("_crossref_doi_rejected") == "10.2307/411791"
        and "Language and information" in out.get("_crossref_title_rejected", ""),
        f"got {out!r}",
    )


def test_merge_clean_match() -> bool:
    pipe = _make_pipeline_stub()
    grobid = {"title": "How to measure and evaluate binding affinities", "doi": "10.7554/eLife.57264"}
    crossref = {
        "title": "How to measure and evaluate binding affinities",
        "container-title": ["eLife"],
        "published-print": {"date-parts": [[2020]]},
    }
    out = pipe._merge_metadata(dict(grobid), crossref)
    return _check(
        "merge: clean match applies Crossref enrichment",
        out["title"] == crossref["title"]
        and out["year"] == 2020
        and out["journal"] == "eLife"
        and "_crossref_doi_rejected" not in out,
    )


def test_merge_strips_html_from_stored_title() -> bool:
    pipe = _make_pipeline_stub()
    # Titles match after normalisation; the merge path runs and should
    # strip JATS/HTML before storage.
    grobid = {"title": "Gd3+-Gd3+ distances exceeding 3 nm", "doi": "10.1/x"}
    crossref = {
        "title": "Gd<sup>3+</sup>-Gd<sup>3+</sup> distances exceeding 3 nm",
        "container-title": ["<i>Phys. Chem. Chem. Phys.</i>"],
    }
    out = pipe._merge_metadata(dict(grobid), crossref)
    return _check(
        "merge: HTML stripped from stored title + journal",
        "<sup>" not in out["title"]
        and "<i>" not in out["journal"]
        and out["title"] == "Gd3+-Gd3+ distances exceeding 3 nm",
    )


def test_merge_crossref_title_as_list() -> bool:
    """Crossref returns `title` as either a string OR a list. The merge
    must handle both shapes."""
    pipe = _make_pipeline_stub()
    grobid = {"title": "How to measure binding affinities", "doi": "10.1/x"}
    crossref = {
        "title": ["How to measure binding affinities"],
        "published-print": {"date-parts": [[2020]]},
    }
    out = pipe._merge_metadata(dict(grobid), crossref)
    return _check(
        "merge: handles Crossref title as list",
        out["title"] == "How to measure binding affinities"
        and out["year"] == 2020,
    )


def test_merge_missing_crossref_title_still_applies_other_fields() -> bool:
    """If Crossref has no title (rare but possible), the guard skips
    and year/journal still flow through."""
    pipe = _make_pipeline_stub()
    grobid = {"title": "Some title", "doi": "10.1/x", "year": 2020}
    crossref = {
        "container-title": ["Journal of X"],
        "published-print": {"date-parts": [[2021]]},
    }
    out = pipe._merge_metadata(dict(grobid), crossref)
    return _check(
        "merge: missing Crossref title still applies year/journal",
        out["title"] == "Some title"
        and out["journal"] == "Journal of X"
        and out["year"] == 2021,
    )


def test_merge_empty_grobid_title_bypasses_guard() -> bool:
    """If GROBID didn't parse a title, the guard has nothing to compare
    against. Crossref title applies as before."""
    pipe = _make_pipeline_stub()
    grobid = {"doi": "10.1/x"}
    crossref = {"title": "Crossref title here", "container-title": ["J"]}
    out = pipe._merge_metadata(dict(grobid), crossref)
    return _check(
        "merge: empty GROBID title bypasses guard",
        out["title"] == "Crossref title here"
        and "_crossref_doi_rejected" not in out,
    )


# ---------------------------------------------------------------------------
# _extract_doi_from_filename — the brittle digit-walk
# ---------------------------------------------------------------------------

def test_extract_doi_standard() -> bool:
    pipe = _make_pipeline_stub()
    return _check(
        "extract_doi: standard doi_X_Y.pdf -> 10.X/Y",
        pipe._extract_doi_from_filename("doi_10.1234_example.pdf") == "10.1234/example",
    )


def test_extract_doi_elife_alphanumeric_suffix() -> bool:
    """eLife DOIs have an alphanumeric suffix; the digit-walk must
    stop at the first underscore after the registrant digits."""
    pipe = _make_pipeline_stub()
    return _check(
        "extract_doi: eLife-style suffix",
        pipe._extract_doi_from_filename("doi_10.7554_eLife.57264.pdf") == "10.7554/eLife.57264",
    )


def test_extract_doi_jstor_short_registrant() -> bool:
    pipe = _make_pipeline_stub()
    return _check(
        "extract_doi: short JSTOR registrant",
        pipe._extract_doi_from_filename("doi_10.2307_411791.pdf") == "10.2307/411791",
    )


def test_extract_doi_without_pdf_suffix() -> bool:
    pipe = _make_pipeline_stub()
    return _check(
        "extract_doi: no .pdf extension still parses",
        pipe._extract_doi_from_filename("doi_10.1234_example") == "10.1234/example",
    )


def test_extract_doi_returns_none_for_non_doi_filename() -> bool:
    pipe = _make_pipeline_stub()
    return _check(
        "extract_doi: non-doi_ filename -> None",
        pipe._extract_doi_from_filename("LIGPLOT_1995.pdf") is None
        and pipe._extract_doi_from_filename("") is None
        and pipe._extract_doi_from_filename("doi_garbage.pdf") is None,
    )


def test_extract_doi_returns_none_when_no_underscore_separator() -> bool:
    """doi_10.1234example.pdf (no underscore after registrant) is
    malformed — must not silently misparse."""
    pipe = _make_pipeline_stub()
    return _check(
        "extract_doi: no _ separator -> None",
        pipe._extract_doi_from_filename("doi_10.1234example.pdf") is None,
    )


# ---------------------------------------------------------------------------
# _load_contributor_sidecar — picks up filename_doi_hint (Stage 1.5)
# ---------------------------------------------------------------------------

def test_sidecar_loads_filename_doi_hint() -> bool:
    import json, tempfile
    with tempfile.TemporaryDirectory() as td:
        pdf = os.path.join(td, "abc.pdf")
        open(pdf, "w").write("fake")
        with open(os.path.join(td, "abc.contributor.json"), "w") as f:
            json.dump({
                "contributor_email": "contributor-d@example.org",
                "research_group": "elgeti",
                "filename_doi_hint": "10.7554/eLife.57264",
            }, f)
        loaded = pp._load_contributor_sidecar(pdf)
    return _check(
        "sidecar: filename_doi_hint propagated",
        loaded["filename_doi_hint"] == "10.7554/eLife.57264"
        and loaded["email"] == "contributor-d@example.org"
        and loaded["group_slug"] == "elgeti",
    )


def test_sidecar_missing_filename_doi_hint() -> bool:
    """Back-compat: old sidecars without the hint key load with None."""
    import json, tempfile
    with tempfile.TemporaryDirectory() as td:
        pdf = os.path.join(td, "abc.pdf")
        open(pdf, "w").write("fake")
        with open(os.path.join(td, "abc.contributor.json"), "w") as f:
            json.dump({
                "contributor_email": "a@b",
                "research_group": "unknown",
            }, f)
        loaded = pp._load_contributor_sidecar(pdf)
    return _check(
        "sidecar: missing hint -> None (back-compat)",
        loaded["filename_doi_hint"] is None,
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

TESTS = [
    test_normalize_basic,
    test_normalize_drops_stopwords,
    test_normalize_drops_short_tokens,
    test_normalize_strips_html_and_entities,
    test_normalize_empty_input,
    test_similarity_identical,
    test_similarity_ligplot_vs_barhillel_zero,
    test_similarity_ocr_drift_high,
    test_similarity_case_insensitive,
    test_similarity_html_markup_tolerated,
    test_similarity_empty_returns_zero,
    test_similarity_threshold_value,
    test_strip_plain_unchanged,
    test_strip_basic_tags,
    test_strip_chemistry_notation,
    test_strip_mathml,
    test_strip_named_entities,
    test_strip_numeric_entities,
    test_strip_whitespace_collapse,
    test_strip_idempotent,
    test_strip_empty,
    test_merge_ligplot_rejects,
    test_merge_clean_match,
    test_merge_strips_html_from_stored_title,
    test_merge_crossref_title_as_list,
    test_merge_missing_crossref_title_still_applies_other_fields,
    test_merge_empty_grobid_title_bypasses_guard,
    test_extract_doi_standard,
    test_extract_doi_elife_alphanumeric_suffix,
    test_extract_doi_jstor_short_registrant,
    test_extract_doi_without_pdf_suffix,
    test_extract_doi_returns_none_for_non_doi_filename,
    test_extract_doi_returns_none_when_no_underscore_separator,
    test_sidecar_loads_filename_doi_hint,
    test_sidecar_missing_filename_doi_hint,
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
