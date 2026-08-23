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
        "merge: LIGPLOT-style mismatch keeps GROBID metadata and DROPS the DOI",
        out["title"] == grobid["title"]
        and out.get("year") == 1995
        and out.get("journal") == "Protein Engineering"
        and out.get("_crossref_doi_rejected") == "10.2307/411791"
        and "Language and information" in out.get("_crossref_title_rejected", "")
        # The 2026-08-18 change. Keeping this DOI is what let an upload
        # overwrite the cited paper's record.
        and out.get("doi") is None,
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
# State sidecar + dispose helper (Phase B of 2026-05-13)
# ---------------------------------------------------------------------------

def test_state_sidecar_path_alongside_pdf() -> bool:
    """The sidecar lives next to the PDF, with the same stem and a
    `.state.json` suffix. Critical: both `live` (in /papers/pdf/) and
    `quarantine` PDFs use this convention."""
    from pathlib import Path
    return _check(
        "state_sidecar_path: <stem>.state.json sibling",
        pp._state_sidecar_path("/x/doi_10.1_y.pdf") == Path("/x/doi_10.1_y.state.json")
        and pp._state_sidecar_path(Path("/y/uuid.pdf")) == Path("/y/uuid.state.json"),
    )


def test_build_state_sidecar_fresh_record() -> bool:
    """First time a record is disposed: no `existing` sidecar; the
    new doc establishes first_seen_at = last_modified_at and seeds
    history with one entry."""
    paper = pp.Paper(
        id="abc1234", title="X", abstract="", authors=[], doi="10.1/x",
        year=2020, journal="J", references=[],
    )
    doc = pp._build_state_sidecar(
        paper=paper, ingest_path="upload", state="live",
        quarantine_reasons=[], contributor=None, existing=None,
    )
    return _check(
        "build_state_sidecar: fresh record initialises history + first_seen_at",
        doc["state"] == "live"
        and doc["doi"] == "10.1/x"
        and doc["ingest_path"] == "upload"
        and doc["first_seen_at"] == doc["last_modified_at"]
        and len(doc["history"]) == 1
        and doc["history"][0]["state"] == "live"
        and doc["history"][0]["via"] == "upload"
        and doc["history"][0]["reason"] is None
        and doc["schema_version"] == pp.STATE_SCHEMA_VERSION,
    )


def test_build_state_sidecar_preserves_first_seen_across_transitions() -> bool:
    """A live record that later transitions to quarantine: `first_seen_at`
    stays at the original timestamp; `history` grows by one entry."""
    paper = pp.Paper(
        id="abc1234", title="X", abstract="", authors=[], doi="10.1/x",
        year=2020, journal="J", references=[],
    )
    fresh = pp._build_state_sidecar(
        paper=paper, ingest_path="upload", state="live",
        quarantine_reasons=[], existing=None,
    )
    transitioned = pp._build_state_sidecar(
        paper=paper, ingest_path="manual", state="quarantine",
        quarantine_reasons=["title_mismatch_with_crossref"],
        existing=fresh,
    )
    return _check(
        "build_state_sidecar: live -> quarantine preserves first_seen + appends history",
        transitioned["first_seen_at"] == fresh["first_seen_at"]
        and transitioned["state"] == "quarantine"
        and transitioned["quarantine_reasons"] == ["title_mismatch_with_crossref"]
        and len(transitioned["history"]) == 2
        and transitioned["history"][-1]["state"] == "quarantine"
        and transitioned["history"][-1]["reason"] == "title_mismatch_with_crossref",
    )


def test_write_and_load_state_sidecar_roundtrip() -> bool:
    """Atomic write + load returns the same content. Edge: idempotent
    rewrite doesn't corrupt the sidecar."""
    import tempfile, json
    from pathlib import Path
    with tempfile.TemporaryDirectory() as td:
        pdf = Path(td) / "doi_10.1_x.pdf"
        pdf.write_text("fake")
        paper = pp.Paper(
            id="abc1234", title="X", abstract="", authors=[],
            doi="10.1/x", year=2020, journal="J", references=[],
        )
        doc = pp._build_state_sidecar(
            paper=paper, ingest_path="upload", state="live",
            quarantine_reasons=[], existing=None,
        )
        pp._write_state_sidecar(pdf, doc)
        loaded = pp._load_state_sidecar(pdf)
        # Idempotent re-write
        pp._write_state_sidecar(pdf, loaded)
        loaded_again = pp._load_state_sidecar(pdf)
    return _check(
        "state_sidecar: write+load roundtrip preserves content",
        loaded == doc and loaded_again == doc,
    )


