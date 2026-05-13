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

# ---------------------------------------------------------------------------
# Phase E: review subcommand (interactive quarantine triage)
# ---------------------------------------------------------------------------

def _seed_quarantine_record(quar: "pc.Path", uuid: str, doi: str | None,
                             reasons: list, audit: dict | None = None) -> "pc.Path":
    """Drop a fake PDF + state sidecar into `quar` representing a
    quarantined record. Returns the sidecar path."""
    import json as _json
    quar.mkdir(parents=True, exist_ok=True)
    pdf = quar / f"{uuid}.pdf"
    pdf.write_text("fake-pdf-bytes")
    sc = quar / f"{uuid}.state.json"
    _json.dump({
        "schema_version": 1,
        "doi": doi,
        "state": "quarantine",
        "ingest_path": "upload",
        "quarantine_reasons": reasons,
        "first_seen_at": "2026-05-01T10:00:00Z",
        "last_modified_at": "2026-05-13T12:00:00Z",
        "contributor": None,
        "audit_findings": audit,
        "history": [{
            "at": "2026-05-13T12:00:00Z",
            "state": "quarantine",
            "via": "test",
            "reason": reasons[0] if reasons else None,
        }],
    }, open(sc, "w"))
    return sc


def test_review_empty_quarantine_returns_zero() -> bool:
    import tempfile, pathlib
    with tempfile.TemporaryDirectory() as td:
        orig = pc.PDF_DIR
        pc.PDF_DIR = pathlib.Path(td)
        try:
            rc = pc.review(prompt_fn=lambda *_: "q")
        finally:
            pc.PDF_DIR = orig
    return _check(
        "review: empty quarantine returns rc=0 with no prompts",
        rc == 0,
    )


def test_review_reject_updates_sidecar_state() -> bool:
    """'r' (reject) flips state to 'rejected' and appends history."""
    import tempfile, pathlib, json as _json
    with tempfile.TemporaryDirectory() as td:
        orig = pc.PDF_DIR
        pc.PDF_DIR = pathlib.Path(td) / "pdf"
        try:
            quar = pc.PDF_DIR / "quarantine"
            sc = _seed_quarantine_record(quar, "rec1", "10.1/x",
                                         ["title_mismatch_with_crossref"])
            responses = iter(["r", "q"])
            pc.review(prompt_fn=lambda *_: next(responses))
            updated = _json.load(open(sc))
        finally:
            pc.PDF_DIR = orig
    return _check(
        "review: 'r' (reject) sets state=rejected and appends history",
        updated["state"] == "rejected"
        and len(updated["history"]) == 2
        and updated["history"][-1]["state"] == "rejected"
        and updated["history"][-1]["via"] == "review",
    )


def test_review_skip_leaves_sidecar_unchanged() -> bool:
    """'s' (skip) doesn't write anything to the sidecar."""
    import tempfile, pathlib, json as _json
    with tempfile.TemporaryDirectory() as td:
        orig = pc.PDF_DIR
        pc.PDF_DIR = pathlib.Path(td) / "pdf"
        try:
            quar = pc.PDF_DIR / "quarantine"
            sc = _seed_quarantine_record(quar, "rec1", "10.1/x",
                                         ["title_mismatch_with_crossref"])
            before = _json.load(open(sc))
            responses = iter(["s", "q"])
            pc.review(prompt_fn=lambda *_: next(responses))
            after = _json.load(open(sc))
        finally:
            pc.PDF_DIR = orig
    return _check(
        "review: 's' (skip) leaves sidecar unchanged",
        before == after,
    )


