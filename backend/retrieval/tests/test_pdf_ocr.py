"""
Tests for OCR of uploaded PDFs that carry no text layer.

WHY. A PDF printed from a browser ("Skia/PDF") or scanned to "Microsoft: Print
To PDF" is page images and nothing else, so GROBID and pypdf both extract zero
characters. Two such documents sat in the store on 2026-09-01, both reported to
their owner as successful uploads and neither reachable by search. The papers
pipeline had OCR'd scanned papers for months; user uploads had no such path.

The design constraint that shapes these tests: OCR takes 8 to 40 seconds on the
real documents here (measured), so it must NOT run inside the upload request.
`upload_document` returns immediately with `reason="no_text_layer"` and an
internal `ocr_pending` marker; the route turns that into a BackgroundTask
calling `ocr_and_embed`, and the document flips from stored to embedded a
minute later. Everything below pins that split, plus the idempotence that makes
it safe to retry after a restart or to run from the maintenance sweep.

Real OCR is stubbed. The end-to-end proof that ocrmypdf reads these documents
is a live run against the two real files, not a unit test.

    docker exec munin-retrieval python /app/tests/test_pdf_ocr.py
"""

from __future__ import annotations

import asyncio
import importlib
import os
import sys
import tempfile
import traceback

sys.path.insert(0, "/app")
sys.path.insert(0, ".")

DS = importlib.import_module("document_store")

# A minimal PDF with no text layer. Never actually parsed here: every test
# that reaches extraction stubs it, so the bytes only need to be plausible.
_PDF = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj<</Type/Catalog>>endobj\ntrailer\n%%EOF\n"


def _check(name, ok, detail=""):
    print(("[PASS] " if ok else "[FAIL] ") + name + (f"  {detail}" if detail and not ok else ""))
    return ok


class _FakeQdrant:
    def __init__(self, existing=0):
        self.points = []
        self.existing = existing

    def upsert(self, collection_name, points):
        self.points.extend(points)

    def count(self, collection_name, count_filter=None, exact=True):
        class _R:
            count = self.existing
        return _R()


class _FakeBGE:
    def encode(self, chunks, show_progress_bar=False):
        import numpy as np
        return np.zeros((len(chunks), 768), dtype="float32")


def _patched(tmpdir, *, qdrant=None, bge=None, extract="", ocr_text=None,
             ocr_present=True):
    """Swap the module's collaborators. Returns a restore callable."""
    saved = {n: getattr(DS, n) for n in (
        "USER_DOCS_DIR", "get_qdrant", "get_bge", "ensure_collection",
        "extract_text", "ocr_pdf_to_text", "ocr_available")}
    DS.USER_DOCS_DIR = tmpdir
    DS.get_qdrant = lambda: qdrant
    DS.get_bge = lambda: bge
    DS.ensure_collection = lambda: True

    async def _extract(filename, file_bytes):
        return extract
    DS.extract_text = _extract
    if ocr_text is not None:
        DS.ocr_pdf_to_text = lambda raw: ocr_text
    DS.ocr_available = lambda: ocr_present

    def restore():
        for n, v in saved.items():
            setattr(DS, n, v)
    return restore


# --- the upload half: fast response, OCR deferred ---------------------------

def test_textless_pdf_is_marked_for_ocr():
    with tempfile.TemporaryDirectory() as tmp:
        restore = _patched(tmp, qdrant=_FakeQdrant(), bge=_FakeBGE(), extract="")
        try:
            res = asyncio.run(DS.upload_document(
                filename="scan.pdf", file_bytes=_PDF, user_email="u@example.org"))
        finally:
            restore()
    return _check("a text-less PDF is stored with reason no_text_layer",
                  res["status"] == "stored" and res["reason"] == "no_text_layer"
                  and res.get("ocr_pending") is True, f"{res}")