def test_load_state_sidecar_missing_returns_none() -> bool:
    """Legacy records have no sidecar; loader returns None cleanly
    rather than raising."""
    return _check(
        "state_sidecar: missing file -> None",
        pp._load_state_sidecar("/tmp/definitely-not-here.pdf") is None,
    )


def test_dispose_live_writes_sidecar_and_moves_pdf() -> bool:
    """End-to-end: success path moves PDF from inbox to /papers/pdf/,
    writes a live state sidecar, mirrors to Qdrant via set_payload."""
    import tempfile, shutil
    from pathlib import Path
    from unittest.mock import MagicMock

    with tempfile.TemporaryDirectory() as td:
        # Stub out PAPERS_DIR + PROCESSED_DIR via monkeypatch to keep
        # the test hermetic.
        td_papers = Path(td) / "pdf"; td_papers.mkdir()
        td_inbox = td_papers / "inbox"; td_inbox.mkdir()
        td_processed = Path(td) / "processed"; td_processed.mkdir()
        td_quar = td_papers / "quarantine"
        orig_papers = pp.PAPERS_DIR
        orig_processed = pp.PROCESSED_DIR
        orig_quar = pp.QUARANTINE_DIR
        pp.PAPERS_DIR = str(td_papers)
        pp.PROCESSED_DIR = str(td_processed)
        pp.QUARANTINE_DIR = str(td_quar)
        try:
            inbox_pdf = td_inbox / "uuid.pdf"
            inbox_pdf.write_text("fake-pdf")
            paper = pp.Paper(
                id="abcd0123", title="T", abstract="", authors=[],
                doi="10.1/x", year=2020, journal="J", references=[],
            )
            qdrant = MagicMock()
            result = pp._dispose_post_pipeline(
                pdf_path=str(inbox_pdf),
                paper=paper,
                skip_reason=None,
                ingest_path="upload",
                qdrant_client=qdrant,
            )
            final_pdf = td_papers / "doi_10.1_x.pdf"
            sidecar = final_pdf.with_name("doi_10.1_x.state.json")
            marker = td_processed / "doi_10.1_x.json"
            ok = (
                result["state"] == "live"
                and result["doi"] == "10.1/x"
                and final_pdf.is_file()
                and not inbox_pdf.is_file()
                and sidecar.is_file()
                and marker.is_file()
                and qdrant.set_payload.called
            )
        finally:
            pp.PAPERS_DIR = orig_papers
            pp.PROCESSED_DIR = orig_processed
            pp.QUARANTINE_DIR = orig_quar
    return _check(
        "dispose: live path moves PDF + writes sidecar + marker + Qdrant mirror",
        ok,
    )


def test_dispose_quarantine_when_paper_is_none() -> bool:
    """Pipeline returned None (quality filter / GROBID failure):
    PDF goes to quarantine/, sidecar records the skip reason."""
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as td:
        td_papers = Path(td) / "pdf"; td_papers.mkdir()
        td_inbox = td_papers / "inbox"; td_inbox.mkdir()
        td_quar = td_papers / "quarantine"
        orig_papers = pp.PAPERS_DIR
        orig_quar = pp.QUARANTINE_DIR
        pp.PAPERS_DIR = str(td_papers)
        pp.QUARANTINE_DIR = str(td_quar)
        try:
            inbox_pdf = td_inbox / "uuid.pdf"
            inbox_pdf.write_text("fake-pdf")
            result = pp._dispose_post_pipeline(
                pdf_path=str(inbox_pdf),
                paper=None,
                skip_reason="grobid_parse_failed",
                ingest_path="upload",
                qdrant_client=None,
            )
            quar_pdf = td_quar / "uuid.pdf"
            sidecar = td_quar / "uuid.state.json"
            ok = (
                result["state"] == "quarantine"
                and result["quarantine_reasons"] == ["grobid_parse_failed"]
                and quar_pdf.is_file()
                and not inbox_pdf.is_file()
                and sidecar.is_file()
            )
        finally:
            pp.PAPERS_DIR = orig_papers
            pp.QUARANTINE_DIR = orig_quar
    return _check(
        "dispose: paper=None routes to quarantine with reason",
        ok,
    )


