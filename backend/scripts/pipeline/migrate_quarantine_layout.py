#!/usr/bin/env python3
"""
migrate_quarantine_layout.py — one-shot migration of the legacy
quarantine directories into the Phase B state-sidecar layout.

Phase C of the 2026-05-13 pipeline consolidation
(docs/PIPELINE-CONSOLIDATION-PLAN.md). Run once after Phase B has
landed; idempotent so it's safe to re-run.

Three legacy sources funnel into the new
``/opt/munin/data/papers/pdf/quarantine/`` directory:

1. ``papers/skipped/`` — pipeline-side ``_log_skipped_pdf`` JSON
   records. PDFs referenced here usually still sit in
   ``/papers/pdf/`` and the watcher has been re-trying them every
   poll. Migration: locate the PDF, move it to ``quarantine/``,
   reconstruct a quarantine state sidecar from the log JSON, then
   delete the log.

2. ``papers/pdf/skipped/`` — admin/ingest quality / null-DOI
   quarantine. PDFs + contributor sidecars + ``*.skip_info.json``
   triples. Migration: move PDF + contributor sidecar into
   ``quarantine/`` and synthesize a state sidecar from the
   skip_info.

3. ``papers/pdf/failed/`` — admin/ingest crash/timeout quarantine.
   Same triple-format as (2). Same migration.

Defaults to ``--dry-run`` (prints what would happen, writes
nothing). Pass ``--commit`` to actually move files.

After a successful ``--commit`` run, the three source directories
can be removed by the operator (the script does not delete them in
case anything was missed; ``ls`` them first to confirm they're
empty before ``rmdir``ing).

Usage:
    python migrate_quarantine_layout.py                # dry-run
    python migrate_quarantine_layout.py --commit       # do it
    python migrate_quarantine_layout.py --commit --no-pipeline-skipped
        # only migrate the two admin/ingest dirs; leave the
        # pipeline-side `papers/skipped/` logs alone (useful if
        # those PDFs are still in active use somewhere).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Tuple

# Reuse the canonical sidecar builder from paper_pipeline so format
# changes there land here too. The script and the pipeline live in
# the same directory at deploy time.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paper_pipeline import (  # noqa: E402
    _build_state_sidecar,
    _write_state_sidecar,
    _load_state_sidecar,
    STATE_SCHEMA_VERSION,
    Paper,
)


DATA_DIR = Path(os.getenv("MUNIN_DATA_DIR", "/opt/munin/data"))
PAPERS_DIR = DATA_DIR / "papers"
PDF_DIR = PAPERS_DIR / "pdf"

LEGACY_PIPELINE_SKIPPED = PAPERS_DIR / "skipped"          # source 1: JSON logs only
LEGACY_INGEST_SKIPPED = PDF_DIR / "skipped"               # source 2: full PDFs
LEGACY_INGEST_FAILED = PDF_DIR / "failed"                 # source 3: full PDFs
QUARANTINE_DIR = PDF_DIR / "quarantine"


# Map legacy `outcome` / skip-reason strings onto the controlled set
# of quarantine reasons the Phase B sidecar uses.
_OUTCOME_TO_REASON = {
    "skipped": "quality_filter_or_duplicate",
    "doi_extraction_failed": "doi_extraction_failed",
    "timeout": "pipeline_timeout",
    "pipeline_error": "pipeline_error",
    "pipeline_no_dispose_line": "pipeline_no_dispose_line",
}


def _utcnow_iso() -> str:
    return (datetime.now(timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z"))


def _normalise_reason(outcome: str, reason: str) -> str:
    """Pick the controlled quarantine_reason for a legacy entry.

    Prefers the outcome mapping; falls back to the raw reason
    (truncated) if outcome is missing or unrecognised."""
    if outcome in _OUTCOME_TO_REASON:
        return _OUTCOME_TO_REASON[outcome]
    if outcome:
        return f"legacy:{outcome}"
    if reason:
        return f"legacy:{reason[:80]}"
    return "legacy:unknown"


def _synth_paper_from_contributor(
    pdf_path: Path,
    contributor_sidecar: Optional[Dict],
    skip_info: Dict,
) -> Optional[Paper]:
    """Synthesize a minimal Paper so _build_state_sidecar has something
    to chew on. We only need enough metadata for the sidecar; downstream
    code already handles None gracefully where DOI is missing."""
    # The legacy quarantine path used inbox UUIDs as the filename;
    # there is no DOI available at this point.
    paper_id_stem = pdf_path.stem
    return Paper(
        id=paper_id_stem[:16] or "legacy",
        title=skip_info.get("original_filename") or pdf_path.name,
        abstract="",
        authors=[],
        doi=None,
        year=None,
        journal=None,
        references=[],
        pdf_path=str(pdf_path),
    )


def _move_pair(
    src_pdf: Path,
    src_sidecar: Optional[Path],
    dst_dir: Path,
    dry_run: bool,
) -> Tuple[Path, Optional[Path]]:
    """Move a PDF and (optionally) its contributor sidecar into
    ``dst_dir``, preserving the filename stem. If the destination PDF
    already exists, the source is removed instead of overwriting.
    Returns the resolved destination paths even in dry-run mode."""
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst_pdf = dst_dir / src_pdf.name
    dst_sidecar = (dst_dir / src_sidecar.name) if src_sidecar else None

    if dry_run:
        return dst_pdf, dst_sidecar

    if dst_pdf.exists():
        if src_pdf.exists():
            src_pdf.unlink()
    elif src_pdf.exists():
        shutil.move(str(src_pdf), str(dst_pdf))
    if src_sidecar and src_sidecar.is_file():
        if dst_sidecar and dst_sidecar.exists():
            src_sidecar.unlink()
        elif dst_sidecar:
            shutil.move(str(src_sidecar), str(dst_sidecar))
    return dst_pdf, dst_sidecar


def _load_json(path: Path) -> Optional[Dict]:
    if not path.is_file():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"  [WARN] unreadable JSON ({path}): {e}")
        return None


def _legacy_history_entry(skip_info: Dict, reason: str) -> Dict:
    """One synthetic history entry capturing the original quarantine
    event timestamp + reason, so the new sidecar's `history[]` is
    chronologically accurate."""
    legacy_ts = skip_info.get("timestamp") or _utcnow_iso()
    return {
        "at": legacy_ts,
        "state": "quarantine",
        "via": "upload",  # legacy admin/ingest quarantine; source 1 overrides
        "reason": reason,
    }


# -------------------------------------------------------------------
# Source 1: papers/skipped/  (pipeline-side _log_skipped_pdf logs)
# -------------------------------------------------------------------
def migrate_pipeline_skipped(dry_run: bool) -> Dict[str, int]:
    counts = {"considered": 0, "pdf_moved": 0,
              "pdf_missing": 0, "log_removed": 0}
    if not LEGACY_PIPELINE_SKIPPED.is_dir():
        return counts

    for log_path in sorted(LEGACY_PIPELINE_SKIPPED.glob("*.json")):
        counts["considered"] += 1
        log_data = _load_json(log_path)
        if not log_data:
            continue
        ref_pdf = Path(log_data.get("pdf_path") or "")
        # The referenced PDF is usually in /papers/pdf/<same stem>.pdf
        if not ref_pdf.is_file():
            candidate = PDF_DIR / f"{log_path.stem}.pdf"
            if candidate.is_file():
                ref_pdf = candidate

        if not ref_pdf.is_file():
            counts["pdf_missing"] += 1
            if not dry_run:
                log_path.unlink()  # orphan log: clean up
                counts["log_removed"] += 1
            continue

        reason = _normalise_reason("", log_data.get("reason", ""))
        history_entry = {
            "at": log_data.get("skipped_at") or _utcnow_iso(),
            "state": "quarantine",
            "via": "crawler",   # source 1 is the watcher's log
            "reason": reason,
        }
        synth_paper = Paper(
            id=log_path.stem[:16] or "legacy",
            title=ref_pdf.stem,
            abstract="",
            authors=[],
            doi=ref_pdf.stem.replace("doi_", "").replace("_", "/", 1)
                if ref_pdf.stem.startswith("doi_") else None,
            year=None,
            journal=None,
            references=[],
            pdf_path=str(ref_pdf),
        )

        # _move_pair will move into QUARANTINE_DIR and produce the
        # final paths we then write the sidecar against.
        dst_pdf, _ = _move_pair(ref_pdf, None, QUARANTINE_DIR, dry_run)
        existing = _load_state_sidecar(dst_pdf) if not dry_run else None
        sidecar = _build_state_sidecar(
            paper=synth_paper,
            ingest_path="crawler",
            state="quarantine",
            quarantine_reasons=[reason],
            contributor=None,
            existing=existing,
        )
        # Override history[0] with the legacy timestamp instead of "now",
        # so first_seen_at reflects when the skip actually happened.
        if existing is None:
            sidecar["first_seen_at"] = history_entry["at"]
            sidecar["last_modified_at"] = history_entry["at"]
            sidecar["history"] = [history_entry]
        if not dry_run:
            _write_state_sidecar(dst_pdf, sidecar)
            log_path.unlink()
            counts["log_removed"] += 1
        counts["pdf_moved"] += 1

    return counts


# -------------------------------------------------------------------
# Sources 2 + 3: papers/pdf/{skipped,failed}/ (admin/ingest quarantine)
# -------------------------------------------------------------------
def migrate_ingest_quarantine(src_dir: Path, dry_run: bool) -> Dict[str, int]:
    counts = {"considered": 0, "pdf_moved": 0, "sidecar_built": 0,
              "skip_info_removed": 0, "orphan_skipped": 0}
    if not src_dir.is_dir():
        return counts

    for pdf in sorted(src_dir.glob("*.pdf")):
        counts["considered"] += 1
        skip_info_path = pdf.with_suffix(".skip_info.json")
        contrib_path = pdf.with_suffix(".contributor.json")

        skip_info = _load_json(skip_info_path) or {}
        contributor = _load_json(contrib_path)

        outcome = (skip_info.get("outcome") or "").strip()
        reason_text = (skip_info.get("reason") or "").strip()
        reason = _normalise_reason(outcome, reason_text)

        synth_paper = _synth_paper_from_contributor(pdf, contributor, skip_info)
        history_entry = _legacy_history_entry(skip_info, reason)

        dst_pdf, dst_contrib = _move_pair(
            pdf,
            contrib_path if contrib_path.is_file() else None,
            QUARANTINE_DIR,
            dry_run,
        )
        existing = _load_state_sidecar(dst_pdf) if not dry_run else None
        sidecar = _build_state_sidecar(
            paper=synth_paper,
            ingest_path="upload",
            state="quarantine",
            quarantine_reasons=[reason],
            contributor=contributor,
            existing=existing,
        )
        if existing is None:
            sidecar["first_seen_at"] = history_entry["at"]
            sidecar["last_modified_at"] = history_entry["at"]
            sidecar["history"] = [history_entry]
            # Stash any extra info from skip_info that doesn't fit the
            # standard sidecar shape — it can help during review.
            extras = {}
            for k in ("email", "group_slug", "original_filename",
                      "qdrant_point_id_deleted", "log_tail", "returncode"):
                if k in skip_info:
                    extras[k] = skip_info[k]
            if extras:
                sidecar["legacy_skip_info"] = extras

        if not dry_run:
            _write_state_sidecar(dst_pdf, sidecar)
            if skip_info_path.is_file():
                skip_info_path.unlink()
                counts["skip_info_removed"] += 1
        counts["pdf_moved"] += 1
        counts["sidecar_built"] += 1

    return counts


# -------------------------------------------------------------------
# Main
# -------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true",
                        help="Actually move files. Without this flag the "
                             "script runs in dry-run mode and prints "
                             "what it would do.")
    parser.add_argument("--no-pipeline-skipped", action="store_true",
                        help="Skip migration of source 1 (papers/skipped/ "
                             "JSON logs). Useful if those PDFs are still "
                             "in active use.")
    args = parser.parse_args()
    dry_run = not args.commit

    print("=" * 70)
    print("Quarantine-layout migration (Phase C of 2026-05-13 consolidation)")
    print("=" * 70)
    print(f"Mode      : {'DRY-RUN (no writes)' if dry_run else 'COMMIT'}")
    print(f"Sources   : {LEGACY_PIPELINE_SKIPPED}")
    print(f"            {LEGACY_INGEST_SKIPPED}")
    print(f"            {LEGACY_INGEST_FAILED}")
    print(f"Target    : {QUARANTINE_DIR}")
    print(f"Sidecar v : {STATE_SCHEMA_VERSION}")
    print()

    totals: Dict[str, Dict[str, int]] = {}

    if not args.no_pipeline_skipped:
        print(f"--- Source 1: {LEGACY_PIPELINE_SKIPPED} ---")
        totals["source_1"] = migrate_pipeline_skipped(dry_run)
        for k, v in totals["source_1"].items():
            print(f"    {k:<20s} {v}")
        print()
    else:
        print("--- Source 1 skipped (--no-pipeline-skipped) ---\n")

    print(f"--- Source 2: {LEGACY_INGEST_SKIPPED} ---")
    totals["source_2"] = migrate_ingest_quarantine(LEGACY_INGEST_SKIPPED, dry_run)
    for k, v in totals["source_2"].items():
        print(f"    {k:<20s} {v}")
    print()

    print(f"--- Source 3: {LEGACY_INGEST_FAILED} ---")
    totals["source_3"] = migrate_ingest_quarantine(LEGACY_INGEST_FAILED, dry_run)
    for k, v in totals["source_3"].items():
        print(f"    {k:<20s} {v}")
    print()

    grand_moved = sum(t.get("pdf_moved", 0) for t in totals.values())
    print("=" * 70)
    print(f"TOTAL PDFs migrated: {grand_moved}")
    if dry_run:
        print()
        print("Re-run with --commit to actually move files.")
    else:
        print()
        print("Done. The source directories still exist (in case anything")
        print("was missed). After verifying everything migrated correctly:")
        print(f"  ls {LEGACY_PIPELINE_SKIPPED} && rmdir {LEGACY_PIPELINE_SKIPPED}")
        print(f"  ls {LEGACY_INGEST_SKIPPED} && rmdir {LEGACY_INGEST_SKIPPED}")
        print(f"  ls {LEGACY_INGEST_FAILED} && rmdir {LEGACY_INGEST_FAILED}")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