def test_review_keep_moves_pdf_back_and_clears_sidecar() -> bool:
    """'k' (keep) moves PDF back to PDF_DIR and removes the state
    sidecar so the watcher re-ingests it."""
    import tempfile, pathlib
    with tempfile.TemporaryDirectory() as td:
        orig_pdf = pc.PDF_DIR
        orig_proc = pc.PROCESSED_DIR
        pc.PDF_DIR = pathlib.Path(td) / "pdf"
        pc.PROCESSED_DIR = pathlib.Path(td) / "processed"
        pc.PROCESSED_DIR.mkdir(parents=True)
        try:
            quar = pc.PDF_DIR / "quarantine"
            sc = _seed_quarantine_record(quar, "doi_10.1_keep", "10.1/keep", ["test"])
            responses = iter(["k", "q"])
            pc.review(prompt_fn=lambda *_: next(responses))

            moved_pdf = pc.PDF_DIR / "doi_10.1_keep.pdf"
            old_pdf = quar / "doi_10.1_keep.pdf"
            ok = (
                moved_pdf.is_file()
                and not old_pdf.is_file()
                and not sc.is_file()
            )
        finally:
            pc.PDF_DIR = orig_pdf
            pc.PROCESSED_DIR = orig_proc
    return _check(
        "review: 'k' (keep) moves PDF back to PDF_DIR and drops sidecar",
        ok,
    )


def test_review_dry_run_is_read_only() -> bool:
    """With --dry-run, 'r' prints what it would do but doesn't modify
    the sidecar."""
    import tempfile, pathlib, json as _json
    with tempfile.TemporaryDirectory() as td:
        orig = pc.PDF_DIR
        pc.PDF_DIR = pathlib.Path(td) / "pdf"
        try:
            quar = pc.PDF_DIR / "quarantine"
            sc = _seed_quarantine_record(quar, "rec1", "10.1/x", ["test"])
            before = _json.load(open(sc))
            responses = iter(["r", "q"])
            pc.review(prompt_fn=lambda *_: next(responses), dry_run=True)
            after = _json.load(open(sc))
        finally:
            pc.PDF_DIR = orig
    return _check(
        "review: dry-run leaves sidecar unchanged on reject",
        before == after,
    )


def test_review_quit_short_circuits() -> bool:
    """Choosing 'q' on the first record stops the loop without
    visiting the second."""
    import tempfile, pathlib, json as _json
    with tempfile.TemporaryDirectory() as td:
        orig = pc.PDF_DIR
        pc.PDF_DIR = pathlib.Path(td) / "pdf"
        try:
            quar = pc.PDF_DIR / "quarantine"
            sc1 = _seed_quarantine_record(quar, "rec1", "10.1/a", ["test"])
            sc2 = _seed_quarantine_record(quar, "rec2", "10.1/b", ["test"])
            before1 = _json.load(open(sc1))
            before2 = _json.load(open(sc2))
            pc.review(prompt_fn=lambda *_: "q")
            ok = (
                _json.load(open(sc1)) == before1
                and _json.load(open(sc2)) == before2
            )
        finally:
            pc.PDF_DIR = orig
    return _check(
        "review: 'q' (quit) stops immediately, no records touched",
        ok,
    )


def test_review_non_interactive_prints_no_prompts() -> bool:
    """--non-interactive emits per-record summaries but never calls
    the prompt function."""
    import tempfile, pathlib
    with tempfile.TemporaryDirectory() as td:
        orig = pc.PDF_DIR
        pc.PDF_DIR = pathlib.Path(td) / "pdf"
        try:
            quar = pc.PDF_DIR / "quarantine"
            _seed_quarantine_record(quar, "rec1", "10.1/x", ["test"])
            calls = []
            def fail_if_called(*_):
                calls.append("called")
                return "q"
            rc = pc.review(prompt_fn=fail_if_called, non_interactive=True)
        finally:
            pc.PDF_DIR = orig
    return _check(
        "review: --non-interactive doesn't prompt",
        rc == 0 and calls == [],
    )


