#!/usr/bin/env python3
"""
Backfill repair of author metadata in the papers corpus (Phase C3, 2026-08).

Reads the damaged and empty author lists the audit found (audit_authors.py),
resolves the real authors by DOI, and writes them back to Qdrant. Qdrant only:
the Neo4j :Author graph is a deliberate follow-up, so the two stores are
knowingly out of step between the runs.

DRY RUN BY DEFAULT. Nothing is written without --apply.

Why the corpus needs this: until 2026-08 the Crossref merge in
paper_pipeline.py enriched title / journal / year but never authors, so every
stored author list was GROBID's parse of the PDF text layer. Audit of
2026-08-03 over 68,426 records: 80.3% clean, 11.2% damaged, 8.5% with no
authors at all, 13,438 of them repairable by DOI.

Safety properties, in the order they matter:

  1. Identity guard. A record is only rewritten when the Crossref title
     matches the stored title (same token-set Jaccard test, and the same 0.3
     threshold, the ingest path uses). A record whose identity is in doubt is
     never given someone else's authors: it is skipped and reported.
  2. Quarantine. Records already carrying `_crossref_title_rejected` (the
     ingest guard fired at ingest time) are skipped outright. They belong to
     the separate GROBID mis-keying problem, and this run produces their
     worklist rather than papering over it.
  3. Reversible. Every write stamps `_authors_previous` with the exact prior
     value, plus `_authors_source` and `_authors_repaired_at`. A record can be
     restored from its own payload, without the snapshot.
  4. Additive. Only `authors` and the three provenance keys are written.
     Vectors are never touched: embeddings are built from title + abstract
     (paper_pipeline.py `_store_vectors`), so author repair cannot move any
     retrieval metric.
  5. Resumable. Processed point ids are appended to a state file, so an
     interrupted run continues where it stopped instead of re-querying
     Crossref for 13k DOIs.

Usage:
    ./repair_authors.py                          # dry run over everything
    ./repair_authors.py --limit 50               # dry run, first 50 candidates
    ./repair_authors.py --limit 50 --apply       # canary: write 50 records
    ./repair_authors.py --apply --snapshot       # snapshot, then full run
    ./repair_authors.py --apply --resume         # continue an interrupted run
    ./repair_authors.py --report repair.json     # machine-readable outcome

Recommended sequence:
    1. ./repair_authors.py --report dry.json                  (dry run, read it)
    2. ./repair_authors.py --limit 50 --apply --snapshot      (canary + backup)
    3. spot-check 5 of the 50 against Crossref/PubMed by hand
    4. ./repair_authors.py --apply --resume                   (the rest)
    5. audit_authors.py --markdown after.md                   (verify)

Environment: QDRANT_HOST / QDRANT_PORT / PAPERS_COLLECTION, same defaults as
the retrieval service.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from author_names import best_author_list, is_damaged, sanitize_authors  # noqa: E402
from paper_pipeline import _title_similarity  # noqa: E402

QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
PAPERS_COLLECTION = os.getenv("PAPERS_COLLECTION", "papers_bge")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "research@muninai.org")
SEMANTIC_SCHOLAR_API_KEY = os.getenv("SEMANTIC_SCHOLAR_API_KEY", "")

# Same threshold as the ingest-time guard (paper_pipeline
# _MERGE_TITLE_SIM_THRESHOLD, calibrated 2026-05-12). Keep them equal: a
# record this run would rewrite is one the ingest path would also have
# enriched.
TITLE_SIM_THRESHOLD = 0.3

_PAGE = 4096
_DEFAULT_PACE_S = 0.4          # ~2.5 lookups/s, well inside the polite pool
_STATE_FILE = Path(os.getenv("REPAIR_STATE_FILE",
                             "/opt/munin/data/author_repair_state.json"))
_QDRANT_BASE = f"http://{QDRANT_HOST}:{QDRANT_PORT}"


# ---------------------------------------------------------------------------
# Qdrant
# ---------------------------------------------------------------------------
def _post(path: str, body: dict, timeout: int = 120) -> dict:
    req = urllib.request.Request(
        f"{_QDRANT_BASE}{path}", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    return json.load(urllib.request.urlopen(req, timeout=timeout))


def scroll_candidates() -> list[dict]:
    """Every record with a DOI whose author list is damaged or empty."""
    out: list[dict] = []
    offset = None
    while True:
        body = {
            "limit": _PAGE,
            "with_payload": ["authors", "title", "doi",
                             "_crossref_title_rejected", "_authors_source"],
            "with_vector": False,
        }
        if offset is not None:
            body["offset"] = offset
        result = _post(f"/collections/{PAPERS_COLLECTION}/points/scroll", body)["result"]
        for point in result["points"]:
            payload = point.get("payload") or {}
            if not payload.get("doi"):
                continue
            authors = payload.get("authors") or []
            damaged, _ = is_damaged(authors) if authors else (True, ["empty"])
            if damaged:
                out.append({"id": point["id"], **payload})
        offset = result.get("next_page_offset")
        if offset is None:
            break
    return out


def set_authors(point_id, authors: list[str], previous, source: str) -> None:
    """Write the repaired list plus its provenance. Only these four keys."""
    _post(f"/collections/{PAPERS_COLLECTION}/points/payload?wait=true", {
        "payload": {
            "authors": authors,
            "_authors_source": source,
            "_authors_repaired_at": datetime.now(timezone.utc).isoformat(),
            "_authors_previous": previous,
        },
        "points": [point_id],
    })


def create_snapshot() -> str:
    """Snapshot the collection before the first write. Cheap insurance:
    ~300 MB, and it is the only way back if the provenance keys themselves
    get clobbered by a later run."""
    req = urllib.request.Request(
        f"{_QDRANT_BASE}/collections/{PAPERS_COLLECTION}/snapshots",
        data=b"", headers={"Content-Type": "application/json"}, method="POST")
    result = json.load(urllib.request.urlopen(req, timeout=900))["result"]
    return result.get("name", "?")


# ---------------------------------------------------------------------------
# Metadata sources
# ---------------------------------------------------------------------------
def crossref_record(doi: str) -> dict | None:
    """Title + authors for a DOI. None on any failure (never raises)."""
    url = "https://api.crossref.org/works/" + urllib.parse.quote(doi, safe="")
    req = urllib.request.Request(url, headers={
        "User-Agent": f"MuninBot/1.0 (https://muninai.org; mailto:{ADMIN_EMAIL})"})
    try:
        msg = json.load(urllib.request.urlopen(req, timeout=25))["message"]
    except Exception:
        return None
    titles = msg.get("title") or []
    return {
        "title": titles[0] if titles else "",
        "authors": [f"{a.get('given', '')} {a.get('family', '')}".strip()
                    for a in (msg.get("author") or []) if a.get("family")],
    }


def s2_record(doi: str) -> dict | None:
    """Semantic Scholar, used only with --with-s2.

    S2 often carries fuller given names than Crossref ("Laurent Le Guyader"
    vs "L Le Guyader"), which the fuller-name policy prefers. Off by default
    because it is rate-limited and the run is 13k DOIs long.
    """
    url = ("https://api.semanticscholar.org/graph/v1/paper/DOI:"
           + urllib.parse.quote(doi, safe="") + "?fields=title,authors")
    headers = {"User-Agent": f"MuninBot/1.0 (mailto:{ADMIN_EMAIL})"}
    if SEMANTIC_SCHOLAR_API_KEY:
        headers["x-api-key"] = SEMANTIC_SCHOLAR_API_KEY
    try:
        data = json.load(urllib.request.urlopen(
            urllib.request.Request(url, headers=headers), timeout=25))
    except Exception:
        return None
    return {"title": data.get("title") or "",
            "authors": [a.get("name", "") for a in (data.get("authors") or [])
                        if a.get("name")]}


# ---------------------------------------------------------------------------
# Repair decision (pure, unit-testable)
# ---------------------------------------------------------------------------
def decide(record: dict, external: list[dict]) -> tuple[str, list[str], str]:
    """Decide what to do with one record.

    Returns (action, authors, source) where action is one of:
        write            authors differ from what is stored
        skip_quarantine  ingest guard already flagged this record's identity
        skip_title       resolved title does not describe the stored paper
        skip_unresolved  nothing usable came back
        skip_unchanged   the stored list already sanitises to the same thing

    `external` is a list of {"title", "authors", "source"} in preference
    order; each is title-checked independently, so a wrong Crossref record
    cannot be rescued by a right S2 one or vice versa.
    """
    if record.get("_crossref_title_rejected"):
        return ("skip_quarantine", [], "")

    stored_titles = record.get("title") or ""
    current = sanitize_authors(record.get("authors"))

    candidates: list[tuple[list[str], str]] = []
    saw_title_mismatch = False
    for ext in external:
        if not ext or not ext.get("authors"):
            continue
        sim = _title_similarity(stored_titles, ext.get("title") or "")
        if stored_titles and ext.get("title") and sim < TITLE_SIM_THRESHOLD:
            saw_title_mismatch = True
            continue
        candidates.append((ext["authors"], ext.get("source", "external")))

    if not candidates:
        if saw_title_mismatch:
            return ("skip_title", [], "")
        # Nothing external, but sanitising alone may still be an improvement
        # ("Herv6 Bottin" -> "Hervé Bottin").
        if current and current != (record.get("authors") or []):
            return ("write", current, "sanitized")
        return ("skip_unresolved", [], "")

    # Stored names come first so a tie keeps what we already have; the
    # fullness score still lets a richer external list win.
    chosen = best_author_list(current, *[c[0] for c in candidates])
    if not chosen:
        return ("skip_unresolved", [], "")
    if chosen == (record.get("authors") or []):
        return ("skip_unchanged", [], "")

    source = "sanitized" if chosen == current else candidates[0][1]
    if chosen != current:
        for authors, name in candidates:
            if chosen == sanitize_authors(authors):
                source = name
                break
    return ("write", chosen, source)


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
def load_state(path: Path) -> set:
    if not path.exists():
        return set()
    try:
        return set(json.loads(path.read_text()).get("done", []))
    except Exception:
        print(f"[warn] unreadable state file {path}, starting fresh",
              file=sys.stderr)
        return set()


def save_state(path: Path, done: set) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"done": sorted(done, key=str),
                                    "updated_at": datetime.now(timezone.utc).isoformat()}))
    except Exception as e:
        print(f"[warn] could not persist state: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="actually write to Qdrant (default is a dry run)")
    ap.add_argument("--limit", type=int, default=0, metavar="N",
                    help="process at most N candidates (canary runs)")
    ap.add_argument("--resume", action="store_true",
                    help="skip point ids recorded in the state file")
    ap.add_argument("--snapshot", action="store_true",
                    help="create a Qdrant snapshot before the first write")
    ap.add_argument("--with-s2", action="store_true",
                    help="also query Semantic Scholar (fuller given names, slower)")
    ap.add_argument("--pace", type=float, default=_DEFAULT_PACE_S, metavar="SECONDS",
                    help=f"delay between lookups (default {_DEFAULT_PACE_S})")
    ap.add_argument("--report", metavar="PATH", help="write a JSON outcome report")
    ap.add_argument("--state-file", metavar="PATH", default=str(_STATE_FILE))
    args = ap.parse_args()

    state_path = Path(args.state_file)
    done = load_state(state_path) if args.resume else set()

    mode = "APPLY (writing)" if args.apply else "DRY RUN (no writes)"
    print(f"[repair] {mode} on {PAPERS_COLLECTION} at {QDRANT_HOST}:{QDRANT_PORT}")
    if args.resume and done:
        print(f"[repair] resuming, {len(done)} points already processed")

    print("[repair] scanning for candidates ...", file=sys.stderr)
    candidates = scroll_candidates()
    if args.resume:
        candidates = [c for c in candidates if c["id"] not in done]
    if args.limit:
        candidates = candidates[:args.limit]
    print(f"[repair] {len(candidates)} candidate records")

    if args.apply and args.snapshot:
        print("[repair] creating snapshot (this can take a minute) ...")
        print(f"[repair] snapshot: {create_snapshot()}")

    outcomes = Counter()
    examples: list[dict] = []
    quarantined: list[dict] = []
    title_mismatch: list[dict] = []
    written = 0

    for i, record in enumerate(candidates, 1):
        if record.get("_crossref_title_rejected"):
            outcomes["skip_quarantine"] += 1
            quarantined.append({"id": record["id"], "doi": record.get("doi"),
                                "title": (record.get("title") or "")[:90]})
            continue

        external = []
        cr = crossref_record(record["doi"])
        time.sleep(args.pace)
        if cr:
            external.append({**cr, "source": "crossref"})
        if args.with_s2:
            s2 = s2_record(record["doi"])
            time.sleep(args.pace)
            if s2:
                external.append({**s2, "source": "semantic_scholar"})

        action, authors, source = decide(record, external)
        outcomes[action] += 1

        if action == "skip_title":
            title_mismatch.append({
                "id": record["id"], "doi": record.get("doi"),
                "stored_title": (record.get("title") or "")[:90],
                "resolved_title": (external[0].get("title") if external else "")[:90],
            })
        elif action == "write":
            if len(examples) < 25:
                examples.append({
                    "doi": record.get("doi"),
                    "before": record.get("authors"),
                    "after": authors,
                    "source": source,
                })
            if args.apply:
                try:
                    set_authors(record["id"], authors,
                                record.get("authors"), source)
                    written += 1
                    done.add(record["id"])
                except Exception as e:
                    outcomes["write_failed"] += 1
                    outcomes["write"] -= 1
                    print(f"[error] {record.get('doi')}: {e}", file=sys.stderr)

        if args.apply and i % 100 == 0:
            save_state(state_path, done)
        if i % 250 == 0:
            print(f"[repair] {i}/{len(candidates)} "
                  f"(written {written}, {dict(outcomes)})", file=sys.stderr)

    if args.apply:
        save_state(state_path, done)

    print("\noutcomes:")
    for action, count in outcomes.most_common():
        print(f"  {action:18s} {count}")
    if not args.apply:
        print(f"\nDRY RUN: {outcomes.get('write', 0)} records WOULD be rewritten.")
        print("Re-run with --apply (and --snapshot on the first write) to commit.")
    else:
        print(f"\nwritten: {written}")

    if examples:
        print("\nexample repairs:")
        for ex in examples[:8]:
            print(f"  {ex['doi']}  [{ex['source']}]")
            print(f"    before: {str(ex['before'])[:88]}")
            print(f"    after : {str(ex['after'])[:88]}")
    if quarantined:
        print(f"\n{len(quarantined)} records skipped as quarantined "
              "(_crossref_title_rejected). Worklist for the GROBID "
              "mis-keying fix, not repaired here.")
    if title_mismatch:
        print(f"{len(title_mismatch)} records skipped: the DOI resolves to a "
              "different paper than the stored title. These are new "
              "mis-keying suspects.")

    if args.report:
        Path(args.report).write_text(json.dumps({
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "mode": "apply" if args.apply else "dry_run",
            "collection": PAPERS_COLLECTION,
            "candidates": len(candidates),
            "written": written,
            "outcomes": dict(outcomes),
            "examples": examples,
            "quarantined": quarantined,
            "title_mismatch": title_mismatch,
        }, indent=2, ensure_ascii=False))
        print(f"\n[repair] report -> {args.report}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
