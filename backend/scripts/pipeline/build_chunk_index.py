#!/usr/bin/env python3
"""Build `papers_chunks`: chunk-level evidence index over the paper corpus.

Runs on the HOST (the retrieval container has no GPU: torch.cuda.is_available()
is False there). Measured 2026-08-28: GPU 564 chunks/s vs CPU 2.3, so embedding
is ~44 min for the whole corpus while GROBID extraction is ~14.5 h at
concurrency 2. GROBID is the bottleneck, hence --deadline and resumability.

    PY=/opt/munin/services/pipeline/venv/bin/python
    $PY build_chunk_index.py --tagged-first --deadline 05:30 --concurrency 4

Idempotent: point ids are deterministic, and papers already present are skipped,
so it can be stopped and resumed across nights.
"""
from __future__ import annotations

import argparse, asyncio, hashlib, os, sys, time
from datetime import datetime, timedelta

import httpx
from qdrant_client import QdrantClient, models

GROBID_URL = os.getenv("GROBID_URL", "http://127.0.0.1:8070")
SRC = os.getenv("PAPERS_COLLECTION", "papers_bge")
DST = os.getenv("CHUNKS_COLLECTION", "papers_chunks")
MODEL = os.getenv("BGE_LARGE_PATH", "/opt/munin/data/models/bge-large")
DIM = 1024
# Points per upsert request. ~256 x (1024-d vector + ~2 KB chunk text) stays an
# order of magnitude under Qdrant's 32 MB payload ceiling.
UPSERT_POINTS = 256
HOST_PDF = "/opt/munin/data/papers/pdf"

# Reuse the retrieval service's chunker verbatim so chunk boundaries match what
# user_docs already produces (~512 tokens, ~50 overlap, paragraph-aware).
sys.path.insert(0, "/opt/munin/services/retrieval")
from document_store import chunk_text  # noqa: E402


def resolve_pdf(fp: str) -> str:
    """Absolute host path for a payload `pdf_path`, or "" if not on disk.

    The corpus stores TWO conventions depending on ingest route: crawler rows
    hold host paths (/opt/munin/data/papers/pdf/x.pdf) while contributor
    uploads hold CONTAINER paths (/papers/x.pdf). Measured 2026-08-28: only 4%
    of tagged papers resolved when the raw value was trusted. Resolve by
    basename against the host PDF directory, which is correct for both.
    """
    if not fp:
        return ""
    if os.path.exists(fp):
        return fp
    cand = os.path.join(HOST_PDF, os.path.basename(fp))
    return cand if os.path.exists(cand) else ""


def point_id(paper_id: str, idx: int) -> int:
    """Deterministic 63-bit id so a re-run upserts in place instead of duplicating."""
    h = hashlib.sha256(f"{paper_id}:{idx}".encode()).digest()
    return int.from_bytes(h[:8], "big") >> 1


