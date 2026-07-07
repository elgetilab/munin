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

Commands (root):

```bash
sudo systemctl edit munin-embedding-map    # opens a drop-in override
```

Add exactly:

```ini
[Service]
Environment="QDRANT_COLLECTION=papers_bge"
```

(A drop-in `Environment=` overrides the same key in the base unit; the other
env lines - QDRANT_HOST, VLLM_URL, NUMBA_CACHE_DIR, etc. - are inherited
unchanged. Do NOT edit the base unit file.)

Then:

```bash
sudo systemctl daemon-reload
# verify the override won:
systemctl show munin-embedding-map -p Environment | tr ' ' '\n' | grep QDRANT_COLLECTION
#   -> Environment=QDRANT_COLLECTION=papers_bge  (papers must NOT also appear)

# optional: run once now instead of waiting for 01:30
sudo systemctl start munin-embedding-map
journalctl -u munin-embedding-map -f    # watch: "Scrolling papers_bge ...", ~5-6 min
```

**Verify success:** after the run, `/opt/munin/knowledge/embedding_map.json`
mtime is fresh and the log shows it scrolled `papers_bge` (68,121 records).
The cluster labels/coordinates WILL shift vs the old map - expected, it is a
fresh projection of a different (better) embedding space, not a bug.

**Rollback:** `sudo systemctl revert munin-embedding-map` (drops the drop-in),
`daemon-reload`. Falls back to `papers`.

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
