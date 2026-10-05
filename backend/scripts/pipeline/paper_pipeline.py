#!/usr/bin/env python3
"""
==============================================================================
MUNIN Paper Processing Pipeline
==============================================================================
Processes scientific PDFs and stores them in the knowledge base.

Pipeline:
  PDF → GROBID (parse) → CrossRef (enrich) → SPECTER (embed) → Qdrant + Neo4j

Usage:
    # Process all PDFs in default directory
    ./paper_pipeline.py

    # Process a single PDF
    ./paper_pipeline.py --single /path/to/paper.pdf

    # Reprocess all PDFs (clears markers and re-runs)
    ./paper_pipeline.py --reprocess

    # Fast mode with parallel processing
    ./paper_pipeline.py --fast --workers 8

    # Reprocess all in fast mode
    ./paper_pipeline.py --reprocess --fast --workers 8

    # Watch directory for new PDFs
    ./paper_pipeline.py --watch

Options:
    --single FILE     Process a single PDF file
    --dir DIR         Directory with PDFs (default: /opt/munin/data/papers/pdf)
    --reprocess       Clear processed markers and reprocess all PDFs
    --fast            Fast mode: parallel GROBID + batch embeddings
    --workers N       Number of parallel workers for fast mode (default: 4)
    --watch           Watch directory for new PDFs continuously

Requirements:
    pip install qdrant-client neo4j sentence-transformers requests
==============================================================================
"""

import argparse
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

# Deployed side by side into /opt/cluster/scripts/pipeline by deploy.sh, so a
# plain module import works both from the repo and from the install dir.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from author_names import best_author_list  # noqa: E402
import doi_filename  # noqa: E402

# ==============================================================================
# Configuration
# ==============================================================================
GROBID_URL = os.getenv("GROBID_URL", "http://localhost:8070")
QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "munin-neo4j-password")

# Corpus locations. These MUST be environment-overridable: the host-side
# watcher (munin-paper-pipeline.service) sees the corpus at the paths below,
# but /api/admin/ingest runs this same script inside the retrieval container,
# where the corpus is bind-mounted at /papers and /papers-processed.
#
# Until 2026-08-18 they were hardcoded to the host paths. Inside the container
# those paths do not exist, and _dispose_post_pipeline created them with
# mkdir(parents=True) before moving the PDF in, so every uploaded PDF was
# filed into the container's writable layer, recorded in Qdrant as if it were
# on disk, and destroyed by the next container rebuild. Half of one
# contributor's 627 papers were lost this way before anyone noticed, because
# the failure looked exactly like success. See UPLOAD-INGEST-REPAIR-PLAN.md
# defect 1. The defaults are the host paths, so the watcher and manual
# invocations are unaffected; the container already exports the two env names
# used here. `require_corpus_dirs()` refuses to run if they are wrong.
PAPERS_DIR = os.getenv("PAPERS_PDF_DIR", "/opt/munin/data/papers/pdf")
PROCESSED_DIR = os.getenv("PAPERS_PROCESSED_DIR", "/opt/munin/data/papers/processed")
# SKIPPED_DIR removed 2026-05-13 (Phase E follow-up): the legacy
# disk-log path was replaced by the state sidecar in
# pdf/quarantine/<stem>.state.json. _log_skipped_pdf now only sets
# self._last_skip_reason, which the dispose helper reads.
QUARANTINE_DIR = os.getenv(
    "PAPERS_QUARANTINE_DIR", os.path.join(PAPERS_DIR, "quarantine")
)
OCR_CACHE_DIR = os.getenv("PAPERS_OCR_CACHE_DIR", "/opt/munin/data/papers/ocr_cache")
# Encoder migration: the pipeline must embed NEW papers with the SAME encoder
# the retrieval service serves (PAPER_ENCODER), into the SAME collection. The
# two move together or ingest writes vectors the search path cannot read.
# See docs/paper-track/done/ENCODER-MIGRATION-PLAN.md.
#
# THE DEFAULTS ARE PRODUCTION (corrected 2026-08-14). They read `specter` /
# `papers` until then, i.e. the pre-migration rollback pair, so any run without
# cluster.env -- a fresh cluster, a container started without the env file, a
# manual invocation -- silently ingested into the retired 768d corpus and
# looked like it worked. That is the identical bug DECISIONS.md 2026-08 fixed
# in retrieval/database.py; this copy of it was missed. Rollback is the flip in
# the other direction (specter + papers), never a half-flip.
COLLECTION_NAME = os.getenv("PAPERS_COLLECTION", "papers_bge")
PAPER_ENCODER = os.getenv("PAPER_ENCODER", "bge-large")   # specter | bge-large
EMBED_DIM = 1024 if PAPER_ENCODER == "bge-large" else 768

# Phase B (2026-05-13): every PDF the pipeline touches gets a sibling
# `<stem>.state.json` sidecar tracking its lifecycle state. Forward-
# only: existing records get their sidecar lazy-backfilled by the
# `detect` command (Phase D). See docs/PIPELINE-CONSOLIDATION-PLAN.md.
STATE_SCHEMA_VERSION = 1

# User agent email for API requests
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL") or os.getenv("MUNIN_CONTACT_EMAIL") or "admin@example.com"

# Quality filtering configuration
MIN_PAGE_COUNT = int(os.getenv("MIN_PAGE_COUNT", "3"))
REQUIRE_ABSTRACT_OR_REFS = os.getenv("REQUIRE_ABSTRACT_OR_REFS", "true").lower() == "true"

# Special return value to indicate PDF has no text (scanned/image-only)
NO_TEXT_CONTENT = "__NO_TEXT_CONTENT__"


# ==============================================================================
# Data Classes
# ==============================================================================
@dataclass
class Paper:
    id: str
    title: str
    abstract: str
    authors: List[Dict[str, str]]
    doi: Optional[str]
    year: Optional[int]
    journal: Optional[str]
    references: List[str]
    sections: List[Dict[str, str]] = field(default_factory=list)
    pdf_path: str = ""
    processed_at: str = ""
    # §28: when a PDF arrives with a sibling `{pdf}.contributor.json`
    # sidecar, that JSON is loaded here and stamped onto the Qdrant
    # payload (as a merged-in element of a `contributors` list) and
    # Neo4j (as a :Contributor node + CONTRIBUTED relationship).
    # Absent for normal admin-curated ingests, so untouched paths stay
    # byte-for-byte identical.
    contributor: Optional[Dict[str, str]] = None
    # Audit fields written through to Qdrant as `_grobid_title`,
    # `_grobid_doi`, `_ingest_source`, `_crossref_doi_rejected`,
    # `_crossref_title_rejected`. Used by the remediation tool to
    # detect upstream metadata splices (e.g. LIGPLOT PDF stored under
    # a JSTOR DOI). See docs/PAPER-INGEST-AUDIT.md.
    grobid_title: Optional[str] = None
    grobid_doi: Optional[str] = None
    ingest_source: str = "unknown"
    crossref_doi_rejected: Optional[str] = None
    crossref_title_rejected: Optional[str] = None
    # Set when the DOI on this record did NOT come off the PDF but was
    # recovered by searching Crossref for the GROBID title, after the
    # PDF's own DOI was rejected. Worth surfacing in audits: these are
    # papers whose identity was inferred rather than read.
    doi_recovered_by_title: Optional[str] = None


# Common short English words. Removed from titles before similarity
# scoring so generic terms don't artificially inflate Jaccard overlap.
_TITLE_STOPWORDS = frozenset({
    "the", "and", "for", "with", "from", "into", "via", "using",
    "this", "that", "were", "was", "are", "but", "not", "new",
    "one", "two", "three",
})

# Markup we strip from titles before tokenising. Crossref returns
# JATS-flavoured HTML in many records (<i>, <b>, <sup>, <sub>,
# <mml:math>...), which pollutes SPECTER embeddings and looks broken
# in the UI; for similarity it also distorts the token set.
_TITLE_MARKUP_RE = re.compile(r"<[^>]+>")
_TITLE_ENTITY_RE = re.compile(r"&[#a-zA-Z0-9]+;")
_TITLE_NONALNUM_RE = re.compile(r"[^a-z0-9 ]+")


def _normalize_title_tokens(s: str) -> set:
    """Tokenise a title for similarity scoring.

    Strips HTML/JATS markup and HTML entities, lowercases, drops
    non-alphanumeric characters, splits on whitespace, removes
    short tokens (<3 chars) and a small English-stopword set.
    Pure function, no I/O.

    Markup is replaced with the empty string (not a space) so
    intra-word notation like `Gd<sup>3+</sup>` collapses to
    `gd3` rather than splitting into below-cutoff `gd` + `3`.
    This matches how `_strip_markup` renders for storage.
    """
    if not s:
        return set()
    s = _TITLE_MARKUP_RE.sub("", s)
    s = _TITLE_ENTITY_RE.sub(" ", s)
    s = _TITLE_NONALNUM_RE.sub(" ", s.lower())
    return {w for w in s.split()
            if len(w) >= 3 and w not in _TITLE_STOPWORDS}


def _strip_markup(s: str) -> str:
    """Strip JATS/HTML tags and HTML entities from a string while
    preserving case, punctuation, and word boundaries.

    Crossref returns JATS-flavoured HTML in many records (`<b>`,
    `<i>`, `<sup>`, `<sub>`, `<mml:math>...`), which pollutes SPECTER
    embeddings and looks broken in the UI. Returns the original
    string when already clean; idempotent on safe input.
    """
    if not s:
        return s
    out = _TITLE_MARKUP_RE.sub("", s)
    out = html.unescape(out)
    # Collapse whitespace introduced by stripped block-level tags.
    out = re.sub(r"\s+", " ", out).strip()
    return out


def _norm_doi(doi: Optional[str]) -> Optional[str]:
    """Canonical form of a DOI for use as a key: lowercased, trimmed.

    Qdrant has always keyed points on `sha256(doi.lower())`, so two case
    variants of one DOI share a point. Neo4j did NOT: `MERGE (p:Paper {doi})`
    matches case-sensitively, so `10.1017/S0033...` and `10.1017/s0033...`
    became two nodes holding half a paper each -- citations on one, authors
    and metadata on the other. 13,053 DOIs were split this way before it was
    noticed on 2026-08-25, and it was user-visible: /citations returned a
    different paper depending on the case the caller happened to use.

    DOIs are case-insensitive by specification (the DOI Handbook says so for
    the whole string), so lowercasing loses nothing.
    """
    if not doi:
        return doi
    return doi.strip().lower()


def _title_similarity(a: str, b: str) -> float:
    """Token-set Jaccard similarity between two titles.

    Used by the pipeline to decide whether a Crossref enrichment is
    describing the same paper as the GROBID-parsed header. Returns
    0.0 when either side is empty after tokenisation. Threshold for
    the ingest-time guard is 0.3 (calibrated 2026-05-12 against
    known-bad records vs random samples; see
    docs/PAPER-INGEST-AUDIT.md).
    """
    ta = _normalize_title_tokens(a)
    tb = _normalize_title_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


# ==============================================================================
# State sidecar (Phase B of 2026-05-13 consolidation)
# ==============================================================================
# Every PDF the hardened pipeline touches gets a sibling
# `<stem>.state.json` describing its lifecycle position. Same JSON is
# mirrored to the Qdrant payload (`_state`, `_quarantine_reasons`,
# `_state_history`, `_first_seen_at`, `_ingest_path`) for fast filtered
# queries from paper_search / detect runs.
#
# State values:
#   "live"        in the corpus, searchable
#   "quarantine"  out of the corpus, PDF preserved in quarantine/,
#                 needs operator review (CLI `review` subcommand)
#   "rejected"    operator explicitly rejected via `review` (Phase E)
#
# The sidecar is the source of truth on disk; the Qdrant payload
# mirror only exists for `state=live` records (quarantine items have
# no Qdrant point).


