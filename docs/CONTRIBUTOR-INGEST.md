# Contributor-Corpus Ingest — Operator Reference

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

Synchronous end-to-end — the 200 OK only comes back after the paper
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

**200 ingested** — paper is in Qdrant + Neo4j, PDF moved to final
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

**200 skipped** — pipeline ran but didn't create a Qdrant point.
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

| Code | Meaning |
|---|---|
| 400 | File empty or not a PDF (`%PDF` header missing), or `email` missing |
| 401 | Missing or wrong bearer token |
| 500 | Pipeline exited non-zero (traceback in `log_tail`) |
| 503 | `ADMIN_INGEST_TOKEN` not configured on the server |
| 504 | Pipeline exceeded `PIPELINE_TIMEOUT_SECS` (default 600) |

All errors use the standard `{"detail": {"error": {"message": "..."}}}`
envelope.

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
| `config/contributors.yml` | Allowlist (email → slug + display name) |
| `config/munin.env.template` | Documents `ADMIN_INGEST_TOKEN` and related vars |
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

Adding/editing an allowlist entry:

```bash
# Edit config/contributors.yml in this repo, then:
sudo ./deploy.sh agents
# The endpoint auto-reloads on file mtime change — no container restart.
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

### DOI misidentification by GROBID

The inbox uses UUID filenames, so the pipeline's `doi_X.pdf`
filename heuristic is bypassed and GROBID's content-based
extraction is authoritative. GROBID occasionally picks a
cited paper's DOI instead of the paper's own — especially for
short papers with dense reference sections. Example observed
during smoke test: a file named `doi_10.1001_archinte.158.18.2063.pdf`
was re-identified by GROBID as `10.1288/00005537-199203000-00005`
(a paper appearing in the references).

For the real upload surface (`upload.muninai.org`), filenames are
user-chosen so this heuristic is unavailable regardless — GROBID is
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
the one-time 4,236-paper backfill — budget several hours regardless
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
attribution is stored but not query-surfaced — papers filter
identically to any other, and the `contributors[]` field is visible
only via direct Qdrant / Neo4j queries.
