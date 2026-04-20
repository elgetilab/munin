# User Documents — Answers to Cluster Questions

Responses to the open questions from the cluster side, based on
current VPS infrastructure and operational preferences.

---

## Transport from VPS to cluster

### 1. Pull or push?

**Push via HTTP through the existing tunnel.**

The autossh tunnel already exposes the cluster retrieval API at
`127.0.0.1:18080` on the VPS. After `hook_service.py` moves a
finished upload to `/mnt/uploads/complete/<email>/`, it should POST
the file to a new cluster endpoint:

```
POST http://127.0.0.1:18080/api/admin/ingest
Content-Type: multipart/form-data

file: <the PDF>
email: <uploader email>
filename: <original filename>
```

No new SSH keys, no new tunnel ports, no rsync. Just an HTTP call
over the port that's already open.

I will update `hook_service.py` on the VPS to make this call once the
cluster endpoint exists.

### 2. Cadence?

**Real-time, triggered by the tusd webhook.**

`hook_service.py` already fires on every `post-finish` event. The
HTTP POST to the cluster will happen immediately after the file move.
No cron, no polling — papers should be searchable within minutes of
upload.

### 3. Idempotency / dedup tracking?

**Option (a): move to `/mnt/uploads/processed/<email>/` on the VPS
after successful cluster ingest.**

Flow:
1. tusd completes upload → file lands in `/mnt/uploads/staging/`
2. `hook_service.py` moves to `/mnt/uploads/complete/<email>/`
3. `hook_service.py` POSTs to cluster
4. On 200 OK → move from `complete/` to `processed/<email>/`
5. On error → leave in `complete/`, log the failure, retry on next
   restart or manual trigger

This is visible (ls the directories to see what's pending), simple,
and re-ingestable (move files back to `complete/` to reprocess).

The cluster can additionally track by content hash as a safety net,
but the VPS-side directory move is the primary signal.

---

## Attribution

### 4. Allowlist miss?

**Ingest anyway with `contributor_username="unknown"` and no group
tag.**

Don't silently drop papers because of a missing YAML entry. An admin
can add attribution retroactively. Better to have the paper searchable
with incomplete metadata than not searchable at all.

### 5. Group mapping for existing users

Use these for `contributors.yml`:

| Email | Slug | Display Name |
|-------|------|--------------|
| `contributor-a@example.org` | `corzilius` | Corzilius Lab (Rostock) |
| `contributor-b@example.org` | `deibel` | Deibel Group (Chemnitz) |
| `contributor-c@example.org` | `zeitler` | Zeitler Lab (Leipzig) |
| `admin@example.org` | `admin` | Admin (test uploads) |

These are derived from email domains. Correct them if you have better
display names.

---

## File shape

### 6. ZIPs?

**All 4,236 existing files are PDFs.** Breakdown:
- 4,235 × `.pdf`
- 1 × `.PDF`
- 0 ZIPs, 0 other formats

No unzipping logic needed. The upload page (Uppy) already restricts
to PDF-only. No change needed for future uploads.

Per-user breakdown:

| User | Files | Size |
|------|-------|------|
| `contributor-a@example.org` | 1,819 | 3.0 GB |
| `contributor-b@example.org` | 1,998 | 7.6 GB |
| `contributor-c@example.org` | 418 | 2.1 GB |
| `admin@example.org` | 1 | 2.1 MB |
| **Total** | **4,236** | **~13 GB** |

### 7. Cross-group duplicates?

**One Qdrant point per paper, multiple contributors (list field).**

If the same DOI is uploaded by two groups, dedup by DOI and append the
new contributor to the existing point's contributor list. This avoids
duplicate embeddings and correctly credits both groups.

Semantics: the `contributors` field becomes a list of
`{email, group_slug, upload_time}` objects rather than a single value.

---

## Retention

### 8. After successful ingestion?

**Keep on both sides.**

- **VPS**: move from `complete/` to `processed/<email>/` (archive
  copy, not deleted)
- **Cluster**: copy to `/opt/munin/data/papers/pdf/` (working copy
  for `get_paper_pdf` / `read_paper` MCP tools)

The VPS copy is the backup. The cluster copy is the working copy that
serves PDF downloads and re-embedding if the model changes. No
deletion on either side.

---

## Summary of VPS-side changes needed

Once the cluster endpoint (`POST /api/admin/ingest`) is ready, I will
update `hook_service.py` to:

1. POST the file to `http://127.0.0.1:18080/api/admin/ingest` after
   the move to `complete/`
2. On success, move to `processed/<email>/`
3. On failure, leave in `complete/` and log the error

I will also write a one-time migration script that walks
`/mnt/uploads/complete/` and POSTs all existing files to the ingest
endpoint (with progress logging and resume capability).

Let me know when the endpoint is deployed and I'll wire it up.
