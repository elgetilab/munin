# VPS-Side Ingest Wiring + Backfill — Hand-off

Closes out §28 from the VPS (`munin-vps` repo, host
`<vps-host>`). The cluster side is fully live; only the VPS push
path and the one-time migration remain.

Related docs:
- `docs/CONTRIBUTOR-INGEST.md` — cluster endpoint contract
- `docs/CONTRIBUTOR-CORPUS-PLAN.md` — full sprint plan
- `docs/USER-DOCUMENTS-ANSWERS.md` — VPS architecture (tusd, hook_service, /mnt/uploads layout)

## 1. Share the admin token with the VPS

The cluster holds `ADMIN_INGEST_TOKEN` (64-char hex) in
`/opt/hugin/config/cluster.env`. The VPS needs the same value in its
environment so `hook_service.py` can authenticate.

```bash
# On the cluster (hugin):
sudo grep '^ADMIN_INGEST_TOKEN=' /opt/hugin/config/cluster.env

# On the VPS: copy that value into hook_service's env. Wherever
# hook_service.py reads its config (systemd unit EnvironmentFile,
# /etc/default/hook-service, or inline shell export), set:
#   ADMIN_INGEST_TOKEN=<the 64-char hex>
sudo systemctl restart hook-service.service   # or whatever unit hosts it
```

## 2. Extend `hook_service.py` to POST to the cluster

After the existing `post-finish` logic (file moved to
`/mnt/uploads/complete/<email>/<filename>`), add:

```python
import os, shutil, requests, pathlib, logging

CLUSTER_INGEST_URL = "http://127.0.0.1:18080/api/admin/ingest"
ADMIN_INGEST_TOKEN = os.environ["ADMIN_INGEST_TOKEN"]
PROCESSED_ROOT = pathlib.Path("/mnt/uploads/processed")
log = logging.getLogger("hook_service")

def push_to_cluster(pdf_path: pathlib.Path, uploader_email: str,
                    original_filename: str | None = None) -> bool:
    """POST one PDF to the cluster. On 200 OK move it to processed/.
    Returns True on success, False otherwise (file stays in complete/)."""
    try:
        with pdf_path.open("rb") as f:
            r = requests.post(
                CLUSTER_INGEST_URL,
                headers={"Authorization": f"Bearer {ADMIN_INGEST_TOKEN}"},
                files={"file": (pdf_path.name, f, "application/pdf")},
                data={
                    "email": uploader_email,
                    "filename": original_filename or pdf_path.name,
                },
                timeout=900,   # pipeline may take up to ~10 min
            )
    except requests.RequestException as e:
        log.error("cluster POST failed for %s: %s", pdf_path, e)
        return False

    if r.status_code != 200:
        log.error("cluster returned %d for %s: %s",
                  r.status_code, pdf_path, r.text[:500])
        return False

    body = r.json()
    log.info("ingested %s: status=%s doi=%s",
             pdf_path.name, body.get("status"), body.get("doi"))

    # Move to processed/<email>/ on success (ingested OR skipped —
    # both mean "the cluster saw it; don't retry next restart").
    processed_dir = PROCESSED_ROOT / uploader_email
    processed_dir.mkdir(parents=True, exist_ok=True)
    shutil.move(str(pdf_path), str(processed_dir / pdf_path.name))
    return True
```

Hook this into the tusd `post-finish` handler right after the
existing `complete/<email>/` move. The response body has three
outcomes worth logging:

- `status: "ingested"` — paper in Qdrant + Neo4j, visible in search.
- `status: "skipped"` — pipeline ran, skipped by quality filter or
  non-research detector. Move to `processed/` regardless so it
  doesn't retry.
- `status != 200` — leave in `complete/`, the retry happens on the
  next hook_service restart or manual sweep.

## 3. One-time backfill of the 4,236 existing files

A standalone script invoked once. Contents of
`/mnt/uploads/complete/`:

| User | Files | Size | Action |
|---|---|---|---|
| `contributor-a@example.org` | 1,819 | 3.0 GB | backfill |
| `contributor-b@example.org` | 1,998 | 7.6 GB | backfill |
| `contributor-c@example.org` | 418 | 2.1 GB | backfill |
| `admin@example.org` | 1 | 2.1 MB | **skip** (admin test file) |

Shape (Python, run with a venv that has `requests`):

