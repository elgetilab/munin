#!/usr/bin/env python3
"""
backfill_user_docs.py — re-embed uploaded documents that are on disk but
missing from the `user_docs` Qdrant collection.

WHY. `upload_document` writes the file to disk first and embeds second, and
until 2026-09-01 an extraction that produced nothing returned status "stored"
with no log line, indistinguishable from an image upload. `_extract_docx` read
only `document.paragraphs`, so every .docx whose content lived in a TABLE
extracted to zero characters: valid file, successful-looking upload, invisible
to `search_user_docs`, silent everywhere. Six documents belonging to one user
sat that way for three months. The extractor is fixed; this recovers what was
lost while it was broken, and stays useful for the next extraction gap.

Idempotent. A document that already has points in `user_docs` is skipped, so
re-running costs one scroll and nothing else.

TWO LIMITS, both inherent rather than incidental:

  * `conversation_id` and `project_id` are NOT recoverable. They were only ever
    written into the Qdrant payload, never to disk, so a backfilled document
    comes back user-global. `search_user_docs` falls back from project to
    user-global, so a recovered document filed in a project is still found,
    just not preferred inside that project. Re-uploading is the only way to
    restore project scope.
  * A user's email is recovered by hashing every email in the index and
    matching it against the directory name (the directory is
    sha256(email)[:16], one-way). A user whose every document failed to embed
    therefore has no resolvable email and is reported, not guessed. Pass
    --email to handle that case explicitly.

`backend/scripts/` is not baked into the image, so copy the script in and run
it in the retrieval container, which already has the deps, the BGE model, the
store mounted and the right QDRANT_HOST.

    docker cp backend/scripts/maintenance/backfill_user_docs.py \\
      munin-retrieval:/tmp/backfill_user_docs.py
    docker exec munin-retrieval python /tmp/backfill_user_docs.py --dry-run
    docker exec munin-retrieval python /tmp/backfill_user_docs.py

Environment:
    QDRANT_HOST / QDRANT_PORT   as the retrieval service uses
    USER_DOCS_DIR               inherited from document_store, so it matches
                                the service (/data/user_docs in the container)

CLI:
    --dry-run          report what would be embedded, write nothing
    --ocr              also OCR PDFs that have no text layer (needs ocrmypdf;
                       present in the retrieval image since 2026-09-01). New
                       uploads OCR themselves in the background, so this is for
                       documents that predate that path, or whose OCR was
                       interrupted by a restart.
    --prune-ocr-cache  delete OCR'd copies whose source document no longer
                       exists. Deletion evicts the cache entry since
                       2026-09-02; this clears what earlier deletions left
                       behind, and each orphan is a readable copy of a
                       document its owner already deleted.
    --email EMAIL      resolve directories for this address explicitly
                       (repeatable; needed only for users with no indexed docs)
    --user-hash HASH   restrict the run to one user directory
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, "/app")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "retrieval"))

import document_store as DS  # noqa: E402

# Take the store location from document_store rather than re-deriving it: the
# service reads USER_DOCS_DIR from the environment and it differs between the
# host (/opt/munin/data/user_docs) and the container (/data/user_docs). A second
# default here is a way to scan an empty directory and report "nothing to do".
USER_DOCS_DIR = DS.USER_DOCS_DIR


def _indexed_documents() -> tuple[set[str], dict[str, str]]:
    """(document_ids already embedded, {email_hash: email}) from Qdrant."""
    qdrant = DS.get_qdrant()
    if qdrant is None:
        raise SystemExit("Qdrant unavailable; refusing to run")
    doc_ids: set[str] = set()
    emails: dict[str, str] = {}
    offset = None
    while True:
        points, offset = qdrant.scroll(
            collection_name=DS.USER_DOCS_COLLECTION,
            limit=1000,
            with_payload=["document_id", "user_email"],
            with_vectors=False,
            offset=offset,
        )
        for p in points:
            payload = p.payload or {}
            if payload.get("document_id"):
                doc_ids.add(payload["document_id"])
            email = payload.get("user_email")
            if email:
                emails[DS._email_hash(email)] = email
        if offset is None:
            break
    return doc_ids, emails


async def _candidates(embedded: set[str], only_hash: str | None) -> list[dict]:
    """Every on-disk TEXT document with no points in the index."""
    out: list[dict] = []
    if not os.path.isdir(USER_DOCS_DIR):
        raise SystemExit(f"{USER_DOCS_DIR} does not exist")
    for user_hash in sorted(os.listdir(USER_DOCS_DIR)):
        if only_hash and user_hash != only_hash:
            continue
        user_dir = os.path.join(USER_DOCS_DIR, user_hash)
        if not os.path.isdir(user_dir):
            continue
        for doc_id in sorted(os.listdir(user_dir)):
            doc_dir = os.path.join(user_dir, doc_id)
            if not os.path.isdir(doc_dir) or doc_id in embedded:
                continue
            files = os.listdir(doc_dir)
            if not files:
                continue
            filename = files[0]
            ext = os.path.splitext(filename)[1].lower()
            if ext not in DS.SUPPORTED_TEXT_EXT:
                continue  # images are correctly unembedded
            path = os.path.join(doc_dir, filename)
            with open(path, "rb") as f:
                raw = f.read()
            text = await DS.extract_text(filename, raw)
            out.append({
                "user_hash": user_hash,
                "document_id": doc_id,
                "filename": filename,
                "ext": ext,
                "bytes": len(raw),
                "text": text,
                "chunks": DS.chunk_text(text),
                "path": path,
            })
    return out


def _embed(row: dict, user_email: str) -> int:
    """Upsert one document's chunks. Returns the number of points written."""
    import uuid
    from qdrant_client.http import models as qm

    qdrant, bge = DS.get_qdrant(), DS.get_bge()
    if qdrant is None or bge is None:
        raise SystemExit("Qdrant or BGE unavailable; refusing to write")
    DS.ensure_collection()

    chunks = row["chunks"]
    vectors = bge.encode(chunks, show_progress_bar=False).tolist()
    # Upload time is taken from the FILE, not from now: the document was
    # uploaded when the user uploaded it, and list_documents sorts on this.
    try:
        from datetime import datetime, timezone
        mtime = os.path.getmtime(row["path"])
        upload_time = (datetime.fromtimestamp(mtime, tz=timezone.utc)
                       .isoformat().replace("+00:00", "Z"))
    except OSError:
        upload_time = DS._iso_now()

    points = [
        qm.PointStruct(
            id=str(uuid.uuid4()),
            vector=vec,
            payload={
                "user_email": user_email,
                # Unrecoverable, see the module docstring. Explicit None rather
                # than omitted so the payload shape matches a fresh upload.
                "conversation_id": None,
                "project_id": None,
                "document_id": row["document_id"],
                "filename": row["filename"],
                "chunk_index": i,
                "chunk_text": chunk,
                "total_chunks": len(chunks),
                "upload_time": upload_time,
                # Marks a recovered document, so a future audit can tell these
                # apart from documents that embedded correctly on upload.
                "backfilled": True,
            },
        )
        for i, (chunk, vec) in enumerate(zip(chunks, vectors))
    ]
    qdrant.upsert(collection_name=DS.USER_DOCS_COLLECTION, points=points)
    return len(points)


