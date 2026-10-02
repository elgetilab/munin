"""
repair_doi_filenames.py end to end against THROWAWAY Qdrant and Neo4j.

Never point this at production: it creates and drops its own collections.
Start the stores with plain `docker run` (not compose), for example:

    docker run -d --rm --name doi-test-qdrant -p 127.0.0.1:16333:6333 qdrant/qdrant:latest
    docker run -d --rm --name doi-test-neo4j -p 127.0.0.1:17687:7687 \
        -e NEO4J_AUTH=neo4j/testpassword neo4j:5-community
    QDRANT_URL=http://127.0.0.1:16333 NEO4J_URI=bolt://127.0.0.1:17687 \
    NEO4J_PASSWORD=testpassword PAPERS_PDF_DIR=/tmp/doi-test-pdf \
        /opt/munin/services/pipeline/venv/bin/python3 tests/test_repair_doi_filenames_live.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "repair_doi_filenames.py"
assert "16333" in os.environ.get("QDRANT_URL", ""), "refusing: QDRANT_URL is not the test instance"
assert "17687" in os.environ.get("NEO4J_URI", ""), "refusing: NEO4J_URI is not the test instance"
os.environ["PAPERS_COLLECTION"] = "test_papers"
os.environ["CHUNKS_COLLECTION"] = "test_chunks"
sys.path.insert(0, str(HERE.parent))
import repair_doi_filenames as R  # noqa: E402

results: list[bool] = []


def check(name, ok, detail=""):
    ok = bool(ok)
    results.append(ok)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{(' - ' + str(detail)) if detail and not ok else ''}")


# --- fixture ------------------------------------------------------------------
A_OLD, A_NEW = "10.1093/nar_10.21.6553", "10.1093/nar/10.21.6553"      # graph: merge into stub
B_OLD, B_NEW = "10.1023/A_1008289724077", "10.1023/A:1008289724077"    # graph: rename
C_OLD, C_NEW = "10.1093/nar_gkl546", "10.1093/nar/gkl546"              # dup: merge, untouched
D_OLD, D_NEW = "10.1093/hmg_11.10.1153", "10.1093/hmg/11.10.1153"      # low sim: review
pid = R.point_id

for c in ("test_papers", "test_chunks"):
    try:
        R._q("DELETE", f"/collections/{c}")
    except Exception:
        pass
    R._q("PUT", f"/collections/{c}", {"vectors": {"size": 4, "distance": "Cosine"}})

pdf_dir = Path(os.environ["PAPERS_PDF_DIR"])
pdf_dir.mkdir(parents=True, exist_ok=True)
(pdf_dir / "doi_10.1093_nar_10.21.6553.state.json").write_text(json.dumps({"doi": A_OLD, "state": "live"}))

points = [(pid(d), [0.1 * i, 0.2, 0.3, 0.4], {"doi": d, "title": f"Paper {d}",
           "pdf_path": f"/opt/munin/data/papers/pdf/doi_{d.replace('/', '_')}.pdf",
           "contributors": [{"email": "u@x"}]})
          for i, d in enumerate((A_OLD, B_OLD, C_OLD, C_NEW, D_OLD), 1)]
R._q("PUT", "/collections/test_papers/points?wait=true",
     {"points": [{"id": i, "vector": v, "payload": p} for i, v, p in points]})
R._q("PUT", "/collections/test_chunks/points?wait=true",
     {"points": [{"id": n, "vector": [1, 0, 0, 0], "payload": {"doi": A_OLD, "chunk": n}} for n in (1, 2, 3)]
      + [{"id": 4, "vector": [1, 0, 0, 0], "payload": {"doi": "10.1/other", "chunk": 0}}]})

g = R.Graph()
g._run("MATCH (n) DETACH DELETE n")
g._run("""
CREATE (a:Paper {doi: $a_old, doi_key: $a_old, title: 'A full', paper_id: 'pa'})
CREATE (stub:Paper {doi: $a_new, doi_key: $a_new})
CREATE (au:Author {author_id: 'au1', name: 'Ada'})-[:AUTHORED]->(a)
CREATE (a)-[:CITES]->(:Paper {doi: '10.1/x', doi_key: '10.1/x'})
CREATE (:Paper {doi: '10.1/y', doi_key: '10.1/y'})-[:CITES]->(a)
CREATE (:Paper {doi: '10.1/z', doi_key: '10.1/z'})-[:CITES]->(stub)
CREATE (:Contributor {email: 'u@x'})-[:CONTRIBUTED {at: 't'}]->(a)
CREATE (b:Paper {doi: $b_old, doi_key: $b_old, title: 'B'})
CREATE (:Author {author_id: 'au2', name: 'Bo'})-[:AUTHORED]->(b)
""", a_old=A_OLD.lower(), a_new=A_NEW.lower(), b_old=B_OLD.lower())

audit = {"rows": [
    {"point_id": pid(A_OLD), "doi": A_OLD, "proposed_doi": A_NEW, "status": "mis-keyed", "proposed_sim": 1.0, "title": "A"},
    {"point_id": pid(B_OLD), "doi": B_OLD, "proposed_doi": B_NEW, "status": "mis-keyed", "proposed_sim": 0.9, "title": "B"},
    {"point_id": pid(C_OLD), "doi": C_OLD, "proposed_doi": C_NEW, "status": "mis-keyed", "proposed_sim": 1.0, "title": "C"},
    {"point_id": pid(D_OLD), "doi": D_OLD, "proposed_doi": D_NEW, "status": "mis-keyed", "proposed_sim": 0.4, "title": "D"},
    {"point_id": 1, "doi": "10.1007/x_1", "status": "ok"},
    {"point_id": 2, "doi": "10.1088/q_1", "status": "candidate-title-mismatch", "proposed_doi": "10.1088/q/1"},
]}
tmp = Path(tempfile.mkdtemp(prefix="doi-repair-test-"))
(tmp / "audit.json").write_text(json.dumps(audit))


def run(*extra):
    out = subprocess.run([sys.executable, str(SCRIPT), "--audit", str(tmp / "audit.json"),
                          "--report", str(tmp / "r.json"), *extra],
                         capture_output=True, text=True, env=os.environ)
    if out.returncode not in (0, 1):
        print(out.stdout, out.stderr)
    return json.loads((tmp / "r.json").read_text())


# --- dry run --------------------------------------------------------------------
before = R._q("POST", "/collections/test_papers/points/count", {})["result"]["count"]
rep = run()
plans = {p["old_doi"]: p for p in rep["plans"]}
check("dry run: A planned as repair with a graph merge",
      plans[A_OLD]["action"] == "repair" and plans[A_OLD]["graph"] == "merge-into-existing", plans[A_OLD])
check("dry run: B planned as repair with a graph rename",
      plans[B_OLD]["action"] == "repair" and plans[B_OLD]["graph"] == "rename", plans[B_OLD])
check("dry run: duplicate is a merge, low sim and unsettled rows go to review",
      plans[C_OLD]["action"] == "merge" and plans[D_OLD]["action"] == "review"
      and plans["10.1088/q_1"]["action"] == "review" and "10.1007/x_1" not in plans)
check("dry run: A's 3 chunks and its sidecar counted",
      plans[A_OLD]["chunks"] == 3 and plans[A_OLD]["sidecar"])
check("dry run writes nothing",
      R._q("POST", "/collections/test_papers/points/count", {})["result"]["count"] == before
      and R.q_get("test_papers", pid(A_OLD)) is not None)

# --- apply ---------------------------------------------------------------------------
out = subprocess.run([sys.executable, str(SCRIPT), "--audit", str(tmp / "audit.json"), "--apply",
                      "--journal", str(tmp / "j.jsonl")], capture_output=True, text=True, env=os.environ)
check("apply refuses without --allow-ids", out.returncode == 2 and "--allow-ids" in out.stderr
      and R.q_get("test_papers", pid(A_OLD)) is not None)
(tmp / "allow-b.txt").write_text(f"{pid(B_OLD)}\n")
rep = run("--apply", "--journal", str(tmp / "j.jsonl"), "--allow-ids", str(tmp / "allow-b.txt"))
check("allow-list: only B is repaired, A is left for review",
      rep["summary"].get("repaired") == 1 and R.q_get("test_papers", pid(A_OLD)) is not None
      and R.q_get("test_papers", pid(B_NEW)) is not None)
(tmp / "allow.txt").write_text(f"{pid(A_OLD)}\n{pid(B_OLD)}\n")
rep = run("--apply", "--journal", str(tmp / "j.jsonl"), "--allow-ids", str(tmp / "allow.txt"))
check("apply: A repaired (B already done), 0 failed", rep["summary"].get("repaired") == 1 and rep["summary"].get("failed") == 0,
      rep["summary"])
a_new = R.q_get("test_papers", pid(A_NEW), vector=True)
check("papers: A moved to the corrected id with its vector, payload and provenance",
      a_new and a_new["payload"]["doi"] == A_NEW and a_new["payload"]["_doi_before_repair"] == A_OLD
      and a_new["payload"]["contributors"] == [{"email": "u@x"}] and abs(a_new["vector"][1] - 0.2 / (0.1**2+0.2**2+0.3**2+0.4**2) ** 0.5) < 1e-3)
check("papers: old A point gone", R.q_get("test_papers", pid(A_OLD)) is None)
check("papers: duplicate C and review D untouched",
      R.q_get("test_papers", pid(C_OLD)) is not None and R.q_get("test_papers", pid(D_OLD))["payload"]["doi"] == D_OLD)
chunks = R._q("POST", "/collections/test_chunks/points", {"ids": [1, 2, 3, 4], "with_payload": True})["result"]
check("chunks: A's chunks re-keyed, others untouched",
      sorted(c["payload"]["doi"] for c in chunks) == sorted([A_NEW] * 3 + ["10.1/other"]))
node = g.node(A_NEW.lower())
rels = {(r["type"], r["outgoing"], r["other_doi"]) for r in g.rels(A_NEW.lower())}
check("graph: old A node gone, merged into the stub", g.node(A_OLD.lower()) is None and node is not None)
check("graph: every relationship moved (authored, cites out/in, contributed, the stub's own)",
      {("AUTHORED", False, None), ("CITES", True, "10.1/x"), ("CITES", False, "10.1/y"),
       ("CITES", False, "10.1/z"), ("CONTRIBUTED", False, None)} <= rels, rels)
check("graph: missing properties copied onto the stub",
      node["props"].get("title") == "A full" and node["props"].get("doi_before_repair") == A_OLD.lower())
check("graph: B renamed in place",
      g.node(B_OLD.lower()) is None and g.node(B_NEW.lower())["props"]["title"] == "B"
      and {r["type"] for r in g.rels(B_NEW.lower())} == {"AUTHORED"})
sc = json.loads((pdf_dir / "doi_10.1093_nar_10.21.6553.state.json").read_text())
check("sidecar: doi updated, previous kept", sc["doi"] == A_NEW and sc["doi_before_repair"] == A_OLD)
journal = [json.loads(l) for l in (tmp / "j.jsonl").read_text().splitlines()]
check("journal: one restorable entry per repair (payload, vector, chunks, graph)",
      len(journal) == 2 and all(j["vector"] and j["payload"] and "graph_rels" in j for j in journal)
      and len(next(j for j in journal if j["old_doi"] == A_OLD)["chunk_ids"]) == 3)

# --- idempotent re-run -------------------------------------------------------------
rep = run("--apply", "--journal", str(tmp / "j.jsonl"), "--allow-ids", str(tmp / "allow.txt"))
plans = {p["old_doi"]: p for p in rep["plans"]}
check("re-run: repaired rows report done, nothing written twice",
      plans[A_OLD]["action"] == "done" and plans[B_OLD]["action"] == "done"
      and rep["summary"].get("repaired") == 0
      and len((tmp / "j.jsonl").read_text().splitlines()) == 2)

g.close()
print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