def _state_sidecar_path(pdf_path) -> "Path":
    """Sibling state-sidecar path for a given PDF (live or quarantine)."""
    p = Path(pdf_path)
    return p.with_name(f"{p.stem}.state.json")


def _utcnow_iso() -> str:
    return (datetime.now(timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z"))


def _load_state_sidecar(pdf_path) -> Optional[Dict]:
    """Read the state sidecar next to `pdf_path`. Returns None if
    missing or unparseable (legacy records without a sidecar)."""
    sc = _state_sidecar_path(pdf_path)
    if not sc.is_file():
        return None
    try:
        with open(sc, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"  [WARN] state sidecar unreadable ({sc}): {e}")
        return None


def _write_state_sidecar(pdf_path, payload: Dict) -> None:
    """Atomically write a state sidecar next to `pdf_path`.
    Uses a tempfile + rename so partial writes never leave a corrupt
    sidecar visible."""
    sc = _state_sidecar_path(pdf_path)
    sc.parent.mkdir(exist_ok=True)
    tmp = sc.with_suffix(sc.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp, sc)


def _build_state_sidecar(
    paper: Optional["Paper"],
    ingest_path: str,
    state: str,
    quarantine_reasons: List[str],
    contributor: Optional[Dict] = None,
    audit_findings: Optional[Dict] = None,
    existing: Optional[Dict] = None,
) -> Dict:
    """Construct the state-sidecar dict. When `existing` is provided
    (a previously-written sidecar), the `first_seen_at` is preserved
    and the new entry is appended to `history` instead of replacing
    it. New ingests pass `existing=None` and get a fresh record."""
    now = _utcnow_iso()
    first_seen = (existing or {}).get("first_seen_at") or now
    history: List[Dict] = list((existing or {}).get("history") or [])
    history.append({
        "at": now,
        "state": state,
        "via": ingest_path,
        "reason": quarantine_reasons[0] if quarantine_reasons else None,
    })
    payload = {
        "schema_version": STATE_SCHEMA_VERSION,
        "doi": (paper.doi if paper else None),
        "state": state,
        "ingest_path": ingest_path,
        "quarantine_reasons": list(quarantine_reasons or []),
        "first_seen_at": first_seen,
        "last_modified_at": now,
        "contributor": contributor or (existing or {}).get("contributor"),
        "audit_findings": audit_findings or (existing or {}).get("audit_findings"),
        "history": history,
    }
    return payload


def require_corpus_dirs(check_collection: bool = True) -> None:
    """Refuse to run unless the corpus directories are really there.

    The 2026-08-18 data loss happened because a wrong PAPERS_DIR was silently
    created instead of raising: the pipeline reported success while filing
    PDFs into a container layer. So the corpus root is now a precondition,
    not something this script will conjure. Call once at startup, before any
    PDF is touched.

    `check_collection` additionally catches a mount that exists but is empty,
    which in production means the bind mount is missing rather than that the
    corpus is genuinely new. Skipped on a fresh install (empty collection) and
    on any Qdrant error, so an unrelated outage cannot block ingest.
    """
    for label, path in (("PAPERS_PDF_DIR", PAPERS_DIR),
                        ("PAPERS_PROCESSED_DIR", PROCESSED_DIR)):
        if not os.path.isdir(path):
            sys.exit(
                f"[FATAL] {label}={path} is not a directory. This process will "
                f"not create it: on the cluster that path is a bind mount, and "
                f"creating it silently sends every ingested PDF into a "
                f"container layer that the next rebuild deletes. Set {label} "
                f"to the mounted corpus (the retrieval container uses "
                f"/papers and /papers-processed)."
            )
        if not os.access(path, os.W_OK | os.X_OK):
            sys.exit(f"[FATAL] {label}={path} is not writable by this process.")

    if not check_collection:
        return
    try:
        has_pdf = any(n.endswith(".pdf") for n in os.listdir(PAPERS_DIR))
    except OSError:
        return
    if has_pdf:
        return
    try:
        from qdrant_client import QdrantClient
        count = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT).count(
            collection_name=COLLECTION_NAME, exact=True
        ).count
    except Exception:
        return  # Qdrant unreachable or collection absent; not our problem here.
    if count > 0:
        sys.exit(
            f"[FATAL] PAPERS_PDF_DIR={PAPERS_DIR} holds no PDFs, but "
            f"{COLLECTION_NAME} has {count} points. That combination means the "
            f"corpus bind mount is missing. Refusing to run rather than "
            f"re-filing the corpus into an empty directory."
        )


def _dispose_post_pipeline(
    pdf_path: str,
    paper: "Optional[Paper]",
    skip_reason: Optional[str],
    ingest_path: str,
    qdrant_client=None,
) -> Dict:
    """Single source of truth for post-pipeline disposal.

    Moves the PDF to its final on-disk location, writes the state
    sidecar, mirrors state to the Qdrant payload (live records only),
    writes the watcher's processed marker (live records only).

    Called from `_process_and_dispose` after every pipeline run on
    both ingest paths (--watch and admin/ingest --single).

    Returns a dict with keys: `state`, `final_pdf_path`,
    `quarantine_reasons`, `doi`, `qdrant_point_id`.
    """
    pdf = Path(pdf_path)

    # Decide target state.
    if paper is not None and paper.doi and skip_reason is None:
        target_state = "live"
        reasons: List[str] = []
    elif paper is not None and not paper.doi:
        # Stage 1.6 of the 2026-05-12 audit: pipeline succeeded but
        # extracted no DOI. Quarantine to keep these out of the corpus.
        target_state = "quarantine"
        reasons = ["doi_extraction_failed"]
    else:
        target_state = "quarantine"
        reasons = [skip_reason or "pipeline_error"]

    # Decide destination path.
    if target_state == "live":
        safe_doi = paper.doi.replace("/", "_")
        final_pdf = Path(PAPERS_DIR) / f"doi_{safe_doi}.pdf"
    else:
        Path(QUARANTINE_DIR).mkdir(exist_ok=True)
        if paper and paper.doi:
            safe_doi = paper.doi.replace("/", "_")
            final_pdf = Path(QUARANTINE_DIR) / f"doi_{safe_doi}.pdf"
        else:
            final_pdf = Path(QUARANTINE_DIR) / pdf.name

    # Move PDF (and contributor sidecar, if present) to the final
    # destination. If the destination already exists, we drop the
    # newcomer rather than stomping (e.g. re-ingest of an existing
    # DOI from a different contributor; their entry is already
    # merged into the contributors[] list on the Qdrant point).
    if pdf.resolve() != final_pdf.resolve():
        try:
            # exist_ok but NOT parents: the corpus root is validated at
            # startup by require_corpus_dirs(), so a missing parent here means
            # something is wrong with the mount and we want the OSError, not a
            # freshly invented directory. See UPLOAD-INGEST-REPAIR-PLAN.md.
            final_pdf.parent.mkdir(exist_ok=True)
            if final_pdf.exists():
                if pdf.exists():
                    pdf.unlink()
            elif pdf.exists():
                shutil.move(str(pdf), str(final_pdf))
        except OSError as e:
            print(f"  [WARN] PDF move {pdf} -> {final_pdf} failed: {e}")
            final_pdf = pdf
        old_contrib = pdf.with_name(f"{pdf.stem}.contributor.json")
        new_contrib = final_pdf.with_name(f"{final_pdf.stem}.contributor.json")
        if old_contrib.is_file() and old_contrib != new_contrib:
            try:
                shutil.move(str(old_contrib), str(new_contrib))
            except OSError as e:
                print(f"  [WARN] contributor sidecar move failed: {e}")

    # Write the state sidecar (preserving any existing first_seen_at +
    # history). The sidecar is the source of truth on disk.
    existing = _load_state_sidecar(final_pdf)
    contributor = _load_contributor_sidecar(str(final_pdf))
    state_doc = _build_state_sidecar(
        paper=paper,
        ingest_path=ingest_path,
        state=target_state,
        quarantine_reasons=reasons,
        contributor=contributor,
        existing=existing,
    )
    _write_state_sidecar(final_pdf, state_doc)

    # Mirror state into the Qdrant payload (live records only). For
    # quarantined null-DOI records, delete the point that
    # _store_vectors created — we don't want paper_search to surface
    # records whose state is `quarantine`.
    qdrant_point_id: Optional[int] = None
    if paper and paper.doi:
        qdrant_point_id = int(
            hashlib.sha256(paper.doi.lower().encode()).hexdigest()[:16], 16
        )
    elif paper and paper.id:
        try:
            qdrant_point_id = int(paper.id, 16)
        except (TypeError, ValueError):
            qdrant_point_id = None

    if qdrant_client is not None and qdrant_point_id is not None:
        if target_state == "live":
            try:
                qdrant_client.set_payload(
                    collection_name=COLLECTION_NAME,
                    payload={
                        "pdf_path": str(final_pdf),
                        "_state": "live",
                        "_quarantine_reasons": [],
                        "_state_history": state_doc["history"][-5:],
                        "_first_seen_at": state_doc["first_seen_at"],
                        "_ingest_path": ingest_path,
                    },
                    points=[qdrant_point_id],
                    wait=False,
                )
            except Exception as e:
                print(f"  [WARN] Qdrant state-mirror set_payload failed: {e}")
        else:
            try:
                qdrant_client.delete(
                    collection_name=COLLECTION_NAME,
                    points_selector=[qdrant_point_id],
                    wait=False,
                )
            except Exception:
                pass  # best-effort; the Qdrant point may not exist

    # Watcher's "already seen" marker. Only for live records: a
    # quarantined PDF in /papers/pdf/ would otherwise re-attract the
    # watcher every poll. (Quarantined PDFs live in quarantine/, not
    # scanned by the watcher, so this only matters for live.)
    if target_state == "live":
        try:
            Path(PROCESSED_DIR).mkdir(exist_ok=True)
            marker = Path(PROCESSED_DIR) / f"{final_pdf.stem}.json"
            with open(marker, "w") as f:
                json.dump({
                    "doi": paper.doi if paper else None,
                    "paper_id": paper.id if paper else None,
                    "state": target_state,
                    "ingested_at": state_doc["last_modified_at"],
                    "ingest_path": ingest_path,
                }, f)
        except OSError as e:
            print(f"  [WARN] processed-marker write failed: {e}")

    return {
        "state": target_state,
        "final_pdf_path": str(final_pdf),
        "quarantine_reasons": reasons,
        "doi": paper.doi if paper else None,
        "qdrant_point_id": qdrant_point_id,
    }


def _process_and_dispose(pipeline, pdf_path: str, ingest_path: str) -> Dict:
    """Run `pipeline.process_pdf` and call `_dispose_post_pipeline`.
    The single entry-point both `--watch` and `--single` go through.

    Returns the dispose result. On any uncaught exception inside
    `process_pdf`, captures it as the skip_reason and routes the
    record to quarantine instead of letting the watcher crash."""
    pipeline._last_skip_reason = None
    try:
        paper = pipeline.process_pdf(pdf_path)
    except Exception as e:
        print(f"  [ERROR] Pipeline crashed on {pdf_path}: {e}")
        import traceback
        traceback.print_exc()
        paper = None
        pipeline._last_skip_reason = f"pipeline_error: {type(e).__name__}: {e}"

    skip_reason = None
    if paper is None:
        skip_reason = getattr(pipeline, "_last_skip_reason", None) or "pipeline_error"

    return _dispose_post_pipeline(
        pdf_path=pdf_path,
        paper=paper,
        skip_reason=skip_reason,
        ingest_path=ingest_path,
        qdrant_client=getattr(pipeline, "qdrant", None),
    )


def _load_contributor_sidecar(pdf_path: str) -> Optional[Dict[str, str]]:
    """Look for `{pdf_path_without_ext}.contributor.json` next to the
    PDF and return its parsed contents. Missing or unparseable sidecar
    → None (the caller ingests without attribution)."""
    base = os.path.splitext(pdf_path)[0]
    sidecar = f"{base}.contributor.json"
    if not os.path.isfile(sidecar):
        return None
    try:
        with open(sidecar, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"  [WARN] contributor sidecar unreadable ({sidecar}): {e}")
        return None
    # Normalise to exactly the keys we store. Anything extra is ignored.
    return {
        "email": (data.get("contributor_email") or "").strip().lower() or None,
        "username": data.get("contributor_username"),
        "display_name": data.get("contributor_display_name"),
        "group_slug": data.get("research_group"),
        "group_display_name": data.get("research_group_display_name"),
        "upload_time": data.get("uploaded_at") or data.get("upload_time"),
        # Stage 1.5: when the uploader's original filename was
        # `doi_<doi>.pdf`, the admin/ingest endpoint extracts the DOI
        # and stashes it here so the pipeline can use it as a hint
        # before falling back to GROBID's DOI extraction (which is
        # vulnerable to picking up citation DOIs).
        "filename_doi_hint": data.get("filename_doi_hint"),
    }


# ==============================================================================
# Pipeline Class
# ==============================================================================
class PaperPipeline:
    def __init__(self, fast_mode: bool = False, workers: int = 4):
        self.grobid_url = GROBID_URL
        self.qdrant = None
        self.neo4j = None
        self.embedder = None
        self.fast_mode = fast_mode
        self.workers = workers
        self._grobid_consecutive_failures = 0
        # Side-channel: each skip point in process_pdf sets this so
        # _process_and_dispose can stamp the reason into the state
        # sidecar without needing to refactor process_pdf's return
        # type. Reset at the start of every _process_and_dispose call.
        self._last_skip_reason: Optional[str] = None
        self._init_clients()

    def _check_grobid_health(self, max_wait: int = 60) -> bool:
        """Check if GROBID is alive, wait for it to come back up if needed."""
        import time
        start_time = time.time()
        while time.time() - start_time < max_wait:
            try:
                response = requests.get(f"{self.grobid_url}/api/isalive", timeout=5)
                if response.status_code == 200:
                    return True
            except Exception:
                pass
            print(f"        Waiting for GROBID to become available...")
            time.sleep(5)
        return False

    def _validate_pdf(self, pdf_path: str) -> Tuple[bool, str]:
        """
        Validate that a PDF file is readable and not corrupt.

        Returns:
            Tuple of (is_valid, error_message)
        """
        # Check file exists and has content
        if not os.path.exists(pdf_path):
            return False, "File does not exist"

        file_size = os.path.getsize(pdf_path)
        if file_size == 0:
            return False, "File is empty"

        if file_size < 100:
            return False, f"File too small ({file_size} bytes)"

        # Check PDF magic bytes
        try:
            with open(pdf_path, 'rb') as f:
                header = f.read(8)
                if not header.startswith(b'%PDF'):
                    return False, "Not a PDF file (missing %PDF header)"
        except Exception as e:
            return False, f"Cannot read file: {e}"

        # Try to parse with PyPDF2 for deeper validation
        try:
            from pypdf import PdfReader
            reader = PdfReader(pdf_path)
            num_pages = len(reader.pages)
            if num_pages == 0:
                return False, "PDF has no pages"
            # Try to access first page to verify it's readable
            _ = reader.pages[0]
            return True, ""
        except ImportError:
            # PyPDF2/pypdf not available, try basic validation with pdfinfo
            try:
                result = subprocess.run(
                    ['pdfinfo', pdf_path],
                    capture_output=True,
                    timeout=10
                )
                if result.returncode != 0:
                    stderr = result.stderr.decode('utf-8', errors='ignore')
                    if 'damage' in stderr.lower() or 'error' in stderr.lower():
                        return False, f"Corrupt PDF: {stderr[:100]}"
                    return False, f"pdfinfo failed: {stderr[:100]}"
                return True, ""
            except FileNotFoundError:
                # Neither pypdf nor pdfinfo available, fall back to header check only
                return True, ""  # Already passed header check
            except subprocess.TimeoutExpired:
                return False, "PDF validation timed out"
            except Exception as e:
                return False, f"Validation error: {e}"
        except Exception as e:
            error_msg = str(e)
            if 'encrypt' in error_msg.lower():
                return False, "PDF is encrypted"
            elif 'eof' in error_msg.lower() or 'trailer' in error_msg.lower():
                return False, "Corrupt PDF: missing EOF or trailer"
            elif 'xref' in error_msg.lower():
                return False, "Corrupt PDF: damaged xref table"
            else:
                return False, f"Invalid PDF: {error_msg[:80]}"

    def _init_clients(self):
        """Initialize database clients and embedder"""
        # Qdrant
        try:
            from qdrant_client import QdrantClient
            from qdrant_client.models import Distance, VectorParams

            self.qdrant = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)

            # Create collection if not exists
            collections = [c.name for c in self.qdrant.get_collections().collections]
            if COLLECTION_NAME not in collections:
                self.qdrant.create_collection(
                    collection_name=COLLECTION_NAME,
                    vectors_config=VectorParams(size=EMBED_DIM, distance=Distance.COSINE)
                )
                print(f"[OK] Created Qdrant collection: {COLLECTION_NAME}")
        except Exception as e:
            print(f"[WARNING] Qdrant connection failed: {e}")
            self.qdrant = None

        # Neo4j
        try:
            from neo4j import GraphDatabase

            self.neo4j = GraphDatabase.driver(
                NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD)
            )
            # Initialize schema
            with self.neo4j.session() as session:
                session.run(
                    "CREATE CONSTRAINT paper_id IF NOT EXISTS "
                    "FOR (p:Paper) REQUIRE p.paper_id IS UNIQUE"
                )
                session.run(
                    "CREATE CONSTRAINT author_id IF NOT EXISTS "
                    "FOR (a:Author) REQUIRE a.author_id IS UNIQUE"
                )
                # Papers are looked up by DOI far more often than by
                # paper_id: the ingest path, /api/papers/{doi}/citations,
                # get_author_papers, and the graph reconciliation all match
                # on it. Without this index each lookup is a full label scan
                # (measured 2026-08-04 on 718,795 Paper nodes: 1,437,591 db
                # hits per lookup, versus 2 with the index). NOT unique:
                # 170 DOIs legitimately carry two nodes today, which is a
                # separate defect and would make a constraint fail.
                session.run(
                    "CREATE INDEX paper_doi IF NOT EXISTS "
                    "FOR (p:Paper) ON (p.doi)"
                )
            print("[OK] Neo4j connected")
        except Exception as e:
            print(f"[WARNING] Neo4j connection failed: {e}")
            self.neo4j = None

        # SPECTER Embedder (use local model)
        try:
            from sentence_transformers import SentenceTransformer

            # Determine which device to use
            # GPU 1 is reserved for vLLM, so use GPU 0 for paper processing
            import torch
            if torch.cuda.is_available():
                device = "cuda:0"  # Use first GPU (vLLM uses GPU 1)
                print(f"[INFO] Using {device} for embeddings")
            else:
                device = "cpu"
                print("[INFO] CUDA not available, using CPU for embeddings")

            # Paper embedder — MUST match the retrieval service's PAPER_ENCODER.
            if PAPER_ENCODER == "bge-large":
                bge_path = os.getenv("BGE_LARGE_MODEL_PATH",
                                     "/opt/munin/data/models/bge-large")
                # A model directory, not just a path: compose mounts an empty
                # directory when no weights were staged (see retrieval's
                # database.local_model_or_hub).
                has_model = any(os.path.isfile(os.path.join(bge_path, f))
                                for f in ("modules.json", "config.json"))
                model = bge_path if has_model else "BAAI/bge-large-en-v1.5"
                self.embedder = SentenceTransformer(model, device=device)  # 1024d, docs raw
                print(f"[OK] BGE-large embedder loaded from {model}")
            else:
                # Local SPECTER model downloaded by 05-knowledge-base.sh
                specter_path = "/opt/munin/data/models/specter"
                if any(os.path.isfile(os.path.join(specter_path, f))
                       for f in ("modules.json", "config.json")):
                    self.embedder = SentenceTransformer(specter_path, device=device)
                    print(f"[OK] SPECTER embedder loaded from {specter_path}")
                else:
                    self.embedder = SentenceTransformer("sentence-transformers/allenai-specter", device=device)
                    print("[OK] SPECTER embedder loaded from HuggingFace")
        except Exception as e:
            print(f"[WARNING] Embedder failed to load: {e}")
            self.embedder = None

    def _extract_doi_from_filename(self, filename: str) -> Optional[str]:
        """The DOI a `doi_*.pdf` filename stands for, when it stands for
        exactly one. Ambiguous names (more than one `_` after the prefix)
        return None here; _verified_filename_doi() checks their candidates
        against Crossref and the PDF's title. See doi_filename.py."""
        return doi_filename.decode(filename)[0]

    def _verified_filename_doi(self, filename: str, pdf_title: str) -> Optional[str]:
        """Resolve an ambiguous `doi_*.pdf` filename, or None. The old
        decoder restored only the first `_` and the result overrode GROBID,
        re-keying multi-slash and colon DOIs to DOIs that do not exist."""
        exact, cands = doi_filename.decode(filename)
        if exact or not cands:
            return exact
        doi, trace = doi_filename.verify(
            cands, pdf_title, self._fetch_crossref, _title_similarity,
            threshold=self._MERGE_TITLE_SIM_THRESHOLD)
        if doi:
            print(f"  [INFO] ambiguous filename {filename} verified as {doi}")
        else:
            print(f"  [WARN] ambiguous filename {filename}: no candidate confirmed by "
                  f"Crossref + title ({trace}); not overriding GROBID")
        return doi

    def _check_ocrmypdf_available(self) -> bool:
        """Check if ocrmypdf is installed and available"""
        return shutil.which("ocrmypdf") is not None

    def _check_qpdf_available(self) -> bool:
        """Check if qpdf is installed and available"""
        return shutil.which("qpdf") is not None

    def _check_pdf_encrypted(self, pdf_path: str) -> bool:
        """Check if a PDF is encrypted using qpdf"""
        if not self._check_qpdf_available():
            return False

        try:
            result = subprocess.run(
                ["qpdf", "--show-encryption", pdf_path],
                capture_output=True,
                text=True,
                timeout=30
            )
            # qpdf returns 0 and shows encryption info if encrypted
            # If not encrypted, it says "File is not encrypted"
            return "File is not encrypted" not in result.stdout
        except Exception:
            return False

    def _decrypt_pdf(self, pdf_path: str) -> Optional[str]:
        """
        Decrypt a PDF using qpdf.
        Returns the path to the decrypted PDF, or None if decryption fails.
        Only works for PDFs with owner passwords (restrictions) but no user password.
        """
        if not self._check_qpdf_available():
            print("        [WARNING] qpdf not installed, cannot decrypt PDFs")
            print("        Install with: sudo apt install qpdf")
            return None

        # Create a temporary decrypted version
        ocr_cache = Path(OCR_CACHE_DIR)
        ocr_cache.mkdir(parents=True, exist_ok=True)

        pdf_hash = hashlib.sha256(pdf_path.encode()).hexdigest()[:16]
        decrypted_path = ocr_cache / f"{pdf_hash}_decrypted.pdf"

        # Check if already decrypted
        if decrypted_path.exists():
            print("        Using cached decrypted PDF")
            return str(decrypted_path)

        try:
            print("        Decrypting PDF with qpdf...")
            result = subprocess.run(
                ["qpdf", "--decrypt", pdf_path, str(decrypted_path)],
                capture_output=True,
                text=True,
                timeout=60
            )

            if result.returncode == 0:
                print("        PDF decrypted successfully")
                return str(decrypted_path)
            else:
                # qpdf returns 2 for warnings (file still created) and 3 for errors
                if result.returncode == 2 and decrypted_path.exists():
                    print("        PDF decrypted with warnings")
                    return str(decrypted_path)
                else:
                    print(f"        Decryption failed (code {result.returncode})")
                    if result.stderr:
                        # Check for user password error
                        if "password" in result.stderr.lower():
                            print("        Error: PDF requires a user password (cannot decrypt)")
                        else:
                            print(f"        Error: {result.stderr[:200]}")
                    return None

        except subprocess.TimeoutExpired:
            print("        Decryption timed out")
            return None
        except Exception as e:
            print(f"        Decryption error: {e}")
            return None

    def _run_ocr(self, pdf_path: str) -> Optional[str]:
        """
        Run OCR on a PDF using ocrmypdf.
        Returns the path to the OCR'd PDF (cached), or None if OCR fails.
        Automatically decrypts encrypted PDFs before OCR.
        """
        if not self._check_ocrmypdf_available():
            print("        [WARNING] ocrmypdf not installed, cannot OCR scanned PDFs")
            print("        Install with: sudo apt install ocrmypdf")
            return None

        # Create OCR cache directory
        ocr_cache = Path(OCR_CACHE_DIR)
        ocr_cache.mkdir(parents=True, exist_ok=True)

        # Use hash of original path for cache filename
        pdf_hash = hashlib.sha256(pdf_path.encode()).hexdigest()[:16]
        ocr_pdf_path = ocr_cache / f"{pdf_hash}_ocr.pdf"

        # Check if already OCR'd
        if ocr_pdf_path.exists():
            print("        Using cached OCR result")
            return str(ocr_pdf_path)

        # Check if PDF is encrypted and decrypt if needed
        pdf_to_ocr = pdf_path
        if self._check_pdf_encrypted(pdf_path):
            print("        PDF is encrypted, attempting decryption...")
            decrypted_path = self._decrypt_pdf(pdf_path)
            if decrypted_path:
                pdf_to_ocr = decrypted_path
            else:
                print("        Cannot proceed without decryption")
                return None

        try:
            print("        Running OCR (this may take a while)...")
            result = subprocess.run(
                [
                    "ocrmypdf",
                    "--skip-text",  # Skip pages that already have text
                    "--optimize", "1",  # Light optimization
                    "--quiet",
                    pdf_to_ocr,
                    str(ocr_pdf_path)
                ],
                capture_output=True,
                text=True,
                timeout=300  # 5 minute timeout per PDF
            )

            if result.returncode == 0:
                print("        OCR completed successfully")
                return str(ocr_pdf_path)
            elif result.returncode == 6:
                # Exit code 6 means the file already has text (shouldn't happen but handle it)
                print("        PDF already has text layer")
                # Copy original to cache location
                shutil.copy2(pdf_to_ocr, ocr_pdf_path)
                return str(ocr_pdf_path)
            else:
                print(f"        OCR failed with code {result.returncode}")
                if result.stderr:
                    print(f"        Error: {result.stderr[:200]}")
                return None

        except subprocess.TimeoutExpired:
            print("        OCR timed out (>5 minutes)")
            return None
        except Exception as e:
            print(f"        OCR error: {e}")
            return None

    def _log_skipped_pdf(self, pdf_path: str, reason: str):
        """Record why a PDF was skipped.

        Sets ``self._last_skip_reason`` so the post-pipeline disposal
        (Phase B) can stamp the reason into the state sidecar. The
        legacy JSON dump into ``SKIPPED_DIR`` was retired after Phase C
        of the 2026-05-13 consolidation — the state sidecar at
        ``quarantine/<stem>.state.json`` is now the source of truth.
        """
        self._last_skip_reason = reason
        print(f"  [SKIPPED] {reason}")

    def process_pdf(self, pdf_path: str) -> Optional[Paper]:
        """Process a single PDF through the pipeline"""
        pdf_path = str(pdf_path)
        filename = Path(pdf_path).name
        print(f"\n{'=' * 60}")
        print(f"Processing: {filename}")
        print(f"{'=' * 60}")

        try:
            # Validate PDF before processing
            is_valid, error_msg = self._validate_pdf(pdf_path)
            if not is_valid:
                print(f"  [SKIP] Invalid PDF: {error_msg}")
                self._log_skipped_pdf(pdf_path, f"Invalid PDF: {error_msg}")
                return None

            # Extract DOI from filename first (most reliable source).
            # Stage 1.5: uploads land as inbox/<uuid>.pdf so this
            # returns None for them; we fall back to a
            # `filename_doi_hint` written by the admin/ingest endpoint
            # when the user's original filename followed the doi_*.pdf
            # convention. Lifting the sidecar load up-front lets that
            # hint feed into the filename-DOI override below.
            contributor_sidecar = _load_contributor_sidecar(pdf_path)
            filename_doi = self._extract_doi_from_filename(filename)
            if filename_doi:
                print(f"  [INFO] DOI from filename: {filename_doi}")
            elif contributor_sidecar and contributor_sidecar.get("filename_doi_hint"):
                filename_doi = contributor_sidecar["filename_doi_hint"]
                print(f"  [INFO] DOI from uploader filename hint: {filename_doi}")

            # Step 1: Parse with GROBID
            print("  [1/4] Parsing with GROBID...")
            grobid_data = self._parse_grobid(pdf_path)

            # Handle scanned PDFs - try OCR fallback
            if grobid_data == NO_TEXT_CONTENT:
                print("  [1/4] Attempting OCR fallback for scanned PDF...")
                ocr_pdf_path = self._run_ocr(pdf_path)
                if ocr_pdf_path:
                    print("        Re-parsing OCR'd PDF with GROBID...")
                    grobid_data = self._parse_grobid(ocr_pdf_path)
                    if grobid_data == NO_TEXT_CONTENT:
                        # OCR didn't help - skip this PDF
                        self._log_skipped_pdf(pdf_path, "OCR completed but still no extractable text")
                        return None
                    elif not grobid_data:
                        self._log_skipped_pdf(pdf_path, "GROBID failed after OCR")
                        return None
                    print("        OCR fallback successful!")
                else:
                    # OCR failed - skip this PDF
                    self._log_skipped_pdf(pdf_path, "Scanned PDF and OCR unavailable or failed")
                    return None

            if not grobid_data:
                print("  [ERROR] GROBID parsing failed")
                self._log_skipped_pdf(pdf_path, "grobid_parse_failed")
                return None
            print(f"        Title: {grobid_data.get('title', 'Unknown')[:50]}...")

            # Snapshot the GROBID-extracted title/DOI before any downstream
            # mutation (filename override, Crossref merge). These become
            # audit fields on the Qdrant payload so the remediation tool
            # can spot upstream metadata splices.
            grobid_title_raw = (grobid_data.get("title") or "").strip() or None
            grobid_doi_raw = grobid_data.get("doi")

            # An ambiguous doi_*.pdf name is only trusted once Crossref and
            # the parsed title confirm one of its candidate DOIs.
            if not filename_doi and filename.startswith("doi_"):
                filename_doi = self._verified_filename_doi(filename, grobid_title_raw or "")

            # Use filename DOI as authoritative source if available
            # GROBID can mistakenly extract DOIs from citations instead of the paper itself
            if filename_doi:
                grobid_doi = grobid_data.get("doi")
                if grobid_doi and grobid_doi != filename_doi:
                    print(f"  [WARNING] GROBID DOI ({grobid_doi}) differs from filename DOI")
                    print(f"            Using filename DOI: {filename_doi}")
                grobid_data["doi"] = filename_doi

            # Step 2: Enrich with CrossRef
            doi = grobid_data.get("doi")
            if doi:
                print(f"  [2/4] Enriching via CrossRef (DOI: {doi})...")
                crossref = self._fetch_crossref(doi)
                if crossref:
                    grobid_data = self._merge_metadata(grobid_data, crossref)
                    print("        Metadata enriched")
            else:
                print("  [2/4] No DOI found, skipping CrossRef")

            # Step 2.4: the merge clears the DOI when Crossref's record for it
            # describes a different paper, which means GROBID most likely read
            # it off the reference list. Ask Crossref what DOI actually belongs
            # to the title on the PDF. If that comes back empty we continue
            # with no DOI, and dispose quarantines the paper rather than
            # filing it under a DOI we know is wrong.
            if doi and not grobid_data.get("doi"):
                recovered = self._fetch_crossref_by_title(
                    grobid_data.get("title") or "", grobid_data.get("authors")
                )
                if recovered:
                    grobid_data["doi"] = recovered.get("DOI")
                    # Re-merge against the RECOVERED record so journal, year
                    # and authors come from the paper we actually have. The
                    # title guard runs again inside, so a bad search result
                    # cannot sneak enrichment through.
                    grobid_data = self._merge_metadata(grobid_data, recovered)
                    grobid_data["_doi_recovered_by_title"] = recovered.get("DOI")

            # The DOI may have been cleared or replaced above, so re-read it
            # rather than trusting the value captured before the merge. It
            # feeds the quality filter, the Paper object, the Qdrant point id
            # and the on-disk filename.
            doi = grobid_data.get("doi")

            # Step 2.5: Quality filter check via OpenAlex
            if doi:
                passes_filter, filter_reason = self._check_quality_filters(doi, grobid_data, pdf_path)
                if not passes_filter:
                    print(f"  [SKIP] Quality filter: {filter_reason}")
                    self._log_skipped_pdf(pdf_path, f"Quality filter: {filter_reason}")
                    return None

            # Step 3: Create Paper object
            paper_id = hashlib.sha256(pdf_path.encode()).hexdigest()[:16]

            # Validate title - skip papers with empty titles
            title = grobid_data.get("title", "").strip()
            if not title:
                print("  [ERROR] Empty title - skipping (metadata extraction failed)")
                self._log_skipped_pdf(pdf_path, "empty_title")
                return None

            # Skip non-research content (news articles, editorials, etc.)
            non_research_titles = [
                "news and views", "editorial", "erratum", "correction", "retraction",
                "graphical abstract", "notes and references", "cover picture",
                "table of contents", "advertisement", "book review", "corrigendum",
                "front matter", "back matter", "index", "contents", "author index",
                "subject index", "acknowledgements", "preface", "foreword",
                "notes for notes", "in this issue", "issue information", "masthead",
            ]
            if title.lower() in non_research_titles:
                print(f"  [SKIP] Non-research content: '{title}'")
                self._log_skipped_pdf(pdf_path, f"non_research_title: {title.lower()[:40]}")
                return None

            # Supporting-information PDFs (2026-08-23). Publishers ship these
            # as separate downloads and contributors upload them alongside the
            # article, so they arrive looking like ordinary papers. GROBID
            # reads the real parent title off the header ("Supporting
            # Information for <paper>"), the DOI logic then resolves the
            # PARENT's DOI, and the SI lands as the canonical record for a
            # paper it is not -- read_paper on that DOI returns figures and
            # methods appendices instead of the article. Caught during the
            # 2026-08-23 R1 canary on jz9b01407_si_001.pdf.
            #
            # Prefix rather than exact match: the whole point is that these
            # titles carry the parent's title after the marker. Anchored at
            # the start so a paper that merely discusses supplementary data
            # is unaffected.
            si_title_prefixes = (
                "supporting information", "supplementary information",
                "supplementary material", "supplemental material",
                "supplementary materials", "supplemental materials",
                "supporting material", "supporting materials",
                "electronic supplementary material", "supplementary data",
                "supplementary figures", "supplementary tables",
                "supplementary methods", "supplementary notes",
                "supporting text", "supporting figures", "si appendix",
                "appendix s1", "supplementary appendix",
            )
            if title.lower().lstrip().startswith(si_title_prefixes):
                print(f"  [SKIP] Supporting information, not the article: '{title[:60]}'")
                self._log_skipped_pdf(
                    pdf_path, f"supporting_information: {title.lower()[:60]}"
                )
                return None

            # Skip papers with sparse metadata + short title (likely non-research)
            abstract = grobid_data.get("abstract", "")
            authors = grobid_data.get("authors", [])
            if not abstract and not authors and len(title) < 30:
                print(f"  [SKIP] Sparse metadata + short title: '{title}'")
                self._log_skipped_pdf(pdf_path, "sparse_metadata_short_title")
                return None

            # §28: contributor sidecar was loaded up-front (Stage 1.5)
            # so the filename_doi_hint could feed the override branch.
            contributor = contributor_sidecar
            if contributor and contributor.get("email"):
                print(
                    f"  [INFO] Contributor attribution: "
                    f"{contributor.get('display_name') or contributor['email']} "
                    f"({contributor.get('group_slug') or 'unknown group'})"
                )

            # Classify the ingest path so audit/remediation can split
            # by source. A contributor sidecar means this came through
            # the upload endpoint; a `doi_*.pdf` filename means the
            # crawler (or a manual DOI-named drop) placed it.
            if contributor and contributor.get("email"):
                ingest_source = "upload"
            elif filename_doi:
                ingest_source = "crawler"
            else:
                ingest_source = "unknown"

            paper = Paper(
                id=paper_id,
                title=title,
                abstract=abstract,
                authors=authors,
                doi=doi,
                year=grobid_data.get("year"),
                journal=grobid_data.get("journal"),
                references=grobid_data.get("references", []),
                pdf_path=pdf_path,
                processed_at=datetime.now().isoformat(),
                contributor=contributor,
                grobid_title=grobid_title_raw,
                grobid_doi=grobid_doi_raw,
                ingest_source=ingest_source,
                # _merge_metadata stashes the rejected Crossref evidence
                # on the dict when the title-similarity guard fires.
                crossref_doi_rejected=grobid_data.get("_crossref_doi_rejected"),
                crossref_title_rejected=grobid_data.get("_crossref_title_rejected"),
                doi_recovered_by_title=grobid_data.get("_doi_recovered_by_title"),
            )

            # Step 3.5: refuse to overwrite a different paper. Runs before
            # BOTH stores, so a rejected paper touches neither Qdrant nor the
            # Neo4j graph -- returning None routes it to quarantine with the
            # PDF preserved. See UPLOAD-INGEST-REPAIR-PLAN.md defect 2.
            collision = self._collision_reason(paper)
            if collision:
                print(f"  [SKIP] {collision}")
                self._log_skipped_pdf(pdf_path, collision)
                return None

            # Step 4: Store in databases
            if self.qdrant and self.embedder:
                print("  [3/4] Generating embeddings and storing in Qdrant...")
                self._store_vectors(paper)
                print("        Stored in vector database")
            else:
                print("  [3/4] Skipping Qdrant (not configured)")

            if self.neo4j:
                print("  [4/4] Storing in Neo4j graph...")
                self._store_graph(paper)
                print("        Stored in graph database")
            else:
                print("  [4/4] Skipping Neo4j (not configured)")

            print(f"\n[OK] Successfully processed: {paper.title[:50]}...")
            return paper

        except Exception as e:
            print(f"  [ERROR] Processing failed: {e}")
            import traceback
            traceback.print_exc()
            self._last_skip_reason = f"pipeline_error: {type(e).__name__}: {e}"
            return None

    def _parse_grobid(self, pdf_path: str, max_retries: int = 5):
        """
        Parse PDF using GROBID with retry logic for transient errors.

        Returns:
            - Dict with parsed data on success
            - NO_TEXT_CONTENT string if PDF has no extractable text (scanned/image-only)
            - None on other failures
        """
        filename = Path(pdf_path).name

        # If we've had many consecutive failures, check GROBID health first
        if self._grobid_consecutive_failures >= 3:
            print("        Multiple consecutive GROBID failures, checking health...")
            if not self._check_grobid_health(max_wait=120):
                print("        GROBID is not responding. Please check the container.")
                return None
            self._grobid_consecutive_failures = 0

        for attempt in range(max_retries):
            try:
                with open(pdf_path, "rb") as f:
                    response = requests.post(
                        f"{self.grobid_url}/api/processFulltextDocument",
                        files={"input": f},
                        data={"consolidateHeader": "1", "consolidateCitations": "2"},
                        timeout=180  # Increased timeout for large PDFs
                    )

                if response.status_code == 200:
                    self._grobid_consecutive_failures = 0  # Reset on success
                    return self._parse_tei_xml(response.text)
                elif response.status_code == 500:
                    # Check if this is the NO_BLOCKS error (scanned PDF with no text)
                    # GROBID returns 500 with "NO_BLOCKS" in the response for image-only PDFs
                    response_text = response.text.lower() if response.text else ""
                    if "no_blocks" in response_text or "empty content" in response_text:
                        print("        PDF has no extractable text (likely scanned)")
                        return NO_TEXT_CONTENT
                    else:
                        print(f"        GROBID returned status 500 (internal error)")
                        return None
                elif response.status_code == 503:
                    # Service overloaded - wait and retry
                    wait_time = (2 ** attempt) * 2  # 2, 4, 8 seconds
                    if attempt < max_retries - 1:
                        print(f"        GROBID busy (503), retrying in {wait_time}s... ({attempt + 1}/{max_retries})")
                        time.sleep(wait_time)
                        continue
                    else:
                        print(f"        GROBID still busy after {max_retries} retries")
                        return None
                else:
                    print(f"        GROBID returned status {response.status_code}")
                    return None

            except requests.exceptions.Timeout:
                wait_time = (2 ** attempt) * 2
                if attempt < max_retries - 1:
                    print(f"        GROBID timeout, retrying in {wait_time}s... ({attempt + 1}/{max_retries})")
                    time.sleep(wait_time)
                    continue
                else:
                    print(f"        GROBID timeout after {max_retries} retries")
                    return None
            except requests.exceptions.ConnectionError:
                wait_time = (2 ** attempt) * 2
                if attempt < max_retries - 1:
                    print(f"        GROBID connection refused, retrying in {wait_time}s... ({attempt + 1}/{max_retries})")
                    time.sleep(wait_time)
                    continue
                else:
                    print("        Cannot connect to GROBID after retries. Is it running?")
                    print(f"        Check: curl {self.grobid_url}/api/isalive")
                    self._grobid_consecutive_failures += 1
                    return None
            except Exception as e:
                print(f"        GROBID error: {e}")
                self._grobid_consecutive_failures += 1
                return None

        self._grobid_consecutive_failures += 1
        return None

    def _parse_tei_xml(self, xml_text: str) -> Dict:
        """Parse GROBID TEI-XML output"""
        ns = {'tei': 'http://www.tei-c.org/ns/1.0'}
        result = {
            "title": "",
            "abstract": "",
            "authors": [],
            "doi": None,
            "year": None,
            "journal": None,
            "references": []
        }

        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return result

        # Title
        title = root.find('.//tei:titleStmt/tei:title', ns)
        if title is not None and title.text:
            result["title"] = title.text.strip()

        # Abstract
        abstract = root.find('.//tei:abstract', ns)
        if abstract is not None:
            result["abstract"] = ' '.join(abstract.itertext()).strip()

        # Authors
        for author in root.findall('.//tei:fileDesc//tei:author', ns):
            persname = author.find('tei:persName', ns)
            if persname is not None:
                forename = persname.find('tei:forename', ns)
                surname = persname.find('tei:surname', ns)
                name = ' '.join(filter(None, [
                    forename.text if forename is not None else None,
                    surname.text if surname is not None else None
                ]))
                if name:
                    result["authors"].append({"name": name})

        # DOI
        for idno in root.findall('.//tei:idno', ns):
            if idno.get('type') == 'DOI' and idno.text:
                result["doi"] = idno.text.strip()
                break

        # Year
        date = root.find('.//tei:publicationStmt/tei:date', ns)
        if date is not None and date.get('when'):
            try:
                result["year"] = int(date.get('when')[:4])
            except (ValueError, TypeError):
                pass

        # References (DOIs only for now)
        for ref in root.findall('.//tei:listBibl/tei:biblStruct', ns):
            ref_doi = ref.find('.//tei:idno[@type="DOI"]', ns)
            if ref_doi is not None and ref_doi.text:
                result["references"].append(ref_doi.text.strip())

        return result

    def _fetch_crossref(self, doi: str) -> Optional[Dict]:
        """Fetch metadata from CrossRef"""
        try:
            url = f"https://api.crossref.org/works/{doi}"
            headers = {"User-Agent": f"MuninCluster/1.0 (mailto:{ADMIN_EMAIL})"}
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                return response.json().get("message", {})
        except Exception:
            pass
        return None

    def _fetch_crossref_by_title(
        self, title: str, authors: Optional[List] = None
    ) -> Optional[Dict]:
        """Find a paper's real DOI from its title when the extracted DOI is
        not trustworthy.

        GROBID sometimes reads a DOI out of the reference list rather than the
        header. Looking that DOI up returns a real, well-formed record for
        the WRONG paper, which is far more damaging than having no DOI at all
        (the DOI keys the Qdrant point and the Neo4j node, and names the file
        on disk). So when the title guard rejects a DOI we come back here and
        search by what the PDF actually says.

        Accepts a candidate only when its title clears
        `_RECOVERY_TITLE_SIM_THRESHOLD`, which is deliberately much stricter
        than the 0.3 rejection bar: we are searching Crossref BY this title,
        so a genuine match scores near 1.0, and anything marginal is the
        search engine reaching. Returns the Crossref record, or None to leave
        the paper DOI-less (and therefore quarantined) rather than guess.
        """
        if not title or not title.strip():
            return None
        try:
            params = {
                "query.bibliographic": title.strip()[:512],
                "rows": 5,
                "select": "DOI,title,container-title,published-print,author",
            }
            # A first-author surname measurably sharpens Crossref's ranking on
            # short or generic titles.
            first_author = ""
            for a in (authors or []):
                name = a.get("name", "") if isinstance(a, dict) else str(a)
                if name.strip():
                    first_author = name.strip().split()[-1]
                    break
            if first_author:
                params["query.author"] = first_author
            headers = {"User-Agent": f"MuninCluster/1.0 (mailto:{ADMIN_EMAIL})"}
            response = requests.get(
                "https://api.crossref.org/works",
                params=params, headers=headers, timeout=15,
            )
            if response.status_code != 200:
                return None
            items = response.json().get("message", {}).get("items", []) or []
        except Exception as e:
            print(f"  [WARN] Crossref title search failed: {e}")
            return None

        best, best_sim = None, 0.0
        for item in items:
            cand = item.get("title")
            if isinstance(cand, list):
                cand = cand[0] if cand else ""
            sim = _title_similarity(title, cand or "")
            if sim > best_sim:
                best, best_sim = item, sim
        if best is None or best_sim < self._RECOVERY_TITLE_SIM_THRESHOLD:
            print(
                f"  [WARN] Crossref title search found no confident match "
                f"(best sim={best_sim:.2f} < "
                f"{self._RECOVERY_TITLE_SIM_THRESHOLD}); leaving DOI unset"
            )
            return None
        print(f"  [OK] Recovered DOI by title: {best.get('DOI')} (sim={best_sim:.2f})")
        return best

    def _fetch_openalex(self, doi: str) -> Optional[Dict]:
        """
        Fetch metadata from OpenAlex API.

        OpenAlex provides richer metadata than CrossRef including:
        - Page counts (biblio.first_page, biblio.last_page)
        - Abstract availability (abstract_inverted_index)
        - Citation counts (cited_by_count)
        - Reference counts (referenced_works)
        - Retraction status (is_retracted)
        - Document type
        """
        api_key = os.getenv("OPENALEX_API_KEY", "")
        try:
            url = f"https://api.openalex.org/works/https://doi.org/{doi}"
            params = {"api_key": api_key} if api_key else {}
            headers = {"User-Agent": f"MuninCluster/1.0 (mailto:{ADMIN_EMAIL})"}
            response = requests.get(url, params=params, headers=headers, timeout=15)
            if response.status_code == 200:
                return response.json()
        except Exception:
            pass
        return None

    def _pdf_page_count(self, pdf_path: str) -> Optional[int]:
        """Actual page count of the PDF, or None if it cannot be determined.

        The quality filter used to infer this from OpenAlex `biblio`
        (last_page - first_page + 1), which silently discards any paper
        published with an ARTICLE NUMBER rather than a page range: eLife,
        PLoS, Scientific Reports, Nature Communications and modern JBC all
        report first_page == last_page == the article id, giving a page count
        of 1. The 2026-08-23 restore quarantined 74 real papers this way
        (median length 13 pages, longest 42) before anyone noticed, because
        "only 1 page" reads like a plausible rejection.

        pypdf first since it is already a hard dependency of _validate_pdf;
        pdfinfo as a fallback.
        """
        try:
            from pypdf import PdfReader
            return len(PdfReader(pdf_path).pages)
        except Exception:
            pass
        try:
            out = subprocess.run(
                ["pdfinfo", pdf_path], capture_output=True, timeout=15, text=True
            )
            if out.returncode == 0:
                for line in out.stdout.splitlines():
                    if line.startswith("Pages:"):
                        return int(line.split()[-1])
        except Exception:
            pass
        return None

    def _check_quality_filters(self, doi: str, grobid_data: Dict, pdf_path: str) -> tuple[bool, str]:
        """
        Check paper against quality filters using OpenAlex metadata.

        Returns:
            Tuple of (passes_filter, reason)
            - passes_filter: True if paper passes all quality checks
            - reason: Description of why paper was filtered (empty if passes)
        """
        if not doi:
            return True, ""  # Can't check without DOI

        openalex_data = self._fetch_openalex(doi)
        if not openalex_data:
            return True, ""  # Can't check, allow through

        # Check if retracted
        if openalex_data.get("is_retracted"):
            return False, "Paper has been retracted"

        # Check page count. The PDF in hand is the authority; OpenAlex
        # `biblio` is only consulted when the file cannot be counted, and even
        # then an article-number record (first_page == last_page) is treated as
        # "unknown" rather than "one page". See _pdf_page_count.
        actual_pages = self._pdf_page_count(pdf_path)
        if actual_pages is not None:
            if actual_pages < MIN_PAGE_COUNT:
                return False, (
                    f"Only {actual_pages} page(s) in the PDF "
                    f"(minimum: {MIN_PAGE_COUNT})"
                )
        else:
            biblio = openalex_data.get("biblio", {})
            first_page = biblio.get("first_page")
            last_page = biblio.get("last_page")
            if first_page and last_page and str(first_page) != str(last_page):
                try:
                    page_count = int(last_page) - int(first_page) + 1
                    if page_count < MIN_PAGE_COUNT:
                        return False, (
                            f"Only {page_count} page(s) per OpenAlex biblio "
                            f"(minimum: {MIN_PAGE_COUNT}; PDF was uncountable)"
                        )
                except ValueError:
                    pass  # Non-numeric pages (e.g. roman numerals), allow through

        # Check abstract OR references requirement
        if REQUIRE_ABSTRACT_OR_REFS:
            # Check from GROBID data first
            has_abstract = bool(grobid_data.get("abstract"))
            has_refs = bool(grobid_data.get("references"))

            # Also check OpenAlex data
            if not has_abstract:
                has_abstract = bool(openalex_data.get("abstract_inverted_index"))
            if not has_refs:
                has_refs = len(openalex_data.get("referenced_works", [])) > 0

            if not has_abstract and not has_refs:
                return False, "No abstract and no references"

        return True, ""

    # Token-set Jaccard threshold below which we reject a Crossref
    # enrichment as describing a different paper than GROBID parsed.
    # Calibrated 2026-05-12 against 5 known-bad records (all 0.000) vs
    # 18 random samples (0.818-1.000); see docs/PAPER-INGEST-AUDIT.md.
    _MERGE_TITLE_SIM_THRESHOLD = 0.3
    # Accepting a DOI recovered by title search is a much stronger claim than
    # rejecting one, so it needs a much stronger match. We query Crossref with
    # the paper's own title, so the right record comes back near 1.0; 0.6
    # leaves room for markup and OCR drift while refusing near-misses.
    _RECOVERY_TITLE_SIM_THRESHOLD = 0.6

    def _merge_metadata(self, grobid: Dict, crossref: Dict) -> Dict:
        """Merge CrossRef metadata into GROBID data, guarded by a
        title-similarity check.

        If GROBID parsed a real title from the PDF and Crossref came
        back with a title that doesn't match (e.g. GROBID extracted a
        citation DOI by mistake, or Sci-Hub returned the wrong PDF),
        we keep GROBID metadata and drop the Crossref enrichment. The
        rejected Crossref title is preserved on the payload for the
        remediation tool to surface.
        """
        crossref_title = crossref.get("title")
        if isinstance(crossref_title, list):
            crossref_title = crossref_title[0] if crossref_title else ""
        grobid_title = grobid.get("title") or ""

        if grobid_title and crossref_title:
            sim = _title_similarity(grobid_title, crossref_title)
            if sim < self._MERGE_TITLE_SIM_THRESHOLD:
                print(
                    f"  [WARN] GROBID/Crossref title mismatch "
                    f"(sim={sim:.2f} < {self._MERGE_TITLE_SIM_THRESHOLD}); "
                    f"dropping Crossref enrichment AND the DOI"
                )
                print(f"           grobid : {grobid_title[:90]}")
                print(f"           crossref: {crossref_title[:90]}")
                # Preserve the rejected Crossref evidence so the
                # remediation tool can flag this paper later. Audit
                # fields are written through to the Qdrant payload by
                # _store_vectors (Stage 1.4).
                grobid["_crossref_doi_rejected"] = grobid.get("doi")
                grobid["_crossref_title_rejected"] = crossref_title
                # Clear the DOI (2026-08-18). Until then this branch kept it,
                # on the reasoning that only the ENRICHMENT was suspect. But a
                # DOI whose Crossref record describes a different paper is
                # itself the bad datum, and it is load-bearing: it keys the
                # Qdrant point and the Neo4j node and names the PDF on disk.
                # Keeping it meant an upload silently overwrote the cited
                # paper's record and left its own PDF under the wrong name --
                # 61 of one contributor's papers served a different paper's
                # PDF this way. The caller retries via title search; if that
                # fails the paper stays DOI-less and is quarantined, which is
                # recoverable. See UPLOAD-INGEST-REPAIR-PLAN.md defect 2.
                grobid["doi"] = None
                return grobid

        if crossref_title:
            # Strip JATS/HTML so <b>...</b>, <mml:math>, &amp;, etc.
            # don't reach Qdrant / the UI. The pre-strip raw value is
            # already captured in any rejection branch above; here we
            # only run when the titles match, so stripping is safe.
            grobid["title"] = _strip_markup(crossref_title)

        if crossref.get("container-title"):
            grobid["journal"] = _strip_markup(crossref["container-title"][0])

        if crossref.get("published-print", {}).get("date-parts"):
            try:
                grobid["year"] = crossref["published-print"]["date-parts"][0][0]
            except (IndexError, TypeError):
                pass

        # Authors. Until 2026-08 this merge enriched title/journal/year and
        # left authors as whatever GROBID scraped off the PDF text layer,
        # which is why 9.2% of papers_bge carried names like 'R Tait1',
        # 'Walfram Tet~laff~~' or 'Theodor-Kocher Institute', and another
        # 8.5% carried none. We only get here when the title-similarity guard
        # above passed, so the Crossref record is known to describe THIS
        # paper and its author list is safe to consider.
        #
        # best_author_list picks the more informative of the two rather than
        # always trusting Crossref: Crossref frequently gives initials
        # ("D C Leitman") where the PDF gave the full name ("Dale Leitman"),
        # but Crossref wins when GROBID dropped or mangled entries.
        crossref_authors = [
            f"{a.get('given', '')} {a.get('family', '')}".strip()
            for a in (crossref.get("author") or [])
            if a.get("family") or a.get("name")
        ] or [
            a.get("name", "") for a in (crossref.get("author") or [])
        ]
        merged_authors = best_author_list(grobid.get("authors"), crossref_authors)
        if merged_authors:
            grobid["authors"] = [{"name": n} for n in merged_authors]

        return grobid

    def _store_vectors(self, paper: Paper):
        """Store paper embeddings in Qdrant.

        §28: when `paper.contributor` is set, we preserve any existing
        `contributors` list on the point (paper may have been uploaded by
        a different group earlier) and append the new entry dedup'd by
        email. The §15 cluster_id/topic_label/topic_slug fields are also
        preserved — they're set by `build_embedding_map.py`, which runs
        on a different schedule and would otherwise be clobbered by this
        upsert.
        """
        from qdrant_client.models import PointStruct

        # Generate embedding for title + abstract
        main_text = f"{paper.title}\n\n{paper.abstract}"
        embedding = self.embedder.encode(main_text).tolist()

        # §28: key the Qdrant point on the DOI when known so multiple
        # uploads of the same paper share one point (contributors merge).
        # DOI-less papers fall back to the original pdf_path-derived ID.
        # Neo4j already keys on DOI, so this brings the two stores into
        # alignment.
        if paper.doi:
            point_id = int(
                hashlib.sha256(paper.doi.lower().encode()).hexdigest()[:16], 16
            )
        else:
            point_id = int(paper.id, 16)

        # Preserve fields from any existing point that upsert would wipe.
        existing_contributors, existing_cluster = self._fetch_preserved_fields(point_id)
        merged_contributors = self._merge_contributors(
            existing_contributors, paper.contributor
        )

        payload = {
            "paper_id": paper.id,  # Keep original hex ID in payload
            "title": paper.title,
            "abstract": paper.abstract[:2000],
            "doi": paper.doi,
            "year": paper.year,
            "authors": [a.get("name") for a in paper.authors],
            "journal": paper.journal,
            "pdf_path": paper.pdf_path,
        }
        if merged_contributors:
            payload["contributors"] = merged_contributors
        # Audit fields (Stage 1.4) — only write when set, so we don't
        # litter the payload with explicit nulls. Leading-underscore
        # convention signals "internal, set by the pipeline, not for
        # direct user-facing display".
        if paper.grobid_title:
            payload["_grobid_title"] = paper.grobid_title
        if paper.grobid_doi:
            payload["_grobid_doi"] = paper.grobid_doi
        if paper.ingest_source:
            payload["_ingest_source"] = paper.ingest_source
        if paper.processed_at:
            payload["_ingest_at"] = paper.processed_at
        if paper.crossref_doi_rejected:
            payload["_crossref_doi_rejected"] = paper.crossref_doi_rejected
        if paper.crossref_title_rejected:
            payload["_crossref_title_rejected"] = paper.crossref_title_rejected
        if paper.doi_recovered_by_title:
            payload["_doi_recovered_by_title"] = paper.doi_recovered_by_title
        # §15 clustering fields — only write back if they were already set
        # by the nightly embedding map; otherwise leave absent so we don't
        # pre-populate nonsense.
        if existing_cluster:
            payload.update(existing_cluster)

        self.qdrant.upsert(
            collection_name=COLLECTION_NAME,
            points=[PointStruct(
                id=point_id,
                vector=embedding,
                payload=payload,
            )]
        )

    def _existing_point_title(self, doi: str) -> Optional[str]:
        """Title already stored under this DOI, or None if the DOI is new.

        Same point-id derivation as `_store_vectors`, so the answer is
        exactly "what would this upsert land on".
        """
        if not doi or not self.qdrant:
            return None
        point_id = int(hashlib.sha256(doi.lower().encode()).hexdigest()[:16], 16)
        try:
            records = self.qdrant.retrieve(
                collection_name=COLLECTION_NAME, ids=[point_id],
                with_payload=True, with_vectors=False,
            )
        except Exception as e:
            print(f"        [WARN] collision pre-check retrieve failed: {e}")
            return None
        if not records:
            return None
        return (records[0].payload or {}).get("title")

    def _collision_reason(self, paper: "Paper") -> Optional[str]:
        """Reject an ingest that would overwrite a DIFFERENT paper's record.

        The backstop for defect 2. Even with the DOI guard in
        `_merge_metadata`, a wrong DOI can still reach this point: Crossref
        may be unreachable so no title check ran at all, or the DOI may carry
        no Crossref record while still colliding with something in our corpus.
        Because the point id is derived from the DOI, an upsert in that state
        does not add a paper, it destroys one -- and the crawler's PDF for the
        real paper keeps the filename, so the damage is invisible from the
        payload alone.

        Refusing is cheap and reversible: the paper is quarantined with its
        PDF intact for `paper_cleanup.py review`. Silently overwriting is
        neither. So this refuses regardless of who contributed the stored
        record, including the same uploader -- one contributor's batch
        mis-attributed to a single repeated citation DOI would otherwise
        overwrite itself paper by paper.

        Returns a quarantine reason, or None when the write is safe.
        """
        if not paper.doi:
            return None
        existing_title = self._existing_point_title(paper.doi)
        if not existing_title or not paper.title:
            return None
        sim = _title_similarity(paper.title, existing_title)
        if sim >= self._MERGE_TITLE_SIM_THRESHOLD:
            return None
        print(f"  [WARN] DOI {paper.doi} already holds a different paper "
              f"(sim={sim:.2f} < {self._MERGE_TITLE_SIM_THRESHOLD})")
        print(f"           incoming: {paper.title[:90]}")
        print(f"           stored  : {existing_title[:90]}")
        return (
            f"doi_collision_different_paper: {paper.doi} holds "
            f"{existing_title[:80]!r}"
        )

    def _fetch_preserved_fields(self, point_id: int) -> Tuple[List[Dict], Dict]:
        """Return (contributors_list, cluster_fields) for the existing point
        at `point_id`, or ([], {}) if none. Used to avoid clobbering fields
        that live on paper payloads but aren't set by this pipeline."""
        try:
            records = self.qdrant.retrieve(
                collection_name=COLLECTION_NAME,
                ids=[point_id],
                with_payload=True,
                with_vectors=False,
            )
        except Exception as e:
            print(f"        [WARN] Qdrant retrieve failed, treating as new point: {e}")
            return [], {}
        if not records:
            return [], {}
        payload = records[0].payload or {}
        contributors = payload.get("contributors") or []
        cluster_fields = {
            k: payload[k]
            for k in ("cluster_id", "topic_label", "topic_slug")
            if k in payload
        }
        return list(contributors), cluster_fields

    @staticmethod
    def _merge_contributors(
        existing: List[Dict], new: Optional[Dict]
    ) -> List[Dict]:
        """Append `new` to `existing` dedup'd by `email`. A second upload
        by the same email just refreshes its entry (latest upload_time
        wins). Contributors without email (malformed sidecar) are dropped."""
        if not new or not new.get("email"):
            return existing or []
        email = new["email"].strip().lower()
        out = [c for c in (existing or []) if (c.get("email") or "").lower() != email]
        out.append({
            "email": email,
            "username": new.get("username"),
            "display_name": new.get("display_name"),
            "group_slug": new.get("group_slug") or "unknown",
            "group_display_name": new.get("group_display_name"),
            "upload_time": new.get("upload_time"),
        })
        return out

    def _store_graph(self, paper: Paper):
        """Store paper and relationships in Neo4j"""
        # Key every node on the canonical (lowercased) DOI. Qdrant already
        # does this; Neo4j did not, which silently split 13,053 papers into
        # two nodes each holding half the graph. See _norm_doi.
        doi_key = _norm_doi(paper.doi)
        with self.neo4j.session() as session:
            # Create/update paper node
            # First check if a stub node exists with this DOI (created as citation target)
            # If so, update it with the full paper info; otherwise create new
            if doi_key:
                session.run("""
                    MERGE (p:Paper {doi: $doi})
                    SET p.doi_key = $doi,
                        p.paper_id = $paper_id,
                        p.title = $title,
                        p.year = $year,
                        p.journal = $journal,
                        p.abstract = $abstract
                """, doi=doi_key, paper_id=paper.id, title=paper.title,
                    year=paper.year, journal=paper.journal,
                    abstract=paper.abstract[:1000])
            else:
                # No DOI - use paper_id as the merge key
                session.run("""
                    MERGE (p:Paper {paper_id: $paper_id})
                    SET p.title = $title,
                        p.year = $year,
                        p.journal = $journal,
                        p.abstract = $abstract
                """, paper_id=paper.id, title=paper.title,
                    year=paper.year, journal=paper.journal,
                    abstract=paper.abstract[:1000])

            # Create author nodes and relationships
            for author in paper.authors:
                name = author.get("name", "").strip()
                if name:
                    author_id = hashlib.sha256(name.lower().encode()).hexdigest()[:16]
                    # Match paper by DOI if available, otherwise by paper_id
                    if doi_key:
                        session.run("""
                            MERGE (a:Author {author_id: $aid})
                            SET a.name = $name
                            WITH a
                            MATCH (p:Paper {doi: $doi})
                            MERGE (a)-[:AUTHORED]->(p)
                        """, aid=author_id, name=name, doi=doi_key)
                    else:
                        session.run("""
                            MERGE (a:Author {author_id: $aid})
                            SET a.name = $name
                            WITH a
                            MATCH (p:Paper {paper_id: $pid})
                            MERGE (a)-[:AUTHORED]->(p)
                        """, aid=author_id, name=name, pid=paper.id)

            # Create citation relationships
            for ref_doi in paper.references:
                if doi_key:
                    session.run("""
                        MATCH (p:Paper {doi: $doi})
                        MERGE (cited:Paper {doi: $ref})
                        SET cited.doi_key = $ref
                        MERGE (p)-[:CITES]->(cited)
                    """, doi=doi_key, ref=_norm_doi(ref_doi))
                else:
                    session.run("""
                        MATCH (p:Paper {paper_id: $pid})
                        MERGE (cited:Paper {doi: $ref})
                        SET cited.doi_key = $ref
                        MERGE (p)-[:CITES]->(cited)
                    """, pid=paper.id, ref=_norm_doi(ref_doi))

            # §28: contributor attribution. If a sidecar was present,
            # upsert the :Contributor node and link it to this paper.
            # CONTRIBUTED relationships are keyed by (contributor, paper)
            # via MERGE, so re-ingesting the same PDF from the same
            # uploader is idempotent (only upload_time is refreshed).
            if paper.contributor and paper.contributor.get("email"):
                c = paper.contributor
                if doi_key:
                    session.run("""
                        MERGE (co:Contributor {email: $email})
                        SET co.username = coalesce($username, co.username),
                            co.display_name = coalesce($display_name, co.display_name),
                            co.group_slug = coalesce($group_slug, co.group_slug),
                            co.group_display_name = coalesce($group_display_name, co.group_display_name)
                        WITH co
                        MATCH (p:Paper {doi: $doi})
                        MERGE (co)-[r:CONTRIBUTED]->(p)
                        SET r.upload_time = $upload_time
                    """,
                        email=c["email"],
                        username=c.get("username"),
                        display_name=c.get("display_name"),
                        group_slug=c.get("group_slug") or "unknown",
                        group_display_name=c.get("group_display_name"),
                        doi=doi_key,
                        upload_time=c.get("upload_time"),
                    )
                else:
                    session.run("""
                        MERGE (co:Contributor {email: $email})
                        SET co.username = coalesce($username, co.username),
                            co.display_name = coalesce($display_name, co.display_name),
                            co.group_slug = coalesce($group_slug, co.group_slug),
                            co.group_display_name = coalesce($group_display_name, co.group_display_name)
                        WITH co
                        MATCH (p:Paper {paper_id: $pid})
                        MERGE (co)-[r:CONTRIBUTED]->(p)
                        SET r.upload_time = $upload_time
                    """,
                        email=c["email"],
                        username=c.get("username"),
                        display_name=c.get("display_name"),
                        group_slug=c.get("group_slug") or "unknown",
                        group_display_name=c.get("group_display_name"),
                        pid=paper.id,
                        upload_time=c.get("upload_time"),
                    )

    def _process_single_grobid(self, pdf_path: str, delay: float = 0.5) -> Tuple[str, Optional[Dict]]:
        """Process a single PDF with GROBID (for parallel processing)"""
        filename = Path(pdf_path).name
        try:
            # Validate PDF first
            is_valid, error_msg = self._validate_pdf(pdf_path)
            if not is_valid:
                print(f"  [SKIP] {filename}: {error_msg}")
                return (pdf_path, None)

            # Small delay to avoid overwhelming GROBID
            time.sleep(delay)
            grobid_data = self._parse_grobid(pdf_path)
            return (pdf_path, grobid_data)
        except Exception as e:
            print(f"  [ERROR] GROBID failed for {filename}: {e}")
            return (pdf_path, None)

    def process_batch(self, pdf_paths: List[str]) -> List[Paper]:
        """
        Process multiple PDFs in batch mode for speed.
        Uses parallel GROBID processing and batch embeddings.
        """
        if not pdf_paths:
            return []

        print(f"\n{'=' * 60}")
        print(f"FAST MODE: Processing {len(pdf_paths)} PDFs in batch")
        print(f"Workers: {self.workers}")
        print(f"{'=' * 60}")

        papers = []
        grobid_results = {}

        # Step 1: Parallel GROBID processing
        print(f"\n[1/3] Parsing {len(pdf_paths)} PDFs with GROBID (parallel)...")
        with ThreadPoolExecutor(max_workers=self.workers) as executor:
            futures = {executor.submit(self._process_single_grobid, p): p for p in pdf_paths}
            for i, future in enumerate(as_completed(futures)):
                pdf_path, grobid_data = future.result()
                if grobid_data:
                    grobid_results[pdf_path] = grobid_data
                if (i + 1) % 10 == 0:
                    print(f"        Parsed {i + 1}/{len(pdf_paths)} PDFs...")

        print(f"        Successfully parsed: {len(grobid_results)}/{len(pdf_paths)}")

        # Step 2: Enrich with CrossRef and create Paper objects
        print(f"\n[2/3] Enriching metadata and creating embeddings...")
        texts_to_embed = []
        papers_to_store = []

        for pdf_path, grobid_data in grobid_results.items():
            filename = Path(pdf_path).name

            # Extract DOI from filename (ambiguous names only once verified)
            filename_doi = self._extract_doi_from_filename(filename)
            if not filename_doi and filename.startswith("doi_"):
                filename_doi = self._verified_filename_doi(
                    filename, (grobid_data.get("title") or "").strip())
            if filename_doi:
                grobid_doi = grobid_data.get("doi")
                if grobid_doi and grobid_doi != filename_doi:
                    pass  # Silently use filename DOI in batch mode
                grobid_data["doi"] = filename_doi

            # Enrich with CrossRef
            doi = grobid_data.get("doi")
            if doi:
                crossref = self._fetch_crossref(doi)
                if crossref:
                    grobid_data = self._merge_metadata(grobid_data, crossref)

            # Quality filter check via OpenAlex
            if doi:
                passes_filter, filter_reason = self._check_quality_filters(doi, grobid_data, pdf_path)
                if not passes_filter:
                    print(f"        Skipping {filename}: {filter_reason}")
                    continue

            # Create Paper object
            paper_id = hashlib.sha256(pdf_path.encode()).hexdigest()[:16]

            # Validate title - skip papers with empty titles
            title = grobid_data.get("title", "").strip()
            if not title:
                print(f"        Skipping {filename}: empty title (metadata extraction failed)")
                continue

            # Skip non-research content (news articles, editorials, etc.)
            non_research_titles = [
                "news and views", "editorial", "erratum", "correction", "retraction",
                "graphical abstract", "notes and references", "cover picture",
                "table of contents", "advertisement", "book review", "corrigendum",
                "front matter", "back matter", "index", "contents", "author index",
                "subject index", "acknowledgements", "preface", "foreword",
                "notes for notes", "in this issue", "issue information", "masthead",
            ]
            if title.lower() in non_research_titles:
                print(f"        Skipping {filename}: non-research content ('{title}')")
                continue

            # Skip papers with sparse metadata + short title (likely non-research)
            abstract = grobid_data.get("abstract", "")
            authors = grobid_data.get("authors", [])
            if not abstract and not authors and len(title) < 30:
                print(f"        Skipping {filename}: sparse metadata + short title ('{title}')")
                continue

            paper = Paper(
                id=paper_id,
                title=title,
                abstract=abstract,
                authors=authors,
                doi=doi,
                year=grobid_data.get("year"),
                journal=grobid_data.get("journal"),
                references=grobid_data.get("references", []),
                pdf_path=pdf_path,
                processed_at=datetime.now().isoformat(),
                contributor=_load_contributor_sidecar(pdf_path),
            )
            papers_to_store.append(paper)
            texts_to_embed.append(f"{paper.title}\n\n{paper.abstract}")

        # Step 3: Batch embeddings and storage
        if self.qdrant and self.embedder and papers_to_store:
            print(f"        Generating embeddings for {len(papers_to_store)} papers (batch)...")
            embeddings = self.embedder.encode(texts_to_embed, batch_size=32, show_progress_bar=True)

            print(f"        Storing in Qdrant...")
            from qdrant_client.models import PointStruct
            points = []
            for paper, embedding in zip(papers_to_store, embeddings):
                # §28 DOI-keyed point IDs (see _store_vectors). Fast mode
                # does not preserve contributors / cluster fields — it's
                # intended for bulk crawler reprocessing where those fields
                # aren't yet present.
                if paper.doi:
                    point_id = int(
                        hashlib.sha256(paper.doi.lower().encode()).hexdigest()[:16],
                        16,
                    )
                else:
                    point_id = int(paper.id, 16)
                points.append(PointStruct(
                    id=point_id,
                    vector=embedding.tolist(),
                    payload={
                        "paper_id": paper.id,
                        "title": paper.title,
                        "abstract": paper.abstract[:2000],
                        "doi": paper.doi,
                        "year": paper.year,
                        "authors": [a.get("name") for a in paper.authors],
                        "journal": paper.journal,
                        "pdf_path": paper.pdf_path
                    }
                ))
            # Batch upsert
            self.qdrant.upsert(collection_name=COLLECTION_NAME, points=points)
            print(f"        Stored {len(points)} vectors")

        # Store in Neo4j (still sequential due to transaction handling)
        if self.neo4j and papers_to_store:
            print(f"\n[3/3] Storing in Neo4j graph...")
            for i, paper in enumerate(papers_to_store):
                self._store_graph(paper)
                if (i + 1) % 50 == 0:
                    print(f"        Stored {i + 1}/{len(papers_to_store)} papers...")
            print(f"        Stored {len(papers_to_store)} papers in graph")

        papers = papers_to_store
        print(f"\n[OK] Batch processing complete: {len(papers)} papers processed")
        return papers


