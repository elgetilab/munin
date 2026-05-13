"""
Integration test for the Phase C quarantine-layout migration.

Builds a fake legacy layout (one entry per source: pipeline-skipped
log + admin/ingest skipped triple + admin/ingest failed triple),
runs the migration in --commit mode pointing at a tempdir, and
verifies that the state sidecars look right + source directories
end up empty.

Pure Python (no Qdrant, no subprocess). Run:
    python scripts/pipeline/tests/test_migrate_quarantine_layout.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
_PIPELINE_DIR = os.path.dirname(_HERE)
_MIGRATE_PATH = os.path.join(_PIPELINE_DIR, "migrate_quarantine_layout.py")

sys.path.insert(0, _PIPELINE_DIR)


def _check(name: str, ok: bool, detail: str = "") -> bool:
    label = "PASS" if ok else "FAIL"
    suffix = f" — {detail}" if detail and not ok else ""
    print(f"[{label}] {name}{suffix}")
    return ok


def _load_migrate_with_tmp_dirs(tmp_root: Path):
    """Import the migration module with its DATA_DIR pointed at the
    tempdir, so we don't touch /opt/munin/data during tests."""
    os.environ["MUNIN_DATA_DIR"] = str(tmp_root)
    spec = importlib.util.spec_from_file_location(
        "migrate_under_test", _MIGRATE_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _seed_legacy_layout(papers_dir: Path) -> None:
    """Create one entry per legacy source under `papers_dir`."""
    pdf_dir = papers_dir / "pdf"
    legacy_pipeline = papers_dir / "skipped"
    legacy_ingest_skipped = pdf_dir / "skipped"
    legacy_ingest_failed = pdf_dir / "failed"
    for d in [pdf_dir, legacy_pipeline, legacy_ingest_skipped, legacy_ingest_failed]:
        d.mkdir(parents=True, exist_ok=True)

    # ---- Source 1: pipeline-side log + the live PDF it refers to.
    live_pdf = pdf_dir / "doi_10.1234_skipped_by_pipeline.pdf"
    live_pdf.write_text("fake-pdf-bytes-source-1")
    log = legacy_pipeline / "doi_10.1234_skipped_by_pipeline.json"
    log.write_text(json.dumps({
        "pdf_path": str(live_pdf),
        "reason": "Quality filter: Only 1 page(s) (minimum: 3)",
        "skipped_at": "2026-04-27T18:49:37.724786",
    }))

    # ---- Source 2: admin/ingest skipped (3-file triple).
    uuid_skipped = "uuidskipped00001"
    p2_pdf = legacy_ingest_skipped / f"{uuid_skipped}.pdf"
    p2_pdf.write_text("fake-pdf-bytes-source-2")
    (legacy_ingest_skipped / f"{uuid_skipped}.contributor.json").write_text(json.dumps({
        "contributor_email": "alice@example.org",
        "contributor_username": "alice",
        "research_group": "alpha",
        "uploaded_at": "2026-04-22T12:00:00Z",
        "original_filename": "alice_paper.pdf",
    }))
    (legacy_ingest_skipped / f"{uuid_skipped}.skip_info.json").write_text(json.dumps({
        "outcome": "doi_extraction_failed",
        "reason": "Pipeline produced a Qdrant point but no DOI was extracted.",
        "timestamp": "2026-04-22T12:00:30Z",
        "email": "alice@example.org",
        "group_slug": "alpha",
        "original_filename": "alice_paper.pdf",
        "qdrant_point_id_deleted": 12345,
    }))

    # ---- Source 3: admin/ingest failed (timeout case).
    uuid_failed = "uuidfailed000001"
    p3_pdf = legacy_ingest_failed / f"{uuid_failed}.pdf"
    p3_pdf.write_text("fake-pdf-bytes-source-3")
    (legacy_ingest_failed / f"{uuid_failed}.contributor.json").write_text(json.dumps({
        "contributor_email": "bob@example.org",
        "research_group": "beta",
        "uploaded_at": "2026-04-15T08:00:00Z",
        "original_filename": "bob_giant_paper.pdf",
    }))
    (legacy_ingest_failed / f"{uuid_failed}.skip_info.json").write_text(json.dumps({
        "outcome": "timeout",
        "reason": "Pipeline timed out after 600s",
        "timestamp": "2026-04-15T08:10:00Z",
        "email": "bob@example.org",
        "group_slug": "beta",
        "original_filename": "bob_giant_paper.pdf",
    }))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_dry_run_is_read_only() -> bool:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        papers = root / "papers"
        _seed_legacy_layout(papers)
        mod = _load_migrate_with_tmp_dirs(root)

        # Run dry-run by calling the per-source helpers directly with
        # dry_run=True (parses the same code paths as the CLI).
        mod.migrate_pipeline_skipped(dry_run=True)
        mod.migrate_ingest_quarantine(mod.LEGACY_INGEST_SKIPPED, dry_run=True)
        mod.migrate_ingest_quarantine(mod.LEGACY_INGEST_FAILED, dry_run=True)

        quar = papers / "pdf" / "quarantine"
        # Sources must be unchanged; quarantine empty
        sources_unchanged = (
            (papers / "skipped" / "doi_10.1234_skipped_by_pipeline.json").is_file()
            and (papers / "pdf" / "skipped" / "uuidskipped00001.pdf").is_file()
            and (papers / "pdf" / "failed" / "uuidfailed000001.pdf").is_file()
        )
        target_empty = not quar.exists() or not any(quar.iterdir())
        ok = sources_unchanged and target_empty
    return _check("dry-run is read-only (sources untouched, target empty)", ok)


def test_commit_moves_pipeline_skipped_pdf_and_writes_sidecar() -> bool:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        papers = root / "papers"
        _seed_legacy_layout(papers)
        mod = _load_migrate_with_tmp_dirs(root)

        mod.migrate_pipeline_skipped(dry_run=False)

        quar = papers / "pdf" / "quarantine"
        moved_pdf = quar / "doi_10.1234_skipped_by_pipeline.pdf"
        sidecar = quar / "doi_10.1234_skipped_by_pipeline.state.json"
        log_gone = not (papers / "skipped" / "doi_10.1234_skipped_by_pipeline.json").is_file()
        sidecar_data = json.loads(sidecar.read_text()) if sidecar.is_file() else {}
        ok = (
            moved_pdf.is_file()
            and sidecar.is_file()
            and log_gone
            and sidecar_data.get("state") == "quarantine"
            and sidecar_data.get("ingest_path") == "crawler"
            and sidecar_data.get("first_seen_at") == "2026-04-27T18:49:37.724786"
            and len(sidecar_data.get("history") or []) == 1
            and sidecar_data["history"][0]["reason"].startswith("legacy:Quality filter")
        )
    return _check(
        "commit moves source-1 PDF + writes quarantine sidecar with legacy timestamp",
        ok,
    )


def test_commit_handles_ingest_skipped_triple() -> bool:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        papers = root / "papers"
        _seed_legacy_layout(papers)
        mod = _load_migrate_with_tmp_dirs(root)

        mod.migrate_ingest_quarantine(mod.LEGACY_INGEST_SKIPPED, dry_run=False)

        quar = papers / "pdf" / "quarantine"
        moved_pdf = quar / "uuidskipped00001.pdf"
        moved_contrib = quar / "uuidskipped00001.contributor.json"
        sidecar = quar / "uuidskipped00001.state.json"
        skip_info_gone = not (papers / "pdf" / "skipped" / "uuidskipped00001.skip_info.json").is_file()
        sidecar_data = json.loads(sidecar.read_text()) if sidecar.is_file() else {}
        ok = (
            moved_pdf.is_file()
            and moved_contrib.is_file()
            and sidecar.is_file()
            and skip_info_gone
            and sidecar_data.get("state") == "quarantine"
            and sidecar_data.get("ingest_path") == "upload"
            and sidecar_data.get("quarantine_reasons") == ["doi_extraction_failed"]
            and sidecar_data.get("first_seen_at") == "2026-04-22T12:00:30Z"
            # contributor field is carried through from the loaded JSON
            and (sidecar_data.get("contributor") or {}).get("contributor_email") == "alice@example.org"
            and (sidecar_data.get("legacy_skip_info") or {}).get("qdrant_point_id_deleted") == 12345
        )
    return _check(
        "commit moves source-2 PDF + contributor + writes sidecar",
        ok,
    )


def test_commit_handles_ingest_failed_with_timeout_outcome() -> bool:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        papers = root / "papers"
        _seed_legacy_layout(papers)
        mod = _load_migrate_with_tmp_dirs(root)

        mod.migrate_ingest_quarantine(mod.LEGACY_INGEST_FAILED, dry_run=False)

        quar = papers / "pdf" / "quarantine"
        sidecar = quar / "uuidfailed000001.state.json"
        sidecar_data = json.loads(sidecar.read_text()) if sidecar.is_file() else {}
        ok = (
            sidecar_data.get("state") == "quarantine"
            and sidecar_data.get("quarantine_reasons") == ["pipeline_timeout"]
            and (sidecar_data.get("legacy_skip_info") or {}).get("original_filename") == "bob_giant_paper.pdf"
        )
    return _check(
        "commit maps 'timeout' outcome to canonical pipeline_timeout reason",
        ok,
    )


def test_idempotent_rerun_is_safe() -> bool:
    """Running --commit twice should not double-move or corrupt the
    quarantine state. The second pass finds the source PDFs gone and
    silently does nothing for those entries."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        papers = root / "papers"
        _seed_legacy_layout(papers)
        mod = _load_migrate_with_tmp_dirs(root)

        # First pass migrates everything.
        mod.migrate_pipeline_skipped(dry_run=False)
        mod.migrate_ingest_quarantine(mod.LEGACY_INGEST_SKIPPED, dry_run=False)
        mod.migrate_ingest_quarantine(mod.LEGACY_INGEST_FAILED, dry_run=False)

        # Second pass: sources are now empty so each function should
        # report considered=0 / pdf_moved=0.
        c1 = mod.migrate_pipeline_skipped(dry_run=False)
        c2 = mod.migrate_ingest_quarantine(mod.LEGACY_INGEST_SKIPPED, dry_run=False)
        c3 = mod.migrate_ingest_quarantine(mod.LEGACY_INGEST_FAILED, dry_run=False)

        # Sidecars from the first pass should still be intact.
        quar = papers / "pdf" / "quarantine"
        sidecar1 = quar / "doi_10.1234_skipped_by_pipeline.state.json"
        sidecar2 = quar / "uuidskipped00001.state.json"
        ok = (
            c1["considered"] == 0
            and c2["considered"] == 0
            and c3["considered"] == 0
            and sidecar1.is_file() and sidecar2.is_file()
        )
    return _check("re-running migration after completion is a no-op", ok)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

TESTS = [
    test_dry_run_is_read_only,
    test_commit_moves_pipeline_skipped_pdf_and_writes_sidecar,
    test_commit_handles_ingest_skipped_triple,
    test_commit_handles_ingest_failed_with_timeout_outcome,
    test_idempotent_rerun_is_safe,
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
