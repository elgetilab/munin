#!/usr/bin/env python3
"""
==============================================================================
MUNIN Paper Cleanup Script
==============================================================================
Remove papers from Qdrant, Neo4j, SQLite, and filesystem by DOI or criteria.
Multi-source metadata enrichment and repair-first cleanup logic.

Commands:
    # Verify single DOI against all sources (debugging)
    ./paper_cleanup.py verify-doi 10.1016/0021-9991(77)90112-7

    # Multi-source repair and clean - enriches from all sources, only removes
    # papers if ALL metadata sources (OpenAlex, Semantic Scholar, CrossRef) fail
    ./paper_cleanup.py repair-and-clean --max-check 500 --dry-run
    ./paper_cleanup.py repair-and-clean --max-check 500 --auto-remove --limit 50

    # Scan processed papers directory for orphaned papers
    ./paper_cleanup.py scan-processed --check-pdf --check-metadata

    # Remove a single paper by DOI
    ./paper_cleanup.py remove --doi 10.1111/j.1749-6632.1978.tb22009.x

    # Find papers with no abstract AND no references (OpenAlex only)
    ./paper_cleanup.py find-low-quality

    # Find papers with less than 3 pages
    ./paper_cleanup.py find-short --min-pages 3

    # Bulk remove papers from a file (one DOI per line)
    ./paper_cleanup.py bulk-remove --file papers_to_remove.txt

    # Dry run (show what would be removed without actually removing)
    ./paper_cleanup.py remove --doi 10.1234/example --dry-run

Environment Variables:
    OPENALEX_API_KEY          - OpenAlex API key (optional, for higher limits)
    SEMANTIC_SCHOLAR_API_KEY  - Semantic Scholar API key (optional)
    ADMIN_EMAIL               - Email for API polite pools (required)

Requirements:
    pip install qdrant-client neo4j requests
==============================================================================
"""

import argparse
import csv
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET

import yaml
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Set, Tuple

import requests

# ==============================================================================
# Configuration
# ==============================================================================
DATA_DIR = Path(os.getenv("MUNIN_DATA_DIR", "/opt/munin/data"))
PAPERS_DIR = DATA_DIR / "papers"
PDF_DIR = PAPERS_DIR / "pdf"
PROCESSED_DIR = PAPERS_DIR / "processed"
SKIPPED_DIR = PAPERS_DIR / "skipped"
QUEUE_DB = PAPERS_DIR / "crawler_queue.db"
BLOCKLIST_FILE = PAPERS_DIR / "blocklist.txt"
FAILED_DOWNLOADS_FILE = PAPERS_DIR / "failed_downloads.txt"
LOGS_DIR = PAPERS_DIR / "logs"

# User agent email for API requests
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@example.com")

QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "munin-neo4j-password")
COLLECTION_NAME = "papers"

# Stage 2 remediation: GROBID URL for header-only title re-extraction.
GROBID_URL = os.getenv("GROBID_URL", "http://127.0.0.1:8070")
INBOX_DIR = PDF_DIR / "inbox"
# Keep in sync with paper_pipeline._MERGE_TITLE_SIM_THRESHOLD;
# calibrated 2026-05-12 (see docs/PAPER-INGEST-AUDIT.md).
MISMATCH_THRESHOLD = 0.3

# API rate limit delays (seconds)
OPENALEX_DELAY = 0.1      # 100k/day with key
SEMANTIC_SCHOLAR_DELAY = 2.0  # Conservative for unauthenticated access
CROSSREF_DELAY = 0.02     # 50/s polite pool


# ==============================================================================
# Dataclasses for Multi-Source Metadata
# ==============================================================================
@dataclass
class MetadataResult:
    """Result from a single metadata source."""
    source: str  # 'openalex', 'semantic_scholar', 'crossref'
    found: bool
    title: Optional[str] = None
    abstract: Optional[str] = None
    year: Optional[int] = None
    page_count: Optional[int] = None
    reference_count: Optional[int] = None
    citation_count: Optional[int] = None
    is_retracted: bool = False
    authors: List[str] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class AggregatedMetadata:
    """Merged metadata from all sources."""
    doi: str
    sources_checked: List[str] = field(default_factory=list)
    sources_with_data: List[str] = field(default_factory=list)

    # Best available data (prioritized across sources)
    title: Optional[str] = None
    abstract: Optional[str] = None
    year: Optional[int] = None
    page_count: Optional[int] = None
    reference_count: Optional[int] = None
    citation_count: Optional[int] = None
    authors: List[str] = field(default_factory=list)

    is_retracted: bool = False
    pdf_exists: bool = False
    pdf_page_count: Optional[int] = None  # Page count from actual PDF file

    # Track which source provided each field
    abstract_source: Optional[str] = None
    title_source: Optional[str] = None

    @property
    def has_any_data(self) -> bool:
        """Returns True if any source had data for this paper."""
        return len(self.sources_with_data) > 0

    @property
    def effective_page_count(self) -> Optional[int]:
        """Returns page count from PDF if available, otherwise from API."""
        return self.pdf_page_count if self.pdf_page_count is not None else self.page_count

    @property
    def is_short(self) -> bool:
        """Returns True if page count < 3 (from PDF or API)."""
        pages = self.effective_page_count
        return pages is not None and pages < 3

    @property
    def should_remove(self) -> bool:
        """Returns True if paper should be removed: retracted, short, or no data anywhere."""
        return self.is_retracted or self.is_short or not self.has_any_data

    @property
    def status(self) -> str:
        """Human-readable status string."""
        if self.is_retracted:
            return "RETRACTED"
        if self.is_short:
            return "SHORT"
        if not self.has_any_data:
            return "NO DATA"
        return "OK"


# ==============================================================================
# Database Connections
# ==============================================================================
def get_qdrant_client():
    """Get Qdrant client."""
    try:
        from qdrant_client import QdrantClient
        return QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
    except Exception as e:
        print(f"[WARNING] Could not connect to Qdrant: {e}")
        return None


def get_neo4j_driver():
    """Get Neo4j driver."""
    try:
        from neo4j import GraphDatabase
        return GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    except Exception as e:
        print(f"[WARNING] Could not connect to Neo4j: {e}")
        return None


def get_sqlite_conn():
    """Get SQLite connection for crawler queue."""
    if not QUEUE_DB.exists():
        print(f"[WARNING] Crawler queue database not found: {QUEUE_DB}")
        return None
    return sqlite3.connect(QUEUE_DB)


# ==============================================================================
# Blocklist Management
# ==============================================================================
def load_blocklist() -> Set[str]:
    """Load DOI blocklist from file."""
    if not BLOCKLIST_FILE.exists():
        return set()
    with open(BLOCKLIST_FILE) as f:
        return {line.strip() for line in f if line.strip() and not line.startswith("#")}


def add_to_blocklist(doi: str):
    """Add a DOI to the blocklist."""
    blocklist = load_blocklist()
    if doi in blocklist:
        return  # Already blocked

    # Ensure parent directory exists
    BLOCKLIST_FILE.parent.mkdir(parents=True, exist_ok=True)

    with open(BLOCKLIST_FILE, "a") as f:
        f.write(f"{doi}\n")
    print(f"  Added to blocklist: {doi}")


def load_failed_downloads() -> Set[str]:
    """Load failed downloads list from file."""
    if not FAILED_DOWNLOADS_FILE.exists():
        return set()
    with open(FAILED_DOWNLOADS_FILE) as f:
        return {line.strip() for line in f if line.strip() and not line.startswith("#")}


def add_to_failed_downloads(doi: str):
    """Add a DOI to the failed downloads list."""
    failed = load_failed_downloads()
    if doi in failed:
        return  # Already in list

    FAILED_DOWNLOADS_FILE.parent.mkdir(parents=True, exist_ok=True)

    with open(FAILED_DOWNLOADS_FILE, "a") as f:
        f.write(f"{doi}\n")


def clear_failed_downloads():
    """Clear the failed downloads list."""
    if FAILED_DOWNLOADS_FILE.exists():
        FAILED_DOWNLOADS_FILE.unlink()
        print(f"[OK] Cleared failed downloads list: {FAILED_DOWNLOADS_FILE}")


# ==============================================================================
# Paper Removal Functions
# ==============================================================================
def find_paper_in_qdrant(qdrant, doi: str) -> List[int]:
    """Find paper point IDs in Qdrant by DOI."""
    if not qdrant:
        return []

    try:
        from qdrant_client.models import Filter, FieldCondition, MatchValue

        # Search by DOI in payload
        results = qdrant.scroll(
            collection_name=COLLECTION_NAME,
            scroll_filter=Filter(
                must=[FieldCondition(key="doi", match=MatchValue(value=doi))]
            ),
            limit=100
        )

        points = results[0] if results else []
        return [p.id for p in points]
    except Exception as e:
        print(f"  [WARNING] Qdrant search failed: {e}")
        return []


def remove_from_qdrant(qdrant, doi: str, dry_run: bool = False) -> bool:
    """Remove paper from Qdrant by DOI."""
    if not qdrant:
        return False

    point_ids = find_paper_in_qdrant(qdrant, doi)
    if not point_ids:
        print(f"  [INFO] Paper not found in Qdrant: {doi}")
        return False

    if dry_run:
        print(f"  [DRY-RUN] Would remove {len(point_ids)} point(s) from Qdrant")
        return True

    try:
        qdrant.delete(
            collection_name=COLLECTION_NAME,
            points_selector=point_ids
        )
        print(f"  [OK] Removed {len(point_ids)} point(s) from Qdrant")
        return True
    except Exception as e:
        print(f"  [ERROR] Failed to remove from Qdrant: {e}")
        return False


def remove_from_neo4j(driver, doi: str, dry_run: bool = False) -> tuple[bool, bool]:
    """
    Remove paper from Neo4j by DOI.

    Returns:
        Tuple of (was_removed, was_stub_node)
        - was_removed: True if paper was found and removed
        - was_stub_node: True if it was a stub (citation target, never processed)
    """
    if not driver:
        return False, False

    try:
        with driver.session() as session:
            # First check if paper exists and get details
            result = session.run("""
                MATCH (p:Paper {doi: $doi})
                RETURN p.title as title, p.abstract as abstract, p.paper_id as paper_id
            """, doi=doi).single()

            if not result:
                print(f"  [INFO] Paper not found in Neo4j: {doi}")
                return False, False

            title = result['title']
            has_abstract = bool(result['abstract'])
            has_paper_id = bool(result['paper_id'])

            # Stub nodes are citation targets that were never downloaded/processed
            # They have a DOI but no paper_id and usually no title/abstract
            is_stub = not has_paper_id and not has_abstract

            if dry_run:
                node_type = "stub node (citation target)" if is_stub else "paper"
                print(f"  [DRY-RUN] Would remove {node_type} from Neo4j: {title or doi}")
                return True, is_stub

            # Delete paper and all relationships
            session.run("""
                MATCH (p:Paper {doi: $doi})
                DETACH DELETE p
            """, doi=doi)

            node_type = "stub node" if is_stub else "paper"
            print(f"  [OK] Removed {node_type} from Neo4j: {title or doi}")
            return True, is_stub
    except Exception as e:
        print(f"  [ERROR] Failed to remove from Neo4j: {e}")
        return False, False


def remove_from_sqlite(doi: str, dry_run: bool = False) -> bool:
    """Remove paper from SQLite crawler queue by DOI."""
    conn = get_sqlite_conn()
    if not conn:
        return False

    try:
        # Check if paper exists
        result = conn.execute(
            "SELECT id, pdf_path FROM papers WHERE doi = ?", (doi,)
        ).fetchone()

        if not result:
            print(f"  [INFO] Paper not found in SQLite: {doi}")
            conn.close()
            return False

        paper_id, pdf_path = result

        if dry_run:
            print(f"  [DRY-RUN] Would remove paper from SQLite: {paper_id}")
            conn.close()
            return True

        conn.execute("DELETE FROM papers WHERE doi = ?", (doi,))
        conn.commit()
        conn.close()

        print(f"  [OK] Removed paper from SQLite: {doi}")
        return True
    except Exception as e:
        print(f"  [ERROR] Failed to remove from SQLite: {e}")
        if conn:
            conn.close()
        return False


def doi_to_filename(doi: str) -> str:
    """Convert DOI to safe filename (matches paper_crawler.py logic)."""
    # Same logic as paper_crawler.py line 613
    return f"doi_{doi.replace('/', '_').replace(':', '_')}.pdf"


def get_pdf_page_count(pdf_path: Path) -> Optional[int]:
    """Get page count from a PDF file."""
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(pdf_path))
        return len(reader.pages)
    except ImportError:
        # Try pdfinfo as fallback
        import subprocess
        try:
            result = subprocess.run(
                ["pdfinfo", str(pdf_path)],
                capture_output=True,
                text=True,
                timeout=10
            )
            for line in result.stdout.split("\n"):
                if line.startswith("Pages:"):
                    return int(line.split(":")[1].strip())
        except Exception:
            pass
    except Exception:
        pass
    return None


def find_pdf_by_doi(doi: str) -> Optional[Path]:
    """Find PDF file by DOI."""
    # Generate expected filename (same as paper_crawler.py)
    filename = doi_to_filename(doi)
    pdf_path = PDF_DIR / filename

    if pdf_path.exists():
        return pdf_path

    # Try case-insensitive search if exact match fails
    if PDF_DIR.exists():
        filename_lower = filename.lower()
        for f in PDF_DIR.iterdir():
            if f.name.lower() == filename_lower:
                return f

    # Also check processed markers
    marker_name = filename.replace('.pdf', '.json')
    marker_path = PROCESSED_DIR / marker_name
    if marker_path.exists():
        return marker_path

    return None


def remove_pdf_file(doi: str, dry_run: bool = False) -> bool:
    """Remove PDF file and processed marker by DOI."""
    pdf_path = find_pdf_by_doi(doi)
    if not pdf_path:
        print(f"  [INFO] PDF file not found for: {doi}")
        return False

    if dry_run:
        print(f"  [DRY-RUN] Would remove file: {pdf_path}")
        return True

    try:
        pdf_path.unlink()
        print(f"  [OK] Removed file: {pdf_path}")

        # Also remove processed marker
        marker_name = f"{pdf_path.stem}.json"
        marker_path = PROCESSED_DIR / marker_name
        if marker_path.exists():
            marker_path.unlink()
            print(f"  [OK] Removed marker: {marker_path}")

        # Also remove skipped marker if exists
        skipped_path = SKIPPED_DIR / marker_name
        if skipped_path.exists():
            skipped_path.unlink()
            print(f"  [OK] Removed skipped marker: {skipped_path}")

        return True
    except Exception as e:
        print(f"  [ERROR] Failed to remove file: {e}")
        return False


