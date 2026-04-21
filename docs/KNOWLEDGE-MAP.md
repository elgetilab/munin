# Paper-Embedding Knowledge Map — Operator Reference

§15 of `docs/future_features.md`, shipped 2026-04-20. Builds a 2D
visualisation map + topical clusters over the shared `papers` Qdrant
corpus. Every paper gets a `cluster_id` / `topic_label` / `topic_slug`
payload stamp, which is what §28's `#topic` tag filter will read.

## What it does

1. **Scroll** the Qdrant `papers` collection with vectors.
2. **L2-normalise** every vector (SPECTER outputs aren't guaranteed
   unit-norm; without this the downstream Euclidean HDBSCAN produces
   one mega-cluster).
3. **UMAP → 10d** (`n_neighbors=15`, `min_dist=0`, cosine metric) for
   clustering. 2D is too lossy on a dense corpus.
4. **HDBSCAN** (`min_cluster_size=15`, Euclidean) on the 10d output.
   Points that don't fit any cluster land in `cluster_id = -1`
   (slug `unclustered`) — honest noise rather than forced assignment.
5. **UMAP → 2d** separately, for the visual map coordinates.
6. **Label** each cluster via vLLM: sample 10 titles closest to the
   cluster's 2D centroid, ask the model for a 2-4 word topic name
   (`enable_thinking=false`, `temperature=0.2`, `max_tokens=20`).
7. **Write back** `cluster_id`, `topic_label`, `topic_slug` to every
   Qdrant point, grouped by cluster so the full corpus costs
   `O(cluster_count)` HTTP calls rather than `O(paper_count)`.
8. **Emit** `/opt/munin/knowledge/embedding_map.json` with per-point
   coordinates + cluster metadata. Atomic write via tempfile rename.

## Files

| Path | Purpose |
|---|---|
| `scripts/knowledge/build_embedding_map.py` | The actual script |
| `scripts/knowledge/requirements.txt` | `umap-learn`, `hdbscan`, `qdrant-client`, `numpy`, `requests` |
| `config/munin-embedding-map.service` | systemd oneshot unit |
| `config/munin-embedding-map.timer` | Nightly 03:00 timer (5 min random jitter) |
| `/opt/munin/services/knowledge/venv/` | Dedicated Python venv (Debian 12 PEP 668) |
| `/opt/cluster/scripts/knowledge/build_embedding_map.py` | Deployed copy the unit executes |
| `/opt/munin/knowledge/embedding_map.json` | Output file (~5-10 MB on 30k papers) |
| `/opt/munin/knowledge/numba-cache/` | Numba JIT cache (required under `ProtectSystem=strict`) |

## API

```
GET /api/embedding_map
```

Served by `retrieval/main.py`. Returns the raw JSON file as-is.
404 with the standard error envelope if the map hasn't been built
yet. No auth — the shape is already public information (paper DOIs
+ cluster labels).

Response shape:

```json
{
  "generated_at": "2026-04-20T12:09:03.390034Z",
  "paper_count": 29893,
  "cluster_count": 213,
  "points": [
    {"id": "...", "doi": "10.1234/...", "title": "...", "year": 2023,
     "x": -4.23, "y": 1.87, "cluster": 186}
  ],
  "clusters": [
    {"id": 186, "label": "NMR studies of lipid bilayers",
     "slug": "nmr-studies-of-lipid-bilayers", "size": 216,
     "centroid": [0.71, -0.58]}
  ]
}
```