def test_review_skips_already_rejected() -> bool:
    """Records whose state is already 'rejected' are not re-shown."""
    import tempfile, pathlib, json as _json
    with tempfile.TemporaryDirectory() as td:
        orig = pc.PDF_DIR
        pc.PDF_DIR = pathlib.Path(td) / "pdf"
        try:
            quar = pc.PDF_DIR / "quarantine"
            sc = _seed_quarantine_record(quar, "rec1", "10.1/x", ["test"])
            # Flip it to rejected directly on disk to simulate a
            # prior review session.
            doc = _json.load(open(sc))
            doc["state"] = "rejected"
            _json.dump(doc, open(sc, "w"))
            # Build a prompt_fn that records calls.
            calls = []
            def stub_prompt(*_):
                calls.append("prompted")
                return "q"
            rc = pc.review(prompt_fn=stub_prompt)
        finally:
            pc.PDF_DIR = orig
    return _check(
        "review: rejected records are skipped",
        rc == 0 and calls == [],
    )


# ---------------------------------------------------------------------------
# Phase D: detect orchestrator + auto-quarantine helpers
# ---------------------------------------------------------------------------

def test_detect_rejects_unknown_kind() -> bool:
    rc = pc.detect(kinds=["totally-not-a-real-kind"])
    return _check(
        "detect: unknown --kinds returns rc=2",
        rc == 2,
        f"got {rc}",
    )


def test_auto_quarantine_csv_only_acts_on_high_by_default() -> bool:
    """CSV with mixed severities → only severity=high gets
    quarantined under the default threshold."""
    import csv as _csv
    import tempfile, pathlib
    calls = []
    orig = pc._quarantine_by_doi
    pc._quarantine_by_doi = lambda doi, reason, audit_findings=None, dry_run=False: (
        calls.append((doi, reason, dry_run)) or True
    )
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv",
                                         delete=False, newline="") as tf:
            writer = _csv.DictWriter(tf, fieldnames=[
                "doi", "severity", "jaccard", "grobid_title", "downgrade_reason"
            ])
            writer.writeheader()
            writer.writerow({"doi": "10.1/high", "severity": "high",
                             "jaccard": "0.1", "grobid_title": "X",
                             "downgrade_reason": ""})
            writer.writerow({"doi": "10.1/medium", "severity": "medium",
                             "jaccard": "0.35", "grobid_title": "Y",
                             "downgrade_reason": ""})
            writer.writerow({"doi": "10.1/clean", "severity": "clean",
                             "jaccard": "1.0", "grobid_title": "Z",
                             "downgrade_reason": ""})
            tf.flush()
            path = pathlib.Path(tf.name)
        try:
            n = pc._auto_quarantine_from_mismatch_csv(path, dry_run=True)
        finally:
            path.unlink()
    finally:
        pc._quarantine_by_doi = orig

    quarantined = {c[0] for c in calls}
    return _check(
        "detect: auto-quarantine on mismatch CSV defaults to severity=high only",
        n == 1 and quarantined == {"10.1/high"},
        f"got n={n} quarantined={quarantined}",
    )


def test_auto_quarantine_csv_medium_threshold_includes_medium() -> bool:
    """severity-threshold=medium quarantines high+medium."""
    import csv as _csv
    import tempfile, pathlib
    calls = []
    orig = pc._quarantine_by_doi
    pc._quarantine_by_doi = lambda doi, reason, audit_findings=None, dry_run=False: (
        calls.append((doi, reason, dry_run)) or True
    )
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv",
                                         delete=False, newline="") as tf:
            writer = _csv.DictWriter(tf, fieldnames=[
                "doi", "severity", "jaccard", "grobid_title", "downgrade_reason"
            ])
            writer.writeheader()
            for doi, sev in [("10.1/h", "high"), ("10.1/m", "medium"),
                             ("10.1/c", "clean")]:
                writer.writerow({"doi": doi, "severity": sev,
                                 "jaccard": "0", "grobid_title": "",
                                 "downgrade_reason": ""})
            tf.flush()
            path = pathlib.Path(tf.name)
        try:
            n = pc._auto_quarantine_from_mismatch_csv(
                path, dry_run=True, severity_threshold="medium")
        finally:
            path.unlink()
    finally:
        pc._quarantine_by_doi = orig

    return _check(
        "detect: severity-threshold=medium includes both high and medium",
        n == 2 and {c[0] for c in calls} == {"10.1/h", "10.1/m"},
    )


