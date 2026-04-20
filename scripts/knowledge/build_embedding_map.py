#!/usr/bin/env python3
"""
build_embedding_map.py — §15 paper-embedding 2D map + clustering.

Scrolls the Qdrant `papers` collection, runs UMAP to 2D, clusters the
embeddings with HDBSCAN, asks vLLM for a short human label per cluster,
and:

1. Writes `cluster_id`, `topic_label`, `topic_slug` back into every
   Qdrant point's payload (this is what §28's `#topic` filter reads).
2. Emits `/opt/munin/knowledge/embedding_map.json` — flat file served
   by `GET /api/embedding_map` for the future map UI.

Idempotent: if a file of the current `paper_count` and latest
`contributed_at` / upload marker already exists and no point is
missing a `cluster_id`, the rebuild is skipped. Force a rebuild with
`--force`.

Run on the cluster head. Install deps:
    python3 -m pip install --user -r scripts/knowledge/requirements.txt

Environment:
    QDRANT_HOST          default localhost
    QDRANT_PORT          default 6333
    QDRANT_COLLECTION    default papers
    VLLM_URL             default http://127.0.0.1:8000
    VLLM_MODEL_NAME      default qwen3.5-35b-a3b
    EMBEDDING_MAP_PATH   default /opt/munin/knowledge/embedding_map.json

CLI:
    --force              rebuild even if nothing looks stale
    --no-label           skip vLLM labelling (useful on fresh clusters
                         where vLLM is down; clusters get a slug like
                         "cluster-7" and can be relabeled later)
    --scroll-batch N     Qdrant scroll page size (default 512)
    --umap-neighbors N   UMAP n_neighbors (default 15)
    --min-cluster-size N HDBSCAN min_cluster_size (default 30)
    --dry-run            read + cluster + print, write nothing
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

import numpy as np
import requests


QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "papers")
VLLM_URL = os.getenv("VLLM_URL", "http://127.0.0.1:8000")
VLLM_MODEL_NAME = os.getenv("VLLM_MODEL_NAME", "qwen3.5-35b-a3b")
EMBEDDING_MAP_PATH = os.getenv(
    "EMBEDDING_MAP_PATH", "/opt/munin/knowledge/embedding_map.json"
)

# HDBSCAN sentinel for unclustered points.
NOISE_CLUSTER_ID = -1


@dataclass
class PaperPoint:
    qdrant_id: Any
    doi: Optional[str]
    title: Optional[str]
    year: Optional[int]
    authors: list
    vector: np.ndarray
    existing_cluster_id: Optional[int]


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _slugify(label: str) -> str:
    """Lowercase, hyphen-separated, ascii-only, trimmed to ~40 chars."""
    s = label.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    return s[:40] or "untitled"


# ---------------------------------------------------------------------------
# Qdrant I/O
# ---------------------------------------------------------------------------

def scroll_papers(client, collection: str, batch: int) -> list[PaperPoint]:
    """Walk the whole collection with vectors+payload, return rich records."""
    out: list[PaperPoint] = []
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=collection,
            scroll_filter=None,
            limit=batch,
            offset=offset,
            with_payload=True,
            with_vectors=True,
        )
        if not points:
            break
        for p in points:
            vec = p.vector
            if isinstance(vec, dict):
                # Named vectors — pick the first (papers uses a single default)
                vec = next(iter(vec.values()), None)
            if vec is None:
                continue
            payload = p.payload or {}
            out.append(
                PaperPoint(
                    qdrant_id=p.id,
                    doi=payload.get("doi"),
                    title=payload.get("title"),
                    year=payload.get("year"),
                    authors=payload.get("authors") or [],
                    vector=np.asarray(vec, dtype=np.float32),
                    existing_cluster_id=payload.get("cluster_id"),
                )
            )
        if offset is None:
            break
    return out


def write_cluster_payloads(
    client,
    collection: str,
    records: list[PaperPoint],
    cluster_ids: np.ndarray,
    cluster_labels: dict[int, tuple[str, str]],
) -> None:
    """
    Stamp `cluster_id`, `topic_label`, `topic_slug` on every point,
    grouped by cluster so we issue one `set_payload` call per cluster
    rather than per point. UMAP coordinates live only in the JSON
    export — they're not needed for `#topic` filter matching.
    """
    by_cluster: dict[int, list] = {}
    for rec, cid in zip(records, cluster_ids):
        by_cluster.setdefault(int(cid), []).append(rec.qdrant_id)

    for cid, point_ids in by_cluster.items():
        label, slug = cluster_labels.get(
            cid, (f"cluster-{cid}", f"cluster-{cid}")
        )
        client.set_payload(
            collection_name=collection,
            payload={
                "cluster_id": cid,
                "topic_label": label,
                "topic_slug": slug,
            },
            points=point_ids,
            wait=False,
        )


# ---------------------------------------------------------------------------
# Clustering
# ---------------------------------------------------------------------------

def l2_normalize(vectors: np.ndarray) -> np.ndarray:
    """SPECTER outputs are not guaranteed to be unit-norm. Cosine UMAP
    tolerates non-normalised input but downstream Euclidean HDBSCAN
    doesn't. Normalise once, use everywhere."""
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)
    return vectors / norms


