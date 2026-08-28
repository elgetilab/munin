#!/usr/bin/env python3
"""Mark corpus entries with/without usable full text, and recover what we can.

MEASURED 2026-08-28: 1,777 of 68,863 corpus entries (2.6%) have no resolvable
PDF. They are NOT junk and must not be bulk-deleted:
  854 are contributor-tagged (papers users uploaded into their group corpora)
  990 (56%) still carry an abstract, so `source` answers from it today
  1,593 carry a DOI, so most are re-fetchable
Checked against r1_staging (1,301 PDFs from the R1 restore): zero filename
matches, so these are genuinely absent rather than misfiled.

Two passes:
  --mark      set `has_fulltext` on every point so `search` can report the gap
              honestly in coverage_note instead of a miss looking like absence
  --recover   for missing entries WITH a doi, try Unpaywall (repository copies
              first) and save any real PDF into the corpus pdf directory

Both are additive and reversible. Nothing is deleted.

    PY=/opt/munin/services/pipeline/venv/bin/python
    $PY mark_and_recover_fulltext.py --mark
    $PY mark_and_recover_fulltext.py --recover --limit 300
"""
from __future__ import annotations

import argparse, asyncio, os, sys, time

import httpx
from qdrant_client import QdrantClient

SRC = os.getenv("PAPERS_COLLECTION", "papers_bge")
HOST_PDF = "/opt/munin/data/papers/pdf"
CACHE = os.getenv("FULLTEXT_CACHE", "/opt/munin/data/papers/fulltext")
UNPAYWALL_EMAIL = os.getenv("UNPAYWALL_EMAIL", "research@muninai.org")
BATCH = 512


def resolve_pdf(fp: str) -> str:
    if not fp:
        return ""
    if os.path.exists(fp):
        return fp
    cand = os.path.join(HOST_PDF, os.path.basename(fp))
    return cand if os.path.exists(cand) else ""


def all_rows(qc: QdrantClient) -> list:
    rows, off = [], None
    while True:
        pts, off = qc.scroll(collection_name=SRC, limit=5000, offset=off,
                             with_payload=True, with_vectors=False)
        if not pts:
            break
        rows.extend(pts)
        if off is None:
            break
    return rows


def do_mark(qc: QdrantClient) -> int:
    rows = all_rows(qc)
    yes = [p.id for p in rows if resolve_pdf((p.payload or {}).get("pdf_path") or "")]
    no = [p.id for p in rows if p.id not in set(yes)]
    print(f"corpus {len(rows):,}: has_fulltext true={len(yes):,} false={len(no):,} "
          f"({100*len(no)/max(len(rows),1):.1f}%)", flush=True)
    for label, ids, val in (("true", yes, True), ("false", no, False)):
        for i in range(0, len(ids), BATCH):
            qc.set_payload(SRC, payload={"has_fulltext": val},
                           points=ids[i:i + BATCH], wait=False)
        print(f"  marked {len(ids):,} as has_fulltext={label}", flush=True)
    return 0


async def recover(rows: list, limit: int, concurrency: int) -> tuple[int, int]:
    # Reimplemented rather than imported: `mcp.tools.source` pulls the whole
    # MCP package, and the pipeline venv has no jsonschema. Behaviour is kept
    # faithful to source.py -- repository copies ranked before publisher ones
    # (publishers 403 or serve an HTML interstitial), and the magic-byte check
    # so an anti-bot page returned with a 200 is never written as a "PDF".
    UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

    async def _unpaywall_pdf_urls(doi: str) -> list:
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as cl:
                r = await cl.get(f"https://api.unpaywall.org/v2/{doi}",
                                 params={"email": UNPAYWALL_EMAIL})
            if r.status_code != 200:
                return []
            locs = [l for l in (r.json().get("oa_locations") or []) if l.get("url_for_pdf")]
            def rank(l):
                host = l.get("host_type") or ""
                url = l.get("url_for_pdf") or ""
                return (0 if host == "repository" else 1,
                        0 if "ncbi.nlm.nih.gov" in url else 1)
            locs.sort(key=rank)
            return [l["url_for_pdf"] for l in locs]
        except Exception:
            return []

    async def _download_valid_pdf(url: str):
        try:
            async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as cl:
                r = await cl.get(url, headers={"User-Agent": UA})
            if r.status_code != 200:
                return None
            data = r.content
            return data if data[:5] == b"%PDF-" else None
        except Exception:
            return None

    cands = [p for p in rows
             if not resolve_pdf((p.payload or {}).get("pdf_path") or "")
             and (p.payload or {}).get("doi")]
    if limit:
        cands = cands[:limit]
    print(f"recovery candidates (missing PDF, has DOI): {len(cands):,}", flush=True)

    sem = asyncio.Semaphore(concurrency)
    got = failed = 0
    t0 = time.monotonic()

    async def one(p):
        nonlocal got, failed
        pl = p.payload or {}
        doi = pl.get("doi")
        dest = os.path.join(HOST_PDF, os.path.basename(pl.get("pdf_path") or f"doi_{doi}.pdf".replace("/", "_")))
        async with sem:
            try:
                urls = await _unpaywall_pdf_urls(doi)
            except Exception:
                urls = []
            for u in urls[:3]:
                try:
                    data = await _download_valid_pdf(u)
                except Exception:
                    data = None
                if data:
                    tmp = dest + ".tmp"
                    with open(tmp, "wb") as fh:
                        fh.write(data)
                    os.replace(tmp, dest)     # atomic
                    got += 1
                    return
            failed += 1

    step = concurrency * 5
    for i in range(0, len(cands), step):
        await asyncio.gather(*(one(p) for p in cands[i:i + step]))
        el = time.monotonic() - t0
        done = got + failed
        if done:
            print(f"  recovered {got:,} / attempted {done:,}  ({100*got/done:.0f}%)  "
                  f"{done/el:.2f}/s", flush=True)
    return got, failed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mark", action="store_true")
    ap.add_argument("--recover", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--concurrency", type=int, default=4)
    args = ap.parse_args()
    qc = QdrantClient(host="localhost", port=6333)
    if args.mark:
        do_mark(qc)
    if args.recover:
        got, failed = asyncio.run(recover(all_rows(qc), args.limit, args.concurrency))
        print(f"\nDONE recovered={got:,} failed={failed:,}", flush=True)
        if got:
            print("re-run --mark afterwards so has_fulltext reflects the recovery", flush=True)
    if not (args.mark or args.recover):
        ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