def process_directory(
    pipeline: PaperPipeline,
    papers_dir: str,
    reprocess: bool = False,
    ingest_path: str = "manual",
):
    """Process all PDFs in a directory.

    `ingest_path` is stamped into every state sidecar and Qdrant payload this
    run produces. It used to be hardcoded to "manual", which made this
    function unusable for a bulk re-ingest: the 2026-08-23 restore of a
    contributor's 567 archived uploads had to drive `_process_and_dispose`
    directly to avoid labelling all of them as operator drops. Pass "upload"
    for contributor material, "crawler" for downloader output.

    Note this is provenance only. Contributor ATTRIBUTION comes from each
    PDF's `.contributor.json` sidecar and is unaffected by this argument.
    """
    papers_path = Path(papers_dir)
    processed_path = Path(PROCESSED_DIR)
    processed_path.mkdir(exist_ok=True)

    pdf_files = list(papers_path.glob("*.pdf"))
    print(f"Found {len(pdf_files)} PDF files")

    # Handle reprocessing
    if reprocess:
        print(f"\n[REPROCESS MODE] Clearing {len(pdf_files)} processed markers...")
        cleared = 0
        for pdf in pdf_files:
            marker = processed_path / f"{pdf.stem}.json"
            if marker.exists():
                marker.unlink()
                cleared += 1
        print(f"        Cleared {cleared} markers")

    # Collect PDFs to process
    pdfs_to_process = []
    for pdf in pdf_files:
        marker = processed_path / f"{pdf.stem}.json"
        if not marker.exists():
            pdfs_to_process.append(str(pdf))

    if not pdfs_to_process:
        print("\n[OK] All PDFs already processed. Use --reprocess to re-run.")
        return

    print(f"\nPDFs to process: {len(pdfs_to_process)}")

    # Phase B (2026-05-13): every per-PDF processing path now routes
    # through _process_and_dispose so the state sidecar + Qdrant
    # mirror are populated. Batch (fast) mode keeps its parallel
    # GROBID + embedding strategy but the post-batch handling
    # converges on dispose.
    if pipeline.fast_mode and len(pdfs_to_process) > 1:
        papers = pipeline.process_batch(pdfs_to_process)
        # process_batch returns only the successful Paper objects;
        # disposal still needs to fire so the sidecars get written.
        successful_paths = {Path(p.pdf_path).resolve() for p in papers}
        for paper in papers:
            _dispose_post_pipeline(
                pdf_path=paper.pdf_path,
                paper=paper,
                skip_reason=None,
                ingest_path=ingest_path,
                qdrant_client=pipeline.qdrant,
            )
        # PDFs the batch didn't return are skips; dispose them as
        # such so they leave the live directory.
        for pdf_path in pdfs_to_process:
            if Path(pdf_path).resolve() in successful_paths:
                continue
            _dispose_post_pipeline(
                pdf_path=pdf_path,
                paper=None,
                skip_reason="batch_skipped",
                ingest_path=ingest_path,
                qdrant_client=pipeline.qdrant,
            )
        processed = len(papers)
        failed = len(pdfs_to_process) - processed
    else:
        # Sequential processing
        processed = 0
        failed = 0
        for pdf_path in pdfs_to_process:
            result = _process_and_dispose(pipeline, pdf_path, ingest_path)
            if result["state"] == "live":
                processed += 1
            else:
                failed += 1

    print(f"\n{'=' * 60}")
    print(f"Processing complete: {processed} succeeded, {failed} failed")
    print(f"{'=' * 60}")


