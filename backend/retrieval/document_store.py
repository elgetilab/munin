"""
User document store.

Responsibilities:
    * Accept file uploads (PDF, TXT, MD, DOCX, images), store the original
      file under a per-user directory, and — for text formats — chunk and
      embed the contents into the Qdrant `user_docs` collection.
    * List and delete the authenticated user's documents.
    * Run semantic search over a user's own documents, scoped by email and
      optionally by conversation.

Embedding model: BGE-base (768 dims, cosine), reused from database.py.
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from database import get_qdrant, get_bge, is_bge_loaded

logger = logging.getLogger(__name__)

USER_DOCS_DIR = os.getenv("USER_DOCS_DIR", "/data/user_docs")
USER_DOCS_COLLECTION = "user_docs"
BGE_DIM = 768
GROBID_URL = os.getenv("GROBID_URL", "http://grobid:8070")

SUPPORTED_TEXT_EXT = {".pdf", ".txt", ".md", ".docx"}
SUPPORTED_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}

# Chunking parameters (approximate tokens, ~4 chars/token)
CHUNK_TOKENS = 512
CHUNK_OVERLAP = 50
CHARS_PER_TOKEN = 4
CHUNK_CHAR_TARGET = CHUNK_TOKENS * CHARS_PER_TOKEN
CHUNK_OVERLAP_CHARS = CHUNK_OVERLAP * CHARS_PER_TOKEN


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _email_hash(email: str) -> str:
    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()[:16]


def _user_dir(email: str) -> str:
    return os.path.join(USER_DOCS_DIR, _email_hash(email))


def _doc_dir(email: str, doc_id: str) -> str:
    return os.path.join(_user_dir(email), doc_id)


# ==============================================================================
# Collection setup
# ==============================================================================

def ensure_collection() -> bool:
    """
    Create the `user_docs` Qdrant collection if it doesn't already exist, and
    add payload indexes for user_email / conversation_id so filtered searches
    are fast. Safe to call repeatedly.
    """
    qdrant = get_qdrant()
    if qdrant is None:
        logger.warning("Qdrant not available, skipping user_docs collection setup")
        return False

    try:
        from qdrant_client.http import models as qm

        existing = {c.name for c in qdrant.get_collections().collections}
        if USER_DOCS_COLLECTION not in existing:
            qdrant.create_collection(
                collection_name=USER_DOCS_COLLECTION,
                vectors_config=qm.VectorParams(
                    size=BGE_DIM, distance=qm.Distance.COSINE
                ),
            )
            logger.info("Created Qdrant collection: %s", USER_DOCS_COLLECTION)

        for field_name, schema in (
            ("user_email", qm.PayloadSchemaType.KEYWORD),
            ("conversation_id", qm.PayloadSchemaType.KEYWORD),
            ("document_id", qm.PayloadSchemaType.KEYWORD),
        ):
            try:
                qdrant.create_payload_index(
                    collection_name=USER_DOCS_COLLECTION,
                    field_name=field_name,
                    field_schema=schema,
                )
            except Exception:
                # Index likely already exists; ignore.
                pass

        return True
    except Exception as e:
        logger.exception("Failed to set up user_docs collection")
        return False


# ==============================================================================
# Text extraction
# ==============================================================================

async def _extract_pdf(file_bytes: bytes) -> str:
    """Try GROBID first for scientific PDFs; fall back to pypdf."""
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(
                f"{GROBID_URL}/api/processFulltextDocument",
                files={"input": ("doc.pdf", file_bytes, "application/pdf")},
                data={"consolidateHeader": "0"},
            )
            if r.status_code == 200 and r.text:
                # GROBID returns TEI XML. Strip tags for now — a fuller
                # implementation would parse sections, but for chunking we
                # just need linear text.
                text = re.sub(r"<[^>]+>", " ", r.text)
                text = re.sub(r"\s+", " ", text).strip()
                if len(text) > 100:
                    return text
    except Exception as e:
        logger.info("GROBID extraction failed, falling back to pypdf: %s", e)

    try:
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(file_bytes))
        parts: list[str] = []
        for page in reader.pages:
            try:
                parts.append(page.extract_text() or "")
            except Exception:
                continue
        return "\n\n".join(parts).strip()
    except Exception as e:
        logger.exception("pypdf extraction failed")
        return ""


# ------------------------------------------------------------------ OCR ----
# A PDF printed from a browser or scanned to "Print To PDF" has page images and
# no text layer, so GROBID and pypdf both come back empty and the upload lands
# unsearchable. Two such documents were sitting in the store on 2026-09-01, both
# reported to their owner as successful uploads. The papers pipeline already
# OCRs scanned papers with ocrmypdf; user uploads had no such path.
#
# OCR is NOT part of `extract_text`. It costs tens of seconds to minutes, and
# the upload request must not block on it, so `upload_document` returns
# immediately with reason "no_text_layer" and the endpoint schedules
# `ocr_and_embed` as a background task. The document becomes searchable a
# minute or so later and `list_documents` shows it flip from stored to embedded.
OCR_CACHE_DIR = os.getenv(
    "USER_DOCS_OCR_CACHE_DIR",
    os.path.join(os.path.dirname(USER_DOCS_DIR.rstrip("/")) or "/data", "ocr_cache"),
)
OCR_TIMEOUT_SEC = int(os.getenv("USER_DOCS_OCR_TIMEOUT_SEC", "300"))


def ocr_available() -> bool:
    """Is ocrmypdf on PATH? False in a stripped image or a bare test env."""
    import shutil
    return shutil.which("ocrmypdf") is not None


def ocr_pdf_to_text(file_bytes: bytes) -> str:
    """OCR a PDF and return its text. BLOCKING: call from a thread.

    The OCR'd PDF is cached by content hash, so a retry, a re-upload of the
    same file, or a sweep re-run costs a disk read rather than another minute
    of tesseract. `--skip-text` leaves pages that already carry text alone,
    which makes this safe to run on a mixed document and is the same flag the
    papers pipeline uses.
    """
    import hashlib
    import subprocess
    import tempfile

    if not ocr_available():
        logger.warning("ocrmypdf not installed; cannot OCR a PDF with no text layer")
        return ""

    digest = hashlib.sha256(file_bytes).hexdigest()[:16]
    try:
        os.makedirs(OCR_CACHE_DIR, exist_ok=True)
    except OSError as e:
        logger.warning("OCR cache dir %s unusable: %s", OCR_CACHE_DIR, e)
        return ""
    cached = os.path.join(OCR_CACHE_DIR, f"{digest}_ocr.pdf")

    if not os.path.isfile(cached):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as src:
            src.write(file_bytes)
            src_path = src.name
        try:
            proc = subprocess.run(
                ["ocrmypdf", "--skip-text", "--optimize", "1", "--quiet",
                 src_path, cached],
                capture_output=True, text=True, timeout=OCR_TIMEOUT_SEC,
            )
            # 6 means "page already has text", which --skip-text makes unlikely
            # but which is a success for our purposes: the input is usable.
            if proc.returncode not in (0, 6):
                logger.warning("ocrmypdf failed rc=%s: %s",
                               proc.returncode, (proc.stderr or "")[:300])
                return ""
            if proc.returncode == 6 and not os.path.isfile(cached):
                import shutil as _sh
                _sh.copyfile(src_path, cached)
        except subprocess.TimeoutExpired:
            logger.warning("ocrmypdf timed out after %ss", OCR_TIMEOUT_SEC)
            return ""
        except Exception:
            logger.exception("ocrmypdf raised")
            return ""
        finally:
            try:
                os.unlink(src_path)
            except OSError:
                pass

    try:
        import pypdf
        with open(cached, "rb") as f:
            reader = pypdf.PdfReader(f)
            parts = []
            for page in reader.pages:
                try:
                    parts.append(page.extract_text() or "")
                except Exception:
                    continue
        return "\n\n".join(parts).strip()
    except Exception:
        logger.exception("reading OCR output failed")
        return ""


def _docx_table_lines(table) -> list[str]:
    """One line per table row, cells joined with ' | '.

    `row.cells` yields the SAME cell object once per grid column it spans, so a
    horizontally merged cell would otherwise repeat its text across the row.
    Deduped on the underlying XML element rather than on the string, because two
    genuinely distinct cells can legitimately hold the same value.
    """
    lines: list[str] = []
    for row in table.rows:
        seen: set = set()
        cells: list[str] = []
        for cell in row.cells:
            key = id(cell._tc)
            if key in seen:
                continue
            seen.add(key)
            text = (cell.text or "").strip()
            if text:
                cells.append(text)
        if cells:
            lines.append(" | ".join(cells))
    return lines


def _extract_docx(file_bytes: bytes) -> str:
    """Linear text from a .docx, covering paragraphs AND tables.

    Reading only `document.paragraphs` (the shape until 2026-09-01) silently
    lost every document whose content lives in a table. Measured on one user's
    uploads: 6 of 7 .docx files extracted to zero characters, each one a valid
    OOXML file with no paragraph text and a single table holding 187 to 3636
    characters. They were stored on disk, embedded nowhere, and reported as a
    successful upload, so `search_user_docs` could never see them and nothing
    anywhere said why. A form, a questionnaire or a lab record exported to Word
    is table-shaped far more often than not.

    Walks the body in document ORDER rather than concatenating
    `document.paragraphs` then `document.tables`, so a table stays next to the
    prose that introduces it and chunking does not interleave unrelated
    material.

    Still not covered, and deliberately: headers/footers, text boxes
    (`w:txbxContent`) and tables nested inside a table cell. None appeared in
    the observed corpus. `_supported_text_is_empty` below now warns when an
    extraction comes back empty, so the next shape announces itself instead of
    going quiet.
    """
    try:
        import docx  # python-docx
        from docx.oxml.table import CT_Tbl
        from docx.oxml.text.paragraph import CT_P
        from docx.table import Table
        from docx.text.paragraph import Paragraph

        document = docx.Document(io.BytesIO(file_bytes))
        parts: list[str] = []
        for child in document.element.body.iterchildren():
            if isinstance(child, CT_P):
                text = Paragraph(child, document).text.strip()
                if text:
                    parts.append(text)
            elif isinstance(child, CT_Tbl):
                parts.extend(_docx_table_lines(Table(child, document)))
        return "\n\n".join(parts).strip()
    except Exception:
        logger.exception("docx extraction failed")
        return ""


def _extract_text(file_bytes: bytes) -> str:
    for encoding in ("utf-8", "latin-1"):
        try:
            return file_bytes.decode(encoding).strip()
        except UnicodeDecodeError:
            continue
    return ""


async def extract_text(filename: str, file_bytes: bytes) -> str:
    ext = os.path.splitext(filename)[1].lower()
    if ext == ".pdf":
        return await _extract_pdf(file_bytes)
    if ext == ".docx":
        return _extract_docx(file_bytes)
    if ext in (".txt", ".md"):
        return _extract_text(file_bytes)
    return ""


# ==============================================================================
# Chunking
# ==============================================================================

def chunk_text(text: str) -> list[str]:
    """
    Split text into ~512-token chunks with ~50-token overlap, respecting
    paragraph boundaries. Oversized paragraphs are split by sentence.
    """
    text = re.sub(r"\r\n?", "\n", text or "").strip()
    if not text:
        return []

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paragraphs:
        return []

    # Split any paragraph larger than the target into sentences so we never
    # emit a chunk bigger than ~CHUNK_CHAR_TARGET.
    units: list[str] = []
    for para in paragraphs:
        if len(para) <= CHUNK_CHAR_TARGET:
            units.append(para)
            continue
        sentences = re.split(r"(?<=[.!?])\s+", para)
        buf = ""
        for sent in sentences:
            if len(buf) + len(sent) + 1 <= CHUNK_CHAR_TARGET:
                buf = f"{buf} {sent}".strip()
            else:
                if buf:
                    units.append(buf)
                # Sentence may itself be oversized; hard-split it.
                while len(sent) > CHUNK_CHAR_TARGET:
                    units.append(sent[:CHUNK_CHAR_TARGET])
                    sent = sent[CHUNK_CHAR_TARGET:]
                buf = sent
        if buf:
            units.append(buf)

    chunks: list[str] = []
    current = ""
    for unit in units:
        if not current:
            current = unit
            continue
        if len(current) + len(unit) + 2 <= CHUNK_CHAR_TARGET:
            current = f"{current}\n\n{unit}"
        else:
            chunks.append(current)
            overlap_tail = current[-CHUNK_OVERLAP_CHARS:] if CHUNK_OVERLAP_CHARS else ""
            current = f"{overlap_tail}\n\n{unit}" if overlap_tail else unit
    if current:
        chunks.append(current)
    return chunks


# ==============================================================================
# Upload / list / delete
# ==============================================================================

def unfile_project_documents(user_email: str, project_id: str) -> int:
    """
    Clear the ``project_id`` payload key on every user_docs point that
    is currently filed into ``project_id`` for this user. Used when a
    project is deleted - the docs become user-global (equivalent to
    never having been filed). Returns the Qdrant operation status.
    """
    qdrant = get_qdrant()
    if qdrant is None:
        return 0
    try:
        from qdrant_client.http import models as qm
        qdrant.delete_payload(
            collection_name=USER_DOCS_COLLECTION,
            keys=["project_id"],
            points_selector=qm.FilterSelector(
                filter=qm.Filter(
                    must=[
                        qm.FieldCondition(
                            key="user_email",
                            match=qm.MatchValue(value=user_email),
                        ),
                        qm.FieldCondition(
                            key="project_id",
                            match=qm.MatchValue(value=project_id),
                        ),
                    ]
                )
            ),
        )
        return 1
    except Exception as e:
        logger.warning("unfile_project_documents failed: %s", e)
        return 0


def count_project_documents(user_email: str, project_id: str) -> int:
    """Return the number of distinct user_docs document_ids filed in
    ``project_id``. Used by the GET /api/projects/{id} counts payload."""
    qdrant = get_qdrant()
    if qdrant is None:
        return 0
    try:
        from qdrant_client.http import models as qm
        seen: set[str] = set()
        offset = None
        while True:
            points, offset = qdrant.scroll(
                collection_name=USER_DOCS_COLLECTION,
                scroll_filter=qm.Filter(
                    must=[
                        qm.FieldCondition(
                            key="user_email",
                            match=qm.MatchValue(value=user_email),
                        ),
                        qm.FieldCondition(
                            key="project_id",
                            match=qm.MatchValue(value=project_id),
                        ),
                    ]
                ),
                limit=256,
                offset=offset,
                with_payload=["document_id"],
                with_vectors=False,
            )
            for point in points:
                doc_id = (point.payload or {}).get("document_id")
                if doc_id:
                    seen.add(doc_id)
            if offset is None:
                break
        return len(seen)
    except Exception as e:
        logger.warning("count_project_documents failed: %s", e)
        return 0


def get_document_file_path(user_email: str, document_id: str) -> Optional[str]:
    """
    Resolve ``document_id`` to the on-disk path of the single file stored
    for that document. Returns None if the directory is missing or empty.
    Convention: upload_document writes exactly one file per doc_dir, so
    we return the first regular file we find.
    """
    if not document_id or not user_email:
        return None
    target = _doc_dir(user_email, document_id)
    if not os.path.isdir(target):
        return None
    try:
        entries = os.listdir(target)
    except OSError:
        return None
    for name in sorted(entries):
        full = os.path.join(target, name)
        if os.path.isfile(full):
            return full
    return None


async def get_document_text(
    user_email: str, document_id: str
) -> Optional[str]:
    """Return the extracted plain text of a stored text document, or None
    if the document is missing on disk.

    Composes ``get_document_file_path`` + ``extract_text`` (no new
    extraction logic). Used to inline an attached ``.pdf/.txt/.md/.docx``
    into a chat turn so the model can act on its contents (translate,
    summarise, ...). Returns ``""`` when the file exists but yields no
    extractable text (e.g. a scanned PDF, or an image mistyped as a
    document) so the caller can distinguish "not found" from "empty".
    """
    path = get_document_file_path(user_email, document_id)
    if not path:
        return None
    try:
        with open(path, "rb") as f:
            file_bytes = f.read()
    except OSError:
        return None
    return await extract_text(os.path.basename(path), file_bytes)


async def upload_document(
    filename: str,
    file_bytes: bytes,
    user_email: str,
    conversation_id: Optional[str] = None,
    project_id: Optional[str] = None,
) -> dict:
    ext = os.path.splitext(filename)[1].lower()
    if ext not in SUPPORTED_TEXT_EXT and ext not in SUPPORTED_IMAGE_EXT:
        raise ValueError(f"Unsupported file type: {ext}")

    document_id = f"doc_{uuid.uuid4().hex[:12]}"
    target_dir = _doc_dir(user_email, document_id)
    os.makedirs(target_dir, exist_ok=True)
    safe_filename = os.path.basename(filename)
    target_path = os.path.join(target_dir, safe_filename)
    with open(target_path, "wb") as f:
        f.write(file_bytes)

    upload_time = _iso_now()

    if ext in SUPPORTED_IMAGE_EXT:
        return {
            "document_id": document_id,
            "filename": safe_filename,
            "chunks": 0,
            "status": "stored",
            "upload_time": upload_time,
        }

    text = await extract_text(safe_filename, file_bytes)
    chunks = chunk_text(text)
    if not chunks:
        # A supported TEXT type that yields nothing is a defect, not a
        # property of the file, and it used to be indistinguishable from an
        # image upload: both returned status "stored" with no log line. Six
        # table-only .docx files sat unsearchable for three months that way,
        # each reported to its user as a successful upload. Name the reason
        # in the response AND in the log, so the next unsupported shape
        # (a scanned PDF, a text box, a header-only document) announces
        # itself on the first upload rather than on a complaint.
        empty = not (text or "").strip()
        # A PDF that extracts to nothing has page images and no text layer, and
        # OCR is the fix rather than a parser change. Say so specifically: the
        # caller schedules ocr_and_embed, and the user is told the document is
        # being read rather than that it failed.
        if empty and ext == ".pdf" and ocr_available():
            reason = "no_text_layer"
        elif empty:
            reason = "no_text_extracted"
        else:
            reason = "no_chunks"
        logger.warning(
            "upload produced no searchable text: file=%r ext=%s bytes=%d "
            "extracted_chars=%d reason=%s",
            safe_filename, ext, len(file_bytes), len((text or "")), reason,
        )
        out = {
            "document_id": document_id,
            "filename": safe_filename,
            "chunks": 0,
            "status": "stored",
            "reason": reason,
            "upload_time": upload_time,
        }
        if reason == "no_text_layer":
            # Consumed by the route, which turns it into a BackgroundTask.
            # Kept as a flag rather than started here so document_store stays
            # free of request-lifecycle concerns and remains callable from the
            # maintenance sweep, which schedules nothing.
            out["ocr_pending"] = True
        return out

    qdrant = get_qdrant()
    bge = get_bge()
    if qdrant is None or bge is None:
        # File is saved; embedding will be unavailable until infra is ready.
        # Distinct from the branch above: the document is fine, the index is
        # not, and the fix is operational rather than a parser change.
        logger.warning(
            "upload stored without embedding, index unavailable: file=%r "
            "qdrant=%s bge=%s",
            safe_filename, qdrant is not None, bge is not None,
        )
        return {
            "document_id": document_id,
            "filename": safe_filename,
            "chunks": 0,
            "status": "stored",
            "reason": "index_unavailable",
            "upload_time": upload_time,
        }

    _embed_chunks(
        chunks=chunks, user_email=user_email, document_id=document_id,
        filename=safe_filename, upload_time=upload_time,
        conversation_id=conversation_id, project_id=project_id,
    )

    return {
        "document_id": document_id,
        "filename": safe_filename,
        "chunks": len(chunks),
        "status": "embedded",
        "upload_time": upload_time,
    }


def _embed_chunks(
    chunks: list[str],
    user_email: str,
    document_id: str,
    filename: str,
    upload_time: str,
    conversation_id: Optional[str] = None,
    project_id: Optional[str] = None,
    extra_payload: Optional[dict] = None,
) -> int:
    """Encode and upsert one document's chunks. Returns points written.

    Shared by the upload path and the OCR follow-up so the two cannot drift in
    payload shape; a difference there is invisible until a filter stops
    matching. BLOCKING (BGE encode + upsert), so async callers use a thread.
    """
    qdrant = get_qdrant()
    bge = get_bge()
    if qdrant is None or bge is None or not chunks:
        return 0
    ensure_collection()

    vectors = bge.encode(chunks, show_progress_bar=False).tolist()

    from qdrant_client.http import models as qm
    points = []
    for i, (chunk, vec) in enumerate(zip(chunks, vectors)):
        payload = {
            "user_email": user_email,
            "conversation_id": conversation_id,
            "project_id": project_id,
            "document_id": document_id,
            "filename": filename,
            "chunk_index": i,
            "chunk_text": chunk,
            "total_chunks": len(chunks),
            "upload_time": upload_time,
        }
        if extra_payload:
            payload.update(extra_payload)
        points.append(qm.PointStruct(id=str(uuid.uuid4()), vector=vec,
                                     payload=payload))
    qdrant.upsert(collection_name=USER_DOCS_COLLECTION, points=points)
    return len(points)


def count_document_points(document_id: str) -> int:
    """How many chunks of this document are in the index. 0 means unsearchable."""
    qdrant = get_qdrant()
    if qdrant is None or not document_id:
        return 0
    try:
        from qdrant_client.http import models as qm
        res = qdrant.count(
            collection_name=USER_DOCS_COLLECTION,
            count_filter=qm.Filter(must=[qm.FieldCondition(
                key="document_id", match=qm.MatchValue(value=document_id))]),
            exact=True,
        )
        return int(getattr(res, "count", 0))
    except Exception:
        logger.warning("count_document_points failed for %s", document_id)
        return 0


async def ocr_and_embed(
    document_id: str,
    user_email: str,
    conversation_id: Optional[str] = None,
    project_id: Optional[str] = None,
) -> dict:
    """OCR a stored PDF that has no text layer, then embed it.

    Runs as a background task after the upload response has already gone out,
    so a scan that takes a minute of tesseract does not hold the request open.
    Idempotent: a document that already has points is left alone, which makes
    it safe to call from both the upload path and the maintenance sweep, and
    safe to retry after a restart mid-OCR.
    """
    import asyncio

    path = get_document_file_path(user_email, document_id)
    if not path or not path.lower().endswith(".pdf"):
        return {"document_id": document_id, "ocr": "skipped",
                "reason": "not a stored pdf"}
    if count_document_points(document_id):
        return {"document_id": document_id, "ocr": "skipped",
                "reason": "already embedded"}

    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as e:
        logger.warning("OCR could not read %s: %s", document_id, e)
        return {"document_id": document_id, "ocr": "error", "reason": str(e)}

    filename = os.path.basename(path)
    logger.info("OCR starting for %s (%s, %d bytes)", document_id, filename, len(raw))
    text = await asyncio.to_thread(ocr_pdf_to_text, raw)
    chunks = chunk_text(text)
    if not chunks:
        # OCR ran and still produced nothing: a blank scan, a photograph, or a
        # language the installed tesseract pack does not cover. Logged so it is
        # visible, and left as "stored" rather than retried forever.
        logger.warning("OCR produced no text for %s (%s)", document_id, filename)
        return {"document_id": document_id, "ocr": "empty", "chunks": 0}

    try:
        mtime = os.path.getmtime(path)
        upload_time = (datetime.fromtimestamp(mtime, tz=timezone.utc)
                       .isoformat().replace("+00:00", "Z"))
    except OSError:
        upload_time = _iso_now()

    n = await asyncio.to_thread(
        _embed_chunks,
        chunks, user_email, document_id, filename, upload_time,
        conversation_id, project_id, {"ocr": True},
    )
    logger.info("OCR embedded %s: %d chunks from %d chars", document_id, n, len(text))
    return {"document_id": document_id, "ocr": "embedded", "chunks": n}


def _scan_user_disk(user_email: str) -> dict[str, dict]:
    """
    Walk the on-disk user directory to recover `document_id -> {filename,
    upload_time}`. This is the source of truth for files that exist but
    weren't (or couldn't be) embedded.
    """
    out: dict[str, dict] = {}
    user_dir = _user_dir(user_email)
    if not os.path.isdir(user_dir):
        return out
    for doc_id in os.listdir(user_dir):
        doc_path = os.path.join(user_dir, doc_id)
        if not os.path.isdir(doc_path):
            continue
        try:
            entries = os.listdir(doc_path)
        except OSError:
            continue
        if not entries:
            continue
        filename = entries[0]
        try:
            mtime = os.path.getmtime(os.path.join(doc_path, filename))
            upload_time = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        except OSError:
            upload_time = _iso_now()
        out[doc_id] = {"filename": filename, "upload_time": upload_time}
    return out


async def list_documents(
    user_email: str, conversation_id: Optional[str] = None
) -> list[dict]:
    disk_docs = _scan_user_disk(user_email)
    qdrant = get_qdrant()

    qdrant_docs: dict[str, dict] = {}
    if qdrant is not None:
        try:
            from qdrant_client.http import models as qm

            must = [
                qm.FieldCondition(
                    key="user_email", match=qm.MatchValue(value=user_email)
                )
            ]
            if conversation_id:
                must.append(
                    qm.FieldCondition(
                        key="conversation_id",
                        match=qm.MatchValue(value=conversation_id),
                    )
                )

            offset: Any = None
            while True:
                points, offset = qdrant.scroll(
                    collection_name=USER_DOCS_COLLECTION,
                    scroll_filter=qm.Filter(must=must),
                    limit=256,
                    with_payload=True,
                    with_vectors=False,
                    offset=offset,
                )
                for p in points:
                    payload = p.payload or {}
                    doc_id = payload.get("document_id")
                    if not doc_id:
                        continue
                    entry = qdrant_docs.setdefault(
                        doc_id,
                        {
                            "document_id": doc_id,
                            "filename": payload.get("filename"),
                            "chunks": payload.get("total_chunks", 0),
                            "status": "embedded",
                            "upload_time": payload.get("upload_time"),
                        },
                    )
                    entry["chunks"] = max(
                        entry["chunks"], payload.get("total_chunks", 0)
                    )
                if offset is None:
                    break
        except Exception as e:
            logger.warning("Failed to scroll user_docs: %s", e)

    documents: list[dict] = []
    seen_ids: set[str] = set()
    for doc_id, qdoc in qdrant_docs.items():
        seen_ids.add(doc_id)
        documents.append(qdoc)

    if not conversation_id:
        # Include files that exist on disk but have no embeddings (images,
        # empty extractions, or uploads that predate current infra).
        for doc_id, disk_meta in disk_docs.items():
            if doc_id in seen_ids:
                continue
            documents.append({
                "document_id": doc_id,
                "filename": disk_meta["filename"],
                "chunks": 0,
                "status": "stored",
                "upload_time": disk_meta["upload_time"],
            })

    documents.sort(key=lambda d: d.get("upload_time") or "", reverse=True)
    return documents


async def delete_document(document_id: str, user_email: str) -> bool:
    """Remove a document's file tree and all of its Qdrant points."""
    if not document_id or not user_email:
        return False

    doc_dir = _doc_dir(user_email, document_id)
    had_files = os.path.isdir(doc_dir)
    if had_files:
        try:
            shutil.rmtree(doc_dir)
        except OSError as e:
            logger.warning("Failed to delete %s: %s", doc_dir, e)

    qdrant = get_qdrant()
    had_points = False
    if qdrant is not None:
        try:
            from qdrant_client.http import models as qm

            result = qdrant.delete(
                collection_name=USER_DOCS_COLLECTION,
                points_selector=qm.FilterSelector(
                    filter=qm.Filter(
                        must=[
                            qm.FieldCondition(
                                key="user_email",
                                match=qm.MatchValue(value=user_email),
                            ),
                            qm.FieldCondition(
                                key="document_id",
                                match=qm.MatchValue(value=document_id),
                            ),
                        ]
                    )
                ),
            )
            had_points = bool(result)
        except Exception as e:
            logger.warning("Failed to delete points for %s: %s", document_id, e)

    return had_files or had_points


