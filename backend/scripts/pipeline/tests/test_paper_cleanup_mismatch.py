"""
Unit tests for the metadata-mismatch remediation tool added in
Stage 2 of the 2026-05-12 paper-ingest audit (see
docs/PAPER-INGEST-AUDIT.md).

Pure-function tests. No GROBID, no Qdrant, no PDFs. The
helpers under test are imported via spec_from_file_location so
the module's other side-effects (Qdrant client, Neo4j driver
imports inside functions) don't fire.

Run:
    python scripts/pipeline/tests/test_paper_cleanup_mismatch.py
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
_PIPELINE_DIR = os.path.dirname(_HERE)
_PAPER_CLEANUP_PATH = os.path.join(_PIPELINE_DIR, "paper_cleanup.py")

# paper_cleanup imports paper_pipeline from its own directory, so we
# put the pipeline dir on the path before exec'ing the module.
sys.path.insert(0, _PIPELINE_DIR)

_spec = importlib.util.spec_from_file_location("paper_cleanup_under_test", _PAPER_CLEANUP_PATH)
pc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pc)


def _check(name: str, ok: bool, detail: str = "") -> bool:
    label = "PASS" if ok else "FAIL"
    suffix = f" — {detail}" if detail and not ok else ""
    print(f"[{label}] {name}{suffix}")
    return ok


# ---------------------------------------------------------------------------
# _score_severity — the LIGPLOT case must stay high; running-header case
# must downgrade to medium.
# ---------------------------------------------------------------------------

# _score_severity returns (severity, downgrade_reason). Helper
# that asserts only severity when the reason isn't part of the test.
def _sev(*args, **kwargs) -> str:
    return pc._score_severity(*args, **kwargs)[0]


def test_severity_ligplot_real_mismatch_stays_high() -> bool:
    """LIGPLOT had 8 GROBID tokens against Bar-Hillel's 9 stored. The
    8-token side is well above the 5-token threshold so this remains
    high severity — the production guard would auto-queue it."""
    return _check(
        "severity: LIGPLOT-style (jaccard=0, 8 grobid tokens) -> high",
        _sev(0.0, False, False, 8) == "high",
    )


def test_severity_running_header_downgrades_to_medium() -> bool:
    """Angewandte Chemie Communications layout makes GROBID extract
    a 4-token running header ('Solid-State NMR Spectroscopy') instead
    of the real article title. The header heuristic must drop this
    from high to medium so it isn't auto-queued at the default
    threshold."""
    sev, reason = pc._score_severity(0.0, False, False, 4)
    return _check(
        "severity: running-header (jaccard=0, 4 grobid tokens) -> medium + reason=few_tokens",
        sev == "medium" and reason == "few_tokens",
        f"got ({sev}, {reason!r})",
    )


def test_severity_exactly_5_tokens_promotes_to_high() -> bool:
    """Boundary at >=5 tokens — fewer downgrades, 5+ promotes."""
    return _check(
        "severity: 5 grobid tokens at threshold -> high",
        _sev(0.0, False, False, 5) == "high",
    )


def test_severity_clean_match() -> bool:
    return _check(
        "severity: jaccard=1.0 -> clean",
        _sev(1.0, False, False, 10) == "clean",
    )


def test_severity_ocr_drift_clean() -> bool:
    """The calibration OCR-drift case (sim ~0.818) must score clean,
    not flag a false positive for the user to review."""
    return _check(
        "severity: OCR-drift (jaccard=0.818) -> clean",
        _sev(0.818, False, False, 10) == "clean",
    )


def test_severity_medium_band() -> bool:
    return _check(
        "severity: jaccard in [0.3, 0.5) -> medium",
        _sev(0.4, False, False, 10) == "medium",
    )


def test_severity_html_only_low() -> bool:
    """Title is clean by similarity (>=0.5) but has JATS markup —
    that's still worth flagging at the lowest severity for the
    backfilled `_strip_markup` to clean up over time."""
    return _check(
        "severity: clean similarity + has_html -> low",
        _sev(0.95, True, False, 10) == "low",
    )


def test_severity_grobid_failed_is_unparseable() -> bool:
    return _check(
        "severity: GROBID failed -> unparseable",
        _sev(None, False, True, 0) == "unparseable"
        and _sev(0.0, False, True, 8) == "unparseable",
    )


def test_severity_none_jaccard_is_unparseable() -> bool:
    """When the comparison couldn't run (e.g. stored title or proxy
    text was empty), we bucket as unparseable rather than guessing."""
    return _check(
        "severity: jaccard=None -> unparseable",
        _sev(None, False, False, 0) == "unparseable",
    )


# ---------------------------------------------------------------------------
# Heuristic downgrades layered on the high branch (2026-05-13 refinement).
# ---------------------------------------------------------------------------

def test_severity_doi_match_downgrades() -> bool:
    """Real-world case (Nature s41560-024-01518-6): GROBID extracted
    the same DOI that's already stored, but the title looks different
    because the PDF is the Research Briefing variant. DOI match
    confirms the record's claimed identity is correct."""
    sev, reason = pc._score_severity(
        0.211, False, False, 12, doi_matches_grobid=True
    )
    return _check(
        "severity: doi_matches_grobid -> medium + reason=doi_match",
        sev == "medium" and reason == "doi_match",
        f"got ({sev}, {reason!r})",
    )


