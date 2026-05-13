# Contributor-Corpus Ingest: Operator Reference

§28 Sprint A, shipped 2026-04-20. Accepts PDFs uploaded via
`upload.muninai.org` (tusd → `hook_service.py` on the VPS) and routes
them into the shared `papers` corpus with contributor attribution.

Full design lives in `docs/future_features.md` §28 and the agreed
plan in `docs/CONTRIBUTOR-CORPUS-PLAN.md`.

## Pipeline

```
VPS                                       Cluster (hugin)
───                                       ───────────────
upload.muninai.org (Uppy)
    │  PDF, 50 MB resumable chunks
    ▼
tusd :8080
    │  post-finish webhook
    ▼
hook_service.py
    │  move → /mnt/uploads/complete/<email>/
    │
    │  HTTP POST over autossh tunnel
    │  http://127.0.0.1:18080/api/admin/ingest
    │  Authorization: Bearer $ADMIN_INGEST_TOKEN
    │  (file, email, filename, [upload_time])
    ▼                                     POST /api/admin/ingest
                                              │
                                              ▼
                                          Save to /papers/inbox/{uuid}.pdf
                                          Write sidecar {uuid}.contributor.json
                                          Look up email in contributors.yml
                                              │
                                              ▼
                                          python3 /app/pipeline/paper_pipeline.py --single
                                          GROBID → CrossRef → SPECTER → Qdrant + Neo4j
                                              │
                                              ▼
                                          Move PDF → /papers/doi_{slug}.pdf
                                          Delete sidecar
                                              │
    ▲  200 OK                                 │
    │  {status, doi, paper_id, contributor}   │
    │  └──────────────────────────────────────┘
    ▼
hook_service.py: move → /mnt/uploads/processed/<email>/
```

Synchronous end-to-end, the 200 OK only comes back after the paper
is fully indexed in Qdrant + Neo4j. Matches the VPS's
"200 = move to processed/" semantics.

## Auth

Shared bearer token, 64-char hex:

```bash
# Generate once
openssl rand -hex 32

# Cluster side: /opt/hugin/config/cluster.env  (or wherever docker
# compose loads env from; see /opt/munin/docker/.env symlink)
ADMIN_INGEST_TOKEN=<the 64-char value>

# VPS side: hook_service.py config, same value
```

The retrieval container reads `ADMIN_INGEST_TOKEN` at startup. Token
comparison is constant-time (`secrets.compare_digest`). Empty token
→ endpoint returns 503 rather than accepting requests.

## Endpoint contract

```
POST /api/admin/ingest
Authorization: Bearer <token>
Content-Type: multipart/form-data

file: <PDF bytes>          (required; first 4 bytes must be %PDF)
email: <uploader email>    (required; case-insensitive)
filename: <str>            (optional; original filename for logs/payload)
upload_time: <ISO 8601>    (optional; defaults to server now())
```

### Responses

**200 ingested**: paper is in Qdrant + Neo4j, PDF moved to final
location.

```json
{
  "status": "ingested",
  "paper_id": "c9220a8cf7717b0e",
  "doi": "10.1288/00005537-199203000-00005",
  "title": "Angioedema: 5 Years' experience, ...",
  "final_pdf_path": "/papers/doi_10.1288_00005537-199203000-00005.pdf",
  "contributor": {
    "email": "contributor-c@example.org",
    "group_slug": "zeitler",
    "known": true
  }
}
```