async def extract(path: str, client: httpx.AsyncClient) -> str:
    with open(path, "rb") as fh:
        data = fh.read()
    r = await client.post(f"{GROBID_URL}/api/processFulltextDocument",
                          files={"input": ("doc.pdf", data, "application/pdf")},
                          data={"consolidateHeader": "0"})
    if r.status_code != 200 or not r.text:
        return ""
    import re
    xml = r.text
    body = re.search(r"<text\b.*?>(.*?)</text>", xml, re.S) or re.search(r"<body\b.*?>(.*?)</body>", xml, re.S)
    raw = body.group(1) if body else xml
    txt = re.sub(r"<[^>]+>", " ", raw)
    return re.sub(r"\s+", " ", txt).strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--deadline", default=None, help="HH:MM local; stop cleanly before this")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--batch", type=int, default=64, help="papers per embed+upsert flush")
    ap.add_argument("--tagged-first", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--device", default="cuda:0")
    args = ap.parse_args()

    stop_at = None
    if args.deadline:
        hh, mm = (int(x) for x in args.deadline.split(":"))
        now = datetime.now()
        stop_at = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if stop_at <= now:
            stop_at += timedelta(days=1)
        print(f"deadline: {stop_at:%Y-%m-%d %H:%M}", flush=True)

    qc = QdrantClient(host="localhost", port=6333)
    existing = {c.name for c in qc.get_collections().collections}
    if DST not in existing:
        qc.create_collection(DST, vectors_config=models.VectorParams(
            size=DIM, distance=models.Distance.COSINE))
        for f in ("doi", "paper_id", "topic_slug"):
            try:
                qc.create_payload_index(DST, field_name=f,
                                        field_schema=models.PayloadSchemaType.KEYWORD)
            except Exception:
                pass
        print(f"created {DST}", flush=True)

    # Which papers are already done (resumability).
    done: set[str] = set()
    off = None
    while True:
        pts, off = qc.scroll(collection_name=DST, limit=10000, offset=off,
                             with_payload=["paper_id"], with_vectors=False)
        if not pts:
            break
        done.update((p.payload or {}).get("paper_id") for p in pts)
        if off is None:
            break
    print(f"already indexed papers: {len(done):,}", flush=True)

    # Source papers, tagged first when asked.
    rows, off = [], None
    while True:
        pts, off = qc.scroll(collection_name=SRC, limit=5000, offset=off,
                             with_payload=True, with_vectors=False)
        if not pts:
            break
        rows.extend(pts)
        if off is None:
            break
    def key(p):
        pl = p.payload or {}
        return (0 if (pl.get("contributors") or []) else 1) if args.tagged_first else 0
    rows.sort(key=key)
    todo = [p for p in rows if (p.payload or {}).get("paper_id") not in done]
    if args.limit:
        todo = todo[:args.limit]
    n_tagged = sum(1 for p in todo if ((p.payload or {}).get("contributors") or []))
    print(f"to process: {len(todo):,} papers ({n_tagged:,} tagged)", flush=True)

    from sentence_transformers import SentenceTransformer
    enc = SentenceTransformer(MODEL, device=args.device)
    print(f"encoder on {args.device}", flush=True)

    async def run() -> tuple[int, int, int]:
        sem = asyncio.Semaphore(args.concurrency)
        papers = chunks = failed = 0
        buf: list[tuple[dict, list[str]]] = []
        t0 = time.monotonic()

        async def one(p):
            nonlocal failed
            pl = p.payload or {}
            fp = resolve_pdf(pl.get("pdf_path") or "")
            if not fp:
                failed += 1
                return None
            async with sem:
                try:
                    txt = await extract(fp, client)
                except Exception:
                    failed += 1
                    return None
            if len(txt) < 200:
                failed += 1
                return None
            cs = chunk_text(txt)
            return (pl, cs) if cs else None

        def flush():
            nonlocal chunks
            if not buf:
                return
            texts, points = [], []
            for pl, cs in buf:
                for i, c in enumerate(cs):
                    texts.append(c)
                    points.append((pl, i, len(cs), c))
            vecs = enc.encode(texts, batch_size=64, show_progress_bar=False,
                              normalize_embeddings=True)
            structs = [
                models.PointStruct(
                    id=point_id(pl.get("paper_id") or pl.get("doi") or "", i),
                    vector=v.tolist(),
                    payload={
                        # Paper identity denormalised onto every chunk so an
                        # evidence hit is citable without a second lookup.
                        "paper_id": pl.get("paper_id"), "doi": pl.get("doi"),
                        "title": pl.get("title"), "year": pl.get("year"),
                        "authors": pl.get("authors"), "journal": pl.get("journal"),
                        "contributors": pl.get("contributors"),
                        "topic_slug": pl.get("topic_slug"),
                        "chunk_index": i, "total_chunks": tot, "chunk_text": c,
                    })
                for v, (pl, i, tot, c) in zip(vecs, points)]
            # Upsert in fixed-size POINT slices. Sizing the flush by PAPERS was
            # the bug that killed the 2026-08-28 run 66 seconds in: 128 papers
            # is ~2,800 points carrying chunk_text plus 1024-d vectors, and the
            # single request came to 91.7 MB against Qdrant's 32 MB limit. The
            # 8-paper smoke test passed precisely because it never reached the
            # ceiling, so batch size has to be bounded in the unit that actually
            # determines request size.
            for j in range(0, len(structs), UPSERT_POINTS):
                sl = structs[j:j + UPSERT_POINTS]
                try:
                    qc.upsert(DST, points=sl)
                    chunks += len(sl)
                except Exception as exc:
                    # One bad slice must never end a multi-hour job.
                    print(f"  upsert slice failed ({len(sl)} pts): "
                          f"{type(exc).__name__}: {str(exc)[:120]}", flush=True)
            buf.clear()

        async with httpx.AsyncClient(timeout=120.0) as client_:
            globals()["client"] = client_
            for i in range(0, len(todo), args.concurrency * 4):
                if stop_at and datetime.now() >= stop_at:
                    print("deadline reached; stopping cleanly", flush=True)
                    break
                batch = todo[i:i + args.concurrency * 4]
                for r in await asyncio.gather(*(one(p) for p in batch)):
                    if r:
                        buf.append(r); papers += 1
                if len(buf) >= args.batch:
                    flush()
                    el = time.monotonic() - t0
                    print(f"  {papers:,} papers  {chunks:,} chunks  {failed:,} failed  "
                          f"{papers/el:.2f} papers/s", flush=True)
            flush()
        return papers, chunks, failed

    p, c, f = asyncio.run(run())
    print(f"\nDONE papers={p:,} chunks={c:,} failed={f:,}", flush=True)
    print(f"{DST} now has {qc.get_collection(DST).points_count:,} points", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
