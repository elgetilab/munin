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
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from database import get_qdrant, get_bge, is_bge_loaded

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
        print("[WARNING] Qdrant not available, skipping user_docs collection setup")
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
            print(f"[OK] Created Qdrant collection: {USER_DOCS_COLLECTION}")

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
        print(f"[ERROR] Failed to set up user_docs collection: {e}")
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
        print(f"[INFO] GROBID extraction failed, falling back to pypdf: {e}")

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
        print(f"[ERROR] pypdf extraction failed: {e}")
        return ""


def _extract_docx(file_bytes: bytes) -> str:
    try:
        import docx  # python-docx
        document = docx.Document(io.BytesIO(file_bytes))
        parts = [p.text for p in document.paragraphs if p.text]
        return "\n\n".join(parts).strip()
    except Exception as e:
        print(f"[ERROR] docx extraction failed: {e}")
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


async def upload_document(
    filename: str,
    file_bytes: bytes,
    user_email: str,
    conversation_id: Optional[str] = None,
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
        return {
            "document_id": document_id,
            "filename": safe_filename,
            "chunks": 0,
            "status": "stored",
            "upload_time": upload_time,
        }

    qdrant = get_qdrant()
    bge = get_bge()
    if qdrant is None or bge is None:
        # File is saved; embedding will be unavailable until infra is ready.
        return {
            "document_id": document_id,
            "filename": safe_filename,
            "chunks": 0,
            "status": "stored",
            "upload_time": upload_time,
        }

    ensure_collection()

    vectors = bge.encode(chunks, show_progress_bar=False).tolist()

    from qdrant_client.http import models as qm
    points = []
    for i, (chunk, vec) in enumerate(zip(chunks, vectors)):
        points.append(
            qm.PointStruct(
                id=str(uuid.uuid4()),
                vector=vec,
                payload={
                    "user_email": user_email,
                    "conversation_id": conversation_id,
                    "document_id": document_id,
                    "filename": safe_filename,
                    "chunk_index": i,
                    "chunk_text": chunk,
                    "total_chunks": len(chunks),
                    "upload_time": upload_time,
                },
            )
        )
    qdrant.upsert(collection_name=USER_DOCS_COLLECTION, points=points)

    return {
        "document_id": document_id,
        "filename": safe_filename,
        "chunks": len(chunks),
        "status": "embedded",
        "upload_time": upload_time,
    }


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
            print(f"[WARNING] Failed to scroll user_docs: {e}")

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
            print(f"[WARNING] Failed to delete {doc_dir}: {e}")

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
            print(f"[WARNING] Failed to delete points for {document_id}: {e}")

    return had_files or had_points


# ==============================================================================
# Search
# ==============================================================================

async def search_user_docs(
    query: str,
    user_email: str,
    conversation_id: Optional[str] = None,
    top_k: int = 5,
) -> dict:
    qdrant = get_qdrant()
    bge = get_bge()
    if qdrant is None or bge is None or not query:
        return {"results": []}

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
            })
        return {"results": out}
    except Exception as e:
        print(f"[ERROR] search_user_docs failed: {e}")
        return {"results": [], "error": str(e)}