def remove_paper_by_doi(doi: str, dry_run: bool = False, add_blocklist: bool = True):
    """Remove a paper from all databases by DOI."""
    print(f"\n{'='*60}")
    print(f"Removing paper: {doi}")
    print(f"{'='*60}")

    if dry_run:
        print("[DRY-RUN MODE - No changes will be made]")

    # Connect to databases
    qdrant = get_qdrant_client()
    neo4j = get_neo4j_driver()

    # Track what was found/removed
    removed_from_any = False
    was_stub_node = False

    if remove_from_qdrant(qdrant, doi, dry_run):
        removed_from_any = True

    neo4j_removed, was_stub = remove_from_neo4j(neo4j, doi, dry_run)
    if neo4j_removed:
        removed_from_any = True
        was_stub_node = was_stub

    if remove_from_sqlite(doi, dry_run):
        removed_from_any = True

    if remove_pdf_file(doi, dry_run):
        removed_from_any = True

    # Add to blocklist
    if add_blocklist and not dry_run:
        add_to_blocklist(doi)
    elif add_blocklist and dry_run:
        print(f"  [DRY-RUN] Would add to blocklist: {doi}")

    # Cleanup connections
    if neo4j:
        neo4j.close()

    if removed_from_any:
        if was_stub_node:
            print(f"\n[OK] Stub node removal complete: {doi}")
            print(f"     (This was a citation target that was never downloaded)")
        else:
            print(f"\n[OK] Paper removal complete: {doi}")
    else:
        print(f"\n[INFO] Paper not found in any store: {doi}")


# ==============================================================================
# Finding Low-Quality Papers
# ==============================================================================
def fetch_openalex_metadata(doi: str) -> Optional[Dict]:
    """Fetch metadata from OpenAlex API."""
    api_key = os.getenv("OPENALEX_API_KEY", "")
    try:
        url = f"https://api.openalex.org/works/https://doi.org/{doi}"
        params = {"api_key": api_key} if api_key else {}
        headers = {"User-Agent": f"MuninCleanup/1.0 (mailto:{ADMIN_EMAIL})"}
        response = requests.get(url, params=params, headers=headers, timeout=15)
        if response.status_code == 200:
            return response.json()
    except Exception:
        pass
    return None


def enrich_paper_from_openalex(driver, doi: str) -> Dict:
    """
    Fetch metadata from OpenAlex and update Neo4j if data is available.

    Returns dict with:
        - enriched: bool - whether paper was enriched
        - has_abstract: bool
        - has_refs: bool
        - page_count: int or None
        - is_retracted: bool
    """
    result = {
        "enriched": False,
        "has_abstract": False,
        "has_refs": False,
        "page_count": None,
        "is_retracted": False,
        "title": None
    }

    metadata = fetch_openalex_metadata(doi)
    if not metadata:
        return result

    # Extract data from OpenAlex
    result["is_retracted"] = metadata.get("is_retracted", False)
    result["title"] = metadata.get("title")

    # Check abstract (OpenAlex uses inverted index format)
    abstract_inv = metadata.get("abstract_inverted_index")
    if abstract_inv:
        result["has_abstract"] = True
        # Reconstruct abstract from inverted index
        try:
            word_positions = []
            for word, positions in abstract_inv.items():
                for pos in positions:
                    word_positions.append((pos, word))
            word_positions.sort()
            abstract_text = " ".join(word for _, word in word_positions)
        except Exception:
            abstract_text = None
    else:
        abstract_text = None

    # Check references
    referenced_works = metadata.get("referenced_works", [])
    result["has_refs"] = len(referenced_works) > 0

    # Check page count
    biblio = metadata.get("biblio", {})
    first_page = biblio.get("first_page")
    last_page = biblio.get("last_page")
    if first_page and last_page:
        try:
            result["page_count"] = int(last_page) - int(first_page) + 1
        except ValueError:
            pass

    # Enrich Neo4j if we have new data
    if driver and (abstract_text or result["has_refs"]):
        try:
            with driver.session() as session:
                # Update abstract if we have one and Neo4j doesn't
                if abstract_text:
                    session.run("""
                        MATCH (p:Paper {doi: $doi})
                        WHERE p.abstract IS NULL OR p.abstract = ''
                        SET p.abstract = $abstract
                    """, doi=doi, abstract=abstract_text[:2000])
                    result["enriched"] = True

                # We could also add citation relationships here if needed
                # For now, just mark that refs exist

            if result["enriched"]:
                print(f"    [ENRICHED] Updated abstract from OpenAlex")
        except Exception as e:
            print(f"    [WARNING] Failed to enrich Neo4j: {e}")

    return result


# ==============================================================================
# Multi-Source Metadata Fetcher
# ==============================================================================
class MultiSourceMetadataFetcher:
    """Fetches and aggregates metadata from OpenAlex, Semantic Scholar, and CrossRef."""

    def __init__(self):
        self.openalex_api_key = os.getenv("OPENALEX_API_KEY", "")
        self.semantic_scholar_api_key = os.getenv("SEMANTIC_SCHOLAR_API_KEY", "")
        self.admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")

    def fetch_openalex(self, doi: str) -> MetadataResult:
        """Fetch metadata from OpenAlex."""
        result = MetadataResult(source="openalex", found=False)

        try:
            url = f"https://api.openalex.org/works/https://doi.org/{doi}"
            params = {"api_key": self.openalex_api_key} if self.openalex_api_key else {}
            headers = {"User-Agent": f"MuninCleanup/1.0 (mailto:{self.admin_email})"}
            response = requests.get(url, params=params, headers=headers, timeout=15)

            time.sleep(OPENALEX_DELAY)

            if response.status_code != 200:
                result.error = f"HTTP {response.status_code}"
                return result

            data = response.json()
            result.found = True
            result.title = data.get("title")
            result.year = data.get("publication_year")
            result.is_retracted = data.get("is_retracted", False)

            # Extract abstract from inverted index
            abstract_inv = data.get("abstract_inverted_index")
            if abstract_inv:
                try:
                    word_positions = []
                    for word, positions in abstract_inv.items():
                        for pos in positions:
                            word_positions.append((pos, word))
                    word_positions.sort()
                    result.abstract = " ".join(word for _, word in word_positions)
                except Exception:
                    pass

            # Reference count
            referenced_works = data.get("referenced_works", [])
            result.reference_count = len(referenced_works) if referenced_works else None

            # Citation count
            cited_by_count = data.get("cited_by_count")
            if cited_by_count is not None:
                result.citation_count = cited_by_count

            # Page count from biblio
            biblio = data.get("biblio", {})
            first_page = biblio.get("first_page")
            last_page = biblio.get("last_page")
            if first_page and last_page:
                try:
                    result.page_count = int(last_page) - int(first_page) + 1
                except ValueError:
                    pass

            # Authors
            authorships = data.get("authorships", [])
            result.authors = [
                a.get("author", {}).get("display_name", "")
                for a in authorships if a.get("author", {}).get("display_name")
            ]

        except requests.Timeout:
            result.error = "Timeout"
        except Exception as e:
            result.error = str(e)

        return result

    def fetch_semantic_scholar(self, doi: str) -> MetadataResult:
        """Fetch metadata from Semantic Scholar."""
        result = MetadataResult(source="semantic_scholar", found=False)

        try:
            url = f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}"
            params = {"fields": "title,abstract,year,citationCount,referenceCount,authors,isOpenAccess"}
            headers = {}
            if self.semantic_scholar_api_key:
                headers["x-api-key"] = self.semantic_scholar_api_key

            response = requests.get(url, params=params, headers=headers, timeout=15)

            time.sleep(SEMANTIC_SCHOLAR_DELAY)

            if response.status_code == 404:
                result.error = "Not found"
                return result
            if response.status_code != 200:
                result.error = f"HTTP {response.status_code}"
                return result

            data = response.json()
            result.found = True
            result.title = data.get("title")
            result.abstract = data.get("abstract")
            result.year = data.get("year")
            result.citation_count = data.get("citationCount")
            result.reference_count = data.get("referenceCount")

            # Authors
            authors = data.get("authors", [])
            result.authors = [a.get("name", "") for a in authors if a.get("name")]

        except requests.Timeout:
            result.error = "Timeout"
        except Exception as e:
            result.error = str(e)

        return result

    def fetch_crossref(self, doi: str) -> MetadataResult:
        """Fetch metadata from CrossRef."""
        result = MetadataResult(source="crossref", found=False)

        try:
            url = f"https://api.crossref.org/works/{doi}"
            headers = {"User-Agent": f"MuninCleanup/1.0 (mailto:{self.admin_email})"}
            response = requests.get(url, headers=headers, timeout=15)

            time.sleep(CROSSREF_DELAY)

            if response.status_code == 404:
                result.error = "Not found"
                return result
            if response.status_code != 200:
                result.error = f"HTTP {response.status_code}"
                return result

            data = response.json().get("message", {})
            result.found = True
            result.title = data.get("title", [None])[0] if data.get("title") else None
            result.abstract = data.get("abstract")

            # Year from published-print or published-online
            for date_field in ["published-print", "published-online", "issued"]:
                if date_field in data:
                    date_parts = data[date_field].get("date-parts", [[None]])
                    if date_parts and date_parts[0] and date_parts[0][0]:
                        result.year = date_parts[0][0]
                        break

            # Reference count
            result.reference_count = data.get("references-count")

            # Citation count (CrossRef calls it "is-referenced-by-count")
            result.citation_count = data.get("is-referenced-by-count")

            # Authors
            authors = data.get("author", [])
            result.authors = [
                f"{a.get('given', '')} {a.get('family', '')}".strip()
                for a in authors
            ]

        except requests.Timeout:
            result.error = "Timeout"
        except Exception as e:
            result.error = str(e)

        return result

    def fetch_all(self, doi: str, check_pdf: bool = False) -> AggregatedMetadata:
        """
        Fetch metadata from all sources and aggregate.

        Args:
            doi: The DOI to look up
            check_pdf: Whether to check if PDF exists on filesystem

        Returns:
            AggregatedMetadata with best available data from all sources
        """
        aggregated = AggregatedMetadata(doi=doi)

        # Fetch from all sources
        results = {
            "openalex": self.fetch_openalex(doi),
            "semantic_scholar": self.fetch_semantic_scholar(doi),
            "crossref": self.fetch_crossref(doi),
        }

        for source, result in results.items():
            aggregated.sources_checked.append(source)
            if result.found:
                aggregated.sources_with_data.append(source)

        # Merge results with priority
        # Title: prefer CrossRef (authoritative), then S2, then OpenAlex
        for source in ["crossref", "semantic_scholar", "openalex"]:
            if results[source].found and results[source].title and not aggregated.title:
                aggregated.title = results[source].title
                aggregated.title_source = source

        # Abstract: prefer longest non-empty abstract
        best_abstract = ""
        best_abstract_source = None
        for source, result in results.items():
            if result.found and result.abstract and len(result.abstract) > len(best_abstract):
                best_abstract = result.abstract
                best_abstract_source = source
        if best_abstract:
            aggregated.abstract = best_abstract
            aggregated.abstract_source = best_abstract_source

        # Year: prefer any available
        for source in ["openalex", "semantic_scholar", "crossref"]:
            if results[source].found and results[source].year and not aggregated.year:
                aggregated.year = results[source].year

        # Page count: only OpenAlex provides this reliably
        if results["openalex"].found and results["openalex"].page_count:
            aggregated.page_count = results["openalex"].page_count

        # Citation count: prefer Semantic Scholar (most accurate)
        for source in ["semantic_scholar", "openalex", "crossref"]:
            if results[source].found and results[source].citation_count is not None and aggregated.citation_count is None:
                aggregated.citation_count = results[source].citation_count

        # Reference count: prefer Semantic Scholar
        for source in ["semantic_scholar", "openalex", "crossref"]:
            if results[source].found and results[source].reference_count is not None and aggregated.reference_count is None:
                aggregated.reference_count = results[source].reference_count

        # Authors: prefer CrossRef
        for source in ["crossref", "semantic_scholar", "openalex"]:
            if results[source].found and results[source].authors and not aggregated.authors:
                aggregated.authors = results[source].authors

        # Retraction: any source reporting retracted = retracted
        aggregated.is_retracted = results["openalex"].is_retracted

        # Check PDF if requested
        if check_pdf:
            pdf_path = find_pdf_by_doi(doi)
            aggregated.pdf_exists = pdf_path is not None
            # Get page count from actual PDF file
            if pdf_path and pdf_path.suffix.lower() == '.pdf':
                aggregated.pdf_page_count = get_pdf_page_count(pdf_path)

        return aggregated


def verify_doi(doi: str, verbose: bool = True) -> AggregatedMetadata:
    """
    Verify a single DOI against all metadata sources.

    Args:
        doi: The DOI to verify
        verbose: Whether to print detailed output

    Returns:
        AggregatedMetadata for the DOI
    """
    fetcher = MultiSourceMetadataFetcher()
    metadata = fetcher.fetch_all(doi, check_pdf=True)

    if verbose:
        print(f"\n{'='*60}")
        print(f"DOI Verification: {doi}")
        print(f"{'='*60}")
        print()

        # Source status
        print("Metadata Sources:")
        for source in ["openalex", "semantic_scholar", "crossref"]:
            status = "Y" if source in metadata.sources_with_data else "N"
            print(f"  {source:<20} {status}")

        print()
        print("Aggregated Data:")
        print(f"  Title:           {metadata.title or 'N/A'}")
        print(f"  Year:            {metadata.year or 'N/A'}")
        print(f"  Authors:         {', '.join(metadata.authors[:3]) if metadata.authors else 'N/A'}")
        print(f"  Abstract:        {('Yes (' + metadata.abstract_source + ')') if metadata.abstract else 'N/A'}")
        print(f"  Page count (API):{metadata.page_count or 'N/A'}")
        print(f"  Page count (PDF):{metadata.pdf_page_count or 'N/A'}")
        print(f"  Citations:       {metadata.citation_count if metadata.citation_count is not None else 'N/A'}")
        print(f"  References:      {metadata.reference_count if metadata.reference_count is not None else 'N/A'}")
        print(f"  PDF exists:      {'Yes' if metadata.pdf_exists else 'No'}")

        print()
        print("Decision:")
        print(f"  Is retracted:    {'Yes' if metadata.is_retracted else 'No'}")
        print(f"  Is short (<3p):  {'Yes' if metadata.is_short else 'No'}")
        print(f"  Has any data:    {'Yes' if metadata.has_any_data else 'No'}")
        print(f"  Status:          {metadata.status}")
        print(f"  Should remove:   {'Yes' if metadata.should_remove else 'No'}")
        print()

    return metadata


