"""Track C2b shadow corpus: build both shadow collections from the FROZEN
removed-DOI set, or drop them again. Minutes, not hours.

    PYTHONPATH=... $PY -m munin_bench.abstention.shadow_corpus build [--force]
    PYTHONPATH=... $PY -m munin_bench.abstention.shadow_corpus verify
    PYTHONPATH=... $PY -m munin_bench.abstention.shadow_corpus drop

`build` snapshots `papers_bge` and `papers_chunks` inside Qdrant, recovers each
snapshot under the shadow name (`papers_shadow`, `papers_chunks_shadow`), then
deletes the frozen C2 source papers from both: by point id in the paper
collection, by indexed `paper_id` / `doi` filter in the chunk collection. It
finishes with the leakage check (0 matching points in either shadow) and
writes a JSON record beside the C2 runs.

Why both collections: the chunk-level evidence layer (papers_chunks,
2026-08-30) reads full text by paper_id, so a shadow of papers_bge alone
leaves the removed papers reachable through `source(mode=evidence)` and the
absent arm measures nothing (RESULTS.md 2026-09-15). Why snapshot-recover
rather than a scroll copy: 1.43M chunk points take seconds this way and hours
the other. Why the frozen set: `build_shadow.main()` re-selects questions from
the CURRENT corpus and must not be run; the paired comparison needs the same
50 questions every time.

Talks to Qdrant's REST API with urllib only, so it runs from the bench deps or
bare python3.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

from .corpus import normalize_doi

_HERE = os.path.dirname(__file__)
_QSET = os.path.join(_HERE, "c2_questions.json")
_RUNS = os.path.abspath(os.path.join(_HERE, "..", "..", "c2_runs"))

QDRANT = os.getenv("QDRANT_URL", f"http://{os.getenv('QDRANT_HOST', '127.0.0.1')}:{os.getenv('QDRANT_PORT', '6333')}")
PAIRS = (("papers_bge", "papers_shadow"), ("papers_chunks", "papers_chunks_shadow"))
# Snapshots live inside the Qdrant container; recovery takes a container path.
SNAPSHOT_ROOT = "/qdrant/snapshots"


def _req(method: str, path: str, body: dict | None = None, timeout: float = 600.0) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(QDRANT + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise SystemExit(f"[shadow] {method} {path} -> HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}")


def _exists(coll: str) -> bool:
    try:
        _req("GET", f"/collections/{coll}", timeout=30)
        return True
    except SystemExit:
        return False


def _count(coll: str, flt: dict | None = None) -> int:
    body = {"exact": True}
    if flt:
        body["filter"] = flt
    return _req("POST", f"/collections/{coll}/points/count", body, timeout=300)["result"]["count"]


def frozen_removed_dois() -> list[str]:
    d = json.load(open(_QSET))
    dois = d["meta"]["removed_dois"]
    if not dois:
        raise SystemExit(f"[shadow] {_QSET} has no removed_dois")
    return [normalize_doi(x) for x in dois]


def resolve_removed(dois: list[str]) -> tuple[list, list[str], list[str]]:
    """Scroll papers_bge once (payload only) -> point ids, paper_ids and the
    RAW doi strings of the frozen set, matched on the normalised form."""
    want = set(dois)
    ids, paper_ids, raw = [], [], []
    offset = None
    while True:
        body = {"limit": 2000, "with_payload": ["doi", "paper_id"], "with_vector": False}
        if offset is not None:
            body["offset"] = offset
        res = _req("POST", "/collections/papers_bge/points/scroll", body, timeout=300)["result"]
        for p in res["points"]:
            pl = p.get("payload") or {}
            doi = pl.get("doi") or ""
            if normalize_doi(doi) in want:
                ids.append(p["id"])
                if pl.get("paper_id"):
                    paper_ids.append(pl["paper_id"])
                raw.append(doi)
        offset = res.get("next_page_offset")
        if offset is None:
            break
    return ids, sorted(set(paper_ids)), sorted(set(raw))


def removed_filter(paper_ids: list[str], raw_dois: list[str], dois: list[str]) -> dict:
    return {"should": [
        {"key": "paper_id", "match": {"any": paper_ids}},
        {"key": "doi", "match": {"any": sorted(set(raw_dois) | set(dois))}},
    ]}


def snapshot_recover(src: str, dst: str, keep_snapshot: bool) -> dict:
    t0 = time.time()
    snap = _req("POST", f"/collections/{src}/snapshots?wait=true", timeout=1800)["result"]["name"]
    t1 = time.time()
    _req("PUT", f"/collections/{dst}/snapshots/recover?wait=true",
         {"location": f"file://{SNAPSHOT_ROOT}/{src}/{snap}"}, timeout=1800)
    t2 = time.time()
    if not keep_snapshot:
        _req("DELETE", f"/collections/{src}/snapshots/{snap}?wait=true", timeout=600)
    print(f"[shadow] {src} -> {dst}: snapshot {t1-t0:.1f}s, recover {t2-t1:.1f}s"
          f"{'' if keep_snapshot else ', source snapshot deleted'}")
    return {"src": src, "dst": dst, "snapshot": snap, "snapshot_s": round(t1 - t0, 1),
            "recover_s": round(t2 - t1, 1)}


def build(force: bool, keep_snapshots: bool) -> dict:
    dois = frozen_removed_dois()
    for _, dst in PAIRS:
        if _exists(dst):
            if not force:
                raise SystemExit(f"[shadow] {dst} exists; pass --force to rebuild (or run `verify`)")
            _req("DELETE", f"/collections/{dst}", timeout=300)
            print(f"[shadow] dropped existing {dst}")
    rec = {"built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "n_removed_dois": len(dois),
           "steps": []}
    for src, dst in PAIRS:
        rec["steps"].append(snapshot_recover(src, dst, keep_snapshots))
    ids, paper_ids, raw = resolve_removed(dois)
    print(f"[shadow] resolved {len(dois)} frozen DOIs -> {len(ids)} paper points, {len(paper_ids)} paper_ids")
    if len(ids) < len(dois):
        print(f"[shadow] WARNING: {len(dois) - len(ids)} frozen DOIs not found in papers_bge "
              f"(already absent from the corpus; the absent arm is unaffected for them)")
    flt = removed_filter(paper_ids, raw, dois)
    before_p, before_c = _count("papers_shadow"), _count("papers_chunks_shadow")
    if ids:
        _req("POST", "/collections/papers_shadow/points/delete?wait=true", {"points": ids}, timeout=600)
    _req("POST", "/collections/papers_chunks_shadow/points/delete?wait=true", {"filter": flt}, timeout=1800)
    after_p, after_c = _count("papers_shadow"), _count("papers_chunks_shadow")
    rec.update({"papers_bge": _count("papers_bge"), "papers_shadow": after_p,
                "papers_removed": before_p - after_p,
                "papers_chunks": _count("papers_chunks"), "papers_chunks_shadow": after_c,
                "chunks_removed": before_c - after_c,
                "removed_point_ids": len(ids), "removed_paper_ids": paper_ids})
    print(f"[shadow] papers_shadow {after_p:,} (removed {before_p - after_p}); "
          f"papers_chunks_shadow {after_c:,} (removed {before_c - after_c:,})")
    rec["leakage"] = verify(dois, paper_ids, raw, quiet=True)
    os.makedirs(_RUNS, exist_ok=True)
    out = os.path.join(_RUNS, f"shadow_build.{rec['built_at'][:10]}.json")
    json.dump(rec, open(out, "w"), indent=2)
    print(f"[shadow] -> {out}")
    if rec["leakage"]["papers_shadow"] or rec["leakage"]["papers_chunks_shadow"]:
        raise SystemExit("[shadow] LEAKAGE: removed papers still reachable in a shadow collection")
    return rec


def verify(dois: list[str] | None = None, paper_ids: list[str] | None = None,
           raw: list[str] | None = None, quiet: bool = False) -> dict:
    dois = dois or frozen_removed_dois()
    if paper_ids is None or raw is None:
        _, paper_ids, raw = resolve_removed(dois)
    flt = removed_filter(paper_ids, raw, dois)
    out = {}
    for src, dst in PAIRS:
        if not _exists(dst):
            out[dst] = None
            if not quiet:
                print(f"[shadow] {dst}: MISSING")
            continue
        leak, in_src = _count(dst, flt), _count(src, flt)
        out[dst] = leak
        out[src + "_matching"] = in_src
        if not quiet:
            print(f"[shadow] {dst}: {_count(dst):,} points, {leak} matching removed papers "
                  f"(vs {in_src:,} in {src})  {'OK' if leak == 0 else 'LEAK'}")
    return out


def drop(keep_snapshot: bool = True) -> None:
    for _, dst in PAIRS:
        if not _exists(dst):
            print(f"[shadow] {dst}: already absent")
            continue
        if keep_snapshot and dst == "papers_shadow":
            snap = _req("POST", f"/collections/{dst}/snapshots?wait=true", timeout=1800)["result"]["name"]
            print(f"[shadow] kept snapshot {dst}/{snap}")
        _req("DELETE", f"/collections/{dst}?wait=true", timeout=600)
        print(f"[shadow] dropped {dst}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--force", action="store_true", help="drop and rebuild existing shadow collections")
    b.add_argument("--keep-snapshots", action="store_true", help="keep the source snapshots (GBs)")
    sub.add_parser("verify")
    d = sub.add_parser("drop")
    d.add_argument("--no-snapshot", action="store_true", help="do not snapshot papers_shadow before dropping")
    args = ap.parse_args()
    if args.cmd == "build":
        build(args.force, args.keep_snapshots)
    elif args.cmd == "verify":
        res = verify()
        leaks = [k for k, v in res.items() if k.endswith("_shadow") and v]
        if leaks:
            print(f"[shadow] LEAK in {leaks}")
            return 1
        if any(res.get(dst) is None for _, dst in PAIRS):
            return 2
    elif args.cmd == "drop":
        drop(keep_snapshot=not args.no_snapshot)
    return 0


if __name__ == "__main__":
    sys.exit(main())
