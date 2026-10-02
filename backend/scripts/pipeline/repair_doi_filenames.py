#!/usr/bin/env python3
"""
Re-key papers whose DOI the old doi_*.pdf decoder mangled.

DRY RUN BY DEFAULT. Nothing is written without --apply.

The audit (audit_doi_filenames.py, report in docs/corpus-quality/) found 921
of 68,919 papers keyed to DOIs that do not exist: doi_*.pdf filenames map `/`
and `:` to `_`, the old decoder restored only the first `_`, and its result
overrode GROBID's DOI (`10.1093/molehr/3.5.431` -> `10.1093/molehr_3.5.431`).
Each mis-keyed row names a proposed DOI that Crossref resolves with a title
matching the stored one.

What a repair of one paper does, in order (each step is idempotent, so an
interrupted run is resumed by running it again):

  1. Qdrant papers collection. Point ids are sha256(doi.lower()), so the point
     is copied (vector + payload) to the id of the corrected DOI with
     `doi` replaced and `_doi_before_repair`, `_doi_repaired_at`,
     `_doi_repair_source` added, then the old point is deleted. Upsert
     before delete: a crash in between leaves both, never neither.
  2. Chunk collection. Chunk payloads whose `doi` is the old DOI get the new
     one (plus `_doi_before_repair`).
  3. Neo4j. If no :Paper node has the corrected DOI, the old node is renamed.
     If one exists (typically a stub created by another paper's CITES
     edge), every relationship of the old node moves onto it, properties it
     lacks are copied over, and the old node is deleted.
  4. The PDF's state sidecar, when there is one, gets the new `doi`. The PDF
     itself keeps its name: retrieval's get_pdf_path() already tries the
     `/`->`_` and `:`->`_` spellings, so the corrected DOI finds the same file.

Not repaired here, listed in the report instead:
  - merge: the corrected DOI is already another point (the same paper twice).
    Needs a contributor-preserving merge, a separate decision.
  - review: proposed_sim below --min-sim (default 0.5), and every row the
    audit could not settle (candidate-title-mismatch, mismatch, unresolved).

Safety:
  - Identity: only audit rows classified mis-keyed are candidates, and each
    is re-checked against the live point (its doi must still be the audited
    one) before anything is written.
  - Journal: before each paper is written, one JSON line with the full prior
    state (payload, vector, chunk ids, the graph relationships it moves) is
    appended to --journal, so any paper can be restored from the journal.
  - --snapshot takes Qdrant snapshots of both collections first.

Usage (pipeline venv, on hugin; Neo4j needs NEO4J_PASSWORD from cluster.env):
    ./repair_doi_filenames.py --audit AUDIT.json --report dry.json     # dry run
    ./repair_doi_filenames.py --audit AUDIT.json --no-graph ...        # Qdrant-only dry run
    ./repair_doi_filenames.py --audit AUDIT.json --apply --limit 20 --snapshot \
        --journal j.jsonl                                              # canary
    ./repair_doi_filenames.py --audit AUDIT.json --apply --journal j.jsonl  # rest
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

QDRANT_URL = os.getenv("QDRANT_URL") or (
    f"http://{os.getenv('QDRANT_HOST', 'localhost')}:{os.getenv('QDRANT_PORT', '6333')}")
PAPERS_COLLECTION = os.getenv("PAPERS_COLLECTION", "papers_bge")
CHUNKS_COLLECTION = os.getenv("CHUNKS_COLLECTION", "papers_chunks")
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://127.0.0.1:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")
PDF_DIR = os.getenv("PAPERS_PDF_DIR", "/opt/munin/data/papers/pdf")
SOURCE = "doi-filename-repair-2026-10"

_REL_TYPE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def point_id(doi: str) -> int:
    """The pipeline's id for a DOI-keyed point (paper_pipeline._store_vectors)."""
    return int(hashlib.sha256(doi.lower().encode()).hexdigest()[:16], 16)


def norm(doi: str) -> str:
    """The graph's key for a DOI (paper_pipeline._norm_doi)."""
    return doi.strip().lower()


