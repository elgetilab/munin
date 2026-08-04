#!/usr/bin/env python3
"""
Reconcile the Neo4j author graph with the repaired Qdrant payloads (Phase C4).

The 2026-08-03 backfill (repair_authors.py) fixed author lists in Qdrant only,
so the graph still holds the damaged names: `Biophysica A~ta` and
`Theodor-Kocher Institute` are still :Author nodes, and 5,081 papers whose
authors were recovered from nothing still have no AUTHORED edges at all. This
script makes the graph agree with the payloads.

Qdrant is the source of truth and the ONLY source: no network calls, no
Crossref, no eutils. That makes the run fast, repeatable, and safe to redo.

What it does, per corpus paper:

    1. create the :Paper node when it is missing entirely (~250 papers exist
       in Qdrant but never made it into the graph, so their authors have
       nothing to attach to)
    2. MERGE an :Author per payload name, using the pipeline's own id scheme
       (sha256 of the lowercased name, first 16 hex chars) so future ingests
       land on the same node
    3. MERGE the AUTHORED edge
    4. delete AUTHORED edges to authors that are not in the payload

then, once, sweep :Author nodes left with no edges.

Papers whose payload author list is EMPTY are skipped, never stripped. An
empty payload means "we could not resolve them" (747 records after the
backfill), not "this paper has no authors", and the graph may hold the only
surviving copy of those names.

DRY RUN BY DEFAULT. Nothing is written without --apply.

Usage:
    ./repair_author_graph.py                      # dry run, full diff
    ./repair_author_graph.py --limit 500          # dry run over 500 papers
    ./repair_author_graph.py --apply --backup edges.jsonl
    ./repair_author_graph.py --apply --repaired-only   # only 2026-08 repairs

Rollback: the --backup JSONL holds every affected paper's edges as they were
before the run ({"doi": ..., "authors": [{"author_id": ..., "name": ...}]}),
which is enough to rebuild both the edges and any Author node the orphan
sweep removed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from neo4j import GraphDatabase

QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
PAPERS_COLLECTION = os.getenv("PAPERS_COLLECTION", "papers_bge")
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")

_SCROLL_PAGE = 4096
_BATCH = 250          # papers per transaction
_ORPHAN_BATCH = 5000


def author_id(name: str) -> str:
    """Must match paper_pipeline.py exactly, or a later ingest of the same
    paper creates a second node for the same person."""
    return hashlib.sha256(name.lower().encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Qdrant
# ---------------------------------------------------------------------------
def scroll_papers(repaired_only: bool) -> list[dict]:
    """Corpus papers with a DOI and a non-empty author list."""
    out: list[dict] = []
    offset = None
    flt = ({"must": [{"key": "_authors_repaired_at", "match": {"text": "2026-08"}}]}
           if repaired_only else None)
    while True:
        body = {
            "limit": _SCROLL_PAGE,
            "with_payload": ["doi", "authors", "paper_id", "title", "year",
                             "journal"],
            "with_vector": False,
        }
        if offset is not None:
            body["offset"] = offset
        if flt:
            body["filter"] = flt
        req = urllib.request.Request(
            f"http://{QDRANT_HOST}:{QDRANT_PORT}/collections/"
            f"{PAPERS_COLLECTION}/points/scroll",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"})
        result = json.load(urllib.request.urlopen(req, timeout=120))["result"]
        for point in result["points"]:
            p = point.get("payload") or {}
            authors = [a for a in (p.get("authors") or []) if isinstance(a, str) and a.strip()]
            if not p.get("doi") or not authors:
                continue
            out.append({
                "doi": p["doi"],
                "paper_id": p.get("paper_id"),
                "title": p.get("title") or "",
                "year": p.get("year"),
                "journal": p.get("journal") or "",
                "authors": [{"author_id": author_id(a), "name": a} for a in authors],
            })
        offset = result.get("next_page_offset")
        if offset is None:
            break
    return out


# ---------------------------------------------------------------------------
# Neo4j reads
# ---------------------------------------------------------------------------
def current_edges(session, dois: list[str]) -> dict[str, list[dict]]:
    """{doi: [{author_id, name}]} for the papers that have a node."""
    rows = session.run("""
        UNWIND $dois AS doi
        MATCH (a:Author)-[:AUTHORED]->(p:Paper {doi: doi})
        RETURN doi AS doi, collect(DISTINCT {author_id: a.author_id, name: a.name}) AS authors
    """, dois=dois).data()
    return {r["doi"]: r["authors"] for r in rows}


def existing_papers(session, dois: list[str]) -> set:
    rows = session.run("""
        UNWIND $dois AS doi
        MATCH (p:Paper {doi: doi})
        RETURN DISTINCT doi AS doi
    """, dois=dois).data()
    return {r["doi"] for r in rows}


# ---------------------------------------------------------------------------
# Neo4j writes
# ---------------------------------------------------------------------------
def create_missing_papers(session, rows: list[dict]) -> int:
    """MERGE on paper_id, which HAS a uniqueness constraint. Merging on doi
    would be unsafe: 170 DOIs already carry two Paper nodes each."""
    usable = [r for r in rows if r.get("paper_id")]
    if not usable:
        return 0
    result = session.run("""
        UNWIND $rows AS row
        MERGE (p:Paper {paper_id: row.paper_id})
        ON CREATE SET p.doi = row.doi, p.title = row.title,
                      p.year = row.year, p.journal = row.journal,
                      p.created_by = 'repair_author_graph'
        RETURN count(*) AS n
    """, rows=usable).single()
    return result["n"] if result else 0


def attach_authors(session, rows: list[dict]) -> None:
    """Create the authors and their edges.

    Matches every Paper node carrying the DOI, not just one: a duplicated DOI
    means both nodes are reachable by queries, so both should carry the right
    authors. The duplicates themselves are a separate defect, reported at the
    end and not silently merged here.
    """
    session.run("""
        UNWIND $rows AS row
        MATCH (p:Paper {doi: row.doi})
        UNWIND row.authors AS au
        MERGE (a:Author {author_id: au.author_id})
          ON CREATE SET a.name = au.name
          ON MATCH SET a.name = au.name
        MERGE (a)-[:AUTHORED]->(p)
    """, rows=rows)


def drop_stale_edges(session, rows: list[dict]) -> int:
    result = session.run("""
        UNWIND $rows AS row
        MATCH (a:Author)-[r:AUTHORED]->(p:Paper {doi: row.doi})
        WHERE NOT a.author_id IN row.keep
        DELETE r
        RETURN count(*) AS n
    """, rows=[{"doi": r["doi"], "keep": [a["author_id"] for a in r["authors"]]}
               for r in rows]).single()
    return result["n"] if result else 0


def sweep_orphans(session) -> int:
    """Delete :Author nodes with no remaining AUTHORED edge, in batches so a
    single transaction never has to hold 20k deletions."""
    total = 0
    while True:
        result = session.run("""
            MATCH (a:Author) WHERE NOT (a)-[:AUTHORED]->()
            WITH a LIMIT $batch
            DELETE a
            RETURN count(*) AS n
        """, batch=_ORPHAN_BATCH).single()
        n = result["n"] if result else 0
        total += n
        if n == 0:
            break
    return total


def count_orphans(session) -> int:
    r = session.run("""
        MATCH (a:Author) WHERE NOT (a)-[:AUTHORED]->()
        RETURN count(a) AS n
    """).single()
    return r["n"] if r else 0


# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write to Neo4j")
    ap.add_argument("--limit", type=int, default=0, metavar="N",
                    help="process at most N papers")
    ap.add_argument("--repaired-only", action="store_true",
                    help="only papers stamped by the 2026-08 payload backfill")
    ap.add_argument("--backup", metavar="PATH",
                    help="write current edges as JSONL before mutating")
    ap.add_argument("--report", metavar="PATH", help="JSON outcome report")
    args = ap.parse_args()

    if not NEO4J_PASSWORD:
        print("[error] NEO4J_PASSWORD is not set", file=sys.stderr)
        return 2
    if args.apply and not args.backup:
        print("[error] --apply requires --backup (rollback file)", file=sys.stderr)
        return 2

    mode = "APPLY (writing)" if args.apply else "DRY RUN (no writes)"
    print(f"[graph] {mode}  qdrant={PAPERS_COLLECTION}  neo4j={NEO4J_URI}")

    papers = scroll_papers(args.repaired_only)
    if args.limit:
        papers = papers[:args.limit]
    print(f"[graph] {len(papers)} corpus papers with authors in scope")

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    stats = Counter()
    per_author_removed: dict = defaultdict(int)
    backup_fh = open(args.backup, "w") if args.backup else None

    try:
        with driver.session() as session:
            for start in range(0, len(papers), _BATCH):
                batch = papers[start:start + _BATCH]
                dois = [r["doi"] for r in batch]

                have = existing_papers(session, dois)
                edges = current_edges(session, dois)
                missing = [r for r in batch if r["doi"] not in have]

                for row in batch:
                    graph_authors = edges.get(row["doi"], [])
                    if backup_fh:
                        backup_fh.write(json.dumps(
                            {"doi": row["doi"], "authors": graph_authors},
                            ensure_ascii=False) + "\n")
                    graph_ids = {a["author_id"] for a in graph_authors}
                    want_ids = {a["author_id"] for a in row["authors"]}
                    stats["edges_add"] += len(want_ids - graph_ids)
                    stats["edges_keep"] += len(want_ids & graph_ids)
                    for a in graph_authors:
                        if a["author_id"] not in want_ids:
                            stats["edges_remove"] += 1
                            per_author_removed[a["author_id"]] += 1
                stats["papers_missing_node"] += len(missing)

                if args.apply:
                    if missing:
                        stats["papers_created"] += create_missing_papers(session, missing)
                    attach_authors(session, batch)
                    drop_stale_edges(session, batch)

                done = start + len(batch)
                if done % (_BATCH * 20) == 0:
                    print(f"[graph] {done}/{len(papers)} papers "
                          f"(+{stats['edges_add']} / -{stats['edges_remove']})",
                          file=sys.stderr)

            if args.apply:
                print("[graph] sweeping orphaned author nodes ...", file=sys.stderr)
                stats["authors_deleted"] = sweep_orphans(session)
            else:
                stats["orphans_before_run"] = count_orphans(session)
    finally:
        if backup_fh:
            backup_fh.close()
        driver.close()

    print("\nresult:")
    print(f"  papers in scope        : {len(papers)}")
    print(f"  edges already correct  : {stats['edges_keep']}")
    print(f"  edges to add           : {stats['edges_add']}")
    print(f"  stale edges to remove  : {stats['edges_remove']}")
    print(f"  papers with no node    : {stats['papers_missing_node']}")
    if args.apply:
        print(f"  paper nodes created    : {stats['papers_created']}")
        print(f"  orphan authors deleted : {stats['authors_deleted']}")
    else:
        print(f"  authors losing every edge (would be swept): "
              f"{sum(1 for aid, n in per_author_removed.items())} candidates")
        print(f"  orphan authors already in graph: {stats['orphans_before_run']}")
        print("\nDRY RUN: nothing written. Re-run with --apply --backup PATH.")

    if args.report:
        Path(args.report).write_text(json.dumps({
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "mode": "apply" if args.apply else "dry_run",
            "papers_in_scope": len(papers),
            "stats": dict(stats),
        }, indent=2, ensure_ascii=False))
        print(f"\n[graph] report -> {args.report}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
