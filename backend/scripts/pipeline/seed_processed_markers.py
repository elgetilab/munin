#!/usr/bin/env python3
"""
seed_processed_markers.py — one-shot stop-gap that drains the pipeline
watcher's "I have to GROBID-and-dedup all 60k PDFs" backlog.

Background
----------
``paper_pipeline.py --watch`` skips any PDF whose stem matches a JSON
file in PROCESSED_DIR. But the original ~30k crawler ingest predates
that marker convention, so when the new munin-paper-pipeline.service
started it saw 60k+ unmarked PDFs and is dutifully re-running the full
pipeline on every single one to dedup against Qdrant.

This script seeds a stub marker for every `doi_*.pdf` whose DOI is
already represented in Qdrant. After it runs, the watcher's backlog
drops from "everything" to "things genuinely new since the last
crawler ingest", and operator drops + crawler output continue to
land normally.

Run once after deploying the daemon. Idempotent — safe to re-run.

Usage:
    sudo /opt/munin/services/pipeline/venv/bin/python3 \\
         /opt/cluster/scripts/pipeline/seed_processed_markers.py
    # or with a dry run first:
    sudo /opt/munin/services/pipeline/venv/bin/python3 \\
         /opt/cluster/scripts/pipeline/seed_processed_markers.py --dry-run

Environment:
    QDRANT_HOST          default 127.0.0.1
    QDRANT_PORT          default 6333
    QDRANT_COLLECTION    default papers
    PAPERS_DIR           default /opt/munin/data/papers/pdf
    PROCESSED_DIR        default /opt/munin/data/papers/processed
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


QDRANT_HOST = os.getenv("QDRANT_HOST", "127.0.0.1")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "papers")
PAPERS_DIR = os.getenv("PAPERS_DIR", "/opt/munin/data/papers/pdf")
PROCESSED_DIR = os.getenv("PROCESSED_DIR", "/opt/munin/data/papers/processed")


def _doi_from_filename(filename: str) -> Optional[str]:
    """Parse `doi_10.1234_example.pdf` → `10.1234/example`.

    Mirrors paper_pipeline.PaperPipeline._extract_doi_from_filename so
    seeded markers match the conventions the watcher would have
    written itself.
    """
    if not filename.startswith("doi_"):
        return None
    doi_part = filename[4:]
    if doi_part.lower().endswith(".pdf"):
        doi_part = doi_part[:-4]
    if not doi_part.startswith("10."):
        return None
    # Find the first underscore after the registrant prefix
    idx = 3
    while idx < len(doi_part) and doi_part[idx].isdigit():
        idx += 1
    if idx >= len(doi_part) or doi_part[idx] != "_":
        return None
    return doi_part[:idx] + "/" + doi_part[idx + 1:]


def _load_known_dois() -> set[str]:
    """Scroll the Qdrant collection once, collect every DOI's
    lower-cased form. ~30k entries fit comfortably in memory (a few
    MB)."""
    from qdrant_client import QdrantClient

    client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
    print(
        f"[INFO] Scrolling {QDRANT_COLLECTION} @ {QDRANT_HOST}:{QDRANT_PORT}...",
        flush=True,
    )
    t0 = time.time()
    out: set[str] = set()
    offset = None
    page = 0
    while True:
        points, offset = client.scroll(
            collection_name=QDRANT_COLLECTION,
            scroll_filter=None,
            limit=512,
            offset=offset,
            with_payload=["doi"],
            with_vectors=False,
        )
        if not points:
            break
        for p in points:
            doi = (p.payload or {}).get("doi")
            if isinstance(doi, str) and doi:
                out.add(doi.strip().lower())
        page += 1
        if page % 20 == 0:
            print(f"[INFO]   scrolled {len(out)} so far...", flush=True)
        if offset is None:
            break
    print(f"[INFO] Loaded {len(out)} DOIs in {time.time() - t0:.1f}s")
    return out


def _stub_marker(filename: str, doi: str) -> dict:
    """Minimal payload — the watcher only checks for existence.
    Tagging with `seeded_at` distinguishes these from
    pipeline-written markers if anyone ever audits."""
    return {
        "seeded": True,
        "filename": filename,
        "doi": doi,
        "seeded_at": datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed pipeline-watcher markers for already-ingested PDFs")
    parser.add_argument("--dry-run", action="store_true",
                        help="Walk + count, write nothing")
    parser.add_argument("--limit", type=int, default=None,
                        help="Stop after N markers written (for testing)")
    args = parser.parse_args()

    pdf_dir = Path(PAPERS_DIR)
    marker_dir = Path(PROCESSED_DIR)
    if not pdf_dir.is_dir():
        print(f"[ERROR] PAPERS_DIR not found: {pdf_dir}", file=sys.stderr)
        return 2

    if not args.dry_run:
        marker_dir.mkdir(parents=True, exist_ok=True)

    known = _load_known_dois()
    if not known:
        print("[ERROR] Qdrant returned 0 DOIs. Refusing to seed (would mark everything as 'unknown').",
              file=sys.stderr)
        return 3

    pdfs_total = 0
    no_marker_needed = 0
    no_doi_in_filename = 0
    not_in_qdrant = 0
    seeded = 0
    already_marked = 0
    started = time.time()

    for pdf in pdf_dir.glob("*.pdf"):
        pdfs_total += 1
        marker = marker_dir / f"{pdf.stem}.json"
        if marker.exists():
            already_marked += 1
            no_marker_needed += 1
            continue
        doi = _doi_from_filename(pdf.name)
        if doi is None:
            no_doi_in_filename += 1
            continue
        if doi.strip().lower() not in known:
            not_in_qdrant += 1
            continue

        if args.dry_run:
            seeded += 1
        else:
            try:
                with open(marker, "w", encoding="utf-8") as f:
                    json.dump(_stub_marker(pdf.name, doi), f)
                seeded += 1
            except OSError as e:
                print(f"[WARN] could not write {marker}: {e}", file=sys.stderr)
                continue

        if seeded % 1000 == 0:
            elapsed = time.time() - started
            rate = seeded / elapsed if elapsed else 0
            print(f"[INFO] seeded {seeded} markers (rate={rate:.0f}/s)", flush=True)
        if args.limit is not None and seeded >= args.limit:
            print(f"[INFO] Hit --limit {args.limit}, stopping early.")
            break

    elapsed = time.time() - started
    print(
        f"\n[DONE] in {elapsed:.1f}s. {('DRY RUN — wrote nothing' if args.dry_run else 'wrote ' + str(seeded) + ' markers')}\n"
        f"  total PDFs scanned:   {pdfs_total}\n"
        f"  already had marker:   {already_marked}\n"
        f"  no doi_ in filename:  {no_doi_in_filename}\n"
        f"  doi not in Qdrant:    {not_in_qdrant}\n"
        f"  markers seeded:       {seeded}\n"
    )
    if not_in_qdrant:
        print(
            f"  ↪ those {not_in_qdrant} PDFs will be picked up by the\n"
            f"    watcher on its next pass (they're genuinely un-ingested).",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