# ----------------------------------------------------------------------------
# Qdrant (REST; the same calls work against a throwaway test instance)
# ----------------------------------------------------------------------------

def _q(method: str, path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(
        QDRANT_URL + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=120))


def q_get(collection: str, pid: int, vector: bool = False) -> dict | None:
    res = _q("POST", f"/collections/{collection}/points",
             {"ids": [pid], "with_payload": True, "with_vector": vector})["result"]
    return res[0] if res else None


def q_chunk_ids(old_doi: str) -> list:
    ids, offset = [], None
    while True:
        body = {"limit": 1000, "with_payload": False, "with_vector": False,
                "filter": {"must": [{"key": "doi", "match": {"value": old_doi}}]}}
        if offset is not None:
            body["offset"] = offset
        res = _q("POST", f"/collections/{CHUNKS_COLLECTION}/points/scroll", body)["result"]
        ids += [p["id"] for p in res["points"]]
        offset = res.get("next_page_offset")
        if offset is None:
            return ids


def q_snapshot(collection: str) -> str:
    return _q("POST", f"/collections/{collection}/snapshots?wait=true")["result"]["name"]


# ----------------------------------------------------------------------------
# Neo4j
# ----------------------------------------------------------------------------

class Graph:
    def __init__(self) -> None:
        from neo4j import GraphDatabase
        self.driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
        self.driver.verify_connectivity()

    def close(self) -> None:
        self.driver.close()

    def _run(self, cypher: str, **params) -> list[dict]:
        with self.driver.session() as s:
            return [r.data() for r in s.run(cypher, **params)]

    def node(self, doi: str) -> dict | None:
        rows = self._run("MATCH (p:Paper {doi: $doi}) RETURN properties(p) AS props, "
                         "COUNT { (p)--() } AS degree", doi=doi)
        if len(rows) > 1:
            raise RuntimeError(f"{len(rows)} :Paper nodes with doi {doi!r}")
        return rows[0] if rows else None

    def rels(self, doi: str) -> list[dict]:
        """Every relationship of the node, as data a restore can replay."""
        return self._run(
            "MATCH (p:Paper {doi: $doi})-[r]-(x) "
            "RETURN type(r) AS type, startNode(r) = p AS outgoing, elementId(x) AS other, "
            "labels(x) AS other_labels, x.doi AS other_doi, properties(r) AS props", doi=doi)

    def rename(self, old: str, new: str) -> None:
        self._run("MATCH (p:Paper {doi: $old}) SET p.doi = $new, p.doi_key = $new, "
                  "p.doi_before_repair = $old", old=old, new=new)

    def merge_into(self, old: str, new: str) -> None:
        """Move every relationship of `old` onto the existing `new` node, copy
        the properties `new` lacks, delete `old`. No APOC: relationship types
        come from the graph and are checked before being spliced into Cypher."""
        types = {(r["type"], r["outgoing"]) for r in self.rels(old)}
        for rtype, outgoing in types:
            if not _REL_TYPE_RE.match(rtype):
                raise RuntimeError(f"unexpected relationship type {rtype!r}")
            if outgoing:
                cypher = (f"MATCH (o:Paper {{doi: $old}})-[r:`{rtype}`]->(x) "
                          f"MATCH (n:Paper {{doi: $new}}) WHERE x <> n "
                          f"MERGE (n)-[r2:`{rtype}`]->(x) SET r2 += properties(r) DELETE r")
            else:
                cypher = (f"MATCH (x)-[r:`{rtype}`]->(o:Paper {{doi: $old}}) "
                          f"MATCH (n:Paper {{doi: $new}}) WHERE x <> n "
                          f"MERGE (x)-[r2:`{rtype}`]->(n) SET r2 += properties(r) DELETE r")
            self._run(cypher, old=old, new=new)
        old_props = (self.node(old) or {}).get("props") or {}
        new_props = (self.node(new) or {}).get("props") or {}
        fill = {k: v for k, v in old_props.items()
                if k not in ("doi", "doi_key") and new_props.get(k) in (None, "", [])}
        fill.update(doi_key=new, doi_before_repair=old)
        # Any relationship left joined old and new directly; it goes with old.
        self._run("MATCH (n:Paper {doi: $new}) SET n += $fill WITH n "
                  "MATCH (o:Paper {doi: $old}) DETACH DELETE o", old=old, new=new, fill=fill)