def repair_and_clean(
    source: str = "both",
    max_check: int = 500,
    auto_remove: bool = False,
    limit: Optional[int] = None,
    confirm: bool = False,
    output_file: Optional[str] = None,
    dry_run: bool = False,
    check_pdf: bool = True,
    export_orphaned: Optional[str] = None
) -> Dict:
    """
    Multi-source metadata enrichment and cleanup.

    Process:
    1. Get papers from Neo4j and/or processed directory
    2. Check each against OpenAlex, Semantic Scholar, and CrossRef
    3. If ANY source has data: enrich the paper
    4. If ALL sources fail: flag for removal

    Args:
        source: 'neo4j', 'processed', or 'both' (default: both)
        max_check: Maximum papers to check (default: 500)
        auto_remove: Automatically remove flagged papers
        limit: Maximum papers to auto-remove
        confirm: Required for auto-remove without limit
        output_file: Save results to JSON file
        dry_run: Don't modify databases
        check_pdf: Check if PDFs exist for papers

    Returns:
        Dict with summary statistics
    """
    driver = get_neo4j_driver()
    fetcher = MultiSourceMetadataFetcher()

    print("=" * 70)
    print("Repair and Clean - Multi-Source Metadata Enrichment")
    print("=" * 70)
    print()
    print("This tool checks papers against multiple sources and only removes")
    print("papers that have NO DATA in any source.")
    print()

    # Collect DOIs to check
    dois_to_check = set()

    if source in ("neo4j", "both"):
        if driver:
            try:
                with driver.session() as session:
                    result = session.run("""
                        MATCH (p:Paper)
                        WHERE p.doi IS NOT NULL
                        RETURN p.doi as doi
                        ORDER BY rand()
                        LIMIT $max_check
                    """, max_check=max_check)
                    neo4j_dois = [r["doi"] for r in result]
                    dois_to_check.update(neo4j_dois)
                    print(f"Found {len(neo4j_dois)} papers from Neo4j")
            except Exception as e:
                print(f"[WARNING] Failed to query Neo4j: {e}")

    if source in ("processed", "both"):
        if PROCESSED_DIR.exists():
            processed_files = list(PROCESSED_DIR.glob("*.json"))[:max_check]
            for pf in processed_files:
                try:
                    with open(pf) as f:
                        data = json.load(f)
                        if "doi" in data:
                            dois_to_check.add(data["doi"])
                except Exception:
                    pass
            print(f"Found {len(processed_files)} processed files")

    dois_to_check = list(dois_to_check)[:max_check]
    print(f"\nTotal unique DOIs to check: {len(dois_to_check)}")
    print()

    if not dois_to_check:
        print("No papers to check.")
        return {"checked": 0}

    # Process papers
    results = {
        "checked": 0,
        "ok": 0,
        "enriched": 0,
        "no_data": 0,
        "retracted": 0,
        "short": 0,
        "orphaned": 0,  # In DB but no PDF
        "to_remove": [],
        "to_enrich": [],
        "orphaned_dois": [],
    }

    # Print header
    print(f"{'DOI':<45} {'OA':<4} {'S2':<4} {'CR':<4} {'PDF':<5} {'Status'}")
    print("-" * 80)

    for i, doi in enumerate(dois_to_check):
        metadata = fetcher.fetch_all(doi, check_pdf=check_pdf)
        results["checked"] += 1

        # Source indicators
        oa = "Y" if "openalex" in metadata.sources_with_data else "N"
        s2 = "Y" if "semantic_scholar" in metadata.sources_with_data else "N"
        cr = "Y" if "crossref" in metadata.sources_with_data else "N"
        pdf = "OK" if metadata.pdf_exists else "MISS" if check_pdf else "?"

        # Determine status and action
        if metadata.is_retracted:
            status = "RETRACTED"
            results["retracted"] += 1
            results["to_remove"].append(doi)
        elif metadata.is_short:
            status = "SHORT"
            results["short"] += 1
            results["to_remove"].append(doi)
        elif not metadata.has_any_data:
            status = "NO DATA"
            results["no_data"] += 1
            results["to_remove"].append(doi)
        elif metadata.has_any_data and check_pdf and not metadata.pdf_exists:
            status = "ORPHANED"
            results["orphaned"] += 1
            results["orphaned_dois"].append(doi)
        else:
            # Has data - check if we should enrich
            if metadata.abstract and driver and not dry_run:
                # Try to enrich Neo4j with abstract if missing
                try:
                    with driver.session() as session:
                        update_result = session.run("""
                            MATCH (p:Paper {doi: $doi})
                            WHERE p.abstract IS NULL OR p.abstract = ''
                            SET p.abstract = $abstract
                            RETURN count(p) as updated
                        """, doi=doi, abstract=metadata.abstract[:2000])
                        if update_result.single()["updated"] > 0:
                            status = "ENRICHED"
                            results["enriched"] += 1
                            results["to_enrich"].append(doi)
                        else:
                            status = "OK"
                            results["ok"] += 1
                except Exception:
                    status = "OK"
                    results["ok"] += 1
            else:
                status = "OK"
                results["ok"] += 1

        print(f"{doi:<45} {oa:<4} {s2:<4} {cr:<4} {pdf:<5} {status}")

        # Progress update
        if (i + 1) % 50 == 0:
            print(f"... processed {i + 1}/{len(dois_to_check)} ...")

    # Print summary
    print("-" * 80)
    print()
    print("Summary:")
    print(f"  Papers checked:        {results['checked']}")
    print(f"  Already OK:            {results['ok']}")
    print(f"  Enriched:              {results['enriched']}  (metadata added)")
    print(f"  Removal candidates:    {results['no_data']}  (no data from any source)")
    print(f"  Retracted:             {results['retracted']}")
    print(f"  Short (<3 pages):      {results['short']}")
    if check_pdf:
        print(f"  Orphaned (no PDF):     {results['orphaned']}")

    # Export orphaned DOIs for re-crawling
    if export_orphaned and results["orphaned_dois"]:
        with open(export_orphaned, "w") as f:
            f.write("# Orphaned papers - in database but PDF missing\n")
            f.write("# Re-crawl these DOIs to restore PDFs\n")
            for doi in results["orphaned_dois"]:
                f.write(f"{doi}\n")
        print(f"\n[OK] Exported {len(results['orphaned_dois'])} orphaned DOIs to: {export_orphaned}")

    # Save results to file
    if output_file:
        with open(output_file, "w") as f:
            json.dump({
                "timestamp": datetime.now().isoformat(),
                "source": source,
                "max_check": max_check,
                "results": results
            }, f, indent=2)
        print(f"\n[OK] Results saved to: {output_file}")

    # Auto-remove if requested
    if auto_remove and results["to_remove"]:
        _auto_remove_papers(results["to_remove"], limit, confirm)

    if driver:
        driver.close()

    return results


def repair_auto(
    source: str = "neo4j",
    max_papers: int = 100,
    output_file: Optional[str] = None,
    scripts_dir: Optional[str] = None,
    retry_failed: bool = False,
    single_doi: Optional[str] = None
) -> Dict:
    """
    Autonomous paper repair - works one paper at a time with immediate actions.

    For each paper:
    - If no data in any source → REMOVE immediately
    - If retracted → REMOVE immediately
    - If short (<3 pages) → REMOVE immediately
    - If orphaned (missing PDF) → RE-DOWNLOAD via paper_crawler.py
    - If missing embeddings → RE-EMBED via paper_pipeline.py
    - Otherwise → ENRICH metadata if possible

    Args:
        source: 'neo4j' or 'processed' (where to get paper list)
        max_papers: Maximum papers to process
        output_file: Save log to JSON file
        scripts_dir: Directory containing paper_crawler.py and paper_pipeline.py

    Returns:
        Dict with processing statistics
    """
    import subprocess

    # Find scripts directory
    if scripts_dir is None:
        scripts_dir = Path(__file__).parent
    else:
        scripts_dir = Path(scripts_dir)

    crawler_script = scripts_dir / "paper_crawler.py"
    pipeline_script = scripts_dir / "paper_pipeline.py"

    if not crawler_script.exists():
        print(f"[ERROR] paper_crawler.py not found at: {crawler_script}")
        return {"error": "crawler_script_not_found"}

    if not pipeline_script.exists():
        print(f"[ERROR] paper_pipeline.py not found at: {pipeline_script}")
        return {"error": "pipeline_script_not_found"}

    driver = get_neo4j_driver()
    qdrant = get_qdrant_client()
    fetcher = MultiSourceMetadataFetcher()

    print("=" * 70)
    print("Autonomous Paper Repair")
    print("=" * 70)
    print()
    print("Actions:")
    print("  - NO DATA in any source  → Remove from all databases")
    print("  - RETRACTED              → Remove from all databases")
    print("  - SHORT (<3 pages)       → Remove from all databases")
    print("  - ORPHANED (no PDF)      → Re-download via paper_crawler.py")
    print("  - NO EMBEDDINGS          → Re-embed via paper_pipeline.py")
    print()

    # Handle failed downloads list
    if retry_failed:
        clear_failed_downloads()
        failed_downloads = set()
    else:
        failed_downloads = load_failed_downloads()
        if failed_downloads:
            print(f"Skipping {len(failed_downloads)} previously failed downloads")
            print(f"  (use --retry-failed to retry them)")
            print()

    # Collect DOIs to check
    dois_to_check = []

    if single_doi:
        # Process a single DOI
        dois_to_check = [single_doi]
        print(f"Processing single DOI: {single_doi}")
    elif source == "neo4j" and driver:
        try:
            with driver.session() as session:
                result = session.run("""
                    MATCH (p:Paper)
                    WHERE p.doi IS NOT NULL
                    RETURN p.doi as doi
                    ORDER BY rand()
                    LIMIT $max_papers
                """, max_papers=max_papers)
                dois_to_check = [r["doi"] for r in result]
                print(f"Found {len(dois_to_check)} papers from Neo4j")
        except Exception as e:
            print(f"[ERROR] Failed to query Neo4j: {e}")
            return {"error": str(e)}

    elif source == "processed":
        if PROCESSED_DIR.exists():
            processed_files = list(PROCESSED_DIR.glob("*.json"))[:max_papers]
            for pf in processed_files:
                try:
                    with open(pf) as f:
                        data = json.load(f)
                        if "doi" in data:
                            dois_to_check.append(data["doi"])
                except Exception:
                    pass
            print(f"Found {len(dois_to_check)} papers from processed directory")

    if not dois_to_check:
        print("No papers to process.")
        return {"checked": 0}

    # Stats
    stats = {
        "checked": 0,
        "ok": 0,
        "enriched": 0,
        "removed": 0,
        "redownloaded": 0,
        "redownload_failed": 0,
        "reembedded": 0,
        "reembed_failed": 0,
        "errors": [],
        "log": []
    }

    print(f"\nProcessing {len(dois_to_check)} papers...\n")
    print(f"{'#':<5} {'DOI':<40} {'OA':<3} {'S2':<3} {'CR':<3} {'Action':<20} {'Result'}")
    print("-" * 100)

    for i, doi in enumerate(dois_to_check):
        stats["checked"] += 1
        log_entry = {"doi": doi, "index": i + 1}

        try:
            # Check metadata from all sources
            metadata = fetcher.fetch_all(doi, check_pdf=True)

            oa = "Y" if "openalex" in metadata.sources_with_data else "N"
            s2 = "Y" if "semantic_scholar" in metadata.sources_with_data else "N"
            cr = "Y" if "crossref" in metadata.sources_with_data else "N"

            # Decide action
            if metadata.is_retracted:
                action = "REMOVE (retracted)"
                remove_paper_by_doi(doi, dry_run=False, add_blocklist=True)
                stats["removed"] += 1
                result = "Removed"
                log_entry["action"] = "removed"
                log_entry["reason"] = "retracted"

            elif metadata.is_short:
                action = "REMOVE (short)"
                remove_paper_by_doi(doi, dry_run=False, add_blocklist=True)
                stats["removed"] += 1
                result = "Removed"
                log_entry["action"] = "removed"
                log_entry["reason"] = "short"

            elif not metadata.has_any_data:
                action = "REMOVE (no data)"
                remove_paper_by_doi(doi, dry_run=False, add_blocklist=True)
                stats["removed"] += 1
                result = "Removed"
                log_entry["action"] = "removed"
                log_entry["reason"] = "no_data"

            elif not metadata.pdf_exists:
                # Check if this DOI previously failed to download
                if doi in failed_downloads:
                    action = "SKIP (prev failed)"
                    stats["skipped_failed"] = stats.get("skipped_failed", 0) + 1
                    result = "Skipped"
                    log_entry["action"] = "skipped_previous_failure"
                else:
                    action = "RE-DOWNLOAD"
                    # Call paper_crawler.py download-single
                    proc = subprocess.run(
                        ["python3", str(crawler_script), "download-single", doi, "--quiet"],
                        capture_output=True,
                        text=True,
                        timeout=120
                    )
                    if proc.returncode == 0:
                        stats["redownloaded"] += 1
                        result = "Downloaded"
                        log_entry["action"] = "redownloaded"

                        # Now check if we need to embed
                        pdf_path = find_pdf_by_doi(doi)
                        if pdf_path and qdrant:
                            # Check if embeddings exist
                            has_embeddings = bool(find_paper_in_qdrant(qdrant, doi))
                            if not has_embeddings:
                                # Re-embed
                                proc2 = subprocess.run(
                                    ["python3", str(pipeline_script), "--single", str(pdf_path)],
                                    capture_output=True,
                                    text=True,
                                    timeout=300
                                )
                                if proc2.returncode == 0:
                                    stats["reembedded"] += 1
                                    result = "Downloaded+Embedded"
                                    log_entry["action"] = "redownloaded_and_embedded"
                                else:
                                    stats["reembed_failed"] += 1
                                    result = "Downloaded (embed failed)"
                                    log_entry["embed_error"] = proc2.stderr[:200]
                    else:
                        stats["redownload_failed"] += 1
                        result = "Download failed"
                        log_entry["action"] = "redownload_failed"
                        log_entry["error"] = proc.stderr[:200]
                        # Add to failed downloads list
                        add_to_failed_downloads(doi)

            else:
                # PDF exists, check if embeddings exist
                has_embeddings = bool(find_paper_in_qdrant(qdrant, doi)) if qdrant else True

                if not has_embeddings:
                    action = "RE-EMBED"
                    pdf_path = find_pdf_by_doi(doi)
                    if pdf_path:
                        proc = subprocess.run(
                            ["python3", str(pipeline_script), "--single", str(pdf_path)],
                            capture_output=True,
                            text=True,
                            timeout=300
                        )
                        if proc.returncode == 0:
                            stats["reembedded"] += 1
                            result = "Embedded"
                            log_entry["action"] = "reembedded"
                        else:
                            stats["reembed_failed"] += 1
                            result = "Embed failed"
                            log_entry["action"] = "reembed_failed"
                            log_entry["error"] = proc.stderr[:200]
                    else:
                        result = "PDF not found"
                        log_entry["action"] = "error"
                        log_entry["error"] = "pdf_not_found"
                else:
                    # Check if we can enrich with abstract
                    if metadata.abstract and driver:
                        try:
                            with driver.session() as session:
                                update_result = session.run("""
                                    MATCH (p:Paper {doi: $doi})
                                    WHERE p.abstract IS NULL OR p.abstract = ''
                                    SET p.abstract = $abstract
                                    RETURN count(p) as updated
                                """, doi=doi, abstract=metadata.abstract[:2000])
                                if update_result.single()["updated"] > 0:
                                    action = "ENRICH"
                                    stats["enriched"] += 1
                                    result = "Enriched"
                                    log_entry["action"] = "enriched"
                                else:
                                    action = "OK"
                                    stats["ok"] += 1
                                    result = "OK"
                                    log_entry["action"] = "ok"
                        except Exception:
                            action = "OK"
                            stats["ok"] += 1
                            result = "OK"
                            log_entry["action"] = "ok"
                    else:
                        action = "OK"
                        stats["ok"] += 1
                        result = "OK"
                        log_entry["action"] = "ok"

            print(f"{i+1:<5} {doi:<40} {oa:<3} {s2:<3} {cr:<3} {action:<20} {result}")

        except Exception as e:
            print(f"{i+1:<5} {doi:<40} {'?':<3} {'?':<3} {'?':<3} {'ERROR':<20} {str(e)[:20]}")
            stats["errors"].append({"doi": doi, "error": str(e)})
            log_entry["action"] = "error"
            log_entry["error"] = str(e)

        stats["log"].append(log_entry)

    # Print summary
    print("-" * 100)
    print()
    print("Summary:")
    print(f"  Papers checked:      {stats['checked']}")
    print(f"  OK (no action):      {stats['ok']}")
    print(f"  Enriched:            {stats['enriched']}")
    print(f"  Removed:             {stats['removed']}")
    print(f"  Re-downloaded:       {stats['redownloaded']}")
    print(f"  Re-download failed:  {stats['redownload_failed']}")
    print(f"  Re-embedded:         {stats['reembedded']}")
    print(f"  Re-embed failed:     {stats['reembed_failed']}")
    if stats.get("skipped_failed", 0) > 0:
        print(f"  Skipped (prev fail): {stats['skipped_failed']}")
    print(f"  Errors:              {len(stats['errors'])}")

    if stats['redownload_failed'] > 0:
        print(f"\n  Failed downloads logged to: {FAILED_DOWNLOADS_FILE}")

    # Save log
    if output_file:
        # If just a filename (no path), save to logs directory
        output_path = Path(output_file)
        if not output_path.is_absolute() and "/" not in output_file:
            LOGS_DIR.mkdir(parents=True, exist_ok=True)
            output_path = LOGS_DIR / output_file

        try:
            with open(output_path, "w") as f:
                json.dump({
                    "timestamp": datetime.now().isoformat(),
                    "source": source,
                    "max_papers": max_papers,
                    "stats": {k: v for k, v in stats.items() if k != "log"},
                    "log": stats["log"]
                }, f, indent=2)
            print(f"\n[OK] Log saved to: {output_path}")
        except PermissionError:
            print(f"\n[WARNING] Could not write log to {output_path} (permission denied)")

    if driver:
        driver.close()

    return stats


