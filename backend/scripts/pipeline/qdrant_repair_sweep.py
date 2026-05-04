#!/usr/bin/env python3
# ==============================================================================
# QDRANT-SCOPED REPAIR SWEEP
# ==============================================================================
# Walks every DOI in the Qdrant `papers` collection (~31k) and runs targeted
# repair on the ones whose PDFs are missing from disk.
#
# Why this exists: `paper_cleanup.py repair-auto --source neo4j` walks the full
# Neo4j citation graph (~485k nodes) which is mostly metadata-only references
# that will never be downloadable. This script constrains the sweep to the
# actual searchable corpus — papers that have a vector in Qdrant — so the run
# time is bounded to a few hours instead of weeks.
#
# Fast-path design: a sample of 50 random Qdrant DOIs showed ~98% have their
# PDF on disk. We skip the expensive OpenAlex/S2/CrossRef fetch for those
# healthy papers (a quick filesystem check instead) and only invoke the full
# `repair_auto` logic on the actual orphans.
#
# Usage:
#   sudo bash -c 'set -a && source /opt/hugin/config/cluster.env && set +a && \
#       /opt/anaconda/2024.10-1/bin/python3 \
#       backend/scripts/pipeline/qdrant_repair_sweep.py'
#
# Idempotent: safe to re-run after interruption. Each paper is processed
# atomically (subprocess call per orphan, filesystem check per healthy).
# ==============================================================================

from __future__ import annotations

import contextlib
import io
import os
import sys
import time
import traceback

# Import paper_cleanup from wherever it's deployed (the HuginSLURM path) so
# we reuse the exact same repair_auto logic without re-implementing it.
CLEANUP_DIR = "/opt/cluster/scripts/knowledge"
if CLEANUP_DIR not in sys.path:
    sys.path.insert(0, CLEANUP_DIR)

import httpx  # noqa: E402
import paper_cleanup  # noqa: E402

QDRANT_URL = os.getenv("QDRANT_URL", "http://127.0.0.1:6333")
COLLECTION = "papers"
BATCH_SIZE = 1000
PROGRESS_EVERY = 200      # healthy papers: log every 200
ORPHAN_PROGRESS_EVERY = 5  # orphans: log every 5 (they're slow, we want feedback)


# ------------------------------------------------------------------------------
# Qdrant DOI scroll
# ------------------------------------------------------------------------------

def scroll_dois() -> list[str]:
    """
    Pull every DOI from the Qdrant papers collection. De-duped, sorted for
    stable ordering across runs (so interrupt+resume starts at roughly the
    same position).
    """
    print(f"[scroll] reading {COLLECTION} from {QDRANT_URL} ...")
    offset = None
    seen: set[str] = set()
    total_points = 0
    with_doi = 0

    while True:
        body: dict = {
            "limit": BATCH_SIZE,
            "with_payload": ["doi"],
            "with_vector": False,
        }
        if offset is not None:
            body["offset"] = offset

        r = httpx.post(
            f"{QDRANT_URL}/collections/{COLLECTION}/points/scroll",
            json=body,
            timeout=60,
        )
        r.raise_for_status()
        data = r.json()["result"]

        for point in data["points"]:
            total_points += 1
            doi = (point.get("payload") or {}).get("doi")
            if doi:
                with_doi += 1
                seen.add(doi.strip())

        offset = data.get("next_page_offset")
        if offset is None:
            break

    print(
        f"[scroll] visited {total_points} points, "
        f"{with_doi} had DOIs, {len(seen)} unique"
    )
    return sorted(seen)


# ------------------------------------------------------------------------------
# Per-paper healthy-fast-path / orphan-repair decision
# ------------------------------------------------------------------------------

def repair_one(doi: str) -> str:
    """
    Returns one of: 'skip' (healthy, PDF present), 'repaired' (orphan got
    fixed), 'failed' (repair errored). Orphans go through paper_cleanup's
    repair_auto so retractions, short papers, and dead metadata all get the
    same treatment as the stock tool.
    """
    # Fast path: is the PDF on disk already? If so, we trust the sweep and
    # move on without hitting any external APIs.
    pdf_path = paper_cleanup.get_pdf_path(doi) if hasattr(paper_cleanup, "get_pdf_path") else None
    if pdf_path:
        return "skip"

    # Slow path: orphan. Hand it to repair_auto in single-DOI mode so it
    # can decide to remove / re-download / re-embed / enrich as appropriate.
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            paper_cleanup.repair_auto(
                single_doi=doi,
                max_papers=1,
                output_file=None,
            )
        return "repaired"
    except Exception as e:
        print(f"[FAIL] {doi}: {e}", file=sys.stderr)
        return "failed"


# ------------------------------------------------------------------------------
# Main sweep
# ------------------------------------------------------------------------------

def main() -> int:
    print("=" * 70)
    print("Qdrant-scoped Repair Sweep")
    print("=" * 70)
    print(f"Source: {QDRANT_URL}/{COLLECTION}")
    print()

    dois = scroll_dois()
    total = len(dois)
    if not total:
        print("No DOIs found in Qdrant. Exiting.")
        return 0

    print(f"\nProcessing {total} papers...")
    print(f"  - PDF-present papers: fast-skipped (filesystem check only)")
    print(f"  - Orphans: full repair_auto (OpenAlex + S2 + CrossRef + actions)")
    print()

    start = time.monotonic()
    skipped = 0
    repaired = 0
    failed = 0

    for i, doi in enumerate(dois, 1):
        result = repair_one(doi)
        if result == "skip":
            skipped += 1
        elif result == "repaired":
            repaired += 1
        else:
            failed += 1

        # Log progress cheaply for the fast path and frequently for orphans
        log_now = (
            i % PROGRESS_EVERY == 0
            or i == total
            or (result != "skip" and (repaired + failed) % ORPHAN_PROGRESS_EVERY == 0)
        )
        if log_now:
            elapsed = time.monotonic() - start
            rate = i / elapsed if elapsed > 0 else 0
            remaining = total - i
            eta_s = (remaining / rate) if rate > 0 else 0
            print(
                f"[progress] {i}/{total} ({100*i/total:.1f}%) "
                f"skip={skipped} repaired={repaired} failed={failed} "
                f"rate={rate:.1f}/s eta={eta_s/3600:.1f}h"
            )

    elapsed = time.monotonic() - start
    print()
    print("=" * 70)
    print(f"DONE: {total} papers processed in {elapsed/3600:.2f} h")
    print(f"  healthy (skipped):  {skipped}")
    print(f"  orphans (repaired): {repaired}")
    print(f"  failures:            {failed}")
    print("=" * 70)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[ABORT] Interrupted by user", file=sys.stderr)
        sys.exit(130)
    except Exception:
        traceback.print_exc()
        sys.exit(1)