# ----------------------------------------------------------------------------
# Planning
# ----------------------------------------------------------------------------

def sidecar_path(pdf_path: str | None) -> Path | None:
    if not pdf_path:
        return None
    p = Path(PDF_DIR) / Path(pdf_path).name
    sc = p.with_name(f"{p.stem}.state.json")
    return sc if sc.is_file() else None


def plan(audit_rows: list[dict], min_sim: float, graph: Graph | None) -> list[dict]:
    plans = []
    for r in audit_rows:
        base = {"point_id": r["point_id"], "old_doi": r["doi"], "new_doi": r.get("proposed_doi"),
                "audit_status": r["status"], "proposed_sim": r.get("proposed_sim"),
                "title": r.get("title")}
        if r["status"] == "ok":
            continue
        if r["status"] != "mis-keyed":
            plans.append({**base, "action": "review", "why": f"audit: {r['status']}"})
            continue
        if (r.get("proposed_sim") or 0) < min_sim:
            plans.append({**base, "action": "review", "why": f"proposed_sim < {min_sim}"})
            continue
        live = q_get(PAPERS_COLLECTION, r["point_id"])
        new_id = point_id(r["proposed_doi"])
        if live is None:
            if q_get(PAPERS_COLLECTION, new_id):
                plans.append({**base, "action": "done", "why": "already repaired"})
            else:
                plans.append({**base, "action": "review", "why": "point no longer exists"})
            continue
        if (live["payload"].get("doi") or "").strip() != r["doi"]:
            plans.append({**base, "action": "review", "why": "point changed since the audit"})
            continue
        if new_id != r["point_id"] and q_get(PAPERS_COLLECTION, new_id):
            plans.append({**base, "action": "merge", "why": "corrected DOI is already a point"})
            continue
        p = {**base, "action": "repair", "new_point_id": new_id,
             "chunks": len(q_chunk_ids(r["doi"])),
             "sidecar": str(sidecar_path(live["payload"].get("pdf_path")) or "") or None}
        if graph is not None:
            old_node, new_node = graph.node(norm(r["doi"])), graph.node(norm(r["proposed_doi"]))
            p["graph"] = ("absent" if not old_node else
                          "merge-into-existing" if new_node else "rename")
            p["graph_old_degree"] = (old_node or {}).get("degree")
            p["graph_new_degree"] = (new_node or {}).get("degree")
        plans.append(p)
    return plans


# ----------------------------------------------------------------------------
# Apply
# ----------------------------------------------------------------------------