def scan_processed(
    check_metadata: bool = False,
    check_pdf: bool = False,
    find_orphaned: bool = False,
    max_scan: int = 1000,
    output_file: Optional[str] = None
) -> Dict:
    """
    Scan the processed papers directory for issues.

    Args:
        check_metadata: Check each paper against metadata sources
        check_pdf: Check if PDFs exist for processed papers
        find_orphaned: Find papers in database but missing PDF
        max_scan: Maximum files to scan
        output_file: Save results to JSON file

    Returns:
        Dict with scan results
    """
    print("=" * 70)
    print("Scan Processed Papers Directory")
    print("=" * 70)
    print()

    if not PROCESSED_DIR.exists():
        print(f"[ERROR] Processed directory not found: {PROCESSED_DIR}")
        return {"error": "directory_not_found"}

    results = {
        "total_files": 0,
        "valid_json": 0,
        "with_doi": 0,
        "pdf_missing": [],
        "metadata_issues": [],
    }

    processed_files = list(PROCESSED_DIR.glob("*.json"))
    results["total_files"] = len(processed_files)
    print(f"Found {len(processed_files)} processed marker files")

    if max_scan < len(processed_files):
        processed_files = processed_files[:max_scan]
        print(f"Scanning first {max_scan} files...")

    fetcher = MultiSourceMetadataFetcher() if check_metadata else None

    for i, pf in enumerate(processed_files):
        try:
            with open(pf) as f:
                data = json.load(f)
                results["valid_json"] += 1

                doi = data.get("doi")
                if doi:
                    results["with_doi"] += 1

                    # Check PDF existence
                    if check_pdf or find_orphaned:
                        pdf_path = find_pdf_by_doi(doi)
                        if not pdf_path:
                            results["pdf_missing"].append({
                                "doi": doi,
                                "marker_file": str(pf)
                            })

                    # Check metadata sources
                    if check_metadata and fetcher:
                        metadata = fetcher.fetch_all(doi)
                        if not metadata.has_any_data:
                            results["metadata_issues"].append({
                                "doi": doi,
                                "reason": "no_data_in_any_source"
                            })

        except json.JSONDecodeError:
            pass
        except Exception as e:
            pass

        if (i + 1) % 100 == 0:
            print(f"  Scanned {i + 1}/{len(processed_files)}...")

    # Print summary
    print()
    print("Scan Results:")
    print(f"  Total files:       {results['total_files']}")
    print(f"  Valid JSON:        {results['valid_json']}")
    print(f"  With DOI:          {results['with_doi']}")

    if check_pdf or find_orphaned:
        print(f"  Missing PDF:       {len(results['pdf_missing'])}")
        if results["pdf_missing"][:5]:
            print("    Examples:")
            for item in results["pdf_missing"][:5]:
                print(f"      - {item['doi']}")

    if check_metadata:
        print(f"  Metadata issues:   {len(results['metadata_issues'])}")
        if results["metadata_issues"][:5]:
            print("    Examples:")
            for item in results["metadata_issues"][:5]:
                print(f"      - {item['doi']}: {item['reason']}")

    # Save results
    if output_file:
        with open(output_file, "w") as f:
            json.dump({
                "timestamp": datetime.now().isoformat(),
                "results": results
            }, f, indent=2)
        print(f"\n[OK] Results saved to: {output_file}")

    return results


def find_low_quality_papers(
    output_file: Optional[str] = None,
    auto_remove: bool = False,
    limit: Optional[int] = None,
    confirm: bool = False,
    enrich: bool = True,
    max_check: int = 500
) -> List[str]:
    """
    Find truly low-quality papers using smart filtering.

    Process:
    1. Find papers with missing metadata in Neo4j
    2. Check OpenAlex for each paper:
       - If page count < 3: mark for removal (definitely low quality)
       - If has abstract/refs in OpenAlex: enrich Neo4j (not low quality, just missing data)
       - If no data in OpenAlex either: mark for removal (truly low quality)
    3. Only remove papers that are truly low quality

    Args:
        output_file: Write found DOIs to this file
        auto_remove: Automatically remove found papers from all knowledge bases
        limit: Maximum number of papers to auto-remove
        confirm: Required for auto-remove without limit
        enrich: Try to enrich papers from OpenAlex before deciding to remove
        max_check: Maximum number of papers to check from Neo4j

    Returns:
        List of DOIs marked for removal
    """
    driver = get_neo4j_driver()
    if not driver:
        print("[ERROR] Cannot connect to Neo4j")
        return []

    print("=" * 60)
    print("Smart Low-Quality Paper Detection")
    print("=" * 60)
    print("\nProcess:")
    print("  1. Find papers with missing metadata in Neo4j")
    print("  2. Check OpenAlex for actual quality signals")
    print("  3. Enrich papers that have data in OpenAlex")
    print("  4. Only remove truly low-quality papers")
    print("")

    candidates = []
    to_remove = []
    enriched_count = 0
    short_count = 0
    retracted_count = 0
    no_data_count = 0

    try:
        with driver.session() as session:
            # Find papers with missing abstract or no citations in Neo4j
            result = session.run("""
                MATCH (p:Paper)
                WHERE p.doi IS NOT NULL
                  AND ((p.abstract IS NULL OR p.abstract = '')
                       OR NOT EXISTS { MATCH (p)-[:CITES]->() })
                RETURN p.doi as doi, p.title as title, p.year as year,
                       p.abstract as abstract
                ORDER BY p.year DESC
                LIMIT $max_check
            """, max_check=max_check)

            candidates = list(result)

        if not candidates:
            print("\nNo papers with missing metadata found.")
            return []

        print(f"Found {len(candidates)} papers with incomplete metadata in Neo4j")
        print("Checking against OpenAlex...\n")

        print(f"{'DOI':<45} {'Status':<15} {'Pages':<6} {'Title':<30}")
        print("-" * 100)

        for i, record in enumerate(candidates):
            doi = record['doi']
            title = (record['title'] or 'N/A')[:28]
            neo4j_has_abstract = bool(record['abstract'])

            if (i + 1) % 20 == 0:
                print(f"... checked {i + 1}/{len(candidates)} ...")

            # Check OpenAlex
            oa_result = enrich_paper_from_openalex(driver if enrich else None, doi)

            pages = oa_result["page_count"]
            pages_str = str(pages) if pages else "?"

            # Determine status
            if oa_result["is_retracted"]:
                status = "RETRACTED"
                to_remove.append(doi)
                retracted_count += 1
            elif pages and pages < 3:
                status = "SHORT"
                to_remove.append(doi)
                short_count += 1
            elif oa_result["enriched"]:
                status = "ENRICHED"
                enriched_count += 1
            elif oa_result["has_abstract"] or oa_result["has_refs"]:
                status = "OK (OA data)"
                # Has data in OpenAlex, just wasn't enriched (maybe enrich=False)
            elif neo4j_has_abstract:
                status = "OK (Neo4j)"
                # Has abstract in Neo4j already
            else:
                status = "NO DATA"
                to_remove.append(doi)
                no_data_count += 1

            print(f"{doi:<45} {status:<15} {pages_str:<6} {title:<30}")

        print("-" * 100)
        print(f"\nSummary:")
        print(f"  Papers checked:    {len(candidates)}")
        print(f"  Enriched:          {enriched_count} (metadata added from OpenAlex)")
        print(f"  Short (<3 pages):  {short_count} (to remove)")
        print(f"  Retracted:         {retracted_count} (to remove)")
        print(f"  No data anywhere:  {no_data_count} (to remove)")
        print(f"  Total to remove:   {len(to_remove)}")

    finally:
        driver.close()

    # Write to output file if specified
    if output_file and to_remove:
        with open(output_file, "w") as f:
            f.write("# Truly low-quality papers (short, retracted, or no data anywhere)\n")
            for doi in to_remove:
                f.write(f"{doi}\n")
        print(f"\n[OK] Wrote {len(to_remove)} DOIs to: {output_file}")

    # Auto-remove if requested
    if auto_remove and to_remove:
        _auto_remove_papers(to_remove, limit, confirm)

    return to_remove


def find_short_papers(
    min_pages: int = 3,
    output_file: Optional[str] = None,
    auto_remove: bool = False,
    limit: Optional[int] = None,
    confirm: bool = False
) -> List[str]:
    """
    Find papers with fewer than min_pages pages using OpenAlex.

    Args:
        min_pages: Minimum page count threshold
        output_file: Write found DOIs to this file
        auto_remove: Automatically remove found papers from all knowledge bases
        limit: Maximum number of papers to auto-remove
        confirm: Required for auto-remove without limit

    Returns:
        List of DOIs found
    """
    conn = get_sqlite_conn()
    if not conn:
        print("[ERROR] Cannot connect to SQLite")
        return []

    print("=" * 60)
    print(f"Finding papers with fewer than {min_pages} pages")
    print("=" * 60)

    # Get all DOIs from database
    results = conn.execute(
        "SELECT doi FROM papers WHERE doi IS NOT NULL AND status = 'downloaded'"
    ).fetchall()
    conn.close()

    if not results:
        print("\nNo downloaded papers found.")
        return []

    print(f"\nChecking {len(results)} papers against OpenAlex...")

    short_papers = []
    checked = 0

    for (doi,) in results:
        checked += 1
        if checked % 10 == 0:
            print(f"  Checked {checked}/{len(results)}...")

        metadata = fetch_openalex_metadata(doi)
        if not metadata:
            continue

        biblio = metadata.get("biblio", {})
        first_page = biblio.get("first_page")
        last_page = biblio.get("last_page")

        if first_page and last_page:
            try:
                page_count = int(last_page) - int(first_page) + 1
                if page_count < min_pages:
                    title = metadata.get("title", "N/A")
                    short_papers.append({
                        "doi": doi,
                        "title": title,
                        "pages": page_count
                    })
            except ValueError:
                pass  # Non-numeric pages

    if not short_papers:
        print(f"\nNo papers with fewer than {min_pages} pages found.")
        return []

    print(f"\nFound {len(short_papers)} papers with fewer than {min_pages} pages:\n")
    print(f"{'DOI':<50} {'Pages':<6} {'Title':<40}")
    print("-" * 100)

    for paper in short_papers:
        doi = paper['doi'][:48]
        title = (paper['title'] or 'N/A')[:38]
        pages = paper['pages']
        print(f"{doi:<50} {pages:<6} {title:<40}")

    print("-" * 100)
    print(f"\nTotal: {len(short_papers)} papers")

    found_dois = [p['doi'] for p in short_papers]

    # Write to output file if specified
    if output_file and found_dois:
        with open(output_file, "w") as f:
            f.write(f"# Short papers (fewer than {min_pages} pages)\n")
            for doi in found_dois:
                f.write(f"{doi}\n")
        print(f"\n[OK] Wrote {len(found_dois)} DOIs to: {output_file}")

    # Auto-remove if requested
    if auto_remove and found_dois:
        _auto_remove_papers(found_dois, limit, confirm)

    return found_dois


def _auto_remove_papers(dois: List[str], limit: Optional[int], confirm: bool):
    """
    Auto-remove papers from all knowledge bases.

    Args:
        dois: List of DOIs to remove
        limit: Maximum number to remove (None = all)
        confirm: Required if limit is None
    """
    # Safety check: require --confirm if no limit
    if limit is None and not confirm:
        print("\n[ERROR] Auto-remove without --limit requires --confirm flag")
        print("        This is a safety measure to prevent accidental mass deletion.")
        print(f"        Would remove {len(dois)} papers.")
        print("\n        Use: --auto-remove --confirm")
        print("        Or:  --auto-remove --limit N")
        return

    to_remove = dois[:limit] if limit else dois

    print(f"\n{'='*60}")
    print(f"AUTO-REMOVING {len(to_remove)} papers from all knowledge bases")
    print(f"{'='*60}")
    print("This will remove from: Qdrant, Neo4j, SQLite, filesystem")
    print("Papers will be added to blocklist to prevent re-download")
    print("")

    removed_count = 0
    for i, doi in enumerate(to_remove, 1):
        print(f"\n[{i}/{len(to_remove)}] Removing: {doi}")
        remove_paper_by_doi(doi, dry_run=False, add_blocklist=True)
        removed_count += 1

    print(f"\n{'='*60}")
    print(f"Auto-removal complete: {removed_count} papers removed")
    print(f"{'='*60}")


def bulk_remove(filepath: str, dry_run: bool = False, add_blocklist: bool = True):
    """Remove multiple papers from a file containing DOIs."""
    path = Path(filepath)
    if not path.exists():
        print(f"[ERROR] File not found: {filepath}")
        return

    # Read DOIs from file
    with open(path) as f:
        dois = [line.strip() for line in f if line.strip() and not line.startswith("#")]

    if not dois:
        print("[ERROR] No DOIs found in file")
        return

    print(f"{'='*60}")
    print(f"Bulk removing {len(dois)} papers")
    print(f"{'='*60}")

    for doi in dois:
        remove_paper_by_doi(doi, dry_run=dry_run, add_blocklist=add_blocklist)

    print(f"\n{'='*60}")
    print(f"Bulk removal complete: {len(dois)} papers processed")
    print(f"{'='*60}")