# ==============================================================================
# Search
# ==============================================================================

async def search_user_docs(
    query: str,
    user_email: str,
    conversation_id: Optional[str] = None,
    top_k: int = 5,
    project_id: Optional[str] = None,
    project_fallback: bool = True,
) -> dict:
    """
    Semantic search over the user's uploaded documents (BGE-base
    embeddings in Qdrant, collection ``user_docs``).

    Project scoping (§21):
      * ``project_id=None`` (default) means the caller is not inside a
        project, or wants user-global results unconditionally.
      * ``project_id="<pid>"`` searches only that project's docs.
      * When ``project_id`` is set AND ``project_fallback=True``, and
        the scoped search returns zero hits, a second search is run
        without the project filter and its results are returned under
        the same key. The response carries a ``sources_used`` list so
        callers (and the model) can tell which path actually found
        something.
    """
    qdrant = get_qdrant()
    bge = get_bge()
    if qdrant is None or bge is None or not query:
        return {"results": [], "sources_used": []}

    try:
        from qdrant_client.http import models as qm

        def _run(with_project: Optional[str]) -> list[dict]:
            must = [
                qm.FieldCondition(
                    key="user_email",
                    match=qm.MatchValue(value=user_email),
                )
            ]
            if conversation_id:
                must.append(
                    qm.FieldCondition(
                        key="conversation_id",
                        match=qm.MatchValue(value=conversation_id),
                    )
                )
            if with_project:
                must.append(
                    qm.FieldCondition(
                        key="project_id",
                        match=qm.MatchValue(value=with_project),
                    )
                )
            vector = bge.encode(query).tolist()
            results = qdrant.query_points(
                collection_name=USER_DOCS_COLLECTION,
                query=vector,
                query_filter=qm.Filter(must=must),
                limit=top_k,
                with_payload=True,
            )
            out = []
            for hit in results.points:
                payload = hit.payload or {}
                out.append({
                    "score": float(hit.score),
                    "document_id": payload.get("document_id"),
                    "filename": payload.get("filename"),
                    "chunk_index": payload.get("chunk_index"),
                    "content": payload.get("chunk_text", ""),
                    "project_id": payload.get("project_id"),
                })
            return out

        if project_id:
            scoped = _run(project_id)
            if scoped:
                return {
                    "results": scoped,
                    "sources_used": ["project"],
                    "project_id": project_id,
                }
            if not project_fallback:
                return {
                    "results": [],
                    "sources_used": ["project"],
                    "project_id": project_id,
                }
            # Two-phase fallback: the scoped search returned zero, try
            # user-global so the model can answer with something and
            # honestly note the fallback in its response.
            fallback = _run(None)
            return {
                "results": fallback,
                "sources_used": ["project", "global"] if fallback else ["project"],
                "project_id": project_id,
                "fallback_reason": (
                    "no results in project, searched user-global"
                    if fallback else
                    "no results in project or user-global"
                ),
            }
        # No project context - straight user-global search.
        return {"results": _run(None), "sources_used": ["global"]}
    except Exception as e:
        logger.exception("search_user_docs failed")
        return {"results": [], "sources_used": [], "error": str(e)}