`contributor.known = false` means the uploader wasn't in
`contributors.yml`; the paper was still ingested, but with
`group_slug="unknown"` and no display name. Add an allowlist entry
and POST the file again (the pipeline's DOI dedup will merge).

**200 skipped**: pipeline ran but didn't create a Qdrant point.
Usually quality filter (low citation count, non-research content,
empty title, etc.). PDF and sidecar are left in `inbox/` for
forensic inspection.

```json
{
  "status": "skipped",
  "reason": "Pipeline completed but no Qdrant point was created ...",
  "log_tail": "<last 2 KB of pipeline stdout/stderr>"
}
```

**Other statuses:**

| Code | Meaning | Client action |
|---|---|---|
| 400 | File empty or not a PDF (`%PDF` header missing), or `email` missing | Don't retry, fix the request |
| 401 | Missing or wrong bearer token | Don't retry, fix auth |
| 500 | Pipeline exited non-zero (traceback in `log_tail`) | Don't retry, file moved to `pdf/failed/` |
| **503** | **Either `ADMIN_INGEST_TOKEN` not configured (rare), OR the ingest-pipeline concurrency cap has been hit (common under heavy load)** | **Honour the `Retry-After: <seconds>` response header. Sleep that long, then retry the same file.** |
| 504 | Pipeline exceeded `PIPELINE_TIMEOUT_SECS` (default 600) | File moved to `pdf/failed/`. Manual triage; don't auto-retry. |

All errors use the standard `{"detail": {"error": {"message": "..."}}}`
envelope.

### 503 + Retry-After backpressure (from 2026-04-23)

`/api/admin/ingest` runs at most `INGEST_CONCURRENCY` (default 4)
pipeline subprocesses simultaneously. The cap exists because each
in-flight call holds an HTTP connection to GROBID, and GROBID's
engine pool is bounded (10 by default). Without the cap a high-
volume client (VPS hook + cron retry) saturated the pool and
produced sustained 503s (the 2026-04-23 fork-bomb incident).

When over the cap, the endpoint waits up to 5 s for a free slot,
then fast-fails:

```
HTTP/1.1 503 Service Unavailable
Retry-After: 60
Content-Type: application/json

{"detail": {"error": {"message": "Ingest pipeline saturated (>= 4 concurrent jobs). Retry after the cooldown."}}}
```

**Clients MUST honour `Retry-After`.** Spinning at 1 req/s against
a 503 endpoint accomplishes nothing and wastes both sides' cycles.
Recommended client logic:

```python
if r.status_code == 503:
    sleep_secs = int(r.headers.get("Retry-After", "60"))
    log.info("cluster saturated, sleeping %ds before next paper", sleep_secs)
    time.sleep(sleep_secs)
    return False, "503:saturated", None    # leave file in complete/
```

For the VPS-side `hook_service.py` + cron, this means:

- **`hook_service.py`** background task: on 503, log + sleep
  Retry-After + leave the file in `complete/<email>/`. The next
  cron sweep retries.
- **The cron-driven retry sweep**: on 503, exit the loop (don't
  process more files this run); next cron tick will succeed once
  the cluster has spare capacity.

The cap is env-tunable on the cluster:

```
# docker-compose.yml retrieval service
INGEST_CONCURRENCY=${INGEST_CONCURRENCY:-4}
```

Bump to 6-8 only if GROBID's pool is also raised
(`grobid.yaml: concurrency`); never above `<grobid concurrency> -
<watcher --workers>` or you re-create the saturation.

## Payload shape on Qdrant `papers`

After an ingest, the point gains a `contributors` array:

```json
{
  "paper_id": "...",
  "title": "...",
  "abstract": "...",
  "doi": "10.1288/00005537-199203000-00005",
  "year": 1992,
  "authors": [...],
  "journal": "The Laryngoscope",
  "pdf_path": "/papers/doi_10.1288_00005537-199203000-00005.pdf",
  "contributors": [
    {
      "email": "contributor-c@example.org",
      "username": "zeitler",
      "display_name": "Contributor C",
      "group_slug": "zeitler",
      "group_display_name": "Zeitler Lab (Leipzig)",
      "upload_time": "2026-04-20T13:28:28.622014Z"
    }
  ],
  "cluster_id": 186,
  "topic_label": "NMR studies of lipid bilayers",
  "topic_slug": "nmr-studies-of-lipid-bilayers"
}
```

Multiple groups uploading the same paper produce one Qdrant point
with multiple `contributors[]` entries (DOI-keyed point IDs merge
them automatically). `cluster_*` fields are §15's and are
preserved across ingests.

## Neo4j shape

```cypher
(:Contributor {
    email: "contributor-c@example.org",
    username: "zeitler",
    display_name: "Contributor C",
    group_slug: "zeitler",
    group_display_name: "Zeitler Lab (Leipzig)"
})
-[:CONTRIBUTED {upload_time: "2026-04-20T13:28:28.622014Z"}]->
(:Paper {doi: "10.1288/00005537-199203000-00005", ...})
```

Queries:

```cypher
// All papers contributed by a group
MATCH (c:Contributor {group_slug: "zeitler"})-[:CONTRIBUTED]->(p:Paper)
RETURN p.doi, p.title
ORDER BY p.year DESC

// Who contributed this paper?
MATCH (c:Contributor)-[r:CONTRIBUTED]->(p:Paper {doi: $doi})
RETURN c.display_name, c.group_slug, r.upload_time

// Papers contributed by more than one group
MATCH (p:Paper)<-[:CONTRIBUTED]-(c:Contributor)
WITH p, count(DISTINCT c) AS groups
WHERE groups > 1
RETURN p.doi, p.title, groups
```

## Files

| Path | Purpose |
|---|---|
| `retrieval/main.py` | `/api/admin/ingest` endpoint (§28 block near `/api/embedding_map`) |
| `scripts/pipeline/paper_pipeline.py` | Reads sidecars, stamps contributors, DOI-keyed Qdrant point IDs |
| `shared/config/contributors.yml` | Allowlist (email → slug + display name), single source of truth, read by backend deploy + VPS backfill cron |
| `backend/config/munin.env.template` | Documents `ADMIN_INGEST_TOKEN` and related vars |
| `/opt/munin/data/papers/pdf/inbox/` | Staging dir; cleared after successful ingest |
| `/opt/munin/data/papers/pdf/doi_{slug}.pdf` | Final home; reachable via `get_paper_pdf` MCP tool |
| `/opt/cluster/scripts/pipeline/paper_pipeline.py` | Host-side deployed copy (mounted into retrieval at `/app/pipeline:ro`) |

## Deploy

```bash
sudo ./deploy.sh agents       # syncs contributors.yml + faq.yml + agents.yml
sudo ./deploy.sh pipeline     # syncs paper_pipeline.py → /opt/cluster/scripts/pipeline/
sudo ./deploy.sh compose      # new volume mounts (:rw on /papers, pipeline mount)
sudo ./deploy.sh retrieval    # rebuild container with the endpoint + `requests` dep
```

### Adding a new contributor

The workflow takes ~5 minutes and doesn't require a container restart.

1. **Edit `shared/config/contributors.yml`** in this repo. Two YAML
   shapes are supported:

   ```yaml
   # Single person, single email (most common).
   - email: alice@example.org
     username: alice
     display_name: Alice Mustermann
     research_group: alice
     research_group_display_name: Mustermann Lab (Leipzig)

   # One person with multiple aliases (use `emails:` list).
   - emails:
       - bob@example.org
       - bob.example@gmail.com
     username: bob
     display_name: Bob Beispiel
     research_group: bob
     research_group_display_name: Beispiel Group (Leipzig)
   ```

   **Multiple people in the same group**: write each as a separate
   entry, all sharing the same `research_group` slug. Per-person
   attribution AND `#group` filtering both work that way:

   ```yaml
   - email: contributor-d@example.org
     username: elgeti
     display_name: Contributor D
     research_group: elgeti
     research_group_display_name: Elgeti Lab (Leipzig)

   - email: contributor-e@example.org
     username: contributor-e
     display_name: Contributor E
     research_group: elgeti                          # SAME group slug
     research_group_display_name: Elgeti Lab (Leipzig)
   ```

2. **Deploy the change**: syncs `contributors.yml` to
   `/opt/munin/config/`:

   ```bash
   sudo ./deploy.sh agents
   ```

   The endpoint auto-reloads the YAML on file mtime change. **No
   container restart needed.** Future uploads from any of the
   listed emails get attributed correctly from that moment on.

3. **Backfill past `unknown` uploads** (optional but recommended).
   Uploads from a person who was added LATER are stamped as
   `group_slug: "unknown"` until you re-attribute them:

   ```bash
   # Dry-run first: see what WOULD change
   sudo /opt/munin/services/pipeline/venv/bin/python3 \
       /opt/cluster/scripts/pipeline/paper_cleanup.py reattribute --dry-run

   # Real run: updates Qdrant payloads + Neo4j Contributor edges
   sudo /opt/munin/services/pipeline/venv/bin/python3 \
       /opt/cluster/scripts/pipeline/paper_cleanup.py reattribute
   ```

   Folded into `paper_cleanup.py` on 2026-05-13; was previously a
   standalone `reattribute_unknown.py` script. The
   `munin-paper-reattribute.timer` service (added the same day but
   not enabled until the consolidation completes) will eventually
   run this nightly so manual re-attribution becomes optional.

   The script walks every paper with `contributors[].group_slug
   == "unknown"`, looks up the email against the current
   `contributors.yml`, and rewrites the contributor entry if the
   uploader is now allowlisted. Idempotent, safe to re-run after
   every allowlist edit.

4. **Verify** the new contributor's papers are visible:

   ```bash
   curl -s 'http://127.0.0.1:8080/api/tags/group/<slug>/papers?limit=5' | jq .
   ```

## Smoke test

```bash
TOKEN=$(sudo grep '^ADMIN_INGEST_TOKEN=' /opt/hugin/config/cluster.env | cut -d= -f2-)

# Should 401
curl -si -X POST http://127.0.0.1:8080/api/admin/ingest \
    -H "Authorization: Bearer wrong" \
    -F "file=@/opt/munin/data/papers/pdf/doi_10.1001_archinte.158.18.2063.pdf" \
    -F "email=x@y" | head -5

# Should 200 ingested (or 200 skipped for quality-filtered papers)
curl -s -X POST http://127.0.0.1:8080/api/admin/ingest \
    -H "Authorization: Bearer $TOKEN" \
    -F "file=@/opt/munin/data/papers/pdf/doi_10.1001_archinte.158.18.2063.pdf" \
    -F "email=contributor-c@example.org" | jq .

# Verify Qdrant stamp
curl -s http://127.0.0.1:6333/collections/papers/points/scroll \
    -H 'Content-Type: application/json' \
    -d '{"filter": {"must": [{"key": "contributors[].group_slug",
         "match": {"value": "zeitler"}}]}, "limit": 3,
         "with_payload": ["doi","title","contributors"]}' | jq .

# Verify Neo4j stamp
NEOPW=$(sudo grep '^NEO4J_PASSWORD=' /opt/hugin/config/cluster.env | cut -d= -f2-)
curl -s -X POST http://127.0.0.1:7474/db/neo4j/tx/commit \
    -u "neo4j:$NEOPW" -H 'Content-Type: application/json' \
    -d '{"statements":[{"statement":"MATCH (c:Contributor)-[r:CONTRIBUTED]->(p:Paper) WHERE c.group_slug = \"zeitler\" RETURN c.email, c.display_name, p.doi, p.title LIMIT 5"}]}' \
    | jq '.results[0].data'
```

## Known issues

### GROBID heap + concurrency tuning (resolved 2026-04-23)

Default lfoppiano/grobid:0.8.0 ships with a ~4 GB JVM heap, which
OOMs in the AsyncAppender thread under sustained concurrent load
without crashing the container (so docker shows healthy while
requests time out). Fixed by setting `JAVA_TOOL_OPTIONS=-Xmx12g
-Xms2g` on the grobid service in `docker-compose.yml` (the
lfoppiano image ignores `JAVA_OPTS`; only the JVM-built-in
`JAVA_TOOL_OPTIONS` is honoured).

Companion fix on the cluster API side: the new
`INGEST_CONCURRENCY` semaphore on `/api/admin/ingest` documented
above prevents the engine pool from being starved in the first
place. Both fixes are in place; a future GROBID upgrade or pool-
size increase would let us tune `INGEST_CONCURRENCY` higher.

### DOI misidentification by GROBID

The inbox uses UUID filenames, so the pipeline's `doi_X.pdf`
filename heuristic is bypassed and GROBID's content-based
extraction is authoritative. GROBID occasionally picks a
cited paper's DOI instead of the paper's own, especially for
short papers with dense reference sections. Example observed
during smoke test: a file named `doi_10.1001_archinte.158.18.2063.pdf`
was re-identified by GROBID as `10.1288/00005537-199203000-00005`
(a paper appearing in the references).

For the real upload surface (`upload.muninai.org`), filenames are
user-chosen so this heuristic is unavailable regardless, GROBID is
the sole source. Mitigations, not yet implemented:

- CrossRef title round-trip: fetch CrossRef record for the extracted
  DOI; reject if returned title doesn't roughly match GROBID's.
- OpenAlex disambiguation as a fallback.
- Accept user-provided DOI alongside the file (needs Uppy UI change).

### Existing crawler-ingested papers have path-hashed point IDs

Before §28, Qdrant point IDs were derived from the PDF's filesystem
path (`sha256(pdf_path)`). §28 switched to DOI-hashed IDs. Existing
points are unchanged; new ingests write to new point IDs. If a paper
already indexed by the crawler is then contributor-ingested, a
duplicate point appears.

This doesn't affect search (both points are in the same collection
and will score similarly) but does waste space and splits the
`contributors[]` merge behaviour. A migration sweep that re-keys
existing points on their payload `doi` would resolve it; not yet
written.

