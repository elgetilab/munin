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

def test_severity_ligplot_real_mismatch_stays_high() -> bool:
    """LIGPLOT had 8 GROBID tokens against Bar-Hillel's 9 stored. The
    8-token side is well above the 5-token threshold so this remains
    high severity — the production guard would auto-queue it."""
    return _check(
        "severity: LIGPLOT-style (jaccard=0, 8 grobid tokens) -> high",
        pc._score_severity(0.0, False, False, 8) == "high",
    )


def test_severity_running_header_downgrades_to_medium() -> bool:
    """Angewandte Chemie Communications layout makes GROBID extract
    a 4-token running header ('Solid-State NMR Spectroscopy') instead
    of the real article title. The header heuristic must drop this
    from high to medium so it isn't auto-queued at the default
    threshold."""
    return _check(
        "severity: running-header (jaccard=0, 4 grobid tokens) -> medium",
        pc._score_severity(0.0, False, False, 4) == "medium",
    )


def test_severity_exactly_5_tokens_promotes_to_high() -> bool:
    """Boundary at >=5 tokens — fewer downgrades, 5+ promotes."""
    return _check(
        "severity: 5 grobid tokens at threshold -> high",
        pc._score_severity(0.0, False, False, 5) == "high",
    )


def test_severity_clean_match() -> bool:
    return _check(
        "severity: jaccard=1.0 -> clean",
        pc._score_severity(1.0, False, False, 10) == "clean",
    )


def test_severity_ocr_drift_clean() -> bool:
    """The calibration OCR-drift case (sim ~0.818) must score clean,
    not flag a false positive for the user to review."""
    return _check(
        "severity: OCR-drift (jaccard=0.818) -> clean",
        pc._score_severity(0.818, False, False, 10) == "clean",
    )


def test_severity_medium_band() -> bool:
    return _check(
        "severity: jaccard in [0.3, 0.5) -> medium",
        pc._score_severity(0.4, False, False, 10) == "medium",
    )


def test_severity_html_only_low() -> bool:
    """Title is clean by similarity (>=0.5) but has JATS markup —
    that's still worth flagging at the lowest severity for the
    backfilled `_strip_markup` to clean up over time."""
    return _check(
        "severity: clean similarity + has_html -> low",
        pc._score_severity(0.95, True, False, 10) == "low",
    )


def test_severity_grobid_failed_is_unparseable() -> bool:
    return _check(
        "severity: GROBID failed -> unparseable",
        pc._score_severity(None, False, True, 0) == "unparseable"
        and pc._score_severity(0.0, False, True, 8) == "unparseable",
    )


def test_severity_none_jaccard_is_unparseable() -> bool:
    """When the comparison couldn't run (e.g. stored title or proxy
    text was empty), we bucket as unparseable rather than guessing."""
    return _check(
        "severity: jaccard=None -> unparseable",
        pc._score_severity(None, False, False, 0) == "unparseable",
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
