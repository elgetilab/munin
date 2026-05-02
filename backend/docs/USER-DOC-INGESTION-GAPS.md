# User-Document Ingestion — Known Gaps

Scope: everything that enters the system via `POST /api/documents/upload`
(chat drag-and-drop, inline attachments, Documents panel). Files are
stored on disk, text-extracted, chunked, BGE-base embedded, and upserted
into the Qdrant `user_docs` collection. Search is exposed to the model
via the `search_user_docs` MCP tool.

Source of truth:
- `retrieval/document_store.py` — upload, extract, chunk, embed, list,
  delete, search.
- `retrieval/main.py:1399-1482` — `/api/documents/upload`, `GET
  /api/documents`, `DELETE /api/documents/{id}`.
- `retrieval/mcp/tools/documents.py` + `retrieval/mcp/schemas.py:321`
  — `search_user_docs`, `view_attachment`.

Issues are listed in rough priority order. Nothing here is scheduled yet
— this file is a backlog, not a plan.

---

## 1. No real tokenizer; chunk sizes drift

`chunk_text` uses a fixed `CHARS_PER_TOKEN = 4` approximation
(`CHUNK_CHAR_TARGET = 2048`). BGE-base has a hard 512-token input
limit; dense scientific prose routinely exceeds 5 chars/token, so
chunks can overflow and get silently truncated by the encoder,
discarding the tail of each chunk from the index.

**Fix**: tokenise with the BGE tokenizer (already loaded alongside the
model) and chunk on real token counts with explicit padding/truncation
checks.

**Effort**: half a day. **Risk**: low.

---

## 2. GROBID TEI is stripped to linear text

`_extract_pdf` calls GROBID's `processFulltextDocument` (which returns
structured TEI XML with `<title>`, `<author>`, `<abstract>`,
`<div>`/`<head>` sections, bibliography) and then throws all of that
away with `re.sub(r"<[^>]+>", " ", r.text)`. Downstream effects:

- Documents have no title/author/abstract payload — the UI only knows
  the filename.
- Chunks carry no `section` field, so search can't weight by section
  (abstract/intro chunks aren't privileged over references).
- No cross-document dedup by DOI/title is possible.

**Fix**: parse TEI (lxml / beautifulsoup). Extract `title`, `authors`,
`abstract`, `doi`, per-chunk `section_heading`, attach to Qdrant
payload, persist doc-level metadata in a small `user_documents` SQLite
table so listing doesn't require scrolling the vector store.

**Effort**: 1–1.5 days. **Risk**: TEI schema corner cases.

---

## 3. Silent failure for scanned PDFs (no OCR)

If GROBID returns too-short text and pypdf extracts empty string
(image-only PDFs), the upload succeeds with `chunks: 0, status:
"stored"` and the doc is silently unsearchable. The user gets no
warning.

**Fix**: detect the "short text" case, run OCR (tesseract via
`pytesseract`, or an OCR container) on page rasters, proceed with
chunk+embed. Until OCR is wired in, at minimum return an explicit
`status: "unextractable"` + a user-visible warning so people know why
the doc isn't answering their questions.

**Effort**: half a day for the warning path; +1 day to land tesseract.
**Risk**: adds a system dependency and image-raster memory cost.

---

## 4. Upload is fully blocking

`api_upload_document` calls `await file.read()` (buffers the whole 50
MB in memory), then runs GROBID extraction (~seconds for large PDFs)
and `bge.encode(chunks)` (synchronous, can block the event loop for
5-30s on a cold model) before returning. One slow upload stalls every
other request sharing that worker.

**Fix**: stream the file to disk, respond 202 immediately with a
`document_id` and `status: "processing"`, run extract+embed in a
background task, emit a status change the Documents UI can poll (or
push via SSE). Alternatively run embedding in a threadpool so the
event loop isn't blocked.

**Effort**: ~1 day. **Risk**: introduces a status lifecycle the
frontend has to render.

---

## 5. No deduplication

Same PDF uploaded twice produces two `document_id`s, two file trees,
and duplicate chunks that both score near the top of retrieval, wasting
the tool's `top_k` budget.

**Fix**: hash the file bytes (sha256) on upload; store hashes in a
`user_documents` table; on collision, return the existing
`document_id` and skip re-embedding. Expose the hash in the list
response so the frontend can show "already uploaded" UX.

**Effort**: a few hours once (2) lands the SQLite sidecar table.

---

## 6. No hybrid search

`search_user_docs` is dense-only. Short exact-keyword queries
("Dlg1 knockout", specific gene names, filenames, figure captions) hit
BGE's semantic blur and miss. No BM25 / FTS, no filename match, no
title match.

**Fix**: add a sparse leg — SQLite FTS5 over `chunk_text` + `filename`
+ `title`, then RRF-merge with dense hits. Cheap, no new infra.

**Effort**: ~1 day. **Risk**: low. Biggest retrieval-quality win on
the list.

---

## 7. Missing file types

Supported: PDF, TXT, MD, DOCX + images. Conspicuous gaps:

- `.pptx` (researchers share talks constantly) — parse with
  `python-pptx`, one slide per chunk.
- `.xlsx` / `.csv` — ingest as markdown tables, one sheet per chunk;
  cap cell count to avoid exploding chunk sizes.
- `.html` / `.htm` — common "save page as…" input; strip with
  `beautifulsoup` + `html2text`.
- `.epub` — nice-to-have for textbook ingestion.

**Effort**: 2-4 hours per format. **Risk**: low.

---

## 8. No `project_id` payload index

`document_store.ensure_collection` adds indexes on `user_email`,
`conversation_id`, `document_id` but not `project_id`. Project-scoped
search (§21) runs an unindexed filter. Fine at current scale; will
bite when any user has thousands of chunks across multiple projects.

**Fix**: add `project_id` to the index list. One-liner; safe to
backfill because the index is created with `try/except ignore` if it
already exists.

**Effort**: 10 minutes.

---

## 9. `list_documents` scrolls the whole collection

Every `GET /api/documents` call scrolls the Qdrant collection (256 at
a time) and deduplicates by `document_id`. There is no pagination, no
cache, no doc-level table. Doc-level fields are also redundantly
stored on every chunk payload.

**Fix**: once (2) introduces a `user_documents` SQLite table, drive
listing from SQL. Keep Qdrant authoritative for chunks only. Add
server-side pagination + filter params (`?status=`, `?project_id=`,
`?search=`).

**Effort**: half a day after (2).

---

## 10. No document-level summary / auto-title

Documents show up in the UI as bare filenames. A short auto-title
("Membrane dynamics in lipid bilayers — Watts 1989") and a
1-paragraph abstract would massively improve scanability. GROBID
already gives us the title; an async vLLM call could generate a
short summary when the model is online (same pattern as auto-title
for chats).

**Fix**: on successful extraction, store `title` (GROBID) +
`summary` (async vLLM job, nullable). Surface in `GET /api/documents`.

**Effort**: half a day. Depends on (2) for the GROBID-title handoff.

---

## Suggested bundles

- **Quick win, no infra**: (8) index, (5) dedup hash (after (2) lands
  the table), (7) add one-two file types.
- **Biggest retrieval quality**: (6) hybrid search.
- **Biggest UX lift**: (4) async upload + (10) auto-summary.
- **Structural prerequisite for most of the rest**: (2) TEI parsing
  + `user_documents` SQLite sidecar — unblocks (5), (9), (10).

Pick the bundle when we come back to this.
