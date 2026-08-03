"""Build the `papers_bge` collection: a 1024-d BGE-large re-embed of `papers`.

Phase A of the encoder migration (ENCODER-MIGRATION-PLAN.md). NON-INVASIVE:
creates a NEW collection read only by the eval harness; never touches the live
`papers` collection or production. Reads title+abstract from the existing
payloads (no GROBID re-parse), embeds `title\n\nabstract` RAW (BGE docs take no
prefix), and upserts under the SAME point id + payload.

Idempotent / resumable: skips ids already present in `papers_bge`, so it can be
re-run after an interruption. The same tool is reused for the Phase B production
re-embed.

    PYTHONPATH=<deps>:. MUNIN_BENCH_SPECTER_DEVICE=cpu \\
      python -m munin_bench.pipelines.build_papers_bge
"""

from __future__ import annotations

import argparse
import os

from .. import config
from ..clients import get_qdrant, load_encoder


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--scroll", type=int, default=1000)
    args = ap.parse_args()

    from qdrant_client.models import Distance, PointStruct, VectorParams

    device = os.getenv("MUNIN_BENCH_SPECTER_DEVICE", "cpu")
    qc = get_qdrant()
    # Source is always the legacy 768-d corpus: this pipeline exists to build
    # (and top up) papers_bge FROM papers, so it must not follow
    # PAPERS_COLLECTION now that that points at the destination.
    src, dst = config.PAPERS_LEGACY_COLLECTION, config.PAPERS_BGE_COLLECTION
    total_src = qc.count(src).count
    print(f"[papers_bge] source {src}={total_src} -> {dst} (1024d) on {device}")

    if not qc.collection_exists(dst):
        qc.create_collection(dst, vectors_config=VectorParams(
            size=1024, distance=Distance.COSINE))
        done = set()
    else:
        done = set()
        off = None
        while True:
            pts, off = qc.scroll(dst, limit=4000, offset=off,
                                 with_payload=False, with_vectors=False)
            done.update(p.id for p in pts)
            if off is None:
                break
        print(f"[papers_bge] resuming: {len(done)} already embedded")

    model = load_encoder(config.BGE_LARGE_PATH, device=device,
                         hf_fallback=config.BGE_LARGE_HF_ID)

    buf_ids, buf_payloads, buf_texts = [], [], []
    embedded = 0

    def flush():
        nonlocal embedded
        if not buf_texts:
            return
        vecs = model.encode(buf_texts, batch_size=args.batch,
                            normalize_embeddings=True, show_progress_bar=False)
        qc.upsert(dst, points=[
            PointStruct(id=i, vector=v.tolist(), payload=pl)
            for i, v, pl in zip(buf_ids, vecs, buf_payloads)])
        embedded += len(buf_ids)

    off = None
    scanned = 0
    while True:
        pts, off = qc.scroll(src, limit=args.scroll, offset=off,
                             with_payload=True, with_vectors=False)
        for p in pts:
            scanned += 1
            if p.id in done:
                continue
            pl = p.payload or {}
            text = f"{pl.get('title') or ''}\n\n{pl.get('abstract') or ''}".strip()
            buf_ids.append(p.id)
            buf_payloads.append(pl)
            buf_texts.append(text)
            if len(buf_texts) >= 256:
                flush()
                buf_ids, buf_payloads, buf_texts = [], [], []
                print(f"[papers_bge] embedded {embedded} (scanned {scanned}/{total_src})",
                      end="\r")
        if off is None:
            break
    flush()
    final = qc.count(dst).count
    print(f"\n[papers_bge] done: {final}/{total_src} points in {dst} "
          f"(+{embedded} this run)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
