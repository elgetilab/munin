# Encoder migration - loose ends (hand-off)

Two tail tasks left after the SPECTER-v1 -> BGE-large cutover (which is LIVE;
see `done/ENCODER-MIGRATION-PLAN.md` + `backend/benchmarks/RESULTS.md`). Both
touch systemd / production Qdrant, so they are **varghele/root** (`vi` cannot
sudo or delete production collections). Status set 2026-07-07.

Current state (verified 2026-07-07):

| Collection | Points | Dim | Encoder | Read by |
|---|---|---|---|---|
| `papers`     | 68,121 | 768  | SPECTER-v1 | ONLY the embedding-map nightly (task 1); rollback safety net |
| `papers_bge` | 68,121 | 1024 | BGE-large  | all live retrieval (search_papers, /search/hybrid, MCP paper_search) + the paper pipeline |

---

## Task 1 - repoint the nightly embedding-map at `papers_bge`  (do soon)

**Why now:** `munin-embedding-map.timer` rebuilds the 2D paper map nightly at
01:30 from `QDRANT_COLLECTION`, which is **hardcoded to `papers`** in the unit
(no drop-in, does NOT read cluster.env). So every night it projects the OLD
SPECTER vectors while live search uses BGE. The map you see and the space you
search have silently diverged since the cutover (last rebuild 2026-07-04, still
SPECTER). Repointing realigns them.

**No code change needed.** `build_embedding_map.py` is dimension-agnostic: it
scrolls `with_vectors=True`, L2-normalizes, and UMAP-reduces whatever dim it
finds. 768 -> 1024 just works. Only the env var changes.

**GOTCHA - a plain repoint is NOT enough; you must force one rebuild.**
`is_fresh()` skips the rebuild when every point already carries a `cluster_id`
payload (it checks presence, not whether the geometry still matches). But
`papers_bge` was built by COPYING payloads from `papers`, so its points
inherited SPECTER-era `cluster_id`s (verified: a sample point still reads
`cluster_id=89`). So the nightly (no `--force`) would scroll `papers_bge`, see
cluster_ids everywhere, and no-op - leaving the map in SPECTER geometry. A
one-time `--force` run redoes UMAP/HDBSCAN on the BGE vectors and re-stamps
`papers_bge` with BGE-geometry cluster_ids; every nightly after that stays
fresh on its own.

Preconditions for the forced run: vLLM up (cluster labelling calls it) and
`/opt/munin/knowledge/numba-cache` writable - both were true 2026-07-07.

Commands (root):

```bash
# 0. back up the current SPECTER-geometry map (map flipback insurance)
sudo cp -a /opt/munin/knowledge/embedding_map.json \
           /opt/munin/knowledge/embedding_map.json.specter.bak

# 1. drop-in so every FUTURE nightly uses papers_bge
sudo systemctl edit munin-embedding-map
#    add exactly (leave the base unit alone; other env lines are inherited):
#      [Service]
#      Environment="QDRANT_COLLECTION=papers_bge"

# 2. reload + verify the override won
sudo systemctl daemon-reload
systemctl show munin-embedding-map -p Environment | tr ' ' '\n' | grep QDRANT_COLLECTION
#    -> QDRANT_COLLECTION=papers_bge   (papers must NOT also appear)

# 3. ONE-TIME forced rebuild now (nightly won't, due to inherited cluster_ids).
#    Replicates the unit's env + adds --force. ~6-10 min.
sudo env \
  QDRANT_HOST=127.0.0.1 QDRANT_PORT=6333 \
  QDRANT_COLLECTION=papers_bge \
  VLLM_URL=http://127.0.0.1:8000 VLLM_MODEL_NAME=qwen3.6-35b-a3b \
  EMBEDDING_MAP_PATH=/opt/munin/knowledge/embedding_map.json \
  NUMBA_CACHE_DIR=/opt/munin/knowledge/numba-cache \
  /opt/munin/services/knowledge/venv/bin/python3 \
  /opt/cluster/scripts/knowledge/build_embedding_map.py --force
```

**Verify success:** log shows `Scrolling papers_bge`, then
`Writing cluster_id/topic_label/topic_slug to 68121 points`, then the map
write; `/opt/munin/knowledge/embedding_map.json` mtime is fresh and a sampled
`papers_bge` point's `cluster_id` has changed from the inherited value. The
cluster labels/coordinates WILL shift vs the old map - expected, it is a fresh
projection of a different (better) embedding space, not a bug.

**Rollback (map):** `sudo systemctl revert munin-embedding-map` (drops the
drop-in) + `daemon-reload` restores the `papers` pointer; restore the backup
with `sudo cp -a .../embedding_map.json.specter.bak .../embedding_map.json`.
The `papers` collection itself is never touched here, so search rollback is
unaffected.

---

## Task 2 - retire the old `papers` collection  (DEFERRED - post-soak, one-way)

`papers` (768d, SPECTER) is now dead weight EXCEPT as the instant rollback for
the encoder migration. Deleting it frees ~210 MB Qdrant RAM and is
**irreversible** (re-embedding 68k docs to rebuild it = hours).

**Preconditions - ALL must hold before deleting:**

1. BGE has soaked in production with no quality regressions / user reports
   (suggest >= 1-2 weeks live; cutover was ~2026-07-06).
2. Task 1 is done, so nothing at all still reads `papers`
   (grep confirmed: after task 1, the only remaining reference is the rollback).
3. You are fully committed to BGE - deleting `papers` removes the one-env-flip
   rollback documented in `done/ENCODER-MIGRATION-PLAN.md`.

Delete (root, from a host with the retrieval venv / or the container):

```python
from qdrant_client import QdrantClient
c = QdrantClient(host="127.0.0.1", port=6333)
assert "papers_bge" in [x.name for x in c.get_collections().collections]  # safety
c.delete_collection("papers")
```

**Do NOT touch** the `notion` collection - it is a separate 768d SPECTER space
still served by `get_specter()` and is unrelated to this migration.

After deletion, note it in `RESULTS.md` / this file so the rollback path is
known to be gone.