def reduce_umap(
    vectors: np.ndarray,
    n_components: int,
    n_neighbors: int,
    random_state: int = 42,
) -> np.ndarray:
    """
    Generic UMAP reducer. Used twice: once at `cluster_dims` (default 10)
    for HDBSCAN input, once at 2 for the visual map. 2D UMAP is too lossy
    to cluster on directly — everything collapses into one blob when the
    corpus is dense.
    """
    import umap  # lazy — heavy import

    reducer = umap.UMAP(
        n_components=n_components,
        n_neighbors=n_neighbors,
        min_dist=0.1 if n_components == 2 else 0.0,
        metric="cosine",
        random_state=random_state,
    )
    return reducer.fit_transform(vectors)


def cluster_hdbscan(points: np.ndarray, min_cluster_size: int) -> np.ndarray:
    import hdbscan  # lazy

    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=min_cluster_size,
        metric="euclidean",
        prediction_data=False,
    )
    return clusterer.fit_predict(points)


def centroids_and_samples(
    records: list[PaperPoint],
    cluster_ids: np.ndarray,
    xy: np.ndarray,
    n_samples: int = 10,
) -> dict[int, dict]:
    """
    For each non-noise cluster, compute the 2D centroid and pick the
    n_samples titles closest to it. Titles are what we feed into vLLM
    for labeling.
    """
    out: dict[int, dict] = {}
    unique = sorted(set(int(c) for c in cluster_ids))
    for cid in unique:
        if cid == NOISE_CLUSTER_ID:
            continue
        mask = cluster_ids == cid
        member_xy = xy[mask]
        cx, cy = member_xy.mean(axis=0)
        dists = np.linalg.norm(member_xy - np.array([cx, cy]), axis=1)
        # indices into the masked subset
        nearest_local = np.argsort(dists)[:n_samples]
        # map back to global record list
        global_indices = np.where(mask)[0][nearest_local]
        sample_titles = [
            records[i].title for i in global_indices if records[i].title
        ]
        out[cid] = {
            "size": int(mask.sum()),
            "centroid": [float(cx), float(cy)],
            "sample_titles": sample_titles,
        }
    return out


# ---------------------------------------------------------------------------
# vLLM labelling
# ---------------------------------------------------------------------------

LABEL_PROMPT = """You are labelling a cluster of scientific papers by their common topic.

Here are {n} sample titles from the cluster:

{titles}

Reply with ONLY a short topic label (2-4 words), no punctuation, no
quotes, no preamble. Examples of good answers:
- Kinase inhibitors in membranes
- Solid-state NMR
- Transformer attention mechanisms
- Lipid raft biology

Label:"""