def watch_directory(pipeline: PaperPipeline, papers_dir: str):
    """Watch directory for new PDFs and process them.

    Poll interval is controlled by the ``WATCH_POLL_SECS`` env var
    (default 60 s). Keep it low for operator-drop responsiveness,
    high to avoid churn on a mostly-idle corpus.

    Phase B (2026-05-13): per-PDF processing now goes through
    `_process_and_dispose`, which calls `process_pdf` then writes
    the state sidecar + watcher marker + (on failure) moves the PDF
    to quarantine/. The watcher no longer writes the marker itself —
    that responsibility is in dispose. Failed PDFs leaving the live
    directory means the next poll doesn't re-attempt them.
    """
    poll_secs = max(1, int(os.getenv("WATCH_POLL_SECS", "60")))
    print(f"Watching {papers_dir} for new PDFs (poll every {poll_secs}s)...")
    print("Press Ctrl+C to stop")

    processed_path = Path(PROCESSED_DIR)
    processed_path.mkdir(exist_ok=True)

    while True:
        try:
            for pdf in Path(papers_dir).glob("*.pdf"):
                marker = processed_path / f"{pdf.stem}.json"
                if marker.exists():
                    continue
                # Files in inbox/ are admin/ingest's territory; the
                # glob above is non-recursive so this guard is
                # informational only — it's here for defence in
                # depth if PAPERS_DIR ever ends up containing inbox
                # entries through some other route.
                if pdf.parent.name == "inbox":
                    continue
                _process_and_dispose(pipeline, str(pdf), ingest_path="crawler")

            time.sleep(poll_secs)
        except KeyboardInterrupt:
            print("\nStopping watch...")
            break