# ==============================================================================
# Main
# ==============================================================================
# ==============================================================================
# Metadata-Mismatch Remediation (Stage 2)
# ==============================================================================
# Detects records whose stored title disagrees with the PDF's GROBID
# header title (the LIGPLOT/JSTOR splice pattern from the 2026-05-12
# audit). Writes a CSV report, lazy-backfills `_grobid_title` /
# `_grobid_doi` / `_ingest_source` / `_inspected_at` audit fields onto
# the existing Qdrant points, and optionally queues high-severity
# records for reingest under the hardened pipeline (Stage 1).
#
# Pacing: GROBID is called at most once per `--grobid-pace` seconds
# (default 30s). At 1-2 calls/min the 67k corpus needs weeks of
# intermittent runs; that's intentional ("self-cleaning").
#
# See docs/PAPER-INGEST-AUDIT.md for calibration data + the severity
# ladder behind the 0.3 / 0.5 thresholds.

# Reuse the title-similarity helpers from the sibling pipeline module
# so the threshold and tokenisation rules have a single source of
# truth. The leading underscores mark these as internal, but they're
# safe to import within the same script collection.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paper_pipeline import (  # noqa: E402
    _title_similarity,
    _normalize_title_tokens,
    _strip_markup,
)


def _container_pdf_to_host(pdf_path: Optional[str]) -> Optional[Path]:
    """Map container-side `pdf_path` (`/papers/X.pdf`) to host path."""
    if not pdf_path:
        return None
    if pdf_path.startswith("/papers/"):
        host = PDF_DIR / pdf_path[len("/papers/"):]
    else:
        host = Path(pdf_path)
    return host if host.is_file() else None