The container reads from `/knowledge/embedding_map.json` (mount of
the host's `/opt/munin/knowledge/` directory, read-only).

## Deploy

```bash
sudo ./deploy.sh knowledge
```

This mode:

1. Syncs the script to `/opt/cluster/scripts/knowledge/`.
2. Creates `/opt/munin/services/knowledge/venv/` if missing and
   `pip install`s the requirements into it. System pip is blocked by
   PEP 668 on Debian 12+; a dedicated venv mirrors the vLLM pattern.
3. Installs the systemd `.service` + `.timer` units to
   `/etc/systemd/system/`.
4. `systemctl enable` and `restart` the timer.

The retrieval container needs the `/opt/munin/knowledge` volume
mount to serve `/api/embedding_map`. That's wired up in
`docker/docker-compose.yml` under the `retrieval:` service. If you
touched the compose file, run:

```bash
sudo ./deploy.sh compose
sudo ./deploy.sh retrieval
```

## Operate

### Trigger a run manually

```bash
sudo systemctl start munin-embedding-map.service
sudo journalctl -fu munin-embedding-map.service
```

### Force a rebuild (default skips if JSON is fresh)

```bash
sudo /opt/munin/services/knowledge/venv/bin/python3 \
    /opt/cluster/scripts/knowledge/build_embedding_map.py --force
```

### Inspect the result

```bash
jq '{paper_count, cluster_count, generated_at}' \
    /opt/munin/knowledge/embedding_map.json

# Top 20 clusters by size
jq '.clusters | sort_by(-.size) | .[0:20]' \
    /opt/munin/knowledge/embedding_map.json

# Noise ratio
jq '[.points[] | select(.cluster == -1)] | length' \
    /opt/munin/knowledge/embedding_map.json
```

### Confirm Qdrant payloads got stamped

```bash
curl -s http://127.0.0.1:6333/collections/papers/points/scroll \
    -H 'Content-Type: application/json' \
    -d '{"limit": 3,
         "with_payload": ["doi","title","topic_slug","topic_label","cluster_id"]}' \
    | jq '.result.points[].payload'
```

### Hit the endpoint

```bash
curl -s http://127.0.0.1:8080/api/embedding_map \
    | jq '{paper_count, cluster_count, generated_at}'
```

### Watch the timer

```bash
systemctl list-timers munin-embedding-map.timer
# next trigger is printed; run completes in ~1-2 min on 30k papers
```

## Tunables (CLI flags on the script)

| Flag | Default | Effect |
|---|---|---|
| `--force` | off | Rebuild even when JSON looks fresh |
| `--no-label` | off | Skip vLLM labelling (fallback `cluster-N`) |
| `--dry-run` | off | Read + cluster + print summary, write nothing |
| `--scroll-batch N` | 512 | Qdrant scroll page size |
| `--umap-neighbors N` | 15 | UMAP `n_neighbors` |
| `--cluster-dims N` | 10 | UMAP dim for HDBSCAN input (not the map) |
| `--min-cluster-size N` | 15 | HDBSCAN minimum cluster size |

Tuning knobs you'd actually touch:

- **Noise ratio too high** (currently ~42 % at default settings): drop
  `--min-cluster-size` to 10 or 8. Or switch to
  `cluster_selection_method="leaf"` inside `cluster_hdbscan` for more
  permissive fragmentation.
- **Labels look generic** ("Molecular biology", "Biochemistry"):
  increase the number of sample titles per cluster from 10 to
  20-30 in `centroids_and_samples`, or tighten the prompt in
  `LABEL_PROMPT`.
- **Cluster count exploding** (> 500): raise `--min-cluster-size`
  or lower `--umap-neighbors` (less global structure).

## Guardrails

The script refuses to clobber existing good labels when vLLM is
unreachable or mid-outage. Two checks:

1. **Pre-labelling health probe.** Before the first label call, the
   script GETs `VLLM_URL/v1/models` (5 s timeout) and confirms the
   configured `VLLM_MODEL_NAME` is in the loaded-models list. If
   either fails, the script exits **3** without touching Qdrant.
   This is the check that protects the nightly 01:30 run from
   firing while vLLM is in its 02:00-06:00 downtime window, or
   mid-restart.

2. **Post-labelling success-rate threshold.** After the labelling
   loop, the script counts how many clusters fell back to
   `cluster-N`. If more than 50% (the `FALLBACK_FAIL_THRESHOLD`
   constant) got fallbacks, the script exits **4** without
   writing payloads or the JSON. Catches the case where vLLM was
   up for the initial probe but flaked mid-run (restart, OOM, S2
   timeout cascade pushing requests out of window).

Both guardrails are suppressed by `--no-label`, which explicitly
asks for `cluster-N` labels.

| Exit code | Meaning |
|---|---|
| 0 | Success (or freshness skip) |
| 1 | Clustering error or unexpected failure |
| 2 | Not enough points in the corpus to cluster |
| 3 | vLLM health probe failed |
| 4 | Labelling success rate below threshold |

systemd surfaces non-zero exits in `systemctl status` and
`journalctl -u munin-embedding-map.service`. Treat 3 and 4 as
"skipped this run — the previous map is still authoritative."

## Idempotency

The script skips the rebuild when:

- A `/opt/munin/knowledge/embedding_map.json` already exists, AND
- Its `paper_count` matches the current Qdrant count, AND
- Every point already has a `cluster_id` payload.

Use `--force` to override. The timer is safe to run nightly even if
nothing changed — the skip is very fast (one Qdrant scroll).

## Known limitations

- **No incremental updates.** Every run reclusters the full corpus.
  On 30k papers that's ~1-2 min total (UMAP dominates). Fine until
  the corpus hits ~200k, at which point we'd need `UMAP.transform`
  on new points against a pinned reducer rather than a full rebuild.
- **Labels can drift night to night** if the vLLM sampling hits
  different representative titles. In practice labels are stable for
  the big clusters; small clusters (30-50 papers) can flip between
  runs. Not a problem for `#topic` filter matching because slugs
  land on the same point set — filters are by `cluster_id`, not slug.
- **Noise points are not tag-retrievable.** Papers in `cluster_id =
  -1` don't match any `#topic` tag. This is a feature, not a bug:
  it's more honest than forcing every paper into something.
- **Cluster IDs are not stable across rebuilds.** HDBSCAN numbers
  clusters by discovery order. If the frontend persists a
  user-pinned cluster ID overnight, it needs to key off `topic_slug`
  (reasonably stable for big topics) rather than `cluster_id`.
