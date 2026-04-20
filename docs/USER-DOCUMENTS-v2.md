# Per-User Document Store — v2

## Overview

Users can upload documents via two paths. These documents need to be
embedded and stored in a per-user Qdrant collection so they can be
retrieved during chat. This spec updates v1 with the current state of
the upload infrastructure and what the cluster needs to build.

## Current State (VPS side — already working)

### Upload page (`upload.muninai.org`)

- **tusd** handles resumable file uploads (50 MB chunks, 3 parallel)
- On upload completion, tusd fires a `post-finish` webhook to `hook_service.py`
- The hook reads the `X-Munin-Email` header and moves the file from
  staging to its final location

### File locations on VPS

```
/mnt/uploads/
├── staging/          ← tusd writes chunks here during upload
└── complete/         ← finished files, organised by email
    ├── admin@example.org/
    ├── contributor-a@example.org/
    ├── contributor-b@example.org/
    └── contributor-c@example.org/
```

**Current stats:** ~13 GB, 4,236 files across 4 users.

`/mnt/uploads/` is a Hetzner storage volume attached to the VPS
(`<vps-host>`). The files are plain PDFs with sanitised filenames,
no metadata database — just files on disk.

### Chat file attachment (`POST /api/documents/upload`)

The chat frontend also has a file attachment button. This route goes
through the API gateway to the cluster. The frontend already has:

- `uploadDocument()` in `api.ts` (multipart POST with progress)
- `fetchDocuments()` / `deleteDocument()` for listing and removal
- Types: `UploadedDocument` with `document_id`, `filename`, `chunks`,
  `status`, `upload_time`

The gateway exempts `/api/documents/upload` from concurrent request
limits but still logs the request.

### What the VPS does NOT do

- No text extraction
- No chunking or embedding
- No Qdrant interaction
- No indexing or search

All of this needs to happen on the cluster.

## Two Upload Paths

| Path | Entry point | Stored on VPS | For |
|------|-------------|---------------|-----|
| Upload page | `upload.muninai.org` → tusd → hook | `/mnt/uploads/complete/<email>/` | Bulk PDF upload (papers, reports) |
| Chat attachment | `POST /api/documents/upload` → gateway → cluster | Cluster disk | In-conversation document upload |

### Reconciling the upload page files

The 4,236 files already in `/mnt/uploads/complete/` need to be
ingested into the per-user collection. Options:

1. **One-time migration script** — reads `/mnt/uploads/complete/`,
   extracts text, chunks, embeds, stores in Qdrant. Run once, then
   switch the upload page to go through the same pipeline as chat
   attachments.
2. **SSH/rsync pull** — cluster pulls files from VPS on a schedule
   and processes new arrivals. Keeps the upload page flow unchanged.
3. **Redirect upload page through the cluster** — change the upload
   page to POST to `/api/documents/upload` instead of tusd. Simplest
   long-term but requires handling large files through the gateway.

Recommendation: option 2 for existing files + option 1 for new uploads
by pointing the upload page at the cluster endpoint.

## What the Cluster Needs to Build

### 1. Document processing pipeline

```
Receive file (PDF, TXT, MD, DOCX)
    │
    ▼
Extract text:
    - PDF → GROBID (academic) or pdftotext (general)
    - TXT/MD → direct read
    - DOCX → python-docx
    │
    ▼
Chunk text (512 tokens, 50 token overlap)
    - Split on paragraph boundaries first
    - Then sentence boundaries
    - Then hard token limit
    │
    ▼
Embed chunks with BGE-base (768 dimensions)
    │
    ▼
Store in Qdrant `user_docs` collection
```

### 2. Qdrant collection: `user_docs`

**Vector config:** 768 dimensions (BGE-base), cosine distance.

**Payload fields:**

| Field | Type | Purpose |
|-------|------|---------|
| `user_email` | string | Filter: users only see their own docs |
| `document_id` | string | Groups chunks belonging to the same file |
| `conversation_id` | string | Optional: link to a specific chat |
| `filename` | string | Original filename for display |
| `chunk_index` | integer | Position within the document |
| `chunk_text` | string | The actual text chunk |
| `total_chunks` | integer | How many chunks the document was split into |
| `upload_time` | string | ISO 8601 timestamp |

**Payload indexes:** `user_email`, `conversation_id`, `document_id`.

### 3. API endpoints (cluster side)

#### `POST /api/documents/upload`

Already expected by the frontend. Multipart form data.

**Request:**
- `file`: uploaded file (PDF, TXT, MD, DOCX)
- `conversation_id`: (optional) associated conversation

**Headers:** `X-Munin-Email` (forwarded by gateway)

**Response:**
```json
{
  "document_id": "doc_abc123",
  "filename": "my_draft.pdf",
  "chunks": 24,
  "status": "embedded",
  "upload_time": "2026-04-20T14:30:00Z"
}
```

For large documents (>50 pages), return `"status": "processing"` and
embed asynchronously.

#### `GET /api/documents`

List user's documents. Query param: `conversation_id` (optional).

**Response:**
```json
{
  "documents": [
    {
      "document_id": "doc_abc123",
      "filename": "my_draft.pdf",
      "chunks": 24,
      "status": "embedded",
      "upload_time": "2026-04-20T14:30:00Z"
    }
  ]
}
```

#### `DELETE /api/documents/{document_id}`

Delete document and all its chunks from Qdrant.

### 4. MCP tool: `search_user_docs`

Available to all personas. Searches the user's personal collection.

**Input:**
```json
{
  "query": "methods section experimental setup",
  "top_k": 5,
  "conversation_id": null
}
```

Backend automatically filters by `X-Munin-Email`. If
`conversation_id` is provided, restrict to docs from that chat.

### 5. File storage on cluster

Store originals at:
```
/opt/munin/data/user_docs/{email}/{document_id}/{filename}
```

Allows re-downloading and re-embedding if the model changes.

## Migration Plan for Existing Files

The 4,236 files on the VPS need to be ingested. Suggested approach:

```bash
# 1. From cluster, pull the files
rsync -avz tunnel@<vps-host>:/mnt/uploads/complete/ /opt/munin/data/user_docs_import/

# 2. Run migration script
python migrate_uploads.py --source /opt/munin/data/user_docs_import/ --collection user_docs
```

The migration script should:
1. Walk each `<email>/` directory
2. For each PDF, run the extract → chunk → embed pipeline
3. Store in Qdrant with the email as `user_email`
4. Log progress and skip already-processed files (idempotent)

## Frontend — Already Done

The chat frontend already supports:

- File attachment button (paperclip) in `ChatInput.tsx`
- `uploadDocument()` with progress tracking in `api.ts`
- Document list display via `fetchDocuments()`
- Delete via `deleteDocument()`
- Types defined in `api.ts`: `UploadedDocument`

No frontend changes needed — just the cluster endpoints.

## System prompt addition

Once the tool is available, add to persona system prompts:

```
If the user has uploaded documents, you can search them using the
search_user_docs tool. Use this when the user asks about "my document",
"the file I uploaded", "my draft", etc.
```

## Supported File Types

| Type | Extension | Extraction |
|------|-----------|------------|
| PDF | .pdf | GROBID or pdftotext |
| Plain text | .txt | Direct read |
| Markdown | .md | Direct read |
| Word | .docx | python-docx |

Images, spreadsheets, and presentations are out of scope for v2.

## Access Control

- Documents are **private per user** — filtered by `user_email` in
  every Qdrant query
- Deleting a conversation does NOT delete its documents (user may
  reference them in future chats)
- No disk quota enforced initially — monitor via usage tracking