def test_auto_quarantine_doi_list_skips_comments() -> bool:
    """find-low-quality / find-short produce simple DOI lists; the
    consumer must skip comment + blank lines."""
    import tempfile, pathlib
    calls = []
    orig = pc._quarantine_by_doi
    pc._quarantine_by_doi = lambda doi, reason, audit_findings=None, dry_run=False: (
        calls.append((doi, reason, dry_run)) or True
    )
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt",
                                         delete=False) as tf:
            tf.write("# header comment\n")
            tf.write("\n")
            tf.write("10.1/first\n")
            tf.write("10.1/second  trailing-junk\n")
            tf.write("   # leading-whitespace comment\n")
            tf.write("10.1/third\n")
            tf.flush()
            path = pathlib.Path(tf.name)
        try:
            n = pc._auto_quarantine_from_doi_list(
                path, reason="testing", limit=None, dry_run=True)
        finally:
            path.unlink()
    finally:
        pc._quarantine_by_doi = orig

    quarantined = [c[0] for c in calls]
    return _check(
        "auto_quarantine_from_doi_list: skips comments + blanks",
        n == 3
        and quarantined == ["10.1/first", "10.1/second", "10.1/third"],
        f"got n={n} quarantined={quarantined}",
    )


def test_auto_quarantine_doi_list_respects_limit() -> bool:
    """Stop after `limit` records to mirror the legacy --auto-remove --limit cap."""
    import tempfile, pathlib
    calls = []
    orig = pc._quarantine_by_doi
    pc._quarantine_by_doi = lambda doi, reason, audit_findings=None, dry_run=False: (
        calls.append((doi, reason, dry_run)) or True
    )
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt",
                                         delete=False) as tf:
            for i in range(10):
                tf.write(f"10.1/x{i}\n")
            tf.flush()
            path = pathlib.Path(tf.name)
        try:
            n = pc._auto_quarantine_from_doi_list(
                path, reason="testing", limit=3, dry_run=True)
        finally:
            path.unlink()
    finally:
        pc._quarantine_by_doi = orig

    return _check(
        "auto_quarantine_from_doi_list: respects --limit",
        n == 3 and len(calls) == 3,
    )


def test_quarantine_helper_missing_pdf_returns_false() -> bool:
    """When the PDF can't be located on disk, the quarantine helper
    must abort cleanly rather than partially-mutating state."""
    import tempfile, pathlib
    orig_pdf_dir = pc.PDF_DIR
    pc.PDF_DIR = pathlib.Path("/tmp/nonexistent-quarantine-helper-test-xyz")
    try:
        ok = pc._quarantine_by_doi(
            "10.1/no-such-paper", reason="testing", dry_run=False,
        )
    finally:
        pc.PDF_DIR = orig_pdf_dir
    return _check(
        "quarantine_by_doi: missing PDF -> returns False, no mutation",
        ok is False,
    )


def test_quarantine_helper_dry_run_returns_true_without_side_effects() -> bool:
    """Dry-run claims success without touching anything."""
    ok = pc._quarantine_by_doi("10.1/test", reason="testing", dry_run=True)
    return _check(
        "quarantine_by_doi: dry-run returns True with no side effects",
        ok is True,
    )


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
    # Phase D
    test_detect_rejects_unknown_kind,
    test_auto_quarantine_csv_only_acts_on_high_by_default,
    test_auto_quarantine_csv_medium_threshold_includes_medium,
    test_auto_quarantine_doi_list_skips_comments,
    test_auto_quarantine_doi_list_respects_limit,
    test_quarantine_helper_missing_pdf_returns_false,
    test_quarantine_helper_dry_run_returns_true_without_side_effects,
    # Phase E
    test_review_empty_quarantine_returns_zero,
    test_review_reject_updates_sidecar_state,
    test_review_skip_leaves_sidecar_unchanged,
    test_review_keep_moves_pdf_back_and_clears_sidecar,
    test_review_dry_run_is_read_only,
    test_review_quit_short_circuits,
    test_review_non_interactive_prints_no_prompts,
    test_review_skips_already_rejected,
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
