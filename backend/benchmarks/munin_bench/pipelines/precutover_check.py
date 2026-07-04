"""Pre-cutover checklist for the encoder migration (Phase B).

Validates + prepares everything needed before flipping PAPER_ENCODER /
PAPERS_COLLECTION to BGE, so the cutover is push-button. Non-invasive: it only
reads/tops-up `papers_bge` and reads deployed config; it NEVER touches the live
`papers` collection, production config, or the running services. Prints a
GO/NO-GO summary and the exact cutover + rollback commands.

    PYTHONPATH=<deps>:. MUNIN_BENCH_SPECTER_DEVICE=cpu \\
      python -m munin_bench.pipelines.precutover_check [--check-only]

Checks:
  1. Flag-gated code deployed (retrieval database.py + compose have the flag).
  2. BGE-large model present at the container mount source (host path).
  3. papers_bge exists, 1024-d, and is topped up to match papers (idempotent
     re-embed of anything ingested since the initial build) - unless --check-only.
  4. A BGE query against papers_bge returns sensible hits.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

from .. import config
from ..clients import get_qdrant, load_encoder

BGE_HOST_MODEL = "/opt/munin/data/models/bge-large"          # compose mount source
DEPLOYED_DB = "/opt/munin/services/retrieval/database.py"
DEPLOYED_COMPOSE = "/opt/munin/docker/docker-compose.yml"

OK, FAIL, WARN = "[OK]  ", "[FAIL]", "[WARN]"


def _file_has(path, needle):
    try:
        with open(path) as fh:
            return needle in fh.read()
    except Exception:
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check-only", action="store_true",
                    help="do not run the papers_bge top-up re-embed")
    args = ap.parse_args()
    device = os.getenv("MUNIN_BENCH_SPECTER_DEVICE", "cpu")
    qc = get_qdrant()
    results = []  # (ok, line)

    # --- 1. code deployed ---------------------------------------------------
    db_ok = _file_has(DEPLOYED_DB, "get_paper_encoder")
    comp_ok = _file_has(DEPLOYED_COMPOSE, "PAPER_ENCODER")
    code_ok = db_ok and comp_ok
    results.append((code_ok,
        f"{OK if code_ok else FAIL} 1. flag-gated code deployed "
        f"(database.py={'yes' if db_ok else 'NO'}, compose={'yes' if comp_ok else 'NO'})"))
    if not code_ok:
        results.append((None, "        -> run: sudo ./deploy.sh compose retrieval pipeline"))

    # --- 2. BGE model present ----------------------------------------------
    model_ok = os.path.isdir(BGE_HOST_MODEL) and any(
        os.path.exists(os.path.join(BGE_HOST_MODEL, f))
        for f in ("config.json", "model.safetensors", "pytorch_model.bin"))
    results.append((model_ok,
        f"{OK if model_ok else FAIL} 2. BGE-large model at {BGE_HOST_MODEL}"))
    if not model_ok:
        results.append((None,
            "        -> place it (needs write access, likely sudo):\n"
            "           python -c \"from sentence_transformers import SentenceTransformer as S;"
            f" S('BAAI/bge-large-en-v1.5').save('{BGE_HOST_MODEL}')\""))

    # --- 3. papers_bge parity (+ optional top-up) --------------------------
    papers = qc.count(config.PAPERS_COLLECTION).count
    bge_exists = qc.collection_exists(config.PAPERS_BGE_COLLECTION)
    dim = (qc.get_collection(config.PAPERS_BGE_COLLECTION).config.params.vectors.size
           if bge_exists else None)
    bge_n = qc.count(config.PAPERS_BGE_COLLECTION).count if bge_exists else 0
    behind = papers - bge_n
    if bge_exists and behind > 0 and not args.check_only:
        print(f"  topping up papers_bge ({behind} behind)...")
        subprocess.run([sys.executable, "-m", "munin_bench.pipelines.build_papers_bge"],
                       env={**os.environ}, check=False)
        bge_n = qc.count(config.PAPERS_BGE_COLLECTION).count
        behind = papers - bge_n
    parity_ok = bge_exists and dim == 1024 and behind == 0
    results.append((parity_ok,
        f"{OK if parity_ok else FAIL} 3. papers_bge ready "
        f"(exists={bge_exists}, dim={dim}, count={bge_n}/{papers}, behind={behind})"))
    if bge_exists and behind > 0 and args.check_only:
        results.append((None, "        -> re-run without --check-only to top up "
                              "(or: python -m munin_bench.pipelines.build_papers_bge)"))

    # --- 4. BGE smoke query against papers_bge -----------------------------
    smoke_ok = False
    if model_ok and parity_ok:
        try:
            enc = load_encoder(BGE_HOST_MODEL, device=device)
            vec = enc.encode(config.BGE_QUERY_INSTRUCTION + "protein folding",
                             normalize_embeddings=True).tolist()
            hits = qc.query_points(config.PAPERS_BGE_COLLECTION, query=vec, limit=3).points
            smoke_ok = len(hits) > 0
            results.append((smoke_ok,
                f"{OK if smoke_ok else FAIL} 4. BGE query on papers_bge -> {len(hits)} hits"))
            for h in hits:
                results.append((None, f"        {h.score:.3f}  {(h.payload or {}).get('title','')[:60]}"))
        except Exception as e:
            results.append((False, f"{FAIL} 4. BGE smoke query failed: {type(e).__name__}: {e}"))
    else:
        results.append((None, f"{WARN} 4. BGE smoke query skipped (needs checks 2+3 green)"))

    # --- report -------------------------------------------------------------
    print("\n=== Encoder migration pre-cutover checklist ===\n")
    for _, line in results:
        print(line)
    hard = [ok for ok, _ in results if ok is not None]
    go = all(hard)
    print("\n" + ("GO - ready to cut over." if go else "NO-GO - resolve the [FAIL] items above."))
    if go:
        print(_runbook())
    return 0 if go else 1


def _runbook() -> str:
    return f"""
--- CUTOVER (varghele/root) ---
1. In the compose .env (cluster.env), set BOTH:
     PAPER_ENCODER=bge-large
     PAPERS_COLLECTION=papers_bge
2. Restart retrieval + pipeline:
     cd /opt/munin/docker && sudo docker compose --profile rag up -d --force-recreate retrieval
     sudo systemctl restart munin-paper-pipeline
3. Embedding-map: set QDRANT_COLLECTION=papers_bge for build_embedding_map.
4. Smoke: retrieval log shows "Paper encoder: BGE-large"; a chat paper_search returns hits.

--- ROLLBACK (instant) ---
   Set PAPER_ENCODER=specter, PAPERS_COLLECTION=papers in cluster.env; restart
   retrieval + pipeline. `papers` (SPECTER) is untouched throughout.
"""


if __name__ == "__main__":
    raise SystemExit(main())