def main():
    parser = argparse.ArgumentParser(
        description="Process scientific PDFs into the Munin knowledge base"
    )
    parser.add_argument("--single", help="Process a single PDF file")
    parser.add_argument("--dir", default=PAPERS_DIR,
                        help=f"Process all PDFs in directory (default: {PAPERS_DIR})")
    parser.add_argument("--watch", action="store_true",
                        help="Watch directory for new PDFs")
    parser.add_argument("--reprocess", action="store_true",
                        help="Clear processed markers and reprocess all PDFs")
    parser.add_argument("--fast", action="store_true",
                        help="Fast mode: parallel GROBID + batch embeddings")
    parser.add_argument("--workers", type=int, default=4,
                        help="Number of parallel workers for fast mode (default: 4)")
    parser.add_argument("--ingest-path", default="manual",
                        choices=["upload", "crawler", "manual"],
                        help=(
                            "Ingest classification stamped into the state "
                            "sidecar. Default 'manual' for ad-hoc operator "
                            "runs; admin/ingest passes 'upload', the "
                            "watcher passes 'crawler' automatically."
                        ))

    args = parser.parse_args()

    # Before touching a single PDF: the corpus directories must already
    # exist and be writable. Exits non-zero with an explanation otherwise.
    # See UPLOAD-INGEST-REPAIR-PLAN.md defect 1 for what this prevents.
    require_corpus_dirs()

    pipeline = PaperPipeline(fast_mode=args.fast, workers=args.workers)

    if args.single:
        # Phase B: route through _process_and_dispose so the state
        # sidecar + Qdrant mirror are populated regardless of how
        # the script was invoked. Print the dispose result as JSON
        # so /api/admin/ingest can parse it.
        result = _process_and_dispose(pipeline, args.single, args.ingest_path)
        print(f"\n[DISPOSE] {json.dumps(result)}")
    elif args.watch:
        watch_directory(pipeline, args.dir)
    else:
        process_directory(
            pipeline, args.dir,
            reprocess=args.reprocess,
            ingest_path=args.ingest_path,
        )


if __name__ == "__main__":
    main()
