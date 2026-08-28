#!/usr/bin/env python3
"""Sizing probe for the chunk-level evidence backfill (CHUNK-EVIDENCE-PLAN §7).

Measures, on a random sample of the live corpus, the numbers the plan currently
guesses at: GROBID extraction rate and failure rate, chunk yield per paper, and
embedding throughput. Prints a projection to the full corpus.

Read-only: extracts and chunks, writes NOTHING to Qdrant.

    docker exec munin-retrieval python /app/scripts/probe_chunk_backfill.py --n 200

GROBID is shared with the live ingest pipeline, so concurrency defaults to 2.
"""
from __future__ import annotations

import argparse, asyncio, os, random, statistics, sys, time

sys.path.insert(0, "/app")
sys.path.insert(0, ".")

import database
import document_store
from qdrant_client import QdrantClient


async def one(path: str, sem: asyncio.Semaphore) -> dict:
    out = {"ok": False, "chars": 0, "chunks": 0, "extract_s": 0.0, "err": None}
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except Exception as e:
        out["err"] = f"read:{type(e).__name__}"
        return out
    async with sem:
        t0 = time.monotonic()
        try:
            text = await document_store._extract_pdf(data)
        except Exception as e:
            out["err"] = f"extract:{type(e).__name__}"
            out["extract_s"] = time.monotonic() - t0
            return out
        out["extract_s"] = time.monotonic() - t0
    text = text or ""
    out["chars"] = len(text)
    if len(text) < 200:
        out["err"] = "empty_or_tiny"
        return out
    out["chunks"] = len(document_store.chunk_text(text))
    out["ok"] = True
    return out


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--concurrency", type=int, default=2)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    qc = QdrantClient(host=database.QDRANT_HOST, port=database.QDRANT_PORT)
    total_points = qc.get_collection(database.PAPERS_COLLECTION).points_count
    # Sample well past n: many payloads have no pdf_path or the file is gone.
    pts, _ = qc.scroll(collection_name=database.PAPERS_COLLECTION,
                       limit=args.n * 6, with_payload=["pdf_path", "doi"])
    random.seed(args.seed)
    random.shuffle(pts)
    # pdf_path payloads are HOST paths (/opt/munin/data/papers/pdf/...) while
    # this runs in the container, where that directory is mounted at /papers.
    # Without the translation the probe silently samples only whatever happens
    # to resolve and reports a biased success rate.
    HOST_PREFIX, CONT_PREFIX = "/opt/munin/data/papers/pdf", "/papers"
    paths, missing = [], 0
    for p in pts:
        fp = (p.payload or {}).get("pdf_path")
        if not fp:
            missing += 1
            continue
        cand = fp.replace(HOST_PREFIX, CONT_PREFIX) if fp.startswith(HOST_PREFIX) else fp
        if os.path.exists(cand):
            paths.append(cand)
        else:
            missing += 1
        if len(paths) >= args.n:
            break
    print(f"pdf resolution: {len(paths)} usable, {missing} missing/unresolvable "
          f"in the scanned prefix", flush=True)
    print(f"corpus={total_points} sampled={len(paths)} concurrency={args.concurrency}", flush=True)
    if not paths:
        print("no readable pdf_path in the sample; cannot probe")
        return 1

    sem = asyncio.Semaphore(args.concurrency)
    t0 = time.monotonic()
    res = await asyncio.gather(*(one(p, sem) for p in paths))
    wall = time.monotonic() - t0

    ok = [r for r in res if r["ok"]]
    fails = [r for r in res if not r["ok"]]
    chunks = [r["chunks"] for r in ok]
    chars = [r["chars"] for r in ok]
    ex = [r["extract_s"] for r in res if r["extract_s"] > 0]

    print("\n=== EXTRACTION ===")
    print(f"  ok {len(ok)}/{len(res)}  ({100*len(ok)/len(res):.1f}%)   failures {len(fails)}")
    import collections
    for k, v in collections.Counter(r["err"] for r in fails).most_common():
        print(f"    {k}: {v}")
    if ex:
        ex.sort()
        print(f"  extract seconds/paper: median {statistics.median(ex):.2f}  "
              f"mean {statistics.mean(ex):.2f}  p90 {ex[int(len(ex)*.9)]:.2f}  max {max(ex):.2f}")
    print(f"  wall {wall:.0f}s for {len(paths)} papers at concurrency {args.concurrency}"
          f"  -> {len(paths)/wall:.2f} papers/s")

    if chunks:
        chunks.sort()
        print("\n=== CHUNK YIELD ===")
        print(f"  chars/paper: median {statistics.median(chars):,.0f}  mean {statistics.mean(chars):,.0f}")
        print(f"  chunks/paper: median {statistics.median(chunks)}  mean {statistics.mean(chunks):.1f}  "
              f"p90 {chunks[int(len(chunks)*.9)]}  max {max(chunks)}")

    # --- embedding throughput on a real batch --------------------------------
    print("\n=== EMBEDDING ===")
    try:
        enc = database.get_paper_encoder()
        dev = getattr(getattr(enc, "device", None), "type", "?")
        sample_chunks = []
        for r, p in zip(res, paths):
            if r["ok"]:
                with open(p, "rb") as fh:
                    pass
        # re-chunk one ok paper to get real text for timing
        text = await document_store._extract_pdf(open(paths[0], "rb").read())
        base = document_store.chunk_text(text or "") or ["x"]
        # WARM UP first: the previous version timed a 5-chunk cold call and
        # reported model-load overhead as throughput.
        enc.encode(base[:8], batch_size=8, show_progress_bar=False)
        sample = (base * 40)[:256]
        t1 = time.monotonic()
        enc.encode(sample, batch_size=32, show_progress_bar=False)
        dt = time.monotonic() - t1
        rate = len(sample) / dt
        print(f"  encoder device={dev}  {len(sample)} chunks in {dt:.2f}s -> {rate:.1f} chunks/s (warmed)")
    except Exception as e:
        print(f"  encoder timing failed: {type(e).__name__}: {e}")
        rate = None

    # --- projection ----------------------------------------------------------
    if ok:
        mean_chunks = statistics.mean(chunks)
        est_chunks = mean_chunks * total_points * (len(ok) / len(res))
        pps = len(paths) / wall
        print("\n=== PROJECTION TO FULL CORPUS ===")
        print(f"  papers: {total_points:,}")
        print(f"  est. chunks: {est_chunks:,.0f}  (mean {mean_chunks:.1f}/paper x {100*len(ok)/len(res):.0f}% success)")
        print(f"  est. vectors: {est_chunks*1024*4/1e9:.1f} GB at 1024d fp32")
        print(f"  est. GROBID wall at concurrency {args.concurrency}: "
              f"{total_points/pps/3600:.1f} h")
        if rate:
            print(f"  est. embedding wall: {est_chunks/rate/3600:.1f} h at {rate:.0f} chunks/s")
        print(f"  papers with NO chunk coverage: ~{total_points*len(fails)/len(res):,.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