def test_without_ocrmypdf_it_is_a_plain_failure():
    """No OCR binary means no recovery path, and claiming one would be a lie."""
    with tempfile.TemporaryDirectory() as tmp:
        restore = _patched(tmp, qdrant=_FakeQdrant(), bge=_FakeBGE(), extract="",
                           ocr_present=False)
        try:
            res = asyncio.run(DS.upload_document(
                filename="scan.pdf", file_bytes=_PDF, user_email="u@example.org"))
        finally:
            restore()
    return _check("with no ocrmypdf the reason stays no_text_extracted",
                  res["reason"] == "no_text_extracted" and "ocr_pending" not in res,
                  f"{res}")


def test_textless_docx_is_not_sent_to_ocr():
    """OCR is for page images. A .docx that extracts to nothing is a parser
    gap, and routing it to tesseract would waste a minute to learn nothing."""
    with tempfile.TemporaryDirectory() as tmp:
        restore = _patched(tmp, qdrant=_FakeQdrant(), bge=_FakeBGE(), extract="")
        try:
            res = asyncio.run(DS.upload_document(
                filename="notes.docx", file_bytes=b"PK\x03\x04junk",
                user_email="u@example.org"))
        finally:
            restore()
    return _check("a non-PDF is never queued for OCR",
                  res["reason"] == "no_text_extracted" and "ocr_pending" not in res,
                  f"{res}")


def test_readable_pdf_never_touches_ocr():
    with tempfile.TemporaryDirectory() as tmp:
        restore = _patched(tmp, qdrant=_FakeQdrant(), bge=_FakeBGE(),
                           extract="Real extracted text, long enough to chunk. " * 5)
        try:
            res = asyncio.run(DS.upload_document(
                filename="paper.pdf", file_bytes=_PDF, user_email="u@example.org"))
        finally:
            restore()
    return _check("a PDF with a text layer embeds directly",
                  res["status"] == "embedded" and "ocr_pending" not in res, f"{res}")


# --- the background half: ocr_and_embed -------------------------------------

def _stage(tmp, email, doc_id, filename, data=_PDF):
    d = os.path.join(tmp, DS._email_hash(email), doc_id)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, filename), "wb") as f:
        f.write(data)


def test_ocr_and_embed_writes_chunks():
    q = _FakeQdrant()
    with tempfile.TemporaryDirectory() as tmp:
        _stage(tmp, "u@example.org", "doc_0000000000a1", "scan.pdf")
        restore = _patched(tmp, qdrant=q, bge=_FakeBGE(),
                           ocr_text="Recovered OCR text. " * 40)
        try:
            res = asyncio.run(DS.ocr_and_embed("doc_0000000000a1", "u@example.org"))
        finally:
            restore()
    payload = q.points[0].payload if q.points else {}
    return _check("OCR text is embedded and flagged as OCR-derived",
                  res["ocr"] == "embedded" and res["chunks"] >= 1
                  and payload.get("ocr") is True
                  and payload.get("document_id") == "doc_0000000000a1", f"{res} {payload!r}")


def test_ocr_and_embed_is_idempotent():
    """Safe to retry after a restart, and safe to call from both the upload
    path and the sweep, because the two can race on the same document."""
    q = _FakeQdrant(existing=7)  # already has points
    with tempfile.TemporaryDirectory() as tmp:
        _stage(tmp, "u@example.org", "doc_0000000000a1", "scan.pdf")
        restore = _patched(tmp, qdrant=q, bge=_FakeBGE(), ocr_text="text " * 200)
        try:
            res = asyncio.run(DS.ocr_and_embed("doc_0000000000a1", "u@example.org"))
        finally:
            restore()
    return _check("an already-embedded document is skipped, not duplicated",
                  res["ocr"] == "skipped" and not q.points, f"{res}")