def test_severity_few_tokens_takes_precedence_over_doi_match() -> bool:
    """When both heuristics could fire, ``few_tokens`` (the cheapest
    check) is the reported reason — easier to diagnose."""
    sev, reason = pc._score_severity(
        0.0, False, False, 2, doi_matches_grobid=True
    )
    return _check(
        "severity: few_tokens reported when both fire",
        sev == "medium" and reason == "few_tokens",
        f"got ({sev}, {reason!r})",
    )


def test_severity_masthead_downgrades() -> bool:
    """The S0040-4039 case from the first crawler pass: GROBID
    extracted '0040-4039/88 $3.00 ... Pergamon Press plc 2-MERCAPTO...'
    — masthead + real title concatenated. Jaccard is diluted by the
    masthead tokens; without this heuristic the record would auto-
    queue at the default high threshold."""
    sev, reason = pc._score_severity(
        0.263, False, False, 14, grobid_title_has_masthead=True
    )
    return _check(
        "severity: masthead -> medium + reason=masthead",
        sev == "medium" and reason == "masthead",
        f"got ({sev}, {reason!r})",
    )


def test_severity_high_still_fires_for_real_mismatch_without_heuristic() -> bool:
    """Sanity check: a LIGPLOT-style real mismatch (8 GROBID tokens,
    no DOI match, no masthead markers) still reaches `high` so the
    queue auto-action remains useful."""
    sev, reason = pc._score_severity(
        0.0, False, False, 8,
        doi_matches_grobid=False,
        grobid_title_has_masthead=False,
    )
    return _check(
        "severity: real mismatch still scores high with no heuristic fire",
        sev == "high" and reason == "",
        f"got ({sev}, {reason!r})",
    )


# ---------------------------------------------------------------------------
# _looks_like_masthead — pattern detector for GROBID-extracted noise.
# ---------------------------------------------------------------------------

def test_masthead_issn_year_prefix() -> bool:
    return _check(
        "masthead: ISSN-with-year prefix (0040-4039/88)",
        pc._looks_like_masthead(
            "0040-4039/88 $3.00 + .OO Printed in Great Britain Pergamon Press plc"
        ) is True,
    )


def test_masthead_letters_to_nature() -> bool:
    return _check(
        "masthead: 'letters to nature' (the s41560 case from audit)",
        pc._looks_like_masthead("letters to nature 704") is True
        and pc._looks_like_masthead("Letters to Nature") is True,
    )


def test_masthead_publishers_and_research_article() -> bool:
    return _check(
        "masthead: publisher names + 'Research Article'",
        pc._looks_like_masthead("Wiley-VCH RESEARCH ARTICLE") is True
        and pc._looks_like_masthead("Royal Society of Chemistry") is True
        and pc._looks_like_masthead("View Article Online published by RSC") is True,
    )


def test_masthead_volume_page_citation() -> bool:
    return _check(
        "masthead: volume/page citation patterns",
        pc._looks_like_masthead("Biochemistry, Vol. 25, pp. 7470-7476") is True,
    )


def test_masthead_clean_title_passes() -> bool:
    """Real article titles must NOT trigger the heuristic."""
    return _check(
        "masthead: clean titles return False",
        pc._looks_like_masthead(
            "LIGPLOT: a program to generate schematic diagrams of protein-ligand interactions"
        ) is False
        and pc._looks_like_masthead("How to measure and evaluate binding affinities") is False,
    )


def test_masthead_empty_input() -> bool:
    return _check(
        "masthead: empty / None safe",
        pc._looks_like_masthead("") is False
        and pc._looks_like_masthead(None) is False,
    )


def test_masthead_only_scans_prefix() -> bool:
    """Masthead-like text deep inside a long real title (>120 chars in)
    shouldn't trigger the heuristic — only the leading region is
    scanned."""
    long_clean = "x" * 130 + " Printed in Great Britain"
    return _check(
        "masthead: deep matches past first ~120 chars are ignored",
        pc._looks_like_masthead(long_clean) is False,
    )