def _prune_ocr_cache(dry_run: bool) -> None:
    """Delete cached OCR output with no surviving source document.

    The cache is content-addressed, so the live set is derived by hashing every
    PDF still on disk; anything else in the cache belongs to a document that
    was deleted, and is a readable copy of content its owner asked to remove.
    """
    # This script is copied into a container whose document_store may predate
    # it. Say so plainly rather than dying on an AttributeError two hundred
    # lines in, which is what happened the first time this was run.
    if not hasattr(DS, "ocr_cache_path"):
        print("\n  --prune-ocr-cache needs the eviction fix (2026-09-02) in the "
              "running image: document_store has no ocr_cache_path. Deploy "
              "retrieval first, or run this against a mounted repo.")
        return

    cache = DS.OCR_CACHE_DIR
    if not os.path.isdir(cache):
        print(f"\nOCR cache {cache} does not exist, nothing to prune")
        return

    live: set[str] = set()
    for user_hash in os.listdir(USER_DOCS_DIR):
        user_dir = os.path.join(USER_DOCS_DIR, user_hash)
        if not os.path.isdir(user_dir):
            continue
        for doc_id in os.listdir(user_dir):
            doc_dir = os.path.join(user_dir, doc_id)
            if not os.path.isdir(doc_dir):
                continue
            for entry in os.listdir(doc_dir):
                if not entry.lower().endswith(".pdf"):
                    continue
                try:
                    with open(os.path.join(doc_dir, entry), "rb") as f:
                        live.add(os.path.basename(DS.ocr_cache_path(f.read())))
                except OSError:
                    continue

    orphans, freed, n_cached = [], 0, 0
    for name in sorted(os.listdir(cache)):
        if not name.endswith("_ocr.pdf"):
            continue
        n_cached += 1
        if name in live:
            continue
        path = os.path.join(cache, name)
        try:
            freed += os.path.getsize(path)
        except OSError:
            pass
        orphans.append(path)

    # Three different counts, and conflating them is how a reader concludes the
    # cache is 176 files when it holds 3. `live` is every PDF on disk, i.e.
    # every key the cache COULD hold; most were never OCR'd because they had a
    # text layer.
    print(f"\nOCR cache {cache}: {n_cached} cached file(s), {len(orphans)} "
          f"orphaned ({freed / 1e6:.1f} MB). {len(live)} PDFs on disk could "
          f"legitimately have an entry.")
    for path in orphans:
        if dry_run:
            print(f"    [would delete] {os.path.basename(path)}")
            continue
        try:
            os.unlink(path)
            print(f"    [deleted] {os.path.basename(path)}")
        except OSError as e:
            print(f"    [error] {os.path.basename(path)}: {e}")


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--ocr", action="store_true")
    ap.add_argument("--prune-ocr-cache", action="store_true")
    ap.add_argument("--email", action="append", default=[])
    ap.add_argument("--user-hash")
    args = ap.parse_args()

    embedded, emails = _indexed_documents()
    for email in args.email:
        emails[DS._email_hash(email)] = email
    print(f"index holds {len(embedded)} embedded documents "
          f"across {len(emails)} resolvable users")

    rows = await _candidates(embedded, args.user_hash)
    recoverable = [r for r in rows if r["chunks"] and r["user_hash"] in emails]
    unresolved = [r for r in rows if r["chunks"] and r["user_hash"] not in emails]
    empty = [r for r in rows if not r["chunks"]]

    print(f"\nunembedded text documents on disk: {len(rows)}")
    print(f"  recoverable:              {len(recoverable)}")
    print(f"  no resolvable user email: {len(unresolved)}")
    print(f"  still extract to nothing: {len(empty)}")

    # A PDF that extracts to nothing has page images and no text layer, so OCR
    # is the fix rather than a parser change. Split it out: an .md or .docx in
    # this bucket means something else is wrong and OCR will not help it.
    ocr_targets = [r for r in empty
                   if r["ext"] == ".pdf" and r["user_hash"] in emails]
    for r in empty:
        if r in ocr_targets and args.ocr:
            continue
        hint = ("no text layer; re-run with --ocr" if r["ext"] == ".pdf"
                else "extracts to nothing and is not a PDF; OCR will not help")
        print(f"    [empty]  {r['user_hash']}/{r['document_id']} {r['ext']} "
              f"bytes={r['bytes']} — {hint}")
    for r in unresolved:
        print(f"    [no-email] {r['user_hash']}/{r['document_id']} {r['ext']} "
              f"chunks={len(r['chunks'])} — pass --email for this user")

    written = 0
    for r in recoverable:
        email = emails[r["user_hash"]]
        label = (f"{r['user_hash']}/{r['document_id']} {r['ext']} "
                 f"chars={len(r['text'])} chunks={len(r['chunks'])}")
        if args.dry_run:
            print(f"    [would embed] {label}")
            continue
        n = _embed(r, email)
        written += n
        print(f"    [embedded] {label} points={n}")

    ocr_written = 0
    if args.ocr and ocr_targets:
        if not DS.ocr_available():
            print("\n  --ocr requested but ocrmypdf is not on PATH; run this "
                  "inside the retrieval container, which ships it")
        else:
            print(f"\nOCR pass over {len(ocr_targets)} PDF(s) with no text layer:")
            for r in ocr_targets:
                label = f"{r['user_hash']}/{r['document_id']} bytes={r['bytes']}"
                if args.dry_run:
                    print(f"    [would OCR] {label}")
                    continue
                # ocr_and_embed re-reads the file, OCRs, chunks and embeds, and
                # is idempotent, so this is the same path a fresh upload takes.
                res = await DS.ocr_and_embed(
                    document_id=r["document_id"], user_email=emails[r["user_hash"]])
                ocr_written += int(res.get("chunks") or 0)
                print(f"    [{res.get('ocr')}] {label} chunks={res.get('chunks', 0)}")

    if args.prune_ocr_cache:
        _prune_ocr_cache(dry_run=args.dry_run)

    if args.dry_run:
        print(f"\ndry run: {len(recoverable)} documents would be embedded"
              + (f", {len(ocr_targets)} would be OCR'd" if args.ocr else ""))
    else:
        print(f"\nwrote {written} points for {len(recoverable)} documents"
              + (f", plus {ocr_written} points from OCR" if args.ocr else ""))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