def test_ocr_and_embed_skips_non_pdf():
    q = _FakeQdrant()
    with tempfile.TemporaryDirectory() as tmp:
        _stage(tmp, "u@example.org", "doc_0000000000a1", "notes.docx", b"PK\x03\x04")
        restore = _patched(tmp, qdrant=q, bge=_FakeBGE(), ocr_text="x " * 200)
        try:
            res = asyncio.run(DS.ocr_and_embed("doc_0000000000a1", "u@example.org"))
        finally:
            restore()
    return _check("a non-PDF is skipped", res["ocr"] == "skipped" and not q.points,
                  f"{res}")


def test_ocr_that_finds_nothing_is_reported_not_retried():
    """A blank scan or an unsupported language: OCR ran and there is genuinely
    nothing. It must be visible in the logs and left alone, not looped on."""
    q = _FakeQdrant()
    with tempfile.TemporaryDirectory() as tmp:
        _stage(tmp, "u@example.org", "doc_0000000000a1", "blank.pdf")
        restore = _patched(tmp, qdrant=q, bge=_FakeBGE(), ocr_text="")
        try:
            res = asyncio.run(DS.ocr_and_embed("doc_0000000000a1", "u@example.org"))
        finally:
            restore()
    return _check("an empty OCR result is reported as empty",
                  res["ocr"] == "empty" and res["chunks"] == 0 and not q.points,
                  f"{res}")


def test_missing_file_does_not_raise():
    q = _FakeQdrant()
    with tempfile.TemporaryDirectory() as tmp:
        restore = _patched(tmp, qdrant=q, bge=_FakeBGE(), ocr_text="x " * 200)
        try:
            res = asyncio.run(DS.ocr_and_embed("doc_missing", "u@example.org"))
        finally:
            restore()
    return _check("a missing document returns rather than raising",
                  res["ocr"] == "skipped", f"{res}")


# --- the cache --------------------------------------------------------------

def test_ocr_result_is_cached_by_content():
    """A retry, a re-upload of the same file, or a sweep re-run must cost a
    disk read rather than another 40 seconds of tesseract."""
    import subprocess
    calls = []
    saved_run, saved_cache = subprocess.run, DS.OCR_CACHE_DIR
    saved_avail = DS.ocr_available

    def fake_run(cmd, **kw):
        calls.append(cmd)
        # ocrmypdf writes its output file; emulate that so the cache is warm.
        with open(cmd[-1], "wb") as f:
            f.write(_PDF)
        class _P:
            returncode = 0
            stderr = ""
        return _P()

    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run = fake_run
        DS.OCR_CACHE_DIR = tmp
        DS.ocr_available = lambda: True
        try:
            DS.ocr_pdf_to_text(_PDF)
            DS.ocr_pdf_to_text(_PDF)
            cached = [f for f in os.listdir(tmp) if f.endswith("_ocr.pdf")]
        finally:
            subprocess.run = saved_run
            DS.OCR_CACHE_DIR = saved_cache
            DS.ocr_available = saved_avail
    return _check("ocrmypdf runs once for the same bytes",
                  len(calls) == 1 and len(cached) == 1,
                  f"{len(calls)} runs, {len(cached)} cached files")


def test_ocr_without_the_binary_returns_empty():
    saved = DS.ocr_available
    DS.ocr_available = lambda: False
    try:
        out = DS.ocr_pdf_to_text(_PDF)
    finally:
        DS.ocr_available = saved
    return _check("no ocrmypdf on PATH returns empty rather than raising", out == "")


# --- deletion must take the OCR'd copy with it ------------------------------