def test_dispose_quarantine_null_doi_deletes_qdrant_point() -> bool:
    """Stage 1.6: pipeline succeeded but no DOI was extracted. The
    just-created Qdrant point gets deleted; PDF moves to quarantine."""
    import tempfile
    from pathlib import Path
    from unittest.mock import MagicMock
    with tempfile.TemporaryDirectory() as td:
        td_papers = Path(td) / "pdf"; td_papers.mkdir()
        td_inbox = td_papers / "inbox"; td_inbox.mkdir()
        td_quar = td_papers / "quarantine"
        orig_papers = pp.PAPERS_DIR
        orig_quar = pp.QUARANTINE_DIR
        pp.PAPERS_DIR = str(td_papers)
        pp.QUARANTINE_DIR = str(td_quar)
        try:
            inbox_pdf = td_inbox / "uuid.pdf"
            inbox_pdf.write_text("fake-pdf")
            paper = pp.Paper(
                id="abcd0123", title="T", abstract="", authors=[],
                doi=None,                # null DOI -> quarantine
                year=2020, journal="J", references=[],
            )
            qdrant = MagicMock()
            result = pp._dispose_post_pipeline(
                pdf_path=str(inbox_pdf),
                paper=paper,
                skip_reason=None,
                ingest_path="upload",
                qdrant_client=qdrant,
            )
            ok = (
                result["state"] == "quarantine"
                and result["quarantine_reasons"] == ["doi_extraction_failed"]
                and qdrant.delete.called
                and not qdrant.set_payload.called
                and (td_quar / "uuid.pdf").is_file()
            )
        finally:
            pp.PAPERS_DIR = orig_papers
            pp.QUARANTINE_DIR = orig_quar
    return _check(
        "dispose: null-DOI papers quarantined + Qdrant point deleted",
        ok,
    )