def repair_one(p: dict, graph: Graph, journal) -> None:
    old, new = p["old_doi"], p["new_doi"]
    live = q_get(PAPERS_COLLECTION, p["point_id"], vector=True)
    if live is None or (live["payload"].get("doi") or "").strip() != old:
        raise RuntimeError("point changed since planning; re-run the dry run")
    chunk_ids = q_chunk_ids(old)
    old_key, new_key = norm(old), norm(new)
    journal.write(json.dumps({
        "at": now(), "point_id": p["point_id"], "new_point_id": p["new_point_id"],
        "old_doi": old, "new_doi": new, "payload": live["payload"], "vector": live["vector"],
        "chunk_ids": chunk_ids, "graph_old_node": graph.node(old_key),
        "graph_new_node": graph.node(new_key), "graph_rels": graph.rels(old_key),
    }) + "\n")
    journal.flush()
    os.fsync(journal.fileno())

    # 1. papers: upsert under the corrected id, then drop the old point
    payload = {**live["payload"], "doi": new, "_doi_before_repair": old,
               "_doi_repaired_at": now(), "_doi_repair_source": SOURCE}
    _q("PUT", f"/collections/{PAPERS_COLLECTION}/points?wait=true",
       {"points": [{"id": p["new_point_id"], "vector": live["vector"], "payload": payload}]})
    if p["new_point_id"] != p["point_id"]:
        _q("POST", f"/collections/{PAPERS_COLLECTION}/points/delete?wait=true",
           {"points": [p["point_id"]]})
    # 2. chunks
    if chunk_ids:
        _q("POST", f"/collections/{CHUNKS_COLLECTION}/points/payload?wait=true",
           {"payload": {"doi": new, "_doi_before_repair": old}, "points": chunk_ids})
    # 3. graph
    if graph.node(old_key):
        if graph.node(new_key):
            graph.merge_into(old_key, new_key)
        else:
            graph.rename(old_key, new_key)
    # 4. state sidecar
    sc = sidecar_path(live["payload"].get("pdf_path"))
    if sc:
        doc = json.loads(sc.read_text())
        if doc.get("doi") != new:
            doc.update(doi=new, doi_before_repair=doc.get("doi"))
            tmp = sc.with_suffix(sc.suffix + ".tmp")
            tmp.write_text(json.dumps(doc, indent=2))
            os.replace(tmp, sc)


# ----------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--audit", required=True, help="audit_doi_filenames.py --report JSON")
    ap.add_argument("--min-sim", type=float, default=0.5,
                    help="lowest proposed_sim repaired automatically (default 0.5)")
    ap.add_argument("--report", help="write the plan / outcome JSON here")
    ap.add_argument("--review-csv", help="write the rows left for a human here")
    ap.add_argument("--limit", type=int, help="repair at most N papers (canary)")
    ap.add_argument("--no-graph", action="store_true", help="dry run without Neo4j")
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    ap.add_argument("--snapshot", action="store_true", help="snapshot both collections first")
    ap.add_argument("--journal", help="append-only restore journal (required with --apply)")
    args = ap.parse_args()
    if args.apply and (args.no_graph or not args.journal):
        ap.error("--apply needs Neo4j (no --no-graph) and --journal")

    rows = json.loads(Path(args.audit).read_text())["rows"]
    graph = None if args.no_graph else Graph()
    plans = plan(rows, args.min_sim, graph)
    summary = {"generated_at": now(), "mode": "apply" if args.apply else "dry-run",
               "audit": args.audit, "min_sim": args.min_sim,
               "by_action": dict(Counter(p["action"] for p in plans)),
               "graph": dict(Counter(p.get("graph") for p in plans if p["action"] == "repair")),
               "chunks_to_update": sum(p.get("chunks", 0) for p in plans if p["action"] == "repair"),
               "sidecars_to_update": sum(1 for p in plans if p.get("sidecar"))}

    if args.apply:
        if args.snapshot:
            summary["snapshots"] = [q_snapshot(PAPERS_COLLECTION), q_snapshot(CHUNKS_COLLECTION)]
        todo = [p for p in plans if p["action"] == "repair"][: args.limit]
        done, failed = 0, []
        with open(args.journal, "a") as journal:
            for p in todo:
                try:
                    repair_one(p, graph, journal)
                    p["outcome"] = "repaired"
                    done += 1
                except Exception as e:  # noqa: BLE001 - keep going, report it
                    p["outcome"] = f"failed: {type(e).__name__}: {e}"
                    failed.append(p["old_doi"])
        summary.update(repaired=done, failed=len(failed))

    if graph is not None:
        graph.close()
    print(json.dumps(summary, indent=2))
    if args.report:
        Path(args.report).write_text(json.dumps({"summary": summary, "plans": plans}, indent=1))
    if args.review_csv:
        keep = [p for p in plans if p["action"] in ("review", "merge")]
        with open(args.review_csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["action", "why", "old_doi", "new_doi",
                                              "proposed_sim", "audit_status", "point_id", "title"],
                               extrasaction="ignore")
            w.writeheader()
            w.writerows(keep)
    return 1 if summary.get("failed") else 0


if __name__ == "__main__":
    sys.exit(main())