def test_delete_evicts_the_ocr_cache():
    """A cached OCR output is the document's full content as searchable text.
    Leaving it after a delete means a user who removed a document still has a
    readable copy of it on the server. Found by smoke-testing the OCR path on
    2026-09-02: the points and the file went, the cached PDF stayed.
    """
    q = _FakeQdrant()
    with tempfile.TemporaryDirectory() as tmp:
        cache = os.path.join(tmp, "cache")
        os.makedirs(cache)
        _stage(tmp, "u@example.org", "doc_0000000000a1", "scan.pdf")
        saved_cache = DS.OCR_CACHE_DIR
        DS.OCR_CACHE_DIR = cache
        restore = _patched(tmp, qdrant=q, bge=_FakeBGE())
        try:
            cached = DS.ocr_cache_path(_PDF)
            with open(cached, "wb") as f:
                f.write(b"pretend OCR output")
            existed = os.path.isfile(cached)
            asyncio.run(DS.delete_document("doc_0000000000a1", "u@example.org"))
            gone = not os.path.isfile(cached)
        finally:
            restore()
            DS.OCR_CACHE_DIR = saved_cache
    return _check("deleting a document evicts its OCR'd copy",
                  existed and gone, f"existed={existed} gone={gone}")


def test_delete_without_a_cache_entry_is_fine():
    """Most documents never get OCR'd, so the common path must not care."""
    q = _FakeQdrant()
    with tempfile.TemporaryDirectory() as tmp:
        cache = os.path.join(tmp, "cache")
        os.makedirs(cache)
        _stage(tmp, "u@example.org", "doc_0000000000a1", "notes.txt", b"plain text")
        saved_cache = DS.OCR_CACHE_DIR
        DS.OCR_CACHE_DIR = cache
        restore = _patched(tmp, qdrant=q, bge=_FakeBGE())
        try:
            ok = asyncio.run(DS.delete_document("doc_0000000000a1", "u@example.org"))
        finally:
            restore()
            DS.OCR_CACHE_DIR = saved_cache
    return _check("deleting a document with no cached OCR still succeeds", ok)


def test_cache_key_is_derived_in_one_place():
    """`ocr_pdf_to_text` writes the entry and `delete_document` removes it, so
    they must agree on the key. Two copies of sha256(...)[:16] is how an
    eviction silently stops matching what it is meant to remove."""
    import subprocess
    saved_run, saved_cache, saved_avail = (
        subprocess.run, DS.OCR_CACHE_DIR, DS.ocr_available)

    def fake_run(cmd, **kw):
        with open(cmd[-1], "wb") as f:
            f.write(_PDF)
        class _P:
            returncode = 0
            stderr = ""
        return _P()

    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run = fake_run
        DS.OCR_CACHE_DIR = tmp
        DS.ocr_available = lambda: True
        try:
            DS.ocr_pdf_to_text(_PDF)
            written = [f for f in os.listdir(tmp) if f.endswith("_ocr.pdf")]
            predicted = os.path.basename(DS.ocr_cache_path(_PDF))
        finally:
            subprocess.run = saved_run
            DS.OCR_CACHE_DIR = saved_cache
            DS.ocr_available = saved_avail
    return _check("the writer and the evictor derive the same cache key",
                  written == [predicted], f"wrote {written}, evictor wants {predicted}")


TESTS = [
    test_textless_pdf_is_marked_for_ocr,
    test_without_ocrmypdf_it_is_a_plain_failure,
    test_textless_docx_is_not_sent_to_ocr,
    test_readable_pdf_never_touches_ocr,
    test_ocr_and_embed_writes_chunks,
    test_ocr_and_embed_is_idempotent,
    test_ocr_and_embed_skips_non_pdf,
    test_ocr_that_finds_nothing_is_reported_not_retried,
    test_missing_file_does_not_raise,
    test_ocr_result_is_cached_by_content,
    test_ocr_without_the_binary_returns_empty,
    test_delete_evicts_the_ocr_cache,
    test_delete_without_a_cache_entry_is_fine,
    test_cache_key_is_derived_in_one_place,
]


def main() -> int:
    failed = 0
    for fn in TESTS:
        try:
            if not fn():
                failed += 1
        except Exception:
            failed += 1
            print(f"[FAIL] {fn.__name__} raised:")
            traceback.print_exc()
    print(f"\n{len(TESTS) - failed}/{len(TESTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