# ---------------------------------------------------------------------------
# _container_pdf_to_host — path translation between Qdrant payload and disk.
# ---------------------------------------------------------------------------

def test_container_path_mapped_to_host_when_file_exists() -> bool:
    """`/papers/X.pdf` (the path stored on Qdrant payloads, written
    from inside the retrieval container's mount) maps to
    `/opt/munin/data/papers/pdf/X.pdf` on the host."""
    with tempfile.TemporaryDirectory() as td:
        # Simulate the host pdf dir by temporarily overriding PDF_DIR.
        orig_pdf_dir = pc.PDF_DIR
        pc.PDF_DIR = pc.Path(td)
        try:
            (pc.PDF_DIR / "doi_10.1234_test.pdf").write_text("fake")
            got = pc._container_pdf_to_host("/papers/doi_10.1234_test.pdf")
            ok = got is not None and got.is_file() and got.name == "doi_10.1234_test.pdf"
        finally:
            pc.PDF_DIR = orig_pdf_dir
    return _check(
        "container_path: /papers/X.pdf -> host file",
        ok,
    )


def test_container_path_returns_none_when_file_missing() -> bool:
    with tempfile.TemporaryDirectory() as td:
        orig_pdf_dir = pc.PDF_DIR
        pc.PDF_DIR = pc.Path(td)
        try:
            # No file written; expect None
            got = pc._container_pdf_to_host("/papers/doi_10.1_nothing.pdf")
        finally:
            pc.PDF_DIR = orig_pdf_dir
    return _check(
        "container_path: missing file -> None",
        got is None,
    )


def test_container_path_empty_input_returns_none() -> bool:
    return _check(
        "container_path: empty / None input -> None",
        pc._container_pdf_to_host("") is None
        and pc._container_pdf_to_host(None) is None,
    )


def test_container_path_non_container_prefix_unchanged_check() -> bool:
    """A path that doesn't start with `/papers/` is treated as a
    raw host path. Still returns None when the file doesn't exist."""
    return _check(
        "container_path: non-/papers/ prefix treated as raw",
        pc._container_pdf_to_host("/tmp/definitely-not-a-real-pdf-zzz.pdf") is None,
    )


# ---------------------------------------------------------------------------
# Module-level constant matches the audit document.
# ---------------------------------------------------------------------------

def test_threshold_constant_matches_pipeline() -> bool:
    """MISMATCH_THRESHOLD in paper_cleanup must equal the production
    guard's threshold in paper_pipeline — duplicating the value across
    modules invites drift. The audit doc pins it at 0.3."""
    # Re-load paper_pipeline through the same spec mechanism to avoid
    # any global caching surprises.
    pp_spec = importlib.util.spec_from_file_location(
        "pp_threshold_check",
        os.path.join(_PIPELINE_DIR, "paper_pipeline.py"),
    )
    pp = importlib.util.module_from_spec(pp_spec)
    pp_spec.loader.exec_module(pp)
    return _check(
        "thresholds: cleanup MISMATCH_THRESHOLD == pipeline _MERGE_TITLE_SIM_THRESHOLD",
        pc.MISMATCH_THRESHOLD == pp.PaperPipeline._MERGE_TITLE_SIM_THRESHOLD == 0.3,
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

TESTS = [
    test_severity_ligplot_real_mismatch_stays_high,
    test_severity_running_header_downgrades_to_medium,
    test_severity_exactly_5_tokens_promotes_to_high,
    test_severity_clean_match,
    test_severity_ocr_drift_clean,
    test_severity_medium_band,
    test_severity_html_only_low,
    test_severity_grobid_failed_is_unparseable,
    test_severity_none_jaccard_is_unparseable,
    test_severity_doi_match_downgrades,
    test_severity_few_tokens_takes_precedence_over_doi_match,
    test_severity_masthead_downgrades,
    test_severity_high_still_fires_for_real_mismatch_without_heuristic,
    test_masthead_issn_year_prefix,
    test_masthead_letters_to_nature,
    test_masthead_publishers_and_research_article,
    test_masthead_volume_page_citation,
    test_masthead_clean_title_passes,
    test_masthead_empty_input,
    test_masthead_only_scans_prefix,
    test_container_path_mapped_to_host_when_file_exists,
    test_container_path_returns_none_when_file_missing,
    test_container_path_empty_input_returns_none,
    test_container_path_non_container_prefix_unchanged_check,
    test_threshold_constant_matches_pipeline,
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