```python
# /usr/local/bin/backfill_uploads.py
import os, sys, shutil, pathlib, requests, concurrent.futures, time

COMPLETE = pathlib.Path("/mnt/uploads/complete")
PROCESSED = pathlib.Path("/mnt/uploads/processed")
SKIPPED_USERS = {"admin@example.org"}
CLUSTER_INGEST_URL = "http://127.0.0.1:18080/api/admin/ingest"
TOKEN = os.environ["ADMIN_INGEST_TOKEN"]
MAX_CONCURRENT = 3  # GROBID + SPECTER on cluster side are the bottleneck

def post_one(pdf_path: pathlib.Path, email: str) -> tuple[pathlib.Path, str]:
    try:
        with pdf_path.open("rb") as f:
            r = requests.post(
                CLUSTER_INGEST_URL,
                headers={"Authorization": f"Bearer {TOKEN}"},
                files={"file": (pdf_path.name, f, "application/pdf")},
                data={"email": email, "filename": pdf_path.name},
                timeout=900,
            )
        status = f"{r.status_code}:{r.json().get('status', '?')}" if r.ok else f"{r.status_code}"
        if r.ok:
            dst = PROCESSED / email / pdf_path.name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(pdf_path), str(dst))
        return pdf_path, status
    except Exception as e:
        return pdf_path, f"ERR:{e.__class__.__name__}"

def all_pending():
    for email_dir in sorted(COMPLETE.iterdir()):
        if not email_dir.is_dir():
            continue
        email = email_dir.name
        if email in SKIPPED_USERS:
            print(f"[skip] {email} (admin)", file=sys.stderr)
            continue
        for pdf in sorted(email_dir.iterdir()):
            if pdf.is_file() and pdf.suffix.lower() == ".pdf":
                yield pdf, email

def main():
    pending = list(all_pending())
    print(f"backfilling {len(pending)} papers, concurrency={MAX_CONCURRENT}",
          file=sys.stderr)
    done = 0
    t0 = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_CONCURRENT) as ex:
        for pdf, status in ex.map(lambda p: post_one(*p), pending):
            done += 1
            elapsed = time.time() - t0
            rate = done / elapsed if elapsed else 0
            eta = (len(pending) - done) / rate if rate else 0
            print(f"[{done}/{len(pending)}] {status:16} {pdf.name} "
                  f"(rate={rate:.2f}/s eta={eta/60:.1f}min)", flush=True)

if __name__ == "__main__":
    main()
```

Run:

```bash
# On the VPS:
sudo ADMIN_INGEST_TOKEN=$(cat /path/to/env/ADMIN_INGEST_TOKEN) \
     python3 /usr/local/bin/backfill_uploads.py 2>&1 \
     | tee /var/log/backfill-uploads.log
```

Concurrency of 3 is conservative — the cluster-side bottleneck is
GROBID + SPECTER. Expected duration: several hours. Resumable — if
interrupted, re-running skips anything already moved to `processed/`.

## 4. Monitor from the cluster

While the backfill runs, watch from `hugin`:

```bash
# Container-side ingest activity
docker logs --since 1m -f munin-retrieval | grep -E 'ingest|Processing:'

# Group counts climb as files arrive
watch -n 30 'curl -s http://127.0.0.1:8080/api/tags | \
    jq ".groups[] | {slug, paper_count}"'

# Pipeline-level activity (GROBID, SPECTER, DB writes)
docker logs --since 1m -f munin-retrieval | grep -E '\[INFO\]|\[OK\]|\[SKIP\]|\[ERROR\]'
```

## 5. Re-run §15 when the backfill completes

After the backfill finishes, the ~4,235 new papers all sit in
Qdrant without `cluster_id` / `topic_slug` / `topic_label`. The next
nightly §15 rebuild (03:00) picks them up automatically, but you can
kick it manually:

```bash
sudo systemctl start munin-embedding-map.service
sudo journalctl -fu munin-embedding-map.service
```

The script's freshness check notices the paper-count jump and
rebuilds (~1-2 min). After that, every paper has a topic and the
`#topic` tag filter sees the full corpus.

## 6. Verify end-to-end

```bash
# Browse papers contributed by a real user
curl -s 'http://127.0.0.1:8080/api/tags/group/corzilius/papers?limit=3' | jq .

# Catalog now reflects 4,235-ish new papers across the three groups
curl -s http://127.0.0.1:8080/api/tags | jq '.groups'

# Neo4j knows about the new contributor relationships
# (run on cluster, needs NEO4J_PASSWORD from /opt/hugin/config/cluster.env)
# MATCH (c:Contributor) RETURN c.group_slug AS grp, count{(c)-[:CONTRIBUTED]->()} AS n
```

## 7. What happens to future uploads

Once step 2 is live, every new upload via `upload.muninai.org`
automatically flows:

```
user → Uppy → tusd → /mnt/uploads/staging
                          ↓ post-finish
                  hook_service moves → /mnt/uploads/complete/<email>/
                          ↓ hook_service POSTs to cluster
                  /api/admin/ingest → pipeline → Qdrant + Neo4j
                          ↓ 200 OK
                  hook_service moves → /mnt/uploads/processed/<email>/
```

No operator action per upload. The paper is searchable and tag-
filterable within a few minutes.

## Known risks

- **GROBID DOI misidentification** (see `CONTRIBUTOR-INGEST.md`
  Known Issues). Some papers in the backfill will be stored under a
  DOI that belongs to one of their references, not the paper
  itself. Spot-check `GET /api/tags/group/{slug}/papers` against
  known titles; fix individual cases later if needed.
- **Cluster-side disk usage**. 13 GB of PDFs will land at
  `/opt/munin/data/papers/pdf/`. Pre-check free space
  (`df -h /opt/munin`) before starting.
- **Neo4j growth**. Every new `Paper` node + `CONTRIBUTED`
  relationship adds ~100 bytes. 4,235 papers ≈ 0.5 MB. Fine.