def test_dispose_watcher_pdf_already_in_place_no_move() -> bool:
    """Watcher case: PDF is already at /papers/pdf/doi_X.pdf (no
    inbox staging). Dispose writes the sidecar in place and doesn't
    error on the no-op move."""
    import tempfile
    from pathlib import Path
    from unittest.mock import MagicMock
    with tempfile.TemporaryDirectory() as td:
        td_papers = Path(td) / "pdf"; td_papers.mkdir()
        orig_papers = pp.PAPERS_DIR
        pp.PAPERS_DIR = str(td_papers)
        try:
            in_place = td_papers / "doi_10.1_y.pdf"
            in_place.write_text("fake-pdf")
            paper = pp.Paper(
                id="abcd0123", title="T", abstract="", authors=[],
                doi="10.1/y", year=2020, journal="J", references=[],
            )
            qdrant = MagicMock()
            result = pp._dispose_post_pipeline(
                pdf_path=str(in_place),
                paper=paper,
                skip_reason=None,
                ingest_path="crawler",
                qdrant_client=qdrant,
            )
            ok = (
                result["state"] == "live"
                and result["final_pdf_path"] == str(in_place)
                and in_place.is_file()
                and in_place.with_name("doi_10.1_y.state.json").is_file()
            )
        finally:
            pp.PAPERS_DIR = orig_papers
    return _check(
        "dispose: watcher path doesn't move PDF that's already in place",
        ok,
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Corpus-path configuration + the require_corpus_dirs precondition
# (2026-08-18 upload-ingest repair, defect 1). The regression these guard
# against: PAPERS_DIR was hardcoded to a host path, so the same script running
# inside the retrieval container created that path in the container's writable
# layer and filed every uploaded PDF there. See UPLOAD-INGEST-REPAIR-PLAN.md.
# ---------------------------------------------------------------------------

def _reload_pipeline(env: dict):
    """Re-exec the module with `env` applied, returning the fresh module.

    The path constants are read at import time, so overriding them has to
    happen through a reload rather than by assignment.
    """
    import importlib.util
    previous = {k: os.environ.get(k) for k in env}
    os.environ.update({k: v for k, v in env.items() if v is not None})
    for k, v in env.items():
        if v is None:
            os.environ.pop(k, None)
    try:
        spec = importlib.util.spec_from_file_location(
            "paper_pipeline_env_test", _PAPER_PIPELINE_PATH
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for k, v in previous.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_corpus_paths_default_to_host_layout() -> bool:
    """No env set: the host watcher's paths, unchanged from before the fix."""
    mod = _reload_pipeline({
        "PAPERS_PDF_DIR": None,
        "PAPERS_PROCESSED_DIR": None,
        "PAPERS_QUARANTINE_DIR": None,
        "PAPERS_OCR_CACHE_DIR": None,
    })
    return _check(
        "corpus paths: default to the host layout",
        mod.PAPERS_DIR == "/opt/munin/data/papers/pdf"
        and mod.PROCESSED_DIR == "/opt/munin/data/papers/processed"
        and mod.QUARANTINE_DIR == "/opt/munin/data/papers/pdf/quarantine"
        and mod.OCR_CACHE_DIR == "/opt/munin/data/papers/ocr_cache",
        f"got {mod.PAPERS_DIR!r} / {mod.PROCESSED_DIR!r} / {mod.QUARANTINE_DIR!r}",
    )


def test_corpus_paths_follow_container_env() -> bool:
    """The container's mounts win when exported, and quarantine follows
    PAPERS_PDF_DIR rather than staying pinned to the host path."""
    mod = _reload_pipeline({
        "PAPERS_PDF_DIR": "/papers",
        "PAPERS_PROCESSED_DIR": "/papers-processed",
        "PAPERS_QUARANTINE_DIR": None,
        "PAPERS_OCR_CACHE_DIR": "/data/papers/ocr_cache",
    })
    return _check(
        "corpus paths: follow the container env",
        mod.PAPERS_DIR == "/papers"
        and mod.PROCESSED_DIR == "/papers-processed"
        and mod.QUARANTINE_DIR == "/papers/quarantine"
        and mod.OCR_CACHE_DIR == "/data/papers/ocr_cache",
        f"got {mod.PAPERS_DIR!r} / {mod.PROCESSED_DIR!r} / {mod.QUARANTINE_DIR!r}",
    )


def test_require_corpus_dirs_exits_on_missing_dir() -> bool:
    """A PAPERS_DIR that isn't there is fatal, not something we create."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as td:
        missing = str(Path(td) / "not-mounted")
        orig = pp.PAPERS_DIR
        pp.PAPERS_DIR = missing
        try:
            try:
                pp.require_corpus_dirs(check_collection=False)
                raised = False
            except SystemExit:
                raised = True
        finally:
            pp.PAPERS_DIR = orig
        return _check(
            "require_corpus_dirs: missing PAPERS_DIR exits",
            raised and not os.path.exists(missing),
            "it either did not exit or it created the directory",
        )


def test_require_corpus_dirs_passes_on_real_dirs() -> bool:
    """The happy path stays quiet."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as td:
        papers = Path(td) / "pdf"; papers.mkdir()
        processed = Path(td) / "processed"; processed.mkdir()
        (papers / "doi_10.1_x.pdf").write_text("fake-pdf")
        orig = (pp.PAPERS_DIR, pp.PROCESSED_DIR)
        pp.PAPERS_DIR, pp.PROCESSED_DIR = str(papers), str(processed)
        try:
            pp.require_corpus_dirs(check_collection=False)
            ok = True
        except SystemExit:
            ok = False
        finally:
            pp.PAPERS_DIR, pp.PROCESSED_DIR = orig
        return _check("require_corpus_dirs: real dirs pass", ok)


def test_dispose_does_not_create_a_missing_corpus_dir() -> bool:
    """The heart of defect 1: if PAPERS_DIR has vanished, disposal must fail
    loudly instead of conjuring the directory and filing the PDF into it."""
    import tempfile
    from pathlib import Path
    from unittest.mock import MagicMock

    with tempfile.TemporaryDirectory() as td:
        td_inbox = Path(td) / "inbox"; td_inbox.mkdir()
        ghost = Path(td) / "ghost" / "pdf"          # two levels missing
        orig = (pp.PAPERS_DIR, pp.PROCESSED_DIR, pp.QUARANTINE_DIR)
        pp.PAPERS_DIR = str(ghost)
        pp.PROCESSED_DIR = str(Path(td) / "ghost" / "processed")
        pp.QUARANTINE_DIR = str(ghost / "quarantine")
        try:
            inbox_pdf = td_inbox / "uuid.pdf"
            inbox_pdf.write_text("fake-pdf")
            paper = pp.Paper(
                id="abcd0123", title="T", abstract="", authors=[],
                doi="10.1/x", year=2020, journal="J", references=[],
            )
            pp._dispose_post_pipeline(
                pdf_path=str(inbox_pdf), paper=paper, skip_reason=None,
                ingest_path="upload", qdrant_client=MagicMock(),
            )
            # The PDF must still be in inbox, and the bogus tree must not exist.
            ok = inbox_pdf.exists() and not ghost.exists()
        finally:
            pp.PAPERS_DIR, pp.PROCESSED_DIR, pp.QUARANTINE_DIR = orig
        return _check(
            "dispose: missing corpus dir is not invented, PDF is not lost",
            ok,
            "the PDF left inbox/ or the directory tree was created",
        )


# ---------------------------------------------------------------------------
# DOI recovery by title + the collision backstop
# (2026-08-18 upload-ingest repair, defect 2). GROBID sometimes reads a DOI
# off the reference list; looking it up returns a valid record for the WRONG
# paper. Because the DOI keys the Qdrant point, the Neo4j node and the
# filename, keeping it overwrites the cited paper instead of adding this one.
# See UPLOAD-INGEST-REPAIR-PLAN.md defect 2.
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload, self.status_code = payload, status_code

    def json(self):
        return self._payload


def _crossref_items(*titles_and_dois):
    return {"message": {"items": [
        {"DOI": doi, "title": [title]} for title, doi in titles_and_dois
    ]}}


def _with_fake_requests(payload, status_code=200):
    """Swap pp.requests.get for one returning `payload`. Returns a restore fn
    plus the list that captures the params of each call."""
    calls = []

    class _FakeRequests:
        @staticmethod
        def get(url, params=None, headers=None, timeout=None):
            calls.append(params or {})
            return _FakeResponse(payload, status_code)

    original = pp.requests
    pp.requests = _FakeRequests
    return (lambda: setattr(pp, "requests", original)), calls


def test_recovery_accepts_confident_title_match() -> bool:
    pipe = _make_pipeline_stub()
    title = "Pressure Dependence of the Photocycle Kinetics of Bacteriorhodopsin"
    restore, calls = _with_fake_requests(_crossref_items(
        ("Pressure dependence of the photocycle kinetics of bacteriorhodopsin", "10.1016/right"),
        ("Something else entirely about lipids", "10.1016/wrong"),
    ))
    try:
        got = pipe._fetch_crossref_by_title(title, [{"name": "Klink"}])
    finally:
        restore()
    return _check(
        "recovery: confident title match returns the right DOI",
        got is not None and got.get("DOI") == "10.1016/right"
        and calls and calls[0].get("query.author") == "Klink",
        f"got {got!r}, calls={calls!r}",
    )


def test_recovery_rejects_weak_title_match() -> bool:
    """Below the recovery bar we must return None, not the best of a bad
    field. A wrong DOI here is exactly the damage we are undoing."""
    pipe = _make_pipeline_stub()
    restore, _ = _with_fake_requests(_crossref_items(
        ("Regulation of enzyme activity in bacterial systems", "10.1016/nope"),
    ))
    try:
        got = pipe._fetch_crossref_by_title(
            "Conformational selection or induced fit: a flux description", None
        )
    finally:
        restore()
    return _check("recovery: weak match returns None", got is None, f"got {got!r}")


def test_recovery_handles_empty_and_error_responses() -> bool:
    pipe = _make_pipeline_stub()
    restore, _ = _with_fake_requests({"message": {"items": []}})
    try:
        empty = pipe._fetch_crossref_by_title("Some title", None)
    finally:
        restore()
    restore, _ = _with_fake_requests({}, status_code=503)
    try:
        errored = pipe._fetch_crossref_by_title("Some title", None)
    finally:
        restore()
    no_title = pipe._fetch_crossref_by_title("", None)
    return _check(
        "recovery: empty results, HTTP errors and blank titles all return None",
        empty is None and errored is None and no_title is None,
    )


class _FakeQdrant:
    """Minimal stand-in exposing just the retrieve() the guard calls."""

    def __init__(self, payload_by_id=None, raises=False):
        self._by_id, self._raises = payload_by_id or {}, raises

    def retrieve(self, collection_name, ids, with_payload=True, with_vectors=False):
        if self._raises:
            raise RuntimeError("qdrant down")
        out = []
        for i in ids:
            if i in self._by_id:
                out.append(type("R", (), {"payload": self._by_id[i]})())
        return out


def _point_id(doi: str) -> int:
    import hashlib
    return int(hashlib.sha256(doi.lower().encode()).hexdigest()[:16], 16)


def _paper(title: str, doi: str):
    return pp.Paper(id="abcd0123", title=title, abstract="", authors=[],
                    doi=doi, year=2020, journal="J", references=[])


def test_collision_guard_refuses_a_different_paper() -> bool:
    """The backstop: the DOI already holds an unrelated paper."""
    pipe = _make_pipeline_stub()
    doi = "10.1021/bi9714969"
    pipe.qdrant = _FakeQdrant({_point_id(doi): {
        "title": "Time and pH Dependence of the L-to-M Transition in the Photocycle"
    }})
    reason = pipe._collision_reason(_paper("Molecular Basis of Olfactory Receptor Signalling", doi))
    return _check(
        "collision: refuses to overwrite a different paper",
        reason is not None and "doi_collision_different_paper" in reason,
        f"got {reason!r}",
    )


def test_collision_guard_allows_the_same_paper() -> bool:
    """A genuine re-ingest of the same paper must still go through, even
    when the title has been cleaned up between runs."""
    pipe = _make_pipeline_stub()
    doi = "10.7554/elife.57264"
    pipe.qdrant = _FakeQdrant({_point_id(doi): {
        "title": "How to measure and evaluate binding affinities"
    }})
    reason = pipe._collision_reason(
        _paper("How to measure and evaluate <i>binding affinities</i>", doi)
    )
    return _check("collision: same paper re-ingests cleanly", reason is None, f"got {reason!r}")


def test_collision_guard_allows_a_new_doi() -> bool:
    pipe = _make_pipeline_stub()
    pipe.qdrant = _FakeQdrant({})
    reason = pipe._collision_reason(_paper("A brand new paper", "10.1234/new"))
    return _check("collision: unseen DOI is not a collision", reason is None, f"got {reason!r}")


def test_collision_guard_is_inert_without_a_doi() -> bool:
    pipe = _make_pipeline_stub()
    pipe.qdrant = _FakeQdrant({})
    return _check(
        "collision: DOI-less paper short-circuits (dispose quarantines it)",
        pipe._collision_reason(_paper("No DOI here", None)) is None,
    )


def test_collision_guard_fails_open_when_qdrant_errors() -> bool:
    """A Qdrant outage must not block ingest: the guard is a backstop, not a
    gate. The upstream DOI guard is still in force."""
    pipe = _make_pipeline_stub()
    pipe.qdrant = _FakeQdrant(raises=True)
    return _check(
        "collision: Qdrant error fails open",
        pipe._collision_reason(_paper("Any paper", "10.1234/x")) is None,
    )


# ---------------------------------------------------------------------------
# Supporting-information rejection (2026-08-23, found by the R1 canary).
# An SI PDF carries the PARENT paper's title after the marker, so the DOI
# logic resolves the parent's DOI and the SI becomes the canonical record for
# an article it is not.
# ---------------------------------------------------------------------------

def _si_rejected(title: str) -> bool:
    """Mirror of the prefix test in process_pdf."""
    prefixes = (
        "supporting information", "supplementary information",
        "supplementary material", "supplemental material",
        "supplementary materials", "supplemental materials",
        "supporting material", "supporting materials",
        "electronic supplementary material", "supplementary data",
        "supplementary figures", "supplementary tables",
        "supplementary methods", "supplementary notes",
        "supporting text", "supporting figures", "si appendix",
        "appendix s1", "supplementary appendix",
    )
    return title.lower().lstrip().startswith(prefixes)


def test_si_rejects_the_canary_case() -> bool:
    """The exact title that slipped through on 2026-08-23."""
    return _check(
        "SI filter: rejects the jz9b01407_si_001 title",
        _si_rejected(
            "Supporting Information for Hybrid refinement of heterogeneous "
            "conformational ensembles using spectroscopic data"
        ),
    )


def test_si_rejects_common_variants() -> bool:
    variants = [
        "Supporting Information",
        "Supplementary Information for A Study of Things",
        "Supplementary Material",
        "Electronic Supplementary Material (ESI) for Chem Comm",
        "  Supporting Information with leading whitespace",
        "SI Appendix, Materials and Methods",
        "Supplementary Figures and Tables",
    ]
    bad = [v for v in variants if not _si_rejected(v)]
    return _check("SI filter: rejects common variants", not bad, f"missed {bad!r}")


def test_si_does_not_reject_real_papers() -> bool:
    """Anchored at the start so ordinary papers survive, including ones whose
    titles mention supplementary data."""
    keep = [
        "Supporting evidence for a two-state model of GPCR activation",
        "A method for generating supplementary information from sparse data",
        "Structure and dynamics of rhodopsin",
        "Supportive care in oncology: a review",
    ]
    wrong = [t for t in keep if _si_rejected(t)]
    return _check("SI filter: leaves real papers alone", not wrong, f"wrongly rejected {wrong!r}")


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
    # Phase B state sidecar + dispose helper
    test_state_sidecar_path_alongside_pdf,
    test_build_state_sidecar_fresh_record,
    test_build_state_sidecar_preserves_first_seen_across_transitions,
    test_write_and_load_state_sidecar_roundtrip,
    test_load_state_sidecar_missing_returns_none,
    test_dispose_live_writes_sidecar_and_moves_pdf,
    test_dispose_quarantine_when_paper_is_none,
    test_dispose_quarantine_null_doi_deletes_qdrant_point,
    test_dispose_watcher_pdf_already_in_place_no_move,
    # 2026-08-23 supporting-information filter (R1 canary finding)
    test_si_rejects_the_canary_case,
    test_si_rejects_common_variants,
    test_si_does_not_reject_real_papers,
    # 2026-08-18 upload-ingest repair, defect 2
    test_recovery_accepts_confident_title_match,
    test_recovery_rejects_weak_title_match,
    test_recovery_handles_empty_and_error_responses,
    test_collision_guard_refuses_a_different_paper,
    test_collision_guard_allows_the_same_paper,
    test_collision_guard_allows_a_new_doi,
    test_collision_guard_is_inert_without_a_doi,
    test_collision_guard_fails_open_when_qdrant_errors,
    # 2026-08-18 upload-ingest repair, defect 1
    test_corpus_paths_default_to_host_layout,
    test_corpus_paths_follow_container_env,
    test_require_corpus_dirs_exits_on_missing_dir,
    test_require_corpus_dirs_passes_on_real_dirs,
    test_dispose_does_not_create_a_missing_corpus_dir,
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