def label_cluster_via_vllm(
    titles: list[str],
    vllm_url: str,
    model: str,
    timeout: float = 30.0,
) -> Optional[str]:
    if not titles:
        return None
    numbered = "\n".join(f"- {t}" for t in titles if t)
    prompt = LABEL_PROMPT.format(n=len(titles), titles=numbered)
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "max_tokens": 20,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    try:
        r = requests.post(
            f"{vllm_url}/v1/chat/completions",
            json=body,
            timeout=timeout,
        )
        r.raise_for_status()
        content = (
            r.json().get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        # Strip model verbosity — keep only the first line, cap length.
        label = content.strip().splitlines()[0] if content.strip() else ""
        label = re.sub(r'["\'.]', "", label).strip()
        return label[:60] or None
    except Exception as e:
        print(f"[WARN] vLLM label request failed: {e}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# JSON export
# ---------------------------------------------------------------------------

def emit_map_json(
    path: str,
    records: list[PaperPoint],
    cluster_ids: np.ndarray,
    xy: np.ndarray,
    cluster_meta: dict[int, dict],
    cluster_labels: dict[int, tuple[str, str]],
) -> None:
    points = []
    for rec, cid, (x, y) in zip(records, cluster_ids, xy):
        points.append(
            {
                "id": str(rec.qdrant_id),
                "doi": rec.doi,
                "title": rec.title,
                "year": rec.year,
                "x": float(x),
                "y": float(y),
                "cluster": int(cid),
            }
        )
    clusters_out = []
    for cid, meta in sorted(cluster_meta.items()):
        label, slug = cluster_labels.get(cid, (f"cluster-{cid}", f"cluster-{cid}"))
        clusters_out.append(
            {
                "id": int(cid),
                "label": label,
                "slug": slug,
                "size": meta["size"],
                "centroid": meta["centroid"],
            }
        )
    payload = {
        "generated_at": _iso_now(),
        "paper_count": len(records),
        "cluster_count": len(clusters_out),
        "points": points,
        "clusters": clusters_out,
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, separators=(",", ":"))
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def is_fresh(path: str, paper_count: int, records: list[PaperPoint]) -> bool:
    """Skip rebuild when the existing JSON matches current state and
    every point already has a cluster_id."""
    try:
        with open(path, encoding="utf-8") as f:
            existing = json.load(f)
    except (OSError, json.JSONDecodeError):
        return False
    if existing.get("paper_count") != paper_count:
        return False
    missing = sum(1 for r in records if r.existing_cluster_id is None)
    return missing == 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Build paper-embedding 2D map")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-label", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--scroll-batch", type=int, default=512)
    parser.add_argument("--umap-neighbors", type=int, default=15)
    parser.add_argument(
        "--cluster-dims",
        type=int,
        default=10,
        help="UMAP dimensionality for HDBSCAN input (separate from the "
             "2D visualisation pass). 2D is too lossy to cluster on.",
    )
    parser.add_argument("--min-cluster-size", type=int, default=15)
    args = parser.parse_args()

    # Numba (used by UMAP) needs a writable cache location. Under the
    # systemd unit's ProtectSystem=strict sandbox the default locators
    # all fail, so we honour NUMBA_CACHE_DIR if set and make sure it exists.
    numba_cache = os.getenv("NUMBA_CACHE_DIR")
    if numba_cache:
        os.makedirs(numba_cache, exist_ok=True)

    from qdrant_client import QdrantClient

    client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
    print(
        f"[INFO] Scrolling {QDRANT_COLLECTION} @ {QDRANT_HOST}:{QDRANT_PORT}...",
        flush=True,
    )
    t0 = time.time()
    records = scroll_papers(client, QDRANT_COLLECTION, args.scroll_batch)
    print(f"[INFO] Loaded {len(records)} points in {time.time() - t0:.1f}s")

    if len(records) < args.min_cluster_size * 2:
        print(
            f"[ERROR] Too few points ({len(records)}) for clustering "
            f"(min_cluster_size={args.min_cluster_size})"
        )
        return 1

    if not args.force and is_fresh(EMBEDDING_MAP_PATH, len(records), records):
        print(
            f"[OK] {EMBEDDING_MAP_PATH} is fresh ({len(records)} papers, "
            "all points already carry cluster_id). Nothing to do."
        )
        return 0

    vectors = l2_normalize(np.stack([r.vector for r in records]))

    print(
        f"[INFO] UMAP→{args.cluster_dims}d for clustering "
        f"(n_neighbors={args.umap_neighbors}) on {vectors.shape}..."
    )
    t0 = time.time()
    cluster_space = reduce_umap(
        vectors,
        n_components=args.cluster_dims,
        n_neighbors=args.umap_neighbors,
    )
    print(f"[INFO] UMAP ({args.cluster_dims}d) finished in {time.time() - t0:.1f}s")

    print(f"[INFO] HDBSCAN min_cluster_size={args.min_cluster_size}...")
    t0 = time.time()
    cluster_ids = cluster_hdbscan(
        cluster_space, min_cluster_size=args.min_cluster_size
    )
    n_clusters = len(set(int(c) for c in cluster_ids) - {NOISE_CLUSTER_ID})
    n_noise = int((cluster_ids == NOISE_CLUSTER_ID).sum())
    print(
        f"[INFO] HDBSCAN: {n_clusters} clusters, {n_noise} noise points, "
        f"{time.time() - t0:.1f}s"
    )

    # Separate 2D projection for the visual map. HDBSCAN doesn't see
    # this; it only feeds the JSON export's x/y fields.
    print(f"[INFO] UMAP→2d for visualisation...")
    t0 = time.time()
    xy = reduce_umap(
        vectors,
        n_components=2,
        n_neighbors=args.umap_neighbors,
    )
    print(f"[INFO] UMAP (2d) finished in {time.time() - t0:.1f}s")

    cluster_meta = centroids_and_samples(records, cluster_ids, xy)

    # Label each cluster
    cluster_labels: dict[int, tuple[str, str]] = {}
    # Noise cluster gets a static label so points still have payload values.
    cluster_labels[NOISE_CLUSTER_ID] = ("Unclustered", "unclustered")
    for cid, meta in sorted(cluster_meta.items()):
        if args.no_label:
            label = f"cluster-{cid}"
        else:
            print(f"[INFO] Labelling cluster {cid} (size={meta['size']})...")
            label = label_cluster_via_vllm(
                meta["sample_titles"], VLLM_URL, VLLM_MODEL_NAME
            ) or f"cluster-{cid}"
        cluster_labels[cid] = (label, _slugify(label))

    if args.dry_run:
        print("[DRY RUN] Skipping payload write-back and JSON emit.")
        for cid, (label, slug) in sorted(cluster_labels.items()):
            if cid == NOISE_CLUSTER_ID:
                continue
            print(
                f"  cluster {cid:>3} "
                f"size={cluster_meta[cid]['size']:>5}  "
                f"label={label!r}  slug={slug!r}"
            )
        return 0

    print(f"[INFO] Writing cluster_id/topic_label/topic_slug to {len(records)} points...")
    t0 = time.time()
    write_cluster_payloads(
        client, QDRANT_COLLECTION, records, cluster_ids, cluster_labels
    )
    print(f"[INFO] Payload write finished in {time.time() - t0:.1f}s")

    print(f"[INFO] Writing {EMBEDDING_MAP_PATH}...")
    emit_map_json(
        EMBEDDING_MAP_PATH, records, cluster_ids, xy, cluster_meta, cluster_labels
    )
    print(f"[OK] embedding_map.json written ({len(records)} points, {n_clusters} clusters)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
