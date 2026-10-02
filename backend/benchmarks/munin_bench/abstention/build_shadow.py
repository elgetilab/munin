"""Track C2b: build the shadow corpus + freeze the paired question set.

Selects N single-source-DOI LitQA2 questions whose source IS in `papers_bge`,
then clones `papers_bge` into `papers_shadow` MINUS those N source papers. The
paired test then asks the SAME N questions against:
  - the live corpus (papers_bge, source PRESENT)  -> should answer
  - the shadow (papers_shadow, source ABSENT)     -> should abstain / not fabricate

Non-destructive: `papers_bge` is untouched; `papers_shadow` is a separate
collection a SECOND retrieval instance points at (PAPERS_COLLECTION=papers_shadow).

    PYTHONPATH=$HOME/.cache/munin_bench_deps:. \
    /opt/munin/services/pipeline/venv/bin/python -m munin_bench.abstention.build_shadow --n 50
"""

from __future__ import annotations

import argparse
import json
import os
import random

from qdrant_client import QdrantClient
from qdrant_client import models as qm

from .corpus import normalize_doi, in_corpus
from ..benchmarks.litqa2_runner import load_litqa2

_HERE = os.path.dirname(__file__)
_SRC = "papers_bge"
_SHADOW = "papers_shadow"


def select_questions(n: int, seed: int = 42) -> list[dict]:
    qs = load_litqa2()
    single = []
    for q in qs:
        sd = [d for d in q.get("source_dois", []) if d]
        if len(sd) == 1 and in_corpus(sd[0]):
            # qid and DOI only: LitQA2 text is not redistributed, and
            # run_c2 reloads it from the dataset by qid.
            single.append({"qid": q["qid"], "source_doi": normalize_doi(sd[0])})
    rng = random.Random(seed)
    rng.shuffle(single)
    return single[:n]


def build_shadow(remove_dois: set[str], host="127.0.0.1", port=6333,
                 batch: int = 512) -> int:
    c = QdrantClient(host=host, port=port)
    src_info = c.get_collection(_SRC)
    vec = src_info.config.params.vectors
    if _SHADOW in [x.name for x in c.get_collections().collections]:
        c.delete_collection(_SHADOW)
    c.create_collection(_SHADOW, vectors_config=qm.VectorParams(
        size=vec.size, distance=vec.distance))
    copied = skipped = 0
    offset = None
    buf: list[qm.PointStruct] = []
    while True:
        pts, offset = c.scroll(_SRC, limit=batch, with_payload=True,
                               with_vectors=True, offset=offset)
        for p in pts:
            doi = normalize_doi((p.payload or {}).get("doi") or "")
            if doi and doi in remove_dois:
                skipped += 1
                continue
            buf.append(qm.PointStruct(id=p.id, vector=p.vector, payload=p.payload))
            copied += 1
            if len(buf) >= batch:
                c.upsert(_SHADOW, points=buf); buf = []
        if offset is None:
            break
    if buf:
        c.upsert(_SHADOW, points=buf)
    print(f"[shadow] {_SHADOW}: copied {copied}, removed {skipped} source papers")
    return copied


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default=os.path.join(_HERE, "c2_questions.json"))
    ap.add_argument("--skip-build", action="store_true",
                    help="only (re)select + freeze the question set, don't touch Qdrant")
    args = ap.parse_args()

    sel = select_questions(args.n, seed=args.seed)
    remove = {q["source_doi"] for q in sel}
    payload = {"meta": {"n": len(sel), "seed": args.seed,
                        "removed_dois": sorted(remove),
                        "shadow_collection": _SHADOW},
               "questions": sel}
    json.dump(payload, open(args.out, "w"), indent=2)
    print(f"[c2] froze {len(sel)} questions ({len(remove)} DOIs to remove) -> {args.out}")

    if not args.skip_build:
        n = build_shadow(remove)
        # sanity: none of the removed DOIs survive in the shadow
        from .corpus import build_from_qdrant  # reuse scroll (but against shadow)
        # quick check via direct scroll
        c = QdrantClient(host="127.0.0.1", port=6333)
        leaked = 0
        offset = None
        while True:
            pts, offset = c.scroll(_SHADOW, limit=2000, with_payload=["doi"],
                                   with_vectors=False, offset=offset)
            for p in pts:
                if normalize_doi((p.payload or {}).get("doi") or "") in remove:
                    leaked += 1
            if offset is None:
                break
        print(f"[c2] shadow points: {n} | removed-DOI leakage: {leaked} (must be 0)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