def _pdftotext_first_page(pdf_path: Path, max_chars: int = 2000) -> Optional[str]:
    """First-page text extract via pdftotext. None on any error."""
    try:
        out = subprocess.run(
            ["pdftotext", "-l", "1", str(pdf_path), "-"],
            capture_output=True, text=True, timeout=20,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    text = (out.stdout or "")[:max_chars]
    return text or None


def _grobid_header_title(pdf_path: Path) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Call GROBID `processHeaderDocument` and parse TEI.

    Returns ``(title, doi, error)``. On success ``error`` is None;
    on any failure the title/doi are None and ``error`` describes
    what went wrong.
    """
    TEI_NS = {"tei": "http://www.tei-c.org/ns/1.0"}
    try:
        with open(pdf_path, "rb") as f:
            r = requests.post(
                f"{GROBID_URL}/api/processHeaderDocument",
                files={"input": (pdf_path.name, f, "application/pdf")},
                headers={"Accept": "application/xml"},
                timeout=120,
            )
    except Exception as e:
        return None, None, f"request_error: {e}"
    if r.status_code != 200:
        return None, None, f"http_{r.status_code}: {r.text[:80]}"
    try:
        root = ET.fromstring(r.text)
    except ET.ParseError as e:
        return None, None, f"tei_parse_error: {e}"
    title_el = root.find(".//tei:titleStmt/tei:title", TEI_NS)
    title = (title_el.text.strip()
             if (title_el is not None and title_el.text) else None)
    doi = None
    for idno in root.findall(".//tei:idno", TEI_NS):
        if (idno.get("type") or "").lower() == "doi" and idno.text:
            doi = idno.text.strip()
            break
    if not title:
        return None, doi, "no_title_in_tei"
    return title, doi, None


# Markers that suggest GROBID extracted journal masthead/cover-page text
# along with (or instead of) the real article title. Seen 2026-05-13
# in the first upload-source remediation pass: GROBID titles like
# "0040-4039/88 $3.00 + .OO Printed in Great Britain Peqamon Press plc
# 2-MERCAPTOBENZOTHlAZOLE..." score high-severity even though the real
# title IS embedded in there, just diluted by masthead noise.
_MASTHEAD_PATTERNS = re.compile(
    r"(?:"
    r"\b\d{4}\s*-\s*\d{4}\s*/\s*\d{2,4}\b"             # ISSN-like + year (0040-4039/88)
    r"|\bPergamon\b|\bWiley[ -]VCH\b|\bElsevier\b"
    r"|\bSpringer\b|\bACS Publications\b"
    r"|\bRoyal Society of Chemistry\b|\bRSC\b"
    r"|\bAmerican Chemical Society\b"
    r"|\bPrinted in\b|\bPublished by\b"
    r"|\bResearch Article\b|\bResearch Briefing\b"
    r"|\bLetters? to [Nn]ature\b"
    r"|\bRESEARCH (?:ARTICLE|BRIEFING|REPORTS?)\b"
    r"|\bVol\.?\s*\d+\b|\bpp\.?\s*\d+\b"
    r"|\bdownloaded by\b|\bView Article Online\b"
    r")",
    re.IGNORECASE,
)


def _looks_like_masthead(title: Optional[str]) -> bool:
    """True when the GROBID title contains journal-masthead markers.

    Used as a severity-downgrade signal: if GROBID extracted masthead
    along with the real title, the low-jaccard score is misleading and
    the record shouldn't auto-queue at the default high threshold.
    Only the first ~120 chars are scanned (mastheads live at the start).
    """
    if not title:
        return False
    return bool(_MASTHEAD_PATTERNS.search(title[:120]))


def _score_severity(
    jaccard: Optional[float],
    has_html: bool,
    grobid_failed: bool,
    grobid_token_count: int = 0,
    doi_matches_grobid: bool = False,
    grobid_title_has_masthead: bool = False,
) -> Tuple[str, str]:
    """Bucket a record by likely-mismatch severity.

    Returns `(severity, downgrade_reason)`. The reason is the empty
    string when no heuristic downgrade fired, otherwise one of
    ``few_tokens`` / ``doi_match`` / ``masthead``.

    Boundaries come from the 2026-05-12 calibration: 5 known-bad all
    scored 0.000, 18 random samples all scored 0.818-1.000. The gap
    0.05-0.7 was empty. Three layered heuristics suppress false
    positives on `high`:

    - ``few_tokens``: GROBID returned <5 meaningful tokens. Common with
      running headers on Angewandte Chemie Communications and similar
      layouts where GROBID picks up a section heading instead of the
      real title.
    - ``doi_match``: GROBID extracted the same DOI that's already
      stored. If the DOI lines up, the PDF most likely IS what the
      record claims; the title difference is more likely a GROBID
      extraction quirk than a true splice (e.g. Nature pairing a
      Research Briefing PDF with the research paper's DOI).
    - ``masthead``: GROBID title contains journal-masthead markers
      (ISSN-like prefix, "Printed in", publisher names, etc.) — the
      real title is buried inside concatenated masthead text and the
      Jaccard is diluted by the noise.

    In all three cases a would-be `high` is downgraded to `medium` so
    the auto-queue at the default high threshold doesn't act on the
    signal alone. The user can still queue them explicitly with
    ``--severity-threshold medium``.
    """
    if grobid_failed or jaccard is None:
        return "unparseable", ""
    if jaccard < MISMATCH_THRESHOLD:    # < 0.3, production guard
        # Apply heuristics in order; first match wins as the reason.
        if grobid_token_count < 5:
            return "medium", "few_tokens"
        if doi_matches_grobid:
            return "medium", "doi_match"
        if grobid_title_has_masthead:
            return "medium", "masthead"
        return "high", ""
    if jaccard < 0.5:
        return "medium", ""
    if has_html:
        return "low", ""
    return "clean", ""


def _qdrant_url(path: str) -> str:
    return f"http://{QDRANT_HOST}:{QDRANT_PORT}/collections/{COLLECTION_NAME}{path}"


def _scroll_unaudited(source_filter: str = "all", batch: int = 200) -> Iterator[Tuple[int, Dict]]:
    """Yield `(point_id, payload)` for Qdrant points without an
    `_inspected_at` marker. Resume semantics: every prior run wrote
    the marker on inspected points, so subsequent runs naturally pick
    up where the last one left off.

    Source filter is applied in Python because Qdrant's filter DSL
    can't express "contributors array is empty / non-empty" without
    a payload index on the array element key.
    """
    offset = None
    while True:
        body: Dict = {
            "filter": {"must": [{"is_empty": {"key": "_inspected_at"}}]},
            "limit": batch,
            "with_payload": True,
            "with_vector": False,
        }
        if offset is not None:
            body["offset"] = offset
        r = requests.post(_qdrant_url("/points/scroll"), json=body, timeout=30)
        r.raise_for_status()
        data = r.json().get("result", {}) or {}
        for pt in data.get("points", []):
            payload = pt.get("payload") or {}
            contribs = payload.get("contributors") or []
            if source_filter == "upload" and not contribs:
                continue
            if source_filter == "crawler" and contribs:
                continue
            yield pt.get("id"), payload
        offset = data.get("next_page_offset")
        if not offset:
            return


def _set_audit_payload(point_id: int, fields: Dict, dry_run: bool = False) -> None:
    """Lazy backfill: partial-update the Qdrant point's payload with
    new audit keys. None-valued fields are skipped so we never store
    explicit nulls."""
    fields = {k: v for k, v in fields.items() if v is not None}
    if not fields:
        return
    if dry_run:
        print(f"        [DRY-RUN] would set_payload {list(fields.keys())} on {point_id}")
        return
    r = requests.post(
        _qdrant_url("/points/payload"),
        json={"payload": fields, "points": [point_id]},
        timeout=30,
    )
    r.raise_for_status()


def _delete_qdrant_point(point_id: int, dry_run: bool = False) -> None:
    if dry_run:
        print(f"        [DRY-RUN] would delete Qdrant point {point_id}")
        return
    r = requests.post(
        _qdrant_url("/points/delete"),
        json={"points": [point_id], "wait": True},
        timeout=30,
    )
    r.raise_for_status()


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _queue_one_for_reingest(
    point_id: int,
    payload: Dict,
    host_pdf: Path,
    log_writer: csv.DictWriter,
    dry_run: bool = False,
) -> bool:
    """Move PDF to inbox/, write reconstructed sidecar, log the action,
    delete the Qdrant point. PDF is moved, not copied; the rollback
    record is the log CSV. Returns True on success.
    """
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    new_uuid = uuid.uuid4().hex[:16]
    new_pdf = INBOX_DIR / f"{new_uuid}.pdf"
    new_sidecar = INBOX_DIR / f"{new_uuid}.contributor.json"
    doi = payload.get("doi")
    contribs = payload.get("contributors") or []
    contrib = (max(contribs, key=lambda c: c.get("upload_time") or "")
               if contribs else None)
    sidecar = {
        "contributor_email": (contrib or {}).get("email"),
        "contributor_username": (contrib or {}).get("username"),
        "contributor_display_name": (contrib or {}).get("display_name"),
        "research_group": (contrib or {}).get("group_slug") or "unknown",
        "research_group_display_name": (contrib or {}).get("group_display_name"),
        "uploaded_at": (contrib or {}).get("upload_time") or _utcnow_iso(),
        "original_filename": host_pdf.name,
    }
    # Deliberately NOT setting filename_doi_hint here — the very
    # reason we queued this record is that the stored DOI looks
    # suspect. Passing it would short-circuit the hardened pipeline's
    # GROBID-driven DOI extraction and likely reproduce the same
    # bad ingest. Operators can manually edit the sidecar to add a
    # known-good DOI hint before the reingest run.
    if dry_run:
        print(f"        [DRY-RUN] would move {host_pdf.name} -> inbox/{new_pdf.name}")
        print(f"        [DRY-RUN] would write sidecar (no DOI hint)")
        print(f"        [DRY-RUN] would write reingest log row")
        print(f"        [DRY-RUN] would delete Qdrant point {point_id}")
        return True

    try:
        shutil.move(str(host_pdf), str(new_pdf))
    except OSError as e:
        print(f"        [ERROR] failed to move PDF: {e}")
        return False
    try:
        with open(new_sidecar, "w") as f:
            json.dump(sidecar, f)
    except OSError as e:
        print(f"        [ERROR] failed to write sidecar: {e}")
        # Best-effort: undo the move so the original PDF location
        # stays canonical.
        try:
            shutil.move(str(new_pdf), str(host_pdf))
        except OSError:
            pass
        return False

    # Rollback record gets written BEFORE the Qdrant delete, so if
    # the delete fails we still know exactly what to restore.
    log_writer.writerow({
        "doi": doi or "",
        "point_id": point_id,
        "pdf_old_path": str(host_pdf),
        "pdf_new_path": str(new_pdf),
        "queued_at": _utcnow_iso(),
        "stored_title": (payload.get("title") or "")[:500],
        "stored_authors_json": json.dumps(payload.get("authors") or []),
        "stored_year": payload.get("year") or "",
        "stored_journal": payload.get("journal") or "",
        "contributors_json": json.dumps(contribs),
    })

    try:
        _delete_qdrant_point(point_id, dry_run=False)
    except Exception as e:
        print(f"        [ERROR] Qdrant delete failed: {e}")
        # Leave PDF in inbox; operator can retry the delete and/or
        # restore the Qdrant point from the log.
        return False
    return True


def find_metadata_mismatch(
    limit: int = 100,
    source_filter: str = "all",
    grobid_pace_secs: int = 30,
    no_grobid: bool = False,
    no_backfill: bool = False,
    report_out: Optional[str] = None,
    queue_for_reingest: bool = False,
    severity_threshold: str = "high",
    reingest_log: Optional[str] = None,
    dry_run: bool = False,
) -> None:
    """Detect title-vs-PDF mismatches, optionally queue suspects for
    reingest. See docs/PAPER-INGEST-AUDIT.md."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = Path(report_out or f"metadata-mismatch-{ts}.csv")
    reingest_log_path = Path(reingest_log or f"reingest-log-{ts}.csv") if queue_for_reingest else None

    severity_rank = {"clean": 0, "low": 1, "medium": 2, "high": 3, "unparseable": 1}
    threshold_rank = severity_rank.get(severity_threshold, 3)

    print(f"=== find-metadata-mismatch ===")
    print(f"  limit            : {limit}")
    print(f"  source           : {source_filter}")
    print(f"  GROBID pacing    : {grobid_pace_secs}s ({'OFF' if no_grobid else 'ON'})")
    print(f"  lazy backfill    : {'OFF' if no_backfill else 'ON'}")
    print(f"  queue for reingest: {'YES' if queue_for_reingest else 'NO'}"
          f"{' (severity>=' + severity_threshold + ')' if queue_for_reingest else ''}")
    print(f"  dry run          : {dry_run}")
    print(f"  report           : {report_path}")
    if reingest_log_path:
        print(f"  reingest log     : {reingest_log_path}")
    print()

    report_fields = [
        "doi", "point_id", "severity", "downgrade_reason", "jaccard",
        "ingest_source", "contributors_groups",
        "stored_title", "grobid_title", "grobid_doi",
        "doi_matches_grobid", "has_html", "has_masthead",
        "crossref_doi_rejected", "pdf_path",
        "inspected_at", "queued_for_reingest",
    ]
    reingest_fields = [
        "doi", "point_id", "pdf_old_path", "pdf_new_path", "queued_at",
        "stored_title", "stored_authors_json", "stored_year",
        "stored_journal", "contributors_json",
    ]

    counts = {"inspected": 0, "skipped_no_pdf": 0, "queued": 0,
              "high": 0, "medium": 0, "low": 0, "clean": 0, "unparseable": 0}
    last_grobid_call_at: Optional[float] = None

    report_f = open(report_path, "w", newline="")
    report_writer = csv.DictWriter(report_f, fieldnames=report_fields)
    report_writer.writeheader()

    reingest_f = None
    reingest_writer = None
    if reingest_log_path is not None:
        reingest_f = open(reingest_log_path, "w", newline="")
        reingest_writer = csv.DictWriter(reingest_f, fieldnames=reingest_fields)
        reingest_writer.writeheader()

    try:
        for point_id, payload in _scroll_unaudited(source_filter):
            if counts["inspected"] >= limit:
                break

            doi = payload.get("doi") or ""
            stored_title = payload.get("title") or ""
            contribs = payload.get("contributors") or []
            contrib_groups = ";".join(
                sorted({c.get("group_slug") or "" for c in contribs if c.get("group_slug")})
            )
            ingest_source = (
                "upload" if contribs else
                ("crawler" if doi else "unknown")
            )
            has_html = bool(
                "<b>" in stored_title or "<i>" in stored_title
                or "<sup>" in stored_title or "<sub>" in stored_title
                or "<mml:" in stored_title
            )
            host_pdf = _container_pdf_to_host(payload.get("pdf_path"))

            print(f"[{counts['inspected']+1}/{limit}] doi={doi or '<none>'} source={ingest_source}")

            if host_pdf is None:
                counts["skipped_no_pdf"] += 1
                counts["inspected"] += 1
                report_writer.writerow({
                    "doi": doi, "point_id": point_id, "severity": "unparseable",
                    "jaccard": "", "ingest_source": ingest_source,
                    "contributors_groups": contrib_groups,
                    "stored_title": stored_title[:300], "grobid_title": "",
                    "grobid_doi": "", "has_html": has_html,
                    "crossref_doi_rejected": payload.get("_crossref_doi_rejected") or "",
                    "pdf_path": payload.get("pdf_path") or "",
                    "inspected_at": _utcnow_iso(), "queued_for_reingest": False,
                })
                print(f"        [skip] PDF not on disk")
                continue

            # Re-extract title from the PDF. Two paths:
            #   --no-grobid:  use pdftotext first-page text as a noisy
            #                 proxy. Useful for a quick visual sweep,
            #                 but we never write this back into the
            #                 `_grobid_title` audit field (it would
            #                 poison the field with mastheads / DOIs).
            #   default:      GROBID processHeaderDocument, paced.
            grobid_title: Optional[str] = None
            grobid_doi: Optional[str] = None
            title_for_compare: Optional[str] = None
            grobid_failed = False
            if no_grobid:
                proxy = _pdftotext_first_page(host_pdf)
                title_for_compare = (proxy or "").strip() or None
                if title_for_compare is None:
                    grobid_failed = True
            else:
                if last_grobid_call_at is not None:
                    elapsed = time.time() - last_grobid_call_at
                    wait = max(0.0, grobid_pace_secs - elapsed)
                    if wait > 0:
                        print(f"        [pacing] sleep {wait:.0f}s")
                        time.sleep(wait)
                last_grobid_call_at = time.time()
                grobid_title, grobid_doi, err = _grobid_header_title(host_pdf)
                title_for_compare = grobid_title
                if err:
                    print(f"        [GROBID skip] {err}")
                    grobid_failed = True

            jaccard: Optional[float] = None
            grobid_token_count = 0
            if title_for_compare and stored_title:
                jaccard = _title_similarity(title_for_compare, stored_title)
                grobid_token_count = len(_normalize_title_tokens(title_for_compare))
                print(f"        sim={jaccard:.3f}  grobid_tokens={grobid_token_count}")

            # Heuristic signals: did GROBID extract the same DOI we
            # already have? Does the GROBID title carry masthead text?
            # Both downgrade a would-be `high` to `medium` so the auto-
            # queue at the default high threshold doesn't fire on
            # what's almost certainly a clean record with noisy title.
            doi_matches_grobid = bool(
                doi and grobid_doi
                and doi.strip().lower() == grobid_doi.strip().lower()
            )
            grobid_title_has_masthead = _looks_like_masthead(grobid_title)
            severity, downgrade_reason = _score_severity(
                jaccard, has_html, grobid_failed, grobid_token_count,
                doi_matches_grobid=doi_matches_grobid,
                grobid_title_has_masthead=grobid_title_has_masthead,
            )
            if downgrade_reason:
                print(f"        [downgrade] high -> medium (reason: {downgrade_reason})")
            counts[severity] = counts.get(severity, 0) + 1

            # Lazy backfill — write audit fields onto the Qdrant point
            # so future runs (with the resume filter) skip this record
            # and the hardened pipeline / remediation tool have ground
            # truth to work with. Two important constraints:
            #   - In --no-grobid mode we DON'T mark _inspected_at,
            #     because the pdftotext proxy isn't authoritative;
            #     the record still needs a real GROBID inspection.
            #   - We never write pdftotext output as _grobid_title;
            #     it would mislead the next run.
            audit_payload: Dict = {
                "_ingest_source": ingest_source,
            }
            if not no_grobid:
                audit_payload["_grobid_title"] = grobid_title
                audit_payload["_grobid_doi"] = grobid_doi
                audit_payload["_inspected_at"] = _utcnow_iso()
            if not no_backfill:
                try:
                    _set_audit_payload(point_id, audit_payload, dry_run=dry_run)
                except Exception as e:
                    print(f"        [WARN] payload backfill failed: {e}")

            queued = False
            if (queue_for_reingest
                    and severity_rank.get(severity, 0) >= threshold_rank
                    and severity in ("high", "medium")  # never queue 'clean'/'low'
                    and host_pdf is not None):
                print(f"        [queue] severity={severity}")
                if _queue_one_for_reingest(
                    point_id, payload, host_pdf, reingest_writer, dry_run=dry_run
                ):
                    counts["queued"] += 1
                    queued = True

            report_writer.writerow({
                "doi": doi, "point_id": point_id, "severity": severity,
                "downgrade_reason": downgrade_reason,
                "jaccard": f"{jaccard:.3f}" if jaccard is not None else "",
                "ingest_source": ingest_source,
                "contributors_groups": contrib_groups,
                "stored_title": stored_title[:300],
                # In --no-grobid mode this is the pdftotext proxy
                # (clearly labelled by the source column).
                "grobid_title": (title_for_compare or "")[:300],
                "grobid_doi": grobid_doi or "",
                "doi_matches_grobid": doi_matches_grobid,
                "has_html": has_html,
                "has_masthead": grobid_title_has_masthead,
                "crossref_doi_rejected": payload.get("_crossref_doi_rejected") or "",
                "pdf_path": payload.get("pdf_path") or "",
                "inspected_at": _utcnow_iso(),
                "queued_for_reingest": queued,
            })
            report_f.flush()
            if reingest_f is not None:
                reingest_f.flush()
            counts["inspected"] += 1
            print(f"        verdict={severity}")
    finally:
        report_f.close()
        if reingest_f is not None:
            reingest_f.close()

    print()
    print(f"=== Summary ===")
    for k in ("inspected", "high", "medium", "low", "clean", "unparseable",
              "skipped_no_pdf", "queued"):
        print(f"  {k:<18s}: {counts.get(k, 0)}")
    print(f"  report          : {report_path}")
    if reingest_log_path is not None:
        print(f"  reingest log    : {reingest_log_path}")


def reingest_queue(
    pipeline_script: Optional[str] = None,
    limit: int = 50,
    pace_secs: int = 30,
    dry_run: bool = False,
) -> None:
    """Drive reingest of PDFs that find-metadata-mismatch queued.

    Walks `INBOX_DIR` for `*.pdf` files with a sibling
    `*.contributor.json` sidecar and invokes paper_pipeline.py as a
    subprocess on each, paced.

    Note: the pipeline stores the new Qdrant record but does NOT
    relocate the PDF out of inbox/ — that's normally done by the
    admin/ingest endpoint, which we bypass here. The reingested
    record will have pdf_path pointing into inbox/, which is fine
    for record-keeping but means `/api/papers/<doi>/pdf` can't serve
    the file until an operator (or a future follow-up command)
    moves it to /papers/pdf/doi_<new_doi>.pdf. This is a deliberate
    v1 limit: the goal here is to surface the queue, not to fully
    re-house the PDF.
    """
    if pipeline_script is None:
        pipeline_script = str(Path(__file__).resolve().parent / "paper_pipeline.py")
    if not Path(pipeline_script).is_file():
        print(f"[ERROR] paper_pipeline.py not found at {pipeline_script}")
        sys.exit(1)
    if not INBOX_DIR.is_dir():
        print(f"[ERROR] inbox not present: {INBOX_DIR}")
        sys.exit(1)

    queued = sorted(p for p in INBOX_DIR.glob("*.pdf")
                    if p.with_suffix(".contributor.json").is_file())
    if not queued:
        print(f"No queued PDFs found in {INBOX_DIR}")
        return
    queued = queued[:limit]
    print(f"=== reingest-queue ===")
    print(f"  pipeline    : {pipeline_script}")
    print(f"  inbox       : {INBOX_DIR}")
    print(f"  queued (cap): {len(queued)}")
    print(f"  pace        : {pace_secs}s between pipeline runs")
    print(f"  dry run     : {dry_run}")
    print()

    n_ok = n_fail = 0
    for i, pdf in enumerate(queued, 1):
        print(f"[{i}/{len(queued)}] {pdf.name}")
        if dry_run:
            print(f"        [DRY-RUN] would invoke paper_pipeline.py --single {pdf}")
            continue
        try:
            proc = subprocess.run(
                ["python3", pipeline_script, "--single", str(pdf)],
                capture_output=True, text=True, timeout=600,
            )
            if proc.returncode == 0:
                n_ok += 1
                print(f"        [ok]")
            else:
                n_fail += 1
                print(f"        [fail rc={proc.returncode}]")
                print((proc.stdout or "").splitlines()[-3:] if proc.stdout else "")
        except subprocess.TimeoutExpired:
            n_fail += 1
            print(f"        [timeout after 600s]")
        if i < len(queued) and pace_secs > 0:
            print(f"        [pacing] sleep {pace_secs}s")
            time.sleep(pace_secs)

    print()
    print(f"=== Summary ===")
    print(f"  ok      : {n_ok}")
    print(f"  failed  : {n_fail}")
    print(f"  pending : {max(0, len(queued) - n_ok - n_fail)}")


# ==============================================================================
# Contributor Re-attribution (formerly reattribute_unknown.py)
# ==============================================================================
# Retroactively assign group attribution to papers that were ingested
# before their uploader was added to `config/contributors.yml`.
#
# When a user uploads via upload.muninai.org and their email isn't on
# the allowlist yet, /api/admin/ingest still ingests the paper but
# stamps the contributor as `group_slug: "unknown"`. After the admin
# adds the uploader to contributors.yml, this command walks the
# Qdrant `papers` collection, finds points whose contributors[] list
# contains an `unknown` entry whose email IS now allowlisted, and
# rewrites that entry with the now-known fields (display_name,
# username, group_slug, group_display_name). Optionally also updates
# the matching Neo4j :Contributor node + CONTRIBUTED edge.
#
# Idempotent. Safe to re-run on every contributors.yml change. Run
# nightly by the (Phase E) munin-paper-reattribute.timer service.

CONTRIBUTORS_CONFIG_PATH = os.getenv(
    "CONTRIBUTORS_CONFIG_PATH", "/opt/munin/config/contributors.yml"
)


def _load_contributors_yaml(path: str = CONTRIBUTORS_CONFIG_PATH) -> Dict[str, Dict]:
    """Parse contributors.yml into a {email: entry} map.

    Supports both `email:` single and `emails:` list forms. Mirrors
    retrieval/main.py::_load_contributors so the two stay
    behaviour-compatible.
    """
    try:
        with open(path, encoding="utf-8") as f:
            doc = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as e:
        print(f"[ERROR] contributors.yml unreadable: {e}", file=sys.stderr)
        return {}
    out: Dict[str, Dict] = {}
    for entry in doc.get("contributors", []) or []:
        addrs: List[str] = []
        single = entry.get("email")
        if isinstance(single, str) and single.strip():
            addrs.append(single.strip().lower())
        listed = entry.get("emails")
        if isinstance(listed, list):
            for a in listed:
                if isinstance(a, str) and a.strip():
                    addrs.append(a.strip().lower())
        for addr in addrs:
            out[addr] = entry
    return out


def _rebuild_unknown_contributor(c: Dict, allowlist: Dict[str, Dict]) -> Optional[Dict]:
    """If `c` is an `unknown`-tagged entry whose email is now
    allowlisted, return the rebuilt entry. Otherwise return None."""
    if not isinstance(c, dict):
        return None
    if (c.get("group_slug") or "") != "unknown":
        return None
    email = (c.get("email") or "").strip().lower()
    if not email:
        return None
    known = allowlist.get(email)
    if known is None:
        return None
    return {
        "email": email,
        "username": known.get("username"),
        "display_name": known.get("display_name"),
        "group_slug": known.get("research_group") or "unknown",
        "group_display_name": known.get("research_group_display_name"),
        "upload_time": c.get("upload_time"),
    }


def reattribute(dry_run: bool = False, no_neo4j: bool = False) -> int:
    """Walk Qdrant for unknown-tagged contributors whose email is now
    allowlisted; rewrite them with the proper group slug + display
    fields. Optionally mirror the change into Neo4j.

    Returns 0 on success, 2 on missing allowlist.
    """
    allowlist = _load_contributors_yaml()
    if not allowlist:
        print("[ERROR] No contributors loaded; nothing to do.", file=sys.stderr)
        return 2
    print(f"[INFO] Allowlist has {len(allowlist)} email(s).")

    client = get_qdrant_client()
    if client is None:
        print("[ERROR] Qdrant unavailable", file=sys.stderr)
        return 2

    from qdrant_client.http import models as qm
    flt = qm.Filter(
        must=[
            qm.FieldCondition(
                key="contributors[].group_slug",
                match=qm.MatchValue(value="unknown"),
            )
        ]
    )
    print(f"[INFO] Scrolling {COLLECTION_NAME} for unknown contributors...")
    t0 = time.time()
    candidates = []
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=COLLECTION_NAME,
            scroll_filter=flt,
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        if not points:
            break
        candidates.extend(points)
        if offset is None:
            break
    print(f"[INFO] {len(candidates)} candidate point(s) in {time.time() - t0:.1f}s")

    neo4j_driver = None
    if not no_neo4j and NEO4J_PASSWORD:
        try:
            from neo4j import GraphDatabase
            neo4j_driver = GraphDatabase.driver(
                NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD)
            )
            with neo4j_driver.session() as s:
                s.run("RETURN 1")
        except Exception as e:
            print(f"[WARN] Neo4j unavailable, skipping graph updates: {e}")
            neo4j_driver = None

    updated = 0
    skipped = 0
    by_group: Dict[str, int] = {}

    for p in candidates:
        payload = p.payload or {}
        contribs = payload.get("contributors") or []
        new_contribs: List[Dict] = []
        any_change = False
        for c in contribs:
            rebuilt = _rebuild_unknown_contributor(c, allowlist)
            if rebuilt is not None:
                new_contribs.append(rebuilt)
                any_change = True
                by_group[rebuilt["group_slug"]] = by_group.get(rebuilt["group_slug"], 0) + 1
            else:
                new_contribs.append(c if isinstance(c, dict) else {})
        if not any_change:
            skipped += 1
            continue
        if dry_run:
            updated += 1
            continue
        try:
            client.set_payload(
                collection_name=COLLECTION_NAME,
                payload={"contributors": new_contribs},
                points=[p.id],
                wait=False,
            )
        except Exception as e:
            print(f"[WARN] Qdrant set_payload failed for {p.id}: {e}")
            continue
        # Mirror into Neo4j: MERGE the (now-known) :Contributor node
        # and CONTRIBUTED edge for each newly-attributed entry.
        if neo4j_driver is not None:
            doi = payload.get("doi")
            for c in new_contribs:
                if not isinstance(c, dict) or not c.get("email"):
                    continue
                if c.get("group_slug") == "unknown":
                    continue
                try:
                    with neo4j_driver.session() as s:
                        if doi:
                            s.run(
                                """
                                MERGE (co:Contributor {email: $email})
                                SET co.username = coalesce($username, co.username),
                                    co.display_name = coalesce($display_name, co.display_name),
                                    co.group_slug = $group_slug,
                                    co.group_display_name = coalesce($group_display_name, co.group_display_name)
                                WITH co
                                MATCH (p:Paper {doi: $doi})
                                MERGE (co)-[r:CONTRIBUTED]->(p)
                                """,
                                email=c["email"],
                                username=c.get("username"),
                                display_name=c.get("display_name"),
                                group_slug=c["group_slug"],
                                group_display_name=c.get("group_display_name"),
                                doi=doi,
                            )
                except Exception as e:
                    print(f"[WARN] Neo4j update failed for {p.id}: {e}")
        updated += 1
        if updated % 100 == 0:
            print(f"[INFO]   updated {updated} so far...", flush=True)

    if neo4j_driver is not None:
        neo4j_driver.close()

    print(
        f"\n[DONE]{' DRY RUN' if dry_run else ''}\n"
        f"  candidates with group_slug=unknown: {len(candidates)}\n"
        f"  re-attributed: {updated}\n"
        f"  unchanged (still unknown / not in allowlist): {skipped}\n"
        f"  by group:"
    )
    for slug, n in sorted(by_group.items(), key=lambda x: -x[1]):
        print(f"    {slug}: {n}")
    return 0


# ==============================================================================
# Unified detection + quarantine (Phase D of 2026-05-13 consolidation)
# ==============================================================================
# `detect` dispatches to the existing per-kind detector functions
# but exposes a single CLI surface and a single soft-action verb
# (`--auto-quarantine`) that replaces today's destructive
# `--auto-remove`. Quarantined records keep their PDF (preserved
# in /papers/pdf/quarantine/), keep their Neo4j citation graph
# node, and get their Qdrant point deleted so paper_search doesn't
# surface them. The state sidecar carries the reason + the audit
# findings; the operator confirms via `review` (Phase E).
#
# Old find-*/repair-* subcommands stay functional for backward
# compat but print a deprecation notice pointing at `detect`.

DETECT_KINDS = ("metadata-mismatch", "low-quality", "short",
                "metadata-unverifiable", "orphan")


def _quarantine_by_doi(
    doi: str,
    reason: str,
    audit_findings: Optional[Dict] = None,
    dry_run: bool = False,
) -> bool:
    """Soft-action: move a paper out of the searchable corpus into
    quarantine for operator review.

    Versus `remove_paper_by_doi`:
      - Keeps the PDF (moved to /papers/pdf/quarantine/, not deleted).
      - Keeps the Neo4j node (citation graph intact).
      - Does NOT add to blocklist (operator may decide to keep on review).
      - Deletes the Qdrant point so paper_search doesn't surface it.
      - Writes a state sidecar capturing the reason + audit findings
        so the review tool (Phase E) has full context.

    Returns True on success, False if the PDF couldn't be located
    or moved.
    """
    print(f"\n[quarantine] {doi} (reason: {reason})")
    if dry_run:
        print(f"  [DRY-RUN] would move PDF + delete Qdrant point + write sidecar")
        return True

    # Locate the PDF on disk via existing helper.
    pdf_path = find_pdf_by_doi(doi)
    if pdf_path is None:
        print(f"  [WARN] PDF not found on disk; skipping quarantine for {doi}")
        return False

    # Move PDF + contributor sidecar (if any) to quarantine/.
    quar_dir = PDF_DIR / "quarantine"
    quar_dir.mkdir(parents=True, exist_ok=True)
    dst_pdf = quar_dir / pdf_path.name
    try:
        if dst_pdf.exists():
            pdf_path.unlink()
        else:
            shutil.move(str(pdf_path), str(dst_pdf))
    except OSError as e:
        print(f"  [ERROR] PDF move failed: {e}")
        return False
    contrib_src = pdf_path.with_name(f"{pdf_path.stem}.contributor.json")
    contrib_dst = dst_pdf.with_name(f"{dst_pdf.stem}.contributor.json")
    if contrib_src.is_file() and contrib_src != contrib_dst:
        try:
            shutil.move(str(contrib_src), str(contrib_dst))
        except OSError:
            pass

    # Build / update the state sidecar.
    sidecar_path = dst_pdf.with_name(f"{dst_pdf.stem}.state.json")
    now = _utcnow_iso()
    existing = None
    if sidecar_path.is_file():
        try:
            with open(sidecar_path, encoding="utf-8") as f:
                existing = json.load(f)
        except (OSError, json.JSONDecodeError):
            existing = None
    first_seen = (existing or {}).get("first_seen_at") or now
    history = list((existing or {}).get("history") or [])
    history.append({
        "at": now,
        "state": "quarantine",
        "via": "detect_auto_quarantine",
        "reason": reason,
    })
    sidecar = {
        "schema_version": 1,
        "doi": doi,
        "state": "quarantine",
        "ingest_path": (existing or {}).get("ingest_path", "unknown"),
        "quarantine_reasons": [reason],
        "first_seen_at": first_seen,
        "last_modified_at": now,
        "contributor": (existing or {}).get("contributor"),
        "audit_findings": audit_findings or (existing or {}).get("audit_findings"),
        "history": history,
    }
    try:
        with open(sidecar_path, "w", encoding="utf-8") as f:
            json.dump(sidecar, f, indent=2)
    except OSError as e:
        print(f"  [WARN] state sidecar write failed: {e}")

    # Delete the Qdrant point so paper_search doesn't return it.
    qdrant = get_qdrant_client()
    if qdrant is not None:
        try:
            remove_from_qdrant(qdrant, doi, dry_run=False)
        except Exception as e:
            print(f"  [WARN] Qdrant point delete failed: {e}")

    # Drop the watcher's "already seen" marker so the next pass
    # doesn't re-attempt this file (it's gone from /papers/pdf/
    # anyway; this is defence in depth).
    marker = PROCESSED_DIR / f"{pdf_path.stem}.json"
    if marker.is_file():
        try:
            marker.unlink()
        except OSError:
            pass

    print(f"  [OK] quarantined to {dst_pdf}")
    return True


def detect(
    kinds: List[str],
    limit: Optional[int] = None,
    source: str = "all",
    report_out: Optional[str] = None,
    auto_quarantine: bool = False,
    dry_run: bool = False,
    # Pass-through flags per kind:
    grobid_pace_secs: int = 30,
    no_grobid: bool = False,
    no_backfill: bool = False,
    severity_threshold: str = "high",
    min_pages: int = 3,
    max_check: int = 200,
    enrich: bool = True,
) -> int:
    """Dispatch a detection run over one or more kinds.

    Each kind reuses the existing single-purpose detector function
    underneath (`find_metadata_mismatch`, `find_low_quality_papers`,
    `find_short_papers`, `repair_and_clean`). The unification is
    primarily at the CLI surface; the per-kind logic is unchanged
    so behaviour stays familiar.

    With ``--auto-quarantine`` (the new soft action), flagged records
    are moved to ``pdf/quarantine/`` with their state sidecar updated.
    Without it, the run is a detection-only pass that writes a CSV /
    DOI list (kind-specific format).

    Returns 0 on success, non-zero if any kind reported an error.
    """
    bad = [k for k in kinds if k not in DETECT_KINDS]
    if bad:
        print(f"[ERROR] unknown detect kind(s): {bad}", file=sys.stderr)
        print(f"        valid: {', '.join(DETECT_KINDS)}", file=sys.stderr)
        return 2

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    print(f"=== detect ===")
    print(f"  kinds            : {','.join(kinds)}")
    print(f"  limit            : {limit if limit is not None else '(no cap)'}")
    print(f"  auto-quarantine  : {'YES' if auto_quarantine else 'NO'}")
    print(f"  dry run          : {dry_run}")
    print()

    overall_rc = 0
    for kind in kinds:
        kind_report = report_out or f"detect-{kind}-{ts}.csv"
        kind_report_path = Path(kind_report)
        if len(kinds) > 1 and report_out:
            # When multiple kinds share a single report path, suffix
            # with kind so they don't clobber.
            kind_report_path = kind_report_path.with_name(
                f"{kind_report_path.stem}.{kind}{kind_report_path.suffix}"
            )

        if kind == "metadata-mismatch":
            try:
                find_metadata_mismatch(
                    limit=limit or 100,
                    source_filter=source,
                    grobid_pace_secs=grobid_pace_secs,
                    no_grobid=no_grobid,
                    no_backfill=no_backfill,
                    report_out=str(kind_report_path),
                    queue_for_reingest=False,
                    severity_threshold=severity_threshold,
                    reingest_log=None,
                    dry_run=dry_run,
                )
                # Auto-quarantine post-pass: read the CSV, quarantine
                # severity>=high rows.
                if auto_quarantine and kind_report_path.is_file():
                    n_quar = _auto_quarantine_from_mismatch_csv(
                        kind_report_path, dry_run=dry_run,
                        severity_threshold=severity_threshold,
                    )
                    print(f"\n[detect/metadata-mismatch] auto-quarantined {n_quar} record(s)")
            except Exception as e:
                print(f"[ERROR] metadata-mismatch run failed: {e}", file=sys.stderr)
                overall_rc = 1

        elif kind == "low-quality":
            try:
                find_low_quality_papers(
                    output_file=str(kind_report_path),
                    auto_remove=False,         # never auto-remove here
                    limit=limit,
                    confirm=True,              # programmatic call
                    enrich=enrich,
                    max_check=max_check,
                )
                if auto_quarantine and kind_report_path.is_file():
                    n_quar = _auto_quarantine_from_doi_list(
                        kind_report_path, reason="low_quality",
                        limit=limit, dry_run=dry_run,
                    )
                    print(f"\n[detect/low-quality] auto-quarantined {n_quar} record(s)")
            except Exception as e:
                print(f"[ERROR] low-quality run failed: {e}", file=sys.stderr)
                overall_rc = 1

        elif kind == "short":
            try:
                find_short_papers(
                    min_pages=min_pages,
                    output_file=str(kind_report_path),
                    auto_remove=False,
                    limit=limit,
                    confirm=True,
                )
                if auto_quarantine and kind_report_path.is_file():
                    n_quar = _auto_quarantine_from_doi_list(
                        kind_report_path, reason=f"short_pdf_lt_{min_pages}pages",
                        limit=limit, dry_run=dry_run,
                    )
                    print(f"\n[detect/short] auto-quarantined {n_quar} record(s)")
            except Exception as e:
                print(f"[ERROR] short run failed: {e}", file=sys.stderr)
                overall_rc = 1

        elif kind == "metadata-unverifiable":
            # The historic "repair-and-clean --auto-remove" path:
            # walks Neo4j, re-enriches from OpenAlex / S2 / Crossref,
            # quarantines records where ALL three sources fail.
            # We invoke repair_and_clean with auto_remove=False to
            # get its detection output, then post-process to
            # quarantine instead of remove.
            try:
                # repair_and_clean writes its DOI list via the
                # --output flag; we'll route through that.
                repair_and_clean(
                    source="neo4j",
                    max_check=max_check,
                    auto_remove=False,
                    limit=limit,
                    confirm=True,
                    output_file=str(kind_report_path),
                    dry_run=dry_run,
                    check_pdf=False,
                    export_orphaned=None,
                )
                if auto_quarantine and kind_report_path.is_file():
                    n_quar = _auto_quarantine_from_doi_list(
                        kind_report_path, reason="metadata_unverifiable",
                        limit=limit, dry_run=dry_run,
                    )
                    print(f"\n[detect/metadata-unverifiable] auto-quarantined {n_quar} record(s)")
            except Exception as e:
                print(f"[ERROR] metadata-unverifiable run failed: {e}", file=sys.stderr)
                overall_rc = 1

        elif kind == "orphan":
            # Find live Qdrant records whose PDF is missing from disk.
            try:
                n_orphan = _detect_orphans(
                    report_path=kind_report_path,
                    limit=limit, dry_run=dry_run,
                    auto_quarantine=auto_quarantine,
                )
                print(f"\n[detect/orphan] found {n_orphan} orphan record(s)")
            except Exception as e:
                print(f"[ERROR] orphan run failed: {e}", file=sys.stderr)
                overall_rc = 1

    return overall_rc


def _auto_quarantine_from_mismatch_csv(
    csv_path: Path,
    dry_run: bool,
    severity_threshold: str = "high",
) -> int:
    """Walk a find-metadata-mismatch CSV and quarantine severity-high
    rows (or severity-medium too if threshold='medium')."""
    promote_at = {"high": 3, "medium": 2}.get(severity_threshold, 3)
    rank = {"clean": 0, "low": 1, "medium": 2, "high": 3, "unparseable": 1}
    n_quar = 0
    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            severity = row.get("severity") or ""
            if rank.get(severity, 0) < promote_at:
                continue
            doi = row.get("doi") or ""
            if not doi:
                continue
            audit = {
                "jaccard": row.get("jaccard"),
                "grobid_title": row.get("grobid_title"),
                "downgrade_reason": row.get("downgrade_reason"),
            }
            if _quarantine_by_doi(doi, f"metadata_mismatch_{severity}",
                                  audit_findings=audit, dry_run=dry_run):
                n_quar += 1
    return n_quar


def _auto_quarantine_from_doi_list(
    list_path: Path, reason: str,
    limit: Optional[int], dry_run: bool,
) -> int:
    """Walk a plain DOI-list file (one per line, optionally
    whitespace + comment) and quarantine each. Used by `low-quality`,
    `short`, and `metadata-unverifiable` kinds."""
    n_quar = 0
    with open(list_path, encoding="utf-8") as f:
        for line in f:
            doi = line.strip().split()[0] if line.strip() else ""
            if not doi or doi.startswith("#"):
                continue
            if limit is not None and n_quar >= limit:
                break
            if _quarantine_by_doi(doi, reason, dry_run=dry_run):
                n_quar += 1
    return n_quar


def _detect_orphans(
    report_path: Path, limit: Optional[int],
    dry_run: bool, auto_quarantine: bool,
) -> int:
    """Scroll Qdrant for live records whose PDF is missing from disk.
    Replaces the historic qdrant_repair_sweep.py functionality.

    Currently emits the orphan DOIs to a CSV; auto-quarantine would
    leave the records in Qdrant (no PDF to move into quarantine/),
    so for now this kind is detect-only."""
    qdrant = get_qdrant_client()
    if qdrant is None:
        return 0
    n_orphan = 0
    with open(report_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["doi", "pdf_path", "title"])
        offset = None
        while True:
            points, offset = qdrant.scroll(
                collection_name=COLLECTION_NAME,
                limit=200, offset=offset,
                with_payload=True, with_vectors=False,
            )
            if not points:
                break
            for p in points:
                payload = p.payload or {}
                doi = payload.get("doi") or ""
                if not doi:
                    continue
                pdf = _container_pdf_to_host(payload.get("pdf_path"))
                if pdf is not None and pdf.is_file():
                    continue
                writer.writerow([doi, payload.get("pdf_path") or "",
                                 (payload.get("title") or "")[:200]])
                n_orphan += 1
                if limit is not None and n_orphan >= limit:
                    return n_orphan
            if offset is None:
                break
    return n_orphan


# Deprecation helper used by the old find-*/repair-* subcommands.
def _deprecation_notice(old: str, new: str) -> None:
    print(f"\n[DEPRECATED] `{old}` is deprecated; use `{new}` instead.")
    print(f"             Old form continues to work but will be removed in")
    print(f"             a future commit. See INGEST.md.\n", flush=True)


def main():
    parser = argparse.ArgumentParser(
        description="Munin Paper Cleanup - Remove papers from all databases"
    )
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # remove command
    remove_parser = subparsers.add_parser("remove", help="Remove a single paper by DOI")
    remove_parser.add_argument("--doi", required=True, help="DOI of paper to remove")
    remove_parser.add_argument("--dry-run", action="store_true", help="Show what would be removed without actually removing")
    remove_parser.add_argument("--no-blocklist", action="store_true", help="Don't add DOI to blocklist")

    # find-low-quality command
    low_quality_parser = subparsers.add_parser("find-low-quality", help="[DEPRECATED] Use `detect --kinds low-quality` instead")
    low_quality_parser.add_argument("--output", help="Write found DOIs to this file")
    low_quality_parser.add_argument("--auto-remove", action="store_true", help="Automatically remove found papers from all databases")
    low_quality_parser.add_argument("--limit", type=int, help="Maximum number of papers to auto-remove")
    low_quality_parser.add_argument("--confirm", action="store_true", help="Required for auto-remove without --limit")
    low_quality_parser.add_argument("--no-enrich", action="store_true", help="Don't enrich papers from OpenAlex (just detect)")
    low_quality_parser.add_argument("--max-check", type=int, default=500, help="Maximum papers to check from Neo4j (default: 500)")

    # find-short command
    short_parser = subparsers.add_parser("find-short", help="[DEPRECATED] Use `detect --kinds short` instead")
    short_parser.add_argument("--min-pages", type=int, default=3, help="Minimum pages required (default: 3)")
    short_parser.add_argument("--output", help="Write found DOIs to this file")
    short_parser.add_argument("--auto-remove", action="store_true", help="Automatically remove found papers from all databases")
    short_parser.add_argument("--limit", type=int, help="Maximum number of papers to auto-remove")
    short_parser.add_argument("--confirm", action="store_true", help="Required for auto-remove without --limit")

    # bulk-remove command
    bulk_parser = subparsers.add_parser("bulk-remove", help="Remove multiple papers from a file")
    bulk_parser.add_argument("--file", required=True, help="File containing DOIs (one per line)")
    bulk_parser.add_argument("--dry-run", action="store_true", help="Show what would be removed without actually removing")
    bulk_parser.add_argument("--no-blocklist", action="store_true", help="Don't add DOIs to blocklist")

    # verify-doi command
    verify_parser = subparsers.add_parser("verify-doi", help="Verify a single DOI against all metadata sources")
    verify_parser.add_argument("doi", help="DOI to verify (e.g., 10.1016/0021-9991(77)90112-7)")

    # repair-and-clean command
    repair_parser = subparsers.add_parser("repair-and-clean", help="[DEPRECATED] Use `detect --kinds metadata-unverifiable --auto-quarantine` instead")
    repair_parser.add_argument("--source", choices=["neo4j", "processed", "both"], default="both",
                               help="Which papers to scan (default: both)")
    repair_parser.add_argument("--max-check", type=int, default=500, help="Maximum papers to check (default: 500)")
    repair_parser.add_argument("--auto-remove", action="store_true", help="Automatically remove flagged papers")
    repair_parser.add_argument("--limit", type=int, help="Maximum papers to auto-remove")
    repair_parser.add_argument("--confirm", action="store_true", help="Required for auto-remove without --limit")
    repair_parser.add_argument("--output", help="Save results to JSON file")
    repair_parser.add_argument("--dry-run", action="store_true", help="Don't modify databases")
    repair_parser.add_argument("--no-pdf-check", action="store_true", help="Don't check if PDFs exist")
    repair_parser.add_argument("--export-orphaned", metavar="FILE", help="Export orphaned DOIs (missing PDF) to file for re-crawling")

    # scan-processed command
    scan_parser = subparsers.add_parser("scan-processed", help="[DEPRECATED] Use `detect --kinds orphan` instead")
    scan_parser.add_argument("--check-metadata", action="store_true", help="Check each paper against metadata sources")
    scan_parser.add_argument("--check-pdf", action="store_true", help="Check if PDFs exist for processed papers")
    scan_parser.add_argument("--find-orphaned", action="store_true", help="Find papers in database but missing PDF")
    scan_parser.add_argument("--max-scan", type=int, default=1000, help="Maximum files to scan (default: 1000)")
    scan_parser.add_argument("--output", help="Save results to JSON file")

    # detect command (Phase D of 2026-05-13 consolidation) — the
    # unified successor to find-low-quality / find-short /
    # find-metadata-mismatch / repair-and-clean / qdrant-repair-sweep.
    detect_parser = subparsers.add_parser(
        "detect",
        help="Unified detection: --kinds metadata-mismatch[,low-quality,short,metadata-unverifiable,orphan]",
    )
    detect_parser.add_argument("--kinds", required=True,
                               help=f"Comma-separated detection kinds. Valid: {','.join(DETECT_KINDS)}")
    detect_parser.add_argument("--limit", type=int,
                               help="Max records to inspect / quarantine this run")
    detect_parser.add_argument("--source", default="all",
                               choices=["all", "upload", "crawler"],
                               help="Restrict to upload-only or crawler-only (metadata-mismatch only)")
    detect_parser.add_argument("--report-out", help="CSV / DOI-list output path")
    detect_parser.add_argument("--auto-quarantine", action="store_true",
                               help="Move flagged records to pdf/quarantine/ (soft action; replaces --auto-remove)")
    detect_parser.add_argument("--dry-run", action="store_true",
                               help="Print actions; don't write")
    # Per-kind pass-through flags:
    detect_parser.add_argument("--grobid-pace", type=int, default=30,
                               help="(metadata-mismatch) seconds between GROBID calls")
    detect_parser.add_argument("--no-grobid", action="store_true",
                               help="(metadata-mismatch) skip GROBID; pdftotext proxy only")
    detect_parser.add_argument("--no-backfill", action="store_true",
                               help="(metadata-mismatch) don't write audit fields to Qdrant")
    detect_parser.add_argument("--severity-threshold",
                               choices=["high", "medium"], default="high",
                               help="(metadata-mismatch) min severity for quarantine")
    detect_parser.add_argument("--min-pages", type=int, default=3,
                               help="(short) minimum page count")
    detect_parser.add_argument("--max-check", type=int, default=200,
                               help="(low-quality / metadata-unverifiable) max records to scan")
    detect_parser.add_argument("--no-enrich", action="store_true",
                               help="(low-quality) skip OpenAlex enrichment")

    # find-metadata-mismatch command (Stage 2 remediation tool)
    mismatch_parser = subparsers.add_parser(
        "find-metadata-mismatch",
        help="[DEPRECATED] Use `detect --kinds metadata-mismatch` instead",
    )
    mismatch_parser.add_argument("--limit", type=int, default=100,
                                 help="Max records to inspect this run (default: 100)")
    mismatch_parser.add_argument("--source", choices=["all", "upload", "crawler"],
                                 default="all", help="Restrict to upload-only or crawler-only (default: all)")
    mismatch_parser.add_argument("--grobid-pace", type=int, default=30,
                                 help="Seconds between GROBID calls (default: 30)")
    mismatch_parser.add_argument("--no-grobid", action="store_true",
                                 help="Skip GROBID; use pdftotext as title proxy (much noisier)")
    mismatch_parser.add_argument("--no-backfill", action="store_true",
                                 help="Don't write audit fields back to Qdrant")
    mismatch_parser.add_argument("--report-out", help="CSV output path (default: metadata-mismatch-<ts>.csv)")
    mismatch_parser.add_argument("--queue-for-reingest", action="store_true",
                                 help="Move high-severity PDFs back to inbox/ for the hardened pipeline to re-process")
    mismatch_parser.add_argument("--severity-threshold",
                                 choices=["high", "medium"], default="high",
                                 help="Minimum severity to queue (default: high)")
    mismatch_parser.add_argument("--reingest-log",
                                 help="Reingest rollback log CSV (default: reingest-log-<ts>.csv)")
    mismatch_parser.add_argument("--dry-run", action="store_true",
                                 help="Print actions; don't write to Qdrant or move files")

    # reattribute command (formerly the standalone reattribute_unknown.py)
    reattribute_parser = subparsers.add_parser(
        "reattribute",
        help="Backfill contributor attribution on records whose uploader was added to contributors.yml after ingest",
    )
    reattribute_parser.add_argument("--dry-run", action="store_true",
                                    help="Preview re-attributions; write nothing")
    reattribute_parser.add_argument("--no-neo4j", action="store_true",
                                    help="Skip Neo4j updates (Qdrant only)")

    # reingest-queue command (drives the queue produced above)
    reingest_parser = subparsers.add_parser(
        "reingest-queue",
        help="Run paper_pipeline.py --single on each PDF queued by find-metadata-mismatch",
    )
    reingest_parser.add_argument("--pipeline-script",
                                 help="Path to paper_pipeline.py (default: sibling in same dir)")
    reingest_parser.add_argument("--limit", type=int, default=50,
                                 help="Max queued PDFs to process this run (default: 50)")
    reingest_parser.add_argument("--pace", type=int, default=30,
                                 help="Seconds between pipeline subprocess invocations (default: 30)")
    reingest_parser.add_argument("--dry-run", action="store_true",
                                 help="Print actions; don't invoke pipeline")

    # repair-auto command (autonomous mode)
    auto_parser = subparsers.add_parser("repair-auto", help="[DEPRECATED] Use `detect --kinds metadata-unverifiable --auto-quarantine` instead")
    auto_parser.add_argument("--doi", help="Process a single DOI instead of bulk")
    auto_parser.add_argument("--source", choices=["neo4j", "processed"], default="neo4j",
                             help="Where to get paper list (default: neo4j)")
    auto_parser.add_argument("--max-papers", type=int, default=100, help="Maximum papers to process (default: 100)")
    auto_parser.add_argument("--output", help="Save log to JSON file")
    auto_parser.add_argument("--scripts-dir", help="Directory containing paper_crawler.py and paper_pipeline.py")
    auto_parser.add_argument("--retry-failed", action="store_true", help="Retry previously failed downloads")

    args = parser.parse_args()

    if args.command == "remove":
        remove_paper_by_doi(
            args.doi,
            dry_run=args.dry_run,
            add_blocklist=not args.no_blocklist
        )
    elif args.command == "find-low-quality":
        _deprecation_notice("find-low-quality", "detect --kinds low-quality")
        find_low_quality_papers(
            output_file=args.output,
            auto_remove=args.auto_remove,
            limit=args.limit,
            confirm=args.confirm,
            enrich=not args.no_enrich,
            max_check=args.max_check
        )
    elif args.command == "find-short":
        _deprecation_notice("find-short", "detect --kinds short")
        find_short_papers(
            min_pages=args.min_pages,
            output_file=args.output,
            auto_remove=args.auto_remove,
            limit=args.limit,
            confirm=args.confirm
        )
    elif args.command == "bulk-remove":
        bulk_remove(
            args.file,
            dry_run=args.dry_run,
            add_blocklist=not args.no_blocklist
        )
    elif args.command == "verify-doi":
        verify_doi(args.doi)
    elif args.command == "repair-and-clean":
        _deprecation_notice(
            "repair-and-clean",
            "detect --kinds metadata-unverifiable --auto-quarantine",
        )
        repair_and_clean(
            source=args.source,
            max_check=args.max_check,
            auto_remove=args.auto_remove,
            limit=args.limit,
            confirm=args.confirm,
            output_file=args.output,
            dry_run=args.dry_run,
            check_pdf=not args.no_pdf_check,
            export_orphaned=args.export_orphaned
        )
    elif args.command == "scan-processed":
        _deprecation_notice("scan-processed", "detect --kinds orphan")
        scan_processed(
            check_metadata=args.check_metadata,
            check_pdf=args.check_pdf,
            find_orphaned=args.find_orphaned,
            max_scan=args.max_scan,
            output_file=args.output
        )
    elif args.command == "repair-auto":
        _deprecation_notice(
            "repair-auto",
            "detect --kinds metadata-unverifiable --auto-quarantine",
        )
        repair_auto(
            source=args.source,
            max_papers=args.max_papers,
            output_file=args.output,
            scripts_dir=args.scripts_dir,
            retry_failed=args.retry_failed,
            single_doi=args.doi
        )
    elif args.command == "detect":
        kinds = [k.strip() for k in args.kinds.split(",") if k.strip()]
        sys.exit(detect(
            kinds=kinds,
            limit=args.limit,
            source=args.source,
            report_out=args.report_out,
            auto_quarantine=args.auto_quarantine,
            dry_run=args.dry_run,
            grobid_pace_secs=args.grobid_pace,
            no_grobid=args.no_grobid,
            no_backfill=args.no_backfill,
            severity_threshold=args.severity_threshold,
            min_pages=args.min_pages,
            max_check=args.max_check,
            enrich=not args.no_enrich,
        ))
    elif args.command == "find-metadata-mismatch":
        find_metadata_mismatch(
            limit=args.limit,
            source_filter=args.source,
            grobid_pace_secs=args.grobid_pace,
            no_grobid=args.no_grobid,
            no_backfill=args.no_backfill,
            report_out=args.report_out,
            queue_for_reingest=args.queue_for_reingest,
            severity_threshold=args.severity_threshold,
            reingest_log=args.reingest_log,
            dry_run=args.dry_run,
        )
    elif args.command == "reattribute":
        sys.exit(reattribute(dry_run=args.dry_run, no_neo4j=args.no_neo4j))
    elif args.command == "reingest-queue":
        reingest_queue(
            pipeline_script=args.pipeline_script,
            limit=args.limit,
            pace_secs=args.pace,
            dry_run=args.dry_run,
        )
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