### Pipeline SPECTER cold-start per call

Each `/api/admin/ingest` POST spawns a fresh `paper_pipeline.py`
subprocess, which loads SPECTER (~2-3 GB, ~10-20 s) from scratch.
Fine for steady-state ingestion (5-10 papers/day) but painful for
the one-time 4,236-paper backfill, budget several hours regardless
of VPS-side parallelism, because GROBID + SPECTER are the bottleneck
on the cluster. Acceptable for a one-time migration. A persistent
pipeline daemon is the longer-term fix; not in this sprint.

## Tunables (env vars)

| Var | Default | Purpose |
|---|---|---|
| `ADMIN_INGEST_TOKEN` | _unset_ | Bearer token; endpoint returns 503 without it |
| `CONTRIBUTORS_CONFIG` | `/app/config/contributors.yml` | Allowlist path (mtime-reloaded) |
| `PAPER_PIPELINE_SCRIPT` | `/app/pipeline/paper_pipeline.py` | Pipeline path (mounted read-only) |
| `PIPELINE_TIMEOUT_SECS` | `600` | Per-paper timeout before 504 |
| `PAPERS_PDF_DIR` | `/papers` | Final PDF home (and inbox parent) |
| `GROBID_URL` | `http://grobid:8070` | Passed to the subprocess env |

## What's next

Sprint B (tag-scoped search) brings the `#zeitler` / `#corzilius` /
`#deibel` / `#nmr` tag filters into chat. Until it lands, contributor
attribution is stored but not query-surfaced, papers filter
identically to any other, and the `contributors[]` field is visible
only via direct Qdrant / Neo4j queries.
