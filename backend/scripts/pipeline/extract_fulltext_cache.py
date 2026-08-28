#!/usr/bin/env python3
"""Phase 1 of the chunk-index build: extract paper full text to a disk cache.

Split out from build_chunk_index.py because the two costs are separable and
very unequal (measured 2026-08-28):

    GROBID extraction  ~14.5 h for the corpus, CPU only, no GPU
    embedding          ~44 min for the corpus, GPU only (564 vs 2.3 chunks/s)

Running extraction in daylight and embedding in the nightly vLLM downtime turns
a multi-night job into one day plus minutes. The cache is also a win in its own
right: `source` currently re-parses a PDF through GROBID on EVERY read, because
/opt/munin/data/papers/processed holds ingest metadata, not text.

    PY=/opt/munin/services/pipeline/venv/bin/python
    $PY extract_fulltext_cache.py --tagged-first --concurrency 4

Resumable and idempotent: a paper whose cache file exists is skipped.
"""
from __future__ import annotations

import argparse, asyncio, json, os, re, sys, time
from datetime import datetime, timedelta

import httpx
from qdrant_client import QdrantClient

GROBID_URL = os.getenv("GROBID_URL", "http://127.0.0.1:8070")
SRC = os.getenv("PAPERS_COLLECTION", "papers_bge")
HOST_PDF = "/opt/munin/data/papers/pdf"
CACHE = os.getenv("FULLTEXT_CACHE", "/opt/munin/data/papers/fulltext")


def resolve_pdf(fp: str) -> str:
    """Absolute host path for a payload `pdf_path`, or "".

    The corpus stores two conventions: crawler rows hold host paths, contributor
    uploads hold CONTAINER paths (/papers/...). Trusting the raw value resolved
    only 4% of tagged papers; basename against the host dir gets 86%.
    """
    if not fp:
        return ""
    if os.path.exists(fp):
        return fp
    cand = os.path.join(HOST_PDF, os.path.basename(fp))
    return cand if os.path.exists(cand) else ""


def cache_path(paper_id: str) -> str:
    # Two-level fan-out: 68k files in one directory is painful to list.
    pid = re.sub(r"[^A-Za-z0-9_.-]", "_", paper_id or "unknown")
    return os.path.join(CACHE, pid[:2] or "__", f"{pid}.json")


async def grobid_text(path: str, client: httpx.AsyncClient) -> str:
    with open(path, "rb") as fh:
        data = fh.read()
    r = await client.post(f"{GROBID_URL}/api/processFulltextDocument",
                          files={"input": ("doc.pdf", data, "application/pdf")},
                          data={"consolidateHeader": "0"})
    if r.status_code != 200 or not r.text:
        return ""
    xml = r.text
    body = (re.search(r"<text\b.*?>(.*?)</text>", xml, re.S)
            or re.search(r"<body\b.*?>(.*?)</body>", xml, re.S))
    raw = body.group(1) if body else xml
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw)).strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--tagged-first", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--deadline", default=None, help="HH:MM local")
    args = ap.parse_args()

    stop_at = None
    if args.deadline:
        hh, mm = (int(x) for x in args.deadline.split(":"))
        now = datetime.now()
        stop_at = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if stop_at <= now:
            stop_at += timedelta(days=1)

    os.makedirs(CACHE, exist_ok=True)
    qc = QdrantClient(host="localhost", port=6333)
    rows, off = [], None
    while True:
        pts, off = qc.scroll(collection_name=SRC, limit=5000, offset=off,
                             with_payload=True, with_vectors=False)
        if not pts:
            break
        rows.extend(pts)
        if off is None:
            break
    if args.tagged_first:
        rows.sort(key=lambda p: 0 if ((p.payload or {}).get("contributors") or []) else 1)

    todo = [p for p in rows
            if not os.path.exists(cache_path((p.payload or {}).get("paper_id") or ""))]
    if args.limit:
        todo = todo[:args.limit]
    n_tag = sum(1 for p in todo if ((p.payload or {}).get("contributors") or []))
    print(f"corpus={len(rows):,} to extract={len(todo):,} ({n_tag:,} tagged) "
          f"cache={CACHE}", flush=True)

    async def run():
        sem = asyncio.Semaphore(args.concurrency)
        ok = miss = fail = 0
        t0 = time.monotonic()

        async def one(p, client):
            nonlocal ok, miss, fail
            pl = p.payload or {}
            pid = pl.get("paper_id") or ""
            fp = resolve_pdf(pl.get("pdf_path") or "")
            if not fp:
                miss += 1
                return
            async with sem:
                try:
                    txt = await grobid_text(fp, client)
                except Exception:
                    fail += 1
                    return
            if len(txt) < 200:
                fail += 1
                return
            cp = cache_path(pid)
            os.makedirs(os.path.dirname(cp), exist_ok=True)
            tmp = cp + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"paper_id": pid, "doi": pl.get("doi"),
                           "chars": len(txt), "text": txt}, fh)
            os.replace(tmp, cp)     # atomic: a killed run never leaves a partial
            ok += 1

        async with httpx.AsyncClient(timeout=180.0) as client:
            step = args.concurrency * 8
            for i in range(0, len(todo), step):
                if stop_at and datetime.now() >= stop_at:
                    print("deadline reached; stopping cleanly", flush=True)
                    break
                await asyncio.gather(*(one(p, client) for p in todo[i:i + step]))
                el = time.monotonic() - t0
                done = ok + miss + fail
                if done and (i // step) % 5 == 0:
                    rate = done / el
                    left = (len(todo) - done) / rate / 3600 if rate else 0
                    print(f"  {ok:,} cached  {miss:,} no-pdf  {fail:,} failed  "
                          f"{rate:.2f}/s  ~{left:.1f}h left", flush=True)
        return ok, miss, fail

    ok, miss, fail = asyncio.run(run())
    print(f"\nDONE cached={ok:,} no_pdf={miss:,} failed={fail:,}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
